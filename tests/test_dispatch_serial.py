import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import AppConfig, dispatch_queued_tasks


def build_config(app_home: Path) -> AppConfig:
    codex_home = app_home / "codex-home"
    return AppConfig(
        app_home=app_home,
        tasks_root=app_home / "tasks",
        locks_root=app_home / "locks",
        followups_root=app_home / "followups",
        legacy_task_roots=tuple(),
        tmux_socket_path=app_home / "tmux" / "default",
        codex_home=codex_home,
        threads_db_path=codex_home / "state_5.sqlite",
        thread_manifest_path=codex_home / "thread_sync_manifest.jsonl",
        sync_script_path=codex_home / "scripts" / "sync_codex_threads.py",
        codex_bin="codex",
        tmux_bin="tmux",
    )


def write_state(config: AppConfig, task_id: str, **fields: object) -> None:
    task_dir = config.tasks_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "task_id": task_id,
        "task_key": task_id,
        "status": "queued",
        "submitted_at": "2026-03-16T19:29:44Z",
        "priority": 0,
        **fields,
    }
    (task_dir / "state.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class DispatchSerialTests(unittest.TestCase):
    def test_dispatch_starts_only_oldest_queued_task_per_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "serial-b",
                submitted_at="2026-03-16T19:29:44.409317Z",
            )
            write_state(
                config,
                "serial-a",
                submitted_at="2026-03-16T19:29:44.409774Z",
            )
            started: list[str] = []

            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=0), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, **_kwargs: started.append(task_id),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="serial",
                    max_running=10,
                    limit=100,
                    gpu_count_override=0,
                    cpu_thread_limit=40,
                )

            self.assertEqual(started, ["serial-b"])
            self.assertEqual(result["started"], ["serial-b"])
            self.assertEqual(result["capacity"], 1)
            self.assertEqual(result["running_count"], 0)

    def test_dispatch_does_not_start_new_task_while_another_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "serial-a")
            write_state(config, "running-task", status="running", tmux_session_name="ctb-running")

            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=1), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=AssertionError("start_existing_task should not be called"),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="serial",
                    max_running=10,
                    limit=100,
                    gpu_count_override=0,
                    cpu_thread_limit=40,
                )

            self.assertEqual(result["started"], [])
            self.assertEqual(result["capacity"], 0)
            self.assertEqual(result["running_count"], 1)


if __name__ == "__main__":
    unittest.main()
