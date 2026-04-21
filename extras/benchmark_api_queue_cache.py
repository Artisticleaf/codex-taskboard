#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import AppConfig, build_task_list_payload_for_api, write_task_state
from codex_taskboard.task_index import clear_task_index_cache


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


def prepare_tasks(config: AppConfig, *, task_count: int) -> None:
    for index in range(task_count):
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


def measure_scenario(
    config: AppConfig,
    *,
    queue_calls: int,
    limit: int,
    drop_index_file: bool,
) -> dict[str, object]:
    clear_task_index_cache(config.app_home)
    index_path = config.app_home / "task-index.json"
    if drop_index_file and index_path.exists():
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

    start_ts = time.perf_counter()
    with (
        patch("codex_taskboard.cli.get_gpu_summary_table", return_value=[]),
        patch("codex_taskboard.cli.detect_gpu_count", return_value=0),
        patch("codex_taskboard.task_index.refresh_task_index", wraps=task_index_module.refresh_task_index) as refresh_mock,
        patch("codex_taskboard.cli.load_task_spec", wraps=cli_module.load_task_spec) as load_task_spec_mock,
    ):
        first_visible_tasks = 0
        for _call_index in range(queue_calls):
            payload = build_task_list_payload_for_api(
                config,
                token_record,
                status_filter="queued",
                limit=limit,
                view="queue",
            )
            first_visible_tasks = int(payload["summary"]["visible_tasks"])
    elapsed_seconds = time.perf_counter() - start_ts
    refresh_calls = refresh_mock.call_count
    return {
        "drop_index_file": drop_index_file,
        "queue_calls": queue_calls,
        "queue_limit": limit,
        "visible_tasks": first_visible_tasks,
        "refresh_calls": refresh_calls,
        "refresh_calls_per_queue_call": refresh_calls / max(1, queue_calls),
        "index_hit_rate": (queue_calls - refresh_calls) / max(1, queue_calls),
        "load_task_spec_calls": load_task_spec_mock.call_count,
        "load_task_spec_calls_per_queue_call": load_task_spec_mock.call_count / max(1, queue_calls),
        "elapsed_seconds": elapsed_seconds,
        "avg_queue_call_ms": (elapsed_seconds / max(1, queue_calls)) * 1000.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark /queue task-index cache behavior.")
    parser.add_argument("--task-count", type=int, default=1200)
    parser.add_argument("--queue-calls", type=int, default=12)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmpdir:
        config = build_config(Path(tmpdir))
        prepare_tasks(config, task_count=max(1, int(args.task_count)))
        warm_metrics = measure_scenario(
            config,
            queue_calls=max(1, int(args.queue_calls)),
            limit=max(1, int(args.limit)),
            drop_index_file=False,
        )
        cold_metrics = measure_scenario(
            config,
            queue_calls=max(1, int(args.queue_calls)),
            limit=max(1, int(args.limit)),
            drop_index_file=True,
        )

    print(
        json.dumps(
            {
                "task_count": max(1, int(args.task_count)),
                "queue_calls": max(1, int(args.queue_calls)),
                "queue_limit": max(1, int(args.limit)),
                "warm_existing_index": warm_metrics,
                "cold_missing_index": cold_metrics,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
