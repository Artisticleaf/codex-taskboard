import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from codex_taskboard.session_runtime import (
    SessionRuntimeHooks,
    active_codex_resume_pids_for_session,
    classify_platform_error,
    latest_local_rollout_output_snapshot,
    rollout_candidates_for_session,
)


def parse_timestamp(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def build_hooks(**overrides: object) -> SessionRuntimeHooks:
    base: dict[str, object] = {
        "find_thread_info": lambda config, session_id: None,
        "should_use_executor_codex": lambda spec: False,
        "latest_remote_session_activity_ts": lambda spec, session_id: 0.0,
        "parse_timestamp_to_unix": parse_timestamp,
        "read_pid_cmdline": lambda pid: "",
        "active_feedback_entries_for_session": lambda config, session_id: [],
        "canonicalize_taskboard_signal": lambda signal: str(signal or "").strip(),
        "extract_taskboard_protocol_footer": lambda text: {},
        "list_proc_entries": lambda: [],
        "now_ts": lambda: 100.0,
        "taskboard_final_signal_values": {"WAITING_ON_ASYNC", "CLOSEOUT_READY", "TASK_DONE"},
        "rate_limit_patterns": ("429 too many requests", "retry limit"),
        "session_busy_patterns": ("session is busy",),
        "platform_error_signatures": (
            {
                "kind": "upstream_platform_transient",
                "retryable": True,
                "summary": "temporary upstream failure",
                "patterns": ("503 service unavailable",),
            },
        ),
        "max_rollout_output_busy_tail_lines": 256,
        "default_session_output_busy_retry_seconds": 30,
        "default_session_output_busy_open_turn_stall_seconds": 300,
        "default_platform_error_human_retry_seconds": 300,
        "default_resume_retry_seconds": 60,
        "rollout_fallback_entry_grace_seconds": 1.0,
        "rollout_fallback_mtime_grace_seconds": 1.0,
    }
    base.update(overrides)
    return SessionRuntimeHooks(**base)


class SessionRuntimeTests(unittest.TestCase):
    def test_rollout_candidates_dedupe_thread_and_glob_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / "codex-home"
            session_id = "019session-runtime-dedupe"
            rollout_dir = codex_home / "sessions" / "2026" / "04" / "21"
            rollout_dir.mkdir(parents=True, exist_ok=True)
            primary_path = rollout_dir / f"rollout-2026-04-21T00-00-00-{session_id}.jsonl"
            primary_path.write_text("{}\n", encoding="utf-8")
            archived_dir = codex_home / "archived_sessions"
            archived_dir.mkdir(parents=True, exist_ok=True)
            archived_path = archived_dir / f"rollout-2026-04-20T00-00-00-{session_id}.jsonl"
            archived_path.write_text("{}\n", encoding="utf-8")
            config = SimpleNamespace(codex_home=codex_home)
            hooks = build_hooks(find_thread_info=lambda config, sid: {"rollout_path": str(primary_path)})

            candidates = rollout_candidates_for_session(config, session_id, hooks=hooks)

            self.assertEqual(candidates, [archived_path, primary_path])

    def test_latest_local_rollout_output_snapshot_survives_invalid_tail_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / "codex-home"
            session_id = "019session-runtime-snapshot"
            rollout_dir = codex_home / "sessions" / "2026" / "04" / "21"
            rollout_dir.mkdir(parents=True, exist_ok=True)
            rollout_path = rollout_dir / f"rollout-2026-04-21T00-00-00-{session_id}.jsonl"
            rollout_path.write_text(
                "\n".join(
                    [
                        "not-json-at-all",
                        '{"timestamp":"2026-04-21T00:00:00Z","type":"turn_context","payload":{"turn_id":"turn-1"}}',
                        '{"timestamp":"2026-04-21T00:00:05Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"partial"}]}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config = SimpleNamespace(codex_home=codex_home)
            hooks = build_hooks()

            snapshot = latest_local_rollout_output_snapshot(config, session_id, hooks=hooks)

            self.assertTrue(snapshot["turn_in_progress"])
            self.assertEqual(snapshot["last_payload_type"], "message")
            self.assertGreater(snapshot["last_assistant_message_ts"], 0.0)

    def test_active_codex_resume_pids_for_session_dedupes_and_filters(self) -> None:
        hooks = build_hooks(
            list_proc_entries=lambda: [Path("/proc/101"), Path("/proc/skip"), Path("/proc/101"), Path("/proc/202")],
            read_pid_cmdline=lambda pid: {
                101: "python codex exec resume 019session-runtime-proc",
                202: "python other_tool run 019session-runtime-proc",
            }.get(pid, ""),
        )

        matches = active_codex_resume_pids_for_session("019session-runtime-proc", hooks=hooks)

        self.assertEqual(matches, [101])

    def test_active_codex_resume_pids_for_session_matches_interactive_resume(self) -> None:
        hooks = build_hooks(
            list_proc_entries=lambda: [Path("/proc/303"), Path("/proc/404")],
            read_pid_cmdline=lambda pid: {
                303: "node /usr/local/bin/codex resume 019session-runtime-proc continue",
                404: "node /usr/local/bin/codex start other-session",
            }.get(pid, ""),
        )

        matches = active_codex_resume_pids_for_session("019session-runtime-proc", hooks=hooks)

        self.assertEqual(matches, [303])

    def test_active_codex_resume_pids_for_session_tolerates_proc_scan_failure(self) -> None:
        hooks = build_hooks(list_proc_entries=lambda: (_ for _ in ()).throw(OSError("proc unavailable")))

        matches = active_codex_resume_pids_for_session("019session-runtime-proc", hooks=hooks)

        self.assertEqual(matches, [])

    def test_classify_platform_error_handles_blank_and_known_signatures(self) -> None:
        hooks = build_hooks()

        blank = classify_platform_error("", hooks=hooks)
        transient = classify_platform_error("503 Service Unavailable: overloaded", hooks=hooks)

        self.assertEqual(blank["kind"], "")
        self.assertFalse(blank["retryable"])
        self.assertEqual(transient["kind"], "upstream_platform_transient")
        self.assertTrue(transient["retryable"])


if __name__ == "__main__":
    unittest.main()
