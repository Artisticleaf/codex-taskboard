from __future__ import annotations

from codex_taskboard.task_dashboard import (
    TaskDashboardHooks,
    build_dashboard_task_entries,
    dashboard_issue_text,
    filter_dashboard_tasks,
    sort_dashboard_tasks,
)
from codex_taskboard.task_results import TaskResultHooks, build_task_result_payload


class DummyConfig:
    pass


def make_dashboard_hooks() -> TaskDashboardHooks:
    return TaskDashboardHooks(
        active_task_statuses={"running", "watching"},
        runnable_statuses={"queued", "submitted"},
        timestamp_sort_value=lambda value, *, missing: missing if value in {None, ""} else float(value),
        task_list_sort_key=lambda item: (0, int(item.get("priority", 0) or 0), 0.0, str(item.get("task_id", ""))),
        state_has_unresolved_dependencies=lambda _config, item: bool(item.get("blocked_reason")),
        dashboard_short_time=lambda value: str(value or "-"),
        dashboard_trim=lambda text, width: str(text or "")[:width],
    )


def test_task_dashboard_filter_sort_and_entries() -> None:
    hooks = make_dashboard_hooks()
    config = DummyConfig()
    states = [
        {"task_id": "done-1", "status": "completed", "priority": 0, "updated_at": "3"},
        {"task_id": "queued-ready", "status": "queued", "priority": 1, "updated_at": "2"},
        {"task_id": "running-1", "status": "running", "priority": 0, "updated_at": "4"},
        {"task_id": "queued-blocked", "status": "queued", "priority": 5, "blocked_reason": "dependency:x:waiting", "updated_at": "1"},
    ]

    queued_only = filter_dashboard_tasks(states, status_filter="queued", agent_filter="all", hooks=hooks)
    assert [item["task_id"] for item in queued_only] == ["queued-ready", "queued-blocked"]

    ordered = sort_dashboard_tasks(config, states, "queue", hooks=hooks)
    assert [item["task_id"] for item in ordered[:3]] == ["running-1", "queued-ready", "queued-blocked"]

    entries = build_dashboard_task_entries(
        config,
        ordered,
        sort_mode="queue",
        status_filter="all",
        agent_filter="all",
        limit=3,
        already_ordered=True,
        hooks=hooks,
    )
    assert [entry["task_id"] for entry in entries] == ["running-1", "queued-ready", "queued-blocked"]
    assert entries[2]["issue_text"] == "blocked dependency:x:waiting"


def test_dashboard_issue_text_prefers_attention_then_failure_then_phase() -> None:
    assert dashboard_issue_text({"pending_feedback": True}) == "pending_feedback"
    assert dashboard_issue_text({"needs_attention": True, "attention_reason": "startup_failure"}) == "startup_failure"
    assert dashboard_issue_text({"failure_kind": "launch_failed"}) == "launch_failed"
    assert dashboard_issue_text({"phase": "waiting_report"}) == "waiting_report"


def make_result_hooks() -> TaskResultHooks:
    state = {
        "task_id": "demo-task",
        "task_key": "demo-task",
        "status": "completed",
        "lifecycle_state": "completed",
        "runtime_state": "not_live",
        "phase": "completed",
        "submitted_at": "2026-04-21T00:00:00+08:00",
        "updated_at": "2026-04-21T00:00:10+08:00",
        "cpu_profile_resolved": "cpu_compute",
        "dispatch_diagnostics": {"scheduler_state": "historical_after_launch"},
        "launch_diagnostics": {"launch_state": "finished"},
        "platform_recovery": {"state": "none"},
        "automation_recommendation": "none",
    }
    spec = {
        "task_id": "demo-task",
        "task_key": "demo-task",
        "execution_mode": "shell",
        "workdir": "/tmp",
        "command": "printf hi",
        "cpu_profile": "auto",
        "gpu_slots": 0,
    }
    return TaskResultHooks(
        normalize_task_id=lambda value: str(value).strip(),
        load_task_state=lambda _config, _task_id: dict(state),
        get_gpu_summary_table=lambda: [],
        detect_gpu_count=lambda: 0,
        iter_task_states=lambda _config: [dict(state)],
        active_task_statuses={"running", "watching"},
        task_requested_cpu_budget=lambda _spec: 0,
        merged_spec_with_state=lambda _config, current_state: dict(current_state),
        enrich_task_state=lambda _config, current_state, **_kwargs: dict(current_state),
        detect_default_cpu_thread_limit=lambda: 40,
        load_task_spec=lambda _config, _task_id: dict(spec),
        is_terminal_status=lambda status: status == "completed",
        parse_gpu_id_list=lambda raw: [int(item) for item in raw] if isinstance(raw, list) else [],
        resolved_cpu_profile=lambda _spec: "cpu_compute",
        task_paths=lambda _config, task_id: {"task_root": f"/tmp/{task_id}"},
    )


def test_task_result_payload_builds_terminal_snapshot() -> None:
    payload = build_task_result_payload(DummyConfig(), "demo-task", hooks=make_result_hooks())

    assert payload["task_id"] == "demo-task"
    assert payload["status"] == "completed"
    assert payload["result_ready"] is True
    assert payload["paths"]["task_root"] == "/tmp/demo-task"
    assert payload["platform_recovery"]["state"] == "none"
