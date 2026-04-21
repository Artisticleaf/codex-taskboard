from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ApiViewHooks:
    active_task_statuses: set[str]
    runnable_statuses: set[str]
    normalize_task_id: Callable[[str], str]
    resolve_api_visible_task_id: Callable[[Any, str, dict[str, Any]], str]
    load_task_state: Callable[[Any, str], dict[str, Any]]
    load_task_spec: Callable[[Any, str], dict[str, Any]]
    build_task_result_payload: Callable[[Any, str], dict[str, Any]]
    task_visible_to_api_token: Callable[[dict[str, Any], dict[str, Any] | None, dict[str, Any]], bool]
    task_visible_in_api_queue: Callable[[dict[str, Any], dict[str, Any] | None, dict[str, Any]], bool]
    task_index_rows: Callable[[Any], list[dict[str, Any]]]
    is_hidden_status: Callable[[str], bool]
    latest_task_states_by_key: Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]]
    dependency_resolution: Callable[..., tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]]
    task_requested_cpu_budget: Callable[[dict[str, Any]], int]
    merged_spec_with_state: Callable[[Any, dict[str, Any]], dict[str, Any]]
    get_gpu_summary_table: Callable[[], list[dict[str, Any]]]
    detect_gpu_count: Callable[[], int]
    detect_default_cpu_thread_limit: Callable[[], int]
    filter_dashboard_tasks: Callable[..., list[dict[str, Any]]]
    sort_dashboard_tasks: Callable[[Any, list[dict[str, Any]], str], list[dict[str, Any]]]
    enrich_task_state: Callable[..., dict[str, Any]]
    dashboard_issue_text: Callable[[dict[str, Any]], str]
    parse_gpu_id_list: Callable[[Any], list[int]]
    is_terminal_status: Callable[[str], bool]
    build_api_visibility_scope: Callable[[dict[str, Any], str], str]
    is_public_queue_view: Callable[[dict[str, Any], str], bool]
    build_spec_from_submit_job_payload: Callable[..., tuple[dict[str, Any], bool]]
    apply_api_token_submit_policy: Callable[[Any, dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
    submit_spec: Callable[[Any, dict[str, Any], bool], dict[str, Any]]


def build_task_result_payload_for_api(
    config: Any,
    task_id: str,
    token_record: dict[str, Any],
    *,
    hooks: ApiViewHooks,
) -> dict[str, Any]:
    normalized_task_id = hooks.resolve_api_visible_task_id(config, task_id, token_record)
    state = hooks.load_task_state(config, normalized_task_id)
    if not state:
        raise ValueError(f"Task not found: {normalized_task_id}")
    spec = hooks.load_task_spec(config, normalized_task_id)
    if not hooks.task_visible_to_api_token(state, spec, token_record):
        raise PermissionError(f"Task not found: {normalized_task_id}")
    return hooks.build_task_result_payload(config, normalized_task_id)


def reconcile_task_index_row(config: Any, row: dict[str, Any], *, hooks: ApiViewHooks) -> dict[str, Any]:
    task_id = str(row.get("task_id", "")).strip()
    if not task_id or str(row.get("status", "")) not in hooks.active_task_statuses:
        return dict(row)
    state = hooks.load_task_state(config, task_id)
    if not state:
        return dict(row)
    merged = dict(row)
    merged.update(state)
    return merged


def annotate_cached_queue_dependencies(
    config: Any,
    rows: list[dict[str, Any]],
    *,
    latest_states_by_key: dict[str, dict[str, Any]],
    hooks: ApiViewHooks,
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if str(item.get("status", "")) in hooks.runnable_statuses:
            dep_resolution, _latest_dependency_states = hooks.dependency_resolution(
                config,
                item,
                latest_states_by_key=latest_states_by_key,
            )
            if dep_resolution:
                item["dependency_resolution"] = dep_resolution
                dependency_state = "ready" if all(bool(entry.get("satisfied", False)) for entry in dep_resolution) else "waiting"
                item["dependency_state"] = dependency_state
                if dependency_state != "ready":
                    first = next((entry for entry in dep_resolution if not bool(entry.get("satisfied", False))), dep_resolution[0])
                    item["blocked_reason"] = f"dependency:{first.get('task_key', '')}:{first.get('reason', 'waiting')}"
            else:
                item["dependency_state"] = "none"
        annotated.append(item)
    return annotated


def scheduler_active_cpu_budget(config: Any, rows: list[dict[str, Any]], *, hooks: ApiViewHooks) -> int:
    total = 0
    for row in rows:
        task_id = str(row.get("task_id", "")).strip()
        if not task_id:
            continue
        state = hooks.load_task_state(config, task_id)
        total += hooks.task_requested_cpu_budget(hooks.merged_spec_with_state(config, state or row))
    return total


def build_task_list_payload_for_api(
    config: Any,
    token_record: dict[str, Any],
    *,
    status_filter: str = "all",
    sort_mode: str = "queue",
    limit: int = 30,
    view: str = "tasks",
    hooks: ApiViewHooks,
) -> dict[str, Any]:
    normalized_view = str(view or "tasks").strip().lower() or "tasks"
    if normalized_view not in {"tasks", "queue"}:
        raise ValueError(f"invalid task list view: {normalized_view}")
    normalized_status_filter = str(status_filter or "all").strip().lower() or "all"
    if normalized_status_filter not in {"all", "active", "queued", "attention", "pending", "done"}:
        raise ValueError(f"invalid status filter: {normalized_status_filter}")
    if normalized_view == "queue":
        normalized_status_filter = "queued"
    normalized_sort_mode = str(sort_mode or "queue").strip().lower() or "queue"
    if normalized_sort_mode not in {"queue", "priority", "updated", "agent", "status"}:
        raise ValueError(f"invalid sort mode: {normalized_sort_mode}")
    resolved_limit = max(1, min(200, int(limit or 30)))

    all_rows = [
        reconcile_task_index_row(config, item, hooks=hooks)
        for item in hooks.task_index_rows(config)
        if not hooks.is_hidden_status(str(item.get("status", "")))
    ]
    latest_states_index = hooks.latest_task_states_by_key(all_rows)

    visible_rows: list[dict[str, Any]] = []
    for row in all_rows:
        task_id = hooks.normalize_task_id(str(row.get("task_id", "")).strip())
        if not task_id:
            continue
        if normalized_view == "queue":
            allowed = hooks.task_visible_in_api_queue(row, row, token_record)
        else:
            allowed = hooks.task_visible_to_api_token(row, row, token_record)
        if allowed:
            visible_rows.append(row)
    visible_rows = annotate_cached_queue_dependencies(
        config,
        visible_rows,
        latest_states_by_key=latest_states_index,
        hooks=hooks,
    )

    gpu_rows = hooks.get_gpu_summary_table()
    total_gpu_slots = hooks.detect_gpu_count() or len(gpu_rows)
    active_rows = [item for item in all_rows if str(item.get("status", "")) in hooks.active_task_statuses]
    active_cpu_threads = scheduler_active_cpu_budget(config, active_rows, hooks=hooks)
    active_gpu_slots = sum(int(item.get("gpu_slots", 0) or 0) for item in active_rows)
    cpu_thread_limit = hooks.detect_default_cpu_thread_limit()

    counts = Counter(str(item.get("status", "unknown")) for item in visible_rows)
    pending_feedback_count = sum(1 for item in visible_rows if bool(item.get("pending_feedback", False)))
    filtered_states = hooks.filter_dashboard_tasks(
        config,
        visible_rows,
        status_filter=normalized_status_filter,
        agent_filter="all",
    )
    sorted_states = hooks.sort_dashboard_tasks(config, filtered_states, normalized_sort_mode)
    queue_position_map = {
        str(item.get("task_id", "")): index
        for index, item in enumerate(
            hooks.sort_dashboard_tasks(
                config,
                [item for item in visible_rows if str(item.get("status", "")) in hooks.runnable_statuses],
                "queue",
            ),
            start=1,
        )
    }

    tasks: list[dict[str, Any]] = []
    queue_public_view = hooks.is_public_queue_view(token_record, normalized_view)
    for row in sorted_states[:resolved_limit]:
        task_id = str(row.get("task_id", ""))
        selected_state = dict(row)
        if task_id:
            state = hooks.load_task_state(config, task_id)
            if state:
                selected_state.update(state)
        item = hooks.enrich_task_state(
            config,
            selected_state,
            gpu_rows=gpu_rows,
            total_gpu_slots=total_gpu_slots,
            active_cpu_threads=active_cpu_threads,
            cpu_thread_limit=cpu_thread_limit,
            latest_states_by_key=latest_states_index,
        )
        task_id = str(item.get("task_id", ""))
        status = str(item.get("status", ""))
        blocked_reason = str(item.get("blocked_reason", "")) if status in hooks.runnable_statuses or status in hooks.active_task_statuses else ""
        issue_state = dict(item)
        issue_state["blocked_reason"] = blocked_reason
        queue_position = queue_position_map.get(task_id)
        if queue_public_view:
            tasks.append(
                {
                    "task_id": task_id,
                    "client_task_id": str(item.get("client_task_id", "")),
                    "status": status,
                    "lifecycle_state": str(item.get("lifecycle_state", "")),
                    "runtime_state": str(item.get("runtime_state", "")),
                    "phase": str(item.get("phase", "")),
                    "blocked_reason": blocked_reason,
                    "issue_text": str(hooks.dashboard_issue_text(issue_state)),
                    "priority": int(item.get("priority", 0) or 0),
                    "owner_tenant": str(item.get("owner_tenant", "")),
                    "gpu_slots": int(item.get("gpu_slots", 0) or 0),
                    "cpu_profile": str(item.get("cpu_profile", "")),
                    "cpu_profile_resolved": str(item.get("cpu_profile_resolved", "")),
                    "cpu_budget": int(item.get("cpu_budget", 0) or 0),
                    "automation_recommendation": str(item.get("automation_recommendation", "")),
                    "result_ready": hooks.is_terminal_status(status),
                    "submitted_at": str(item.get("submitted_at", "")),
                    "updated_at": str(item.get("updated_at", "")),
                    "queue_position_visible": queue_position,
                }
            )
        else:
            tasks.append(
                {
                    "task_id": task_id,
                    "task_key": str(item.get("task_key", "")),
                    "client_task_id": str(item.get("client_task_id", "")),
                    "client_task_key": str(item.get("client_task_key", "")),
                    "status": status,
                    "lifecycle_state": str(item.get("lifecycle_state", "")),
                    "runtime_state": str(item.get("runtime_state", "")),
                    "phase": str(item.get("phase", "")),
                    "blocked_reason": blocked_reason,
                    "issue_text": str(hooks.dashboard_issue_text(issue_state)),
                    "priority": int(item.get("priority", 0) or 0),
                    "agent_name": str(item.get("agent_name", "")),
                    "owner_tenant": str(item.get("owner_tenant", "")),
                    "owner_label": str(item.get("owner_label", "")),
                    "feedback_mode": str(item.get("feedback_mode", "")),
                    "execution_mode": str(item.get("execution_mode", "")),
                    "executor_name": str(item.get("executor_name", "")),
                    "workdir": str(item.get("workdir", "")),
                    "closeout_proposal_dir": str(item.get("closeout_proposal_dir", "")),
                    "gpu_slots": int(item.get("gpu_slots", 0) or 0),
                    "assigned_gpus": hooks.parse_gpu_id_list(item.get("assigned_gpus", [])),
                    "cpu_profile": str(item.get("cpu_profile", "")),
                    "cpu_profile_resolved": str(item.get("cpu_profile_resolved", "")),
                    "cpu_budget": int(item.get("cpu_budget", 0) or 0),
                    "pending_feedback": bool(item.get("pending_feedback", False)),
                    "platform_recovery": item.get("platform_recovery", {}),
                    "automation_recommendation": str(item.get("automation_recommendation", "")),
                    "result_ready": hooks.is_terminal_status(status),
                    "submitted_at": str(item.get("submitted_at", "")),
                    "updated_at": str(item.get("updated_at", "")),
                    "queue_position_visible": queue_position,
                }
            )

    return {
        "summary": {
            "view": normalized_view,
            "visibility_scope": hooks.build_api_visibility_scope(token_record, normalized_view),
            "status_filter": normalized_status_filter,
            "sort_mode": normalized_sort_mode,
            "limit": resolved_limit,
            "visible_tasks": len(visible_rows),
            "returned_tasks": len(tasks),
            "queued_tasks": int(counts.get("queued", 0) + counts.get("submitted", 0)),
            "active_tasks": sum(1 for item in visible_rows if str(item.get("status", "")) in hooks.active_task_statuses),
            "done_tasks": int(counts.get("completed", 0) + counts.get("observed_exit", 0)),
            "failed_tasks": int(counts.get("failed", 0) + counts.get("terminated", 0) + counts.get("launch_failed", 0)),
            "pending_feedback_tasks": pending_feedback_count,
            "scheduler_total_gpu_slots": int(total_gpu_slots or 0),
            "scheduler_active_gpu_slots": int(active_gpu_slots),
            "scheduler_cpu_thread_limit": int(cpu_thread_limit or 0),
            "scheduler_active_cpu_budget": int(active_cpu_threads),
        },
        "tasks": tasks,
    }


def wait_for_result_payload(
    config: Any,
    task_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
    token_record: dict[str, Any] | None = None,
    hooks: ApiViewHooks,
) -> dict[str, Any] | None:
    deadline = time.time() + timeout_seconds
    normalized_task_id = hooks.normalize_task_id(task_id)
    last_payload: dict[str, Any] | None = None
    while time.time() <= deadline:
        try:
            if token_record is None:
                payload = hooks.build_task_result_payload(config, normalized_task_id)
            else:
                payload = build_task_result_payload_for_api(
                    config,
                    normalized_task_id,
                    token_record,
                    hooks=hooks,
                )
        except (ValueError, PermissionError):
            payload = None
        if payload:
            last_payload = payload
            if bool(payload.get("result_ready", False)):
                return payload
        time.sleep(max(0.1, poll_seconds))
    return last_payload


def submit_job_for_api(
    config: Any,
    payload: dict[str, Any],
    token_record: dict[str, Any],
    *,
    hooks: ApiViewHooks,
) -> dict[str, Any]:
    forced_executor = str(token_record.get("executor", "")).strip()
    submitted_spec, hold = hooks.build_spec_from_submit_job_payload(
        config,
        payload,
        forced_executor=forced_executor,
        default_feedback_mode=str(token_record.get("default_feedback_mode", "off") or "off"),
        default_agent_name=str(token_record.get("agent_name", "")).strip(),
    )
    submitted_spec = hooks.apply_api_token_submit_policy(config, token_record, submitted_spec, payload)
    state = hooks.submit_spec(config, submitted_spec, hold=hold)
    return build_task_result_payload_for_api(
        config,
        str(state.get("task_id", submitted_spec.get("task_id", ""))),
        token_record,
        hooks=hooks,
    )
