from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import (
    AppConfig,
    apply_local_submission_context,
    automation_mode_is_managed,
    build_continuous_mode_status_payload,
    build_continuous_planning_prompt,
    build_continuous_research_prompt,
    build_continuous_transition_prompt,
    build_successor_bootstrap_prompt,
    build_standard_followup_prompt,
    command_enter_stage,
    continuous_research_session_state,
    clear_reflow_backlog,
    extract_taskboard_protocol_footer,
    followup_key_for,
    followup_message_path,
    followup_path,
    process_single_followup,
    reflow_backlog_summary,
    resolve_api_service_port,
    stable_default_api_port,
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

        self.assertIn("现在处于 planning 阶段", prompt)
        self.assertIn("先做继承审计，不要急着开新题", prompt)
        self.assertIn("围绕 `project_history_file` 描述的主线科研目标", prompt)
        self.assertIn("当前主线处于什么科研阶段", prompt)
        self.assertIn("先做主线定位", prompt)
        self.assertIn("有顶刊/顶会潜力的模型或方法", prompt)
        self.assertIn("创新点必须从我们自己的证据里长出来", prompt)
        self.assertIn("首批实验包如何最大区分几种竞争解释", prompt)
        self.assertIn("创新点要讲清它来自哪条内部证据", prompt)
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

        self.assertIn("现在处于 execution 阶段", prompt)
        self.assertIn("持续推进 `project_history_file` 描述的主线科研目标", prompt)
        self.assertIn("如何改变我们对模型/方法性能、机制、可解释性、优势、不足或论文级证据链的判断", prompt)
        self.assertIn("它支持或削弱了当前 proposal 的哪条科学假设", prompt)
        self.assertIn("这一轮默认在同一个 execution 上下文里完成", prompt)
        self.assertIn("TASKBOARD_SIGNAL=WAITING_ON_ASYNC", prompt)

    def test_build_continuous_execution_prompt_adds_repeat_guard_after_repeated_microsteps(self) -> None:
        prompt = build_continuous_research_prompt(
            {
                "proposal_path": "/tmp/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "project_history_file": "/tmp/HISTORY.md",
                "project_history_file_source": "explicit",
                "current_session_state": {
                    "next_action_repeat_count": 5,
                },
            },
            trigger_signal="EXECUTION_READY",
        )

        self.assertIn("不要再把它拆成下一小步", prompt)
        self.assertIn("转入 closeout", prompt)

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

        self.assertIn("现在处于 closeout 阶段", prompt)
        self.assertIn("closeout 初审", prompt)
        self.assertIn("面向主线目标的综合分析", prompt)
        self.assertIn("框架/方法完成度如何", prompt)
        self.assertIn("核心创新点、优势、不足", prompt)
        self.assertIn("强制开启新的 Codex session", prompt)
        self.assertIn("proposal、history、handoff 三个入口分别是哪一份文件", prompt)

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

        self.assertIn("强制创建的新 Codex session", prompt)
        self.assertIn("上一轮已收口的 session", prompt)
        self.assertIn("这个新 session 的特殊任务，是先复审上一轮 closeout 的可靠性", prompt)

    def test_continuous_transition_consumes_cached_none_without_resuming_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec = {
                "task_id": "task-closeout-001",
                "task_key": "task-closeout",
                "codex_session_id": "session-old-001",
                "agent_name": "research-agent",
            }
            followup_key = followup_key_for(spec)
            followup = {
                "version": 1,
                "followup_key": followup_key,
                "followup_type": "continuous_research_closeout_transition",
                "task_id": "task-closeout-001",
                "task_key": "task-closeout",
                "execution_mode": "shell",
                "codex_session_id": "session-old-001",
                "agent_name": "research-agent",
                "proposal_path": "/tmp/PLAN.md",
                "proposal_source": "explicit",
                "proposal_owner": True,
                "closeout_proposal_dir": "/tmp/closeout",
                "closeout_proposal_dir_source": "explicit",
                "project_history_file": "/tmp/HISTORY.md",
                "project_history_file_source": "explicit",
                "workdir": "/tmp/project",
                "reason": "continuous_research_closeout_transition",
                "created_at": "2026-04-24T00:00:00+08:00",
                "check_after_ts": 0,
                "interval_seconds": 300,
                "min_idle_seconds": 0,
                "nudge_count": 0,
                "stopped": False,
                "last_signal": "",
                "codex_exec_mode": "dangerous",
                "resume_timeout_seconds": 3600,
                "fallback_provider": "",
                "prompt_max_chars": 12000,
            }
            message_path = followup_message_path(config, followup_key)
            message_path.parent.mkdir(parents=True, exist_ok=True)
            message_path.write_text(
                "closeout done\nTASKBOARD_SIGNAL=none\nTASKBOARD_SELF_CHECK=pass\nLIVE_TASK_STATUS=none\n",
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.resume_codex_session_with_prompt") as resume_mock:
                with patch(
                    "codex_taskboard.cli.bootstrap_successor_session_after_closeout",
                    return_value={
                        "ok": True,
                        "action": "successor_bootstrap_execution_scheduled",
                        "successor_session_id": "session-new-001",
                        "taskboard_signal": "EXECUTION_READY",
                    },
                ) as bootstrap_mock:
                    processed = process_single_followup(config, followup)

            resume_mock.assert_not_called()
            bootstrap_mock.assert_called_once()
            self.assertEqual(bootstrap_mock.call_args.kwargs["predecessor_session_id"], "session-old-001")
            self.assertEqual(bootstrap_mock.call_args.kwargs["trigger_signal"], "none")
            self.assertEqual(processed[-1]["action"], "successor_bootstrap_execution_scheduled")
            self.assertEqual(processed[-1]["successor_session_id"], "session-new-001")

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

    def test_enter_stage_binds_current_session_and_persists_runtime_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "taskboard-home"
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            proposal_path = project_dir / "PROPOSAL.md"
            proposal_path.write_text("# proposal\n", encoding="utf-8")
            history_path = project_dir / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            handoff_path = project_dir / "HANDOFF.md"
            handoff_path.write_text("# handoff\n", encoding="utf-8")
            closeout_dir = project_dir / "closeout"
            closeout_dir.mkdir(parents=True, exist_ok=True)
            args = Namespace(
                app_home=str(app_home),
                codex_home=str(app_home / "codex-home"),
                codex_bin="codex",
                tmux_bin="tmux",
                stage="planning",
                codex_session_id="",
                workdir=str(project_dir),
                agent_name="",
                proposal=str(proposal_path),
                closeout_proposal_dir=str(closeout_dir),
                project_history_file=str(history_path),
                handoff_file=str(handoff_path),
                trigger_signal="",
                successor_bootstrap=False,
                predecessor_session_id="",
                automation_mode="managed",
                no_bind=False,
                json=False,
            )
            stdout = io.StringIO()
            with patch.dict(os.environ, {"CODEX_SESSION_ID": "session-enter-001", "PWD": str(project_dir)}, clear=False):
                with redirect_stdout(stdout):
                    exit_code = command_enter_stage(args)

            self.assertEqual(exit_code, 0)
            self.assertIn("现在处于 planning 阶段", stdout.getvalue())
            state = continuous_research_session_state(build_config(app_home), "session-enter-001")
            self.assertEqual(state["proposal_path"], str(proposal_path.resolve()))
            self.assertEqual(state["project_history_file"], str(history_path.resolve()))
            self.assertEqual(state["handoff_file"], str(handoff_path.resolve()))
            self.assertEqual(state["research_phase"], "planning")

    def test_apply_local_submission_context_inherits_bound_stage_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "taskboard-home"
            config = build_config(app_home)
            project_dir = Path(tmpdir) / "project"
            project_dir.mkdir(parents=True, exist_ok=True)
            proposal_path = project_dir / "PROPOSAL.md"
            proposal_path.write_text("# proposal\n", encoding="utf-8")
            history_path = project_dir / "HISTORY.md"
            history_path.write_text("# history\n", encoding="utf-8")
            closeout_dir = project_dir / "closeout"
            closeout_dir.mkdir(parents=True, exist_ok=True)
            args = Namespace(
                app_home=str(app_home),
                codex_home=str(app_home / "codex-home"),
                codex_bin="codex",
                tmux_bin="tmux",
                stage="execution",
                codex_session_id="session-bind-001",
                workdir=str(project_dir),
                agent_name="",
                proposal=str(proposal_path),
                closeout_proposal_dir=str(closeout_dir),
                project_history_file=str(history_path),
                handoff_file=None,
                trigger_signal="",
                successor_bootstrap=False,
                predecessor_session_id="",
                automation_mode="keep",
                no_bind=False,
                json=False,
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(command_enter_stage(args), 0)

            spec = apply_local_submission_context(
                config,
                {
                    "workdir": str(project_dir),
                    "codex_session_id": "session-bind-001",
                    "feedback_mode": "auto",
                    "agent_name": "",
                },
            )

            self.assertEqual(spec["proposal_path"], str(proposal_path.resolve()))
            self.assertEqual(spec["project_history_file"], str(history_path.resolve()))
            self.assertEqual(spec["closeout_proposal_dir"], str(closeout_dir.resolve()))

    def test_resolve_api_service_port_picks_stable_free_port_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            stable_port = stable_default_api_port(config)
            with patch(
                "codex_taskboard.cli.can_bind_tcp_port",
                side_effect=lambda bind, port: int(port) == stable_port + 1,
            ):
                resolved_port, source = resolve_api_service_port(
                    config,
                    requested_port=0,
                    bind="127.0.0.1",
                )

            self.assertEqual(resolved_port, stable_port + 1)
            self.assertEqual(source, "auto_hash_scan")
            persisted_port, persisted_source = resolve_api_service_port(
                config,
                requested_port=0,
                bind="127.0.0.1",
            )
            self.assertEqual(persisted_port, stable_port + 1)
            self.assertEqual(persisted_source, "stored")

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
