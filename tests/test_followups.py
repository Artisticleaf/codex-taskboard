import argparse
import json
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import (
    ANALYZING_NEW_EVIDENCE_SIGNAL,
    AppConfig,
    CONTINUOUS_RESEARCH_LOCAL_FASTPATH_REPEAT_THRESHOLD,
    CONTINUOUS_RESEARCH_NEXT_ACTION_REASON,
    DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS,
    DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS,
    CONTINUOUS_RESEARCH_IDLE_REASON,
    CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON,
    CONTINUOUS_RESEARCH_NEW_TASK_SIGNAL,
    CONTINUOUS_RESEARCH_REASON,
    CONTINUOUS_RESEARCH_TRANSITION_FOLLOWUP_TYPE,
    CONTINUOUS_RESEARCH_TRANSITION_REASON,
    CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
    LOCAL_CONTINUE_NO_WAKE_SIGNAL,
    LOCAL_MICROSTEP_BATCH_SIGNAL,
    MATERIALS_READY_FOR_PROPOSAL_SIGNAL,
    PARKED_IDLE_SIGNAL,
    WAITING_ON_LIVE_TASK_SIGNAL,
    build_continuous_research_prompt,
    build_continuous_transition_prompt,
    build_materials_ready_for_proposal_prompt,
    build_parked_watchdog_prompt,
    build_protocol_self_check_repair_prompt,
    build_standard_followup_prompt,
    build_queued_feedback_batch_prompt,
    canonical_head_next_action_hint,
    command_followup_reconcile,
    command_followup_stop,
    continuous_session_reminder_schedule_params,
    continuous_session_followup_key_for,
    continuous_research_session_evidence_token,
    current_thread_info,
    ensure_continuous_research_session_reminders,
    extract_taskboard_signal,
    followup_key_for,
    followup_path,
    handle_task_feedback,
    load_continuous_research_mode,
    load_human_guidance_mode,
    load_task_state,
    park_continuous_research_session,
    process_followups,
    queue_feedback_resume,
    queued_feedback_key_for,
    recent_local_evidence_sweep_hint,
    recent_project_history_next_action_hint,
    resume_codex_session_with_prompt,
    resolve_requested_codex_session_id,
    schedule_waiting_on_async_watchdog,
    session_continuation_hint,
    set_continuous_research_mode,
    set_human_guidance_mode,
    should_inherit_recent_next_action,
    write_task_state,
    write_task_spec,
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


def write_state(config: AppConfig, task_id: str, **fields: object) -> None:
    task_dir = config.tasks_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "task_id": task_id,
        "task_key": fields.get("task_key", task_id),
        "status": "completed",
        "submitted_at": "2026-03-19T00:00:00Z",
        "updated_at": "2026-03-19T00:00:00Z",
        **fields,
    }
    write_task_state(config, task_id, payload)


class FollowupTests(unittest.TestCase):
    def test_extract_taskboard_signal_prefers_last_occurrence(self) -> None:
        text = "\n".join(
            [
                "不要把 `TASKBOARD_SIGNAL=NO_FURTHER_TASKS` 当作全局停机。",
                "当前继续本地短步骤。",
                "TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH",
            ]
        )

        self.assertEqual(extract_taskboard_signal(text), "LOCAL_MICROSTEP_BATCH")

    def test_should_inherit_recent_next_action_allows_extra_fastpath_repeat_before_parking(self) -> None:
        next_action_hint = {
            "controller_inherit_local": True,
            "action_hash": "action-hash-001",
        }

        self.assertTrue(
            should_inherit_recent_next_action(
                {
                    "next_action_hash": "action-hash-001",
                    "next_action_repeat_count": CONTINUOUS_RESEARCH_LOCAL_FASTPATH_REPEAT_THRESHOLD - 1,
                },
                next_action_hint,
            )
        )
        self.assertFalse(
            should_inherit_recent_next_action(
                {
                    "next_action_hash": "action-hash-001",
                    "next_action_repeat_count": CONTINUOUS_RESEARCH_LOCAL_FASTPATH_REPEAT_THRESHOLD,
                },
                next_action_hint,
            )
        )

    def test_extract_taskboard_signal_falls_back_to_final_signal_footer(self) -> None:
        text = "\n".join(
            [
                "TASKBOARD_PROTOCOL_ACK=TBP1",
                "CURRENT_STEP_CLASS=async_task",
                "TASKBOARD_SELF_CHECK=pass",
                "LIVE_TASK_STATUS=awaiting",
                "FINAL_SIGNAL=WAITING_ON_ASYNC",
            ]
        )

        self.assertEqual(extract_taskboard_signal(text), "WAITING_ON_ASYNC")

    def test_extract_taskboard_signal_normalizes_legacy_waiting_on_live_task(self) -> None:
        text = "TASKBOARD_SIGNAL=WAITING_ON_LIVE_TASK\n"

        self.assertEqual(extract_taskboard_signal(text), "WAITING_ON_ASYNC")

    def test_build_standard_followup_prompt_includes_footer_contract(self) -> None:
        prompt = build_standard_followup_prompt(
            {
                "proposal_path": "/tmp/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "project_history_file": "/tmp/HISTORY.md",
                "project_history_file_source": "explicit",
            },
            continuous_research_enabled=False,
        )

        self.assertIn("proposal_file: [/tmp/PLAN.md]", prompt)
        self.assertIn("Taskboard 操作方法：", prompt)
        self.assertIn("TASKBOARD_PROTOCOL_ACK=TBP1", prompt)
        self.assertIn("CURRENT_STEP_CLASS=inline_now|inline_batch|async_task|milestone_closeout|stop", prompt)
        self.assertIn("LIVE_TASK_STATUS=none|submitted|awaiting", prompt)
        self.assertNotIn("Taskboard protocol card `TBP1`", prompt)

    def test_build_standard_followup_prompt_reports_canonical_head_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text(
                "\n".join(
                    [
                        "<!-- TASKBOARD_CANONICAL_HEAD_BEGIN CH1 role=proposal -->",
                        "BIG_MAINLINE=contrastive_retention_guardrail_reweight_followthrough",
                        "SMALL_MAINLINE=qwen3-8b realization-gap repair",
                        "CURRENT_BOUNDARY=only repaired-owner style claims are allowed",
                        "NEXT_STEP=finish CPU-only audit before new GPU launch",
                        "<!-- TASKBOARD_CANONICAL_HEAD_END -->",
                    ]
                ),
                encoding="utf-8",
            )
            history_path.write_text("# history\n", encoding="utf-8")

            prompt = build_standard_followup_prompt(
                {
                    "proposal_path": str(proposal_path),
                    "proposal_source": "explicit",
                    "proposal_owner": True,
                    "project_history_file": str(history_path),
                    "project_history_file_source": "explicit",
                },
                continuous_research_enabled=False,
        )

        self.assertIn("proposal_head: status=ok", prompt)
        self.assertIn("history_head: status=missing_block", prompt)
        self.assertIn("BIG_MAINLINE、SMALL_MAINLINE、CURRENT_BOUNDARY、NEXT_STEP", prompt)

    def test_current_thread_info_prefers_unique_live_workdir_session_over_stale_codex_thread_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            write_state(
                config,
                "task-live-001",
                status="running",
                task_key="task-live",
                workdir=str(project_dir),
                agent_name="toposem-agent",
                codex_session_id="session-new",
            )

            payload = current_thread_info(
                config,
                environ={
                    "PWD": str(project_dir),
                    "CODEX_THREAD_ID": "session-old",
                    "CODEX_AGENT_NAME": "toposem-agent",
                },
            )

            assert payload is not None
            self.assertEqual(payload["current_codex_session_id"], "session-new")
            self.assertEqual(payload["resolved_from"], "taskboard_workdir_agent")
            self.assertEqual(payload["env_codex_session_id"], "session-old")
            self.assertEqual(payload["taskboard_workdir_session_id"], "session-new")
            self.assertTrue(payload["session_resolution_conflict"])
            self.assertTrue(payload["preferred_taskboard_over_env"])

    def test_resolve_requested_codex_session_id_prefers_unique_live_workdir_session_over_stale_codex_thread_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            write_state(
                config,
                "task-live-002",
                status="submitted",
                task_key="task-live",
                workdir=str(project_dir),
                agent_name="toposem-agent",
                codex_session_id="session-new",
            )

            resolved = resolve_requested_codex_session_id(
                "",
                feedback_mode="auto",
                environ={"CODEX_THREAD_ID": "session-old"},
                config=config,
                workdir=str(project_dir),
                agent_name="toposem-agent",
            )

            self.assertEqual(resolved, "session-new")

    def test_resolve_requested_codex_session_id_keeps_explicit_codex_session_id_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            write_state(
                config,
                "task-live-003",
                status="running",
                task_key="task-live",
                workdir=str(project_dir),
                agent_name="toposem-agent",
                codex_session_id="session-new",
            )

            resolved = resolve_requested_codex_session_id(
                "",
                feedback_mode="auto",
                environ={"CODEX_SESSION_ID": "session-explicit"},
                config=config,
                workdir=str(project_dir),
                agent_name="toposem-agent",
            )

            self.assertEqual(resolved, "session-explicit")

    def test_handle_task_feedback_coalesces_first_session_bound_result_into_queued_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-queue-001",
                task_key="task-queue",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
            )
            spec = {
                "task_id": "task-queue-001",
                "task_key": "task-queue",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
                "success_prompt": "",
                "failure_prompt": "",
                "task_note": "",
                "prompt_max_chars": 12000,
            }
            event = {
                "status": "completed",
                "event_path": "/tmp/task-queue-001-event.json",
                "feedback_data_path": "/tmp/task-queue-001-feedback.json",
                "command_log_path": "/tmp/task-queue-001.log",
                "runner_log_path": "/tmp/task-queue-001-runner.log",
                "failure_kind": "completed",
                "failure_summary": "The task finished successfully.",
                "duration_seconds": 12,
                "artifact_context": [],
                "log_tail": "",
            }

            with patch("codex_taskboard.cli.resume_codex_session") as mocked_resume:
                notification = handle_task_feedback(config, task_id="task-queue-001", spec=spec, event=event)

            mocked_resume.assert_not_called()
            self.assertTrue(notification["deferred"])
            self.assertTrue(notification["coalesced"])
            self.assertEqual(notification["deferred_reason"], "session_coalescing_window")
            self.assertEqual(notification["queue_depth"], 1)
            queued_key = queued_feedback_key_for(spec)
            queued_payload = json.loads(followup_path(config, queued_key).read_text(encoding="utf-8"))
            self.assertEqual(queued_payload["followup_type"], "queued_feedback_resume")
            self.assertEqual(queued_payload["proposal_path"], "/home/Awei/project/PLAN.md")
            self.assertTrue(queued_payload["proposal_owner"])
            self.assertEqual(len(queued_payload["queued_notifications"]), 1)
            state = load_task_state(config, "task-queue-001")
            self.assertTrue(state["pending_feedback"])
            self.assertEqual(state["followup_status"], "scheduled")
            self.assertEqual(state["followup_last_action"], "queued_feedback_resume:session_coalescing_window")
            self.assertEqual(state["notification_summary"]["deferred_reason"], "session_coalescing_window")

    def test_handle_task_feedback_appends_second_session_bound_result_to_existing_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-queue-a-001",
                task_key="task-queue-a",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
            )
            write_state(
                config,
                "task-queue-b-001",
                task_key="task-queue-b",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
            )
            spec = {
                "task_id": "task-queue-a-001",
                "task_key": "task-queue-a",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
            }
            queue_feedback_resume(
                config,
                task_id="task-queue-a-001",
                spec=spec,
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-queue-a-event.json",
                    "feedback_data_path": "/tmp/task-queue-a-feedback.json",
                    "command_log_path": "/tmp/task-queue-a.log",
                    "runner_log_path": "/tmp/task-queue-a-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task A done.",
                    "duration_seconds": 6,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="session_coalescing_window",
                min_idle_seconds=30,
            )

            with patch("codex_taskboard.cli.resume_codex_session") as mocked_resume:
                notification = handle_task_feedback(
                    config,
                    task_id="task-queue-b-001",
                    spec={**spec, "task_id": "task-queue-b-001", "task_key": "task-queue-b"},
                    event={
                        "status": "completed",
                        "event_path": "/tmp/task-queue-b-event.json",
                        "feedback_data_path": "/tmp/task-queue-b-feedback.json",
                        "command_log_path": "/tmp/task-queue-b.log",
                        "runner_log_path": "/tmp/task-queue-b-runner.log",
                        "failure_kind": "completed",
                        "failure_summary": "Task B done.",
                        "duration_seconds": 7,
                        "artifact_context": [],
                        "log_tail": "",
                    },
                )

            mocked_resume.assert_not_called()
            self.assertTrue(notification["deferred"])
            self.assertEqual(notification["deferred_reason"], "queue_already_open")
            self.assertEqual(notification["queue_depth"], 2)
            queued_key = queued_feedback_key_for(spec)
            queued_payload = json.loads(followup_path(config, queued_key).read_text(encoding="utf-8"))
            self.assertEqual(len(queued_payload["queued_notifications"]), 2)
            state = load_task_state(config, "task-queue-b-001")
            self.assertTrue(state["pending_feedback"])
            self.assertEqual(state["followup_last_action"], "queued_feedback_resume:queue_already_open")

    def test_handle_task_feedback_honors_stop_signal_without_scheduling_followup_when_not_session_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-stop-001",
                task_key="task-stop",
                feedback_mode="auto",
                agent_name="toposem-agent",
            )
            spec = {
                "task_id": "task-stop-001",
                "task_key": "task-stop",
                "codex_session_id": "",
                "agent_name": "toposem-agent",
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
            }
            event = {"status": "completed"}

            with patch(
                "codex_taskboard.cli.resume_codex_session",
                return_value={
                    "ok": True,
                    "taskboard_signal": "NO_FURTHER_TASKS",
                    "resumed_session_id": "",
                    "used_fallback_clone": False,
                    "finished_at": "2026-03-19T00:10:00Z",
                },
            ), patch("codex_taskboard.cli.schedule_followup") as mocked_schedule:
                notification = handle_task_feedback(config, task_id="task-stop-001", spec=spec, event=event)

            self.assertEqual(notification["taskboard_signal"], "NO_FURTHER_TASKS")
            mocked_schedule.assert_not_called()
            state = load_task_state(config, "task-stop-001")
            self.assertEqual(state["notification_signal"], "NO_FURTHER_TASKS")
            self.assertEqual(state["followup_status"], "stopped")
            self.assertEqual(state["followup_last_signal"], "NO_FURTHER_TASKS")
            self.assertEqual(state["followup_last_action"], "resolved_notification_signal_stop")

    def test_handle_task_feedback_session_bound_continuous_mode_still_coalesces_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-continue-001",
                task_key="task-continue",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
            )
            spec = {
                "task_id": "task-continue-001",
                "task_key": "task-continue",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
            }
            event = {
                "status": "completed",
                "event_path": "/tmp/task-continue-001-event.json",
                "feedback_data_path": "/tmp/task-continue-001-feedback.json",
                "command_log_path": "/tmp/task-continue-001.log",
                "runner_log_path": "/tmp/task-continue-001-runner.log",
                "failure_kind": "completed",
                "failure_summary": "Task finished successfully.",
                "duration_seconds": 8,
                "artifact_context": [],
                "log_tail": "",
            }

            with patch("codex_taskboard.cli.resume_codex_session") as mocked_resume:
                notification = handle_task_feedback(config, task_id="task-continue-001", spec=spec, event=event)

            mocked_resume.assert_not_called()
            self.assertTrue(notification["deferred"])
            self.assertEqual(notification["deferred_reason"], "session_coalescing_window")
            state = load_task_state(config, "task-continue-001")
            self.assertTrue(state["pending_feedback"])
            self.assertEqual(state["followup_status"], "scheduled")
            self.assertEqual(state["followup_last_action"], "queued_feedback_resume:session_coalescing_window")
            self.assertFalse(followup_path(config, followup_key_for(spec)).exists())

    def test_handle_task_feedback_without_agent_name_when_session_bound_still_coalesces(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-no-agent-001",
                task_key="task-no-agent",
                feedback_mode="auto",
                codex_session_id="session-topo-001",
            )
            spec = {
                "task_id": "task-no-agent-001",
                "task_key": "task-no-agent",
                "codex_session_id": "session-topo-001",
                "agent_name": "",
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
            }
            event = {
                "status": "completed",
                "event_path": "/tmp/task-no-agent-001-event.json",
                "feedback_data_path": "/tmp/task-no-agent-001-feedback.json",
                "command_log_path": "/tmp/task-no-agent-001.log",
                "runner_log_path": "/tmp/task-no-agent-001-runner.log",
                "failure_kind": "completed",
                "failure_summary": "Task finished successfully.",
                "duration_seconds": 6,
                "artifact_context": [],
                "log_tail": "",
            }

            with patch("codex_taskboard.cli.resume_codex_session") as mocked_resume:
                notification = handle_task_feedback(config, task_id="task-no-agent-001", spec=spec, event=event)

            mocked_resume.assert_not_called()
            self.assertTrue(notification["deferred"])
            self.assertEqual(notification["deferred_reason"], "session_coalescing_window")
            self.assertTrue(followup_path(config, queued_feedback_key_for(spec)).exists())
            state = load_task_state(config, "task-no-agent-001")
            self.assertEqual(state["followup_status"], "scheduled")
            self.assertEqual(state["followup_last_action"], "queued_feedback_resume:session_coalescing_window")

    def test_handle_task_feedback_continuous_mode_without_agent_name_still_coalesces(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-no-agent-continue-001",
                task_key="task-no-agent-continue",
                feedback_mode="auto",
                codex_session_id="session-topo-001",
            )
            spec = {
                "task_id": "task-no-agent-continue-001",
                "task_key": "task-no-agent-continue",
                "codex_session_id": "session-topo-001",
                "agent_name": "",
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
            }
            event = {
                "status": "completed",
                "event_path": "/tmp/task-no-agent-continue-001-event.json",
                "feedback_data_path": "/tmp/task-no-agent-continue-001-feedback.json",
                "command_log_path": "/tmp/task-no-agent-continue-001.log",
                "runner_log_path": "/tmp/task-no-agent-continue-001-runner.log",
                "failure_kind": "completed",
                "failure_summary": "Task finished successfully.",
                "duration_seconds": 9,
                "artifact_context": [],
                "log_tail": "",
            }

            with patch("codex_taskboard.cli.resume_codex_session") as mocked_resume:
                notification = handle_task_feedback(config, task_id="task-no-agent-continue-001", spec=spec, event=event)

            mocked_resume.assert_not_called()
            self.assertTrue(notification["deferred"])
            self.assertEqual(notification["deferred_reason"], "session_coalescing_window")
            self.assertTrue(followup_path(config, queued_feedback_key_for(spec)).exists())
            state = load_task_state(config, "task-no-agent-continue-001")
            self.assertTrue(state["pending_feedback"])
            self.assertEqual(state["followup_status"], "scheduled")
            self.assertEqual(state["followup_last_action"], "queued_feedback_resume:session_coalescing_window")

    def test_process_followups_stops_all_matching_followups_for_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "task-a", task_key="task-a", agent_name="toposem-agent", codex_session_id="session-topo-001")
            write_state(config, "task-b", task_key="task-b", agent_name="toposem-agent", codex_session_id="session-topo-001")

            followup_a = {
                "version": 1,
                "followup_key": "followup-a",
                "task_id": "task-a",
                "task_key": "task-a",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "workdir": "/home/Awei",
                "reason": "no_new_task_after_feedback",
                "created_at": "2026-03-19T00:00:00Z",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
            }
            followup_b = {
                "version": 1,
                "followup_key": "followup-b",
                "task_id": "task-b",
                "task_key": "task-b",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "workdir": "/home/Awei",
                "reason": "no_new_task_after_feedback",
                "created_at": "2026-03-19T00:00:00Z",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
            }
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "followup-a").write_text(json.dumps(followup_a), encoding="utf-8")
            followup_path(config, "followup-b").write_text(json.dumps(followup_b), encoding="utf-8")

            with patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "TASKBOARD_SIGNAL=NO_FURTHER_TASKS\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ), patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0):
                processed = process_followups(config)

            self.assertTrue(any(item.get("action") == "resolved_signal_stop" for item in processed))
            self.assertFalse(followup_path(config, "followup-a").exists())
            self.assertFalse(followup_path(config, "followup-b").exists())
            state_a = load_task_state(config, "task-a")
            state_b = load_task_state(config, "task-b")
            self.assertEqual(state_a["followup_status"], "stopped")
            self.assertEqual(state_b["followup_status"], "stopped")
            self.assertEqual(state_a["followup_last_signal"], "NO_FURTHER_TASKS")
            self.assertEqual(state_b["followup_last_signal"], "NO_FURTHER_TASKS")

    def test_process_followups_delivers_queued_feedback_in_one_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "task-a", task_key="task-a", feedback_mode="auto", agent_name="toposem-agent", codex_session_id="session-topo-001")
            write_state(config, "task-b", task_key="task-b", feedback_mode="auto", agent_name="toposem-agent", codex_session_id="session-topo-001")
            base_spec = {
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
                "success_prompt": "",
                "failure_prompt": "",
                "task_note": "",
                "prompt_max_chars": 12000,
            }
            queue_feedback_resume(
                config,
                task_id="task-a",
                spec={**base_spec, "task_id": "task-a", "task_key": "task-a"},
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-a-event.json",
                    "feedback_data_path": "/tmp/task-a-feedback.json",
                    "command_log_path": "/tmp/task-a.log",
                    "runner_log_path": "/tmp/task-a-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task A done.",
                    "duration_seconds": 5,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="recent_activity",
                min_idle_seconds=1,
            )
            queue_feedback_resume(
                config,
                task_id="task-b",
                spec={**base_spec, "task_id": "task-b", "task_key": "task-b"},
                event={
                    "status": "terminated",
                    "event_path": "/tmp/task-b-event.json",
                    "feedback_data_path": "/tmp/task-b-feedback.json",
                    "command_log_path": "/tmp/task-b.log",
                    "runner_log_path": "/tmp/task-b-runner.log",
                    "failure_kind": "external_termination",
                    "failure_summary": "Task B got SIGHUP.",
                    "duration_seconds": 7,
                    "artifact_context": [],
                    "log_tail": "SIGHUP",
                },
                reason="queue_already_open",
                min_idle_seconds=1,
            )
            queued_key = queued_feedback_key_for({**base_spec, "task_id": "task-b", "task_key": "task-b"})
            queued_payload = json.loads(followup_path(config, queued_key).read_text(encoding="utf-8"))
            queued_payload["check_after_ts"] = 0
            followup_path(config, queued_key).write_text(json.dumps(queued_payload), encoding="utf-8")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "\n".join(
                        [
                            "TASKBOARD_PROTOCOL_ACK=TBP1",
                            "CURRENT_STEP_CLASS=milestone_closeout",
                            "TASKBOARD_SELF_CHECK=pass",
                            "LIVE_TASK_STATUS=none",
                            "FINAL_SIGNAL=none",
                        ]
                    ),
                    "taskboard_protocol": {
                        "ack": "TBP1",
                        "step_class": "milestone_closeout",
                        "self_check": "pass",
                        "live_task_status": "none",
                        "final_signal": "none",
                        "valid": True,
                    },
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ) as mocked_resume, patch("codex_taskboard.cli.schedule_followup") as mocked_schedule:
                processed = process_followups(config)

            mocked_resume.assert_called_once()
            delivered_prompt = mocked_resume.call_args.args[2]
            self.assertIn("queued_update_count: 2", delivered_prompt)
            self.assertIn("合并任务更新 1/2", delivered_prompt)
            self.assertIn("合并任务更新 2/2", delivered_prompt)
            self.assertIn("feedback_data_file: [/tmp/task-a-feedback.json]", delivered_prompt)
            self.assertIn("feedback_data_file: [/tmp/task-b-feedback.json]", delivered_prompt)
            self.assertIn("proposal_file: [/home/Awei/project/PLAN.md]", delivered_prompt)
            self.assertEqual(delivered_prompt.count("proposal_file: [/home/Awei/project/PLAN.md]"), 1)
            self.assertEqual(delivered_prompt.count("安全说明："), 1)
            self.assertNotIn("proposal binding guard：", delivered_prompt)
            mocked_schedule.assert_called_once()
            self.assertFalse(followup_path(config, queued_key).exists())
            state_a = load_task_state(config, "task-a")
            state_b = load_task_state(config, "task-b")
            self.assertFalse(state_a["pending_feedback"])
            self.assertFalse(state_b["pending_feedback"])
            self.assertEqual(state_a["followup_status"], "resolved")
            self.assertEqual(state_b["followup_status"], "resolved")
            self.assertTrue(any(item.get("action") == "queued_feedback_delivered" for item in processed))

    def test_process_followups_batch_falls_back_to_legacy_prompt_when_snapshot_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "task-legacy", task_key="task-legacy", feedback_mode="auto", agent_name="toposem-agent", codex_session_id="session-topo-001")
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup = {
                "version": 1,
                "followup_key": "legacy-batch",
                "followup_type": "queued_feedback_resume",
                "task_id": "task-legacy",
                "task_key": "task-legacy",
                "execution_mode": "shell",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "workdir": "/home/Awei/project",
                "reason": "recent_activity",
                "created_at": "2026-03-19T00:00:00Z",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
                "queued_notifications": [
                    {
                        "task_id": "task-legacy",
                        "task_key": "task-legacy",
                        "status": "completed",
                        "prompt": "LEGACY QUEUED PROMPT\nfeedback_data_file: /tmp/task-legacy-feedback.json",
                    }
                ],
            }
            followup_path(config, "legacy-batch").write_text(json.dumps(followup), encoding="utf-8")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "\n".join(
                        [
                            "TASKBOARD_PROTOCOL_ACK=TBP1",
                            "CURRENT_STEP_CLASS=milestone_closeout",
                            "TASKBOARD_SELF_CHECK=pass",
                            "LIVE_TASK_STATUS=none",
                            "FINAL_SIGNAL=none",
                        ]
                    ),
                    "taskboard_protocol": {
                        "ack": "TBP1",
                        "step_class": "milestone_closeout",
                        "self_check": "pass",
                        "live_task_status": "none",
                        "final_signal": "none",
                        "valid": True,
                    },
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ) as mocked_resume, patch("codex_taskboard.cli.schedule_followup") as mocked_schedule:
                processed = process_followups(config)

            delivered_prompt = mocked_resume.call_args.args[2]
            self.assertIn("LEGACY QUEUED PROMPT", delivered_prompt)
            self.assertTrue(any(item.get("action") == "queued_feedback_delivered" for item in processed))
            mocked_schedule.assert_called_once()

    def test_process_followups_resolves_queued_feedback_when_newer_task_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-old",
                task_key="task-old",
                status="completed",
                feedback_mode="auto",
                pending_feedback=True,
                followup_status="scheduled",
                followup_last_action="queued_feedback_resume:session_coalescing_window",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                submitted_at="2026-03-19T00:00:00Z",
                updated_at="2026-03-19T00:00:00Z",
            )
            write_state(
                config,
                "task-new",
                task_key="task-new",
                status="running",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                submitted_at="2026-03-19T01:00:00Z",
                updated_at="2026-03-19T01:00:00Z",
            )
            followup = {
                "version": 1,
                "followup_key": "queued-old",
                "followup_type": "queued_feedback_resume",
                "task_id": "task-old",
                "task_key": "task-old",
                "execution_mode": "shell",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "workdir": "/home/Awei/project",
                "reason": "session_coalescing_window",
                "created_at": "2026-03-19T00:30:00Z",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
                "queued_notifications": [
                    {
                        "task_id": "task-old",
                        "task_key": "task-old",
                        "status": "completed",
                        "prompt": "QUEUED PROMPT",
                    }
                ],
            }
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "queued-old").write_text(json.dumps(followup), encoding="utf-8")

            with patch("codex_taskboard.cli.resume_codex_session_with_prompt") as mocked_resume:
                processed = process_followups(config)

            mocked_resume.assert_not_called()
            self.assertFalse(followup_path(config, "queued-old").exists())
            state = load_task_state(config, "task-old")
            self.assertEqual(state["followup_status"], "resolved")
            self.assertEqual(state["followup_last_action"], "resolved_new_task_seen")
            self.assertFalse(state["pending_feedback"])
            self.assertTrue(any(item.get("action") == "resolved_new_task_seen" for item in processed))

    def test_process_followups_resolves_queued_feedback_before_rebind_to_newer_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-old",
                task_key="task-old",
                status="completed",
                feedback_mode="auto",
                pending_feedback=True,
                followup_status="scheduled",
                followup_last_action="queued_feedback_resume:session_coalescing_window",
                codex_session_id="session-topo-001",
                submitted_at="2026-03-19T00:00:00Z",
                updated_at="2026-03-19T00:00:00Z",
            )
            write_state(
                config,
                "task-new",
                task_key="task-new",
                status="running",
                feedback_mode="auto",
                codex_session_id="session-topo-001",
                submitted_at="2026-03-19T01:00:00Z",
                updated_at="2026-03-19T01:00:00Z",
            )
            followup = {
                "version": 1,
                "followup_key": "queued-old",
                "followup_type": "queued_feedback_resume",
                "task_id": "task-old",
                "task_key": "task-old",
                "execution_mode": "shell",
                "codex_session_id": "session-topo-001",
                "agent_name": "",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "workdir": "/home/Awei/project",
                "reason": "session_coalescing_window",
                "created_at": "2026-03-19T00:30:00Z",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
                "queued_notifications": [
                    {
                        "task_id": "task-old",
                        "task_key": "task-old",
                        "status": "completed",
                        "prompt": "QUEUED PROMPT",
                    }
                ],
            }
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "queued-old").write_text(json.dumps(followup), encoding="utf-8")

            rebound_payload = dict(followup)
            rebound_payload.update({"task_id": "task-new", "task_key": "task-new"})
            with patch(
                "codex_taskboard.cli.rebind_followup_to_current_task",
                return_value=(rebound_payload, True, False),
            ) as mocked_rebind, patch("codex_taskboard.cli.resume_codex_session_with_prompt") as mocked_resume:
                processed = process_followups(config)

            mocked_rebind.assert_not_called()
            mocked_resume.assert_not_called()
            self.assertFalse(followup_path(config, "queued-old").exists())
            state = load_task_state(config, "task-old")
            self.assertEqual(state["followup_status"], "resolved")
            self.assertEqual(state["followup_last_action"], "resolved_new_task_seen")
            self.assertFalse(state["pending_feedback"])
            self.assertTrue(any(item.get("action") == "resolved_new_task_seen" for item in processed))

    def test_process_followups_converts_no_further_tasks_batch_into_continuous_followup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(config, "task-a", task_key="task-a", feedback_mode="auto", agent_name="toposem-agent", codex_session_id="session-topo-001")
            base_spec = {
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
                "success_prompt": "",
                "failure_prompt": "",
                "task_note": "",
                "prompt_max_chars": 12000,
            }
            queue_feedback_resume(
                config,
                task_id="task-a",
                spec={**base_spec, "task_id": "task-a", "task_key": "task-a"},
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-a-event.json",
                    "feedback_data_path": "/tmp/task-a-feedback.json",
                    "command_log_path": "/tmp/task-a.log",
                    "runner_log_path": "/tmp/task-a-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task A done.",
                    "duration_seconds": 5,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="recent_activity",
                min_idle_seconds=1,
            )
            queued_key = queued_feedback_key_for({**base_spec, "task_id": "task-a", "task_key": "task-a"})
            queued_payload = json.loads(followup_path(config, queued_key).read_text(encoding="utf-8"))
            queued_payload["check_after_ts"] = 0
            followup_path(config, queued_key).write_text(json.dumps(queued_payload), encoding="utf-8")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "TASKBOARD_SIGNAL=NO_FURTHER_TASKS\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                processed = process_followups(config)

            self.assertFalse(followup_path(config, queued_key).exists())
            rescheduled_path = followup_path(config, followup_key_for({**base_spec, "task_id": "task-a", "task_key": "task-a"}))
            self.assertTrue(rescheduled_path.exists())
            rescheduled = json.loads(rescheduled_path.read_text(encoding="utf-8"))
            self.assertEqual(rescheduled["reason"], CONTINUOUS_RESEARCH_TRANSITION_REASON)
            self.assertEqual(rescheduled["followup_type"], CONTINUOUS_RESEARCH_TRANSITION_FOLLOWUP_TYPE)
            state_a = load_task_state(config, "task-a")
            self.assertFalse(state_a["pending_feedback"])
            self.assertEqual(state_a["followup_status"], "scheduled")
            self.assertEqual(state_a["followup_last_signal"], "NO_FURTHER_TASKS")
            self.assertEqual(state_a["followup_last_action"], f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}")
            self.assertTrue(any(item.get("action") == "continuous_transition_scheduled" for item in processed))

    def test_process_followups_reschedules_local_microstep_batch_after_queued_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "task-local-001", task_key="task-local", feedback_mode="auto", agent_name="toposem-agent", codex_session_id="session-topo-001")
            base_spec = {
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python audit.py",
                "execution_mode": "shell",
                "success_prompt": "",
                "failure_prompt": "",
                "task_note": "",
                "prompt_max_chars": 12000,
            }
            task_spec = {**base_spec, "task_id": "task-local-001", "task_key": "task-local"}
            queue_feedback_resume(
                config,
                task_id="task-local-001",
                spec=task_spec,
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-local-event.json",
                    "feedback_data_path": "/tmp/task-local-feedback.json",
                    "command_log_path": "/tmp/task-local.log",
                    "runner_log_path": "/tmp/task-local-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task local done.",
                    "duration_seconds": 5,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="recent_activity",
                min_idle_seconds=1,
            )
            queued_key = queued_feedback_key_for(task_spec)
            queued_payload = json.loads(followup_path(config, queued_key).read_text(encoding="utf-8"))
            queued_payload["check_after_ts"] = 0
            followup_path(config, queued_key).write_text(json.dumps(queued_payload), encoding="utf-8")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                processed = process_followups(config)

            self.assertFalse(followup_path(config, queued_key).exists())
            rebound_key = followup_key_for(task_spec)
            self.assertTrue(followup_path(config, rebound_key).exists())
            rebound_payload = json.loads(followup_path(config, rebound_key).read_text(encoding="utf-8"))
            self.assertEqual(rebound_payload["reason"], "local_microstep_batch")
            state = load_task_state(config, "task-local-001")
            self.assertFalse(state["pending_feedback"])
            self.assertEqual(state["session_flow_state"], "local_active")
            self.assertEqual(state["followup_status"], "scheduled")
            self.assertEqual(state["followup_last_signal"], "LOCAL_MICROSTEP_BATCH")
            self.assertEqual(state["followup_last_action"], "scheduled:local_microstep_batch")
            self.assertTrue(any(item.get("action") == "queued_feedback_delivered_local_microstep" for item in processed))

    def test_process_followups_materials_ready_for_proposal_schedules_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            config.followups_root.mkdir(parents=True, exist_ok=True)
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text("# proposal\n", encoding="utf-8")
            history_path.write_text("# history\n", encoding="utf-8")
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-proposal-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-proposal-001",
                task_key="task-proposal",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-proposal-001",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
                workdir="/home/Awei/project",
            )
            write_task_spec(
                config,
                "task-proposal-001",
                {
                    "task_id": "task-proposal-001",
                    "task_key": "task-proposal",
                    "feedback_mode": "auto",
                    "agent_name": "toposem-agent",
                    "codex_session_id": "session-proposal-001",
                    "proposal_path": str(proposal_path),
                    "proposal_source": "explicit",
                    "proposal_owner": True,
                    "project_history_file": str(history_path),
                    "project_history_file_source": "explicit",
                    "workdir": "/home/Awei/project",
                    "command": "python plan.py",
                    "execution_mode": "shell",
                },
            )
            followup_path(config, "followup-proposal-001").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "followup-proposal-001",
                        "task_id": "task-proposal-001",
                        "task_key": "task-proposal",
                        "codex_session_id": "session-proposal-001",
                        "agent_name": "toposem-agent",
                        "proposal_path": str(proposal_path),
                        "proposal_source": "explicit",
                        "proposal_owner": True,
                        "project_history_file": str(history_path),
                        "project_history_file_source": "explicit",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                        "continuous_research_origin": True,
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-proposal-001",
                    "resumed_session_id": "session-proposal-001",
                    "used_fallback_clone": False,
                    "last_message_text": f"TASKBOARD_SIGNAL={MATERIALS_READY_FOR_PROPOSAL_SIGNAL}\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                processed = process_followups(config)

            target_followup_key = followup_key_for(
                {
                    "task_id": "task-proposal-001",
                    "task_key": "task-proposal",
                    "codex_session_id": "session-proposal-001",
                    "agent_name": "toposem-agent",
                    "proposal_path": str(proposal_path),
                    "workdir": "/home/Awei/project",
                }
            )
            self.assertFalse(followup_path(config, "followup-proposal-001").exists())
            payload = json.loads(followup_path(config, target_followup_key).read_text(encoding="utf-8"))
            self.assertEqual(payload["reason"], CONTINUOUS_RESEARCH_TRANSITION_REASON)
            self.assertEqual(payload["followup_type"], CONTINUOUS_RESEARCH_TRANSITION_FOLLOWUP_TYPE)
            self.assertEqual(payload["last_signal"], MATERIALS_READY_FOR_PROPOSAL_SIGNAL)
            state = load_task_state(config, "task-proposal-001")
            self.assertEqual(state["session_flow_state"], "proposal_materialization")
            self.assertEqual(state["followup_status"], "scheduled")
            self.assertEqual(state["followup_last_signal"], MATERIALS_READY_FOR_PROPOSAL_SIGNAL)
            self.assertEqual(state["followup_last_action"], f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}")
            self.assertTrue(any(item.get("action") == "proposal_materialization_followup_scheduled" for item in processed))

    def test_process_followups_resolves_waiting_on_async_when_newer_task_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-await-001",
                task_key="task-await",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                submitted_at="2026-03-19T00:00:00Z",
                updated_at="2026-03-19T00:00:00Z",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup = {
                "version": 1,
                "followup_key": "followup-await",
                "task_id": "task-await-001",
                "task_key": "task-await",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "workdir": "/home/Awei/project",
                "reason": "no_new_task_after_feedback",
                "created_at": "2026-03-19T00:00:00Z",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
            }
            followup_path(config, "followup-await").write_text(json.dumps(followup), encoding="utf-8")

            def fake_resume(*args: object, **kwargs: object) -> dict[str, object]:
                write_state(
                    config,
                    "task-await-002",
                    status="submitted",
                    task_key="task-await-next",
                    feedback_mode="auto",
                    agent_name="toposem-agent",
                    codex_session_id="session-topo-001",
                    submitted_at="2026-03-19T00:05:00Z",
                    updated_at="2026-03-19T00:05:00Z",
                )
                return {
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "TASKBOARD_SIGNAL=WAITING_ON_ASYNC\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                }

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                side_effect=fake_resume,
            ):
                processed = process_followups(config)

            self.assertFalse(followup_path(config, "followup-await").exists())
            self.assertEqual(list(config.followups_root.glob("*.json")), [])
            state = load_task_state(config, "task-await-001")
            self.assertEqual(state["session_flow_state"], "awaiting_async")
            self.assertEqual(state["followup_status"], "resolved")
            self.assertEqual(state["followup_last_signal"], "WAITING_ON_ASYNC")
            self.assertEqual(state["followup_last_action"], "resolved_waiting_on_async_newer_task")
            self.assertTrue(any(item.get("action") == "resolved_waiting_on_async_newer_task" for item in processed))

    def test_process_followups_keeps_queued_feedback_pending_when_resume_is_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "task-a", task_key="task-a", feedback_mode="auto", agent_name="toposem-agent", codex_session_id="session-topo-001")
            base_spec = {
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
                "success_prompt": "",
                "failure_prompt": "",
                "task_note": "",
                "prompt_max_chars": 12000,
            }
            queue_feedback_resume(
                config,
                task_id="task-a",
                spec={**base_spec, "task_id": "task-a", "task_key": "task-a"},
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-a-event.json",
                    "feedback_data_path": "/tmp/task-a-feedback.json",
                    "command_log_path": "/tmp/task-a.log",
                    "runner_log_path": "/tmp/task-a-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task A done.",
                    "duration_seconds": 5,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="recent_activity",
                min_idle_seconds=1,
            )
            queued_key = queued_feedback_key_for({**base_spec, "task_id": "task-a", "task_key": "task-a"})
            queued_payload = json.loads(followup_path(config, queued_key).read_text(encoding="utf-8"))
            queued_payload["check_after_ts"] = 0
            followup_path(config, queued_key).write_text(json.dumps(queued_payload), encoding="utf-8")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "attempted": False,
                    "ok": False,
                    "deferred": True,
                    "deferred_reason": "session_locked",
                    "retry_after_seconds": 45,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                processed = process_followups(config)

            self.assertTrue(followup_path(config, queued_key).exists())
            queued_payload = json.loads(followup_path(config, queued_key).read_text(encoding="utf-8"))
            self.assertEqual(queued_payload["last_action"], "deferred:session_locked")
            state_a = load_task_state(config, "task-a")
            self.assertTrue(state_a["pending_feedback"])
            self.assertEqual(state_a["followup_status"], "scheduled")
            self.assertEqual(state_a["followup_last_action"], "deferred:session_locked")
            self.assertTrue(any(item.get("action") == "deferred:session_locked" for item in processed))

    def test_process_followups_nudge_mentions_bound_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-nudge-001",
                task_key="task-nudge",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                proposal_path="/home/Awei/project/PLAN.md",
                proposal_source="history",
                proposal_owner=False,
                closeout_proposal_dir="/home/Awei/project/closeout_proposal",
                closeout_proposal_dir_source="history",
            )
            followup = {
                "version": 1,
                "followup_key": "followup-nudge",
                "task_id": "task-nudge-001",
                "task_key": "task-nudge",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "history",
                "proposal_owner": False,
                "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
                "closeout_proposal_dir_source": "history",
                "workdir": "/home/Awei/project",
                "reason": "no_new_task_after_feedback",
                "created_at": "2026-03-19T00:00:00Z",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
            }
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "followup-nudge").write_text(json.dumps(followup), encoding="utf-8")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ) as mocked_resume:
                process_followups(config)

            delivered_prompt = mocked_resume.call_args.args[2]
            self.assertIn("proposal_file: [/home/Awei/project/PLAN.md]", delivered_prompt)
            self.assertIn("closeout_proposal_dir: [/home/Awei/project/closeout_proposal]", delivered_prompt)
            self.assertIn("请把本任务的结果、分析和 next bounded action 写回上面的 proposal", delivered_prompt)
            self.assertIn("写回与转场要求：", delivered_prompt)
            self.assertIn("不能写成流水账，要挑重点", delivered_prompt)

    def test_build_continuous_research_prompt_mentions_closeout_dir_and_chinese_writing(self) -> None:
        prompt = build_continuous_research_prompt(
            {
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
                "closeout_proposal_dir_source": "explicit",
            },
            trigger_signal="LOCAL_MICROSTEP_BATCH",
        )

        self.assertIn("在 continuous 模式下，你被设计为一位无需人工干预，也能进行高质量自动科研的 agent。", prompt)
        self.assertIn("轻度科研约定：", prompt)
        self.assertIn("写回与转场要求：", prompt)
        self.assertIn("不能写成流水账，要挑重点", prompt)
        self.assertIn("benchmark、比较对象是谁、变化趋势如何", prompt)
        self.assertIn("closeout_proposal_dir: [/home/Awei/project/closeout_proposal]", prompt)
        self.assertIn("绑定提醒：", prompt)
        self.assertIn("LOCAL_MICROSTEP_BATCH", prompt)
        self.assertIn("所有实验建议优先使用 tmux", prompt)
        self.assertIn("4 卡规划高吞吐", prompt)
        self.assertIn("WAITING_ON_ASYNC", prompt)
        self.assertNotIn("WAITING_ON_LIVE_TASK", prompt)

    def test_build_standard_followup_prompt_uses_compact_profile(self) -> None:
        prompt = build_standard_followup_prompt(
            {
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/home/Awei/project/HISTORY.md",
                "project_history_file_source": "explicit",
            },
            continuous_research_enabled=False,
        )

        self.assertIn("proposal_file: [/home/Awei/project/PLAN.md]", prompt)
        self.assertIn("closeout_proposal_dir: [/home/Awei/project/closeout_proposal]", prompt)
        self.assertIn("project_history_file: [/home/Awei/project/HISTORY.md]", prompt)
        self.assertIn("轻度科研约定：", prompt)
        self.assertIn("next bounded action", prompt)
        self.assertIn("默认在当前长上下文完成", prompt)
        self.assertIn("写回与转场要求：", prompt)
        self.assertIn("Taskboard 操作方法：", prompt)
        self.assertIn("FINAL_SIGNAL=LOCAL_CONTINUE_NO_WAKE|LOCAL_MICROSTEP_BATCH|ANALYZING_NEW_EVIDENCE", prompt)
        self.assertIn("WAITING_ON_ASYNC", prompt)
        self.assertNotIn("WAITING_ON_LIVE_TASK", prompt)
        self.assertNotIn("Taskboard protocol card `TBP1`", prompt)
        self.assertNotIn("不要先想要不要扩动作", prompt)
        self.assertNotIn("不要先为了推进而扩动作", prompt)
        self.assertNotIn("不要先为了证明推进而扩动作", prompt)
        self.assertNotIn("以下内容是对当前对话的后台提醒，请把它当作补充上下文", prompt)
        self.assertNotIn("proposal binding guard：", prompt)
        self.assertNotIn("项目发展史维护要求：", prompt)
        self.assertNotIn("必须在本轮把它提交成真实任务", prompt)
        self.assertLess(len(prompt), 5000)

    def test_light_runtime_prompt_scenes_stay_within_compact_soft_caps(self) -> None:
        spec = {
            "proposal_path": "/home/Awei/project/PLAN.md",
            "proposal_source": "explicit",
            "proposal_owner": True,
            "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
            "closeout_proposal_dir_source": "explicit",
            "project_history_file": "/home/Awei/project/HISTORY.md",
            "project_history_file_source": "explicit",
        }
        prompts = {
            "standard_followup": (
                build_standard_followup_prompt(spec, continuous_research_enabled=False),
                4320,
            ),
            "continuous_research": (
                build_continuous_research_prompt(spec, trigger_signal=LOCAL_MICROSTEP_BATCH_SIGNAL),
                4380,
            ),
            "parked_watchdog": (
                build_parked_watchdog_prompt(spec, trigger_signal=PARKED_IDLE_SIGNAL),
                3900,
            ),
            "materials_ready": (
                build_materials_ready_for_proposal_prompt(
                    spec,
                    trigger_signal=MATERIALS_READY_FOR_PROPOSAL_SIGNAL,
                ),
                3900,
            ),
            "continuous_transition": (
                build_continuous_transition_prompt(spec, trigger_signal="NO_FURTHER_TASKS"),
                3900,
            ),
        }

        for scene, (prompt, soft_cap) in prompts.items():
            with self.subTest(scene=scene):
                self.assertIn("proposal_file: [/home/Awei/project/PLAN.md]", prompt)
                self.assertIn("回复末尾请单独补一组自检行", prompt)
                self.assertNotIn("不要先为了推进而扩动作", prompt)
                self.assertNotIn("不要先为了证明推进而扩动作", prompt)
                self.assertLess(len(prompt), soft_cap)

    def test_light_static_prompt_assets_stay_compact(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow_notice = (repo_root / "prompts" / "taskboard_agent_workflow_notice_zh.md").read_text(
            encoding="utf-8"
        )
        redispatch_notice = (repo_root / "prompts" / "redispatch_unfinished_agent.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("高质量自动科研", workflow_notice)
        self.assertIn("bind-before-launch", workflow_notice)
        self.assertIn("proposal/history", workflow_notice)
        self.assertIn("tmux", workflow_notice)
        self.assertNotIn("不要先为了推进而扩动作", workflow_notice)
        self.assertLess(len(workflow_notice), 2600)

        self.assertIn("只处理你自己之前启动", redispatch_notice)
        self.assertIn("attach-pid", redispatch_notice)
        self.assertIn("proposal/history", redispatch_notice)
        self.assertNotIn("不要先为了推进而扩动作", redispatch_notice)
        self.assertLess(len(redispatch_notice), 1800)

    def test_recent_project_history_next_action_hint_prefers_created_at_over_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            log_dir = Path(tmpdir) / "HISTORY-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            latest_created_log = log_dir / "20260411T080000Z-latest-created.md"
            stale_named_log = log_dir / "20260411T120000Z-stale-named.md"
            latest_created_log.write_text(
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
            stale_named_log.write_text(
                "\n".join(
                    [
                        "- created_at: `2026-04-11T18:00:00+08:00`",
                        "",
                        "## Next bounded action",
                        "1. 继续保留旧 parked 说明。",
                    ]
                ),
                encoding="utf-8",
            )

            hint = recent_project_history_next_action_hint(
                {"project_history_file": str(history_path)},
                now_ts=datetime(2026, 4, 11, 20, 0, tzinfo=timezone(timedelta(hours=8))).timestamp(),
            )

        self.assertEqual(hint["source_path"], str(latest_created_log))
        self.assertIn("起草新 family proposal 骨架", hint["action_text"])
        self.assertTrue(hint["proposal_bootstrap"])
        self.assertEqual(hint["status"], "ready_local")

    def test_recent_project_history_next_action_hint_detects_direct_local_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            log_dir = Path(tmpdir) / "HISTORY-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            launch_spec_log = log_dir / "20260411T200000Z-phase-f-launch-spec.md"
            launch_spec_log.write_text(
                "\n".join(
                    [
                        "- created_at: `2026-04-11T20:00:00+08:00`",
                        "",
                        "## Next bounded action",
                        "1. 物化一个显式绑定 executable rematerializer 的 Phase F-D single-seed nonthinking microcycle launch spec。",
                    ]
                ),
                encoding="utf-8",
            )

            hint = recent_project_history_next_action_hint(
                {"project_history_file": str(history_path)},
                now_ts=datetime(2026, 4, 11, 20, 5, tzinfo=timezone(timedelta(hours=8))).timestamp(),
            )

        self.assertEqual(hint["source_path"], str(launch_spec_log))
        self.assertTrue(hint["direct_local_artifact"])
        self.assertEqual(hint["direct_local_artifact_reason"], "launch spec")
        self.assertEqual(hint["status"], "ready_local")

    def test_recent_project_history_next_action_hint_treats_negative_live_task_phrases_as_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            log_dir = Path(tmpdir) / "HISTORY-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            launch_spec_log = log_dir / "20260411T204500Z-phase-f-rematerializer.md"
            launch_spec_log.write_text(
                "\n".join(
                    [
                        "- created_at: `2026-04-11T20:45:00+08:00`",
                        "",
                        "## Next bounded action",
                        "1. 物化一个显式绑定 executable rematerializer 的 Phase F-D single-seed nonthinking microcycle launch spec；",
                        "2. 在 launch spec 明确前，不提交 live task，不重开 GPU。",
                    ]
                ),
                encoding="utf-8",
            )

            hint = recent_project_history_next_action_hint(
                {"project_history_file": str(history_path)},
                now_ts=datetime(2026, 4, 11, 20, 50, tzinfo=timezone(timedelta(hours=8))).timestamp(),
            )

        self.assertEqual(hint["source_path"], str(launch_spec_log))
        self.assertEqual(hint["status"], "ready_local")
        self.assertTrue(hint["direct_local_artifact"])
        self.assertFalse(hint["requires_live_task"])
        self.assertFalse(hint["requires_async"])
        self.assertFalse(hint["conflict"])

    def test_recent_project_history_next_action_hint_marks_bound_live_task_as_dispatch_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            log_dir = Path(tmpdir) / "HISTORY-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            dispatch_log = log_dir / "20260411T210144Z-phase-f-launch-dispatch.md"
            dispatch_log.write_text(
                "\n".join(
                    [
                        "- created_at: `2026-04-11T21:01:44+08:00`",
                        "",
                        "## Next bounded action",
                        "1. 使用当前 proposal/history 显式绑定 launch spec 与 runner wrapper；",
                        "2. 提交一条 `Phase F-D single-seed nonthinking microcycle` live task；",
                        "3. 提交后用 `codex-taskboard status --json` 校验 proposal_path 与 live 状态。",
                    ]
                ),
                encoding="utf-8",
            )

            hint = recent_project_history_next_action_hint({"project_history_file": str(history_path)})

        self.assertEqual(hint["source_path"], str(dispatch_log))
        self.assertEqual(hint["status"], "dispatch_ready")
        self.assertTrue(hint["dispatch_ready"])
        self.assertTrue(hint["requires_live_task"])
        self.assertTrue(hint["requires_async"])
        self.assertFalse(hint["conflict"])

    def test_canonical_head_next_action_hint_uses_next_step_when_history_log_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text(
                "\n".join(
                    [
                        "<!-- TASKBOARD_CANONICAL_HEAD_BEGIN CH1 role=proposal -->",
                        "BIG_MAINLINE=phase_f_survival_mainline",
                        "SMALL_MAINLINE=nonthinking_microcycle_repair",
                        "CURRENT_BOUNDARY=only CPU-only closeout and route-planning claims are allowed",
                        "NEXT_STEP=继续吸收当前 closeout receipt，并完成 CPU-only claim-boundary 审计；无需 GPU，无需 future callback。",
                        "<!-- TASKBOARD_CANONICAL_HEAD_END -->",
                    ]
                ),
                encoding="utf-8",
            )
            history_path.write_text("# history\n", encoding="utf-8")

            hint = canonical_head_next_action_hint(
                {
                    "proposal_path": str(proposal_path),
                    "project_history_file": str(history_path),
                }
            )

        self.assertEqual(hint["source_kind"], "canonical_head")
        self.assertEqual(hint["source_path"], str(proposal_path))
        self.assertEqual(hint["parser"], "canonical_head")
        self.assertEqual(hint["status"], "ready_local")
        self.assertTrue(hint["controller_inherit_local"])
        self.assertIn("继续吸收当前 closeout receipt", hint["action_text"])

    def test_session_continuation_hint_prefers_recent_local_receipt_over_canonical_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-local-receipt-001"
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            event_path = config.tasks_root / "task-local-receipt-001" / "events" / "completed.json"
            feedback_path = event_path.parent / "completed-feedback.json"
            command_log_path = event_path.parent.parent / "command.log"
            runner_log_path = event_path.parent.parent / "runner.log"
            artifact_path = event_path.parent.parent / "artifacts" / "summary.json"
            event_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            feedback_path.write_text("{\"status\":\"ok\"}\n", encoding="utf-8")
            command_log_path.write_text("done\n", encoding="utf-8")
            runner_log_path.write_text("runner done\n", encoding="utf-8")
            artifact_path.write_text("{\"metric\": 1}\n", encoding="utf-8")
            proposal_path.write_text(
                "\n".join(
                    [
                        "<!-- TASKBOARD_CANONICAL_HEAD_BEGIN CH1 role=proposal -->",
                        "BIG_MAINLINE=phase_f_survival_mainline",
                        "SMALL_MAINLINE=nonthinking_microcycle_repair",
                        "CURRENT_BOUNDARY=keep claims at closeout + route replanning level",
                        "NEXT_STEP=如果没有新 receipt，就继续按 canonical route 做 CPU-only 审计。",
                        "<!-- TASKBOARD_CANONICAL_HEAD_END -->",
                    ]
                ),
                encoding="utf-8",
            )
            history_path.write_text(
                "\n".join(
                    [
                        "<!-- TASKBOARD_CANONICAL_HEAD_BEGIN CH1 role=history -->",
                        "BIG_MAINLINE=phase_f_survival_mainline",
                        "SMALL_MAINLINE=nonthinking_microcycle_repair",
                        "CURRENT_BOUNDARY=keep claims at closeout + route replanning level",
                        "NEXT_STEP=若 receipt 已吸收，再刷新 history/proposal 锚点。",
                        "<!-- TASKBOARD_CANONICAL_HEAD_END -->",
                    ]
                ),
                encoding="utf-8",
            )
            event_path.parent.mkdir(parents=True, exist_ok=True)
            event_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "ended_at": "2030-04-11T21:20:04+08:00",
                        "feedback_data_path": str(feedback_path),
                        "command_log_path": str(command_log_path),
                        "runner_log_path": str(runner_log_path),
                        "failure_kind": "completed",
                        "artifact_context": [
                            {"pattern": "summary.json", "path": str(artifact_path), "summary": "fresh receipt artifact"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            task_id = "task-local-receipt-001"
            anchor_spec = {
                "task_id": task_id,
                "task_key": "task-local-receipt",
                "execution_mode": "shell",
                "codex_session_id": session_id,
                "agent_name": "research-agent",
                "proposal_path": str(proposal_path),
                "proposal_source": "explicit",
                "proposal_owner": True,
                "project_history_file": str(history_path),
                "project_history_file_source": "explicit",
                "workdir": "/home/Awei/project",
            }
            write_task_spec(config, task_id, anchor_spec)
            write_state(
                config,
                task_id,
                task_key="task-local-receipt",
                status="completed",
                codex_session_id=session_id,
                agent_name="research-agent",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
                last_event_path=str(event_path),
            )

            direct_hint = recent_local_evidence_sweep_hint(config, session_id, spec=anchor_spec)
            hint = session_continuation_hint(config, session_id, spec=anchor_spec)

        self.assertEqual(direct_hint["source_kind"], "local_receipt")
        self.assertTrue(direct_hint["collect_local_evidence"])
        self.assertEqual(hint["source_kind"], "local_receipt")
        self.assertTrue(hint["collect_local_evidence"])
        self.assertTrue(hint["controller_inherit_local"])
        self.assertEqual(hint["status"], "ready_local")
        self.assertEqual(hint["source_path"], str(feedback_path))
        self.assertEqual(hint["receipt_event_path"], str(event_path))
        self.assertIn("吸收最近 receipt 与本地 artifact", hint["action_text"])

    def test_ensure_continuous_research_session_reminders_unparks_parked_session_for_recent_local_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-receipt-unpark-001"
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text(
                "\n".join(
                    [
                        "<!-- TASKBOARD_CANONICAL_HEAD_BEGIN CH1 role=proposal -->",
                        "BIG_MAINLINE=phase_f_survival_mainline",
                        "SMALL_MAINLINE=nonthinking_microcycle_repair",
                        "CURRENT_BOUNDARY=closeout and successor-bootstrap only",
                        "NEXT_STEP=若无新 receipt，再继续 canonical route note。",
                        "<!-- TASKBOARD_CANONICAL_HEAD_END -->",
                    ]
                ),
                encoding="utf-8",
            )
            history_path.write_text(
                "\n".join(
                    [
                        "<!-- TASKBOARD_CANONICAL_HEAD_BEGIN CH1 role=history -->",
                        "BIG_MAINLINE=phase_f_survival_mainline",
                        "SMALL_MAINLINE=nonthinking_microcycle_repair",
                        "CURRENT_BOUNDARY=closeout and successor-bootstrap only",
                        "NEXT_STEP=若 evidence 无新增，再刷新 parked 结论。",
                        "<!-- TASKBOARD_CANONICAL_HEAD_END -->",
                    ]
                ),
                encoding="utf-8",
            )
            task_id = "task-receipt-unpark-001"
            event_path = config.tasks_root / task_id / "events" / "completed.json"
            feedback_path = event_path.parent / "completed-feedback.json"
            event_path.parent.mkdir(parents=True, exist_ok=True)
            feedback_path.write_text("{\"delta\":\"fresh\"}\n", encoding="utf-8")
            event_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "ended_at": "2030-04-11T22:20:04+08:00",
                        "feedback_data_path": str(feedback_path),
                        "command_log_path": str(event_path.parent.parent / "command.log"),
                        "runner_log_path": str(event_path.parent.parent / "runner.log"),
                        "failure_kind": "completed",
                        "artifact_context": [],
                    }
                ),
                encoding="utf-8",
            )
            anchor_spec = {
                "version": 1,
                "task_id": task_id,
                "task_key": "task-receipt-unpark",
                "execution_mode": "shell",
                "workdir": "/home/Awei/project",
                "command": "python audit.py",
                "codex_session_id": session_id,
                "agent_name": "research-agent",
                "feedback_mode": "auto",
                "proposal_path": str(proposal_path),
                "proposal_source": "explicit",
                "proposal_owner": True,
                "project_history_file": str(history_path),
                "project_history_file_source": "explicit",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 3600,
                "fallback_provider": "",
                "prompt_max_chars": 12000,
            }
            write_task_spec(config, task_id, anchor_spec)
            write_state(
                config,
                task_id,
                task_key="task-receipt-unpark",
                status="completed",
                codex_session_id=session_id,
                workdir="/home/Awei/project",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
                last_event_path=str(event_path),
                agent_name="research-agent",
            )
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
                evidence_token=continuous_research_session_evidence_token(config, session_id),
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )

            processed = ensure_continuous_research_session_reminders(config)

            self.assertTrue(
                any(
                    item.get("action") == "continuous_session_reminder_scheduled_next_action"
                    and item.get("reason") == CONTINUOUS_RESEARCH_NEXT_ACTION_REASON
                    for item in processed
                )
            )
            session_state = load_continuous_research_mode(config, codex_session_id=session_id)["target_session_state"]
            self.assertEqual(session_state["waiting_state"], "")
            self.assertEqual(session_state["last_signal"], LOCAL_MICROSTEP_BATCH_SIGNAL)
            reminder_files = list(config.followups_root.glob("*.json"))
            self.assertEqual(len(reminder_files), 1)
            reminder_payload = json.loads(reminder_files[0].read_text(encoding="utf-8"))
            self.assertEqual(reminder_payload["reason"], CONTINUOUS_RESEARCH_NEXT_ACTION_REASON)
            self.assertEqual(reminder_payload["controller_continuation_hint"]["source_kind"], "local_receipt")
            self.assertTrue(reminder_payload["controller_continuation_hint"]["collect_local_evidence"])

    def test_continuous_session_reminder_schedule_params_promotes_dispatch_ready_to_materials_ready_signal(self) -> None:
        params = continuous_session_reminder_schedule_params(
            build_config(Path("/tmp")),
            "session-001",
            {"proposal_path": "/tmp/PLAN.md"},
            session_state={"last_signal": LOCAL_MICROSTEP_BATCH_SIGNAL},
            next_action_hint={"dispatch_ready": True, "dispatch_ready_reason": "提交一条"},
        )

        self.assertEqual(params["last_signal"], MATERIALS_READY_FOR_PROPOSAL_SIGNAL)
        self.assertEqual(params["reason"], "proposal_materialization")
        self.assertEqual(params["delay_seconds"], 0)

    def test_build_continuous_research_prompt_uses_compact_profile(self) -> None:
        prompt = build_continuous_research_prompt(
            {
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/home/Awei/project/HISTORY.md",
                "project_history_file_source": "explicit",
            },
            trigger_signal="LOCAL_MICROSTEP_BATCH",
        )

        self.assertIn("proposal_file: [/home/Awei/project/PLAN.md]", prompt)
        self.assertIn("next bounded action", prompt)
        self.assertIn("轻度科研约定：", prompt)
        self.assertIn("Taskboard 操作方法：", prompt)
        self.assertIn("高质量自动科研", prompt)
        self.assertIn("4 卡规划高吞吐", prompt)
        self.assertIn("写回与转场要求：", prompt)
        self.assertIn("不能写成流水账，要挑重点", prompt)
        self.assertIn("benchmark、比较对象是谁、变化趋势如何", prompt)
        self.assertIn("tmux", prompt)
        self.assertIn("FINAL_SIGNAL=LOCAL_CONTINUE_NO_WAKE|LOCAL_MICROSTEP_BATCH|ANALYZING_NEW_EVIDENCE", prompt)
        self.assertIn("WAITING_ON_ASYNC", prompt)
        self.assertNotIn("WAITING_ON_LIVE_TASK", prompt)

    def test_build_continuous_research_prompt_pushes_direct_local_artifact_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            log_dir = Path(tmpdir) / "HISTORY-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "20260411T200000Z-phase-f-launch-spec.md").write_text(
                "\n".join(
                    [
                        "- created_at: `2026-04-11T20:00:00+08:00`",
                        "",
                        "## Next bounded action",
                        "1. 物化一个显式绑定 executable rematerializer 的 Phase F-D single-seed nonthinking microcycle launch spec。",
                    ]
                ),
                encoding="utf-8",
            )

            prompt = build_continuous_research_prompt(
                {
                    "proposal_path": "/home/Awei/project/PLAN.md",
                    "proposal_source": "explicit",
                    "proposal_owner": True,
                    "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
                    "closeout_proposal_dir_source": "explicit",
                    "project_history_file": str(history_path),
                    "project_history_file_source": "explicit",
                },
                trigger_signal="LOCAL_MICROSTEP_BATCH",
            )

        self.assertIn("direct_local_artifact: true (launch spec)", prompt)
        self.assertIn("不要只复述动作名后就结束当前唤起", prompt)
        self.assertIn("请不要仅返回 `TASKBOARD_SIGNAL=LOCAL_CONTINUE_NO_WAKE` 或 `TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH`", prompt)

    def test_build_continuous_research_prompt_for_materials_ready_focuses_on_dispatch(self) -> None:
        prompt = build_continuous_research_prompt(
            {
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/home/Awei/project/HISTORY.md",
                "project_history_file_source": "explicit",
            },
            trigger_signal=MATERIALS_READY_FOR_PROPOSAL_SIGNAL,
        )

        self.assertIn("理论与实验前置材料整理", prompt)
        self.assertIn("不要重新做 parked 复核", prompt)
        self.assertIn("代码审计结论", prompt)
        self.assertIn("proposal 已成型且实验包已经可执行", prompt)
        self.assertIn("阻塞项和补齐动作", prompt)
        self.assertNotIn("不要为了满足流程而机械提交 live task", prompt)
        self.assertIn("TASKBOARD_SIGNAL=NEW_TASKS_STARTED", prompt)
        self.assertNotIn("当前是 parked continuity followup", prompt)

    def test_build_continuous_research_prompt_for_parked_watchdog_requires_bounded_self_review(self) -> None:
        prompt = build_continuous_research_prompt(
            {
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/home/Awei/project/HISTORY.md",
                "project_history_file_source": "explicit",
            },
            trigger_signal=PARKED_IDLE_SIGNAL,
        )

        self.assertIn("parked watchdog", prompt)
        self.assertIn("bounded local action", prompt)
        self.assertIn("最近时间日志、summary、report 与 artifact", prompt)
        self.assertIn("waiting/parked 只是兜底状态", prompt)
        self.assertIn("本轮最小闭环", prompt)
        self.assertIn("同上下文 CPU-only 数据处理", prompt)
        self.assertIn("successor 材料已齐", prompt)
        self.assertIn("MATERIALS_READY_FOR_PROPOSAL", prompt)
        self.assertIn("WAITING_ON_ASYNC", prompt)
        self.assertNotIn("WAITING_ON_LIVE_TASK", prompt)
        self.assertIn("回复末尾请单独补一组自检行", prompt)
        self.assertNotIn("以下内容是对当前对话的后台提醒，请把它当作补充上下文", prompt)
        self.assertNotIn("proposal binding guard：", prompt)
        self.assertNotIn("项目发展史维护要求：", prompt)
        self.assertNotIn("只要当前没有等价 live task，就在同一轮分发真实任务", prompt)
        self.assertNotIn("Taskboard quick memory", prompt)
        self.assertNotIn("Taskboard protocol card `TBP1`", prompt)
        self.assertLess(len(prompt), 4000)

    def test_build_continuous_transition_prompt_requires_new_task_start_signal(self) -> None:
        prompt = build_continuous_transition_prompt(
            {
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/home/Awei/project/HISTORY.md",
                "project_history_file_source": "explicit",
            },
            trigger_signal="NO_FURTHER_TASKS",
        )

        self.assertIn("continuous 收口转场提醒", prompt)
        self.assertIn("最小必要的 writeback", prompt)
        self.assertIn("project_history_file: [/home/Awei/project/HISTORY.md]", prompt)
        self.assertIn("closeout_proposal_dir: [/home/Awei/project/closeout_proposal]", prompt)
        self.assertIn("阶段目标、实验包、决策分支、实现要点、验证指标与停止条件", prompt)
        self.assertIn(f"TASKBOARD_SIGNAL={CONTINUOUS_RESEARCH_NEW_TASK_SIGNAL}", prompt)
        self.assertIn("至少一条真实验证实验或受托管任务已提交", prompt)
        self.assertIn("否则继续留在当前上下文补齐实验前置", prompt)

    def test_footer_only_prompt_scenes_keep_footer_contract_without_full_protocol_card(self) -> None:
        spec = {
            "task_id": "task-scene-matrix",
            "workdir": "/home/Awei/project",
            "command": "python audit.py",
            "execution_mode": "shell",
            "proposal_path": "/home/Awei/project/PLAN.md",
            "proposal_source": "explicit",
            "proposal_owner": True,
            "closeout_proposal_dir": "/home/Awei/project/closeout_proposal",
            "closeout_proposal_dir_source": "explicit",
            "project_history_file": "/home/Awei/project/HISTORY.md",
            "project_history_file_source": "explicit",
            "task_note": "CPU-only audit worker",
        }
        event = {
            "status": "completed",
            "event_path": "/tmp/task-scene-matrix-event.json",
            "feedback_data_path": "/tmp/task-scene-matrix-feedback.json",
            "command_log_path": "/tmp/task-scene-matrix.log",
            "runner_log_path": "/tmp/task-scene-matrix-runner.log",
            "failure_kind": "completed",
            "failure_summary": "Audit finished successfully.",
            "duration_seconds": 7,
            "artifact_context": [],
            "log_tail": "",
        }
        followup = {"protocol_issue": "missing_footer", "protocol_footer": {}}
        prompts = {
            "continuous_research": (
                build_continuous_research_prompt(spec, trigger_signal=LOCAL_MICROSTEP_BATCH_SIGNAL),
                5000,
            ),
            "parked_watchdog": (
                build_parked_watchdog_prompt(spec, trigger_signal=PARKED_IDLE_SIGNAL),
                3900,
            ),
            "materials_ready": (
                build_materials_ready_for_proposal_prompt(spec, trigger_signal=MATERIALS_READY_FOR_PROPOSAL_SIGNAL),
                3900,
            ),
            "protocol_repair": (
                build_protocol_self_check_repair_prompt(spec, followup, continuous_research_enabled=True),
                1300,
            ),
            "queued_batch": (
                build_queued_feedback_batch_prompt(spec, [{"resume_spec": spec, "resume_event": event}]),
                4600,
            ),
            "continuous_transition": (
                build_continuous_transition_prompt(spec, trigger_signal="NO_FURTHER_TASKS"),
                4560,
            ),
        }

        for scene, (prompt, soft_cap) in prompts.items():
            with self.subTest(scene=scene):
                self.assertIn("回复末尾请单独补一组自检行", prompt)
                self.assertNotIn("Taskboard protocol card `TBP1`", prompt)
                self.assertNotIn("不要先想要不要扩动作", prompt)
                self.assertNotIn("不要先为了推进而扩动作", prompt)
                self.assertNotIn("不要先为了证明推进而扩动作", prompt)
                self.assertLess(len(prompt), soft_cap)

    def test_process_followups_keeps_continuous_transition_until_new_tasks_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-transition-001",
                task_key="task-transition",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "followup-transition").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "followup-transition",
                        "followup_type": CONTINUOUS_RESEARCH_TRANSITION_FOLLOWUP_TYPE,
                        "task_id": "task-transition-001",
                        "task_key": "task-transition",
                        "execution_mode": "shell",
                        "codex_session_id": "session-topo-001",
                        "agent_name": "toposem-agent",
                        "proposal_path": "/home/Awei/project/PLAN.md",
                        "proposal_source": "explicit",
                        "proposal_owner": True,
                        "project_history_file": "/home/Awei/project/HISTORY.md",
                        "project_history_file_source": "explicit",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_TRANSITION_REASON,
                        "created_at": "2026-03-20T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                        "last_signal": "NO_FURTHER_TASKS",
                    }
                ),
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                processed = process_followups(config)

            rebound_key = followup_key_for(
                {
                    "codex_session_id": "session-topo-001",
                    "agent_name": "toposem-agent",
                    "task_key": "task-transition",
                    "task_id": "task-transition-001",
                }
            )
            self.assertTrue(followup_path(config, rebound_key).exists())
            state = load_task_state(config, "task-transition-001")
            self.assertEqual(state["followup_last_action"], f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}")
            self.assertTrue(any(item.get("action") == "continuous_transition_rescheduled" for item in processed))
            rebound_payload = json.loads(followup_path(config, rebound_key).read_text(encoding="utf-8"))
            rebound_payload["check_after_ts"] = 0
            followup_path(config, rebound_key).write_text(json.dumps(rebound_payload), encoding="utf-8")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": f"TASKBOARD_SIGNAL={CONTINUOUS_RESEARCH_NEW_TASK_SIGNAL}\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:05:00Z",
                },
            ):
                processed = process_followups(config)

            self.assertFalse(followup_path(config, rebound_key).exists())
            state = load_task_state(config, "task-transition-001")
            self.assertEqual(state["followup_status"], "resolved")
            self.assertEqual(state["followup_last_action"], "resolved_continuous_transition_new_tasks_started")
            self.assertTrue(any(item.get("action") == "resolved_continuous_transition_new_tasks_started" for item in processed))

    def test_process_followups_passes_recorded_signal_into_continuous_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-continuous-001",
                task_key="task-continuous",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "followup-continuous").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "followup-continuous",
                        "task_id": "task-continuous-001",
                        "task_key": "task-continuous",
                        "execution_mode": "shell",
                        "codex_session_id": "session-topo-001",
                        "agent_name": "toposem-agent",
                        "proposal_path": "/home/Awei/project/PLAN.md",
                        "proposal_source": "explicit",
                        "proposal_owner": True,
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 1,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                        "last_signal": "LOCAL_MICROSTEP_BATCH",
                    }
                ),
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ) as mocked_resume:
                process_followups(config)

            delivered_prompt = mocked_resume.call_args.args[2]
            self.assertIn("TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH", delivered_prompt)
            self.assertNotIn("你刚刚输出了 TASKBOARD_SIGNAL=NO_FURTHER_TASKS", delivered_prompt)

    def test_process_followups_schedules_protocol_self_check_repair_when_footer_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-protocol-001",
                task_key="task-protocol",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-protocol-001",
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "followup-protocol").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "followup-protocol",
                        "task_id": "task-protocol-001",
                        "task_key": "task-protocol",
                        "execution_mode": "shell",
                        "codex_session_id": "session-protocol-001",
                        "agent_name": "toposem-agent",
                        "proposal_path": "/home/Awei/project/PLAN.md",
                        "proposal_source": "explicit",
                        "proposal_owner": True,
                        "workdir": "/home/Awei/project",
                        "reason": "no_new_task_after_feedback",
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 1,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-protocol-001",
                    "resumed_session_id": "session-protocol-001",
                    "used_fallback_clone": False,
                    "last_message_text": "继续分析中\n",
                    "taskboard_protocol": {
                        "ack": "",
                        "step_class": "",
                        "self_check": "",
                        "live_task_status": "",
                        "final_signal": "",
                        "valid": False,
                    },
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                process_followups(config)

            followup_files = sorted(config.followups_root.glob("*.json"))
            self.assertTrue(followup_files)
            updated_followup = json.loads(followup_files[0].read_text(encoding="utf-8"))
            self.assertEqual(updated_followup["followup_type"], "protocol_self_check_repair")
            self.assertEqual(updated_followup["reason"], "protocol_self_check_repair")
            self.assertEqual(updated_followup["protocol_issue"], "missing_or_wrong_ack,missing_or_wrong_step_class,missing_or_wrong_self_check,missing_or_wrong_live_task_status,missing_or_wrong_final_signal")
            state = load_task_state(config, "task-protocol-001")
            self.assertEqual(state["followup_last_action"], "scheduled:protocol_self_check_repair")

    def test_waiting_on_async_reschedule_clears_protocol_repair_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-waiting-001",
                task_key="task-waiting",
                feedback_mode="auto",
                codex_session_id="session-waiting-001",
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "followup_key": "followup-waiting",
                "task_id": "task-waiting-001",
                "task_key": "task-waiting",
                "execution_mode": "shell",
                "codex_session_id": "session-waiting-001",
                "agent_name": "",
                "workdir": "/home/Awei/project",
                "reason": "protocol_self_check_repair",
                "created_at": "2026-03-19T00:00:00Z",
                "check_after_ts": 0,
                "interval_seconds": 1,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
                "followup_type": "protocol_self_check_repair",
                "protocol_issue": "missing_protocol_footer",
                "protocol_footer": {
                    "ack": "",
                    "step_class": "",
                    "self_check": "",
                    "live_task_status": "",
                    "final_signal": "",
                    "valid": False,
                },
                "protocol_observed_signal": "",
            }
            path = followup_path(config, "followup-waiting")
            path.write_text(json.dumps(payload), encoding="utf-8")

            schedule_waiting_on_async_watchdog(
                config,
                task_id="task-waiting-001",
                spec={"task_id": "task-waiting-001"},
                followup=payload,
            )

            updated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(updated["reason"], "waiting_on_async_watchdog")
            self.assertNotIn("followup_type", updated)
            self.assertNotIn("protocol_issue", updated)
            self.assertNotIn("protocol_footer", updated)
            self.assertNotIn("protocol_observed_signal", updated)

    def test_build_continuous_research_prompt_flags_manual_decision_gate_from_bound_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_path = Path(tmpdir) / "PROPOSAL.md"
            proposal_path.write_text(
                "\n".join(
                    [
                        "# proposal",
                        "当前 active continuation 已不再是“继续自动补 launch-prep sidecar”，而是“等待对单一 manual dispatch handoff 的显式解释”。",
                        "required_fields_before_interpretation = decision_author + decision_timestamp + decision_outcome",
                    ]
                ),
                encoding="utf-8",
            )

            prompt = build_continuous_research_prompt(
                {
                    "proposal_path": str(proposal_path),
                    "proposal_source": "explicit",
                    "proposal_owner": True,
                }
            )

            self.assertIn("检测到当前绑定 proposal 中可能存在人工决策门或手动 handoff。", prompt)
            self.assertIn("请先回顾之前日志，以及与当前人工决策门最相关的 proposal 段落、summary、report、handoff packet", prompt)
            self.assertIn("不要只写“等待人工”", prompt)
            self.assertIn("等待对单一 manual dispatch handoff 的显式解释", prompt)

    def test_process_followups_schedules_continuous_session_reminder_for_idle_enabled_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-idle-queued-001",
                status="completed",
                task_key="task-idle-queued",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )

            with patch("codex_taskboard.cli.resume_codex_session_with_prompt") as mocked_resume:
                processed = process_followups(config)

            reminder_files = list(config.followups_root.glob("*.json"))
            self.assertEqual(len(reminder_files), 1)
            payload = json.loads(reminder_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["followup_type"], CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE)
            self.assertEqual(payload["reason"], CONTINUOUS_RESEARCH_IDLE_REASON)
            self.assertEqual(payload["codex_session_id"], "session-topo-001")
            mocked_resume.assert_not_called()
            state = load_task_state(config, "task-idle-queued-001")
            self.assertEqual(state["followup_status"], "scheduled")
            self.assertIn(
                state["followup_last_action"],
                {f"scheduled:{CONTINUOUS_RESEARCH_IDLE_REASON}", "rebound_session_binding"},
            )
            self.assertTrue(any(item.get("action") == "continuous_session_reminder_scheduled" for item in processed))

    def test_process_followups_schedules_continuous_session_reminder_for_fresh_parked_idle_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text("# parked\n", encoding="utf-8")
            history_path.write_text("# history\n", encoding="utf-8")
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-parked-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-idle-parked-001",
                status="completed",
                task_key="task-idle-parked",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-parked-001",
                workdir="/home/Awei/project",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
            )
            evidence_token = continuous_research_session_evidence_token(config, "session-topo-parked-001")
            park_continuous_research_session(
                config,
                codex_session_id="session-topo-parked-001",
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token=evidence_token,
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )

            processed = ensure_continuous_research_session_reminders(config)

            reminder_files = list(config.followups_root.glob("*.json")) if config.followups_root.exists() else []
            self.assertEqual(len(reminder_files), 1)
            payload = json.loads(reminder_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["reason"], CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON)
            self.assertEqual(payload["last_signal"], PARKED_IDLE_SIGNAL)
            self.assertEqual(payload["interval_seconds"], DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS)
            self.assertTrue(
                any(
                    item.get("action") == "continuous_session_reminder_scheduled_parked_watchdog_pending"
                    for item in processed
                )
            )
            session_state = load_continuous_research_mode(config, codex_session_id="session-topo-parked-001")["target_session_state"]
            self.assertEqual(session_state["waiting_state"], PARKED_IDLE_SIGNAL)
            self.assertEqual(session_state["waiting_evidence_token"], evidence_token)

    def test_process_followups_reschedules_continuous_session_reminder_for_stale_parked_idle_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text("# parked\n", encoding="utf-8")
            history_path.write_text("# history\n", encoding="utf-8")
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-parked-watchdog-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-idle-parked-watchdog-001",
                status="completed",
                task_key="task-idle-parked-watchdog",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-parked-watchdog-001",
                workdir="/home/Awei/project",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
            )
            evidence_token = continuous_research_session_evidence_token(config, "session-topo-parked-watchdog-001")
            park_continuous_research_session(
                config,
                codex_session_id="session-topo-parked-watchdog-001",
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token=evidence_token,
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )

            future_ts = time.time() + DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS + 5
            with patch("codex_taskboard.cli.time.time", return_value=future_ts):
                processed = ensure_continuous_research_session_reminders(config)

            reminder_files = list(config.followups_root.glob("*.json")) if config.followups_root.exists() else []
            self.assertEqual(len(reminder_files), 1)
            payload = json.loads(reminder_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["followup_type"], CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE)
            self.assertEqual(payload["reason"], CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON)
            self.assertEqual(payload["codex_session_id"], "session-topo-parked-watchdog-001")
            self.assertEqual(payload["last_signal"], PARKED_IDLE_SIGNAL)
            self.assertEqual(payload["min_idle_seconds"], 0)
            self.assertEqual(payload["interval_seconds"], DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS)
            self.assertEqual(payload["check_after_ts"], future_ts)
            self.assertTrue(any(item.get("action") == "continuous_session_reminder_parked_watchdog_due" for item in processed))
            self.assertTrue(any(item.get("action") == "continuous_session_reminder_scheduled" for item in processed))
            session_state = load_continuous_research_mode(config, codex_session_id="session-topo-parked-watchdog-001")["target_session_state"]
            self.assertEqual(session_state["waiting_state"], PARKED_IDLE_SIGNAL)
            self.assertEqual(session_state["waiting_evidence_token"], evidence_token)

    def test_process_followups_schedules_initial_parked_watchdog_without_waiting_for_long_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text("# parked\n", encoding="utf-8")
            history_path.write_text("# history\n", encoding="utf-8")
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-parked-initial-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-idle-parked-initial-001",
                status="completed",
                task_key="task-idle-parked-initial",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-parked-initial-001",
                workdir="/home/Awei/project",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
            )
            evidence_token = continuous_research_session_evidence_token(config, "session-topo-parked-initial-001")
            park_continuous_research_session(
                config,
                codex_session_id="session-topo-parked-initial-001",
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token=evidence_token,
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )

            base_ts = time.time()
            with patch("codex_taskboard.cli.time.time", return_value=base_ts):
                processed = ensure_continuous_research_session_reminders(config)

            reminder_files = list(config.followups_root.glob("*.json")) if config.followups_root.exists() else []
            self.assertEqual(len(reminder_files), 1)
            payload = json.loads(reminder_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["followup_type"], CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE)
            self.assertEqual(payload["reason"], CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON)
            self.assertEqual(payload["last_signal"], PARKED_IDLE_SIGNAL)
            self.assertEqual(payload["interval_seconds"], DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS)
            self.assertGreaterEqual(payload["check_after_ts"], base_ts)
            self.assertLessEqual(
                payload["check_after_ts"],
                base_ts + DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS,
            )
            self.assertTrue(
                any(
                    item.get("action") == "continuous_session_reminder_scheduled_parked_watchdog_pending"
                    for item in processed
                )
            )

    def test_process_followups_schedules_backoff_parked_watchdog_when_repeat_count_increases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text("# parked\n", encoding="utf-8")
            history_path.write_text("# history\n", encoding="utf-8")
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-parked-watchdog-backoff-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-idle-parked-watchdog-backoff-001",
                status="completed",
                task_key="task-idle-parked-watchdog-backoff",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-parked-watchdog-backoff-001",
                workdir="/home/Awei/project",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
            )
            evidence_token = continuous_research_session_evidence_token(config, "session-topo-parked-watchdog-backoff-001")
            park_continuous_research_session(
                config,
                codex_session_id="session-topo-parked-watchdog-backoff-001",
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token=evidence_token,
                last_signal=PARKED_IDLE_SIGNAL,
                stable_idle_repeat_count=5,
                updated_by="test",
                source="unit",
            )

            early_future_ts = time.time() + DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS + 5
            with patch("codex_taskboard.cli.time.time", return_value=early_future_ts):
                processed_early = ensure_continuous_research_session_reminders(config)

            reminder_files = list(config.followups_root.glob("*.json")) if config.followups_root.exists() else []
            self.assertEqual(len(reminder_files), 1)
            early_payload = json.loads(reminder_files[0].read_text(encoding="utf-8"))
            self.assertEqual(early_payload["reason"], CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON)
            self.assertEqual(early_payload["interval_seconds"], DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS * 2)
            self.assertGreater(early_payload["check_after_ts"], early_future_ts)
            self.assertTrue(
                any(
                    item.get("action") == "continuous_session_reminder_scheduled_parked_watchdog_pending"
                    for item in processed_early
                )
            )

    def test_process_followups_resolves_continuous_session_reminder_on_explicit_parked_idle_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text("# parked\n", encoding="utf-8")
            history_path.write_text("# history\n", encoding="utf-8")
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-parked-002",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-reminder-parked-001",
                task_key="task-reminder-parked",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-parked-002",
                workdir="/home/Awei/project",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "continuous-session-reminder-parked").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "continuous-session-reminder-parked",
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": "task-reminder-parked-001",
                        "task_key": "task-reminder-parked",
                        "codex_session_id": "session-topo-parked-002",
                        "agent_name": "toposem-agent",
                        "proposal_path": str(proposal_path),
                        "proposal_source": "explicit",
                        "proposal_owner": True,
                        "project_history_file": str(history_path),
                        "project_history_file_source": "explicit",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_IDLE_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )
            expected_token = continuous_research_session_evidence_token(config, "session-topo-parked-002")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-parked-002",
                    "resumed_session_id": "session-topo-parked-002",
                    "used_fallback_clone": False,
                    "last_message_text": f"TASKBOARD_SIGNAL={PARKED_IDLE_SIGNAL}\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                processed = process_followups(config)

            reminder_files = list(config.followups_root.glob("*.json"))
            self.assertEqual(len(reminder_files), 1)
            reminder_entity = reminder_files[0]
            self.assertTrue(any(item.get("action") == "scheduled_immediate_parked_watchdog" for item in processed))
            state = load_task_state(config, "task-reminder-parked-001")
            self.assertEqual(state["session_flow_state"], "parked_idle")
            self.assertEqual(state["followup_status"], "scheduled")
            self.assertEqual(state["followup_last_signal"], PARKED_IDLE_SIGNAL)
            self.assertEqual(
                state["followup_last_action"],
                f"scheduled:{CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON}",
            )
            reminder_payload = json.loads(reminder_entity.read_text(encoding="utf-8"))
            self.assertEqual(reminder_payload["reason"], CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON)
            self.assertEqual(reminder_payload["last_signal"], PARKED_IDLE_SIGNAL)
            session_state = load_continuous_research_mode(config, codex_session_id="session-topo-parked-002")["target_session_state"]
            self.assertEqual(session_state["waiting_state"], PARKED_IDLE_SIGNAL)
            self.assertEqual(session_state["waiting_evidence_token"], expected_token)
            self.assertEqual(session_state["last_signal"], PARKED_IDLE_SIGNAL)

    def test_process_followups_reconfirming_parked_idle_increments_repeat_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text("# parked\n", encoding="utf-8")
            history_path.write_text("# history\n", encoding="utf-8")
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-parked-repeat-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-reminder-parked-repeat-001",
                task_key="task-reminder-parked-repeat",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-parked-repeat-001",
                workdir="/home/Awei/project",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "continuous-session-reminder-parked-repeat").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "continuous-session-reminder-parked-repeat",
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": "task-reminder-parked-repeat-001",
                        "task_key": "task-reminder-parked-repeat",
                        "codex_session_id": "session-topo-parked-repeat-001",
                        "agent_name": "toposem-agent",
                        "proposal_path": str(proposal_path),
                        "proposal_source": "explicit",
                        "proposal_owner": True,
                        "project_history_file": str(history_path),
                        "project_history_file_source": "explicit",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_IDLE_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )
            expected_token = continuous_research_session_evidence_token(config, "session-topo-parked-repeat-001")
            park_continuous_research_session(
                config,
                codex_session_id="session-topo-parked-repeat-001",
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token=expected_token,
                last_signal=PARKED_IDLE_SIGNAL,
                stable_idle_repeat_count=2,
                updated_by="test",
                source="unit",
            )

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-parked-repeat-001",
                    "resumed_session_id": "session-topo-parked-repeat-001",
                    "used_fallback_clone": False,
                    "last_message_text": f"TASKBOARD_SIGNAL={PARKED_IDLE_SIGNAL}\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                processed = process_followups(config)

            self.assertFalse(followup_path(config, "continuous-session-reminder-parked-repeat").exists())
            self.assertTrue(any(item.get("action") == "resolved_parked_idle" for item in processed))
            session_state = load_continuous_research_mode(config, codex_session_id="session-topo-parked-repeat-001")["target_session_state"]
            self.assertEqual(session_state["waiting_state"], PARKED_IDLE_SIGNAL)
            self.assertEqual(session_state["waiting_evidence_token"], expected_token)
            self.assertEqual(session_state["stable_idle_repeat_count"], 4)

    def test_process_followups_guards_invalid_waiting_signal_from_parked_watchdog_without_live_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text("# parked\n", encoding="utf-8")
            history_path.write_text("# history\n", encoding="utf-8")
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-parked-guard-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-reminder-parked-guard-001",
                task_key="task-reminder-parked-guard",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-parked-guard-001",
                workdir="/home/Awei/project",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "continuous-session-reminder-parked-guard").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "continuous-session-reminder-parked-guard",
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": "task-reminder-parked-guard-001",
                        "task_key": "task-reminder-parked-guard",
                        "codex_session_id": "session-topo-parked-guard-001",
                        "agent_name": "toposem-agent",
                        "proposal_path": str(proposal_path),
                        "proposal_source": "explicit",
                        "proposal_owner": True,
                        "project_history_file": str(history_path),
                        "project_history_file_source": "explicit",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS,
                        "min_idle_seconds": 0,
                        "last_signal": PARKED_IDLE_SIGNAL,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )
            expected_token = continuous_research_session_evidence_token(config, "session-topo-parked-guard-001")
            park_continuous_research_session(
                config,
                codex_session_id="session-topo-parked-guard-001",
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token=expected_token,
                last_signal=PARKED_IDLE_SIGNAL,
                stable_idle_repeat_count=2,
                updated_by="test",
                source="unit",
            )

            with patch("codex_taskboard.cli.resume_codex_session_with_prompt", return_value={
                "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                "ok": True,
                "original_session_id": "session-topo-parked-guard-001",
                "resumed_session_id": "session-topo-parked-guard-001",
                "used_fallback_clone": False,
                "last_message_text": f"TASKBOARD_SIGNAL={WAITING_ON_LIVE_TASK_SIGNAL}\n",
                "continue_attempts": 0,
                "recovered_with_continue": False,
                "finished_at": "2026-03-20T10:00:00Z",
            }):
                processed = process_followups(config)

            self.assertFalse(followup_path(config, "continuous-session-reminder-parked-guard").exists())
            self.assertEqual(list(config.followups_root.glob("*.json")), [])
            self.assertTrue(any(item.get("action") == "guarded_invalid_waiting_signal_to_parked_idle" for item in processed))
            session_state = load_continuous_research_mode(config, codex_session_id="session-topo-parked-guard-001")["target_session_state"]
            self.assertEqual(session_state["waiting_state"], PARKED_IDLE_SIGNAL)
            self.assertEqual(session_state["waiting_evidence_token"], expected_token)
            self.assertEqual(session_state["stable_idle_repeat_count"], 4)
            state = load_task_state(config, "task-reminder-parked-guard-001")
            self.assertEqual(state["session_flow_state"], "parked_idle")
            self.assertEqual(state["followup_last_action"], "guarded_invalid_waiting_signal_to_parked_idle")
            self.assertEqual(state["followup_last_signal"], "WAITING_ON_ASYNC")

    def test_process_followups_skips_continuous_session_reminder_during_human_guidance_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-002",
                updated_by="test",
                source="unit",
            )
            set_human_guidance_mode(
                config,
                active=True,
                codex_session_id="session-topo-002",
                lease_seconds=900,
                reason="manual steer",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-idle-queued-002",
                status="queued",
                task_key="task-idle-queued-002",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-002",
                workdir="/home/Awei/project",
            )

            processed = process_followups(config)

            reminder_files = list(config.followups_root.glob("*.json")) if config.followups_root.exists() else []
            self.assertEqual(reminder_files, [])
            self.assertTrue(any(item.get("action") == "continuous_session_reminder_skipped_human_guidance_pause" for item in processed))
            self.assertTrue(load_human_guidance_mode(config, codex_session_id="session-topo-002")["active"])

    def test_process_followups_does_not_schedule_continuous_session_reminder_when_live_running_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-live-running-001",
                status="running",
                task_key="task-live-running",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )

            with patch("codex_taskboard.cli.task_execution_still_live", return_value=True):
                processed = process_followups(config)

            reminder_files = list(config.followups_root.glob("*.json")) if config.followups_root.exists() else []
            self.assertEqual(reminder_files, [])
            self.assertTrue(any(item.get("action") == "continuous_session_reminder_skipped_running" for item in processed))

    def test_process_followups_does_not_duplicate_continuous_session_reminder_when_queued_feedback_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-pending-001",
                task_key="task-pending",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
            )
            spec = {
                "task_id": "task-pending-001",
                "task_key": "task-pending",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
                "success_prompt": "",
                "failure_prompt": "",
                "task_note": "",
                "prompt_max_chars": 12000,
            }
            queue_feedback_resume(
                config,
                task_id="task-pending-001",
                spec=spec,
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-pending-event.json",
                    "feedback_data_path": "/tmp/task-pending-feedback.json",
                    "command_log_path": "/tmp/task-pending.log",
                    "runner_log_path": "/tmp/task-pending-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task done.",
                    "duration_seconds": 5,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="recent_activity",
                min_idle_seconds=120,
            )

            with patch("codex_taskboard.cli.resume_codex_session_with_prompt") as mocked_resume:
                processed = process_followups(config)

            followup_files = list(config.followups_root.glob("*.json"))
            self.assertEqual(len(followup_files), 1)
            payload = json.loads(followup_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["followup_type"], "queued_feedback_resume")
            mocked_resume.assert_not_called()
            self.assertFalse(any(item.get("action") == "continuous_session_reminder_scheduled" for item in processed))

    def test_process_followups_does_not_duplicate_continuous_session_reminder_when_followup_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-followup-existing-001",
                task_key="task-followup-existing",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "followup-existing").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "followup-existing",
                        "task_id": "task-followup-existing-001",
                        "task_key": "task-followup-existing",
                        "codex_session_id": "session-topo-001",
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": "no_new_task_after_feedback",
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": time.time() + 3600,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.resume_codex_session_with_prompt") as mocked_resume:
                processed = process_followups(config)

            followup_files = list(config.followups_root.glob("*.json"))
            self.assertEqual(len(followup_files), 1)
            payload = json.loads(followup_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["codex_session_id"], "session-topo-001")
            self.assertNotEqual(payload.get("followup_type", ""), CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE)
            mocked_resume.assert_not_called()
            self.assertFalse(any(item.get("action") == "continuous_session_reminder_scheduled" for item in processed))

    def test_process_followups_only_schedules_continuous_session_reminder_for_enabled_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-enabled-session-001",
                status="completed",
                task_key="task-enabled-session",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )
            write_state(
                config,
                "task-disabled-session-001",
                status="queued",
                task_key="task-disabled-session",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-other-001",
                workdir="/home/Awei/project",
            )

            with patch("codex_taskboard.cli.resume_codex_session_with_prompt") as mocked_resume:
                process_followups(config)

            reminder_files = list(config.followups_root.glob("*.json"))
            self.assertEqual(len(reminder_files), 1)
            payload = json.loads(reminder_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["codex_session_id"], "session-topo-001")
            self.assertEqual(payload["followup_type"], CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE)
            mocked_resume.assert_not_called()

    def test_process_followups_defers_continuous_session_reminder_while_session_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-reminder-anchor-001",
                task_key="task-reminder-anchor",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )
            write_state(
                config,
                "task-reminder-live-001",
                status="running",
                task_key="task-reminder-live",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "continuous-session-reminder").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "continuous-session-reminder",
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": "task-reminder-anchor-001",
                        "task_key": "task-reminder-anchor",
                        "codex_session_id": "session-topo-001",
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_IDLE_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.task_execution_still_live", return_value=True), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt"
            ) as mocked_resume:
                processed = process_followups(config)

            followup_files = list(config.followups_root.glob("*.json"))
            self.assertEqual(len(followup_files), 1)
            payload = json.loads(followup_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["followup_type"], CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE)
            self.assertEqual(payload["last_action"], "deferred:session_has_running_task")
            mocked_resume.assert_not_called()
            state = load_task_state(config, str(payload["task_id"]))
            self.assertEqual(state["followup_last_action"], "deferred:session_has_running_task")
            self.assertTrue(any(item.get("action") == "deferred_session_has_running_task" for item in processed))

    def test_process_followups_parks_continuous_idle_loop_after_repeated_local_microsteps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = Path(tmpdir) / "PLAN.md"
            history_path = Path(tmpdir) / "HISTORY.md"
            proposal_path.write_text("# parked\n", encoding="utf-8")
            history_path.write_text("# history\n", encoding="utf-8")
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-loop-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-reminder-loop-001",
                task_key="task-reminder-loop",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-loop-001",
                workdir="/home/Awei/project",
                proposal_path=str(proposal_path),
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            reminder_key = "continuous-session-reminder-loop"
            followup_path(config, reminder_key).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": reminder_key,
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": "task-reminder-loop-001",
                        "task_key": "task-reminder-loop",
                        "codex_session_id": "session-topo-loop-001",
                        "agent_name": "toposem-agent",
                        "proposal_path": str(proposal_path),
                        "proposal_source": "explicit",
                        "proposal_owner": True,
                        "project_history_file": str(history_path),
                        "project_history_file_source": "explicit",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_IDLE_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )
            expected_token = continuous_research_session_evidence_token(config, "session-topo-loop-001")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-loop-001",
                    "resumed_session_id": "session-topo-loop-001",
                    "used_fallback_clone": False,
                    "last_message_text": "TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                processed_first = process_followups(config)

            reminder_files = list(config.followups_root.glob("*.json"))
            self.assertEqual(len(reminder_files), 1)
            reminder_file = reminder_files[0]
            self.assertTrue(any(item.get("action") == "local_microstep_followup_scheduled" for item in processed_first))
            session_state = load_continuous_research_mode(config, codex_session_id="session-topo-loop-001")["target_session_state"]
            self.assertEqual(session_state["waiting_state"], "")
            self.assertEqual(session_state["last_evidence_token"], expected_token)
            self.assertEqual(session_state["stable_idle_repeat_count"], 1)

            reminder_payload = json.loads(reminder_file.read_text(encoding="utf-8"))
            reminder_payload["check_after_ts"] = 0
            reminder_file.write_text(json.dumps(reminder_payload), encoding="utf-8")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-loop-001",
                    "resumed_session_id": "session-topo-loop-001",
                    "used_fallback_clone": False,
                    "last_message_text": "TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:05:00Z",
                },
            ):
                processed_second = process_followups(config)

            self.assertTrue(reminder_file.exists())
            self.assertTrue(any(item.get("action") == "local_microstep_followup_scheduled" for item in processed_second))
            state = load_task_state(config, "task-reminder-loop-001")
            self.assertEqual(state["session_flow_state"], "local_active")
            self.assertEqual(state["followup_status"], "scheduled")

            reminder_payload = json.loads(reminder_file.read_text(encoding="utf-8"))
            reminder_payload["check_after_ts"] = 0
            reminder_file.write_text(json.dumps(reminder_payload), encoding="utf-8")

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-loop-001",
                    "resumed_session_id": "session-topo-loop-001",
                    "used_fallback_clone": False,
                    "last_message_text": "TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:10:00Z",
                },
            ):
                processed_third = process_followups(config)

            self.assertFalse(reminder_file.exists())
            self.assertTrue(any(item.get("action") == "resolved_parked_idle" for item in processed_third))
            state = load_task_state(config, "task-reminder-loop-001")
            self.assertEqual(state["session_flow_state"], "parked_idle")
            self.assertEqual(state["followup_status"], "resolved")
            self.assertEqual(state["followup_last_action"], "resolved_parked_idle")
            session_state = load_continuous_research_mode(config, codex_session_id="session-topo-loop-001")["target_session_state"]
            self.assertEqual(session_state["waiting_state"], PARKED_IDLE_SIGNAL)
            self.assertEqual(session_state["waiting_evidence_token"], expected_token)
            self.assertEqual(session_state["last_signal"], "LOCAL_MICROSTEP_BATCH")
            self.assertGreaterEqual(session_state["stable_idle_repeat_count"], 3)

    def test_process_followups_local_microstep_batch_reschedules_reminder_not_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-local-batch-001"
            task_id = "task-local-batch-001"
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                task_id,
                task_key="task-local-batch",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id=session_id,
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            reminder_key = continuous_session_followup_key_for(session_id)
            reminder_file = followup_path(config, reminder_key)
            reminder_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": reminder_key,
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": task_id,
                        "task_key": "task-local-batch",
                        "codex_session_id": session_id,
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_IDLE_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": session_id,
                    "resumed_session_id": session_id,
                    "used_fallback_clone": False,
                    "last_message_text": f"TASKBOARD_SIGNAL={LOCAL_MICROSTEP_BATCH_SIGNAL}\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ):
                processed = process_followups(config)

            reminder_payload = json.loads(reminder_file.read_text(encoding="utf-8"))
            state = load_task_state(config, task_id)

            self.assertTrue(any(item.get("action") == "local_microstep_followup_scheduled" for item in processed))
            self.assertEqual(reminder_payload["reason"], "local_microstep_batch")
            self.assertEqual(reminder_payload["last_action"], "scheduled:local_microstep_batch")
            self.assertEqual(state["followup_status"], "scheduled")
            self.assertEqual(state["followup_last_action"], "scheduled:local_microstep_batch")

    def test_process_followups_inline_continue_no_wake_resolves_without_reminder_or_park_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-inline-continue-001"
            task_id = "task-inline-continue-001"
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                task_id,
                task_key="task-inline-continue",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id=session_id,
                workdir="/home/Awei/project",
            )
            mode_path = config.app_home / "continuous_research_mode.json"
            mode_payload = json.loads(mode_path.read_text(encoding="utf-8"))
            mode_payload["sessions"][session_id].update(
                {
                    "waiting_state": PARKED_IDLE_SIGNAL,
                    "waiting_reason": "unit_test_waiting",
                    "waiting_since": "2026-03-19T00:00:00Z",
                    "waiting_evidence_token": "old-evidence-token",
                    "last_evidence_token": "old-evidence-token",
                    "last_signal": PARKED_IDLE_SIGNAL,
                    "stable_idle_repeat_count": 7,
                    "next_action_hash": "repeat-action-hash",
                    "next_action_repeat_count": CONTINUOUS_RESEARCH_LOCAL_FASTPATH_REPEAT_THRESHOLD,
                }
            )
            mode_path.write_text(json.dumps(mode_payload), encoding="utf-8")
            config.followups_root.mkdir(parents=True, exist_ok=True)
            reminder_key = continuous_session_followup_key_for(session_id)
            reminder_file = followup_path(config, reminder_key)
            reminder_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": reminder_key,
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": task_id,
                        "task_key": "task-inline-continue",
                        "codex_session_id": session_id,
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_IDLE_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": session_id,
                    "resumed_session_id": session_id,
                    "used_fallback_clone": False,
                    "last_message_text": f"TASKBOARD_SIGNAL={LOCAL_CONTINUE_NO_WAKE_SIGNAL}\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:05:00Z",
                },
            ):
                processed = process_followups(config)

            session_state = load_continuous_research_mode(config, codex_session_id=session_id)["target_session_state"]
            state = load_task_state(config, task_id)

            self.assertFalse(reminder_file.exists())
            self.assertTrue(any(item.get("action") == "inline_continue_no_wake" for item in processed))
            self.assertEqual(session_state["waiting_state"], "")
            self.assertEqual(session_state["last_signal"], LOCAL_CONTINUE_NO_WAKE_SIGNAL)
            self.assertEqual(session_state["stable_idle_repeat_count"], 0)
            self.assertEqual(session_state["next_action_repeat_count"], 0)
            self.assertEqual(state["followup_status"], "resolved")
            self.assertEqual(state["followup_last_action"], "resolved_inline_continue_no_wake")

    def test_followup_log_tracks_continuous_reminder_defer_and_reschedule_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-followup-log-001"
            task_id = "task-followup-log-001"
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id=session_id,
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                task_id,
                task_key="task-followup-log",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id=session_id,
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            reminder_key = continuous_session_followup_key_for(session_id)
            reminder_file = followup_path(config, reminder_key)
            reminder_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": reminder_key,
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": task_id,
                        "task_key": "task-followup-log",
                        "codex_session_id": session_id,
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_IDLE_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )
            followup_path(config, "other-live-followup").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "other-live-followup",
                        "task_id": task_id,
                        "task_key": "task-followup-log",
                        "codex_session_id": session_id,
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": "no_new_task_after_feedback",
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": time.time() + 3600,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            process_followups(config)

            deferred_payload = json.loads(reminder_file.read_text(encoding="utf-8"))
            deferred_log = (config.followups_root / "followup.log").read_text(encoding="utf-8")
            self.assertEqual(deferred_payload["last_action"], "deferred:session_has_other_followup")
            self.assertIn('"event": "deferred"', deferred_log)
            self.assertIn('"reason": "session_has_other_followup"', deferred_log)
            self.assertIn(f'"followup_key": "{reminder_key}"', deferred_log)

            for stale_followup in config.followups_root.glob("*.json"):
                if stale_followup.name != reminder_file.name:
                    stale_followup.unlink()
            deferred_payload["check_after_ts"] = 0
            reminder_file.write_text(json.dumps(deferred_payload), encoding="utf-8")

            with patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": session_id,
                    "resumed_session_id": session_id,
                    "used_fallback_clone": False,
                    "last_message_text": "TASKBOARD_SIGNAL=NO_FURTHER_TASKS\n",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:10:00Z",
                },
            ):
                process_followups(config)

            rescheduled_payload = json.loads(reminder_file.read_text(encoding="utf-8"))
            rescheduled_log = (config.followups_root / "followup.log").read_text(encoding="utf-8")
            self.assertEqual(rescheduled_payload["last_action"], f"scheduled:{CONTINUOUS_RESEARCH_IDLE_REASON}")
            self.assertIn('"event": "scheduled"', rescheduled_log)
            self.assertIn(f'"reason": "{CONTINUOUS_RESEARCH_IDLE_REASON}"', rescheduled_log)
            self.assertIn('"detail": "continuous_session_reminder_rescheduled"', rescheduled_log)

    def test_process_followups_defers_continuous_session_reminder_when_other_followup_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-reminder-anchor-002",
                task_key="task-reminder-anchor-002",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "continuous-session-reminder-existing").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "continuous-session-reminder-existing",
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "task_id": "task-reminder-anchor-002",
                        "task_key": "task-reminder-anchor-002",
                        "codex_session_id": "session-topo-001",
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_IDLE_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )
            followup_path(config, "other-live-followup").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "other-live-followup",
                        "task_id": "task-reminder-anchor-002",
                        "task_key": "task-reminder-anchor-002",
                        "codex_session_id": "session-topo-001",
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": "no_new_task_after_feedback",
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": time.time() + 3600,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.resume_codex_session_with_prompt") as mocked_resume:
                processed = process_followups(config)

            followup_payloads = [
                json.loads(path.read_text(encoding="utf-8")) for path in config.followups_root.glob("*.json")
            ]
            reminder_payload = next(
                payload for payload in followup_payloads if payload.get("followup_type") == CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE
            )
            self.assertEqual(reminder_payload["last_action"], "deferred:session_has_other_followup")
            mocked_resume.assert_not_called()
            state = load_task_state(config, "task-reminder-anchor-002")
            self.assertEqual(state["followup_last_action"], "deferred:session_has_other_followup")
            self.assertTrue(any(item.get("action") == "deferred_session_has_other_followup" for item in processed))

    def test_ensure_continuous_session_reminders_unparks_when_recent_local_next_action_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "session-next-action-reminder-001"
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
            (log_dir / "20260411T010500Z-next-action.md").write_text(
                "\n".join(
                    [
                        "# next action",
                        f"- created_at: `{created_at}`",
                        "## 5. Next bounded action",
                        "1. 起草新 family 的 problem formulation 与 method 骨架。",
                        "2. 当前下一步仍保持 CPU-only，无需 GPU，无需 future callback，无需 live task。",
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
            write_state(
                config,
                "task-next-action-anchor-001",
                task_key="task-next-action-anchor",
                status="completed",
                codex_session_id=session_id,
                workdir="/home/Awei/project",
                proposal_path="/home/Awei/project/PLAN.md",
                proposal_source="explicit",
                proposal_owner=True,
                project_history_file=str(history_path),
                project_history_file_source="explicit",
            )
            park_continuous_research_session(
                config,
                codex_session_id=session_id,
                waiting_state=PARKED_IDLE_SIGNAL,
                waiting_reason="unit_test_waiting",
                evidence_token=continuous_research_session_evidence_token(config, session_id),
                last_signal=PARKED_IDLE_SIGNAL,
                updated_by="test",
                source="unit",
            )

            processed = ensure_continuous_research_session_reminders(config)

            self.assertTrue(
                any(
                    item.get("action") == "continuous_session_reminder_scheduled_next_action"
                    and item.get("reason") == CONTINUOUS_RESEARCH_NEXT_ACTION_REASON
                    for item in processed
                )
            )
            session_state = load_continuous_research_mode(config, codex_session_id=session_id)["target_session_state"]
            self.assertEqual(session_state["waiting_state"], "")
            self.assertEqual(session_state["last_signal"], LOCAL_MICROSTEP_BATCH_SIGNAL)
            self.assertEqual(str(session_state.get("source", "")), "continuous-session-reminder-next-action-unpark")
            reminder_files = list(config.followups_root.glob("*.json"))
            self.assertEqual(len(reminder_files), 1)
            reminder_payload = json.loads(reminder_files[0].read_text(encoding="utf-8"))
            self.assertEqual(reminder_payload["reason"], CONTINUOUS_RESEARCH_NEXT_ACTION_REASON)

    def test_process_followups_defers_continuous_followup_while_session_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-topo-001",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-continuous-anchor-001",
                task_key="task-continuous-anchor",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )
            write_state(
                config,
                "task-continuous-live-001",
                status="running",
                task_key="task-continuous-live",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "continuous-followup").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "continuous-followup",
                        "task_id": "task-continuous-anchor-001",
                        "task_key": "task-continuous-anchor",
                        "codex_session_id": "session-topo-001",
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": CONTINUOUS_RESEARCH_REASON,
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.task_execution_still_live", return_value=True), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt"
            ) as mocked_resume:
                processed = process_followups(config)

            rebound_key = followup_key_for(
                {
                    "task_id": "task-continuous-anchor-001",
                    "task_key": "task-continuous-anchor",
                    "codex_session_id": "session-topo-001",
                    "agent_name": "toposem-agent",
                }
            )
            payload = json.loads(followup_path(config, rebound_key).read_text(encoding="utf-8"))
            self.assertEqual(payload["last_action"], "deferred:session_has_running_task")
            mocked_resume.assert_not_called()
            state = load_task_state(config, "task-continuous-anchor-001")
            self.assertEqual(state["followup_last_action"], "deferred:session_has_running_task")
            self.assertTrue(any(item.get("action") == "deferred_session_has_running_task" for item in processed))

    def test_process_followups_defers_followup_during_human_guidance_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_human_guidance_mode(
                config,
                active=True,
                codex_session_id="session-topo-003",
                lease_seconds=900,
                reason="manual steer",
                updated_by="test",
                source="unit",
            )
            write_state(
                config,
                "task-followup-human-pause-001",
                task_key="task-followup-human-pause",
                feedback_mode="auto",
                agent_name="toposem-agent",
                codex_session_id="session-topo-003",
                workdir="/home/Awei/project",
            )
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "followup-human-pause").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "followup_key": "followup-human-pause",
                        "task_id": "task-followup-human-pause-001",
                        "task_key": "task-followup-human-pause",
                        "codex_session_id": "session-topo-003",
                        "agent_name": "toposem-agent",
                        "workdir": "/home/Awei/project",
                        "reason": "no_new_task_after_feedback",
                        "created_at": "2026-03-19T00:00:00Z",
                        "check_after_ts": 0,
                        "interval_seconds": 300,
                        "min_idle_seconds": 0,
                        "nudge_count": 0,
                        "stopped": False,
                    }
                ),
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.resume_codex_session_with_prompt") as mocked_resume:
                processed = process_followups(config)

            mocked_resume.assert_not_called()
            state = load_task_state(config, "task-followup-human-pause-001")
            self.assertEqual(state["followup_last_action"], "deferred:human_guidance_pause")
            self.assertEqual(state["session_flow_state"], "human_guidance_paused")
            self.assertTrue(any(item.get("action") == "deferred_human_guidance_pause" for item in processed))

    def test_process_followups_rebinds_stale_session_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            task_id = "task-rebind-001"
            task_spec = {
                "version": 1,
                "task_id": task_id,
                "task_key": "task-rebind",
                "execution_mode": "shell",
                "workdir": str(project_dir),
                "command": "python train.py",
                "codex_session_id": "session-new",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 3600,
                "fallback_provider": "",
                "prompt_max_chars": 12000,
            }
            write_task_spec(config, task_id, task_spec)
            write_state(
                config,
                task_id,
                task_key="task-rebind",
                workdir=str(project_dir),
                agent_name="toposem-agent",
                codex_session_id="session-new",
            )
            stale_followup = {
                "version": 1,
                "followup_key": followup_key_for(
                    {
                        "task_id": task_id,
                        "task_key": "task-rebind",
                        "codex_session_id": "session-old",
                        "agent_name": "toposem-agent",
                    }
                ),
                "task_id": task_id,
                "task_key": "task-rebind",
                "codex_session_id": "session-old",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "workdir": str(project_dir),
                "reason": "no_new_task_after_feedback",
                "created_at": "2026-03-19T00:00:00Z",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
            }
            config.followups_root.mkdir(parents=True, exist_ok=True)
            stale_key = stale_followup["followup_key"]
            followup_path(config, stale_key).write_text(json.dumps(stale_followup), encoding="utf-8")
            new_key = followup_key_for(task_spec)

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0) as mocked_activity, patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-new",
                    "resumed_session_id": "session-new",
                    "used_fallback_clone": False,
                    "last_message_text": "",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ) as mocked_resume:
                process_followups(config)

            self.assertEqual(mocked_activity.call_args.args[1], "session-new")
            self.assertEqual(mocked_resume.call_args.args[1]["codex_session_id"], "session-new")
            self.assertFalse(followup_path(config, stale_key).exists())
            self.assertTrue(followup_path(config, new_key).exists())

    def test_process_followups_recovers_missing_queued_feedback_entity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            event_path = Path(tmpdir) / "task-recover-event.json"
            event_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "event_path": str(event_path),
                        "feedback_data_path": "/tmp/task-recover-feedback.json",
                        "command_log_path": "/tmp/task-recover.log",
                        "runner_log_path": "/tmp/task-recover-runner.log",
                        "failure_kind": "completed",
                        "failure_summary": "Recovered task done.",
                        "duration_seconds": 12,
                        "artifact_context": [],
                        "log_tail": "",
                    }
                ),
                encoding="utf-8",
            )
            task_id = "task-recover-001"
            spec = {
                "version": 1,
                "task_id": task_id,
                "task_key": "task-recover",
                "execution_mode": "shell",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "codex_session_id": "session-topo-001",
                "agent_name": "platform-maintainer",
                "feedback_mode": "auto",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 3600,
                "fallback_provider": "",
                "prompt_max_chars": 12000,
            }
            write_task_spec(config, task_id, spec)
            write_state(
                config,
                task_id,
                task_key="task-recover",
                feedback_mode="auto",
                agent_name="platform-maintainer",
                codex_session_id="session-topo-001",
                pending_feedback=True,
                followup_status="scheduled",
                followup_last_action="queued_feedback_resume:queue_already_open",
                last_event_path=str(event_path),
            )

            with patch("codex_taskboard.cli.latest_session_activity_ts", return_value=0.0), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt",
                return_value={
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "ok": True,
                    "original_session_id": "session-topo-001",
                    "resumed_session_id": "session-topo-001",
                    "used_fallback_clone": False,
                    "last_message_text": "",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                    "finished_at": "2026-03-20T10:00:00Z",
                },
            ) as mocked_resume:
                processed = process_followups(config)

            self.assertTrue(any(item.get("action") == "recovered_missing_queued_feedback_entity" for item in processed))
            self.assertTrue(any(item.get("action") == "queued_feedback_delivered" for item in processed))
            mocked_resume.assert_called_once()
            self.assertFalse(followup_path(config, queued_feedback_key_for(spec)).exists())
            state = load_task_state(config, task_id)
            self.assertFalse(state["pending_feedback"])
            self.assertEqual(state["followup_status"], "resolved")
            self.assertEqual(state["followup_last_action"], "queued_feedback_delivered")

    def test_followup_stop_updates_all_tasks_in_queued_feedback_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir)
            config = build_config(app_home)
            write_state(config, "task-a", task_key="task-a", feedback_mode="auto", agent_name="toposem-agent", codex_session_id="session-topo-001")
            write_state(config, "task-b", task_key="task-b", feedback_mode="auto", agent_name="toposem-agent", codex_session_id="session-topo-001")
            base_spec = {
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
                "success_prompt": "",
                "failure_prompt": "",
                "task_note": "",
                "prompt_max_chars": 12000,
            }
            queue_feedback_resume(
                config,
                task_id="task-a",
                spec={**base_spec, "task_id": "task-a", "task_key": "task-a"},
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-a-event.json",
                    "feedback_data_path": "/tmp/task-a-feedback.json",
                    "command_log_path": "/tmp/task-a.log",
                    "runner_log_path": "/tmp/task-a-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task A done.",
                    "duration_seconds": 5,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="recent_activity",
                min_idle_seconds=1,
            )
            queue_feedback_resume(
                config,
                task_id="task-b",
                spec={**base_spec, "task_id": "task-b", "task_key": "task-b"},
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-b-event.json",
                    "feedback_data_path": "/tmp/task-b-feedback.json",
                    "command_log_path": "/tmp/task-b.log",
                    "runner_log_path": "/tmp/task-b-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task B done.",
                    "duration_seconds": 5,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="queue_already_open",
                min_idle_seconds=1,
            )

            rc = command_followup_stop(
                argparse.Namespace(
                    app_home=str(app_home),
                    codex_home=str(app_home / "codex-home"),
                    codex_bin="codex",
                    tmux_bin="tmux",
                    task_id=None,
                    agent_name="toposem-agent",
                )
            )

            self.assertEqual(rc, 0)
            state_a = load_task_state(config, "task-a")
            state_b = load_task_state(config, "task-b")
            self.assertEqual(state_a["followup_status"], "stopped")
            self.assertEqual(state_b["followup_status"], "stopped")
            self.assertFalse(state_a["pending_feedback"])
            self.assertFalse(state_b["pending_feedback"])

    def test_followup_reconcile_resolves_missing_scheduled_entity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir)
            config = build_config(app_home)
            write_state(
                config,
                "task-missing-followup",
                task_key="task-missing-followup",
                status="completed",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                followup_status="scheduled",
                followup_last_action="scheduled:no_new_task_after_feedback",
                pending_feedback=False,
            )

            rc = command_followup_reconcile(
                argparse.Namespace(
                    app_home=str(app_home),
                    codex_home=str(app_home / "codex-home"),
                    codex_bin="codex",
                    tmux_bin="tmux",
                    task_id=None,
                    agent_name=None,
                    dry_run=False,
                )
            )

            self.assertEqual(rc, 0)
            state = load_task_state(config, "task-missing-followup")
            self.assertEqual(state["followup_status"], "resolved")
            self.assertEqual(state["followup_last_action"], "reconciled_missing_followup_entity")
            self.assertFalse(state["pending_feedback"])

    def test_followup_reconcile_stops_missing_pending_feedback_entity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir)
            config = build_config(app_home)
            write_state(
                config,
                "task-missing-pending",
                task_key="task-missing-pending",
                status="completed",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
                followup_status="scheduled",
                followup_last_action="queued_feedback_resume:recent_activity",
                pending_feedback=True,
            )

            rc = command_followup_reconcile(
                argparse.Namespace(
                    app_home=str(app_home),
                    codex_home=str(app_home / "codex-home"),
                    codex_bin="codex",
                    tmux_bin="tmux",
                    task_id=None,
                    agent_name=None,
                    dry_run=False,
                )
            )

            self.assertEqual(rc, 0)
            state = load_task_state(config, "task-missing-pending")
            self.assertEqual(state["followup_status"], "stopped")
            self.assertEqual(state["followup_last_action"], "reconciled_missing_followup_entity_pending_feedback")
            self.assertFalse(state["pending_feedback"])

    def test_process_followups_defers_when_session_output_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "task-output-busy-001",
                task_key="task-output-busy",
                agent_name="toposem-agent",
                codex_session_id="session-topo-001",
            )
            followup = {
                "version": 1,
                "followup_key": "followup-output-busy-001",
                "task_id": "task-output-busy-001",
                "task_key": "task-output-busy",
                "codex_session_id": "session-topo-001",
                "agent_name": "toposem-agent",
                "workdir": "/home/Awei/project",
                "reason": "no_new_task_after_feedback",
                "created_at": "2026-03-19T00:00:00Z",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
            }
            config.followups_root.mkdir(parents=True, exist_ok=True)
            followup_path(config, "followup-output-busy-001").write_text(json.dumps(followup), encoding="utf-8")

            with patch(
                "codex_taskboard.cli.session_output_busy_snapshot",
                return_value={
                    "busy": True,
                    "detail": "active_codex_resume_process",
                    "retry_after_seconds": 17,
                    "latest_activity_ts": 0.0,
                },
            ), patch("codex_taskboard.cli.newer_task_exists", return_value=False), patch(
                "codex_taskboard.cli.resume_codex_session_with_prompt"
            ) as mocked_resume:
                processed = process_followups(config)

            mocked_resume.assert_not_called()
            deferred_key = next(item["followup_key"] for item in processed if item.get("action") == "deferred_session_output_busy")
            updated_followup = json.loads(followup_path(config, deferred_key).read_text(encoding="utf-8"))
            self.assertEqual(updated_followup["last_deferred_reason"], "session_output_busy")
            self.assertEqual(updated_followup["last_action"], "deferred:session_output_busy")
            state = load_task_state(config, "task-output-busy-001")
            self.assertEqual(state["followup_last_action"], "deferred:session_output_busy")
            self.assertTrue(any(item.get("action") == "deferred_session_output_busy" for item in processed))

    def test_resume_codex_session_with_prompt_defers_when_session_output_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            output_path = Path(tmpdir) / "last-message.txt"
            log_path = Path(tmpdir) / "runner.log"
            spec = {
                "task_id": "task-resume-output-busy",
                "codex_session_id": "session-topo-001",
                "codex_exec_mode": "dangerous",
                "workdir": str(Path(tmpdir)),
                "resume_timeout_seconds": 30,
            }

            with patch(
                "codex_taskboard.cli.session_output_busy_snapshot",
                return_value={
                    "busy": True,
                    "detail": "active_codex_resume_process",
                    "retry_after_seconds": 23,
                    "latest_activity_ts": 123.0,
                },
            ), patch("codex_taskboard.cli.run_codex_prompt_with_continue_recovery") as mocked_run:
                result = resume_codex_session_with_prompt(
                    config,
                    spec,
                    "resume prompt",
                    output_last_message_path=str(output_path),
                    log_path=log_path,
                    min_idle_seconds=0,
                )

            mocked_run.assert_not_called()
            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "session_output_busy")
            self.assertEqual(result["retry_after_seconds"], 23)
            self.assertFalse(result["attempted"])

    def test_resume_codex_session_with_prompt_keeps_rate_limit_behavior_distinct_from_output_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            output_path = Path(tmpdir) / "last-message.txt"
            log_path = Path(tmpdir) / "runner.log"
            spec = {
                "task_id": "task-resume-rate-limit",
                "codex_session_id": "session-topo-001",
                "codex_exec_mode": "dangerous",
                "workdir": str(Path(tmpdir)),
                "resume_timeout_seconds": 30,
            }
            completed = subprocess.CompletedProcess(
                args=["codex", "exec", "resume"],
                returncode=1,
                stdout="429 too many requests exceeded retry limit",
                stderr="",
            )

            with patch(
                "codex_taskboard.cli.session_output_busy_snapshot",
                return_value={
                    "busy": False,
                    "detail": "",
                    "retry_after_seconds": 0,
                    "latest_activity_ts": 0.0,
                },
            ), patch(
                "codex_taskboard.cli.run_codex_prompt_with_continue_recovery",
                return_value={
                    "completed": completed,
                    "message_written": False,
                    "last_message_text": "",
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                },
            ):
                result = resume_codex_session_with_prompt(
                    config,
                    spec,
                    "resume prompt",
                    output_last_message_path=str(output_path),
                    log_path=log_path,
                    min_idle_seconds=0,
                )

            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "rate_limited")
            self.assertTrue(result["attempted"])


if __name__ == "__main__":
    unittest.main()
