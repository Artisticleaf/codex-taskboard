from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_taskboard.cli import (
    AppConfig,
    automation_mode_is_managed,
    build_continuous_mode_status_payload,
    build_continuous_planning_prompt,
    build_continuous_research_prompt,
    build_continuous_transition_prompt,
    build_successor_bootstrap_prompt,
    build_standard_followup_prompt,
    clear_reflow_backlog,
    extract_taskboard_protocol_footer,
    followup_path,
    reflow_backlog_summary,
    set_automation_mode,
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


class FollowupTests(unittest.TestCase):
    def test_extract_taskboard_protocol_footer_accepts_new_contract(self) -> None:
        protocol = extract_taskboard_protocol_footer(
            "\n".join(
                [
                    "TASKBOARD_SIGNAL=EXECUTION_READY",
                    "TASKBOARD_SELF_CHECK=pass",
                    "LIVE_TASK_STATUS=submitted",
                ]
            )
        )

        self.assertTrue(protocol["valid"])
        self.assertEqual(protocol["signal"], "EXECUTION_READY")
        self.assertEqual(protocol["effective_research_phase"], "execution")

    def test_build_standard_followup_prompt_uses_compact_footer_contract(self) -> None:
        prompt = build_standard_followup_prompt(
            {
                "proposal_path": "/tmp/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
            },
            continuous_research_enabled=False,
        )

        self.assertIn("TASKBOARD_SIGNAL=EXECUTION_READY|WAITING_ON_ASYNC|CLOSEOUT_READY|none", prompt)
        self.assertIn("TASKBOARD_SELF_CHECK=pass|fail", prompt)
        self.assertIn("LIVE_TASK_STATUS=none|submitted|awaiting", prompt)
        self.assertNotIn("TASKBOARD_PROTOCOL_ACK=TBP1", prompt)
        self.assertNotIn("FINAL_SIGNAL=", prompt)

    def test_build_continuous_planning_prompt_targets_execution_ready(self) -> None:
        prompt = build_continuous_planning_prompt(
            {
                "proposal_path": "/tmp/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "project_history_file": "/tmp/HISTORY.md",
                "project_history_file_source": "explicit",
            }
        )

        self.assertIn("你现在处于 planning。", prompt)
        self.assertIn("planning 完成标准不是写完一份空文档", prompt)
        self.assertIn("TASKBOARD_SIGNAL=EXECUTION_READY", prompt)

    def test_build_continuous_execution_prompt_keeps_unified_context(self) -> None:
        prompt = build_continuous_research_prompt(
            {
                "proposal_path": "/tmp/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/tmp/closeout",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/tmp/HISTORY.md",
                "project_history_file_source": "explicit",
            },
            trigger_signal="EXECUTION_READY",
        )

        self.assertIn("你现在处于 execution。", prompt)
        self.assertIn("统一 execution 上下文", prompt)
        self.assertIn("CPU-only 工作", prompt)
        self.assertIn("TASKBOARD_SIGNAL=WAITING_ON_ASYNC", prompt)

    def test_build_continuous_execution_prompt_scans_manual_gate_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proposal_path = Path(tmpdir) / "PROPOSAL.md"
            proposal_path.write_text(
                "\n".join(
                    [
                        "# proposal",
                        "当前 active continuation 已不再是继续自动补 launch-prep sidecar，而是等待对单一 manual dispatch handoff 的显式解释。",
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
                },
                trigger_signal="EXECUTION_READY",
            )

        self.assertIn("proposal 里看起来出现了人工确认点", prompt)
        self.assertIn("manual dispatch handoff", prompt)

    def test_build_continuous_transition_prompt_requires_handoff_confirmation(self) -> None:
        prompt = build_continuous_transition_prompt(
            {
                "proposal_path": "/tmp/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/tmp/closeout",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/tmp/HISTORY.md",
                "project_history_file_source": "explicit",
            },
            trigger_signal="CLOSEOUT_READY",
        )

        self.assertIn("你现在处于 closeout。", prompt)
        self.assertIn("handoff 确认", prompt)
        self.assertIn("强制开启新的 Codex session", prompt)

    def test_build_successor_bootstrap_prompt_forces_new_session_planning(self) -> None:
        prompt = build_successor_bootstrap_prompt(
            {
                "proposal_path": "/tmp/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/tmp/closeout",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/tmp/HISTORY.md",
                "project_history_file_source": "explicit",
            },
            predecessor_session_id="session-closeout-001",
            trigger_signal="none",
        )

        self.assertIn("强制开启的新 planning session", prompt)
        self.assertIn("上一轮已收口的 session", prompt)
        self.assertIn("planning 不要用 `TASKBOARD_SIGNAL=none` 停住", prompt)

    def test_managed_mode_only_becomes_active_after_explicit_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            self.assertFalse(automation_mode_is_managed(config, codex_session_id="session-001"))

            set_automation_mode(
                config,
                mode="managed",
                codex_session_id="session-001",
                updated_by="test",
                source="unit",
            )

            self.assertTrue(automation_mode_is_managed(config, codex_session_id="session-001"))
            self.assertFalse(automation_mode_is_managed(config, codex_session_id="session-002"))

    def test_status_payload_and_backlog_summary_surface_reflow_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            set_automation_mode(
                config,
                mode="continuous",
                codex_session_id="session-001",
                updated_by="test",
                source="unit",
            )
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

            summary = reflow_backlog_summary(config, codex_session_id="session-001")
            status = build_continuous_mode_status_payload(
                config,
                target_session_id="session-001",
                resolved_from="unit",
            )

            self.assertEqual(summary["queue_depth"], 2)
            self.assertEqual(status["automation_mode"], "continuous")
            self.assertFalse(status["managed_mode_active"])
            self.assertEqual(status["reflow_backlog_queue_depth"], 2)

            cleared = clear_reflow_backlog(config, codex_session_id="session-001")
            self.assertEqual(cleared["cleared_events"], 2)
            self.assertEqual(reflow_backlog_summary(config, codex_session_id="session-001")["queue_depth"], 0)


if __name__ == "__main__":
    unittest.main()
