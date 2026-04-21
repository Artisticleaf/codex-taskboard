from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TaskResultHooks:
    normalize_task_id: Callable[[str], str]
    load_task_state: Callable[[Any, str], dict[str, Any]]
    get_gpu_summary_table: Callable[[], list[dict[str, Any]]]
    detect_gpu_count: Callable[[], int]
    iter_task_states: Callable[[Any], list[dict[str, Any]]]
    active_task_statuses: set[str]
    task_requested_cpu_budget: Callable[[dict[str, Any]], int]
    merged_spec_with_state: Callable[[Any, dict[str, Any]], dict[str, Any]]
    enrich_task_state: Callable[..., dict[str, Any]]
    detect_default_cpu_thread_limit: Callable[[], int]
    load_task_spec: Callable[[Any, str], dict[str, Any]]
    is_terminal_status: Callable[[str], bool]
    parse_gpu_id_list: Callable[[Any], list[int]]
    resolved_cpu_profile: Callable[[dict[str, Any]], str]
    task_paths: Callable[[Any, str], dict[str, Any]]


def build_task_result_payload(
    config: Any,
    task_id: str,
    *,
    hooks: TaskResultHooks,
) -> dict[str, Any]:
    normalized_task_id = hooks.normalize_task_id(task_id)
    state = hooks.load_task_state(config, normalized_task_id)
    if not state:
        raise ValueError(f"Task not found: {normalized_task_id}")
    gpu_rows = hooks.get_gpu_summary_table()
    total_gpu_slots = hooks.detect_gpu_count() or len(gpu_rows)
    active_states = [item for item in hooks.iter_task_states(config) if str(item.get("status", "")) in hooks.active_task_statuses]
    active_cpu_threads = sum(hooks.task_requested_cpu_budget(hooks.merged_spec_with_state(config, item)) for item in active_states)
    state = hooks.enrich_task_state(
        config,
        state,
        gpu_rows=gpu_rows,
        total_gpu_slots=total_gpu_slots,
        active_cpu_threads=active_cpu_threads,
        cpu_thread_limit=hooks.detect_default_cpu_thread_limit(),
    )
    spec = hooks.load_task_spec(config, normalized_task_id)
    return {
        "task_id": normalized_task_id,
        "task_key": str(state.get("task_key", spec.get("task_key", normalized_task_id))),
        "client_task_id": str(state.get("client_task_id", spec.get("client_task_id", ""))),
        "client_task_key": str(state.get("client_task_key", spec.get("client_task_key", ""))),
        "owner_tenant": str(state.get("owner_tenant", spec.get("owner_tenant", ""))),
        "owner_role": str(state.get("owner_role", spec.get("owner_role", ""))),
        "owner_label": str(state.get("owner_label", spec.get("owner_label", ""))),
        "submitted_via_api": bool(state.get("submitted_via_api", spec.get("submitted_via_api", False))),
        "status": str(state.get("status", "")),
        "lifecycle_state": str(state.get("lifecycle_state", "")),
        "runtime_state": str(state.get("runtime_state", "")),
        "phase": str(state.get("phase", "")),
        "blocked_reason": str(state.get("blocked_reason", "")),
        "cpu_block_reason": str(state.get("cpu_block_reason", "")),
        "gpu_block_reason": str(state.get("gpu_block_reason", "")),
        "eligible_gpu_ids": hooks.parse_gpu_id_list(state.get("eligible_gpu_ids", [])),
        "execution_mode": str(state.get("execution_mode", spec.get("execution_mode", ""))),
        "result_ready": hooks.is_terminal_status(str(state.get("status", ""))),
        "executor_name": str(spec.get("executor_name", state.get("executor_name", ""))),
        "remote_workdir": str(spec.get("remote_workdir", state.get("remote_workdir", ""))),
        "workdir": str(state.get("workdir", spec.get("workdir", ""))),
        "command": str(state.get("command", spec.get("command", ""))),
        "proposal_path": str(state.get("proposal_path", spec.get("proposal_path", ""))),
        "proposal_source": str(state.get("proposal_source", spec.get("proposal_source", ""))),
        "proposal_owner": bool(state.get("proposal_owner", spec.get("proposal_owner", False))),
        "closeout_proposal_dir": str(state.get("closeout_proposal_dir", spec.get("closeout_proposal_dir", ""))),
        "closeout_proposal_dir_source": str(
            state.get("closeout_proposal_dir_source", spec.get("closeout_proposal_dir_source", ""))
        ),
        "project_history_file": str(state.get("project_history_file", spec.get("project_history_file", ""))),
        "project_history_file_source": str(
            state.get("project_history_file_source", spec.get("project_history_file_source", ""))
        ),
        "allow_duplicate_submit": bool(state.get("allow_duplicate_submit", spec.get("allow_duplicate_submit", False))),
        "duplicate_submit_matches": state.get("duplicate_submit_matches", spec.get("duplicate_submit_matches", [])),
        "duplicate_submit_warning": str(state.get("duplicate_submit_warning", spec.get("duplicate_submit_warning", ""))),
        "submitted_at": str(state.get("submitted_at", "")),
        "started_at": str(state.get("started_at", "")),
        "ended_at": str(state.get("ended_at", "")),
        "updated_at": str(state.get("updated_at", "")),
        "duration_seconds": state.get("duration_seconds", None),
        "exit_code": state.get("exit_code", None),
        "exit_signal": str(state.get("exit_signal", "")),
        "failure_kind": str(state.get("failure_kind", "")),
        "failure_summary": str(state.get("failure_summary", "")),
        "failure_excerpt": str(state.get("failure_excerpt", "")),
        "needs_attention": bool(state.get("needs_attention", False)),
        "attention_reason": str(state.get("attention_reason", "")),
        "attention_message": str(state.get("attention_message", "")),
        "report_summary": str(state.get("report_summary", "")),
        "structured_report": state.get("structured_report", {}),
        "taskboard_signal": str(state.get("taskboard_signal", "")),
        "notification_signal": str(state.get("notification_signal", "")),
        "gpu_slots": int(state.get("gpu_slots", spec.get("gpu_slots", 0)) or 0),
        "assigned_gpus": hooks.parse_gpu_id_list(state.get("assigned_gpus") or spec.get("assigned_gpus", [])),
        "gpu_assignment_source": str(state.get("gpu_assignment_source", spec.get("gpu_assignment_source", ""))),
        "allowed_gpus": hooks.parse_gpu_id_list(state.get("allowed_gpus") or spec.get("allowed_gpus", [])),
        "cpu_profile": str(state.get("cpu_profile", spec.get("cpu_profile", "auto"))),
        "cpu_profile_resolved": str(state.get("cpu_profile_resolved", hooks.resolved_cpu_profile(spec))),
        "assigned_cpu_threads": int(state.get("assigned_cpu_threads", spec.get("assigned_cpu_threads", 0)) or 0),
        "cpu_threads": int(state.get("cpu_threads", spec.get("cpu_threads", 0)) or 0),
        "cpu_threads_mode": str(state.get("cpu_threads_mode", spec.get("cpu_threads_mode", ""))),
        "assigned_cpu_workers": int(state.get("assigned_cpu_workers", spec.get("assigned_cpu_workers", 0)) or 0),
        "cpu_workers": int(state.get("cpu_workers", spec.get("cpu_workers", 0)) or 0),
        "cpu_workers_min": int(state.get("cpu_workers_min", spec.get("cpu_workers_min", 0)) or 0),
        "cpu_workers_max": int(state.get("cpu_workers_max", spec.get("cpu_workers_max", 0)) or 0),
        "cpu_budget": int(state.get("cpu_budget", hooks.task_requested_cpu_budget(spec)) or 0),
        "followup_status": str(state.get("followup_status", "")),
        "followup_last_signal": str(state.get("followup_last_signal", "")),
        "followup_last_action": str(state.get("followup_last_action", "")),
        "followup_stopped_at": str(state.get("followup_stopped_at", "")),
        "followup_entity_present": bool(state.get("followup_entity_present", False)),
        "followup_entity_key": str(state.get("followup_entity_key", "")),
        "followup_audit_status": str(state.get("followup_audit_status", "")),
        "paths": hooks.task_paths(config, normalized_task_id),
        "last_event_path": str(state.get("last_event_path", "")),
        "dispatch_diagnostics": state.get("dispatch_diagnostics", {}),
        "launch_diagnostics": state.get("launch_diagnostics", {}),
        "platform_recovery": state.get("platform_recovery", {}),
        "automation_recommendation": str(state.get("automation_recommendation", "")),
    }
