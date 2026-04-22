import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import (
    AppConfig,
    extract_codex_session_id,
    extract_taskboard_signal,
    is_rate_limit_retry_error,
    run_codex_prompt_with_continue_recovery,
    run_codex_subagent,
    subagent_last_message_path,
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


class SubagentTests(unittest.TestCase):
    def test_extract_codex_session_id(self) -> None:
        text = "session id: 019cefd3-adbe-71b2-ad92-853c02d3c8b3"
        self.assertEqual(extract_codex_session_id(text), "019cefd3-adbe-71b2-ad92-853c02d3c8b3")

    def test_extract_taskboard_signal(self) -> None:
        text = "done\nTASKBOARD_SIGNAL=TASK_DONE\n"
        self.assertEqual(extract_taskboard_signal(text), "TASK_DONE")

    def test_rate_limit_detection(self) -> None:
        self.assertTrue(is_rate_limit_retry_error("exceeded retry limit", "429 Too Many Requests"))
        self.assertFalse(is_rate_limit_retry_error("some other error"))

    def test_continue_recovery_resumes_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            output_path = config.app_home / "out.txt"
            calls: list[list[str]] = []

            def fake_run(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                calls.append(command)
                if len(calls) == 1:
                    return {
                        "completed": subprocess.CompletedProcess(
                            args=command,
                            returncode=1,
                            stdout="",
                            stderr="session id: 019test-session-0001\nexceeded retry limit, last status: 429 Too Many Requests, request id: abc\n",
                        ),
                        "session_id": "019test-session-0001",
                        "message_written": False,
                        "last_message_text": "",
                    }
                output_path.write_text("recovered\n", encoding="utf-8")
                return {
                    "completed": subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout="recovered\n",
                        stderr="",
                    ),
                    "session_id": "019test-session-0001",
                    "message_written": True,
                    "last_message_text": "recovered\n",
                }

            with patch("codex_taskboard.cli.run_local_interactive_codex", side_effect=fake_run), patch("codex_taskboard.cli.time.sleep"):
                result = run_codex_prompt_with_continue_recovery(
                    config,
                    mode="exec",
                    prompt="do work",
                    output_last_message_path=str(output_path),
                    codex_exec_mode="dangerous",
                    workdir="/home/Awei",
                    timeout_seconds=60,
                    log_path=config.app_home / "runner.log",
                    model="gpt-5.4",
                    max_continue_attempts=2,
                )

            self.assertEqual(len(calls), 2)
            self.assertIn("resume", calls[1])
            self.assertIn("continue", calls[1])
            self.assertTrue(result["message_written"])
            self.assertTrue(result["recovered_with_continue"])

    def test_continue_recovery_resumes_after_retryable_platform_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            output_path = config.app_home / "out.txt"
            calls: list[list[str]] = []

            def fake_run(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                calls.append(command)
                if len(calls) == 1:
                    return {
                        "completed": subprocess.CompletedProcess(
                            args=command,
                            returncode=1,
                            stdout="session id: 019test-session-0002\n",
                            stderr="503 Service Unavailable: server overloaded\n",
                        ),
                        "session_id": "019test-session-0002",
                        "message_written": False,
                        "last_message_text": "",
                    }
                output_path.write_text("recovered transient\n", encoding="utf-8")
                return {
                    "completed": subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout="recovered transient\n",
                        stderr="",
                    ),
                    "session_id": "019test-session-0002",
                    "message_written": True,
                    "last_message_text": "recovered transient\n",
                }

            with patch("codex_taskboard.cli.run_local_interactive_codex", side_effect=fake_run), patch(
                "codex_taskboard.cli.time.sleep"
            ) as sleep_mock:
                result = run_codex_prompt_with_continue_recovery(
                    config,
                    mode="exec",
                    prompt="do work",
                    output_last_message_path=str(output_path),
                    codex_exec_mode="dangerous",
                    workdir="/home/Awei",
                    timeout_seconds=60,
                    log_path=config.app_home / "runner.log",
                    model="gpt-5.4",
                    max_continue_attempts=2,
                )

            self.assertEqual(len(calls), 2)
            self.assertIn("resume", calls[1])
            self.assertIn("continue", calls[1])
            self.assertTrue(result["message_written"])
            self.assertTrue(result["recovered_with_continue"])
            sleep_mock.assert_called_once_with(5)

    def test_continue_recovery_resumes_after_relay_error_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            output_path = config.app_home / "out.txt"
            calls: list[list[str]] = []

            def fake_run(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                calls.append(command)
                if len(calls) == 1:
                    return {
                        "completed": subprocess.CompletedProcess(
                            args=command,
                            returncode=1,
                            stdout="session id: 019test-session-0003\n",
                            stderr="中转站错误: upstream proxy error\n",
                        ),
                        "session_id": "019test-session-0003",
                        "message_written": False,
                        "last_message_text": "",
                    }
                output_path.write_text("recovered relay\n", encoding="utf-8")
                return {
                    "completed": subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout="recovered relay\n",
                        stderr="",
                    ),
                    "session_id": "019test-session-0003",
                    "message_written": True,
                    "last_message_text": "recovered relay\n",
                }

            with patch("codex_taskboard.cli.run_local_interactive_codex", side_effect=fake_run), patch(
                "codex_taskboard.cli.time.sleep"
            ) as sleep_mock:
                result = run_codex_prompt_with_continue_recovery(
                    config,
                    mode="exec",
                    prompt="do work",
                    output_last_message_path=str(output_path),
                    codex_exec_mode="dangerous",
                    workdir="/home/Awei",
                    timeout_seconds=60,
                    log_path=config.app_home / "runner.log",
                    model="gpt-5.4",
                    max_continue_attempts=2,
                )

            self.assertEqual(len(calls), 2)
            self.assertIn("resume", calls[1])
            self.assertIn("continue", calls[1])
            self.assertTrue(result["message_written"])
            self.assertTrue(result["recovered_with_continue"])
            sleep_mock.assert_called_once_with(5)

    def test_run_codex_subagent_reads_child_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            task_id = "subagent-a"
            task_dir = config.tasks_root / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            spec = {
                "task_id": task_id,
                "workdir": "/home/Awei",
                "subagent_prompt": "Reply with exactly CHILD-OK",
                "subagent_model": "gpt-5.4",
                "subagent_exec_mode": "dangerous",
                "subagent_timeout_seconds": 60,
            }

            def fake_run(*args, **kwargs):
                path = subagent_last_message_path(config, task_id)
                path.write_text("CHILD-OK\nTASKBOARD_SIGNAL=TASK_DONE\n", encoding="utf-8")
                return {
                    "completed": subprocess.CompletedProcess(args=["codex"], returncode=0, stdout="CHILD-OK\n", stderr="session id: 019child-session-0001\n"),
                    "session_id": "019child-session-0001",
                    "message_written": True,
                    "continue_attempts": 0,
                    "recovered_with_continue": False,
                }

            with patch("codex_taskboard.cli.run_codex_prompt_with_continue_recovery", side_effect=fake_run):
                result = run_codex_subagent(config, spec)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["subagent_session_id"], "019child-session-0001")
            self.assertEqual(result["subagent_last_message"], "CHILD-OK\nTASKBOARD_SIGNAL=TASK_DONE\n")
            self.assertEqual(result["taskboard_signal"], "TASK_DONE")

    def test_run_codex_prompt_uses_rollout_fallback_when_output_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "019followup-session-0001"
            output_path = config.app_home / "followup-last-message.txt"

            def fake_run(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                rollout_dir = config.codex_home / "sessions" / "2026" / "03" / "19"
                rollout_dir.mkdir(parents=True, exist_ok=True)
                rollout_path = rollout_dir / f"rollout-2026-03-19T00-00-00-{session_id}.jsonl"
                rollout_path.write_text(
                    json.dumps(
                        {
                            "timestamp": "1970-01-01T00:01:41Z",
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "done\nTASKBOARD_SIGNAL=NO_FURTHER_TASKS\n",
                                    }
                                ],
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {
                    "completed": subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr=""),
                    "session_id": session_id,
                    "message_written": False,
                    "last_message_text": "",
                }

            with patch("codex_taskboard.cli.run_local_interactive_codex", side_effect=fake_run), patch(
                "codex_taskboard.cli.time.time",
                return_value=100.0,
            ):
                result = run_codex_prompt_with_continue_recovery(
                    config,
                    mode="resume",
                    prompt="continue",
                    output_last_message_path=str(output_path),
                    codex_exec_mode="dangerous",
                    workdir="/home/Awei",
                    timeout_seconds=60,
                    log_path=config.app_home / "followup.log",
                    session_id=session_id,
                )

            self.assertTrue(result["message_written"])
            self.assertIn("NO_FURTHER_TASKS", result["last_message_text"])
            self.assertTrue(output_path.exists())

    def test_run_codex_prompt_rollout_fallback_ignores_invalid_json_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "019followup-session-invalid-json"
            output_path = config.app_home / "followup-last-message.txt"

            def fake_run(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                rollout_dir = config.codex_home / "sessions" / "2026" / "03" / "19"
                rollout_dir.mkdir(parents=True, exist_ok=True)
                rollout_path = rollout_dir / f"rollout-2026-03-19T00-00-00-{session_id}.jsonl"
                rollout_path.write_text(
                    "\n".join(
                        [
                            "this is not valid json",
                            json.dumps(
                                {
                                    "timestamp": "1970-01-01T00:01:41Z",
                                    "type": "response_item",
                                    "payload": {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [
                                            {
                                                "type": "output_text",
                                                "text": "stable\nTASKBOARD_SIGNAL=WAITING_ON_ASYNC\n",
                                            }
                                        ],
                                    },
                                }
                            ),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {
                    "completed": subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr=""),
                    "session_id": session_id,
                    "message_written": False,
                    "last_message_text": "",
                }

            with patch("codex_taskboard.cli.run_local_interactive_codex", side_effect=fake_run), patch(
                "codex_taskboard.cli.time.time",
                return_value=100.0,
            ):
                result = run_codex_prompt_with_continue_recovery(
                    config,
                    mode="resume",
                    prompt="continue",
                    output_last_message_path=str(output_path),
                    codex_exec_mode="dangerous",
                    workdir="/home/Awei",
                    timeout_seconds=60,
                    log_path=config.app_home / "followup.log",
                    session_id=session_id,
                )

            self.assertTrue(result["message_written"])
            self.assertIn("WAITING_ON_ASYNC", result["last_message_text"])
            self.assertTrue(output_path.exists())

    def test_run_codex_prompt_ignores_stale_rollout_message_when_only_non_assistant_events_are_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "019followup-session-0002"
            output_path = config.app_home / "followup-last-message.txt"

            def fake_run(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                rollout_dir = config.codex_home / "sessions" / "2026" / "03" / "19"
                rollout_dir.mkdir(parents=True, exist_ok=True)
                rollout_path = rollout_dir / f"rollout-2026-03-19T00-00-00-{session_id}.jsonl"
                rollout_path.write_text(
                    "\n".join(
                        [
                            json.dumps(
                                {
                                    "timestamp": "1970-01-01T00:01:40Z",
                                    "type": "response_item",
                                    "payload": {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [
                                            {
                                                "type": "output_text",
                                                "text": "stale\nTASKBOARD_SIGNAL=NO_FURTHER_TASKS\n",
                                            }
                                        ],
                                    },
                                }
                            ),
                            json.dumps(
                                {
                                    "timestamp": "1970-01-01T00:03:25Z",
                                    "type": "event_msg",
                                    "payload": {
                                        "type": "token_count",
                                        "info": {"last_token_usage": {"total_tokens": 7}},
                                    },
                                }
                            ),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {
                    "completed": subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr=""),
                    "session_id": session_id,
                    "message_written": False,
                    "last_message_text": "",
                }

            with patch("codex_taskboard.cli.run_local_interactive_codex", side_effect=fake_run), patch(
                "codex_taskboard.cli.time.time",
                return_value=200.0,
            ):
                result = run_codex_prompt_with_continue_recovery(
                    config,
                    mode="resume",
                    prompt="continue",
                    output_last_message_path=str(output_path),
                    codex_exec_mode="dangerous",
                    workdir="/home/Awei",
                    timeout_seconds=60,
                    log_path=config.app_home / "followup.log",
                    session_id=session_id,
                )

            self.assertFalse(result["message_written"])
            self.assertEqual(result["last_message_text"], "")
            self.assertFalse(output_path.exists())

    def test_run_codex_prompt_skips_rollout_fallback_for_vscode_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            session_id = "019followup-session-vscode"
            output_path = config.app_home / "followup-last-message.txt"

            def fake_run(
                _config: AppConfig,
                *,
                command: list[str],
                **_kwargs: object,
            ) -> dict[str, object]:
                rollout_dir = config.codex_home / "sessions" / "2026" / "03" / "19"
                rollout_dir.mkdir(parents=True, exist_ok=True)
                rollout_path = rollout_dir / f"rollout-2026-03-19T00-00-00-{session_id}.jsonl"
                rollout_path.write_text(
                    json.dumps(
                        {
                            "timestamp": "1970-01-01T00:01:41Z",
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "stale live vscode message\nTASKBOARD_SIGNAL=NO_FURTHER_TASKS\n",
                                    }
                                ],
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {
                    "completed": subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr=""),
                    "session_id": session_id,
                    "message_written": False,
                    "last_message_text": "",
                }

            with patch("codex_taskboard.cli.run_local_interactive_codex", side_effect=fake_run), patch(
                "codex_taskboard.cli.find_thread_info",
                return_value={"id": session_id, "source": "vscode"},
            ), patch("codex_taskboard.cli.time.time", return_value=100.0):
                result = run_codex_prompt_with_continue_recovery(
                    config,
                    mode="resume",
                    prompt="continue",
                    output_last_message_path=str(output_path),
                    codex_exec_mode="dangerous",
                    workdir="/home/Awei",
                    timeout_seconds=60,
                    log_path=config.app_home / "followup.log",
                    session_id=session_id,
                )

            self.assertFalse(result["message_written"])
            self.assertEqual(result["last_message_text"], "")
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
