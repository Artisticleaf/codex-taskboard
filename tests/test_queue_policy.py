import io
import json
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import (
    AppConfig,
    CLOSEOUT_PROPOSAL_DIR_ENV_KEY,
    PROPOSAL_ENV_KEY,
    PROPOSAL_SOURCE_ENV_KEY,
    apply_local_submission_context,
    build_task_result_payload,
    build_remote_ssh_command,
    build_spec_from_submit_job_payload,
    build_config as build_runtime_config,
    command_bind_before_launch,
    command_submit_job,
    command_status,
    command_status_result,
    command_cleanup,
    command_current_thread,
    command_run,
    command_wait_result,
    compute_attention,
    dependency_satisfied,
    dispatch_queued_tasks,
    extract_failure_excerpt,
    load_task_state,
    load_task_spec,
    looks_like_training_command,
    persist_task_cpu_assignment,
    parse_timestamp_to_unix,
    prepare_task_slot,
    select_cpu_resources_for_start,
    start_existing_task,
    submit_spec,
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


def write_state(config: AppConfig, task_id: str, **fields: object) -> None:
    task_dir = config.tasks_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "task_id": task_id,
        "task_key": fields.get("task_key", task_id),
        "status": "queued",
        "submitted_at": "2026-03-16T19:29:44Z",
        "priority": 0,
        **fields,
    }
    write_task_state(config, task_id, payload)


def write_spec(config: AppConfig, task_id: str, **fields: object) -> None:
    task_dir = config.tasks_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "task_id": task_id,
        "task_key": fields.get("task_key", task_id),
        "execution_mode": "shell",
        "workdir": str(config.app_home),
        "command": "python train.py",
        "codex_session_id": "session-1",
        "priority": 0,
        "gpu_slots": 0,
        **fields,
    }
    (task_dir / "spec.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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


class QueuePolicyTests(unittest.TestCase):
    def test_build_config_uses_explicit_app_home(self) -> None:
        args = Namespace(
            app_home="/tmp/codex-taskboard-test-home",
            codex_home="/tmp/codex-home-test",
            codex_bin="codex",
            tmux_bin="tmux",
        )
        config = build_runtime_config(args)
        self.assertEqual(config.app_home, Path("/tmp/codex-taskboard-test-home").resolve())
        self.assertEqual(config.tasks_root, Path("/tmp/codex-taskboard-test-home").resolve() / "tasks")

    def test_compute_attention_marks_early_failure(self) -> None:
        event = {
            "status": "failed",
            "failure_kind": "oom",
            "duration_seconds": 5,
        }
        spec = {"startup_failure_threshold_seconds": 90}
        needs_attention, reason, message = compute_attention(event, spec)
        self.assertTrue(needs_attention)
        self.assertEqual(reason, "startup_failure:oom")
        self.assertIn("非常早的阶段就失败", message)

    def test_compute_attention_marks_terminal_signal_without_closeout_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "taskboard-home"
            config = build_config(app_home)
            proposal_dir = Path(tmpdir) / "project" / "experiments"
            proposal_dir.mkdir(parents=True, exist_ok=True)
            proposal_path = proposal_dir / "PROPOSAL-TEST-20260405.md"
            proposal_path.write_text("# proposal\n", encoding="utf-8")
            closeout_dir = Path(tmpdir) / "project" / "closeout_proposal"
            closeout_dir.mkdir(parents=True, exist_ok=True)
            now_ts = time.time()
            event = {
                "task_id": "task-a",
                "status": "completed",
                "failure_kind": "",
                "ended_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "taskboard_signal": "NO_FURTHER_TASKS",
            }
            spec = {
                "task_id": "task-a",
                "workdir": str(Path(tmpdir) / "project"),
                "proposal_path": str(proposal_path),
                "closeout_proposal_dir": str(closeout_dir),
                "codex_session_id": "session-1",
            }
            with patch.dict(
                "os.environ",
                {
                    "CODEX_TASKBOARD_HOME": str(config.app_home),
                    "CODEX_HOME": str(config.codex_home),
                },
                clear=False,
            ):
                needs_attention, reason, message = compute_attention(event, spec)

            self.assertTrue(needs_attention)
            self.assertEqual(reason, "research_stall:terminal_signal_without_closeout_or_followthrough")
            self.assertIn("阶段性终止信号", message)

    def test_compute_attention_marks_queued_backlog_after_proposal_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "taskboard-home"
            config = build_config(app_home)
            project_dir = Path(tmpdir) / "project"
            proposal_dir = project_dir / "experiments"
            proposal_dir.mkdir(parents=True, exist_ok=True)
            old_proposal = proposal_dir / "PROPOSAL-OLD-20260404.md"
            new_proposal = proposal_dir / "PROPOSAL-NEW-20260405.md"
            old_proposal.write_text("# old\n", encoding="utf-8")
            new_proposal.write_text("# new\n", encoding="utf-8")
            now_ts = time.time()
            os.utime(old_proposal, (now_ts - 7200, now_ts - 7200))
            os.utime(new_proposal, (now_ts, now_ts))
            write_state(
                config,
                "queued-old",
                task_key="queued-old",
                status="queued",
                submitted_at=datetime.fromtimestamp(now_ts - 7200, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                workdir=str(project_dir),
                proposal_path=str(old_proposal),
                codex_session_id="session-1",
            )
            event = {
                "task_id": "task-b",
                "status": "completed",
                "failure_kind": "",
                "ended_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "taskboard_signal": "TASK_DONE",
            }
            spec = {
                "task_id": "task-b",
                "workdir": str(project_dir),
                "proposal_path": str(new_proposal),
                "closeout_proposal_dir": str(project_dir / "closeout_proposal"),
                "codex_session_id": "session-1",
            }
            with patch.dict(
                "os.environ",
                {
                    "CODEX_TASKBOARD_HOME": str(config.app_home),
                    "CODEX_HOME": str(config.codex_home),
                },
                clear=False,
            ):
                needs_attention, reason, message = compute_attention(event, spec)

            self.assertTrue(needs_attention)
            self.assertEqual(reason, "research_stall:queued_backlog_after_proposal_switch")
            self.assertIn("旧 proposal", message)

    def test_compute_attention_marks_no_dispatch_after_proposal_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "taskboard-home"
            config = build_config(app_home)
            project_dir = Path(tmpdir) / "project"
            proposal_dir = project_dir / "experiments"
            proposal_dir.mkdir(parents=True, exist_ok=True)
            proposal_path = proposal_dir / "PROPOSAL-IDLE-20260405.md"
            proposal_path.write_text("# idle\n", encoding="utf-8")
            now_ts = time.time()
            old_ts = now_ts - 8 * 60 * 60
            os.utime(proposal_path, (old_ts, old_ts))
            event = {
                "task_id": "task-c",
                "status": "completed",
                "failure_kind": "",
                "ended_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "taskboard_signal": "TASK_DONE",
            }
            spec = {
                "task_id": "task-c",
                "workdir": str(project_dir),
                "proposal_path": str(proposal_path),
                "closeout_proposal_dir": str(project_dir / "closeout_proposal"),
                "codex_session_id": "session-2",
            }
            with patch.dict(
                "os.environ",
                {
                    "CODEX_TASKBOARD_HOME": str(config.app_home),
                    "CODEX_HOME": str(config.codex_home),
                },
                clear=False,
            ):
                needs_attention, reason, message = compute_attention(event, spec)

            self.assertTrue(needs_attention)
            self.assertEqual(reason, "research_stall:no_dispatch_after_proposal_update")
            self.assertIn("proposal 已更新较长时间", message)

    def test_compute_attention_marks_repeated_followup_without_queue_hygiene(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "taskboard-home"
            config = build_config(app_home)
            project_dir = Path(tmpdir) / "project"
            proposal_dir = project_dir / "experiments"
            proposal_dir.mkdir(parents=True, exist_ok=True)
            proposal_path = proposal_dir / "PROPOSAL-FOLLOWUP-20260405.md"
            proposal_path.write_text("# followup\n", encoding="utf-8")
            now_ts = time.time()
            proposal_ts = now_ts - 4 * 60 * 60
            os.utime(proposal_path, (proposal_ts, proposal_ts))
            write_state(
                config,
                "queued-stale-001",
                task_key="queued-stale",
                status="queued",
                submitted_at=datetime.fromtimestamp(now_ts - 3 * 60 * 60, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                workdir=str(project_dir),
                proposal_path=str(proposal_path),
                codex_session_id="session-followup-1",
            )
            write_state(
                config,
                "followup-a-001",
                task_key="followup-worker-a",
                status="completed",
                submitted_at=datetime.fromtimestamp(now_ts - 90 * 60, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                workdir=str(project_dir),
                proposal_path=str(proposal_path),
                codex_session_id="session-followup-1",
                command="python followup_worker.py",
            )
            write_state(
                config,
                "followup-b-001",
                task_key="monitor-worker-b",
                status="completed",
                submitted_at=datetime.fromtimestamp(now_ts - 60 * 60, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                workdir=str(project_dir),
                proposal_path=str(proposal_path),
                codex_session_id="session-followup-1",
                task_note="followup monitor pass",
            )
            event = {
                "task_id": "task-d",
                "status": "completed",
                "failure_kind": "",
                "ended_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "taskboard_signal": "TASK_DONE",
            }
            spec = {
                "task_id": "task-d",
                "workdir": str(project_dir),
                "proposal_path": str(proposal_path),
                "closeout_proposal_dir": str(project_dir / "closeout_proposal"),
                "codex_session_id": "session-followup-1",
                "research_stall_followup_threshold": 2,
            }
            with patch.dict(
                "os.environ",
                {
                    "CODEX_TASKBOARD_HOME": str(config.app_home),
                    "CODEX_HOME": str(config.codex_home),
                },
                clear=False,
            ):
                needs_attention, reason, message = compute_attention(event, spec)

            self.assertTrue(needs_attention)
            self.assertEqual(reason, "research_stall:repeated_followup_without_queue_hygiene")
            self.assertIn("queue hygiene", message)

    def test_load_task_state_returns_empty_for_missing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            self.assertEqual(load_task_state(config, "missing-task"), {})

    def test_command_status_reports_missing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            args = Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                task_id="missing-task",
                limit=20,
                json=True,
            )
            stderr = io.StringIO()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch(
                "codex_taskboard.cli.detect_gpu_count",
                return_value=0,
            ), patch("sys.stderr", stderr):
                rc = command_status(args)

            self.assertEqual(rc, 1)
            self.assertIn("Task not found: missing-task", stderr.getvalue())

    def test_prepare_task_slot_supersedes_terminal_same_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "old-task", task_key="same-key", status="failed")

            prepare_task_slot(config, task_id="new-task", task_key="same-key", replace_existing=True)

            old_state = json.loads((config.tasks_root / "old-task" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(old_state["status"], "superseded")
            self.assertEqual(old_state["superseded_by"], "new-task")

    def test_prepare_task_slot_rejects_active_same_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "old-task", task_key="same-key", status="queued")

            with self.assertRaisesRegex(ValueError, "Active or queued task"):
                prepare_task_slot(config, task_id="new-task", task_key="same-key", replace_existing=True)

    def test_dependency_requires_signal_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "dep-a",
                task_key="dep-a",
                status="completed",
                require_signal_to_unblock=True,
                taskboard_signal="",
            )
            self.assertFalse(dependency_satisfied(config, "dep-a"))
            write_state(
                config,
                "dep-a",
                task_key="dep-a",
                status="completed",
                require_signal_to_unblock=True,
                taskboard_signal="TASK_DONE",
            )
            self.assertTrue(dependency_satisfied(config, "dep-a"))

    def test_dispatch_gpu_fill_respects_gpu_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "gpu2", task_key="gpu2", status="queued", submitted_at="2026-03-16T00:00:00Z", gpu_slots=2)
            write_state(config, "gpu1", task_key="gpu1", status="queued", submitted_at="2026-03-16T00:00:01Z", gpu_slots=1)
            started: list[str] = []

            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=0), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, **_kwargs: started.append(task_id),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="gpu-fill",
                    max_running=0,
                    limit=100,
                    gpu_count_override=2,
                    cpu_thread_limit=40,
                )

            self.assertEqual(started, ["gpu2"])
            self.assertEqual(result["started"], ["gpu2"])
            self.assertEqual(result["total_gpu_slots"], 2)

    def test_dispatch_gpu_fill_places_four_gpu_job_on_partially_busy_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "big4", task_key="big4", status="queued", submitted_at="2026-03-16T00:00:00Z", gpu_slots=4, priority=100)
            write_spec(config, "big4", gpu_slots=4)
            started: list[tuple[str, list[int] | None, str]] = []

            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=1), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[
                    {"index": 0, "name": "GPU0", "memory_total_mb": 32607, "memory_used_mb": 3887, "gpu_util_percent": 30},
                    {"index": 1, "name": "GPU1", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                    {"index": 2, "name": "GPU2", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                    {"index": 3, "name": "GPU3", "memory_total_mb": 32607, "memory_used_mb": 44, "gpu_util_percent": 0},
                ],
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, assigned_gpus=None, assignment_source="", **_kwargs: started.append((task_id, assigned_gpus, assignment_source)),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="gpu-fill",
                    max_running=0,
                    limit=100,
                    gpu_count_override=4,
                    cpu_thread_limit=40,
                )

            self.assertEqual(started, [("big4", [0, 1, 2, 3], "scheduler")])
            self.assertEqual(result["placements"], {"big4": [0, 1, 2, 3]})
            self.assertTrue(result["headroom_scheduler"])

    def test_dispatch_gpu_fill_skips_impossible_large_job_and_backfills_smaller_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "big4", task_key="big4", status="queued", submitted_at="2026-03-16T00:00:00Z", gpu_slots=4, priority=100)
            write_state(config, "small1", task_key="small1", status="queued", submitted_at="2026-03-16T00:00:01Z", gpu_slots=1, priority=90)
            write_spec(config, "big4", gpu_slots=4)
            write_spec(config, "small1", gpu_slots=1)
            started: list[tuple[str, list[int] | None, str]] = []

            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=0), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[
                    {"index": 0, "name": "GPU0", "memory_total_mb": 32607, "memory_used_mb": 12000, "gpu_util_percent": 97},
                    {"index": 1, "name": "GPU1", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                    {"index": 2, "name": "GPU2", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                    {"index": 3, "name": "GPU3", "memory_total_mb": 32607, "memory_used_mb": 44, "gpu_util_percent": 0},
                ],
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, assigned_gpus=None, assignment_source="", **_kwargs: started.append((task_id, assigned_gpus, assignment_source)),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="gpu-fill",
                    max_running=0,
                    limit=100,
                    gpu_count_override=4,
                    cpu_thread_limit=40,
                )

            self.assertEqual(started, [("small1", [1], "scheduler")])
            self.assertEqual(result["started"], ["small1"])

    def test_dispatch_gpu_fill_respects_allowed_gpu_pool_for_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "lxy-gpu2", task_key="lxy-gpu2", status="queued", submitted_at="2026-03-16T00:00:00Z", gpu_slots=2)
            write_spec(config, "lxy-gpu2", gpu_slots=2, allowed_gpus=[1, 2, 3], command="python train.py")
            started: list[tuple[str, list[int] | None, str]] = []

            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=0), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[
                    {"index": 0, "name": "GPU0", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                    {"index": 1, "name": "GPU1", "memory_total_mb": 32607, "memory_used_mb": 20000, "gpu_util_percent": 92},
                    {"index": 2, "name": "GPU2", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                    {"index": 3, "name": "GPU3", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                ],
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, assigned_gpus=None, assignment_source="", **_kwargs: started.append((task_id, assigned_gpus, assignment_source)),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="gpu-fill",
                    max_running=0,
                    limit=100,
                    gpu_count_override=4,
                    cpu_thread_limit=40,
                )

            self.assertEqual(started, [("lxy-gpu2", [2, 3], "scheduler")])
            self.assertEqual(result["started"], ["lxy-gpu2"])

    def test_extract_failure_excerpt_prefers_traceback_window(self) -> None:
        text = "\n".join(
            [
                "epoch=1",
                "loss=1.2",
                "Traceback (most recent call last):",
                '  File "train.py", line 10, in <module>',
                "    raise RuntimeError('oom happened')",
                "RuntimeError: CUDA out of memory",
                "cleanup done",
            ]
        )
        excerpt = extract_failure_excerpt(text, status="failed", failure_kind="python_traceback")
        self.assertIn("Traceback", excerpt)
        self.assertIn("RuntimeError: CUDA out of memory", excerpt)

    def test_dispatch_gpu_fill_backfills_cpu_only_tasks_with_cpu_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            for idx in range(4):
                write_state(
                    config,
                    f"gpu-run-{idx}",
                    task_key=f"gpu-run-{idx}",
                    status="running",
                    submitted_at=f"2026-03-16T00:00:0{idx}Z",
                    gpu_slots=1,
                    cpu_threads=4,
                    tmux_session_name=f"ctb-gpu-run-{idx}",
                )
            write_state(config, "cpu-a", task_key="cpu-a", status="queued", submitted_at="2026-03-16T00:01:00Z", gpu_slots=0, cpu_threads=10)
            write_state(config, "cpu-b", task_key="cpu-b", status="queued", submitted_at="2026-03-16T00:01:01Z", gpu_slots=0, cpu_threads=10)
            write_state(config, "cpu-c", task_key="cpu-c", status="queued", submitted_at="2026-03-16T00:01:02Z", gpu_slots=0, cpu_threads=10)
            write_spec(config, "cpu-a", gpu_slots=0, cpu_threads=10, command="python summarize_a.py")
            write_spec(config, "cpu-b", gpu_slots=0, cpu_threads=10, command="python summarize_b.py")
            write_spec(config, "cpu-c", gpu_slots=0, cpu_threads=10, command="python summarize_c.py")
            started: list[str] = []

            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=4), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, **_kwargs: started.append(task_id),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="gpu-fill",
                    max_running=0,
                    limit=100,
                    gpu_count_override=4,
                    cpu_thread_limit=40,
                )

            self.assertEqual(started, ["cpu-a", "cpu-b"])
            self.assertEqual(result["started"], ["cpu-a", "cpu-b"])
            self.assertEqual(result["active_cpu_threads"], 16)
            self.assertEqual(result["cpu_thread_limit"], 40)

    def test_select_cpu_resources_prefers_workers_for_gpu_feeder_profile(self) -> None:
        assignment = select_cpu_resources_for_start(
            {
                "execution_mode": "shell",
                "command": "python train.py",
                "gpu_slots": 1,
                "cpu_profile": "gpu_feeder",
                "cpu_threads": 2,
                "cpu_workers_min": 4,
                "cpu_workers_max": 12,
            },
            available_cpu_threads=12,
        )

        self.assertEqual(assignment["cpu_profile"], "gpu_feeder")
        self.assertEqual(assignment["assigned_cpu_threads"], 2)
        self.assertEqual(assignment["assigned_cpu_workers"], 10)
        self.assertEqual(assignment["assigned_cpu_budget"], 12)

    def test_persist_task_cpu_assignment_renders_command_template_and_runtime_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_spec(
                config,
                "templated-cpu-20260319",
                command_template="python train.py --threads {cpu_threads} --num-workers {cpu_workers}",
                command="python train.py --threads {cpu_threads} --num-workers {cpu_workers}",
                cpu_profile="gpu_feeder",
                cpu_threads=2,
                cpu_workers_min=4,
                cpu_workers_max=12,
            )
            write_state(config, "templated-cpu-20260319", task_key="templated-cpu", status="queued")

            updated = persist_task_cpu_assignment(
                config,
                "templated-cpu-20260319",
                load_task_spec(config, "templated-cpu-20260319"),
                cpu_threads=3,
                cpu_workers=7,
                assignment_source="scheduler",
                worker_assignment_source="scheduler",
            )

            self.assertEqual(updated["command"], "python train.py --threads 3 --num-workers 7")
            self.assertEqual(updated["env"]["CODEX_TASKBOARD_CPU_THREADS"], "3")
            self.assertEqual(updated["env"]["CODEX_TASKBOARD_CPU_WORKERS"], "7")
            self.assertEqual(updated["env"]["CODEX_TASKBOARD_CPU_BUDGET"], "10")
            self.assertEqual(updated["env"]["OMP_NUM_THREADS"], "3")
            state = load_task_state(config, "templated-cpu-20260319")
            self.assertEqual(state["assigned_cpu_threads"], 3)
            self.assertEqual(state["assigned_cpu_workers"], 7)
            self.assertEqual(state["cpu_profile"], "gpu_feeder")

    def test_build_task_result_payload_includes_cpu_profile_worker_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_spec(
                config,
                "cpu-result-20260319",
                command="python eval.py",
                cpu_profile="hybrid",
                cpu_threads=4,
                cpu_workers=6,
                assigned_cpu_threads=4,
                assigned_cpu_workers=6,
            )
            write_state(
                config,
                "cpu-result-20260319",
                task_key="cpu-result",
                status="completed",
                workdir=str(config.app_home),
                command="python eval.py",
                cpu_profile="hybrid",
                cpu_threads=4,
                assigned_cpu_threads=4,
                cpu_workers=6,
                assigned_cpu_workers=6,
                cpu_budget=10,
                ended_at="2026-03-19T00:01:00Z",
                exit_code=0,
            )

            with patch("codex_taskboard.cli.get_gpu_summary_table", return_value=[]), patch(
                "codex_taskboard.cli.detect_gpu_count",
                return_value=0,
            ):
                payload = build_task_result_payload(config, "cpu-result-20260319")

            self.assertEqual(payload["cpu_profile"], "hybrid")
            self.assertEqual(payload["assigned_cpu_threads"], 4)
            self.assertEqual(payload["assigned_cpu_workers"], 6)
            self.assertEqual(payload["cpu_budget"], 10)

    def test_submit_spec_keeps_cpu_only_task_queued_when_cpu_budget_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "cpu-busy",
                task_key="cpu-busy",
                status="running",
                cpu_threads=32,
                tmux_session_name="ctb-cpu-busy",
                command="python busy.py",
                workdir=str(config.app_home),
                codex_session_id="session-1",
            )
            with patch("codex_taskboard.cli.detect_default_cpu_thread_limit", return_value=40), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch(
                "codex_taskboard.cli.detect_gpu_count",
                return_value=0,
            ), patch("codex_taskboard.cli.start_existing_task", side_effect=AssertionError("should not start")):
                result = submit_spec(
                    config,
                    {
                        "task_id": "cpu-followup-20260318",
                        "task_key": "cpu-followup",
                        "workdir": str(config.app_home),
                        "command": "python summarize.py",
                        "codex_session_id": "session-1",
                        "cpu_threads": 12,
                    },
                    hold=False,
                )

            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["phase"], "blocked_by_cpu_budget")
            self.assertIn("cpu_budget:need=12:available=8:limit=40", result["blocked_reason"])

    def test_dispatch_gpu_fill_starts_sidecar_task_without_cpu_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            for idx in range(4):
                write_state(
                    config,
                    f"gpu-run-{idx}",
                    task_key=f"gpu-run-{idx}",
                    status="running",
                    submitted_at=f"2026-03-16T00:00:0{idx}Z",
                    gpu_slots=1,
                    cpu_threads=10,
                    tmux_session_name=f"ctb-gpu-run-{idx}",
                )
            write_state(
                config,
                "receipt",
                task_key="receipt",
                status="queued",
                submitted_at="2026-03-16T00:01:00Z",
                gpu_slots=0,
                cpu_profile="sidecar",
            )
            write_spec(
                config,
                "receipt",
                gpu_slots=0,
                cpu_profile="sidecar",
                command="python poll_receipt.py",
            )

            started: list[str] = []
            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=4), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, **_kwargs: started.append(task_id),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="gpu-fill",
                    max_running=0,
                    limit=100,
                    gpu_count_override=4,
                    cpu_thread_limit=40,
                )

            self.assertEqual(started, ["receipt"])
            self.assertEqual(result["started"], ["receipt"])
            self.assertEqual(result["active_cpu_threads"], 40)

    def test_submit_spec_defaults_cpu_only_python_task_to_adaptive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))

            result = submit_spec(
                config,
                {
                    "task_id": "cpu-adaptive-default-20260318",
                    "task_key": "cpu-adaptive-default",
                    "workdir": str(config.app_home),
                    "command": "python summarize.py",
                    "codex_session_id": "session-1",
                },
                hold=True,
            )

            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["cpu_threads_mode"], "adaptive")
            self.assertEqual(result["cpu_threads_min"], 4)
            self.assertEqual(result["cpu_threads_max"], 0)

    def test_submit_spec_allows_missing_codex_session_when_feedback_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))

            result = submit_spec(
                config,
                {
                    "task_id": "agentless-job-20260319",
                    "task_key": "agentless-job",
                    "workdir": str(config.app_home),
                    "command": "python eval.py",
                    "feedback_mode": "off",
                    "report_format": "key-value",
                },
                hold=True,
            )

            self.assertEqual(result["status"], "queued")
            spec = json.loads((config.tasks_root / "agentless-job-20260319" / "spec.json").read_text(encoding="utf-8"))
            self.assertEqual(spec["codex_session_id"], "")
            self.assertEqual(spec["feedback_mode"], "off")

    def test_submit_job_auto_inherits_codex_thread_id_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            submit_args = Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                task_id="agentless-env-submit-20260327",
                task_key="agentless-env-submit",
                workdir=str(config.app_home),
                command="python eval.py",
                executor="",
                codex_session_id="",
                agent_name="",
                priority=0,
                gpu_slots=None,
                assigned_gpus=[],
                cpu_profile="auto",
                cpu_threads=0,
                cpu_threads_min=0,
                cpu_threads_max=0,
                cpu_threads_mode="",
                cpu_workers=0,
                cpu_workers_min=0,
                cpu_workers_max=0,
                gpu_min_free_mb=0,
                gpu_max_util_percent=0,
                feedback_mode="auto",
                depends_on=[],
                required_artifact_glob=[],
                required_report=[],
                report_format="key-value",
                report_key=[],
                report_contract="",
                success_prompt=None,
                success_prompt_file=None,
                failure_prompt=None,
                failure_prompt_file=None,
                task_note="",
                artifact_glob=[],
                env=[],
                codex_exec_mode="dangerous",
                resume_timeout_seconds=7200,
                launch_grace_seconds=0,
                prompt_max_chars=12000,
                log_tail_lines=80,
                log_tail_chars=5000,
                artifact_max_chars=1200,
                artifact_max_lines=40,
                startup_failure_threshold_seconds=90,
                fallback_provider="",
                allow_session_rebind=False,
                no_replace_existing=False,
                hold=True,
            )
            stdout = io.StringIO()
            with patch("codex_taskboard.cli.build_config", return_value=config), patch.dict(
                "os.environ",
                {"CODEX_THREAD_ID": "thread-env-123"},
                clear=False,
            ), patch("sys.stdout", stdout):
                rc = command_submit_job(submit_args)

            self.assertEqual(rc, 0)
            spec = json.loads((config.tasks_root / "agentless-env-submit-20260327" / "spec.json").read_text(encoding="utf-8"))
            self.assertEqual(spec["codex_session_id"], "thread-env-123")

    def test_submit_job_auto_inherits_proposal_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = str((config.app_home / "plan.md").resolve())
            closeout_dir = str((config.app_home / "closeout_proposal").resolve())
            submit_args = Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                task_id="agentless-proposal-env-20260329",
                task_key="agentless-proposal-env",
                workdir=str(config.app_home),
                command="python eval.py",
                executor="",
                codex_session_id="",
                agent_name="",
                priority=0,
                gpu_slots=None,
                assigned_gpus=[],
                cpu_profile="auto",
                cpu_threads=0,
                cpu_threads_min=0,
                cpu_threads_max=0,
                cpu_threads_mode="",
                cpu_workers=0,
                cpu_workers_min=0,
                cpu_workers_max=0,
                gpu_min_free_mb=0,
                gpu_max_util_percent=0,
                feedback_mode="auto",
                depends_on=[],
                required_artifact_glob=[],
                required_report=[],
                report_format="key-value",
                report_key=[],
                report_contract="",
                success_prompt=None,
                success_prompt_file=None,
                failure_prompt=None,
                failure_prompt_file=None,
                task_note="",
                artifact_glob=[],
                env=[],
                codex_exec_mode="dangerous",
                resume_timeout_seconds=7200,
                launch_grace_seconds=0,
                prompt_max_chars=12000,
                log_tail_lines=80,
                log_tail_chars=5000,
                artifact_max_chars=1200,
                artifact_max_lines=40,
                startup_failure_threshold_seconds=90,
                fallback_provider="",
                allow_session_rebind=False,
                no_replace_existing=False,
                hold=True,
            )
            stdout = io.StringIO()
            with patch("codex_taskboard.cli.build_config", return_value=config), patch.dict(
                "os.environ",
                {
                    "CODEX_THREAD_ID": "thread-env-123",
                    PROPOSAL_ENV_KEY: proposal_path,
                    CLOSEOUT_PROPOSAL_DIR_ENV_KEY: closeout_dir,
                },
                clear=False,
            ), patch("sys.stdout", stdout):
                rc = command_submit_job(submit_args)

            self.assertEqual(rc, 0)
            spec = json.loads((config.tasks_root / "agentless-proposal-env-20260329" / "spec.json").read_text(encoding="utf-8"))
            self.assertEqual(spec["proposal_path"], proposal_path)
            self.assertEqual(spec["proposal_source"], "env")
            self.assertEqual(spec["env"][PROPOSAL_ENV_KEY], proposal_path)
            self.assertEqual(spec["closeout_proposal_dir"], closeout_dir)
            self.assertEqual(spec["closeout_proposal_dir_source"], "env")
            self.assertEqual(spec["env"][CLOSEOUT_PROPOSAL_DIR_ENV_KEY], closeout_dir)

    def test_apply_local_submission_context_inherits_proposal_from_same_session_workdir_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = str((config.app_home / "history-plan.md").resolve())
            closeout_dir = str((config.app_home / "closeout_proposal").resolve())
            submit_spec(
                config,
                {
                    "task_id": "existing-proposal-task-20260329",
                    "task_key": "existing-proposal-task",
                    "workdir": str(config.app_home),
                    "command": "python train.py",
                    "codex_session_id": "session-1",
                    "agent_name": "planner",
                    "feedback_mode": "auto",
                    "proposal_path": proposal_path,
                    "proposal_source": "explicit",
                    "proposal_owner": True,
                    "closeout_proposal_dir": closeout_dir,
                    "closeout_proposal_dir_source": "explicit",
                },
                hold=True,
            )

            resolved = apply_local_submission_context(
                config,
                {
                    "task_id": "new-proposal-task-20260329",
                    "task_key": "new-proposal-task",
                    "workdir": str(config.app_home),
                    "command": "python eval.py",
                    "codex_session_id": "session-1",
                    "agent_name": "planner",
                    "feedback_mode": "auto",
                },
                environ={},
            )

            self.assertEqual(resolved["proposal_path"], proposal_path)
            self.assertEqual(resolved["proposal_source"], "history")
            self.assertTrue(resolved["proposal_owner"])
            self.assertEqual(resolved["closeout_proposal_dir"], closeout_dir)
            self.assertEqual(resolved["closeout_proposal_dir_source"], "history")

    def test_apply_local_submission_context_explicit_clear_disables_proposal_inheritance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = str((config.app_home / "history-plan.md").resolve())
            closeout_dir = str((config.app_home / "closeout_proposal").resolve())
            submit_spec(
                config,
                {
                    "task_id": "existing-proposal-task-20260329",
                    "task_key": "existing-proposal-task",
                    "workdir": str(config.app_home),
                    "command": "python train.py",
                    "codex_session_id": "session-1",
                    "agent_name": "planner",
                    "feedback_mode": "auto",
                    "proposal_path": proposal_path,
                    "proposal_source": "explicit",
                    "proposal_owner": True,
                    "closeout_proposal_dir": closeout_dir,
                    "closeout_proposal_dir_source": "explicit",
                },
                hold=True,
            )

            resolved = apply_local_submission_context(
                config,
                {
                    "task_id": "new-sidecar-task-20260329",
                    "task_key": "new-sidecar-task",
                    "workdir": str(config.app_home),
                    "command": "python eval.py",
                    "codex_session_id": "session-1",
                    "agent_name": "planner",
                    "feedback_mode": "auto",
                    "proposal": "",
                },
                environ={PROPOSAL_SOURCE_ENV_KEY: "explicit_clear"},
            )
            submit_spec(config, resolved, hold=True)

            spec = json.loads((config.tasks_root / "new-sidecar-task-20260329" / "spec.json").read_text(encoding="utf-8"))
            self.assertEqual(spec["proposal_path"], "")
            self.assertEqual(spec["proposal_source"], "explicit_clear")
            self.assertFalse(spec["proposal_owner"])
            self.assertEqual(spec["env"][PROPOSAL_ENV_KEY], "")
            self.assertEqual(spec["env"][PROPOSAL_SOURCE_ENV_KEY], "explicit_clear")
            self.assertEqual(spec["closeout_proposal_dir"], closeout_dir)
            self.assertEqual(spec["closeout_proposal_dir_source"], "history")
            self.assertEqual(spec["env"][CLOSEOUT_PROPOSAL_DIR_ENV_KEY], closeout_dir)

    def test_submit_spec_auto_without_session_still_errors_when_env_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))

            with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(
                ValueError,
                "Missing required field: codex_session_id when feedback_mode=auto",
            ):
                submit_spec(
                    config,
                    {
                        "task_id": "agentless-missing-session-20260327",
                        "task_key": "agentless-missing-session",
                        "workdir": str(config.app_home),
                        "command": "python eval.py",
                        "feedback_mode": "auto",
                    },
                    hold=True,
                )

    def test_submit_spec_rejects_cross_session_lineage_rebind_for_agentless_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "existing-lineage-task",
                task_key="shared-lineage",
                status="completed",
                workdir=str(config.app_home),
                codex_session_id="session-a",
                agent_name="Codex",
            )

            with self.assertRaisesRegex(ValueError, "Session binding conflict"):
                submit_spec(
                    config,
                    {
                        "task_id": "agentless-rebind-20260327",
                        "task_key": "shared-lineage",
                        "workdir": str(config.app_home),
                        "command": "python eval.py",
                        "codex_session_id": "session-b",
                        "feedback_mode": "auto",
                    },
                    hold=True,
                )

    def test_submit_spec_allows_cross_session_lineage_rebind_when_explicitly_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "existing-lineage-task",
                task_key="shared-lineage",
                status="completed",
                workdir=str(config.app_home),
                codex_session_id="session-a",
                agent_name="Codex",
            )

            result = submit_spec(
                config,
                {
                    "task_id": "agentless-rebind-20260327",
                    "task_key": "shared-lineage",
                    "workdir": str(config.app_home),
                    "command": "python eval.py",
                    "codex_session_id": "session-b",
                    "feedback_mode": "auto",
                    "allow_session_rebind": True,
                },
                hold=True,
            )

            self.assertEqual(result["status"], "queued")
            spec = json.loads((config.tasks_root / "agentless-rebind-20260327" / "spec.json").read_text(encoding="utf-8"))
            self.assertTrue(spec["allow_session_rebind"])

    def test_submit_spec_rejects_duplicate_submit_without_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = str((config.app_home / "proposal.md").resolve())
            write_state(
                config,
                "existing-duplicate-task",
                task_key="existing-duplicate-task",
                status="running",
                workdir=str(config.app_home),
                codex_session_id="session-a",
                proposal_path=proposal_path,
                command="python eval.py --variant smoke",
                tmux_session_name="ctb-existing-dup",
            )

            with self.assertRaisesRegex(ValueError, "Duplicate submit guard"):
                submit_spec(
                    config,
                    {
                        "task_id": "duplicate-submit-20260415",
                        "task_key": "duplicate-submit-20260415",
                        "workdir": str(config.app_home),
                        "command": "python eval.py --variant smoke",
                        "codex_session_id": "session-a",
                        "proposal_path": proposal_path,
                        "feedback_mode": "auto",
                    },
                    hold=True,
                )

    def test_submit_spec_allows_duplicate_submit_with_explicit_override_and_records_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            proposal_path = str((config.app_home / "proposal.md").resolve())
            write_state(
                config,
                "existing-duplicate-task",
                task_key="existing-duplicate-task",
                status="queued",
                workdir=str(config.app_home),
                codex_session_id="session-a",
                proposal_path=proposal_path,
                command="python eval.py --variant smoke",
                tmux_session_name="ctb-existing-dup",
            )

            result = submit_spec(
                config,
                {
                    "task_id": "duplicate-submit-override-20260415",
                    "task_key": "duplicate-submit-override-20260415",
                    "workdir": str(config.app_home),
                    "command": "python eval.py --variant smoke",
                    "codex_session_id": "session-a",
                    "proposal_path": proposal_path,
                    "feedback_mode": "auto",
                    "allow_duplicate_submit": True,
                },
                hold=True,
            )

            self.assertEqual(result["status"], "queued")
            self.assertTrue(result["allow_duplicate_submit"])
            self.assertIn("Duplicate submit guard", result["duplicate_submit_warning"])
            self.assertEqual(len(result["duplicate_submit_matches"]), 1)
            spec = json.loads((config.tasks_root / "duplicate-submit-override-20260415" / "spec.json").read_text(encoding="utf-8"))
            self.assertTrue(spec["allow_duplicate_submit"])
            self.assertEqual(len(spec["duplicate_submit_matches"]), 1)
            payload = build_task_result_payload(config, "duplicate-submit-override-20260415")
            self.assertTrue(payload["allow_duplicate_submit"])
            self.assertEqual(len(payload["duplicate_submit_matches"]), 1)
            self.assertIn("Duplicate submit guard", payload["duplicate_submit_warning"])

    def test_submit_job_payload_reads_allow_duplicate_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            spec, hold = build_spec_from_submit_job_payload(
                config,
                {
                    "task_id": "payload-duplicate-20260415",
                    "workdir": str(config.app_home),
                    "command": "python eval.py",
                    "allow_duplicate_submit": True,
                    "hold": True,
                },
            )

            self.assertTrue(spec["allow_duplicate_submit"])
            self.assertTrue(hold)

    def test_current_thread_reports_env_bound_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_thread_row(
                config,
                "thread-env-123",
                model_provider="openai",
                source="vscode",
                archived=0,
                updated_at=1710812345,
                title="Restore codex-taskboard",
                cwd="/home/Awei",
                first_user_message="restore taskboard",
            )
            args = Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                json=True,
            )
            stdout = io.StringIO()
            with patch("codex_taskboard.cli.build_config", return_value=config), patch.dict(
                "os.environ",
                {"CODEX_THREAD_ID": "thread-env-123"},
                clear=False,
            ), patch("sys.stdout", stdout):
                rc = command_current_thread(args)

            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["current_codex_session_id"], "thread-env-123")
            self.assertEqual(payload["resolved_from_env"], "CODEX_THREAD_ID")
            self.assertTrue(payload["thread_found"])
            self.assertEqual(payload["title"], "Restore codex-taskboard")

    def test_parse_timestamp_without_timezone_assumes_beijing(self) -> None:
        self.assertEqual(
            parse_timestamp_to_unix("2026-03-17T03:29:44"),
            parse_timestamp_to_unix("2026-03-16T19:29:44Z"),
        )

    def test_load_task_state_normalizes_legacy_utc_timestamp_to_beijing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "legacy-utc-task",
                submitted_at="2026-03-16T19:29:44Z",
                updated_at="2026-03-16T19:29:45Z",
            )

            state = load_task_state(config, "legacy-utc-task")

            self.assertEqual(state["submitted_at"], "2026-03-17T03:29:44+08:00")
            self.assertEqual(state["updated_at"], "2026-03-17T03:29:45+08:00")

    def test_submit_spec_immediate_start_assigns_full_cpu_headroom_for_adaptive_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            start_calls: list[tuple[str, int | None]] = []

            with patch("codex_taskboard.cli.detect_default_cpu_thread_limit", return_value=40), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch(
                "codex_taskboard.cli.detect_gpu_count",
                return_value=0,
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, assigned_cpu_threads=None, **_kwargs: start_calls.append((task_id, assigned_cpu_threads)) or load_task_state(config, task_id),
            ):
                submit_spec(
                    config,
                    {
                        "task_id": "cpu-adaptive-start-20260318",
                        "task_key": "cpu-adaptive-start",
                        "workdir": str(config.app_home),
                        "command": "python summarize.py",
                        "codex_session_id": "session-1",
                        "cpu_threads_mode": "adaptive",
                        "cpu_threads_min": 8,
                    },
                    hold=False,
                )

            self.assertEqual(start_calls, [("cpu-adaptive-start-20260318", 40)])

    def test_dispatch_gpu_fill_assigns_adaptive_cpu_threads_without_starving_later_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            for idx, task_id in enumerate(["cpu-a", "cpu-b", "cpu-c"]):
                write_state(
                    config,
                    task_id,
                    task_key=task_id,
                    status="queued",
                    submitted_at=f"2026-03-16T00:01:0{idx}Z",
                    gpu_slots=0,
                    cpu_threads=8,
                    cpu_threads_min=8,
                    cpu_threads_mode="adaptive",
                )
                write_spec(
                    config,
                    task_id,
                    gpu_slots=0,
                    cpu_threads=8,
                    cpu_threads_min=8,
                    cpu_threads_mode="adaptive",
                    command=f"python {task_id}.py",
                )

            start_calls: list[tuple[str, int | None]] = []
            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=0), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, assigned_cpu_threads=None, **_kwargs: start_calls.append((task_id, assigned_cpu_threads)),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="gpu-fill",
                    max_running=0,
                    limit=100,
                    gpu_count_override=4,
                    cpu_thread_limit=40,
                )

            self.assertEqual(start_calls, [("cpu-a", 24), ("cpu-b", 8), ("cpu-c", 8)])
            self.assertEqual(result["cpu_assignments"], {"cpu-a": 24, "cpu-b": 8, "cpu-c": 8})

    def test_dispatch_gpu_fill_ignores_user_cpu_threads_max_before_any_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "cpu-a",
                task_key="cpu-a",
                status="queued",
                submitted_at="2026-03-16T00:01:00Z",
                gpu_slots=0,
                cpu_threads=8,
                cpu_threads_min=8,
                cpu_threads_max=12,
                cpu_threads_mode="adaptive",
            )
            write_state(
                config,
                "cpu-b",
                task_key="cpu-b",
                status="queued",
                submitted_at="2026-03-16T00:01:01Z",
                gpu_slots=0,
                cpu_threads=8,
                cpu_threads_min=8,
                cpu_threads_mode="adaptive",
            )
            write_spec(config, "cpu-a", gpu_slots=0, cpu_threads=8, cpu_threads_min=8, cpu_threads_max=12, cpu_threads_mode="adaptive", command="python cpu_a.py")
            write_spec(config, "cpu-b", gpu_slots=0, cpu_threads=8, cpu_threads_min=8, cpu_threads_mode="adaptive", command="python cpu_b.py")

            start_calls: list[tuple[str, int | None]] = []
            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=0), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, assigned_cpu_threads=None, **_kwargs: start_calls.append((task_id, assigned_cpu_threads)),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="gpu-fill",
                    max_running=0,
                    limit=100,
                    gpu_count_override=4,
                    cpu_thread_limit=40,
                )

            self.assertEqual(start_calls, [("cpu-a", 32), ("cpu-b", 8)])
            self.assertEqual(result["cpu_assignments"], {"cpu-a": 32, "cpu-b": 8})

    def test_dispatch_gpu_fill_respects_backoff_cap_after_cpu_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "cpu-a",
                task_key="cpu-a",
                status="queued",
                submitted_at="2026-03-16T00:01:00Z",
                gpu_slots=0,
                cpu_threads=8,
                cpu_threads_min=8,
                cpu_threads_max=16,
                cpu_threads_mode="adaptive",
                cpu_retry_attempts=1,
            )
            write_state(
                config,
                "cpu-b",
                task_key="cpu-b",
                status="queued",
                submitted_at="2026-03-16T00:01:01Z",
                gpu_slots=0,
                cpu_threads=8,
                cpu_threads_min=8,
                cpu_threads_mode="adaptive",
            )
            write_spec(
                config,
                "cpu-a",
                gpu_slots=0,
                cpu_threads=8,
                cpu_threads_min=8,
                cpu_threads_max=16,
                cpu_threads_mode="adaptive",
                cpu_retry_attempts=1,
                command="python cpu_a.py",
            )
            write_spec(config, "cpu-b", gpu_slots=0, cpu_threads=8, cpu_threads_min=8, cpu_threads_mode="adaptive", command="python cpu_b.py")

            start_calls: list[tuple[str, int | None]] = []
            with patch("codex_taskboard.cli.count_live_running_tasks", return_value=0), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch(
                "codex_taskboard.cli.start_existing_task",
                side_effect=lambda _config, task_id, assigned_cpu_threads=None, **_kwargs: start_calls.append((task_id, assigned_cpu_threads)),
            ):
                result = dispatch_queued_tasks(
                    config,
                    mode="gpu-fill",
                    max_running=0,
                    limit=100,
                    gpu_count_override=4,
                    cpu_thread_limit=40,
                )

            self.assertEqual(start_calls, [("cpu-a", 16), ("cpu-b", 24)])
            self.assertEqual(result["cpu_assignments"], {"cpu-a": 16, "cpu-b": 24})

    def test_start_existing_task_injects_cpu_thread_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            task_id = "cpu-launch"
            write_spec(
                config,
                task_id,
                gpu_slots=0,
                cpu_threads=12,
                command="python analyze.py",
                tmux_session_name="ctb-cpu-launch",
            )
            write_state(
                config,
                task_id,
                task_key=task_id,
                status="queued",
                workdir=str(config.app_home),
                command="python analyze.py",
                codex_session_id="session-1",
                tmux_session_name="ctb-cpu-launch",
                cpu_threads=12,
            )

            with patch("codex_taskboard.cli.tmux_session_exists", return_value=False), patch(
                "codex_taskboard.cli.run_subprocess",
                return_value=subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr=""),
            ):
                start_existing_task(config, task_id, why_started="dispatch_gpu-fill")

            updated_spec = json.loads((config.tasks_root / task_id / "spec.json").read_text(encoding="utf-8"))
            env = updated_spec.get("env", {})
            self.assertEqual(env.get("OMP_NUM_THREADS"), "12")
            self.assertEqual(env.get("MKL_NUM_THREADS"), "12")
            self.assertEqual(env.get("OPENBLAS_NUM_THREADS"), "12")
            self.assertEqual(env.get("NUMEXPR_NUM_THREADS"), "12")
            self.assertEqual(env.get("TORCH_NUM_THREADS"), "12")

    def test_submit_spec_keeps_task_queued_when_dependency_unsatisfied(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(config, "upstream-live-20260318", task_key="upstream-live", status="running")

            with patch("codex_taskboard.cli.start_existing_task", side_effect=AssertionError("should not start")):
                result = submit_spec(
                    config,
                    {
                        "task_id": "downstream-task-20260318",
                        "task_key": "downstream-task",
                        "workdir": str(config.app_home),
                        "command": "python analyze.py",
                        "codex_session_id": "session-1",
                        "depends_on": ["upstream-live"],
                    },
                    hold=False,
                )

            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["phase"], "blocked_by_dependency")
            self.assertIn("dependency:upstream-live", result["blocked_reason"])

    def test_submit_spec_keeps_task_queued_when_required_artifact_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))

            with patch("codex_taskboard.cli.start_existing_task", side_effect=AssertionError("should not start")):
                result = submit_spec(
                    config,
                    {
                        "task_id": "summary-task-20260318",
                        "task_key": "summary-task",
                        "workdir": str(config.app_home),
                        "command": "python summarize.py",
                        "codex_session_id": "session-1",
                        "required_artifact_globs": ["results/**/summary.json"],
                    },
                    hold=False,
                )

            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["phase"], "waiting_artifact")
            self.assertIn("artifact:results/**/summary.json", result["blocked_reason"])

    def test_submit_spec_keeps_task_queued_when_required_report_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "upstream-done-20260318",
                task_key="upstream-done",
                status="completed",
                structured_report={"winner": "alpha"},
            )

            with patch("codex_taskboard.cli.start_existing_task", side_effect=AssertionError("should not start")):
                result = submit_spec(
                    config,
                    {
                        "task_id": "consumer-task-20260318",
                        "task_key": "consumer-task",
                        "workdir": str(config.app_home),
                        "command": "python consume.py",
                        "codex_session_id": "session-1",
                        "depends_on": ["upstream-done"],
                        "required_report_conditions": ["winner=beta"],
                    },
                    hold=False,
                )

            self.assertEqual(result["status"], "queued")
            self.assertEqual(result["phase"], "waiting_report")
            self.assertIn("report:winner", result["blocked_reason"])

    def test_command_run_requeues_adaptive_cpu_task_after_thread_resource_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            task_id = "cpu-backoff-20260318"
            task_dir = config.tasks_root / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            spec = {
                "version": 1,
                "task_id": task_id,
                "task_key": task_id,
                "execution_mode": "shell",
                "workdir": str(config.app_home),
                "command": "python cpu_eval.py",
                "codex_session_id": "session-1",
                "cpu_threads": 8,
                "cpu_threads_min": 8,
                "cpu_threads_max": 32,
                "cpu_threads_mode": "adaptive",
                "assigned_cpu_threads": 32,
                "cpu_retry_attempts": 0,
                "cpu_retry_max_attempts": 3,
                "tmux_session_name": "ctb-cpu-backoff",
            }
            (task_dir / "spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
            write_state(
                config,
                task_id,
                task_key=task_id,
                status="submitted",
                workdir=str(config.app_home),
                command="python cpu_eval.py",
                codex_session_id="session-1",
                cpu_threads=32,
                cpu_threads_min=8,
                cpu_threads_max=32,
                cpu_threads_mode="adaptive",
                assigned_cpu_threads=32,
            )
            args = type(
                "Args",
                (),
                {
                    "app_home": str(config.app_home),
                    "codex_home": str(config.codex_home),
                    "codex_bin": "codex",
                    "tmux_bin": "tmux",
                    "spec_file": str(task_dir / "spec.json"),
                },
            )()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "subprocess.Popen",
                side_effect=RuntimeError("can't start new thread"),
            ), patch("codex_taskboard.cli.handle_task_feedback", side_effect=AssertionError("feedback should be suppressed")):
                rc = command_run(args)

            self.assertEqual(rc, 0)
            state = load_task_state(config, task_id)
            self.assertEqual(state["status"], "queued")
            self.assertEqual(state["cpu_retry_attempts"], 1)
            self.assertEqual(state["cpu_threads_max"], 16)
            self.assertEqual(state["assigned_cpu_threads"], 0)
            event_files = list((task_dir / "events").glob("*-launch_failed.json"))
            self.assertTrue(event_files)
            event_payload = json.loads(event_files[-1].read_text(encoding="utf-8"))
            self.assertTrue(event_payload["cpu_backoff_retry_scheduled"])
            self.assertEqual(event_payload["next_cpu_threads_max"], 16)

    def test_command_run_requeues_task_when_launch_recheck_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            task_id = "launch-race-20260318"
            task_dir = config.tasks_root / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            spec = {
                "version": 1,
                "task_id": task_id,
                "task_key": task_id,
                "execution_mode": "shell",
                "workdir": str(config.app_home),
                "command": "python train.py",
                "codex_session_id": "session-1",
                "assigned_gpus": [0, 1, 2, 3],
                "gpu_slots": 4,
                "gpu_min_free_mb": 16000,
                "gpu_max_util_percent": 70,
                "tmux_session_name": "ctb-launch-race",
            }
            (task_dir / "spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
            write_state(
                config,
                task_id,
                task_key=task_id,
                status="submitted",
                workdir=str(config.app_home),
                command="python train.py",
                codex_session_id="session-1",
                assigned_gpus=[0, 1, 2, 3],
            )
            args = type(
                "Args",
                (),
                {
                    "app_home": str(config.app_home),
                    "codex_home": str(config.codex_home),
                    "codex_bin": "codex",
                    "tmux_bin": "tmux",
                    "spec_file": str(task_dir / "spec.json"),
                },
            )()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "codex_taskboard.cli.shutil_which",
                return_value="nvidia-smi",
            ), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[
                    {"index": 0, "name": "GPU0", "memory_total_mb": 32607, "memory_used_mb": 20000, "gpu_util_percent": 95},
                    {"index": 1, "name": "GPU1", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                    {"index": 2, "name": "GPU2", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                    {"index": 3, "name": "GPU3", "memory_total_mb": 32607, "memory_used_mb": 20, "gpu_util_percent": 0},
                ],
            ), patch("subprocess.Popen", side_effect=AssertionError("child should not launch")):
                rc = command_run(args)

            self.assertEqual(rc, 0)
            state = load_task_state(config, task_id)
            self.assertEqual(state["status"], "queued")
            self.assertIn("launch_recheck_failed:gpu0", state["rejected_reason"])
            self.assertTrue((task_dir / "events").exists())
            self.assertTrue(list((task_dir / "events").glob("*-launch_deferred.json")))

    def test_command_status_running_task_hides_dispatch_blocker_after_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            task_id = "running-consistency-20260411"
            write_spec(
                config,
                task_id,
                gpu_slots=4,
                assigned_gpus=[0, 1, 2, 3],
                gpu_min_free_mb=16000,
                gpu_max_util_percent=70,
            )
            write_state(
                config,
                task_id,
                status="running",
                task_key="running-consistency",
                codex_session_id="session-1",
                tmux_session_name="ctb-running-consistency",
                assigned_gpus=[0, 1, 2, 3],
                gpu_slots=4,
                started_at="2026-04-11T00:00:00Z",
                started_via_tmux_at="2026-04-11T00:00:00Z",
            )
            args = Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                task_id=task_id,
                json=True,
                limit=30,
            )
            stdout = io.StringIO()
            with patch("codex_taskboard.cli.build_config", return_value=config), patch("sys.stdout", stdout), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[
                    {"index": 0, "name": "GPU0", "memory_total_mb": 32607, "memory_used_mb": 30000, "gpu_util_percent": 98},
                    {"index": 1, "name": "GPU1", "memory_total_mb": 32607, "memory_used_mb": 30000, "gpu_util_percent": 98},
                    {"index": 2, "name": "GPU2", "memory_total_mb": 32607, "memory_used_mb": 30000, "gpu_util_percent": 98},
                    {"index": 3, "name": "GPU3", "memory_total_mb": 32607, "memory_used_mb": 30000, "gpu_util_percent": 98},
                ],
            ), patch("codex_taskboard.cli.detect_gpu_count", return_value=4), patch(
                "codex_taskboard.cli.tmux_session_exists",
                return_value=True,
            ):
                rc = command_status(args)

            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "running")
            self.assertEqual(payload["lifecycle_state"], "running")
            self.assertEqual(payload["runtime_state"], "child_live")
            self.assertEqual(payload["blocked_reason"], "")
            self.assertEqual(payload["gpu_block_reason"], "")
            self.assertEqual(payload["eligible_gpu_ids"], [0, 1, 2, 3])
            self.assertEqual(payload["dispatch_diagnostics"]["scheduler_state"], "historical_after_launch")
            self.assertEqual(payload["dispatch_diagnostics"]["blocked_reason"], "")
            self.assertEqual(payload["automation_recommendation"], "wait_for_live_task")

    def test_build_remote_ssh_command_embeds_remote_env_and_workdir(self) -> None:
        command = build_remote_ssh_command(
            {
                "task_id": "docker-job-20260319",
                "command": "python train.py --epochs 1",
                "remote_workdir": "/home/ly1/project",
                "env": {"CUDA_VISIBLE_DEVICES": "0", "OMP_NUM_THREADS": "16"},
                "executor_target": "ly1@127.0.0.1",
                "executor_identity_file": "/tmp/fake-key",
                "executor_ssh_options": ["-o", "BatchMode=yes"],
            }
        )
        joined = " ".join(command)
        self.assertEqual(command[:3], ["ssh", "-i", "/tmp/fake-key"])
        self.assertIn("ly1@127.0.0.1", command)
        self.assertIn("cd /home/ly1/project", joined)
        self.assertIn("export CUDA_VISIBLE_DEVICES=0", joined)
        self.assertIn("python train.py --epochs 1", joined)

    def test_command_run_uses_ssh_for_remote_executor_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            task_id = "remote-job-20260319"
            task_dir = config.tasks_root / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            spec = {
                "version": 1,
                "task_id": task_id,
                "task_key": task_id,
                "execution_mode": "ssh_shell",
                "workdir": str(config.app_home),
                "remote_workdir": "/home/ly1/project",
                "command": "python train.py",
                "codex_session_id": "",
                "feedback_mode": "off",
                "env": {"CUDA_VISIBLE_DEVICES": "0", "OMP_NUM_THREADS": "12"},
                "executor_name": "ly1-rootless",
                "executor_target": "ly1@127.0.0.1",
                "executor_identity_file": "/tmp/fake-key",
                "executor_ssh_options": ["-o", "BatchMode=yes"],
                "tmux_session_name": "ctb-remote-job",
            }
            (task_dir / "spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
            write_state(
                config,
                task_id,
                task_key=task_id,
                status="submitted",
                workdir=str(config.app_home),
                remote_workdir="/home/ly1/project",
                command="python train.py",
                codex_session_id="",
                feedback_mode="off",
                executor_name="ly1-rootless",
            )
            args = type(
                "Args",
                (),
                {
                    "app_home": str(config.app_home),
                    "codex_home": str(config.codex_home),
                    "codex_bin": "codex",
                    "tmux_bin": "tmux",
                    "spec_file": str(task_dir / "spec.json"),
                },
            )()

            popen_calls: list[list[str]] = []

            class FakeProcess:
                pid = 23456

                def wait(self) -> int:
                    return 0

            def fake_popen(command, **kwargs):
                popen_calls.append(command)
                return FakeProcess()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "subprocess.Popen",
                side_effect=fake_popen,
            ), patch("codex_taskboard.cli.handle_task_feedback", return_value={"ok": False, "skipped": True}):
                rc = command_run(args)

            self.assertEqual(rc, 0)
            self.assertTrue(popen_calls)
            self.assertEqual(popen_calls[0][0], "ssh")
            self.assertIn("ly1@127.0.0.1", popen_calls[0])

    def test_command_submit_job_and_wait_result_for_agentless_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            submit_args = Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                task_id="agentless-submit-20260319",
                task_key="agentless-submit",
                workdir=str(config.app_home),
                command="python eval.py",
                executor="",
                codex_session_id="",
                agent_name="docker-user",
                priority=0,
                gpu_slots=None,
                assigned_gpus=[],
                cpu_threads=0,
                cpu_threads_min=0,
                cpu_threads_max=0,
                cpu_threads_mode="",
                gpu_min_free_mb=0,
                gpu_max_util_percent=0,
                feedback_mode="off",
                depends_on=[],
                required_artifact_glob=[],
                required_report=[],
                report_format="key-value",
                report_key=[],
                report_contract="",
                success_prompt=None,
                success_prompt_file=None,
                failure_prompt=None,
                failure_prompt_file=None,
                task_note="",
                artifact_glob=[],
                env=[],
                codex_exec_mode="dangerous",
                resume_timeout_seconds=7200,
                launch_grace_seconds=0,
                prompt_max_chars=12000,
                log_tail_lines=80,
                log_tail_chars=5000,
                artifact_max_chars=1200,
                artifact_max_lines=40,
                startup_failure_threshold_seconds=90,
                fallback_provider="",
                no_replace_existing=False,
                hold=True,
            )
            stdout = io.StringIO()
            with patch("codex_taskboard.cli.build_config", return_value=config), patch("sys.stdout", stdout):
                rc = command_submit_job(submit_args)
            self.assertEqual(rc, 0)
            submit_payload = json.loads(stdout.getvalue())
            self.assertEqual(submit_payload["task_id"], "agentless-submit-20260319")
            self.assertEqual(submit_payload["status"], "queued")

            write_state(
                config,
                "agentless-submit-20260319",
                task_key="agentless-submit",
                status="completed",
                submitted_at="2026-03-19T00:00:00Z",
                ended_at="2026-03-19T00:01:00Z",
                exit_code=0,
                report_summary="score=0.91",
                structured_report={"score": "0.91"},
            )

            status_args = Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                task_id="agentless-submit-20260319",
            )
            stdout = io.StringIO()
            with patch("codex_taskboard.cli.build_config", return_value=config), patch("sys.stdout", stdout), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch("codex_taskboard.cli.detect_gpu_count", return_value=0):
                rc = command_status_result(status_args)
            self.assertEqual(rc, 0)
            status_payload = json.loads(stdout.getvalue())
            self.assertTrue(status_payload["result_ready"])
            self.assertEqual(status_payload["structured_report"]["score"], "0.91")

            wait_args = Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                task_id="agentless-submit-20260319",
                timeout_seconds=1.0,
                poll_seconds=0.1,
                expect_status="completed",
            )
            stdout = io.StringIO()
            with patch("codex_taskboard.cli.build_config", return_value=config), patch("sys.stdout", stdout), patch(
                "codex_taskboard.cli.get_gpu_summary_table",
                return_value=[],
            ), patch("codex_taskboard.cli.detect_gpu_count", return_value=0):
                rc = command_wait_result(wait_args)
            self.assertEqual(rc, 0)
            wait_payload = json.loads(stdout.getvalue())
            self.assertEqual(wait_payload["status"], "completed")

    def test_command_bind_before_launch_creates_cpu_only_bound_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            bind_args = Namespace(
                app_home=str(config.app_home),
                codex_home=str(config.codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                task_id="bind-local-20260408",
                task_key="bind-local",
                workdir=str(config.app_home),
                command="python audit.py",
                codex_session_id="session-bind-001",
                agent_name="binder-agent",
                proposal=None,
                closeout_proposal_dir=None,
                project_history_file=None,
                no_inherit_proposal=False,
                priority=0,
                cpu_profile="auto",
                cpu_threads=0,
                cpu_threads_min=0,
                cpu_threads_max=0,
                cpu_threads_mode="",
                cpu_workers=0,
                cpu_workers_min=0,
                cpu_workers_max=0,
                feedback_mode="auto",
                depends_on=[],
                required_artifact_glob=[],
                required_report=[],
                report_format="auto",
                report_key=[],
                report_contract="",
                success_prompt=None,
                success_prompt_file=None,
                failure_prompt=None,
                failure_prompt_file=None,
                task_note="",
                artifact_glob=[],
                env=[],
                codex_exec_mode="dangerous",
                resume_timeout_seconds=7200,
                launch_grace_seconds=0,
                prompt_max_chars=12000,
                log_tail_lines=80,
                log_tail_chars=5000,
                artifact_max_chars=1200,
                artifact_max_lines=40,
                startup_failure_threshold_seconds=90,
                fallback_provider="",
                allow_session_rebind=False,
                no_replace_existing=False,
                hold=True,
            )
            stdout = io.StringIO()
            with patch("codex_taskboard.cli.build_config", return_value=config), patch("sys.stdout", stdout):
                rc = command_bind_before_launch(bind_args)

            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["task_id"], "bind-local-20260408")
            self.assertEqual(payload["status"], "queued")

            spec = load_task_spec(config, "bind-local-20260408")
            assert spec is not None
            self.assertEqual(spec["gpu_slots"], 0)
            self.assertEqual(spec["assigned_gpus"], [])
            self.assertEqual(spec["feedback_mode"], "auto")
            self.assertEqual(spec["env"]["CODEX_TASKBOARD_BIND_BEFORE_LAUNCH"], "1")
            self.assertIn("bind_before_launch", spec["task_note"])

    def test_training_matcher_covers_continuous_probe_style_commands(self) -> None:
        self.assertTrue(
            looks_like_training_command(
                "/home/Awei/LLM/passage/scripts/continuous_reasoning_sweep.py --model-dir /tmp/model"
            )
        )
        self.assertTrue(
            looks_like_training_command(
                "/home/Awei/LLM/passage/scripts/continuous_speculative_probe.py --dataset-path /tmp/data.jsonl"
            )
        )

    def test_cleanup_keeps_running_task_when_runner_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            write_state(
                config,
                "runner-alive",
                task_key="runner-alive",
                status="running",
                tmux_session_name="ctb-runner-alive",
                pid=12345,
                workdir=str(Path(tmpdir)),
            )
            args = type(
                "Args",
                (),
                {
                    "app_home": str(config.app_home),
                    "codex_home": str(config.codex_home),
                    "codex_bin": "codex",
                    "tmux_bin": "tmux",
                    "task_id": "runner-alive",
                    "kill_if_running": False,
                    "include_nonterminal": True,
                },
            )()

            with patch("codex_taskboard.cli.build_config", return_value=config), patch(
                "codex_taskboard.cli.tmux_session_exists", return_value=False
            ), patch(
                "codex_taskboard.cli.pid_exists",
                return_value=True,
            ), patch(
                "codex_taskboard.cli.read_pid_cmdline",
                return_value=f"python3 -m codex_taskboard.cli run --spec-file {config.tasks_root / 'runner-alive' / 'spec.json'}",
            ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = command_cleanup(args)

            self.assertEqual(rc, 1)
            self.assertIn("runner_alive", stdout.getvalue())
            self.assertTrue((config.tasks_root / "runner-alive").exists())


if __name__ == "__main__":
    unittest.main()
