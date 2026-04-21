from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TaskDashboardHooks:
    active_task_statuses: set[str]
    runnable_statuses: set[str]
    timestamp_sort_value: Callable[..., float]
    task_list_sort_key: Callable[[dict[str, Any]], tuple[Any, ...]]
    state_has_unresolved_dependencies: Callable[[Any, dict[str, Any]], bool]
    dashboard_short_time: Callable[[str], str]
    dashboard_trim: Callable[[Any, int], str]


def filter_dashboard_tasks(
    states: list[dict[str, Any]],
    *,
    status_filter: str,
    agent_filter: str,
    hooks: TaskDashboardHooks,
) -> list[dict[str, Any]]:
    filtered = states
    if agent_filter != "all":
        filtered = [state for state in filtered if str(state.get("agent_name", "")) == agent_filter]
    if status_filter == "all":
        return filtered
    if status_filter == "active":
        return [state for state in filtered if str(state.get("status", "")) in hooks.active_task_statuses]
    if status_filter == "queued":
        return [state for state in filtered if str(state.get("status", "")) in hooks.runnable_statuses]
    if status_filter == "attention":
        return [state for state in filtered if bool(state.get("needs_attention", False))]
    if status_filter == "pending":
        return [state for state in filtered if bool(state.get("pending_feedback", False))]
    if status_filter == "done":
        return [state for state in filtered if str(state.get("status", "")) in {"completed", "observed_exit"}]
    return filtered


def sort_dashboard_tasks(
    config: Any,
    states: list[dict[str, Any]],
    sort_mode: str,
    *,
    hooks: TaskDashboardHooks,
) -> list[dict[str, Any]]:
    if sort_mode == "priority":
        return sorted(
            states,
            key=lambda item: (
                -int(item.get("priority", 0) or 0),
                hooks.timestamp_sort_value(item.get("submitted_at"), missing=float("inf")),
                str(item.get("task_id", "")),
            ),
        )
    if sort_mode == "updated":
        return sorted(
            states,
            key=lambda item: (
                hooks.timestamp_sort_value(item.get("updated_at"), missing=float("-inf")),
                str(item.get("task_id", "")),
            ),
            reverse=True,
        )
    if sort_mode == "agent":
        return sorted(
            states,
            key=lambda item: (
                str(item.get("agent_name", "")),
                -int(item.get("priority", 0) or 0),
                hooks.timestamp_sort_value(item.get("submitted_at"), missing=float("inf")),
                str(item.get("task_id", "")),
            ),
        )
    if sort_mode == "status":
        return sorted(states, key=lambda item: (hooks.task_list_sort_key(item), str(item.get("task_id", ""))))
    return sorted(
        states,
        key=lambda item: (
            0
            if str(item.get("status", "")) in hooks.active_task_statuses
            else 1
            if str(item.get("status", "")) in hooks.runnable_statuses and not hooks.state_has_unresolved_dependencies(config, item)
            else 2
            if str(item.get("status", "")) in hooks.runnable_statuses
            else 3
            if item.get("pending_feedback", False)
            else 4
            if str(item.get("status", "")) in {"completed", "observed_exit"}
            else 5,
            -int(item.get("priority", 0) or 0),
            hooks.timestamp_sort_value(item.get("submitted_at"), missing=float("inf")),
            str(item.get("task_id", "")),
        ),
    )


def dashboard_issue_text(state: dict[str, Any]) -> str:
    if state.get("blocked_reason"):
        return f"blocked {state.get('blocked_reason')}"
    if bool(state.get("pending_feedback", False)):
        return "pending_feedback"
    if bool(state.get("needs_attention", False)) and state.get("attention_reason"):
        return str(state.get("attention_reason", ""))
    failure_kind = str(state.get("failure_kind", "")).strip()
    if failure_kind and failure_kind not in {"completed", "observed_exit"}:
        return failure_kind
    phase = str(state.get("phase", "")).strip()
    if phase and phase not in {"completed", "observed_exit", "running", "watching"}:
        return phase
    report_summary = str(state.get("report_summary", "")).strip()
    if report_summary:
        return report_summary
    return ""


def build_dashboard_task_entries(
    config: Any,
    states: list[dict[str, Any]],
    *,
    sort_mode: str,
    status_filter: str,
    agent_filter: str,
    limit: int,
    already_ordered: bool = False,
    hooks: TaskDashboardHooks,
) -> list[dict[str, Any]]:
    visible_states = (
        states
        if already_ordered
        else sort_dashboard_tasks(
            config,
            filter_dashboard_tasks(states, status_filter=status_filter, agent_filter=agent_filter, hooks=hooks),
            sort_mode,
            hooks=hooks,
        )
    )
    entries: list[dict[str, Any]] = []
    for state in visible_states[:limit]:
        cpu_threads = int(state.get("assigned_cpu_threads", 0) or 0) or int(state.get("cpu_threads", 0) or 0)
        cpu_workers = int(state.get("assigned_cpu_workers", 0) or 0) or int(state.get("cpu_workers", 0) or 0)
        entries.append(
            {
                "task_id": str(state.get("task_id", "")),
                "status": str(state.get("status", "")),
                "priority": int(state.get("priority", 0) or 0),
                "agent_name": str(state.get("agent_name", "")),
                "gpu_text": str(int(state.get("gpu_slots", 0) or 0)),
                "cpu_text": str(cpu_threads + cpu_workers),
                "feedback_mode": str(state.get("feedback_mode", "auto")),
                "updated_text": hooks.dashboard_short_time(str(state.get("updated_at", ""))),
                "issue_text": hooks.dashboard_trim(dashboard_issue_text(state), 80),
                "state": state,
            }
        )
    return entries
