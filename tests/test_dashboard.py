from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import (
    AppConfig,
    build_dashboard_view_from_snapshot,
    command_automation_mode,
    command_backlog,
    dashboard_lines_from_view,
    followup_path,
)


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


def make_args(app_home: Path, **overrides: object) -> argparse.Namespace:
    base = {
        "app_home": str(app_home),
        "codex_home": str(app_home / "codex-home"),
        "codex_bin": "codex",
        "tmux_bin": "tmux",
        "action": "status",
        "codex_session_id": "session-001",
        "json": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class DashboardTests(unittest.TestCase):
    def test_command_automation_mode_switches_between_continuous_and_managed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir)
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = command_automation_mode(make_args(app_home, action="continuous"))
            self.assertEqual(rc, 0)
            self.assertIn("automation_mode=continuous", stdout.getvalue())

            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = command_automation_mode(make_args(app_home, action="managed"))
            self.assertEqual(rc, 0)
            self.assertIn("automation_mode=managed", stdout.getvalue())

    def test_command_backlog_reports_and_clears_reflow_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir)
            config = build_config(app_home)
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "queued-backlog").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "queued-backlog",
                        "followup_type": "queued_feedback_resume",
                        "codex_session_id": "session-001",
                        "queued_notifications": [
                            {"event_timestamp": "2026-04-22T00:00:00Z", "task_id": "task-a"},
                            {"event_timestamp": "2026-04-22T01:00:00Z", "task_id": "task-b"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = command_backlog(make_args(app_home, action="status"))
            self.assertEqual(rc, 0)
            self.assertIn("events=2", stdout.getvalue())

            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = command_backlog(make_args(app_home, action="clear"))
            self.assertEqual(rc, 0)
            self.assertIn("cleared_events=2", stdout.getvalue())

    def test_build_dashboard_view_from_snapshot_shows_mode_and_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir)
            config = build_config(app_home)
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "queued-backlog").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "queued-backlog",
                        "followup_type": "queued_feedback_resume",
                        "codex_session_id": "session-001",
                        "queued_notifications": [
                            {"event_timestamp": "2026-04-22T00:00:00Z", "task_id": "task-a"},
                            {"event_timestamp": "2026-04-22T01:00:00Z", "task_id": "task-b"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            command_automation_mode(make_args(app_home, action="continuous"))
            snapshot = {
                "counts": {"queued": 0, "submitted": 0, "completed": 0, "observed_exit": 0, "failed": 0, "terminated": 0, "launch_failed": 0},
                "running_live": 0,
                "active_states": [],
                "cpu_thread_limit": 40,
                "active_cpu_threads": 0,
                "gpu_rows": [],
                "total_gpu_slots": 0,
                "followups": {},
                "active_sessions": [],
                "pending_feedback_count": 0,
                "enriched_states": [],
                "process_hints": [],
                "process_panel_mode": "off",
            }

            view = build_dashboard_view_from_snapshot(config, snapshot, limit=20, width=140, height=30)

            header = "\n".join(view["header_lines"])
            self.assertIn("[mode continuous]", header)
            self.assertIn("[backlog 2]", header)

    def test_dashboard_lines_show_active_session_without_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir)
            config = build_config(app_home)
            command_automation_mode(make_args(app_home, action="continuous", codex_session_id="session-active-001"))
            snapshot = {
                "counts": {"queued": 0, "submitted": 0, "completed": 0, "observed_exit": 0, "failed": 0, "terminated": 0, "launch_failed": 0},
                "running_live": 0,
                "active_states": [],
                "cpu_thread_limit": 40,
                "active_cpu_threads": 0,
                "gpu_rows": [],
                "total_gpu_slots": 0,
                "followups": {},
                "active_sessions": [
                    {
                        "session_id": "session-active-001",
                        "mode": "continuous",
                        "enabled": True,
                        "default": True,
                        "phase": "execution",
                        "last_signal": "EXECUTION_READY",
                        "followup_type": "continuous_session_reminder",
                        "next_resume_at": "2026-04-25T18:00:00+08:00",
                        "workdir": "/tmp/project",
                    }
                ],
                "pending_feedback_count": 0,
                "enriched_states": [],
                "process_hints": [],
                "process_panel_mode": "off",
            }

            view = build_dashboard_view_from_snapshot(config, snapshot, limit=20, width=140, height=30)
            lines = "\n".join(dashboard_lines_from_view(view, width=140))

            self.assertIn("Active Sessions", lines)
            self.assertIn("session-active-001", lines)
            self.assertIn("mode=continuous/continuous", lines)


if __name__ == "__main__":
    unittest.main()
