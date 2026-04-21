#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_APP_HOME = Path("/home/ubunut/.local/state/codex-taskboard")
DEFAULT_IMAGE = "localhost/workspace-offline:20260330"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def default_base_url(runtime: str, port: int) -> str:
    if runtime == "docker":
        return f"http://host.docker.internal:{port}"
    return f"http://host.containers.internal:{port}"


def runtime_network_args(runtime: str) -> list[str]:
    if runtime == "docker":
        return ["--add-host", "host.docker.internal:host-gateway"]
    return []


def resolve_runtime(raw_runtime: str) -> str:
    runtime = str(raw_runtime or "auto").strip().lower() or "auto"
    if runtime == "auto":
        for candidate in ("podman", "docker"):
            if shutil.which(candidate):
                return candidate
        raise RuntimeError("Neither podman nor docker is available on this host.")
    if not shutil.which(runtime):
        raise RuntimeError(f"Container runtime not found: {runtime}")
    return runtime


def resolve_docker_user(raw_user: str) -> str:
    user = str(raw_user or "auto").strip() or "auto"
    if user != "auto":
        return user
    candidate_names: list[str] = []
    for candidate in (
        os.environ.get("CODEX_TASKBOARD_DOCKER_USER", ""),
        "ju",
        os.environ.get("USER", ""),
    ):
        candidate = str(candidate).strip()
        if candidate and candidate not in candidate_names:
            candidate_names.append(candidate)
    for candidate in candidate_names:
        try:
            pwd.getpwnam(candidate)
        except KeyError:
            continue
        probe = subprocess.run(
            ["sudo", "-n", "-u", candidate, "true"],
            text=True,
            capture_output=True,
        )
        if probe.returncode == 0:
            return candidate
    raise RuntimeError(
        "Could not auto-detect a runnable rootless container user. "
        "Set --docker-user explicitly or export CODEX_TASKBOARD_DOCKER_USER."
    )


def taskboard_cli_path(repo_root: Path) -> Path:
    candidate = repo_root / ".venv" / "bin" / "codex-taskboard"
    if candidate.exists():
        return candidate
    raise RuntimeError(f"Missing taskboard CLI entrypoint: {candidate}")


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, text=True, capture_output=True)
    if check and completed.returncode != 0:
        raise RuntimeError(
            "command failed\n"
            f"cmd={' '.join(command)}\n"
            f"returncode={completed.returncode}\n"
            f"stdout={completed.stdout}\n"
            f"stderr={completed.stderr}"
        )
    return completed


def run_container_python(
    *,
    runtime: str,
    docker_user: str,
    image: str,
    repo_root: Path,
    code: str,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    command = [
        "sudo",
        "-n",
        "-u",
        docker_user,
        runtime,
        "run",
        "--rm",
        *runtime_network_args(runtime),
        "-v",
        f"{repo_root}:/workspace/codex-taskboard:ro",
    ]
    for key, value in sorted((env or {}).items()):
        command.extend(["-e", f"{key}={value}"])
    command.extend(
        [
            image,
            "sh",
            "-lc",
            f"python3 - <<'PY'\n{code}\nPY",
        ]
    )
    completed = run_command(command)
    return json.loads(completed.stdout)


def run_container_client(
    *,
    runtime: str,
    docker_user: str,
    image: str,
    repo_root: Path,
    base_url: str,
    token: str,
    args: list[str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [
        "sudo",
        "-n",
        "-u",
        docker_user,
        runtime,
        "run",
        "--rm",
        *runtime_network_args(runtime),
        "-v",
        f"{repo_root}:/workspace/codex-taskboard:ro",
        "-e",
        f"CODEX_TASKBOARD_API_URL={base_url}",
        "-e",
        f"CODEX_TASKBOARD_API_TOKEN={token}",
        image,
        "python3",
        "/workspace/codex-taskboard/extras/codex_taskboard_client.py",
        *args,
    ]
    return run_command(command, check=check)


def load_token_registry(path: Path) -> dict[str, Any]:
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {"tokens": []}
    tokens = payload.get("tokens", [])
    if not isinstance(tokens, list):
        tokens = []
    return {"tokens": [item for item in tokens if isinstance(item, dict)]}


def install_temp_tokens(
    registry_path: Path,
    *,
    tenant: str,
    secondary_tenant: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    original = load_token_registry(registry_path)
    updated = {"tokens": list(original.get("tokens", []))}
    primary_token = secrets.token_urlsafe(24)
    secondary_token = secrets.token_urlsafe(24)
    primary_record = {
        "token_hash": sha256_text(primary_token),
        "tenant": tenant,
        "executor": "",
        "role": "user",
        "agent_name": f"docker:{tenant}:smoke",
        "allow_submit_job": True,
        "allow_read_results": True,
        "allow_session_feedback": False,
        "allow_dangerous_codex_exec": False,
        "allow_read_global_queue": True,
        "default_feedback_mode": "off",
    }
    secondary_record = {
        "token_hash": sha256_text(secondary_token),
        "tenant": secondary_tenant,
        "executor": "",
        "role": "user",
        "agent_name": f"docker:{secondary_tenant}:smoke",
        "allow_submit_job": False,
        "allow_read_results": True,
        "allow_session_feedback": False,
        "allow_dangerous_codex_exec": False,
        "allow_read_global_queue": False,
        "default_feedback_mode": "off",
    }
    updated["tokens"].extend([primary_record, secondary_record])
    atomic_write_json(registry_path, updated)
    return original, {"token": primary_token, "record": primary_record}, {"token": secondary_token, "record": secondary_record}


def assert_json_success(payload: dict[str, Any], *, label: str) -> None:
    if not bool(payload.get("ok", False)):
        raise RuntimeError(f"{label} did not return ok=true: {json.dumps(payload, ensure_ascii=False, indent=2)}")


def find_task_in_list(payload: dict[str, Any], task_id: str) -> bool:
    raw_items = payload.get("items", payload.get("tasks", []))
    if not isinstance(raw_items, list):
        return False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("task_id", "")).strip() == task_id:
            return True
    return False


def cleanup_tasks(repo_root: Path, task_ids: list[str]) -> None:
    cli = str(taskboard_cli_path(repo_root))
    for task_id in task_ids:
        run_command(
            [
                cli,
                "cleanup",
                "--task-id",
                task_id,
                "--include-nonterminal",
            ],
            check=False,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real rootless container -> taskboard API smoke flow.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--app-home", type=Path, default=DEFAULT_APP_HOME)
    parser.add_argument("--runtime", choices=["auto", "podman", "docker"], default="auto")
    parser.add_argument("--docker-user", default="auto")
    parser.add_argument("--tenant", default="")
    parser.add_argument("--secondary-tenant", default="")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--keep-tokens", action="store_true")
    parser.add_argument("--keep-tasks", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    runtime = resolve_runtime(args.runtime)
    docker_user = resolve_docker_user(args.docker_user)
    tenant = str(args.tenant or docker_user).strip()
    secondary_tenant = str(args.secondary_tenant or f"{tenant}-smoke-other").strip()
    taskboard_cli_path(repo_root)
    registry_path = Path(args.app_home).expanduser().resolve() / "api_tokens.json"
    base_url = str(args.base_url or default_base_url(runtime, args.port)).strip()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    queue_task_id = f"{tenant}.docker-queue-smoke-{timestamp}"
    result_task_id = f"{tenant}.docker-result-smoke-{timestamp}"
    temp_tasks = [queue_task_id, result_task_id]

    original_registry: dict[str, Any] | None = None
    summary: dict[str, Any] = {
        "runtime": runtime,
        "docker_user": docker_user,
        "tenant": tenant,
        "secondary_tenant": secondary_tenant,
        "image": args.image,
        "base_url": base_url,
        "queue_task_id": queue_task_id,
        "result_task_id": result_task_id,
    }
    try:
        original_registry, primary_token, secondary_token = install_temp_tokens(
            registry_path,
            tenant=tenant,
            secondary_tenant=secondary_tenant,
        )
        summary["primary_token_hash"] = primary_token["record"]["token_hash"]
        summary["secondary_token_hash"] = secondary_token["record"]["token_hash"]

        gpu_probe = run_container_python(
            runtime=runtime,
            docker_user=docker_user,
            image=args.image,
            repo_root=repo_root,
            env={},
            code=(
                "import glob, json, os\n"
                "print(json.dumps({"
                "\"gpu_nodes\": sorted(glob.glob('/dev/nvidia*')),"
                "\"cuda_visible_devices\": os.environ.get('CUDA_VISIBLE_DEVICES', '')"
                "}, ensure_ascii=False))\n"
            ),
        )
        if gpu_probe.get("gpu_nodes"):
            raise RuntimeError(f"container unexpectedly sees GPU devices: {gpu_probe}")
        summary["container_gpu_probe"] = gpu_probe

        queue_submit = json.loads(
            run_container_client(
                runtime=runtime,
                docker_user=docker_user,
                image=args.image,
                repo_root=repo_root,
                base_url=base_url,
                token=primary_token["token"],
                args=[
                    "submit-job",
                    "--task-id",
                    queue_task_id,
                    "--workdir",
                    str(repo_root),
                    "--command",
                    "bash -lc 'printf queue_smoke\\n'",
                    "--task-note",
                    "real-docker-api-smoke-held",
                    "--hold",
                ],
            ).stdout
        )
        assert_json_success(queue_submit, label="held queue submit")
        summary["queue_submit"] = queue_submit

        queue_payload = json.loads(
            run_container_client(
                runtime=runtime,
                docker_user=docker_user,
                image=args.image,
                repo_root=repo_root,
                base_url=base_url,
                token=primary_token["token"],
                args=["queue", "--limit", "20"],
            ).stdout
        )
        assert_json_success(queue_payload, label="/queue")
        if not find_task_in_list(queue_payload, queue_task_id):
            raise RuntimeError(f"held queue task not visible in /queue: {json.dumps(queue_payload, ensure_ascii=False, indent=2)}")
        summary["queue_payload"] = queue_payload

        cleanup_tasks(repo_root, [queue_task_id])

        result_submit = json.loads(
            run_container_client(
                runtime=runtime,
                docker_user=docker_user,
                image=args.image,
                repo_root=repo_root,
                base_url=base_url,
                token=primary_token["token"],
                args=[
                    "submit-job",
                    "--task-id",
                    result_task_id,
                    "--workdir",
                    str(repo_root),
                    "--command",
                    "bash -lc 'printf CODEX_TASKBOARD_DOCKER_SMOKE_OK\\n'",
                    "--task-note",
                    "real-docker-api-smoke-result",
                ],
            ).stdout
        )
        assert_json_success(result_submit, label="result submit")
        summary["result_submit"] = result_submit

        wait_payload = json.loads(
            run_container_client(
                runtime=runtime,
                docker_user=docker_user,
                image=args.image,
                repo_root=repo_root,
                base_url=base_url,
                token=primary_token["token"],
                args=[
                    "wait-result",
                    "--task-id",
                    result_task_id,
                    "--timeout-seconds",
                    "1800",
                    "--poll-seconds",
                    "2",
                    "--expect-status",
                    "completed",
                ],
            ).stdout
        )
        assert_json_success(wait_payload, label="wait-result")
        summary["wait_payload"] = wait_payload

        own_tasks_payload = json.loads(
            run_container_client(
                runtime=runtime,
                docker_user=docker_user,
                image=args.image,
                repo_root=repo_root,
                base_url=base_url,
                token=primary_token["token"],
                args=["tasks", "--status", "done", "--limit", "20"],
            ).stdout
        )
        assert_json_success(own_tasks_payload, label="own /tasks")
        if not find_task_in_list(own_tasks_payload, result_task_id):
            raise RuntimeError(f"result task not visible to owner tenant: {json.dumps(own_tasks_payload, ensure_ascii=False, indent=2)}")
        summary["own_tasks_payload"] = own_tasks_payload

        other_tasks_payload = json.loads(
            run_container_client(
                runtime=runtime,
                docker_user=docker_user,
                image=args.image,
                repo_root=repo_root,
                base_url=base_url,
                token=secondary_token["token"],
                args=["tasks", "--status", "done", "--limit", "20"],
            ).stdout
        )
        assert_json_success(other_tasks_payload, label="other tenant /tasks")
        if find_task_in_list(other_tasks_payload, result_task_id):
            raise RuntimeError(
                "secondary tenant unexpectedly sees primary completed task in /tasks: "
                + json.dumps(other_tasks_payload, ensure_ascii=False, indent=2)
            )
        summary["secondary_tasks_payload"] = other_tasks_payload

        denied_status = run_container_client(
            runtime=runtime,
            docker_user=docker_user,
            image=args.image,
            repo_root=repo_root,
            base_url=base_url,
            token=secondary_token["token"],
            args=["status-result", "--task-id", result_task_id],
            check=False,
        )
        if denied_status.returncode == 0:
            raise RuntimeError("secondary tenant unexpectedly accessed owner status-result")
        summary["secondary_status_result_denied"] = {
            "returncode": denied_status.returncode,
            "stdout": denied_status.stdout,
            "stderr": denied_status.stderr,
        }

        summary["ok"] = True
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        if not args.keep_tasks:
            cleanup_tasks(repo_root, temp_tasks)
        if original_registry is not None and not args.keep_tokens:
            atomic_write_json(registry_path, original_registry)


if __name__ == "__main__":
    raise SystemExit(main())
