from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable


TASK_INDEX_FILENAME = "task-index.json"
TASK_INDEX_VERSION = 1
TASK_INDEX_REFRESH_INTERVAL_SECONDS = 5.0
_TASK_INDEX_ROW_CACHE: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}


def task_index_path(app_home: Path) -> Path:
    return app_home / TASK_INDEX_FILENAME


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _normalize_int_list(raw_value: Any) -> list[int]:
    values = raw_value if isinstance(raw_value, list) else []
    normalized: list[int] = []
    for item in values:
        try:
            normalized.append(int(item))
        except (TypeError, ValueError):
            continue
    return normalized


def _normalize_str_list(raw_value: Any) -> list[str]:
    values = raw_value if isinstance(raw_value, list) else []
    normalized: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_bool(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)
    text = str(raw_value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def _normalize_task_index_entry(raw: Any) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    return {
        "task_id": str(payload.get("task_id", "")).strip(),
        "task_key": str(payload.get("task_key", "")).strip(),
        "client_task_id": str(payload.get("client_task_id", "")).strip(),
        "client_task_key": str(payload.get("client_task_key", "")).strip(),
        "status": str(payload.get("status", "")).strip(),
        "owner_tenant": str(payload.get("owner_tenant", "")).strip(),
        "owner_role": str(payload.get("owner_role", "")).strip(),
        "owner_label": str(payload.get("owner_label", "")).strip(),
        "submitted_via_api": _normalize_bool(payload.get("submitted_via_api", False)),
        "feedback_mode": str(payload.get("feedback_mode", "")).strip(),
        "pending_feedback": _normalize_bool(payload.get("pending_feedback", False)),
        "needs_attention": _normalize_bool(payload.get("needs_attention", False)),
        "execution_mode": str(payload.get("execution_mode", "")).strip(),
        "executor_name": str(payload.get("executor_name", "")).strip(),
        "workdir": str(payload.get("workdir", "")).strip(),
        "closeout_proposal_dir": str(payload.get("closeout_proposal_dir", "")).strip(),
        "gpu_slots": int(payload.get("gpu_slots", 0) or 0),
        "priority": int(payload.get("priority", 0) or 0),
        "submitted_at": str(payload.get("submitted_at", "")).strip(),
        "updated_at": str(payload.get("updated_at", "")).strip(),
        "assigned_gpus": _normalize_int_list(payload.get("assigned_gpus", [])),
        "agent_name": str(payload.get("agent_name", "")).strip(),
        "depends_on": _normalize_str_list(payload.get("depends_on", [])),
        "require_signal_to_unblock": _normalize_bool(payload.get("require_signal_to_unblock", False)),
        "taskboard_signal": str(payload.get("taskboard_signal", "")).strip(),
        "state_mtime_ns": int(payload.get("state_mtime_ns", 0) or 0),
        "spec_mtime_ns": int(payload.get("spec_mtime_ns", 0) or 0),
        "root_path": str(payload.get("root_path", "")).strip(),
        "task_dir": str(payload.get("task_dir", "")).strip(),
    }


def load_task_index(app_home: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(task_index_path(app_home), {})
    raw_entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
    if not isinstance(raw_entries, dict):
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for raw_task_id, raw_entry in raw_entries.items():
        task_id = str(raw_task_id or "").strip()
        if not task_id:
            continue
        normalized = _normalize_task_index_entry(raw_entry)
        if normalized["task_id"]:
            entries[task_id] = normalized
    return entries


def _sorted_task_index_rows(entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries.values(), key=lambda item: (str(item.get("task_id", "")),))


def _task_index_cache_key(app_home: Path, task_roots: Iterable[Path]) -> tuple[str, tuple[str, ...]]:
    normalized_app_home = str(Path(app_home).expanduser().resolve())
    normalized_roots = tuple(str(Path(root).expanduser().resolve()) for root in task_roots)
    return normalized_app_home, normalized_roots


def _invalidate_task_index_cache(app_home: Path) -> None:
    normalized_app_home = str(Path(app_home).expanduser().resolve())
    stale_keys = [key for key in _TASK_INDEX_ROW_CACHE if key[0] == normalized_app_home]
    for key in stale_keys:
        _TASK_INDEX_ROW_CACHE.pop(key, None)


def clear_task_index_cache(app_home: Path | None = None) -> None:
    if app_home is None:
        _TASK_INDEX_ROW_CACHE.clear()
        return
    _invalidate_task_index_cache(app_home)


def write_task_index(app_home: Path, entries: dict[str, dict[str, Any]]) -> None:
    normalized_entries = {
        task_id: _normalize_task_index_entry(entry)
        for task_id, entry in entries.items()
        if str(task_id or "").strip()
    }
    _atomic_write_json(
        task_index_path(app_home),
        {
            "version": TASK_INDEX_VERSION,
            "entries": normalized_entries,
        },
    )
    _invalidate_task_index_cache(app_home)


def _task_file_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _load_task_index_entry(task_dir: Path, *, root_path: Path) -> dict[str, Any]:
    spec_path = task_dir / "spec.json"
    state_path = task_dir / "state.json"
    spec = _read_json(spec_path, {})
    state = _read_json(state_path, {})
    if not isinstance(spec, dict):
        spec = {}
    if not isinstance(state, dict):
        state = {}
    if not spec and not state:
        return {}
    task_id = str(state.get("task_id", spec.get("task_id", task_dir.name))).strip() or task_dir.name
    return _normalize_task_index_entry(
        {
            "task_id": task_id,
            "task_key": str(state.get("task_key", spec.get("task_key", task_id))).strip() or task_id,
            "client_task_id": str(state.get("client_task_id", spec.get("client_task_id", ""))).strip(),
            "client_task_key": str(state.get("client_task_key", spec.get("client_task_key", ""))).strip(),
            "status": str(state.get("status", "")).strip(),
            "owner_tenant": str(state.get("owner_tenant", spec.get("owner_tenant", ""))).strip(),
            "owner_role": str(state.get("owner_role", spec.get("owner_role", ""))).strip(),
            "owner_label": str(state.get("owner_label", spec.get("owner_label", ""))).strip(),
            "submitted_via_api": state.get("submitted_via_api", spec.get("submitted_via_api", False)),
            "feedback_mode": str(state.get("feedback_mode", spec.get("feedback_mode", ""))).strip(),
            "pending_feedback": state.get("pending_feedback", False),
            "needs_attention": state.get("needs_attention", False),
            "execution_mode": str(state.get("execution_mode", spec.get("execution_mode", ""))).strip(),
            "executor_name": str(state.get("executor_name", spec.get("executor_name", ""))).strip(),
            "workdir": str(state.get("workdir", spec.get("workdir", ""))).strip(),
            "closeout_proposal_dir": str(
                state.get("closeout_proposal_dir", spec.get("closeout_proposal_dir", ""))
            ).strip(),
            "gpu_slots": state.get("gpu_slots", spec.get("gpu_slots", 0)),
            "priority": state.get("priority", spec.get("priority", 0)),
            "submitted_at": str(state.get("submitted_at", spec.get("submitted_at", ""))).strip(),
            "updated_at": str(state.get("updated_at", spec.get("updated_at", ""))).strip(),
            "assigned_gpus": state.get("assigned_gpus", spec.get("assigned_gpus", [])),
            "agent_name": str(state.get("agent_name", spec.get("agent_name", ""))).strip(),
            "depends_on": state.get("depends_on", spec.get("depends_on", [])),
            "require_signal_to_unblock": state.get(
                "require_signal_to_unblock",
                spec.get("require_signal_to_unblock", False),
            ),
            "taskboard_signal": str(state.get("taskboard_signal", spec.get("taskboard_signal", ""))).strip(),
            "state_mtime_ns": _task_file_mtime_ns(state_path),
            "spec_mtime_ns": _task_file_mtime_ns(spec_path),
            "root_path": str(root_path),
            "task_dir": str(task_dir),
        }
    )


def refresh_task_index(app_home: Path, task_roots: Iterable[Path]) -> list[dict[str, Any]]:
    existing_entries = load_task_index(app_home)
    updated_entries: dict[str, dict[str, Any]] = {}
    changed = False
    normalized_roots = [Path(root).expanduser().resolve() for root in task_roots]
    seen_task_ids: set[str] = set()
    for root in normalized_roots:
        if not root.exists():
            continue
        for task_dir in sorted(root.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = str(task_dir.name).strip()
            if not task_id or task_id in seen_task_ids:
                continue
            seen_task_ids.add(task_id)
            spec_mtime_ns = _task_file_mtime_ns(task_dir / "spec.json")
            state_mtime_ns = _task_file_mtime_ns(task_dir / "state.json")
            cached = existing_entries.get(task_id)
            if (
                cached
                and int(cached.get("spec_mtime_ns", 0) or 0) == spec_mtime_ns
                and int(cached.get("state_mtime_ns", 0) or 0) == state_mtime_ns
                and str(cached.get("root_path", "")).strip() == str(root)
            ):
                updated_entries[task_id] = cached
                continue
            entry = _load_task_index_entry(task_dir, root_path=root)
            if entry:
                updated_entries[task_id] = entry
            changed = True
    if set(updated_entries) != set(existing_entries):
        changed = True
    if changed:
        write_task_index(app_home, updated_entries)
    return _sorted_task_index_rows(updated_entries)


def load_cached_task_index_rows(
    app_home: Path,
    task_roots: Iterable[Path],
    *,
    refresh_interval_seconds: float = TASK_INDEX_REFRESH_INTERVAL_SECONDS,
) -> list[dict[str, Any]]:
    normalized_app_home = Path(app_home).expanduser().resolve()
    normalized_roots = tuple(Path(root).expanduser().resolve() for root in task_roots)
    cache_key = _task_index_cache_key(normalized_app_home, normalized_roots)
    index_path = task_index_path(normalized_app_home)
    index_mtime_ns = _task_file_mtime_ns(index_path)
    cached = _TASK_INDEX_ROW_CACHE.get(cache_key)
    now = time.monotonic()
    if index_mtime_ns > 0:
        if cached and int(cached.get("index_mtime_ns", 0) or 0) == index_mtime_ns:
            age_seconds = now - float(cached.get("last_resync_monotonic", 0.0) or 0.0)
            if age_seconds < max(0.0, float(refresh_interval_seconds or 0.0)):
                return list(cached.get("rows", []))
        else:
            rows = _sorted_task_index_rows(load_task_index(normalized_app_home))
            _TASK_INDEX_ROW_CACHE[cache_key] = {
                "rows": rows,
                "index_mtime_ns": index_mtime_ns,
                "last_resync_monotonic": now,
            }
            return list(rows)
    rows = refresh_task_index(normalized_app_home, normalized_roots)
    _TASK_INDEX_ROW_CACHE[cache_key] = {
        "rows": rows,
        "index_mtime_ns": _task_file_mtime_ns(index_path),
        "last_resync_monotonic": now,
    }
    return list(rows)


def update_task_index_entry(app_home: Path, *, task_dir: Path, root_path: Path) -> None:
    task_id = str(task_dir.name).strip()
    if not task_id:
        return
    entries = load_task_index(app_home)
    entry = _load_task_index_entry(task_dir, root_path=root_path)
    if entry:
        entries[task_id] = entry
    else:
        entries.pop(task_id, None)
    write_task_index(app_home, entries)


def remove_task_index_entry(app_home: Path, task_id: str) -> None:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return
    entries = load_task_index(app_home)
    if normalized_task_id not in entries:
        return
    entries.pop(normalized_task_id, None)
    write_task_index(app_home, entries)
