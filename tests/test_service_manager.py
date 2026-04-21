from __future__ import annotations

import fcntl
from dataclasses import dataclass
from pathlib import Path

from codex_taskboard.service_manager import (
    ServiceManagerHooks,
    TaskboardServiceSpec,
    build_service_doctor_payload,
    cleanup_stale_legacy_pid_files,
    load_runtime_record,
    render_systemd_unit,
    run_managed_service,
    service_instance_lock_path,
)


@dataclass(frozen=True)
class DummyConfig:
    app_home: Path
    locks_root: Path
    codex_home: Path
    codex_bin: str = "/usr/local/bin/codex"
    tmux_bin: str = "/usr/bin/tmux"


def make_hooks() -> ServiceManagerHooks:
    def ensure_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def append_log(path: Path, message: str) -> None:
        ensure_dir(path.parent)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    return ServiceManagerHooks(ensure_dir=ensure_dir, append_log=append_log)


def make_spec(tmp_path: Path, *, port: int = 0) -> TaskboardServiceSpec:
    return TaskboardServiceSpec(
        name="api",
        unit_name="codex-taskboard-api.service",
        description="codex-taskboard authenticated API server",
        exec_args=("api", "--bind", "0.0.0.0", "--port", str(port or 8765)),
        legacy_pid_files=(tmp_path / "serve-api.pid",),
        process_match_fragments=("codex-taskboard serve-api", "codex-taskboard service run api"),
        service_log_path=tmp_path / "service-api.log",
        after=("network-online.target",),
        wants=("network-online.target",),
        bind="0.0.0.0" if port else "",
        port=port,
    )


def test_render_systemd_unit_uses_managed_service_entrypoint(tmp_path: Path) -> None:
    config = DummyConfig(app_home=tmp_path / "state", locks_root=tmp_path / "locks", codex_home=tmp_path / "codex")
    spec = make_spec(tmp_path, port=8765)

    rendered = render_systemd_unit(
        config,
        spec,
        user="alice",
        group="alice",
        working_directory=Path("/repo"),
        entrypoint_path=Path("/repo/.venv/bin/codex-taskboard"),
    )

    assert "ExecStart=/repo/.venv/bin/codex-taskboard service run api --bind 0.0.0.0 --port 8765" in rendered
    assert "RestartPreventExitStatus=3" in rendered
    assert "Environment=CODEX_TASKBOARD_HOME=" in rendered


def test_cleanup_stale_legacy_pid_files_removes_dead_pid(tmp_path: Path) -> None:
    pidfile = tmp_path / "serve-api.pid"
    pidfile.write_text("99999999\n", encoding="utf-8")

    results = cleanup_stale_legacy_pid_files(
        (pidfile,),
        process_match_fragments=("codex-taskboard serve-api",),
    )

    assert results[0]["status"] == "dead"
    assert results[0]["removed"] is True
    assert not pidfile.exists()


def test_run_managed_service_blocks_when_lock_is_held(tmp_path: Path) -> None:
    config = DummyConfig(app_home=tmp_path / "state", locks_root=tmp_path / "locks", codex_home=tmp_path / "codex")
    spec = make_spec(tmp_path)
    hooks = make_hooks()

    lock_path = service_instance_lock_path(config, "api")
    hooks.ensure_dir(lock_path.parent)
    lock_handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        exit_code = run_managed_service(
            config,
            spec,
            hooks=hooks,
            details={"bind": "0.0.0.0", "port": 8765},
            run=lambda: 0,
        )
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    record = load_runtime_record(config, "api")
    assert exit_code == 3
    assert record["status"] == "blocked"
    assert "lock" in record.get("message", "")


def test_build_service_doctor_payload_flags_api_port_owner_mismatch(tmp_path: Path, monkeypatch) -> None:
    config = DummyConfig(app_home=tmp_path / "state", locks_root=tmp_path / "locks", codex_home=tmp_path / "codex")
    spec = make_spec(tmp_path, port=8765)
    unit_path = tmp_path / "codex-taskboard-api.service"
    unit_path.write_text(
        render_systemd_unit(
            config,
            spec,
            user="alice",
            group="alice",
            working_directory=Path("/repo"),
            entrypoint_path=Path("/repo/.venv/bin/codex-taskboard"),
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "codex_taskboard.service_manager.systemd_service_snapshot",
        lambda unit_name: {
            "available": True,
            "unit_name": unit_name,
            "active_state": "active",
            "sub_state": "running",
            "unit_file_state": "enabled",
            "main_pid": 100,
            "fragment_path": str(unit_path),
            "execstart_lines": [
                "ExecStart=/repo/.venv/bin/codex-taskboard service run api --bind 0.0.0.0 --port 8765"
            ],
        },
    )
    monkeypatch.setattr(
        "codex_taskboard.service_manager.tcp_port_listener_snapshot",
        lambda port: [{"pid": 200, "command": "codex-taskboard", "pid_command": "manual api", "line": ""}],
    )

    payload = build_service_doctor_payload(
        config,
        {"api": spec},
        user="alice",
        group="alice",
        working_directory=Path("/repo"),
        entrypoint_path=Path("/repo/.venv/bin/codex-taskboard"),
    )

    assert payload["healthy"] is False
    assert any("api port owner mismatch" in issue for issue in payload["issues"])
