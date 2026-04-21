from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codex_taskboard.scheduler_readiness import (
    SchedulerEnrichmentHooks,
    SchedulerReadinessHooks,
    dependency_resolution,
    enrich_task_state,
    evaluate_task_readiness,
    latest_task_state_for_key,
    latest_task_states_by_key,
    report_resolution,
)


@dataclass(frozen=True)
class DummyConfig:
    states: list[dict[str, Any]]


def make_hooks() -> SchedulerReadinessHooks:
    def resolve_cpu_thread_policy(spec: dict[str, Any], *, cpu_thread_limit: int = 0) -> dict[str, Any]:
        max_threads = int(spec.get("cpu_threads_max", spec.get("cpu_threads", 0)) or 0)
        return {
            "reservation_threads": int(spec.get("cpu_threads", 0) or 0),
            "mode": str(spec.get("cpu_threads_mode", "fixed") or "fixed"),
            "min_threads": int(spec.get("cpu_threads_min", 0) or 0),
            "max_threads": max_threads if max_threads > 0 else int(spec.get("cpu_threads", 0) or 0),
            "source": "test",
        }

    def resolve_cpu_worker_policy(spec: dict[str, Any], *, cpu_thread_limit: int = 0) -> dict[str, Any]:
        max_workers = int(spec.get("cpu_workers_max", spec.get("cpu_workers", 0)) or 0)
        return {
            "reservation_workers": int(spec.get("cpu_workers", 0) or 0),
            "mode": str(spec.get("cpu_workers_mode", "fixed") or "fixed"),
            "min_workers": int(spec.get("cpu_workers_min", 0) or 0),
            "max_workers": max_workers if max_workers > 0 else int(spec.get("cpu_workers", 0) or 0),
            "source": "test",
        }

    def select_gpu_ids_for_task(
        spec: dict[str, Any],
        *,
        total_gpu_slots: int,
        gpu_rows: list[dict[str, Any]],
        reserved_gpu_ids: set[int],
    ) -> tuple[list[int] | None, str]:
        gpu_slots = int(spec.get("gpu_slots", 0) or 0)
        if gpu_slots <= 0:
            return [], ""
        available = [int(row.get("index", -1)) for row in gpu_rows if int(row.get("index", -1)) not in reserved_gpu_ids]
        if len(available) < gpu_slots:
            return None, "no_gpu_headroom"
        return available[:gpu_slots], "scheduler"

    return SchedulerReadinessHooks(
        is_hidden_status=lambda status: status == "superseded",
        task_state_recency_key=lambda state: (float(state.get("updated_at", 0) or 0), str(state.get("task_id", ""))),
        iter_all_task_states=lambda config: list(config.states),
        newest_matches=lambda pattern, workdir: [Path(workdir) / pattern] if pattern == "artifact.json" else [],
        success_taskboard_signals={"TASK_DONE"},
        resolved_cpu_profile=lambda spec: str(spec.get("cpu_profile", "auto")),
        declared_cpu_profile=lambda spec: str(spec.get("cpu_profile", "auto")),
        resolve_cpu_thread_policy=resolve_cpu_thread_policy,
        resolve_cpu_worker_policy=resolve_cpu_worker_policy,
        coerce_non_negative_int=lambda raw: max(0, int(raw or 0)),
        select_gpu_ids_for_task=select_gpu_ids_for_task,
        gpu_row_free_mb=lambda row: max(0, int(row.get("memory_total_mb", 0) or 0) - int(row.get("memory_used_mb", 0) or 0)),
    )


def make_enrichment_hooks(
    specs: dict[str, dict[str, Any]],
    *,
    readiness_hooks: SchedulerReadinessHooks,
) -> SchedulerEnrichmentHooks:
    def parse_gpu_ids(raw: object) -> list[int]:
        if isinstance(raw, list):
            return [int(item) for item in raw]
        return []

    def task_lifecycle_state(state: dict[str, Any]) -> str:
        status = str(state.get("status", "")).strip()
        if bool(state.get("pending_feedback", False)):
            return "awaiting_feedback"
        if status in {"queued", "submitted"}:
            return "queued"
        if status in {"running", "watching"}:
            return "running"
        if status in {"completed", "observed_exit"}:
            return "completed"
        if status in {"failed", "terminated", "launch_failed"}:
            return "failed"
        return status or "unknown"

    def task_runtime_state(_config: DummyConfig, state: dict[str, Any]) -> str:
        lifecycle_state = task_lifecycle_state(state)
        if lifecycle_state == "running":
            return "watch_pid_live"
        if lifecycle_state == "queued":
            return "not_started"
        if lifecycle_state == "awaiting_feedback":
            return "awaiting_feedback"
        return "not_live"

    def task_automation_recommendation(state: dict[str, Any]) -> str:
        lifecycle_state = str(state.get("lifecycle_state", "")).strip() or task_lifecycle_state(state)
        if lifecycle_state in {"queued", "running"}:
            return "wait_for_live_task"
        return "safe_to_dispatch"

    return SchedulerEnrichmentHooks(
        load_task_spec=lambda _config, task_id: dict(specs.get(task_id, {})),
        normalize_task_spec_payload=lambda state: dict(state),
        parse_gpu_id_list=parse_gpu_ids,
        evaluate_task_readiness=lambda config, spec, **kwargs: evaluate_task_readiness(
            config,
            spec,
            hooks=readiness_hooks,
            **kwargs,
        ),
        task_lifecycle_state=task_lifecycle_state,
        task_runtime_state=task_runtime_state,
        task_has_launch_metadata=lambda state: bool(str(state.get("started_at", "")).strip()),
        task_platform_recovery_state=lambda _state: {"state": "none"},
        task_automation_recommendation=task_automation_recommendation,
        followup_entity_info=lambda _config, task_id: (task_id == "gpu-train", "followup-1" if task_id == "gpu-train" else ""),
    )


def test_latest_task_state_helpers_ignore_hidden_and_pick_newest() -> None:
    hooks = make_hooks()
    states = [
        {"task_id": "task-a-old", "task_key": "task-a", "status": "completed", "updated_at": "1"},
        {"task_id": "task-a-hidden", "task_key": "task-a", "status": "superseded", "updated_at": "99"},
        {"task_id": "task-a-new", "task_key": "task-a", "status": "completed", "updated_at": "2"},
    ]

    latest = latest_task_states_by_key(states, hooks=hooks)
    selected = latest_task_state_for_key(DummyConfig(states), "task-a", hooks=hooks)

    assert latest["task-a"]["task_id"] == "task-a-new"
    assert selected is not None
    assert selected["task_id"] == "task-a-new"


def test_dependency_and_report_resolution_use_preindexed_states() -> None:
    hooks = make_hooks()
    spec = {
        "depends_on": ["prep-task"],
        "required_report_conditions": ["quality=green"],
    }
    indexed = {
        "prep-task": {
            "task_id": "prep-task-001",
            "task_key": "prep-task",
            "status": "completed",
            "taskboard_signal": "TASK_DONE",
            "structured_report": {"quality": "green"},
        }
    }

    dep_resolution, latest_states = dependency_resolution(
        DummyConfig([]),
        spec,
        hooks=hooks,
        latest_states_by_key=indexed,
    )
    report_req = report_resolution(spec, latest_states)

    assert dep_resolution[0]["satisfied"] is True
    assert dep_resolution[0]["resolved_task_id"] == "prep-task-001"
    assert report_req[0]["satisfied"] is True
    assert report_req[0]["resolved_task_id"] == "prep-task-001"


def test_evaluate_task_readiness_reports_cpu_and_gpu_constraints() -> None:
    hooks = make_hooks()
    spec = {
        "task_id": "gpu-train",
        "cpu_profile": "single",
        "cpu_threads": 4,
        "gpu_slots": 1,
        "depends_on": ["prep-task"],
        "required_artifact_globs": ["artifact.json"],
        "required_report_conditions": ["quality=green"],
        "workdir": "/tmp/demo",
    }
    latest_states = {
        "prep-task": {
            "task_id": "prep-task-001",
            "task_key": "prep-task",
            "status": "completed",
            "taskboard_signal": "TASK_DONE",
            "structured_report": {"quality": "green"},
        }
    }
    gpu_rows = [{"index": 0, "memory_total_mb": 24000, "memory_used_mb": 1000, "gpu_util_percent": 10}]

    cpu_blocked = evaluate_task_readiness(
        DummyConfig([]),
        spec,
        hooks=hooks,
        latest_states_by_key=latest_states,
        gpu_rows=gpu_rows,
        total_gpu_slots=1,
        cpu_thread_limit=2,
    )
    ready = evaluate_task_readiness(
        DummyConfig([]),
        spec,
        hooks=hooks,
        latest_states_by_key=latest_states,
        gpu_rows=gpu_rows,
        total_gpu_slots=1,
        cpu_thread_limit=8,
    )

    assert cpu_blocked["blocked_reason"].startswith("cpu_budget:")
    assert cpu_blocked["artifact_state"] == "ready"
    assert ready["blocked_reason"] == ""
    assert ready["eligible_gpu_ids"] == [0]
    assert ready["gpu_assignment_source"] == "scheduler"


def test_enrich_task_state_adds_dispatch_diagnostics_and_followup_audit() -> None:
    readiness_hooks = make_hooks()
    specs = {
        "gpu-train": {
            "task_id": "gpu-train",
            "task_key": "gpu-train",
            "workdir": "/tmp/demo",
            "command": "python train.py",
            "cpu_profile": "single",
            "cpu_threads": 4,
            "gpu_slots": 1,
            "depends_on": ["prep-task"],
            "required_artifact_globs": ["artifact.json"],
            "required_report_conditions": ["quality=green"],
        }
    }
    state = {
        "task_id": "gpu-train",
        "task_key": "gpu-train",
        "status": "queued",
        "followup_status": "scheduled",
    }
    latest_states = {
        "prep-task": {
            "task_id": "prep-task-001",
            "task_key": "prep-task",
            "status": "completed",
            "taskboard_signal": "TASK_DONE",
            "structured_report": {"quality": "green"},
        }
    }
    gpu_rows = [{"index": 0, "memory_total_mb": 24000, "memory_used_mb": 1000, "gpu_util_percent": 10}]

    enriched = enrich_task_state(
        DummyConfig([]),
        state,
        hooks=make_enrichment_hooks(specs, readiness_hooks=readiness_hooks),
        gpu_rows=gpu_rows,
        total_gpu_slots=1,
        cpu_thread_limit=8,
        latest_states_by_key=latest_states,
    )

    assert enriched["phase"] == "eligible"
    assert enriched["blocked_reason"] == ""
    assert enriched["eligible_gpu_ids"] == [0]
    assert enriched["dispatch_diagnostics"]["scheduler_state"] == "eligible"
    assert enriched["followup_entity_present"] is True
    assert enriched["followup_entity_key"] == "followup-1"
    assert enriched["followup_audit_status"] == "live_entity_present"
    assert enriched["automation_recommendation"] == "wait_for_live_task"
