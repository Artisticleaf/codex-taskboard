from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class FollowupRuntimeHooks:
    version: int
    continuous_session_reminder_followup_type: str
    default_followup_workdir: str
    normalize_task_id: Callable[[str], str]
    load_task_spec: Callable[[Any, str], dict[str, Any]]
    load_task_state: Callable[[Any, str], dict[str, Any]]
    normalize_timestamp_fields: Callable[[dict[str, Any]], dict[str, Any]]
    iter_all_task_states: Callable[[Any], list[dict[str, Any]]]
    parse_timestamp_to_unix: Callable[[str], float | None]
    merge_task_state: Callable[..., dict[str, Any]]
    read_json: Callable[[Path, Any], Any]
    atomic_write_json: Callable[[Path, Any], None]
    append_followup_event_log: Callable[..., None]
    utc_now: Callable[[], str]
    apply_session_redirect_to_spec: Callable[..., dict[str, Any]]
    latest_continuous_research_anchor_spec: Callable[[Any, str], dict[str, Any] | None]
    parse_gpu_id_list: Callable[[Any], list[int]]
    build_resume_prompt: Callable[..., str]
    continuous_research_mode_enabled: Callable[..., bool]
    run_with_followup_lock: Callable[[Any, str, Callable[[], dict[str, Any]]], dict[str, Any]]
    task_last_message_path: Callable[[Any, str], Path]


def followup_key_for(spec: dict[str, Any]) -> str:
    return hashlib.sha1(
        f"{spec.get('codex_session_id', '')}::{spec.get('agent_name', '')}::{spec.get('task_key', spec.get('task_id', ''))}".encode(
            "utf-8"
        )
    ).hexdigest()[:20]


def queued_feedback_key_for(spec: dict[str, Any]) -> str:
    digest = hashlib.sha1(f"{spec.get('codex_session_id', '')}::queued_feedback".encode("utf-8")).hexdigest()[:20]
    return f"queued-feedback-{digest}"


def continuous_session_followup_key_for(codex_session_id: str) -> str:
    digest = hashlib.sha1(f"{codex_session_id}::continuous_session_reminder".encode("utf-8")).hexdigest()[:20]
    return f"continuous-session-reminder-{digest}"


def followup_path(config: Any, followup_key: str) -> Path:
    return config.followups_root / f"{followup_key}.json"


def followup_message_path(config: Any, followup_key: str) -> Path:
    return config.followups_root / f"{followup_key}.last-message.txt"


def build_followup_resume_spec_from_payload(
    followup: dict[str, Any],
    *,
    hooks: FollowupRuntimeHooks,
) -> dict[str, Any]:
    return {
        "task_id": str(followup.get("task_id", "")),
        "task_key": str(followup.get("task_key", "")),
        "agent_name": str(followup.get("agent_name", "")),
        "codex_session_id": str(followup.get("codex_session_id", "")),
        "proposal_path": str(followup.get("proposal_path", "")),
        "proposal_source": str(followup.get("proposal_source", "")),
        "proposal_owner": bool(followup.get("proposal_owner", False)),
        "closeout_proposal_dir": str(followup.get("closeout_proposal_dir", "")),
        "closeout_proposal_dir_source": str(followup.get("closeout_proposal_dir_source", "")),
        "project_history_file": str(followup.get("project_history_file", "")),
        "project_history_file_source": str(followup.get("project_history_file_source", "")),
        "workdir": str(followup.get("workdir", hooks.default_followup_workdir)),
        "remote_workdir": str(followup.get("remote_workdir", "")),
        "executor_name": str(followup.get("executor_name", "")),
        "executor_target": str(followup.get("executor_target", "")),
        "executor_identity_file": str(followup.get("executor_identity_file", "")),
        "executor_ssh_options": [str(item) for item in followup.get("executor_ssh_options", []) if str(item).strip()],
        "executor_remote_workdir_prefix": str(followup.get("executor_remote_workdir_prefix", "")),
        "executor_remote_home": str(followup.get("executor_remote_home", "")),
        "executor_remote_codex_home": str(followup.get("executor_remote_codex_home", "")),
        "executor_remote_codex_bin": str(followup.get("executor_remote_codex_bin", "codex")),
        "codex_exec_mode": str(followup.get("codex_exec_mode", "dangerous")),
        "resume_timeout_seconds": int(followup.get("resume_timeout_seconds", 3600) or 3600),
        "fallback_provider": str(followup.get("fallback_provider", "")),
        "execution_mode": str(followup.get("execution_mode", "shell")),
        "prompt_max_chars": int(followup.get("prompt_max_chars", 12000) or 12000),
        "controller_continuation_hint": (
            followup.get("controller_continuation_hint", {})
            if isinstance(followup.get("controller_continuation_hint", {}), dict)
            else {}
        ),
    }


def should_schedule_followup_for_spec(spec: dict[str, Any]) -> bool:
    session_id = str(spec.get("codex_session_id", "")).strip()
    if not session_id:
        return False
    return str(spec.get("agent_name", "")).strip() != "platform-maintainer"


def current_followup_resume_spec(
    config: Any,
    followup: dict[str, Any],
    *,
    hooks: FollowupRuntimeHooks,
) -> dict[str, Any]:
    spec = build_followup_resume_spec_from_payload(followup, hooks=hooks)
    task_id = hooks.normalize_task_id(str(followup.get("task_id", "")).strip())
    if not task_id:
        return spec
    current_spec = hooks.load_task_spec(config, task_id) or {}
    current_state = hooks.load_task_state(config, task_id) or {}
    if not current_spec and not current_state:
        return spec
    spec.update(
        {
            "task_id": str(current_spec.get("task_id", current_state.get("task_id", spec["task_id"]))),
            "task_key": str(current_spec.get("task_key", current_state.get("task_key", spec["task_key"]))),
            "agent_name": str(current_spec.get("agent_name", current_state.get("agent_name", spec["agent_name"]))),
            "codex_session_id": str(
                current_spec.get("codex_session_id", current_state.get("codex_session_id", spec["codex_session_id"]))
            ),
            "proposal_path": str(current_spec.get("proposal_path", current_state.get("proposal_path", spec["proposal_path"]))),
            "proposal_source": str(
                current_spec.get("proposal_source", current_state.get("proposal_source", spec["proposal_source"]))
            ),
            "proposal_owner": bool(
                current_spec.get("proposal_owner", current_state.get("proposal_owner", spec["proposal_owner"]))
            ),
            "closeout_proposal_dir": str(
                current_spec.get(
                    "closeout_proposal_dir",
                    current_state.get("closeout_proposal_dir", spec["closeout_proposal_dir"]),
                )
            ),
            "closeout_proposal_dir_source": str(
                current_spec.get(
                    "closeout_proposal_dir_source",
                    current_state.get("closeout_proposal_dir_source", spec["closeout_proposal_dir_source"]),
                )
            ),
            "project_history_file": str(
                current_spec.get(
                    "project_history_file",
                    current_state.get("project_history_file", spec["project_history_file"]),
                )
            ),
            "project_history_file_source": str(
                current_spec.get(
                    "project_history_file_source",
                    current_state.get("project_history_file_source", spec["project_history_file_source"]),
                )
            ),
            "workdir": str(current_spec.get("workdir", current_state.get("workdir", spec["workdir"]))),
            "remote_workdir": str(
                current_spec.get("remote_workdir", current_state.get("remote_workdir", spec["remote_workdir"]))
            ),
            "executor_name": str(
                current_spec.get("executor_name", current_state.get("executor_name", spec["executor_name"]))
            ),
            "executor_target": str(
                current_spec.get("executor_target", current_state.get("executor_target", spec["executor_target"]))
            ),
            "executor_identity_file": str(
                current_spec.get(
                    "executor_identity_file",
                    current_state.get("executor_identity_file", spec["executor_identity_file"]),
                )
            ),
            "executor_ssh_options": [
                str(item)
                for item in current_spec.get(
                    "executor_ssh_options",
                    current_state.get("executor_ssh_options", spec["executor_ssh_options"]),
                )
                if str(item).strip()
            ],
            "executor_remote_workdir_prefix": str(
                current_spec.get(
                    "executor_remote_workdir_prefix",
                    current_state.get("executor_remote_workdir_prefix", spec["executor_remote_workdir_prefix"]),
                )
            ),
            "executor_remote_home": str(
                current_spec.get(
                    "executor_remote_home",
                    current_state.get("executor_remote_home", spec["executor_remote_home"]),
                )
            ),
            "executor_remote_codex_home": str(
                current_spec.get(
                    "executor_remote_codex_home",
                    current_state.get("executor_remote_codex_home", spec["executor_remote_codex_home"]),
                )
            ),
            "executor_remote_codex_bin": str(
                current_spec.get(
                    "executor_remote_codex_bin",
                    current_state.get("executor_remote_codex_bin", spec["executor_remote_codex_bin"]),
                )
            ),
            "codex_exec_mode": str(
                current_spec.get("codex_exec_mode", current_state.get("codex_exec_mode", spec["codex_exec_mode"]))
            ),
            "resume_timeout_seconds": int(
                current_spec.get(
                    "resume_timeout_seconds",
                    current_state.get("resume_timeout_seconds", spec["resume_timeout_seconds"]),
                )
                or spec["resume_timeout_seconds"]
            ),
            "fallback_provider": str(
                current_spec.get("fallback_provider", current_state.get("fallback_provider", spec["fallback_provider"]))
            ),
            "execution_mode": str(
                current_spec.get("execution_mode", current_state.get("execution_mode", spec["execution_mode"]))
            ),
            "prompt_max_chars": int(
                current_spec.get("prompt_max_chars", current_state.get("prompt_max_chars", spec["prompt_max_chars"]))
                or spec["prompt_max_chars"]
            ),
            "controller_continuation_hint": (
                current_spec.get(
                    "controller_continuation_hint",
                    current_state.get("controller_continuation_hint", spec.get("controller_continuation_hint", {})),
                )
                if isinstance(
                    current_spec.get(
                        "controller_continuation_hint",
                        current_state.get("controller_continuation_hint", spec.get("controller_continuation_hint", {})),
                    ),
                    dict,
                )
                else {}
            ),
        }
    )
    return spec


def merge_queued_notification_lists(
    existing_items: list[dict[str, Any]],
    incoming_items: list[dict[str, Any]],
    *,
    hooks: FollowupRuntimeHooks,
) -> list[dict[str, Any]]:
    merged_by_task_id: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for item in [*existing_items, *incoming_items]:
        if not isinstance(item, dict):
            continue
        task_id = hooks.normalize_task_id(str(item.get("task_id", "")).strip())
        if not task_id:
            passthrough.append(item)
            continue
        merged_by_task_id[task_id] = item
    return [*passthrough, *[merged_by_task_id[key] for key in sorted(merged_by_task_id)]]


def queued_notification_entries(followup: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in followup.get("queued_notifications", []) if isinstance(item, dict)]


def followup_task_ids(
    followup: dict[str, Any],
    *,
    hooks: FollowupRuntimeHooks,
) -> list[str]:
    task_ids: list[str] = []
    primary_task_id = hooks.normalize_task_id(str(followup.get("task_id", "")).strip())
    if primary_task_id:
        task_ids.append(primary_task_id)
    for item in queued_notification_entries(followup):
        queued_task_id = hooks.normalize_task_id(str(item.get("task_id", "")).strip())
        if queued_task_id:
            task_ids.append(queued_task_id)
    return sorted(set(task_ids))


def sync_followup_state(
    config: Any,
    followup: dict[str, Any],
    *,
    hooks: FollowupRuntimeHooks,
    followup_status: str,
    followup_last_action: str,
    followup_last_signal: str = "",
    followup_stopped_at: str = "",
    pending_feedback: bool | None = None,
    notification_signal: str | None = None,
    message_path: str = "",
) -> None:
    updates: dict[str, Any] = {
        "followup_status": followup_status,
        "followup_last_signal": followup_last_signal,
        "followup_last_action": followup_last_action,
        "followup_stopped_at": followup_stopped_at,
        "followup_last_message_path": message_path
        or str(followup_message_path(config, str(followup.get("followup_key", "")).strip())),
    }
    if pending_feedback is not None:
        updates["pending_feedback"] = pending_feedback
    if notification_signal is not None:
        updates["notification_signal"] = notification_signal
    for task_id in followup_task_ids(followup, hooks=hooks):
        hooks.merge_task_state(config, task_id, **updates)


def load_followups(
    config: Any,
    *,
    hooks: FollowupRuntimeHooks,
) -> list[dict[str, Any]]:
    if not config.followups_root.exists():
        return []
    payloads: list[dict[str, Any]] = []
    for path in sorted(config.followups_root.glob("*.json")):
        payload = hooks.read_json(path, {})
        if isinstance(payload, dict) and payload:
            payloads.append(hooks.normalize_timestamp_fields(payload))
    return payloads


def followup_entity_info(
    config: Any,
    task_id: str,
    *,
    hooks: FollowupRuntimeHooks,
) -> tuple[bool, str]:
    normalized_task_id = hooks.normalize_task_id(task_id)
    if not normalized_task_id:
        return False, ""
    for payload in load_followups(config, hooks=hooks):
        if normalized_task_id in followup_task_ids(payload, hooks=hooks):
            return True, str(payload.get("followup_key", "")).strip()
    return False, ""


def rebind_followup_to_current_task(
    config: Any,
    followup: dict[str, Any],
    *,
    hooks: FollowupRuntimeHooks,
) -> tuple[dict[str, Any], bool, bool]:
    followup_type = str(followup.get("followup_type", "")).strip()
    if followup_type == hooks.continuous_session_reminder_followup_type:
        session_id = str(followup.get("codex_session_id", "")).strip()
        updated_spec = hooks.latest_continuous_research_anchor_spec(config, session_id) or current_followup_resume_spec(
            config,
            followup,
            hooks=hooks,
        )
    else:
        updated_spec = current_followup_resume_spec(config, followup, hooks=hooks)
    updated_followup = dict(followup)
    updated_followup.update(
        {
            "task_id": updated_spec["task_id"],
            "task_key": updated_spec["task_key"],
            "agent_name": updated_spec["agent_name"],
            "codex_session_id": updated_spec["codex_session_id"],
            "proposal_path": updated_spec["proposal_path"],
            "proposal_source": updated_spec["proposal_source"],
            "proposal_owner": updated_spec["proposal_owner"],
            "closeout_proposal_dir": updated_spec["closeout_proposal_dir"],
            "closeout_proposal_dir_source": updated_spec["closeout_proposal_dir_source"],
            "project_history_file": updated_spec["project_history_file"],
            "project_history_file_source": updated_spec["project_history_file_source"],
            "workdir": updated_spec["workdir"],
            "remote_workdir": updated_spec["remote_workdir"],
            "executor_name": updated_spec["executor_name"],
            "executor_target": updated_spec["executor_target"],
            "executor_identity_file": updated_spec["executor_identity_file"],
            "executor_ssh_options": updated_spec["executor_ssh_options"],
            "executor_remote_workdir_prefix": updated_spec["executor_remote_workdir_prefix"],
            "executor_remote_home": updated_spec["executor_remote_home"],
            "executor_remote_codex_home": updated_spec["executor_remote_codex_home"],
            "executor_remote_codex_bin": updated_spec["executor_remote_codex_bin"],
            "codex_exec_mode": updated_spec["codex_exec_mode"],
            "resume_timeout_seconds": updated_spec["resume_timeout_seconds"],
            "fallback_provider": updated_spec["fallback_provider"],
            "execution_mode": updated_spec["execution_mode"],
            "prompt_max_chars": updated_spec["prompt_max_chars"],
        }
    )
    old_key = str(followup.get("followup_key", "")).strip()
    if followup_type == "queued_feedback_resume":
        new_key = queued_feedback_key_for(updated_spec)
    elif followup_type == hooks.continuous_session_reminder_followup_type:
        new_key = continuous_session_followup_key_for(
            str(updated_spec.get("codex_session_id", followup.get("codex_session_id", ""))).strip()
        )
    else:
        new_key = followup_key_for(updated_spec)
    updated_followup["followup_key"] = new_key
    if updated_followup == followup:
        return updated_followup, False, False

    new_path = followup_path(config, new_key)
    if new_key != old_key and new_path.exists():
        existing_payload = hooks.read_json(new_path, {})
        merged_payload = existing_payload if isinstance(existing_payload, dict) and existing_payload else {}
        if not merged_payload:
            merged_payload = dict(updated_followup)
        else:
            merged_payload.update(updated_followup)
            if followup_type == "queued_feedback_resume":
                merged_payload["queued_notifications"] = merge_queued_notification_lists(
                    [item for item in merged_payload.get("queued_notifications", []) if isinstance(item, dict)],
                    [item for item in updated_followup.get("queued_notifications", []) if isinstance(item, dict)],
                    hooks=hooks,
                )
        hooks.atomic_write_json(new_path, merged_payload)
        sync_followup_state(
            config,
            merged_payload,
            hooks=hooks,
            followup_status="scheduled",
            followup_last_action="rebound_session_binding",
            pending_feedback=followup_type == "queued_feedback_resume",
            message_path=str(followup_message_path(config, new_key)),
        )
        resolve_followup(config, old_key)
        return merged_payload, True, True

    hooks.atomic_write_json(new_path, updated_followup)
    if new_key != old_key:
        resolve_followup(config, old_key)
    sync_followup_state(
        config,
        updated_followup,
        hooks=hooks,
        followup_status="scheduled",
        followup_last_action="rebound_session_binding",
        pending_feedback=followup_type == "queued_feedback_resume",
        message_path=str(followup_message_path(config, new_key)),
    )
    return updated_followup, True, False


def schedule_followup(
    config: Any,
    *,
    task_id: str,
    spec: dict[str, Any],
    reason: str,
    delay_seconds: int = 900,
    interval_seconds: int = 300,
    min_idle_seconds: int = 600,
    followup_key_override: str = "",
    followup_type: str = "",
    last_signal: str = "",
    hooks: FollowupRuntimeHooks,
) -> None:
    spec = hooks.apply_session_redirect_to_spec(config, spec, include_migrating=True)
    if not should_schedule_followup_for_spec(spec):
        return
    followup_key = str(followup_key_override or followup_key_for(spec)).strip()
    message_path = followup_message_path(config, followup_key)
    created_at = hooks.utc_now()
    payload = {
        "version": hooks.version,
        "followup_key": followup_key,
        "task_id": task_id,
        "task_key": str(spec.get("task_key", task_id)),
        "execution_mode": str(spec.get("execution_mode", "shell")),
        "codex_session_id": str(spec.get("codex_session_id")),
        "agent_name": str(spec.get("agent_name", "")),
        "proposal_path": str(spec.get("proposal_path", "")),
        "proposal_source": str(spec.get("proposal_source", "")),
        "proposal_owner": bool(spec.get("proposal_owner", False)),
        "closeout_proposal_dir": str(spec.get("closeout_proposal_dir", "")),
        "closeout_proposal_dir_source": str(spec.get("closeout_proposal_dir_source", "")),
        "project_history_file": str(spec.get("project_history_file", "")),
        "project_history_file_source": str(spec.get("project_history_file_source", "")),
        "workdir": str(spec.get("workdir", "")),
        "remote_workdir": str(spec.get("remote_workdir", "")),
        "executor_name": str(spec.get("executor_name", "")),
        "executor_target": str(spec.get("executor_target", "")),
        "executor_identity_file": str(spec.get("executor_identity_file", "")),
        "executor_ssh_options": [str(item) for item in spec.get("executor_ssh_options", []) if str(item).strip()],
        "executor_remote_workdir_prefix": str(spec.get("executor_remote_workdir_prefix", "")),
        "executor_remote_home": str(spec.get("executor_remote_home", "")),
        "executor_remote_codex_home": str(spec.get("executor_remote_codex_home", "")),
        "executor_remote_codex_bin": str(spec.get("executor_remote_codex_bin", "codex")),
        "reason": reason,
        "created_at": created_at,
        "check_after_ts": time.time() + delay_seconds,
        "interval_seconds": interval_seconds,
        "min_idle_seconds": min_idle_seconds,
        "nudge_count": 0,
        "stopped": False,
        "last_signal": str(last_signal or "").strip(),
    }
    if isinstance(spec.get("controller_continuation_hint", {}), dict) and spec.get("controller_continuation_hint"):
        payload["controller_continuation_hint"] = dict(spec.get("controller_continuation_hint", {}))
    normalized_followup_type = str(followup_type or "").strip()
    if normalized_followup_type:
        payload["followup_type"] = normalized_followup_type
    hooks.atomic_write_json(followup_path(config, followup_key), payload)
    hooks.append_followup_event_log(config, event="scheduled", reason=reason, followup=payload)
    hooks.merge_task_state(
        config,
        task_id,
        followup_status="scheduled",
        followup_last_signal=str(last_signal or "").strip(),
        followup_last_action=f"scheduled:{reason}",
        followup_stopped_at="",
        followup_last_message_path=str(message_path),
    )


def queue_feedback_resume(
    config: Any,
    *,
    task_id: str,
    spec: dict[str, Any],
    event: dict[str, Any],
    reason: str,
    min_idle_seconds: int,
    hooks: FollowupRuntimeHooks,
) -> dict[str, Any]:
    spec = hooks.apply_session_redirect_to_spec(config, spec, include_migrating=True)
    followup_key = queued_feedback_key_for(spec)
    path = followup_path(config, followup_key)
    message_path = followup_message_path(config, followup_key)
    normalized_min_idle_seconds = max(0, int(min_idle_seconds))

    def update_queue() -> dict[str, Any]:
        payload = hooks.read_json(path, {})
        if not isinstance(payload, dict) or not payload:
            payload = {
                "version": hooks.version,
                "followup_key": followup_key,
                "followup_type": "queued_feedback_resume",
                "task_id": task_id,
                "task_key": str(spec.get("task_key", task_id)),
                "execution_mode": str(spec.get("execution_mode", "shell")),
                "codex_session_id": str(spec.get("codex_session_id", "")),
                "agent_name": str(spec.get("agent_name", "")),
                "proposal_path": str(spec.get("proposal_path", "")),
                "proposal_source": str(spec.get("proposal_source", "")),
                "proposal_owner": bool(spec.get("proposal_owner", False)),
                "closeout_proposal_dir": str(spec.get("closeout_proposal_dir", "")),
                "closeout_proposal_dir_source": str(spec.get("closeout_proposal_dir_source", "")),
                "project_history_file": str(spec.get("project_history_file", "")),
                "project_history_file_source": str(spec.get("project_history_file_source", "")),
                "workdir": str(spec.get("workdir", "")),
                "remote_workdir": str(spec.get("remote_workdir", "")),
                "executor_name": str(spec.get("executor_name", "")),
                "executor_target": str(spec.get("executor_target", "")),
                "executor_identity_file": str(spec.get("executor_identity_file", "")),
                "executor_ssh_options": [str(item) for item in spec.get("executor_ssh_options", []) if str(item).strip()],
                "executor_remote_workdir_prefix": str(spec.get("executor_remote_workdir_prefix", "")),
                "executor_remote_home": str(spec.get("executor_remote_home", "")),
                "executor_remote_codex_home": str(spec.get("executor_remote_codex_home", "")),
                "executor_remote_codex_bin": str(spec.get("executor_remote_codex_bin", "codex")),
                "codex_exec_mode": str(spec.get("codex_exec_mode", "dangerous")),
                "resume_timeout_seconds": int(spec.get("resume_timeout_seconds", 7200) or 7200),
                "fallback_provider": str(spec.get("fallback_provider", "")),
                "prompt_max_chars": int(spec.get("prompt_max_chars", 12000) or 12000),
                "reason": reason,
                "created_at": hooks.utc_now(),
                "check_after_ts": time.time() + normalized_min_idle_seconds,
                "interval_seconds": 300,
                "min_idle_seconds": normalized_min_idle_seconds,
                "nudge_count": 0,
                "stopped": False,
                "queued_notifications": [],
            }
        queued_notifications = payload.get("queued_notifications", [])
        if not isinstance(queued_notifications, list):
            queued_notifications = []
        resume_spec_snapshot = {
            "task_id": task_id,
            "workdir": str(spec.get("workdir", "")),
            "command": str(spec.get("command", "")),
            "remote_workdir": str(spec.get("remote_workdir", "")),
            "executor_name": str(spec.get("executor_name", "")),
            "execution_mode": str(spec.get("execution_mode", "shell")),
            "watch_pid": spec.get("watch_pid"),
            "watch_log_path": str(spec.get("watch_log_path", "")),
            "subagent_model": str(spec.get("subagent_model", "")),
            "task_note": str(spec.get("task_note", "")),
            "assigned_gpus": hooks.parse_gpu_id_list(spec.get("assigned_gpus", [])),
            "proposal_path": str(spec.get("proposal_path", "")),
            "proposal_source": str(spec.get("proposal_source", "")),
            "proposal_owner": bool(spec.get("proposal_owner", False)),
            "closeout_proposal_dir": str(spec.get("closeout_proposal_dir", "")),
            "closeout_proposal_dir_source": str(spec.get("closeout_proposal_dir_source", "")),
            "project_history_file": str(spec.get("project_history_file", "")),
            "project_history_file_source": str(spec.get("project_history_file_source", "")),
        }
        resume_event_snapshot = {
            "status": str(event.get("status", "")),
            "queued_at": hooks.utc_now(),
            "queued_reason": reason,
            "event_path": str(event.get("event_path", "")),
            "feedback_data_path": str(event.get("feedback_data_path", "")),
            "command_log_path": str(event.get("command_log_path", "")),
            "runner_log_path": str(event.get("runner_log_path", "")),
            "assigned_gpus": hooks.parse_gpu_id_list(event.get("assigned_gpus", spec.get("assigned_gpus", []))),
            "rejected_reason": str(event.get("rejected_reason", "")),
            "watch_log_path": str(event.get("watch_log_path", "")),
            "subagent_model": str(event.get("subagent_model", spec.get("subagent_model", ""))),
            "subagent_session_id": str(event.get("subagent_session_id", "")),
            "subagent_message_written": bool(event.get("subagent_message_written", False)),
            "subagent_last_message_path": str(event.get("subagent_last_message_path", "")),
            "continue_attempts": int(event.get("continue_attempts", 0) or 0),
            "recovered_with_continue": bool(event.get("recovered_with_continue", False)),
            "exit_code": event.get("exit_code"),
            "exit_signal": str(event.get("exit_signal", "")),
            "failure_kind": str(event.get("failure_kind", "")),
            "failure_summary": str(event.get("failure_summary", "")),
            "taskboard_signal": str(event.get("taskboard_signal", "")),
            "needs_attention": bool(event.get("needs_attention", False)),
            "attention_message": str(event.get("attention_message", "")),
            "duration_seconds": event.get("duration_seconds"),
            "artifact_context": [item for item in event.get("artifact_context", []) if isinstance(item, dict)],
        }
        entry = {
            "task_id": task_id,
            "task_key": str(spec.get("task_key", task_id)),
            "status": str(event.get("status", "")),
            "queued_at": str(resume_event_snapshot.get("queued_at", "")),
            "queued_reason": reason,
            "event_path": str(event.get("event_path", "")),
            "feedback_data_path": str(event.get("feedback_data_path", "")),
            "prompt": hooks.build_resume_prompt(
                spec,
                event,
                continuous_research_enabled=hooks.continuous_research_mode_enabled(
                    config,
                    codex_session_id=str(spec.get("codex_session_id", "")).strip(),
                ),
            ),
            "resume_spec": resume_spec_snapshot,
            "resume_event": resume_event_snapshot,
        }
        existing_index = next(
            (
                index
                for index, item in enumerate(queued_notifications)
                if isinstance(item, dict) and str(item.get("task_id", "")).strip() == task_id
            ),
            -1,
        )
        if existing_index >= 0:
            queued_notifications[existing_index] = entry
        else:
            queued_notifications.append(entry)
        payload["task_id"] = task_id
        payload["task_key"] = str(spec.get("task_key", task_id))
        payload["agent_name"] = str(spec.get("agent_name", payload.get("agent_name", "")))
        payload["codex_session_id"] = str(spec.get("codex_session_id", payload.get("codex_session_id", "")))
        payload["proposal_path"] = str(spec.get("proposal_path", payload.get("proposal_path", "")))
        payload["proposal_source"] = str(spec.get("proposal_source", payload.get("proposal_source", "")))
        payload["proposal_owner"] = bool(spec.get("proposal_owner", payload.get("proposal_owner", False)))
        payload["closeout_proposal_dir"] = str(spec.get("closeout_proposal_dir", payload.get("closeout_proposal_dir", "")))
        payload["closeout_proposal_dir_source"] = str(
            spec.get("closeout_proposal_dir_source", payload.get("closeout_proposal_dir_source", ""))
        )
        payload["project_history_file"] = str(spec.get("project_history_file", payload.get("project_history_file", "")))
        payload["project_history_file_source"] = str(
            spec.get("project_history_file_source", payload.get("project_history_file_source", ""))
        )
        payload["workdir"] = str(spec.get("workdir", payload.get("workdir", "")))
        payload["remote_workdir"] = str(spec.get("remote_workdir", payload.get("remote_workdir", "")))
        payload["executor_name"] = str(spec.get("executor_name", payload.get("executor_name", "")))
        payload["executor_target"] = str(spec.get("executor_target", payload.get("executor_target", "")))
        payload["executor_identity_file"] = str(
            spec.get("executor_identity_file", payload.get("executor_identity_file", ""))
        )
        payload["executor_ssh_options"] = [
            str(item) for item in spec.get("executor_ssh_options", payload.get("executor_ssh_options", [])) if str(item).strip()
        ]
        payload["executor_remote_workdir_prefix"] = str(
            spec.get("executor_remote_workdir_prefix", payload.get("executor_remote_workdir_prefix", ""))
        )
        payload["executor_remote_home"] = str(spec.get("executor_remote_home", payload.get("executor_remote_home", "")))
        payload["executor_remote_codex_home"] = str(
            spec.get("executor_remote_codex_home", payload.get("executor_remote_codex_home", ""))
        )
        payload["executor_remote_codex_bin"] = str(
            spec.get("executor_remote_codex_bin", payload.get("executor_remote_codex_bin", "codex"))
        )
        payload["codex_exec_mode"] = str(spec.get("codex_exec_mode", payload.get("codex_exec_mode", "dangerous")))
        payload["resume_timeout_seconds"] = int(spec.get("resume_timeout_seconds", payload.get("resume_timeout_seconds", 7200)) or 7200)
        payload["fallback_provider"] = str(spec.get("fallback_provider", payload.get("fallback_provider", "")))
        payload["prompt_max_chars"] = int(spec.get("prompt_max_chars", payload.get("prompt_max_chars", 12000)) or 12000)
        payload["reason"] = reason
        payload["last_deferred_reason"] = reason
        payload["check_after_ts"] = time.time() + normalized_min_idle_seconds
        payload["min_idle_seconds"] = max(
            int(payload.get("min_idle_seconds", normalized_min_idle_seconds) or normalized_min_idle_seconds),
            normalized_min_idle_seconds,
        )
        payload["queued_notifications"] = queued_notifications
        payload["updated_at"] = hooks.utc_now()
        hooks.atomic_write_json(path, payload)
        hooks.merge_task_state(
            config,
            task_id,
            pending_feedback=True,
            followup_status="scheduled",
            followup_last_signal="",
            followup_last_action=f"queued_feedback_resume:{reason}",
            followup_stopped_at="",
            followup_last_message_path=str(message_path),
        )
        return {
            "followup_key": followup_key,
            "queue_depth": len(queued_notifications),
            "message_path": str(message_path),
        }

    return hooks.run_with_followup_lock(config, followup_key, update_queue)


def followup_processing_sort_key(
    followup: dict[str, Any],
    *,
    continuous_session_reminder_followup_type: str,
) -> tuple[int, float, str]:
    followup_type = str(followup.get("followup_type", "")).strip()
    if followup_type == "queued_feedback_resume":
        priority = 0
    elif followup_type == continuous_session_reminder_followup_type:
        priority = 2
    else:
        priority = 1
    try:
        check_after_ts = float(followup.get("check_after_ts", 0) or 0)
    except (TypeError, ValueError):
        check_after_ts = 0.0
    return (priority, check_after_ts, str(followup.get("followup_key", "")).strip())


def session_followup_present(
    followups: list[dict[str, Any]],
    session_id: str,
    *,
    exclude_followup_key: str = "",
) -> bool:
    normalized_session_id = str(session_id or "").strip()
    excluded_key = str(exclude_followup_key or "").strip()
    if not normalized_session_id:
        return False
    for payload in followups:
        if not isinstance(payload, dict) or not payload:
            continue
        if bool(payload.get("stopped", False)):
            continue
        followup_key = str(payload.get("followup_key", "")).strip()
        if excluded_key and followup_key == excluded_key:
            continue
        if str(payload.get("codex_session_id", "")).strip() == normalized_session_id:
            return True
    return False


def active_session_followup(
    followups: list[dict[str, Any]],
    session_id: str,
    *,
    continuous_session_reminder_followup_type: str,
    exclude_followup_key: str = "",
) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    excluded_key = str(exclude_followup_key or "").strip()
    if not normalized_session_id:
        return {}
    candidates: list[dict[str, Any]] = []
    for payload in followups:
        if not isinstance(payload, dict) or not payload or bool(payload.get("stopped", False)):
            continue
        followup_key = str(payload.get("followup_key", "")).strip()
        if excluded_key and followup_key == excluded_key:
            continue
        if str(payload.get("codex_session_id", "")).strip() != normalized_session_id:
            continue
        candidates.append(payload)
    if not candidates:
        return {}
    candidates.sort(
        key=lambda item: followup_processing_sort_key(
            item,
            continuous_session_reminder_followup_type=continuous_session_reminder_followup_type,
        )
    )
    return dict(candidates[0])


def followup_map_by_task_id(
    config: Any,
    *,
    hooks: FollowupRuntimeHooks,
) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for payload in load_followups(config, hooks=hooks):
        for task_id in followup_task_ids(payload, hooks=hooks):
            mapping[task_id] = payload
    return mapping


def newer_task_exists(
    config: Any,
    followup: dict[str, Any],
    *,
    hooks: FollowupRuntimeHooks,
) -> bool:
    target_session = str(followup.get("codex_session_id", ""))
    target_agent = str(followup.get("agent_name", ""))
    source_task_id = str(followup.get("task_id", ""))
    source_task = hooks.load_task_state(config, source_task_id)
    comparison_floor = (
        str(followup.get("created_at", "")).strip()
        or str(source_task.get("updated_at", "")).strip()
        or str(source_task.get("submitted_at", "")).strip()
    )
    comparison_floor_ts = hooks.parse_timestamp_to_unix(comparison_floor)
    for state in hooks.iter_all_task_states(config):
        if str(state.get("task_id", "")) == source_task_id:
            continue
        submitted_at = str(state.get("submitted_at", "")).strip()
        submitted_ts = hooks.parse_timestamp_to_unix(submitted_at)
        is_newer = False
        if submitted_ts is not None and comparison_floor_ts is not None:
            is_newer = submitted_ts > comparison_floor_ts
        elif comparison_floor_ts is None:
            is_newer = bool(submitted_at and submitted_at > comparison_floor)
        elif submitted_ts is not None:
            is_newer = True
        if target_session and str(state.get("codex_session_id", "")) == target_session and is_newer:
            return True
        if target_agent and str(state.get("agent_name", "")) == target_agent and is_newer:
            return True
    return False


def newer_task_exists_for_spec(
    config: Any,
    *,
    source_task_id: str,
    spec: dict[str, Any],
    hooks: FollowupRuntimeHooks,
) -> bool:
    return newer_task_exists(
        config,
        {
            "task_id": source_task_id,
            "codex_session_id": str(spec.get("codex_session_id", "")).strip(),
            "agent_name": str(spec.get("agent_name", "")).strip(),
        },
        hooks=hooks,
    )


def resolve_followup(config: Any, followup_key: str) -> None:
    for path in [followup_path(config, followup_key), followup_message_path(config, followup_key)]:
        if path.exists():
            path.unlink()


def defer_followup_retry(
    config: Any,
    followup: dict[str, Any],
    *,
    reason: str,
    retry_after_seconds: int,
    message_path: str = "",
    hooks: FollowupRuntimeHooks,
) -> None:
    followup, _rebound, _skip_processing = rebind_followup_to_current_task(config, followup, hooks=hooks)
    followup_key = str(followup.get("followup_key", "")).strip()
    if not followup_key:
        return
    updated_at = hooks.utc_now()
    followup["check_after_ts"] = time.time() + max(1, int(retry_after_seconds or 1))
    followup["last_action"] = f"deferred:{reason}"
    followup["last_checked_at"] = updated_at
    followup["updated_at"] = updated_at
    if reason:
        followup["reason"] = reason
        followup["last_deferred_reason"] = reason
    hooks.atomic_write_json(followup_path(config, followup_key), followup)
    hooks.append_followup_event_log(config, event="deferred", reason=reason, followup=followup)
    is_queued_feedback = str(followup.get("followup_type", "")).strip() == "queued_feedback_resume"
    for task_id in followup_task_ids(followup, hooks=hooks):
        existing_state = hooks.load_task_state(config, task_id) or {}
        hooks.merge_task_state(
            config,
            task_id,
            pending_feedback=is_queued_feedback or bool(existing_state.get("pending_feedback", False)),
            followup_status="scheduled",
            followup_last_action=f"deferred:{reason}",
            followup_last_message_path=message_path or str(followup_message_path(config, followup_key)),
        )


def resolve_followups_for_stop_signal(
    config: Any,
    *,
    session_id: str,
    agent_name: str,
    signal_value: str,
    reason: str,
    message_path: str = "",
    hooks: FollowupRuntimeHooks,
) -> list[str]:
    resolved_keys: list[str] = []
    stop_ts = hooks.utc_now()
    for followup in load_followups(config, hooks=hooks):
        followup_key = str(followup.get("followup_key", "")).strip()
        if not followup_key:
            continue
        followup_session_id = str(followup.get("codex_session_id", "")).strip()
        followup_agent_name = str(followup.get("agent_name", "")).strip()
        matches = False
        if session_id:
            matches = followup_session_id == session_id
        elif agent_name:
            matches = followup_agent_name == agent_name
        if not matches:
            continue
        for task_id in followup_task_ids(followup, hooks=hooks):
            hooks.merge_task_state(
                config,
                task_id,
                followup_status="stopped",
                followup_last_signal=signal_value,
                followup_last_action=reason,
                followup_stopped_at=stop_ts,
                followup_last_message_path=message_path or str(followup_message_path(config, followup_key)),
            )
        resolve_followup(config, followup_key)
        resolved_keys.append(followup_key)
    return resolved_keys
