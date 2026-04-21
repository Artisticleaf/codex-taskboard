import argparse
import curses
import io
import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import (
    AppConfig,
    LOCAL_MICROSTEP_BATCH_SIGNAL,
    DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS,
    DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS,
    CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON,
    CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
    PARKED_IDLE_SIGNAL,
    WAITING_ON_FEEDBACK_SIGNAL,
    WAITING_ON_LIVE_TASK_SIGNAL,
    build_dashboard_view_from_snapshot,
    command_continuous_mode,
    command_dashboard,
    command_human_guidance,
    dashboard_short_time,
    dependency_resolution,
    load_continuous_research_mode,
    load_human_guidance_mode,
    park_continuous_research_session,
    read_dashboard_input_key,
    suggested_project_history_log_dir,
    set_continuous_research_mode,
    sort_dashboard_tasks,
    write_task_state,
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


class DashboardTests(unittest.TestCase):
    def test_read_dashboard_input_key_parses_escape_arrow_sequence(self) -> None:
        class FakeWindow:
            def __init__(self, keys: list[int]) -> None:
                self._keys = list(keys)
                self.timeouts: list[int] = []
                self.nodelay_values: list[bool] = []

            def getch(self) -> int:
                return self._keys.pop(0) if self._keys else -1

            def timeout(self, value: int) -> None:
                self.timeouts.append(value)

            def nodelay(self, value: bool) -> None:
                self.nodelay_values.append(value)

        window = FakeWindow([27, ord("["), ord("B")])

        key = read_dashboard_input_key(window, poll_ms=50)

        self.assertEqual(key, 258)
        self.assertEqual(window.nodelay_values, [True, False])
        self.assertEqual(window.timeouts, [50])

    def test_dependency_resolution_uses_preindexed_latest_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {"depends_on": ["dep-a"]}
            indexed_states = {
                "dep-a": {
                    "task_id": "dep-a-run-2",
                    "task_key": "dep-a",
                    "status": "completed",
                    "require_signal_to_unblock": False,
                    "updated_at": "2026-03-19T00:00:02Z",
                }
            }

            with patch("codex_taskboard.cli.latest_task_state_for_key", side_effect=AssertionError("unexpected fallback lookup")):
                resolution, latest_states = dependency_resolution(config, spec, latest_states_by_key=indexed_states)

            self.assertEqual(resolution[0]["resolved_task_id"], "dep-a-run-2")
            self.assertEqual(latest_states["dep-a"]["task_id"], "dep-a-run-2")

    def test_sort_dashboard_tasks_uses_enriched_dependency_state_without_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            states = [
                {
                    "task_id": "ready-task",
                    "task_key": "ready-task",
                    "status": "queued",
                    "priority": 0,
                    "submitted_at": "2026-03-19T00:00:00Z",
                    "dependency_state": "ready",
                },
                {
                    "task_id": "waiting-task",
                    "task_key": "waiting-task",
                    "status": "queued",
                    "priority": 0,
                    "submitted_at": "2026-03-19T00:00:01Z",
                    "dependency_state": "waiting",
                },
            ]

            with patch("codex_taskboard.cli.unresolved_dependencies", side_effect=AssertionError("unexpected dependency rescan")):
                ordered = sort_dashboard_tasks(config, states, "queue")

            self.assertEqual([item["task_id"] for item in ordered], ["ready-task", "waiting-task"])

    def test_build_dashboard_view_uses_selected_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            states = [
                {
                    "task_id": "task-a",
                    "task_key": "task-a",
                    "status": "queued",
                    "phase": "eligible",
                    "priority": 10,
                    "agent_name": "agent-a",
                    "feedback_mode": "auto",
                    "updated_at": "2026-03-19T00:00:00Z",
                    "command": "python a.py",
                    "gpu_slots": 0,
                    "cpu_threads": 2,
                    "assigned_cpu_threads": 2,
                    "cpu_threads_mode": "fixed",
                    "cpu_threads_min": 2,
                    "cpu_threads_max": 2,
                    "submitted_at": "2026-03-19T00:00:00Z",
                },
                {
                    "task_id": "task-b",
                    "task_key": "task-b",
                    "status": "queued",
                    "phase": "eligible",
                    "priority": 5,
                    "agent_name": "agent-b",
                    "feedback_mode": "manual",
                    "updated_at": "2026-03-19T00:00:01Z",
                    "command": "python b.py",
                    "gpu_slots": 0,
                    "cpu_threads": 1,
                    "assigned_cpu_threads": 1,
                    "cpu_threads_mode": "fixed",
                    "cpu_threads_min": 1,
                    "cpu_threads_max": 1,
                    "submitted_at": "2026-03-19T00:00:01Z",
                },
            ]
            snapshot = {
                "counts": Counter({"queued": 2}),
                "running_live": 0,
                "active_states": [],
                "cpu_thread_limit": 40,
                "active_cpu_threads": 0,
                "gpu_rows": [],
                "total_gpu_slots": 0,
                "followups": {},
                "pending_feedback_count": 0,
                "enriched_states": states,
                "process_hints": [],
                "process_panel_mode": "off",
            }

            view = build_dashboard_view_from_snapshot(
                config,
                snapshot,
                10,
                width=120,
                height=30,
                selected_index=1,
            )

            self.assertEqual(view["selected_task_id"], "task-b")
            self.assertEqual(view["selected_index"], 1)

    def test_dashboard_short_time_converts_legacy_utc_to_beijing(self) -> None:
        self.assertEqual(dashboard_short_time("2026-03-19T00:00:00Z"), "03-19 08:00")

    def test_sort_dashboard_tasks_orders_mixed_timezone_timestamps_by_real_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            states = [
                {
                    "task_id": "task-earlier",
                    "task_key": "task-earlier",
                    "status": "queued",
                    "priority": 0,
                    "submitted_at": "2026-03-19T01:00:00+08:00",
                },
                {
                    "task_id": "task-later",
                    "task_key": "task-later",
                    "status": "queued",
                    "priority": 0,
                    "submitted_at": "2026-03-18T18:00:00Z",
                },
            ]

            ordered = sort_dashboard_tasks(config, states, "priority")

            self.assertEqual([item["task_id"] for item in ordered], ["task-earlier", "task-later"])

    def test_build_dashboard_view_reports_continuous_research_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-dashboard-001",
                updated_by="test",
                source="unit",
            )
            snapshot = {
                "counts": Counter(),
                "running_live": 0,
                "active_states": [],
                "cpu_thread_limit": 40,
                "active_cpu_threads": 0,
                "gpu_rows": [],
                "total_gpu_slots": 0,
                "followups": {},
                "pending_feedback_count": 0,
                "enriched_states": [],
                "process_hints": [],
                "process_panel_mode": "off",
            }

            view = build_dashboard_view_from_snapshot(
                config,
                snapshot,
                10,
                width=120,
                height=20,
            )

            self.assertEqual(view["continuous_research_mode"], "on")
            self.assertTrue(any("[continuous on]" in line for line in view["header_lines"]))

    def test_build_dashboard_view_reports_human_guidance_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            command_human_guidance(
                argparse.Namespace(
                    app_home=str(config.app_home),
                    codex_home=str(config.codex_home),
                    codex_bin="codex",
                    tmux_bin="tmux",
                    codex_session_id="session-dashboard-003",
                    action="on",
                    lease_seconds=900,
                    reason="manual steer",
                    json=False,
                )
            )
            snapshot = {
                "counts": Counter(),
                "running_live": 0,
                "active_states": [],
                "cpu_thread_limit": 8,
                "active_cpu_threads": 0,
                "gpu_rows": [],
                "total_gpu_slots": 0,
                "followups": {},
                "pending_feedback_count": 0,
                "enriched_states": [],
                "process_hints": [],
                "process_panel_mode": "off",
            }

            view = build_dashboard_view_from_snapshot(
                config,
                snapshot,
                10,
                width=120,
                height=20,
            )

            self.assertEqual(view["human_guidance_mode"], "on")
            self.assertTrue(any("[human-pause on]" in line for line in view["header_lines"]))

    def test_command_continuous_mode_toggle_persists_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            base_args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id="session-dashboard-001",
                json=False,
            )

            rc_on = command_continuous_mode(argparse.Namespace(**{**vars(base_args), "action": "on"}))
            rc_toggle = command_continuous_mode(argparse.Namespace(**{**vars(base_args), "action": "toggle"}))

            self.assertEqual(rc_on, 0)
            self.assertEqual(rc_toggle, 0)
            self.assertFalse(load_continuous_research_mode(config, codex_session_id="session-dashboard-001")["enabled"])

    def test_command_continuous_mode_bind_and_clear_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            base_args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id="session-dashboard-002",
                json=False,
            )

            rc_bind = command_continuous_mode(argparse.Namespace(**{**vars(base_args), "action": "bind"}))
            rc_on = command_continuous_mode(argparse.Namespace(**{**vars(base_args), "action": "on"}))
            rc_clear = command_continuous_mode(argparse.Namespace(**{**vars(base_args), "action": "clear-session"}))

            self.assertEqual(rc_bind, 0)
            self.assertEqual(rc_on, 0)
            self.assertEqual(rc_clear, 0)
            payload = load_continuous_research_mode(config, codex_session_id="session-dashboard-002")
            self.assertFalse(payload["enabled"])
            self.assertNotIn("session-dashboard-002", payload["sessions"])

    def test_command_continuous_mode_returns_error_on_persistence_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id="session-dashboard-003",
                action="off",
                json=False,
            )

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "codex_taskboard.cli.set_continuous_research_mode",
                side_effect=RuntimeError("continuous-mode persistence mismatch: expected enabled=False, got True"),
            ), patch("sys.stderr", new_callable=io.StringIO) as stderr:
                rc = command_continuous_mode(args)

            self.assertEqual(rc, 1)
            self.assertIn("continuous-mode persistence mismatch", stderr.getvalue())

    def test_command_continuous_mode_status_prefers_live_task_over_parked_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-dashboard-live-001"
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            task_dir = config.tasks_root / "live-queued-001"
            task_dir.mkdir(parents=True, exist_ok=True)
            write_task_state(
                config,
                "live-queued-001",
                {
                    "version": 1,
                    "task_id": "live-queued-001",
                    "task_key": "live-queued",
                    "status": "queued",
                    "submitted_at": "2026-04-11T00:00:00Z",
                    "updated_at": "2026-04-11T00:00:00Z",
                    "codex_session_id": session_id,
                    "proposal_path": "/tmp/PLAN.md",
                    "proposal_source": "explicit",
                    "project_history_file": "/tmp/HISTORY.md",
                    "project_history_file_source": "explicit",
                },
            )
            mode_path = config.app_home / "continuous_research_mode.json"
            payload = json.loads(mode_path.read_text(encoding="utf-8"))
            payload["sessions"][session_id]["waiting_state"] = ""
            payload["sessions"][session_id]["last_signal"] = PARKED_IDLE_SIGNAL
            mode_path.write_text(json.dumps(payload), encoding="utf-8")

            args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id=session_id,
                action="status",
                json=True,
            )
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = command_continuous_mode(args)

            self.assertEqual(rc, 0)
            status_payload = json.loads(stdout.getvalue())
            self.assertEqual(status_payload["live_task_count"], 1)
            self.assertEqual(status_payload["proposal_bound_live_task_count"], 1)
            self.assertEqual(status_payload["effective_wait_state"], "WAITING_ON_ASYNC")
            self.assertEqual(status_payload["automation_recommendation"], "wait_for_live_task")
            self.assertEqual(status_payload["target_session_state"]["waiting_state"], "WAITING_ON_ASYNC")
            self.assertEqual(status_payload["target_session_state"]["last_signal"], "WAITING_ON_ASYNC")
            self.assertEqual(status_payload["target_session_state"]["stored_last_signal"], PARKED_IDLE_SIGNAL)

    def test_command_continuous_mode_status_separates_awaiting_feedback_from_running_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-dashboard-feedback-001"
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            write_task_state(
                config,
                "feedback-completed-001",
                {
                    "version": 1,
                    "task_id": "feedback-completed-001",
                    "task_key": "feedback-completed",
                    "status": "completed",
                    "submitted_at": "2026-04-11T00:00:00Z",
                    "updated_at": "2026-04-11T00:10:00Z",
                    "feedback_mode": "manual",
                    "pending_feedback": True,
                    "codex_session_id": session_id,
                    "proposal_path": "/tmp/PLAN.md",
                    "proposal_source": "explicit",
                },
            )
            write_task_state(
                config,
                "feedback-failed-001",
                {
                    "version": 1,
                    "task_id": "feedback-failed-001",
                    "task_key": "feedback-failed",
                    "status": "failed",
                    "submitted_at": "2026-04-11T00:20:00Z",
                    "updated_at": "2026-04-11T00:30:00Z",
                    "feedback_mode": "manual",
                    "pending_feedback": True,
                    "codex_session_id": session_id,
                    "proposal_path": "/tmp/PLAN.md",
                    "proposal_source": "explicit",
                },
            )

            args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id=session_id,
                action="status",
                json=True,
            )
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = command_continuous_mode(args)

            self.assertEqual(rc, 0)
            status_payload = json.loads(stdout.getvalue())
            self.assertEqual(status_payload["running_task_count"], 0)
            self.assertEqual(status_payload["live_task_count"], 0)
            self.assertEqual(status_payload["awaiting_feedback_task_count"], 2)
            self.assertEqual(status_payload["effective_wait_state"], WAITING_ON_FEEDBACK_SIGNAL)
            self.assertEqual(status_payload["automation_recommendation"], "absorb_completed_receipt")
            self.assertEqual(status_payload["target_session_state"]["waiting_state"], WAITING_ON_FEEDBACK_SIGNAL)
            self.assertEqual(status_payload["target_session_state"]["last_signal"], WAITING_ON_FEEDBACK_SIGNAL)

    def test_command_continuous_mode_status_prefers_recent_local_next_action_over_parked_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-dashboard-next-action-001"
            history_path = Path(tmpdir) / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            log_dir = Path(tmpdir) / "HISTORY-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            created_at = (
                datetime.now(timezone.utc)
                .astimezone(timezone(timedelta(hours=8)))
                .replace(microsecond=0)
                .isoformat()
            )
            (log_dir / "20260411T010000Z-local-next-action.md").write_text(
                "\n".join(
                    [
                        "# local next action",
                        f"- created_at: `{created_at}`",
                        "## 5. Next bounded action",
                        "1. 在当前 proposal 中补一段方法学 guardrail。",
                        "2. 当前下一步仍保持 CPU-only。",
                        "3. 无需 GPU，无需 future callback，无需 live task。",
                    ]
                ),
                encoding="utf-8",
            )
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            task_dir = config.tasks_root / "anchor-task-001"
            task_dir.mkdir(parents=True, exist_ok=True)
            write_task_state(
                config,
                "anchor-task-001",
                {
                    "version": 1,
                    "task_id": "anchor-task-001",
                    "task_key": "anchor-task",
                    "status": "completed",
                    "submitted_at": "2026-04-11T00:00:00Z",
                    "updated_at": "2026-04-11T00:00:00Z",
                    "codex_session_id": session_id,
                    "proposal_path": "/tmp/PLAN.md",
                    "proposal_source": "explicit",
                    "project_history_file": str(history_path),
                    "project_history_file_source": "explicit",
                },
            )
            park_continuous_research_session(
                config,
                codex_session_id=session_id,
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token="evidence-next-action-001",
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )

            args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id=session_id,
                action="status",
                json=True,
            )
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = command_continuous_mode(args)

            self.assertEqual(rc, 0)
            status_payload = json.loads(stdout.getvalue())
            self.assertEqual(status_payload["effective_wait_state"], "")
            self.assertEqual(status_payload["effective_last_signal"], LOCAL_MICROSTEP_BATCH_SIGNAL)
            self.assertEqual(status_payload["automation_recommendation"], "continue_local_microstep")
            self.assertEqual(status_payload["target_session_state"]["waiting_state"], "")
            self.assertEqual(status_payload["target_session_state"]["last_signal"], LOCAL_MICROSTEP_BATCH_SIGNAL)
            self.assertEqual(status_payload["target_session_state"]["stored_waiting_state"], PARKED_IDLE_SIGNAL)
            self.assertEqual(status_payload["target_session_state"]["stored_last_signal"], PARKED_IDLE_SIGNAL)
            self.assertEqual(status_payload["recent_next_bounded_action"]["status"], "ready_local")

    def test_command_continuous_mode_status_prefers_successor_proposal_materialization_when_parked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-dashboard-bootstrap-001"
            history_path = Path(tmpdir) / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            log_dir = Path(suggested_project_history_log_dir(str(history_path)))
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "20260411T100000Z-bootstrap.md").write_text(
                "\n".join(
                    [
                        "- created_at: `2026-04-11T19:00:00+08:00`",
                        "",
                        "## Next bounded action",
                        "1. 起草新 family proposal 骨架，并准备最小 pilot gate。",
                    ]
                ),
                encoding="utf-8",
            )
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            write_task_state(
                config,
                "task-bootstrap-001",
                {
                    "task_id": "task-bootstrap-001",
                    "task_key": "task-bootstrap",
                    "status": "completed",
                    "submitted_at": "2026-04-11T00:00:00Z",
                    "updated_at": "2026-04-11T00:00:00Z",
                    "codex_session_id": session_id,
                    "proposal_path": "/tmp/PLAN.md",
                    "proposal_source": "explicit",
                    "project_history_file": str(history_path),
                    "project_history_file_source": "explicit",
                },
            )
            park_continuous_research_session(
                config,
                codex_session_id=session_id,
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token="evidence-bootstrap-001",
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )

            args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id=session_id,
                action="status",
                json=True,
            )
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = command_continuous_mode(args)

            self.assertEqual(rc, 0)
            status_payload = json.loads(stdout.getvalue())
            self.assertTrue(status_payload["proposal_bootstrap_ready"])
            self.assertEqual(status_payload["proposal_bootstrap_reason"], "新 family")
            self.assertEqual(status_payload["automation_recommendation"], "materialize_successor_proposal")
            self.assertTrue(status_payload["recent_next_bounded_action"]["proposal_bootstrap"])

    def test_command_continuous_mode_status_keeps_parked_reaffirmation_out_of_local_microstep(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-dashboard-parked-002"
            history_path = Path(tmpdir) / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            log_dir = Path(suggested_project_history_log_dir(str(history_path)))
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "20260411T100500Z-parked.md").write_text(
                "\n".join(
                    [
                        "- created_at: `2026-04-11T19:05:00+08:00`",
                        "",
                        "## Next bounded action",
                        "1. 保持 route-1 parked，并进入 PARKED_IDLE 等待新 evidence。",
                    ]
                ),
                encoding="utf-8",
            )
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            write_task_state(
                config,
                "task-parked-002",
                {
                    "task_id": "task-parked-002",
                    "task_key": "task-parked",
                    "status": "completed",
                    "submitted_at": "2026-04-11T00:00:00Z",
                    "updated_at": "2026-04-11T00:00:00Z",
                    "codex_session_id": session_id,
                    "proposal_path": "/tmp/PLAN.md",
                    "proposal_source": "explicit",
                    "project_history_file": str(history_path),
                    "project_history_file_source": "explicit",
                },
            )
            park_continuous_research_session(
                config,
                codex_session_id=session_id,
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token="evidence-parked-002",
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )

            args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id=session_id,
                action="status",
                json=True,
            )
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                rc = command_continuous_mode(args)

            self.assertEqual(rc, 0)
            status_payload = json.loads(stdout.getvalue())
            self.assertFalse(status_payload["proposal_bootstrap_ready"])
            self.assertEqual(status_payload["automation_recommendation"], "wait_for_external_evidence")
            self.assertEqual(status_payload["recent_next_bounded_action"]["status"], "parked")
            self.assertTrue(status_payload["recent_next_bounded_action"]["parked_reaffirmation"])

    def test_command_human_guidance_toggle_persists_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            base_args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id="session-dashboard-004",
                lease_seconds=900,
                reason="manual",
                json=False,
            )

            rc_on = command_human_guidance(argparse.Namespace(**{**vars(base_args), "action": "on"}))
            rc_toggle = command_human_guidance(argparse.Namespace(**{**vars(base_args), "action": "toggle"}))

            self.assertEqual(rc_on, 0)
            self.assertEqual(rc_toggle, 0)
            self.assertFalse(load_human_guidance_mode(config, codex_session_id="session-dashboard-004")["active"])

    def test_command_continuous_mode_status_reports_parked_watchdog_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-dashboard-parked-watchdog-001"
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            park_continuous_research_session(
                config,
                codex_session_id=session_id,
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token="evidence-001",
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )

            args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id=session_id,
                action="status",
                json=True,
            )
            stdout = io.StringIO()
            future_ts = 4_200_000_000 + DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS + 5
            with patch("codex_taskboard.cli.time.time", return_value=future_ts), patch("sys.stdout", stdout):
                rc = command_continuous_mode(args)

            self.assertEqual(rc, 0)
            status_payload = json.loads(stdout.getvalue())
            self.assertEqual(status_payload["effective_wait_state"], PARKED_IDLE_SIGNAL)
            self.assertTrue(status_payload["parked_watchdog_due"])
            self.assertEqual(
                status_payload["parked_watchdog_interval_seconds"],
                DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS,
            )
            self.assertEqual(status_payload["automation_recommendation"], "dispatch_parked_watchdog")
            self.assertGreater(status_payload["parked_watchdog_due_ts"], 0)
            self.assertTrue(status_payload["parked_watchdog_due_at"])
            self.assertTrue(
                status_payload["target_session_state"]["parked_wait_age_seconds"]
                >= DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS
            )

    def test_command_continuous_mode_status_reports_dynamic_parked_watchdog_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-dashboard-parked-watchdog-002"
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            park_continuous_research_session(
                config,
                codex_session_id=session_id,
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token="evidence-002",
                last_signal=PARKED_IDLE_SIGNAL,
                stable_idle_repeat_count=4,
                updated_by="test",
                source="unit",
            )

            args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id=session_id,
                action="status",
                json=True,
            )
            stdout = io.StringIO()
            future_ts = 4_200_000_000 + (DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS * 2) + 5
            with patch("codex_taskboard.cli.time.time", return_value=future_ts), patch("sys.stdout", stdout):
                rc = command_continuous_mode(args)

            self.assertEqual(rc, 0)
            status_payload = json.loads(stdout.getvalue())
            self.assertTrue(status_payload["parked_watchdog_due"])
            self.assertEqual(
                status_payload["parked_watchdog_interval_seconds"],
                DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS,
            )
            self.assertEqual(
                status_payload["target_session_state"]["parked_watchdog_interval_seconds"],
                DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS,
            )

    def test_command_continuous_mode_status_reports_active_followup_resume_fields_for_parked_watchdog(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-dashboard-parked-followup-001"
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            park_continuous_research_session(
                config,
                codex_session_id=session_id,
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token="evidence-003",
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            next_resume_ts = 4_200_000_000 + 120
            (config.followups_root / "continuous-session-reminder-active.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "continuous-session-reminder-active",
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": "task-dashboard-parked-followup-001",
                        "task_key": "task-dashboard-parked-followup",
                        "codex_session_id": session_id,
                        "reason": CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON,
                        "check_after_ts": next_resume_ts,
                        "interval_seconds": DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS,
                        "min_idle_seconds": 0,
                        "last_signal": PARKED_IDLE_SIGNAL,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                codex_session_id=session_id,
                action="status",
                json=True,
            )
            stdout = io.StringIO()
            with patch("codex_taskboard.cli.time.time", return_value=4_200_000_000), patch("sys.stdout", stdout):
                rc = command_continuous_mode(args)

            self.assertEqual(rc, 0)
            status_payload = json.loads(stdout.getvalue())
            self.assertEqual(status_payload["active_followup_reason"], CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON)
            self.assertEqual(status_payload["active_followup_type"], CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE)
            self.assertEqual(status_payload["active_followup_interval_seconds"], DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS)
            self.assertEqual(status_payload["active_followup_min_idle_seconds"], 0)
            self.assertEqual(status_payload["active_followup_last_signal"], PARKED_IDLE_SIGNAL)
            self.assertEqual(status_payload["next_actual_resume_ts"], next_resume_ts)
            self.assertEqual(status_payload["next_actual_resume_in_seconds"], 120)
            self.assertTrue(status_payload["next_actual_resume_at"])
            self.assertEqual(
                status_payload["target_session_state"]["active_followup_reason"],
                CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON,
            )
            self.assertEqual(status_payload["target_session_state"]["next_actual_resume_ts"], next_resume_ts)

    def test_command_dashboard_swallows_keyboard_interrupt_in_curses_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            args = type(
                "Args",
                (),
                {
                    "app_home": str(config.app_home),
                    "codex_home": str(config.codex_home),
                    "codex_bin": "codex",
                    "tmux_bin": "tmux",
                    "render_mode": "curses",
                    "once": False,
                    "limit": 10,
                    "refresh_seconds": 1.0,
                    "process_panel": "auto",
                },
            )()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "codex_taskboard.cli.curses.wrapper",
                side_effect=KeyboardInterrupt,
            ):
                rc = command_dashboard(args)

            self.assertEqual(rc, 130)

    def test_command_dashboard_swallows_keyboard_interrupt_in_plain_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            args = type(
                "Args",
                (),
                {
                    "app_home": str(config.app_home),
                    "codex_home": str(config.codex_home),
                    "codex_bin": "codex",
                    "tmux_bin": "tmux",
                    "render_mode": "plain",
                    "once": False,
                    "limit": 10,
                    "refresh_seconds": 1.0,
                    "process_panel": "auto",
                },
            )()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "codex_taskboard.cli.run_plain_dashboard",
                side_effect=KeyboardInterrupt,
            ):
                rc = command_dashboard(args)

            self.assertEqual(rc, 130)

    def test_command_dashboard_falls_back_to_plain_mode_on_curses_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            args = type(
                "Args",
                (),
                {
                    "app_home": str(config.app_home),
                    "codex_home": str(config.codex_home),
                    "codex_bin": "codex",
                    "tmux_bin": "tmux",
                    "render_mode": "curses",
                    "once": False,
                    "limit": 10,
                    "refresh_seconds": 1.0,
                    "process_panel": "auto",
                },
            )()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "codex_taskboard.cli.curses.wrapper",
                side_effect=curses.error("addnwstr() returned ERR"),
            ), patch("codex_taskboard.cli.run_plain_dashboard") as plain_dashboard:
                rc = command_dashboard(args)

            self.assertEqual(rc, 0)
            plain_dashboard.assert_called_once()


if __name__ == "__main__":
    unittest.main()
