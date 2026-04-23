from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class AutomationStateHooks:
    read_json: Callable[[Path, Any], Any]
    atomic_write_json: Callable[[Path, Any], None]
    normalize_timestamp_fields: Callable[[dict[str, Any]], dict[str, Any]]
    parse_boolish: Callable[[Any], bool]
    current_thread_info: Callable[[Any], dict[str, Any] | None]
    utc_now: Callable[[], str]
    canonicalize_taskboard_signal: Callable[[str], str]
    parse_timestamp_to_unix: Callable[[str], float | None]
    format_unix_timestamp: Callable[[float], str]
    retry_after_seconds_from_target: Callable[[float], int]
    continuous_research_mode_filename: str
    human_guidance_mode_filename: str
    default_human_guidance_lease_seconds: int
    continuous_research_idle_loop_threshold: int
    continuous_research_override_signals: set[str]


def continuous_research_mode_path(config: Any, *, hooks: AutomationStateHooks) -> Path:
    return Path(config.app_home) / hooks.continuous_research_mode_filename


def _normalize_automation_mode(value: Any, *, enabled: bool) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"continuous", "managed"}:
        return mode
    return "continuous" if enabled else "managed"


def normalize_continuous_research_mode_payload(payload: Any, *, hooks: AutomationStateHooks) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    sessions_raw = raw.get("sessions", {})
    sessions: dict[str, dict[str, Any]] = {}
    if isinstance(sessions_raw, dict):
        for raw_session_id, raw_state in sessions_raw.items():
            session_id = str(raw_session_id or "").strip()
            if not session_id:
                continue
            state = raw_state if isinstance(raw_state, dict) else {}
            enabled = hooks.parse_boolish(state.get("enabled", False), default=False)
            sessions[session_id] = {
                "enabled": enabled,
                "mode": _normalize_automation_mode(state.get("mode", ""), enabled=enabled),
                "updated_at": str(state.get("updated_at", "")),
                "updated_by": str(state.get("updated_by", "")),
                "source": str(state.get("source", "")),
                "waiting_state": str(state.get("waiting_state", "")),
                "waiting_reason": str(state.get("waiting_reason", "")),
                "waiting_since": str(state.get("waiting_since", "")),
                "waiting_evidence_token": str(state.get("waiting_evidence_token", "")),
                "last_evidence_token": str(state.get("last_evidence_token", "")),
                "stable_idle_repeat_count": max(0, int(state.get("stable_idle_repeat_count", 0) or 0)),
                "research_phase": str(state.get("research_phase", "")),
                "last_signal": str(state.get("last_signal", "")),
                "next_action_hash": str(state.get("next_action_hash", "")),
                "next_action_text": str(state.get("next_action_text", "")),
                "next_action_state": str(state.get("next_action_state", "")),
                "next_action_source_path": str(state.get("next_action_source_path", "")),
                "next_action_source_updated_at": str(state.get("next_action_source_updated_at", "")),
                "next_action_repeat_count": max(0, int(state.get("next_action_repeat_count", 0) or 0)),
                "binding_scope": str(state.get("binding_scope", "")),
                "bound_workdir": str(state.get("bound_workdir", "")),
                "bound_remote_workdir": str(state.get("bound_remote_workdir", "")),
                "proposal_path": str(state.get("proposal_path", "")),
                "proposal_source": str(state.get("proposal_source", "")),
                "proposal_owner": hooks.parse_boolish(state.get("proposal_owner", False), default=False),
                "closeout_proposal_dir": str(state.get("closeout_proposal_dir", "")),
                "closeout_proposal_dir_source": str(state.get("closeout_proposal_dir_source", "")),
                "project_history_file": str(state.get("project_history_file", "")),
                "project_history_file_source": str(state.get("project_history_file_source", "")),
                "handoff_file": str(state.get("handoff_file", "")),
                "handoff_source": str(state.get("handoff_source", "")),
            }
    enabled_sessions = sorted(session_id for session_id, state in sessions.items() if bool(state.get("enabled", False)))
    return hooks.normalize_timestamp_fields(
        {
            "version": int(raw.get("version", 1) or 1),
            "legacy_enabled": hooks.parse_boolish(raw.get("enabled", False), default=False),
            "default_codex_session_id": str(raw.get("default_codex_session_id", "")),
            "updated_at": str(raw.get("updated_at", "")),
            "updated_by": str(raw.get("updated_by", "")),
            "source": str(raw.get("source", "")),
            "sessions": sessions,
            "enabled_sessions": enabled_sessions,
        }
    )


def load_continuous_research_mode(config: Any, *, hooks: AutomationStateHooks, codex_session_id: str = "") -> dict[str, Any]:
    payload = normalize_continuous_research_mode_payload(
        hooks.read_json(continuous_research_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    requested_session_id = str(codex_session_id or "").strip()
    default_session_id = str(payload.get("default_codex_session_id", "")).strip()
    sessions = dict(payload.get("sessions", {}))
    target_session_id = requested_session_id or default_session_id
    enabled = False
    target_state: dict[str, Any] = {}
    if target_session_id and target_session_id in sessions:
        target_state = dict(sessions[target_session_id])
        enabled = bool(target_state.get("enabled", False))
    elif target_session_id and sessions:
        enabled = False
    elif default_session_id and default_session_id in sessions:
        target_session_id = default_session_id
        target_state = dict(sessions[default_session_id])
        enabled = bool(target_state.get("enabled", False))
    elif len(sessions) == 1:
        target_session_id = next(iter(sessions))
        target_state = dict(sessions[target_session_id])
        enabled = bool(target_state.get("enabled", False))
    elif sessions:
        enabled = bool(payload.get("enabled_sessions"))
    else:
        enabled = bool(payload.get("legacy_enabled", False))
    mode = _normalize_automation_mode(target_state.get("mode", "") if target_state else "", enabled=enabled)
    return {
        "enabled": enabled,
        "mode": mode,
        "target_codex_session_id": target_session_id,
        "target_session_state": target_state,
        "legacy_enabled": bool(payload.get("legacy_enabled", False)),
        "default_codex_session_id": default_session_id,
        "updated_at": str(payload.get("updated_at", "")),
        "updated_by": str(payload.get("updated_by", "")),
        "source": str(payload.get("source", "")),
        "sessions": sessions,
        "enabled_sessions": list(payload.get("enabled_sessions", [])),
    }


def continuous_research_session_state(config: Any, codex_session_id: str, *, hooks: AutomationStateHooks) -> dict[str, Any]:
    session_id = str(codex_session_id or "").strip()
    if not session_id:
        return {}
    payload = normalize_continuous_research_mode_payload(
        hooks.read_json(continuous_research_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    session_state = payload.get("sessions", {}).get(session_id, {})
    return dict(session_state) if isinstance(session_state, dict) else {}


def resolve_continuous_research_target_session_id(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    raw_session_id: Any = "",
    environ: Any | None = None,
) -> tuple[str, str]:
    session_id = str(raw_session_id or "").strip()
    if session_id:
        return session_id, "arg"
    payload = load_continuous_research_mode(config, hooks=hooks)
    default_session_id = str(payload.get("default_codex_session_id", "")).strip()
    if default_session_id:
        return default_session_id, "stored_default"
    current = hooks.current_thread_info(config, environ=environ)
    if current is not None:
        current_session_id = str(current.get("current_codex_session_id", "")).strip()
        if current_session_id:
            return current_session_id, str(current.get("resolved_from") or "current_thread")
    enabled_sessions = [str(item).strip() for item in payload.get("enabled_sessions", []) if str(item).strip()]
    if len(enabled_sessions) == 1:
        return enabled_sessions[0], "sole_enabled_session"
    sessions = [str(item).strip() for item in payload.get("sessions", {}).keys() if str(item).strip()]
    if len(sessions) == 1:
        return sessions[0], "sole_session"
    return "", ""


def write_continuous_research_mode_payload(
    config: Any,
    payload: dict[str, Any],
    *,
    hooks: AutomationStateHooks,
    verify_session_id: str = "",
    verify_enabled: bool | None = None,
    verify_session_present: bool | None = None,
    verify_default_session_id: str | None = None,
) -> dict[str, Any]:
    hooks.atomic_write_json(continuous_research_mode_path(config, hooks=hooks), payload)
    persisted = normalize_continuous_research_mode_payload(
        hooks.read_json(continuous_research_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    target_session_id = str(verify_session_id or payload.get("default_codex_session_id", "") or "").strip()
    if verify_default_session_id is not None:
        actual_default = str(persisted.get("default_codex_session_id", "")).strip()
        if actual_default != str(verify_default_session_id or "").strip():
            raise RuntimeError(
                f"continuous-mode persistence mismatch: expected default session {verify_default_session_id!r}, got {actual_default!r}"
            )
    if target_session_id:
        sessions = persisted.get("sessions", {})
        session_present = target_session_id in sessions
        if verify_session_present is not None and session_present != bool(verify_session_present):
            expected_text = "present" if verify_session_present else "absent"
            actual_text = "present" if session_present else "absent"
            raise RuntimeError(
                f"continuous-mode persistence mismatch: expected session {target_session_id!r} to be {expected_text}, got {actual_text}"
            )
        if verify_enabled is not None:
            actual_enabled = bool((sessions.get(target_session_id, {}) or {}).get("enabled", False))
            if actual_enabled != bool(verify_enabled):
                raise RuntimeError(
                    f"continuous-mode persistence mismatch: expected session {target_session_id!r} enabled={bool(verify_enabled)}, got {actual_enabled}"
                )
    return load_continuous_research_mode(config, hooks=hooks, codex_session_id=target_session_id)


def update_continuous_research_session_state(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str,
    updated_by: str = "followup",
    source: str = "",
    **updates: Any,
) -> dict[str, Any]:
    session_id = str(codex_session_id or "").strip()
    if not session_id:
        return load_continuous_research_mode(config, hooks=hooks)
    current = normalize_continuous_research_mode_payload(
        hooks.read_json(continuous_research_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    sessions = dict(current.get("sessions", {}))
    state = dict(sessions.get(session_id, {}))
    state.update(
        {
            "enabled": bool(state.get("enabled", False)),
            "mode": _normalize_automation_mode(state.get("mode", ""), enabled=bool(state.get("enabled", False))),
            "updated_at": hooks.utc_now(),
            "updated_by": str(updated_by or "followup"),
            "source": str(source or "session-state-update"),
        }
    )
    state.update(updates)
    sessions[session_id] = state
    payload = {
        "version": 2,
        "enabled": False,
        "default_codex_session_id": str(current.get("default_codex_session_id", "")).strip() or session_id,
        "updated_at": hooks.utc_now(),
        "updated_by": str(updated_by or "followup"),
        "source": str(source or "session-state-update"),
        "sessions": sessions,
    }
    return write_continuous_research_mode_payload(
        config,
        payload,
        hooks=hooks,
        verify_session_id=session_id,
        verify_session_present=True,
    )


def clear_continuous_research_session_waiting_state(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str,
    evidence_token: str = "",
    last_signal: str = "",
    stable_idle_repeat_count: int = 0,
    updated_by: str = "followup",
    source: str = "",
    **updates: Any,
) -> dict[str, Any]:
    normalized_last_signal = hooks.canonicalize_taskboard_signal(last_signal)
    return update_continuous_research_session_state(
        config,
        hooks=hooks,
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source or "continuous-session-clear-waiting",
        waiting_state="",
        waiting_reason="",
        waiting_since="",
        waiting_evidence_token="",
        last_evidence_token=str(evidence_token or ""),
        stable_idle_repeat_count=max(0, int(stable_idle_repeat_count or 0)),
        last_signal=normalized_last_signal,
        **updates,
    )


def continuous_research_mode_enabled(config: Any, *, hooks: AutomationStateHooks, codex_session_id: str = "") -> bool:
    return bool(load_continuous_research_mode(config, hooks=hooks, codex_session_id=codex_session_id).get("enabled", False))


def set_continuous_research_mode(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    enabled: bool,
    codex_session_id: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    target_session_id = str(codex_session_id or "").strip()
    current = normalize_continuous_research_mode_payload(
        hooks.read_json(continuous_research_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    timestamp = hooks.utc_now()
    if target_session_id:
        sessions = dict(current.get("sessions", {}))
        session_state = dict(sessions.get(target_session_id, {}))
        session_state.update(
            {
                "enabled": bool(enabled),
                "mode": "continuous" if bool(enabled) else "managed",
                "updated_at": timestamp,
                "updated_by": str(updated_by or "cli"),
                "source": str(source or "manual"),
            }
        )
        sessions[target_session_id] = session_state
        payload = {
            "version": 2,
            "enabled": False,
            "default_codex_session_id": target_session_id,
            "updated_at": timestamp,
            "updated_by": str(updated_by or "cli"),
            "source": str(source or "manual"),
            "sessions": sessions,
        }
        return write_continuous_research_mode_payload(
            config,
            payload,
            hooks=hooks,
            verify_session_id=target_session_id,
            verify_enabled=bool(enabled),
            verify_session_present=True,
            verify_default_session_id=target_session_id,
        )
    payload = {
        "version": 1,
        "enabled": bool(enabled),
        "updated_at": timestamp,
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "manual"),
    }
    return write_continuous_research_mode_payload(config, payload, hooks=hooks, verify_default_session_id="")


def toggle_continuous_research_mode(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    current = load_continuous_research_mode(config, hooks=hooks, codex_session_id=codex_session_id)
    return set_continuous_research_mode(
        config,
        hooks=hooks,
        enabled=not bool(current.get("enabled", False)),
        codex_session_id=str(codex_session_id or current.get("target_codex_session_id", "")).strip(),
        updated_by=updated_by,
        source=source or "toggle",
    )


def bind_continuous_research_mode_session(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    target_session_id = str(codex_session_id or "").strip()
    if not target_session_id:
        raise ValueError("Missing codex_session_id for continuous-mode bind.")
    current = normalize_continuous_research_mode_payload(
        hooks.read_json(continuous_research_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    sessions = dict(current.get("sessions", {}))
    timestamp = hooks.utc_now()
    if not sessions and bool(current.get("legacy_enabled", False)):
        sessions[target_session_id] = {
            **dict(sessions.get(target_session_id, {})),
            "enabled": True,
            "updated_at": str(current.get("updated_at", "")) or timestamp,
            "updated_by": str(current.get("updated_by", "")) or str(updated_by or "cli"),
            "source": str(current.get("source", "")) or "migrated_from_legacy_global",
        }
    elif target_session_id not in sessions:
        sessions[target_session_id] = {
            "enabled": False,
            "mode": "managed",
            "updated_at": timestamp,
            "updated_by": str(updated_by or "cli"),
            "source": str(source or "continuous-mode:bind"),
        }
    payload = {
        "version": 2,
        "enabled": False,
        "default_codex_session_id": target_session_id,
        "updated_at": timestamp,
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "continuous-mode:bind"),
        "sessions": sessions,
    }
    return write_continuous_research_mode_payload(
        config,
        payload,
        hooks=hooks,
        verify_session_id=target_session_id,
        verify_session_present=target_session_id in sessions,
        verify_default_session_id=target_session_id,
    )


def clear_continuous_research_mode_session(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    target_session_id = str(codex_session_id or "").strip()
    if not target_session_id:
        raise ValueError("Missing codex_session_id for continuous-mode clear-session.")
    current = normalize_continuous_research_mode_payload(
        hooks.read_json(continuous_research_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    sessions = dict(current.get("sessions", {}))
    sessions.pop(target_session_id, None)
    default_session_id = str(current.get("default_codex_session_id", "")).strip()
    if default_session_id == target_session_id:
        default_session_id = ""
    payload = {
        "version": 2,
        "enabled": False,
        "default_codex_session_id": default_session_id,
        "updated_at": hooks.utc_now(),
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "continuous-mode:clear-session"),
        "sessions": sessions,
    }
    return write_continuous_research_mode_payload(
        config,
        payload,
        hooks=hooks,
        verify_session_id=target_session_id,
        verify_enabled=False,
        verify_session_present=False,
        verify_default_session_id=default_session_id,
    )


def clear_all_continuous_research_mode(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    payload = {
        "version": 2,
        "enabled": False,
        "default_codex_session_id": "",
        "updated_at": hooks.utc_now(),
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "continuous-mode:clear-all"),
        "sessions": {},
    }
    return write_continuous_research_mode_payload(config, payload, hooks=hooks, verify_default_session_id="")


def should_override_stop_signal_with_continuous_research(
    config: Any,
    signal_value: str,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str = "",
) -> bool:
    return bool(
        signal_value in hooks.continuous_research_override_signals
        and continuous_research_mode_enabled(config, hooks=hooks, codex_session_id=codex_session_id)
    )


def continuous_research_mode_label(config: Any, *, hooks: AutomationStateHooks) -> str:
    payload = load_continuous_research_mode(config, hooks=hooks)
    return "continuous" if str(payload.get("mode", "")).strip() == "continuous" else "managed"


def continuous_research_enabled_session_ids(config: Any, *, hooks: AutomationStateHooks) -> list[str]:
    payload = normalize_continuous_research_mode_payload(
        hooks.read_json(continuous_research_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    enabled_sessions = sorted(
        session_id
        for session_id, state in payload.get("sessions", {}).items()
        if bool((state or {}).get("enabled", False))
    )
    if enabled_sessions:
        return enabled_sessions
    loaded = load_continuous_research_mode(config, hooks=hooks)
    target_session_id = str(loaded.get("target_codex_session_id", "")).strip()
    if target_session_id and bool(loaded.get("enabled", False)):
        return [target_session_id]
    return []


def automation_mode(config: Any, *, hooks: AutomationStateHooks, codex_session_id: str = "") -> dict[str, Any]:
    payload = load_continuous_research_mode(config, hooks=hooks, codex_session_id=codex_session_id)
    target_state = payload.get("target_session_state", {}) if isinstance(payload.get("target_session_state", {}), dict) else {}
    mode = _normalize_automation_mode(target_state.get("mode", ""), enabled=bool(payload.get("enabled", False)))
    return {
        **payload,
        "mode": mode,
        "target_session_state": (
            {
                **target_state,
                "mode": mode,
                "enabled": bool(payload.get("enabled", False)),
            }
            if target_state
            else {}
        ),
    }


def automation_mode_label(config: Any, *, hooks: AutomationStateHooks, codex_session_id: str = "") -> str:
    return str(automation_mode(config, hooks=hooks, codex_session_id=codex_session_id).get("mode", "managed"))


def automation_mode_is_managed(config: Any, *, hooks: AutomationStateHooks, codex_session_id: str = "") -> bool:
    payload = automation_mode(config, hooks=hooks, codex_session_id=codex_session_id)
    target_state = payload.get("target_session_state", {}) if isinstance(payload.get("target_session_state", {}), dict) else {}
    if not target_state:
        return False
    return str(payload.get("mode", "")).strip() == "managed"


def set_automation_mode(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    mode: str,
    codex_session_id: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    normalized_mode = _normalize_automation_mode(mode, enabled=(str(mode).strip().lower() == "continuous"))
    return set_continuous_research_mode(
        config,
        hooks=hooks,
        enabled=normalized_mode == "continuous",
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source or f"automation-mode:{normalized_mode}",
    )


def toggle_automation_mode(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    current_mode = automation_mode_label(config, hooks=hooks, codex_session_id=codex_session_id)
    target_mode = "managed" if current_mode == "continuous" else "continuous"
    return set_automation_mode(
        config,
        hooks=hooks,
        mode=target_mode,
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source or "automation-mode:toggle",
    )


def human_guidance_mode_path(config: Any, *, hooks: AutomationStateHooks) -> Path:
    return Path(config.app_home) / hooks.human_guidance_mode_filename


def normalize_human_guidance_mode_payload(payload: Any, *, hooks: AutomationStateHooks) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    sessions_raw = raw.get("sessions", {})
    sessions: dict[str, dict[str, Any]] = {}
    active_sessions: list[str] = []
    now_ts = time.time()
    if isinstance(sessions_raw, dict):
        for raw_session_id, raw_state in sessions_raw.items():
            session_id = str(raw_session_id or "").strip()
            if not session_id:
                continue
            state = raw_state if isinstance(raw_state, dict) else {}
            paused = hooks.parse_boolish(state.get("paused", False), default=False)
            paused_until = str(state.get("paused_until", "")).strip()
            paused_until_ts = hooks.parse_timestamp_to_unix(paused_until) if paused_until else None
            active = bool(paused and (paused_until_ts is None or paused_until_ts > now_ts))
            normalized_state = {
                "paused": paused,
                "active": active,
                "paused_until": paused_until,
                "paused_until_ts": paused_until_ts,
                "reason": str(state.get("reason", "")),
                "updated_at": str(state.get("updated_at", "")),
                "updated_by": str(state.get("updated_by", "")),
                "source": str(state.get("source", "")),
            }
            sessions[session_id] = normalized_state
            if active:
                active_sessions.append(session_id)
    active_sessions = sorted(active_sessions)
    return hooks.normalize_timestamp_fields(
        {
            "version": int(raw.get("version", 1) or 1),
            "default_codex_session_id": str(raw.get("default_codex_session_id", "")),
            "updated_at": str(raw.get("updated_at", "")),
            "updated_by": str(raw.get("updated_by", "")),
            "source": str(raw.get("source", "")),
            "sessions": sessions,
            "active_sessions": active_sessions,
        }
    )


def load_human_guidance_mode(config: Any, *, hooks: AutomationStateHooks, codex_session_id: str = "") -> dict[str, Any]:
    payload = normalize_human_guidance_mode_payload(
        hooks.read_json(human_guidance_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    requested_session_id = str(codex_session_id or "").strip()
    default_session_id = str(payload.get("default_codex_session_id", "")).strip()
    sessions = dict(payload.get("sessions", {}))
    target_session_id = requested_session_id or default_session_id
    active = False
    target_state: dict[str, Any] = {}
    if target_session_id and target_session_id in sessions:
        target_state = dict(sessions[target_session_id])
        active = bool(target_state.get("active", False))
    elif default_session_id and default_session_id in sessions:
        target_session_id = default_session_id
        target_state = dict(sessions[default_session_id])
        active = bool(target_state.get("active", False))
    elif len(sessions) == 1:
        target_session_id = next(iter(sessions))
        target_state = dict(sessions[target_session_id])
        active = bool(target_state.get("active", False))
    return {
        "active": active,
        "target_codex_session_id": target_session_id,
        "target_session_state": target_state,
        "default_codex_session_id": default_session_id,
        "updated_at": str(payload.get("updated_at", "")),
        "updated_by": str(payload.get("updated_by", "")),
        "source": str(payload.get("source", "")),
        "sessions": sessions,
        "active_sessions": list(payload.get("active_sessions", [])),
    }


def resolve_human_guidance_target_session_id(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    raw_session_id: Any = "",
    environ: Any | None = None,
) -> tuple[str, str]:
    session_id = str(raw_session_id or "").strip()
    if session_id:
        return session_id, "arg"
    payload = load_human_guidance_mode(config, hooks=hooks)
    default_session_id = str(payload.get("default_codex_session_id", "")).strip()
    if default_session_id:
        return default_session_id, "stored_default"
    current = hooks.current_thread_info(config, environ=environ)
    if current is not None:
        current_session_id = str(current.get("current_codex_session_id", "")).strip()
        if current_session_id:
            return current_session_id, str(current.get("resolved_from") or "current_thread")
    active_sessions = [str(item).strip() for item in payload.get("active_sessions", []) if str(item).strip()]
    if len(active_sessions) == 1:
        return active_sessions[0], "sole_active_session"
    sessions = [str(item).strip() for item in payload.get("sessions", {}).keys() if str(item).strip()]
    if len(sessions) == 1:
        return sessions[0], "sole_session"
    return "", ""


def write_human_guidance_mode_payload(config: Any, payload: dict[str, Any], *, hooks: AutomationStateHooks) -> dict[str, Any]:
    hooks.atomic_write_json(human_guidance_mode_path(config, hooks=hooks), payload)
    target_session_id = str(payload.get("default_codex_session_id", "") or "").strip()
    return load_human_guidance_mode(config, hooks=hooks, codex_session_id=target_session_id)


def set_human_guidance_mode(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    active: bool,
    codex_session_id: str = "",
    lease_seconds: int,
    reason: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    target_session_id = str(codex_session_id or "").strip()
    if not target_session_id:
        raise ValueError("Missing codex_session_id for human-guidance session-scoped action.")
    current = normalize_human_guidance_mode_payload(
        hooks.read_json(human_guidance_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    sessions = dict(current.get("sessions", {}))
    timestamp = hooks.utc_now()
    paused_until = ""
    if active:
        normalized_lease_seconds = max(30, int(lease_seconds or hooks.default_human_guidance_lease_seconds))
        paused_until = hooks.format_unix_timestamp(time.time() + normalized_lease_seconds)
    sessions[target_session_id] = {
        "paused": bool(active),
        "paused_until": paused_until,
        "reason": str(reason or sessions.get(target_session_id, {}).get("reason", "")),
        "updated_at": timestamp,
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "human-guidance"),
    }
    payload = {
        "version": 1,
        "default_codex_session_id": target_session_id,
        "updated_at": timestamp,
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "human-guidance"),
        "sessions": sessions,
    }
    return write_human_guidance_mode_payload(config, payload, hooks=hooks)


def toggle_human_guidance_mode(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str = "",
    lease_seconds: int,
    reason: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    current = load_human_guidance_mode(config, hooks=hooks, codex_session_id=codex_session_id)
    return set_human_guidance_mode(
        config,
        hooks=hooks,
        active=not bool(current.get("active", False)),
        codex_session_id=str(codex_session_id or current.get("target_codex_session_id", "")).strip(),
        lease_seconds=lease_seconds,
        reason=reason,
        updated_by=updated_by,
        source=source or "toggle",
    )


def bind_human_guidance_mode_session(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    target_session_id = str(codex_session_id or "").strip()
    if not target_session_id:
        raise ValueError("Missing codex_session_id for human-guidance bind.")
    current = normalize_human_guidance_mode_payload(
        hooks.read_json(human_guidance_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    payload = {
        "version": 1,
        "default_codex_session_id": target_session_id,
        "updated_at": hooks.utc_now(),
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "human-guidance:bind"),
        "sessions": dict(current.get("sessions", {})),
    }
    return write_human_guidance_mode_payload(config, payload, hooks=hooks)


def clear_human_guidance_mode_session(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    codex_session_id: str,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    target_session_id = str(codex_session_id or "").strip()
    if not target_session_id:
        raise ValueError("Missing codex_session_id for human-guidance clear-session.")
    current = normalize_human_guidance_mode_payload(
        hooks.read_json(human_guidance_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    sessions = dict(current.get("sessions", {}))
    sessions.pop(target_session_id, None)
    default_session_id = str(current.get("default_codex_session_id", "")).strip()
    if default_session_id == target_session_id:
        default_session_id = ""
    payload = {
        "version": 1,
        "default_codex_session_id": default_session_id,
        "updated_at": hooks.utc_now(),
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "human-guidance:clear-session"),
        "sessions": sessions,
    }
    return write_human_guidance_mode_payload(config, payload, hooks=hooks)


def clear_all_human_guidance_mode(
    config: Any,
    *,
    hooks: AutomationStateHooks,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    payload = {
        "version": 1,
        "default_codex_session_id": "",
        "updated_at": hooks.utc_now(),
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "human-guidance:clear-all"),
        "sessions": {},
    }
    return write_human_guidance_mode_payload(config, payload, hooks=hooks)


def human_guidance_mode_active(config: Any, *, hooks: AutomationStateHooks, codex_session_id: str = "") -> bool:
    return bool(load_human_guidance_mode(config, hooks=hooks, codex_session_id=codex_session_id).get("active", False))


def human_guidance_retry_after_seconds(config: Any, *, hooks: AutomationStateHooks, codex_session_id: str = "") -> int:
    payload = load_human_guidance_mode(config, hooks=hooks, codex_session_id=codex_session_id)
    target_state = payload.get("target_session_state", {}) if isinstance(payload.get("target_session_state", {}), dict) else {}
    paused_until_ts = target_state.get("paused_until_ts")
    if paused_until_ts is None:
        return hooks.default_human_guidance_lease_seconds
    return max(30, hooks.retry_after_seconds_from_target(float(paused_until_ts)))


def human_guidance_mode_label(config: Any, *, hooks: AutomationStateHooks) -> str:
    payload = load_human_guidance_mode(config, hooks=hooks)
    return "on" if bool(payload.get("active", False) or payload.get("active_sessions")) else "off"


def human_guidance_active_session_ids(config: Any, *, hooks: AutomationStateHooks) -> list[str]:
    payload = normalize_human_guidance_mode_payload(
        hooks.read_json(human_guidance_mode_path(config, hooks=hooks), {}),
        hooks=hooks,
    )
    return [str(item).strip() for item in payload.get("active_sessions", []) if str(item).strip()]
