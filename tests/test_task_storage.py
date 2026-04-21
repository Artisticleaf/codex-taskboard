from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codex_taskboard.task_storage import (
    TaskStorageHooks,
    iter_all_task_states,
    iter_task_states,
    load_task_spec,
    load_task_state,
    resolve_event_path,
    task_paths,
    write_task_spec,
    write_task_state,
)


@dataclass(frozen=True)
class DummyConfig:
    app_home: Path
    tasks_root: Path
    legacy_task_roots: tuple[Path, ...] = tuple()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_task_spec_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("task_id", "")
    normalized.setdefault("task_key", normalized.get("task_id", ""))
    return normalized


def normalize_task_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("task_id", "")
    normalized.setdefault("task_key", normalized.get("task_id", ""))
    normalized.setdefault("status", "queued")
    return normalized


def make_hooks() -> TaskStorageHooks:
    return TaskStorageHooks(
        all_task_roots=lambda config: (config.tasks_root, *config.legacy_task_roots),
        find_task_dir=lambda config, task_id: next(
            (root / task_id for root in (config.tasks_root, *config.legacy_task_roots) if (root / task_id).exists()),
            None,
        ),
        ensure_dir=lambda path: path.mkdir(parents=True, exist_ok=True),
        read_json=read_json,
        atomic_write_json=atomic_write_json,
        normalize_task_spec_payload=normalize_task_spec_payload,
        normalize_task_state_payload=normalize_task_state_payload,
        normalize_timestamp_fields=lambda payload: dict(payload),
        reconcile_active_task_state=lambda _config, payload: dict(payload),
        is_hidden_status=lambda status: status == "superseded",
        task_list_sort_key=lambda item: (str(item.get("status", "")), str(item.get("task_id", ""))),
        update_task_index_entry=lambda *args, **kwargs: None,
    )


def test_task_storage_round_trip_and_paths(tmp_path: Path) -> None:
    config = DummyConfig(app_home=tmp_path, tasks_root=tmp_path / "tasks")
    hooks = make_hooks()

    write_task_spec(config, "demo-task", {"task_id": "demo-task", "command": "python demo.py"}, hooks=hooks)
    write_task_state(config, "demo-task", {"task_id": "demo-task", "status": "running"}, hooks=hooks)

    spec = load_task_spec(config, "demo-task", hooks=hooks)
    state = load_task_state(config, "demo-task", hooks=hooks)
    paths = task_paths(config, "demo-task", hooks=hooks)

    assert spec["command"] == "python demo.py"
    assert state["status"] == "running"
    assert paths["spec_path"].endswith("demo-task/spec.json")
    assert paths["state_path"].endswith("demo-task/state.json")


def test_iter_task_states_filters_hidden_and_resolves_event_path(tmp_path: Path) -> None:
    config = DummyConfig(app_home=tmp_path, tasks_root=tmp_path / "tasks")
    hooks = make_hooks()

    write_task_state(config, "visible-task", {"task_id": "visible-task", "status": "completed"}, hooks=hooks)
    write_task_state(config, "hidden-task", {"task_id": "hidden-task", "status": "superseded"}, hooks=hooks)

    event_path = config.tasks_root / "visible-task" / "events" / "latest.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text(json.dumps({"status": "completed"}) + "\n", encoding="utf-8")
    write_task_state(
        config,
        "visible-task",
        {"task_id": "visible-task", "status": "completed", "last_event_path": str(event_path)},
        hooks=hooks,
    )

    visible = iter_task_states(config, hooks=hooks)
    all_states = iter_all_task_states(config, hooks=hooks)

    assert [item["task_id"] for item in visible] == ["visible-task"]
    assert sorted(item["task_id"] for item in all_states) == ["hidden-task", "visible-task"]
    assert resolve_event_path(config, "visible-task", hooks=hooks) == event_path.resolve()
