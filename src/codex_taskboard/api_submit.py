from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from codex_taskboard.api_access import (
    api_token_is_admin,
    apply_api_task_namespace,
    apply_api_token_ownership,
    normalize_task_id,
    parse_boolish,
)


@dataclass(frozen=True)
class ApiSubmitHooks:
    normalize_cpu_profile: Callable[[Any], str]
    parse_gpu_id_list: Callable[[Any], list[int]]
    extract_raw_proposal_value: Callable[[dict[str, Any]], Any]
    extract_raw_closeout_proposal_dir: Callable[[dict[str, Any]], Any]
    extract_raw_project_history_file: Callable[[dict[str, Any]], Any]
    apply_executor_to_spec: Callable[[Any, dict[str, Any], str], dict[str, Any]]
    codex_session_exists_for_spec: Callable[[Any, dict[str, Any], str], bool]
    missing_sentinel: Any
    default_startup_failure_seconds: int


def build_spec_from_submit_job_payload(
    config: Any,
    payload: dict[str, Any],
    *,
    forced_executor: str = "",
    default_feedback_mode: str = "off",
    default_agent_name: str = "",
    hooks: ApiSubmitHooks,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(payload, dict):
        raise ValueError("submit-job payload must be a JSON object")
    task_id = str(payload.get("task_id", "")).strip()
    workdir = str(payload.get("workdir", "")).strip()
    command = str(payload.get("command", "")).strip()
    if not task_id:
        raise ValueError("submit-job payload is missing task_id")
    if not workdir:
        raise ValueError("submit-job payload is missing workdir")
    if not command:
        raise ValueError("submit-job payload is missing command")
    env_payload = payload.get("env", {})
    if env_payload is None:
        env_payload = {}
    if not isinstance(env_payload, dict):
        raise ValueError("submit-job payload env must be an object")
    feedback_mode = str(payload.get("feedback_mode", default_feedback_mode)).strip() or default_feedback_mode or "off"
    spec = {
        "task_id": task_id,
        "task_key": str(payload.get("task_key", task_id)).strip() or task_id,
        "execution_mode": "shell",
        "workdir": workdir,
        "proposal_base_workdir": workdir,
        "command": command,
        "codex_session_id": str(payload.get("codex_session_id", "")).strip(),
        "agent_name": str(payload.get("agent_name", default_agent_name)).strip() or default_agent_name,
        "allow_session_rebind": bool(payload.get("allow_session_rebind", False)),
        "allow_duplicate_submit": bool(payload.get("allow_duplicate_submit", False)),
        "priority": int(payload.get("priority", 0) or 0),
        "gpu_slots": payload.get("gpu_slots", None),
        "cpu_profile": hooks.normalize_cpu_profile(payload.get("cpu_profile", "auto")),
        "cpu_threads": int(payload.get("cpu_threads", 0) or 0),
        "cpu_threads_min": int(payload.get("cpu_threads_min", 0) or 0),
        "cpu_threads_max": int(payload.get("cpu_threads_max", 0) or 0),
        "cpu_threads_mode": str(payload.get("cpu_threads_mode", "")).strip(),
        "cpu_workers": int(payload.get("cpu_workers", 0) or 0),
        "cpu_workers_min": int(payload.get("cpu_workers_min", 0) or 0),
        "cpu_workers_max": int(payload.get("cpu_workers_max", 0) or 0),
        "gpu_min_free_mb": int(payload.get("gpu_min_free_mb", 0) or 0),
        "gpu_max_util_percent": int(payload.get("gpu_max_util_percent", 0) or 0),
        "assigned_gpus": hooks.parse_gpu_id_list(payload.get("assigned_gpus", [])),
        "replace_existing": bool(payload.get("replace_existing", True)),
        "feedback_mode": feedback_mode,
        "depends_on": [str(item) for item in payload.get("depends_on", []) if str(item).strip()],
        "required_artifact_globs": [str(item) for item in payload.get("required_artifact_globs", []) if str(item).strip()],
        "required_report_conditions": [str(item) for item in payload.get("required_report_conditions", []) if str(item).strip()],
        "report_format": str(payload.get("report_format", "auto")).strip() or "auto",
        "report_keys": [str(item) for item in payload.get("report_keys", []) if str(item).strip()],
        "report_contract": str(payload.get("report_contract", "")).strip(),
        "success_prompt": str(payload.get("success_prompt", "")).strip(),
        "failure_prompt": str(payload.get("failure_prompt", "")).strip(),
        "task_note": str(payload.get("task_note", "")).strip(),
        "artifact_globs": [str(item) for item in payload.get("artifact_globs", []) if str(item).strip()],
        "env": {str(key): str(value) for key, value in env_payload.items()},
        "codex_exec_mode": str(payload.get("codex_exec_mode", "dangerous")).strip() or "dangerous",
        "resume_timeout_seconds": int(payload.get("resume_timeout_seconds", 7200) or 7200),
        "launch_grace_seconds": int(payload.get("launch_grace_seconds", 0) or 0),
        "prompt_max_chars": int(payload.get("prompt_max_chars", 12000) or 12000),
        "log_tail_lines": int(payload.get("log_tail_lines", 80) or 80),
        "log_tail_chars": int(payload.get("log_tail_chars", 5000) or 5000),
        "artifact_max_chars": int(payload.get("artifact_max_chars", 1200) or 1200),
        "artifact_max_lines": int(payload.get("artifact_max_lines", 40) or 40),
        "startup_failure_threshold_seconds": int(
            payload.get("startup_failure_threshold_seconds", hooks.default_startup_failure_seconds)
            or hooks.default_startup_failure_seconds
        ),
        "fallback_provider": str(payload.get("fallback_provider", "")).strip(),
    }
    raw_proposal = hooks.extract_raw_proposal_value(payload)
    if raw_proposal is not hooks.missing_sentinel:
        if "proposal" in payload:
            spec["proposal"] = raw_proposal
        else:
            spec["proposal_path"] = raw_proposal
    raw_closeout_proposal_dir = hooks.extract_raw_closeout_proposal_dir(payload)
    if raw_closeout_proposal_dir is not hooks.missing_sentinel:
        spec["closeout_proposal_dir"] = raw_closeout_proposal_dir
    raw_project_history_file = hooks.extract_raw_project_history_file(payload)
    if raw_project_history_file is not hooks.missing_sentinel:
        if "project_history" in payload:
            spec["project_history"] = raw_project_history_file
        else:
            spec["project_history_file"] = raw_project_history_file
    executor_name = normalize_task_id(forced_executor or str(payload.get("executor", "")).strip())
    if executor_name:
        spec = hooks.apply_executor_to_spec(config, spec, executor_name)
    hold = bool(payload.get("hold", False))
    return spec, hold


def apply_api_token_submit_policy(
    config: Any,
    *,
    token_record: dict[str, Any],
    spec: dict[str, Any],
    payload: dict[str, Any],
    hooks: ApiSubmitHooks,
) -> dict[str, Any]:
    if not parse_boolish(token_record.get("allow_submit_job", True), default=True):
        raise ValueError("This API token cannot submit jobs.")
    updated = apply_api_task_namespace(apply_api_token_ownership(spec, token_record), token_record)
    if api_token_is_admin(token_record):
        return updated
    if payload.get("assigned_gpus"):
        raise ValueError("submit-job payload cannot set assigned_gpus for non-admin API tokens; use gpu_slots instead.")
    if str(updated.get("fallback_provider", "")).strip():
        raise ValueError("submit-job payload cannot set fallback_provider for non-admin API tokens.")
    feedback_mode = str(updated.get("feedback_mode", "off")).strip() or "off"
    codex_session_id = str(updated.get("codex_session_id", "")).strip()
    wants_session_feedback = bool(codex_session_id) or feedback_mode != "off"
    if wants_session_feedback and not parse_boolish(token_record.get("allow_session_feedback", False), default=False):
        raise ValueError("This API token is result-only and cannot target a Codex session.")
    if feedback_mode != "off" and not codex_session_id:
        raise ValueError("feedback_mode requires codex_session_id for API submit-job requests.")
    if codex_session_id and not hooks.codex_session_exists_for_spec(config, updated, codex_session_id):
        raise ValueError("codex_session_id was not found inside the bound executor Codex home.")
    if codex_session_id and str(updated.get("codex_exec_mode", "dangerous")).strip() == "dangerous":
        if not parse_boolish(token_record.get("allow_dangerous_codex_exec", False), default=False):
            raise ValueError("This API token cannot use codex_exec_mode=dangerous.")
    return updated
