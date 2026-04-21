from __future__ import annotations

import errno
import fcntl
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class TaskboardServiceSpec:
    name: str
    unit_name: str
    description: str
    exec_args: tuple[str, ...]
    legacy_pid_files: tuple[Path, ...]
    process_match_fragments: tuple[str, ...]
    service_log_path: Path
    after: tuple[str, ...] = ()
    wants: tuple[str, ...] = ()
    kill_mode: str = ""
    bind: str = ""
    port: int = 0


@dataclass(frozen=True)
class ServiceManagerHooks:
    ensure_dir: Callable[[Path], None]
    append_log: Callable[[Path, str], None]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_entrypoint_path() -> Path:
    candidate = repo_root() / ".venv" / "bin" / "codex-taskboard"
    if candidate.exists():
        return candidate
    argv0 = Path(sys.argv[0]).resolve()
    return argv0 if argv0.exists() else candidate


def service_runtime_root(config: Any) -> Path:
    return config.app_home / "service-runtime"


def service_runtime_path(config: Any, service_name: str) -> Path:
    return service_runtime_root(config) / f"{service_name}.json"


def service_instance_lock_path(config: Any, service_name: str) -> Path:
    return config.locks_root / f"service-{service_name}.lock"


def service_unit_command(entrypoint_path: Path, spec: TaskboardServiceSpec) -> list[str]:
    return [str(entrypoint_path), "service", "run", *spec.exec_args]


def resolved_binary(binary: str) -> str:
    resolved = shutil.which(str(binary or "").strip())
    return resolved or str(binary)


def render_systemd_unit(
    config: Any,
    spec: TaskboardServiceSpec,
    *,
    user: str,
    group: str,
    working_directory: Path | None = None,
    entrypoint_path: Path | None = None,
    wanted_by: str = "multi-user.target",
) -> str:
    resolved_workdir = working_directory or repo_root()
    resolved_entrypoint = entrypoint_path or default_entrypoint_path()
    lines = [
        "[Unit]",
        f"Description={spec.description}",
    ]
    if spec.after:
        lines.append(f"After={' '.join(spec.after)}")
    if spec.wants:
        lines.append(f"Wants={' '.join(spec.wants)}")
    lines.extend(
        [
            "",
            "[Service]",
            "Type=simple",
            f"User={user}",
            f"Group={group}",
            f"WorkingDirectory={resolved_workdir}",
            f"Environment=CODEX_TASKBOARD_HOME={config.app_home}",
            f"Environment=CODEX_HOME={config.codex_home}",
            f"Environment=CODEX_BIN={resolved_binary(config.codex_bin)}",
            f"Environment=TMUX_BIN={resolved_binary(config.tmux_bin)}",
            "Environment=PYTHONUNBUFFERED=1",
            f"ExecStart={shlex.join(service_unit_command(resolved_entrypoint, spec))}",
            "Restart=always",
            "RestartSec=3",
            "RestartPreventExitStatus=3",
        ]
    )
    if spec.kill_mode:
        lines.append(f"KillMode={spec.kill_mode}")
    lines.extend(["", "[Install]", f"WantedBy={wanted_by}"])
    return "\n".join(lines) + "\n"


def expected_unit_lines(
    config: Any,
    spec: TaskboardServiceSpec,
    *,
    user: str,
    group: str,
    working_directory: Path | None = None,
    entrypoint_path: Path | None = None,
) -> list[str]:
    rendered = render_systemd_unit(
        config,
        spec,
        user=user,
        group=group,
        working_directory=working_directory,
        entrypoint_path=entrypoint_path,
    )
    return [line for line in rendered.splitlines() if line and not line.startswith("[")]


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_process_command_text(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except Exception:
        return ""
    return " ".join(part for part in raw.decode("utf-8", errors="replace").split("\x00") if part).strip()


def read_pidfile(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not text:
        return None
    try:
        return int(text)
    except Exception:
        return None


def inspect_legacy_pid_file(path: Path, *, process_match_fragments: tuple[str, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": str(path),
        "present": path.exists(),
        "status": "missing",
        "pid": None,
        "command": "",
    }
    if not path.exists():
        return payload
    pid = read_pidfile(path)
    payload["pid"] = pid
    if pid is None:
        payload["status"] = "invalid"
        return payload
    if not process_alive(pid):
        payload["status"] = "dead"
        return payload
    command_text = read_process_command_text(pid)
    payload["command"] = command_text
    if process_match_fragments and not any(fragment in command_text for fragment in process_match_fragments):
        payload["status"] = "mismatched_process"
        return payload
    payload["status"] = "live"
    return payload


def cleanup_stale_legacy_pid_files(
    pid_paths: tuple[Path, ...],
    *,
    process_match_fragments: tuple[str, ...],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in pid_paths:
        result = inspect_legacy_pid_file(path, process_match_fragments=process_match_fragments)
        status = str(result.get("status", "missing"))
        if status in {"invalid", "dead", "mismatched_process"} and path.exists():
            try:
                path.unlink()
                result["removed"] = True
            except FileNotFoundError:
                result["removed"] = True
            except Exception as exc:
                result["removed"] = False
                result["remove_error"] = str(exc)
        else:
            result["removed"] = False
        results.append(result)
    return results


def try_acquire_service_lock(lock_path: Path, *, hooks: ServiceManagerHooks) -> tuple[Any | None, str]:
    hooks.ensure_dir(lock_path.parent)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None, f"service instance lock is already held: {lock_path}"
    except Exception as exc:
        handle.close()
        return None, f"failed to acquire service instance lock {lock_path}: {exc}"
    return handle, ""


def release_service_lock(handle: Any | None) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        handle.close()
    except Exception:
        pass


def can_bind_tcp_port(bind: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind, int(port)))
        return True
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return False
        raise
    finally:
        sock.close()


def tcp_port_listener_snapshot(port: int) -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["ss", "-H", "-ltnp"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    listeners: list[dict[str, Any]] = []
    marker = f":{int(port)}"
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if marker not in stripped:
            continue
        pid = None
        command = ""
        if "pid=" in stripped:
            after_pid = stripped.split("pid=", 1)[1]
            digits = []
            for char in after_pid:
                if char.isdigit():
                    digits.append(char)
                else:
                    break
            if digits:
                pid = int("".join(digits))
        if 'users:(("' in stripped:
            command = stripped.split('users:(("', 1)[1].split('"', 1)[0]
        listeners.append(
            {
                "line": stripped,
                "pid": pid,
                "command": command,
                "pid_command": read_process_command_text(pid) if isinstance(pid, int) and pid > 0 else "",
            }
        )
    return listeners


def systemd_service_snapshot(unit_name: str) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "available": False,
        "unit_name": unit_name,
        "active_state": "",
        "sub_state": "",
        "unit_file_state": "",
        "main_pid": 0,
        "fragment_path": "",
        "execstart_lines": [],
    }
    try:
        completed = subprocess.run(
            [
                "systemctl",
                "show",
                unit_name,
                "--no-pager",
                "--property=ActiveState,SubState,UnitFileState,MainPID,FragmentPath",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        snapshot["error"] = str(exc)
        return snapshot
    if completed.returncode != 0:
        snapshot["error"] = (completed.stderr or completed.stdout or "systemctl show failed").strip()
        return snapshot
    snapshot["available"] = True
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "ActiveState":
            snapshot["active_state"] = value
        elif key == "SubState":
            snapshot["sub_state"] = value
        elif key == "UnitFileState":
            snapshot["unit_file_state"] = value
        elif key == "MainPID":
            try:
                snapshot["main_pid"] = int(value or 0)
            except Exception:
                snapshot["main_pid"] = 0
        elif key == "FragmentPath":
            snapshot["fragment_path"] = value
    fragment_path = Path(str(snapshot.get("fragment_path", "")).strip())
    if fragment_path.exists():
        try:
            execstart_lines = [
                line.strip()
                for line in fragment_path.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("ExecStart=")
            ]
        except Exception:
            execstart_lines = []
        snapshot["execstart_lines"] = execstart_lines
    return snapshot


def load_runtime_record(config: Any, service_name: str) -> dict[str, Any]:
    path = service_runtime_path(config, service_name)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_runtime_record(config: Any, service_name: str, payload: dict[str, Any], *, hooks: ServiceManagerHooks) -> None:
    path = service_runtime_path(config, service_name)
    hooks.ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_runtime_record(
    spec: TaskboardServiceSpec,
    *,
    status: str,
    pid: int,
    lock_path: Path,
    details: dict[str, Any],
    legacy_pid_cleanup: list[dict[str, Any]],
    message: str = "",
) -> dict[str, Any]:
    record = {
        "service": spec.name,
        "unit_name": spec.unit_name,
        "status": status,
        "pid": int(pid or 0),
        "lock_path": str(lock_path),
        "legacy_pid_cleanup": legacy_pid_cleanup,
        "details": details,
        "systemd_invocation_id": str(os.environ.get("INVOCATION_ID", "") or ""),
        "command": read_process_command_text(pid) if int(pid or 0) > 0 else "",
    }
    if message:
        record["message"] = message
    return record


def run_managed_service(
    config: Any,
    spec: TaskboardServiceSpec,
    *,
    hooks: ServiceManagerHooks,
    details: dict[str, Any],
    run: Callable[[], int],
) -> int:
    hooks.ensure_dir(config.app_home)
    legacy_pid_cleanup = cleanup_stale_legacy_pid_files(
        spec.legacy_pid_files,
        process_match_fragments=spec.process_match_fragments,
    )
    for result in legacy_pid_cleanup:
        if bool(result.get("removed", False)):
            hooks.append_log(spec.service_log_path, f"legacy_pid_file_removed path={result['path']} status={result['status']}")
    lock_path = service_instance_lock_path(config, spec.name)
    lock_handle, lock_error = try_acquire_service_lock(lock_path, hooks=hooks)
    if lock_handle is None:
        message = lock_error or "service instance lock is already held"
        write_runtime_record(
            config,
            spec.name,
            build_runtime_record(
                spec,
                status="blocked",
                pid=os.getpid(),
                lock_path=lock_path,
                details=details,
                legacy_pid_cleanup=legacy_pid_cleanup,
                message=message,
            ),
            hooks=hooks,
        )
        hooks.append_log(spec.service_log_path, f"service_blocked service={spec.name} reason={message}")
        print(message, file=sys.stderr)
        return 3
    try:
        if spec.port and spec.bind and not can_bind_tcp_port(spec.bind, spec.port):
            listeners = tcp_port_listener_snapshot(spec.port)
            message = f"tcp port already in use: {spec.bind}:{spec.port}"
            if listeners:
                listener = listeners[0]
                message = f"{message} pid={listener.get('pid')} command={listener.get('pid_command') or listener.get('command') or listener.get('line')}"
            payload = build_runtime_record(
                spec,
                status="blocked",
                pid=os.getpid(),
                lock_path=lock_path,
                details={**details, "listeners": listeners},
                legacy_pid_cleanup=legacy_pid_cleanup,
                message=message,
            )
            write_runtime_record(config, spec.name, payload, hooks=hooks)
            hooks.append_log(spec.service_log_path, f"service_blocked service={spec.name} reason={message}")
            print(message, file=sys.stderr)
            return 3
        start_payload = build_runtime_record(
            spec,
            status="running",
            pid=os.getpid(),
            lock_path=lock_path,
            details=details,
            legacy_pid_cleanup=legacy_pid_cleanup,
        )
        write_runtime_record(config, spec.name, start_payload, hooks=hooks)
        hooks.append_log(spec.service_log_path, f"service_started service={spec.name} pid={os.getpid()}")
        exit_code = int(run())
        stop_payload = build_runtime_record(
            spec,
            status="stopped",
            pid=os.getpid(),
            lock_path=lock_path,
            details={**details, "exit_code": exit_code},
            legacy_pid_cleanup=legacy_pid_cleanup,
        )
        write_runtime_record(config, spec.name, stop_payload, hooks=hooks)
        hooks.append_log(spec.service_log_path, f"service_stopped service={spec.name} pid={os.getpid()} exit_code={exit_code}")
        return exit_code
    finally:
        release_service_lock(lock_handle)


def build_service_doctor_payload(
    config: Any,
    service_specs: dict[str, TaskboardServiceSpec],
    *,
    user: str,
    group: str,
    working_directory: Path | None = None,
    entrypoint_path: Path | None = None,
) -> dict[str, Any]:
    resolved_workdir = working_directory or repo_root()
    resolved_entrypoint = entrypoint_path or default_entrypoint_path()
    services: dict[str, Any] = {}
    issues: list[str] = []
    for name, spec in service_specs.items():
        unit_snapshot = systemd_service_snapshot(spec.unit_name)
        runtime_record = load_runtime_record(config, name)
        legacy_pid_files = [
            inspect_legacy_pid_file(path, process_match_fragments=spec.process_match_fragments)
            for path in spec.legacy_pid_files
        ]
        port_listeners = tcp_port_listener_snapshot(spec.port) if spec.port else []
        expected_lines = expected_unit_lines(
            config,
            spec,
            user=user,
            group=group,
            working_directory=resolved_workdir,
            entrypoint_path=resolved_entrypoint,
        )
        actual_lines: list[str] = []
        fragment_path = Path(str(unit_snapshot.get("fragment_path", "")).strip())
        if fragment_path.exists():
            try:
                actual_lines = [line.strip() for line in fragment_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except Exception:
                actual_lines = []
        missing_expected_lines = [line for line in expected_lines if line not in actual_lines]
        service_issues: list[str] = []
        if not bool(unit_snapshot.get("available", False)):
            service_issues.append(f"systemd unit missing: {spec.unit_name}")
        else:
            if str(unit_snapshot.get("active_state", "")) != "active":
                service_issues.append(
                    f"systemd unit inactive: {spec.unit_name} state={unit_snapshot.get('active_state')} substate={unit_snapshot.get('sub_state')}"
                )
            if missing_expected_lines:
                service_issues.append(f"systemd unit drift: {spec.unit_name}")
        for pidfile in legacy_pid_files:
            if str(pidfile.get("status", "missing")) != "missing":
                service_issues.append(f"legacy pid file present: {pidfile['path']} status={pidfile['status']}")
        if spec.port and port_listeners:
            main_pid = int(unit_snapshot.get("main_pid", 0) or 0)
            listener_pids = {int(item.get("pid", 0) or 0) for item in port_listeners if int(item.get("pid", 0) or 0) > 0}
            if main_pid <= 0 or (listener_pids and main_pid not in listener_pids):
                service_issues.append(f"api port owner mismatch on {spec.bind}:{spec.port}")
        services[name] = {
            "unit_name": spec.unit_name,
            "systemd": unit_snapshot,
            "runtime_record": runtime_record,
            "legacy_pid_files": legacy_pid_files,
            "port_listeners": port_listeners,
            "missing_expected_lines": missing_expected_lines,
            "healthy": not service_issues,
            "issues": service_issues,
        }
        issues.extend(service_issues)
    return {
        "entrypoint_path": str(resolved_entrypoint),
        "working_directory": str(resolved_workdir),
        "services": services,
        "healthy": not issues,
        "issues": issues,
    }
