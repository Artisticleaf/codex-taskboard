from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class SchedulerReadinessHooks:
    is_hidden_status: Callable[[str], bool]
    task_state_recency_key: Callable[[dict[str, Any]], Any]
    iter_all_task_states: Callable[[Any], list[dict[str, Any]]]
    newest_matches: Callable[[str, str], list[Path]]
    success_taskboard_signals: set[str]
    resolved_cpu_profile: Callable[[dict[str, Any]], str]
    declared_cpu_profile: Callable[[dict[str, Any]], str]
    resolve_cpu_thread_policy: Callable[..., dict[str, Any]]
    resolve_cpu_worker_policy: Callable[..., dict[str, Any]]
    coerce_non_negative_int: Callable[[Any], int]
    select_gpu_ids_for_task: Callable[..., tuple[list[int] | None, str]]
    gpu_row_free_mb: Callable[[dict[str, Any]], int]


@dataclass(frozen=True)
class SchedulerEnrichmentHooks:
    load_task_spec: Callable[[Any, str], dict[str, Any]]
    normalize_task_spec_payload: Callable[[dict[str, Any]], dict[str, Any]]
    parse_gpu_id_list: Callable[[Any], list[int]]
    evaluate_task_readiness: Callable[..., dict[str, Any]]
    task_lifecycle_state: Callable[[dict[str, Any]], str]
    task_runtime_state: Callable[[Any, dict[str, Any]], str]
    task_has_launch_metadata: Callable[[dict[str, Any]], bool]
    task_platform_recovery_state: Callable[[dict[str, Any]], dict[str, Any]]
    task_automation_recommendation: Callable[[dict[str, Any]], str]
    followup_entity_info: Callable[[Any, str], tuple[bool, str]]


def latest_task_states_by_key(
    states: list[dict[str, Any]],
    *,
    hooks: SchedulerReadinessHooks,
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for state in states:
        status = str(state.get("status", ""))
        if hooks.is_hidden_status(status):
            continue
        task_key = str(state.get("task_key", state.get("task_id", ""))).strip()
        if not task_key:
            continue
        previous = latest.get(task_key)
        if previous is None or hooks.task_state_recency_key(state) > hooks.task_state_recency_key(previous):
            latest[task_key] = state
    return latest


def latest_task_state_for_key(
    config: Any,
    task_key: str,
    *,
    hooks: SchedulerReadinessHooks,
) -> dict[str, Any] | None:
    return latest_task_states_by_key(hooks.iter_all_task_states(config), hooks=hooks).get(task_key)


def stringify_report_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def required_report_conditions(spec: dict[str, Any]) -> list[dict[str, str]]:
    conditions = spec.get("required_report_conditions", []) or []
    parsed: list[dict[str, str]] = []
    for item in conditions:
        if isinstance(item, dict):
            key = str(item.get("key", "")).strip()
            expected = str(item.get("expected", "")).strip()
        else:
            raw = str(item).strip()
            if "=" not in raw:
                continue
            key, expected = raw.split("=", 1)
            key = key.strip()
            expected = expected.strip()
        if not key:
            continue
        parsed.append({"key": key, "expected": expected})
    return parsed


def report_value_from_state(state: dict[str, Any], key: str) -> str:
    if key in state:
        return stringify_report_value(state.get(key))
    structured_report = state.get("structured_report", {})
    if isinstance(structured_report, dict) and key in structured_report:
        return stringify_report_value(structured_report.get(key))
    return ""


def dependency_resolution(
    config: Any,
    spec: dict[str, Any],
    *,
    hooks: SchedulerReadinessHooks,
    latest_states_by_key: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    dependencies = spec.get("depends_on", [])
    if not isinstance(dependencies, list):
        return [], {}
    resolution: list[dict[str, Any]] = []
    latest_states: dict[str, dict[str, Any]] = {}
    for dep in dependencies:
        dep_key = str(dep).strip()
        if not dep_key:
            continue
        state = (
            latest_states_by_key.get(dep_key)
            if latest_states_by_key is not None
            else latest_task_state_for_key(config, dep_key, hooks=hooks)
        )
        latest_states[dep_key] = state or {}
        if state is None:
            resolution.append(
                {
                    "task_key": dep_key,
                    "resolved_task_id": "",
                    "resolved_status": "missing",
                    "satisfied": False,
                    "reason": "missing_dependency",
                }
            )
            continue
        status = str(state.get("status", ""))
        signal_required = bool(state.get("require_signal_to_unblock", False))
        signal_value = str(state.get("taskboard_signal", "")).strip()
        satisfied = status in {"completed", "observed_exit"} and (
            not signal_required or signal_value in hooks.success_taskboard_signals
        )
        reason = "ready"
        if status not in {"completed", "observed_exit"}:
            reason = f"upstream_status:{status or 'unknown'}"
        elif signal_required and signal_value not in hooks.success_taskboard_signals:
            reason = f"waiting_signal:{signal_value or 'missing'}"
        resolution.append(
            {
                "task_key": dep_key,
                "resolved_task_id": str(state.get("task_id", "")),
                "resolved_status": status,
                "resolved_signal": signal_value,
                "require_signal_to_unblock": signal_required,
                "satisfied": satisfied,
                "reason": reason,
            }
        )
    return resolution, latest_states


def artifact_resolution(
    spec: dict[str, Any],
    *,
    hooks: SchedulerReadinessHooks,
) -> list[dict[str, Any]]:
    patterns = spec.get("required_artifact_globs", []) or []
    if not isinstance(patterns, list):
        return []
    resolution: list[dict[str, Any]] = []
    workdir = str(spec.get("workdir", ""))
    for pattern in patterns:
        raw_pattern = str(pattern).strip()
        if not raw_pattern:
            continue
        matches = hooks.newest_matches(raw_pattern, workdir)
        newest = matches[-1] if matches else None
        resolution.append(
            {
                "pattern": raw_pattern,
                "matched": bool(newest),
                "newest_path": str(newest) if newest else "",
            }
        )
    return resolution


def report_resolution(
    spec: dict[str, Any],
    latest_dependency_states: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    conditions = required_report_conditions(spec)
    if not conditions:
        return []
    resolution: list[dict[str, Any]] = []
    dependency_items = [(task_key, state) for task_key, state in latest_dependency_states.items() if state]
    for condition in conditions:
        key = condition["key"]
        expected = condition["expected"]
        matched_task_id = ""
        actual_values: list[str] = []
        satisfied = False
        for _dep_key, state in dependency_items:
            actual = report_value_from_state(state, key)
            if actual:
                actual_values.append(actual)
            if actual == expected:
                matched_task_id = str(state.get("task_id", ""))
                satisfied = True
                break
        deduped_actual_values = sorted(set(actual_values))
        reason = "ready" if satisfied else ("missing_report_value" if not deduped_actual_values else "report_value_mismatch")
        resolution.append(
            {
                "key": key,
                "expected": expected,
                "actual_values": deduped_actual_values,
                "resolved_task_id": matched_task_id,
                "satisfied": satisfied,
                "reason": reason,
            }
        )
    return resolution


def selected_gpu_snapshot(
    gpu_rows: list[dict[str, Any]],
    gpu_ids: list[int],
    *,
    hooks: SchedulerReadinessHooks,
) -> list[dict[str, Any]]:
    row_by_index = {int(row.get("index", -1)): row for row in gpu_rows}
    snapshot: list[dict[str, Any]] = []
    for gpu_id in gpu_ids:
        row = row_by_index.get(int(gpu_id))
        if row is None:
            snapshot.append({"index": int(gpu_id), "missing": True})
            continue
        snapshot.append(
            {
                "index": int(row.get("index", gpu_id)),
                "name": str(row.get("name", "")),
                "memory_total_mb": int(row.get("memory_total_mb", 0) or 0),
                "memory_used_mb": int(row.get("memory_used_mb", 0) or 0),
                "memory_free_mb": hooks.gpu_row_free_mb(row),
                "gpu_util_percent": int(row.get("gpu_util_percent", 0) or 0),
            }
        )
    return snapshot


def evaluate_task_readiness(
    config: Any,
    spec: dict[str, Any],
    *,
    hooks: SchedulerReadinessHooks,
    gpu_rows: list[dict[str, Any]] | None = None,
    total_gpu_slots: int = 0,
    reserved_gpu_ids: set[int] | None = None,
    active_cpu_threads: int = 0,
    reserved_cpu_threads: int = 0,
    cpu_thread_limit: int = 0,
    latest_states_by_key: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dep_resolution, latest_dependency_states = dependency_resolution(
        config,
        spec,
        hooks=hooks,
        latest_states_by_key=latest_states_by_key,
    )
    artifact_req_resolution = artifact_resolution(spec, hooks=hooks)
    report_req_resolution = report_resolution(spec, latest_dependency_states)
    cpu_profile = hooks.resolved_cpu_profile(spec)
    cpu_policy = hooks.resolve_cpu_thread_policy(spec, cpu_thread_limit=cpu_thread_limit)
    cpu_worker_policy = hooks.resolve_cpu_worker_policy(spec, cpu_thread_limit=cpu_thread_limit)
    cpu_threads = max(0, int(cpu_policy.get("reservation_threads", 0) or 0))
    cpu_workers = max(0, int(cpu_worker_policy.get("reservation_workers", 0) or 0))
    cpu_budget = cpu_threads + cpu_workers
    cpu_thread_source = str(cpu_policy.get("source", "") or "default")
    cpu_worker_source = str(cpu_worker_policy.get("source", "") or "default")

    dependency_state = "none"
    blocked_reason = ""
    if dep_resolution:
        dependency_state = "ready" if all(bool(item.get("satisfied", False)) for item in dep_resolution) else "waiting"
        if dependency_state != "ready":
            first = next((item for item in dep_resolution if not bool(item.get("satisfied", False))), dep_resolution[0])
            blocked_reason = f"dependency:{first.get('task_key', '')}:{first.get('reason', 'waiting')}"

    artifact_state = "none"
    if artifact_req_resolution:
        artifact_state = "ready" if all(bool(item.get("matched", False)) for item in artifact_req_resolution) else "waiting"
        if not blocked_reason and artifact_state != "ready":
            first = next((item for item in artifact_req_resolution if not bool(item.get("matched", False))), artifact_req_resolution[0])
            blocked_reason = f"artifact:{first.get('pattern', '')}"

    report_state = "none"
    if report_req_resolution:
        report_state = "ready" if all(bool(item.get("satisfied", False)) for item in report_req_resolution) else "waiting"
        if not blocked_reason and report_state != "ready":
            first = next((item for item in report_req_resolution if not bool(item.get("satisfied", False))), report_req_resolution[0])
            blocked_reason = f"report:{first.get('key', '')}:{first.get('reason', 'waiting')}"

    eligible_gpu_ids: list[int] = []
    gpu_assignment_source = ""
    gpu_block_reason = ""
    available_cpu_threads = (
        max(0, int(cpu_thread_limit) - int(active_cpu_threads) - int(reserved_cpu_threads))
        if int(cpu_thread_limit) > 0
        else 0
    )
    cpu_block_reason = ""
    if not blocked_reason and int(cpu_thread_limit) > 0 and cpu_budget > 0 and cpu_budget > available_cpu_threads:
        cpu_block_reason = f"need={cpu_budget}:available={available_cpu_threads}:limit={int(cpu_thread_limit)}"
        blocked_reason = f"cpu_budget:{cpu_block_reason}"
    if not blocked_reason and gpu_rows is not None and int(spec.get("gpu_slots", 0) or 0) > 0:
        selected, selected_reason = hooks.select_gpu_ids_for_task(
            spec,
            total_gpu_slots=total_gpu_slots,
            gpu_rows=gpu_rows,
            reserved_gpu_ids=reserved_gpu_ids or set(),
        )
        if selected is None:
            gpu_block_reason = selected_reason
            blocked_reason = f"gpu_headroom:{selected_reason}"
        else:
            eligible_gpu_ids = selected
            gpu_assignment_source = selected_reason

    return {
        "dependency_state": dependency_state,
        "dependency_resolution": dep_resolution,
        "artifact_state": artifact_state,
        "artifact_resolution": artifact_req_resolution,
        "report_state": report_state,
        "report_resolution": report_req_resolution,
        "blocked_reason": blocked_reason,
        "cpu_profile": hooks.declared_cpu_profile(spec),
        "cpu_profile_resolved": cpu_profile,
        "cpu_threads": cpu_threads,
        "cpu_threads_mode": str(cpu_policy.get("mode", "fixed")),
        "cpu_threads_min": int(cpu_policy.get("min_threads", 0) or 0),
        "cpu_threads_max": int(cpu_policy.get("max_threads", 0) or 0),
        "assigned_cpu_threads": hooks.coerce_non_negative_int(spec.get("assigned_cpu_threads", 0)),
        "cpu_thread_source": cpu_thread_source,
        "cpu_workers": cpu_workers,
        "cpu_workers_min": int(cpu_worker_policy.get("min_workers", 0) or 0),
        "cpu_workers_max": int(cpu_worker_policy.get("max_workers", 0) or 0),
        "assigned_cpu_workers": hooks.coerce_non_negative_int(spec.get("assigned_cpu_workers", 0)),
        "cpu_worker_source": cpu_worker_source,
        "cpu_budget": cpu_budget,
        "available_cpu_threads": available_cpu_threads,
        "cpu_block_reason": cpu_block_reason,
        "cpu_policy": cpu_policy,
        "cpu_worker_policy": cpu_worker_policy,
        "eligible_gpu_ids": eligible_gpu_ids,
        "gpu_assignment_source": gpu_assignment_source,
        "gpu_block_reason": gpu_block_reason,
    }


def merged_spec_with_state_for_readiness(
    config: Any,
    state: dict[str, Any],
    *,
    hooks: SchedulerEnrichmentHooks,
) -> dict[str, Any]:
    task_id = str(state.get("task_id", "")).strip()
    spec = hooks.load_task_spec(config, task_id) if task_id else hooks.normalize_task_spec_payload(state)
    state_assigned_gpus = state.get("assigned_gpus", [])
    state_allowed_gpus = state.get("allowed_gpus", [])
    state_depends_on = state.get("depends_on", [])
    merged_spec = dict(spec)
    merged_spec.update(
        {
            "task_id": task_id or str(spec.get("task_id", "")),
            "task_key": str(state.get("task_key", spec.get("task_key", ""))),
            "workdir": str(state.get("workdir", spec.get("workdir", ""))),
            "command": str(state.get("command", spec.get("command", ""))),
            "gpu_slots": int(state.get("gpu_slots", spec.get("gpu_slots", 0)) or 0),
            "cpu_profile": str(state.get("cpu_profile", spec.get("cpu_profile", "auto"))),
            "cpu_threads": int(state.get("cpu_threads", spec.get("cpu_threads", 0)) or 0),
            "cpu_threads_min": int(state.get("cpu_threads_min", spec.get("cpu_threads_min", 0)) or 0),
            "cpu_threads_max": int(state.get("cpu_threads_max", spec.get("cpu_threads_max", 0)) or 0),
            "cpu_threads_mode": str(state.get("cpu_threads_mode", spec.get("cpu_threads_mode", ""))),
            "assigned_cpu_threads": int(state.get("assigned_cpu_threads", spec.get("assigned_cpu_threads", 0)) or 0),
            "cpu_workers": int(state.get("cpu_workers", spec.get("cpu_workers", 0)) or 0),
            "cpu_workers_min": int(state.get("cpu_workers_min", spec.get("cpu_workers_min", 0)) or 0),
            "cpu_workers_max": int(state.get("cpu_workers_max", spec.get("cpu_workers_max", 0)) or 0),
            "assigned_cpu_workers": int(state.get("assigned_cpu_workers", spec.get("assigned_cpu_workers", 0)) or 0),
            "assigned_gpus": hooks.parse_gpu_id_list(state_assigned_gpus or spec.get("assigned_gpus", [])),
            "allowed_gpus": hooks.parse_gpu_id_list(state_allowed_gpus or spec.get("allowed_gpus", [])),
            "depends_on": state_depends_on or spec.get("depends_on", []),
        }
    )
    return merged_spec


def dispatch_diagnostics_payload(
    enriched: dict[str, Any],
    readiness: dict[str, Any],
    *,
    lifecycle_state: str,
    recorded_gpu_assignment_source: str,
    hooks: SchedulerEnrichmentHooks,
) -> dict[str, Any]:
    if lifecycle_state == "queued":
        eligible_gpu_ids = hooks.parse_gpu_id_list(readiness.get("eligible_gpu_ids", []))
        blocked_reason = str(readiness.get("blocked_reason", "")).strip()
        cpu_block_reason = str(readiness.get("cpu_block_reason", "")).strip()
        gpu_block_reason = str(readiness.get("gpu_block_reason", "")).strip()
        gpu_assignment_source = str(readiness.get("gpu_assignment_source", "")).strip()
    elif lifecycle_state == "running":
        eligible_gpu_ids = hooks.parse_gpu_id_list(enriched.get("assigned_gpus", []))
        blocked_reason = ""
        cpu_block_reason = ""
        gpu_block_reason = ""
        gpu_assignment_source = recorded_gpu_assignment_source
    else:
        eligible_gpu_ids = []
        blocked_reason = ""
        cpu_block_reason = ""
        gpu_block_reason = ""
        gpu_assignment_source = recorded_gpu_assignment_source
    return {
        "scheduler_state": (
            "blocked"
            if lifecycle_state == "queued" and blocked_reason
            else "eligible"
            if lifecycle_state == "queued"
            else "historical_after_launch"
            if hooks.task_has_launch_metadata(enriched)
            else "not_applicable"
        ),
        "blocked_reason": blocked_reason,
        "cpu_block_reason": cpu_block_reason,
        "gpu_block_reason": gpu_block_reason,
        "eligible_gpu_ids": eligible_gpu_ids,
        "gpu_assignment_source": gpu_assignment_source,
        "dependency_state": str(readiness.get("dependency_state", "")).strip(),
        "dependency_resolution": readiness.get("dependency_resolution", []),
        "artifact_state": str(readiness.get("artifact_state", "")).strip(),
        "artifact_resolution": readiness.get("artifact_resolution", []),
        "report_state": str(readiness.get("report_state", "")).strip(),
        "report_resolution": readiness.get("report_resolution", []),
        "dispatch_gpu_snapshot": list(enriched.get("dispatch_gpu_snapshot", [])),
    }


def phase_for_enriched_state(
    enriched: dict[str, Any],
    dispatch_diagnostics: dict[str, Any],
    *,
    lifecycle_state: str,
) -> str:
    phase = str(enriched.get("status", ""))
    if lifecycle_state == "queued":
        blocked_reason = str(dispatch_diagnostics.get("blocked_reason", "")).strip()
        if blocked_reason.startswith("dependency:"):
            return "blocked_by_dependency"
        if blocked_reason.startswith("artifact:"):
            return "waiting_artifact"
        if blocked_reason.startswith("report:"):
            return "waiting_report"
        if blocked_reason.startswith("cpu_budget:"):
            return "blocked_by_cpu_budget"
        if blocked_reason.startswith("gpu_headroom:"):
            return "blocked_by_gpu_headroom"
        return "eligible"
    if lifecycle_state == "running":
        return "running"
    if lifecycle_state == "awaiting_feedback":
        return "awaiting_feedback"
    if lifecycle_state == "completed":
        return "completed"
    if lifecycle_state == "failed":
        return "failed"
    return phase or lifecycle_state or "unknown"


def enrich_task_state(
    config: Any,
    state: dict[str, Any],
    *,
    hooks: SchedulerEnrichmentHooks,
    gpu_rows: list[dict[str, Any]] | None = None,
    total_gpu_slots: int = 0,
    reserved_gpu_ids: set[int] | None = None,
    active_cpu_threads: int = 0,
    reserved_cpu_threads: int = 0,
    cpu_thread_limit: int = 0,
    latest_states_by_key: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    enriched = dict(state)
    merged_spec = merged_spec_with_state_for_readiness(config, state, hooks=hooks)
    recorded_gpu_assignment_source = str(
        state.get("gpu_assignment_source", merged_spec.get("gpu_assignment_source", ""))
    ).strip()
    readiness = hooks.evaluate_task_readiness(
        config,
        merged_spec,
        gpu_rows=gpu_rows,
        total_gpu_slots=total_gpu_slots,
        reserved_gpu_ids=reserved_gpu_ids,
        active_cpu_threads=active_cpu_threads,
        reserved_cpu_threads=reserved_cpu_threads,
        cpu_thread_limit=cpu_thread_limit,
        latest_states_by_key=latest_states_by_key,
    )
    enriched.update(readiness)
    lifecycle_state = hooks.task_lifecycle_state(enriched)
    runtime_state = hooks.task_runtime_state(config, enriched)
    if lifecycle_state == "queued":
        enriched["blocked_reason"] = str(readiness.get("blocked_reason", "")).strip()
        enriched["cpu_block_reason"] = str(readiness.get("cpu_block_reason", "")).strip()
        enriched["gpu_block_reason"] = str(readiness.get("gpu_block_reason", "")).strip()
        enriched["eligible_gpu_ids"] = hooks.parse_gpu_id_list(readiness.get("eligible_gpu_ids", []))
        enriched["gpu_assignment_source"] = str(readiness.get("gpu_assignment_source", "")).strip()
    else:
        enriched["blocked_reason"] = ""
        enriched["cpu_block_reason"] = ""
        enriched["gpu_block_reason"] = ""
        enriched["eligible_gpu_ids"] = (
            hooks.parse_gpu_id_list(enriched.get("assigned_gpus", []))
            if lifecycle_state == "running"
            else []
        )
        enriched["gpu_assignment_source"] = recorded_gpu_assignment_source
    dispatch_diagnostics = dispatch_diagnostics_payload(
        enriched,
        readiness,
        lifecycle_state=lifecycle_state,
        recorded_gpu_assignment_source=recorded_gpu_assignment_source,
        hooks=hooks,
    )
    launch_diagnostics = {
        "launch_state": (
            "launched"
            if lifecycle_state == "running"
            else "finished_awaiting_feedback"
            if lifecycle_state == "awaiting_feedback"
            else "finished"
            if lifecycle_state in {"completed", "failed"} and hooks.task_has_launch_metadata(enriched)
            else "launch_rejected"
            if str(enriched.get("rejected_reason", "")).strip()
            else "not_started"
        ),
        "started_at": str(enriched.get("started_at", "")).strip(),
        "started_via_tmux_at": str(enriched.get("started_via_tmux_at", "")).strip(),
        "why_started": str(enriched.get("why_started", "")).strip(),
        "assigned_gpus": hooks.parse_gpu_id_list(enriched.get("assigned_gpus", [])),
        "dispatch_gpu_snapshot": list(enriched.get("dispatch_gpu_snapshot", [])),
        "launch_gpu_snapshot": list(enriched.get("launch_gpu_snapshot", [])),
        "rejected_reason": str(enriched.get("rejected_reason", "")).strip(),
    }
    enriched["lifecycle_state"] = lifecycle_state
    enriched["runtime_state"] = runtime_state
    enriched["dispatch_diagnostics"] = dispatch_diagnostics
    enriched["launch_diagnostics"] = launch_diagnostics
    enriched["platform_recovery"] = hooks.task_platform_recovery_state(enriched)
    enriched["automation_recommendation"] = hooks.task_automation_recommendation(enriched)
    followup_present, followup_key = hooks.followup_entity_info(config, str(enriched.get("task_id", "")))
    enriched["followup_entity_present"] = followup_present
    enriched["followup_entity_key"] = followup_key
    followup_status = str(enriched.get("followup_status", "")).strip()
    if followup_present:
        enriched["followup_audit_status"] = "live_entity_present"
    elif followup_status == "scheduled":
        enriched["followup_audit_status"] = "state_scheduled_but_entity_missing"
    elif followup_status == "stopped":
        enriched["followup_audit_status"] = "stopped_no_entity"
    elif followup_status == "resolved":
        enriched["followup_audit_status"] = "resolved_no_entity"
    else:
        enriched["followup_audit_status"] = ""
    enriched["phase"] = phase_for_enriched_state(
        enriched,
        dispatch_diagnostics,
        lifecycle_state=lifecycle_state,
    )
    return enriched
