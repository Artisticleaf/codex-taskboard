import io
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import subprocess

from codex_taskboard.cli import AppConfig, command_cleanup


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
    spec_path = task_dir / "spec.json"
    spec_path.write_text("{}\n", encoding="utf-8")
    payload = {
        "version": 1,
        "task_id": task_id,
        "task_key": task_id,
        "status": "completed",
        "submitted_at": "2026-03-16T19:29:44Z",
        "tmux_session_name": f"ctb-{task_id}",
        "workdir": "/home/Awei",
        "pid": 12345,
        "paths": {
            "task_root": str(task_dir),
            "spec_path": str(spec_path),
        },
        **fields,
    }
    (task_dir / "state.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_args(app_home: Path, task_id: str, *, kill_if_running: bool) -> Namespace:
    return Namespace(
        app_home=str(app_home),
        codex_home=str(app_home / "codex-home"),
        codex_bin="codex",
        tmux_bin="tmux",
        task_id=task_id,
        kill_if_running=kill_if_running,
        include_nonterminal=False,
    )


class CleanupTests(unittest.TestCase):
    def test_cleanup_skips_task_when_runner_pid_is_alive_without_tmux_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir)
            config = build_config(app_home)
            write_state(config, "normal-a")
            args = build_args(app_home, "normal-a", kill_if_running=False)
            stdout = io.StringIO()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "codex_taskboard.cli.tmux_session_exists", return_value=False
            ), patch(
                "codex_taskboard.cli.pid_exists",
                return_value=True,
            ), patch(
                "codex_taskboard.cli.read_pid_cmdline",
                return_value=f"python3 -m codex_taskboard.cli run --spec-file {config.tasks_root / 'normal-a' / 'spec.json'}",
            ), redirect_stdout(stdout):
                rc = command_cleanup(args)

            self.assertEqual(rc, 1)
            self.assertTrue((config.tasks_root / "normal-a").exists())
            self.assertIn("normal-a:runner_alive", stdout.getvalue())

    def test_cleanup_skips_task_when_runner_survives_kill_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir)
            config = build_config(app_home)
            write_state(config, "normal-a")
            args = build_args(app_home, "normal-a", kill_if_running=True)
            stdout = io.StringIO()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "codex_taskboard.cli.tmux_session_exists",
                side_effect=[True, False],
            ), patch(
                "codex_taskboard.cli.run_subprocess",
                return_value=subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr=""),
            ), patch(
                "codex_taskboard.cli.pid_exists",
                return_value=True,
            ), patch(
                "codex_taskboard.cli.read_pid_cmdline",
                return_value=f"python3 -m codex_taskboard.cli run --spec-file {config.tasks_root / 'normal-a' / 'spec.json'}",
            ), patch("codex_taskboard.cli.time.sleep"), redirect_stdout(stdout):
                rc = command_cleanup(args)

            self.assertEqual(rc, 1)
            self.assertTrue((config.tasks_root / "normal-a").exists())
            self.assertIn("normal-a:runner_alive", stdout.getvalue())

if __name__ == "__main__":
    unittest.main()
