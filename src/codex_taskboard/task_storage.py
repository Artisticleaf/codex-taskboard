from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class TaskStorageHooks:
    all_task_roots: Callable[[Any], tuple[Path, ...]]
    find_task_dir: Callable[[Any, str], Path | None]
    ensure_dir: Callable[[Path], None]
    read_json: Callable[[Path, Any], Any]
    atomic_write_json: Callable[[Path, Any], None]
    normalize_task_spec_payload: Callable[[dict[str, Any]], dict[str, Any]]
    normalize_task_state_payload: Callable[[dict[str, Any]], dict[str, Any]]
    normalize_timestamp_fields: Callable[[dict[str, Any]], dict[str, Any]]
    reconcile_active_task_state: Callable[[Any, dict[str, Any]], dict[str, Any]]
    is_hidden_status: Callable[[str], bool]
    task_list_sort_key: Callable[[dict[str, Any]], Any]
    update_task_index_entry: Callable[..., None]


def task_root(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> Path:
    existing = hooks.find_task_dir(config, task_id)
    if existing is not None:
        return existing
    return Path(config.tasks_root) / task_id


def task_spec_path(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> Path:
    return task_root(config, task_id, hooks=hooks) / "spec.json"


def task_state_path(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> Path:
    return task_root(config, task_id, hooks=hooks) / "state.json"


def task_command_log_path(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> Path:
    return task_root(config, task_id, hooks=hooks) / "command.log"


def task_runner_log_path(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> Path:
    return task_root(config, task_id, hooks=hooks) / "runner.log"


def task_last_message_path(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> Path:
    return task_root(config, task_id, hooks=hooks) / "codex-last-message.txt"


def subagent_last_message_path(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> Path:
    return task_root(config, task_id, hooks=hooks) / "subagent-last-message.txt"


def task_events_dir(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> Path:
    return task_root(config, task_id, hooks=hooks) / "events"


def task_paths(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> dict[str, str]:
    return {
        "task_root": str(task_root(config, task_id, hooks=hooks)),
        "spec_path": str(task_spec_path(config, task_id, hooks=hooks)),
        "state_path": str(task_state_path(config, task_id, hooks=hooks)),
        "command_log_path": str(task_command_log_path(config, task_id, hooks=hooks)),
        "runner_log_path": str(task_runner_log_path(config, task_id, hooks=hooks)),
        "last_message_path": str(task_last_message_path(config, task_id, hooks=hooks)),
        "events_dir": str(task_events_dir(config, task_id, hooks=hooks)),
    }


def task_paths_for_root(root: Path, task_id: str) -> dict[str, str]:
    return {
        "task_root": str(root / task_id),
        "spec_path": str(root / task_id / "spec.json"),
        "state_path": str(root / task_id / "state.json"),
        "command_log_path": str(root / task_id / "command.log"),
        "runner_log_path": str(root / task_id / "runner.log"),
        "last_message_path": str(root / task_id / "codex-last-message.txt"),
        "events_dir": str(root / task_id / "events"),
    }


def ensure_task_layout(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> None:
    hooks.ensure_dir(task_root(config, task_id, hooks=hooks))
    hooks.ensure_dir(task_events_dir(config, task_id, hooks=hooks))


def load_task_spec(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> dict[str, Any]:
    spec_path = task_spec_path(config, task_id, hooks=hooks)
    if not spec_path.exists():
        return {}
    spec = hooks.read_json(spec_path, {})
    if not isinstance(spec, dict):
        raise ValueError(f"Invalid spec file for task '{task_id}'.")
    if not spec:
        return {}
    return hooks.normalize_task_spec_payload(spec)


def load_task_state(config: Any, task_id: str, *, hooks: TaskStorageHooks) -> dict[str, Any]:
    state_path = task_state_path(config, task_id, hooks=hooks)
    if not state_path.exists():
        return {}
    state = hooks.read_json(state_path, {})
    if not isinstance(state, dict):
        return {}
    if not state:
        return {}
    return hooks.reconcile_active_task_state(config, hooks.normalize_task_state_payload(state))


def load_event(path: Path, *, hooks: TaskStorageHooks) -> dict[str, Any]:
    payload = hooks.read_json(path, {})
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid event file: {path}")
    return hooks.normalize_timestamp_fields(payload)


def resolve_event_path(
    config: Any,
    task_id: str,
    *,
    hooks: TaskStorageHooks,
    explicit_path: str | None = None,
) -> Path:
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Event file does not exist: {path}")
        return path
    state = load_task_state(config, task_id, hooks=hooks)
    last_event = str(state.get("last_event_path", "")).strip()
    if not last_event:
        raise ValueError(f"Task {task_id} has no recorded event yet.")
    path = Path(last_event).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"Recorded event file does not exist: {path}")
    return path


def write_task_state(config: Any, task_id: str, state: dict[str, Any], *, hooks: TaskStorageHooks) -> None:
    path = task_state_path(config, task_id, hooks=hooks)
    hooks.atomic_write_json(path, hooks.normalize_task_state_payload(state))
    hooks.update_task_index_entry(config.app_home, task_dir=path.parent, root_path=path.parent.parent)


def merge_task_state(config: Any, task_id_value: str, *, hooks: TaskStorageHooks, updated_at: str, **updates: Any) -> dict[str, Any]:
    state = load_task_state(config, task_id_value, hooks=hooks)
    state.update(updates)
    state["updated_at"] = updated_at
    write_task_state(config, task_id_value, state, hooks=hooks)
    return state


def write_task_spec(config: Any, task_id: str, spec: dict[str, Any], *, hooks: TaskStorageHooks) -> None:
    path = task_spec_path(config, task_id, hooks=hooks)
    hooks.atomic_write_json(path, hooks.normalize_task_spec_payload(spec))
    hooks.update_task_index_entry(config.app_home, task_dir=path.parent, root_path=path.parent.parent)


def iter_task_states(config: Any, *, hooks: TaskStorageHooks, include_hidden: bool = False) -> list[dict[str, Any]]:
    task_states: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for root in hooks.all_task_roots(config):
        if not root.exists():
            continue
        for item in sorted(root.iterdir()):
            state = hooks.read_json(item / "state.json", {})
            if not isinstance(state, dict) or not state:
                continue
            normalized_state = hooks.reconcile_active_task_state(config, hooks.normalize_task_state_payload(state))
            task_id = str(normalized_state.get("task_id", "")).strip()
            if task_id and task_id in seen_task_ids:
                continue
            if not include_hidden and hooks.is_hidden_status(str(normalized_state.get("status", ""))):
                continue
            if task_id:
                seen_task_ids.add(task_id)
            task_states.append(normalized_state)
    task_states.sort(key=hooks.task_list_sort_key)
    return task_states


def iter_all_task_states(config: Any, *, hooks: TaskStorageHooks) -> list[dict[str, Any]]:
    return iter_task_states(config, hooks=hooks, include_hidden=True)
