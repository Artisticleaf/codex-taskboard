from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SchedulerDispatchHooks:
    iter_task_states: Callable[[Any], list[dict[str, Any]]]
    count_live_running_tasks: Callable[[Any, list[dict[str, Any]]], int]
    detect_default_cpu_thread_limit: Callable[[], int]
    task_requested_cpu_budget: Callable[[dict[str, Any]], int]
    merged_spec_with_state: Callable[[Any, dict[str, Any]], dict[str, Any]]
    detect_gpu_count: Callable[[], int]
    parse_gpu_id_list: Callable[[Any], list[int]]
    get_gpu_summary_table: Callable[[], list[dict[str, Any]]]
    active_task_statuses: set[str]
    runnable_statuses: set[str]
    timestamp_sort_value: Callable[..., float]
    evaluate_task_readiness: Callable[..., dict[str, Any]]
    select_cpu_resources_for_start: Callable[..., dict[str, int | str]]
    start_existing_task: Callable[..., dict[str, Any]]
    selected_gpu_snapshot: Callable[[list[dict[str, Any]], list[int]], list[dict[str, Any]]]


@dataclass(frozen=True)
class SchedulerSubmitHooks:
    iter_task_states: Callable[[Any], list[dict[str, Any]]]
    active_task_statuses: set[str]
    task_requested_cpu_budget: Callable[[dict[str, Any]], int]
    merged_spec_with_state: Callable[[Any, dict[str, Any]], dict[str, Any]]
    parse_gpu_id_list: Callable[[Any], list[int]]
    get_gpu_summary_table: Callable[[], list[dict[str, Any]]]
    detect_gpu_count: Callable[[], int]
    detect_default_cpu_thread_limit: Callable[[], int]
    evaluate_task_readiness: Callable[..., dict[str, Any]]
    enrich_task_state: Callable[..., dict[str, Any]]
    select_cpu_resources_for_start: Callable[..., dict[str, int | str]]
    start_existing_task: Callable[..., dict[str, Any]]
    selected_gpu_snapshot: Callable[[list[dict[str, Any]], list[int]], list[dict[str, Any]]]
    merge_task_state: Callable[..., dict[str, Any]]


def reserve_cpu_threads_for_later_tasks(
    config: Any,
    queued_items: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    hooks: SchedulerDispatchHooks,
    start_index: int,
    gpu_rows: list[dict[str, Any]] | None,
    total_gpu_slots: int,
    reserved_gpu_ids: set[int],
    active_cpu_threads: int,
    reserved_cpu_threads: int,
    cpu_thread_limit: int,
) -> int:
    reserve = 0
    future_reserved_gpu_ids = set(reserved_gpu_ids)
    for _future_state, future_spec in queued_items[start_index + 1 :]:
        readiness = hooks.evaluate_task_readiness(
            config,
            future_spec,
            gpu_rows=gpu_rows,
            total_gpu_slots=total_gpu_slots,
            reserved_gpu_ids=future_reserved_gpu_ids,
            active_cpu_threads=active_cpu_threads,
            reserved_cpu_threads=reserved_cpu_threads + reserve,
            cpu_thread_limit=cpu_thread_limit,
        )
        if readiness["blocked_reason"]:
            continue
        required_budget = max(0, int(readiness.get("cpu_budget", 0) or 0))
        if required_budget > 0:
            reserve += required_budget
        eligible_gpu_ids = hooks.parse_gpu_id_list(readiness.get("eligible_gpu_ids", []))
        if eligible_gpu_ids:
            future_reserved_gpu_ids.update(eligible_gpu_ids)
    return reserve


def dispatch_queued_tasks_unlocked(
    config: Any,
    *,
    hooks: SchedulerDispatchHooks,
    mode: str,
    max_running: int,
    limit: int,
    gpu_count_override: int,
    cpu_thread_limit: int,
) -> dict[str, Any]:
    states = hooks.iter_task_states(config)
    running_count = hooks.count_live_running_tasks(config, states)
    active_states = [state for state in states if str(state.get("status", "")) in hooks.active_task_statuses]
    resolved_cpu_thread_limit = int(cpu_thread_limit or 0) if int(cpu_thread_limit or 0) > 0 else hooks.detect_default_cpu_thread_limit()
    active_cpu_threads = sum(hooks.task_requested_cpu_budget(hooks.merged_spec_with_state(config, state)) for state in active_states)
    total_gpu_slots = gpu_count_override if gpu_count_override > 0 else hooks.detect_gpu_count()
    active_gpu_slots = sum(int(state.get("gpu_slots", 0) or 0) for state in active_states)
    active_reserved_gpu_ids: set[int] = set()
    for state in active_states:
        active_reserved_gpu_ids.update(hooks.parse_gpu_id_list(state.get("assigned_gpus", [])))
    gpu_rows = hooks.get_gpu_summary_table()
    if gpu_count_override > 0 and gpu_rows:
        gpu_rows = [row for row in gpu_rows if int(row.get("index", -1)) < gpu_count_override]
    headroom_scheduler = mode == "gpu-fill" and total_gpu_slots > 0 and bool(gpu_rows)
    if mode == "serial":
        capacity = 0 if running_count > 0 else 1
    elif mode == "gpu-fill":
        hard_task_limit = max_running if max_running > 0 else max(limit, 1)
        capacity = max(0, hard_task_limit - running_count)
    else:
        raise ValueError(f"Unsupported dispatch mode: {mode}")
    started: list[str] = []
    errors: list[str] = []
    placements: dict[str, list[int]] = {}
    if capacity <= 0:
        return {
            "started": started,
            "errors": errors,
            "running_count": running_count,
            "capacity": capacity,
            "mode": mode,
            "active_gpu_slots": active_gpu_slots,
            "total_gpu_slots": total_gpu_slots,
            "active_cpu_threads": active_cpu_threads,
            "cpu_thread_limit": resolved_cpu_thread_limit,
            "headroom_scheduler": headroom_scheduler,
            "placements": placements,
            "cpu_assignments": {},
        }
    queued = [
        (
            state,
            hooks.merged_spec_with_state(config, state),
        )
        for state in sorted(
            states,
            key=lambda item: (
                -int(item.get("priority", 0) or 0),
                hooks.timestamp_sort_value(item.get("submitted_at"), missing=float("inf")),
                str(item.get("task_id", "")),
            ),
        )
        if str(state.get("status", "")) in hooks.runnable_statuses
    ]
    reserved_gpu_ids: set[int] = set(active_reserved_gpu_ids)
    reserved_cpu_threads = 0
    remaining_gpu_slots = max(0, total_gpu_slots - active_gpu_slots) if total_gpu_slots > 0 else 0
    started_limit = min(capacity, limit)
    cpu_assignments: dict[str, int] = {}
    for index, (state, spec) in enumerate(queued):
        if len(started) >= started_limit:
            break
        task_id = str(state["task_id"])
        readiness = hooks.evaluate_task_readiness(
            config,
            spec,
            gpu_rows=gpu_rows if headroom_scheduler else None,
            total_gpu_slots=total_gpu_slots,
            reserved_gpu_ids=reserved_gpu_ids,
            active_cpu_threads=active_cpu_threads,
            reserved_cpu_threads=reserved_cpu_threads,
            cpu_thread_limit=resolved_cpu_thread_limit,
        )
        if readiness["blocked_reason"]:
            continue
        gpu_slots = int(state.get("gpu_slots", 0) or 0)
        assigned_gpus: list[int] | None = readiness["eligible_gpu_ids"] or None
        assignment_source = readiness["gpu_assignment_source"]
        cpu_assignment = hooks.select_cpu_resources_for_start(
            spec,
            available_cpu_threads=int(readiness.get("available_cpu_threads", 0) or 0),
        )
        assigned_cpu_threads = int(cpu_assignment.get("assigned_cpu_threads", 0) or 0)
        assigned_cpu_workers = int(cpu_assignment.get("assigned_cpu_workers", 0) or 0)
        assigned_cpu_budget = int(cpu_assignment.get("assigned_cpu_budget", 0) or 0)
        cpu_assignment_source = str(cpu_assignment.get("cpu_thread_source", "") or readiness.get("cpu_thread_source", "") or "default")
        cpu_worker_assignment_source = str(cpu_assignment.get("cpu_worker_source", "") or readiness.get("cpu_worker_source", "") or "default")
        if (
            gpu_slots <= 0
            and (
                str(readiness.get("cpu_threads_mode", "fixed")) == "adaptive"
                or str(readiness.get("cpu_worker_policy", {}).get("mode", "fixed")) == "adaptive"
            )
        ):
            reserve_for_later = reserve_cpu_threads_for_later_tasks(
                config,
                queued,
                hooks=hooks,
                start_index=index,
                gpu_rows=gpu_rows if headroom_scheduler else None,
                total_gpu_slots=total_gpu_slots,
                reserved_gpu_ids=reserved_gpu_ids,
                active_cpu_threads=active_cpu_threads,
                reserved_cpu_threads=reserved_cpu_threads,
                cpu_thread_limit=resolved_cpu_thread_limit,
            )
            cpu_assignment = hooks.select_cpu_resources_for_start(
                spec,
                available_cpu_threads=int(readiness.get("available_cpu_threads", 0) or 0),
                reserve_for_other_tasks=reserve_for_later,
            )
            assigned_cpu_threads = int(cpu_assignment.get("assigned_cpu_threads", 0) or 0)
            assigned_cpu_workers = int(cpu_assignment.get("assigned_cpu_workers", 0) or 0)
            assigned_cpu_budget = int(cpu_assignment.get("assigned_cpu_budget", 0) or 0)
            cpu_assignment_source = str(cpu_assignment.get("cpu_thread_source", "") or readiness.get("cpu_thread_source", "") or "default")
            cpu_worker_assignment_source = str(cpu_assignment.get("cpu_worker_source", "") or readiness.get("cpu_worker_source", "") or "default")
        if mode == "gpu-fill" and gpu_slots > 0 and not headroom_scheduler and total_gpu_slots > 0 and gpu_slots > remaining_gpu_slots:
            continue
        try:
            if assigned_gpus is not None or assignment_source:
                hooks.start_existing_task(
                    config,
                    task_id,
                    assigned_gpus=assigned_gpus,
                    assignment_source=assignment_source,
                    assigned_cpu_threads=assigned_cpu_threads or None,
                    assigned_cpu_workers=assigned_cpu_workers or None,
                    cpu_assignment_source=cpu_assignment_source,
                    cpu_worker_assignment_source=cpu_worker_assignment_source,
                    why_started=f"dispatch_{mode}",
                    dispatch_gpu_snapshot=hooks.selected_gpu_snapshot(gpu_rows, assigned_gpus or []),
                )
            else:
                hooks.start_existing_task(
                    config,
                    task_id,
                    assigned_cpu_threads=assigned_cpu_threads or None,
                    assigned_cpu_workers=assigned_cpu_workers or None,
                    cpu_assignment_source=cpu_assignment_source,
                    cpu_worker_assignment_source=cpu_worker_assignment_source,
                    why_started=f"dispatch_{mode}",
                )
            started.append(task_id)
            if assigned_gpus:
                placements[task_id] = assigned_gpus
                reserved_gpu_ids.update(assigned_gpus)
            reserved_cpu_threads += assigned_cpu_budget or int(readiness.get("cpu_budget", 0) or 0)
            if assigned_cpu_budget:
                cpu_assignments[task_id] = assigned_cpu_budget
            if mode == "gpu-fill" and total_gpu_slots > 0 and not headroom_scheduler:
                remaining_gpu_slots = max(0, remaining_gpu_slots - gpu_slots)
        except Exception as exc:
            errors.append(f"{task_id}: {exc}")
    return {
        "started": started,
        "errors": errors,
        "running_count": running_count,
        "capacity": capacity,
        "mode": mode,
        "active_gpu_slots": active_gpu_slots,
        "total_gpu_slots": total_gpu_slots,
        "active_cpu_threads": active_cpu_threads,
        "cpu_thread_limit": resolved_cpu_thread_limit,
        "headroom_scheduler": headroom_scheduler,
        "placements": placements,
        "cpu_assignments": cpu_assignments,
    }


def finalize_submitted_task(
    config: Any,
    spec: dict[str, Any],
    state: dict[str, Any],
    *,
    hold: bool,
    hooks: SchedulerSubmitHooks,
) -> dict[str, Any]:
    gpu_rows = hooks.get_gpu_summary_table()
    total_gpu_slots = hooks.detect_gpu_count() or len(gpu_rows)
    active_states = [
        item
        for item in hooks.iter_task_states(config)
        if str(item.get("status", "")) in hooks.active_task_statuses
    ]
    active_cpu_threads = sum(
        hooks.task_requested_cpu_budget(hooks.merged_spec_with_state(config, item))
        for item in active_states
    )
    cpu_thread_limit = hooks.detect_default_cpu_thread_limit()
    if hold:
        response = hooks.enrich_task_state(
            config,
            state,
            gpu_rows=gpu_rows,
            total_gpu_slots=total_gpu_slots,
            active_cpu_threads=active_cpu_threads,
            cpu_thread_limit=cpu_thread_limit,
        )
        response["held"] = True
        response["submitted_and_dispatch_attempted"] = False
        return response
    active_reserved_gpu_ids: set[int] = set()
    for item in active_states:
        active_reserved_gpu_ids.update(hooks.parse_gpu_id_list(item.get("assigned_gpus", [])))
    readiness = hooks.evaluate_task_readiness(
        config,
        spec,
        gpu_rows=gpu_rows,
        total_gpu_slots=total_gpu_slots,
        reserved_gpu_ids=active_reserved_gpu_ids,
        active_cpu_threads=active_cpu_threads,
        cpu_thread_limit=cpu_thread_limit,
    )
    if readiness["blocked_reason"]:
        queued_state = hooks.merge_task_state(config, str(state.get("task_id", "")), status="queued")
        response = hooks.enrich_task_state(
            config,
            queued_state,
            gpu_rows=gpu_rows,
            total_gpu_slots=total_gpu_slots,
            active_cpu_threads=active_cpu_threads,
            cpu_thread_limit=cpu_thread_limit,
        )
        response["held"] = False
        response["submitted_and_dispatch_attempted"] = False
        return response
    cpu_assignment = hooks.select_cpu_resources_for_start(
        spec,
        available_cpu_threads=int(readiness.get("available_cpu_threads", 0) or 0),
    )
    started_state = hooks.start_existing_task(
        config,
        str(state.get("task_id", "")),
        assigned_gpus=readiness["eligible_gpu_ids"] or None,
        assignment_source=readiness["gpu_assignment_source"],
        assigned_cpu_threads=int(cpu_assignment.get("assigned_cpu_threads", 0) or 0) or None,
        assigned_cpu_workers=int(cpu_assignment.get("assigned_cpu_workers", 0) or 0) or None,
        cpu_assignment_source=str(
            cpu_assignment.get("cpu_thread_source", "")
            or readiness.get("cpu_thread_source", "")
            or "default"
        ),
        cpu_worker_assignment_source=str(
            cpu_assignment.get("cpu_worker_source", "")
            or readiness.get("cpu_worker_source", "")
            or "default"
        ),
        why_started="submit_no_blockers",
        dispatch_gpu_snapshot=(
            hooks.selected_gpu_snapshot(gpu_rows, readiness["eligible_gpu_ids"])
            if readiness["eligible_gpu_ids"]
            else []
        ),
    )
    response = hooks.enrich_task_state(
        config,
        started_state,
        gpu_rows=gpu_rows,
        total_gpu_slots=total_gpu_slots,
        active_cpu_threads=active_cpu_threads,
        cpu_thread_limit=cpu_thread_limit,
    )
    response["held"] = False
    response["submitted_and_dispatch_attempted"] = True
    return response
