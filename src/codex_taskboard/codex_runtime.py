from __future__ import annotations

import fcntl
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class CodexRuntimeHooks:
    should_use_executor_codex: Callable[[dict[str, Any] | None], bool]
    build_remote_codex_command: Callable[..., list[str]]
    build_codex_resume_command: Callable[..., list[str]]
    build_codex_exec_command: Callable[..., list[str]]
    run_local_interactive_codex: Callable[..., dict[str, Any]]
    run_tracked_feedback_subprocess: Callable[..., subprocess.CompletedProcess[str]]
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]]
    extract_remote_last_message: Callable[[str], tuple[str, str]]
    ensure_dir: Callable[[Path], None]
    allow_local_rollout_fallback: Callable[..., bool]
    latest_local_assistant_message_for_session: Callable[..., str]
    extract_codex_session_id: Callable[[str], str]
    continue_retry_error_kind: Callable[..., str]
    append_log: Callable[[Path, str], None]
    sleep: Callable[[float], None]
    now_ts: Callable[[], float]
    build_resume_prompt: Callable[..., str]
    continuous_research_mode_enabled: Callable[..., bool]
    task_last_message_path: Callable[[Any, str], Path]
    subagent_last_message_path: Callable[[Any, str], Path]
    task_runner_log_path: Callable[[Any, str], Path]
    session_migration_entry: Callable[[Any, str], dict[str, Any]]
    session_redirect_target: Callable[..., str]
    session_lock_path: Callable[[Any, str], Path]
    human_guidance_mode_active: Callable[..., bool]
    human_guidance_retry_after_seconds: Callable[..., int]
    default_retry_delay_seconds: Callable[[int], int]
    retry_after_seconds_from_target: Callable[[float], int]
    build_deferred_resume_result: Callable[..., dict[str, Any]]
    session_output_busy_snapshot: Callable[..., dict[str, Any]]
    latest_session_activity_ts: Callable[..., float]
    run_codex_prompt_with_continue_recovery: Callable[..., dict[str, Any]]
    resume_codex_session_with_prompt: Callable[..., dict[str, Any]]
    command_runtime_result_fields: Callable[[Any, dict[str, Any], str], dict[str, Any]]
    classify_platform_error: Callable[..., dict[str, Any]]
    platform_error_result_fields: Callable[[dict[str, Any], str], dict[str, Any]]
    is_rate_limit_retry_error: Callable[..., bool]
    is_session_busy_error: Callable[..., bool]
    session_busy_retry_after_seconds: Callable[[], int]
    platform_error_retry_after_seconds: Callable[..., int]
    platform_error_deferred_reason: Callable[[str], str]
    sync_thread_for_fallback: Callable[..., tuple[bool, str, str]]
    task_root: Callable[[Any, str], Path]
    extract_taskboard_signal: Callable[[str], str]
    extract_text_tail_signal_source: Callable[[str, str, str], str]
    extract_codex_session_id_from_completed: Callable[[subprocess.CompletedProcess[str]], str]
    utc_now: Callable[[], str]
    default_session_migration_interrupt_grace_seconds: int
    default_session_output_busy_retry_seconds: int


def run_codex_prompt_with_continue_recovery(
    config: Any,
    *,
    mode: str,
    prompt: str,
    output_last_message_path: str,
    codex_exec_mode: str,
    workdir: str,
    timeout_seconds: int,
    log_path: Path,
    hooks: CodexRuntimeHooks,
    model: str = "",
    session_id: str = "",
    max_continue_attempts: int = 3,
    spec: dict[str, Any] | None = None,
    feedback_source_kind: str = "",
    feedback_source_key: str = "",
    feedback_task_id: str = "",
    feedback_task_ids: list[str] | None = None,
    feedback_followup_key: str = "",
    requested_session_id: str = "",
    track_resume_feedback: bool = False,
) -> dict[str, Any]:
    current_mode = mode
    current_prompt = prompt
    current_session_id = session_id
    normalized_requested_session_id = str(requested_session_id or session_id or "").strip()
    normalized_feedback_source_kind = str(feedback_source_kind or "").strip()
    continue_attempts = 0
    while True:
        message_path = Path(output_last_message_path)
        if message_path.exists():
            message_path.unlink()
        command_started_at = hooks.now_ts()
        use_remote_codex = hooks.should_use_executor_codex(spec)
        if use_remote_codex:
            command = hooks.build_remote_codex_command(
                spec or {},
                mode=current_mode,
                session_id=current_session_id,
                prompt=current_prompt,
                codex_exec_mode=codex_exec_mode,
                workdir=workdir,
                model=model,
            )
        elif current_mode == "resume":
            if not current_session_id:
                raise ValueError("Missing session id for resume mode.")
            command = hooks.build_codex_resume_command(
                config,
                session_id=current_session_id,
                prompt=current_prompt,
                output_last_message_path=output_last_message_path,
                codex_exec_mode=codex_exec_mode,
                workdir=workdir,
            )
        else:
            command = hooks.build_codex_exec_command(
                config,
                prompt=current_prompt,
                output_last_message_path=output_last_message_path,
                codex_exec_mode=codex_exec_mode,
                workdir=workdir,
                model=model,
            )

        tracking_source_kind = normalized_feedback_source_kind or (
            "resume" if current_mode == "resume" and current_session_id else ""
        )
        should_track_resume_feedback = bool(track_resume_feedback and current_mode == "resume" and current_session_id)
        message_written = False
        last_message_text = ""
        parsed_session_id = ""
        if not use_remote_codex:
            interactive_result = hooks.run_local_interactive_codex(
                config,
                command=command,
                mode=current_mode,
                workdir=workdir,
                prompt=current_prompt,
                session_id=current_session_id,
                output_last_message_path=output_last_message_path,
                timeout_seconds=timeout_seconds,
                log_path=log_path,
                requested_session_id=normalized_requested_session_id or current_session_id,
                feedback_source_kind=tracking_source_kind if should_track_resume_feedback else "",
                feedback_source_key=str(feedback_source_key or current_session_id).strip() if should_track_resume_feedback else "",
                feedback_task_id=feedback_task_id if should_track_resume_feedback else "",
                feedback_task_ids=feedback_task_ids if should_track_resume_feedback else None,
                feedback_followup_key=feedback_followup_key if should_track_resume_feedback else "",
                command_started_at=command_started_at,
            )
            completed = interactive_result["completed"]
            combined_stdout = str(completed.stdout)
            parsed_session_id = str(interactive_result.get("session_id", "") or "")
            if parsed_session_id and not current_session_id:
                current_session_id = parsed_session_id
            message_written = bool(interactive_result.get("message_written", False))
            last_message_text = str(interactive_result.get("last_message_text", "") or "")
        elif tracking_source_kind and should_track_resume_feedback:
            completed = hooks.run_tracked_feedback_subprocess(
                config,
                command,
                cwd=workdir,
                timeout=timeout_seconds,
                session_id=current_session_id,
                requested_session_id=normalized_requested_session_id or current_session_id,
                source_kind=tracking_source_kind,
                source_key=str(feedback_source_key or current_session_id).strip() or current_session_id,
                task_id=feedback_task_id,
                task_ids=feedback_task_ids,
                followup_key=feedback_followup_key,
            )
            combined_stdout = str(completed.stdout)
        else:
            completed = hooks.run_subprocess(command, cwd=workdir, timeout=timeout_seconds)
            combined_stdout = str(completed.stdout)
        if use_remote_codex:
            remote_message, combined_stdout = hooks.extract_remote_last_message(combined_stdout)
            if remote_message:
                hooks.ensure_dir(message_path.parent)
                message_path.write_text(remote_message, encoding="utf-8")
        combined = f"{combined_stdout}\n{completed.stderr}"
        if use_remote_codex:
            parsed_session_id = hooks.extract_codex_session_id(combined)
            if parsed_session_id and not current_session_id:
                current_session_id = parsed_session_id
            message_written = message_path.exists() and message_path.stat().st_size > 0
            last_message_text = ""
            if message_written:
                last_message_text = message_path.read_text(encoding="utf-8", errors="ignore")
        else:
            if not parsed_session_id:
                parsed_session_id = hooks.extract_codex_session_id(combined)
                if parsed_session_id and not current_session_id:
                    current_session_id = parsed_session_id
            if last_message_text and not message_written:
                hooks.ensure_dir(message_path.parent)
                message_path.write_text(last_message_text, encoding="utf-8")
                message_written = True
            elif message_path.exists() and message_path.stat().st_size > 0:
                message_written = True
                last_message_text = message_path.read_text(encoding="utf-8", errors="ignore")
        if not last_message_text and not use_remote_codex and hooks.allow_local_rollout_fallback(
            config,
            mode=current_mode,
            session_id=current_session_id or parsed_session_id,
        ):
            fallback_session_id = current_session_id or parsed_session_id
            last_message_text = hooks.latest_local_assistant_message_for_session(
                config,
                fallback_session_id,
                min_mtime=command_started_at,
                min_entry_ts=command_started_at,
            )
            if last_message_text:
                hooks.ensure_dir(message_path.parent)
                message_path.write_text(last_message_text, encoding="utf-8")
                message_written = True
        hooks.append_log(
            log_path,
            f"codex_prompt mode={current_mode} returncode={completed.returncode} session_id={current_session_id or parsed_session_id} continue_attempts={continue_attempts}",
        )
        if completed.returncode == 0 or message_written:
            return {
                "completed": completed,
                "session_id": current_session_id,
                "message_written": message_written,
                "last_message_text": last_message_text,
                "continue_attempts": continue_attempts,
                "recovered_with_continue": continue_attempts > 0,
            }

        continue_error_kind = hooks.continue_retry_error_kind(completed.stdout, completed.stderr)
        if continue_attempts >= max_continue_attempts or not current_session_id or not continue_error_kind:
            return {
                "completed": completed,
                "session_id": current_session_id,
                "message_written": message_written,
                "last_message_text": last_message_text,
                "continue_attempts": continue_attempts,
                "recovered_with_continue": False,
            }

        continue_attempts += 1
        hooks.append_log(
            log_path,
            f"continue_retry_detected kind={continue_error_kind} session_id={current_session_id} continue_attempt={continue_attempts}",
        )
        current_mode = "resume"
        current_prompt = "continue"
        hooks.sleep(min(5 * continue_attempts, 15))


def resume_codex_session(
    config: Any,
    spec: dict[str, Any],
    event: dict[str, Any],
    *,
    hooks: CodexRuntimeHooks,
    min_idle_seconds: int,
) -> dict[str, Any]:
    prompt = hooks.build_resume_prompt(
        spec,
        event,
        continuous_research_enabled=hooks.continuous_research_mode_enabled(
            config,
            codex_session_id=str(spec.get("codex_session_id", "")).strip(),
        ),
    )
    output_path = hooks.task_last_message_path(config, spec["task_id"])
    task_id = str(spec.get("task_id", "")).strip()
    return hooks.resume_codex_session_with_prompt(
        config,
        spec,
        prompt,
        output_last_message_path=str(output_path),
        log_path=hooks.task_runner_log_path(config, spec["task_id"]),
        min_idle_seconds=min_idle_seconds,
        feedback_source_kind="task_feedback",
        feedback_source_key=task_id,
        feedback_task_id=task_id,
        feedback_task_ids=[task_id],
    )


def resume_codex_session_with_prompt(
    config: Any,
    spec: dict[str, Any],
    prompt: str,
    *,
    output_last_message_path: str,
    log_path: Path,
    hooks: CodexRuntimeHooks,
    min_idle_seconds: int,
    feedback_source_kind: str = "",
    feedback_source_key: str = "",
    feedback_task_id: str = "",
    feedback_task_ids: list[str] | None = None,
    feedback_followup_key: str = "",
) -> dict[str, Any]:
    original_session_id = str(spec.get("codex_session_id", "")).strip()
    active_migration_entry = hooks.session_migration_entry(config, original_session_id) if original_session_id else {}
    if original_session_id and str(active_migration_entry.get("state", "")).strip() == "migrating":
        redirected_session_id = str(active_migration_entry.get("to_session_id", "")).strip() or original_session_id
        retry_after = hooks.default_session_migration_interrupt_grace_seconds
        hooks.append_log(
            log_path,
            f"resume_deferred reason=session_migration_in_progress session_id={original_session_id} redirected_session_id={redirected_session_id} retry_after_seconds={retry_after}",
        )
        return hooks.build_deferred_resume_result(
            original_session_id=original_session_id,
            resumed_session_id=redirected_session_id,
            codex_exec_mode=spec["codex_exec_mode"],
            prompt_chars=len(prompt),
            deferred_reason="session_migration_in_progress",
            retry_after_seconds=retry_after,
            attempted=False,
            started_at=hooks.utc_now(),
            finished_at=hooks.utc_now(),
        )
    resumed_session_id = (
        hooks.session_redirect_target(config, original_session_id, include_migrating=False) if original_session_id else ""
    ) or original_session_id
    output_path = Path(output_last_message_path)
    hooks.ensure_dir(output_path.parent)
    lock_path = hooks.session_lock_path(config, resumed_session_id)
    hooks.ensure_dir(lock_path.parent)
    started_at = hooks.utc_now()
    result: dict[str, Any] = {
        "attempted": False,
        "ok": False,
        "original_session_id": original_session_id,
        "resumed_session_id": resumed_session_id,
        "used_fallback_clone": False,
        "codex_exec_mode": spec["codex_exec_mode"],
        "prompt_chars": len(prompt),
        "started_at": started_at,
    }
    if resumed_session_id and hooks.human_guidance_mode_active(config, codex_session_id=resumed_session_id):
        retry_after = hooks.human_guidance_retry_after_seconds(config, codex_session_id=resumed_session_id)
        hooks.append_log(
            log_path,
            f"resume_deferred reason=managed_mode_pause session_id={resumed_session_id} retry_after_seconds={retry_after}",
        )
        result.update(
            hooks.build_deferred_resume_result(
                original_session_id=original_session_id,
                resumed_session_id=resumed_session_id,
                codex_exec_mode=spec["codex_exec_mode"],
                prompt_chars=len(prompt),
                deferred_reason="managed_mode_pause",
                retry_after_seconds=retry_after,
                attempted=False,
                started_at=started_at,
                finished_at=hooks.utc_now(),
            )
        )
        return result
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            retry_after = hooks.default_retry_delay_seconds(min_idle_seconds)
            hooks.append_log(
                log_path,
                f"resume_deferred reason=session_locked session_id={resumed_session_id} retry_after_seconds={retry_after}",
            )
            result.update(
                hooks.build_deferred_resume_result(
                    original_session_id=original_session_id,
                    resumed_session_id=resumed_session_id,
                    codex_exec_mode=spec["codex_exec_mode"],
                    prompt_chars=len(prompt),
                    deferred_reason="session_locked",
                    retry_after_seconds=retry_after,
                    attempted=False,
                    started_at=started_at,
                    finished_at=hooks.utc_now(),
                )
            )
            return result
        if resumed_session_id:
            output_busy = hooks.session_output_busy_snapshot(config, resumed_session_id, spec=spec)
            result["latest_activity_ts"] = float(output_busy.get("latest_activity_ts", 0.0) or 0.0)
            if output_busy.get("busy", False):
                retry_after = int(
                    output_busy.get(
                        "retry_after_seconds",
                        hooks.default_session_output_busy_retry_seconds,
                    )
                    or hooks.default_session_output_busy_retry_seconds
                )
                hooks.append_log(
                    log_path,
                    f"resume_deferred reason=session_output_busy detail={output_busy.get('detail', '')} session_id={resumed_session_id} retry_after_seconds={retry_after}",
                )
                result.update(
                    hooks.build_deferred_resume_result(
                        original_session_id=original_session_id,
                        resumed_session_id=resumed_session_id,
                        codex_exec_mode=spec["codex_exec_mode"],
                        prompt_chars=len(prompt),
                        deferred_reason="session_output_busy",
                        retry_after_seconds=retry_after,
                        attempted=False,
                        started_at=started_at,
                        finished_at=hooks.utc_now(),
                    )
                )
                return result
        if resumed_session_id and int(min_idle_seconds or 0) > 0:
            last_activity_ts = float(
                result.get("latest_activity_ts", 0.0)
                or hooks.latest_session_activity_ts(config, resumed_session_id, spec)
                or 0.0
            )
            result["latest_activity_ts"] = last_activity_ts
            if last_activity_ts and hooks.now_ts() - last_activity_ts < int(min_idle_seconds):
                retry_after = hooks.retry_after_seconds_from_target(last_activity_ts + int(min_idle_seconds))
                hooks.append_log(
                    log_path,
                    f"resume_deferred reason=recent_activity session_id={resumed_session_id} retry_after_seconds={retry_after}",
                )
                result.update(
                    hooks.build_deferred_resume_result(
                        original_session_id=original_session_id,
                        resumed_session_id=resumed_session_id,
                        codex_exec_mode=spec["codex_exec_mode"],
                        prompt_chars=len(prompt),
                        deferred_reason="recent_activity",
                        retry_after_seconds=retry_after,
                        attempted=False,
                        started_at=started_at,
                        finished_at=hooks.utc_now(),
                    )
                )
                return result
        result["attempted"] = True
        exec_result = hooks.run_codex_prompt_with_continue_recovery(
            config,
            mode="resume",
            session_id=resumed_session_id,
            prompt=prompt,
            output_last_message_path=str(output_path),
            codex_exec_mode=spec["codex_exec_mode"],
            workdir=spec["workdir"],
            timeout_seconds=int(spec["resume_timeout_seconds"]),
            log_path=log_path,
            spec=spec,
            feedback_source_kind=feedback_source_kind,
            feedback_source_key=feedback_source_key,
            feedback_task_id=feedback_task_id,
            feedback_task_ids=feedback_task_ids,
            feedback_followup_key=feedback_followup_key,
            requested_session_id=original_session_id or resumed_session_id,
            track_resume_feedback=True,
        )
        completed = exec_result["completed"]
        message_written = bool(exec_result["message_written"])
        last_message_text = str(exec_result.get("last_message_text", "") or "")
        runtime_result_fields = hooks.command_runtime_result_fields(completed, exec_result, last_message_text)
        result.update({"completed": completed, **runtime_result_fields})
        hooks.append_log(
            log_path,
            f"resume_primary returncode={completed.returncode} stdout_tail={completed.stdout[-1000:]} stderr_tail={completed.stderr[-1000:]}",
        )
        primary_platform_error = hooks.classify_platform_error(completed.stdout, completed.stderr)
        if primary_platform_error.get("kind"):
            result.update(hooks.platform_error_result_fields(primary_platform_error, source="primary"))

        def defer_after_attempt(
            *,
            deferred_reason: str,
            retry_after: int,
            target_session_id: str,
            log_message: str,
            platform_error: dict[str, Any] | None = None,
            platform_error_source: str = "primary",
        ) -> dict[str, Any]:
            hooks.append_log(log_path, log_message)
            result.update(
                hooks.build_deferred_resume_result(
                    original_session_id=original_session_id,
                    resumed_session_id=target_session_id,
                    codex_exec_mode=spec["codex_exec_mode"],
                    prompt_chars=len(prompt),
                    deferred_reason=deferred_reason,
                    retry_after_seconds=retry_after,
                    attempted=True,
                    started_at=started_at,
                    finished_at=hooks.utc_now(),
                )
            )
            result.update(runtime_result_fields)
            if platform_error and platform_error.get("kind"):
                result.update(hooks.platform_error_result_fields(platform_error, source=platform_error_source))
                result["needs_human_attention"] = bool(platform_error.get("needs_human_attention", False))
            return result

        if completed.returncode == 0 or message_written:
            result["ok"] = True
            result["finished_at"] = hooks.utc_now()
            return result
        redirect_target_after_attempt = (
            hooks.session_redirect_target(config, original_session_id, include_migrating=True)
            if original_session_id
            else ""
        )
        redirect_entry_after_attempt = hooks.session_migration_entry(config, original_session_id) if original_session_id else {}
        redirect_state_after_attempt = str(redirect_entry_after_attempt.get("state", "")).strip()
        if (
            original_session_id
            and resumed_session_id == original_session_id
            and redirect_target_after_attempt
            and redirect_target_after_attempt != original_session_id
            and not message_written
        ):
            deferred_reason = "session_migration_cutover"
            retry_after = hooks.default_session_migration_interrupt_grace_seconds
            if redirect_state_after_attempt == "migrating":
                deferred_reason = "session_migration_in_progress"
            result.update(
                defer_after_attempt(
                    deferred_reason=deferred_reason,
                    retry_after=retry_after,
                    target_session_id=redirect_target_after_attempt,
                    log_message=(
                        f"resume_deferred reason={deferred_reason} session_id={original_session_id} "
                        f"redirected_session_id={redirect_target_after_attempt} retry_after_seconds={retry_after}"
                    ),
                )
            )
            return result
        if hooks.is_rate_limit_retry_error(completed.stdout, completed.stderr):
            retry_after = hooks.default_session_output_busy_retry_seconds
            result.update(
                defer_after_attempt(
                    deferred_reason="rate_limited",
                    retry_after=retry_after,
                    target_session_id=resumed_session_id,
                    log_message=(
                        f"resume_deferred reason=rate_limited session_id={resumed_session_id} "
                        f"retry_after_seconds={retry_after}"
                    ),
                )
            )
            return result
        if hooks.is_session_busy_error(completed.stdout, completed.stderr):
            retry_after = hooks.session_busy_retry_after_seconds()
            result.update(
                defer_after_attempt(
                    deferred_reason="session_busy",
                    retry_after=retry_after,
                    target_session_id=resumed_session_id,
                    log_message=(
                        f"resume_deferred reason=session_busy session_id={resumed_session_id} "
                        f"retry_after_seconds={retry_after}"
                    ),
                )
            )
            return result
        fallback_provider = str(spec.get("fallback_provider") or "").strip()
        if primary_platform_error.get("kind") and not fallback_provider:
            retry_after = hooks.platform_error_retry_after_seconds(
                retryable=bool(primary_platform_error.get("retryable", False)),
                min_idle_seconds=min_idle_seconds,
            )
            deferred_reason = hooks.platform_error_deferred_reason(str(primary_platform_error.get("kind", "")).strip())
            result.update(
                defer_after_attempt(
                    deferred_reason=deferred_reason,
                    retry_after=retry_after,
                    target_session_id=resumed_session_id,
                    log_message=(
                        f"resume_deferred reason={deferred_reason} session_id={resumed_session_id} "
                        f"retry_after_seconds={retry_after}"
                    ),
                    platform_error=primary_platform_error,
                )
            )
            return result
        if not fallback_provider:
            result["finished_at"] = hooks.utc_now()
            return result
        ok, cloned_session_id, error = hooks.sync_thread_for_fallback(
            config,
            original_session_id=original_session_id,
            fallback_provider=fallback_provider,
            workdir=spec["workdir"],
            task_id=spec["task_id"],
        )
        if not ok:
            result["fallback_error"] = error
            result["finished_at"] = hooks.utc_now()
            return result
        fallback_output_path = hooks.task_root(config, spec["task_id"]) / f"codex-last-message-{fallback_provider}.txt"
        fallback_command = hooks.build_codex_resume_command(
            config,
            session_id=cloned_session_id,
            prompt=prompt,
            output_last_message_path=str(fallback_output_path),
            codex_exec_mode=spec["codex_exec_mode"],
            workdir=spec["workdir"],
        )
        fallback_exec_result = hooks.run_local_interactive_codex(
            config,
            command=fallback_command,
            mode="resume",
            workdir=spec["workdir"],
            prompt=prompt,
            session_id=cloned_session_id,
            output_last_message_path=str(fallback_output_path),
            timeout_seconds=int(spec["resume_timeout_seconds"]),
            log_path=hooks.task_runner_log_path(config, spec["task_id"]),
            requested_session_id=cloned_session_id,
        )
        fallback_completed = fallback_exec_result["completed"]
        fallback_message_text = ""
        if fallback_output_path.exists():
            fallback_message_text = fallback_output_path.read_text(encoding="utf-8", errors="ignore")
        if not fallback_message_text:
            fallback_message_text = str(fallback_exec_result.get("last_message_text", "") or "")
        fallback_signal_source = hooks.extract_text_tail_signal_source(
            (fallback_message_text or str(result.get("last_message_text", ""))).strip(),
            str(fallback_completed.stdout or ""),
            str(fallback_completed.stderr or ""),
        )
        result.update(
            {
                "completed": fallback_completed,
                "used_fallback_clone": True,
                "resumed_session_id": cloned_session_id,
                "fallback_provider": fallback_provider,
                "fallback_returncode": fallback_completed.returncode,
                "fallback_stdout_tail": str(fallback_completed.stdout or "")[-4000:],
                "fallback_stderr_tail": str(fallback_completed.stderr or "")[-4000:],
                "last_message_text": fallback_message_text or result.get("last_message_text", ""),
                "taskboard_signal": hooks.extract_taskboard_signal(fallback_signal_source),
            }
        )
        hooks.append_log(
            hooks.task_runner_log_path(config, spec["task_id"]),
            f"resume_fallback returncode={fallback_completed.returncode} resumed_session_id={cloned_session_id} stdout_tail={str(fallback_completed.stdout or '')[-1000:]} stderr_tail={str(fallback_completed.stderr or '')[-1000:]}",
        )
        result["ok"] = fallback_completed.returncode == 0
        fallback_platform_error = hooks.classify_platform_error(fallback_completed.stdout, fallback_completed.stderr)
        if fallback_platform_error.get("kind"):
            result.update(hooks.platform_error_result_fields(fallback_platform_error, source="fallback"))
        if not result["ok"] and fallback_platform_error.get("kind"):
            retry_after = hooks.platform_error_retry_after_seconds(
                retryable=bool(fallback_platform_error.get("retryable", False)),
                min_idle_seconds=min_idle_seconds,
            )
            deferred_reason = hooks.platform_error_deferred_reason(str(fallback_platform_error.get("kind", "")).strip())
            result.update(
                defer_after_attempt(
                    deferred_reason=deferred_reason,
                    retry_after=retry_after,
                    target_session_id=cloned_session_id,
                    log_message=(
                        f"resume_deferred reason={deferred_reason} session_id={cloned_session_id} "
                        f"retry_after_seconds={retry_after}"
                    ),
                    platform_error=fallback_platform_error,
                    platform_error_source="fallback",
                )
            )
            result["used_fallback_clone"] = True
            result["fallback_provider"] = fallback_provider
            return result
        result["finished_at"] = hooks.utc_now()
        return result


def run_codex_subagent(
    config: Any,
    spec: dict[str, Any],
    *,
    hooks: CodexRuntimeHooks,
) -> dict[str, Any]:
    task_id = str(spec["task_id"])
    output_path = hooks.subagent_last_message_path(config, task_id)
    hooks.ensure_dir(output_path.parent)
    prompt = str(spec.get("subagent_prompt", "")).strip()
    if not prompt:
        raise ValueError("Missing subagent_prompt for codex_subagent task.")
    if "TASKBOARD_SIGNAL" not in prompt:
        prompt = (
            prompt
            + "\n\nAt the end of your final answer, include exactly one standalone line in this format:\n"
            + "TASKBOARD_SIGNAL=TASK_DONE\n"
            + "If the task is blocked and the parent agent should not continue automatically, use one of:\n"
            + "TASKBOARD_SIGNAL=NEEDS_REVIEW\n"
            + "TASKBOARD_SIGNAL=RETRY_SAME_TASK\n"
            + "TASKBOARD_SIGNAL=START_NEXT_TASK\n"
        )
    model = str(spec.get("subagent_model", "gpt-5.4")).strip() or "gpt-5.4"
    exec_mode = str(spec.get("subagent_exec_mode", "dangerous")).strip() or "dangerous"
    timeout_seconds = int(spec.get("subagent_timeout_seconds", 7200))
    result = hooks.run_codex_prompt_with_continue_recovery(
        config,
        mode="exec",
        prompt=prompt,
        output_last_message_path=str(output_path),
        codex_exec_mode=exec_mode,
        workdir=spec["workdir"],
        model=model,
        timeout_seconds=timeout_seconds,
        log_path=hooks.task_runner_log_path(config, task_id),
        max_continue_attempts=int(spec.get("subagent_continue_attempts", 3)),
    )
    completed = result["completed"]
    message_written = bool(result["message_written"])
    status = "completed" if completed.returncode == 0 or message_written else "failed"
    last_message = ""
    if output_path.exists():
        last_message = output_path.read_text(encoding="utf-8", errors="ignore")
    taskboard_signal = hooks.extract_taskboard_signal(
        hooks.extract_text_tail_signal_source(last_message, str(completed.stdout or ""), str(completed.stderr or ""))
    )
    return {
        "status": status,
        "returncode": completed.returncode,
        "stdout_tail": str(completed.stdout or "")[-4000:],
        "stderr_tail": str(completed.stderr or "")[-4000:],
        "subagent_session_id": result.get("session_id", "") or hooks.extract_codex_session_id_from_completed(completed),
        "subagent_message_written": message_written,
        "subagent_last_message": last_message,
        "subagent_last_message_excerpt": last_message[:4000],
        "subagent_model": model,
        "continue_attempts": int(result["continue_attempts"]),
        "recovered_with_continue": bool(result["recovered_with_continue"]),
        "taskboard_signal": taskboard_signal,
    }
