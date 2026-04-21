from __future__ import annotations

import glob
import json
import math
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class SessionRuntimeHooks:
    find_thread_info: Callable[[Any, str], dict[str, Any] | None]
    should_use_executor_codex: Callable[[dict[str, Any] | None], bool]
    latest_remote_session_activity_ts: Callable[[dict[str, Any], str], float]
    parse_timestamp_to_unix: Callable[[Any], float | None]
    read_pid_cmdline: Callable[[int], str]
    active_feedback_entries_for_session: Callable[[Any, str], list[dict[str, Any]]]
    canonicalize_taskboard_signal: Callable[[str], str]
    extract_taskboard_protocol_footer: Callable[[str], dict[str, Any]]
    list_proc_entries: Callable[[], list[Path]]
    now_ts: Callable[[], float]
    taskboard_final_signal_values: set[str]
    rate_limit_patterns: tuple[str, ...]
    session_busy_patterns: tuple[str, ...]
    platform_error_signatures: tuple[dict[str, Any], ...]
    max_rollout_output_busy_tail_lines: int
    default_session_output_busy_retry_seconds: int
    default_session_output_busy_open_turn_stall_seconds: int
    default_platform_error_human_retry_seconds: int
    default_resume_retry_seconds: int
    rollout_fallback_entry_grace_seconds: float
    rollout_fallback_mtime_grace_seconds: float


def empty_busy_snapshot() -> dict[str, Any]:
    return {
        "busy": False,
        "detail": "",
        "retry_after_seconds": 0,
        "latest_activity_ts": 0.0,
        "rollout_snapshot": {},
        "active_feedback_entries": [],
        "active_resume_pids": [],
    }


def empty_platform_error_details() -> dict[str, Any]:
    return {
        "kind": "",
        "retryable": False,
        "needs_human_attention": False,
        "summary": "",
        "matched_pattern": "",
    }


def command_signal_source_text(last_message_text: str, stdout: str, stderr: str) -> str:
    return str(last_message_text or f"{stdout}\n{stderr}")


def rollout_candidates_for_session(
    config: Any,
    session_id: str,
    *,
    hooks: SessionRuntimeHooks,
) -> list[Path]:
    thread_info = hooks.find_thread_info(config, session_id)
    patterns = [
        str(config.codex_home / "sessions" / "*" / "*" / "*" / f"rollout-*-{session_id}.jsonl"),
        str(config.codex_home / "archived_sessions" / f"rollout-*-{session_id}.jsonl"),
    ]
    candidates: list[Path] = []
    if thread_info is not None:
        rollout_path = str(thread_info.get("rollout_path", "")).strip()
        if rollout_path:
            candidates.append(Path(rollout_path))
    for pattern in patterns:
        candidates.extend(Path(item) for item in glob.glob(pattern))
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in sorted(candidates):
        path_text = str(path)
        if path_text in seen:
            continue
        seen.add(path_text)
        deduped.append(path)
    return deduped


def latest_session_activity_ts(
    config: Any,
    session_id: str,
    spec: dict[str, Any] | None = None,
    *,
    hooks: SessionRuntimeHooks,
) -> float:
    if hooks.should_use_executor_codex(spec):
        return hooks.latest_remote_session_activity_ts(spec or {}, session_id)
    latest = 0.0
    thread_info = hooks.find_thread_info(config, session_id)
    if thread_info is not None:
        try:
            latest = max(latest, float(thread_info.get("updated_at", 0) or 0))
        except (TypeError, ValueError):
            pass
    for path in rollout_candidates_for_session(config, session_id, hooks=hooks):
        try:
            latest = max(latest, path.stat().st_mtime)
        except FileNotFoundError:
            continue
    return latest


def latest_local_rollout_output_snapshot(
    config: Any,
    session_id: str,
    *,
    hooks: SessionRuntimeHooks,
) -> dict[str, Any]:
    latest_path: Path | None = None
    latest_mtime = 0.0
    for path in rollout_candidates_for_session(config, session_id, hooks=hooks):
        try:
            path_mtime = float(path.stat().st_mtime)
        except FileNotFoundError:
            continue
        if latest_path is None or path_mtime >= latest_mtime:
            latest_path = path
            latest_mtime = path_mtime
    snapshot = {
        "path": str(latest_path) if latest_path is not None else "",
        "path_mtime": latest_mtime,
        "last_entry_ts": 0.0,
        "last_entry_type": "",
        "last_payload_type": "",
        "last_turn_context_ts": 0.0,
        "last_task_complete_ts": 0.0,
        "last_assistant_message_ts": 0.0,
        "turn_in_progress": False,
    }
    if latest_path is None:
        return snapshot

    tail_lines: deque[str] = deque(maxlen=hooks.max_rollout_output_busy_tail_lines)
    try:
        with latest_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if line:
                    tail_lines.append(line)
    except OSError:
        return snapshot

    for raw_line in tail_lines:
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        entry_ts = float(hooks.parse_timestamp_to_unix(entry.get("timestamp")) or 0.0)
        entry_type = str(entry.get("type", "")).strip()
        if entry_ts > 0:
            snapshot["last_entry_ts"] = entry_ts
        if entry_type:
            snapshot["last_entry_type"] = entry_type
        if entry_type == "turn_context" and entry_ts > 0:
            snapshot["last_turn_context_ts"] = max(snapshot["last_turn_context_ts"], entry_ts)
        payload = entry.get("payload", {})
        if not isinstance(payload, dict):
            continue
        payload_type = str(payload.get("type", "")).strip()
        if payload_type:
            snapshot["last_payload_type"] = payload_type
        if payload_type == "task_complete" and entry_ts > 0:
            snapshot["last_task_complete_ts"] = max(snapshot["last_task_complete_ts"], entry_ts)
        elif payload_type == "message" and str(payload.get("role", "")).strip() == "assistant" and entry_ts > 0:
            snapshot["last_assistant_message_ts"] = max(snapshot["last_assistant_message_ts"], entry_ts)

    if snapshot["last_entry_ts"] <= 0 and latest_mtime > 0:
        snapshot["last_entry_ts"] = latest_mtime
    snapshot["turn_in_progress"] = bool(
        snapshot["last_turn_context_ts"] > 0
        and snapshot["last_turn_context_ts"] > snapshot["last_task_complete_ts"]
    )
    return snapshot


def active_codex_resume_pids_for_session(
    session_id: str,
    *,
    hooks: SessionRuntimeHooks,
) -> list[int]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return []
    matches: list[int] = []
    try:
        proc_entries = list(hooks.list_proc_entries())
    except Exception:
        return []
    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        cmdline = hooks.read_pid_cmdline(pid)
        if not cmdline:
            continue
        normalized_cmdline = f" {cmdline} "
        if " exec resume " not in normalized_cmdline:
            continue
        if normalized_session_id not in normalized_cmdline:
            continue
        if "codex" not in normalized_cmdline:
            continue
        matches.append(pid)
    return sorted(set(matches))


def session_output_busy_snapshot(
    config: Any,
    session_id: str,
    *,
    hooks: SessionRuntimeHooks,
    spec: dict[str, Any] | None = None,
    activity_window_seconds: int,
) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return empty_busy_snapshot()
    current_now_ts = float(hooks.now_ts())
    active_feedback_entries = hooks.active_feedback_entries_for_session(config, normalized_session_id)
    active_resume_pids = active_codex_resume_pids_for_session(normalized_session_id, hooks=hooks)
    latest_activity = latest_session_activity_ts(config, normalized_session_id, spec, hooks=hooks)
    rollout_snapshot = (
        latest_local_rollout_output_snapshot(config, normalized_session_id, hooks=hooks)
        if not hooks.should_use_executor_codex(spec)
        else {}
    )
    rollout_last_entry_ts = float(rollout_snapshot.get("last_entry_ts", 0.0) or 0.0)
    last_assistant_message_ts = float(rollout_snapshot.get("last_assistant_message_ts", 0.0) or 0.0)
    last_task_complete_ts = float(rollout_snapshot.get("last_task_complete_ts", 0.0) or 0.0)
    if rollout_last_entry_ts > 0:
        latest_activity = max(latest_activity, rollout_last_entry_ts)
    if last_assistant_message_ts > 0:
        latest_activity = max(latest_activity, last_assistant_message_ts)
    activity_window = max(1, int(activity_window_seconds or 0))
    activity_age_seconds = current_now_ts - latest_activity if latest_activity else 0.0
    recent_output_busy = bool(latest_activity and activity_age_seconds >= 0 and activity_age_seconds < activity_window)
    rollout_activity_age_seconds = current_now_ts - rollout_last_entry_ts if rollout_last_entry_ts else 0.0
    assistant_message_age_seconds = current_now_ts - last_assistant_message_ts if last_assistant_message_ts else 0.0
    assistant_message_in_open_turn = bool(
        rollout_snapshot.get("turn_in_progress", False)
        and last_assistant_message_ts > last_task_complete_ts
    )
    active_rollout_turn = bool(
        rollout_snapshot.get("turn_in_progress", False)
        and (
            (
                rollout_last_entry_ts
                and rollout_activity_age_seconds >= 0
                and rollout_activity_age_seconds < activity_window
            )
            or (
                assistant_message_in_open_turn
                and assistant_message_age_seconds >= 0
                and assistant_message_age_seconds < hooks.default_session_output_busy_open_turn_stall_seconds
            )
            or (
                rollout_last_entry_ts
                and str(rollout_snapshot.get("last_payload_type", "")).strip()
                not in {"", "message", "task_complete", "context_compacted"}
                and rollout_activity_age_seconds < hooks.default_session_output_busy_open_turn_stall_seconds
            )
        )
    )
    retry_after_seconds = 0
    detail = ""
    if active_feedback_entries:
        detail = "active_feedback_runtime"
        retry_after_seconds = hooks.default_session_output_busy_retry_seconds
    elif active_resume_pids:
        detail = "active_codex_resume_process"
        retry_after_seconds = hooks.default_session_output_busy_retry_seconds
    elif active_rollout_turn:
        detail = "active_rollout_turn"
        if rollout_activity_age_seconds < activity_window:
            retry_after_seconds = retry_after_seconds_from_target(
                rollout_last_entry_ts + activity_window,
                hooks=hooks,
            )
        else:
            retry_after_seconds = hooks.default_session_output_busy_retry_seconds
    elif recent_output_busy:
        detail = "recent_session_output"
        retry_after_seconds = retry_after_seconds_from_target(latest_activity + activity_window, hooks=hooks)
    return {
        "busy": bool(detail),
        "detail": detail,
        "retry_after_seconds": max(1, int(retry_after_seconds or hooks.default_session_output_busy_retry_seconds)) if detail else 0,
        "latest_activity_ts": latest_activity,
        "activity_age_seconds": activity_age_seconds,
        "rollout_snapshot": rollout_snapshot,
        "rollout_activity_age_seconds": rollout_activity_age_seconds,
        "active_feedback_entries": active_feedback_entries,
        "active_resume_pids": active_resume_pids,
    }


def extract_codex_session_id(text: str) -> str:
    match = re.search(r"session id:\s*([A-Za-z0-9-]{20,})", text)
    if match:
        return match.group(1)
    return ""


def extract_taskboard_signal(text: str, *, hooks: SessionRuntimeHooks) -> str:
    standalone_matches = re.findall(r"(?mi)^[ \t>*`-]*TASKBOARD_SIGNAL\s*=\s*([A-Z0-9_]+)\s*[` ]*$", text or "")
    if standalone_matches:
        return hooks.canonicalize_taskboard_signal(standalone_matches[-1].strip())
    inline_matches = re.findall(r"TASKBOARD_SIGNAL\s*=\s*([A-Z0-9_]+)", text or "")
    if inline_matches:
        return hooks.canonicalize_taskboard_signal(inline_matches[-1].strip())
    footer_matches = re.findall(r"(?mi)^[ \t>*`-]*FINAL_SIGNAL\s*=\s*([A-Z0-9_]+|none)\s*[` ]*$", text or "")
    if footer_matches:
        final_signal = footer_matches[-1].strip()
        normalized_final_signal = hooks.canonicalize_taskboard_signal(final_signal)
        if final_signal in hooks.taskboard_final_signal_values and final_signal != "none":
            return normalized_final_signal
    return ""


def extract_text_from_message_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    fragments: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "")).strip()
        if item_type not in {"output_text", "input_text"}:
            continue
        text = str(item.get("text", "")).strip()
        if text:
            fragments.append(text)
    return "\n".join(fragments).strip()


def extract_last_assistant_message_from_rollout(
    path: Path,
    *,
    hooks: SessionRuntimeHooks,
    min_entry_ts: float = 0.0,
) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    last_message = ""
    for raw_line in lines:
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except Exception:
            continue
        if str(entry.get("type", "")) != "response_item":
            continue
        if min_entry_ts > 0:
            entry_ts = hooks.parse_timestamp_to_unix(entry.get("timestamp"))
            if entry_ts is None or entry_ts + hooks.rollout_fallback_entry_grace_seconds < min_entry_ts:
                continue
        message = entry.get("payload", {})
        if not isinstance(message, dict):
            continue
        if str(message.get("type", "")) != "message" or str(message.get("role", "")) != "assistant":
            continue
        content_text = extract_text_from_message_content(message.get("content", []))
        if content_text:
            last_message = content_text
    return last_message.strip()


def latest_local_assistant_message_for_session(
    config: Any,
    session_id: str,
    *,
    hooks: SessionRuntimeHooks,
    min_mtime: float = 0.0,
    min_entry_ts: float | None = None,
) -> str:
    if not session_id:
        return ""
    if min_entry_ts is None:
        min_entry_ts = min_mtime
    candidates: list[tuple[float, Path]] = []
    for path in rollout_candidates_for_session(config, session_id, hooks=hooks):
        try:
            candidates.append((float(path.stat().st_mtime), path))
        except FileNotFoundError:
            continue
    for mtime, path in sorted(candidates, key=lambda item: (item[0], str(item[1])), reverse=True):
        if min_mtime > 0 and mtime + hooks.rollout_fallback_mtime_grace_seconds < min_mtime:
            continue
        message = extract_last_assistant_message_from_rollout(path, hooks=hooks, min_entry_ts=min_entry_ts)
        if message:
            return message
    return ""


def allow_local_rollout_fallback(
    config: Any,
    *,
    mode: str,
    session_id: str,
    hooks: SessionRuntimeHooks,
) -> bool:
    if mode != "resume":
        return True
    thread = hooks.find_thread_info(config, session_id) or {}
    source = str(thread.get("source", "")).strip().lower()
    if source == "vscode":
        return False
    return True


def is_rate_limit_retry_error(*texts: str, hooks: SessionRuntimeHooks) -> bool:
    combined = "\n".join(texts).lower()
    return all(pattern in combined for pattern in hooks.rate_limit_patterns)


def is_session_busy_error(*texts: str, hooks: SessionRuntimeHooks) -> bool:
    combined = "\n".join(texts).lower()
    if any(pattern in combined for pattern in hooks.session_busy_patterns):
        return True
    return "busy" in combined and any(token in combined for token in ("session", "thread", "conversation", "request", "response", "run"))


def platform_error_spec_for_kind(kind: str, *, hooks: SessionRuntimeHooks) -> dict[str, Any]:
    normalized_kind = str(kind or "").strip()
    if not normalized_kind:
        return {}
    if normalized_kind == "rate_limited":
        return {
            "kind": "rate_limited",
            "retryable": True,
            "needs_human_attention": False,
            "summary": "上游平台触发了 429 / retry limit，taskboard 将延迟重试。",
            "matched_pattern": "429 too many requests",
        }
    for signature in hooks.platform_error_signatures:
        if str(signature.get("kind", "")).strip() == normalized_kind:
            return {
                "kind": normalized_kind,
                "retryable": bool(signature.get("retryable", False)),
                "needs_human_attention": not bool(signature.get("retryable", False)),
                "summary": str(signature.get("summary", "")).strip(),
                "matched_pattern": "",
            }
    return {}


def classify_platform_error(*texts: str, hooks: SessionRuntimeHooks) -> dict[str, Any]:
    combined = "\n".join(str(text or "") for text in texts if str(text or "").strip())
    lowered = combined.lower()
    if not lowered:
        return empty_platform_error_details()
    if is_rate_limit_retry_error(combined, hooks=hooks):
        return platform_error_spec_for_kind("rate_limited", hooks=hooks)
    for signature in hooks.platform_error_signatures:
        for pattern in signature.get("patterns", ()):
            if str(pattern).lower() in lowered:
                details = platform_error_spec_for_kind(str(signature.get("kind", "")).strip(), hooks=hooks)
                details["matched_pattern"] = str(pattern)
                return details
    return empty_platform_error_details()


def continue_retry_error_kind(*texts: str, hooks: SessionRuntimeHooks) -> str:
    if is_rate_limit_retry_error(*texts, hooks=hooks):
        return "rate_limited"
    details = classify_platform_error(*texts, hooks=hooks)
    if bool(details.get("retryable", False)):
        return str(details.get("kind", "")).strip() or "retryable_platform_error"
    return ""


def platform_error_from_reason(reason: str, *, hooks: SessionRuntimeHooks) -> dict[str, Any]:
    normalized_reason = str(reason or "").strip().lower()
    if not normalized_reason:
        return empty_platform_error_details()
    if normalized_reason == "rate_limited":
        return platform_error_spec_for_kind("rate_limited", hooks=hooks)
    if normalized_reason.startswith("platform_error:"):
        return platform_error_spec_for_kind(normalized_reason.split(":", 1)[1], hooks=hooks)
    return empty_platform_error_details()


def platform_error_deferred_reason(kind: str) -> str:
    normalized_kind = str(kind or "").strip()
    if normalized_kind == "rate_limited":
        return "rate_limited"
    return f"platform_error:{normalized_kind}" if normalized_kind else ""


def platform_error_retry_after_seconds(
    *,
    retryable: bool,
    min_idle_seconds: int,
    hooks: SessionRuntimeHooks,
) -> int:
    del min_idle_seconds
    base_delay = hooks.default_session_output_busy_retry_seconds
    if retryable:
        return base_delay
    return max(base_delay, hooks.default_platform_error_human_retry_seconds)


def session_busy_retry_after_seconds(*, hooks: SessionRuntimeHooks) -> int:
    return hooks.default_session_output_busy_retry_seconds


def platform_error_result_fields(details: dict[str, Any], *, source: str) -> dict[str, Any]:
    kind = str(details.get("kind", "")).strip()
    if not kind:
        return {}
    return {
        "platform_error_kind": kind,
        "platform_error_summary": str(details.get("summary", "")).strip(),
        "platform_error_retryable": bool(details.get("retryable", False)),
        "platform_error_needs_human_attention": bool(details.get("needs_human_attention", False)),
        "platform_error_source": str(source or "").strip(),
    }


def retry_after_seconds_from_target(target_ts: float, *, hooks: SessionRuntimeHooks) -> int:
    return max(1, int(math.ceil(max(0.0, float(target_ts) - hooks.now_ts()))))


def default_retry_delay_seconds(min_idle_seconds: int = 0, *, hooks: SessionRuntimeHooks) -> int:
    return max(hooks.default_resume_retry_seconds, int(min_idle_seconds or 0), 1)


def build_deferred_resume_result(
    *,
    original_session_id: str,
    resumed_session_id: str,
    codex_exec_mode: str,
    prompt_chars: int,
    deferred_reason: str,
    retry_after_seconds: int,
    attempted: bool,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    return {
        "attempted": attempted,
        "ok": False,
        "deferred": True,
        "deferred_reason": deferred_reason,
        "retry_after_seconds": max(1, int(retry_after_seconds or 1)),
        "original_session_id": original_session_id,
        "resumed_session_id": resumed_session_id,
        "used_fallback_clone": False,
        "codex_exec_mode": codex_exec_mode,
        "prompt_chars": prompt_chars,
        "started_at": started_at,
        "finished_at": finished_at,
        "message_written": False,
        "last_message_text": "",
        "continue_attempts": 0,
        "recovered_with_continue": False,
        "taskboard_signal": "",
    }


def command_runtime_result_fields(
    completed: Any,
    exec_result: dict[str, Any],
    *,
    last_message_text: str,
    hooks: SessionRuntimeHooks,
) -> dict[str, Any]:
    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "")
    signal_source = command_signal_source_text(last_message_text, stdout, stderr)
    return {
        "first_returncode": getattr(completed, "returncode", None),
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "message_written": bool(exec_result.get("message_written", False)),
        "last_message_text": last_message_text,
        "taskboard_signal": extract_taskboard_signal(signal_source, hooks=hooks),
        "taskboard_protocol": hooks.extract_taskboard_protocol_footer(signal_source),
        "continue_attempts": int(exec_result.get("continue_attempts", 0) or 0),
        "recovered_with_continue": bool(exec_result.get("recovered_with_continue", False)),
    }
