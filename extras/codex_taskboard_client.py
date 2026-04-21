#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "codex-taskboard" / "client.json"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_client_config(path: Path) -> dict[str, Any]:
    payload = read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def parse_env_pairs(values: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for entry in values:
        if "=" not in entry:
            raise ValueError(f"Expected KEY=VALUE entry, got: {entry}")
        key, value = entry.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid empty environment key: {entry}")
        env[key] = value
    return env


def resolve_connection(args: argparse.Namespace) -> tuple[str, str, str]:
    config = load_client_config(Path(args.config).expanduser())
    base_url = str(
        args.base_url
        or os.environ.get("CODEX_TASKBOARD_API_URL", "")
        or config.get("base_url", "")
    ).strip()
    token = str(
        args.api_token
        or os.environ.get("CODEX_TASKBOARD_API_TOKEN", "")
        or config.get("api_token", "")
    ).strip()
    executor = str(
        args.executor
        or os.environ.get("CODEX_TASKBOARD_EXECUTOR", "")
        or config.get("executor", "")
    ).strip()
    if not base_url:
        raise ValueError("Missing taskboard API base_url")
    if not token:
        raise ValueError("Missing taskboard API token")
    return base_url.rstrip("/"), token, executor


def request_json(
    *,
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url=url, method=method.upper(), data=body, headers=headers)
    try:
        with urlopen(request, timeout=3600) as response:
            text = response.read().decode("utf-8")
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {text or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc
    try:
        data = json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON response: {text[:500]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected non-object response")
    return data


def command_submit_job(args: argparse.Namespace) -> int:
    base_url, token, executor = resolve_connection(args)
    payload = {
        "task_id": args.task_id,
        "task_key": args.task_key or args.task_id,
        "workdir": args.workdir or os.getcwd(),
        "command": args.command,
        "codex_session_id": args.codex_session_id or "",
        "feedback_mode": args.feedback_mode,
        "priority": args.priority,
        "gpu_slots": args.gpu_slots,
        "cpu_threads": args.cpu_threads,
        "cpu_threads_min": args.cpu_threads_min,
        "cpu_threads_max": args.cpu_threads_max,
        "cpu_threads_mode": args.cpu_threads_mode,
        "report_format": args.report_format,
        "report_keys": args.report_key or [],
        "report_contract": args.report_contract or "",
        "task_note": args.task_note or "",
        "success_prompt": args.success_prompt or "",
        "failure_prompt": args.failure_prompt or "",
        "artifact_globs": args.artifact_glob or [],
        "codex_exec_mode": args.codex_exec_mode,
        "resume_timeout_seconds": args.resume_timeout_seconds,
        "env": parse_env_pairs(args.env or []),
        "depends_on": args.depends_on or [],
        "hold": args.hold,
    }
    if executor:
        payload["executor"] = executor
    if args.agent_name:
        payload["agent_name"] = args.agent_name
    if args.closeout_proposal_dir is not None:
        payload["closeout_proposal_dir"] = args.closeout_proposal_dir
    response = request_json(method="POST", url=f"{base_url}/submit-job", token=token, payload=payload)
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if bool(response.get("ok", False)) else 1


def command_status_result(args: argparse.Namespace) -> int:
    base_url, token, _executor = resolve_connection(args)
    response = request_json(
        method="GET",
        url=f"{base_url}/status-result?{urlencode({'task_id': args.task_id})}",
        token=token,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if bool(response.get("ok", False)) else 1


def command_wait_result(args: argparse.Namespace) -> int:
    base_url, token, _executor = resolve_connection(args)
    query = urlencode(
        {
            "task_id": args.task_id,
            "timeout_seconds": args.timeout_seconds,
            "poll_seconds": args.poll_seconds,
        }
    )
    response = request_json(
        method="GET",
        url=f"{base_url}/wait-result?{query}",
        token=token,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))
    if not bool(response.get("ok", False)):
        return 1
    result = response.get("result", {})
    if args.expect_status and str(result.get("status", "")) != args.expect_status:
        return 2
    return 0


def command_queue(args: argparse.Namespace) -> int:
    base_url, token, _executor = resolve_connection(args)
    query = urlencode(
        {
            "limit": args.limit,
            "sort": args.sort,
        }
    )
    response = request_json(
        method="GET",
        url=f"{base_url}/queue?{query}",
        token=token,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if bool(response.get("ok", False)) else 1


def command_tasks(args: argparse.Namespace) -> int:
    base_url, token, _executor = resolve_connection(args)
    query = urlencode(
        {
            "status": args.status,
            "sort": args.sort,
            "limit": args.limit,
        }
    )
    response = request_json(
        method="GET",
        url=f"{base_url}/tasks?{query}",
        token=token,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if bool(response.get("ok", False)) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HTTP client for codex-taskboard agentless job APIs.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--base-url")
    parser.add_argument("--api-token")
    parser.add_argument("--executor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit-job", help="Submit one agentless job to the host taskboard API.")
    submit.add_argument("--task-id", required=True)
    submit.add_argument("--task-key")
    submit.add_argument("--workdir")
    submit.add_argument("--command", required=True)
    submit.add_argument("--codex-session-id")
    submit.add_argument("--agent-name")
    submit.add_argument("--closeout-proposal-dir")
    submit.add_argument("--priority", type=int, default=0)
    submit.add_argument("--gpu-slots", type=int)
    submit.add_argument("--cpu-threads", type=int, default=0)
    submit.add_argument("--cpu-threads-min", type=int, default=0)
    submit.add_argument("--cpu-threads-max", type=int, default=0)
    submit.add_argument("--cpu-threads-mode", choices=["fixed", "adaptive"], default="")
    submit.add_argument("--feedback-mode", choices=["off", "manual", "auto"], default="off")
    submit.add_argument("--success-prompt")
    submit.add_argument("--failure-prompt")
    submit.add_argument("--artifact-glob", action="append")
    submit.add_argument("--codex-exec-mode", choices=["dangerous", "full-auto"], default="dangerous")
    submit.add_argument("--resume-timeout-seconds", type=int, default=7200)
    submit.add_argument("--report-format", choices=["auto", "json-line", "key-value", "artifact-json"], default="auto")
    submit.add_argument("--report-key", action="append")
    submit.add_argument("--report-contract")
    submit.add_argument("--task-note")
    submit.add_argument("--depends-on", action="append")
    submit.add_argument("--env", action="append")
    submit.add_argument("--hold", action="store_true")
    submit.set_defaults(func=command_submit_job)

    status = subparsers.add_parser("status-result", help="Fetch one task result payload.")
    status.add_argument("--task-id", required=True)
    status.set_defaults(func=command_status_result)

    wait = subparsers.add_parser("wait-result", help="Wait until one task reaches a terminal state.")
    wait.add_argument("--task-id", required=True)
    wait.add_argument("--timeout-seconds", type=float, default=3600)
    wait.add_argument("--poll-seconds", type=float, default=2.0)
    wait.add_argument("--expect-status")
    wait.set_defaults(func=command_wait_result)

    queue = subparsers.add_parser("queue", help="List the current queue view from the host taskboard API.")
    queue.add_argument("--limit", type=int, default=30)
    queue.add_argument("--sort", choices=["queue", "priority", "updated", "agent", "status"], default="queue")
    queue.set_defaults(func=command_queue)

    tasks = subparsers.add_parser("tasks", help="List the current visible task view from the host taskboard API.")
    tasks.add_argument("--status", choices=["all", "active", "queued", "attention", "pending", "done"], default="all")
    tasks.add_argument("--sort", choices=["queue", "priority", "updated", "agent", "status"], default="queue")
    tasks.add_argument("--limit", type=int, default=30)
    tasks.set_defaults(func=command_tasks)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
