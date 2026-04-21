from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ProcessRuntimeHooks:
    path_exists: Callable[[Path], bool]
    read_bytes: Callable[[Path], bytes]
    read_text: Callable[[Path], str]
    readlink: Callable[[Path], str]
    run_subprocess: Callable[[list[str], int], Any]


def pid_exists(pid: int, *, hooks: ProcessRuntimeHooks) -> bool:
    return hooks.path_exists(Path(f"/proc/{pid}"))


def read_pid_cmdline(pid: int, *, hooks: ProcessRuntimeHooks) -> str:
    path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = hooks.read_bytes(path)
    except Exception:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def read_pid_cwd(pid: int, *, hooks: ProcessRuntimeHooks) -> str:
    path = Path(f"/proc/{pid}/cwd")
    try:
        return hooks.readlink(path)
    except Exception:
        return ""


def read_pid_state(pid: int, *, hooks: ProcessRuntimeHooks) -> str:
    path = Path(f"/proc/{pid}/status")
    try:
        for line in hooks.read_text(path).splitlines():
            if line.startswith("State:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        return ""
    return ""


def read_pid_snapshot(pid: int, *, hooks: ProcessRuntimeHooks) -> dict[str, Any] | None:
    if not pid_exists(pid, hooks=hooks):
        return None
    completed = hooks.run_subprocess(
        ["ps", "-p", str(pid), "-o", "pid=,ppid=,stat=,etime=,%cpu=,%mem=,args="],
        10,
    )
    if getattr(completed, "returncode", 1) != 0:
        return None
    line = str(getattr(completed, "stdout", "")).strip()
    if not line:
        return None
    parts = line.split(None, 6)
    if len(parts) < 7:
        return None
    return {
        "pid": int(parts[0]),
        "ppid": int(parts[1]),
        "stat": parts[2],
        "etime": parts[3],
        "cpu_percent": parts[4],
        "mem_percent": parts[5],
        "cmd": parts[6],
        "cwd": read_pid_cwd(pid, hooks=hooks),
        "proc_state": read_pid_state(pid, hooks=hooks),
    }


def build_tmux_session_name(task_id: str) -> str:
    digest = hashlib.sha1(task_id.encode("utf-8")).hexdigest()[:8]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", task_id)[:32].strip("-") or "task"
    return f"ctb-{prefix}-{digest}"
