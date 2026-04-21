from __future__ import annotations

from codex_taskboard.scheduler import (
    SchedulerDispatchHooks,
    SchedulerSubmitHooks,
    dispatch_queued_tasks_unlocked,
    finalize_submitted_task,
    reserve_cpu_threads_for_later_tasks,
)


def parse_gpu_id_list(raw: object) -> list[int]:
    if isinstance(raw, list):
        return [int(item) for item in raw]
    return []


def make_hooks(*, started_calls: list[dict[str, object]]) -> SchedulerDispatchHooks:
    states = [
        {"task_id": "gpu-task", "status": "queued", "priority": 10, "submitted_at": "1", "gpu_slots": 1, "cpu_threads": 2},
        {"task_id": "cpu-task", "status": "queued", "priority": 5, "submitted_at": "2", "gpu_slots": 0, "cpu_threads": 3},
    ]

    def evaluate_task_readiness(
        _config: object,
        spec: dict[str, object],
        *,
        gpu_rows=None,
        total_gpu_slots=0,
        reserved_gpu_ids=None,
        active_cpu_threads=0,
        reserved_cpu_threads=0,
        cpu_thread_limit=0,
        latest_states_by_key=None,
    ) -> dict[str, object]:
        cpu_budget = int(spec.get("cpu_threads", 0) or 0)
        available = max(0, int(cpu_thread_limit or 0) - int(active_cpu_threads or 0) - int(reserved_cpu_threads or 0))
        blocked_reason = ""
        eligible_gpu_ids: list[int] = []
        if int(spec.get("gpu_slots", 0) or 0) > 0:
            chosen = [0]
            if reserved_gpu_ids and 0 in reserved_gpu_ids:
                blocked_reason = "gpu_headroom:reserved"
            else:
                eligible_gpu_ids = chosen
        if not blocked_reason and cpu_budget > 0 and int(cpu_thread_limit or 0) > 0 and cpu_budget > available:
            blocked_reason = f"cpu_budget:need={cpu_budget}:available={available}"
        return {
            "blocked_reason": blocked_reason,
            "eligible_gpu_ids": eligible_gpu_ids,
            "gpu_assignment_source": "scheduler" if eligible_gpu_ids else "",
            "available_cpu_threads": available,
            "cpu_budget": cpu_budget,
            "cpu_threads_mode": str(spec.get("cpu_threads_mode", "fixed") or "fixed"),
            "cpu_thread_source": "test",
            "cpu_worker_source": "test",
            "cpu_worker_policy": {"mode": str(spec.get("cpu_workers_mode", "fixed") or "fixed")},
        }

    def select_cpu_resources_for_start(
        spec: dict[str, object],
        *,
        available_cpu_threads: int,
        reserve_for_other_tasks: int = 0,
    ) -> dict[str, int | str]:
        desired = int(spec.get("cpu_threads", 0) or 0)
        assigned = max(0, min(desired, max(0, available_cpu_threads - reserve_for_other_tasks)))
        return {
            "assigned_cpu_threads": assigned,
            "assigned_cpu_workers": 0,
            "assigned_cpu_budget": assigned,
            "cpu_thread_source": "test",
            "cpu_worker_source": "test",
        }

    def start_existing_task(_config: object, task_id: str, **kwargs: object) -> dict[str, object]:
        started_calls.append({"task_id": task_id, **kwargs})
        return {"task_id": task_id, "status": "running"}

    return SchedulerDispatchHooks(
        iter_task_states=lambda _config: list(states),
        count_live_running_tasks=lambda _config, _states: 0,
        detect_default_cpu_thread_limit=lambda: 8,
        task_requested_cpu_budget=lambda spec: int(spec.get("cpu_threads", 0) or 0),
        merged_spec_with_state=lambda _config, state: dict(state),
        detect_gpu_count=lambda: 1,
        parse_gpu_id_list=parse_gpu_id_list,
        get_gpu_summary_table=lambda: [{"index": 0, "memory_total_mb": 24000, "memory_used_mb": 1000, "gpu_util_percent": 10}],
        active_task_statuses={"running", "watching"},
        runnable_statuses={"queued", "submitted"},
        timestamp_sort_value=lambda value, *, missing: missing if value in {None, ""} else float(value),
        evaluate_task_readiness=evaluate_task_readiness,
        select_cpu_resources_for_start=select_cpu_resources_for_start,
        start_existing_task=start_existing_task,
        selected_gpu_snapshot=lambda _gpu_rows, gpu_ids: [{"index": gpu_id} for gpu_id in gpu_ids],
    )


def test_reserve_cpu_threads_for_later_tasks_counts_ready_future_budget() -> None:
    hooks = make_hooks(started_calls=[])
    queued = [
        ({"task_id": "cpu-1"}, {"task_id": "cpu-1", "cpu_threads": 2}),
        ({"task_id": "cpu-2"}, {"task_id": "cpu-2", "cpu_threads": 3}),
    ]

    reserve = reserve_cpu_threads_for_later_tasks(
        object(),
        queued,
        hooks=hooks,
        start_index=0,
        gpu_rows=None,
        total_gpu_slots=0,
        reserved_gpu_ids=set(),
        active_cpu_threads=0,
        reserved_cpu_threads=0,
        cpu_thread_limit=8,
    )

    assert reserve == 3


def test_dispatch_queued_tasks_unlocked_starts_ready_tasks_and_records_assignments() -> None:
    started_calls: list[dict[str, object]] = []
    hooks = make_hooks(started_calls=started_calls)

    result = dispatch_queued_tasks_unlocked(
        object(),
        hooks=hooks,
        mode="gpu-fill",
        max_running=0,
        limit=4,
        gpu_count_override=1,
        cpu_thread_limit=8,
    )

    assert result["started"] == ["gpu-task", "cpu-task"]
    assert result["placements"] == {"gpu-task": [0]}
    assert result["cpu_assignments"] == {"gpu-task": 2, "cpu-task": 3}
    assert started_calls[0]["task_id"] == "gpu-task"
    assert started_calls[0]["assigned_gpus"] == [0]
    assert started_calls[1]["task_id"] == "cpu-task"


def make_submit_hooks(
    *,
    started_calls: list[dict[str, object]],
    merged_calls: list[dict[str, object]],
    blocked_reason: str = "",
) -> SchedulerSubmitHooks:
    def evaluate_task_readiness(
        _config: object,
        _spec: dict[str, object],
        *,
        gpu_rows=None,
        total_gpu_slots=0,
        reserved_gpu_ids=None,
        active_cpu_threads=0,
        reserved_cpu_threads=0,
        cpu_thread_limit=0,
        latest_states_by_key=None,
    ) -> dict[str, object]:
        available = max(0, int(cpu_thread_limit or 0) - int(active_cpu_threads or 0))
        return {
            "blocked_reason": blocked_reason,
            "eligible_gpu_ids": [] if blocked_reason else [0],
            "gpu_assignment_source": "" if blocked_reason else "scheduler",
            "available_cpu_threads": available,
            "cpu_thread_source": "submit-test",
            "cpu_worker_source": "submit-test",
        }

    def enrich_task_state(
        _config: object,
        state: dict[str, object],
        *,
        gpu_rows=None,
        total_gpu_slots=0,
        reserved_gpu_ids=None,
        active_cpu_threads=0,
        reserved_cpu_threads=0,
        cpu_thread_limit=0,
        latest_states_by_key=None,
    ) -> dict[str, object]:
        payload = dict(state)
        payload.setdefault("lifecycle_state", "running" if str(payload.get("status", "")) == "running" else "queued")
        return payload

    def start_existing_task(_config: object, task_id: str, **kwargs: object) -> dict[str, object]:
        started_calls.append({"task_id": task_id, **kwargs})
        return {"task_id": task_id, "status": "running", **kwargs}

    def merge_task_state(_config: object, task_id: str, **kwargs: object) -> dict[str, object]:
        merged_calls.append({"task_id": task_id, **kwargs})
        return {"task_id": task_id, "status": str(kwargs.get("status", ""))}

    return SchedulerSubmitHooks(
        iter_task_states=lambda _config: [{"task_id": "active-1", "status": "running", "cpu_threads": 2}],
        active_task_statuses={"running", "watching"},
        task_requested_cpu_budget=lambda spec: int(spec.get("cpu_threads", 0) or 0),
        merged_spec_with_state=lambda _config, state: dict(state),
        parse_gpu_id_list=parse_gpu_id_list,
        get_gpu_summary_table=lambda: [{"index": 0, "memory_total_mb": 24000, "memory_used_mb": 1000, "gpu_util_percent": 10}],
        detect_gpu_count=lambda: 1,
        detect_default_cpu_thread_limit=lambda: 8,
        evaluate_task_readiness=evaluate_task_readiness,
        enrich_task_state=enrich_task_state,
        select_cpu_resources_for_start=lambda _spec, *, available_cpu_threads: {
            "assigned_cpu_threads": min(2, available_cpu_threads),
            "assigned_cpu_workers": 0,
            "cpu_thread_source": "submit-test",
            "cpu_worker_source": "submit-test",
        },
        start_existing_task=start_existing_task,
        selected_gpu_snapshot=lambda _gpu_rows, gpu_ids: [{"index": gpu_id} for gpu_id in gpu_ids],
        merge_task_state=merge_task_state,
    )


def test_finalize_submitted_task_keeps_hold_without_dispatch() -> None:
    started_calls: list[dict[str, object]] = []
    merged_calls: list[dict[str, object]] = []
    hooks = make_submit_hooks(started_calls=started_calls, merged_calls=merged_calls)

    result = finalize_submitted_task(
        object(),
        {"task_id": "held-task", "cpu_threads": 2},
        {"task_id": "held-task", "status": "submitted"},
        hold=True,
        hooks=hooks,
    )

    assert result["held"] is True
    assert result["submitted_and_dispatch_attempted"] is False
    assert started_calls == []
    assert merged_calls == []


def test_finalize_submitted_task_queues_when_readiness_is_blocked() -> None:
    started_calls: list[dict[str, object]] = []
    merged_calls: list[dict[str, object]] = []
    hooks = make_submit_hooks(
        started_calls=started_calls,
        merged_calls=merged_calls,
        blocked_reason="cpu_budget:need=4:available=1",
    )

    result = finalize_submitted_task(
        object(),
        {"task_id": "blocked-task", "cpu_threads": 4},
        {"task_id": "blocked-task", "status": "submitted"},
        hold=False,
        hooks=hooks,
    )

    assert result["held"] is False
    assert result["submitted_and_dispatch_attempted"] is False
    assert started_calls == []
    assert merged_calls == [{"task_id": "blocked-task", "status": "queued"}]
    assert result["status"] == "queued"


def test_finalize_submitted_task_starts_ready_submission() -> None:
    started_calls: list[dict[str, object]] = []
    merged_calls: list[dict[str, object]] = []
    hooks = make_submit_hooks(started_calls=started_calls, merged_calls=merged_calls)

    result = finalize_submitted_task(
        object(),
        {"task_id": "ready-task", "cpu_threads": 2},
        {"task_id": "ready-task", "status": "submitted"},
        hold=False,
        hooks=hooks,
    )

    assert result["held"] is False
    assert result["submitted_and_dispatch_attempted"] is True
    assert merged_calls == []
    assert started_calls[0]["task_id"] == "ready-task"
    assert started_calls[0]["assigned_gpus"] == [0]
    assert started_calls[0]["assigned_cpu_threads"] == 2
