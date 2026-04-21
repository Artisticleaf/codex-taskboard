import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import AppConfig, build_task_list_payload_for_api, write_task_state
from codex_taskboard.task_index import clear_task_index_cache


TASK_COUNT = 1200
QUEUE_CALLS = 12
QUEUE_LIMIT = 30


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
        "codex_session_id": "",
        "priority": 0,
        "gpu_slots": 0,
        **fields,
    }
    (task_dir / "spec.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_state(config: AppConfig, task_id: str, **fields: object) -> None:
    payload = {
        "version": 1,
        "task_id": task_id,
        "task_key": fields.get("task_key", task_id),
        "status": "queued",
        "submitted_at": "2026-03-19T00:00:00Z",
        "updated_at": "2026-03-19T00:00:01Z",
        "priority": 0,
        **fields,
    }
    write_task_state(config, task_id, payload)


class ApiQueueBenchmarkTests(unittest.TestCase):
    def test_queue_cache_benchmark_counts_for_1k_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            for index in range(TASK_COUNT):
                task_id = f"docker-a.task-{index:04d}"
                write_spec(
                    config,
                    task_id,
                    client_task_id=f"task-{index:04d}",
                    owner_tenant="docker-a",
                    owner_role="user",
                    owner_label="docker:a",
                )
                write_state(
                    config,
                    task_id,
                    client_task_id=f"task-{index:04d}",
                    owner_tenant="docker-a",
                    owner_role="user",
                    owner_label="docker:a",
                    submitted_via_api=True,
                    submitted_at=f"2026-03-19T00:00:{index % 60:02d}Z",
                    updated_at=f"2026-03-19T00:01:{index % 60:02d}Z",
                )

            clear_task_index_cache(config.app_home)
            index_path = config.app_home / "task-index.json"
            if index_path.exists():
                index_path.unlink()

            token_record = {
                "tenant": "docker-a",
                "executor": "",
                "role": "user",
                "allow_read_results": True,
                "allow_read_global_queue": True,
            }

            from codex_taskboard import cli as cli_module
            from codex_taskboard import task_index as task_index_module

            with (
                patch("codex_taskboard.cli.get_gpu_summary_table", return_value=[]),
                patch("codex_taskboard.cli.detect_gpu_count", return_value=0),
                patch("codex_taskboard.task_index.refresh_task_index", wraps=task_index_module.refresh_task_index) as refresh_mock,
                patch("codex_taskboard.cli.load_task_spec", wraps=cli_module.load_task_spec) as load_task_spec_mock,
            ):
                for call_index in range(QUEUE_CALLS):
                    payload = build_task_list_payload_for_api(
                        config,
                        token_record,
                        status_filter="queued",
                        limit=QUEUE_LIMIT,
                        view="queue",
                    )
                    self.assertEqual(payload["summary"]["visible_tasks"], TASK_COUNT)
                    self.assertEqual(len(payload["tasks"]), QUEUE_LIMIT)
                    self.assertEqual(payload["summary"]["returned_tasks"], QUEUE_LIMIT)
                    self.assertEqual(payload["summary"]["queued_tasks"], TASK_COUNT)
                    self.assertEqual(payload["tasks"][0]["queue_position_visible"], 1)

            metrics = {
                "task_count": TASK_COUNT,
                "queue_calls": QUEUE_CALLS,
                "queue_limit": QUEUE_LIMIT,
                "refresh_calls": refresh_mock.call_count,
                "refresh_calls_per_queue_call": refresh_mock.call_count / QUEUE_CALLS,
                "index_hit_rate": (QUEUE_CALLS - refresh_mock.call_count) / QUEUE_CALLS,
                "load_task_spec_calls": load_task_spec_mock.call_count,
                "load_task_spec_calls_per_queue_call": load_task_spec_mock.call_count / QUEUE_CALLS,
            }
            print(json.dumps(metrics, ensure_ascii=False, indent=2))

            self.assertEqual(refresh_mock.call_count, 1)
            self.assertAlmostEqual(metrics["index_hit_rate"], (QUEUE_CALLS - 1) / QUEUE_CALLS)
            self.assertEqual(load_task_spec_mock.call_count, QUEUE_CALLS * QUEUE_LIMIT)
