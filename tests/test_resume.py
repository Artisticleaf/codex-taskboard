import subprocess
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import (
    AppConfig,
    find_recent_local_thread_for_prompt,
    find_recent_local_thread_with_assistant_message,
    latest_session_activity_ts,
    resume_codex_session,
    resume_codex_session_with_prompt,
    session_output_busy_snapshot,
    set_automation_mode,
    task_last_message_path,
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


def write_thread_row(config: AppConfig, session_id: str, **fields: object) -> None:
    config.codex_home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.threads_db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                model_provider TEXT,
                source TEXT,
                archived INTEGER,
                updated_at INTEGER,
                title TEXT,
                cwd TEXT,
                first_user_message TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO threads (id, model_provider, source, archived, updated_at, title, cwd, first_user_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                str(fields.get("model_provider", "openai")),
                str(fields.get("source", "vscode")),
                int(fields.get("archived", 0) or 0),
                int(fields.get("updated_at", 1710812345) or 0),
                str(fields.get("title", "")),
                str(fields.get("cwd", "")),
                str(fields.get("first_user_message", "")),
            ),
        )
        conn.commit()
    finally:
        conn.close()


class ResumeTests(unittest.TestCase):
    def test_find_recent_local_thread_does_not_bind_excluded_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            workdir = Path(tmpdir) / "project"
            workdir.mkdir()
            write_thread_row(
                config,
                "session-old-001",
                cwd=str(workdir),
                updated_at=2000,
                first_user_message="old closeout prompt",
                title="old closeout",
            )

            matched = find_recent_local_thread_for_prompt(
                config,
                workdir=str(workdir),
                prompt="successor planning prompt",
                min_updated_at=2000,
                excluded_session_ids={"session-old-001"},
            )

            self.assertIsNone(matched)

    def test_find_recent_local_thread_with_assistant_message_skips_empty_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            workdir = Path(tmpdir) / "project"
            workdir.mkdir()
            prompt = "successor planning prompt"
            write_thread_row(
                config,
                "session-empty-001",
                cwd=str(workdir),
                updated_at=120,
                first_user_message=prompt,
                title=prompt,
            )
            write_thread_row(
                config,
                "session-ready-001",
                cwd=str(workdir),
                updated_at=110,
                first_user_message=prompt,
                title=prompt,
            )
            rollout_dir = config.codex_home / "sessions" / "2026" / "03" / "19"
            rollout_dir.mkdir(parents=True, exist_ok=True)
            rollout_path = rollout_dir / "rollout-2026-03-19T00-00-00-session-ready-001.jsonl"
            rollout_path.write_text(
                json.dumps(
                    {
                        "timestamp": "1970-01-01T00:02:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "ready\nTASKBOARD_SIGNAL=EXECUTION_READY\nTASKBOARD_SELF_CHECK=pass\nLIVE_TASK_STATUS=none\n",
                                }
                            ],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            matched = find_recent_local_thread_with_assistant_message(
                config,
                workdir=str(workdir),
                prompt=prompt,
                min_updated_at=100,
                min_message_ts=100,
            )

            self.assertIsNotNone(matched)
            assert matched is not None
            thread, message = matched
            self.assertEqual(thread["id"], "session-ready-001")
            self.assertIn("EXECUTION_READY", message)

    def test_session_output_busy_snapshot_detects_open_rollout_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "019followup-session-busy"
            rollout_dir = config.codex_home / "sessions" / "2026" / "03" / "19"
            rollout_dir.mkdir(parents=True, exist_ok=True)
            rollout_path = rollout_dir / f"rollout-2026-03-19T00-00-00-{session_id}.jsonl"
            base_dt = datetime(2026, 3, 19, 0, 0, 0, tzinfo=timezone.utc)
            rollout_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"%s","type":"turn_context","payload":{"turn_id":"turn-busy"}}'
                        % base_dt.isoformat().replace("+00:00", "Z"),
                        '{"timestamp":"%s","type":"response_item","payload":{"type":"function_call","name":"exec_command","call_id":"call-busy"}}'
                        % (base_dt + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.time.time", return_value=base_dt.timestamp() + 8):
                snapshot = session_output_busy_snapshot(config, session_id)

            self.assertTrue(snapshot["busy"])
            self.assertEqual(snapshot["detail"], "active_rollout_turn")
            self.assertEqual(snapshot["rollout_snapshot"]["last_payload_type"], "function_call")

    def test_session_output_busy_snapshot_keeps_open_turn_busy_while_assistant_is_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "019followup-assistant-stream"
            rollout_dir = config.codex_home / "sessions" / "2026" / "03" / "19"
            rollout_dir.mkdir(parents=True, exist_ok=True)
            rollout_path = rollout_dir / f"rollout-2026-03-19T00-00-00-{session_id}.jsonl"
            base_dt = datetime(2026, 3, 19, 0, 0, 0, tzinfo=timezone.utc)
            rollout_path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"%s","type":"turn_context","payload":{"turn_id":"turn-stream"}}'
                        % base_dt.isoformat().replace("+00:00", "Z"),
                        '{"timestamp":"%s","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"partial"}]}}'
                        % (base_dt + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.time.time", return_value=base_dt.timestamp() + 40):
                snapshot = session_output_busy_snapshot(config, session_id)

            self.assertTrue(snapshot["busy"])
            self.assertEqual(snapshot["detail"], "active_rollout_turn")
            self.assertEqual(snapshot["rollout_snapshot"]["last_payload_type"], "message")

    def test_resume_treats_written_message_as_success_even_on_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "fill-a",
                "codex_session_id": "session-123",
                "workdir": "/home/Awei",
                "command": "sleep 10",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
                "success_prompt": "",
                "failure_prompt": "",
                "task_note": "",
            }
            event = {
                "status": "completed",
                "command_log_path": "/tmp/fill-a.log",
                "exit_code": 0,
                "exit_signal": "",
                "failure_kind": "completed",
                "failure_summary": "The task finished successfully.",
                "needs_attention": False,
                "attention_message": "",
                "duration_seconds": 10,
                "log_tail": "task_started",
                "artifact_context": [],
            }

            def fake_run_local_interactive_codex(
                _config: AppConfig,
                *,
                command: list[str],
                output_last_message_path: str,
                **_kwargs: object,
            ) -> dict[str, object]:
                Path(output_last_message_path).write_text("assistant reply\n", encoding="utf-8")
                return {
                    "completed": subprocess.CompletedProcess(
                        args=command,
                        returncode=-6,
                        stdout="assistant reply\n",
                        stderr="node assertion\n",
                    ),
                    "session_id": "session-123",
                    "message_written": True,
                    "last_message_text": "assistant reply\n",
                }

            with patch(
                "codex_taskboard.cli.run_local_interactive_codex",
                side_effect=fake_run_local_interactive_codex,
            ):
                result = resume_codex_session(config, spec, event)

            self.assertTrue(result["ok"])
            self.assertTrue(result["message_written"])
            self.assertEqual(result["first_returncode"], -6)
            self.assertEqual(
                task_last_message_path(config, "fill-a").read_text(encoding="utf-8"),
                "assistant reply\n",
            )

    def test_remote_executor_resume_writes_message_from_ssh_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "remote-fill-a",
                "execution_mode": "ssh_shell",
                "codex_session_id": "remote-session-123",
                "workdir": "/home/Awei",
                "remote_workdir": "/home/ju/project",
                "executor_target": "ju@127.0.0.1",
                "executor_identity_file": "/tmp/fake-key",
                "executor_ssh_options": ["-o", "BatchMode=yes"],
                "executor_remote_workdir_prefix": "/home/ju",
                "executor_remote_codex_home": "/home/ju/.codex",
                "executor_remote_codex_bin": "codex",
                "command": "sleep 1",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
                "success_prompt": "",
                "failure_prompt": "",
                "task_note": "",
            }
            event = {
                "status": "completed",
                "command_log_path": "/tmp/remote-fill-a.log",
                "exit_code": 0,
                "exit_signal": "",
                "failure_kind": "completed",
                "failure_summary": "The task finished successfully.",
                "needs_attention": False,
                "attention_message": "",
                "duration_seconds": 3,
                "log_tail": "task_started",
                "artifact_context": [],
            }

            def fake_run_tracked_feedback_subprocess(
                _config: AppConfig,
                command: list[str],
                *,
                cwd: str,
                timeout: int,
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                self.assertEqual(command[0], "ssh")
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=(
                        "session id: remote-session-123\n"
                        "__CODEX_TASKBOARD_LAST_MESSAGE_BEGIN__\n"
                        "remote assistant reply\n"
                        "__CODEX_TASKBOARD_LAST_MESSAGE_END__\n"
                    ),
                    stderr="",
                )

            with patch(
                "codex_taskboard.cli.run_tracked_feedback_subprocess",
                side_effect=fake_run_tracked_feedback_subprocess,
            ):
                result = resume_codex_session(config, spec, event)

            self.assertTrue(result["ok"])
            self.assertTrue(result["message_written"])
            self.assertEqual(
                task_last_message_path(config, "remote-fill-a").read_text(encoding="utf-8"),
                "remote assistant reply",
            )

    def test_remote_session_activity_uses_executor_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "execution_mode": "ssh_shell",
                "remote_workdir": "/home/ju/project",
                "executor_target": "ju@127.0.0.1",
                "executor_identity_file": "/tmp/fake-key",
                "executor_ssh_options": ["-o", "BatchMode=yes"],
                "executor_remote_workdir_prefix": "/home/ju",
                "executor_remote_codex_home": "/home/ju/.codex",
            }

            def fake_run_subprocess(command: list[str], cwd: str, timeout: int) -> subprocess.CompletedProcess[str]:
                self.assertEqual(command[0], "ssh")
                return subprocess.CompletedProcess(args=command, returncode=0, stdout="1710812345.75\n", stderr="")

            with patch("codex_taskboard.cli.run_subprocess", side_effect=fake_run_subprocess):
                ts = latest_session_activity_ts(config, "remote-session-123", spec)

            self.assertEqual(ts, 1710812345.75)

    def test_resume_defers_on_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "fill-rate-limit",
                "codex_session_id": "session-123",
                "workdir": "/home/Awei",
                "command": "sleep 10",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
            }

            def fake_run_local_interactive_codex(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                return {
                    "completed": subprocess.CompletedProcess(
                        args=command,
                        returncode=1,
                        stdout="session id: session-123\n",
                        stderr="exceeded retry limit, last status: 429 Too Many Requests",
                    ),
                    "session_id": "session-123",
                    "message_written": False,
                    "last_message_text": "",
                }

            with patch(
                "codex_taskboard.cli.run_local_interactive_codex",
                side_effect=fake_run_local_interactive_codex,
            ), patch(
                "codex_taskboard.cli.time.sleep",
                return_value=None,
            ):
                result = resume_codex_session_with_prompt(
                    config,
                    spec,
                    "background batch",
                    output_last_message_path=str(task_last_message_path(config, "fill-rate-limit")),
                    log_path=config.app_home / "resume.log",
                )

            self.assertFalse(result["ok"])
            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "rate_limited")
            self.assertGreaterEqual(result["retry_after_seconds"], 1)

    def test_resume_defers_after_attempt_without_losing_continue_recovery_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "fill-rate-limit-preserve",
                "codex_session_id": "session-continue-123",
                "workdir": "/home/Awei",
                "command": "sleep 10",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
            }
            completed = subprocess.CompletedProcess(
                args=["codex", "exec", "resume"],
                returncode=1,
                stdout="session id: session-continue-123\n",
                stderr="exceeded retry limit, last status: 429 Too Many Requests",
            )

            with patch(
                "codex_taskboard.cli.run_codex_prompt_with_continue_recovery",
                return_value={
                    "completed": completed,
                    "session_id": "session-continue-123",
                    "message_written": False,
                    "last_message_text": "",
                    "continue_attempts": 2,
                    "recovered_with_continue": True,
                },
            ):
                result = resume_codex_session_with_prompt(
                    config,
                    spec,
                    "background batch",
                    output_last_message_path=str(task_last_message_path(config, "fill-rate-limit-preserve")),
                    log_path=config.app_home / "resume.log",
                )

            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "rate_limited")
            self.assertEqual(result["continue_attempts"], 2)
            self.assertTrue(result["recovered_with_continue"])
            self.assertEqual(result["first_returncode"], 1)

    def test_resume_defers_on_transient_platform_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "fill-platform-transient",
                "codex_session_id": "session-123",
                "workdir": "/home/Awei",
                "command": "sleep 10",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
            }

            def fake_run_local_interactive_codex(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                return {
                    "completed": subprocess.CompletedProcess(
                        args=command,
                        returncode=1,
                        stdout="session id: session-123\n",
                        stderr="503 Service Unavailable: server overloaded",
                    ),
                    "session_id": "session-123",
                    "message_written": False,
                    "last_message_text": "",
                }

            with patch(
                "codex_taskboard.cli.run_local_interactive_codex",
                side_effect=fake_run_local_interactive_codex,
            ), patch(
                "codex_taskboard.cli.time.sleep",
                return_value=None,
            ):
                result = resume_codex_session_with_prompt(
                    config,
                    spec,
                    "background batch",
                    output_last_message_path=str(task_last_message_path(config, "fill-platform-transient")),
                    log_path=config.app_home / "resume.log",
                )

            self.assertFalse(result["ok"])
            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "platform_error:upstream_platform_transient")
            self.assertEqual(result["platform_error_kind"], "upstream_platform_transient")
            self.assertTrue(result["platform_error_retryable"])
            self.assertFalse(result["platform_error_needs_human_attention"])

    def test_resume_defers_on_auth_or_quota_platform_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "fill-platform-auth",
                "codex_session_id": "session-123",
                "workdir": "/home/Awei",
                "command": "sleep 10",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
            }

            def fake_run_local_interactive_codex(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                return {
                    "completed": subprocess.CompletedProcess(
                        args=command,
                        returncode=1,
                        stdout="session id: session-123\n",
                        stderr="401 Unauthorized: invalid api key",
                    ),
                    "session_id": "session-123",
                    "message_written": False,
                    "last_message_text": "",
                }

            with patch(
                "codex_taskboard.cli.run_local_interactive_codex",
                side_effect=fake_run_local_interactive_codex,
            ), patch(
                "codex_taskboard.cli.time.sleep",
                return_value=None,
            ):
                result = resume_codex_session_with_prompt(
                    config,
                    spec,
                    "background batch",
                    output_last_message_path=str(task_last_message_path(config, "fill-platform-auth")),
                    log_path=config.app_home / "resume.log",
                )

            self.assertFalse(result["ok"])
            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "platform_error:platform_auth_or_quota")
            self.assertEqual(result["platform_error_kind"], "platform_auth_or_quota")
            self.assertFalse(result["platform_error_retryable"])
            self.assertTrue(result["platform_error_needs_human_attention"])
            self.assertGreaterEqual(result["retry_after_seconds"], 300)

    def test_resume_defers_on_session_busy_with_output_busy_retry_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "fill-session-busy",
                "codex_session_id": "session-busy",
                "workdir": "/home/Awei",
                "command": "sleep 10",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
            }

            def fake_run_local_interactive_codex(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                return {
                    "completed": subprocess.CompletedProcess(
                        args=command,
                        returncode=1,
                        stdout="session id: session-busy\n",
                        stderr="conversation is busy, another response is in progress",
                    ),
                    "session_id": "session-busy",
                    "message_written": False,
                    "last_message_text": "",
                }

            with patch(
                "codex_taskboard.cli.run_local_interactive_codex",
                side_effect=fake_run_local_interactive_codex,
            ), patch(
                "codex_taskboard.cli.time.sleep",
                return_value=None,
            ):
                result = resume_codex_session_with_prompt(
                    config,
                    spec,
                    "background batch",
                    output_last_message_path=str(task_last_message_path(config, "fill-session-busy")),
                    log_path=config.app_home / "resume.log",
                )

            self.assertFalse(result["ok"])
            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "session_busy")
            self.assertEqual(result["retry_after_seconds"], 30)

    def test_resume_defers_when_session_lock_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "fill-lock-busy",
                "codex_session_id": "session-123",
                "workdir": "/home/Awei",
                "command": "sleep 10",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
            }

            with patch("codex_taskboard.cli.fcntl.flock", side_effect=BlockingIOError):
                result = resume_codex_session_with_prompt(
                    config,
                    spec,
                    "background batch",
                    output_last_message_path=str(task_last_message_path(config, "fill-lock-busy")),
                    log_path=config.app_home / "resume.log",
                )

            self.assertFalse(result["ok"])
            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "session_locked")
            self.assertFalse(result["attempted"])

    def test_resume_defers_during_human_guidance_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "fill-human-pause",
                "codex_session_id": "session-human-pause",
                "workdir": "/home/Awei",
                "command": "sleep 10",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 30,
                "fallback_provider": "",
            }
            set_automation_mode(
                config,
                mode="managed",
                codex_session_id="session-human-pause",
                updated_by="test",
                source="unit",
            )

            result = resume_codex_session_with_prompt(
                config,
                spec,
                "background batch",
                output_last_message_path=str(task_last_message_path(config, "fill-human-pause")),
                log_path=config.app_home / "resume.log",
            )

            self.assertFalse(result["ok"])
            self.assertTrue(result["deferred"])
            self.assertEqual(result["deferred_reason"], "managed_mode_pause")
            self.assertFalse(result["attempted"])


if __name__ == "__main__":
    unittest.main()
