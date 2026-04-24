import argparse
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import (
    AppConfig,
    bootstrap_successor_session_after_closeout,
    command_migrate_session,
    followup_path,
    followup_key_for,
    load_continuous_research_mode,
    load_active_feedback_runtime,
    load_human_guidance_mode,
    load_task_spec,
    load_task_state,
    queue_feedback_resume,
    queued_feedback_key_for,
    register_active_feedback_runtime,
    resume_codex_session_with_prompt,
    session_migration_entry,
    session_lock_name,
    set_continuous_research_mode,
    set_human_guidance_mode,
    task_last_message_path,
    update_session_migration_entry,
    write_task_spec,
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


def cli_args(config: AppConfig, **overrides: object) -> argparse.Namespace:
    payload = {
        "app_home": str(config.app_home),
        "codex_home": str(config.codex_home),
        "codex_bin": config.codex_bin,
        "tmux_bin": config.tmux_bin,
        "from_session_id": "",
        "to_session_id": "",
        "interrupt_grace_seconds": 0,
        "dry_run": False,
    }
    payload.update(overrides)
    return argparse.Namespace(**payload)


class SessionMigrationTests(unittest.TestCase):
    def test_session_lock_name_truncates_overlong_identifiers(self) -> None:
        long_session_id = "session-" + ("very-long-segment-" * 30)
        lock_name = session_lock_name(long_session_id)

        self.assertLessEqual(len(lock_name), 120)
        self.assertNotEqual(lock_name, long_session_id)

    def test_load_active_feedback_runtime_drops_stale_pid_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            register_active_feedback_runtime(
                config,
                operation_id="runtime-stale-001",
                session_id="session-old-001",
                requested_session_id="session-old-001",
                pid=999999,
                pgid=999999,
                source_kind="queued_feedback_followup",
                source_key="queued-feedback-old",
                task_id="task-main-001",
                task_ids=["task-main-001"],
                followup_key="queued-feedback-old",
            )

            with patch("codex_taskboard.cli.pid_exists", return_value=False):
                payload = load_active_feedback_runtime(config)

            self.assertEqual(payload["entries"], [])

    def test_resume_redirects_completed_migration_to_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            update_session_migration_entry(
                config,
                from_session_id="session-old-001",
                to_session_id="session-new-001",
                state="completed",
                updated_by="test",
                source="unit",
            )
            spec = {
                "task_id": "task-a",
                "codex_session_id": "session-old-001",
                "workdir": "/home/Awei",
                "command": "sleep 1",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
            }

            def fake_run_local_interactive_codex(
                _config,
                *,
                command: list[str],
                output_last_message_path: str,
                **_kwargs,
            ) -> dict[str, object]:
                self.assertIn("session-new-001", command)
                Path(output_last_message_path).write_text("assistant reply\n", encoding="utf-8")
                return {
                    "completed": subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout="assistant reply\n",
                        stderr="",
                    ),
                    "session_id": "session-new-001",
                    "message_written": True,
                    "last_message_text": "assistant reply\n",
                }

            with patch(
                "codex_taskboard.cli.run_local_interactive_codex",
                side_effect=fake_run_local_interactive_codex,
            ):
                result = resume_codex_session_with_prompt(
                    config,
                    spec,
                    "background batch",
                    output_last_message_path=str(task_last_message_path(config, "task-a")),
                    log_path=config.app_home / "resume.log",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["original_session_id"], "session-old-001")
            self.assertEqual(result["resumed_session_id"], "session-new-001")

    def test_resume_defers_while_session_migration_is_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            update_session_migration_entry(
                config,
                from_session_id="session-old-001",
                to_session_id="session-new-001",
                state="migrating",
                updated_by="test",
                source="unit",
            )
            spec = {
                "task_id": "task-a",
                "codex_session_id": "session-old-001",
                "workdir": "/home/Awei",
                "command": "sleep 1",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
            }

            result = resume_codex_session_with_prompt(
                config,
                spec,
                "background batch",
                output_last_message_path=str(task_last_message_path(config, "task-a")),
                log_path=config.app_home / "resume.log",
            )

            self.assertFalse(result["ok"])
            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "session_migration_in_progress")
            self.assertEqual(result["resumed_session_id"], "session-new-001")

    def test_command_migrate_session_moves_bindings_and_buffered_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            base_spec = {
                "task_id": "task-main-001",
                "task_key": "task-main",
                "codex_session_id": "session-old-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
                "resume_timeout_seconds": 3600,
                "prompt_max_chars": 12000,
                "fallback_provider": "",
            }
            write_task_spec(config, "task-main-001", dict(base_spec))
            write_task_state(
                config,
                "task-main-001",
                {
                    "version": 1,
                    "task_id": "task-main-001",
                    "task_key": "task-main",
                    "status": "completed",
                    "feedback_mode": "auto",
                    "agent_name": "toposem-agent",
                    "codex_session_id": "session-old-001",
                    "submitted_at": "2026-03-20T00:00:00Z",
                    "updated_at": "2026-03-20T00:00:00Z",
                },
            )
            hidden_spec = dict(base_spec)
            hidden_spec["task_id"] = "task-hidden-001"
            hidden_spec["task_key"] = "task-hidden"
            write_task_spec(config, "task-hidden-001", hidden_spec)
            write_task_state(
                config,
                "task-hidden-001",
                {
                    "version": 1,
                    "task_id": "task-hidden-001",
                    "task_key": "task-hidden",
                    "status": "superseded",
                    "feedback_mode": "auto",
                    "agent_name": "toposem-agent",
                    "codex_session_id": "session-old-001",
                    "submitted_at": "2026-03-20T00:00:00Z",
                    "updated_at": "2026-03-20T00:00:00Z",
                },
            )
            queue_feedback_resume(
                config,
                task_id="task-main-001",
                spec=base_spec,
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-main-event.json",
                    "feedback_data_path": "/tmp/task-main-feedback.json",
                    "command_log_path": "/tmp/task-main.log",
                    "runner_log_path": "/tmp/task-main-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task done.",
                    "duration_seconds": 5,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="recent_activity",
                min_idle_seconds=1,
            )
            old_followup_key = queued_feedback_key_for(base_spec)
            register_active_feedback_runtime(
                config,
                operation_id="runtime-op-001",
                session_id="session-old-001",
                requested_session_id="session-old-001",
                pid=os.getpid(),
                pgid=os.getpgrp(),
                source_kind="queued_feedback_followup",
                source_key=old_followup_key,
                task_id="task-main-001",
                task_ids=["task-main-001"],
                followup_key=old_followup_key,
            )
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-old-001",
                updated_by="test",
                source="unit",
            )
            set_human_guidance_mode(
                config,
                active=True,
                codex_session_id="session-old-001",
                lease_seconds=900,
                reason="manual steer",
                updated_by="test",
                source="unit",
            )

            stdout = io.StringIO()
            with patch("codex_taskboard.cli.codex_session_exists_for_spec", return_value=True), patch(
                "codex_taskboard.cli.signal_process_group"
            ) as mocked_signal, patch("codex_taskboard.cli.pid_exists", return_value=True), contextlib.redirect_stdout(stdout):
                exit_code = command_migrate_session(
                    cli_args(
                        config,
                        from_session_id="session-old-001",
                        to_session_id="session-new-001",
                    )
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["from_session_id"], "session-old-001")
            self.assertEqual(payload["to_session_id"], "session-new-001")
            self.assertEqual(load_task_spec(config, "task-main-001")["codex_session_id"], "session-new-001")
            self.assertEqual(load_task_state(config, "task-main-001")["codex_session_id"], "session-new-001")
            self.assertEqual(load_task_spec(config, "task-hidden-001")["codex_session_id"], "session-old-001")
            new_followup_key = queued_feedback_key_for({**base_spec, "codex_session_id": "session-new-001"})
            self.assertFalse(followup_path(config, old_followup_key).exists())
            self.assertTrue(followup_path(config, new_followup_key).exists())
            self.assertTrue(load_continuous_research_mode(config, codex_session_id="session-new-001")["enabled"])
            self.assertTrue(load_human_guidance_mode(config, codex_session_id="session-new-001")["active"])
            migration = session_migration_entry(config, "session-old-001")
            self.assertEqual(migration["state"], "completed")
            self.assertEqual(migration["to_session_id"], "session-new-001")
            self.assertEqual(len(migration["buffered_runtime_entries"]), 1)
            buffered = migration["buffered_runtime_entries"][0]
            self.assertEqual(buffered["redirected_session_id"], "session-new-001")
            state = load_task_state(config, "task-main-001")
            self.assertTrue(state["pending_feedback"])
            self.assertEqual(state["followup_last_action"], "buffered_session_migration_cutover")

    def test_successor_bootstrap_after_closeout_creates_new_session_and_migrates_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            base_spec = {
                "task_id": "task-main-001",
                "task_key": "task-main",
                "codex_session_id": "session-old-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/home/Awei/project/closeout",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/home/Awei/project/HISTORY.md",
                "project_history_file_source": "explicit",
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
                "resume_timeout_seconds": 3600,
                "prompt_max_chars": 12000,
                "fallback_provider": "",
            }
            write_task_spec(config, "task-main-001", dict(base_spec))
            write_task_state(
                config,
                "task-main-001",
                {
                    "version": 1,
                    "task_id": "task-main-001",
                    "task_key": "task-main",
                    "status": "completed",
                    "feedback_mode": "auto",
                    "agent_name": "toposem-agent",
                    "codex_session_id": "session-old-001",
                    "submitted_at": "2026-03-20T00:00:00Z",
                    "updated_at": "2026-03-20T00:00:00Z",
                },
            )
            queue_feedback_resume(
                config,
                task_id="task-main-001",
                spec=base_spec,
                event={
                    "status": "completed",
                    "event_path": "/tmp/task-main-event.json",
                    "feedback_data_path": "/tmp/task-main-feedback.json",
                    "command_log_path": "/tmp/task-main.log",
                    "runner_log_path": "/tmp/task-main-runner.log",
                    "failure_kind": "completed",
                    "failure_summary": "Task done.",
                    "duration_seconds": 5,
                    "artifact_context": [],
                    "log_tail": "",
                },
                reason="recent_activity",
                min_idle_seconds=1,
            )
            set_continuous_research_mode(
                config,
                enabled=True,
                codex_session_id="session-old-001",
                updated_by="test",
                source="unit",
            )

            def fake_bootstrap(
                _config: AppConfig,
                *,
                mode: str,
                prompt: str,
                output_last_message_path: str,
                codex_exec_mode: str,
                workdir: str,
                timeout_seconds: int,
                log_path: Path,
                model: str = "",
                session_id: str = "",
                max_continue_attempts: int = 3,
                spec: dict[str, object] | None = None,
                feedback_source_kind: str = "",
                feedback_source_key: str = "",
                feedback_task_id: str = "",
                feedback_task_ids: list[str] | None = None,
                feedback_followup_key: str = "",
                requested_session_id: str = "",
                track_resume_feedback: bool = False,
            ) -> dict[str, object]:
                del (
                    _config,
                    codex_exec_mode,
                    workdir,
                    timeout_seconds,
                    log_path,
                    model,
                    session_id,
                    max_continue_attempts,
                    spec,
                    feedback_source_kind,
                    feedback_source_key,
                    feedback_task_id,
                    feedback_task_ids,
                    feedback_followup_key,
                    requested_session_id,
                    track_resume_feedback,
                )
                self.assertEqual(mode, "exec")
                self.assertIn("强制创建的新 Codex session", prompt)
                Path(output_last_message_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_last_message_path).write_text(
                    "successor planning done\nTASKBOARD_SIGNAL=EXECUTION_READY\nTASKBOARD_SELF_CHECK=pass\nLIVE_TASK_STATUS=none\n",
                    encoding="utf-8",
                )
                return {
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="session: session-new-001", stderr=""),
                    "session_id": "session-new-001",
                    "message_written": True,
                    "last_message_text": Path(output_last_message_path).read_text(encoding="utf-8"),
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                }

            with patch("codex_taskboard.cli.run_codex_prompt_with_continue_recovery", side_effect=fake_bootstrap), patch(
                "codex_taskboard.cli.codex_session_exists_for_spec",
                return_value=True,
            ), patch("codex_taskboard.cli.signal_process_group"), patch("codex_taskboard.cli.pid_exists", return_value=False):
                result = bootstrap_successor_session_after_closeout(
                    config,
                    task_id="task-main-001",
                    spec=base_spec,
                    predecessor_session_id="session-old-001",
                    resolve_followup_key=followup_key_for(base_spec),
                    updated_by="test",
                    source="unit",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["successor_session_id"], "session-new-001")
            self.assertEqual(result["taskboard_signal"], "EXECUTION_READY")
            self.assertEqual(load_task_spec(config, "task-main-001")["codex_session_id"], "session-new-001")
            self.assertTrue(load_continuous_research_mode(config, codex_session_id="session-new-001")["enabled"])
            old_queued_key = queued_feedback_key_for(base_spec)
            new_queued_key = queued_feedback_key_for({**base_spec, "codex_session_id": "session-new-001"})
            self.assertFalse(followup_path(config, old_queued_key).exists())
            self.assertTrue(followup_path(config, new_queued_key).exists())
            migration = session_migration_entry(config, "session-old-001")
            self.assertEqual(migration["state"], "completed")
            self.assertEqual(migration["to_session_id"], "session-new-001")

    def test_successor_bootstrap_rejects_reused_predecessor_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            base_spec = {
                "task_id": "task-main-001",
                "task_key": "task-main",
                "codex_session_id": "session-old-001",
                "agent_name": "toposem-agent",
                "proposal_path": "/home/Awei/project/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/home/Awei/project/closeout",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/home/Awei/project/HISTORY.md",
                "project_history_file_source": "explicit",
                "feedback_mode": "auto",
                "codex_exec_mode": "dangerous",
                "workdir": "/home/Awei/project",
                "command": "python train.py",
                "execution_mode": "shell",
                "resume_timeout_seconds": 3600,
                "prompt_max_chars": 12000,
                "fallback_provider": "",
            }
            write_task_spec(config, "task-main-001", dict(base_spec))
            write_task_state(
                config,
                "task-main-001",
                {
                    "version": 1,
                    "task_id": "task-main-001",
                    "task_key": "task-main",
                    "status": "completed",
                    "feedback_mode": "auto",
                    "agent_name": "toposem-agent",
                    "codex_session_id": "session-old-001",
                    "submitted_at": "2026-03-20T00:00:00Z",
                    "updated_at": "2026-03-20T00:00:00Z",
                },
            )

            def fake_bootstrap(
                _config: AppConfig,
                *,
                mode: str,
                prompt: str,
                output_last_message_path: str,
                requested_session_id: str = "",
                **_kwargs: object,
            ) -> dict[str, object]:
                self.assertEqual(mode, "exec")
                self.assertEqual(requested_session_id, "session-old-001")
                self.assertIn("强制创建的新 Codex session", prompt)
                Path(output_last_message_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_last_message_path).write_text(
                    "wrongly reused old session\nTASKBOARD_SIGNAL=EXECUTION_READY\nTASKBOARD_SELF_CHECK=pass\nLIVE_TASK_STATUS=none\n",
                    encoding="utf-8",
                )
                return {
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="", stderr=""),
                    "session_id": "session-old-001",
                    "message_written": True,
                    "last_message_text": Path(output_last_message_path).read_text(encoding="utf-8"),
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                }

            with patch("codex_taskboard.cli.run_codex_prompt_with_continue_recovery", side_effect=fake_bootstrap):
                result = bootstrap_successor_session_after_closeout(
                    config,
                    task_id="task-main-001",
                    spec=base_spec,
                    predecessor_session_id="session-old-001",
                    updated_by="test",
                    source="unit",
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["deferred_reason"], "successor_bootstrap_reused_predecessor_session")
            self.assertEqual(session_migration_entry(config, "session-old-001"), {})


if __name__ == "__main__":
    unittest.main()
