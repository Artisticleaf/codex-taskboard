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
    command_migrate_session,
    followup_path,
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

            def fake_run_subprocess(command: list[str], cwd: str, timeout: int) -> subprocess.CompletedProcess[str]:
                self.assertIn("session-new-001", command)
                output_index = command.index("-o") + 1
                Path(command[output_index]).write_text("assistant reply\n", encoding="utf-8")
                return subprocess.CompletedProcess(args=command, returncode=0, stdout="assistant reply\n", stderr="")

            def fake_run_tracked_feedback_subprocess(
                _config,
                command: list[str],
                *,
                cwd: str | None = None,
                timeout: int | None = None,
                session_id: str,
                requested_session_id: str,
                source_kind: str,
                source_key: str,
                task_id: str = "",
                task_ids: list[str] | None = None,
                followup_key: str = "",
            ) -> subprocess.CompletedProcess[str]:
                del _config, session_id, requested_session_id, source_kind, source_key, task_id, task_ids, followup_key
                return fake_run_subprocess(command, cwd or "", int(timeout or 0))

            with patch("codex_taskboard.cli.run_subprocess", side_effect=fake_run_subprocess), patch(
                "codex_taskboard.cli.run_tracked_feedback_subprocess",
                side_effect=fake_run_tracked_feedback_subprocess,
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


if __name__ == "__main__":
    unittest.main()
