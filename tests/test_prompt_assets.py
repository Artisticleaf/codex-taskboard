from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import build_resume_prompt, build_standard_followup_prompt
from codex_taskboard.prompt_assets import active_prompt_source


class PromptAssetTests(unittest.TestCase):
    def test_prompt_file_override_replaces_research_contract_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_file = Path(tmpdir) / "custom_prompt.toml"
            prompt_file.write_text(
                (
                    '[blocks]\n'
                    'managed_followup_intro = """\n'
                    '这是测试自定义 managed prompt。\n'
                    '"""\n'
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_TASKBOARD_PROMPT_FILE": str(prompt_file)}, clear=False):
                prompt = build_standard_followup_prompt(
                    {
                        "proposal_path": "/tmp/PLAN.md",
                        "proposal_source": "explicit",
                        "proposal_owner": True,
                    },
                    continuous_research_enabled=False,
                )
                prompt_source = active_prompt_source()

            self.assertIn("这是测试自定义 managed prompt", prompt)
            self.assertEqual(prompt_source, str(prompt_file.resolve()))

    def test_resume_prompt_keeps_footer_as_last_block(self) -> None:
        spec = {
            "task_id": "task-tail-order",
            "workdir": "/home/Awei/project",
            "command": "python audit.py",
            "execution_mode": "shell",
            "success_prompt": "",
            "failure_prompt": "",
            "task_note": "",
            "prompt_max_chars": 12000,
            "artifact_globs": [],
            "proposal_path": "/tmp/proposal.md",
            "proposal_source": "explicit",
            "proposal_owner": True,
        }
        event = {
            "status": "completed",
            "command_log_path": "/tmp/task-tail-order.log",
            "runner_log_path": "/tmp/task-tail-order-runner.log",
            "feedback_data_path": "/tmp/task-tail-order-feedback.json",
            "failure_kind": "completed",
            "failure_summary": "done",
            "duration_seconds": 3,
            "artifact_context": [{"pattern": "out.json", "path": "/tmp/out.json", "summary": "ok"}],
            "log_tail": "",
        }

        prompt = build_resume_prompt(spec, event)

        self.assertTrue(prompt.strip().endswith("LIVE_TASK_STATUS=none|submitted|awaiting"))
        footer_index = prompt.rfind("TASKBOARD_SIGNAL=EXECUTION_READY|WAITING_ON_ASYNC|CLOSEOUT_READY|none")
        artifact_index = prompt.rfind("结果文件路径：")
        self.assertGreater(footer_index, artifact_index)


if __name__ == "__main__":
    unittest.main()
