from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from codex_taskboard.process_runtime import (
    ProcessRuntimeHooks,
    build_tmux_session_name,
    pid_exists,
    read_pid_cmdline,
    read_pid_cwd,
    read_pid_snapshot,
    read_pid_state,
)


def make_hooks(proc_data: dict[str, Any], *, ps_stdout: str = "", returncode: int = 0) -> ProcessRuntimeHooks:
    return ProcessRuntimeHooks(
        path_exists=lambda path: bool(proc_data.get(str(path), False)),
        read_bytes=lambda path: proc_data[str(path)],
        read_text=lambda path: proc_data[str(path)],
        readlink=lambda path: str(proc_data[str(path)]),
        run_subprocess=lambda _args, _timeout: SimpleNamespace(returncode=returncode, stdout=ps_stdout),
    )


def test_pid_helpers_read_proc_files() -> None:
    hooks = make_hooks(
        {
            "/proc/321": True,
            "/proc/321/cmdline": b"python\x00worker.py\x00--flag\x00",
            "/proc/321/cwd": "/workspace/demo",
            "/proc/321/status": "Name:\tpython\nState:\tS (sleeping)\n",
        },
        ps_stdout="321 1 Sl 00:15 1.2 0.3 python worker.py --flag",
    )

    assert pid_exists(321, hooks=hooks) is True
    assert read_pid_cmdline(321, hooks=hooks) == "python worker.py --flag"
    assert read_pid_cwd(321, hooks=hooks) == "/workspace/demo"
    assert read_pid_state(321, hooks=hooks) == "S (sleeping)"

    snapshot = read_pid_snapshot(321, hooks=hooks)
    assert snapshot == {
        "pid": 321,
        "ppid": 1,
        "stat": "Sl",
        "etime": "00:15",
        "cpu_percent": "1.2",
        "mem_percent": "0.3",
        "cmd": "python worker.py --flag",
        "cwd": "/workspace/demo",
        "proc_state": "S (sleeping)",
    }


def test_read_pid_snapshot_returns_none_when_process_missing_or_ps_fails() -> None:
    missing_hooks = make_hooks({})
    assert read_pid_snapshot(999, hooks=missing_hooks) is None

    failing_hooks = make_hooks({"/proc/999": True}, returncode=1)
    assert read_pid_snapshot(999, hooks=failing_hooks) is None


def test_build_tmux_session_name_sanitizes_and_stabilizes() -> None:
    session_name = build_tmux_session_name("docker user/task demo")
    assert session_name.startswith("ctb-docker-user-task-demo-")
    assert len(session_name.rsplit("-", 1)[-1]) == 8
    assert build_tmux_session_name("docker user/task demo") == session_name
