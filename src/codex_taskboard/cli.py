#!/usr/bin/env python3
from __future__ import annotations

import argparse
import curses
import fcntl
import glob
import grp
import hashlib
import json
import math
import os
import posixpath
import pwd
import re
import secrets
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from codex_taskboard.api_access import (
    api_client_task_id,
    api_token_tenant,
    build_api_visibility_scope,
    is_public_queue_view,
    task_visible_in_api_queue,
    task_visible_to_api_token,
)
from codex_taskboard.api_auth import (
    ApiAuthHooks,
    load_api_token_registry as load_api_token_registry_impl,
    resolve_api_token as resolve_api_token_impl,
)
from codex_taskboard.api_server import ApiServerHooks, serve_api
from codex_taskboard.automation_state import (
    AutomationStateHooks,
    automation_mode as automation_mode_impl,
    automation_mode_is_managed as automation_mode_is_managed_impl,
    automation_mode_label as automation_mode_label_impl,
    bind_continuous_research_mode_session as bind_continuous_research_mode_session_impl,
    bind_human_guidance_mode_session as bind_human_guidance_mode_session_impl,
    clear_all_continuous_research_mode as clear_all_continuous_research_mode_impl,
    clear_all_human_guidance_mode as clear_all_human_guidance_mode_impl,
    clear_continuous_research_mode_session as clear_continuous_research_mode_session_impl,
    clear_continuous_research_session_waiting_state as clear_continuous_research_session_waiting_state_impl,
    clear_human_guidance_mode_session as clear_human_guidance_mode_session_impl,
    continuous_research_enabled_session_ids as continuous_research_enabled_session_ids_impl,
    continuous_research_mode_enabled as continuous_research_mode_enabled_impl,
    continuous_research_mode_label as continuous_research_mode_label_impl,
    continuous_research_mode_path as continuous_research_mode_path_impl,
    continuous_research_session_state as continuous_research_session_state_impl,
    human_guidance_active_session_ids as human_guidance_active_session_ids_impl,
    human_guidance_mode_active as human_guidance_mode_active_impl,
    human_guidance_mode_label as human_guidance_mode_label_impl,
    human_guidance_mode_path as human_guidance_mode_path_impl,
    human_guidance_retry_after_seconds as human_guidance_retry_after_seconds_impl,
    load_continuous_research_mode as load_continuous_research_mode_impl,
    load_human_guidance_mode as load_human_guidance_mode_impl,
    next_parked_idle_repeat_count as next_parked_idle_repeat_count_impl,
    normalize_continuous_research_mode_payload as normalize_continuous_research_mode_payload_impl,
    normalize_human_guidance_mode_payload as normalize_human_guidance_mode_payload_impl,
    park_continuous_research_session as park_continuous_research_session_impl,
    resolve_continuous_research_target_session_id as resolve_continuous_research_target_session_id_impl,
    resolve_human_guidance_target_session_id as resolve_human_guidance_target_session_id_impl,
    set_automation_mode as set_automation_mode_impl,
    set_continuous_research_mode as set_continuous_research_mode_impl,
    set_human_guidance_mode as set_human_guidance_mode_impl,
    should_override_stop_signal_with_continuous_research as should_override_stop_signal_with_continuous_research_impl,
    toggle_automation_mode as toggle_automation_mode_impl,
    toggle_continuous_research_mode as toggle_continuous_research_mode_impl,
    toggle_human_guidance_mode as toggle_human_guidance_mode_impl,
    update_continuous_research_session_state as update_continuous_research_session_state_impl,
    write_continuous_research_mode_payload as write_continuous_research_mode_payload_impl,
    write_human_guidance_mode_payload as write_human_guidance_mode_payload_impl,
)
from codex_taskboard.dispatcher_service import DispatcherServiceHooks, serve_dispatcher_loop
from codex_taskboard.executors import (
    ExecutorRegistryHooks,
    executor_registry_path as executor_registry_path_impl,
    load_executor_registry as load_executor_registry_impl,
    map_host_gpus_to_executor_visible_gpus as map_host_gpus_to_executor_visible_gpus_impl,
    normalize_posix_workdir as normalize_posix_workdir_impl,
    resolve_executor as resolve_executor_impl,
    validate_remote_workdir as validate_remote_workdir_impl,
)
from codex_taskboard.followup_runtime import (
    FollowupRuntimeHooks,
    active_session_followup as active_session_followup_impl,
    build_followup_resume_spec_from_payload as build_followup_resume_spec_from_payload_impl,
    continuous_session_followup_key_for as continuous_session_followup_key_for_impl,
    current_followup_resume_spec as current_followup_resume_spec_impl,
    defer_followup_retry as defer_followup_retry_impl,
    followup_entity_info as followup_entity_info_impl,
    followup_key_for as followup_key_for_impl,
    followup_map_by_task_id as followup_map_by_task_id_impl,
    followup_message_path as followup_message_path_impl,
    followup_path as followup_path_impl,
    followup_processing_sort_key as followup_processing_sort_key_impl,
    followup_task_ids as followup_task_ids_impl,
    load_followups as load_followups_impl,
    merge_queued_notification_lists as merge_queued_notification_lists_impl,
    newer_task_exists as newer_task_exists_impl,
    newer_task_exists_for_spec as newer_task_exists_for_spec_impl,
    queued_feedback_key_for as queued_feedback_key_for_impl,
    queued_notification_entries as queued_notification_entries_impl,
    queue_feedback_resume as queue_feedback_resume_impl,
    rebind_followup_to_current_task as rebind_followup_to_current_task_impl,
    resolve_followup as resolve_followup_impl,
    resolve_followups_for_stop_signal as resolve_followups_for_stop_signal_impl,
    schedule_followup as schedule_followup_impl,
    session_followup_present as session_followup_present_impl,
    should_schedule_followup_for_spec as should_schedule_followup_for_spec_impl,
    sync_followup_state as sync_followup_state_impl,
)
from codex_taskboard.codex_runtime import (
    CodexRuntimeHooks,
    resume_codex_session as resume_codex_session_impl,
    resume_codex_session_with_prompt as resume_codex_session_with_prompt_impl,
    run_codex_prompt_with_continue_recovery as run_codex_prompt_with_continue_recovery_impl,
    run_codex_subagent as run_codex_subagent_impl,
)
from codex_taskboard.session_runtime import (
    SessionRuntimeHooks,
    active_codex_resume_pids_for_session as active_codex_resume_pids_for_session_impl,
    allow_local_rollout_fallback as allow_local_rollout_fallback_impl,
    build_deferred_resume_result as build_deferred_resume_result_impl,
    classify_platform_error as classify_platform_error_impl,
    command_runtime_result_fields as command_runtime_result_fields_impl,
    continue_retry_error_kind as continue_retry_error_kind_impl,
    default_retry_delay_seconds as default_retry_delay_seconds_impl,
    extract_codex_session_id as extract_codex_session_id_impl,
    extract_last_assistant_message_from_rollout as extract_last_assistant_message_from_rollout_impl,
    extract_taskboard_signal as extract_taskboard_signal_impl,
    extract_text_from_message_content as extract_text_from_message_content_impl,
    is_rate_limit_retry_error as is_rate_limit_retry_error_impl,
    is_session_busy_error as is_session_busy_error_impl,
    latest_local_assistant_message_for_session as latest_local_assistant_message_for_session_impl,
    latest_local_rollout_output_snapshot as latest_local_rollout_output_snapshot_impl,
    latest_session_activity_ts as latest_session_activity_ts_impl,
    platform_error_deferred_reason as platform_error_deferred_reason_impl,
    platform_error_from_reason as platform_error_from_reason_impl,
    platform_error_result_fields as platform_error_result_fields_impl,
    platform_error_retry_after_seconds as platform_error_retry_after_seconds_impl,
    platform_error_spec_for_kind as platform_error_spec_for_kind_impl,
    retry_after_seconds_from_target as retry_after_seconds_from_target_impl,
    rollout_candidates_for_session as rollout_candidates_for_session_impl,
    session_busy_retry_after_seconds as session_busy_retry_after_seconds_impl,
    session_output_busy_snapshot as session_output_busy_snapshot_impl,
)
from codex_taskboard.api_submit import (
    ApiSubmitHooks,
    apply_api_token_submit_policy as apply_api_token_submit_policy_impl,
    build_spec_from_submit_job_payload as build_spec_from_submit_job_payload_impl,
)
from codex_taskboard.scheduler_resources import (
    SchedulerResourceHooks,
    command_sets_cpu_thread_limits as command_sets_cpu_thread_limits_impl,
    command_sets_cpu_worker_limits as command_sets_cpu_worker_limits_impl,
    command_sets_cuda_visible_devices as command_sets_cuda_visible_devices_impl,
    command_uses_cpu_runtime_template as command_uses_cpu_runtime_template_impl,
    coerce_non_negative_int as coerce_non_negative_int_impl,
    cpu_resource_retry_reason as cpu_resource_retry_reason_impl,
    declared_cpu_profile as declared_cpu_profile_impl,
    default_cpu_thread_mode as default_cpu_thread_mode_impl,
    default_gpu_min_free_mb as default_gpu_min_free_mb_impl,
    detect_default_cpu_thread_limit as detect_default_cpu_thread_limit_impl,
    detect_gpu_count as detect_gpu_count_impl,
    extract_inline_cuda_visible_devices as extract_inline_cuda_visible_devices_impl,
    extract_inline_thread_limit as extract_inline_thread_limit_impl,
    extract_inline_worker_limit as extract_inline_worker_limit_impl,
    extract_thread_limit_from_env as extract_thread_limit_from_env_impl,
    extract_worker_limit_from_env as extract_worker_limit_from_env_impl,
    gpu_row_can_host_task as gpu_row_can_host_task_impl,
    gpu_row_free_mb as gpu_row_free_mb_impl,
    infer_default_cpu_threads as infer_default_cpu_threads_impl,
    next_cpu_backoff_threads as next_cpu_backoff_threads_impl,
    normalize_cpu_profile as normalize_cpu_profile_impl,
    parse_gpu_id_list as parse_gpu_id_list_impl,
    render_task_command_template as render_task_command_template_impl,
    resolve_cpu_thread_policy as resolve_cpu_thread_policy_impl,
    resolve_cpu_threads as resolve_cpu_threads_impl,
    resolve_cpu_worker_policy as resolve_cpu_worker_policy_impl,
    resolve_cpu_workers as resolve_cpu_workers_impl,
    resolved_cpu_profile as resolved_cpu_profile_impl,
    select_cpu_resources_for_start as select_cpu_resources_for_start_impl,
    select_gpu_ids_for_task as select_gpu_ids_for_task_impl,
    task_gpu_max_util_percent as task_gpu_max_util_percent_impl,
    task_gpu_min_free_mb as task_gpu_min_free_mb_impl,
    task_requested_cpu_budget as task_requested_cpu_budget_impl,
    task_requested_cpu_threads as task_requested_cpu_threads_impl,
    task_requested_cpu_workers as task_requested_cpu_workers_impl,
    task_requested_gpu_ids as task_requested_gpu_ids_impl,
)
from codex_taskboard.scheduler import (
    SchedulerDispatchHooks,
    SchedulerSubmitHooks,
    dispatch_queued_tasks_unlocked as dispatch_queued_tasks_unlocked_impl,
    finalize_submitted_task as finalize_submitted_task_impl,
    reserve_cpu_threads_for_later_tasks as reserve_cpu_threads_for_later_tasks_impl,
)
from codex_taskboard.scheduler_readiness import (
    SchedulerEnrichmentHooks,
    SchedulerReadinessHooks,
    artifact_resolution as artifact_resolution_impl,
    dependency_resolution as dependency_resolution_impl,
    enrich_task_state as enrich_task_state_impl,
    evaluate_task_readiness as evaluate_task_readiness_impl,
    latest_task_state_for_key as latest_task_state_for_key_impl,
    latest_task_states_by_key as latest_task_states_by_key_impl,
    report_resolution as report_resolution_impl,
    report_value_from_state as report_value_from_state_impl,
    required_report_conditions as required_report_conditions_impl,
    selected_gpu_snapshot as selected_gpu_snapshot_impl,
)
from codex_taskboard.api_views import (
    ApiViewHooks,
    build_task_list_payload_for_api as build_task_list_payload_for_api_impl,
    build_task_result_payload_for_api as build_task_result_payload_for_api_impl,
    submit_job_for_api as submit_job_for_api_impl,
    wait_for_result_payload as wait_for_result_payload_impl,
)
from codex_taskboard.task_dashboard import (
    TaskDashboardHooks,
    build_dashboard_task_entries as build_dashboard_task_entries_impl,
    dashboard_issue_text as dashboard_issue_text_impl,
    filter_dashboard_tasks as filter_dashboard_tasks_impl,
    sort_dashboard_tasks as sort_dashboard_tasks_impl,
)
from codex_taskboard.task_index import (
    load_cached_task_index_rows,
    remove_task_index_entry,
    update_task_index_entry,
)
from codex_taskboard.task_storage import (
    TaskStorageHooks,
    ensure_task_layout as ensure_task_layout_impl,
    iter_all_task_states as iter_all_task_states_impl,
    iter_task_states as iter_task_states_impl,
    load_event as load_event_impl,
    load_task_spec as load_task_spec_impl,
    load_task_state as load_task_state_impl,
    merge_task_state as merge_task_state_impl,
    resolve_event_path as resolve_event_path_impl,
    subagent_last_message_path as subagent_last_message_path_impl,
    task_command_log_path as task_command_log_path_impl,
    task_events_dir as task_events_dir_impl,
    task_last_message_path as task_last_message_path_impl,
    task_paths as task_paths_impl,
    task_paths_for_root as task_paths_for_root_impl,
    task_root as task_root_impl,
    task_runner_log_path as task_runner_log_path_impl,
    task_spec_path as task_spec_path_impl,
    task_state_path as task_state_path_impl,
    write_task_spec as write_task_spec_impl,
    write_task_state as write_task_state_impl,
)
from codex_taskboard.process_runtime import (
    ProcessRuntimeHooks,
    build_tmux_session_name as build_tmux_session_name_impl,
    pid_exists as pid_exists_impl,
    read_pid_cmdline as read_pid_cmdline_impl,
    read_pid_cwd as read_pid_cwd_impl,
    read_pid_snapshot as read_pid_snapshot_impl,
    read_pid_state as read_pid_state_impl,
)
from codex_taskboard.prompt_assets import active_prompt_source, prompt_block_lines
from codex_taskboard.task_payloads import (
    TaskPayloadHooks,
    normalize_task_spec_payload as normalize_task_spec_payload_impl,
    normalize_task_state_payload as normalize_task_state_payload_impl,
)
from codex_taskboard.task_results import TaskResultHooks, build_task_result_payload as build_task_result_payload_impl
from codex_taskboard.service_manager import (
    ServiceManagerHooks,
    TaskboardServiceSpec,
    build_service_doctor_payload,
    default_entrypoint_path,
    render_systemd_unit,
    repo_root,
    run_managed_service,
)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


VERSION = 1
TERMINAL_STATUSES = {"completed", "failed", "terminated", "launch_failed", "observed_exit", "superseded"}
RUNNABLE_STATUSES = {"queued", "submitted"}
ACTIVE_TASK_STATUSES = {"running", "watching"}
SESSION_GUARD_SAMPLE_LIMIT = 6
DUPLICATE_SUBMIT_SAMPLE_LIMIT = 6
HIDDEN_TASK_STATUSES = {"superseded"}
LEGACY_TASK_ROOT_ENV = "CODEX_TASKBOARD_LEGACY_ROOTS"
LEGACY_READS_ENV = "CODEX_TASKBOARD_INCLUDE_LEGACY_READS"
LEGACY_TASK_ROOT_GLOBS = (
    "*/.local/state/codex-taskboard/tasks",
    "*/codex-taskboard-state/tasks",
    "*/.codex/tmux-task-codex-wakeup/tasks",
)
OOM_PATTERNS = [
    "out of memory",
    "cuda out of memory",
    "memoryerror",
    "cannot allocate memory",
    "std::bad_alloc",
    "oom-kill",
    "oom kill",
]
DEFAULT_STARTUP_FAILURE_SECONDS = 90
DEFAULT_GPU_MIN_FREE_MB = 4096
DEFAULT_GPU_FREE_RATIO = 0.25
DEFAULT_GPU_MAX_UTIL_PERCENT = 85
DEFAULT_CPU_THREAD_LIMIT = 40
DEFAULT_CPU_ONLY_THREADS = 8
DEFAULT_GPU_TASK_CPU_THREADS = 4
DEFAULT_SUBAGENT_CPU_THREADS = 1
DEFAULT_GENERIC_TASK_CPU_THREADS = 2
DEFAULT_CPU_RETRY_MAX_ATTEMPTS = 3
DEFAULT_NOTIFICATION_MIN_IDLE_SECONDS = 90
DEFAULT_NOTIFICATION_COALESCE_SECONDS = 8
DEFAULT_SESSION_OUTPUT_BUSY_RETRY_SECONDS = 30
DEFAULT_SESSION_OUTPUT_BUSY_ACTIVITY_SECONDS = 20
DEFAULT_SESSION_OUTPUT_BUSY_OPEN_TURN_STALL_SECONDS = 300
MAX_ROLLOUT_OUTPUT_BUSY_TAIL_LINES = 2048
DEFAULT_LOCAL_MICROSTEP_DELAY_SECONDS = 60
DEFAULT_LOCAL_MICROSTEP_INTERVAL_SECONDS = 180
DEFAULT_LOCAL_MICROSTEP_MIN_IDLE_SECONDS = 60
DEFAULT_WAITING_ON_ASYNC_DELAY_SECONDS = 60 * 60
DEFAULT_WAITING_ON_ASYNC_INTERVAL_SECONDS = 60 * 60
DEFAULT_WAITING_ON_ASYNC_MIN_IDLE_SECONDS = 300
CONTINUOUS_RESEARCH_IDLE_LOOP_THRESHOLD = 3
CONTINUOUS_RESEARCH_LOCAL_FASTPATH_REPEAT_THRESHOLD = 4
DEFAULT_API_BIND = "127.0.0.1"
DEFAULT_SERVICE_API_BIND = "0.0.0.0"
DEFAULT_API_PORT = 8765
DEFAULT_API_POLL_SECONDS = 2.0
DEFAULT_SERVICE_DISPATCHER_MODE = "gpu-fill"
DEFAULT_SERVICE_GPU_COUNT = 4
DEFAULT_SERVICE_POLL_SECONDS = 5.0
DEFAULT_RESEARCH_STALL_QUEUE_AGE_SECONDS = 2 * 60 * 60
DEFAULT_RESEARCH_STALL_PROPOSAL_IDLE_SECONDS = 6 * 60 * 60
DEFAULT_RESEARCH_STALL_TERMINAL_SIGNAL_GRACE_SECONDS = 10 * 60
DEFAULT_RESEARCH_STALL_FOLLOWUP_THRESHOLD = 3
ROLLOUT_FALLBACK_MTIME_GRACE_SECONDS = 1.0
ROLLOUT_FALLBACK_ENTRY_GRACE_SECONDS = 1.0
CURRENT_SESSION_ENV_KEYS = ("CODEX_SESSION_ID", "CODEX_THREAD_ID")
PROPOSAL_ENV_KEY = "CODEX_TASKBOARD_PROPOSAL"
PROPOSAL_SOURCE_ENV_KEY = "CODEX_TASKBOARD_PROPOSAL_SOURCE"
PROPOSAL_ENV_KEYS = (PROPOSAL_ENV_KEY, PROPOSAL_SOURCE_ENV_KEY)
CLOSEOUT_PROPOSAL_DIR_ENV_KEY = "CODEX_TASKBOARD_CLOSEOUT_PROPOSAL_DIR"
CLOSEOUT_PROPOSAL_DIR_SOURCE_ENV_KEY = "CODEX_TASKBOARD_CLOSEOUT_PROPOSAL_DIR_SOURCE"
CLOSEOUT_PROPOSAL_DIR_ENV_KEYS = (CLOSEOUT_PROPOSAL_DIR_ENV_KEY, CLOSEOUT_PROPOSAL_DIR_SOURCE_ENV_KEY)
PROJECT_HISTORY_FILE_ENV_KEY = "CODEX_TASKBOARD_PROJECT_HISTORY_FILE"
PROJECT_HISTORY_FILE_SOURCE_ENV_KEY = "CODEX_TASKBOARD_PROJECT_HISTORY_FILE_SOURCE"
PROJECT_HISTORY_FILE_ENV_KEYS = (PROJECT_HISTORY_FILE_ENV_KEY, PROJECT_HISTORY_FILE_SOURCE_ENV_KEY)
CPU_PROFILE_CHOICES = ("auto", "single", "sidecar", "cpu_compute", "gpu_feeder", "hybrid")
CONTINUOUS_RESEARCH_MODE_FILENAME = "automation_mode.json"
HUMAN_GUIDANCE_MODE_FILENAME = "human_guidance_mode.json"
SESSION_MIGRATIONS_FILENAME = "session_migrations.json"
ACTIVE_FEEDBACK_RUNTIME_FILENAME = "active_feedback_runtime.json"
EXECUTION_READY_SIGNAL = "EXECUTION_READY"
CLOSEOUT_READY_SIGNAL = "CLOSEOUT_READY"
CONTINUOUS_RESEARCH_NEW_TASK_SIGNAL = EXECUTION_READY_SIGNAL
CONTINUOUS_RESEARCH_OVERRIDE_SIGNALS = {CLOSEOUT_READY_SIGNAL}
CONTINUOUS_RESEARCH_REASON = "continuous_research_after_no_further_tasks"
CONTINUOUS_RESEARCH_IDLE_REASON = "continuous_research_session_idle"
CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON = "continuous_research_stall_recovery"
CONTINUOUS_RESEARCH_NEXT_ACTION_REASON = "continuous_research_next_bounded_action"
CONTINUOUS_RESEARCH_TRANSITION_REASON = "continuous_research_closeout_transition"
PROPOSAL_MATERIALIZATION_REASON = "proposal_materialization"
CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE = "continuous_session_reminder"
CONTINUOUS_RESEARCH_TRANSITION_FOLLOWUP_TYPE = "continuous_research_closeout_transition"
DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS = 60
DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS = 300
DEFAULT_CONTINUOUS_RESEARCH_MIN_IDLE_SECONDS = 180
DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS = 60
DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS = 15 * 60
MAX_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS = 8 * 60 * 60
MAX_RECENT_PROJECT_HISTORY_LOG_SCAN_FILES = 5
MAX_RECENT_PROJECT_HISTORY_LOG_CHARS = 24000
MAX_RECENT_NEXT_ACTION_AGE_SECONDS = 3 * 24 * 60 * 60
DEFAULT_HUMAN_GUIDANCE_LEASE_SECONDS = DEFAULT_WAITING_ON_ASYNC_INTERVAL_SECONDS
DEFAULT_SESSION_MIGRATION_INTERRUPT_GRACE_SECONDS = 5
MAX_PROPOSAL_GATE_SCAN_CHARS = 65536
# `full` 保留给需要完整重述治理规则的权威型 prompt；后台 resume/followup
# 已经处在同一会话上下文里，默认应使用 compact 版本，避免重复长提示淹没真实动作尾部。
PROMPT_PROFILE_FULL = "full"
PROMPT_PROFILE_RESUME_COMPACT = "resume_compact"
PROPOSAL_BOOTSTRAP_KEYWORDS = (
    "新 family",
    "new family",
    "新 proposal",
    "proposal 骨架",
    "proposal bootstrap",
    "起草 proposal",
    "起草新 proposal",
    "起草新 family",
    "route replanning",
    "route replan",
    "路线重排",
    "路线重写",
    "新小主线",
    "hypothesis packet",
    "successor hypothesis",
    "hypothesis bootstrap",
    "最小 pilot gate",
    "pilot gate 草案",
)
DIRECT_LOCAL_ARTIFACT_KEYWORDS = (
    "launch spec",
    "launch-spec",
    "launch_spec",
    "runner spec",
    "runner-spec",
    "runner_spec",
    "config materialization",
    "resolved config",
    "resolved yaml",
    "launch 配置",
    "运行配置",
    "物化 launch spec",
    "物化 runner spec",
    "物化配置",
    "launch spec materialization",
    "config 物化",
)
PARKED_REAFFIRMATION_KEYWORDS = (
    "PARKED_IDLE",
    "保持 parked",
    "继续 parked",
    "进入 parked",
    "转入 parked",
    "进入静默等待",
    "保持 route-1 parked",
    "继续保持 parked",
    "waiting for new evidence",
    "waiting for external evidence",
)
RATE_LIMIT_PATTERNS = [
    "exceeded retry limit",
    "429 too many requests",
]
SESSION_BUSY_PATTERNS = [
    "session is busy",
    "conversation is busy",
    "thread is busy",
    "another request is active",
    "another response is in progress",
    "already has an active run",
    "already in progress",
    "please wait for the current request",
    "please wait for the current response",
    "please wait for the current run",
]
DEFAULT_RESUME_RETRY_SECONDS = 60
DEFAULT_PLATFORM_ERROR_HUMAN_RETRY_SECONDS = 300
PLATFORM_ERROR_SIGNATURES = (
    {
        "kind": "platform_auth_or_quota",
        "patterns": (
            "401 unauthorized",
            "403 forbidden",
            "authentication failed",
            "invalid api key",
            "insufficient_quota",
            "quota exceeded",
            "billing hard limit",
            "billing_not_active",
            "organization deactivated",
        ),
        "retryable": False,
        "summary": "上游平台鉴权、额度或计费配置异常，需要人工处理。",
    },
    {
        "kind": "upstream_platform_transient",
        "patterns": (
            "500 internal server error",
            "502 bad gateway",
            "503 service unavailable",
            "504 gateway timeout",
            "server overloaded",
            "temporarily unavailable",
            "upstream request timeout",
            "request timed out",
            "timed out while contacting",
            "connection reset",
            "connection aborted",
            "connection refused",
            "network error",
            "transport error",
            "relay error",
            "relay server error",
            "upstream proxy error",
            "proxy error",
            "中转站错误",
            "中转站异常",
            "中转服务错误",
            "代理错误",
            "网关错误",
        ),
        "retryable": True,
        "summary": "上游平台暂时不可用或网络异常，taskboard 将延迟重试。",
    },
)
CPU_THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "TORCH_NUM_THREADS",
)
CPU_WORKER_ENV_KEYS = (
    "CODEX_TASKBOARD_CPU_WORKERS",
    "NUM_WORKERS",
    "DATALOADER_WORKERS",
)
CPU_RUNTIME_ENV_KEYS = (
    "CODEX_TASKBOARD_CPU_THREADS",
    "CODEX_TASKBOARD_CPU_WORKERS",
    "CODEX_TASKBOARD_CPU_PROFILE",
    "CODEX_TASKBOARD_CPU_BUDGET",
)
CPU_RESOURCE_RETRY_PATTERNS = (
    "resource temporarily unavailable",
    "can't start new thread",
    "cannot start new thread",
    "pthread_create failed",
    "thread creation failed",
    "unable to create thread",
    "blas_thread_init",
    "libgomp: thread creation failed",
)
FAILURE_EXCERPT_PATTERNS = [
    "traceback",
    "cuda out of memory",
    "out of memory",
    "memoryerror",
    "std::bad_alloc",
    "exception",
    "error:",
    "runtimeerror",
    "killed",
    "sigkill",
]
MANUAL_DECISION_GATE_KEYWORDS = (
    "manual dispatch handoff",
    "manual decision handoff",
    "manual activation",
    "manual_only",
    "manual-only",
    "manual gate",
    "人工决策",
    "人工审批",
    "人工判断点",
    "等待人工",
    "预算审批",
    "预算承诺",
    "owner budget commit",
    "budget commit",
    "owner_commit",
    "等待对单一 manual dispatch handoff 的显式解释",
    "required_fields_before_interpretation",
    "manual_activation_required",
)
SUCCESS_TASKBOARD_SIGNALS = {"TASK_DONE", "START_NEXT_TASK"}
STOP_FOLLOWUP_SIGNALS = {CLOSEOUT_READY_SIGNAL, "STOP_AUTOMATION"}
LOCAL_MICROSTEP_BATCH_SIGNAL = "LOCAL_MICROSTEP_BATCH"
LOCAL_CONTINUE_NO_WAKE_SIGNAL = "LOCAL_CONTINUE_NO_WAKE"
WAITING_ON_ASYNC_SIGNAL = "WAITING_ON_ASYNC"
WAITING_ON_LIVE_TASK_SIGNAL = "WAITING_ON_LIVE_TASK"
WAITING_ON_FEEDBACK_SIGNAL = "WAITING_ON_FEEDBACK"
PARKED_IDLE_SIGNAL = "PARKED_IDLE"
ANALYZING_NEW_EVIDENCE_SIGNAL = "ANALYZING_NEW_EVIDENCE"
MATERIALS_READY_FOR_PROPOSAL_SIGNAL = "MATERIALS_READY_FOR_PROPOSAL"
TASKBOARD_RESEARCH_PHASE_VALUES = {"planning", "execution", "closeout"}
INLINE_CONTINUE_SIGNALS = {EXECUTION_READY_SIGNAL}
LOCAL_MICROSTEP_BATCH_SIGNALS = {EXECUTION_READY_SIGNAL}
WAITING_ON_ASYNC_SIGNALS = {WAITING_ON_ASYNC_SIGNAL, WAITING_ON_LIVE_TASK_SIGNAL}
PARKED_IDLE_SIGNALS = {PARKED_IDLE_SIGNAL}
SESSION_PROGRESS_SIGNALS = LOCAL_MICROSTEP_BATCH_SIGNALS | INLINE_CONTINUE_SIGNALS | WAITING_ON_ASYNC_SIGNALS
TASKBOARD_LIVE_TASK_STATUS_VALUES = {"none", "submitted", "awaiting"}
TASKBOARD_PUBLIC_SIGNAL_VALUES = {
    EXECUTION_READY_SIGNAL,
    WAITING_ON_ASYNC_SIGNAL,
    CLOSEOUT_READY_SIGNAL,
    "none",
}
TASKBOARD_LEGACY_SIGNAL_VALUES = {
    LOCAL_CONTINUE_NO_WAKE_SIGNAL,
    LOCAL_MICROSTEP_BATCH_SIGNAL,
    MATERIALS_READY_FOR_PROPOSAL_SIGNAL,
    ANALYZING_NEW_EVIDENCE_SIGNAL,
    WAITING_ON_LIVE_TASK_SIGNAL,
    PARKED_IDLE_SIGNAL,
    "NO_FURTHER_TASKS",
    "END_EXPERIMENT",
    "NEW_TASKS_STARTED",
}
TASKBOARD_SIGNAL_VALUES = TASKBOARD_PUBLIC_SIGNAL_VALUES | TASKBOARD_LEGACY_SIGNAL_VALUES | SUCCESS_TASKBOARD_SIGNALS | {"STOP_AUTOMATION"}
CANONICAL_HEAD_CONTRACT_VERSION = "CH1"
CANONICAL_HEAD_BEGIN_MARKER = "TASKBOARD_CANONICAL_HEAD_BEGIN"
CANONICAL_HEAD_END_MARKER = "TASKBOARD_CANONICAL_HEAD_END"
CANONICAL_HEAD_SCAN_CHARS = 8192
CANONICAL_HEAD_REQUIRED_KEYS = ("BIG_MAINLINE", "SMALL_MAINLINE", "CURRENT_BOUNDARY", "NEXT_STEP")
CANONICAL_HEAD_OPTIONAL_KEYS = ("KEY_EVIDENCE", "MILESTONE")
LOCAL_MICROSTEP_BATCH_REASON = "execution_reentry"
WAITING_ON_ASYNC_REASON = "waiting_on_async_watchdog"
PROTOCOL_SELF_CHECK_REPAIR_REASON = "protocol_self_check_repair"
PROTOCOL_SELF_CHECK_REPAIR_FOLLOWUP_TYPE = "protocol_self_check_repair"
DEFAULT_PROTOCOL_REPAIR_DELAY_SECONDS = 20
DEFAULT_PROTOCOL_REPAIR_INTERVAL_SECONDS = 180
DEFAULT_PROTOCOL_REPAIR_MIN_IDLE_SECONDS = 30

LEGACY_TASKBOARD_SIGNAL_ALIASES = {
    WAITING_ON_LIVE_TASK_SIGNAL: WAITING_ON_ASYNC_SIGNAL,
    ANALYZING_NEW_EVIDENCE_SIGNAL: EXECUTION_READY_SIGNAL,
    LOCAL_CONTINUE_NO_WAKE_SIGNAL: EXECUTION_READY_SIGNAL,
    LOCAL_MICROSTEP_BATCH_SIGNAL: EXECUTION_READY_SIGNAL,
    MATERIALS_READY_FOR_PROPOSAL_SIGNAL: EXECUTION_READY_SIGNAL,
    "NEW_TASKS_STARTED": EXECUTION_READY_SIGNAL,
    "NO_FURTHER_TASKS": CLOSEOUT_READY_SIGNAL,
    "END_EXPERIMENT": CLOSEOUT_READY_SIGNAL,
    PARKED_IDLE_SIGNAL: "none",
}


def canonicalize_taskboard_signal(signal: str) -> str:
    normalized_signal = str(signal or "").strip()
    if not normalized_signal:
        return ""
    return LEGACY_TASKBOARD_SIGNAL_ALIASES.get(normalized_signal, normalized_signal)


def infer_taskboard_research_phase(
    *,
    explicit_phase: str = "",
    step_class: str = "",
    final_signal: str = "",
) -> str:
    normalized_phase = str(explicit_phase or "").strip()
    if normalized_phase in TASKBOARD_RESEARCH_PHASE_VALUES:
        return normalized_phase
    normalized_signal = canonicalize_taskboard_signal(final_signal)
    if normalized_signal == CLOSEOUT_READY_SIGNAL:
        return "closeout"
    if normalized_signal == EXECUTION_READY_SIGNAL or normalized_signal == WAITING_ON_ASYNC_SIGNAL:
        return "execution"
    return "execution"
TRAINING_PATTERNS = [
    r"\btorchrun\b",
    r"\bdeepspeed\b",
    r"\baccelerate\b",
    r"\btrainer?\b",
    r"\bfinetune\b",
    r"\bpretrain\b",
    r"\btrain(_worker)?\b",
    r"\bgrpo\b",
    r"\bworker_loop\b",
    r"\bprobe\b",
    r"\bsweep\b",
    r"\bprofile\b",
    r"run_grpo_baseline\.py",
    r"continuous_[a-z0-9_]+\.py",
    r"training",
]
TRAINING_EXCLUDE_PATTERNS = [
    r"codex-taskboard",
    r"\bnvidia-smi\b",
    r"\bnvitop\b",
    r"\brg\b",
]
REMOTE_LAST_MESSAGE_BEGIN = "__CODEX_TASKBOARD_LAST_MESSAGE_BEGIN__"
REMOTE_LAST_MESSAGE_END = "__CODEX_TASKBOARD_LAST_MESSAGE_END__"
PROPOSAL_SIDECAR_HINTS = ("watch", "watcher", "receipt", "dispatch")
PROPOSAL_STRONG_HINTS = ("closeout", "decision")
CONTINUOUS_RESEARCH_LIFECYCLE_TOKEN_FIELDS = (
    "task_id",
    "status",
    "submitted_at",
    "started_at",
    "started_via_tmux_at",
    "ended_at",
    "failure_kind",
    "taskboard_signal",
)
MISSING = object()
TIMESTAMP_FIELD_NAMES = frozenset(
    {
        "captured_at",
        "created_at",
        "ended_at",
        "finished_at",
        "followup_stopped_at",
        "last_checked_at",
        "migrated_at",
        "notification_finished_at",
        "paused_until",
        "queued_at",
        "started_at",
        "started_via_tmux_at",
        "submitted_at",
        "superseded_at",
        "timestamp",
        "updated_at",
        "waiting_since",
    }
)


def build_beijing_timezone() -> timezone:
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:
            pass
    return timezone(timedelta(hours=8), name="UTC+08:00")


BEIJING_TIMEZONE = build_beijing_timezone()


@dataclass(frozen=True)
class AppConfig:
    app_home: Path
    tasks_root: Path
    locks_root: Path
    followups_root: Path
    legacy_task_roots: tuple[Path, ...]
    tmux_socket_path: Path
    codex_home: Path
    threads_db_path: Path
    thread_manifest_path: Path
    sync_script_path: Path
    codex_bin: str
    tmux_bin: str


def format_datetime_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TIMEZONE)
    return dt.astimezone(BEIJING_TIMEZONE).isoformat()


def format_beijing_filename_timestamp(ts: float | None = None) -> str:
    dt = datetime.now(BEIJING_TIMEZONE) if ts is None else datetime.fromtimestamp(ts, tz=BEIJING_TIMEZONE)
    return dt.strftime("%Y%m%dT%H%M%S%z")


def utc_now() -> str:
    # Historical helper name retained so existing call sites keep working while
    # all taskboard timestamps are now emitted in explicit Beijing time.
    return format_datetime_iso(datetime.now(BEIJING_TIMEZONE))


def format_unix_timestamp(ts: int | float) -> str:
    return format_datetime_iso(datetime.fromtimestamp(float(ts), tz=BEIJING_TIMEZONE))


def normalize_timestamp_parse_text(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    upper = normalized.upper()
    if normalized.endswith("Z"):
        return f"{normalized[:-1]}+00:00"
    if upper.endswith(" UTC"):
        return f"{normalized[:-4].rstrip()}+00:00"
    if upper.endswith(" CST"):
        return f"{normalized[:-4].rstrip()}+08:00"
    if normalized.endswith("北京时间"):
        return f"{normalized[:-4].rstrip()}+08:00"
    return normalized


def parse_timestamp_to_unix(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = normalize_timestamp_parse_text(text)
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BEIJING_TIMEZONE)
        return dt.timestamp()
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def canonicalize_timestamp_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = parse_timestamp_to_unix(text)
    if parsed is None:
        return text
    return format_unix_timestamp(parsed)


def normalize_timestamp_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        normalized: dict[str, Any] = {}
        for key, value in payload.items():
            if key in TIMESTAMP_FIELD_NAMES:
                normalized[key] = canonicalize_timestamp_text(value)
            elif isinstance(value, (dict, list)):
                normalized[key] = normalize_timestamp_fields(value)
            else:
                normalized[key] = value
        return normalized
    if isinstance(payload, list):
        return [normalize_timestamp_fields(item) for item in payload]
    return payload


def timestamp_sort_value(value: Any, *, missing: float) -> float:
    parsed = parse_timestamp_to_unix(value)
    if parsed is None:
        return missing
    return parsed


def format_timestamp_for_display(value: Any, *, pattern: str, empty: str = "-") -> str:
    parsed = parse_timestamp_to_unix(value)
    if parsed is None:
        text = str(value or "").strip()
        return text or empty
    return datetime.fromtimestamp(parsed, tz=BEIJING_TIMEZONE).strftime(pattern)


def parse_boolish(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def build_attention_config() -> AppConfig:
    return build_config(
        argparse.Namespace(
            app_home=os.environ.get("CODEX_TASKBOARD_HOME", str(Path.home() / ".local" / "state" / "codex-taskboard")),
            codex_home=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
            codex_bin=os.environ.get("CODEX_BIN", "codex"),
            tmux_bin=os.environ.get("TMUX_BIN", "tmux"),
        )
    )


def safe_iter_attention_states() -> list[dict[str, Any]]:
    try:
        return iter_all_task_states(build_attention_config())
    except Exception:
        return []


def task_state_timestamp(state: dict[str, Any]) -> float | None:
    for key in ("submitted_at", "updated_at", "ended_at", "started_at"):
        parsed = parse_timestamp_to_unix(state.get(key))
        if parsed is not None:
            return parsed
    return None


def is_same_attention_chain(
    state: dict[str, Any],
    *,
    workdir: str,
    proposal_path: str,
    session_id: str,
) -> bool:
    if workdir and str(state.get("workdir", "")).strip() == workdir:
        return True
    if proposal_path and str(state.get("proposal_path", "")).strip() == proposal_path:
        return True
    if session_id and str(state.get("codex_session_id", "")).strip() == session_id:
        return True
    return False


def detect_research_stall_attention(event: dict[str, Any], spec: dict[str, Any]) -> tuple[bool, str, str]:
    status = str(event.get("status", "")).strip()
    if status not in TERMINAL_STATUSES:
        return False, "", ""

    workdir = str(spec.get("workdir", "")).strip()
    proposal_raw = str(spec.get("proposal_path", "")).strip()
    closeout_raw = str(spec.get("closeout_proposal_dir", "")).strip()
    session_id = str(spec.get("codex_session_id", "")).strip()
    current_task_id = str(spec.get("task_id") or event.get("task_id") or "").strip()
    ended_ts = parse_timestamp_to_unix(event.get("ended_at")) or time.time()
    queue_age_threshold = max(
        300,
        int(spec.get("research_stall_queue_age_threshold_seconds", DEFAULT_RESEARCH_STALL_QUEUE_AGE_SECONDS)),
    )
    proposal_idle_threshold = max(
        queue_age_threshold,
        int(spec.get("research_stall_proposal_idle_threshold_seconds", DEFAULT_RESEARCH_STALL_PROPOSAL_IDLE_SECONDS)),
    )
    terminal_signal_grace = max(
        0,
        int(spec.get("research_stall_terminal_signal_grace_seconds", DEFAULT_RESEARCH_STALL_TERMINAL_SIGNAL_GRACE_SECONDS)),
    )
    followup_threshold = max(
        1,
        int(spec.get("research_stall_followup_threshold", DEFAULT_RESEARCH_STALL_FOLLOWUP_THRESHOLD)),
    )

    proposal_path: Path | None = None
    if proposal_raw:
        try:
            proposal_path = Path(proposal_raw).expanduser().resolve()
        except Exception:
            proposal_path = None

    closeout_dir: Path | None = None
    if closeout_raw:
        try:
            closeout_dir = Path(closeout_raw).expanduser().resolve()
        except Exception:
            closeout_dir = None

    proposal_mtime: float | None = None
    latest_proposal_path: Path | None = proposal_path
    if proposal_path and proposal_path.exists():
        try:
            proposal_mtime = proposal_path.stat().st_mtime
            proposal_candidates = [path for path in proposal_path.parent.glob("PROPOSAL-*.md") if path.is_file()]
            if proposal_path not in proposal_candidates:
                proposal_candidates.append(proposal_path)
            if proposal_candidates:
                latest_proposal_path = max(proposal_candidates, key=lambda path: path.stat().st_mtime)
        except Exception:
            latest_proposal_path = proposal_path

    related_states = [
        state
        for state in safe_iter_attention_states()
        if is_same_attention_chain(
            state,
            workdir=workdir,
            proposal_path=proposal_raw,
            session_id=session_id,
        )
    ]

    stale_queued_states: list[dict[str, Any]] = []
    for state in related_states:
        if str(state.get("task_id", "")).strip() == current_task_id:
            continue
        if str(state.get("status", "")).strip() not in RUNNABLE_STATUSES:
            continue
        submitted_ts = task_state_timestamp(state)
        if submitted_ts is None:
            continue
        if ended_ts - submitted_ts >= queue_age_threshold:
            stale_queued_states.append(state)

    signal_text = str(event.get("taskboard_signal", "")).strip().upper()
    if signal_text in STOP_FOLLOWUP_SIGNALS | CONTINUOUS_RESEARCH_OVERRIDE_SIGNALS:
        closeout_materialized = False
        if closeout_dir and closeout_dir.exists():
            try:
                closeout_materialized = any(
                    path.is_file() and path.stat().st_mtime >= ended_ts - terminal_signal_grace
                    for path in closeout_dir.rglob("*.md")
                )
            except Exception:
                closeout_materialized = False
        newer_proposal_materialized = False
        if latest_proposal_path and proposal_path and latest_proposal_path != proposal_path:
            try:
                newer_proposal_materialized = latest_proposal_path.stat().st_mtime >= ended_ts - terminal_signal_grace
            except Exception:
                newer_proposal_materialized = False
        new_task_dispatched = any(
            str(state.get("task_id", "")).strip() != current_task_id
            and (task_state_timestamp(state) or 0) >= ended_ts - terminal_signal_grace
            for state in related_states
        )
        if not (closeout_materialized or newer_proposal_materialized or new_task_dispatched):
            return (
                True,
                "research_stall:terminal_signal_without_closeout_or_followthrough",
                "任务已经输出阶段性终止信号，但没有看到紧随其后的 closeout、新 proposal 或新任务。请先完成总结、路线切换与首批分发，再结束该实验链。",
            )

    if latest_proposal_path and proposal_path and latest_proposal_path == proposal_path:
        stale_old_proposal_states = [
            state
            for state in stale_queued_states
            if str(state.get("proposal_path", "")).strip()
            and str(state.get("proposal_path", "")).strip() != str(proposal_path)
        ]
        if stale_old_proposal_states:
            return (
                True,
                "research_stall:queued_backlog_after_proposal_switch",
                f"检测到 {len(stale_old_proposal_states)} 个长时间排队任务仍绑定旧 proposal，而当前链已经切换到新 proposal。请先做 queue hygiene、supersede 或显式清理旧 backlog。",
            )

    if proposal_mtime is not None:
        followup_like_states: list[dict[str, Any]] = []
        hygiene_like_states: list[dict[str, Any]] = []
        for state in related_states:
            state_ts = task_state_timestamp(state)
            if state_ts is None or state_ts < proposal_mtime:
                continue
            text = " ".join(
                [
                    str(state.get("task_key", "")),
                    str(state.get("task_note", "")),
                    str(state.get("command", "")),
                ]
            ).lower()
            if str(state.get("status", "")).strip() in TERMINAL_STATUSES and any(
                hint in text for hint in ("followup", "nudge", "monitor")
            ):
                followup_like_states.append(state)
            if any(hint in text for hint in ("queue hygiene", "queue_hygiene", "queue-hygiene", "supersede", "cleanup")):
                hygiene_like_states.append(state)
        if stale_queued_states and len(followup_like_states) >= followup_threshold and not hygiene_like_states:
            return (
                True,
                "research_stall:repeated_followup_without_queue_hygiene",
                "同一 proposal 链已经连续触发多次 followup/nudge/monitor，但旧队列仍长期堆积且没有 queue hygiene 痕迹。请先清理或 supersede backlog，再继续自动推进。",
            )

        new_task_after_proposal = any(
            str(state.get("task_id", "")).strip() != current_task_id
            and (task_state_timestamp(state) or 0) >= proposal_mtime
            for state in related_states
        )
        if ended_ts - proposal_mtime >= proposal_idle_threshold and not new_task_after_proposal:
            return (
                True,
                "research_stall:no_dispatch_after_proposal_update",
                "proposal 已更新较长时间，但没有看到新的 proposal-bound task 被分发。请检查是否遗漏 dispatch、queue hygiene 或 next-step routing。",
            )

    return False, "", ""


def build_config(args: argparse.Namespace) -> AppConfig:
    app_home = Path(args.app_home).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser().resolve()
    legacy_task_roots = discover_legacy_task_roots(app_home, codex_home=codex_home)
    return AppConfig(
        app_home=app_home,
        tasks_root=app_home / "tasks",
        locks_root=app_home / "locks",
        followups_root=app_home / "followups",
        legacy_task_roots=legacy_task_roots,
        tmux_socket_path=app_home / "tmux" / "default",
        codex_home=codex_home,
        threads_db_path=codex_home / "state_5.sqlite",
        thread_manifest_path=codex_home / "thread_sync_manifest.jsonl",
        sync_script_path=codex_home / "scripts" / "sync_codex_threads.py",
        codex_bin=args.codex_bin,
        tmux_bin=args.tmux_bin,
    )


def resolve_current_codex_session_id(environ: Any | None = None) -> tuple[str, str]:
    source_env = environ if environ is not None else os.environ
    for key in CURRENT_SESSION_ENV_KEYS:
        value = str(source_env.get(key, "")).strip()
        if value:
            return value, key
    return "", ""


def choose_current_codex_session_binding(
    *,
    env_session_id: str,
    env_key: str,
    taskboard_session_id: str,
    taskboard_source: str,
) -> tuple[str, str, bool]:
    normalized_env_session_id = str(env_session_id or "").strip()
    normalized_env_key = str(env_key or "").strip()
    normalized_taskboard_session_id = str(taskboard_session_id or "").strip()
    normalized_taskboard_source = str(taskboard_source or "").strip()
    prefer_taskboard = (
        normalized_env_key == "CODEX_THREAD_ID"
        and normalized_env_session_id
        and normalized_taskboard_session_id
        and normalized_env_session_id != normalized_taskboard_session_id
    )
    if prefer_taskboard:
        return normalized_taskboard_session_id, normalized_taskboard_source or "taskboard_workdir", True
    return (
        normalized_env_session_id or normalized_taskboard_session_id,
        normalized_env_key or normalized_taskboard_source,
        False,
    )


def workdirs_overlap(left: str, right: str) -> bool:
    normalized_left = normalize_session_guard_workdir(left)
    normalized_right = normalize_session_guard_workdir(right)
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    left_prefix = normalized_left.rstrip("/") + "/"
    right_prefix = normalized_right.rstrip("/") + "/"
    return normalized_left.startswith(right_prefix) or normalized_right.startswith(left_prefix)


def taskboard_session_candidates_for_workdir(
    config: AppConfig,
    *,
    workdir: str,
    agent_name: str = "",
) -> list[dict[str, str]]:
    normalized_workdir = normalize_session_guard_workdir(workdir)
    if not normalized_workdir:
        return []

    candidates: list[dict[str, str]] = []
    for state in iter_all_task_states(config):
        status = str(state.get("status", "")).strip()
        if is_hidden_status(status):
            continue
        if status not in ACTIVE_TASK_STATUSES | RUNNABLE_STATUSES and not bool(state.get("pending_feedback", False)):
            continue
        state_workdir = normalize_session_guard_workdir(str(state.get("workdir", "")))
        if not workdirs_overlap(normalized_workdir, state_workdir):
            continue
        session_id = str(state.get("codex_session_id", "")).strip()
        if not session_id:
            continue
        candidates.append(
            {
                "codex_session_id": session_id,
                "agent_name": str(state.get("agent_name", "")).strip(),
                "workdir": state_workdir,
                "submitted_at": str(state.get("submitted_at", "")).strip(),
                "updated_at": str(state.get("updated_at", "")).strip(),
                "task_id": str(state.get("task_id", "")).strip(),
            }
        )

    exact_matches = [item for item in candidates if item.get("workdir") == normalized_workdir]
    if exact_matches:
        candidates = exact_matches
    candidates.sort(
        key=lambda item: (
            timestamp_sort_value(item.get("updated_at"), missing=float("-inf")),
            timestamp_sort_value(item.get("submitted_at"), missing=float("-inf")),
            item.get("task_id", ""),
        ),
        reverse=True,
    )

    if agent_name:
        matching_agent = [item for item in candidates if item.get("agent_name", "") == agent_name]
        if matching_agent:
            candidates = matching_agent
    return candidates


def infer_taskboard_codex_session_id(
    config: AppConfig,
    *,
    workdir: str,
    agent_name: str = "",
) -> tuple[str, str, list[str]]:
    candidates = taskboard_session_candidates_for_workdir(config, workdir=workdir, agent_name=agent_name)
    if not candidates:
        return "", "", []
    sessions = sorted({str(item.get("codex_session_id", "")).strip() for item in candidates if str(item.get("codex_session_id", "")).strip()})
    if len(sessions) == 1:
        source = "taskboard_workdir_agent" if agent_name else "taskboard_workdir"
        return sessions[0], source, sessions
    return "", "taskboard_workdir_ambiguous", sessions


def resolve_requested_codex_session_id(
    raw_session_id: Any,
    *,
    feedback_mode: str,
    environ: Any | None = None,
    config: AppConfig | None = None,
    workdir: str = "",
    agent_name: str = "",
) -> str:
    session_id = str(raw_session_id or "").strip()
    if session_id or str(feedback_mode or "auto").strip() == "off":
        return session_id
    inferred_session_id, inferred_env_key = resolve_current_codex_session_id(environ)
    if config is not None and str(workdir or "").strip():
        taskboard_session_id, taskboard_source, _candidates = infer_taskboard_codex_session_id(
            config,
            workdir=workdir,
            agent_name=agent_name,
        )
        selected_session_id, _resolved_from, _preferred_taskboard = choose_current_codex_session_binding(
            env_session_id=inferred_session_id,
            env_key=inferred_env_key,
            taskboard_session_id=taskboard_session_id,
            taskboard_source=taskboard_source,
        )
        if selected_session_id:
            return selected_session_id
    if inferred_session_id:
        return inferred_session_id
    return ""


def extract_raw_proposal_value(payload: dict[str, Any]) -> Any:
    if "proposal" in payload:
        value = payload.get("proposal")
        return MISSING if value is None else value
    if "proposal_path" in payload:
        value = payload.get("proposal_path")
        return MISSING if value is None else value
    return MISSING


def extract_raw_closeout_proposal_dir(payload: dict[str, Any]) -> Any:
    if "closeout_proposal_dir" in payload:
        value = payload.get("closeout_proposal_dir")
        return MISSING if value is None else value
    return MISSING


def extract_raw_project_history_file(payload: dict[str, Any]) -> Any:
    if "project_history" in payload:
        value = payload.get("project_history")
        return MISSING if value is None else value
    if "project_history_file" in payload:
        value = payload.get("project_history_file")
        return MISSING if value is None else value
    return MISSING


def normalize_proposal_path(raw_value: Any, *, workdir: str = "") -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        base = Path(workdir).expanduser() if str(workdir or "").strip() else Path.cwd()
        path = base / path
    return str(path.resolve(strict=False))


def normalize_closeout_proposal_dir(raw_value: Any, *, workdir: str = "") -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        base = Path(workdir).expanduser() if str(workdir or "").strip() else Path.cwd()
        path = base / path
    return str(path.resolve(strict=False))


def normalize_project_history_file(raw_value: Any, *, workdir: str = "") -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        base = Path(workdir).expanduser() if str(workdir or "").strip() else Path.cwd()
        path = base / path
    return str(path.resolve(strict=False))


def suggested_project_history_log_dir(project_history_file: str) -> str:
    text = str(project_history_file or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-._") or "project-history"
    return str((path.parent / f"{safe_stem}-logs").resolve(strict=False))


def normalize_history_control_key(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return ""
    normalized = normalized.replace("：", ":")
    normalized = re.sub(r"[`*_]+", "", normalized)
    normalized = re.sub(r"[\s-]+", "_", normalized)
    normalized = re.sub(r"[^0-9a-z_\u4e00-\u9fff]+", "", normalized)
    return normalized.strip("_")


def strip_markdown_line_prefix(line: str) -> str:
    stripped = str(line or "").strip()
    if not stripped:
        return ""
    stripped = re.sub(r"^[>*-]+\s*", "", stripped)
    stripped = re.sub(r"^\d+\.\s*", "", stripped)
    return stripped.strip()


def unwrap_inline_code(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("`") and value.endswith("`") and len(value) >= 2:
        value = value[1:-1].strip()
    return value


def parse_history_control_bool(value: Any) -> bool | None:
    lowered = unwrap_inline_code(str(value or "")).strip().lower()
    if not lowered:
        return None
    lowered = lowered.replace("：", ":")
    if lowered in {"1", "true", "yes", "y", "on", "required", "ready", "local", "cpu_only", "是", "需要"}:
        return True
    if lowered in {"0", "false", "no", "n", "off", "none", "null", "否", "不需要", "无需"}:
        return False
    if any(token in lowered for token in ("无需", "不需要", "no ", "without ")):
        return False
    if any(token in lowered for token in ("require", "need", "必须", "需要")):
        return True
    return None


def iter_loose_key_value_lines(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in str(text or "").splitlines():
        stripped = strip_markdown_line_prefix(line)
        if not stripped:
            continue
        match = re.match(r"^([^:=：]+?)\s*[:=：]\s*(.+)$", stripped)
        if not match:
            continue
        key = unwrap_inline_code(match.group(1)).strip()
        value = unwrap_inline_code(match.group(2)).strip()
        if not key or not value:
            continue
        rows.append((key, value))
    return rows


def parse_project_history_log_created_at(text: str) -> float:
    for key, value in iter_loose_key_value_lines(text[:4000]):
        if normalize_history_control_key(key) not in {"created_at", "createdat"}:
            continue
        parsed = parse_timestamp_to_unix(value)
        if parsed is not None:
            return parsed
    return 0.0


def parse_project_history_log_filename_ts(path: Path) -> float:
    match = re.match(r"^(\d{8}T\d{6}(?:Z|[+-]\d{4})?)", path.name)
    if not match:
        return 0.0
    parsed = parse_timestamp_to_unix(match.group(1))
    return float(parsed or 0.0)


def project_history_log_recency_key(path: Path) -> tuple[float, float, str]:
    created_at_ts = 0.0
    try:
        excerpt = path.read_text(encoding="utf-8", errors="ignore")[:4000]
    except Exception:
        excerpt = ""
    if excerpt:
        created_at_ts = parse_project_history_log_created_at(excerpt) or 0.0
    filename_ts = parse_project_history_log_filename_ts(path)
    try:
        mtime_ts = float(path.stat().st_mtime)
    except OSError:
        mtime_ts = 0.0
    return (
        float(created_at_ts or filename_ts or mtime_ts),
        mtime_ts,
        path.name,
    )


def project_history_log_candidates(project_history_file: str) -> list[Path]:
    log_dir = suggested_project_history_log_dir(project_history_file)
    if not log_dir:
        return []
    root = Path(log_dir).expanduser()
    if not root.exists():
        return []
    paths = [path for path in root.glob("*.md") if path.is_file()]
    paths.sort(key=project_history_log_recency_key, reverse=True)
    return paths[:MAX_RECENT_PROJECT_HISTORY_LOG_SCAN_FILES]


def next_action_heading(text: str) -> bool:
    normalized = normalize_history_control_key(text)
    return any(
        token in normalized
        for token in (
            "next_bounded_action",
            "next_action",
            "next_step",
            "下一步",
            "下一动作",
            "唯一最高优先级动作",
            "最高优先级动作",
        )
    )


def text_contains_any_token(text: str, lowered_text: str, tokens: tuple[str, ...]) -> bool:
    for token in tokens:
        if token in text or token.lower() in lowered_text:
            return True
    return False


def first_matching_token(text: str, lowered_text: str, tokens: tuple[str, ...]) -> str:
    for token in tokens:
        if token in text or token.lower() in lowered_text:
            return token
    return ""


def empty_continuation_hint() -> dict[str, Any]:
    return {
        "status": "missing",
        "action_text": "",
        "action_hash": "",
        "cpu_only": False,
        "requires_async": False,
        "requires_gpu": False,
        "requires_live_task": False,
        "future_callback": False,
        "conflict": False,
        "stale": False,
        "controller_inherit_local": False,
        "proposal_bootstrap": False,
        "proposal_bootstrap_reason": "",
        "direct_local_artifact": False,
        "direct_local_artifact_reason": "",
        "dispatch_ready": False,
        "dispatch_ready_reason": "",
        "parked_reaffirmation": False,
        "parked_reaffirmation_reason": "",
        "collect_local_evidence": False,
        "collect_local_evidence_reason": "",
        "source_kind": "",
        "source_path": "",
        "source_updated_at": "",
        "age_seconds": 0,
        "parser": "none",
    }


def parse_project_history_next_action_from_text(text: str) -> dict[str, Any]:
    explicit_action_lines: list[str] = []
    section_lines: list[str] = []
    metadata_flags: dict[str, bool | None] = {
        "cpu_only": None,
        "requires_async": None,
        "requires_gpu": None,
        "requires_live_task": None,
        "future_callback": None,
    }
    for key, value in iter_loose_key_value_lines(text):
        normalized_key = normalize_history_control_key(key)
        if normalized_key in {
            "next_bounded_action",
            "next_bounded_action_text",
            "next_action",
            "next_step",
            "当前唯一最高优先级动作",
            "唯一最高优先级动作",
            "下一步",
            "下一动作",
        }:
            explicit_action_lines.append(value)
            continue
        if normalized_key in {"cpu_only", "cpuonly", "local_cpu_only", "requires_local_inline"}:
            metadata_flags["cpu_only"] = parse_history_control_bool(value)
        elif normalized_key in {"requires_async", "need_async", "async_required"}:
            metadata_flags["requires_async"] = parse_history_control_bool(value)
        elif normalized_key in {"requires_gpu", "need_gpu", "gpu_required"}:
            metadata_flags["requires_gpu"] = parse_history_control_bool(value)
        elif normalized_key in {"requires_live_task", "live_task_required", "need_live_task"}:
            metadata_flags["requires_live_task"] = parse_history_control_bool(value)
        elif normalized_key in {"future_callback", "requires_future_callback"}:
            metadata_flags["future_callback"] = parse_history_control_bool(value)

    in_next_action_section = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if re.match(r"^\s*#{1,6}\s+", line):
            heading = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
            if next_action_heading(heading):
                section_lines = []
                in_next_action_section = True
                continue
            if in_next_action_section:
                break
            continue
        if not in_next_action_section:
            continue
        if not stripped:
            continue
        cleaned = strip_markdown_line_prefix(stripped)
        if cleaned:
            section_lines.append(unwrap_inline_code(cleaned))

    action_lines = [line.strip() for line in explicit_action_lines if line.strip()]
    if not action_lines and section_lines:
        list_items = [
            strip_markdown_line_prefix(line)
            for line in section_lines
            if re.match(r"^\s*(?:[-*]|\d+\.)\s+", line)
        ]
        action_lines = [item for item in list_items if item]
        if not action_lines:
            action_lines = [line for line in section_lines if line]
    normalized_action_lines: list[str] = []
    seen_lines: set[str] = set()
    for line in action_lines:
        collapsed = re.sub(r"\s+", " ", unwrap_inline_code(line)).strip()
        if not collapsed or collapsed in seen_lines:
            continue
        seen_lines.add(collapsed)
        normalized_action_lines.append(collapsed)

    context_lines = [line for line in section_lines if line] or [line for line in explicit_action_lines if line]
    context_text = "\n".join(context_lines)
    lowered_context = context_text.lower()
    proposal_bootstrap_reason = next(
        (
            token
            for token in PROPOSAL_BOOTSTRAP_KEYWORDS
            if token in context_text or token.lower() in lowered_context
        ),
        "",
    )
    direct_local_artifact_reason = next(
        (
            token
            for token in DIRECT_LOCAL_ARTIFACT_KEYWORDS
            if token in context_text or token.lower() in lowered_context
        ),
        "",
    )
    parked_reaffirmation_reason = next(
        (
            token
            for token in PARKED_REAFFIRMATION_KEYWORDS
            if token in context_text or token.lower() in lowered_context
        ),
        "",
    )
    cpu_only_tokens = (
        "CPU-only",
        "cpu-only",
        "cpu only",
        "本地短步骤",
        "本地微步骤",
        "inline",
        "无需 GPU",
        "不启动 GPU",
        "无需 future callback",
        "无需 live task",
    )
    gpu_negative = text_contains_any_token(
        context_text,
        lowered_context,
        (
            "无需 GPU",
            "不启动 GPU",
            "不重开 GPU",
            "先不重开 GPU",
            "暂不重开 GPU",
            "不提交 GPU",
            "gpu 仍不可直接提交",
        ),
    )
    live_task_negative = text_contains_any_token(
        context_text,
        lowered_context,
        (
            "无需 live task",
            "没有 live task",
            "无 live task",
            "不需要 live task",
            "不提交 live task",
            "先不提交 live task",
            "暂不提交 live task",
            "尚不提交 live task",
            "尚未提交 live task",
            "未提交 live task",
            "live task 尚未提交",
            "live task 未提交",
            "不提交实验",
            "先不提交实验",
            "暂不提交实验",
            "尚不提交实验",
            "实验暂不提交",
        ),
    )
    future_callback_negative = text_contains_any_token(
        context_text,
        lowered_context,
        (
            "无需 future callback",
            "不需要 future callback",
            "无需回流",
            "不需要回流",
        ),
    )
    async_negative = text_contains_any_token(
        context_text,
        lowered_context,
        (
            "CPU-only",
            "cpu-only",
            "cpu only",
            "本地短步骤",
            "本地微步骤",
            "仍保持 CPU-only",
            "无需 GPU",
            "不启动 GPU",
            "不重开 GPU",
            "无需 future callback",
            "无需 live task",
            "无需 async",
            "不需要 async",
            "不提交 async",
            "先不提交 async",
            "暂不提交 async",
            "尚不提交 async",
        ),
    )
    cpu_only = metadata_flags["cpu_only"]
    if cpu_only is None:
        cpu_only = text_contains_any_token(context_text, lowered_context, cpu_only_tokens)
    requires_gpu = metadata_flags["requires_gpu"]
    if requires_gpu is None:
        requires_gpu = (
            text_contains_any_token(
                context_text,
                lowered_context,
                (
                    "需要 GPU",
                    "启动 GPU",
                    "gpu queue",
                    "GPU 队列",
                    "多卡",
                    "正式训练",
                    "full benchmark",
                    "训练发车",
                    "重开 GPU",
                    "提交 GPU",
                ),
            )
            and not gpu_negative
        )
    requires_live_task = metadata_flags["requires_live_task"]
    if requires_live_task is None:
        requires_live_task = (
            text_contains_any_token(
                context_text,
                lowered_context,
                (
                    "live task",
                    "WAITING_ON_LIVE_TASK",
                    "submitted",
                    "awaiting",
                    "提交 live task",
                    "真实 live task",
                    "绑定 live task",
                    "submit live task",
                    "验证实验",
                    "提交实验",
                    "绑定实验",
                    "实验发车",
                    "实验绑定",
                    "launch experiment",
                    "validation experiment",
                ),
            )
            and not live_task_negative
        )
    future_callback = metadata_flags["future_callback"]
    if future_callback is None:
        future_callback = (
            text_contains_any_token(
                context_text,
                lowered_context,
                (
                    "future callback",
                    "等待回流",
                    "远程",
                    "长时等待",
                    "需要 future callback",
                ),
            )
            and not future_callback_negative
        )
    requires_async = metadata_flags["requires_async"]
    if requires_async is None:
        requires_async = (
            requires_gpu
            or requires_live_task
            or future_callback
            or (
                any(
                    token in context_text or token in lowered_context
                    for token in (
                        "WAITING_ON_ASYNC",
                        "async task",
                        "needs async",
                        "submit async",
                        "提交 async",
                        "需要 async",
                    )
                )
                and not async_negative
            )
        )

    dispatch_ready_reason = ""
    if requires_live_task and not requires_gpu and not future_callback:
        dispatch_ready_reason = first_matching_token(
            context_text,
            lowered_context,
            (
                "提交一条",
                "提交 live task",
                "显式绑定",
                "bind-before-launch",
                "bind before launch",
                "submit live task",
                "bound live task",
                "绑定 live task",
                "launch spec",
                "提交实验",
                "绑定实验",
                "验证实验",
                "实验发车",
            ),
        )
    dispatch_ready = bool(dispatch_ready_reason) and not live_task_negative

    conflict = bool(cpu_only) and bool(requires_async or requires_gpu or requires_live_task or future_callback) and not dispatch_ready
    proposal_bootstrap = bool(proposal_bootstrap_reason) and not bool(requires_async or requires_gpu or requires_live_task or future_callback)
    direct_local_artifact = bool(direct_local_artifact_reason) and not bool(
        requires_async or requires_gpu or requires_live_task or future_callback
    )
    parked_reaffirmation = bool(parked_reaffirmation_reason) and not proposal_bootstrap and not bool(requires_async)
    action_text = " | ".join(normalized_action_lines)
    status = "missing"
    if action_text:
        if conflict:
            status = "conflict"
        elif parked_reaffirmation:
            status = "parked"
        elif dispatch_ready:
            status = "dispatch_ready"
        elif requires_async:
            status = "requires_async"
        else:
            status = "ready_local"
    action_hash = ""
    if action_text:
        action_hash = hashlib.sha1(
            json.dumps(
                {
                    "action_text": action_text,
                    "cpu_only": bool(cpu_only),
                    "requires_async": bool(requires_async),
                    "requires_gpu": bool(requires_gpu),
                    "requires_live_task": bool(requires_live_task),
                    "future_callback": bool(future_callback),
                    "proposal_bootstrap": bool(proposal_bootstrap),
                    "direct_local_artifact": bool(direct_local_artifact),
                    "dispatch_ready": bool(dispatch_ready),
                    "parked_reaffirmation": bool(parked_reaffirmation),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:12]
    return {
        "status": status,
        "action_text": action_text,
        "action_hash": action_hash,
        "cpu_only": bool(cpu_only),
        "requires_async": bool(requires_async),
        "requires_gpu": bool(requires_gpu),
        "requires_live_task": bool(requires_live_task),
        "future_callback": bool(future_callback),
        "conflict": conflict,
        "proposal_bootstrap": bool(proposal_bootstrap),
        "proposal_bootstrap_reason": proposal_bootstrap_reason,
        "direct_local_artifact": bool(direct_local_artifact),
        "direct_local_artifact_reason": direct_local_artifact_reason,
        "dispatch_ready": bool(dispatch_ready),
        "dispatch_ready_reason": dispatch_ready_reason,
        "parked_reaffirmation": bool(parked_reaffirmation),
        "parked_reaffirmation_reason": parked_reaffirmation_reason,
        "parser": "section" if section_lines else ("structured" if explicit_action_lines else "none"),
    }


def recent_project_history_next_action_hint(
    spec: dict[str, Any],
    *,
    now_ts: float | None = None,
) -> dict[str, Any]:
    project_history_file = str(spec.get("project_history_file", "")).strip()
    empty = empty_continuation_hint()
    if not project_history_file:
        return empty
    current_ts = float(now_ts if now_ts is not None else time.time())
    for path in project_history_log_candidates(project_history_file):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        excerpt = text[:MAX_RECENT_PROJECT_HISTORY_LOG_CHARS]
        parsed = parse_project_history_next_action_from_text(excerpt)
        if parsed["status"] == "missing":
            continue
        source_ts = parse_project_history_log_created_at(excerpt) or parse_project_history_log_filename_ts(path)
        if source_ts <= 0:
            try:
                source_ts = float(path.stat().st_mtime)
            except OSError:
                source_ts = 0.0
        age_seconds = max(0, int(current_ts - source_ts)) if source_ts > 0 else 0
        stale = source_ts > 0 and age_seconds > MAX_RECENT_NEXT_ACTION_AGE_SECONDS
        status = str(parsed["status"])
        if stale and status == "ready_local":
            status = "stale"
        return {
            **empty,
            **parsed,
            "status": status,
            "stale": stale,
            "controller_inherit_local": status == "ready_local" and not stale and not parsed.get("conflict", False),
            "source_kind": "history_log",
            "source_path": str(path),
            "source_updated_at": format_unix_timestamp(source_ts) if source_ts > 0 else "",
            "age_seconds": age_seconds,
        }
    return empty


def latest_project_history_log_timestamp(project_history_file: str) -> tuple[float, str]:
    for path in project_history_log_candidates(project_history_file):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
        source_ts = parse_project_history_log_created_at(text) or parse_project_history_log_filename_ts(path)
        if source_ts <= 0:
            try:
                source_ts = float(path.stat().st_mtime)
            except OSError:
                source_ts = 0.0
        if source_ts > 0:
            return source_ts, str(path)
    return 0.0, ""


def file_mtime_timestamp(path: str) -> float:
    normalized_path = str(path or "").strip()
    if not normalized_path:
        return 0.0
    try:
        return float(Path(normalized_path).expanduser().stat().st_mtime)
    except OSError:
        return 0.0


def canonical_head_next_action_hint(
    spec: dict[str, Any],
    *,
    now_ts: float | None = None,
) -> dict[str, Any]:
    empty = empty_continuation_hint()
    current_ts = float(now_ts if now_ts is not None else time.time())
    target_specs = [
        ("history_head", str(spec.get("project_history_file", "")).strip()),
        ("proposal_head", str(spec.get("proposal_path", "")).strip()),
    ]
    for _label, path in target_specs:
        inspected = inspect_canonical_head_file(path, role="history" if "history" in _label else "proposal")
        payload = inspected.get("payload", {})
        if not isinstance(payload, dict):
            continue
        next_step = str(payload.get("NEXT_STEP", "")).strip()
        if not next_step:
            continue
        parsed = parse_project_history_next_action_from_text(f"## Next bounded action\n1. {next_step}")
        source_ts = file_mtime_timestamp(path)
        age_seconds = max(0, int(current_ts - source_ts)) if source_ts > 0 else 0
        return {
            **empty,
            **parsed,
            "controller_inherit_local": (
                str(parsed.get("status", "")) == "ready_local"
                and not bool(parsed.get("conflict", False))
            ),
            "source_kind": "canonical_head",
            "source_path": path,
            "source_updated_at": format_unix_timestamp(source_ts) if source_ts > 0 else "",
            "age_seconds": age_seconds,
            "parser": "canonical_head",
        }
    return empty


def recent_local_evidence_sweep_hint(
    config: AppConfig,
    session_id: str,
    *,
    spec: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    empty = empty_continuation_hint()
    normalized_session_id = str(session_id or "").strip()
    anchor_spec = spec or latest_continuous_research_anchor_spec(config, normalized_session_id, states=states) or {}
    if not normalized_session_id:
        normalized_session_id = str(anchor_spec.get("codex_session_id", "")).strip()
    if not normalized_session_id:
        return empty
    candidate_states = [
        state
        for state in (states if states is not None else iter_all_task_states(config))
        if str(state.get("codex_session_id", "")).strip() == normalized_session_id
        and not is_hidden_status(str(state.get("status", "")))
        and str(state.get("last_event_path", "")).strip()
        and str(state.get("status", "")).strip() in {"completed", "failed", "launch_failed", "observed_exit"}
    ]
    if not candidate_states:
        return empty
    latest_state = max(candidate_states, key=task_state_recency_key)
    last_event_path = str(latest_state.get("last_event_path", "")).strip()
    if not last_event_path:
        return empty
    try:
        event = load_event(Path(last_event_path).expanduser().resolve())
    except Exception:
        return empty
    event_ts = (
        parse_timestamp_to_unix(event.get("ended_at"))
        or parse_timestamp_to_unix(event.get("finished_at"))
        or parse_timestamp_to_unix(latest_state.get("ended_at"))
        or parse_timestamp_to_unix(latest_state.get("updated_at"))
        or 0.0
    )
    history_path = str(anchor_spec.get("project_history_file", "")).strip()
    proposal_path = str(anchor_spec.get("proposal_path", "")).strip()
    latest_history_log_ts, latest_history_log_path = latest_project_history_log_timestamp(history_path)
    absorption_ts = max(
        latest_history_log_ts,
        file_mtime_timestamp(history_path),
        file_mtime_timestamp(proposal_path),
    )
    if event_ts > 0 and absorption_ts >= event_ts:
        return empty
    feedback_data_path = str(event.get("feedback_data_path", "")).strip()
    command_log_path = str(event.get("command_log_path", "")).strip()
    runner_log_path = str(event.get("runner_log_path", "")).strip()
    artifact_context = event.get("artifact_context", [])
    artifact_paths = [
        str(item.get("path", "")).strip()
        for item in artifact_context
        if isinstance(item, dict) and str(item.get("path", "")).strip()
    ]
    receipt_path = feedback_data_path or last_event_path
    action_parts = [
        "吸收最近 receipt 与本地 artifact，先写时间日志或 proposal 局部 note，再更新 claim boundary / NEXT_STEP",
    ]
    if str(event.get("failure_kind", "")).strip():
        action_parts.append(f"failure_kind={str(event.get('failure_kind', '')).strip()}")
    if latest_history_log_path:
        action_parts.append(f"latest_history_log={latest_history_log_path}")
    if artifact_paths:
        action_parts.append(f"artifacts={','.join(artifact_paths[:2])}")
    action_text = " | ".join(action_parts)
    action_hash = hashlib.sha1(
        json.dumps(
            {
                "action_text": action_text,
                "source_path": receipt_path,
                "event_status": str(event.get("status", latest_state.get("status", ""))).strip(),
                "artifact_paths": artifact_paths[:4],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    current_ts = float(now_ts if now_ts is not None else time.time())
    age_seconds = max(0, int(current_ts - event_ts)) if event_ts > 0 else 0
    return {
        **empty,
        "status": "ready_local",
        "action_text": action_text,
        "action_hash": action_hash,
        "cpu_only": True,
        "controller_inherit_local": True,
        "collect_local_evidence": True,
        "collect_local_evidence_reason": "receipt_pending_absorption",
        "source_kind": "local_receipt",
        "source_path": receipt_path,
        "source_updated_at": format_unix_timestamp(event_ts) if event_ts > 0 else "",
        "age_seconds": age_seconds,
        "parser": "receipt",
        "requires_async": False,
        "requires_gpu": False,
        "requires_live_task": False,
        "future_callback": False,
        "conflict": False,
        "proposal_bootstrap": False,
        "proposal_bootstrap_reason": "",
        "dispatch_ready": False,
        "dispatch_ready_reason": "",
        "direct_local_artifact": False,
        "direct_local_artifact_reason": "",
        "parked_reaffirmation": False,
        "parked_reaffirmation_reason": "",
        "receipt_event_path": last_event_path,
        "receipt_feedback_data_path": feedback_data_path,
        "receipt_command_log_path": command_log_path,
        "receipt_runner_log_path": runner_log_path,
        "receipt_latest_history_log_path": latest_history_log_path,
    }


def session_continuation_hint(
    config: AppConfig,
    session_id: str,
    *,
    spec: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    anchor_spec = spec or latest_continuous_research_anchor_spec(config, session_id, states=states) or {}
    history_hint = recent_project_history_next_action_hint(anchor_spec, now_ts=now_ts)
    evidence_hint = recent_local_evidence_sweep_hint(
        config,
        session_id,
        spec=anchor_spec,
        states=states,
        now_ts=now_ts,
    )
    if evidence_hint.get("action_text"):
        return evidence_hint
    if history_hint.get("action_text") and str(history_hint.get("status", "")) not in {"missing", "stale"}:
        return history_hint
    canonical_hint = canonical_head_next_action_hint(anchor_spec, now_ts=now_ts)
    if canonical_hint.get("action_text"):
        return canonical_hint
    if history_hint.get("action_text"):
        return history_hint
    return empty_continuation_hint()


def controller_continuation_hint_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    hint = spec.get("controller_continuation_hint", {})
    if isinstance(hint, dict) and hint.get("action_text"):
        return hint
    history_hint = recent_project_history_next_action_hint(spec)
    if history_hint.get("action_text") and str(history_hint.get("status", "")) not in {"missing", "stale"}:
        return history_hint
    canonical_hint = canonical_head_next_action_hint(spec)
    if canonical_hint.get("action_text"):
        return canonical_hint
    if history_hint.get("action_text"):
        return history_hint
    return empty_continuation_hint()


def recent_project_history_next_action_prompt_lines(spec: dict[str, Any]) -> list[str]:
    hint = controller_continuation_hint_from_spec(spec)
    if not hint.get("action_text"):
        return []
    source_kind = str(hint.get("source_kind", "")).strip()
    heading = (
        "controller 解析到的最近时间日志 Next bounded action："
        if source_kind in {"", "history_log"}
        else "controller 解析到的当前 continuation hint："
    )
    lines = [
        heading,
        f"- status: {hint.get('status', 'missing')}",
        f"- action: {hint.get('action_text', '')}",
    ]
    if source_kind:
        lines.append(f"- source_kind: {source_kind}")
    if bool(hint.get("proposal_bootstrap", False)):
        lines.append(f"- proposal_bootstrap: true ({hint.get('proposal_bootstrap_reason', '')})")
    if bool(hint.get("direct_local_artifact", False)):
        lines.append(f"- direct_local_artifact: true ({hint.get('direct_local_artifact_reason', '')})")
    if bool(hint.get("dispatch_ready", False)):
        lines.append(f"- dispatch_ready: true ({hint.get('dispatch_ready_reason', '')})")
    if bool(hint.get("collect_local_evidence", False)):
        lines.append(f"- collect_local_evidence: true ({hint.get('collect_local_evidence_reason', '')})")
    if bool(hint.get("parked_reaffirmation", False)):
        lines.append(f"- parked_reaffirmation: true ({hint.get('parked_reaffirmation_reason', '')})")
    if hint.get("source_path"):
        lines.append(f"- source_log: {prompt_path_marker(hint.get('source_path', ''))}")
    if hint.get("source_updated_at"):
        lines.append(f"- source_updated_at: {hint.get('source_updated_at', '')}")
    if hint.get("receipt_feedback_data_path"):
        lines.append(f"- feedback_data_file: {prompt_path_marker(hint.get('receipt_feedback_data_path', ''))}")
    if hint.get("receipt_command_log_path"):
        lines.append(f"- command_log: {prompt_path_marker(hint.get('receipt_command_log_path', ''))}")
    if hint.get("receipt_runner_log_path"):
        lines.append(f"- runner_log: {prompt_path_marker(hint.get('receipt_runner_log_path', ''))}")
    return lines


def should_inherit_recent_next_action(session_state: dict[str, Any], next_action_hint: dict[str, Any]) -> bool:
    if not bool(next_action_hint.get("controller_inherit_local", False)):
        return False
    action_hash = str(next_action_hint.get("action_hash", "")).strip()
    if not action_hash:
        return False
    previous_hash = str(session_state.get("next_action_hash", "")).strip()
    previous_repeat_count = max(0, int(session_state.get("next_action_repeat_count", 0) or 0))
    if (
        previous_hash
        and previous_hash == action_hash
        and previous_repeat_count >= CONTINUOUS_RESEARCH_LOCAL_FASTPATH_REPEAT_THRESHOLD
    ):
        return False
    return True


def proposal_bootstrap_ready_for_session(
    session_state: dict[str, Any],
    next_action_hint: dict[str, Any],
    *,
    effective_wait_state: str = "",
) -> bool:
    if not bool((next_action_hint or {}).get("proposal_bootstrap", False)):
        return False
    normalized_wait_state = canonicalize_taskboard_signal(str(effective_wait_state or session_state.get("waiting_state", "")).strip())
    return normalized_wait_state not in {WAITING_ON_ASYNC_SIGNAL, WAITING_ON_FEEDBACK_SIGNAL}


def proposal_dispatch_ready_for_session(
    session_state: dict[str, Any],
    next_action_hint: dict[str, Any] | None = None,
) -> bool:
    if str(session_state.get("last_signal", "")).strip() == MATERIALS_READY_FOR_PROPOSAL_SIGNAL:
        return True
    return bool((next_action_hint or {}).get("dispatch_ready", False))


def effective_research_phase_for_session(
    session_state: dict[str, Any],
    *,
    next_action_hint: dict[str, Any] | None = None,
    effective_wait_state: str = "",
) -> str:
    stored_phase = str(session_state.get("research_phase", "")).strip()
    if stored_phase in TASKBOARD_RESEARCH_PHASE_VALUES:
        return stored_phase
    normalized_wait_state = canonicalize_taskboard_signal(str(effective_wait_state or session_state.get("waiting_state", "")).strip())
    if normalized_wait_state == CLOSEOUT_READY_SIGNAL:
        return "closeout"
    last_signal = canonicalize_taskboard_signal(str(session_state.get("last_signal", "")).strip())
    if last_signal == CLOSEOUT_READY_SIGNAL:
        return "closeout"
    if proposal_bootstrap_ready_for_session(
        session_state,
        next_action_hint or {},
        effective_wait_state=normalized_wait_state,
    ):
        return "planning"
    return "execution"


def proposal_scope_key(*, workdir: str = "", remote_workdir: str = "") -> str:
    normalized_remote = normalize_posix_workdir(str(remote_workdir or "").strip())
    if normalized_remote:
        return f"remote:{normalized_remote}"
    normalized_workdir = normalize_session_guard_workdir(workdir)
    if normalized_workdir:
        return f"local:{normalized_workdir}"
    return ""


def infer_proposal_owner(spec: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(spec.get("task_id", "")),
            str(spec.get("task_key", "")),
            str(spec.get("agent_name", "")),
            str(spec.get("task_note", "")),
        ]
    ).lower()
    if any(token in text for token in PROPOSAL_STRONG_HINTS):
        return True
    if any(token in text for token in PROPOSAL_SIDECAR_HINTS):
        return False
    return True


def proposal_prompt_mode(spec: dict[str, Any]) -> str:
    if not str(spec.get("proposal_path", "")).strip():
        return ""
    if parse_boolish(spec.get("proposal_owner", False), default=False):
        return "strong"
    return "weak"


def latest_inherited_proposal_path(
    config: AppConfig,
    *,
    codex_session_id: str,
    workdir: str,
    remote_workdir: str,
    agent_name: str,
) -> tuple[str, str]:
    normalized_session_id = str(codex_session_id or "").strip()
    scope = proposal_scope_key(workdir=workdir, remote_workdir=remote_workdir)
    if not normalized_session_id or not scope:
        return "", ""
    candidates = sorted(iter_all_task_states(config), key=task_state_recency_key, reverse=True)
    fallback: tuple[str, str] = ("", "")
    normalized_agent_name = str(agent_name or "").strip()
    for state in candidates:
        if str(state.get("codex_session_id", "")).strip() != normalized_session_id:
            continue
        merged = merged_spec_with_state(config, state)
        if proposal_scope_key(
            workdir=str(merged.get("workdir", "")),
            remote_workdir=str(merged.get("remote_workdir", "")),
        ) != scope:
            continue
        proposal_path = normalize_proposal_path(merged.get("proposal_path", ""), workdir=str(merged.get("workdir", "")))
        if not proposal_path:
            continue
        if normalized_agent_name and str(merged.get("agent_name", "")).strip() == normalized_agent_name:
            return proposal_path, "history"
        if not fallback[0]:
            fallback = (proposal_path, "history")
    return fallback


def latest_inherited_closeout_proposal_dir(
    config: AppConfig,
    *,
    codex_session_id: str,
    workdir: str,
    remote_workdir: str,
    agent_name: str,
) -> tuple[str, str]:
    normalized_session_id = str(codex_session_id or "").strip()
    scope = proposal_scope_key(workdir=workdir, remote_workdir=remote_workdir)
    if not normalized_session_id or not scope:
        return "", ""
    candidates = sorted(iter_all_task_states(config), key=task_state_recency_key, reverse=True)
    fallback: tuple[str, str] = ("", "")
    normalized_agent_name = str(agent_name or "").strip()
    for state in candidates:
        if str(state.get("codex_session_id", "")).strip() != normalized_session_id:
            continue
        merged = merged_spec_with_state(config, state)
        if proposal_scope_key(
            workdir=str(merged.get("workdir", "")),
            remote_workdir=str(merged.get("remote_workdir", "")),
        ) != scope:
            continue
        closeout_proposal_dir = normalize_closeout_proposal_dir(
            merged.get("closeout_proposal_dir", ""),
            workdir=str(merged.get("workdir", "")),
        )
        if not closeout_proposal_dir:
            continue
        if normalized_agent_name and str(merged.get("agent_name", "")).strip() == normalized_agent_name:
            return closeout_proposal_dir, "history"
        if not fallback[0]:
            fallback = (closeout_proposal_dir, "history")
    return fallback


def latest_inherited_project_history_file(
    config: AppConfig,
    *,
    codex_session_id: str,
    workdir: str,
    remote_workdir: str,
    agent_name: str,
) -> tuple[str, str]:
    normalized_session_id = str(codex_session_id or "").strip()
    scope = proposal_scope_key(workdir=workdir, remote_workdir=remote_workdir)
    if not normalized_session_id or not scope:
        return "", ""
    candidates = sorted(iter_all_task_states(config), key=task_state_recency_key, reverse=True)
    fallback: tuple[str, str] = ("", "")
    normalized_agent_name = str(agent_name or "").strip()
    for state in candidates:
        if str(state.get("codex_session_id", "")).strip() != normalized_session_id:
            continue
        merged = merged_spec_with_state(config, state)
        if proposal_scope_key(
            workdir=str(merged.get("workdir", "")),
            remote_workdir=str(merged.get("remote_workdir", "")),
        ) != scope:
            continue
        project_history_file = normalize_project_history_file(
            merged.get("project_history_file", ""),
            workdir=str(merged.get("workdir", "")),
        )
        if not project_history_file:
            continue
        if normalized_agent_name and str(merged.get("agent_name", "")).strip() == normalized_agent_name:
            return project_history_file, "history"
        if not fallback[0]:
            fallback = (project_history_file, "history")
    return fallback


def resolve_requested_proposal_path(
    config: AppConfig,
    *,
    raw_proposal: Any,
    no_inherit_proposal: bool,
    codex_session_id: str,
    workdir: str,
    remote_workdir: str,
    agent_name: str,
    environ: Any | None = None,
    allow_history: bool = True,
) -> tuple[str, str]:
    if raw_proposal is not MISSING:
        proposal_path = normalize_proposal_path(raw_proposal, workdir=workdir)
        return proposal_path, ("explicit" if proposal_path else "explicit_clear")
    if no_inherit_proposal:
        return "", "explicit_clear"
    source_env = environ if environ is not None else os.environ
    env_source = str(source_env.get(PROPOSAL_SOURCE_ENV_KEY, "") or "").strip()
    if env_source == "explicit_clear":
        return "", "explicit_clear"
    env_value = str(source_env.get(PROPOSAL_ENV_KEY, "") or "").strip()
    if env_value:
        return normalize_proposal_path(env_value, workdir=workdir), "env"
    if allow_history:
        history_path, history_source = latest_inherited_proposal_path(
            config,
            codex_session_id=codex_session_id,
            workdir=workdir,
            remote_workdir=remote_workdir,
            agent_name=agent_name,
        )
        if history_path:
            return history_path, history_source or "history"
    return "", ""


def resolve_requested_closeout_proposal_dir(
    config: AppConfig,
    *,
    raw_closeout_proposal_dir: Any,
    codex_session_id: str,
    workdir: str,
    remote_workdir: str,
    agent_name: str,
    environ: Any | None = None,
    allow_history: bool = True,
) -> tuple[str, str]:
    if raw_closeout_proposal_dir is not MISSING:
        closeout_proposal_dir = normalize_closeout_proposal_dir(raw_closeout_proposal_dir, workdir=workdir)
        return closeout_proposal_dir, ("explicit" if closeout_proposal_dir else "explicit_clear")
    source_env = environ if environ is not None else os.environ
    env_source = str(source_env.get(CLOSEOUT_PROPOSAL_DIR_SOURCE_ENV_KEY, "") or "").strip()
    if env_source == "explicit_clear":
        return "", "explicit_clear"
    env_value = str(source_env.get(CLOSEOUT_PROPOSAL_DIR_ENV_KEY, "") or "").strip()
    if env_value:
        return normalize_closeout_proposal_dir(env_value, workdir=workdir), "env"
    if allow_history:
        history_dir, history_source = latest_inherited_closeout_proposal_dir(
            config,
            codex_session_id=codex_session_id,
            workdir=workdir,
            remote_workdir=remote_workdir,
            agent_name=agent_name,
        )
        if history_dir:
            return history_dir, history_source or "history"
    return "", ""


def resolve_requested_project_history_file(
    config: AppConfig,
    *,
    raw_project_history_file: Any,
    codex_session_id: str,
    workdir: str,
    remote_workdir: str,
    agent_name: str,
    environ: Any | None = None,
    allow_history: bool = True,
) -> tuple[str, str]:
    if raw_project_history_file is not MISSING:
        project_history_file = normalize_project_history_file(raw_project_history_file, workdir=workdir)
        return project_history_file, ("explicit" if project_history_file else "explicit_clear")
    source_env = environ if environ is not None else os.environ
    env_source = str(source_env.get(PROJECT_HISTORY_FILE_SOURCE_ENV_KEY, "") or "").strip()
    if env_source == "explicit_clear":
        return "", "explicit_clear"
    env_value = str(source_env.get(PROJECT_HISTORY_FILE_ENV_KEY, "") or "").strip()
    if env_value:
        return normalize_project_history_file(env_value, workdir=workdir), "env"
    if allow_history:
        history_file, history_source = latest_inherited_project_history_file(
            config,
            codex_session_id=codex_session_id,
            workdir=workdir,
            remote_workdir=remote_workdir,
            agent_name=agent_name,
        )
        if history_file:
            return history_file, history_source or "history"
    return "", ""


def apply_local_submission_context(
    config: AppConfig,
    spec: dict[str, Any],
    *,
    environ: Any | None = None,
    allow_history: bool = True,
) -> dict[str, Any]:
    updated = dict(spec)
    feedback_mode = str(updated.get("feedback_mode", "auto")).strip() or "auto"
    updated["codex_session_id"] = resolve_requested_codex_session_id(
        updated.get("codex_session_id", ""),
        feedback_mode=feedback_mode,
        environ=environ,
        config=config,
        workdir=str(updated.get("workdir", "")),
        agent_name=str(updated.get("agent_name", "")),
    )
    proposal_path, proposal_source = resolve_requested_proposal_path(
        config,
        raw_proposal=extract_raw_proposal_value(updated),
        no_inherit_proposal=parse_boolish(updated.get("no_inherit_proposal", False), default=False),
        codex_session_id=str(updated.get("codex_session_id", "")),
        workdir=str(updated.get("proposal_base_workdir", updated.get("workdir", ""))),
        remote_workdir=str(updated.get("remote_workdir", "")),
        agent_name=str(updated.get("agent_name", "")),
        environ=environ,
        allow_history=allow_history,
    )
    updated["proposal_path"] = proposal_path
    updated["proposal_source"] = proposal_source
    updated["proposal_owner"] = bool(proposal_path) and infer_proposal_owner(updated)
    closeout_proposal_dir, closeout_proposal_dir_source = resolve_requested_closeout_proposal_dir(
        config,
        raw_closeout_proposal_dir=extract_raw_closeout_proposal_dir(updated),
        codex_session_id=str(updated.get("codex_session_id", "")),
        workdir=str(updated.get("proposal_base_workdir", updated.get("workdir", ""))),
        remote_workdir=str(updated.get("remote_workdir", "")),
        agent_name=str(updated.get("agent_name", "")),
        environ=environ,
        allow_history=allow_history,
    )
    updated["closeout_proposal_dir"] = closeout_proposal_dir
    updated["closeout_proposal_dir_source"] = closeout_proposal_dir_source
    project_history_file, project_history_file_source = resolve_requested_project_history_file(
        config,
        raw_project_history_file=extract_raw_project_history_file(updated),
        codex_session_id=str(updated.get("codex_session_id", "")),
        workdir=str(updated.get("proposal_base_workdir", updated.get("workdir", ""))),
        remote_workdir=str(updated.get("remote_workdir", "")),
        agent_name=str(updated.get("agent_name", "")),
        environ=environ,
        allow_history=allow_history,
    )
    updated["project_history_file"] = project_history_file
    updated["project_history_file_source"] = project_history_file_source
    updated.pop("proposal", None)
    updated.pop("project_history", None)
    updated.pop("no_inherit_proposal", None)
    return updated

def prompt_path_marker(raw_value: Any) -> str:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return ""
    return f"[{normalized}]"


def join_prompt_lines(lines: list[str]) -> str:
    compacted: list[str] = []
    previous_blank = False
    for raw in lines:
        line = str(raw).rstrip()
        is_blank = not line.strip()
        if is_blank:
            if previous_blank:
                continue
            compacted.append("")
            previous_blank = True
            continue
        compacted.append(line)
        previous_blank = False
    while compacted and compacted[0] == "":
        compacted.pop(0)
    while compacted and compacted[-1] == "":
        compacted.pop()
    return "\n".join(compacted).strip()


def inspect_canonical_head_file(path: str, *, role: str) -> dict[str, Any]:
    normalized_path = str(path or "").strip()
    status = {
        "role": role,
        "path": normalized_path,
        "status": "unbound",
        "hash": "",
        "version": "",
        "missing_keys": list(CANONICAL_HEAD_REQUIRED_KEYS),
        "keys": [],
        "payload": {},
        "legacy_summary_hits": 0,
    }
    if not normalized_path:
        return status
    target = Path(normalized_path).expanduser()
    try:
        text = target.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {
            **status,
            "status": "read_error",
            "missing_keys": list(CANONICAL_HEAD_REQUIRED_KEYS),
        }
    head_text = text[:CANONICAL_HEAD_SCAN_CHARS]
    block_match = re.search(
        rf"<!--\s*{CANONICAL_HEAD_BEGIN_MARKER}(?P<meta>[^>]*)-->(?P<body>.*?)<!--\s*{CANONICAL_HEAD_END_MARKER}\s*-->",
        head_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not block_match:
        lowered = head_text.lower()
        legacy_patterns = (
            "项目主线",
            "主线",
            "mainline",
            "claim boundary",
            "边界",
            "下一步",
            "next step",
            "proposal",
        )
        legacy_hits = sum(1 for pattern in legacy_patterns if pattern in lowered or pattern in head_text)
        return {
            **status,
            "status": "missing_block",
            "legacy_summary_hits": legacy_hits,
        }
    meta = str(block_match.group("meta") or "")
    body = str(block_match.group("body") or "")
    version_match = re.search(r"\b([A-Z]{2,}[0-9]+)\b", meta)
    version = version_match.group(1) if version_match else ""
    payload = {
        str(key).strip().upper(): str(value).strip()
        for key, value in parse_key_value_lines(body).items()
        if str(key).strip()
    }
    keys = sorted(payload.keys())
    missing_keys = [key for key in CANONICAL_HEAD_REQUIRED_KEYS if not payload.get(key)]
    canonical_text = "\n".join(f"{key}={payload[key]}" for key in keys if payload.get(key))
    status_value = "ok"
    if version and version != CANONICAL_HEAD_CONTRACT_VERSION:
        status_value = "unsupported_version"
    elif missing_keys:
        status_value = "missing_keys"
    return {
        **status,
        "status": status_value,
        "hash": hashlib.sha1(canonical_text.encode("utf-8")).hexdigest()[:12] if canonical_text else "",
        "version": version,
        "missing_keys": missing_keys,
        "keys": keys,
        "payload": payload,
    }


def canonical_head_status_lines(spec: dict[str, Any]) -> list[str]:
    targets = [
        ("proposal_head", inspect_canonical_head_file(str(spec.get("proposal_path", "")).strip(), role="proposal")),
        ("history_head", inspect_canonical_head_file(str(spec.get("project_history_file", "")).strip(), role="history")),
    ]
    bound_targets = [(label, item) for label, item in targets if item.get("path")]
    if not bound_targets:
        return []
    lines = [f"canonical_head_check: contract={CANONICAL_HEAD_CONTRACT_VERSION}"]
    for label, item in bound_targets:
        parts = [
            f"{label}: status={item.get('status', 'unbound')}",
            f"hash={item.get('hash') or '-'}",
        ]
        version = str(item.get("version", "") or "").strip()
        if version:
            parts.append(f"version={version}")
        missing_keys = [str(key).strip() for key in item.get("missing_keys", []) if str(key).strip()]
        if missing_keys:
            parts.append(f"missing={','.join(missing_keys)}")
        legacy_hits = int(item.get("legacy_summary_hits", 0) or 0)
        if item.get("status") == "missing_block" and legacy_hits:
            parts.append(f"legacy_summary_hits={legacy_hits}")
        lines.append(" ".join(parts))
    if any(str(item.get("status", "")) != "ok" for _, item in bound_targets):
        lines.append(
            "若本轮会改 proposal/history，只顺手补齐顶部 canonical head 小块即可；它只是机器锚点。"
        )
        lines.append(
            "canonical head 必填：BIG_MAINLINE、SMALL_MAINLINE、CURRENT_BOUNDARY、NEXT_STEP；可选：KEY_EVIDENCE、MILESTONE。"
        )
    return lines


def runtime_canonical_head_prompt_lines(spec: dict[str, Any]) -> list[str]:
    proposal_status = inspect_canonical_head_file(str(spec.get("proposal_path", "")).strip(), role="proposal")
    history_status = inspect_canonical_head_file(str(spec.get("project_history_file", "")).strip(), role="history")
    bound_targets = [item for item in (proposal_status, history_status) if item.get("path")]
    if not bound_targets:
        return []
    visible_statuses = {
        str(item.get("status", "")).strip()
        for item in bound_targets
        if str(item.get("status", "")).strip() not in {"ok", "read_error", "unbound"}
    }
    if not visible_statuses:
        return []
    return canonical_head_status_lines(spec)


def compact_proposal_feedback_instruction_lines(spec: dict[str, Any]) -> list[str]:
    proposal_path = str(spec.get("proposal_path", "")).strip()
    closeout_proposal_dir = str(spec.get("closeout_proposal_dir", "")).strip()
    project_history_file = str(spec.get("project_history_file", "")).strip()
    if not proposal_path and not closeout_proposal_dir and not project_history_file:
        return []
    lines = [""]
    lines.append("当前绑定：")
    if proposal_path:
        lines.append(f"proposal_file: {prompt_path_marker(proposal_path)}")
    if closeout_proposal_dir:
        lines.append(f"closeout_proposal_dir: {prompt_path_marker(closeout_proposal_dir)}")
    if project_history_file:
        lines.append(f"project_history_file: {prompt_path_marker(project_history_file)}")
    project_history_log_dir = suggested_project_history_log_dir(project_history_file) if project_history_file else ""
    if project_history_log_dir:
        lines.append(f"project_history_log_dir: {prompt_path_marker(project_history_log_dir)}")
    if proposal_path:
        lines.append("当前 proposal 以上面的 proposal_file 为准。")
        if project_history_file:
            lines.append(
                "本轮可靠结果、关键诊断结论和下一步明确动作默认先写回当前 proposal；如果主线方向、结论边界或下一阶段入口发生变化，再同步 project_history_file。"
            )
        else:
            lines.append("本轮可靠结果、关键诊断结论和下一步明确动作默认写回当前 proposal。")
    elif project_history_file:
        lines.append("当前至少先把可靠结果、关键诊断结论和下一步明确动作写进当前历史链路。")
    return lines


def proposal_feedback_instruction_lines(spec: dict[str, Any], *, profile: str = PROMPT_PROFILE_FULL) -> list[str]:
    return compact_proposal_feedback_instruction_lines(spec)


def proposal_manual_decision_gate_hints(spec: dict[str, Any], *, max_hints: int = 5) -> list[str]:
    proposal_path = str(spec.get("proposal_path", "")).strip()
    if not proposal_path:
        return []
    try:
        text = Path(proposal_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    hints: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if not any(keyword.lower() in lowered for keyword in MANUAL_DECISION_GATE_KEYWORDS):
            continue
        hints.append(line[:240])
        if len(hints) >= max_hints:
            break
    return hints


def evidence_first_loop_lines(*, compact: bool = True) -> list[str]:
    del compact
    return prompt_block_lines("evidence_first")


def execution_followthrough_instruction_lines(
    spec: dict[str, Any],
    *,
    allow_no_further_tasks: bool,
    profile: str = PROMPT_PROFILE_FULL,
) -> list[str]:
    del profile
    submit_args: list[str] = []
    if str(spec.get("proposal_path", "")).strip():
        submit_args.append("proposal_file")
    if str(spec.get("closeout_proposal_dir", "")).strip():
        submit_args.append("closeout_proposal_dir")
    if str(spec.get("project_history_file", "")).strip():
        submit_args.append("project_history_file")
    lines = ["", "taskboard 使用说明："]
    lines.append("- 当前上下文里还能做完的 CPU-only 工作，优先继续做完，不要为了 signal 人为拆轮。")
    submit_line = submit_binding_instruction_line(spec, submit_args=submit_args)
    if submit_line:
        lines.append(f"- {submit_line}")
    if allow_no_further_tasks:
        lines.append("- 当前是 managed 模式：taskboard 只托管任务和积压回流，不会自动再把同一对话拆成额外短步骤。")
    else:
        lines.append(
            f"- 如果本轮只剩等待受托管实验回流，就输出 `TASKBOARD_SIGNAL={WAITING_ON_ASYNC_SIGNAL}`；taskboard 会按 1 小时节奏提醒你回来确认实验仍在产出。"
        )
        lines.append(
            f"- 只有在你已经重读 proposal/history 与本轮证据，并且明确写出“继续当前 proposal 已无新的信息收益”的分析后，才允许输出 `TASKBOARD_SIGNAL={CLOSEOUT_READY_SIGNAL}`。"
        )
    return lines


def submit_binding_instruction_line(spec: dict[str, Any], *, submit_args: list[str] | None = None) -> str:
    bound_args = submit_args if submit_args is not None else []
    if submit_args is None:
        if str(spec.get("proposal_path", "")).strip():
            bound_args.append("proposal_file")
        if str(spec.get("closeout_proposal_dir", "")).strip():
            bound_args.append("closeout_proposal_dir")
        if str(spec.get("project_history_file", "")).strip():
            bound_args.append("project_history_file")
    if not bound_args:
        return ""
    return (
        "提交新任务时显式传入 "
        + "、".join(bound_args)
        + "，并用 `codex-taskboard status --json` 校验 `proposal_path` 与 live 状态。"
    )


def reschedule_existing_followup(
    config: AppConfig,
    followup: dict[str, Any],
    *,
    reason: str,
    delay_seconds: int,
    interval_seconds: int,
    min_idle_seconds: int,
    keep_followup_type: bool = False,
    continuous_research_origin: bool | None = None,
) -> None:
    followup_key = str(followup.get("followup_key", "")).strip()
    if not followup_key:
        return
    updated_at = utc_now()
    followup["reason"] = reason
    followup["check_after_ts"] = time.time() + max(1, int(delay_seconds or 1))
    followup["interval_seconds"] = max(1, int(interval_seconds or 1))
    followup["min_idle_seconds"] = max(0, int(min_idle_seconds or 0))
    followup["nudge_count"] = 0
    followup["stopped"] = False
    # This helper is currently used when a followup falls back to a standard
    # watchdog lane. Clear stale protocol-repair metadata so later processing
    # does not keep treating the entity as a repair prompt.
    if not keep_followup_type:
        followup.pop("followup_type", None)
    followup.pop("protocol_issue", None)
    followup.pop("protocol_footer", None)
    followup.pop("protocol_observed_signal", None)
    if continuous_research_origin is not None:
        if continuous_research_origin:
            followup["continuous_research_origin"] = True
        else:
            followup.pop("continuous_research_origin", None)
    followup["last_action"] = f"scheduled:{reason}"
    followup["last_checked_at"] = updated_at
    followup["updated_at"] = updated_at
    atomic_write_json(followup_path(config, followup_key), followup)
    append_followup_event_log(config, event="scheduled", reason=reason, followup=followup, detail="rescheduled_existing_followup")


def followup_has_continuous_research_origin(followup: dict[str, Any]) -> bool:
    if parse_boolish(followup.get("continuous_research_origin", False), default=False):
        return True
    followup_type = str(followup.get("followup_type", "")).strip()
    reason = str(followup.get("reason", "")).strip()
    return followup_type == CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE or reason in {
        CONTINUOUS_RESEARCH_REASON,
        CONTINUOUS_RESEARCH_IDLE_REASON,
        CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON,
    }


def clear_waiting_state_for_inline_continue(
    config: AppConfig,
    *,
    session_id: str,
    spec: dict[str, Any],
    signal_value: str,
    updated_by: str,
    source: str,
) -> str:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return ""
    evidence_token = continuous_research_session_evidence_token(config, normalized_session_id, spec=spec)
    clear_continuous_research_session_waiting_state(
        config,
        codex_session_id=normalized_session_id,
        evidence_token=evidence_token,
        last_signal=signal_value,
        stable_idle_repeat_count=0,
        next_action_repeat_count=0,
        updated_by=updated_by,
        source=source,
    )
    return evidence_token


def execution_autowake_enabled_for_spec(
    config: AppConfig,
    *,
    spec: dict[str, Any],
    session_id: str = "",
) -> bool:
    normalized_session_id = str(session_id or "").strip() or str(spec.get("codex_session_id", "")).strip()
    if not normalized_session_id:
        return False
    return continuous_research_mode_enabled(config, codex_session_id=normalized_session_id)


def schedule_local_microstep_followup(
    config: AppConfig,
    *,
    task_id: str,
    spec: dict[str, Any],
    followup: dict[str, Any] | None = None,
) -> bool:
    if not execution_autowake_enabled_for_spec(config, spec=spec):
        return False
    if followup is not None and str(followup.get("followup_type", "")).strip() != "queued_feedback_resume":
        keep_followup_type = str(followup.get("followup_type", "")).strip() == CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE
        reschedule_existing_followup(
            config,
            followup,
            reason=LOCAL_MICROSTEP_BATCH_REASON,
            delay_seconds=DEFAULT_LOCAL_MICROSTEP_DELAY_SECONDS,
            interval_seconds=DEFAULT_LOCAL_MICROSTEP_INTERVAL_SECONDS,
            min_idle_seconds=DEFAULT_LOCAL_MICROSTEP_MIN_IDLE_SECONDS,
            keep_followup_type=keep_followup_type,
            continuous_research_origin=followup_has_continuous_research_origin(followup),
        )
        merge_task_state(
            config,
            task_id,
            followup_status="scheduled",
            followup_last_action=f"scheduled:{LOCAL_MICROSTEP_BATCH_REASON}",
            followup_stopped_at="",
        )
        return True
    schedule_followup(
        config,
        task_id=task_id,
        spec=spec,
        reason=LOCAL_MICROSTEP_BATCH_REASON,
        delay_seconds=DEFAULT_LOCAL_MICROSTEP_DELAY_SECONDS,
        interval_seconds=DEFAULT_LOCAL_MICROSTEP_INTERVAL_SECONDS,
        min_idle_seconds=DEFAULT_LOCAL_MICROSTEP_MIN_IDLE_SECONDS,
    )
    return True


def schedule_waiting_on_async_watchdog(
    config: AppConfig,
    *,
    task_id: str,
    spec: dict[str, Any],
    followup: dict[str, Any] | None = None,
) -> bool:
    if not execution_autowake_enabled_for_spec(config, spec=spec):
        return False
    if followup is not None and str(followup.get("followup_type", "")).strip() != "queued_feedback_resume":
        keep_followup_type = str(followup.get("followup_type", "")).strip() == CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE
        reschedule_existing_followup(
            config,
            followup,
            reason=WAITING_ON_ASYNC_REASON,
            delay_seconds=DEFAULT_WAITING_ON_ASYNC_DELAY_SECONDS,
            interval_seconds=DEFAULT_WAITING_ON_ASYNC_INTERVAL_SECONDS,
            min_idle_seconds=DEFAULT_WAITING_ON_ASYNC_MIN_IDLE_SECONDS,
            keep_followup_type=keep_followup_type,
            continuous_research_origin=followup_has_continuous_research_origin(followup),
        )
        merge_task_state(
            config,
            task_id,
            followup_status="scheduled",
            followup_last_action=f"scheduled:{WAITING_ON_ASYNC_REASON}",
            followup_stopped_at="",
        )
        return True
    schedule_followup(
        config,
        task_id=task_id,
        spec=spec,
        reason=WAITING_ON_ASYNC_REASON,
        delay_seconds=DEFAULT_WAITING_ON_ASYNC_DELAY_SECONDS,
        interval_seconds=DEFAULT_WAITING_ON_ASYNC_INTERVAL_SECONDS,
        min_idle_seconds=DEFAULT_WAITING_ON_ASYNC_MIN_IDLE_SECONDS,
    )
    return True


def schedule_protocol_self_check_repair(
    config: AppConfig,
    *,
    task_id: str,
    spec: dict[str, Any],
    issue_summary: str,
    protocol_footer: dict[str, Any] | None,
    observed_signal: str = "",
    followup: dict[str, Any] | None = None,
    message_path: str = "",
) -> None:
    followup_key_override = ""
    if followup is not None and str(followup.get("followup_type", "")).strip() != "queued_feedback_resume":
        followup_key_override = str(followup.get("followup_key", "")).strip()
    schedule_followup(
        config,
        task_id=task_id,
        spec=spec,
        reason=PROTOCOL_SELF_CHECK_REPAIR_REASON,
        delay_seconds=DEFAULT_PROTOCOL_REPAIR_DELAY_SECONDS,
        interval_seconds=DEFAULT_PROTOCOL_REPAIR_INTERVAL_SECONDS,
        min_idle_seconds=DEFAULT_PROTOCOL_REPAIR_MIN_IDLE_SECONDS,
        followup_key_override=followup_key_override,
        followup_type=PROTOCOL_SELF_CHECK_REPAIR_FOLLOWUP_TYPE,
        last_signal=observed_signal,
    )
    target_key = followup_key_override or followup_key_for(spec)
    target_path = followup_path(config, target_key)
    payload = read_json(target_path, {})
    if not isinstance(payload, dict) or not payload:
        return
    payload["followup_type"] = PROTOCOL_SELF_CHECK_REPAIR_FOLLOWUP_TYPE
    payload["protocol_issue"] = str(issue_summary or "missing_protocol_footer").strip() or "missing_protocol_footer"
    payload["protocol_footer"] = protocol_footer_snapshot(protocol_footer)
    payload["protocol_observed_signal"] = str(observed_signal or "").strip()
    payload["updated_at"] = utc_now()
    atomic_write_json(target_path, payload)
    merge_task_state(
        config,
        task_id,
        followup_status="scheduled",
        followup_last_signal=str(observed_signal or "").strip(),
        followup_last_action=f"scheduled:{PROTOCOL_SELF_CHECK_REPAIR_REASON}",
        followup_stopped_at="",
        followup_last_message_path=message_path or str(task_last_message_path(config, task_id)),
    )


def followup_queue_hygiene_lines(*, compact: bool) -> list[str]:
    if compact:
        return [
            "阶段性收口后，再运行 `codex-taskboard status --json` 做一次保守的 queue hygiene；只清理明确淘汰、orphan、异常卡住或与当前方向无关的项。",
        ]
    return [
        "在完成证据吸收、proposal 更新和下一方向判断之后，请运行 `codex-taskboard status --json` 做一次保守的 queue hygiene 检查。",
        "只清理已被新 proposal/新链路明确淘汰、明显 orphan、明显异常卡住、或与当前方向无关的 queued / watcher / followup 项；若无法确认是否该删，请先保留并在 proposal 或 closeout 中记录。",
    ]


def combine_default_and_custom_instruction(default_instruction: str, custom_instruction: str) -> str:
    custom = str(custom_instruction or "").strip()
    if not custom:
        return default_instruction
    return f"{default_instruction}\n附加任务指令：\n{custom}"


def normalize_legacy_task_root(candidate: Path) -> Path:
    resolved = candidate.expanduser().resolve()
    if resolved.name != "tasks" and (resolved / "tasks").exists():
        return (resolved / "tasks").resolve()
    return resolved


def resolve_legacy_root_args(raw_roots: list[str] | None) -> tuple[Path, ...]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for raw_root in raw_roots or []:
        candidate = normalize_legacy_task_root(Path(raw_root))
        if not candidate.exists():
            raise ValueError(f"Legacy root does not exist: {candidate}")
        if candidate in seen:
            continue
        seen.add(candidate)
        resolved.append(candidate)
    return tuple(sorted(resolved, key=str))


def discover_legacy_task_roots(
    global_app_home: Path,
    *,
    codex_home: Path | None = None,
    home_root: Path | None = None,
) -> tuple[Path, ...]:
    legacy_candidates: set[Path] = set()
    search_root = home_root or Path("/home")
    for pattern in LEGACY_TASK_ROOT_GLOBS:
        for candidate in search_root.glob(pattern):
            legacy_candidates.add(normalize_legacy_task_root(candidate))
    if codex_home is not None:
        legacy_candidates.add(normalize_legacy_task_root(codex_home / "tmux-task-codex-wakeup" / "tasks"))
    for raw_item in str(os.environ.get(LEGACY_TASK_ROOT_ENV, "") or "").split(os.pathsep):
        item = raw_item.strip()
        if not item:
            continue
        legacy_candidates.add(normalize_legacy_task_root(Path(item)))
    global_tasks_root = (global_app_home / "tasks").resolve()
    return tuple(sorted([path for path in legacy_candidates if path.exists() and path != global_tasks_root], key=str))


def legacy_reads_enabled() -> bool:
    raw = str(os.environ.get(LEGACY_READS_ENV, "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def scheduler_lock_path(config: AppConfig) -> Path:
    return config.locks_root / "scheduler.lock"


def feedback_lock_path(config: AppConfig) -> Path:
    return config.locks_root / "feedback.lock"


def session_lock_path(config: AppConfig, session_id: str) -> Path:
    return config.locks_root / f"{session_lock_name(session_id)}.lock"


def followup_lock_path(config: AppConfig, followup_key: str) -> Path:
    safe_key = bounded_lock_name(str(followup_key).strip(), fallback_prefix="followup")
    return config.locks_root / f"{safe_key}.lock"


def active_feedback_runtime_lock_path(config: AppConfig) -> Path:
    return config.locks_root / "active-feedback-runtime.lock"


def run_with_file_lock(lock_path: Path, callback: Any) -> Any:
    ensure_dir(lock_path.parent)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            return callback()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_with_followup_lock(config: AppConfig, followup_key: str, callback: Any) -> Any:
    return run_with_file_lock(followup_lock_path(config, followup_key), callback)


def run_with_scheduler_lock(config: AppConfig, callback: Any) -> Any:
    return run_with_file_lock(scheduler_lock_path(config), callback)


def run_with_feedback_lock(config: AppConfig, callback: Any) -> Any:
    return run_with_file_lock(feedback_lock_path(config), callback)


def run_with_active_feedback_runtime_lock(config: AppConfig, callback: Any) -> Any:
    return run_with_file_lock(active_feedback_runtime_lock_path(config), callback)


def atomic_write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    ensure_dir(path.parent)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def append_log(path: Path, message: str) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{utc_now()}] {message}\n")


def followup_log_path(config: AppConfig) -> Path:
    return config.followups_root / "followup.log"


def current_followup_wait_snapshot(
    config: AppConfig,
    session_id: str,
) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return {
            "effective_wait_state": "",
            "running_task_count": 0,
            "awaiting_feedback_task_count": 0,
        }
    states = iter_all_task_states(config)
    session_state = continuous_research_session_state(config, normalized_session_id)
    live_snapshot = continuous_session_live_task_snapshot(config, normalized_session_id, states=states)
    anchor_spec = latest_continuous_research_anchor_spec(config, normalized_session_id, states=states) or {}
    next_action_hint = session_continuation_hint(
        config,
        normalized_session_id,
        spec=anchor_spec or session_state,
        states=states,
    )
    return {
        "effective_wait_state": effective_wait_state_for_session(
            session_state,
            live_snapshot,
            next_action_hint=next_action_hint,
        ),
        "running_task_count": int(live_snapshot.get("running_task_count", 0) or 0),
        "awaiting_feedback_task_count": int(live_snapshot.get("awaiting_feedback_task_count", 0) or 0),
    }


def append_followup_event_log(
    config: AppConfig,
    *,
    event: str,
    reason: str = "",
    followup: dict[str, Any] | None = None,
    session_id: str = "",
    task_id: str = "",
    detail: str = "",
) -> None:
    payload = followup if isinstance(followup, dict) else {}
    normalized_session_id = str(session_id or payload.get("codex_session_id", "")).strip()
    wait_snapshot = current_followup_wait_snapshot(config, normalized_session_id)
    log_payload: dict[str, Any] = {
        "kind": "followup_event",
        "event": str(event or "").strip(),
        "reason": str(reason or payload.get("reason", "")).strip(),
        "followup_key": str(payload.get("followup_key", "")).strip(),
        "followup_type": str(payload.get("followup_type", "")).strip(),
        "task_id": str(task_id or payload.get("task_id", "")).strip(),
        "session_id": normalized_session_id,
        "last_signal": str(payload.get("last_signal", "")).strip(),
        "effective_wait_state": str(wait_snapshot.get("effective_wait_state", "")).strip(),
        "running_task_count": int(wait_snapshot.get("running_task_count", 0) or 0),
        "awaiting_feedback_task_count": int(wait_snapshot.get("awaiting_feedback_task_count", 0) or 0),
    }
    try:
        check_after_ts = float(payload.get("check_after_ts", 0) or 0) if payload else 0.0
    except (TypeError, ValueError):
        check_after_ts = 0.0
    if check_after_ts > 0:
        log_payload["check_after_ts"] = check_after_ts
    if detail:
        log_payload["detail"] = str(detail)
    append_log(followup_log_path(config), json.dumps(log_payload, ensure_ascii=False, sort_keys=True))


def automation_state_hooks() -> AutomationStateHooks:
    return AutomationStateHooks(
        read_json=read_json,
        atomic_write_json=atomic_write_json,
        normalize_timestamp_fields=normalize_timestamp_fields,
        parse_boolish=parse_boolish,
        current_thread_info=current_thread_info,
        utc_now=utc_now,
        canonicalize_taskboard_signal=canonicalize_taskboard_signal,
        parse_timestamp_to_unix=parse_timestamp_to_unix,
        format_unix_timestamp=format_unix_timestamp,
        retry_after_seconds_from_target=retry_after_seconds_from_target,
        continuous_research_mode_filename=CONTINUOUS_RESEARCH_MODE_FILENAME,
        human_guidance_mode_filename=HUMAN_GUIDANCE_MODE_FILENAME,
        default_human_guidance_lease_seconds=DEFAULT_HUMAN_GUIDANCE_LEASE_SECONDS,
        continuous_research_idle_loop_threshold=CONTINUOUS_RESEARCH_IDLE_LOOP_THRESHOLD,
        continuous_research_override_signals=CONTINUOUS_RESEARCH_OVERRIDE_SIGNALS,
        parked_idle_signal=PARKED_IDLE_SIGNAL,
        parked_idle_signals=PARKED_IDLE_SIGNALS,
    )


def continuous_research_mode_path(config: AppConfig) -> Path:
    return continuous_research_mode_path_impl(config, hooks=automation_state_hooks())


def normalize_continuous_research_mode_payload(payload: Any) -> dict[str, Any]:
    return normalize_continuous_research_mode_payload_impl(payload, hooks=automation_state_hooks())


def load_continuous_research_mode(config: AppConfig, *, codex_session_id: str = "") -> dict[str, Any]:
    return load_continuous_research_mode_impl(config, hooks=automation_state_hooks(), codex_session_id=codex_session_id)


def continuous_research_session_state(config: AppConfig, codex_session_id: str) -> dict[str, Any]:
    return continuous_research_session_state_impl(config, codex_session_id, hooks=automation_state_hooks())


def resolve_continuous_research_target_session_id(
    config: AppConfig,
    *,
    raw_session_id: Any = "",
    environ: Any | None = None,
) -> tuple[str, str]:
    return resolve_continuous_research_target_session_id_impl(
        config,
        hooks=automation_state_hooks(),
        raw_session_id=raw_session_id,
        environ=environ,
    )


def write_continuous_research_mode_payload(
    config: AppConfig,
    payload: dict[str, Any],
    *,
    verify_session_id: str = "",
    verify_enabled: bool | None = None,
    verify_session_present: bool | None = None,
    verify_default_session_id: str | None = None,
) -> dict[str, Any]:
    return write_continuous_research_mode_payload_impl(
        config,
        payload,
        hooks=automation_state_hooks(),
        verify_session_id=verify_session_id,
        verify_enabled=verify_enabled,
        verify_session_present=verify_session_present,
        verify_default_session_id=verify_default_session_id,
    )


def update_continuous_research_session_state(
    config: AppConfig,
    *,
    codex_session_id: str,
    updated_by: str = "followup",
    source: str = "",
    **updates: Any,
) -> dict[str, Any]:
    return update_continuous_research_session_state_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source,
        **updates,
    )


def clear_continuous_research_session_waiting_state(
    config: AppConfig,
    *,
    codex_session_id: str,
    evidence_token: str = "",
    last_signal: str = "",
    stable_idle_repeat_count: int = 0,
    updated_by: str = "followup",
    source: str = "",
    **updates: Any,
) -> dict[str, Any]:
    return clear_continuous_research_session_waiting_state_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        evidence_token=evidence_token,
        last_signal=last_signal,
        stable_idle_repeat_count=stable_idle_repeat_count,
        updated_by=updated_by,
        source=source,
        **updates,
    )


def park_continuous_research_session(
    config: AppConfig,
    *,
    codex_session_id: str,
    waiting_state: str,
    waiting_reason: str,
    evidence_token: str,
    last_signal: str,
    stable_idle_repeat_count: int = CONTINUOUS_RESEARCH_IDLE_LOOP_THRESHOLD,
    updated_by: str = "followup",
    source: str = "",
    **updates: Any,
) -> dict[str, Any]:
    return park_continuous_research_session_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        waiting_state=waiting_state,
        waiting_reason=waiting_reason,
        evidence_token=evidence_token,
        last_signal=last_signal,
        stable_idle_repeat_count=stable_idle_repeat_count,
        updated_by=updated_by,
        source=source,
        **updates,
    )


def next_parked_idle_repeat_count(
    session_state: dict[str, Any],
    *,
    evidence_token: str,
) -> int:
    return next_parked_idle_repeat_count_impl(
        session_state,
        hooks=automation_state_hooks(),
        evidence_token=evidence_token,
    )


def continuous_research_mode_enabled(config: AppConfig, *, codex_session_id: str = "") -> bool:
    return continuous_research_mode_enabled_impl(config, hooks=automation_state_hooks(), codex_session_id=codex_session_id)


def set_continuous_research_mode(
    config: AppConfig,
    *,
    enabled: bool,
    codex_session_id: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return set_continuous_research_mode_impl(
        config,
        hooks=automation_state_hooks(),
        enabled=enabled,
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source,
    )


def toggle_continuous_research_mode(
    config: AppConfig,
    *,
    codex_session_id: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return toggle_continuous_research_mode_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source,
    )


def bind_continuous_research_mode_session(
    config: AppConfig,
    *,
    codex_session_id: str,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return bind_continuous_research_mode_session_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source,
    )


def clear_continuous_research_mode_session(
    config: AppConfig,
    *,
    codex_session_id: str,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return clear_continuous_research_mode_session_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source,
    )


def clear_all_continuous_research_mode(
    config: AppConfig,
    *,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return clear_all_continuous_research_mode_impl(
        config,
        hooks=automation_state_hooks(),
        updated_by=updated_by,
        source=source,
    )


def should_override_stop_signal_with_continuous_research(
    config: AppConfig,
    signal_value: str,
    *,
    codex_session_id: str = "",
) -> bool:
    return should_override_stop_signal_with_continuous_research_impl(
        config,
        signal_value,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
    )


def continuous_research_mode_label(config: AppConfig) -> str:
    return continuous_research_mode_label_impl(config, hooks=automation_state_hooks())


def continuous_research_enabled_session_ids(config: AppConfig) -> list[str]:
    return continuous_research_enabled_session_ids_impl(config, hooks=automation_state_hooks())


def automation_mode(config: AppConfig, *, codex_session_id: str = "") -> dict[str, Any]:
    return automation_mode_impl(config, hooks=automation_state_hooks(), codex_session_id=codex_session_id)


def automation_mode_label(config: AppConfig, *, codex_session_id: str = "") -> str:
    return automation_mode_label_impl(config, hooks=automation_state_hooks(), codex_session_id=codex_session_id)


def automation_mode_is_managed(config: AppConfig, *, codex_session_id: str = "") -> bool:
    return automation_mode_is_managed_impl(config, hooks=automation_state_hooks(), codex_session_id=codex_session_id)


def set_automation_mode(
    config: AppConfig,
    *,
    mode: str,
    codex_session_id: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return set_automation_mode_impl(
        config,
        hooks=automation_state_hooks(),
        mode=mode,
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source,
    )


def toggle_automation_mode(
    config: AppConfig,
    *,
    codex_session_id: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return toggle_automation_mode_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source,
    )


def human_guidance_mode_path(config: AppConfig) -> Path:
    return human_guidance_mode_path_impl(config, hooks=automation_state_hooks())


def normalize_human_guidance_mode_payload(payload: Any) -> dict[str, Any]:
    return normalize_human_guidance_mode_payload_impl(payload, hooks=automation_state_hooks())


def load_human_guidance_mode(config: AppConfig, *, codex_session_id: str = "") -> dict[str, Any]:
    return load_human_guidance_mode_impl(config, hooks=automation_state_hooks(), codex_session_id=codex_session_id)


def resolve_human_guidance_target_session_id(
    config: AppConfig,
    *,
    raw_session_id: Any = "",
    environ: Any | None = None,
) -> tuple[str, str]:
    return resolve_human_guidance_target_session_id_impl(
        config,
        hooks=automation_state_hooks(),
        raw_session_id=raw_session_id,
        environ=environ,
    )


def write_human_guidance_mode_payload(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    return write_human_guidance_mode_payload_impl(config, payload, hooks=automation_state_hooks())


def set_human_guidance_mode(
    config: AppConfig,
    *,
    active: bool,
    codex_session_id: str = "",
    lease_seconds: int = DEFAULT_WAITING_ON_ASYNC_INTERVAL_SECONDS,
    reason: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return set_human_guidance_mode_impl(
        config,
        hooks=automation_state_hooks(),
        active=active,
        codex_session_id=codex_session_id,
        lease_seconds=lease_seconds,
        reason=reason,
        updated_by=updated_by,
        source=source,
    )


def toggle_human_guidance_mode(
    config: AppConfig,
    *,
    codex_session_id: str = "",
    lease_seconds: int = DEFAULT_WAITING_ON_ASYNC_INTERVAL_SECONDS,
    reason: str = "",
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return toggle_human_guidance_mode_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        lease_seconds=lease_seconds,
        reason=reason,
        updated_by=updated_by,
        source=source,
    )


def bind_human_guidance_mode_session(
    config: AppConfig,
    *,
    codex_session_id: str,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return bind_human_guidance_mode_session_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source,
    )


def clear_human_guidance_mode_session(
    config: AppConfig,
    *,
    codex_session_id: str,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return clear_human_guidance_mode_session_impl(
        config,
        hooks=automation_state_hooks(),
        codex_session_id=codex_session_id,
        updated_by=updated_by,
        source=source,
    )


def clear_all_human_guidance_mode(
    config: AppConfig,
    *,
    updated_by: str = "cli",
    source: str = "",
) -> dict[str, Any]:
    return clear_all_human_guidance_mode_impl(
        config,
        hooks=automation_state_hooks(),
        updated_by=updated_by,
        source=source,
    )


def human_guidance_mode_active(config: AppConfig, *, codex_session_id: str = "") -> bool:
    return automation_mode_is_managed(config, codex_session_id=codex_session_id)


def human_guidance_retry_after_seconds(config: AppConfig, *, codex_session_id: str = "") -> int:
    del codex_session_id
    return DEFAULT_WAITING_ON_ASYNC_INTERVAL_SECONDS


def human_guidance_mode_label(config: AppConfig) -> str:
    return "managed" if automation_mode_label(config) == "managed" else "off"


def human_guidance_active_session_ids(config: AppConfig) -> list[str]:
    payload = load_continuous_research_mode(config)
    sessions = payload.get("sessions", {}) if isinstance(payload.get("sessions", {}), dict) else {}
    return sorted(
        session_id
        for session_id, state in sessions.items()
        if isinstance(state, dict) and str(state.get("mode", "")).strip() == "managed"
    )

def normalize_task_id(raw_value: str) -> str:
    value = raw_value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")


def session_migrations_path(config: AppConfig) -> Path:
    return config.app_home / SESSION_MIGRATIONS_FILENAME


def active_feedback_runtime_path(config: AppConfig) -> Path:
    return config.app_home / ACTIVE_FEEDBACK_RUNTIME_FILENAME


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_session_migrations_payload(payload: Any) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    redirects_raw = raw.get("redirects", {})
    redirects: dict[str, dict[str, Any]] = {}
    if isinstance(redirects_raw, dict):
        for raw_from_session_id, raw_entry in redirects_raw.items():
            from_session_id = str(raw_from_session_id or "").strip()
            entry = raw_entry if isinstance(raw_entry, dict) else {}
            to_session_id = str(entry.get("to_session_id", "")).strip()
            if not from_session_id or not to_session_id or from_session_id == to_session_id:
                continue
            state = str(entry.get("state", "completed")).strip().lower() or "completed"
            if state not in {"migrating", "completed"}:
                state = "completed"
            buffered_runtime_entries: list[dict[str, Any]] = []
            for raw_buffered in entry.get("buffered_runtime_entries", []):
                if not isinstance(raw_buffered, dict):
                    continue
                task_ids: list[str] = []
                for raw_task_id in raw_buffered.get("task_ids", []):
                    normalized_task_id = normalize_task_id(str(raw_task_id or "").strip())
                    if normalized_task_id:
                        task_ids.append(normalized_task_id)
                buffered_runtime_entries.append(
                    {
                        "operation_id": str(raw_buffered.get("operation_id", "")).strip(),
                        "requested_session_id": str(raw_buffered.get("requested_session_id", from_session_id)).strip(),
                        "session_id": str(raw_buffered.get("session_id", from_session_id)).strip(),
                        "redirected_session_id": str(raw_buffered.get("redirected_session_id", to_session_id)).strip(),
                        "source_kind": str(raw_buffered.get("source_kind", "")).strip(),
                        "source_key": str(raw_buffered.get("source_key", "")).strip(),
                        "task_id": normalize_task_id(str(raw_buffered.get("task_id", "")).strip()),
                        "task_ids": sorted(set(task_ids)),
                        "followup_key": str(raw_buffered.get("followup_key", "")).strip(),
                        "captured_at": str(raw_buffered.get("captured_at", "")),
                        "reason": str(raw_buffered.get("reason", "")).strip(),
                    }
                )
            redirects[from_session_id] = {
                "to_session_id": to_session_id,
                "state": state,
                "created_at": str(entry.get("created_at", "")),
                "updated_at": str(entry.get("updated_at", "")),
                "updated_by": str(entry.get("updated_by", "")),
                "source": str(entry.get("source", "")),
                "buffered_runtime_entries": buffered_runtime_entries,
                "last_summary": entry.get("last_summary", {}) if isinstance(entry.get("last_summary", {}), dict) else {},
            }
    return {
        "version": coerce_int(raw.get("version", 1), default=1),
        "redirects": redirects,
    }


def load_session_migrations(config: AppConfig) -> dict[str, Any]:
    return normalize_session_migrations_payload(read_json(session_migrations_path(config), {}))


def write_session_migrations(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_session_migrations_payload(payload)
    atomic_write_json(session_migrations_path(config), normalized)
    return normalized


def session_migration_entry(config: AppConfig, session_id: str) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return {}
    payload = load_session_migrations(config)
    entry = payload.get("redirects", {}).get(normalized_session_id, {})
    return dict(entry) if isinstance(entry, dict) else {}


def session_redirect_target(
    config: AppConfig,
    session_id: str,
    *,
    include_migrating: bool = False,
) -> str:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return ""
    payload = load_session_migrations(config)
    redirects = payload.get("redirects", {}) if isinstance(payload.get("redirects", {}), dict) else {}
    allowed_states = {"completed"}
    if include_migrating:
        allowed_states.add("migrating")
    current = normalized_session_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        entry = redirects.get(current, {})
        if not isinstance(entry, dict):
            break
        if str(entry.get("state", "")).strip() not in allowed_states:
            break
        target = str(entry.get("to_session_id", "")).strip()
        if not target:
            break
        current = target
    return current


def apply_session_redirect_to_spec(
    config: AppConfig,
    spec: dict[str, Any],
    *,
    include_migrating: bool = True,
) -> dict[str, Any]:
    if not isinstance(spec, dict) or not spec:
        return spec
    original_session_id = str(spec.get("codex_session_id", "")).strip()
    if not original_session_id:
        return spec
    redirected_session_id = session_redirect_target(
        config,
        original_session_id,
        include_migrating=include_migrating,
    )
    if not redirected_session_id or redirected_session_id == original_session_id:
        return spec
    updated = dict(spec)
    updated["codex_session_id"] = redirected_session_id
    return updated


def normalize_active_feedback_runtime_payload(payload: Any) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    entries: list[dict[str, Any]] = []
    for raw_entry in raw.get("entries", []):
        if not isinstance(raw_entry, dict):
            continue
        operation_id = str(raw_entry.get("operation_id", "")).strip()
        session_id = str(raw_entry.get("session_id", "")).strip()
        if not operation_id or not session_id:
            continue
        pid = coerce_int(raw_entry.get("pid", 0), default=0)
        # A reboot can leave behind stale active-feedback runtime entries whose
        # PIDs no longer exist. Drop them during normalization so migration and
        # session binding logic do not keep treating dead feedback workers as
        # live runtime ownership.
        if pid > 0 and not pid_exists(pid):
            continue
        task_ids: list[str] = []
        for raw_task_id in raw_entry.get("task_ids", []):
            normalized_task_id = normalize_task_id(str(raw_task_id or "").strip())
            if normalized_task_id:
                task_ids.append(normalized_task_id)
        entries.append(
            {
                "operation_id": operation_id,
                "session_id": session_id,
                "requested_session_id": str(raw_entry.get("requested_session_id", session_id)).strip() or session_id,
                "pid": pid,
                "pgid": coerce_int(raw_entry.get("pgid", 0), default=0),
                "source_kind": str(raw_entry.get("source_kind", "")).strip(),
                "source_key": str(raw_entry.get("source_key", "")).strip(),
                "task_id": normalize_task_id(str(raw_entry.get("task_id", "")).strip()),
                "task_ids": sorted(set(task_ids)),
                "followup_key": str(raw_entry.get("followup_key", "")).strip(),
                "started_at": str(raw_entry.get("started_at", "")),
            }
        )
    return normalize_timestamp_fields(
        {
            "version": coerce_int(raw.get("version", 1), default=1),
            "entries": entries,
        }
    )


def load_active_feedback_runtime(config: AppConfig) -> dict[str, Any]:
    return normalize_active_feedback_runtime_payload(read_json(active_feedback_runtime_path(config), {}))


def write_active_feedback_runtime(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_active_feedback_runtime_payload(payload)
    atomic_write_json(active_feedback_runtime_path(config), normalized)
    return normalized


def register_active_feedback_runtime(
    config: AppConfig,
    *,
    operation_id: str,
    session_id: str,
    requested_session_id: str,
    pid: int,
    pgid: int,
    source_kind: str,
    source_key: str,
    task_id: str = "",
    task_ids: list[str] | None = None,
    followup_key: str = "",
) -> dict[str, Any]:
    normalized_task_ids = sorted(
        {
            normalize_task_id(str(item or "").strip())
            for item in ([task_id] + list(task_ids or []))
            if normalize_task_id(str(item or "").strip())
        }
    )
    entry = {
        "operation_id": str(operation_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "requested_session_id": str(requested_session_id or session_id or "").strip(),
        "pid": int(pid or 0),
        "pgid": int(pgid or 0),
        "source_kind": str(source_kind or "").strip(),
        "source_key": str(source_key or "").strip(),
        "task_id": normalized_task_ids[0] if normalized_task_ids else "",
        "task_ids": normalized_task_ids,
        "followup_key": str(followup_key or "").strip(),
        "started_at": utc_now(),
    }

    def _register() -> dict[str, Any]:
        payload = load_active_feedback_runtime(config)
        entries = [item for item in payload.get("entries", []) if str(item.get("operation_id", "")).strip() != entry["operation_id"]]
        entries.append(entry)
        write_active_feedback_runtime(config, {"version": 1, "entries": entries})
        return entry

    return run_with_active_feedback_runtime_lock(config, _register)


def clear_active_feedback_runtime(config: AppConfig, operation_id: str) -> None:
    normalized_operation_id = str(operation_id or "").strip()
    if not normalized_operation_id:
        return

    def _clear() -> None:
        payload = load_active_feedback_runtime(config)
        entries = [item for item in payload.get("entries", []) if str(item.get("operation_id", "")).strip() != normalized_operation_id]
        write_active_feedback_runtime(config, {"version": 1, "entries": entries})

    run_with_active_feedback_runtime_lock(config, _clear)


def active_feedback_entries_for_session(config: AppConfig, session_id: str) -> list[dict[str, Any]]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return []
    payload = load_active_feedback_runtime(config)
    return [
        dict(item)
        for item in payload.get("entries", [])
        if isinstance(item, dict) and str(item.get("session_id", "")).strip() == normalized_session_id
    ]


def merge_buffered_runtime_entries(
    existing_entries: list[dict[str, Any]],
    incoming_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for item in [*existing_entries, *incoming_entries]:
        if not isinstance(item, dict):
            continue
        operation_id = str(item.get("operation_id", "")).strip()
        if not operation_id:
            passthrough.append(item)
            continue
        merged[operation_id] = item
    return [*passthrough, *[merged[key] for key in sorted(merged)]]


def update_session_migration_entry(
    config: AppConfig,
    *,
    from_session_id: str,
    to_session_id: str,
    state: str,
    updated_by: str,
    source: str,
    buffered_runtime_entries: list[dict[str, Any]] | None = None,
    last_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_from_session_id = str(from_session_id or "").strip()
    normalized_to_session_id = str(to_session_id or "").strip()
    if not normalized_from_session_id or not normalized_to_session_id or normalized_from_session_id == normalized_to_session_id:
        raise ValueError("Invalid session migration pair.")
    normalized_state = str(state or "completed").strip().lower() or "completed"
    if normalized_state not in {"migrating", "completed"}:
        raise ValueError(f"Unsupported session migration state: {state}")
    payload = load_session_migrations(config)
    redirects = dict(payload.get("redirects", {}))
    existing = redirects.get(normalized_from_session_id, {})
    if not isinstance(existing, dict):
        existing = {}
    entry = {
        "to_session_id": normalized_to_session_id,
        "state": normalized_state,
        "created_at": str(existing.get("created_at", "")) or utc_now(),
        "updated_at": utc_now(),
        "updated_by": str(updated_by or "cli"),
        "source": str(source or "migrate-session"),
        "buffered_runtime_entries": merge_buffered_runtime_entries(
            existing.get("buffered_runtime_entries", []) if isinstance(existing.get("buffered_runtime_entries", []), list) else [],
            buffered_runtime_entries or [],
        ),
        "last_summary": last_summary if isinstance(last_summary, dict) else (existing.get("last_summary", {}) if isinstance(existing.get("last_summary", {}), dict) else {}),
    }
    redirects[normalized_from_session_id] = entry
    write_session_migrations(config, {"version": 1, "redirects": redirects})
    return entry


def latest_timestamp_text(left: Any, right: Any) -> str:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    left_ts = parse_timestamp_to_unix(left_text)
    right_ts = parse_timestamp_to_unix(right_text)
    if left_ts is None:
        return canonicalize_timestamp_text(right_text)
    if right_ts is None:
        return canonicalize_timestamp_text(left_text)
    return format_unix_timestamp(left_ts if left_ts >= right_ts else right_ts)


def latest_non_hidden_session_anchor_spec(config: AppConfig, session_id: str) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return {}
    candidates: list[tuple[tuple[str, str, str], dict[str, Any]]] = []
    for state in iter_all_task_states(config):
        if str(state.get("codex_session_id", "")).strip() != normalized_session_id:
            continue
        if is_hidden_status(str(state.get("status", ""))):
            continue
        candidates.append((task_state_recency_key(state), merged_spec_with_state(config, state)))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    return dict(candidates[0][1])


def session_bound_task_records(config: AppConfig, session_id: str) -> list[dict[str, Any]]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return []
    records: list[tuple[tuple[str, str, str], dict[str, Any]]] = []
    for state in iter_all_task_states(config):
        task_id = normalize_task_id(str(state.get("task_id", "")).strip())
        if not task_id:
            continue
        if is_hidden_status(str(state.get("status", ""))):
            continue
        if str(state.get("codex_session_id", "")).strip() != normalized_session_id:
            continue
        records.append(
            (
                task_state_recency_key(state),
                {
                    "task_id": task_id,
                    "state": dict(state),
                    "spec": load_task_spec(config, task_id),
                    "task_root": str(task_root(config, task_id)),
                },
            )
        )
    records.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in records]


def migrate_task_session_bindings(
    config: AppConfig,
    *,
    from_session_id: str,
    to_session_id: str,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for record in session_bound_task_records(config, from_session_id):
        task_id = str(record.get("task_id", "")).strip()
        if not task_id:
            continue
        spec = dict(record.get("spec", {}))
        state = dict(record.get("state", {}))
        changed_spec = bool(spec) and str(spec.get("codex_session_id", "")).strip() == from_session_id
        changed_state = bool(state) and str(state.get("codex_session_id", "")).strip() == from_session_id
        if changed_spec:
            spec["codex_session_id"] = to_session_id
        if changed_state:
            state["codex_session_id"] = to_session_id
        if str(state.get("resumed_session_id", "")).strip() == from_session_id:
            state["resumed_session_id"] = to_session_id
            changed_state = True
        if not dry_run:
            if changed_spec:
                write_task_spec(config, task_id, spec)
            if changed_state:
                write_task_state(config, task_id, state)
        summaries.append(
            {
                "task_id": task_id,
                "task_root": str(record.get("task_root", "")),
                "changed_spec": changed_spec,
                "changed_state": changed_state,
                "status": str(state.get("status", record.get("state", {}).get("status", ""))).strip(),
            }
        )
    return summaries


def planned_followup_session_binding(config: AppConfig, followup: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    followup_type = str(followup.get("followup_type", "")).strip()
    if followup_type == CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE:
        session_id = str(followup.get("codex_session_id", "")).strip()
        updated_spec = latest_continuous_research_anchor_spec(config, session_id) or current_followup_resume_spec(config, followup)
    else:
        updated_spec = current_followup_resume_spec(config, followup)
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
    elif followup_type == CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE:
        new_key = continuous_session_followup_key_for(str(updated_spec.get("codex_session_id", followup.get("codex_session_id", ""))).strip())
    else:
        new_key = followup_key_for(updated_spec)
    updated_followup["followup_key"] = new_key
    return updated_followup, old_key, new_key


def migrate_followup_session_bindings(
    config: AppConfig,
    *,
    from_session_id: str,
    to_session_id: str,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for followup in load_followups(config):
        if str(followup.get("codex_session_id", "")).strip() != from_session_id:
            continue
        original_key = str(followup.get("followup_key", "")).strip()
        if not original_key:
            continue
        updated_followup = dict(followup)
        updated_followup["codex_session_id"] = to_session_id
        planned_followup, _old_key, planned_key = planned_followup_session_binding(config, updated_followup)
        merge_target_exists = planned_key != original_key and followup_path(config, planned_key).exists()
        if not dry_run:
            atomic_write_json(followup_path(config, original_key), updated_followup)
            rebound_followup, changed, merged_existing = rebind_followup_to_current_task(config, updated_followup)
            summaries.append(
                {
                    "followup_key": original_key,
                    "new_followup_key": str(rebound_followup.get("followup_key", "")).strip(),
                    "followup_type": str(followup.get("followup_type", "")).strip(),
                    "task_ids": followup_task_ids(rebound_followup),
                    "changed": changed,
                    "merged_existing": merged_existing,
                }
            )
            continue
        summaries.append(
            {
                "followup_key": original_key,
                "new_followup_key": planned_key,
                "followup_type": str(followup.get("followup_type", "")).strip(),
                "task_ids": followup_task_ids(planned_followup),
                "changed": planned_key != original_key or planned_followup != followup,
                "merged_existing": merge_target_exists,
            }
        )
    return summaries


def merge_continuous_research_session_state(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_state = left if isinstance(left, dict) else {}
    right_state = right if isinstance(right, dict) else {}
    left_waiting_since = str(left_state.get("waiting_since", "")).strip()
    right_waiting_since = str(right_state.get("waiting_since", "")).strip()
    use_right_waiting = (
        parse_timestamp_to_unix(right_waiting_since) or 0.0
    ) >= (
        parse_timestamp_to_unix(left_waiting_since) or 0.0
    )
    return {
        "enabled": bool(left_state.get("enabled", False) or right_state.get("enabled", False)),
        "updated_at": latest_timestamp_text(left_state.get("updated_at", ""), right_state.get("updated_at", "")),
        "updated_by": str(right_state.get("updated_by", "")).strip() or str(left_state.get("updated_by", "")).strip(),
        "source": str(right_state.get("source", "")).strip() or str(left_state.get("source", "")).strip(),
        "waiting_state": (
            str(right_state.get("waiting_state", "")).strip()
            if use_right_waiting
            else str(left_state.get("waiting_state", "")).strip()
        ),
        "waiting_reason": (
            str(right_state.get("waiting_reason", "")).strip()
            if use_right_waiting
            else str(left_state.get("waiting_reason", "")).strip()
        ),
        "waiting_since": right_waiting_since if use_right_waiting else left_waiting_since,
        "waiting_evidence_token": (
            str(right_state.get("waiting_evidence_token", "")).strip()
            if use_right_waiting
            else str(left_state.get("waiting_evidence_token", "")).strip()
        ),
        "last_evidence_token": str(right_state.get("last_evidence_token", "")).strip() or str(left_state.get("last_evidence_token", "")).strip(),
        "stable_idle_repeat_count": max(
            int(left_state.get("stable_idle_repeat_count", 0) or 0),
            int(right_state.get("stable_idle_repeat_count", 0) or 0),
        ),
        "last_signal": str(right_state.get("last_signal", "")).strip() or str(left_state.get("last_signal", "")).strip(),
    }


def migrate_continuous_research_session_binding(
    config: AppConfig,
    *,
    from_session_id: str,
    to_session_id: str,
    updated_by: str,
    source: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    payload = normalize_continuous_research_mode_payload(read_json(continuous_research_mode_path(config), {}))
    sessions = dict(payload.get("sessions", {}))
    from_state = dict(sessions.get(from_session_id, {}))
    to_state = dict(sessions.get(to_session_id, {}))
    default_session_id = str(payload.get("default_codex_session_id", "")).strip()
    changed = bool(from_state) or default_session_id == from_session_id
    merged_state = merge_continuous_research_session_state(to_state, from_state) if changed else to_state
    next_sessions = dict(sessions)
    if from_session_id in next_sessions:
        next_sessions.pop(from_session_id, None)
    if merged_state:
        merged_state["updated_at"] = utc_now()
        merged_state["updated_by"] = str(updated_by or "cli")
        merged_state["source"] = str(source or "migrate-session:continuous")
        next_sessions[to_session_id] = merged_state
    next_default_session_id = to_session_id if default_session_id == from_session_id else default_session_id
    if not dry_run and changed:
        write_continuous_research_mode_payload(
            config,
            {
                "version": 2,
                "enabled": False,
                "default_codex_session_id": next_default_session_id,
                "updated_at": utc_now(),
                "updated_by": str(updated_by or "cli"),
                "source": str(source or "migrate-session:continuous"),
                "sessions": next_sessions,
            },
        )
    return {
        "changed": changed,
        "default_session_id": next_default_session_id,
        "enabled": bool(merged_state.get("enabled", False)),
    }


def merge_human_guidance_session_state(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_state = left if isinstance(left, dict) else {}
    right_state = right if isinstance(right, dict) else {}
    left_paused_until = str(left_state.get("paused_until", "")).strip()
    right_paused_until = str(right_state.get("paused_until", "")).strip()
    use_right_until = (
        parse_timestamp_to_unix(right_paused_until) or 0.0
    ) >= (
        parse_timestamp_to_unix(left_paused_until) or 0.0
    )
    return {
        "paused": bool(left_state.get("paused", False) or right_state.get("paused", False)),
        "paused_until": right_paused_until if use_right_until else left_paused_until,
        "reason": str(right_state.get("reason", "")).strip() or str(left_state.get("reason", "")).strip(),
        "updated_at": latest_timestamp_text(left_state.get("updated_at", ""), right_state.get("updated_at", "")),
        "updated_by": str(right_state.get("updated_by", "")).strip() or str(left_state.get("updated_by", "")).strip(),
        "source": str(right_state.get("source", "")).strip() or str(left_state.get("source", "")).strip(),
    }


def migrate_human_guidance_session_binding(
    config: AppConfig,
    *,
    from_session_id: str,
    to_session_id: str,
    updated_by: str,
    source: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    payload = normalize_human_guidance_mode_payload(read_json(human_guidance_mode_path(config), {}))
    sessions = dict(payload.get("sessions", {}))
    from_state = dict(sessions.get(from_session_id, {}))
    to_state = dict(sessions.get(to_session_id, {}))
    default_session_id = str(payload.get("default_codex_session_id", "")).strip()
    changed = bool(from_state) or default_session_id == from_session_id
    merged_state = merge_human_guidance_session_state(to_state, from_state) if changed else to_state
    next_sessions = dict(sessions)
    if from_session_id in next_sessions:
        next_sessions.pop(from_session_id, None)
    if merged_state:
        merged_state["updated_at"] = utc_now()
        merged_state["updated_by"] = str(updated_by or "cli")
        merged_state["source"] = str(source or "migrate-session:human-guidance")
        next_sessions[to_session_id] = merged_state
    next_default_session_id = to_session_id if default_session_id == from_session_id else default_session_id
    if not dry_run and changed:
        write_human_guidance_mode_payload(
            config,
            {
                "version": 1,
                "default_codex_session_id": next_default_session_id,
                "updated_at": utc_now(),
                "updated_by": str(updated_by or "cli"),
                "source": str(source or "migrate-session:human-guidance"),
                "sessions": next_sessions,
            },
        )
    return {
        "changed": changed,
        "default_session_id": next_default_session_id,
        "active": bool(normalize_human_guidance_mode_payload({"sessions": {to_session_id: merged_state}}).get("active_sessions", [])),
    }


def buffered_runtime_entries_for_migration(
    runtime_entries: list[dict[str, Any]],
    *,
    from_session_id: str,
    to_session_id: str,
    reason: str,
) -> list[dict[str, Any]]:
    captured_at = utc_now()
    buffered: list[dict[str, Any]] = []
    for entry in runtime_entries:
        task_ids = [
            normalize_task_id(str(item or "").strip())
            for item in entry.get("task_ids", [])
            if normalize_task_id(str(item or "").strip())
        ]
        buffered.append(
            {
                "operation_id": str(entry.get("operation_id", "")).strip(),
                "requested_session_id": str(entry.get("requested_session_id", from_session_id)).strip() or from_session_id,
                "session_id": str(entry.get("session_id", from_session_id)).strip() or from_session_id,
                "redirected_session_id": to_session_id,
                "source_kind": str(entry.get("source_kind", "")).strip(),
                "source_key": str(entry.get("source_key", "")).strip(),
                "task_id": normalize_task_id(str(entry.get("task_id", "")).strip()),
                "task_ids": sorted(set(task_ids)),
                "followup_key": str(entry.get("followup_key", "")).strip(),
                "captured_at": captured_at,
                "reason": reason,
            }
        )
    return buffered


def interrupt_active_feedback_for_session(
    config: AppConfig,
    *,
    session_id: str,
    to_session_id: str,
    interrupt_grace_seconds: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runtime_entries = active_feedback_entries_for_session(config, session_id)
    if not runtime_entries:
        return [], []
    for entry in runtime_entries:
        signal_process_group(int(entry.get("pgid", 0) or 0), int(entry.get("pid", 0) or 0), signal.SIGTERM)
    grace_seconds = max(0, int(interrupt_grace_seconds or 0))
    if grace_seconds > 0:
        time.sleep(grace_seconds)
    for entry in runtime_entries:
        pid = int(entry.get("pid", 0) or 0)
        if pid > 0 and pid_exists(pid):
            signal_process_group(int(entry.get("pgid", 0) or 0), pid, signal.SIGKILL)
    return runtime_entries, buffered_runtime_entries_for_migration(
        runtime_entries,
        from_session_id=session_id,
        to_session_id=to_session_id,
        reason="session_migration_interrupt",
    )


def apply_buffered_session_cutover_state(
    config: AppConfig,
    *,
    buffered_runtime_entries: list[dict[str, Any]],
    to_session_id: str,
    followup_key_map: dict[str, str],
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for entry in buffered_runtime_entries:
        source_kind = str(entry.get("source_kind", "")).strip()
        old_followup_key = str(entry.get("followup_key", "")).strip()
        current_followup_key = followup_key_map.get(old_followup_key, old_followup_key)
        task_ids = [
            normalize_task_id(str(item or "").strip())
            for item in entry.get("task_ids", [])
            if normalize_task_id(str(item or "").strip())
        ]
        primary_task_id = normalize_task_id(str(entry.get("task_id", "")).strip())
        if primary_task_id and primary_task_id not in task_ids:
            task_ids.append(primary_task_id)
        task_ids = sorted(set(task_ids))
        summary = {
            "operation_id": str(entry.get("operation_id", "")).strip(),
            "source_kind": source_kind,
            "task_ids": task_ids,
            "followup_key": current_followup_key,
        }
        if dry_run:
            summaries.append(summary)
            continue
        for task_id in task_ids:
            state = load_task_state(config, task_id) or {}
            notification_summary = state.get("notification_summary", {}) if isinstance(state.get("notification_summary", {}), dict) else {}
            updates: dict[str, Any] = {
                "resumed_session_id": to_session_id,
                "notification_ok": False,
                "notification_finished_at": utc_now(),
                "notification_summary": {
                    **notification_summary,
                    "ok": False,
                    "deferred": True,
                    "deferred_reason": "session_migration_cutover_buffered",
                    "resumed_session_id": to_session_id,
                    "buffered_runtime_operation_id": str(entry.get("operation_id", "")).strip(),
                    "buffered_runtime_source_kind": source_kind,
                },
            }
            if source_kind in {"task_feedback", "manual_notify", "queued_feedback_followup"}:
                updates["pending_feedback"] = True
            if current_followup_key:
                updates["followup_status"] = "scheduled"
                updates["followup_last_action"] = "buffered_session_migration_cutover"
                updates["followup_last_message_path"] = str(followup_message_path(config, current_followup_key))
            merge_task_state(config, task_id, **updates)
        summaries.append(summary)
    return summaries


def perform_session_cutover(
    config: AppConfig,
    *,
    from_session_id: str,
    to_session_id: str,
    interrupt_grace_seconds: int,
    updated_by: str,
    source: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized_from_session_id = str(from_session_id or "").strip()
    normalized_to_session_id = str(to_session_id or "").strip()
    if not normalized_from_session_id or not normalized_to_session_id:
        raise ValueError("Missing session id for migrate-session.")
    if normalized_from_session_id == normalized_to_session_id:
        raise ValueError("from-session-id and to-session-id must be different.")
    existing_entry = session_migration_entry(config, normalized_from_session_id)
    existing_target = str(existing_entry.get("to_session_id", "")).strip()
    if existing_target and existing_target != normalized_to_session_id:
        raise ValueError(
            f"Session {normalized_from_session_id} is already redirected to {existing_target}; refusing to rebind to {normalized_to_session_id}."
        )

    anchor_spec = (
        latest_non_hidden_session_anchor_spec(config, normalized_from_session_id)
        or latest_non_hidden_session_anchor_spec(config, normalized_to_session_id)
        or {}
    )
    if not codex_session_exists_for_spec(config, anchor_spec, normalized_to_session_id):
        raise ValueError(f"Target Codex session does not exist or cannot be probed: {normalized_to_session_id}")

    bound_tasks = session_bound_task_records(config, normalized_from_session_id)
    followups = [item for item in load_followups(config) if str(item.get("codex_session_id", "")).strip() == normalized_from_session_id]
    continuous_snapshot = load_continuous_research_mode(config, codex_session_id=normalized_from_session_id)
    human_snapshot = load_human_guidance_mode(config, codex_session_id=normalized_from_session_id)
    if not bound_tasks and not followups and not continuous_snapshot.get("target_session_state") and not human_snapshot.get("target_session_state") and not existing_entry:
        raise ValueError(f"No non-hidden task, followup, or session-scoped mode binding found for session: {normalized_from_session_id}")

    preview_runtime_entries = active_feedback_entries_for_session(config, normalized_from_session_id)
    preview_buffered_entries = buffered_runtime_entries_for_migration(
        preview_runtime_entries,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        reason="session_migration_interrupt",
    )
    task_summaries = migrate_task_session_bindings(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        dry_run=dry_run,
    )
    followup_summaries = migrate_followup_session_bindings(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        dry_run=dry_run,
    )
    continuous_summary = migrate_continuous_research_session_binding(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        updated_by=updated_by,
        source=source,
        dry_run=dry_run,
    )
    human_summary = migrate_human_guidance_session_binding(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        updated_by=updated_by,
        source=source,
        dry_run=dry_run,
    )
    preview_followup_key_map = {
        str(item.get("followup_key", "")).strip(): str(item.get("new_followup_key", "")).strip()
        for item in followup_summaries
        if str(item.get("followup_key", "")).strip() and str(item.get("new_followup_key", "")).strip()
    }
    buffered_state_summary = apply_buffered_session_cutover_state(
        config,
        buffered_runtime_entries=preview_buffered_entries,
        to_session_id=normalized_to_session_id,
        followup_key_map=preview_followup_key_map,
        dry_run=True,
    )

    summary = {
        "from_session_id": normalized_from_session_id,
        "to_session_id": normalized_to_session_id,
        "dry_run": bool(dry_run),
        "task_bindings": task_summaries,
        "followup_bindings": followup_summaries,
        "continuous_mode": continuous_summary,
        "human_guidance": human_summary,
        "active_feedback_entries": preview_runtime_entries,
        "buffered_runtime_entries": preview_buffered_entries,
        "buffered_state_updates": buffered_state_summary,
    }
    if dry_run:
        return summary

    update_session_migration_entry(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        state="migrating",
        updated_by=updated_by,
        source=source,
    )
    runtime_entries, buffered_entries = interrupt_active_feedback_for_session(
        config,
        session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        interrupt_grace_seconds=interrupt_grace_seconds,
    )
    task_summaries = migrate_task_session_bindings(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        dry_run=False,
    )
    followup_summaries = migrate_followup_session_bindings(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        dry_run=False,
    )
    continuous_summary = migrate_continuous_research_session_binding(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        updated_by=updated_by,
        source=source,
        dry_run=False,
    )
    human_summary = migrate_human_guidance_session_binding(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        updated_by=updated_by,
        source=source,
        dry_run=False,
    )
    followup_key_map = {
        str(item.get("followup_key", "")).strip(): str(item.get("new_followup_key", "")).strip()
        for item in followup_summaries
        if str(item.get("followup_key", "")).strip() and str(item.get("new_followup_key", "")).strip()
    }
    buffered_state_summary = apply_buffered_session_cutover_state(
        config,
        buffered_runtime_entries=buffered_entries,
        to_session_id=normalized_to_session_id,
        followup_key_map=followup_key_map,
        dry_run=False,
    )
    completed_summary = {
        "task_binding_count": len(task_summaries),
        "followup_binding_count": len(followup_summaries),
        "interrupted_feedback_count": len(runtime_entries),
        "buffered_state_count": len(buffered_state_summary),
        "continuous_mode_changed": bool(continuous_summary.get("changed", False)),
        "human_guidance_changed": bool(human_summary.get("changed", False)),
    }
    update_session_migration_entry(
        config,
        from_session_id=normalized_from_session_id,
        to_session_id=normalized_to_session_id,
        state="completed",
        updated_by=updated_by,
        source=source,
        buffered_runtime_entries=buffered_entries,
        last_summary=completed_summary,
    )
    return {
        "from_session_id": normalized_from_session_id,
        "to_session_id": normalized_to_session_id,
        "dry_run": False,
        "task_bindings": task_summaries,
        "followup_bindings": followup_summaries,
        "continuous_mode": continuous_summary,
        "human_guidance": human_summary,
        "active_feedback_entries": runtime_entries,
        "buffered_runtime_entries": buffered_entries,
        "buffered_state_updates": buffered_state_summary,
        "migration_entry": session_migration_entry(config, normalized_from_session_id),
    }

def all_task_roots(config: AppConfig, *, include_legacy: bool | None = None) -> tuple[Path, ...]:
    if include_legacy is None:
        include_legacy = legacy_reads_enabled()
    if include_legacy:
        return (config.tasks_root, *config.legacy_task_roots)
    return (config.tasks_root,)


def task_index_rows(config: AppConfig, *, include_legacy: bool | None = None) -> list[dict[str, Any]]:
    return load_cached_task_index_rows(config.app_home, all_task_roots(config, include_legacy=include_legacy))


def find_task_dir(config: AppConfig, task_id: str) -> Path | None:
    for root in all_task_roots(config):
        candidate = root / task_id
        if candidate.exists():
            return candidate
    return None


def archive_root(config: AppConfig) -> Path:
    return config.app_home / "archive"


def executor_registry_path(config: AppConfig) -> Path:
    return executor_registry_path_impl(config)


def api_token_registry_path(config: AppConfig) -> Path:
    return config.app_home / "api_tokens.json"


def api_auth_hooks() -> ApiAuthHooks:
    return ApiAuthHooks(
        read_json=read_json,
        api_token_registry_path=api_token_registry_path,
    )


def normalize_posix_workdir(raw_path: str) -> str:
    return normalize_posix_workdir_impl(raw_path)


def executor_registry_hooks() -> ExecutorRegistryHooks:
    return ExecutorRegistryHooks(
        read_json=read_json,
        parse_gpu_id_list=parse_gpu_id_list,
        normalize_task_id=normalize_task_id,
    )


def load_executor_registry(config: AppConfig) -> dict[str, dict[str, Any]]:
    return load_executor_registry_impl(config, hooks=executor_registry_hooks())


def load_api_token_registry(config: AppConfig) -> dict[str, dict[str, Any]]:
    return load_api_token_registry_impl(config, hooks=api_auth_hooks())


def resolve_executor(config: AppConfig, executor_name: str) -> dict[str, Any]:
    return resolve_executor_impl(config, executor_name, hooks=executor_registry_hooks())


def resolve_api_token(config: AppConfig, token: str) -> dict[str, Any] | None:
    return resolve_api_token_impl(config, token, hooks=api_auth_hooks())


def resolve_api_visible_task_id(config: AppConfig, requested_task_id: str, token_record: dict[str, Any]) -> str:
    normalized_requested = normalize_task_id(requested_task_id)
    if not normalized_requested:
        return ""
    task_rows = task_index_rows(config)
    for row in task_rows:
        task_id = normalize_task_id(str(row.get("task_id", "")).strip())
        if task_id == normalized_requested and task_visible_to_api_token(row, row, token_record):
            return normalized_requested
    matches: list[tuple[tuple[str, str, str], str]] = []
    for row in task_rows:
        task_id = normalize_task_id(str(row.get("task_id", "")).strip())
        if not task_id or is_hidden_status(str(row.get("status", ""))):
            continue
        if not task_visible_to_api_token(row, row, token_record):
            continue
        if api_client_task_id(row, row) != normalized_requested:
            continue
        matches.append((task_state_recency_key(row), task_id))
    if matches:
        matches.sort()
        return matches[-1][1]
    return normalized_requested


def executor_gpu_map(spec: dict[str, Any]) -> dict[int, int]:
    host_gpu_ids = parse_gpu_id_list(spec.get("executor_host_gpu_ids", []))
    remote_gpu_ids = parse_gpu_id_list(spec.get("executor_remote_gpu_ids", []))
    mapping: dict[int, int] = {}
    for index, host_gpu_id in enumerate(host_gpu_ids):
        if index >= len(remote_gpu_ids):
            break
        mapping[host_gpu_id] = remote_gpu_ids[index]
    return mapping


def map_host_gpus_to_executor_visible_gpus(spec: dict[str, Any], host_gpu_ids: list[int]) -> list[int]:
    return map_host_gpus_to_executor_visible_gpus_impl(
        spec,
        host_gpu_ids,
        parse_gpu_id_list=parse_gpu_id_list,
    )


def validate_remote_workdir(remote_workdir: str, remote_prefix: str) -> None:
    validate_remote_workdir_impl(remote_workdir, remote_prefix)


def followup_runtime_hooks() -> FollowupRuntimeHooks:
    return FollowupRuntimeHooks(
        version=VERSION,
        continuous_session_reminder_followup_type=CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
        default_followup_workdir="/home/Awei",
        normalize_task_id=normalize_task_id,
        load_task_spec=load_task_spec,
        load_task_state=load_task_state,
        normalize_timestamp_fields=normalize_timestamp_fields,
        iter_all_task_states=iter_all_task_states,
        parse_timestamp_to_unix=parse_timestamp_to_unix,
        merge_task_state=merge_task_state,
        read_json=read_json,
        atomic_write_json=atomic_write_json,
        append_followup_event_log=append_followup_event_log,
        utc_now=utc_now,
        apply_session_redirect_to_spec=apply_session_redirect_to_spec,
        latest_continuous_research_anchor_spec=latest_continuous_research_anchor_spec,
        parse_gpu_id_list=parse_gpu_id_list,
        build_resume_prompt=build_resume_prompt,
        continuous_research_mode_enabled=continuous_research_mode_enabled,
        run_with_followup_lock=run_with_followup_lock,
        task_last_message_path=task_last_message_path,
    )


def session_runtime_hooks() -> SessionRuntimeHooks:
    return SessionRuntimeHooks(
        find_thread_info=find_thread_info,
        should_use_executor_codex=should_use_executor_codex,
        latest_remote_session_activity_ts=latest_remote_session_activity_ts,
        parse_timestamp_to_unix=parse_timestamp_to_unix,
        read_pid_cmdline=read_pid_cmdline,
        active_feedback_entries_for_session=active_feedback_entries_for_session,
        canonicalize_taskboard_signal=canonicalize_taskboard_signal,
        extract_taskboard_protocol_footer=extract_taskboard_protocol_footer,
        list_proc_entries=lambda: list(Path("/proc").iterdir()),
        now_ts=time.time,
        taskboard_final_signal_values=set(TASKBOARD_SIGNAL_VALUES),
        rate_limit_patterns=tuple(RATE_LIMIT_PATTERNS),
        session_busy_patterns=tuple(SESSION_BUSY_PATTERNS),
        platform_error_signatures=tuple(PLATFORM_ERROR_SIGNATURES),
        max_rollout_output_busy_tail_lines=MAX_ROLLOUT_OUTPUT_BUSY_TAIL_LINES,
        default_session_output_busy_retry_seconds=DEFAULT_SESSION_OUTPUT_BUSY_RETRY_SECONDS,
        default_session_output_busy_open_turn_stall_seconds=DEFAULT_SESSION_OUTPUT_BUSY_OPEN_TURN_STALL_SECONDS,
        default_platform_error_human_retry_seconds=DEFAULT_PLATFORM_ERROR_HUMAN_RETRY_SECONDS,
        default_resume_retry_seconds=DEFAULT_RESUME_RETRY_SECONDS,
        rollout_fallback_entry_grace_seconds=ROLLOUT_FALLBACK_ENTRY_GRACE_SECONDS,
        rollout_fallback_mtime_grace_seconds=ROLLOUT_FALLBACK_MTIME_GRACE_SECONDS,
    )


def codex_runtime_hooks() -> CodexRuntimeHooks:
    return CodexRuntimeHooks(
        should_use_executor_codex=should_use_executor_codex,
        build_remote_codex_command=build_remote_codex_command,
        build_codex_resume_command=build_codex_resume_command,
        build_codex_exec_command=build_codex_exec_command,
        run_local_interactive_codex=run_local_interactive_codex,
        run_tracked_feedback_subprocess=run_tracked_feedback_subprocess,
        run_subprocess=run_subprocess,
        extract_remote_last_message=extract_remote_last_message,
        ensure_dir=ensure_dir,
        allow_local_rollout_fallback=allow_local_rollout_fallback,
        latest_local_assistant_message_for_session=latest_local_assistant_message_for_session,
        extract_codex_session_id=extract_codex_session_id,
        continue_retry_error_kind=continue_retry_error_kind,
        append_log=append_log,
        sleep=time.sleep,
        now_ts=time.time,
        build_resume_prompt=build_resume_prompt,
        continuous_research_mode_enabled=continuous_research_mode_enabled,
        task_last_message_path=task_last_message_path,
        subagent_last_message_path=subagent_last_message_path,
        task_runner_log_path=task_runner_log_path,
        session_migration_entry=session_migration_entry,
        session_redirect_target=session_redirect_target,
        session_lock_path=session_lock_path,
        human_guidance_mode_active=human_guidance_mode_active,
        human_guidance_retry_after_seconds=human_guidance_retry_after_seconds,
        default_retry_delay_seconds=default_retry_delay_seconds,
        retry_after_seconds_from_target=retry_after_seconds_from_target,
        build_deferred_resume_result=build_deferred_resume_result,
        session_output_busy_snapshot=session_output_busy_snapshot,
        latest_session_activity_ts=latest_session_activity_ts,
        run_codex_prompt_with_continue_recovery=run_codex_prompt_with_continue_recovery,
        resume_codex_session_with_prompt=resume_codex_session_with_prompt,
        command_runtime_result_fields=lambda completed, exec_result, last_message_text: command_runtime_result_fields_impl(
            completed,
            exec_result,
            last_message_text=last_message_text,
            hooks=session_runtime_hooks(),
        ),
        classify_platform_error=classify_platform_error,
        platform_error_result_fields=platform_error_result_fields,
        is_rate_limit_retry_error=is_rate_limit_retry_error,
        is_session_busy_error=is_session_busy_error,
        session_busy_retry_after_seconds=session_busy_retry_after_seconds,
        platform_error_retry_after_seconds=platform_error_retry_after_seconds,
        platform_error_deferred_reason=platform_error_deferred_reason,
        sync_thread_for_fallback=sync_thread_for_fallback,
        task_root=task_root,
        extract_taskboard_signal=extract_taskboard_signal,
        extract_text_tail_signal_source=lambda last_message_text, stdout, stderr: (
            last_message_text or f"{stdout}\n{stderr}"
        ),
        extract_codex_session_id_from_completed=lambda completed: extract_codex_session_id(
            f"{completed.stdout}\n{completed.stderr}"
        ),
        utc_now=utc_now,
        default_session_migration_interrupt_grace_seconds=DEFAULT_SESSION_MIGRATION_INTERRUPT_GRACE_SECONDS,
        default_session_output_busy_retry_seconds=DEFAULT_SESSION_OUTPUT_BUSY_RETRY_SECONDS,
    )


def followup_key_for(spec: dict[str, Any]) -> str:
    return followup_key_for_impl(spec)


def queued_feedback_key_for(spec: dict[str, Any]) -> str:
    return queued_feedback_key_for_impl(spec)


def continuous_session_followup_key_for(codex_session_id: str) -> str:
    return continuous_session_followup_key_for_impl(codex_session_id)


def followup_path(config: AppConfig, followup_key: str) -> Path:
    return followup_path_impl(config, followup_key)


def followup_message_path(config: AppConfig, followup_key: str) -> Path:
    return followup_message_path_impl(config, followup_key)


def build_followup_resume_spec_from_payload(followup: dict[str, Any]) -> dict[str, Any]:
    return build_followup_resume_spec_from_payload_impl(
        followup,
        hooks=followup_runtime_hooks(),
    )



def should_schedule_followup_for_spec(spec: dict[str, Any]) -> bool:
    return should_schedule_followup_for_spec_impl(spec)



def current_followup_resume_spec(config: AppConfig, followup: dict[str, Any]) -> dict[str, Any]:
    return current_followup_resume_spec_impl(
        config,
        followup,
        hooks=followup_runtime_hooks(),
    )



def merge_queued_notification_lists(existing_items: list[dict[str, Any]], incoming_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return merge_queued_notification_lists_impl(
        existing_items,
        incoming_items,
        hooks=followup_runtime_hooks(),
    )



def sync_followup_state(
    config: AppConfig,
    followup: dict[str, Any],
    *,
    followup_status: str,
    followup_last_action: str,
    followup_last_signal: str = "",
    followup_stopped_at: str = "",
    pending_feedback: bool | None = None,
    notification_signal: str | None = None,
    message_path: str = "",
) -> None:
    sync_followup_state_impl(
        config,
        followup,
        hooks=followup_runtime_hooks(),
        followup_status=followup_status,
        followup_last_action=followup_last_action,
        followup_last_signal=followup_last_signal,
        followup_stopped_at=followup_stopped_at,
        pending_feedback=pending_feedback,
        notification_signal=notification_signal,
        message_path=message_path,
    )



def rebind_followup_to_current_task(
    config: AppConfig,
    followup: dict[str, Any],
) -> tuple[dict[str, Any], bool, bool]:
    return rebind_followup_to_current_task_impl(
        config,
        followup,
        hooks=followup_runtime_hooks(),
    )



def followup_entity_info(config: AppConfig, task_id: str) -> tuple[bool, str]:
    return followup_entity_info_impl(config, task_id, hooks=followup_runtime_hooks())



def rollout_candidates_for_session(config: AppConfig, session_id: str) -> list[Path]:
    return rollout_candidates_for_session_impl(
        config,
        session_id,
        hooks=session_runtime_hooks(),
    )


def latest_session_activity_ts(config: AppConfig, session_id: str, spec: dict[str, Any] | None = None) -> float:
    return latest_session_activity_ts_impl(
        config,
        session_id,
        spec,
        hooks=session_runtime_hooks(),
    )


def latest_local_rollout_output_snapshot(config: AppConfig, session_id: str) -> dict[str, Any]:
    return latest_local_rollout_output_snapshot_impl(
        config,
        session_id,
        hooks=session_runtime_hooks(),
    )


def active_codex_resume_pids_for_session(session_id: str) -> list[int]:
    return active_codex_resume_pids_for_session_impl(
        session_id,
        hooks=session_runtime_hooks(),
    )


def session_output_busy_snapshot(
    config: AppConfig,
    session_id: str,
    *,
    spec: dict[str, Any] | None = None,
    activity_window_seconds: int = DEFAULT_SESSION_OUTPUT_BUSY_ACTIVITY_SECONDS,
    now_ts: float | None = None,
) -> dict[str, Any]:
    if now_ts is not None:
        hooks = SessionRuntimeHooks(
            **{
                **session_runtime_hooks().__dict__,
                'now_ts': lambda: float(now_ts),
            }
        )
    else:
        hooks = session_runtime_hooks()
    return session_output_busy_snapshot_impl(
        config,
        session_id,
        spec=spec,
        activity_window_seconds=activity_window_seconds,
        hooks=hooks,
    )


def archive_task_dir(config: AppConfig, task_id: str) -> Path | None:
    root = archive_root(config)
    if not root.exists():
        return None
    matches = list(root.glob(f"**/{task_id}"))
    return matches[0] if matches else None


def bounded_lock_name(raw_value: str, *, fallback_prefix: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(raw_value or "")).strip("-")
    if not safe:
        safe = hashlib.sha1(str(raw_value or fallback_prefix).encode("utf-8")).hexdigest()[:16]
    max_chars = 120
    if len(safe) <= max_chars:
        return safe
    digest = hashlib.sha1(str(raw_value or fallback_prefix).encode("utf-8")).hexdigest()[:16]
    head = safe[: max_chars - len(digest) - 1].rstrip("-._")
    return f"{head}-{digest}" if head else digest


def session_lock_name(session_id: str) -> str:
    return bounded_lock_name(session_id, fallback_prefix="session")


def extract_codex_session_id(text: str) -> str:
    return extract_codex_session_id_impl(text)


def extract_taskboard_signal(text: str) -> str:
    return extract_taskboard_signal_impl(text, hooks=session_runtime_hooks())


def taskboard_light_research_brief_lines(*, continuous_mode: bool) -> list[str]:
    lines = prompt_block_lines("light_research_agreement")
    if continuous_mode:
        lines = [*prompt_block_lines("continuous_intro"), *lines]
    return lines


def taskboard_footer_contract_lines() -> list[str]:
    return [
        "回复末尾请单独补一组自检行：",
        f"TASKBOARD_SIGNAL={EXECUTION_READY_SIGNAL}|{WAITING_ON_ASYNC_SIGNAL}|{CLOSEOUT_READY_SIGNAL}|none",
        "TASKBOARD_SELF_CHECK=pass|fail",
        "LIVE_TASK_STATUS=none|submitted|awaiting",
    ]


def compact_research_governance_header_lines(
    spec: dict[str, Any],
    *,
    continuous_mode: bool,
) -> list[str]:
    lines: list[str] = []
    lines.extend(taskboard_light_research_brief_lines(continuous_mode=continuous_mode))
    lines.extend(proposal_feedback_instruction_lines(spec, profile=PROMPT_PROFILE_RESUME_COMPACT))
    return lines


def compact_runtime_resume_header_lines(spec: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.extend(proposal_feedback_instruction_lines(spec, profile=PROMPT_PROFILE_RESUME_COMPACT))
    return lines


def compact_context_sections(
    spec: dict[str, Any],
    *,
    include_canonical_head: bool = True,
    include_evidence_first: bool = False,
    include_footer: bool = False,
) -> list[str]:
    lines: list[str] = []
    if include_canonical_head:
        lines.extend(runtime_canonical_head_prompt_lines(spec))
    if include_evidence_first:
        lines.extend(evidence_first_loop_lines(compact=True))
    if include_footer:
        lines.extend(taskboard_footer_contract_lines())
    return lines


def extract_text_from_message_content(content: Any) -> str:
    return extract_text_from_message_content_impl(content)


def extract_last_assistant_message_from_rollout(path: Path, *, min_entry_ts: float = 0.0) -> str:
    return extract_last_assistant_message_from_rollout_impl(
        path,
        min_entry_ts=min_entry_ts,
        hooks=session_runtime_hooks(),
    )


def latest_local_assistant_message_for_session(
    config: AppConfig,
    session_id: str,
    *,
    min_mtime: float = 0.0,
    min_entry_ts: float | None = None,
) -> str:
    return latest_local_assistant_message_for_session_impl(
        config,
        session_id,
        min_mtime=min_mtime,
        min_entry_ts=min_entry_ts,
        hooks=session_runtime_hooks(),
    )


def allow_local_rollout_fallback(
    config: AppConfig,
    *,
    mode: str,
    session_id: str,
) -> bool:
    return allow_local_rollout_fallback_impl(
        config,
        mode=mode,
        session_id=session_id,
        hooks=session_runtime_hooks(),
    )


def is_rate_limit_retry_error(*texts: str) -> bool:
    return is_rate_limit_retry_error_impl(*texts, hooks=session_runtime_hooks())


def is_session_busy_error(*texts: str) -> bool:
    return is_session_busy_error_impl(*texts, hooks=session_runtime_hooks())


def platform_error_spec_for_kind(kind: str) -> dict[str, Any]:
    return platform_error_spec_for_kind_impl(kind, hooks=session_runtime_hooks())


def classify_platform_error(*texts: str) -> dict[str, Any]:
    return classify_platform_error_impl(*texts, hooks=session_runtime_hooks())


def continue_retry_error_kind(*texts: str) -> str:
    return continue_retry_error_kind_impl(*texts, hooks=session_runtime_hooks())


def platform_error_from_reason(reason: str) -> dict[str, Any]:
    return platform_error_from_reason_impl(reason, hooks=session_runtime_hooks())


def platform_error_deferred_reason(kind: str) -> str:
    return platform_error_deferred_reason_impl(kind)


def platform_error_retry_after_seconds(*, retryable: bool, min_idle_seconds: int) -> int:
    return platform_error_retry_after_seconds_impl(
        retryable=retryable,
        min_idle_seconds=min_idle_seconds,
        hooks=session_runtime_hooks(),
    )


def session_busy_retry_after_seconds() -> int:
    return session_busy_retry_after_seconds_impl(hooks=session_runtime_hooks())


def platform_error_result_fields(details: dict[str, Any], *, source: str) -> dict[str, Any]:
    return platform_error_result_fields_impl(details, source=source)


def retry_after_seconds_from_target(target_ts: float) -> int:
    return retry_after_seconds_from_target_impl(target_ts, hooks=session_runtime_hooks())


def default_retry_delay_seconds(min_idle_seconds: int = 0) -> int:
    return default_retry_delay_seconds_impl(min_idle_seconds, hooks=session_runtime_hooks())


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
    return build_deferred_resume_result_impl(
        original_session_id=original_session_id,
        resumed_session_id=resumed_session_id,
        codex_exec_mode=codex_exec_mode,
        prompt_chars=prompt_chars,
        deferred_reason=deferred_reason,
        retry_after_seconds=retry_after_seconds,
        attempted=attempted,
        started_at=started_at,
        finished_at=finished_at,
    )


def process_runtime_hooks() -> ProcessRuntimeHooks:
    return ProcessRuntimeHooks(
        path_exists=lambda path: path.exists(),
        read_bytes=lambda path: path.read_bytes(),
        read_text=lambda path: path.read_text(encoding="utf-8", errors="ignore"),
        readlink=os.readlink,
        run_subprocess=lambda args, timeout: run_subprocess(args, timeout=timeout),
    )


def pid_exists(pid: int) -> bool:
    return pid_exists_impl(pid, hooks=process_runtime_hooks())


def read_pid_cmdline(pid: int) -> str:
    return read_pid_cmdline_impl(pid, hooks=process_runtime_hooks())


def read_pid_cwd(pid: int) -> str:
    return read_pid_cwd_impl(pid, hooks=process_runtime_hooks())


def read_pid_state(pid: int) -> str:
    return read_pid_state_impl(pid, hooks=process_runtime_hooks())


def read_pid_snapshot(pid: int) -> dict[str, Any] | None:
    return read_pid_snapshot_impl(pid, hooks=process_runtime_hooks())


def build_tmux_session_name(task_id: str) -> str:
    return build_tmux_session_name_impl(task_id)


def task_payload_hooks() -> TaskPayloadHooks:
    return TaskPayloadHooks(
        normalize_task_id=normalize_task_id,
        normalize_timestamp_fields=normalize_timestamp_fields,
    )


def normalize_task_spec_payload(raw: dict[str, Any]) -> dict[str, Any]:
    return normalize_task_spec_payload_impl(
        raw,
        version=VERSION,
        default_cpu_retry_max_attempts=DEFAULT_CPU_RETRY_MAX_ATTEMPTS,
        default_startup_failure_seconds=DEFAULT_STARTUP_FAILURE_SECONDS,
        hooks=task_payload_hooks(),
    )


def normalize_task_state_payload(raw: dict[str, Any]) -> dict[str, Any]:
    return normalize_task_state_payload_impl(
        raw,
        version=VERSION,
        default_cpu_retry_max_attempts=DEFAULT_CPU_RETRY_MAX_ATTEMPTS,
        hooks=task_payload_hooks(),
    )


def task_storage_hooks() -> TaskStorageHooks:
    return TaskStorageHooks(
        all_task_roots=all_task_roots,
        find_task_dir=find_task_dir,
        ensure_dir=ensure_dir,
        read_json=read_json,
        atomic_write_json=atomic_write_json,
        normalize_task_spec_payload=normalize_task_spec_payload,
        normalize_task_state_payload=normalize_task_state_payload,
        normalize_timestamp_fields=normalize_timestamp_fields,
        reconcile_active_task_state=reconcile_active_task_state,
        is_hidden_status=is_hidden_status,
        task_list_sort_key=task_list_sort_key,
        update_task_index_entry=update_task_index_entry,
    )


def task_root(config: AppConfig, task_id: str) -> Path:
    return task_root_impl(config, task_id, hooks=task_storage_hooks())


def task_spec_path(config: AppConfig, task_id: str) -> Path:
    return task_spec_path_impl(config, task_id, hooks=task_storage_hooks())


def task_state_path(config: AppConfig, task_id: str) -> Path:
    return task_state_path_impl(config, task_id, hooks=task_storage_hooks())


def task_command_log_path(config: AppConfig, task_id: str) -> Path:
    return task_command_log_path_impl(config, task_id, hooks=task_storage_hooks())


def task_runner_log_path(config: AppConfig, task_id: str) -> Path:
    return task_runner_log_path_impl(config, task_id, hooks=task_storage_hooks())


def task_last_message_path(config: AppConfig, task_id: str) -> Path:
    return task_last_message_path_impl(config, task_id, hooks=task_storage_hooks())


def subagent_last_message_path(config: AppConfig, task_id: str) -> Path:
    return subagent_last_message_path_impl(config, task_id, hooks=task_storage_hooks())


def task_events_dir(config: AppConfig, task_id: str) -> Path:
    return task_events_dir_impl(config, task_id, hooks=task_storage_hooks())


def task_paths(config: AppConfig, task_id: str) -> dict[str, str]:
    return task_paths_impl(config, task_id, hooks=task_storage_hooks())


def task_paths_for_root(root: Path, task_id: str) -> dict[str, str]:
    return task_paths_for_root_impl(root, task_id)


def ensure_task_layout(config: AppConfig, task_id: str) -> None:
    ensure_task_layout_impl(config, task_id, hooks=task_storage_hooks())


def load_task_spec(config: AppConfig, task_id: str) -> dict[str, Any]:
    return load_task_spec_impl(config, task_id, hooks=task_storage_hooks())


def load_task_state(config: AppConfig, task_id: str) -> dict[str, Any]:
    return load_task_state_impl(config, task_id, hooks=task_storage_hooks())


def load_event(path: Path) -> dict[str, Any]:
    return load_event_impl(path, hooks=task_storage_hooks())


def resolve_event_path(config: AppConfig, task_id: str, explicit_path: str | None = None) -> Path:
    return resolve_event_path_impl(config, task_id, hooks=task_storage_hooks(), explicit_path=explicit_path)


def write_task_state(config: AppConfig, task_id: str, state: dict[str, Any]) -> None:
    write_task_state_impl(config, task_id, state, hooks=task_storage_hooks())


def merge_task_state(config: AppConfig, task_id: str, **updates: Any) -> dict[str, Any]:
    return merge_task_state_impl(config, task_id, hooks=task_storage_hooks(), updated_at=utc_now(), **updates)


def write_task_spec(config: AppConfig, task_id: str, spec: dict[str, Any]) -> None:
    write_task_spec_impl(config, task_id, spec, hooks=task_storage_hooks())

def update_task_feedback_mode(config: AppConfig, task_id: str, mode: str) -> None:
    if mode not in {"auto", "manual", "off"}:
        raise ValueError(f"Unsupported feedback mode: {mode}")
    spec = load_task_spec(config, task_id)
    if spec:
        spec["feedback_mode"] = mode
        write_task_spec(config, task_id, spec)
    state = load_task_state(config, task_id)
    if state:
        pending_feedback = bool(state.get("pending_feedback", False))
        if mode == "off":
            pending_feedback = False
        elif mode == "manual" and is_terminal_status(str(state.get("status", ""))) and not state.get("notification_ok", False):
            pending_feedback = True
        merge_task_state(config, task_id, feedback_mode=mode, pending_feedback=pending_feedback)


def update_task_priority(config: AppConfig, task_id: str, priority: int) -> None:
    spec = load_task_spec(config, task_id)
    if spec:
        spec["priority"] = int(priority)
        write_task_spec(config, task_id, spec)
    state = load_task_state(config, task_id)
    if state:
        merge_task_state(config, task_id, priority=int(priority))


def current_task_priority(config: AppConfig, task_id: str) -> int:
    state = load_task_state(config, task_id)
    if state:
        return int(state.get("priority", 0) or 0)
    spec = load_task_spec(config, task_id)
    return int(spec.get("priority", 0) or 0) if spec else 0


def schedule_followup(
    config: AppConfig,
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
) -> None:
    schedule_followup_impl(
        config,
        task_id=task_id,
        spec=spec,
        reason=reason,
        delay_seconds=delay_seconds,
        interval_seconds=interval_seconds,
        min_idle_seconds=min_idle_seconds,
        followup_key_override=followup_key_override,
        followup_type=followup_type,
        last_signal=last_signal,
        hooks=followup_runtime_hooks(),
    )



def schedule_continuous_session_reminder(
    config: AppConfig,
    *,
    session_id: str,
    spec: dict[str, Any],
    reason: str = CONTINUOUS_RESEARCH_IDLE_REASON,
    delay_seconds: int = DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS,
    interval_seconds: int = DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS,
    min_idle_seconds: int = DEFAULT_CONTINUOUS_RESEARCH_MIN_IDLE_SECONDS,
    last_signal: str = "",
) -> None:
    task_id = normalize_task_id(str(spec.get("task_id", "")).strip())
    normalized_session_id = str(session_id or spec.get("codex_session_id", "")).strip()
    if not task_id or not normalized_session_id:
        return
    schedule_followup(
        config,
        task_id=task_id,
        spec=spec,
        reason=str(reason or CONTINUOUS_RESEARCH_IDLE_REASON),
        delay_seconds=delay_seconds,
        interval_seconds=interval_seconds,
        min_idle_seconds=min_idle_seconds,
        followup_key_override=continuous_session_followup_key_for(normalized_session_id),
        followup_type=CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
        last_signal=last_signal,
    )


def should_schedule_immediate_parked_watchdog(
    config: AppConfig,
    *,
    session_id: str,
    spec: dict[str, Any],
    repeat_count: int,
) -> bool:
    if not session_id:
        return False
    if repeat_count > CONTINUOUS_RESEARCH_IDLE_LOOP_THRESHOLD:
        return False
    if not continuous_research_mode_enabled(config, codex_session_id=session_id):
        return False
    return should_schedule_followup_for_spec(spec)


def schedule_immediate_parked_watchdog(
    config: AppConfig,
    *,
    session_id: str,
    spec: dict[str, Any],
    signal_value: str,
) -> None:
    schedule_continuous_session_reminder(
        config,
        session_id=session_id,
        spec=spec,
        reason=CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON,
        delay_seconds=0,
        interval_seconds=DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS,
        min_idle_seconds=0,
        last_signal=signal_value,
    )


def schedule_continuous_research_followup(
    config: AppConfig,
    *,
    task_id: str,
    spec: dict[str, Any],
    trigger_signal: str,
    message_path: str = "",
) -> None:
    schedule_followup(
        config,
        task_id=task_id,
        spec=spec,
        reason=CONTINUOUS_RESEARCH_REASON,
        delay_seconds=DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS,
        interval_seconds=DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS,
        min_idle_seconds=DEFAULT_CONTINUOUS_RESEARCH_MIN_IDLE_SECONDS,
        last_signal=trigger_signal,
    )
    merge_task_state(
        config,
        task_id,
        followup_status="scheduled",
        followup_last_signal=trigger_signal,
        followup_last_action=f"scheduled:{CONTINUOUS_RESEARCH_REASON}",
        followup_stopped_at="",
        followup_last_message_path=message_path or str(task_last_message_path(config, task_id)),
    )


def schedule_continuous_transition_followup(
    config: AppConfig,
    *,
    task_id: str,
    spec: dict[str, Any],
    trigger_signal: str,
    message_path: str = "",
    followup: dict[str, Any] | None = None,
) -> None:
    followup_key_override = ""
    if followup is not None:
        followup_key_override = str(followup.get("followup_key", "")).strip()
    schedule_followup(
        config,
        task_id=task_id,
        spec=spec,
        reason=CONTINUOUS_RESEARCH_TRANSITION_REASON,
        delay_seconds=DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS,
        interval_seconds=DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS,
        min_idle_seconds=DEFAULT_CONTINUOUS_RESEARCH_MIN_IDLE_SECONDS,
        followup_key_override=followup_key_override,
        followup_type=CONTINUOUS_RESEARCH_TRANSITION_FOLLOWUP_TYPE,
        last_signal=trigger_signal,
    )
    target_key = followup_key_override or followup_key_for(spec)
    target_path = followup_path(config, target_key)
    payload = read_json(target_path, {})
    if not isinstance(payload, dict) or not payload:
        return
    payload["followup_type"] = CONTINUOUS_RESEARCH_TRANSITION_FOLLOWUP_TYPE
    payload["last_signal"] = str(trigger_signal or "").strip()
    payload["updated_at"] = utc_now()
    atomic_write_json(target_path, payload)
    merge_task_state(
        config,
        task_id,
        followup_status="scheduled",
        followup_last_signal=str(trigger_signal or "").strip(),
        followup_last_action=f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}",
        followup_stopped_at="",
        followup_last_message_path=message_path or str(task_last_message_path(config, task_id)),
    )


def queue_feedback_resume(
    config: AppConfig,
    *,
    task_id: str,
    spec: dict[str, Any],
    event: dict[str, Any],
    reason: str,
    min_idle_seconds: int = DEFAULT_NOTIFICATION_MIN_IDLE_SECONDS,
) -> dict[str, Any]:
    return queue_feedback_resume_impl(
        config,
        task_id=task_id,
        spec=spec,
        event=event,
        reason=reason,
        min_idle_seconds=min_idle_seconds,
        hooks=followup_runtime_hooks(),
    )



def load_followups(config: AppConfig) -> list[dict[str, Any]]:
    return load_followups_impl(config, hooks=followup_runtime_hooks())



def queued_notification_entries(followup: dict[str, Any]) -> list[dict[str, Any]]:
    return queued_notification_entries_impl(followup)


def reflow_backlog_entries(
    config: AppConfig,
    *,
    codex_session_id: str = "",
    followups: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    normalized_session_id = str(codex_session_id or "").strip()
    backlog: list[dict[str, Any]] = []
    for followup in followups or load_followups(config):
        if str(followup.get("followup_type", "")).strip() != "queued_feedback_resume":
            continue
        followup_session_id = str(followup.get("codex_session_id", "")).strip() or str(
            (followup.get("spec_snapshot", {}) if isinstance(followup.get("spec_snapshot", {}), dict) else {}).get("codex_session_id", "")
        ).strip()
        if normalized_session_id and followup_session_id != normalized_session_id:
            continue
        entries = queued_notification_entries(followup)
        timestamps = [
            str(item.get("event_timestamp", "") or item.get("finished_at", "") or item.get("queued_at", "")).strip()
            for item in entries
            if isinstance(item, dict)
        ]
        timestamps = [item for item in timestamps if item]
        backlog.append(
            {
                "followup_key": str(followup.get("followup_key", "")).strip(),
                "codex_session_id": followup_session_id,
                "queue_depth": len(entries),
                "oldest_event_at": min(timestamps) if timestamps else "",
                "latest_event_at": max(timestamps) if timestamps else "",
                "task_ids": followup_task_ids(followup),
                "entries": entries,
            }
        )
    backlog.sort(key=lambda item: (item.get("codex_session_id", ""), item.get("oldest_event_at", ""), item.get("followup_key", "")))
    return backlog


def reflow_backlog_summary(
    config: AppConfig,
    *,
    codex_session_id: str = "",
    followups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entries = reflow_backlog_entries(config, codex_session_id=codex_session_id, followups=followups)
    queue_depth = sum(int(item.get("queue_depth", 0) or 0) for item in entries)
    oldest = min((str(item.get("oldest_event_at", "")).strip() for item in entries if str(item.get("oldest_event_at", "")).strip()), default="")
    latest = max((str(item.get("latest_event_at", "")).strip() for item in entries if str(item.get("latest_event_at", "")).strip()), default="")
    return {
        "session_count": len({str(item.get("codex_session_id", "")).strip() for item in entries if str(item.get("codex_session_id", "")).strip()}),
        "followup_count": len(entries),
        "queue_depth": queue_depth,
        "oldest_event_at": oldest,
        "latest_event_at": latest,
        "entries": entries,
    }


def clear_reflow_backlog(config: AppConfig, *, codex_session_id: str = "", clear_all: bool = False) -> dict[str, Any]:
    summaries = reflow_backlog_entries(config, codex_session_id="" if clear_all else codex_session_id)
    cleared_followups = 0
    cleared_events = 0
    cleared_sessions: set[str] = set()
    for item in summaries:
        session_id = str(item.get("codex_session_id", "")).strip()
        if not clear_all and codex_session_id and session_id != str(codex_session_id).strip():
            continue
        followup_key = str(item.get("followup_key", "")).strip()
        if not followup_key:
            continue
        for task_id in item.get("task_ids", []):
            merge_task_state(
                config,
                str(task_id),
                pending_feedback=False,
                followup_status="resolved",
                followup_last_action="backlog_cleared_manually",
                followup_stopped_at=utc_now(),
            )
        resolve_followup(config, followup_key)
        cleared_followups += 1
        cleared_events += int(item.get("queue_depth", 0) or 0)
        if session_id:
            cleared_sessions.add(session_id)
    return {
        "cleared_followups": cleared_followups,
        "cleared_events": cleared_events,
        "cleared_sessions": sorted(cleared_sessions),
    }


def followup_task_ids(followup: dict[str, Any]) -> list[str]:
    return followup_task_ids_impl(followup, hooks=followup_runtime_hooks())



def followup_processing_sort_key(followup: dict[str, Any]) -> tuple[int, float, str]:
    return followup_processing_sort_key_impl(
        followup,
        continuous_session_reminder_followup_type=CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
    )



def session_followup_present(
    followups: list[dict[str, Any]],
    session_id: str,
    *,
    exclude_followup_key: str = "",
) -> bool:
    return session_followup_present_impl(
        followups,
        session_id,
        exclude_followup_key=exclude_followup_key,
    )



def active_session_followup(
    followups: list[dict[str, Any]],
    session_id: str,
    *,
    exclude_followup_key: str = "",
) -> dict[str, Any]:
    return active_session_followup_impl(
        followups,
        session_id,
        continuous_session_reminder_followup_type=CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
        exclude_followup_key=exclude_followup_key,
    )



def followup_map_by_task_id(config: AppConfig) -> dict[str, dict[str, Any]]:
    return followup_map_by_task_id_impl(config, hooks=followup_runtime_hooks())



def newer_task_exists(config: AppConfig, followup: dict[str, Any]) -> bool:
    return newer_task_exists_impl(config, followup, hooks=followup_runtime_hooks())



def newer_task_exists_for_spec(config: AppConfig, *, source_task_id: str, spec: dict[str, Any]) -> bool:
    return newer_task_exists_for_spec_impl(
        config,
        source_task_id=source_task_id,
        spec=spec,
        hooks=followup_runtime_hooks(),
    )



def resolve_followup(config: AppConfig, followup_key: str) -> None:
    resolve_followup_impl(config, followup_key)



def defer_followup_retry(
    config: AppConfig,
    followup: dict[str, Any],
    *,
    reason: str,
    retry_after_seconds: int,
    message_path: str = "",
) -> None:
    defer_followup_retry_impl(
        config,
        followup,
        reason=reason,
        retry_after_seconds=retry_after_seconds,
        message_path=message_path,
        hooks=followup_runtime_hooks(),
    )



def resolve_followups_for_stop_signal(
    config: AppConfig,
    *,
    session_id: str,
    agent_name: str,
    signal_value: str,
    reason: str,
    message_path: str = "",
) -> list[str]:
    return resolve_followups_for_stop_signal_impl(
        config,
        session_id=session_id,
        agent_name=agent_name,
        signal_value=signal_value,
        reason=reason,
        message_path=message_path,
        hooks=followup_runtime_hooks(),
    )



def recover_missing_queued_feedback_followups(config: AppConfig) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    current_followups = followup_map_by_task_id(config)
    for state in iter_all_task_states(config):
        task_id = normalize_task_id(str(state.get("task_id", "")).strip())
        if not task_id or task_id in current_followups:
            continue
        if not bool(state.get("pending_feedback", False)):
            continue
        if str(state.get("feedback_mode", "auto")).strip() != "auto":
            continue
        if str(state.get("followup_status", "")).strip() != "scheduled":
            continue
        if not str(state.get("followup_last_action", "")).strip().startswith("queued_feedback_resume:"):
            continue
        spec = load_task_spec(config, task_id)
        last_event_path = str(state.get("last_event_path", "")).strip()
        if not spec or not last_event_path:
            merge_task_state(
                config,
                task_id,
                pending_feedback=False,
                followup_status="stopped",
                followup_last_action="recovery_failed_missing_spec_or_event",
                followup_stopped_at=utc_now(),
            )
            recovered.append({"task_id": task_id, "action": "recovery_failed_missing_spec_or_event"})
            continue
        event_path = Path(last_event_path).expanduser().resolve()
        if not event_path.exists():
            merge_task_state(
                config,
                task_id,
                pending_feedback=False,
                followup_status="stopped",
                followup_last_action="recovery_failed_missing_event_file",
                followup_stopped_at=utc_now(),
            )
            recovered.append({"task_id": task_id, "action": "recovery_failed_missing_event_file"})
            continue
        try:
            event = load_event(event_path)
        except ValueError:
            merge_task_state(
                config,
                task_id,
                pending_feedback=False,
                followup_status="stopped",
                followup_last_action="recovery_failed_invalid_event_file",
                followup_stopped_at=utc_now(),
            )
            recovered.append({"task_id": task_id, "action": "recovery_failed_invalid_event_file"})
            continue
        queued = queue_feedback_resume(
            config,
            task_id=task_id,
            spec=spec,
            event=event,
            reason="recovered_missing_followup_entity",
            min_idle_seconds=0,
        )
        merge_task_state(
            config,
            task_id,
            pending_feedback=True,
            followup_status="scheduled",
            followup_last_action="recovered_missing_queued_feedback_entity",
            followup_last_message_path=str(queued["message_path"]),
        )
        recovered.append(
            {
                "task_id": task_id,
                "action": "recovered_missing_queued_feedback_entity",
                "followup_key": queued["followup_key"],
                "queue_depth": queued["queue_depth"],
            }
        )
    return recovered


def maybe_park_continuous_idle_loop(
    config: AppConfig,
    *,
    followup: dict[str, Any],
    spec_for_resume: dict[str, Any],
    signal_value: str,
    is_continuous_followup: bool,
    is_continuous_session_reminder: bool,
) -> tuple[bool, str, int]:
    if not (is_continuous_followup or is_continuous_session_reminder):
        return False, "", 0
    session_id = str(followup.get("codex_session_id", "") or spec_for_resume.get("codex_session_id", "")).strip()
    if not session_id:
        return False, "", 0
    evidence_token = continuous_research_session_evidence_token(config, session_id, spec=spec_for_resume)
    session_state = continuous_research_session_state(config, session_id)
    previous_token = str(session_state.get("last_evidence_token", "")).strip()
    previous_count = max(0, int(session_state.get("stable_idle_repeat_count", 0) or 0))
    next_action_hint = session_continuation_hint(config, session_id, spec=spec_for_resume)
    next_action_hash = str(next_action_hint.get("action_hash", "")).strip()
    previous_next_action_hash = str(session_state.get("next_action_hash", "")).strip()
    previous_next_action_repeat_count = max(0, int(session_state.get("next_action_repeat_count", 0) or 0))
    next_action_updates = {
        "next_action_hash": next_action_hash,
        "next_action_text": str(next_action_hint.get("action_text", "")),
        "next_action_state": str(next_action_hint.get("status", "")),
        "next_action_source_path": str(next_action_hint.get("source_path", "")),
        "next_action_source_updated_at": str(next_action_hint.get("source_updated_at", "")),
    }
    if signal_value in PARKED_IDLE_SIGNALS:
        clear_continuous_research_session_waiting_state(
            config,
            codex_session_id=session_id,
            evidence_token=evidence_token,
            last_signal=EXECUTION_READY_SIGNAL,
            stable_idle_repeat_count=max(1, previous_count),
            updated_by="followup",
            source="continuous-idle-loop-ignore-legacy-parked",
            next_action_repeat_count=max(1, previous_next_action_repeat_count),
            **next_action_updates,
        )
        return False, evidence_token, max(1, previous_count)
    if signal_value not in LOCAL_MICROSTEP_BATCH_SIGNALS:
        clear_continuous_research_session_waiting_state(
            config,
            codex_session_id=session_id,
            evidence_token=evidence_token,
            last_signal=signal_value,
            updated_by="followup",
            source="continuous-idle-loop-reset",
            next_action_hash="",
            next_action_text="",
            next_action_state="",
            next_action_source_path="",
            next_action_source_updated_at="",
            next_action_repeat_count=0,
        )
        return False, evidence_token, 0
    if bool(next_action_hint.get("controller_inherit_local", False)) and next_action_hash:
        repeat_count = previous_next_action_repeat_count + 1 if next_action_hash == previous_next_action_hash else 1
        clear_continuous_research_session_waiting_state(
            config,
            codex_session_id=session_id,
            evidence_token=evidence_token,
            last_signal=signal_value,
            stable_idle_repeat_count=repeat_count,
            updated_by="followup",
            source="continuous-idle-loop-observed",
            next_action_repeat_count=repeat_count,
            **next_action_updates,
        )
        return False, evidence_token, repeat_count
    repeat_count = previous_count + 1 if evidence_token and previous_token == evidence_token else 1
    clear_continuous_research_session_waiting_state(
        config,
        codex_session_id=session_id,
        evidence_token=evidence_token,
        last_signal=signal_value,
        stable_idle_repeat_count=repeat_count,
        updated_by="followup",
        source="continuous-idle-loop-observed",
        next_action_repeat_count=0,
        **next_action_updates,
    )
    return False, evidence_token, repeat_count


def parked_waiting_signal_guard_details(
    config: AppConfig,
    *,
    session_id: str,
    spec: dict[str, Any],
    followup_last_signal: str = "",
    newer_async_task_exists: bool = False,
) -> tuple[bool, str, str, int]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id or newer_async_task_exists:
        return False, "", "", 0
    session_state = continuous_research_session_state(config, normalized_session_id)
    parked_waiting_state = str(session_state.get("waiting_state", "")).strip()
    if parked_waiting_state not in PARKED_IDLE_SIGNALS:
        candidate_signal = str(followup_last_signal or session_state.get("last_signal", "")).strip()
        parked_waiting_state = candidate_signal if candidate_signal in PARKED_IDLE_SIGNALS else ""
    if parked_waiting_state not in PARKED_IDLE_SIGNALS:
        return False, "", "", 0
    if session_has_live_task(config, normalized_session_id):
        return False, "", "", 0
    evidence_token = continuous_research_session_evidence_token(config, normalized_session_id, spec=spec)
    repeat_count = next_parked_idle_repeat_count(session_state, evidence_token=evidence_token)
    return True, parked_waiting_state, evidence_token, repeat_count


def guard_parked_waiting_signal_without_live_task(
    config: AppConfig,
    *,
    session_id: str,
    spec: dict[str, Any],
    parked_waiting_state: str,
    evidence_token: str,
    repeat_count: int,
    updated_by: str,
    source: str,
) -> None:
    park_continuous_research_session(
        config,
        codex_session_id=session_id,
        waiting_state=parked_waiting_state,
        waiting_reason="guarded_invalid_waiting_signal_without_live_task",
        evidence_token=evidence_token,
        last_signal=parked_waiting_state,
        stable_idle_repeat_count=repeat_count,
        updated_by=updated_by,
        source=source,
    )


def process_single_followup(config: AppConfig, followup: dict[str, Any]) -> list[dict[str, Any]]:
    followup = normalize_timestamp_fields(followup)
    processed: list[dict[str, Any]] = []
    followup_key = str(followup.get("followup_key", ""))
    if not followup_key:
        return processed
    followup_type = str(followup.get("followup_type", "")).strip()
    is_queued_feedback = followup_type == "queued_feedback_resume"
    is_continuous_session_reminder = followup_type == CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE
    is_continuous_transition = followup_type == CONTINUOUS_RESEARCH_TRANSITION_FOLLOWUP_TYPE
    followup_reason = str(followup.get("reason", "")).strip()
    continuous_origin = (
        not is_queued_feedback
        and not is_continuous_session_reminder
        and not is_continuous_transition
        and parse_boolish(followup.get("continuous_research_origin", False), default=False)
    )
    is_continuous_followup = (
        not is_queued_feedback
        and not is_continuous_session_reminder
        and not is_continuous_transition
        and (
            followup_reason in {CONTINUOUS_RESEARCH_REASON, CONTINUOUS_RESEARCH_IDLE_REASON}
            or continuous_origin
        )
    )
    if bool(followup.get("stopped", False)):
        sync_followup_state(
            config,
            followup,
            followup_status="stopped",
            followup_last_action="resolved_stopped",
            pending_feedback=False if is_queued_feedback else None,
        )
        resolve_followup(config, followup_key)
        processed.append({"followup_key": followup_key, "action": "resolved_stopped"})
        return processed
    if is_queued_feedback and newer_task_exists(config, followup):
        sync_followup_state(
            config,
            followup,
            followup_status="resolved",
            followup_last_action="resolved_new_task_seen",
            pending_feedback=False,
        )
        resolve_followup(config, followup_key)
        processed.append({"followup_key": followup_key, "action": "resolved_new_task_seen"})
        return processed
    followup, rebound, skip_processing = rebind_followup_to_current_task(config, followup)
    followup_key = str(followup.get("followup_key", "")).strip()
    if skip_processing:
        processed.append({"followup_key": followup_key, "action": "resolved_rebound_to_existing"})
        return processed
    source_state = load_task_state(config, str(followup.get("task_id", "")))
    session_id = str(followup.get("codex_session_id", "")).strip()
    if is_continuous_session_reminder and session_id and not continuous_research_mode_enabled(config, codex_session_id=session_id):
        sync_followup_state(
            config,
            followup,
            followup_status="resolved",
            followup_last_action="resolved_continuous_mode_disabled",
        )
        resolve_followup(config, followup_key)
        processed.append({"followup_key": followup_key, "action": "resolved_continuous_mode_disabled"})
        return processed
    if source_state and str(source_state.get("feedback_mode", "auto")) == "off" and not is_continuous_session_reminder:
        sync_followup_state(
            config,
            followup,
            followup_status="stopped",
            followup_last_action="resolved_feedback_off",
            followup_stopped_at=utc_now(),
            pending_feedback=False if is_queued_feedback else None,
        )
        resolve_followup(config, followup_key)
        processed.append({"followup_key": followup_key, "action": "resolved_feedback_off"})
        return processed
    if not is_continuous_session_reminder and newer_task_exists(config, followup):
        sync_followup_state(
            config,
            followup,
            followup_status="resolved",
            followup_last_action="resolved_new_task_seen",
            pending_feedback=False if is_queued_feedback else None,
        )
        resolve_followup(config, followup_key)
        processed.append({"followup_key": followup_key, "action": "resolved_new_task_seen"})
        return processed
    if time.time() < float(followup.get("check_after_ts", 0)):
        if rebound:
            processed.append({"followup_key": followup_key, "action": "rebound_session_binding"})
        return processed
    min_idle_seconds = int(followup.get("min_idle_seconds", 600))
    if session_id and automation_mode_is_managed(config, codex_session_id=session_id):
        defer_followup_retry(
            config,
            followup,
            reason="managed_mode_pause",
            retry_after_seconds=DEFAULT_WAITING_ON_ASYNC_INTERVAL_SECONDS,
            message_path=str(followup_message_path(config, followup_key)),
        )
        for task_id in followup_task_ids(followup):
            merge_task_state(config, task_id, session_flow_state="managed_backlog")
        processed.append({"followup_key": followup_key, "action": "deferred_managed_mode_pause"})
        return processed
    if is_continuous_followup and session_id and session_has_live_task(config, session_id):
        refresh_continuous_session_for_live_tasks(
            config,
            session_id,
            updated_by="followup",
            source="process-followups-live-task",
        )
        defer_followup_retry(
            config,
            followup,
            reason="session_has_running_task",
            retry_after_seconds=DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS,
            message_path=str(followup_message_path(config, followup_key)),
        )
        processed.append({"followup_key": followup_key, "action": "deferred_session_has_running_task"})
        return processed
    if is_continuous_session_reminder and session_id:
        if session_has_live_task(config, session_id):
            refresh_continuous_session_for_live_tasks(
                config,
                session_id,
                updated_by="followup",
                source="process-followups-session-reminder-live-task",
            )
            defer_followup_retry(
                config,
                followup,
                reason="session_has_running_task",
                retry_after_seconds=DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS,
                message_path=str(followup_message_path(config, followup_key)),
            )
            processed.append({"followup_key": followup_key, "action": "deferred_session_has_running_task"})
            return processed
        if session_has_other_live_followup(config, session_id, exclude_followup_key=followup_key):
            defer_followup_retry(
                config,
                followup,
                reason="session_has_other_followup",
                retry_after_seconds=DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS,
                message_path=str(followup_message_path(config, followup_key)),
            )
            processed.append({"followup_key": followup_key, "action": "deferred_session_has_other_followup"})
            return processed
    output_busy = session_output_busy_snapshot(config, session_id, spec=followup) if session_id else {"busy": False}
    if output_busy.get("busy", False):
        defer_followup_retry(
            config,
            followup,
            reason="session_output_busy",
            retry_after_seconds=int(output_busy.get("retry_after_seconds", DEFAULT_SESSION_OUTPUT_BUSY_RETRY_SECONDS) or DEFAULT_SESSION_OUTPUT_BUSY_RETRY_SECONDS),
            message_path=str(followup_message_path(config, followup_key)),
        )
        processed.append({"followup_key": followup_key, "action": "deferred_session_output_busy"})
        return processed
    last_activity_ts = latest_session_activity_ts(config, session_id, followup) if session_id else 0.0
    if last_activity_ts and time.time() - last_activity_ts < min_idle_seconds:
        defer_followup_retry(
            config,
            followup,
            reason="recent_activity",
            retry_after_seconds=retry_after_seconds_from_target(last_activity_ts + min_idle_seconds),
            message_path=str(followup_message_path(config, followup_key)),
        )
        processed.append({"followup_key": followup_key, "action": "deferred_recent_activity"})
        return processed
    followup, rebound, skip_processing = rebind_followup_to_current_task(config, followup)
    followup_key = str(followup.get("followup_key", "")).strip()
    if skip_processing:
        processed.append({"followup_key": followup_key, "action": "resolved_rebound_to_existing"})
        return processed
    spec_for_resume = build_followup_resume_spec_from_payload(followup)
    task_id = normalize_task_id(str(followup.get("task_id", "")).strip())
    entry_count = 0
    if is_queued_feedback:
        queued_notifications = queued_notification_entries(followup)
        entry_count = len(queued_notifications)
        if entry_count == 0:
            sync_followup_state(
                config,
                followup,
                followup_status="resolved",
                followup_last_action="resolved_empty_queue",
                pending_feedback=False,
            )
            resolve_followup(config, followup_key)
            processed.append({"followup_key": followup_key, "action": "resolved_empty_queue"})
            return processed
        if entry_count == 1:
            prompt = str(queued_notifications[0].get("prompt", "")).strip()
        else:
            combined = build_queued_feedback_batch_prompt(
                spec_for_resume,
                queued_notifications,
                continuous_research_enabled=continuous_research_mode_enabled(
                    config,
                    codex_session_id=str(spec_for_resume.get("codex_session_id", "")).strip(),
                ),
            )
            max_chars = max(int(followup.get("prompt_max_chars", 12000) or 12000), 12000)
            max_chars = min(max_chars * entry_count, 24000)
            prompt = combined if len(combined) <= max_chars else combined[: max_chars - 1]
    else:
        continuous_research_enabled = continuous_research_mode_enabled(
            config,
            codex_session_id=str(spec_for_resume.get("codex_session_id", "")).strip(),
        )
        reason = followup_reason
        if followup_type == PROTOCOL_SELF_CHECK_REPAIR_FOLLOWUP_TYPE:
            prompt = build_protocol_self_check_repair_prompt(
                spec_for_resume,
                followup,
                continuous_research_enabled=continuous_research_enabled,
            )
        elif is_continuous_transition:
            prompt = build_continuous_transition_prompt(
                spec_for_resume,
                trigger_signal=str(followup.get("last_signal", "") or ""),
            )
        elif (
            is_continuous_followup
            or is_continuous_session_reminder
        ) and continuous_research_enabled:
            prompt = build_continuous_research_prompt(
                spec_for_resume,
                trigger_signal=str(followup.get("last_signal", "") or ""),
            )
        else:
            prompt = build_standard_followup_prompt(
                spec_for_resume,
                continuous_research_enabled=continuous_research_enabled,
            )
    append_followup_event_log(
        config,
        event="resumed",
        reason=followup_reason,
        followup=followup,
        task_id=task_id,
        detail="process_followups_resume_attempt",
    )
    result = resume_codex_session_with_prompt(
        config,
        spec_for_resume,
        prompt,
        output_last_message_path=str(followup_message_path(config, followup_key)),
        log_path=followup_log_path(config),
        min_idle_seconds=min_idle_seconds,
        feedback_source_kind=(
            "queued_feedback_followup"
            if is_queued_feedback
            else "continuous_session_followup"
            if is_continuous_session_reminder or is_continuous_transition
            else "standard_followup"
        ),
        feedback_source_key=followup_key,
        feedback_task_id=task_id,
        feedback_task_ids=followup_task_ids(followup),
        feedback_followup_key=followup_key,
    )
    if result.get("deferred", False):
        reason = str(result.get("deferred_reason", "") or "resume_deferred")
        retry_after_seconds = int(result.get("retry_after_seconds", default_retry_delay_seconds(min_idle_seconds)) or default_retry_delay_seconds(min_idle_seconds))
        defer_followup_retry(
            config,
            followup,
            reason=reason,
            retry_after_seconds=retry_after_seconds,
            message_path=str(followup_message_path(config, followup_key)),
        )
        attention_updates = platform_attention_updates_from_result(result)
        if attention_updates:
            for queued_task_id in followup_task_ids(followup):
                merge_task_state(config, queued_task_id, **attention_updates)
        processed.append({"followup_key": followup_key, "action": f"deferred:{reason}"})
        return processed
    completed = result.get("completed")
    message_text = str(result.get("last_message_text", "") or "")
    msg_path = followup_message_path(config, followup_key)
    if not message_text and msg_path.exists():
        message_text = msg_path.read_text(encoding="utf-8", errors="ignore")
    if completed is not None:
        stdout_tail = str(getattr(completed, "stdout", "") or "")
        stderr_tail = str(getattr(completed, "stderr", "") or "")
    else:
        stdout_tail = str(result.get("stdout_tail", "") or "")
        stderr_tail = str(result.get("stderr_tail", "") or "")
    signal_value = extract_taskboard_signal(message_text or f"{stdout_tail}\n{stderr_tail}")
    processed.append(
        {
            "followup_key": followup_key,
            "action": "queued_feedback_attempted" if is_queued_feedback else "nudged",
            "signal": signal_value,
            "continue_attempts": result.get("continue_attempts", 0),
        }
    )
    protocol_footer = result.get("taskboard_protocol", {}) if isinstance(result.get("taskboard_protocol", {}), dict) else {}
    protocol_issue = summarize_taskboard_protocol_issue(protocol_footer, signal_value=signal_value)
    if is_continuous_transition:
        transition_session_id = str(followup.get("codex_session_id", "")).strip()
        if transition_session_id and result.get("ok", False):
            clear_continuous_research_session_waiting_state(
                config,
                codex_session_id=transition_session_id,
                evidence_token=continuous_research_session_evidence_token(config, transition_session_id, spec=spec_for_resume),
                last_signal=signal_value,
                updated_by="followup",
                source="continuous-transition",
            )
        if signal_value == CONTINUOUS_RESEARCH_NEW_TASK_SIGNAL and result.get("ok", False):
            sync_followup_state(
                config,
                followup,
                followup_status="resolved",
                followup_last_action="resolved_continuous_transition_new_tasks_started",
                followup_last_signal=signal_value,
                notification_signal=signal_value,
                message_path=str(msg_path),
            )
            if task_id:
                merge_task_state(
                    config,
                    task_id,
                    pending_feedback=False,
                    notification_ok=True,
                    notification_signal=signal_value,
                    notification_finished_at=result.get("finished_at"),
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="resolved_continuous_transition_new_tasks_started",
                    followup_last_message_path=str(msg_path),
                )
            resolve_followup(config, followup_key)
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "resolved_continuous_transition_new_tasks_started",
                    "signal": signal_value,
                }
            )
            return processed
        if signal_value == "none" and result.get("ok", False):
            bootstrap = bootstrap_successor_session_after_closeout(
                config,
                task_id=task_id,
                spec=spec_for_resume,
                predecessor_session_id=transition_session_id or str(spec_for_resume.get("codex_session_id", "")).strip(),
                resolve_followup_key=followup_key,
                trigger_signal=signal_value,
                updated_by="followup",
                source="continuous-transition-closeout-none",
            )
            if not bootstrap.get("ok", False):
                defer_followup_retry(
                    config,
                    followup,
                    reason=str(bootstrap.get("deferred_reason", "") or "successor_bootstrap_failed"),
                    retry_after_seconds=int(
                        followup.get("interval_seconds", DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS)
                        or DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS
                    ),
                    message_path=str(msg_path),
                )
                processed.append(
                    {
                        "followup_key": followup_key,
                        "action": "continuous_transition_successor_bootstrap_retry_scheduled",
                        "signal": signal_value,
                    }
                )
                return processed
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": str(bootstrap.get("action", "") or "continuous_transition_successor_bootstrapped"),
                    "signal": signal_value,
                    "successor_session_id": bootstrap.get("successor_session_id", ""),
                    "successor_signal": bootstrap.get("taskboard_signal", ""),
                }
            )
            return processed
        if not result.get("ok", False):
            defer_followup_retry(
                config,
                followup,
                reason=str(result.get("deferred_reason", "") or "resume_failed"),
                retry_after_seconds=int(followup.get("interval_seconds", DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS) or DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS),
                message_path=str(msg_path),
            )
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "continuous_transition_retry_scheduled",
                    "signal": signal_value,
                }
            )
            return processed
        if task_id:
            schedule_continuous_transition_followup(
                config,
                task_id=task_id,
                spec=spec_for_resume,
                trigger_signal=signal_value,
                message_path=str(msg_path),
                followup=followup,
            )
            merge_task_state(
                config,
                task_id,
                notification_signal=signal_value,
                followup_status="scheduled",
                followup_last_signal=signal_value,
                followup_last_action=f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}",
                followup_stopped_at="",
                followup_last_message_path=str(msg_path),
            )
        processed.append(
            {
                "followup_key": followup_key,
                "action": "continuous_transition_rescheduled",
                "signal": signal_value,
            }
        )
        return processed
    needs_protocol_repair = result.get("ok", False) and taskboard_protocol_requires_repair(protocol_footer, signal_value=signal_value)
    if needs_protocol_repair and task_id and should_schedule_followup_for_spec(spec_for_resume):
        if is_queued_feedback:
            session_id = str(followup.get("codex_session_id", "")).strip()
            evidence_token = continuous_research_session_evidence_token(config, session_id, spec=spec_for_resume) if session_id else ""
            if session_id:
                clear_continuous_research_session_waiting_state(
                    config,
                    codex_session_id=session_id,
                    evidence_token=evidence_token,
                    last_signal=signal_value,
                    stable_idle_repeat_count=1,
                    updated_by="followup",
                    source="queued-feedback-local-microstep",
                )
            delivered_summary = {
                "ok": result.get("ok", False),
                "deferred": True,
                "deferred_reason": str(followup.get("reason", "")),
                "delivered_from_queue": True,
                "queue_depth": entry_count,
                "original_session_id": result.get("original_session_id"),
                "resumed_session_id": result.get("resumed_session_id"),
                "used_fallback_clone": result.get("used_fallback_clone", False),
                "fallback_provider": result.get("fallback_provider", ""),
                "taskboard_signal": signal_value,
                "continue_attempts": result.get("continue_attempts", 0),
                "recovered_with_continue": result.get("recovered_with_continue", False),
                "protocol_repair_scheduled": True,
                "protocol_issue": protocol_issue,
            }
            for queued_task_id in followup_task_ids(followup):
                merge_task_state(
                    config,
                    queued_task_id,
                    pending_feedback=False,
                    notification_ok=result.get("ok", False),
                    notification_signal=signal_value,
                    resumed_session_id=result.get("resumed_session_id", followup.get("codex_session_id", "")),
                    used_fallback_clone=result.get("used_fallback_clone", False),
                    notification_finished_at=result.get("finished_at"),
                    notification_summary=delivered_summary,
                    followup_status="scheduled",
                    followup_last_signal=signal_value,
                    followup_last_action=f"scheduled:{PROTOCOL_SELF_CHECK_REPAIR_REASON}",
                    followup_last_message_path=str(msg_path),
                )
            schedule_protocol_self_check_repair(
                config,
                task_id=task_id,
                spec=spec_for_resume,
                issue_summary=protocol_issue,
                protocol_footer=protocol_footer,
                observed_signal=signal_value,
                message_path=str(msg_path),
            )
            resolve_followup(config, followup_key)
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "queued_feedback_protocol_self_check_repair_scheduled",
                    "queue_depth": entry_count,
                    "signal": signal_value,
                    "protocol_issue": protocol_issue,
                }
            )
            return processed
        schedule_protocol_self_check_repair(
            config,
            task_id=task_id,
            spec=spec_for_resume,
            issue_summary=protocol_issue,
            protocol_footer=protocol_footer,
            observed_signal=signal_value,
            followup=followup,
            message_path=str(msg_path),
        )
        processed.append(
            {
                "followup_key": followup_key,
                "action": "protocol_self_check_repair_scheduled",
                "signal": signal_value,
                "protocol_issue": protocol_issue,
            }
        )
        return processed
    if signal_value == "none" and result.get("ok", False):
        if is_queued_feedback:
            for queued_task_id in followup_task_ids(followup):
                merge_task_state(
                    config,
                    queued_task_id,
                    pending_feedback=False,
                    notification_ok=result.get("ok", False),
                    notification_signal=signal_value,
                    resumed_session_id=result.get("resumed_session_id", followup.get("codex_session_id", "")),
                    used_fallback_clone=result.get("used_fallback_clone", False),
                    notification_finished_at=result.get("finished_at"),
                    notification_summary={
                        "ok": result.get("ok", False),
                        "deferred": True,
                        "deferred_reason": str(followup.get("reason", "")),
                        "delivered_from_queue": True,
                        "queue_depth": entry_count,
                        "original_session_id": result.get("original_session_id"),
                        "resumed_session_id": result.get("resumed_session_id"),
                        "used_fallback_clone": result.get("used_fallback_clone", False),
                        "fallback_provider": result.get("fallback_provider", ""),
                        "taskboard_signal": signal_value,
                        "continue_attempts": result.get("continue_attempts", 0),
                        "recovered_with_continue": result.get("recovered_with_continue", False),
                    },
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="queued_feedback_delivered_none",
                    followup_last_message_path=str(msg_path),
                )
            resolve_followup(config, followup_key)
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "queued_feedback_delivered_none",
                    "queue_depth": entry_count,
                    "signal": signal_value,
                }
            )
            return processed
        sync_followup_state(
            config,
            followup,
            followup_status="resolved",
            followup_last_action="resolved_signal_none",
            followup_last_signal=signal_value,
            followup_stopped_at=utc_now(),
            notification_signal=signal_value,
            message_path=str(msg_path),
        )
        resolve_followup(config, followup_key)
        if task_id:
            merge_task_state(
                config,
                task_id,
                followup_status="resolved",
                followup_last_signal=signal_value,
                followup_last_action="resolved_signal_none",
                followup_stopped_at=utc_now(),
                followup_last_message_path=str(msg_path),
                notification_signal=signal_value,
            )
        processed.append(
            {
                "followup_key": followup_key,
                "action": "resolved_signal_none",
                "signal": signal_value,
            }
        )
        return processed
    if signal_value in PARKED_IDLE_SIGNALS and result.get("ok", False):
        session_id = str(followup.get("codex_session_id", "")).strip()
        evidence_token = continuous_research_session_evidence_token(
            config,
            session_id,
            spec=spec_for_resume,
        )
        repeat_count = CONTINUOUS_RESEARCH_IDLE_LOOP_THRESHOLD
        immediate_watchdog_scheduled = False
        if session_id:
            session_state = continuous_research_session_state(config, session_id)
            repeat_count = next_parked_idle_repeat_count(session_state, evidence_token=evidence_token)
            park_continuous_research_session(
                config,
                codex_session_id=session_id,
                waiting_state=signal_value,
                waiting_reason="agent_requested_parked_idle",
                evidence_token=evidence_token,
                last_signal=signal_value,
                stable_idle_repeat_count=repeat_count,
                updated_by="followup",
                source="followup-parked-idle",
            )
            if should_schedule_immediate_parked_watchdog(
                config,
                session_id=session_id,
                spec=spec_for_resume,
                repeat_count=repeat_count,
            ):
                schedule_immediate_parked_watchdog(
                    config,
                    session_id=session_id,
                    spec=spec_for_resume,
                    signal_value=signal_value,
                )
                immediate_watchdog_scheduled = True
        if is_queued_feedback:
            for queued_task_id in followup_task_ids(followup):
                merge_task_state(
                    config,
                    queued_task_id,
                    pending_feedback=False,
                    notification_ok=result.get("ok", False),
                    notification_signal=signal_value,
                    resumed_session_id=result.get("resumed_session_id", followup.get("codex_session_id", "")),
                    used_fallback_clone=result.get("used_fallback_clone", False),
                    notification_finished_at=result.get("finished_at"),
                    notification_summary={
                        "ok": result.get("ok", False),
                        "taskboard_signal": signal_value,
                        "session_flow_state": "parked_idle",
                        "waiting_evidence_token": evidence_token,
                        "immediate_parked_watchdog_scheduled": immediate_watchdog_scheduled,
                    },
                    session_flow_state="parked_idle",
                    followup_status="scheduled" if immediate_watchdog_scheduled else "resolved",
                    followup_last_signal=signal_value,
                    followup_last_action=(
                        f"scheduled:{CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON}"
                        if immediate_watchdog_scheduled
                        else "resolved_parked_idle"
                    ),
                    followup_last_message_path=str(msg_path),
                )
            if live_task_present and session_id:
                clear_continuous_research_session_waiting_state(
                    config,
                    codex_session_id=session_id,
                    evidence_token=continuous_research_session_evidence_token(config, session_id, spec=spec_for_resume),
                    last_signal=signal_value,
                    updated_by="followup",
                    source="followup-queued-feedback-waiting-on-async-live-task",
                )
            resolve_followup(config, followup_key)
        else:
            sync_followup_state(
                config,
                followup,
                followup_status="scheduled" if immediate_watchdog_scheduled else "resolved",
                followup_last_action=(
                    f"scheduled:{CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON}"
                    if immediate_watchdog_scheduled
                    else "resolved_parked_idle"
                ),
                followup_last_signal=signal_value,
                notification_signal=signal_value,
                message_path=str(msg_path),
            )
            scheduled_on_same_followup = bool(
                immediate_watchdog_scheduled
                and is_continuous_session_reminder
                and session_id
                and followup_key == continuous_session_followup_key_for(session_id)
            )
            if not scheduled_on_same_followup:
                resolve_followup(config, followup_key)
            if task_id:
                merge_task_state(
                    config,
                    task_id,
                    session_flow_state="parked_idle",
                    followup_status="scheduled" if immediate_watchdog_scheduled else "resolved",
                    followup_last_signal=signal_value,
                    followup_last_action=(
                        f"scheduled:{CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON}"
                        if immediate_watchdog_scheduled
                        else "resolved_parked_idle"
                    ),
                    followup_last_message_path=str(msg_path),
                    notification_signal=signal_value,
                )
        processed.append(
            {
                "followup_key": followup_key,
                "action": "scheduled_immediate_parked_watchdog" if immediate_watchdog_scheduled else "resolved_parked_idle",
                "signal": signal_value,
                "waiting_evidence_token": evidence_token,
            }
        )
        return processed
    if signal_value in STOP_FOLLOWUP_SIGNALS and not should_override_stop_signal_with_continuous_research(
        config,
        signal_value,
        codex_session_id=str(followup.get("codex_session_id", "")).strip(),
    ):
        if is_queued_feedback:
            for queued_task_id in followup_task_ids(followup):
                if not queued_task_id:
                    continue
                merge_task_state(
                    config,
                    queued_task_id,
                    pending_feedback=False,
                    notification_signal=signal_value,
                    followup_status="stopped",
                    followup_last_signal=signal_value,
                    followup_last_action="resolved_signal_stop",
                    followup_stopped_at=utc_now(),
                    followup_last_message_path=str(msg_path),
                )
        if task_id:
            merge_task_state(
                config,
                task_id,
                followup_status="stopped",
                followup_last_signal=signal_value,
                followup_last_action="resolved_signal_stop",
                followup_stopped_at=utc_now(),
                followup_last_message_path=str(msg_path),
                notification_signal=signal_value,
            )
        resolved_keys = resolve_followups_for_stop_signal(
            config,
            session_id=str(followup.get("codex_session_id", "")).strip(),
            agent_name=str(followup.get("agent_name", "")).strip(),
            signal_value=signal_value,
            reason="resolved_signal_stop",
            message_path=str(msg_path),
        )
        processed.append({"followup_key": followup_key, "action": "resolved_signal_stop", "resolved_keys": resolved_keys})
        return processed
    if signal_value in CONTINUOUS_RESEARCH_OVERRIDE_SIGNALS and should_override_stop_signal_with_continuous_research(
        config,
        signal_value,
        codex_session_id=str(followup.get("codex_session_id", "")).strip(),
    ):
        if is_queued_feedback:
            for queued_task_id in followup_task_ids(followup):
                merge_task_state(
                    config,
                    queued_task_id,
                    pending_feedback=False,
                    notification_ok=result.get("ok", False),
                    notification_signal=signal_value,
                    resumed_session_id=result.get("resumed_session_id", followup.get("codex_session_id", "")),
                    used_fallback_clone=result.get("used_fallback_clone", False),
                    notification_finished_at=result.get("finished_at"),
                    notification_summary={
                        "ok": result.get("ok", False),
                        "continuous_research_mode": True,
                        "continuous_override_signal": signal_value,
                        "delivered_from_queue": True,
                        "queue_depth": entry_count,
                        "original_session_id": result.get("original_session_id"),
                        "resumed_session_id": result.get("resumed_session_id"),
                        "used_fallback_clone": result.get("used_fallback_clone", False),
                        "fallback_provider": result.get("fallback_provider", ""),
                        "taskboard_signal": signal_value,
                        "continue_attempts": result.get("continue_attempts", 0),
                        "recovered_with_continue": result.get("recovered_with_continue", False),
                    },
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="queued_feedback_delivered_continuous_override",
                    followup_last_message_path=str(msg_path),
                )
            if result.get("ok", False) and task_id and should_schedule_followup_for_spec(spec_for_resume):
                schedule_continuous_transition_followup(
                    config,
                    task_id=task_id,
                    spec=spec_for_resume,
                    trigger_signal=signal_value,
                    message_path=str(msg_path),
                )
            resolve_followup(config, followup_key)
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "continuous_transition_scheduled",
                    "queue_depth": entry_count,
                    "signal": signal_value,
                }
            )
            return processed
        if is_continuous_session_reminder:
            followup["check_after_ts"] = time.time() + DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS
            followup["last_signal"] = signal_value
            followup["last_action"] = f"scheduled:{CONTINUOUS_RESEARCH_IDLE_REASON}"
            updated_at = utc_now()
            followup["last_checked_at"] = updated_at
            followup["updated_at"] = updated_at
            atomic_write_json(followup_path(config, followup_key), followup)
            append_followup_event_log(
                config,
                event="scheduled",
                reason=CONTINUOUS_RESEARCH_IDLE_REASON,
                followup=followup,
                detail="continuous_session_reminder_rescheduled",
            )
            if task_id:
                merge_task_state(
                    config,
                    task_id,
                    notification_signal=signal_value,
                    followup_status="scheduled",
                    followup_last_signal=signal_value,
                    followup_last_action=f"scheduled:{CONTINUOUS_RESEARCH_IDLE_REASON}",
                    followup_last_message_path=str(msg_path),
                )
            processed.append({"followup_key": followup_key, "action": "continuous_session_reminder_rescheduled", "signal": signal_value})
            return processed
        if task_id and should_schedule_followup_for_spec(spec_for_resume):
            schedule_continuous_transition_followup(
                config,
                task_id=task_id,
                spec=spec_for_resume,
                trigger_signal=signal_value,
                message_path=str(msg_path),
                followup=followup,
            )
        else:
            followup["check_after_ts"] = time.time() + DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS
            followup["last_signal"] = signal_value
            followup["last_action"] = f"deferred:{CONTINUOUS_RESEARCH_TRANSITION_REASON}"
            updated_at = utc_now()
            followup["last_checked_at"] = updated_at
            followup["updated_at"] = updated_at
            atomic_write_json(followup_path(config, followup_key), followup)
            append_followup_event_log(
                config,
                event="deferred",
                reason=CONTINUOUS_RESEARCH_TRANSITION_REASON,
                followup=followup,
                detail="continuous_transition_deferred_without_task_binding",
            )
        if task_id:
            merge_task_state(
                config,
                task_id,
                notification_signal=signal_value,
                followup_status="scheduled",
                followup_last_signal=signal_value,
                followup_last_action=f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}",
                followup_last_message_path=str(msg_path),
            )
        processed.append({"followup_key": followup_key, "action": "continuous_transition_scheduled", "signal": signal_value})
        return processed
    if signal_value in INLINE_CONTINUE_SIGNALS and result.get("ok", False):
        session_id = str(followup.get("codex_session_id", "")).strip()
        evidence_token = clear_waiting_state_for_inline_continue(
            config,
            session_id=session_id,
            spec=spec_for_resume,
            signal_value=signal_value,
            updated_by="followup",
            source="followup-inline-continue",
        )
        if is_queued_feedback:
            delivered_summary = {
                "ok": result.get("ok", False),
                "deferred": True,
                "deferred_reason": str(followup.get("reason", "")),
                "delivered_from_queue": True,
                "queue_depth": entry_count,
                "original_session_id": result.get("original_session_id"),
                "resumed_session_id": result.get("resumed_session_id"),
                "used_fallback_clone": result.get("used_fallback_clone", False),
                "fallback_provider": result.get("fallback_provider", ""),
                "taskboard_signal": signal_value,
                "continue_attempts": result.get("continue_attempts", 0),
                "recovered_with_continue": result.get("recovered_with_continue", False),
                "session_flow_state": "inline_continue",
            }
            for queued_task_id in followup_task_ids(followup):
                merge_task_state(
                    config,
                    queued_task_id,
                    pending_feedback=False,
                    notification_ok=result.get("ok", False),
                    notification_signal=signal_value,
                    resumed_session_id=result.get("resumed_session_id", followup.get("codex_session_id", "")),
                    used_fallback_clone=result.get("used_fallback_clone", False),
                    notification_finished_at=result.get("finished_at"),
                    notification_summary=delivered_summary,
                    session_flow_state="inline_continue",
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="queued_feedback_delivered_inline_continue_no_wake",
                    followup_last_message_path=str(msg_path),
                )
            if task_id:
                merge_task_state(
                    config,
                    task_id,
                    session_flow_state="inline_continue",
                    notification_signal=signal_value,
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="resolved_inline_continue_no_wake",
                    followup_stopped_at=utc_now(),
                    followup_last_message_path=str(msg_path),
                )
            resolve_followup(config, followup_key)
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "queued_feedback_delivered_inline_continue_no_wake",
                    "queue_depth": entry_count,
                    "signal": signal_value,
                    "waiting_evidence_token": evidence_token,
                }
            )
            return processed
        sync_followup_state(
            config,
            followup,
            followup_status="resolved",
            followup_last_action="resolved_inline_continue_no_wake",
            followup_last_signal=signal_value,
            followup_stopped_at=utc_now(),
            notification_signal=signal_value,
            message_path=str(msg_path),
        )
        resolve_followup(config, followup_key)
        if task_id:
            merge_task_state(
                config,
                task_id,
                session_flow_state="inline_continue",
                followup_status="resolved",
                followup_last_signal=signal_value,
                followup_last_action="resolved_inline_continue_no_wake",
                followup_stopped_at=utc_now(),
                followup_last_message_path=str(msg_path),
                notification_signal=signal_value,
            )
        processed.append(
            {
                "followup_key": followup_key,
                "action": "inline_continue_no_wake",
                "signal": signal_value,
                "waiting_evidence_token": evidence_token,
            }
        )
        return processed
    if signal_value in LOCAL_MICROSTEP_BATCH_SIGNALS and result.get("ok", False):
        if signal_value == MATERIALS_READY_FOR_PROPOSAL_SIGNAL:
            session_id = str(followup.get("codex_session_id", "")).strip()
            evidence_token = continuous_research_session_evidence_token(config, session_id, spec=spec_for_resume) if session_id else ""
            if session_id:
                clear_continuous_research_session_waiting_state(
                    config,
                    codex_session_id=session_id,
                    evidence_token=evidence_token,
                    last_signal=signal_value,
                    updated_by="followup",
                    source=PROPOSAL_MATERIALIZATION_REASON,
                )
            if is_queued_feedback:
                for queued_task_id in followup_task_ids(followup):
                    merge_task_state(
                        config,
                        queued_task_id,
                        research_phase="closeout",
                        pending_feedback=False,
                        notification_ok=result.get("ok", False),
                        notification_signal=signal_value,
                        resumed_session_id=result.get("resumed_session_id", followup.get("codex_session_id", "")),
                        used_fallback_clone=result.get("used_fallback_clone", False),
                        notification_finished_at=result.get("finished_at"),
                        notification_summary={
                            "ok": result.get("ok", False),
                            "research_phase": "closeout",
                            "taskboard_signal": signal_value,
                            "session_flow_state": "proposal_materialization",
                        },
                        session_flow_state="proposal_materialization",
                        followup_status="resolved",
                        followup_last_signal=signal_value,
                        followup_last_action="queued_feedback_delivered_proposal_materialization",
                        followup_last_message_path=str(msg_path),
                    )
                if task_id and should_schedule_followup_for_spec(spec_for_resume):
                    schedule_continuous_transition_followup(
                        config,
                        task_id=task_id,
                        spec=spec_for_resume,
                        trigger_signal=signal_value,
                        message_path=str(msg_path),
                    )
                    merge_task_state(
                        config,
                        task_id,
                        research_phase="closeout",
                        session_flow_state="proposal_materialization",
                        notification_signal=signal_value,
                        followup_status="scheduled",
                        followup_last_signal=signal_value,
                        followup_last_action=f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}",
                        followup_stopped_at="",
                        followup_last_message_path=str(msg_path),
                    )
                resolve_followup(config, followup_key)
                processed.append(
                    {
                        "followup_key": followup_key,
                        "action": "queued_feedback_delivered_proposal_materialization",
                        "queue_depth": entry_count,
                        "signal": signal_value,
                    }
                )
                return processed
            if task_id and should_schedule_followup_for_spec(spec_for_resume):
                schedule_continuous_transition_followup(
                    config,
                    task_id=task_id,
                    spec=spec_for_resume,
                    trigger_signal=signal_value,
                    message_path=str(msg_path),
                    followup=followup,
                )
                merge_task_state(
                    config,
                    task_id,
                    research_phase="closeout",
                    session_flow_state="proposal_materialization",
                    notification_signal=signal_value,
                    followup_status="scheduled",
                    followup_last_signal=signal_value,
                    followup_last_action=f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}",
                    followup_stopped_at="",
                    followup_last_message_path=str(msg_path),
                )
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "proposal_materialization_followup_scheduled",
                    "signal": signal_value,
                }
            )
            return processed
        if is_queued_feedback:
            session_id = str(followup.get("codex_session_id", "")).strip()
            evidence_token = continuous_research_session_evidence_token(config, session_id, spec=spec_for_resume) if session_id else ""
            if session_id:
                clear_continuous_research_session_waiting_state(
                    config,
                    codex_session_id=session_id,
                    evidence_token=evidence_token,
                    last_signal=signal_value,
                    updated_by="followup",
                    source="queued-feedback-waiting-on-async",
                )
            delivered_summary = {
                "ok": result.get("ok", False),
                "deferred": True,
                "deferred_reason": str(followup.get("reason", "")),
                "delivered_from_queue": True,
                "queue_depth": entry_count,
                "original_session_id": result.get("original_session_id"),
                "resumed_session_id": result.get("resumed_session_id"),
                "used_fallback_clone": result.get("used_fallback_clone", False),
                "fallback_provider": result.get("fallback_provider", ""),
                "taskboard_signal": signal_value,
                "continue_attempts": result.get("continue_attempts", 0),
                "recovered_with_continue": result.get("recovered_with_continue", False),
                "session_flow_state": "local_active",
            }
            for queued_task_id in followup_task_ids(followup):
                merge_task_state(
                    config,
                    queued_task_id,
                    pending_feedback=False,
                    notification_ok=result.get("ok", False),
                    notification_signal=signal_value,
                    resumed_session_id=result.get("resumed_session_id", followup.get("codex_session_id", "")),
                    used_fallback_clone=result.get("used_fallback_clone", False),
                    notification_finished_at=result.get("finished_at"),
                    notification_summary=delivered_summary,
                    session_flow_state="local_active",
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="queued_feedback_delivered_local_microstep",
                    followup_last_message_path=str(msg_path),
                )
            local_followup_scheduled = False
            if task_id and should_schedule_followup_for_spec(spec_for_resume):
                local_followup_scheduled = schedule_local_microstep_followup(
                    config,
                    task_id=task_id,
                    spec=spec_for_resume,
                )
                merge_task_state(
                    config,
                    task_id,
                    session_flow_state="local_active",
                    notification_signal=signal_value,
                    followup_status="scheduled" if local_followup_scheduled else "resolved",
                    followup_last_signal=signal_value,
                    followup_last_action=(
                        f"scheduled:{LOCAL_MICROSTEP_BATCH_REASON}"
                        if local_followup_scheduled
                        else "queued_feedback_delivered_local_microstep_no_autowake"
                    ),
                    followup_stopped_at="" if local_followup_scheduled else utc_now(),
                    followup_last_message_path=str(msg_path),
                )
            resolve_followup(config, followup_key)
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": (
                        "queued_feedback_delivered_local_microstep"
                        if local_followup_scheduled
                        else "queued_feedback_delivered_local_microstep_no_autowake"
                    ),
                    "queue_depth": entry_count,
                    "signal": signal_value,
                }
            )
            return processed
        should_park, evidence_token, repeat_count = maybe_park_continuous_idle_loop(
            config,
            followup=followup,
            spec_for_resume=spec_for_resume,
            signal_value=signal_value,
            is_continuous_followup=is_continuous_followup,
            is_continuous_session_reminder=is_continuous_session_reminder,
        )
        if should_park:
            sync_followup_state(
                config,
                followup,
                followup_status="resolved",
                followup_last_action="resolved_parked_idle",
                followup_last_signal=signal_value,
                notification_signal=signal_value,
                message_path=str(msg_path),
            )
            resolve_followup(config, followup_key)
            if task_id:
                merge_task_state(
                    config,
                    task_id,
                    session_flow_state="parked_idle",
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="resolved_parked_idle",
                    followup_last_message_path=str(msg_path),
                    notification_signal=signal_value,
                )
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "resolved_parked_idle",
                    "signal": signal_value,
                    "idle_repeat_count": repeat_count,
                    "waiting_evidence_token": evidence_token,
                }
            )
            return processed
        local_followup_scheduled = False
        if task_id:
            local_followup_scheduled = schedule_local_microstep_followup(
                config,
                task_id=task_id,
                spec=spec_for_resume,
                followup=followup,
            )
        if not local_followup_scheduled:
            sync_followup_state(
                config,
                followup,
                followup_status="resolved",
                followup_last_action="resolved_local_microstep_without_continuous",
                followup_last_signal=signal_value,
                notification_signal=signal_value,
                message_path=str(msg_path),
            )
            resolve_followup(config, followup_key)
            if task_id:
                merge_task_state(
                    config,
                    task_id,
                    session_flow_state="local_active",
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="resolved_local_microstep_without_continuous",
                    followup_last_message_path=str(msg_path),
                    notification_signal=signal_value,
                )
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "resolved_local_microstep_without_continuous",
                    "signal": signal_value,
                }
            )
            return processed
        if task_id:
            merge_task_state(
                config,
                task_id,
                session_flow_state="local_active",
                notification_signal=signal_value,
                followup_status="scheduled",
                followup_last_signal=signal_value,
                followup_last_action=f"scheduled:{LOCAL_MICROSTEP_BATCH_REASON}",
                followup_stopped_at="",
                followup_last_message_path=str(msg_path),
            )
        processed.append(
            {
                "followup_key": followup_key,
                "action": "local_microstep_followup_scheduled",
                "signal": signal_value,
            }
        )
        return processed
    if signal_value in WAITING_ON_ASYNC_SIGNALS and result.get("ok", False):
        newer_async_task_exists = newer_task_exists(config, followup)
        session_id = str(followup.get("codex_session_id", "")).strip()
        live_task_present = waiting_signal_has_live_task(
            config,
            session_id=session_id,
            source_task_id=task_id,
        )
        guarded_to_parked_idle = (
            is_continuous_session_reminder
            and session_id
            and str(followup.get("last_signal", "")).strip() in PARKED_IDLE_SIGNALS
        )
        if guarded_to_parked_idle:
            guard_enabled, parked_waiting_state, evidence_token, repeat_count = parked_waiting_signal_guard_details(
                config,
                session_id=session_id,
                spec=spec_for_resume,
                followup_last_signal=str(followup.get("last_signal", "")).strip(),
                newer_async_task_exists=newer_async_task_exists,
            )
            if guard_enabled:
                guard_parked_waiting_signal_without_live_task(
                    config,
                    session_id=session_id,
                    spec=spec_for_resume,
                    parked_waiting_state=parked_waiting_state,
                    evidence_token=evidence_token,
                    repeat_count=repeat_count,
                    updated_by="followup",
                    source="followup-guard-invalid-waiting-signal",
                )
                sync_followup_state(
                    config,
                    followup,
                    followup_status="resolved",
                    followup_last_action="guarded_invalid_waiting_signal_to_parked_idle",
                    followup_last_signal=signal_value,
                    notification_signal=signal_value,
                    message_path=str(msg_path),
                )
                resolve_followup(config, followup_key)
                if task_id:
                    current_task_state = load_task_state(config, task_id)
                    current_summary = (
                        current_task_state.get("notification_summary", {})
                        if isinstance(current_task_state.get("notification_summary", {}), dict)
                        else {}
                    )
                    merge_task_state(
                        config,
                        task_id,
                        session_flow_state="parked_idle",
                        followup_status="resolved",
                        followup_last_signal=signal_value,
                        followup_last_action="guarded_invalid_waiting_signal_to_parked_idle",
                        followup_last_message_path=str(msg_path),
                        notification_signal=signal_value,
                        notification_summary={
                            **current_summary,
                            "session_flow_state": "parked_idle",
                            "taskboard_signal": signal_value,
                            "guarded_invalid_waiting_signal": True,
                            "guarded_to": parked_waiting_state,
                            "waiting_evidence_token": evidence_token,
                        },
                    )
                processed.append(
                    {
                        "followup_key": followup_key,
                        "action": "guarded_invalid_waiting_signal_to_parked_idle",
                        "signal": signal_value,
                        "guarded_to": parked_waiting_state,
                        "waiting_evidence_token": evidence_token,
                    }
                )
                return processed
        if is_queued_feedback:
            delivered_summary = {
                "ok": result.get("ok", False),
                "deferred": True,
                "deferred_reason": str(followup.get("reason", "")),
                "delivered_from_queue": True,
                "queue_depth": entry_count,
                "original_session_id": result.get("original_session_id"),
                "resumed_session_id": result.get("resumed_session_id"),
                "used_fallback_clone": result.get("used_fallback_clone", False),
                "fallback_provider": result.get("fallback_provider", ""),
                "taskboard_signal": signal_value,
                "continue_attempts": result.get("continue_attempts", 0),
                "recovered_with_continue": result.get("recovered_with_continue", False),
                "session_flow_state": "awaiting_async",
                "newer_async_task_exists": newer_async_task_exists,
                "live_task_present": live_task_present,
            }
            for queued_task_id in followup_task_ids(followup):
                merge_task_state(
                    config,
                    queued_task_id,
                    pending_feedback=False,
                    notification_ok=result.get("ok", False),
                    notification_signal=signal_value,
                    resumed_session_id=result.get("resumed_session_id", followup.get("codex_session_id", "")),
                    used_fallback_clone=result.get("used_fallback_clone", False),
                    notification_finished_at=result.get("finished_at"),
                    notification_summary=delivered_summary,
                    session_flow_state="awaiting_async",
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action=(
                        "queued_feedback_delivered_waiting_on_async_bound_to_newer_task"
                        if newer_async_task_exists
                        else (
                            "queued_feedback_delivered_waiting_on_async_live_task"
                            if live_task_present
                            else "queued_feedback_delivered_waiting_on_async"
                        )
                    ),
                    followup_last_message_path=str(msg_path),
                )
            resolve_followup(config, followup_key)
            waiting_followup_scheduled = False
            if not newer_async_task_exists and not live_task_present and task_id and should_schedule_followup_for_spec(spec_for_resume):
                waiting_followup_scheduled = schedule_waiting_on_async_watchdog(
                    config,
                    task_id=task_id,
                    spec=spec_for_resume,
                )
                merge_task_state(
                    config,
                    task_id,
                    session_flow_state="awaiting_async",
                    notification_signal=signal_value,
                    followup_status="scheduled" if waiting_followup_scheduled else "resolved",
                    followup_last_signal=signal_value,
                    followup_last_action=(
                        f"scheduled:{WAITING_ON_ASYNC_REASON}"
                        if waiting_followup_scheduled
                        else "queued_feedback_delivered_waiting_on_async_no_autowake"
                    ),
                    followup_stopped_at="" if waiting_followup_scheduled else utc_now(),
                    followup_last_message_path=str(msg_path),
                )
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": (
                        "queued_feedback_waiting_on_async_bound_to_newer_task"
                        if newer_async_task_exists
                        else (
                            "queued_feedback_waiting_on_async_live_task"
                            if live_task_present
                            else (
                                "queued_feedback_waiting_on_async_watchdog_scheduled"
                                if waiting_followup_scheduled
                                else "queued_feedback_waiting_on_async_without_watchdog"
                            )
                        )
                    ),
                    "queue_depth": entry_count,
                    "signal": signal_value,
                }
            )
            return processed
        if newer_async_task_exists:
            sync_followup_state(
                config,
                followup,
                followup_status="resolved",
                followup_last_action="resolved_waiting_on_async_newer_task",
                followup_last_signal=signal_value,
                notification_signal=signal_value,
                message_path=str(msg_path),
            )
            if task_id:
                merge_task_state(
                    config,
                    task_id,
                    session_flow_state="awaiting_async",
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="resolved_waiting_on_async_newer_task",
                    followup_last_message_path=str(msg_path),
                    notification_signal=signal_value,
                )
            resolve_followup(config, followup_key)
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "resolved_waiting_on_async_newer_task",
                    "signal": signal_value,
                }
            )
            return processed
        if live_task_present:
            if session_id:
                clear_continuous_research_session_waiting_state(
                    config,
                    codex_session_id=session_id,
                    evidence_token=continuous_research_session_evidence_token(config, session_id, spec=spec_for_resume),
                    last_signal=signal_value,
                    updated_by="followup",
                    source="followup-waiting-on-async-live-task",
                )
            sync_followup_state(
                config,
                followup,
                followup_status="resolved",
                followup_last_action="resolved_waiting_on_async_live_task",
                followup_last_signal=signal_value,
                notification_signal=signal_value,
                message_path=str(msg_path),
            )
            if task_id:
                merge_task_state(
                    config,
                    task_id,
                    session_flow_state="awaiting_async",
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="resolved_waiting_on_async_live_task",
                    followup_last_message_path=str(msg_path),
                    notification_signal=signal_value,
                )
            resolve_followup(config, followup_key)
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "resolved_waiting_on_async_live_task",
                    "signal": signal_value,
                }
            )
            return processed
        if session_id:
            clear_continuous_research_session_waiting_state(
                config,
                codex_session_id=session_id,
                evidence_token=continuous_research_session_evidence_token(config, session_id, spec=spec_for_resume),
                last_signal=signal_value,
                updated_by="followup",
                source="followup-waiting-on-async",
            )
        waiting_followup_scheduled = False
        if task_id:
            waiting_followup_scheduled = schedule_waiting_on_async_watchdog(
                config,
                task_id=task_id,
                spec=spec_for_resume,
                followup=followup,
            )
        if not waiting_followup_scheduled:
            sync_followup_state(
                config,
                followup,
                followup_status="resolved",
                followup_last_action="resolved_waiting_on_async_without_continuous",
                followup_last_signal=signal_value,
                notification_signal=signal_value,
                message_path=str(msg_path),
            )
            resolve_followup(config, followup_key)
            if task_id:
                merge_task_state(
                    config,
                    task_id,
                    session_flow_state="awaiting_async",
                    followup_status="resolved",
                    followup_last_signal=signal_value,
                    followup_last_action="resolved_waiting_on_async_without_continuous",
                    followup_last_message_path=str(msg_path),
                    notification_signal=signal_value,
                )
            processed.append(
                {
                    "followup_key": followup_key,
                    "action": "resolved_waiting_on_async_without_continuous",
                    "signal": signal_value,
                }
            )
            return processed
        if task_id:
            merge_task_state(
                config,
                task_id,
                session_flow_state="awaiting_async",
                notification_signal=signal_value,
                followup_status="scheduled",
                followup_last_signal=signal_value,
                followup_last_action=f"scheduled:{WAITING_ON_ASYNC_REASON}",
                followup_stopped_at="",
                followup_last_message_path=str(msg_path),
            )
        processed.append(
            {
                "followup_key": followup_key,
                "action": "waiting_on_async_watchdog_scheduled",
                "signal": signal_value,
            }
        )
        return processed
    if is_queued_feedback:
        if not result.get("ok", False):
            defer_followup_retry(
                config,
                followup,
                reason=str(result.get("deferred_reason", "") or "resume_failed"),
                retry_after_seconds=default_retry_delay_seconds(int(followup.get("interval_seconds", 300) or 300)),
                message_path=str(msg_path),
            )
            processed.append({"followup_key": followup_key, "action": "queued_feedback_retry_scheduled"})
            return processed
        for queued_task_id in followup_task_ids(followup):
            merge_task_state(
                config,
                queued_task_id,
                pending_feedback=False,
                notification_ok=result.get("ok", False),
                notification_signal=signal_value,
                resumed_session_id=result.get("resumed_session_id", followup.get("codex_session_id", "")),
                used_fallback_clone=result.get("used_fallback_clone", False),
                notification_finished_at=result.get("finished_at"),
                notification_summary={
                    "ok": result.get("ok", False),
                    "deferred": True,
                    "deferred_reason": str(followup.get("reason", "")),
                    "delivered_from_queue": True,
                    "queue_depth": entry_count,
                    "original_session_id": result.get("original_session_id"),
                    "resumed_session_id": result.get("resumed_session_id"),
                    "used_fallback_clone": result.get("used_fallback_clone", False),
                    "fallback_provider": result.get("fallback_provider", ""),
                    "taskboard_signal": signal_value,
                    "continue_attempts": result.get("continue_attempts", 0),
                    "recovered_with_continue": result.get("recovered_with_continue", False),
                },
                followup_status="resolved",
                followup_last_signal=signal_value,
                followup_last_action="queued_feedback_delivered",
                followup_last_message_path=str(msg_path),
            )
        if result.get("ok", False) and should_schedule_followup_for_spec(spec_for_resume):
            schedule_followup(
                config,
                task_id=task_id,
                spec=spec_for_resume,
                reason="no_new_task_after_feedback",
            )
        resolve_followup(config, followup_key)
        processed.append(
            {
                "followup_key": followup_key,
                "action": "queued_feedback_delivered",
                "queue_depth": entry_count,
                "signal": signal_value,
                "continue_attempts": result.get("continue_attempts", 0),
            }
        )
        return processed
    if not result.get("ok", False):
        followup["nudge_count"] = int(followup.get("nudge_count", 0)) + 1
        defer_followup_retry(
            config,
            followup,
            reason=str(result.get("deferred_reason", "") or "resume_failed"),
            retry_after_seconds=int(followup.get("interval_seconds", 300) or 300),
            message_path=str(msg_path),
        )
        processed.append({"followup_key": followup_key, "action": "nudge_retry_scheduled", "signal": signal_value})
        return processed
    followup["nudge_count"] = int(followup.get("nudge_count", 0)) + 1
    followup["check_after_ts"] = time.time() + int(followup.get("interval_seconds", 300))
    followup["last_signal"] = signal_value
    followup["last_action"] = "nudged"
    updated_at = utc_now()
    followup["last_checked_at"] = updated_at
    followup["updated_at"] = updated_at
    atomic_write_json(followup_path(config, followup_key), followup)
    append_followup_event_log(config, event="scheduled", reason="nudged", followup=followup, detail="nudge_retry_scheduled")
    if task_id:
        merge_task_state(
            config,
            task_id,
            followup_status="scheduled",
            followup_last_signal=signal_value,
            followup_last_action="nudged",
            followup_last_message_path=str(msg_path),
        )
    return processed


def process_followups(config: AppConfig) -> list[dict[str, Any]]:
    processed: list[dict[str, Any]] = []
    processed.extend(recover_missing_queued_feedback_followups(config))
    processed.extend(ensure_continuous_research_session_reminders(config))
    seen_keys: set[str] = set()
    for followup in sorted(load_followups(config), key=followup_processing_sort_key):
        followup_key = str(followup.get("followup_key", "")).strip()
        if not followup_key or followup_key in seen_keys:
            continue
        seen_keys.add(followup_key)
        followup_type = str(followup.get("followup_type", "")).strip()
        if followup_type == "queued_feedback_resume":
            def _process_locked() -> list[dict[str, Any]]:
                latest_followup = read_json(followup_path(config, followup_key), {})
                if not isinstance(latest_followup, dict) or not latest_followup:
                    return []
                return process_single_followup(config, latest_followup)

            processed.extend(run_with_followup_lock(config, followup_key, _process_locked))
            continue
        processed.extend(process_single_followup(config, followup))
    return processed


def load_raw_json_dict(path: Path) -> dict[str, Any]:
    payload = read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def extract_command_metadata_from_log(command_log_path: Path) -> tuple[str, str]:
    workdir = ""
    command = ""
    if not command_log_path.exists():
        return workdir, command
    for line in command_log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("workdir=") and not workdir:
            workdir = line.split("=", 1)[1].strip()
        elif line.startswith("command=") and not command:
            command = line.split("=", 1)[1].strip()
        if workdir and command:
            break
    return workdir, command


def legacy_root_slug(root: Path) -> str:
    text = str(root.resolve())
    text = text.replace("/home/", "")
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
    return text.strip("-") or "legacy"


def unique_archive_destination(config: AppConfig, source_root: Path, task_id: str) -> Path:
    base = archive_root(config) / legacy_root_slug(source_root) / task_id
    if not base.exists():
        return base
    index = 1
    while True:
        candidate = archive_root(config) / legacy_root_slug(source_root) / f"{task_id}--{index:02d}"
        if not candidate.exists():
            return candidate
        index += 1


def task_should_remain_live(state: dict[str, Any]) -> bool:
    status = str(state.get("status", ""))
    if status in ACTIVE_TASK_STATUSES | RUNNABLE_STATUSES:
        return True
    if bool(state.get("pending_feedback", False)):
        return True
    return False


def task_should_count_as_running_task(state: dict[str, Any]) -> bool:
    status = str(state.get("status", "")).strip()
    return status in ACTIVE_TASK_STATUSES | RUNNABLE_STATUSES


def task_has_research_binding(state: dict[str, Any]) -> bool:
    return any(
        str(state.get(field, "")).strip()
        for field in ("proposal_path", "closeout_proposal_dir", "project_history_file")
    )


def task_lifecycle_state(state: dict[str, Any]) -> str:
    status = str(state.get("status", "")).strip()
    if bool(state.get("pending_feedback", False)):
        return "awaiting_feedback"
    if status in ACTIVE_TASK_STATUSES:
        return "running"
    if status in RUNNABLE_STATUSES:
        return "queued"
    if status in {"completed", "observed_exit"}:
        return "completed"
    if status in {"failed", "terminated", "launch_failed"}:
        return "failed"
    return status or "unknown"


def task_runtime_state(config: AppConfig, state: dict[str, Any]) -> str:
    lifecycle_state = task_lifecycle_state(state)
    if lifecycle_state == "running":
        if watched_pid_alive(state):
            return "watch_pid_live"
        if task_execution_still_live(config, state):
            return "child_live"
        return "launch_recorded_not_live"
    if lifecycle_state == "awaiting_feedback":
        return "awaiting_feedback"
    if lifecycle_state in {"completed", "failed", "superseded"}:
        return "not_live"
    if task_has_launch_metadata(state):
        return "launch_recorded_not_live"
    return "not_started"


def task_platform_recovery_state(state: dict[str, Any]) -> dict[str, Any]:
    notification_summary = state.get("notification_summary", {})
    if not isinstance(notification_summary, dict):
        notification_summary = {}
    manual_summary = state.get("manual_notification_summary", {})
    if not isinstance(manual_summary, dict):
        manual_summary = {}
    followup_last_action = str(state.get("followup_last_action", "")).strip()
    candidate_reasons = [
        str(notification_summary.get("deferred_reason", "")).strip(),
        str(manual_summary.get("deferred_reason", "")).strip(),
    ]
    if followup_last_action.startswith("deferred:"):
        candidate_reasons.append(followup_last_action.split(":", 1)[1].strip())
    details = {}
    deferred_reason = ""
    for candidate in candidate_reasons:
        derived = platform_error_from_reason(candidate)
        if derived.get("kind"):
            details = derived
            deferred_reason = candidate
            break
    if not details:
        details = classify_platform_error(
            str(notification_summary.get("stderr_tail", "")).strip(),
            str(notification_summary.get("stdout_tail", "")).strip(),
            str(state.get("failure_summary", "")).strip(),
            str(state.get("failure_excerpt", "")).strip(),
            str(state.get("attention_message", "")).strip(),
        )
    if not details.get("kind"):
        return {
            "state": "none",
            "kind": "",
            "retryable": False,
            "needs_human_attention": False,
            "summary": "",
            "deferred_reason": "",
            "retry_after_seconds": 0,
        }
    retry_after_seconds = int(
        notification_summary.get("retry_after_seconds", 0)
        or manual_summary.get("retry_after_seconds", 0)
        or 0
    )
    needs_human_attention = bool(details.get("needs_human_attention", False)) or (
        bool(state.get("needs_attention", False))
        and str(state.get("attention_reason", "")).strip().startswith("platform_error:")
    )
    summary = (
        str(state.get("attention_message", "")).strip()
        or str(details.get("summary", "")).strip()
    )
    return {
        "state": "needs_human_attention" if needs_human_attention else "recovering",
        "kind": str(details.get("kind", "")).strip(),
        "retryable": bool(details.get("retryable", False)),
        "needs_human_attention": needs_human_attention,
        "summary": summary,
        "deferred_reason": deferred_reason or platform_error_deferred_reason(str(details.get("kind", "")).strip()),
        "retry_after_seconds": retry_after_seconds,
    }


def task_automation_recommendation(state: dict[str, Any]) -> str:
    platform_recovery = state.get("platform_recovery", {})
    if not isinstance(platform_recovery, dict):
        platform_recovery = {}
    if bool(state.get("needs_attention", False)) or str(platform_recovery.get("state", "")) == "needs_human_attention":
        return "needs_human_attention"
    lifecycle_state = str(state.get("lifecycle_state", "")).strip() or task_lifecycle_state(state)
    if lifecycle_state == "awaiting_feedback":
        return "absorb_completed_receipt"
    if lifecycle_state in {"queued", "running"}:
        return "wait_for_live_task"
    if lifecycle_state == "completed":
        return "absorb_completed_receipt"
    if lifecycle_state == "failed":
        return "needs_human_attention"
    return "safe_to_dispatch"


def platform_attention_updates_from_result(result: dict[str, Any]) -> dict[str, Any]:
    kind = str(result.get("platform_error_kind", "")).strip()
    needs_human_attention = bool(
        result.get("needs_human_attention", False)
        or result.get("platform_error_needs_human_attention", False)
    )
    if not kind or not needs_human_attention:
        return {}
    return {
        "needs_attention": True,
        "attention_reason": f"platform_error:{kind}",
        "attention_message": str(result.get("platform_error_summary", "")).strip()
        or "上游平台异常需要人工处理。",
    }


def watched_pid_alive(state: dict[str, Any]) -> bool:
    if str(state.get("execution_mode", "")).strip() != "external_pid":
        return False
    raw_pid = state.get("watch_pid", 0)
    try:
        pid = int(raw_pid or 0)
    except (TypeError, ValueError):
        return False
    return pid > 0 and pid_exists(pid)


def task_id_exists_historically(config: AppConfig, task_id: str) -> bool:
    if find_task_dir(config, task_id) is not None:
        return True
    if archive_task_dir(config, task_id) is not None:
        return True
    return False


def task_dashboard_hooks() -> TaskDashboardHooks:
    return TaskDashboardHooks(
        active_task_statuses=ACTIVE_TASK_STATUSES,
        runnable_statuses=RUNNABLE_STATUSES,
        timestamp_sort_value=timestamp_sort_value,
        task_list_sort_key=task_list_sort_key,
        state_has_unresolved_dependencies=state_has_unresolved_dependencies,
        dashboard_short_time=dashboard_short_time,
        dashboard_trim=dashboard_trim,
    )


def filter_dashboard_tasks(
    config: AppConfig,
    states: list[dict[str, Any]],
    *,
    status_filter: str,
    agent_filter: str,
) -> list[dict[str, Any]]:
    return filter_dashboard_tasks_impl(
        states,
        status_filter=status_filter,
        agent_filter=agent_filter,
        hooks=task_dashboard_hooks(),
    )


def sort_dashboard_tasks(config: AppConfig, states: list[dict[str, Any]], sort_mode: str) -> list[dict[str, Any]]:
    return sort_dashboard_tasks_impl(
        config,
        states,
        sort_mode,
        hooks=task_dashboard_hooks(),
    )


def state_has_unresolved_dependencies(config: AppConfig, state: dict[str, Any]) -> bool:
    dependency_state = str(state.get("dependency_state", "")).strip()
    if dependency_state:
        return dependency_state not in {"none", "ready"}
    dependency_resolution_items = state.get("dependency_resolution", [])
    if isinstance(dependency_resolution_items, list) and dependency_resolution_items:
        return any(not bool(item.get("satisfied", False)) for item in dependency_resolution_items if isinstance(item, dict))
    blocked_reason = str(state.get("blocked_reason", "")).strip()
    if blocked_reason.startswith("dependency:"):
        return True
    return bool(unresolved_dependencies(config, state))


def is_terminal_status(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_hidden_status(status: str) -> bool:
    return status in HIDDEN_TASK_STATUSES


def compute_attention(event: dict[str, Any], spec: dict[str, Any]) -> tuple[bool, str, str]:
    status = str(event.get("status", ""))
    failure_kind = str(event.get("failure_kind", ""))
    duration = event.get("duration_seconds")
    threshold = int(spec.get("startup_failure_threshold_seconds", DEFAULT_STARTUP_FAILURE_SECONDS))
    if status in {"failed", "launch_failed", "terminated"} and (duration is None or int(duration) <= threshold):
        reason = f"startup_failure:{failure_kind or status}"
        return True, reason, "任务在非常早的阶段就失败了。请先检查代码、启动配置或内存设置，再决定是否重跑。"
    if failure_kind == "oom":
        return True, "oom", "任务触发了内存不足。请先调整 batch size、启动形态或其他内存设置，再决定是否重跑。"
    research_stall, reason, message = detect_research_stall_attention(event, spec)
    if research_stall:
        return True, reason, message
    return False, "", ""


def infer_gpu_slots(command: str, env: dict[str, str], explicit_gpu_slots: int | None) -> int:
    if explicit_gpu_slots is not None:
        return max(0, int(explicit_gpu_slots))
    cuda_visible_devices = str(env.get("CUDA_VISIBLE_DEVICES", "")).strip()
    if cuda_visible_devices and cuda_visible_devices.lower() != "none":
        devices = [item.strip() for item in cuda_visible_devices.split(",") if item.strip()]
        if devices:
            return len(devices)
    if looks_like_training_command(command):
        return 1
    return 0


def scheduler_resource_hooks() -> SchedulerResourceHooks:
    return SchedulerResourceHooks(
        looks_like_training_command=looks_like_training_command,
        shutil_which=shutil_which,
        run_subprocess=run_subprocess,
        cpu_profile_choices=CPU_PROFILE_CHOICES,
        cpu_thread_env_keys=CPU_THREAD_ENV_KEYS,
        cpu_worker_env_keys=CPU_WORKER_ENV_KEYS,
        cpu_resource_retry_patterns=tuple(CPU_RESOURCE_RETRY_PATTERNS),
        default_cpu_thread_limit=DEFAULT_CPU_THREAD_LIMIT,
        default_cpu_only_threads=DEFAULT_CPU_ONLY_THREADS,
        default_gpu_task_cpu_threads=DEFAULT_GPU_TASK_CPU_THREADS,
        default_subagent_cpu_threads=DEFAULT_SUBAGENT_CPU_THREADS,
        default_generic_task_cpu_threads=DEFAULT_GENERIC_TASK_CPU_THREADS,
        default_gpu_min_free_mb=DEFAULT_GPU_MIN_FREE_MB,
        default_gpu_free_ratio=DEFAULT_GPU_FREE_RATIO,
        default_gpu_max_util_percent=DEFAULT_GPU_MAX_UTIL_PERCENT,
    )


def scheduler_readiness_hooks() -> SchedulerReadinessHooks:
    return SchedulerReadinessHooks(
        is_hidden_status=is_hidden_status,
        task_state_recency_key=task_state_recency_key,
        iter_all_task_states=iter_all_task_states,
        newest_matches=newest_matches,
        success_taskboard_signals=SUCCESS_TASKBOARD_SIGNALS,
        resolved_cpu_profile=resolved_cpu_profile,
        declared_cpu_profile=declared_cpu_profile,
        resolve_cpu_thread_policy=resolve_cpu_thread_policy,
        resolve_cpu_worker_policy=resolve_cpu_worker_policy,
        coerce_non_negative_int=coerce_non_negative_int,
        select_gpu_ids_for_task=select_gpu_ids_for_task,
        gpu_row_free_mb=gpu_row_free_mb,
    )


def scheduler_enrichment_hooks() -> SchedulerEnrichmentHooks:
    return SchedulerEnrichmentHooks(
        load_task_spec=load_task_spec,
        normalize_task_spec_payload=normalize_task_spec_payload,
        parse_gpu_id_list=parse_gpu_id_list,
        evaluate_task_readiness=evaluate_task_readiness,
        task_lifecycle_state=task_lifecycle_state,
        task_runtime_state=task_runtime_state,
        task_has_launch_metadata=task_has_launch_metadata,
        task_platform_recovery_state=task_platform_recovery_state,
        task_automation_recommendation=task_automation_recommendation,
        followup_entity_info=followup_entity_info,
    )


def detect_default_cpu_thread_limit() -> int:
    return detect_default_cpu_thread_limit_impl(hooks=scheduler_resource_hooks())


def coerce_non_negative_int(raw_value: Any) -> int:
    return coerce_non_negative_int_impl(raw_value)


def normalize_cpu_profile(raw_value: Any) -> str:
    return normalize_cpu_profile_impl(raw_value, hooks=scheduler_resource_hooks())


def declared_cpu_profile(spec: dict[str, Any]) -> str:
    return declared_cpu_profile_impl(spec, hooks=scheduler_resource_hooks())


def resolved_cpu_profile(spec: dict[str, Any]) -> str:
    return resolved_cpu_profile_impl(spec, hooks=scheduler_resource_hooks())


def extract_thread_limit_from_env(env: dict[str, Any]) -> int:
    return extract_thread_limit_from_env_impl(env, hooks=scheduler_resource_hooks())


def extract_inline_thread_limit(command: str) -> int:
    return extract_inline_thread_limit_impl(command, hooks=scheduler_resource_hooks())


def command_sets_cpu_thread_limits(command: str) -> bool:
    return command_sets_cpu_thread_limits_impl(command, hooks=scheduler_resource_hooks())


def extract_worker_limit_from_env(env: dict[str, Any]) -> int:
    return extract_worker_limit_from_env_impl(env, hooks=scheduler_resource_hooks())


def extract_inline_worker_limit(command: str) -> int:
    return extract_inline_worker_limit_impl(command)


def command_sets_cpu_worker_limits(command: str) -> bool:
    return command_sets_cpu_worker_limits_impl(command)


def command_uses_cpu_runtime_template(command: str) -> bool:
    return command_uses_cpu_runtime_template_impl(command)


def render_task_command_template(
    command_template: str,
    *,
    cpu_threads: int,
    cpu_workers: int,
    cpu_profile: str,
    cpu_budget: int,
) -> str:
    return render_task_command_template_impl(
        command_template,
        cpu_threads=cpu_threads,
        cpu_workers=cpu_workers,
        cpu_profile=cpu_profile,
        cpu_budget=cpu_budget,
    )


def infer_default_cpu_threads(spec: dict[str, Any]) -> tuple[int, str]:
    return infer_default_cpu_threads_impl(spec, hooks=scheduler_resource_hooks())


def default_cpu_thread_mode(spec: dict[str, Any]) -> str:
    return default_cpu_thread_mode_impl(spec, hooks=scheduler_resource_hooks())


def resolve_cpu_thread_policy(spec: dict[str, Any], *, cpu_thread_limit: int = 0) -> dict[str, Any]:
    return resolve_cpu_thread_policy_impl(spec, hooks=scheduler_resource_hooks(), cpu_thread_limit=cpu_thread_limit)


def resolve_cpu_worker_policy(spec: dict[str, Any], *, cpu_thread_limit: int = 0) -> dict[str, Any]:
    return resolve_cpu_worker_policy_impl(spec, hooks=scheduler_resource_hooks(), cpu_thread_limit=cpu_thread_limit)


def resolve_cpu_threads(spec: dict[str, Any]) -> tuple[int, str]:
    return resolve_cpu_threads_impl(spec, hooks=scheduler_resource_hooks())


def resolve_cpu_workers(spec: dict[str, Any]) -> tuple[int, str]:
    return resolve_cpu_workers_impl(spec, hooks=scheduler_resource_hooks())


def task_requested_cpu_threads(spec: dict[str, Any]) -> int:
    return task_requested_cpu_threads_impl(spec, hooks=scheduler_resource_hooks())


def task_requested_cpu_workers(spec: dict[str, Any]) -> int:
    return task_requested_cpu_workers_impl(spec, hooks=scheduler_resource_hooks())


def task_requested_cpu_budget(spec: dict[str, Any]) -> int:
    return task_requested_cpu_budget_impl(spec, hooks=scheduler_resource_hooks())


def select_cpu_resources_for_start(
    spec: dict[str, Any],
    *,
    available_cpu_threads: int,
    reserve_for_other_tasks: int = 0,
) -> dict[str, int | str]:
    return select_cpu_resources_for_start_impl(
        spec,
        hooks=scheduler_resource_hooks(),
        available_cpu_threads=available_cpu_threads,
        reserve_for_other_tasks=reserve_for_other_tasks,
    )


def cpu_resource_retry_reason(event: dict[str, Any]) -> str:
    return cpu_resource_retry_reason_impl(event, hooks=scheduler_resource_hooks())


def next_cpu_backoff_threads(current_threads: int, min_threads: int) -> int:
    return next_cpu_backoff_threads_impl(current_threads, min_threads)


def detect_gpu_count() -> int:
    return detect_gpu_count_impl(hooks=scheduler_resource_hooks())


def parse_gpu_id_list(raw_value: Any) -> list[int]:
    return parse_gpu_id_list_impl(raw_value)


def extract_inline_cuda_visible_devices(command: str) -> list[int]:
    return extract_inline_cuda_visible_devices_impl(command)


def command_sets_cuda_visible_devices(command: str) -> bool:
    return command_sets_cuda_visible_devices_impl(command)


def task_requested_gpu_ids(spec: dict[str, Any]) -> list[int]:
    return task_requested_gpu_ids_impl(spec)


def gpu_row_free_mb(row: dict[str, Any]) -> int:
    return gpu_row_free_mb_impl(row)


def default_gpu_min_free_mb(row: dict[str, Any]) -> int:
    return default_gpu_min_free_mb_impl(row, hooks=scheduler_resource_hooks())


def task_gpu_min_free_mb(spec: dict[str, Any], row: dict[str, Any]) -> int:
    return task_gpu_min_free_mb_impl(spec, row, hooks=scheduler_resource_hooks())


def task_gpu_max_util_percent(spec: dict[str, Any]) -> int:
    return task_gpu_max_util_percent_impl(spec, hooks=scheduler_resource_hooks())


def gpu_row_can_host_task(row: dict[str, Any], spec: dict[str, Any]) -> bool:
    return gpu_row_can_host_task_impl(row, spec, hooks=scheduler_resource_hooks())


def select_gpu_ids_for_task(
    spec: dict[str, Any],
    *,
    total_gpu_slots: int,
    gpu_rows: list[dict[str, Any]],
    reserved_gpu_ids: set[int],
) -> tuple[list[int] | None, str]:
    return select_gpu_ids_for_task_impl(
        spec,
        hooks=scheduler_resource_hooks(),
        total_gpu_slots=total_gpu_slots,
        gpu_rows=gpu_rows,
        reserved_gpu_ids=reserved_gpu_ids,
    )

def task_list_sort_key(item: dict[str, Any]) -> tuple[int, int, float, str]:
    status = str(item.get("status", ""))
    priority = -int(item.get("priority", 0) or 0)
    submitted_at = timestamp_sort_value(item.get("submitted_at"), missing=float("inf"))
    task_id = str(item.get("task_id", ""))
    needs_attention = bool(item.get("needs_attention", False))
    pending_feedback = bool(item.get("pending_feedback", False))
    if status in ACTIVE_TASK_STATUSES:
        group = 0
    elif status in RUNNABLE_STATUSES:
        group = 1
    elif pending_feedback:
        group = 2
    elif status in {"completed", "observed_exit"}:
        group = 3
    elif needs_attention:
        group = 4
    else:
        group = 5
    return (group, priority, submitted_at, task_id)


def parse_key_value_pairs(entries: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"Expected KEY=VALUE entry, got: {entry}")
        key, value = entry.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid empty key in entry: {entry}")
        result[key] = value
    return result


def read_optional_text(inline_value: str | None, file_value: str | None) -> str:
    if inline_value and file_value:
        raise ValueError("Use either the inline prompt flag or the prompt file flag, not both.")
    if file_value:
        return Path(file_value).expanduser().read_text(encoding="utf-8")
    return inline_value or ""


def run_subprocess(
    command: list[str],
    *,
    cwd: str | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout)


def signal_process_group(pgid: int, pid: int, sig: signal.Signals) -> None:
    target_pgid = int(pgid or 0)
    target_pid = int(pid or 0)
    try:
        if target_pgid > 0:
            os.killpg(target_pgid, sig)
            return
        if target_pid > 0:
            os.kill(target_pid, sig)
    except ProcessLookupError:
        return


def run_tracked_feedback_subprocess(
    config: AppConfig,
    command: list[str],
    *,
    cwd: str | None = None,
    timeout: int | None = None,
    session_id: str,
    requested_session_id: str,
    source_kind: str,
    source_key: str,
    task_id: str = "",
    task_ids: list[str] | None = None,
    followup_key: str = "",
) -> subprocess.CompletedProcess[str]:
    operation_id = secrets.token_hex(12)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    register_active_feedback_runtime(
        config,
        operation_id=operation_id,
        session_id=session_id,
        requested_session_id=requested_session_id,
        pid=process.pid,
        pgid=process.pid,
        source_kind=source_kind,
        source_key=source_key,
        task_id=task_id,
        task_ids=task_ids,
        followup_key=followup_key,
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            signal_process_group(process.pid, process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                signal_process_group(process.pid, process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(exc.cmd, exc.timeout, output=stdout, stderr=stderr) from exc
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        clear_active_feedback_runtime(config, operation_id)


def tmux_command(config: AppConfig, *args: str) -> list[str]:
    ensure_dir(config.tmux_socket_path.parent)
    return [config.tmux_bin, "-S", str(config.tmux_socket_path), *args]


def tmux_session_exists(config: AppConfig, session_name: str) -> bool:
    completed = subprocess.run(tmux_command(config, "has-session", "-t", session_name), text=True, capture_output=True)
    if completed.returncode == 0:
        return True
    # Backward compatibility: older tasks may still live on the default tmux socket.
    legacy = subprocess.run([config.tmux_bin, "has-session", "-t", session_name], text=True, capture_output=True)
    if legacy.returncode == 0:
        return True
    return completed.returncode == 0


LOCAL_CODEX_INTERACTIVE_CAPTURE_LINES = 240
LOCAL_CODEX_INTERACTIVE_POLL_SECONDS = 1.0
LOCAL_CODEX_TRUST_PROMPT_TEXT = "Do you trust the contents of this directory?"
LOCAL_CODEX_ERROR_PATTERNS = (
    "429 too many requests",
    "retry limit",
    "503 service unavailable",
    "401 unauthorized",
    "conversation is busy",
    "session is busy",
    "another response is in progress",
    "upstream proxy error",
    "中转站错误",
)


def tmux_capture_pane_text(config: AppConfig, session_name: str, *, start_line: int = -LOCAL_CODEX_INTERACTIVE_CAPTURE_LINES) -> str:
    completed = subprocess.run(
        tmux_command(config, "capture-pane", "-p", "-S", str(start_line), "-t", session_name),
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return ""
    return str(completed.stdout or "")


def tmux_send_keys(config: AppConfig, session_name: str, *keys: str) -> bool:
    if not keys:
        return False
    completed = subprocess.run(
        tmux_command(config, "send-keys", "-t", session_name, *keys),
        text=True,
        capture_output=True,
    )
    return completed.returncode == 0


def tmux_session_attached_count(config: AppConfig, session_name: str) -> int:
    completed = subprocess.run(
        tmux_command(config, "display-message", "-p", "-t", session_name, "#{session_attached}"),
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return 0
    try:
        return max(0, int(str(completed.stdout or "").strip() or "0"))
    except ValueError:
        return 0


def tmux_kill_session(config: AppConfig, session_name: str) -> None:
    subprocess.run(tmux_command(config, "kill-session", "-t", session_name), text=True, capture_output=True)


def tmux_pane_pid(config: AppConfig, session_name: str) -> int:
    completed = subprocess.run(
        tmux_command(config, "list-panes", "-t", session_name, "-F", "#{pane_pid}"),
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return 0
    first_line = str(completed.stdout or "").splitlines()
    if not first_line:
        return 0
    try:
        return max(0, int(first_line[0].strip() or "0"))
    except ValueError:
        return 0


def local_interactive_prompt_matches(expected_prompt: str, actual_prompt: str) -> bool:
    expected = str(expected_prompt or "").strip()
    actual = str(actual_prompt or "").strip()
    if not expected or not actual:
        return False
    if expected == actual:
        return True
    prefix_chars = min(240, len(expected), len(actual))
    return prefix_chars > 0 and expected[:prefix_chars] == actual[:prefix_chars]


def find_recent_local_thread_for_prompt(
    config: AppConfig,
    *,
    workdir: str,
    prompt: str,
    min_updated_at: float,
    limit: int = 20,
) -> dict[str, Any] | None:
    if not config.threads_db_path.exists():
        return None
    normalized_workdir = str(Path(workdir).expanduser().resolve())
    min_updated_at_seconds = max(0, int(math.floor(float(min_updated_at or 0.0))) - 5)
    conn = sqlite3.connect(config.threads_db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, cwd, updated_at, title, first_user_message, source
            FROM threads
            WHERE cwd = ? AND updated_at >= ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (normalized_workdir, min_updated_at_seconds, max(1, int(limit or 1))),
        ).fetchall()
    finally:
        conn.close()
    candidates = [dict(row) for row in rows]
    for candidate in candidates:
        if local_interactive_prompt_matches(prompt, str(candidate.get("first_user_message", ""))):
            return candidate
    for candidate in candidates:
        if local_interactive_prompt_matches(prompt, str(candidate.get("title", ""))):
            return candidate
    if len(candidates) == 1:
        return candidates[0]
    return None


def local_interactive_error_text(text: str) -> str:
    normalized = str(text or "").lower()
    for pattern in LOCAL_CODEX_ERROR_PATTERNS:
        if pattern in normalized:
            return pattern
    return ""


def local_interactive_prompt_is_idle(text: str) -> bool:
    tail = "\n".join(str(text or "").splitlines()[-40:])
    if "Working" in tail or "esc to interrupt" in tail:
        return False
    return "› " in tail or "\n> " in tail


def run_local_interactive_codex(
    config: AppConfig,
    *,
    command: list[str],
    mode: str,
    workdir: str,
    prompt: str,
    session_id: str,
    output_last_message_path: str,
    timeout_seconds: int,
    log_path: Path,
    requested_session_id: str = "",
    feedback_source_kind: str = "",
    feedback_source_key: str = "",
    feedback_task_id: str = "",
    feedback_task_ids: list[str] | None = None,
    feedback_followup_key: str = "",
    command_started_at: float | None = None,
) -> dict[str, Any]:
    normalized_workdir = str(Path(workdir).expanduser().resolve())
    message_path = Path(output_last_message_path)
    ensure_dir(message_path.parent)
    if command_started_at is None:
        command_started_at = time.time()
    tmux_session_name = f"ctb-codex-wakeup-{mode}-{secrets.token_hex(6)}"
    shell_lines = [
        "set -e",
        f"export CODEX_HOME={shlex.quote(str(config.codex_home))}",
        f"cd {shlex.quote(normalized_workdir)}",
        f"exec {shlex.join(command)}",
    ]
    launch_command = [
        *tmux_command(config, "new-session"),
        "-d",
        "-s",
        tmux_session_name,
        "bash",
        "-lc",
        "\n".join(shell_lines),
    ]
    launch_completed = run_subprocess(launch_command, cwd=normalized_workdir, timeout=30)
    append_log(
        log_path,
        f"local_interactive_launch mode={mode} tmux_session={tmux_session_name} returncode={launch_completed.returncode} stdout_tail={str(launch_completed.stdout or '')[-1000:]} stderr_tail={str(launch_completed.stderr or '')[-1000:]}",
    )
    if launch_completed.returncode != 0:
        completed = subprocess.CompletedProcess(
            command,
            launch_completed.returncode,
            str(launch_completed.stdout or ""),
            str(launch_completed.stderr or ""),
        )
        return {
            "completed": completed,
            "session_id": str(session_id or "").strip(),
            "message_written": False,
            "last_message_text": "",
        }

    pane_pid = tmux_pane_pid(config, tmux_session_name)
    operation_id = ""
    normalized_session_id = str(session_id or "").strip()
    normalized_requested_session_id = str(requested_session_id or normalized_session_id).strip()
    if normalized_session_id and feedback_source_kind:
        operation_id = secrets.token_hex(12)
        register_active_feedback_runtime(
            config,
            operation_id=operation_id,
            session_id=normalized_session_id,
            requested_session_id=normalized_requested_session_id or normalized_session_id,
            pid=pane_pid,
            pgid=pane_pid,
            source_kind=feedback_source_kind,
            source_key=str(feedback_source_key or normalized_session_id).strip() or normalized_session_id,
            task_id=feedback_task_id,
            task_ids=feedback_task_ids,
            followup_key=feedback_followup_key,
        )

    trust_ack_sent = False
    last_pane_text = ""
    last_message_text = ""
    detected_error = ""
    deadline = command_started_at + max(1, int(timeout_seconds or 1))

    def close_tmux_if_safe() -> None:
        if tmux_session_exists(config, tmux_session_name) and tmux_session_attached_count(config, tmux_session_name) == 0:
            tmux_kill_session(config, tmux_session_name)

    try:
        while time.time() < deadline:
            pane_text = tmux_capture_pane_text(config, tmux_session_name)
            if pane_text:
                last_pane_text = pane_text
            if not trust_ack_sent and LOCAL_CODEX_TRUST_PROMPT_TEXT in last_pane_text:
                if tmux_send_keys(config, tmux_session_name, "Enter"):
                    trust_ack_sent = True
                    append_log(log_path, f"local_interactive_trust_ack tmux_session={tmux_session_name}")
                    time.sleep(1)
                    continue
            if not normalized_session_id and mode != "resume":
                thread = find_recent_local_thread_for_prompt(
                    config,
                    workdir=normalized_workdir,
                    prompt=prompt,
                    min_updated_at=command_started_at,
                )
                if thread is not None:
                    normalized_session_id = str(thread.get("id", "")).strip()
            if normalized_session_id:
                last_message_text = latest_local_assistant_message_for_session(
                    config,
                    normalized_session_id,
                    min_mtime=command_started_at,
                    min_entry_ts=command_started_at,
                )
                if last_message_text:
                    message_path.write_text(last_message_text, encoding="utf-8")
                    close_tmux_if_safe()
                    completed = subprocess.CompletedProcess(command, 0, last_pane_text, "")
                    return {
                        "completed": completed,
                        "session_id": normalized_session_id,
                        "message_written": True,
                        "last_message_text": last_message_text,
                    }
            detected_error = local_interactive_error_text(last_pane_text)
            if detected_error and local_interactive_prompt_is_idle(last_pane_text):
                append_log(
                    log_path,
                    f"local_interactive_error_detected tmux_session={tmux_session_name} session_id={normalized_session_id} pattern={detected_error}",
                )
                close_tmux_if_safe()
                completed = subprocess.CompletedProcess(command, 1, last_pane_text, "")
                return {
                    "completed": completed,
                    "session_id": normalized_session_id,
                    "message_written": False,
                    "last_message_text": "",
                }
            if not tmux_session_exists(config, tmux_session_name):
                break
            time.sleep(LOCAL_CODEX_INTERACTIVE_POLL_SECONDS)

        if not normalized_session_id and mode != "resume":
            thread = find_recent_local_thread_for_prompt(
                config,
                workdir=normalized_workdir,
                prompt=prompt,
                min_updated_at=command_started_at,
            )
            if thread is not None:
                normalized_session_id = str(thread.get("id", "")).strip()
        if normalized_session_id:
            last_message_text = latest_local_assistant_message_for_session(
                config,
                normalized_session_id,
                min_mtime=command_started_at,
                min_entry_ts=command_started_at,
            )
            if last_message_text:
                message_path.write_text(last_message_text, encoding="utf-8")
                close_tmux_if_safe()
                completed = subprocess.CompletedProcess(command, 0, last_pane_text, "")
                return {
                    "completed": completed,
                    "session_id": normalized_session_id,
                    "message_written": True,
                    "last_message_text": last_message_text,
                }
        timed_out = time.time() >= deadline
        append_log(
            log_path,
            f"local_interactive_exit mode={mode} tmux_session={tmux_session_name} session_id={normalized_session_id} timed_out={timed_out} error_pattern={detected_error}",
        )
        close_tmux_if_safe()
        completed = subprocess.CompletedProcess(
            command,
            124 if timed_out else 1,
            last_pane_text,
            "timed out waiting for interactive Codex reply" if timed_out else "",
        )
        return {
            "completed": completed,
            "session_id": normalized_session_id,
            "message_written": False,
            "last_message_text": "",
        }
    finally:
        if operation_id:
            clear_active_feedback_runtime(config, operation_id)


def validate_env_key(key: str) -> str:
    text = str(key).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ValueError(f"Invalid environment variable name: {key}")
    return text


def build_remote_ssh_command(spec: dict[str, Any]) -> list[str]:
    remote_workdir = normalize_posix_workdir(str(spec.get("remote_workdir", "")).strip())
    if not remote_workdir:
        raise ValueError(f"Task {spec.get('task_id', '')} is missing remote_workdir")
    remote_env: dict[str, str] = {}
    default_env = spec.get("executor_default_env", {})
    if isinstance(default_env, dict):
        remote_env.update({validate_env_key(key): str(value) for key, value in default_env.items()})
    env = spec.get("env", {})
    if isinstance(env, dict):
        remote_env.update({validate_env_key(key): str(value) for key, value in env.items()})
    script_lines = ["set -e", f"cd {shlex.quote(remote_workdir)}"]
    for key, value in sorted(remote_env.items()):
        script_lines.append(f"export {key}={shlex.quote(value)}")
    script_lines.append(f"exec bash -lc {shlex.quote(str(spec.get('command', '')))}")
    remote_script = "\n".join(script_lines)
    return build_executor_ssh_command(spec, remote_script)


def build_executor_ssh_command(spec: dict[str, Any], remote_script: str) -> list[str]:
    target = str(spec.get("executor_target", "")).strip()
    if not target:
        raise ValueError(f"Task {spec.get('task_id', '')} is missing executor_target")
    ssh_command = ["ssh"]
    identity_file = str(spec.get("executor_identity_file", "")).strip()
    if identity_file:
        ssh_command.extend(["-i", identity_file])
    ssh_options = [str(item) for item in spec.get("executor_ssh_options", []) if str(item).strip()]
    if ssh_options:
        ssh_command.extend(ssh_options)
    else:
        ssh_command.extend(["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
    ssh_command.extend(["-T", target, shlex.join(["bash", "-lc", remote_script])])
    return ssh_command


def should_use_executor_codex(spec: dict[str, Any] | None) -> bool:
    if not isinstance(spec, dict):
        return False
    return str(spec.get("execution_mode", "")).strip() == "ssh_shell" and bool(str(spec.get("executor_target", "")).strip())


def executor_remote_home(spec: dict[str, Any]) -> str:
    explicit = normalize_posix_workdir(str(spec.get("executor_remote_home", "")).strip())
    if explicit:
        return explicit
    prefix = normalize_posix_workdir(str(spec.get("executor_remote_workdir_prefix", "")).strip())
    if prefix:
        return prefix
    remote_workdir = normalize_posix_workdir(str(spec.get("remote_workdir", "")).strip())
    if not remote_workdir:
        return ""
    parts = [part for part in remote_workdir.split("/") if part]
    if len(parts) >= 2:
        return "/" + "/".join(parts[:2])
    return remote_workdir


def executor_remote_codex_home(spec: dict[str, Any]) -> str:
    explicit = normalize_posix_workdir(str(spec.get("executor_remote_codex_home", "")).strip())
    if explicit:
        return explicit
    remote_home = executor_remote_home(spec)
    if remote_home:
        return posixpath.join(remote_home, ".codex")
    return ""


def executor_remote_codex_bin(spec: dict[str, Any]) -> str:
    return str(spec.get("executor_remote_codex_bin", "codex")).strip() or "codex"


def build_remote_codex_command(
    spec: dict[str, Any],
    *,
    mode: str,
    session_id: str,
    prompt: str,
    codex_exec_mode: str,
    workdir: str,
    model: str,
) -> list[str]:
    remote_workdir = normalize_posix_workdir(str(spec.get("remote_workdir", "")).strip()) or normalize_posix_workdir(workdir)
    if not remote_workdir:
        remote_workdir = executor_remote_home(spec) or "/tmp"
    remote_home = executor_remote_home(spec)
    remote_codex_home = executor_remote_codex_home(spec)
    remote_codex_bin = executor_remote_codex_bin(spec)
    safe_task_id = normalize_task_id(str(spec.get("task_id", "remote-followup")).strip() or "remote-followup")
    remote_tmp_root = posixpath.join(remote_codex_home or remote_home or "/tmp", "tmp")
    remote_output_path = posixpath.join(remote_tmp_root, f"codex-taskboard-{safe_task_id}-{mode}.last-message.txt")
    if mode == "resume":
        if not session_id:
            raise ValueError("Missing session id for remote resume mode.")
        codex_command = [
            remote_codex_bin,
            "exec",
            "resume",
            session_id,
            prompt,
            "--skip-git-repo-check",
            "-o",
            remote_output_path,
        ]
    else:
        codex_command = [
            remote_codex_bin,
            "exec",
            "-C",
            remote_workdir,
            "--skip-git-repo-check",
            "-o",
            remote_output_path,
        ]
        if model:
            codex_command.extend(["-m", model])
    if codex_exec_mode == "dangerous":
        codex_command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        codex_command.append("--full-auto")
    if mode != "resume":
        codex_command.append(prompt)
    script_lines = ["set -u"]
    if remote_home:
        script_lines.append(f"export HOME={shlex.quote(remote_home)}")
    if remote_codex_home:
        script_lines.append(f"export CODEX_HOME={shlex.quote(remote_codex_home)}")
    script_lines.append(f"mkdir -p {shlex.quote(remote_tmp_root)}")
    script_lines.append(f"rm -f {shlex.quote(remote_output_path)}")
    script_lines.append(f"cd {shlex.quote(remote_workdir)}")
    script_lines.append("set +e")
    script_lines.append(shlex.join(codex_command))
    script_lines.append("rc=$?")
    script_lines.append(f"printf '%s\\n' {shlex.quote(REMOTE_LAST_MESSAGE_BEGIN)}")
    script_lines.append(f"if [ -f {shlex.quote(remote_output_path)} ]; then cat {shlex.quote(remote_output_path)}; fi")
    script_lines.append(f"printf '\\n%s\\n' {shlex.quote(REMOTE_LAST_MESSAGE_END)}")
    script_lines.append(f"rm -f {shlex.quote(remote_output_path)}")
    script_lines.append("exit \"$rc\"")
    return build_executor_ssh_command(spec, "\n".join(script_lines))


def extract_remote_last_message(stdout_text: str) -> tuple[str, str]:
    pattern = rf"{re.escape(REMOTE_LAST_MESSAGE_BEGIN)}\n?(.*?)\n?{re.escape(REMOTE_LAST_MESSAGE_END)}"
    match = re.search(pattern, stdout_text, flags=re.DOTALL)
    if not match:
        return "", stdout_text
    message = match.group(1)
    cleaned = stdout_text[: match.start()] + stdout_text[match.end() :]
    return message, cleaned


def latest_remote_session_activity_ts(spec: dict[str, Any], session_id: str) -> float:
    if not should_use_executor_codex(spec) or not session_id:
        return 0.0
    remote_codex_home = executor_remote_codex_home(spec)
    if not remote_codex_home:
        return 0.0
    remote_home = executor_remote_home(spec)
    rollout_name = f"rollout-*-{session_id}.jsonl"
    session_root = posixpath.join(remote_codex_home, "sessions")
    archive_root = posixpath.join(remote_codex_home, "archived_sessions")
    script_lines = ["set -u"]
    if remote_home:
        script_lines.append(f"export HOME={shlex.quote(remote_home)}")
    script_lines.append(
        "latest=$( ("
        f"find {shlex.quote(session_root)} -type f -name {shlex.quote(rollout_name)} -printf '%T@\\n' 2>/dev/null; "
        f"find {shlex.quote(archive_root)} -type f -name {shlex.quote(rollout_name)} -printf '%T@\\n' 2>/dev/null"
        ") | sort -nr | head -n1 )"
    )
    script_lines.append('printf "%s\\n" "${latest:-0}"')
    completed = run_subprocess(build_executor_ssh_command(spec, "\n".join(script_lines)), cwd=str(Path.cwd()), timeout=30)
    if completed.returncode != 0:
        return 0.0
    values = str(completed.stdout).strip().splitlines()
    if not values:
        return 0.0
    try:
        return float(values[-1])
    except ValueError:
        return 0.0


def codex_session_exists_for_spec(config: AppConfig, spec: dict[str, Any], session_id: str) -> bool:
    normalized_session_id = str(session_id).strip()
    if not normalized_session_id:
        return False
    if should_use_executor_codex(spec):
        remote_codex_home = executor_remote_codex_home(spec)
        if not remote_codex_home:
            return False
        remote_home = executor_remote_home(spec)
        session_root = posixpath.join(remote_codex_home, "sessions")
        archived_root = posixpath.join(remote_codex_home, "archived_sessions")
        session_index_path = posixpath.join(remote_codex_home, "session_index.jsonl")
        script_lines = ["set -u"]
        if remote_home:
            script_lines.append(f"export HOME={shlex.quote(remote_home)}")
        script_lines.extend(
            [
                "found=0",
                f"if find {shlex.quote(session_root)} -type f -name {shlex.quote(f'rollout-*-{normalized_session_id}.jsonl')} -print -quit 2>/dev/null | grep -q .; then found=1; fi",
                f"if [ \"$found\" -eq 0 ] && find {shlex.quote(archived_root)} -type f -name {shlex.quote(f'rollout-*-{normalized_session_id}.jsonl')} -print -quit 2>/dev/null | grep -q .; then found=1; fi",
                f"if [ \"$found\" -eq 0 ] && [ -f {shlex.quote(session_index_path)} ] && grep -Fq {shlex.quote(normalized_session_id)} {shlex.quote(session_index_path)}; then found=1; fi",
                'printf "%s\\n" "$found"',
            ]
        )
        completed = run_subprocess(build_executor_ssh_command(spec, "\n".join(script_lines)), cwd=str(Path.cwd()), timeout=30)
        if completed.returncode != 0:
            return False
        return str(completed.stdout).strip().splitlines()[-1:] == ["1"]
    return find_thread_info(config, normalized_session_id) is not None or latest_session_activity_ts(config, normalized_session_id) > 0


def api_submit_hooks() -> ApiSubmitHooks:
    return ApiSubmitHooks(
        normalize_cpu_profile=normalize_cpu_profile,
        parse_gpu_id_list=parse_gpu_id_list,
        extract_raw_proposal_value=extract_raw_proposal_value,
        extract_raw_closeout_proposal_dir=extract_raw_closeout_proposal_dir,
        extract_raw_project_history_file=extract_raw_project_history_file,
        apply_executor_to_spec=apply_executor_to_spec,
        codex_session_exists_for_spec=codex_session_exists_for_spec,
        missing_sentinel=MISSING,
        default_startup_failure_seconds=DEFAULT_STARTUP_FAILURE_SECONDS,
    )


def apply_api_token_submit_policy(
    config: AppConfig,
    *,
    token_record: dict[str, Any],
    spec: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return apply_api_token_submit_policy_impl(
        config,
        token_record=token_record,
        spec=spec,
        payload=payload,
        hooks=api_submit_hooks(),
    )


def newest_matches(pattern: str, workdir: str) -> list[Path]:
    expanded = pattern
    if not os.path.isabs(expanded):
        expanded = str(Path(workdir) / pattern)
    matches = [Path(item) for item in glob.glob(expanded, recursive=True) if Path(item).is_file()]
    matches.sort(key=lambda item: (item.stat().st_mtime_ns, str(item)))
    return matches


def summarize_json_payload(payload: dict[str, Any]) -> str:
    fields: list[str] = []
    keys = [
        "status",
        "phase",
        "step",
        "dataset",
        "dataset_name",
        "epoch",
        "best_metric",
        "loss",
        "accuracy",
        "reward_mean",
        "exact_match",
        "pass_at_1",
        "pass_at_2",
        "pass_at_4",
        "decision_reason",
    ]
    for key in keys:
        if key in payload:
            fields.append(f"{key}={payload[key]}")
    if fields:
        return "; ".join(fields)
    sample_keys = sorted(payload.keys())[:8]
    return "keys=" + ",".join(sample_keys)


def tail_text(path: Path, *, max_lines: int, max_chars: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    text = "\n".join(lines[-max_lines:])
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def summarize_artifact(path: Path, *, max_lines: int, max_chars: int) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = read_json(path, {})
        if isinstance(payload, dict) and payload:
            return summarize_json_payload(payload)[:max_chars]
    if suffix == ".jsonl":
        try:
            lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
        except Exception:
            return ""
        if not lines:
            return ""
        try:
            payload = json.loads(lines[-1])
        except Exception:
            return lines[-1][:max_chars]
        if isinstance(payload, dict):
            return summarize_json_payload(payload)[:max_chars]
        return str(payload)[:max_chars]
    return tail_text(path, max_lines=max_lines, max_chars=max_chars)


def get_gpu_process_table() -> dict[int, int]:
    if shutil_which("nvidia-smi") == "":
        return {}
    completed = run_subprocess(
        ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
        timeout=15,
    )
    if completed.returncode != 0:
        return {}
    table: dict[int, int] = {}
    for line in completed.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = [item.strip() for item in raw.split(",")]
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            memory = int(parts[1])
        except ValueError:
            continue
        table[pid] = table.get(pid, 0) + memory
    return table


def get_gpu_summary_table() -> list[dict[str, Any]]:
    if shutil_which("nvidia-smi") == "":
        return []
    completed = run_subprocess(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        timeout=15,
    )
    if completed.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = [item.strip() for item in raw.split(",")]
        if len(parts) < 5:
            continue
        try:
            rows.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "memory_total_mb": int(parts[2]),
                    "memory_used_mb": int(parts[3]),
                    "gpu_util_percent": int(parts[4]),
                }
            )
        except ValueError:
            continue
    return rows


def looks_like_training_command(cmd: str) -> bool:
    lower = cmd.lower()
    for pattern in TRAINING_EXCLUDE_PATTERNS:
        if re.search(pattern, lower):
            return False
    for pattern in TRAINING_PATTERNS:
        if re.search(pattern, lower):
            return True
    return False


def list_training_processes(limit: int) -> list[dict[str, Any]]:
    completed = run_subprocess(
        ["ps", "-eo", "pid=,ppid=,stat=,etime=,%cpu=,%mem=,args="],
        timeout=20,
    )
    if completed.returncode != 0:
        return []
    gpu_table = get_gpu_process_table()
    processes: list[dict[str, Any]] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            cpu_percent = float(parts[4])
            mem_percent = float(parts[5])
        except ValueError:
            continue
        cmd = parts[6]
        if not looks_like_training_command(cmd):
            continue
        processes.append(
            {
                "pid": pid,
                "ppid": ppid,
                "stat": parts[2],
                "etime": parts[3],
                "cpu_percent": cpu_percent,
                "mem_percent": mem_percent,
                "gpu_memory_mb": gpu_table.get(pid, 0),
                "cmd": cmd,
                "cwd": read_pid_cwd(pid),
                "proc_state": read_pid_state(pid),
            }
        )
    processes.sort(key=lambda item: (-int(item["gpu_memory_mb"]), -float(item["cpu_percent"]), item["pid"]))
    return processes[:limit]


def collect_artifact_context(spec: dict[str, Any]) -> list[dict[str, str]]:
    workdir = spec["workdir"]
    artifact_globs = spec.get("artifact_globs", []) or []
    summaries: list[dict[str, str]] = []
    max_chars = int(spec.get("artifact_max_chars", 1200))
    max_lines = int(spec.get("artifact_max_lines", 40))
    for pattern in artifact_globs:
        matches = newest_matches(pattern, workdir)
        if not matches:
            summaries.append({"pattern": pattern, "path": "", "summary": "no match"})
            continue
        newest = matches[-1]
        summaries.append(
            {
                "pattern": pattern,
                "path": str(newest),
                "summary": summarize_artifact(newest, max_lines=max_lines, max_chars=max_chars),
            }
        )
    return summaries


def parse_key_value_lines(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            continue
        payload[key] = value.strip()
    return payload


def extract_taskboard_protocol_footer(text: str) -> dict[str, Any]:
    payload = parse_key_value_lines(text or "")
    signal_value = canonicalize_taskboard_signal(str(payload.get("TASKBOARD_SIGNAL", "")).strip())
    self_check = str(payload.get("TASKBOARD_SELF_CHECK", "")).strip()
    live_task_status = str(payload.get("LIVE_TASK_STATUS", "")).strip()
    effective_research_phase = infer_taskboard_research_phase(final_signal=signal_value)
    valid = bool(
        signal_value in TASKBOARD_PUBLIC_SIGNAL_VALUES
        and self_check in {"pass", "fail"}
        and live_task_status in TASKBOARD_LIVE_TASK_STATUS_VALUES
    )
    return {
        "effective_research_phase": effective_research_phase,
        "self_check": self_check,
        "live_task_status": live_task_status,
        "signal": signal_value,
        "valid": valid,
    }


def summarize_taskboard_protocol_issue(protocol: dict[str, Any] | None, *, signal_value: str = "") -> str:
    payload = protocol if isinstance(protocol, dict) else {}
    self_check = str(payload.get("self_check", "")).strip()
    live_task_status = str(payload.get("live_task_status", "")).strip()
    footer_signal = canonicalize_taskboard_signal(str(payload.get("signal", "")).strip())
    issues: list[str] = []
    if footer_signal not in TASKBOARD_PUBLIC_SIGNAL_VALUES:
        issues.append("missing_or_wrong_signal")
    if self_check not in {"pass", "fail"}:
        issues.append("missing_or_wrong_self_check")
    elif self_check == "fail":
        issues.append("self_check_fail")
    if live_task_status not in TASKBOARD_LIVE_TASK_STATUS_VALUES:
        issues.append("missing_or_wrong_live_task_status")
    if signal_value and footer_signal and footer_signal not in {canonicalize_taskboard_signal(signal_value), "none"}:
        issues.append(f"signal_mismatch:{footer_signal}->{canonicalize_taskboard_signal(signal_value)}")
    return ",".join(issues or ["missing_protocol_footer"])


def taskboard_protocol_requires_repair(protocol: dict[str, Any] | None, *, signal_value: str = "") -> bool:
    payload = protocol if isinstance(protocol, dict) else {}
    if signal_value:
        return False
    if not bool(payload.get("valid", False)):
        return True
    return str(payload.get("self_check", "")).strip() != "pass"


def protocol_footer_snapshot(protocol: dict[str, Any] | None) -> dict[str, Any]:
    payload = protocol if isinstance(protocol, dict) else {}
    return {
        "effective_research_phase": str(payload.get("effective_research_phase", "")).strip(),
        "signal": str(payload.get("signal", "")).strip(),
        "self_check": str(payload.get("self_check", "")).strip(),
        "live_task_status": str(payload.get("live_task_status", "")).strip(),
        "valid": bool(payload.get("valid", False)),
    }


def parse_json_line(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def extract_structured_report(spec: dict[str, Any], log_text: str, artifact_context: list[dict[str, str]]) -> tuple[dict[str, Any], str]:
    report_format = str(spec.get("report_format", "auto")).strip() or "auto"
    report_keys = [str(item).strip() for item in spec.get("report_keys", []) if str(item).strip()]
    payload: dict[str, Any] = {}

    if report_format == "json-line":
        payload = parse_json_line(log_text)
    elif report_format == "key-value":
        payload = parse_key_value_lines(log_text)
    elif report_format == "artifact-json":
        for item in artifact_context:
            path = item.get("path", "")
            if not path:
                continue
            artifact_payload = read_json(Path(path), {})
            if isinstance(artifact_payload, dict) and artifact_payload:
                payload = artifact_payload
                break
    else:
        payload = parse_json_line(log_text)
        if not payload:
            payload = parse_key_value_lines(log_text)
        if not payload:
            for item in artifact_context:
                path = item.get("path", "")
                if not path:
                    continue
                artifact_payload = read_json(Path(path), {})
                if isinstance(artifact_payload, dict) and artifact_payload:
                    payload = artifact_payload
                    break

    if report_keys and payload:
        payload = {key: payload[key] for key in report_keys if key in payload}

    if payload:
        summary_parts = [f"{key}={payload[key]}" for key in list(payload.keys())[:6]]
        return payload, "; ".join(summary_parts)
    return {}, ""


def extract_failure_excerpt(log_text: str, *, status: str, failure_kind: str) -> str:
    if status == "completed":
        return ""
    lines = [line.rstrip() for line in log_text.splitlines() if line.strip()]
    if not lines:
        return ""
    lowered = [line.lower() for line in lines]

    traceback_index = -1
    for index, line in enumerate(lowered):
        if "traceback" in line:
            traceback_index = index
    if traceback_index >= 0:
        return "\n".join(lines[traceback_index:][:24])[:2400]

    matched_indices = [
        index
        for index, line in enumerate(lowered)
        if any(pattern in line for pattern in FAILURE_EXCERPT_PATTERNS)
    ]
    if matched_indices:
        start = max(0, matched_indices[0] - 2)
        end = min(len(lines), matched_indices[-1] + 8)
        return "\n".join(lines[start:end])[:2400]

    if failure_kind in {"oom", "sigkill", "external_termination", "launch_error", "python_traceback", "runtime_exception", "nonzero_exit"}:
        return "\n".join(lines[-20:])[:2400]
    return ""


def build_default_resume_instruction(status: str) -> str:
    if status == "completed":
        return (
            "请检查这次已完成运行的真实工件，判断它怎样改变了当前 proposal 的结论边界、下一步实验方向和优先级。"
            "如果现在更适合收紧路线或准备收口，也请把原因和边界明确写回 proposal。"
        )
    if status == "terminated":
        return (
            "请把这次任务视为一次中断运行，先判断它为什么停下，以及这到底是执行问题还是研究路线问题。"
            "如果当前方向需要收紧或改写，也请把原因、边界与替代动作写回 proposal。"
        )
    if status == "observed_exit":
        return (
            "被监控的外部 PID 已经消失。请检查日志和产物，判断它是正常结束还是崩溃退出。"
            "若当前方向不宜继续，也请把原因、边界与替代方向写回 proposal。"
        )
    if status == "launch_failed":
        return (
            "后台任务没有成功启动。请先诊断启动器或提交流水线的问题，并检查是否存在应清理或改派的无效排队项。"
        )
    return (
        "请把这次任务视为一次失败运行，检查 traceback 或最后日志，判断这是偶发执行失败，还是说明当前路线需要收紧或切换。"
        "如果当前路线不值得继续，也请把失败原因、边界与替代方向写回 proposal。"
    )


def build_standard_followup_prompt(spec: dict[str, Any], *, continuous_research_enabled: bool) -> str:
    if continuous_research_enabled:
        return build_continuous_research_prompt(spec)
    prompt_lines = compact_research_governance_header_lines(
        spec,
        continuous_mode=False,
    )
    prompt_lines.extend(
        [
            "",
            "这是一次 managed 模式的跟进提示。默认目标是把新增结果并回当前主线，继续推进当前上下文里能做完的工作；managed 只托管任务和 backlog，不会自动再把这段对话拆成额外短步骤。",
        ]
    )
    prompt_lines.extend(runtime_canonical_head_prompt_lines(spec))
    next_action_lines = recent_project_history_next_action_prompt_lines(spec)
    if next_action_lines:
        prompt_lines.append("")
        prompt_lines.extend(next_action_lines)
    prompt_lines.extend(["", *evidence_first_loop_lines()])
    prompt_lines.extend(unified_execution_closure_lines())
    prompt_lines.extend(
        execution_followthrough_instruction_lines(
            spec,
            allow_no_further_tasks=True,
            profile=PROMPT_PROFILE_RESUME_COMPACT,
        )
    )
    prompt_lines.extend(["", *taskboard_footer_contract_lines()])
    return join_prompt_lines(prompt_lines)


def unified_execution_closure_lines() -> list[str]:
    return [
        "",
        "这一轮默认使用统一 execution 上下文：结果回流吸收、代码和数据审计、局部修复、proposal/history 滚动写回、实验包准备与提交尽量在同一轮完成。",
        "本轮决策顺序：",
        "1. 先吸收结果回流 / summary / report / log / 结果文件，提炼关键数字、异常点和它们意味着什么。",
        "2. 只要结果、日志、参数、样本数、吞吐或显存异常，或者和 history、文献、官方推荐参数冲突，就先审代码、数据、配置、split 与运行完整性。",
        "3. 当前上下文里能完成的局部修复、数据处理、smoke 前置和实验包补齐，直接在这一轮做完。",
        "4. 把可靠结论、失败边界、关键诊断与下一步明确动作及时写回 proposal/history，写得让三天后的你和下一位 agent 都能看懂。",
        "5. 只有当实验包已经可执行、可审计，而且确实需要 GPU / remote / 长等待时，才提交 taskboard 任务。",
    ]


def build_unified_execution_prompt(
    spec: dict[str, Any],
    *,
    trigger_signal: str = "",
    parked_origin: bool = False,
) -> str:
    del parked_origin
    normalized_trigger_signal = canonicalize_taskboard_signal(str(trigger_signal or "").strip())
    next_action_hint = controller_continuation_hint_from_spec(spec)
    manual_gate_hints = proposal_manual_decision_gate_hints(spec)
    lines = compact_research_governance_header_lines(spec, continuous_mode=True)
    lines.extend(
        [
            "",
            *prompt_block_lines("execution_scene_intro"),
            "没有人工干预时，你需要比较几条备选路径，主动选择当前信息增益最高的一步，并说明为什么这样选。",
            (
                f"上一轮信号: TASKBOARD_SIGNAL={normalized_trigger_signal}。"
                if normalized_trigger_signal
                else "这是一次 continuous execution 跟进；优先把当前上下文里还能直接完成的分析、审计、修复和写回继续做深。"
            ),
        ]
    )
    lines.extend(runtime_canonical_head_prompt_lines(spec))
    next_action_lines = recent_project_history_next_action_prompt_lines(spec)
    if next_action_lines:
        lines.append("")
        lines.extend(next_action_lines)
    if bool(next_action_hint.get("dispatch_ready", False)):
        lines.extend(
            [
                "",
                "taskboard 读到的当前重点：实验包已经接近可提交状态。不要把还差一点点的本地准备项写成 blocker；先补齐，再判断是否该正式提交。",
                f"- 线索: {next_action_hint.get('dispatch_ready_reason', '')}",
            ]
        )
    if bool(next_action_hint.get("direct_local_artifact", False)):
        lines.extend(
            [
                "",
                "taskboard 读到的当前重点：下一步更像是本地配置、runner 或实验包落盘。请直接把这些工件写出来，不要只复述动作名。",
                f"- 线索: {next_action_hint.get('direct_local_artifact_reason', '')}",
            ]
        )
    if bool(next_action_hint.get("collect_local_evidence", False)):
        lines.extend(
            [
                "",
                "taskboard 读到的当前重点：最近还有结果文件或回流摘要没有被 proposal/history 吸收。请先把这批证据并回主线，再决定下一步。",
                f"- 线索: {next_action_hint.get('collect_local_evidence_reason', '')}",
            ]
        )
    if manual_gate_hints:
        lines.extend(
            [
                "",
                "proposal 里看起来出现了人工确认点。不要因为看到 manual / handoff 字样就立刻停住；先核对相关证据、日志和 proposal 段落，再判断它是不是硬阻塞。",
                "如果最终仍必须人工确认，请把等待的具体决定、默认建议、预算影响和阻塞原因写清楚。",
                "proposal 中检测到的相关片段：",
            ]
        )
        lines.extend([f"- {hint}" for hint in manual_gate_hints])
    lines.extend(unified_execution_closure_lines())
    lines.extend(
        execution_followthrough_instruction_lines(
            spec,
            allow_no_further_tasks=False,
            profile=PROMPT_PROFILE_RESUME_COMPACT,
        )
    )
    lines.extend(["", *taskboard_footer_contract_lines()])
    return join_prompt_lines(lines)


def build_parked_watchdog_prompt(spec: dict[str, Any], *, trigger_signal: str) -> str:
    return build_unified_execution_prompt(spec, trigger_signal=trigger_signal)


def build_continuous_planning_prompt(
    spec: dict[str, Any],
    *,
    trigger_signal: str = "",
    successor_bootstrap: bool = False,
    predecessor_session_id: str = "",
) -> str:
    normalized_trigger_signal = canonicalize_taskboard_signal(str(trigger_signal or "").strip())
    lines = compact_research_governance_header_lines(spec, continuous_mode=True)
    if successor_bootstrap:
        lines.extend(["", *prompt_block_lines("successor_bootstrap_intro")])
        normalized_predecessor = str(predecessor_session_id or "").strip()
        if normalized_predecessor:
            lines.append(f"上一轮已收口的 session: `{normalized_predecessor}`。")
    lines.extend(
        [
            "",
            *prompt_block_lines("planning_scene_intro"),
            (
                "这是新的 Codex session。请直接接住上一轮留下的 history、handoff、proposal 和关键结果，刷新当前最优 proposal，并说明为什么这样选。"
                if successor_bootstrap
                else "没有人工干预时，你需要把上一阶段留下的证据、history、handoff 与必要文献放在一起比较，再主动形成当前最优 proposal，并说明为什么这样选。"
            ),
            (
                f"上一轮信号: TASKBOARD_SIGNAL={normalized_trigger_signal}。"
                if normalized_trigger_signal
                else "当前要把精力放在刷新 proposal 和准备首个验证包，而不是继续证明上一轮已经结束。"
            ),
            "planning 完成标准不是写完一份空文档，而是形成一份可执行、可审计、可分发的 proposal。",
        ]
    )
    lines.extend(runtime_canonical_head_prompt_lines(spec))
    next_action_lines = recent_project_history_next_action_prompt_lines(spec)
    if next_action_lines:
        lines.append("")
        lines.extend(next_action_lines)
    lines.extend(
        [
            "",
            "本轮决策顺序：",
            "1. 先把上一阶段留下的结果、失败边界、未解问题和必须继承的文件整理成当前 planning 的输入；如果主线真的进入新的方法方向，就补读最关键的旧文献与近年的代表性新工作。",
            "2. 写或刷新当前 proposal：明确 benchmark/数据集、比较对象、核心假设、实验设计、实现要点、验证指标、停止条件和风险边界。",
            "3. 如果还缺本地 CPU 审计、脚本或配置落盘、smoke 前置或首个实验包，就在当前对话里补齐，不要把这些准备动作外包成新的阶段。",
            (
                f"4. planning 不要用 `TASKBOARD_SIGNAL=none` 停住；当 proposal 和首批实验包已经准备到可执行、可审计、可分发时，输出 `TASKBOARD_SIGNAL={EXECUTION_READY_SIGNAL}`。如果你已经在这一轮里提交了 live task 并开始等待回流，就输出 `TASKBOARD_SIGNAL={WAITING_ON_ASYNC_SIGNAL}`。"
                if successor_bootstrap
                else f"4. 当 proposal 和首批实验包已经准备到可执行、可审计、可分发时，输出 `TASKBOARD_SIGNAL={EXECUTION_READY_SIGNAL}`，把链路推进到 execution。"
            ),
            "",
            "taskboard 使用说明：",
            "- planning 不要停在“材料差一点”。当前上下文里还能补齐的审计、配置和 smoke 前置，就直接补齐。",
        ]
    )
    submit_line = submit_binding_instruction_line(spec)
    if submit_line:
        lines.append(f"- {submit_line}")
    lines.extend(
        [
            "",
            *taskboard_footer_contract_lines(),
        ]
    )
    return join_prompt_lines(lines)


def build_successor_bootstrap_prompt(
    spec: dict[str, Any],
    *,
    predecessor_session_id: str = "",
    trigger_signal: str = "",
) -> str:
    return build_continuous_planning_prompt(
        spec,
        trigger_signal=trigger_signal,
        successor_bootstrap=True,
        predecessor_session_id=predecessor_session_id,
    )


def build_materials_ready_for_proposal_prompt(spec: dict[str, Any], *, trigger_signal: str = "") -> str:
    return build_continuous_planning_prompt(spec, trigger_signal=trigger_signal or EXECUTION_READY_SIGNAL)


def build_continuous_research_prompt(spec: dict[str, Any], *, trigger_signal: str = "") -> str:
    normalized_trigger_signal = canonicalize_taskboard_signal(str(trigger_signal or "").strip())
    next_action_hint = controller_continuation_hint_from_spec(spec)
    prompt_phase = effective_research_phase_for_session(
        {
            **spec,
            "last_signal": normalized_trigger_signal or str(spec.get("last_signal", "")).strip(),
            "waiting_state": normalized_trigger_signal or str(spec.get("waiting_state", "")).strip(),
        },
        next_action_hint=next_action_hint,
        effective_wait_state=normalized_trigger_signal,
    )
    if normalized_trigger_signal == CLOSEOUT_READY_SIGNAL:
        return build_continuous_transition_prompt(spec, trigger_signal=normalized_trigger_signal)
    if prompt_phase == "planning":
        return build_continuous_planning_prompt(spec, trigger_signal=normalized_trigger_signal)
    return build_unified_execution_prompt(spec, trigger_signal=normalized_trigger_signal)


def build_continuous_transition_prompt(spec: dict[str, Any], *, trigger_signal: str = "") -> str:
    normalized_trigger_signal = canonicalize_taskboard_signal(str(trigger_signal or "").strip())
    lines = compact_research_governance_header_lines(spec, continuous_mode=True)
    lines.extend(
        [
            "",
            *prompt_block_lines("closeout_scene_intro"),
            "没有人工干预时，你要把这一阶段加工成下一轮最稳妥的起点，并说明为什么现在应该收口，而不是继续扩展当前 proposal。",
            (
                f"上一轮信号: TASKBOARD_SIGNAL={normalized_trigger_signal}。"
                if normalized_trigger_signal
                else "当前 proposal 已进入收口阶段。"
            ),
            "请不要把 closeout 当成偷懒出口；只有在 execution 中已经写明“为什么继续下去没有信息收益”后，closeout 才成立。",
        ]
    )
    lines.extend(runtime_canonical_head_prompt_lines(spec))
    next_action_lines = recent_project_history_next_action_prompt_lines(spec)
    if next_action_lines:
        lines.append("")
        lines.extend(next_action_lines)
    lines.extend(
        [
            "",
            "本轮决策顺序：",
            "1. 对当前 proposal 做全量数据统计、结果处理、内容总结，并且说人话：写清具体做了什么、得到了什么结果、这些 benchmark 数字在科学意义上说明了什么、对总体主线有什么影响。",
            "2. 把当前 proposal 的关键结论、失败边界、时间戳和查询路径写入 project history，附上 proposal、report、日志、结果文件、handoff 等文件地址。",
            "3. 写一份说人话的 handoff 文档：说明项目背景和现状、当前主线、待解决问题、必须阅读的文件，以及建议后续查阅哪些顶刊顶会方向；文献只能借鉴灵感，创新点必须从我们自己的结果里生长出来。",
            "4. 做一次 handoff 确认 / binding 确认：明确上一轮 proposal、当前 closeout 文档、history 路径，以及下一阶段 planning 应继承的 proposal/handoff 入口，避免错绑。",
            "5. 完成 closeout 后，本轮 `TASKBOARD_SIGNAL` 默认写 `none`；taskboard 会冻结当前 session，并基于 handoff 强制开启新的 Codex session 进入下一轮 planning。",
        ]
    )
    lines.extend(["", "taskboard 使用说明："])
    submit_line = submit_binding_instruction_line(spec)
    if submit_line:
        lines.append(f"- {submit_line}")
    lines.append("- closeout 期间如果你发现还有一小步低风险、高信息增益、并且当前上下文里就能完成的动作，不要硬收口，说明理由后回到 execution。")
    lines.extend(["", *taskboard_footer_contract_lines()])
    return join_prompt_lines(lines)


def build_resume_intro(execution_mode: str, spec: dict[str, Any] | None = None) -> list[str]:
    resume_source = "一个已完成的 Codex subagent" if execution_mode == "codex_subagent" else "一个已停止的后台任务"
    return prompt_block_lines("resume_intro", resume_source=resume_source)


def truncate_prompt_field(value: Any, *, max_chars: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def build_resume_event_detail_lines(spec: dict[str, Any], event: dict[str, Any]) -> list[str]:
    lines: list[str] = [
        "任务摘要：",
        f"task_id: {spec['task_id']}",
        f"status: {event['status']}",
        f"workdir: {prompt_path_marker(spec['workdir'])}",
    ]
    command_preview = truncate_prompt_field(spec.get("command", ""), max_chars=180)
    if command_preview:
        lines.append(f"command_preview: {command_preview}")
    if event.get("failure_kind"):
        lines.append(f"failure_kind: {event['failure_kind']}")
    if event.get("failure_summary"):
        lines.append(f"failure_summary: {truncate_prompt_field(event['failure_summary'], max_chars=220)}")
    if event.get("duration_seconds") is not None:
        lines.append(f"duration_seconds: {event['duration_seconds']}")
    lines.append(f"command_log: {prompt_path_marker(event['command_log_path'])}")
    if event.get("feedback_data_path"):
        lines.append(f"feedback_data_file: {prompt_path_marker(event.get('feedback_data_path'))}")
    if event.get("runner_log_path"):
        lines.append(f"runner_log: {prompt_path_marker(event.get('runner_log_path'))}")
    if spec.get("remote_workdir"):
        lines.append(f"remote_workdir: {prompt_path_marker(spec.get('remote_workdir'))}")
    if spec.get("executor_name"):
        lines.append(f"executor_name: {spec.get('executor_name')}")
    assigned_gpus = parse_gpu_id_list(event.get("assigned_gpus", spec.get("assigned_gpus", [])))
    if assigned_gpus:
        lines.append(f"assigned_gpus: {','.join(str(item) for item in assigned_gpus)}")
    if event.get("queued_at"):
        lines.append(f"queued_at: {event.get('queued_at')}")
    if event.get("queued_reason"):
        lines.append(f"queued_reason: {event.get('queued_reason')}")
    if event.get("rejected_reason"):
        lines.append(f"rejected_reason: {event.get('rejected_reason')}")
    if spec.get("execution_mode") == "external_pid":
        lines.append(f"watched_pid: {spec.get('watch_pid')}")
        if event.get("watch_log_path") or spec.get("watch_log_path"):
            lines.append(
                f"watched_log_path: {prompt_path_marker(event.get('watch_log_path') or spec.get('watch_log_path'))}"
            )
    if spec.get("execution_mode") == "codex_subagent":
        lines.append(f"subagent_model: {event.get('subagent_model') or spec.get('subagent_model', 'gpt-5.4')}")
        if event.get("subagent_session_id"):
            lines.append(f"subagent_session_id: {event.get('subagent_session_id')}")
        lines.append(f"subagent_message_written: {event.get('subagent_message_written', False)}")
        if event.get("subagent_last_message_path"):
            lines.append(
                f"subagent_last_message_path: {prompt_path_marker(event.get('subagent_last_message_path'))}"
            )
        if event.get("continue_attempts"):
            lines.append(f"subagent_continue_attempts: {event.get('continue_attempts')}")
        if event.get("recovered_with_continue"):
            lines.append("subagent_recovered_with_continue: true")
    if event.get("exit_code") is not None:
        lines.append(f"exit_code: {event['exit_code']}")
    if event.get("exit_signal"):
        lines.append(f"exit_signal: {event['exit_signal']}")
    if event.get("taskboard_signal"):
        lines.append(f"taskboard_signal: {event['taskboard_signal']}")
    if event.get("needs_attention"):
        lines.append(f"needs_attention: {event['needs_attention']}")
    if event.get("attention_message"):
        lines.append(f"attention_message: {truncate_prompt_field(event['attention_message'], max_chars=220)}")
    if spec.get("task_note"):
        lines.append(f"task_note: {truncate_prompt_field(spec['task_note'], max_chars=180)}")
    return lines


def build_resume_safety_lines() -> list[str]:
    return ["", *prompt_block_lines("safety_notice")]


def build_resume_artifact_lines(event: dict[str, Any]) -> list[str]:
    artifact_context = event.get("artifact_context", [])
    if not artifact_context:
        return []
    lines = [
        "",
        "结果文件路径：",
    ]
    display_limit = 4
    for item in artifact_context[:display_limit]:
        path = item["path"] or "(no match)"
        display_path = prompt_path_marker(path) if path != "(no match)" else path
        lines.append(f"- pattern: {item['pattern']} | path: {display_path}")
    if len(artifact_context) > display_limit:
        lines.append(f"- ... {len(artifact_context) - display_limit} more artifacts omitted")
    return lines


def truncate_prompt_preserving_tail(head: str, tail: str, max_chars: int) -> str:
    normalized_max_chars = max(1, int(max_chars or 1))
    combined = "\n\n".join(part for part in [head.strip(), tail.strip()] if part.strip()).strip()
    if len(combined) <= normalized_max_chars:
        return combined
    trimmed_tail = tail.strip()
    if not trimmed_tail:
        return combined[: normalized_max_chars - 1]
    if len(trimmed_tail) >= normalized_max_chars:
        priority_markers = [
            "安全说明：",
            "后续动作指令：",
            "TASKBOARD_SIGNAL=",
        ]
        for marker in priority_markers:
            marker_index = trimmed_tail.find(marker)
            if marker_index < 0:
                continue
            candidate = trimmed_tail[marker_index:].strip()
            if len(candidate) <= normalized_max_chars:
                return candidate
        return trimmed_tail[-(normalized_max_chars - 1):].strip()
    separator = "\n\n" if head.strip() else ""
    available_head_chars = normalized_max_chars - len(trimmed_tail) - len(separator)
    if available_head_chars <= 0:
        return trimmed_tail[-(normalized_max_chars - 1):].strip()
    trimmed_head = head.strip()[:available_head_chars].rstrip()
    if not trimmed_head:
        return trimmed_tail[-(normalized_max_chars - 1):].strip()
    return f"{trimmed_head}{separator}{trimmed_tail}".strip()

def queued_notification_resume_context(entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    spec_snapshot = entry.get("resume_spec", {})
    event_snapshot = entry.get("resume_event", {})
    if not isinstance(spec_snapshot, dict):
        spec_snapshot = {}
    if not isinstance(event_snapshot, dict):
        event_snapshot = {}
    return spec_snapshot, event_snapshot


def resume_prompt_context_spec(spec: dict[str, Any], queued_notifications: list[dict[str, Any]]) -> dict[str, Any]:
    context_spec = dict(spec)
    binding_fields = (
        "proposal_path",
        "proposal_source",
        "closeout_proposal_dir",
        "closeout_proposal_dir_source",
        "project_history_file",
        "project_history_file_source",
    )
    has_binding = any(str(context_spec.get(field, "")).strip() for field in binding_fields) or bool(context_spec.get("proposal_owner", False))
    if has_binding:
        return context_spec
    for entry in queued_notifications:
        entry_spec, _ = queued_notification_resume_context(entry)
        if not entry_spec:
            continue
        for field in binding_fields:
            value = entry_spec.get(field, "")
            if value and not str(context_spec.get(field, "")).strip():
                context_spec[field] = value
        if not context_spec.get("proposal_owner", False) and entry_spec.get("proposal_owner", False):
            context_spec["proposal_owner"] = True
    return context_spec


def build_batched_resume_notification_block(entry: dict[str, Any], *, index: int, entry_count: int) -> str:
    spec_snapshot, event_snapshot = queued_notification_resume_context(entry)
    if not spec_snapshot or not event_snapshot:
        prompt = str(entry.get("prompt", "")).strip() or "(missing queued prompt)"
        return f"===== 合并任务更新 {index}/{entry_count} =====\n{prompt}".strip()
    lines = [f"===== 合并任务更新 {index}/{entry_count} ====="]
    lines.extend(build_resume_event_detail_lines(spec_snapshot, event_snapshot))
    lines.extend(build_resume_artifact_lines(event_snapshot))
    lines.extend(
        [
            "",
            "该条更新状态提示：",
            build_default_resume_instruction(str(event_snapshot.get("status", ""))),
        ]
    )
    return "\n".join(lines).strip()


def build_queued_feedback_batch_prompt(
    spec: dict[str, Any],
    queued_notifications: list[dict[str, Any]],
    *,
    continuous_research_enabled: bool = False,
) -> str:
    entry_count = len(queued_notifications)
    context_spec = resume_prompt_context_spec(spec, queued_notifications)
    header_lines = [
        *compact_runtime_resume_header_lines(context_spec),
        "",
        *prompt_block_lines("reflow_intro"),
    ]
    if continuous_research_enabled:
        header_lines.append(
            "这是 continuous 主线上的一次合并回流。没有人工干预时，请先比较几条可选路径，再主动执行当前最有信息增益的一步，并说明理由。"
        )
    header_lines.extend(
        [
        f"这次共有 {entry_count} 条更新来自同一会话，请合并判断它们的共同影响，不要把它们拆成新的独立对话。",
        f"合并更新数量: {entry_count}",
        "",
        *compact_context_sections(context_spec, include_canonical_head=True, include_evidence_first=True),
        ]
    )
    blocks = [
        build_batched_resume_notification_block(item, index=index, entry_count=entry_count)
        for index, item in enumerate(queued_notifications, start=1)
    ]
    tail_sections = [
        "\n".join(
            execution_followthrough_instruction_lines(
                context_spec,
                allow_no_further_tasks=not continuous_research_enabled,
                profile=PROMPT_PROFILE_RESUME_COMPACT,
            )
        ).strip(),
        "\n".join(build_resume_safety_lines()).strip(),
        "\n".join(
            [
                "后续动作指令：",
                "请先把上面的全部更新并回当前 proposal/history，再综合判断当前唯一最高优先级动作。",
                "如果顺手还能完成便宜的审计、修复、数据处理或实验准备，也尽量在这一轮一起做掉。",
                "请留在当前对话中继续推进，不要重置用户当前上下文。",
            ]
        ).strip(),
        "\n".join(taskboard_footer_contract_lines()).strip(),
    ]
    sections = ["\n".join(header_lines).strip()]
    return "\n\n".join([section for section in [*sections, *blocks, *tail_sections] if section]).strip()


def format_untrusted_text_block(text: str, *, prefix: str) -> list[str]:
    cleaned = str(text).strip()
    if not cleaned:
        return []
    return [f"{prefix}{line}" if line else prefix.rstrip() for line in cleaned.splitlines()]


def classify_failure(
    *,
    status: str,
    exit_code: int | None,
    exit_signal: str,
    launch_error: str,
    log_tail: str,
) -> tuple[str, str]:
    lower_log = log_tail.lower()
    lower_launch = launch_error.lower()
    if status == "completed":
        return "completed", "任务已成功完成。"
    if status == "launch_failed":
        return "launch_error", launch_error or "后台命令没有成功启动。"
    if status == "observed_exit":
        return "external_pid_exit", "被监控的外部 PID 已从 /proc 中消失，当前已经不再运行。"
    if any(pattern in lower_log or pattern in lower_launch for pattern in OOM_PATTERNS):
        return "oom", "任务看起来是因为内存耗尽而失败。"
    if exit_signal == "SIGKILL" or exit_code in {-9, 9, 137}:
        return "sigkill", "任务被 SIGKILL 杀死，或以 137 退出。"
    if exit_signal in {"SIGTERM", "SIGHUP", "SIGINT"}:
        return "external_termination", f"任务因为收到 {exit_signal} 而停止。"
    if "traceback" in lower_log:
        return "python_traceback", "任务日志中包含 Python traceback。"
    if "exception" in lower_log:
        return "runtime_exception", "任务日志中包含异常信息。"
    if "killed" in lower_log:
        return "killed", "任务日志表明该进程被杀死了。"
    return "nonzero_exit", "任务以失败状态退出，但日志中没有更明确的特征。"


def build_resume_prompt(
    spec: dict[str, Any],
    event: dict[str, Any],
    *,
    continuous_research_enabled: bool = False,
) -> str:
    status = event["status"]
    if status == "completed":
        custom_instruction = spec.get("success_prompt", "").strip()
    else:
        custom_instruction = spec.get("failure_prompt", "").strip()

    instruction = combine_default_and_custom_instruction(
        build_default_resume_instruction(status),
        custom_instruction,
    )
    governance_head_lines: list[str] = [
        *compact_runtime_resume_header_lines(spec),
        "",
        *build_resume_intro(str(spec.get("execution_mode", "")), spec),
    ]
    if continuous_research_enabled:
        governance_head_lines.append("这是 continuous 主线上的一次结果回流。没有人工干预时，请先比较几条可选路径，再主动执行当前最有信息增益的一步，并说明理由。")
    context_lines: list[str] = [
        *compact_context_sections(spec, include_canonical_head=True, include_evidence_first=True),
        "",
        *build_resume_event_detail_lines(spec, event),
    ]
    artifact_lines = build_resume_artifact_lines(event)
    governance_tail_lines: list[str] = [
        *execution_followthrough_instruction_lines(
            spec,
            allow_no_further_tasks=not continuous_research_enabled,
            profile=PROMPT_PROFILE_RESUME_COMPACT,
        ),
        *build_resume_safety_lines(),
        "",
        "后续动作指令：",
        "先把这次新增结果并回当前 proposal/history，再判断当前最值得做的一步。",
        "如果顺手还能完成便宜的审计、修复、数据处理或实验准备，也尽量在这一轮一起做掉。",
        instruction,
        "请留在当前对话中继续推进，不要重置用户当前上下文。",
        "",
        *taskboard_footer_contract_lines(),
    ]
    prompt = join_prompt_lines([*governance_head_lines, *context_lines, *artifact_lines, *governance_tail_lines])
    max_chars = int(spec.get("prompt_max_chars", 12000))
    if len(prompt) <= max_chars:
        return prompt
    prompt_without_artifacts = join_prompt_lines([*governance_head_lines, *context_lines, *governance_tail_lines])
    if len(prompt_without_artifacts) <= max_chars:
        return prompt_without_artifacts
    return truncate_prompt_preserving_tail(
        join_prompt_lines([*governance_head_lines, *context_lines, *artifact_lines]),
        join_prompt_lines(governance_tail_lines),
        max_chars,
    )


def build_protocol_self_check_repair_prompt(
    spec: dict[str, Any],
    followup: dict[str, Any],
    *,
    continuous_research_enabled: bool,
) -> str:
    del continuous_research_enabled
    issue_summary = str(followup.get("protocol_issue", "")).strip() or "missing_protocol_footer"
    footer = protocol_footer_snapshot(followup.get("protocol_footer", {}))
    lines = [
        "协议修复提醒：上一条回复没有给出可执行的 taskboard 协议尾部，taskboard 现在无法安全判断下一步。",
        f"检测到的问题：{issue_summary}",
        "请保持当前上下文，只修复当前动作表达与协议尾部。",
    ]
    lines.extend(compact_runtime_resume_header_lines(spec))
    lines.extend(compact_context_sections(spec, include_canonical_head=True))
    lines.extend(
        [
            "请不要重写整段 proposal，也不要重新展开长篇分析。",
            "只做两件事：",
            "1. 用 1-2 句人话确认当前唯一最高优先级动作；如果它仍是当前回合内可吸收的本地短步骤，可以直接做完再回复。",
            "2. 在回复末尾补齐完整协议尾部；若已经提交 live task，LIVE_TASK_STATUS 必须写 `submitted` 或 `awaiting`。",
        ]
    )
    footer_parts = [f"{key}={value}" for key, value in footer.items() if value not in {"", False}]
    if footer_parts:
        lines.extend(
            [
                "上一条回复里解析到的尾部残片：",
                " | ".join(footer_parts),
            ]
        )
    lines.append("请留在当前对话中继续推进，不要重置用户当前上下文。")
    lines.extend(["", *taskboard_footer_contract_lines()])
    return join_prompt_lines(lines)


def bootstrap_successor_session_after_closeout(
    config: AppConfig,
    *,
    task_id: str,
    spec: dict[str, Any],
    predecessor_session_id: str,
    resolve_followup_key: str = "",
    trigger_signal: str = "none",
    updated_by: str,
    source: str,
) -> dict[str, Any]:
    normalized_task_id = normalize_task_id(str(task_id or "").strip())
    normalized_predecessor_session_id = str(predecessor_session_id or "").strip()
    if not normalized_task_id or not normalized_predecessor_session_id:
        return {
            "ok": False,
            "deferred_reason": "successor_bootstrap_missing_context",
            "taskboard_signal": canonicalize_taskboard_signal(str(trigger_signal or "").strip()) or "none",
            "successor_session_id": "",
        }

    bootstrap_spec = apply_session_redirect_to_spec(config, spec, include_migrating=True)
    output_path = task_last_message_path(config, normalized_task_id)
    log_path = task_runner_log_path(config, normalized_task_id)
    prompt = build_successor_bootstrap_prompt(
        bootstrap_spec,
        predecessor_session_id=normalized_predecessor_session_id,
        trigger_signal=trigger_signal,
    )
    bootstrap_run = run_codex_prompt_with_continue_recovery(
        config,
        mode="exec",
        prompt=prompt,
        output_last_message_path=str(output_path),
        codex_exec_mode=str(bootstrap_spec.get("codex_exec_mode", "dangerous")).strip() or "dangerous",
        workdir=str(bootstrap_spec.get("workdir", "")).strip() or os.getcwd(),
        timeout_seconds=int(bootstrap_spec.get("resume_timeout_seconds", 3600) or 3600),
        log_path=log_path,
        model=str(bootstrap_spec.get("model", "")).strip(),
        spec=bootstrap_spec,
    )
    completed = bootstrap_run.get("completed")
    stdout_tail = str(getattr(completed, "stdout", "") or "")
    stderr_tail = str(getattr(completed, "stderr", "") or "")
    last_message_text = str(bootstrap_run.get("last_message_text", "") or "")
    if not last_message_text and output_path.exists():
        last_message_text = output_path.read_text(encoding="utf-8", errors="ignore")
    signal_source = last_message_text or f"{stdout_tail}\n{stderr_tail}"
    successor_session_id = str(bootstrap_run.get("session_id", "") or extract_codex_session_id(signal_source)).strip()
    signal_value = canonicalize_taskboard_signal(extract_taskboard_signal(signal_source))
    protocol_footer = extract_taskboard_protocol_footer(signal_source)
    protocol_issue = summarize_taskboard_protocol_issue(protocol_footer, signal_value=signal_value)
    bootstrap_finished_at = utc_now()
    bootstrap_ok = bool(last_message_text) or bool(getattr(completed, "returncode", 1) == 0)
    result: dict[str, Any] = {
        "ok": False,
        "taskboard_signal": signal_value,
        "protocol_footer": protocol_footer,
        "protocol_issue": protocol_issue,
        "successor_session_id": successor_session_id,
        "finished_at": bootstrap_finished_at,
        "completed_returncode": int(getattr(completed, "returncode", 1) or 0) if completed is not None else 1,
    }
    if not bootstrap_ok or not successor_session_id:
        result["deferred_reason"] = "successor_bootstrap_failed"
        return result

    cutover = perform_session_cutover(
        config,
        from_session_id=normalized_predecessor_session_id,
        to_session_id=successor_session_id,
        interrupt_grace_seconds=DEFAULT_SESSION_MIGRATION_INTERRUPT_GRACE_SECONDS,
        updated_by=updated_by,
        source=source,
    )
    followup_key_map = {
        str(item.get("followup_key", "")).strip(): str(item.get("new_followup_key", "")).strip()
        for item in cutover.get("followup_bindings", [])
        if isinstance(item, dict)
    }
    for key in {
        str(resolve_followup_key or "").strip(),
        str(followup_key_map.get(str(resolve_followup_key or "").strip(), "")).strip(),
    }:
        if key:
            resolve_followup(config, key)

    successor_spec = load_task_spec(config, normalized_task_id) or bootstrap_spec
    successor_spec = apply_session_redirect_to_spec(config, successor_spec, include_migrating=True)
    current_state = load_task_state(config, normalized_task_id)
    current_summary = current_state.get("notification_summary", {}) if isinstance(current_state.get("notification_summary", {}), dict) else {}
    base_summary = {
        **current_summary,
        "ok": True,
        "continuous_research_mode": True,
        "predecessor_session_id": normalized_predecessor_session_id,
        "successor_session_id": successor_session_id,
        "closeout_session_cutover": True,
        "taskboard_signal": signal_value,
    }

    if signal_value == "none":
        protocol_issue = "successor_bootstrap_missing_transition_signal"

    needs_protocol_repair = signal_value == "none" or taskboard_protocol_requires_repair(protocol_footer, signal_value=signal_value)
    if needs_protocol_repair:
        protocol_followup_scheduled = False
        if should_schedule_followup_for_spec(successor_spec):
            schedule_protocol_self_check_repair(
                config,
                task_id=normalized_task_id,
                spec=successor_spec,
                issue_summary=protocol_issue,
                protocol_footer=protocol_footer,
                observed_signal=signal_value,
                message_path=str(output_path),
            )
            protocol_followup_scheduled = True
        merge_task_state(
            config,
            normalized_task_id,
            research_phase="planning",
            session_flow_state="successor_bootstrap",
            pending_feedback=False,
            notification_ok=True,
            notification_signal=signal_value,
            notification_finished_at=bootstrap_finished_at,
            followup_status="scheduled" if protocol_followup_scheduled else str(current_state.get("followup_status", "")),
            followup_last_signal=signal_value,
            followup_last_action=(
                f"scheduled:{PROTOCOL_SELF_CHECK_REPAIR_REASON}"
                if protocol_followup_scheduled
                else "successor_bootstrap_protocol_repair_without_followup"
            ),
            followup_stopped_at="" if protocol_followup_scheduled else str(current_state.get("followup_stopped_at", "")),
            followup_last_message_path=str(output_path),
            notification_summary={
                **base_summary,
                "research_phase": "planning",
                "session_flow_state": "successor_bootstrap",
                "protocol_repair_scheduled": protocol_followup_scheduled,
                "protocol_issue": protocol_issue,
            },
        )
        result.update(
            {
                "ok": True,
                "action": "successor_bootstrap_protocol_repair_scheduled" if protocol_followup_scheduled else "successor_bootstrap_protocol_repair_pending",
                "research_phase": "planning",
                "session_flow_state": "successor_bootstrap",
                "cutover": cutover,
            }
        )
        return result

    if signal_value == EXECUTION_READY_SIGNAL:
        execution_followup_scheduled = False
        if should_schedule_followup_for_spec(successor_spec):
            schedule_continuous_research_followup(
                config,
                task_id=normalized_task_id,
                spec=successor_spec,
                trigger_signal=signal_value,
                message_path=str(output_path),
            )
            execution_followup_scheduled = True
        merge_task_state(
            config,
            normalized_task_id,
            research_phase="execution",
            session_flow_state="local_active",
            pending_feedback=False,
            notification_ok=True,
            notification_signal=signal_value,
            notification_finished_at=bootstrap_finished_at,
            followup_status="scheduled" if execution_followup_scheduled else str(current_state.get("followup_status", "")),
            followup_last_signal=signal_value,
            followup_last_action=(
                f"scheduled:{CONTINUOUS_RESEARCH_REASON}"
                if execution_followup_scheduled
                else "successor_bootstrap_execution_ready_without_followup"
            ),
            followup_stopped_at="" if execution_followup_scheduled else str(current_state.get("followup_stopped_at", "")),
            followup_last_message_path=str(output_path),
            notification_summary={
                **base_summary,
                "research_phase": "execution",
                "session_flow_state": "local_active",
                "execution_followup_scheduled": execution_followup_scheduled,
            },
        )
        result.update(
            {
                "ok": True,
                "action": "successor_bootstrap_execution_scheduled" if execution_followup_scheduled else "successor_bootstrap_execution_ready",
                "research_phase": "execution",
                "session_flow_state": "local_active",
                "cutover": cutover,
            }
        )
        return result

    if signal_value == WAITING_ON_ASYNC_SIGNAL:
        waiting_followup_scheduled = False
        if should_schedule_followup_for_spec(successor_spec):
            waiting_followup_scheduled = schedule_waiting_on_async_watchdog(
                config,
                task_id=normalized_task_id,
                spec=successor_spec,
            )
        merge_task_state(
            config,
            normalized_task_id,
            research_phase="execution",
            session_flow_state="awaiting_async",
            pending_feedback=False,
            notification_ok=True,
            notification_signal=signal_value,
            notification_finished_at=bootstrap_finished_at,
            followup_status="scheduled" if waiting_followup_scheduled else str(current_state.get("followup_status", "")),
            followup_last_signal=signal_value,
            followup_last_action=(
                f"scheduled:{WAITING_ON_ASYNC_REASON}"
                if waiting_followup_scheduled
                else "successor_bootstrap_waiting_without_followup"
            ),
            followup_stopped_at="" if waiting_followup_scheduled else str(current_state.get("followup_stopped_at", "")),
            followup_last_message_path=str(output_path),
            notification_summary={
                **base_summary,
                "research_phase": "execution",
                "session_flow_state": "awaiting_async",
                "waiting_on_async_watchdog_scheduled": waiting_followup_scheduled,
            },
        )
        result.update(
            {
                "ok": True,
                "action": "successor_bootstrap_waiting_scheduled" if waiting_followup_scheduled else "successor_bootstrap_waiting",
                "research_phase": "execution",
                "session_flow_state": "awaiting_async",
                "cutover": cutover,
            }
        )
        return result

    closeout_followup_scheduled = False
    if signal_value == CLOSEOUT_READY_SIGNAL and should_schedule_followup_for_spec(successor_spec):
        schedule_continuous_transition_followup(
            config,
            task_id=normalized_task_id,
            spec=successor_spec,
            trigger_signal=signal_value,
            message_path=str(output_path),
        )
        closeout_followup_scheduled = True
    merge_task_state(
        config,
        normalized_task_id,
        research_phase="closeout",
        session_flow_state="successor_bootstrap",
        pending_feedback=False,
        notification_ok=True,
        notification_signal=signal_value,
        notification_finished_at=bootstrap_finished_at,
        followup_status="scheduled" if closeout_followup_scheduled else str(current_state.get("followup_status", "")),
        followup_last_signal=signal_value,
        followup_last_action=(
            f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}"
            if closeout_followup_scheduled
            else "successor_bootstrap_closeout_without_followup"
        ),
        followup_stopped_at="" if closeout_followup_scheduled else str(current_state.get("followup_stopped_at", "")),
        followup_last_message_path=str(output_path),
        notification_summary={
            **base_summary,
            "research_phase": "closeout",
            "session_flow_state": "successor_bootstrap",
            "closeout_followup_scheduled": closeout_followup_scheduled,
        },
    )
    result.update(
        {
            "ok": True,
            "action": "successor_bootstrap_closeout_scheduled" if closeout_followup_scheduled else "successor_bootstrap_closeout",
            "research_phase": "closeout",
            "session_flow_state": "successor_bootstrap",
            "cutover": cutover,
        }
    )
    return result


def find_thread_info(config: AppConfig, session_id: str) -> dict[str, Any] | None:
    if not config.threads_db_path.exists():
        return None
    conn = sqlite3.connect(config.threads_db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            row = conn.execute(
                """
                SELECT id, rollout_path, model_provider, source, archived, updated_at, title, cwd, first_user_message
                FROM threads
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = conn.execute(
                """
                SELECT id, model_provider, source, archived, updated_at, title, cwd, first_user_message
                FROM threads
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(row)


def current_thread_info(config: AppConfig, environ: Any | None = None) -> dict[str, Any] | None:
    source_env = environ if environ is not None else os.environ
    current_cwd = str(source_env.get("PWD", "")).strip() or str(Path.cwd())
    taskboard_session_id, taskboard_source, taskboard_candidates = infer_taskboard_codex_session_id(
        config,
        workdir=current_cwd,
        agent_name=str(source_env.get("CODEX_AGENT_NAME", "")).strip(),
    )
    env_session_id, env_key = resolve_current_codex_session_id(source_env)
    session_id, resolved_from, preferred_taskboard_over_env = choose_current_codex_session_binding(
        env_session_id=env_session_id,
        env_key=env_key,
        taskboard_session_id=taskboard_session_id,
        taskboard_source=taskboard_source,
    )
    if not session_id:
        return None
    thread = find_thread_info(config, session_id) or {}
    updated_at = thread.get("updated_at")
    return {
        "current_codex_session_id": session_id,
        "resolved_from_env": env_key,
        "resolved_from": resolved_from,
        "env_codex_session_id": env_session_id,
        "taskboard_workdir_session_id": taskboard_session_id,
        "taskboard_session_candidates": taskboard_candidates,
        "session_resolution_conflict": bool(env_session_id and taskboard_session_id and env_session_id != taskboard_session_id),
        "preferred_taskboard_over_env": preferred_taskboard_over_env,
        "cwd_probe": current_cwd,
        "thread_found": bool(thread),
        "model_provider": str(thread.get("model_provider", "")),
        "source": str(thread.get("source", "")),
        "archived": int(thread.get("archived", 0) or 0) if thread else 0,
        "updated_at": int(updated_at) if updated_at is not None else None,
        "updated_at_iso": format_unix_timestamp(int(updated_at)) if updated_at is not None else "",
        "title": str(thread.get("title", "")),
        "cwd": str(thread.get("cwd", "")),
        "first_user_message": str(thread.get("first_user_message", "")),
    }


def load_manifest_mapping(
    config: AppConfig,
    source_provider: str,
    dest_provider: str,
    source_session_id: str,
) -> dict[str, Any] | None:
    if not config.thread_manifest_path.exists():
        return None
    latest: dict[str, Any] | None = None
    with config.thread_manifest_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                payload.get("from_provider") == source_provider
                and payload.get("to_provider") == dest_provider
                and payload.get("source_thread_id") == source_session_id
            ):
                latest = payload
    return latest


def sync_thread_for_fallback(
    config: AppConfig,
    *,
    original_session_id: str,
    fallback_provider: str,
    workdir: str,
    task_id: str,
) -> tuple[bool, str, str]:
    if not config.sync_script_path.exists():
        return False, "", f"Missing sync script: {config.sync_script_path}"
    thread_info = find_thread_info(config, original_session_id)
    if thread_info is None:
        return False, "", f"Could not find original session id: {original_session_id}"
    source_provider = str(thread_info["model_provider"])
    if source_provider == fallback_provider:
        return False, "", "Fallback provider matches the original provider; nothing to clone."
    command = [
        sys.executable,
        str(config.sync_script_path),
        "--from-provider",
        source_provider,
        "--to-provider",
        fallback_provider,
        "--codex-home",
        str(config.codex_home),
        "--thread-id",
        original_session_id,
    ]
    completed = run_subprocess(command, cwd=workdir, timeout=600)
    append_log(
        task_runner_log_path(config, task_id),
        f"fallback_sync returncode={completed.returncode} stdout_tail={completed.stdout[-1000:]} stderr_tail={completed.stderr[-1000:]}",
    )
    if completed.returncode != 0:
        return False, "", completed.stderr[-1000:] or completed.stdout[-1000:] or "fallback sync failed"
    mapping = load_manifest_mapping(
        config,
        source_provider=source_provider,
        dest_provider=fallback_provider,
        source_session_id=original_session_id,
    )
    if mapping is None:
        return False, "", "Fallback sync completed but no mapping was recorded."
    return True, str(mapping["dest_thread_id"]), ""


def build_codex_resume_command(
    config: AppConfig,
    *,
    session_id: str,
    prompt: str,
    output_last_message_path: str,
    codex_exec_mode: str,
    workdir: str,
) -> list[str]:
    del output_last_message_path
    command = [
        config.codex_bin,
        "resume",
        "--include-non-interactive",
        "-C",
        workdir,
        "--no-alt-screen",
        session_id,
        prompt,
    ]
    if codex_exec_mode == "dangerous":
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.append("--full-auto")
    return command


def build_codex_exec_command(
    config: AppConfig,
    *,
    prompt: str,
    output_last_message_path: str,
    codex_exec_mode: str,
    workdir: str,
    model: str,
) -> list[str]:
    del output_last_message_path
    command = [
        config.codex_bin,
        "-C",
        workdir,
        "--no-alt-screen",
    ]
    if model:
        command.extend(["-m", model])
    if codex_exec_mode == "dangerous":
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.append("--full-auto")
    command.append(prompt)
    return command


def run_codex_prompt_with_continue_recovery(
    config: AppConfig,
    *,
    mode: str,
    prompt: str,
    output_last_message_path: str,
    codex_exec_mode: str,
    workdir: str,
    timeout_seconds: int,
    log_path: Path,
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
    return run_codex_prompt_with_continue_recovery_impl(
        config,
        mode=mode,
        prompt=prompt,
        output_last_message_path=output_last_message_path,
        codex_exec_mode=codex_exec_mode,
        workdir=workdir,
        timeout_seconds=timeout_seconds,
        log_path=log_path,
        model=model,
        session_id=session_id,
        max_continue_attempts=max_continue_attempts,
        spec=spec,
        feedback_source_kind=feedback_source_kind,
        feedback_source_key=feedback_source_key,
        feedback_task_id=feedback_task_id,
        feedback_task_ids=feedback_task_ids,
        feedback_followup_key=feedback_followup_key,
        requested_session_id=requested_session_id,
        track_resume_feedback=track_resume_feedback,
        hooks=codex_runtime_hooks(),
    )


def resume_codex_session(
    config: AppConfig,
    spec: dict[str, Any],
    event: dict[str, Any],
    *,
    min_idle_seconds: int = DEFAULT_NOTIFICATION_MIN_IDLE_SECONDS,
) -> dict[str, Any]:
    return resume_codex_session_impl(
        config,
        spec,
        event,
        min_idle_seconds=min_idle_seconds,
        hooks=codex_runtime_hooks(),
    )


def resume_codex_session_with_prompt(
    config: AppConfig,
    spec: dict[str, Any],
    prompt: str,
    *,
    output_last_message_path: str,
    log_path: Path,
    min_idle_seconds: int = DEFAULT_NOTIFICATION_MIN_IDLE_SECONDS,
    feedback_source_kind: str = "",
    feedback_source_key: str = "",
    feedback_task_id: str = "",
    feedback_task_ids: list[str] | None = None,
    feedback_followup_key: str = "",
) -> dict[str, Any]:
    return resume_codex_session_with_prompt_impl(
        config,
        spec,
        prompt,
        output_last_message_path=output_last_message_path,
        log_path=log_path,
        min_idle_seconds=min_idle_seconds,
        feedback_source_kind=feedback_source_kind,
        feedback_source_key=feedback_source_key,
        feedback_task_id=feedback_task_id,
        feedback_task_ids=feedback_task_ids,
        feedback_followup_key=feedback_followup_key,
        hooks=codex_runtime_hooks(),
    )


def create_event_payload(
    config: AppConfig,
    spec: dict[str, Any],
    *,
    status: str,
    started_at: float | None,
    ended_at: float,
    exit_code: int | None,
    exit_signal: str,
    launch_error: str = "",
) -> dict[str, Any]:
    state = load_task_state(config, spec["task_id"])
    log_source = task_command_log_path(config, spec["task_id"])
    watch_log_path = str(spec.get("watch_log_path", "")).strip()
    if watch_log_path:
        candidate = Path(watch_log_path).expanduser()
        if candidate.exists():
            log_source = candidate
    log_tail = tail_text(
        log_source,
        max_lines=int(spec.get("log_tail_lines", 80)),
        max_chars=int(spec.get("log_tail_chars", 5000)),
    )
    artifact_context = collect_artifact_context(spec)
    duration_seconds = None if started_at is None else max(0, int(ended_at - started_at))
    failure_kind, failure_summary = classify_failure(
        status=status,
        exit_code=exit_code,
        exit_signal=exit_signal,
        launch_error=launch_error,
        log_tail=log_tail,
    )
    structured_report, report_summary = extract_structured_report(spec, log_tail, artifact_context)
    failure_excerpt = extract_failure_excerpt(log_tail, status=status, failure_kind=failure_kind)
    taskboard_signal = ""
    if structured_report:
        taskboard_signal = str(structured_report.get("taskboard_signal") or structured_report.get("TASKBOARD_SIGNAL") or "").strip()
    if not taskboard_signal:
        taskboard_signal = extract_taskboard_signal(log_tail)
    return {
        "version": VERSION,
        "task_id": spec["task_id"],
        "status": status,
        "started_at": utc_now() if started_at is None else format_unix_timestamp(started_at),
        "ended_at": format_unix_timestamp(ended_at),
        "duration_seconds": duration_seconds,
        "exit_code": exit_code,
        "exit_signal": exit_signal,
        "launch_error": launch_error,
        "failure_kind": failure_kind,
        "failure_summary": failure_summary,
        "failure_excerpt": failure_excerpt,
        "command_log_path": str(task_command_log_path(config, spec["task_id"])),
        "runner_log_path": str(task_runner_log_path(config, spec["task_id"])),
        "watch_log_path": watch_log_path,
        "artifact_context": artifact_context,
        "log_tail": log_tail,
        "structured_report": structured_report,
        "report_summary": report_summary,
        "taskboard_signal": taskboard_signal,
        "assigned_gpus": parse_gpu_id_list(spec.get("assigned_gpus", [])),
        "cpu_profile": declared_cpu_profile(spec),
        "cpu_profile_resolved": resolved_cpu_profile(spec),
        "assigned_cpu_threads": coerce_non_negative_int(spec.get("assigned_cpu_threads", 0)),
        "assigned_cpu_workers": coerce_non_negative_int(spec.get("assigned_cpu_workers", 0)),
        "cpu_budget": task_requested_cpu_budget(spec),
        "cpu_threads_mode": str(spec.get("cpu_threads_mode", "")),
        "cpu_threads_min": coerce_non_negative_int(spec.get("cpu_threads_min", 0)),
        "cpu_threads_max": coerce_non_negative_int(spec.get("cpu_threads_max", 0)),
        "cpu_workers": coerce_non_negative_int(spec.get("cpu_workers", 0)),
        "cpu_workers_min": coerce_non_negative_int(spec.get("cpu_workers_min", 0)),
        "cpu_workers_max": coerce_non_negative_int(spec.get("cpu_workers_max", 0)),
        "dispatch_gpu_snapshot": state.get("dispatch_gpu_snapshot", []),
        "launch_gpu_snapshot": state.get("launch_gpu_snapshot", []),
        "selected_gpu_ids": parse_gpu_id_list(state.get("assigned_gpus", spec.get("assigned_gpus", []))),
        "rejected_reason": str(state.get("rejected_reason", "")),
    }


def run_codex_subagent(config: AppConfig, spec: dict[str, Any]) -> dict[str, Any]:
    return run_codex_subagent_impl(config, spec, hooks=codex_runtime_hooks())


def write_event(config: AppConfig, task_id: str, payload: dict[str, Any]) -> Path:
    timestamp = format_beijing_filename_timestamp()
    path = task_events_dir(config, task_id) / f"{timestamp}-{payload['status']}.json"
    feedback_data_path = path.with_name(path.stem + "-feedback.json")
    payload["event_path"] = str(path)
    payload["feedback_data_path"] = str(feedback_data_path)
    atomic_write_json(path, payload)
    feedback_payload = {
        "task_id": task_id,
        "status": str(payload.get("status", "")),
        "event_path": str(path),
        "command_log_path": str(payload.get("command_log_path", "")),
        "runner_log_path": str(payload.get("runner_log_path", "")),
        "watch_log_path": str(payload.get("watch_log_path", "")),
        "failure_kind": str(payload.get("failure_kind", "")),
        "failure_summary": str(payload.get("failure_summary", "")),
        "failure_excerpt": str(payload.get("failure_excerpt", "")),
        "log_tail": str(payload.get("log_tail", "")),
        "structured_report": payload.get("structured_report", {}),
        "report_summary": str(payload.get("report_summary", "")),
        "artifact_context": payload.get("artifact_context", []),
        "dispatch_gpu_snapshot": payload.get("dispatch_gpu_snapshot", []),
        "launch_gpu_snapshot": payload.get("launch_gpu_snapshot", []),
        "subagent_session_id": str(payload.get("subagent_session_id", "")),
        "subagent_last_message_path": str(payload.get("subagent_last_message_path", "")),
        "subagent_last_message_excerpt": str(payload.get("subagent_last_message_excerpt", "")),
        "taskboard_signal": str(payload.get("taskboard_signal", "")),
    }
    atomic_write_json(feedback_data_path, feedback_payload)
    return path


def iter_task_states(config: AppConfig) -> list[dict[str, Any]]:
    return iter_task_states_impl(config, hooks=task_storage_hooks())


def iter_all_task_states(config: AppConfig) -> list[dict[str, Any]]:
    return iter_all_task_states_impl(config, hooks=task_storage_hooks())

def find_task_states_by_key(config: AppConfig, task_key: str) -> list[dict[str, Any]]:
    return [state for state in iter_all_task_states(config) if str(state.get("task_key", state.get("task_id", ""))) == task_key]


def supersede_task(config: AppConfig, task_id: str, successor_task_id: str) -> None:
    state = load_task_state(config, task_id)
    if not state:
        return
    merge_task_state(
        config,
        task_id,
        status="superseded",
        superseded_at=utc_now(),
        superseded_by=successor_task_id,
        hidden=True,
    )


def prepare_task_slot(
    config: AppConfig,
    *,
    task_id: str,
    task_key: str,
    replace_existing: bool,
) -> None:
    if task_id_exists_historically(config, task_id):
        raise ValueError(
            f"Task id already exists historically: {task_id}. "
            "Use a new task_id for every run and reuse task_key for logical replacement."
        )

    existing = find_task_states_by_key(config, task_key)
    if not existing:
        return

    active_conflicts = [
        state
        for state in existing
        if str(state.get("status", "")) in ACTIVE_TASK_STATUSES | RUNNABLE_STATUSES and str(state.get("task_id", "")) != task_id
    ]
    if active_conflicts:
        conflict_ids = ", ".join(str(item.get("task_id", "")) for item in active_conflicts)
        raise ValueError(f"Active or queued task(s) already exist for task_key={task_key}: {conflict_ids}")

    if not replace_existing:
        other_visible = [
            state
            for state in existing
            if str(state.get("task_id", "")) != task_id and not is_hidden_status(str(state.get("status", "")))
        ]
        if other_visible:
            conflict_ids = ", ".join(str(item.get("task_id", "")) for item in other_visible)
            raise ValueError(f"Visible task(s) already exist for task_key={task_key}: {conflict_ids}")
        return

    for state in existing:
        old_task_id = str(state.get("task_id", ""))
        if not old_task_id or old_task_id == task_id:
            continue
        if is_hidden_status(str(state.get("status", ""))):
            continue
        supersede_task(config, old_task_id, task_id)


def normalize_session_guard_workdir(raw_workdir: str) -> str:
    value = str(raw_workdir or "").strip()
    if not value:
        return ""
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:  # noqa: BLE001
        return value


def session_binding_conflicts(
    config: AppConfig,
    *,
    task_id: str,
    task_key: str,
    workdir: str,
    agent_name: str,
    codex_session_id: str,
) -> list[dict[str, str]]:
    if not codex_session_id:
        return []
    normalized_workdir = normalize_session_guard_workdir(workdir)
    normalized_task_key = normalize_task_id(task_key) or task_key.strip()
    normalized_agent_name = agent_name.strip()
    conflicts: list[dict[str, str]] = []
    for state in iter_all_task_states(config):
        existing_task_id = str(state.get("task_id", "")).strip()
        if not existing_task_id or existing_task_id == task_id:
            continue
        status = str(state.get("status", "")).strip()
        if is_hidden_status(status):
            continue
        existing_workdir = normalize_session_guard_workdir(str(state.get("workdir", "")))
        if normalized_workdir and existing_workdir and existing_workdir != normalized_workdir:
            continue
        existing_session = str(state.get("codex_session_id", "")).strip()
        if not existing_session or existing_session == codex_session_id:
            continue
        existing_task_key = normalize_task_id(str(state.get("task_key", "")).strip()) or str(state.get("task_key", "")).strip()
        existing_agent_name = str(state.get("agent_name", "")).strip()
        match_reasons: list[str] = []
        if normalized_workdir and existing_workdir == normalized_workdir and normalized_task_key and existing_task_key == normalized_task_key:
            match_reasons.append("task_lineage")
        if normalized_agent_name and existing_agent_name == normalized_agent_name:
            match_reasons.append("agent_identity")
        if not match_reasons:
            continue
        conflicts.append(
            {
                "task_id": existing_task_id,
                "task_key": existing_task_key,
                "status": status,
                "submitted_at": str(state.get("submitted_at", "")).strip(),
                "codex_session_id": existing_session,
                "agent_name": existing_agent_name,
                "match_reason": "+".join(match_reasons),
            }
        )
    conflicts.sort(
        key=lambda item: (
            timestamp_sort_value(item.get("submitted_at"), missing=float("inf")),
            item["task_id"],
        )
    )
    return conflicts


def enforce_session_binding_guard(
    config: AppConfig,
    *,
    task_id: str,
    task_key: str,
    workdir: str,
    agent_name: str,
    codex_session_id: str,
    allow_session_rebind: bool,
) -> None:
    if allow_session_rebind or not codex_session_id:
        return
    conflicts = session_binding_conflicts(
        config,
        task_id=task_id,
        task_key=task_key,
        workdir=workdir,
        agent_name=agent_name,
        codex_session_id=codex_session_id,
    )
    if not conflicts:
        return
    active_conflicts = [item for item in conflicts if item["status"] in ACTIVE_TASK_STATUSES | RUNNABLE_STATUSES]
    same_lineage_conflicts = [item for item in conflicts if "task_lineage" in item.get("match_reason", "")]
    active_lineage_conflicts = [item for item in same_lineage_conflicts if item["status"] in ACTIVE_TASK_STATUSES | RUNNABLE_STATUSES]
    active_agent_conflicts = [item for item in active_conflicts if "agent_identity" in item.get("match_reason", "")]
    relevant = list(active_lineage_conflicts)
    relevant.extend(item for item in active_agent_conflicts if item not in relevant)
    relevant.extend(item for item in same_lineage_conflicts if item not in relevant)
    if not relevant:
        return
    sample = "; ".join(
        f"{item['task_id']}[{item['status']}|{item.get('match_reason', '')}]=>{item['codex_session_id']}"
        for item in relevant[:SESSION_GUARD_SAMPLE_LIMIT]
    )
    sessions = ", ".join(sorted({item["codex_session_id"] for item in relevant}))
    normalized_workdir = normalize_session_guard_workdir(workdir)
    if active_lineage_conflicts and active_agent_conflicts:
        reason = "same task lineage already has active or queued tasks on another session and the same agent/workdir is also active there"
    elif active_lineage_conflicts:
        reason = "same task lineage already has active or queued tasks on another session"
    elif same_lineage_conflicts:
        reason = "this task lineage already has history on another session"
    elif active_agent_conflicts:
        reason = "same agent/workdir already has active or queued tasks on another session"
    else:
        reason = "a conflicting session binding already exists on another session"
    raise ValueError(
        "Session binding conflict: "
        f"task_id={task_id} task_key={task_key} agent_name={agent_name} "
        f"workdir={normalized_workdir or workdir} requests codex_session_id={codex_session_id}, "
        f"but {reason}: {sessions}. "
        f"Sample conflicting tasks: {sample}. "
        "If this rebind is intentional, retry with --allow-session-rebind."
    )


def duplicate_submit_conflicts(
    config: AppConfig,
    *,
    task_id: str,
    codex_session_id: str,
    proposal_path: str,
    command: str,
) -> list[dict[str, str]]:
    normalized_session = str(codex_session_id or "").strip()
    normalized_proposal = str(proposal_path or "").strip()
    normalized_command = str(command or "").strip()
    if not normalized_session or not normalized_proposal or not normalized_command:
        return []
    conflicts: list[dict[str, str]] = []
    for state in iter_all_task_states(config):
        existing_task_id = str(state.get("task_id", "")).strip()
        if not existing_task_id or existing_task_id == task_id:
            continue
        status = str(state.get("status", "")).strip()
        if status not in ACTIVE_TASK_STATUSES | RUNNABLE_STATUSES:
            continue
        if str(state.get("codex_session_id", "")).strip() != normalized_session:
            continue
        if str(state.get("proposal_path", "")).strip() != normalized_proposal:
            continue
        if str(state.get("command", "")).strip() != normalized_command:
            continue
        conflicts.append(
            {
                "task_id": existing_task_id,
                "task_key": str(state.get("task_key", "")).strip(),
                "status": status,
                "submitted_at": str(state.get("submitted_at", "")).strip(),
                "tmux_session_name": str(state.get("tmux_session_name", "")).strip(),
                "agent_name": str(state.get("agent_name", "")).strip(),
            }
        )
    conflicts.sort(
        key=lambda item: (
            timestamp_sort_value(item.get("submitted_at"), missing=float("inf")),
            item["task_id"],
        )
    )
    return conflicts


def build_duplicate_submit_warning(conflicts: list[dict[str, str]]) -> str:
    sample = "; ".join(
        f"{item['task_id']}[{item['status']}]@{item.get('submitted_at', '') or '?'}"
        + (f" tmux={item['tmux_session_name']}" if item.get("tmux_session_name") else "")
        for item in conflicts[:DUPLICATE_SUBMIT_SAMPLE_LIMIT]
    )
    extra = ""
    if len(conflicts) > DUPLICATE_SUBMIT_SAMPLE_LIMIT:
        extra = f" (+{len(conflicts) - DUPLICATE_SUBMIT_SAMPLE_LIMIT} more)"
    return (
        "Duplicate submit guard: an active or queued task already matches the same "
        "codex_session_id + proposal_path + command. Check the authoritative task first via "
        "`codex-taskboard status --json` and re-read the current proposal/history before resubmitting. "
        "If the duplicate is intentional, rerun from taskboard with `--allow-duplicate-submit` "
        "(or `allow_duplicate_submit=true` in the API payload). "
        f"Matches: {sample}{extra}"
    )


def enforce_duplicate_submit_guard(
    config: AppConfig,
    *,
    task_id: str,
    codex_session_id: str,
    proposal_path: str,
    command: str,
    allow_duplicate_submit: bool,
) -> tuple[list[dict[str, str]], str]:
    conflicts = duplicate_submit_conflicts(
        config,
        task_id=task_id,
        codex_session_id=codex_session_id,
        proposal_path=proposal_path,
        command=command,
    )
    if not conflicts:
        return [], ""
    warning = build_duplicate_submit_warning(conflicts)
    if not allow_duplicate_submit:
        raise ValueError(warning)
    return conflicts, warning


def task_state_recency_key(state: dict[str, Any]) -> tuple[float, float, str]:
    return (
        timestamp_sort_value(state.get("updated_at"), missing=float("-inf")),
        timestamp_sort_value(state.get("submitted_at"), missing=float("-inf")),
        str(state.get("task_id", "")),
    )


def latest_task_states_by_key(states: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return latest_task_states_by_key_impl(states, hooks=scheduler_readiness_hooks())


def latest_task_state_for_key(config: AppConfig, task_key: str) -> dict[str, Any] | None:
    return latest_task_state_for_key_impl(config, task_key, hooks=scheduler_readiness_hooks())


def stringify_report_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def required_report_conditions(spec: dict[str, Any]) -> list[dict[str, str]]:
    return required_report_conditions_impl(spec)


def report_value_from_state(state: dict[str, Any], key: str) -> str:
    return report_value_from_state_impl(state, key)


def dependency_resolution(
    config: AppConfig,
    spec: dict[str, Any],
    *,
    latest_states_by_key: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    return dependency_resolution_impl(
        config,
        spec,
        hooks=scheduler_readiness_hooks(),
        latest_states_by_key=latest_states_by_key,
    )


def artifact_resolution(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return artifact_resolution_impl(spec, hooks=scheduler_readiness_hooks())


def report_resolution(spec: dict[str, Any], latest_dependency_states: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return report_resolution_impl(spec, latest_dependency_states)


def selected_gpu_snapshot(gpu_rows: list[dict[str, Any]], gpu_ids: list[int]) -> list[dict[str, Any]]:
    return selected_gpu_snapshot_impl(gpu_rows, gpu_ids, hooks=scheduler_readiness_hooks())


def evaluate_task_readiness(
    config: AppConfig,
    spec: dict[str, Any],
    *,
    gpu_rows: list[dict[str, Any]] | None = None,
    total_gpu_slots: int = 0,
    reserved_gpu_ids: set[int] | None = None,
    active_cpu_threads: int = 0,
    reserved_cpu_threads: int = 0,
    cpu_thread_limit: int = 0,
    latest_states_by_key: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return evaluate_task_readiness_impl(
        config,
        spec,
        hooks=scheduler_readiness_hooks(),
        gpu_rows=gpu_rows,
        total_gpu_slots=total_gpu_slots,
        reserved_gpu_ids=reserved_gpu_ids,
        active_cpu_threads=active_cpu_threads,
        reserved_cpu_threads=reserved_cpu_threads,
        cpu_thread_limit=cpu_thread_limit,
        latest_states_by_key=latest_states_by_key,
    )


def enrich_task_state(
    config: AppConfig,
    state: dict[str, Any],
    *,
    gpu_rows: list[dict[str, Any]] | None = None,
    total_gpu_slots: int = 0,
    reserved_gpu_ids: set[int] | None = None,
    active_cpu_threads: int = 0,
    reserved_cpu_threads: int = 0,
    cpu_thread_limit: int = 0,
    latest_states_by_key: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return enrich_task_state_impl(
        config,
        state,
        hooks=scheduler_enrichment_hooks(),
        gpu_rows=gpu_rows,
        total_gpu_slots=total_gpu_slots,
        reserved_gpu_ids=reserved_gpu_ids,
        active_cpu_threads=active_cpu_threads,
        reserved_cpu_threads=reserved_cpu_threads,
        cpu_thread_limit=cpu_thread_limit,
        latest_states_by_key=latest_states_by_key,
    )


def merged_spec_with_state(config: AppConfig, state: dict[str, Any]) -> dict[str, Any]:
    task_id = str(state.get("task_id", "")).strip()
    spec = load_task_spec(config, task_id) if task_id else normalize_task_spec_payload(state)
    state_assigned_gpus = state.get("assigned_gpus", [])
    state_allowed_gpus = state.get("allowed_gpus", [])
    state_depends_on = state.get("depends_on", [])
    merged = dict(spec)
    merged.update(
        {
            "task_id": task_id or str(spec.get("task_id", "")),
            "task_key": str(state.get("task_key", spec.get("task_key", ""))),
            "agent_name": str(state.get("agent_name", spec.get("agent_name", ""))),
            "codex_session_id": str(state.get("codex_session_id", spec.get("codex_session_id", ""))),
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
            "execution_mode": str(state.get("execution_mode", spec.get("execution_mode", "shell"))),
            "workdir": str(state.get("workdir", spec.get("workdir", ""))),
            "remote_workdir": str(state.get("remote_workdir", spec.get("remote_workdir", ""))),
            "executor_name": str(state.get("executor_name", spec.get("executor_name", ""))),
            "executor_target": str(state.get("executor_target", spec.get("executor_target", ""))),
            "executor_identity_file": str(state.get("executor_identity_file", spec.get("executor_identity_file", ""))),
            "executor_ssh_options": [
                str(item)
                for item in state.get("executor_ssh_options", spec.get("executor_ssh_options", []))
                if str(item).strip()
            ],
            "executor_remote_workdir_prefix": str(
                state.get("executor_remote_workdir_prefix", spec.get("executor_remote_workdir_prefix", ""))
            ),
            "executor_remote_home": str(state.get("executor_remote_home", spec.get("executor_remote_home", ""))),
            "executor_remote_codex_home": str(
                state.get("executor_remote_codex_home", spec.get("executor_remote_codex_home", ""))
            ),
            "executor_remote_codex_bin": str(
                state.get("executor_remote_codex_bin", spec.get("executor_remote_codex_bin", "codex"))
            ),
            "codex_exec_mode": str(state.get("codex_exec_mode", spec.get("codex_exec_mode", "dangerous"))),
            "resume_timeout_seconds": int(state.get("resume_timeout_seconds", spec.get("resume_timeout_seconds", 3600)) or 3600),
            "fallback_provider": str(state.get("fallback_provider", spec.get("fallback_provider", ""))),
            "prompt_max_chars": int(state.get("prompt_max_chars", spec.get("prompt_max_chars", 12000)) or 12000),
            "command": str(state.get("command", spec.get("command", ""))),
            "gpu_slots": int(state.get("gpu_slots", spec.get("gpu_slots", 0)) or 0),
            "cpu_profile": str(state.get("cpu_profile", spec.get("cpu_profile", "auto"))),
            "cpu_threads": int(state.get("cpu_threads", spec.get("cpu_threads", 0)) or 0),
            "cpu_threads_min": int(state.get("cpu_threads_min", spec.get("cpu_threads_min", 0)) or 0),
            "cpu_threads_max": int(state.get("cpu_threads_max", spec.get("cpu_threads_max", 0)) or 0),
            "cpu_threads_mode": str(state.get("cpu_threads_mode", spec.get("cpu_threads_mode", ""))),
            "assigned_cpu_threads": int(state.get("assigned_cpu_threads", spec.get("assigned_cpu_threads", 0)) or 0),
            "cpu_workers": int(state.get("cpu_workers", spec.get("cpu_workers", 0)) or 0),
            "cpu_workers_min": int(state.get("cpu_workers_min", spec.get("cpu_workers_min", 0)) or 0),
            "cpu_workers_max": int(state.get("cpu_workers_max", spec.get("cpu_workers_max", 0)) or 0),
            "assigned_cpu_workers": int(state.get("assigned_cpu_workers", spec.get("assigned_cpu_workers", 0)) or 0),
            "assigned_gpus": parse_gpu_id_list(state_assigned_gpus or spec.get("assigned_gpus", [])),
            "allowed_gpus": parse_gpu_id_list(state_allowed_gpus or spec.get("allowed_gpus", [])),
            "depends_on": state_depends_on or spec.get("depends_on", []),
        }
    )
    return merged


def latest_continuous_research_anchor_spec(
    config: AppConfig,
    session_id: str,
    *,
    states: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None
    candidates: list[tuple[tuple[str, str, str], dict[str, Any]]] = []
    for state in states if states is not None else iter_all_task_states(config):
        if str(state.get("codex_session_id", "")).strip() != normalized_session_id:
            continue
        if is_hidden_status(str(state.get("status", ""))):
            continue
        spec = merged_spec_with_state(config, state)
        if not should_schedule_followup_for_spec(spec):
            continue
        candidates.append((task_state_recency_key(state), spec))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def continuous_research_session_evidence_token(
    config: AppConfig,
    session_id: str,
    *,
    spec: dict[str, Any] | None = None,
    states: list[dict[str, Any]] | None = None,
) -> str:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return ""
    anchor_spec = spec or latest_continuous_research_anchor_spec(config, normalized_session_id, states=states) or {}
    lifecycle_rows: list[dict[str, Any]] = []
    for state in states if states is not None else iter_all_task_states(config):
        if str(state.get("codex_session_id", "")).strip() != normalized_session_id:
            continue
        if is_hidden_status(str(state.get("status", ""))):
            continue
        lifecycle_rows.append(
            {
                field: str(state.get(field, ""))
                for field in CONTINUOUS_RESEARCH_LIFECYCLE_TOKEN_FIELDS
            }
        )
    lifecycle_rows.sort(
        key=lambda item: (
            str(item.get("submitted_at", "")),
            str(item.get("started_at", "")),
            str(item.get("ended_at", "")),
            str(item.get("task_id", "")),
        )
    )
    proposal_head = inspect_canonical_head_file(str(anchor_spec.get("proposal_path", "")).strip(), role="proposal")
    history_head = inspect_canonical_head_file(str(anchor_spec.get("project_history_file", "")).strip(), role="history")
    payload = {
        "session_id": normalized_session_id,
        "task_lifecycle": lifecycle_rows,
        "proposal_head": {
            "status": str(proposal_head.get("status", "")),
            "hash": str(proposal_head.get("hash", "")),
            "path": str(proposal_head.get("path", "")),
        },
        "history_head": {
            "status": str(history_head.get("status", "")),
            "hash": str(history_head.get("hash", "")),
            "path": str(history_head.get("path", "")),
        },
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def session_has_live_running_task(
    config: AppConfig,
    session_id: str,
    *,
    states: list[dict[str, Any]] | None = None,
) -> bool:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return False
    for state in states if states is not None else iter_all_task_states(config):
        if str(state.get("codex_session_id", "")).strip() != normalized_session_id:
            continue
        if is_hidden_status(str(state.get("status", ""))):
            continue
        if str(state.get("status", "")) not in ACTIVE_TASK_STATUSES:
            continue
        if task_execution_still_live(config, state):
            return True
    return False


def session_running_task_rows(
    config: AppConfig,
    session_id: str,
    *,
    states: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return []
    return [
        state
        for state in (states if states is not None else iter_all_task_states(config))
        if str(state.get("codex_session_id", "")).strip() == normalized_session_id
        and not is_hidden_status(str(state.get("status", "")))
        and task_should_count_as_running_task(state)
    ]


def session_awaiting_feedback_rows(
    config: AppConfig,
    session_id: str,
    *,
    states: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return []
    return [
        state
        for state in (states if states is not None else iter_all_task_states(config))
        if str(state.get("codex_session_id", "")).strip() == normalized_session_id
        and not is_hidden_status(str(state.get("status", "")))
        and task_lifecycle_state(state) == "awaiting_feedback"
    ]


def session_live_task_rows(
    config: AppConfig,
    session_id: str,
    *,
    states: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return session_running_task_rows(config, session_id, states=states)


def session_has_live_task(
    config: AppConfig,
    session_id: str,
    *,
    states: list[dict[str, Any]] | None = None,
) -> bool:
    return bool(session_running_task_rows(config, session_id, states=states))


def waiting_signal_has_live_task(
    config: AppConfig,
    *,
    session_id: str = "",
    source_task_id: str = "",
    states: list[dict[str, Any]] | None = None,
) -> bool:
    normalized_session_id = str(session_id or "").strip()
    if normalized_session_id and session_has_live_task(config, normalized_session_id, states=states):
        return True
    normalized_task_id = str(source_task_id or "").strip()
    if not normalized_task_id:
        return False
    return task_should_count_as_running_task(load_task_state(config, normalized_task_id))


def continuous_session_live_task_snapshot(
    config: AppConfig,
    session_id: str,
    *,
    states: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    running_rows = session_running_task_rows(config, session_id, states=states)
    awaiting_feedback_rows = session_awaiting_feedback_rows(config, session_id, states=states)
    tracked_rows_by_id: dict[str, dict[str, Any]] = {}
    for row in [*running_rows, *awaiting_feedback_rows]:
        task_id = str(row.get("task_id", "")).strip()
        tracked_rows_by_id[task_id or f"row:{id(row)}"] = row
    tracked_rows = list(tracked_rows_by_id.values())
    proposal_bound_rows = [row for row in running_rows if task_has_research_binding(row)]
    platform_rows = []
    platform_attention_rows = []
    for row in tracked_rows:
        platform_recovery = task_platform_recovery_state(row)
        if str(platform_recovery.get("state", "")) == "none":
            continue
        platform_rows.append({"task_id": str(row.get("task_id", "")).strip(), "platform_recovery": platform_recovery})
        if str(platform_recovery.get("state", "")) == "needs_human_attention":
            platform_attention_rows.append({"task_id": str(row.get("task_id", "")).strip(), "platform_recovery": platform_recovery})
    return {
        "running_task_count": len(running_rows),
        "running_task_ids": [str(row.get("task_id", "")).strip() for row in running_rows if str(row.get("task_id", "")).strip()],
        "live_task_count": len(running_rows),
        "live_task_ids": [str(row.get("task_id", "")).strip() for row in running_rows if str(row.get("task_id", "")).strip()],
        "proposal_bound_running_task_count": len(proposal_bound_rows),
        "proposal_bound_running_task_ids": [
            str(row.get("task_id", "")).strip()
            for row in proposal_bound_rows
            if str(row.get("task_id", "")).strip()
        ],
        "proposal_bound_live_task_count": len(proposal_bound_rows),
        "proposal_bound_live_task_ids": [
            str(row.get("task_id", "")).strip()
            for row in proposal_bound_rows
            if str(row.get("task_id", "")).strip()
        ],
        "awaiting_feedback_task_count": len(awaiting_feedback_rows),
        "awaiting_feedback_task_ids": [
            str(row.get("task_id", "")).strip()
            for row in awaiting_feedback_rows
            if str(row.get("task_id", "")).strip()
        ],
        "pending_feedback_live_task_count": len(awaiting_feedback_rows),
        "pending_feedback_live_task_ids": [
            str(row.get("task_id", "")).strip()
            for row in awaiting_feedback_rows
            if str(row.get("task_id", "")).strip()
        ],
        "platform_recovery_task_count": len(platform_rows),
        "platform_recovery_task_ids": [item["task_id"] for item in platform_rows if item["task_id"]],
        "platform_attention_task_count": len(platform_attention_rows),
        "platform_attention_task_ids": [item["task_id"] for item in platform_attention_rows if item["task_id"]],
    }


def effective_wait_state_for_session(
    session_state: dict[str, Any],
    live_snapshot: dict[str, Any],
    *,
    next_action_hint: dict[str, Any] | None = None,
) -> str:
    if int(live_snapshot.get("running_task_count", live_snapshot.get("live_task_count", 0)) or 0) > 0:
        return WAITING_ON_ASYNC_SIGNAL
    if int(live_snapshot.get("awaiting_feedback_task_count", 0) or 0) > 0:
        return WAITING_ON_FEEDBACK_SIGNAL
    if should_inherit_recent_next_action(session_state, next_action_hint or {}):
        return ""
    waiting_state = canonicalize_taskboard_signal(str(session_state.get("waiting_state", "")).strip())
    if waiting_state == "none":
        waiting_state = ""
    if waiting_state:
        return waiting_state
    last_signal = canonicalize_taskboard_signal(str(session_state.get("last_signal", "")).strip())
    if last_signal == "none":
        last_signal = ""
    if last_signal in PARKED_IDLE_SIGNALS or last_signal == WAITING_ON_ASYNC_SIGNAL:
        return last_signal
    return ""


def automation_recommendation_for_session(
    live_snapshot: dict[str, Any],
    *,
    human_guidance_active: bool,
    effective_wait_state: str = "",
    parked_watchdog_due: bool = False,
    next_action_hint: dict[str, Any] | None = None,
    session_state: dict[str, Any] | None = None,
) -> str:
    current_session_state = session_state if isinstance(session_state, dict) else {}
    if human_guidance_active or int(live_snapshot.get("platform_attention_task_count", 0) or 0) > 0:
        return "needs_human_attention"
    if int(live_snapshot.get("awaiting_feedback_task_count", live_snapshot.get("pending_feedback_live_task_count", 0)) or 0) > 0:
        return "absorb_completed_receipt"
    if (
        int(live_snapshot.get("proposal_bound_running_task_count", live_snapshot.get("proposal_bound_live_task_count", 0)) or 0) > 0
        or int(live_snapshot.get("running_task_count", live_snapshot.get("live_task_count", 0)) or 0) > 0
    ):
        return "wait_for_live_task"
    normalized_wait_state = str(effective_wait_state or "").strip()
    if proposal_dispatch_ready_for_session(current_session_state, next_action_hint):
        return "finish_proposal_dispatch"
    if proposal_bootstrap_ready_for_session(
        current_session_state,
        next_action_hint or {},
        effective_wait_state=effective_wait_state,
    ):
        return "materialize_successor_proposal"
    if normalized_wait_state == WAITING_ON_ASYNC_SIGNAL:
        return "wait_for_async"
    if bool((next_action_hint or {}).get("collect_local_evidence", False)):
        return "collect_local_evidence"
    if bool((next_action_hint or {}).get("controller_inherit_local", False)):
        return "continue_local_microstep"
    if parked_watchdog_due and normalized_wait_state in PARKED_IDLE_SIGNALS:
        return "dispatch_parked_watchdog"
    if normalized_wait_state in PARKED_IDLE_SIGNALS:
        return "wait_for_external_evidence"
    return "safe_to_dispatch"


def build_continuous_mode_status_payload(
    config: AppConfig,
    *,
    target_session_id: str,
    resolved_from: str,
) -> dict[str, Any]:
    payload = load_continuous_research_mode(config, codex_session_id=target_session_id)
    resolved_session_id = str(target_session_id or payload.get("target_codex_session_id", "")).strip()
    states = iter_all_task_states(config)
    followups = load_followups(config)
    target_state = payload.get("target_session_state", {})
    if not isinstance(target_state, dict):
        target_state = {}
    live_snapshot = continuous_session_live_task_snapshot(config, resolved_session_id, states=states)
    anchor_spec = latest_continuous_research_anchor_spec(config, resolved_session_id, states=states) or {}
    next_action_hint = session_continuation_hint(
        config,
        resolved_session_id,
        spec=anchor_spec or target_state,
        states=states,
    )
    local_next_action_active = should_inherit_recent_next_action(target_state, next_action_hint)
    effective_wait_state = effective_wait_state_for_session(
        target_state,
        live_snapshot,
        next_action_hint=next_action_hint,
    )
    stored_waiting_state = canonicalize_taskboard_signal(str(target_state.get("waiting_state", "")).strip())
    stored_last_signal = canonicalize_taskboard_signal(str(target_state.get("last_signal", "")).strip())
    effective_last_signal = (
        WAITING_ON_ASYNC_SIGNAL
        if int(live_snapshot.get("running_task_count", live_snapshot.get("live_task_count", 0)) or 0) > 0
        else EXECUTION_READY_SIGNAL
        if local_next_action_active
        else (
            effective_wait_state
            or stored_last_signal
            or "none"
        )
    )
    automation_mode_name = automation_mode_label(config, codex_session_id=resolved_session_id) if resolved_session_id else "managed"
    managed_mode_active = automation_mode_is_managed(config, codex_session_id=resolved_session_id) if resolved_session_id else False
    backlog = reflow_backlog_summary(config, codex_session_id=resolved_session_id, followups=followups)
    parked_wait_age_seconds = (
        continuous_session_parked_wait_age_seconds(target_state)
        if effective_wait_state in PARKED_IDLE_SIGNALS
        else 0
    )
    parked_watchdog_due = (
        continuous_session_parked_watchdog_due(target_state)
        if effective_wait_state in PARKED_IDLE_SIGNALS
        else False
    )
    parked_watchdog_interval_seconds = continuous_session_parked_watchdog_interval_seconds(target_state)
    parked_watchdog_due_ts = (
        continuous_session_parked_watchdog_due_ts(target_state)
        if effective_wait_state in PARKED_IDLE_SIGNALS
        else 0.0
    )
    active_followup = active_session_followup(followups, resolved_session_id)
    next_actual_resume_ts = 0.0
    try:
        next_actual_resume_ts = float(active_followup.get("check_after_ts", 0) or 0) if active_followup else 0.0
    except (TypeError, ValueError):
        next_actual_resume_ts = 0.0
    next_actual_resume_at = format_unix_timestamp(next_actual_resume_ts) if next_actual_resume_ts > 0 else ""
    next_actual_resume_in_seconds = max(0, retry_after_seconds_from_target(next_actual_resume_ts)) if next_actual_resume_ts > 0 else 0
    parked_watchdog_due_at = format_unix_timestamp(parked_watchdog_due_ts) if parked_watchdog_due_ts > 0 else ""
    automation_recommendation = automation_recommendation_for_session(
        live_snapshot,
        human_guidance_active=managed_mode_active,
        effective_wait_state=effective_wait_state,
        parked_watchdog_due=parked_watchdog_due,
        next_action_hint=next_action_hint,
        session_state=target_state,
    )
    if managed_mode_active:
        automation_recommendation = "managed_backlog_only"
    effective_research_phase = effective_research_phase_for_session(
        target_state,
        next_action_hint=next_action_hint,
        effective_wait_state=effective_wait_state,
    )
    proposal_bootstrap_ready = proposal_bootstrap_ready_for_session(
        target_state,
        next_action_hint,
        effective_wait_state=effective_wait_state,
    )
    proposal_dispatch_ready = proposal_dispatch_ready_for_session(target_state, next_action_hint)
    enriched_target_state = {
        **target_state,
        "stored_waiting_state": stored_waiting_state,
        "stored_last_signal": stored_last_signal,
        "waiting_state": effective_wait_state if local_next_action_active else (effective_wait_state or stored_waiting_state),
        "last_signal": effective_last_signal,
        "effective_wait_state": effective_wait_state,
        "effective_last_signal": effective_last_signal,
        "research_phase": effective_research_phase,
        "effective_research_phase": effective_research_phase,
        "running_task_count": int(live_snapshot.get("running_task_count", live_snapshot.get("live_task_count", 0)) or 0),
        "running_task_ids": list(live_snapshot.get("running_task_ids", live_snapshot.get("live_task_ids", []))),
        "live_task_count": int(live_snapshot.get("live_task_count", 0) or 0),
        "live_task_ids": list(live_snapshot.get("live_task_ids", [])),
        "proposal_bound_running_task_count": int(
            live_snapshot.get("proposal_bound_running_task_count", live_snapshot.get("proposal_bound_live_task_count", 0)) or 0
        ),
        "proposal_bound_running_task_ids": list(
            live_snapshot.get("proposal_bound_running_task_ids", live_snapshot.get("proposal_bound_live_task_ids", []))
        ),
        "proposal_bound_live_task_count": int(live_snapshot.get("proposal_bound_live_task_count", 0) or 0),
        "proposal_bound_live_task_ids": list(live_snapshot.get("proposal_bound_live_task_ids", [])),
        "awaiting_feedback_task_count": int(
            live_snapshot.get("awaiting_feedback_task_count", live_snapshot.get("pending_feedback_live_task_count", 0)) or 0
        ),
        "awaiting_feedback_task_ids": list(
            live_snapshot.get("awaiting_feedback_task_ids", live_snapshot.get("pending_feedback_live_task_ids", []))
        ),
        "parked_wait_age_seconds": parked_wait_age_seconds,
        "parked_watchdog_due": parked_watchdog_due,
        "parked_watchdog_interval_seconds": parked_watchdog_interval_seconds,
        "parked_watchdog_due_ts": parked_watchdog_due_ts,
        "parked_watchdog_due_at": parked_watchdog_due_at,
        "active_followup_key": str(active_followup.get("followup_key", "")).strip(),
        "active_followup_reason": str(active_followup.get("reason", "")).strip(),
        "active_followup_type": str(active_followup.get("followup_type", "")).strip(),
        "active_followup_interval_seconds": int(active_followup.get("interval_seconds", 0) or 0),
        "active_followup_min_idle_seconds": int(active_followup.get("min_idle_seconds", 0) or 0),
        "active_followup_last_signal": str(active_followup.get("last_signal", "")).strip(),
        "next_actual_resume_ts": next_actual_resume_ts,
        "next_actual_resume_at": next_actual_resume_at,
        "next_actual_resume_in_seconds": next_actual_resume_in_seconds,
        "recent_next_bounded_action": next_action_hint,
        "proposal_bootstrap_ready": proposal_bootstrap_ready,
        "proposal_bootstrap_reason": str(next_action_hint.get("proposal_bootstrap_reason", "")).strip(),
        "proposal_dispatch_ready": proposal_dispatch_ready,
        "automation_recommendation": automation_recommendation,
        "automation_mode": automation_mode_name,
        "reflow_backlog_queue_depth": int(backlog.get("queue_depth", 0) or 0),
        "reflow_backlog_followup_count": int(backlog.get("followup_count", 0) or 0),
        "reflow_backlog_oldest_event_at": str(backlog.get("oldest_event_at", "")).strip(),
        "reflow_backlog_latest_event_at": str(backlog.get("latest_event_at", "")).strip(),
    }
    return {
        **payload,
        "target_codex_session_id": resolved_session_id,
        "target_session_state": enriched_target_state,
        "resolved_from": resolved_from,
        "effective_wait_state": effective_wait_state,
        "effective_last_signal": effective_last_signal,
        "research_phase": effective_research_phase,
        "effective_research_phase": effective_research_phase,
        "running_task_count": int(live_snapshot.get("running_task_count", live_snapshot.get("live_task_count", 0)) or 0),
        "running_task_ids": list(live_snapshot.get("running_task_ids", live_snapshot.get("live_task_ids", []))),
        "live_task_count": int(live_snapshot.get("live_task_count", 0) or 0),
        "live_task_ids": list(live_snapshot.get("live_task_ids", [])),
        "proposal_bound_running_task_count": int(
            live_snapshot.get("proposal_bound_running_task_count", live_snapshot.get("proposal_bound_live_task_count", 0)) or 0
        ),
        "proposal_bound_running_task_ids": list(
            live_snapshot.get("proposal_bound_running_task_ids", live_snapshot.get("proposal_bound_live_task_ids", []))
        ),
        "proposal_bound_live_task_count": int(live_snapshot.get("proposal_bound_live_task_count", 0) or 0),
        "proposal_bound_live_task_ids": list(live_snapshot.get("proposal_bound_live_task_ids", [])),
        "awaiting_feedback_task_count": int(
            live_snapshot.get("awaiting_feedback_task_count", live_snapshot.get("pending_feedback_live_task_count", 0)) or 0
        ),
        "awaiting_feedback_task_ids": list(
            live_snapshot.get("awaiting_feedback_task_ids", live_snapshot.get("pending_feedback_live_task_ids", []))
        ),
        "pending_feedback_live_task_count": int(live_snapshot.get("pending_feedback_live_task_count", 0) or 0),
        "pending_feedback_live_task_ids": list(live_snapshot.get("pending_feedback_live_task_ids", [])),
        "platform_recovery_task_count": int(live_snapshot.get("platform_recovery_task_count", 0) or 0),
        "platform_recovery_task_ids": list(live_snapshot.get("platform_recovery_task_ids", [])),
        "platform_attention_task_count": int(live_snapshot.get("platform_attention_task_count", 0) or 0),
        "platform_attention_task_ids": list(live_snapshot.get("platform_attention_task_ids", [])),
        "automation_mode": automation_mode_name,
        "managed_mode_active": managed_mode_active,
        "parked_wait_age_seconds": parked_wait_age_seconds,
        "parked_watchdog_due": parked_watchdog_due,
        "parked_watchdog_interval_seconds": parked_watchdog_interval_seconds,
        "parked_watchdog_due_ts": parked_watchdog_due_ts,
        "parked_watchdog_due_at": parked_watchdog_due_at,
        "active_followup_key": str(active_followup.get("followup_key", "")).strip(),
        "active_followup_reason": str(active_followup.get("reason", "")).strip(),
        "active_followup_type": str(active_followup.get("followup_type", "")).strip(),
        "active_followup_interval_seconds": int(active_followup.get("interval_seconds", 0) or 0),
        "active_followup_min_idle_seconds": int(active_followup.get("min_idle_seconds", 0) or 0),
        "active_followup_last_signal": str(active_followup.get("last_signal", "")).strip(),
        "next_actual_resume_ts": next_actual_resume_ts,
        "next_actual_resume_at": next_actual_resume_at,
        "next_actual_resume_in_seconds": next_actual_resume_in_seconds,
        "recent_next_bounded_action": next_action_hint,
        "proposal_bootstrap_ready": proposal_bootstrap_ready,
        "proposal_bootstrap_reason": str(next_action_hint.get("proposal_bootstrap_reason", "")).strip(),
        "proposal_dispatch_ready": proposal_dispatch_ready,
        "automation_recommendation": automation_recommendation,
        "reflow_backlog_queue_depth": int(backlog.get("queue_depth", 0) or 0),
        "reflow_backlog_followup_count": int(backlog.get("followup_count", 0) or 0),
        "reflow_backlog_oldest_event_at": str(backlog.get("oldest_event_at", "")).strip(),
        "reflow_backlog_latest_event_at": str(backlog.get("latest_event_at", "")).strip(),
    }


def refresh_continuous_session_for_live_tasks(
    config: AppConfig,
    session_id: str,
    *,
    states: list[dict[str, Any]] | None = None,
    updated_by: str,
    source: str,
) -> None:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return
    current_state = continuous_research_session_state(config, normalized_session_id)
    current_last_signal = canonicalize_taskboard_signal(str(current_state.get("last_signal", "")).strip())
    current_waiting_state = str(current_state.get("waiting_state", "")).strip()
    if current_last_signal == WAITING_ON_ASYNC_SIGNAL and not current_waiting_state:
        return
    clear_continuous_research_session_waiting_state(
        config,
        codex_session_id=normalized_session_id,
        evidence_token=continuous_research_session_evidence_token(config, normalized_session_id, states=states),
        last_signal=WAITING_ON_ASYNC_SIGNAL,
        stable_idle_repeat_count=0,
        updated_by=updated_by,
        source=source,
    )


def session_has_other_live_followup(config: AppConfig, session_id: str, *, exclude_followup_key: str = "") -> bool:
    return session_followup_present(load_followups(config), session_id, exclude_followup_key=exclude_followup_key)


def continuous_session_reminder_delay_seconds(config: AppConfig, session_id: str, spec: dict[str, Any]) -> int:
    last_activity_ts = latest_session_activity_ts(config, session_id, spec)
    if last_activity_ts <= 0:
        return DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS
    target_ts = last_activity_ts + DEFAULT_CONTINUOUS_RESEARCH_MIN_IDLE_SECONDS
    return max(DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS, retry_after_seconds_from_target(target_ts))


def continuous_session_parked_wait_age_seconds(
    session_state: dict[str, Any],
    *,
    now_ts: float | None = None,
) -> int:
    waiting_since_ts = (
        parse_timestamp_to_unix(session_state.get("waiting_since"))
        or parse_timestamp_to_unix(session_state.get("updated_at"))
        or 0.0
    )
    if waiting_since_ts <= 0:
        return 0
    current_ts = float(now_ts if now_ts is not None else time.time())
    return max(0, int(current_ts - waiting_since_ts))


def continuous_session_parked_watchdog_interval_seconds(session_state: dict[str, Any]) -> int:
    repeat_count = max(
        CONTINUOUS_RESEARCH_IDLE_LOOP_THRESHOLD,
        int(session_state.get("stable_idle_repeat_count", 0) or 0),
    )
    if repeat_count <= CONTINUOUS_RESEARCH_IDLE_LOOP_THRESHOLD:
        return DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS
    exponent = max(0, repeat_count - CONTINUOUS_RESEARCH_IDLE_LOOP_THRESHOLD - 1)
    interval = DEFAULT_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS * (2 ** exponent)
    return min(MAX_CONTINUOUS_RESEARCH_PARKED_REMINDER_SECONDS, interval)


def continuous_session_parked_watchdog_due_ts(session_state: dict[str, Any]) -> float:
    waiting_state = str(session_state.get("waiting_state", "")).strip()
    if waiting_state not in PARKED_IDLE_SIGNALS:
        return 0.0
    waiting_since_ts = (
        parse_timestamp_to_unix(session_state.get("waiting_since"))
        or parse_timestamp_to_unix(session_state.get("updated_at"))
        or 0.0
    )
    if waiting_since_ts <= 0:
        return 0.0
    interval_seconds = max(
        DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS,
        continuous_session_parked_watchdog_interval_seconds(session_state),
    )
    return waiting_since_ts + float(interval_seconds)


def continuous_session_parked_watchdog_due(
    session_state: dict[str, Any],
    *,
    now_ts: float | None = None,
    reminder_seconds: int | None = None,
) -> bool:
    waiting_state = str(session_state.get("waiting_state", "")).strip()
    if waiting_state not in PARKED_IDLE_SIGNALS:
        return False
    threshold_seconds = max(
        DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS,
        int(
            reminder_seconds
            if reminder_seconds is not None
            else continuous_session_parked_watchdog_interval_seconds(session_state)
        ),
    )
    if threshold_seconds <= 0:
        return True
    return continuous_session_parked_wait_age_seconds(session_state, now_ts=now_ts) >= threshold_seconds


def continuous_session_parked_watchdog_pending_delay_seconds(
    session_state: dict[str, Any],
    *,
    now_ts: float | None = None,
) -> int:
    due_ts = continuous_session_parked_watchdog_due_ts(session_state)
    current_ts = float(now_ts if now_ts is not None else time.time())
    if due_ts <= 0:
        return DEFAULT_CONTINUOUS_RESEARCH_INITIAL_PARKED_RECHECK_SECONDS
    return max(0, int(math.ceil(due_ts - current_ts)))


def continuous_session_reminder_schedule_params(
    config: AppConfig,
    session_id: str,
    spec: dict[str, Any],
    *,
    session_state: dict[str, Any] | None = None,
    waiting_state: str = "",
    parked_watchdog_due: bool = False,
    next_action_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_state = session_state if isinstance(session_state, dict) else {}
    normalized_waiting_state = str(waiting_state or current_state.get("waiting_state", "")).strip()
    if bool((next_action_hint or {}).get("dispatch_ready", False)):
        return {
            "reason": PROPOSAL_MATERIALIZATION_REASON,
            "delay_seconds": 0,
            "interval_seconds": DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS,
            "min_idle_seconds": 0,
            "last_signal": EXECUTION_READY_SIGNAL,
        }
    if should_inherit_recent_next_action(current_state, next_action_hint or {}):
        return {
            "reason": CONTINUOUS_RESEARCH_NEXT_ACTION_REASON,
            "delay_seconds": 0,
            "interval_seconds": DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS,
            "min_idle_seconds": 0,
            "last_signal": str(current_state.get("last_signal", "") or normalized_waiting_state).strip(),
        }
    if parked_watchdog_due and normalized_waiting_state in PARKED_IDLE_SIGNALS:
        parked_interval_seconds = continuous_session_parked_watchdog_interval_seconds(current_state)
        return {
            "reason": CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON,
            "delay_seconds": 0,
            "interval_seconds": parked_interval_seconds,
            "min_idle_seconds": 0,
            "last_signal": normalized_waiting_state,
        }
    return {
        "reason": CONTINUOUS_RESEARCH_IDLE_REASON,
        "delay_seconds": continuous_session_reminder_delay_seconds(config, session_id, spec),
        "interval_seconds": DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS,
        "min_idle_seconds": DEFAULT_CONTINUOUS_RESEARCH_MIN_IDLE_SECONDS,
        "last_signal": "",
    }


def ensure_continuous_research_session_reminders(config: AppConfig) -> list[dict[str, Any]]:
    processed: list[dict[str, Any]] = []
    enabled_sessions = continuous_research_enabled_session_ids(config)
    if not enabled_sessions:
        return processed
    states = iter_all_task_states(config)
    live_followups = load_followups(config)
    now_ts = time.time()
    for session_id in enabled_sessions:
        if session_followup_present(live_followups, session_id):
            append_followup_event_log(
                config,
                event="suppressed",
                reason="session_has_other_followup",
                followup=active_session_followup(live_followups, session_id),
                session_id=session_id,
                detail="continuous_session_reminder_already_present",
            )
            continue
        if human_guidance_mode_active(config, codex_session_id=session_id):
            append_followup_event_log(
                config,
                event="suppressed",
                reason="human_guidance_pause",
                session_id=session_id,
                detail="continuous_session_reminder_skipped",
            )
            processed.append({"session_id": session_id, "action": "continuous_session_reminder_skipped_human_guidance_pause"})
            continue
        if session_has_live_task(config, session_id, states=states):
            refresh_continuous_session_for_live_tasks(
                config,
                session_id,
                states=states,
                updated_by="dispatcher",
                source="continuous-session-reminder-live-task",
            )
            append_followup_event_log(
                config,
                event="suppressed",
                reason="session_has_running_task",
                session_id=session_id,
                detail="continuous_session_reminder_skipped",
            )
            processed.append({"session_id": session_id, "action": "continuous_session_reminder_skipped_running"})
            continue
        anchor_spec = latest_continuous_research_anchor_spec(config, session_id, states=states)
        if anchor_spec is None:
            append_followup_event_log(
                config,
                event="suppressed",
                reason="no_anchor",
                session_id=session_id,
                detail="continuous_session_reminder_skipped",
            )
            processed.append({"session_id": session_id, "action": "continuous_session_reminder_skipped_no_anchor"})
            continue
        session_state = continuous_research_session_state(config, session_id)
        waiting_state = str(session_state.get("waiting_state", "")).strip()
        next_action_hint = session_continuation_hint(config, session_id, spec=anchor_spec, states=states)
        anchor_spec_with_hint = {
            **anchor_spec,
            "controller_continuation_hint": dict(next_action_hint),
        } if next_action_hint.get("action_text") else dict(anchor_spec)
        parked_watchdog_due = False
        if waiting_state in PARKED_IDLE_SIGNALS:
            current_token = continuous_research_session_evidence_token(
                config,
                session_id,
                spec=anchor_spec,
                states=states,
            )
            waiting_token = str(session_state.get("waiting_evidence_token", "")).strip()
            parked_watchdog_due = continuous_session_parked_watchdog_due(session_state, now_ts=now_ts)
            parked_wait_age_seconds = continuous_session_parked_wait_age_seconds(session_state, now_ts=now_ts)
            if waiting_token and waiting_token == current_token and not parked_watchdog_due:
                if should_inherit_recent_next_action(session_state, next_action_hint):
                    clear_continuous_research_session_waiting_state(
                        config,
                        codex_session_id=session_id,
                        evidence_token=current_token,
                        last_signal=LOCAL_MICROSTEP_BATCH_SIGNAL,
                        stable_idle_repeat_count=max(0, int(session_state.get("stable_idle_repeat_count", 0) or 0)),
                        updated_by="dispatcher",
                        source="continuous-session-reminder-next-action-unpark",
                    )
                    session_state = continuous_research_session_state(config, session_id)
                    schedule_params = continuous_session_reminder_schedule_params(
                        config,
                        session_id,
                        anchor_spec_with_hint,
                        session_state=session_state,
                        waiting_state=waiting_state,
                        parked_watchdog_due=False,
                        next_action_hint=next_action_hint,
                    )
                    followup_key = continuous_session_followup_key_for(session_id)
                    schedule_continuous_session_reminder(
                        config,
                        session_id=session_id,
                        spec=anchor_spec_with_hint,
                        reason=str(schedule_params.get("reason", CONTINUOUS_RESEARCH_NEXT_ACTION_REASON)),
                        delay_seconds=int(schedule_params.get("delay_seconds", 0) or 0),
                        interval_seconds=int(schedule_params.get("interval_seconds", DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS) or 0),
                        min_idle_seconds=int(schedule_params.get("min_idle_seconds", 0) or 0),
                        last_signal=str(schedule_params.get("last_signal", "")).strip(),
                    )
                    live_followups.append(
                        {
                            "followup_key": followup_key,
                            "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                            "codex_session_id": session_id,
                        }
                    )
                    processed.append(
                        {
                            "session_id": session_id,
                            "task_id": str(anchor_spec.get("task_id", "")),
                            "followup_key": followup_key,
                            "action": "continuous_session_reminder_scheduled_next_action",
                            "reason": str(schedule_params.get("reason", CONTINUOUS_RESEARCH_NEXT_ACTION_REASON)),
                            "next_action_hash": str(next_action_hint.get("action_hash", "")),
                        }
                    )
                    continue
                parked_watchdog_interval_seconds = continuous_session_parked_watchdog_interval_seconds(session_state)
                parked_watchdog_delay_seconds = continuous_session_parked_watchdog_pending_delay_seconds(
                    session_state,
                    now_ts=now_ts,
                )
                followup_key = continuous_session_followup_key_for(session_id)
                schedule_continuous_session_reminder(
                    config,
                    session_id=session_id,
                    spec=anchor_spec_with_hint,
                    reason=CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON,
                    delay_seconds=parked_watchdog_delay_seconds,
                    interval_seconds=parked_watchdog_interval_seconds,
                    min_idle_seconds=0,
                    last_signal=waiting_state,
                )
                live_followups.append(
                    {
                        "followup_key": followup_key,
                        "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                        "codex_session_id": session_id,
                    }
                )
                processed.append(
                    {
                        "session_id": session_id,
                        "task_id": str(anchor_spec.get("task_id", "")),
                        "followup_key": followup_key,
                        "action": "continuous_session_reminder_scheduled_parked_watchdog_pending",
                        "waiting_state": waiting_state,
                        "parked_wait_age_seconds": parked_wait_age_seconds,
                        "parked_watchdog_delay_seconds": parked_watchdog_delay_seconds,
                        "parked_watchdog_interval_seconds": parked_watchdog_interval_seconds,
                    }
                )
                continue
            if waiting_token and waiting_token == current_token and parked_watchdog_due:
                parked_watchdog_interval_seconds = continuous_session_parked_watchdog_interval_seconds(session_state)
                processed.append(
                    {
                        "session_id": session_id,
                        "task_id": str(anchor_spec.get("task_id", "")),
                        "action": "continuous_session_reminder_parked_watchdog_due",
                        "waiting_state": waiting_state,
                        "parked_wait_age_seconds": parked_wait_age_seconds,
                        "parked_watchdog_interval_seconds": parked_watchdog_interval_seconds,
                    }
                )
            else:
                clear_continuous_research_session_waiting_state(
                    config,
                    codex_session_id=session_id,
                    evidence_token=current_token,
                    last_signal=waiting_state,
                    updated_by="dispatcher",
                    source="continuous-session-reminder-unparked",
                )
                processed.append(
                    {
                        "session_id": session_id,
                        "task_id": str(anchor_spec.get("task_id", "")),
                        "action": "continuous_session_parked_idle_cleared",
                        "waiting_state": waiting_state,
                    }
                )
        followup_key = continuous_session_followup_key_for(session_id)
        schedule_params = continuous_session_reminder_schedule_params(
            config,
            session_id,
            anchor_spec_with_hint,
            session_state=session_state,
            waiting_state=waiting_state,
            parked_watchdog_due=parked_watchdog_due,
            next_action_hint=next_action_hint,
        )
        schedule_continuous_session_reminder(
            config,
            session_id=session_id,
            spec=anchor_spec_with_hint,
            reason=str(schedule_params.get("reason", CONTINUOUS_RESEARCH_IDLE_REASON)),
            delay_seconds=int(schedule_params.get("delay_seconds", DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS) or 0),
            interval_seconds=int(schedule_params.get("interval_seconds", DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS) or 0),
            min_idle_seconds=int(schedule_params.get("min_idle_seconds", DEFAULT_CONTINUOUS_RESEARCH_MIN_IDLE_SECONDS) or 0),
            last_signal=str(schedule_params.get("last_signal", "")).strip(),
        )
        live_followups.append(
            {
                "followup_key": followup_key,
                "followup_type": CONTINUOUS_SESSION_REMINDER_FOLLOWUP_TYPE,
                "codex_session_id": session_id,
            }
        )
        processed.append(
            {
                "session_id": session_id,
                "task_id": str(anchor_spec.get("task_id", "")),
                "followup_key": followup_key,
                "action": "continuous_session_reminder_scheduled",
                "reason": str(schedule_params.get("reason", CONTINUOUS_RESEARCH_IDLE_REASON)),
                "delay_seconds": int(schedule_params.get("delay_seconds", DEFAULT_CONTINUOUS_RESEARCH_DELAY_SECONDS) or 0),
                "interval_seconds": int(schedule_params.get("interval_seconds", DEFAULT_CONTINUOUS_RESEARCH_INTERVAL_SECONDS) or 0),
                "min_idle_seconds": int(schedule_params.get("min_idle_seconds", DEFAULT_CONTINUOUS_RESEARCH_MIN_IDLE_SECONDS) or 0),
            }
        )
    return processed


def prelaunch_gpu_recheck(spec: dict[str, Any]) -> tuple[bool, list[dict[str, Any]], str]:
    gpu_ids = task_requested_gpu_ids(spec)
    if int(spec.get("gpu_slots", 0) or 0) <= 0 or not gpu_ids:
        return True, [], ""
    if shutil_which("nvidia-smi") == "":
        return True, [], ""
    grace_seconds = int(spec.get("launch_grace_seconds", 0) or 0)
    if grace_seconds > 0:
        time.sleep(grace_seconds)
    gpu_rows = get_gpu_summary_table()
    if not gpu_rows:
        return True, [], ""
    snapshot = selected_gpu_snapshot(gpu_rows, gpu_ids)
    row_by_index = {int(row.get("index", -1)): row for row in gpu_rows}
    for gpu_id in gpu_ids:
        row = row_by_index.get(int(gpu_id))
        if row is None:
            return False, snapshot, f"launch_recheck_missing_gpu:{gpu_id}"
        if not gpu_row_can_host_task(row, spec):
            return (
                False,
                snapshot,
                f"launch_recheck_failed:gpu{gpu_id}:free_mb={gpu_row_free_mb(row)}:util={int(row.get('gpu_util_percent', 0) or 0)}",
            )
    return True, snapshot, ""


def dependency_satisfied(config: AppConfig, dependency_key: str) -> bool:
    state = latest_task_state_for_key(config, dependency_key)
    if state is None:
        return False
    status = str(state.get("status", ""))
    if status not in {"completed", "observed_exit"}:
        return False
    if bool(state.get("require_signal_to_unblock", False)):
        return str(state.get("taskboard_signal", "")).strip() in SUCCESS_TASKBOARD_SIGNALS
    return True


def unresolved_dependencies(config: AppConfig, state: dict[str, Any]) -> list[str]:
    resolution, _latest_states = dependency_resolution(config, state)
    return [str(item.get("task_key", "")) for item in resolution if not bool(item.get("satisfied", False))]


def count_live_running_tasks(config: AppConfig, states: list[dict[str, Any]]) -> int:
    count = 0
    for state in states:
        if str(state.get("status", "")) not in ACTIVE_TASK_STATUSES:
            continue
        if task_execution_still_live(config, state):
            count += 1
    return count


def task_runner_process_alive(state: dict[str, Any]) -> bool:
    raw_pid = state.get("pid", 0)
    try:
        pid = int(raw_pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or not pid_exists(pid):
        return False
    cmdline = read_pid_cmdline(pid)
    if not cmdline or " run " not in f" {cmdline} ":
        return False
    if "codex_taskboard.cli" not in cmdline and "task_bridge.py" not in cmdline:
        return False
    paths = state.get("paths", {})
    spec_path = str(paths.get("spec_path", "")).strip() if isinstance(paths, dict) else ""
    if spec_path and spec_path not in cmdline:
        task_id = str(state.get("task_id", "")).strip()
        legacy_spec_path = str(state.get("legacy_spec_path", "")).strip()
        if legacy_spec_path and legacy_spec_path in cmdline:
            return True
        if task_id and task_id in cmdline:
            return True
        return False
    return True


def task_execution_still_live(config: AppConfig, state: dict[str, Any]) -> bool:
    if watched_pid_alive(state):
        return True
    session_name = str(state.get("tmux_session_name", "")).strip()
    if session_name and tmux_session_exists(config, session_name):
        return True
    return task_runner_process_alive(state)


def task_has_launch_metadata(state: dict[str, Any]) -> bool:
    if any(str(state.get(key, "")).strip() for key in ("started_at", "started_via_tmux_at", "ended_at")):
        return True
    for key in ("pid", "watch_pid"):
        try:
            if int(state.get(key, 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def reconcile_active_task_state(config: AppConfig, state: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_task_state_payload(state)
    status = str(normalized.get("status", ""))
    if status not in ACTIVE_TASK_STATUSES:
        return normalized
    if not task_has_launch_metadata(normalized):
        return normalized
    if task_execution_still_live(config, normalized):
        return normalized
    reconciled = dict(normalized)
    if status == "watching":
        reconciled["status"] = "observed_exit"
        default_summary = "attached PID is no longer present and the watcher process is not alive"
        reconciled["failure_kind"] = str(reconciled.get("failure_kind", "") or "observed_exit")
    else:
        reconciled["status"] = "terminated"
        default_summary = "tmux session missing and task runner process is not alive"
        reconciled["failure_kind"] = str(reconciled.get("failure_kind", "") or "stale_state")
    reconciled["needs_attention"] = True
    reconciled["attention_reason"] = str(reconciled.get("attention_reason", "") or "stale_state:supervisor_missing")
    reconciled["attention_message"] = str(reconciled.get("attention_message", "") or default_summary)
    reconciled["failure_summary"] = str(reconciled.get("failure_summary", "") or default_summary)
    if not str(reconciled.get("ended_at", "")).strip():
        reconciled["ended_at"] = utc_now()
    return normalize_task_state_payload(reconciled)


def persist_task_gpu_assignment(
    config: AppConfig,
    task_id: str,
    spec: dict[str, Any],
    *,
    assigned_gpus: list[int],
    assignment_source: str,
) -> dict[str, Any]:
    normalized_gpus = parse_gpu_id_list(assigned_gpus)
    if not normalized_gpus:
        return spec
    updated_spec = dict(spec)
    updated_spec["assigned_gpus"] = normalized_gpus
    env = updated_spec.get("env", {})
    if not isinstance(env, dict):
        env = {}
    else:
        env = {str(key): str(value) for key, value in env.items()}
    if not command_sets_cuda_visible_devices(str(updated_spec.get("command", ""))):
        visible_gpu_ids = map_host_gpus_to_executor_visible_gpus(updated_spec, normalized_gpus)
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(item) for item in visible_gpu_ids)
    updated_spec["env"] = env
    write_task_spec(config, task_id, updated_spec)
    merge_task_state(
        config,
        task_id,
        assigned_gpus=normalized_gpus,
        gpu_assignment_source=assignment_source,
        remote_visible_gpus=map_host_gpus_to_executor_visible_gpus(updated_spec, normalized_gpus),
    )
    return updated_spec


def persist_task_cpu_assignment(
    config: AppConfig,
    task_id: str,
    spec: dict[str, Any],
    *,
    cpu_threads: int,
    cpu_workers: int = 0,
    assignment_source: str,
    worker_assignment_source: str = "",
) -> dict[str, Any]:
    normalized_threads = max(0, int(cpu_threads or 0))
    normalized_workers = max(0, int(cpu_workers or 0))
    if normalized_threads <= 0 and normalized_workers <= 0:
        return spec
    updated_spec = dict(spec)
    updated_spec["command_template"] = str(updated_spec.get("command_template", updated_spec.get("command", "")))
    cpu_profile = resolved_cpu_profile(updated_spec)
    thread_policy = resolve_cpu_thread_policy(updated_spec)
    worker_policy = resolve_cpu_worker_policy(updated_spec)
    updated_spec["cpu_profile"] = declared_cpu_profile(updated_spec)
    updated_spec["cpu_threads_mode"] = str(thread_policy.get("mode", "fixed"))
    if str(thread_policy.get("mode", "fixed")) == "adaptive":
        updated_spec["cpu_threads"] = int(thread_policy.get("min_threads", normalized_threads) or normalized_threads)
        updated_spec["cpu_threads_min"] = int(thread_policy.get("min_threads", normalized_threads) or normalized_threads)
        updated_spec["cpu_threads_max"] = int(thread_policy.get("max_threads", 0) or 0)
    else:
        updated_spec["cpu_threads"] = normalized_threads
        updated_spec["cpu_threads_min"] = normalized_threads
        updated_spec["cpu_threads_max"] = normalized_threads
    updated_spec["assigned_cpu_threads"] = normalized_threads
    updated_spec["cpu_thread_source"] = assignment_source
    if str(worker_policy.get("mode", "fixed")) == "adaptive":
        updated_spec["cpu_workers"] = int(worker_policy.get("min_workers", normalized_workers) or normalized_workers)
        updated_spec["cpu_workers_min"] = int(worker_policy.get("min_workers", normalized_workers) or normalized_workers)
        if int(worker_policy.get("max_workers", 0) or 0) > 0:
            updated_spec["cpu_workers_max"] = int(worker_policy.get("max_workers", 0) or 0)
    else:
        updated_spec["cpu_workers"] = normalized_workers
        updated_spec["cpu_workers_min"] = normalized_workers
        updated_spec["cpu_workers_max"] = normalized_workers
    updated_spec["assigned_cpu_workers"] = normalized_workers
    updated_spec["cpu_worker_source"] = worker_assignment_source or str(worker_policy.get("source", "") or "default")
    runtime_budget = normalized_threads + normalized_workers
    updated_spec["command"] = render_task_command_template(
        str(updated_spec.get("command_template", updated_spec.get("command", ""))),
        cpu_threads=normalized_threads,
        cpu_workers=normalized_workers,
        cpu_profile=cpu_profile,
        cpu_budget=runtime_budget,
    )
    if str(updated_spec.get("execution_mode", "shell")).strip() == "shell":
        env = updated_spec.get("env", {})
        if not isinstance(env, dict):
            env = {}
        else:
            env = {str(key): str(value) for key, value in env.items()}
        env["CODEX_TASKBOARD_CPU_THREADS"] = str(normalized_threads)
        env["CODEX_TASKBOARD_CPU_WORKERS"] = str(normalized_workers)
        env["CODEX_TASKBOARD_CPU_PROFILE"] = str(cpu_profile)
        env["CODEX_TASKBOARD_CPU_BUDGET"] = str(runtime_budget)
        if not command_sets_cpu_thread_limits(str(updated_spec.get("command_template", updated_spec.get("command", "")))):
            manage_env = str(thread_policy.get("source", "")) not in {"env", "command"}
            for key in CPU_THREAD_ENV_KEYS:
                if manage_env or key not in env:
                    env[key] = str(normalized_threads)
        if not command_sets_cpu_worker_limits(str(updated_spec.get("command_template", updated_spec.get("command", "")))):
            manage_worker_env = str(worker_policy.get("source", "")) not in {"env", "command"}
            for key in CPU_WORKER_ENV_KEYS:
                if manage_worker_env or key not in env:
                    env[key] = str(normalized_workers)
        updated_spec["env"] = env
    write_task_spec(config, task_id, updated_spec)
    merge_task_state(
        config,
        task_id,
        cpu_threads=normalized_threads,
        cpu_threads_mode=updated_spec["cpu_threads_mode"],
        cpu_threads_min=int(updated_spec.get("cpu_threads_min", 0) or 0),
        cpu_threads_max=int(updated_spec.get("cpu_threads_max", 0) or 0),
        assigned_cpu_threads=normalized_threads,
        cpu_profile=updated_spec["cpu_profile"],
        cpu_workers=int(updated_spec.get("cpu_workers", 0) or 0),
        cpu_workers_min=int(updated_spec.get("cpu_workers_min", 0) or 0),
        cpu_workers_max=int(updated_spec.get("cpu_workers_max", 0) or 0),
        assigned_cpu_workers=normalized_workers,
        cpu_thread_source=assignment_source,
        cpu_worker_source=updated_spec["cpu_worker_source"],
        command=updated_spec["command"],
    )
    return updated_spec


def start_existing_task(
    config: AppConfig,
    task_id: str,
    *,
    assigned_gpus: list[int] | None = None,
    assignment_source: str = "",
    assigned_cpu_threads: int | None = None,
    assigned_cpu_workers: int | None = None,
    cpu_assignment_source: str = "",
    cpu_worker_assignment_source: str = "",
    why_started: str = "",
    dispatch_gpu_snapshot: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    spec = load_task_spec(config, task_id)
    if not spec:
        raise ValueError(f"Task spec not found: {task_id}")
    state = load_task_state(config, task_id)
    status = str(state.get("status", ""))
    if status == "running":
        return state
    if status not in RUNNABLE_STATUSES:
        raise ValueError(f"Task {task_id} is not startable from status={status}")
    cpu_policy = resolve_cpu_thread_policy(spec)
    worker_policy = resolve_cpu_worker_policy(spec)
    cpu_threads = (
        max(0, int(assigned_cpu_threads or 0))
        or max(0, int(spec.get("assigned_cpu_threads", 0) or 0))
        or max(0, int(cpu_policy.get("reservation_threads", 0) or 0))
    )
    cpu_workers = (
        max(0, int(assigned_cpu_workers or 0))
        or max(0, int(spec.get("assigned_cpu_workers", 0) or 0))
        or max(0, int(worker_policy.get("reservation_workers", 0) or 0))
    )
    cpu_thread_source = cpu_assignment_source or str(cpu_policy.get("source", "") or "default")
    cpu_worker_source = cpu_worker_assignment_source or str(worker_policy.get("source", "") or "default")
    if cpu_threads > 0 or cpu_workers > 0:
        spec = persist_task_cpu_assignment(
            config,
            task_id,
            spec,
            cpu_threads=cpu_threads,
            cpu_workers=cpu_workers,
            assignment_source=cpu_thread_source,
            worker_assignment_source=cpu_worker_source,
        )
    requested_gpu_ids = task_requested_gpu_ids(spec)
    launch_gpu_ids = parse_gpu_id_list(assigned_gpus if assigned_gpus is not None else requested_gpu_ids)
    gpu_snapshot = list(dispatch_gpu_snapshot or [])
    if int(spec.get("gpu_slots", 0) or 0) > 0 and not launch_gpu_ids:
        gpu_rows = get_gpu_summary_table()
        total_gpu_slots = detect_gpu_count() or len(gpu_rows)
        selected, selected_reason = select_gpu_ids_for_task(
            spec,
            total_gpu_slots=total_gpu_slots,
            gpu_rows=gpu_rows,
            reserved_gpu_ids=set(),
        )
        if selected is None:
            raise ValueError(f"Task {task_id} is not launchable yet: {selected_reason}")
        launch_gpu_ids = selected
        assignment_source = assignment_source or selected_reason
        gpu_snapshot = selected_gpu_snapshot(gpu_rows, launch_gpu_ids)
    if launch_gpu_ids:
        source = assignment_source or ("fixed" if requested_gpu_ids else "scheduler")
        spec = persist_task_gpu_assignment(config, task_id, spec, assigned_gpus=launch_gpu_ids, assignment_source=source)
    elif not gpu_snapshot and requested_gpu_ids:
        gpu_snapshot = selected_gpu_snapshot(get_gpu_summary_table(), requested_gpu_ids)
    session_name = str(spec["tmux_session_name"])
    if tmux_session_exists(config, session_name):
        raise ValueError(f"tmux session already exists: {session_name}")
    runner_command = [
        *tmux_command(config, "new-session"),
        "-d",
        "-s",
        session_name,
        "bash",
        "-lc",
        shlex.join(
            [
                sys.executable,
                "-m",
                "codex_taskboard.cli",
                "run",
                "--app-home",
                str(config.app_home),
                "--codex-home",
                str(config.codex_home),
                "--codex-bin",
                config.codex_bin,
                "--tmux-bin",
                config.tmux_bin,
                "--spec-file",
                str(task_spec_path(config, task_id)),
            ]
        ),
    ]
    completed = run_subprocess(runner_command, cwd=spec["workdir"], timeout=30)
    append_log(
        task_runner_log_path(config, task_id),
        f"tmux_launch returncode={completed.returncode} stdout={completed.stdout[-1000:]} stderr={completed.stderr[-1000:]}",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1000:] or completed.stdout[-1000:] or "failed to start tmux session")
    return merge_task_state(
        config,
        task_id,
        status="running",
        started_via_tmux_at=utc_now(),
        why_started=why_started,
        dispatch_gpu_snapshot=gpu_snapshot,
        rejected_reason="",
    )


def submit_spec_unlocked(config: AppConfig, spec: dict[str, Any], *, hold: bool = False) -> dict[str, Any]:
    execution_mode = str(spec.get("execution_mode", "shell")).strip() or "shell"
    raw_task_id = str(spec.get("task_id", "")).strip()
    if not raw_task_id:
        raise ValueError("Missing required field: task_id")
    task_id = normalize_task_id(raw_task_id)
    if not task_id:
        raise ValueError(f"Invalid task_id: {raw_task_id}")
    workdir = str(Path(str(spec.get("workdir", ""))).expanduser().resolve())
    if not workdir or not Path(workdir).is_dir():
        raise ValueError(f"workdir does not exist: {workdir}")
    command = str(spec.get("command", "")).strip()
    if execution_mode == "codex_subagent" and not command:
        subagent_model = str(spec.get("subagent_model", "gpt-5.4")).strip() or "gpt-5.4"
        command = f"codex-subagent:{subagent_model}"
    if not command:
        raise ValueError("Missing required field: command")
    feedback_mode = str(spec.get("feedback_mode", "auto")).strip() or "auto"
    if feedback_mode not in {"auto", "manual", "off"}:
        raise ValueError("feedback_mode must be one of: auto, manual, off")
    codex_session_id = resolve_requested_codex_session_id(spec.get("codex_session_id", ""), feedback_mode=feedback_mode)
    spec = {**spec, "codex_session_id": codex_session_id}
    if feedback_mode == "auto" and not codex_session_id:
        raise ValueError("Missing required field: codex_session_id when feedback_mode=auto")
    codex_exec_mode = str(spec.get("codex_exec_mode", "dangerous")).strip() or "dangerous"
    if codex_exec_mode not in {"dangerous", "full-auto"}:
        raise ValueError("codex_exec_mode must be either 'dangerous' or 'full-auto'")
    artifact_globs = spec.get("artifact_globs", [])
    if artifact_globs is None:
        artifact_globs = []
    if not isinstance(artifact_globs, list):
        raise ValueError("artifact_globs must be a list")
    env = spec.get("env", {})
    if env is None:
        env = {}
    if not isinstance(env, dict):
        raise ValueError("env must be a dictionary")
    proposal_base_workdir = str(spec.get("proposal_base_workdir", workdir) or workdir)
    raw_proposal = extract_raw_proposal_value(spec)
    proposal_source = str(spec.get("proposal_source", "")).strip()
    if raw_proposal is MISSING:
        proposal_path = normalize_proposal_path(spec.get("proposal_path", ""), workdir=proposal_base_workdir)
    else:
        proposal_path = normalize_proposal_path(raw_proposal, workdir=proposal_base_workdir)
        if not proposal_source:
            proposal_source = "explicit" if proposal_path else "explicit_clear"
    if proposal_path and not proposal_source:
        proposal_source = "explicit"
    if not proposal_path and proposal_source not in {"", "explicit_clear"}:
        proposal_source = ""
    proposal_owner = bool(proposal_path) and parse_boolish(
        spec.get("proposal_owner", infer_proposal_owner(spec)),
        default=infer_proposal_owner(spec),
    )
    raw_closeout_proposal_dir = extract_raw_closeout_proposal_dir(spec)
    closeout_proposal_dir_source = str(spec.get("closeout_proposal_dir_source", "")).strip()
    if raw_closeout_proposal_dir is MISSING:
        closeout_proposal_dir = normalize_closeout_proposal_dir(spec.get("closeout_proposal_dir", ""), workdir=proposal_base_workdir)
    else:
        closeout_proposal_dir = normalize_closeout_proposal_dir(raw_closeout_proposal_dir, workdir=proposal_base_workdir)
        if not closeout_proposal_dir_source:
            closeout_proposal_dir_source = "explicit" if closeout_proposal_dir else "explicit_clear"
    if closeout_proposal_dir and not closeout_proposal_dir_source:
        closeout_proposal_dir_source = "explicit"
    if not closeout_proposal_dir and closeout_proposal_dir_source not in {"", "explicit_clear"}:
        closeout_proposal_dir_source = ""
    raw_project_history_file = extract_raw_project_history_file(spec)
    project_history_file_source = str(spec.get("project_history_file_source", "")).strip()
    if raw_project_history_file is MISSING:
        project_history_file = normalize_project_history_file(spec.get("project_history_file", ""), workdir=proposal_base_workdir)
    else:
        project_history_file = normalize_project_history_file(raw_project_history_file, workdir=proposal_base_workdir)
        if not project_history_file_source:
            project_history_file_source = "explicit" if project_history_file else "explicit_clear"
    if project_history_file and not project_history_file_source:
        project_history_file_source = "explicit"
    if not project_history_file and project_history_file_source not in {"", "explicit_clear"}:
        project_history_file_source = ""
    executor_default_env = spec.get("executor_default_env", {})
    if executor_default_env is None:
        executor_default_env = {}
    if not isinstance(executor_default_env, dict):
        raise ValueError("executor_default_env must be a dictionary")
    task_key = normalize_task_id(str(spec.get("task_key", task_id)).strip() or task_id)
    if not task_key:
        raise ValueError("Invalid task_key")
    agent_name = str(spec.get("agent_name", "")).strip()
    allow_session_rebind = bool(spec.get("allow_session_rebind", False))
    allow_duplicate_submit = bool(spec.get("allow_duplicate_submit", False))
    enforce_session_binding_guard(
        config,
        task_id=task_id,
        task_key=task_key,
        workdir=workdir,
        agent_name=agent_name,
        codex_session_id=codex_session_id,
        allow_session_rebind=allow_session_rebind,
    )
    duplicate_matches, duplicate_warning = enforce_duplicate_submit_guard(
        config,
        task_id=task_id,
        codex_session_id=codex_session_id,
        proposal_path=proposal_path,
        command=command,
        allow_duplicate_submit=allow_duplicate_submit,
    )
    replace_existing = bool(spec.get("replace_existing", True))
    prepare_task_slot(config, task_id=task_id, task_key=task_key, replace_existing=replace_existing)
    ensure_task_layout(config, task_id)
    tmux_session_name = build_tmux_session_name(task_id)
    normalized_env = {str(key): str(value) for key, value in env.items()}
    for key in PROPOSAL_ENV_KEYS:
        normalized_env.pop(key, None)
    for key in CLOSEOUT_PROPOSAL_DIR_ENV_KEYS:
        normalized_env.pop(key, None)
    for key in PROJECT_HISTORY_FILE_ENV_KEYS:
        normalized_env.pop(key, None)
    if execution_mode in {"shell", "ssh_shell"}:
        if proposal_path:
            normalized_env[PROPOSAL_ENV_KEY] = proposal_path
            if proposal_source:
                normalized_env[PROPOSAL_SOURCE_ENV_KEY] = proposal_source
        elif proposal_source == "explicit_clear":
            normalized_env[PROPOSAL_ENV_KEY] = ""
            normalized_env[PROPOSAL_SOURCE_ENV_KEY] = proposal_source
        if closeout_proposal_dir:
            normalized_env[CLOSEOUT_PROPOSAL_DIR_ENV_KEY] = closeout_proposal_dir
            if closeout_proposal_dir_source:
                normalized_env[CLOSEOUT_PROPOSAL_DIR_SOURCE_ENV_KEY] = closeout_proposal_dir_source
        elif closeout_proposal_dir_source == "explicit_clear":
            normalized_env[CLOSEOUT_PROPOSAL_DIR_ENV_KEY] = ""
            normalized_env[CLOSEOUT_PROPOSAL_DIR_SOURCE_ENV_KEY] = closeout_proposal_dir_source
        if project_history_file:
            normalized_env[PROJECT_HISTORY_FILE_ENV_KEY] = project_history_file
            if project_history_file_source:
                normalized_env[PROJECT_HISTORY_FILE_SOURCE_ENV_KEY] = project_history_file_source
        elif project_history_file_source == "explicit_clear":
            normalized_env[PROJECT_HISTORY_FILE_ENV_KEY] = ""
            normalized_env[PROJECT_HISTORY_FILE_SOURCE_ENV_KEY] = project_history_file_source
    gpu_slots = infer_gpu_slots(command, normalized_env, spec.get("gpu_slots"))
    normalized_spec = {
        "version": VERSION,
        "task_id": task_id,
        "task_key": task_key,
        "client_task_id": normalize_task_id(str(spec.get("client_task_id", "")).strip()),
        "client_task_key": normalize_task_id(str(spec.get("client_task_key", "")).strip()),
        "execution_mode": execution_mode,
        "submitted_at": utc_now(),
        "workdir": workdir,
        "command": command,
        "command_template": str(spec.get("command_template", command)),
        "codex_session_id": codex_session_id,
        "agent_name": agent_name,
        "proposal_path": proposal_path,
        "proposal_source": proposal_source,
        "proposal_owner": proposal_owner,
        "closeout_proposal_dir": closeout_proposal_dir,
        "closeout_proposal_dir_source": closeout_proposal_dir_source,
        "project_history_file": project_history_file,
        "project_history_file_source": project_history_file_source,
        "allow_session_rebind": allow_session_rebind,
        "allow_duplicate_submit": allow_duplicate_submit,
        "priority": int(spec.get("priority", 0)),
        "gpu_slots": gpu_slots,
        "cpu_profile": normalize_cpu_profile(spec.get("cpu_profile", "auto")),
        "cpu_threads": int(spec.get("cpu_threads", 0) or 0),
        "cpu_threads_min": int(spec.get("cpu_threads_min", 0) or 0),
        "cpu_threads_max": int(spec.get("cpu_threads_max", 0) or 0),
        "cpu_threads_mode": str(spec.get("cpu_threads_mode", "")).strip(),
        "cpu_workers": int(spec.get("cpu_workers", 0) or 0),
        "cpu_workers_min": int(spec.get("cpu_workers_min", 0) or 0),
        "cpu_workers_max": int(spec.get("cpu_workers_max", 0) or 0),
        "gpu_min_free_mb": int(spec.get("gpu_min_free_mb", 0) or 0),
        "gpu_max_util_percent": int(spec.get("gpu_max_util_percent", 0) or 0),
        "assigned_gpus": parse_gpu_id_list(spec.get("assigned_gpus", [])),
        "allowed_gpus": parse_gpu_id_list(spec.get("allowed_gpus", [])),
        "assigned_cpu_threads": 0,
        "assigned_cpu_workers": 0,
        "replace_existing": replace_existing,
        "feedback_mode": feedback_mode,
        "owner_tenant": normalize_task_id(str(spec.get("owner_tenant", "")).strip()),
        "owner_role": str(spec.get("owner_role", "")).strip().lower(),
        "owner_label": str(spec.get("owner_label", "")).strip(),
        "submitted_via_api": bool(spec.get("submitted_via_api", False)),
        "depends_on": [str(item) for item in spec.get("depends_on", []) if str(item).strip()],
        "required_artifact_globs": [str(item) for item in spec.get("required_artifact_globs", []) if str(item).strip()],
        "required_report_conditions": [str(item) for item in spec.get("required_report_conditions", []) if str(item).strip()],
        "report_format": str(spec.get("report_format", "auto")),
        "report_keys": [str(item) for item in spec.get("report_keys", []) if str(item).strip()],
        "report_contract": str(spec.get("report_contract", "")),
        "success_prompt": str(spec.get("success_prompt", "")),
        "failure_prompt": str(spec.get("failure_prompt", "")),
        "task_note": str(spec.get("task_note", "")),
        "artifact_globs": [str(item) for item in artifact_globs],
        "env": normalized_env,
        "executor_name": str(spec.get("executor_name", "")).strip(),
        "executor_type": str(spec.get("executor_type", "")).strip(),
        "executor_target": str(spec.get("executor_target", "")).strip(),
        "executor_identity_file": str(spec.get("executor_identity_file", "")).strip(),
        "executor_ssh_options": [str(item) for item in spec.get("executor_ssh_options", []) if str(item).strip()],
        "executor_host_gpu_ids": parse_gpu_id_list(spec.get("executor_host_gpu_ids", [])),
        "executor_remote_gpu_ids": parse_gpu_id_list(spec.get("executor_remote_gpu_ids", [])),
        "executor_remote_workdir_prefix": normalize_posix_workdir(str(spec.get("executor_remote_workdir_prefix", "")).strip()),
        "executor_remote_home": normalize_posix_workdir(str(spec.get("executor_remote_home", "")).strip()),
        "executor_remote_codex_home": normalize_posix_workdir(str(spec.get("executor_remote_codex_home", "")).strip()),
        "executor_remote_codex_bin": str(spec.get("executor_remote_codex_bin", "codex")).strip() or "codex",
        "executor_default_env": {str(key): str(value) for key, value in executor_default_env.items()},
        "remote_workdir": normalize_posix_workdir(str(spec.get("remote_workdir", "")).strip()),
        "codex_exec_mode": codex_exec_mode,
        "resume_timeout_seconds": int(spec.get("resume_timeout_seconds", 7200)),
        "launch_grace_seconds": int(spec.get("launch_grace_seconds", 0) or 0),
        "prompt_max_chars": int(spec.get("prompt_max_chars", 12000)),
        "log_tail_lines": int(spec.get("log_tail_lines", 80)),
        "log_tail_chars": int(spec.get("log_tail_chars", 5000)),
        "artifact_max_chars": int(spec.get("artifact_max_chars", 1200)),
        "artifact_max_lines": int(spec.get("artifact_max_lines", 40)),
        "startup_failure_threshold_seconds": int(spec.get("startup_failure_threshold_seconds", DEFAULT_STARTUP_FAILURE_SECONDS)),
        "fallback_provider": str(spec.get("fallback_provider", "")).strip(),
        "tmux_session_name": tmux_session_name,
    }
    cpu_policy = resolve_cpu_thread_policy(normalized_spec)
    cpu_worker_policy = resolve_cpu_worker_policy(normalized_spec)
    normalized_spec["cpu_threads"] = int(cpu_policy.get("requested_threads", 0) or 0)
    normalized_spec["cpu_threads_min"] = int(cpu_policy.get("min_threads", 0) or 0)
    normalized_spec["cpu_threads_max"] = int(cpu_policy.get("max_threads", 0) or 0)
    normalized_spec["cpu_threads_mode"] = str(cpu_policy.get("mode", "fixed"))
    normalized_spec["cpu_thread_source"] = str(cpu_policy.get("source", "") or "default")
    normalized_spec["cpu_workers"] = int(cpu_worker_policy.get("requested_workers", 0) or 0)
    normalized_spec["cpu_workers_min"] = int(cpu_worker_policy.get("min_workers", 0) or 0)
    normalized_spec["cpu_workers_max"] = int(cpu_worker_policy.get("max_workers", 0) or 0)
    normalized_spec["cpu_worker_source"] = str(cpu_worker_policy.get("source", "") or "default")
    if execution_mode == "codex_subagent":
        normalized_spec.update(
            {
                "subagent_prompt": str(spec.get("subagent_prompt", "")),
                "subagent_model": str(spec.get("subagent_model", "gpt-5.4")),
                "subagent_exec_mode": str(spec.get("subagent_exec_mode", "dangerous")),
                "subagent_timeout_seconds": int(spec.get("subagent_timeout_seconds", 7200)),
                "subagent_continue_attempts": int(spec.get("subagent_continue_attempts", 3)),
            }
        )
    if duplicate_matches:
        normalized_spec["duplicate_submit_matches"] = duplicate_matches
        normalized_spec["duplicate_submit_warning"] = duplicate_warning
    atomic_write_json(task_spec_path(config, task_id), normalized_spec)
    state = {
        "version": VERSION,
        "task_id": task_id,
        "task_key": task_key,
        "client_task_id": normalized_spec["client_task_id"],
        "client_task_key": normalized_spec["client_task_key"],
        "execution_mode": execution_mode,
        "status": "queued" if hold else "submitted",
        "submitted_at": normalized_spec["submitted_at"],
        "tmux_session_name": tmux_session_name,
        "workdir": workdir,
        "command": command,
        "codex_session_id": codex_session_id,
        "agent_name": normalized_spec["agent_name"],
        "proposal_path": proposal_path,
        "proposal_source": proposal_source,
        "proposal_owner": proposal_owner,
        "closeout_proposal_dir": closeout_proposal_dir,
        "closeout_proposal_dir_source": closeout_proposal_dir_source,
        "project_history_file": project_history_file,
        "project_history_file_source": project_history_file_source,
        "allow_session_rebind": allow_session_rebind,
        "allow_duplicate_submit": allow_duplicate_submit,
        "priority": normalized_spec["priority"],
        "gpu_slots": gpu_slots,
        "cpu_profile": normalized_spec["cpu_profile"],
        "cpu_threads": normalized_spec["cpu_threads"],
        "cpu_threads_min": normalized_spec["cpu_threads_min"],
        "cpu_threads_max": normalized_spec["cpu_threads_max"],
        "cpu_threads_mode": normalized_spec["cpu_threads_mode"],
        "assigned_cpu_threads": 0,
        "cpu_thread_source": normalized_spec["cpu_thread_source"],
        "cpu_workers": normalized_spec["cpu_workers"],
        "cpu_workers_min": normalized_spec["cpu_workers_min"],
        "cpu_workers_max": normalized_spec["cpu_workers_max"],
        "assigned_cpu_workers": 0,
        "cpu_worker_source": normalized_spec["cpu_worker_source"],
        "assigned_gpus": normalized_spec["assigned_gpus"],
        "allowed_gpus": normalized_spec["allowed_gpus"],
        "feedback_mode": feedback_mode,
        "owner_tenant": normalized_spec["owner_tenant"],
        "owner_role": normalized_spec["owner_role"],
        "owner_label": normalized_spec["owner_label"],
        "submitted_via_api": normalized_spec["submitted_via_api"],
        "depends_on": normalized_spec["depends_on"],
        "executor_name": normalized_spec["executor_name"],
        "remote_workdir": normalized_spec["remote_workdir"],
        "paths": task_paths(config, task_id),
        "updated_at": utc_now(),
    }
    if duplicate_matches:
        state["duplicate_submit_matches"] = duplicate_matches
        state["duplicate_submit_warning"] = duplicate_warning
    write_task_state(config, task_id, state)
    append_log(task_runner_log_path(config, task_id), f"task_submitted session={tmux_session_name} workdir={workdir} hold={hold}")
    if duplicate_warning:
        append_log(task_runner_log_path(config, task_id), f"duplicate_submit_override warning={duplicate_warning}")
    return finalize_submitted_task_impl(
        config,
        normalized_spec,
        state,
        hold=hold,
        hooks=scheduler_submit_hooks(),
    )


def submit_spec(config: AppConfig, spec: dict[str, Any], *, hold: bool = False) -> dict[str, Any]:
    return run_with_scheduler_lock(config, lambda: submit_spec_unlocked(config, spec, hold=hold))


def attach_existing_pid(
    config: AppConfig,
    *,
    pid: int,
    codex_session_id: str,
    task_id: str,
    task_key: str,
    workdir: str,
    task_note: str,
    success_prompt: str,
    failure_prompt: str,
    artifact_globs: list[str],
    watch_log_path: str,
    agent_name: str,
    priority: int,
    gpu_slots: int | None,
    cpu_profile: str,
    cpu_threads: int | None,
    cpu_threads_min: int | None,
    cpu_threads_max: int | None,
    cpu_threads_mode: str,
    cpu_workers: int | None,
    cpu_workers_min: int | None,
    cpu_workers_max: int | None,
    gpu_min_free_mb: int,
    gpu_max_util_percent: int,
    replace_existing: bool,
    feedback_mode: str,
    depends_on: list[str],
    report_format: str,
    report_keys: list[str],
    report_contract: str,
    fallback_provider: str,
    codex_exec_mode: str,
    resume_timeout_seconds: int,
    prompt_max_chars: int,
    log_tail_lines: int,
    log_tail_chars: int,
    artifact_max_chars: int,
    artifact_max_lines: int,
    allow_session_rebind: bool,
    allow_duplicate_submit: bool,
    proposal_path: str,
    proposal_source: str,
    proposal_owner: bool,
    closeout_proposal_dir: str,
    closeout_proposal_dir_source: str,
    project_history_file: str,
    project_history_file_source: str,
) -> dict[str, Any]:
    snapshot = read_pid_snapshot(pid)
    if snapshot is None:
        raise ValueError(f"PID does not exist or cannot be inspected: {pid}")
    normalized_task_id = normalize_task_id(task_id or f"pid-{pid}")
    if not normalized_task_id:
        raise ValueError("Invalid task_id for attached PID.")
    normalized_task_key = normalize_task_id(task_key or normalized_task_id)
    if not normalized_task_key:
        raise ValueError("Invalid task_key for attached PID.")
    normalized_workdir = str(Path(workdir).expanduser().resolve())
    enforce_session_binding_guard(
        config,
        task_id=normalized_task_id,
        task_key=normalized_task_key,
        workdir=normalized_workdir,
        agent_name=agent_name,
        codex_session_id=codex_session_id,
        allow_session_rebind=allow_session_rebind,
    )
    prepare_task_slot(config, task_id=normalized_task_id, task_key=normalized_task_key, replace_existing=replace_existing)
    ensure_task_layout(config, normalized_task_id)
    tmux_session_name = build_tmux_session_name(normalized_task_id)
    command = snapshot["cmd"] or read_pid_cmdline(pid) or f"<attached pid {pid}>"
    duplicate_matches, duplicate_warning = enforce_duplicate_submit_guard(
        config,
        task_id=normalized_task_id,
        codex_session_id=codex_session_id,
        proposal_path=proposal_path,
        command=command,
        allow_duplicate_submit=allow_duplicate_submit,
    )
    inferred_gpu_slots = gpu_slots
    if inferred_gpu_slots is None and int(snapshot.get("pid", 0)) in get_gpu_process_table():
        inferred_gpu_slots = 1
    if inferred_gpu_slots is None and looks_like_training_command(command):
        inferred_gpu_slots = 1
    inferred_gpu_slots = max(0, int(inferred_gpu_slots or 0))
    resolved_cpu_threads, resolved_cpu_thread_source = resolve_cpu_threads(
        {
            "execution_mode": "external_pid",
            "command": command,
            "gpu_slots": inferred_gpu_slots,
            "cpu_profile": cpu_profile,
            "cpu_threads": cpu_threads or 0,
            "cpu_threads_min": cpu_threads_min or 0,
            "cpu_threads_max": cpu_threads_max or 0,
            "cpu_threads_mode": cpu_threads_mode or "",
            "cpu_workers": cpu_workers or 0,
            "cpu_workers_min": cpu_workers_min or 0,
            "cpu_workers_max": cpu_workers_max or 0,
            "env": {},
        }
    )
    cpu_policy = resolve_cpu_thread_policy(
        {
            "execution_mode": "external_pid",
            "command": command,
            "gpu_slots": inferred_gpu_slots,
            "cpu_profile": cpu_profile,
            "cpu_threads": cpu_threads or 0,
            "cpu_threads_min": cpu_threads_min or 0,
            "cpu_threads_max": cpu_threads_max or 0,
            "cpu_threads_mode": cpu_threads_mode or "",
            "cpu_workers": cpu_workers or 0,
            "cpu_workers_min": cpu_workers_min or 0,
            "cpu_workers_max": cpu_workers_max or 0,
            "env": {},
        }
    )
    resolved_cpu_workers, resolved_cpu_worker_source = resolve_cpu_workers(
        {
            "execution_mode": "external_pid",
            "command": command,
            "gpu_slots": inferred_gpu_slots,
            "cpu_profile": cpu_profile,
            "cpu_workers": cpu_workers or 0,
            "cpu_workers_min": cpu_workers_min or 0,
            "cpu_workers_max": cpu_workers_max or 0,
            "env": {},
        }
    )
    cpu_worker_policy = resolve_cpu_worker_policy(
        {
            "execution_mode": "external_pid",
            "command": command,
            "gpu_slots": inferred_gpu_slots,
            "cpu_profile": cpu_profile,
            "cpu_workers": cpu_workers or 0,
            "cpu_workers_min": cpu_workers_min or 0,
            "cpu_workers_max": cpu_workers_max or 0,
            "env": {},
        }
    )
    normalized_spec = {
        "version": VERSION,
        "task_id": normalized_task_id,
        "task_key": normalized_task_key,
        "client_task_id": normalize_task_id(str(task_id).strip()),
        "client_task_key": normalize_task_id(str(task_key or normalized_task_id).strip()),
        "submitted_at": utc_now(),
        "workdir": normalized_workdir,
        "command": command,
        "command_template": command,
        "codex_session_id": codex_session_id,
        "agent_name": agent_name,
        "proposal_path": proposal_path,
        "proposal_source": proposal_source,
        "proposal_owner": proposal_owner,
        "closeout_proposal_dir": closeout_proposal_dir,
        "closeout_proposal_dir_source": closeout_proposal_dir_source,
        "project_history_file": project_history_file,
        "project_history_file_source": project_history_file_source,
        "allow_session_rebind": bool(allow_session_rebind),
        "allow_duplicate_submit": bool(allow_duplicate_submit),
        "priority": priority,
        "gpu_slots": inferred_gpu_slots,
        "cpu_profile": normalize_cpu_profile(cpu_profile),
        "cpu_threads": resolved_cpu_threads,
        "cpu_threads_min": int(cpu_policy.get("min_threads", resolved_cpu_threads) or resolved_cpu_threads),
        "cpu_threads_max": int(cpu_policy.get("max_threads", resolved_cpu_threads) or resolved_cpu_threads),
        "cpu_threads_mode": str(cpu_policy.get("mode", "fixed")),
        "cpu_thread_source": resolved_cpu_thread_source,
        "cpu_workers": resolved_cpu_workers,
        "cpu_workers_min": int(cpu_worker_policy.get("min_workers", resolved_cpu_workers) or resolved_cpu_workers),
        "cpu_workers_max": int(cpu_worker_policy.get("max_workers", resolved_cpu_workers) or resolved_cpu_workers),
        "cpu_worker_source": resolved_cpu_worker_source,
        "gpu_min_free_mb": int(gpu_min_free_mb or 0),
        "gpu_max_util_percent": int(gpu_max_util_percent or 0),
        "assigned_gpus": [],
        "assigned_cpu_threads": resolved_cpu_threads,
        "assigned_cpu_workers": resolved_cpu_workers,
        "replace_existing": replace_existing,
        "feedback_mode": feedback_mode,
        "depends_on": [str(item) for item in depends_on if str(item).strip()],
        "report_format": report_format,
        "report_keys": [str(item) for item in report_keys if str(item).strip()],
        "report_contract": report_contract,
        "success_prompt": success_prompt,
        "failure_prompt": failure_prompt,
        "task_note": task_note,
        "artifact_globs": [str(item) for item in artifact_globs],
        "env": {},
        "codex_exec_mode": codex_exec_mode,
        "resume_timeout_seconds": resume_timeout_seconds,
        "prompt_max_chars": prompt_max_chars,
        "log_tail_lines": log_tail_lines,
        "log_tail_chars": log_tail_chars,
        "artifact_max_chars": artifact_max_chars,
        "artifact_max_lines": artifact_max_lines,
        "startup_failure_threshold_seconds": DEFAULT_STARTUP_FAILURE_SECONDS,
        "fallback_provider": fallback_provider,
        "tmux_session_name": tmux_session_name,
        "execution_mode": "external_pid",
        "watch_pid": int(pid),
        "watch_poll_seconds": 2.0,
        "watch_log_path": watch_log_path,
        "attached_snapshot": snapshot,
    }
    if duplicate_matches:
        normalized_spec["duplicate_submit_matches"] = duplicate_matches
        normalized_spec["duplicate_submit_warning"] = duplicate_warning
    atomic_write_json(task_spec_path(config, normalized_task_id), normalized_spec)
    state = {
        "version": VERSION,
        "task_id": normalized_task_id,
        "task_key": normalized_task_key,
        "client_task_id": normalized_spec["client_task_id"],
        "client_task_key": normalized_spec["client_task_key"],
        "status": "submitted",
        "submitted_at": normalized_spec["submitted_at"],
        "tmux_session_name": tmux_session_name,
        "workdir": normalized_spec["workdir"],
        "command": command,
        "codex_session_id": codex_session_id,
        "agent_name": agent_name,
        "proposal_path": proposal_path,
        "proposal_source": proposal_source,
        "proposal_owner": proposal_owner,
        "closeout_proposal_dir": closeout_proposal_dir,
        "closeout_proposal_dir_source": closeout_proposal_dir_source,
        "project_history_file": project_history_file,
        "project_history_file_source": project_history_file_source,
        "allow_session_rebind": bool(allow_session_rebind),
        "allow_duplicate_submit": bool(allow_duplicate_submit),
        "priority": priority,
        "gpu_slots": inferred_gpu_slots,
        "cpu_profile": normalized_spec["cpu_profile"],
        "cpu_threads": resolved_cpu_threads,
        "cpu_threads_min": int(cpu_policy.get("min_threads", resolved_cpu_threads) or resolved_cpu_threads),
        "cpu_threads_max": int(cpu_policy.get("max_threads", resolved_cpu_threads) or resolved_cpu_threads),
        "cpu_threads_mode": str(cpu_policy.get("mode", "fixed")),
        "assigned_cpu_threads": resolved_cpu_threads,
        "cpu_thread_source": resolved_cpu_thread_source,
        "cpu_workers": resolved_cpu_workers,
        "cpu_workers_min": int(cpu_worker_policy.get("min_workers", resolved_cpu_workers) or resolved_cpu_workers),
        "cpu_workers_max": int(cpu_worker_policy.get("max_workers", resolved_cpu_workers) or resolved_cpu_workers),
        "assigned_cpu_workers": resolved_cpu_workers,
        "cpu_worker_source": resolved_cpu_worker_source,
        "assigned_gpus": [],
        "feedback_mode": feedback_mode,
        "depends_on": [str(item) for item in depends_on if str(item).strip()],
        "watch_pid": int(pid),
        "execution_mode": "external_pid",
        "paths": task_paths(config, normalized_task_id),
        "updated_at": utc_now(),
    }
    if duplicate_matches:
        state["duplicate_submit_matches"] = duplicate_matches
        state["duplicate_submit_warning"] = duplicate_warning
    write_task_state(config, normalized_task_id, state)
    append_log(task_runner_log_path(config, normalized_task_id), f"pid_attached pid={pid} tmux={tmux_session_name}")
    if duplicate_warning:
        append_log(task_runner_log_path(config, normalized_task_id), f"duplicate_submit_override warning={duplicate_warning}")
    return start_existing_task(config, normalized_task_id)


def build_spec_from_submit_args(args: argparse.Namespace) -> dict[str, Any]:
    feedback_mode = args.feedback_mode
    return {
        "task_id": args.task_id,
        "task_key": args.task_key or args.task_id,
        "workdir": args.workdir,
        "command": args.command,
        "codex_session_id": resolve_requested_codex_session_id(args.codex_session_id, feedback_mode=feedback_mode),
        "agent_name": args.agent_name or os.environ.get("CODEX_AGENT_NAME", ""),
        "proposal": getattr(args, "proposal", None),
        "closeout_proposal_dir": getattr(args, "closeout_proposal_dir", None),
        "project_history_file": getattr(args, "project_history_file", None),
        "no_inherit_proposal": getattr(args, "no_inherit_proposal", False),
        "allow_session_rebind": args.allow_session_rebind,
        "allow_duplicate_submit": getattr(args, "allow_duplicate_submit", False),
        "priority": args.priority,
        "gpu_slots": args.gpu_slots,
        "cpu_profile": getattr(args, "cpu_profile", "auto"),
        "cpu_threads": args.cpu_threads,
        "cpu_threads_min": args.cpu_threads_min,
        "cpu_threads_max": args.cpu_threads_max,
        "cpu_threads_mode": args.cpu_threads_mode,
        "cpu_workers": getattr(args, "cpu_workers", 0),
        "cpu_workers_min": getattr(args, "cpu_workers_min", 0),
        "cpu_workers_max": getattr(args, "cpu_workers_max", 0),
        "gpu_min_free_mb": args.gpu_min_free_mb,
        "gpu_max_util_percent": args.gpu_max_util_percent,
        "replace_existing": not args.no_replace_existing,
        "feedback_mode": feedback_mode,
        "depends_on": args.depends_on or [],
        "required_artifact_globs": args.required_artifact_glob or [],
        "required_report_conditions": args.required_report or [],
        "report_format": args.report_format,
        "report_keys": args.report_key or [],
        "report_contract": args.report_contract or "",
        "success_prompt": read_optional_text(args.success_prompt, args.success_prompt_file),
        "failure_prompt": read_optional_text(args.failure_prompt, args.failure_prompt_file),
        "task_note": args.task_note or "",
        "artifact_globs": args.artifact_glob or [],
        "env": parse_key_value_pairs(args.env or []),
        "codex_exec_mode": args.codex_exec_mode,
        "resume_timeout_seconds": args.resume_timeout_seconds,
        "launch_grace_seconds": args.launch_grace_seconds,
        "prompt_max_chars": args.prompt_max_chars,
        "log_tail_lines": args.log_tail_lines,
        "log_tail_chars": args.log_tail_chars,
        "artifact_max_chars": args.artifact_max_chars,
        "artifact_max_lines": args.artifact_max_lines,
        "startup_failure_threshold_seconds": args.startup_failure_threshold_seconds,
        "fallback_provider": args.fallback_provider or "",
    }


def apply_executor_to_spec(config: AppConfig, spec: dict[str, Any], executor_name: str) -> dict[str, Any]:
    executor = resolve_executor(config, executor_name)
    remote_workdir = normalize_posix_workdir(str(spec.get("workdir", "")).strip() or str(executor.get("remote_workdir", "")).strip())
    if not remote_workdir:
        raise ValueError(f"Executor {executor_name} requires a remote workdir")
    validate_remote_workdir(remote_workdir, str(executor.get("remote_workdir_prefix", "")).strip())
    executor_host_gpu_ids = parse_gpu_id_list(executor.get("host_gpu_ids", []))
    executor_remote_gpu_ids = parse_gpu_id_list(executor.get("remote_gpu_ids", []))
    fixed_host_gpus = parse_gpu_id_list(spec.get("assigned_gpus", []))
    gpu_slots = int(spec.get("gpu_slots", 0) or 0)
    if fixed_host_gpus and gpu_slots > 0 and len(fixed_host_gpus) < gpu_slots:
        raise ValueError(
            f"Executor {executor_name} only exposes {len(fixed_host_gpus)} host GPU(s), but task requests gpu_slots={gpu_slots}"
        )
    if executor_remote_gpu_ids and executor_host_gpu_ids and len(executor_remote_gpu_ids) < len(executor_host_gpus := executor_host_gpu_ids):
        raise ValueError(
            f"Executor {executor_name} has fewer remote GPU ids than host GPU ids: "
            f"{len(executor_remote_gpu_ids)} < {len(executor_host_gpus)}"
        )
    updated = dict(spec)
    updated["execution_mode"] = "ssh_shell"
    updated["workdir"] = str(config.app_home)
    updated["remote_workdir"] = remote_workdir
    updated["executor_name"] = str(executor.get("name", executor_name))
    updated["executor_type"] = str(executor.get("type", "ssh"))
    updated["executor_target"] = str(executor.get("ssh_target", ""))
    updated["executor_identity_file"] = str(executor.get("ssh_identity_file", ""))
    updated["executor_ssh_options"] = [str(item) for item in executor.get("ssh_options", []) if str(item).strip()]
    updated["executor_host_gpu_ids"] = executor_host_gpu_ids
    updated["executor_remote_gpu_ids"] = executor_remote_gpu_ids
    updated["executor_remote_workdir_prefix"] = normalize_posix_workdir(str(executor.get("remote_workdir_prefix", "")).strip())
    updated["executor_remote_home"] = normalize_posix_workdir(str(executor.get("remote_home", "")).strip())
    updated["executor_remote_codex_home"] = normalize_posix_workdir(str(executor.get("remote_codex_home", "")).strip())
    updated["executor_remote_codex_bin"] = str(executor.get("remote_codex_bin", "codex")).strip() or "codex"
    updated["executor_default_env"] = {str(key): str(value) for key, value in dict(executor.get("default_env", {})).items()}
    if fixed_host_gpus:
        updated["assigned_gpus"] = fixed_host_gpus
    elif executor_host_gpu_ids:
        updated["allowed_gpus"] = executor_host_gpu_ids
    if not str(updated.get("agent_name", "")).strip():
        updated["agent_name"] = str(executor.get("default_agent_name", executor_name))
    if not str(updated.get("feedback_mode", "")).strip():
        updated["feedback_mode"] = str(executor.get("default_feedback_mode", "off"))
    identity_file = str(updated.get("executor_identity_file", "")).strip()
    if identity_file and not Path(identity_file).expanduser().exists():
        raise ValueError(f"Executor identity file does not exist: {identity_file}")
    return updated


def build_spec_from_submit_job_args(config: AppConfig, args: argparse.Namespace) -> dict[str, Any]:
    env = parse_key_value_pairs(args.env or [])
    feedback_mode = args.feedback_mode
    spec = {
        "task_id": args.task_id,
        "task_key": args.task_key or args.task_id,
        "execution_mode": "shell",
        "workdir": args.workdir,
        "proposal_base_workdir": args.workdir,
        "command": args.command,
        "codex_session_id": resolve_requested_codex_session_id(args.codex_session_id, feedback_mode=feedback_mode),
        "agent_name": args.agent_name or os.environ.get("CODEX_AGENT_NAME", ""),
        "proposal": getattr(args, "proposal", None),
        "closeout_proposal_dir": getattr(args, "closeout_proposal_dir", None),
        "project_history_file": getattr(args, "project_history_file", None),
        "no_inherit_proposal": getattr(args, "no_inherit_proposal", False),
        "allow_session_rebind": getattr(args, "allow_session_rebind", False),
        "allow_duplicate_submit": getattr(args, "allow_duplicate_submit", False),
        "priority": args.priority,
        "gpu_slots": args.gpu_slots,
        "cpu_profile": getattr(args, "cpu_profile", "auto"),
        "cpu_threads": args.cpu_threads,
        "cpu_threads_min": args.cpu_threads_min,
        "cpu_threads_max": args.cpu_threads_max,
        "cpu_threads_mode": args.cpu_threads_mode,
        "cpu_workers": getattr(args, "cpu_workers", 0),
        "cpu_workers_min": getattr(args, "cpu_workers_min", 0),
        "cpu_workers_max": getattr(args, "cpu_workers_max", 0),
        "gpu_min_free_mb": args.gpu_min_free_mb,
        "gpu_max_util_percent": args.gpu_max_util_percent,
        "assigned_gpus": parse_gpu_id_list(args.assigned_gpus or []),
        "replace_existing": not args.no_replace_existing,
        "feedback_mode": feedback_mode,
        "depends_on": args.depends_on or [],
        "required_artifact_globs": args.required_artifact_glob or [],
        "required_report_conditions": args.required_report or [],
        "report_format": args.report_format,
        "report_keys": args.report_key or [],
        "report_contract": args.report_contract or "",
        "success_prompt": read_optional_text(args.success_prompt, args.success_prompt_file),
        "failure_prompt": read_optional_text(args.failure_prompt, args.failure_prompt_file),
        "task_note": args.task_note or "",
        "artifact_globs": args.artifact_glob or [],
        "env": env,
        "codex_exec_mode": args.codex_exec_mode,
        "resume_timeout_seconds": args.resume_timeout_seconds,
        "launch_grace_seconds": args.launch_grace_seconds,
        "prompt_max_chars": args.prompt_max_chars,
        "log_tail_lines": args.log_tail_lines,
        "log_tail_chars": args.log_tail_chars,
        "artifact_max_chars": args.artifact_max_chars,
        "artifact_max_lines": args.artifact_max_lines,
        "startup_failure_threshold_seconds": args.startup_failure_threshold_seconds,
        "fallback_provider": args.fallback_provider or "",
    }
    executor_name = str(args.executor or "").strip()
    if executor_name:
        spec = apply_executor_to_spec(config, spec, executor_name)
    return spec


def build_spec_from_bind_before_launch_args(config: AppConfig, args: argparse.Namespace) -> dict[str, Any]:
    shim = argparse.Namespace(**vars(args))
    shim.executor = ""
    shim.gpu_slots = 0
    shim.assigned_gpus = []
    shim.gpu_min_free_mb = 0
    shim.gpu_max_util_percent = 0
    spec = build_spec_from_submit_job_args(config, shim)
    env = dict(spec.get("env", {}))
    env.setdefault("CODEX_TASKBOARD_BIND_BEFORE_LAUNCH", "1")
    spec["env"] = env
    task_note = str(spec.get("task_note", "")).strip()
    spec["task_note"] = f"bind_before_launch; {task_note}" if task_note else "bind_before_launch"
    return spec


def build_spec_from_submit_job_payload(
    config: AppConfig,
    payload: dict[str, Any],
    *,
    forced_executor: str = "",
    default_feedback_mode: str = "off",
    default_agent_name: str = "",
) -> tuple[dict[str, Any], bool]:
    return build_spec_from_submit_job_payload_impl(
        config,
        payload,
        forced_executor=forced_executor,
        default_feedback_mode=default_feedback_mode,
        default_agent_name=default_agent_name,
        hooks=api_submit_hooks(),
    )


def task_result_hooks() -> TaskResultHooks:
    return TaskResultHooks(
        normalize_task_id=normalize_task_id,
        load_task_state=load_task_state,
        get_gpu_summary_table=get_gpu_summary_table,
        detect_gpu_count=detect_gpu_count,
        iter_task_states=iter_task_states,
        active_task_statuses=ACTIVE_TASK_STATUSES,
        task_requested_cpu_budget=task_requested_cpu_budget,
        merged_spec_with_state=merged_spec_with_state,
        enrich_task_state=enrich_task_state,
        detect_default_cpu_thread_limit=detect_default_cpu_thread_limit,
        load_task_spec=load_task_spec,
        is_terminal_status=is_terminal_status,
        parse_gpu_id_list=parse_gpu_id_list,
        resolved_cpu_profile=resolved_cpu_profile,
        task_paths=task_paths,
    )


def build_task_result_payload(config: AppConfig, task_id: str) -> dict[str, Any]:
    return build_task_result_payload_impl(config, task_id, hooks=task_result_hooks())


def scheduler_dispatch_hooks() -> SchedulerDispatchHooks:
    return SchedulerDispatchHooks(
        iter_task_states=iter_task_states,
        count_live_running_tasks=count_live_running_tasks,
        detect_default_cpu_thread_limit=detect_default_cpu_thread_limit,
        task_requested_cpu_budget=task_requested_cpu_budget,
        merged_spec_with_state=merged_spec_with_state,
        detect_gpu_count=detect_gpu_count,
        parse_gpu_id_list=parse_gpu_id_list,
        get_gpu_summary_table=get_gpu_summary_table,
        active_task_statuses=ACTIVE_TASK_STATUSES,
        runnable_statuses=RUNNABLE_STATUSES,
        timestamp_sort_value=timestamp_sort_value,
        evaluate_task_readiness=evaluate_task_readiness,
        select_cpu_resources_for_start=select_cpu_resources_for_start,
        start_existing_task=start_existing_task,
        selected_gpu_snapshot=selected_gpu_snapshot,
    )


def scheduler_submit_hooks() -> SchedulerSubmitHooks:
    return SchedulerSubmitHooks(
        iter_task_states=iter_task_states,
        active_task_statuses=ACTIVE_TASK_STATUSES,
        task_requested_cpu_budget=task_requested_cpu_budget,
        merged_spec_with_state=merged_spec_with_state,
        parse_gpu_id_list=parse_gpu_id_list,
        get_gpu_summary_table=get_gpu_summary_table,
        detect_gpu_count=detect_gpu_count,
        detect_default_cpu_thread_limit=detect_default_cpu_thread_limit,
        evaluate_task_readiness=evaluate_task_readiness,
        enrich_task_state=enrich_task_state,
        select_cpu_resources_for_start=select_cpu_resources_for_start,
        start_existing_task=start_existing_task,
        selected_gpu_snapshot=selected_gpu_snapshot,
        merge_task_state=merge_task_state,
    )


def reserve_cpu_threads_for_later_tasks(
    config: AppConfig,
    queued_items: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    start_index: int,
    gpu_rows: list[dict[str, Any]] | None,
    total_gpu_slots: int,
    reserved_gpu_ids: set[int],
    active_cpu_threads: int,
    reserved_cpu_threads: int,
    cpu_thread_limit: int,
) -> int:
    return reserve_cpu_threads_for_later_tasks_impl(
        config,
        queued_items,
        hooks=scheduler_dispatch_hooks(),
        start_index=start_index,
        gpu_rows=gpu_rows,
        total_gpu_slots=total_gpu_slots,
        reserved_gpu_ids=reserved_gpu_ids,
        active_cpu_threads=active_cpu_threads,
        reserved_cpu_threads=reserved_cpu_threads,
        cpu_thread_limit=cpu_thread_limit,
    )


def dispatch_queued_tasks_unlocked(
    config: AppConfig,
    *,
    mode: str,
    max_running: int,
    limit: int,
    gpu_count_override: int,
    cpu_thread_limit: int,
) -> dict[str, Any]:
    return dispatch_queued_tasks_unlocked_impl(
        config,
        hooks=scheduler_dispatch_hooks(),
        mode=mode,
        max_running=max_running,
        limit=limit,
        gpu_count_override=gpu_count_override,
        cpu_thread_limit=cpu_thread_limit,
    )


def dispatch_queued_tasks(
    config: AppConfig,
    *,
    mode: str,
    max_running: int,
    limit: int,
    gpu_count_override: int,
    cpu_thread_limit: int,
) -> dict[str, Any]:
    return run_with_scheduler_lock(
        config,
        lambda: dispatch_queued_tasks_unlocked(
            config,
            mode=mode,
            max_running=max_running,
            limit=limit,
            gpu_count_override=gpu_count_override,
            cpu_thread_limit=cpu_thread_limit,
        ),
    )


def shutil_which(name: str) -> str:
    from shutil import which

    return which(name) or ""


def command_doctor(args: argparse.Namespace) -> int:
    config = build_config(args)
    ensure_dir(config.app_home)
    include_legacy = legacy_reads_enabled()
    checks = {
        "codex_bin": shutil_which(config.codex_bin),
        "tmux_bin": shutil_which(config.tmux_bin),
        "tmux_socket_path": str(config.tmux_socket_path),
        "codex_home": str(config.codex_home) if config.codex_home.exists() else "",
        "thread_db": str(config.threads_db_path) if config.threads_db_path.exists() else "",
        "sync_script": str(config.sync_script_path) if config.sync_script_path.exists() else "",
        "app_home": str(config.app_home),
        "tasks_root": str(config.tasks_root),
        "legacy_task_roots": [str(path) for path in config.legacy_task_roots],
        "legacy_reads_enabled": include_legacy,
        "legacy_runtime_roots": [str(path) for path in all_task_roots(config, include_legacy=include_legacy)[1:]],
        "legacy_task_root_patterns": list(LEGACY_TASK_ROOT_GLOBS),
        "legacy_task_root_env": str(os.environ.get(LEGACY_TASK_ROOT_ENV, "") or ""),
        "legacy_reads_env": str(os.environ.get(LEGACY_READS_ENV, "") or ""),
        "executor_registry_path": str(executor_registry_path(config)),
        "executor_registry_entries": sorted(load_executor_registry(config).keys()),
        "api_token_registry_path": str(api_token_registry_path(config)),
        "api_token_count": len(load_api_token_registry(config)),
    }
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["codex_bin"] and checks["tmux_bin"] and checks["thread_db"] else 1


def command_list_threads(args: argparse.Namespace) -> int:
    config = build_config(args)
    if not config.threads_db_path.exists():
        print(f"Missing thread database: {config.threads_db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(config.threads_db_path)
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT id, model_provider, source, archived, updated_at, title, cwd, first_user_message
        FROM threads
        WHERE 1 = 1
    """
    params: list[Any] = []
    if not args.include_archived:
        sql += " AND archived = 0"
    if args.provider:
        sql += " AND model_provider = ?"
        params.append(args.provider)
    if args.source:
        sql += " AND source = ?"
        params.append(args.source)
    if args.search:
        sql += " AND (title LIKE ? OR first_user_message LIKE ?)"
        pattern = f"%{args.search}%"
        params.extend([pattern, pattern])
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(args.limit)
    try:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        updated = format_unix_timestamp(int(row["updated_at"]))
        title = (row["title"] or "").replace("\n", " ").strip()
        cwd = (row["cwd"] or "").strip()
        print(f"{row['id']} | provider={row['model_provider']} | source={row['source']} | archived={row['archived']} | updated_at={updated}")
        print(f"  title: {title}")
        if cwd:
            print(f"  cwd: {cwd}")
    return 0


def command_current_thread(args: argparse.Namespace) -> int:
    config = build_config(args)
    payload = current_thread_info(config)
    if payload is None:
        print(
            "Missing current Codex session context: neither CODEX_SESSION_ID nor CODEX_THREAD_ID is set.",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"{payload['current_codex_session_id']} | resolved_from={payload.get('resolved_from') or payload['resolved_from_env']} | thread_found={payload['thread_found']}")
    if payload.get("updated_at_iso"):
        print(f"  updated_at: {payload['updated_at_iso']}")
    if payload.get("cwd_probe"):
        print(f"  cwd_probe: {payload['cwd_probe']}")
    title = str(payload.get("title", "")).replace("\n", " ").strip()
    if title:
        print(f"  title: {title}")
    cwd = str(payload.get("cwd", "")).strip()
    if cwd:
        print(f"  cwd: {cwd}")
    source = str(payload.get("source", "")).strip()
    if source:
        print(f"  source: {source}")
    return 0


def command_automation_mode(args: argparse.Namespace) -> int:
    config = build_config(args)
    action = str(getattr(args, "action", "status") or "status").strip().lower()
    target_session_id, resolved_from = resolve_continuous_research_target_session_id(
        config,
        raw_session_id=getattr(args, "codex_session_id", ""),
    )
    if action in {"managed", "continuous", "toggle", "bind", "clear-session"} and not target_session_id:
        raise ValueError("Missing target codex session id for session-scoped automation-mode action.")
    try:
        if action == "status":
            payload = build_continuous_mode_status_payload(
                config,
                target_session_id=target_session_id,
                resolved_from=resolved_from,
            )
        elif action == "managed":
            payload = set_automation_mode(
                config,
                mode="managed",
                codex_session_id=target_session_id,
                updated_by="cli",
                source="automation-mode:managed",
            )
        elif action == "continuous":
            payload = set_automation_mode(
                config,
                mode="continuous",
                codex_session_id=target_session_id,
                updated_by="cli",
                source="automation-mode:continuous",
            )
        elif action == "toggle":
            payload = toggle_automation_mode(
                config,
                codex_session_id=target_session_id,
                updated_by="cli",
                source="automation-mode:toggle",
            )
        elif action == "bind":
            payload = bind_continuous_research_mode_session(
                config,
                codex_session_id=target_session_id,
                updated_by="cli",
                source="automation-mode:bind",
            )
        elif action == "clear-session":
            payload = clear_continuous_research_mode_session(
                config,
                codex_session_id=target_session_id,
                updated_by="cli",
                source="automation-mode:clear-session",
            )
        elif action == "clear-all":
            payload = clear_all_continuous_research_mode(
                config,
                updated_by="cli",
                source="automation-mode:clear-all",
            )
        else:
            raise ValueError(f"Unsupported automation mode action: {action}")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    backlog = reflow_backlog_summary(config, codex_session_id=target_session_id)
    payload = {
        **payload,
        "action": action,
        "automation_mode": automation_mode_label(config, codex_session_id=target_session_id or str(payload.get("target_codex_session_id", "")).strip()),
        "target_codex_session_id": target_session_id or str(payload.get("target_codex_session_id", "")).strip(),
        "resolved_from": resolved_from or str(payload.get("resolved_from", "")).strip(),
        "reflow_backlog": backlog,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"automation_mode={payload.get('automation_mode', 'managed')} | action={action}")
    if payload.get("target_codex_session_id"):
        print(f"  target_codex_session_id: {payload['target_codex_session_id']}")
    if payload.get("resolved_from"):
        print(f"  resolved_from: {payload['resolved_from']}")
    if payload.get("default_codex_session_id"):
        print(f"  default_codex_session_id: {payload['default_codex_session_id']}")
    summary = payload.get("reflow_backlog", {}) if isinstance(payload.get("reflow_backlog", {}), dict) else {}
    print(
        "  reflow_backlog: followups={followups} events={events} oldest={oldest} latest={latest}".format(
            followups=int(summary.get("followup_count", 0) or 0),
            events=int(summary.get("queue_depth", 0) or 0),
            oldest=str(summary.get("oldest_event_at", "") or "-"),
            latest=str(summary.get("latest_event_at", "") or "-"),
        )
    )
    return 0


command_continuous_mode = command_automation_mode


def command_backlog(args: argparse.Namespace) -> int:
    config = build_config(args)
    action = str(getattr(args, "action", "status") or "status").strip().lower()
    target_session_id, resolved_from = resolve_continuous_research_target_session_id(
        config,
        raw_session_id=getattr(args, "codex_session_id", ""),
    )
    if action in {"status", "show", "clear"} and not target_session_id:
        raise ValueError("Missing target codex session id for backlog action.")
    if action in {"status", "show"}:
        summary = reflow_backlog_summary(config, codex_session_id=target_session_id)
        payload = {
            "action": action,
            "target_codex_session_id": target_session_id,
            "resolved_from": resolved_from,
            **summary,
        }
    elif action == "clear":
        cleared = clear_reflow_backlog(config, codex_session_id=target_session_id)
        payload = {
            "action": action,
            "target_codex_session_id": target_session_id,
            "resolved_from": resolved_from,
            **cleared,
        }
    elif action == "clear-all":
        cleared = clear_reflow_backlog(config, clear_all=True)
        payload = {
            "action": action,
            "target_codex_session_id": "",
            "resolved_from": resolved_from,
            **cleared,
        }
    else:
        raise ValueError(f"Unsupported backlog action: {action}")
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"backlog action={action}")
    if payload.get("target_codex_session_id"):
        print(f"  target_codex_session_id: {payload['target_codex_session_id']}")
    if payload.get("resolved_from"):
        print(f"  resolved_from: {payload['resolved_from']}")
    if action in {"status", "show"}:
        print(
            "  followups={followups} events={events} oldest={oldest} latest={latest}".format(
                followups=int(payload.get("followup_count", 0) or 0),
                events=int(payload.get("queue_depth", 0) or 0),
                oldest=str(payload.get("oldest_event_at", "") or "-"),
                latest=str(payload.get("latest_event_at", "") or "-"),
            )
        )
        if action == "show":
            for entry in payload.get("entries", []):
                print(
                    "    - followup={followup_key} session={session} events={events} oldest={oldest} latest={latest}".format(
                        followup_key=str(entry.get("followup_key", "") or "-"),
                        session=str(entry.get("codex_session_id", "") or "-"),
                        events=int(entry.get("queue_depth", 0) or 0),
                        oldest=str(entry.get("oldest_event_at", "") or "-"),
                        latest=str(entry.get("latest_event_at", "") or "-"),
                    )
                )
    else:
        print(
            "  cleared_followups={followups} cleared_events={events}".format(
                followups=int(payload.get("cleared_followups", 0) or 0),
                events=int(payload.get("cleared_events", 0) or 0),
            )
        )
    return 0


def command_submit(args: argparse.Namespace) -> int:
    config = build_config(args)
    spec = apply_local_submission_context(config, build_spec_from_submit_args(args))
    state = submit_spec(config, spec, hold=args.hold)
    duplicate_warning = str(state.get("duplicate_submit_warning", "")).strip()
    if duplicate_warning:
        print(f"WARNING: {duplicate_warning}", file=sys.stderr)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def command_submit_job(args: argparse.Namespace) -> int:
    config = build_config(args)
    spec = apply_local_submission_context(config, build_spec_from_submit_job_args(config, args))
    state = submit_spec(config, spec, hold=args.hold)
    result = build_task_result_payload(config, str(state.get("task_id", spec.get("task_id", ""))))
    duplicate_warning = str(result.get("duplicate_submit_warning", "")).strip()
    if duplicate_warning:
        print(f"WARNING: {duplicate_warning}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_bind_before_launch(args: argparse.Namespace) -> int:
    config = build_config(args)
    spec = apply_local_submission_context(config, build_spec_from_bind_before_launch_args(config, args))
    state = submit_spec(config, spec, hold=args.hold)
    result = build_task_result_payload(config, str(state.get("task_id", spec.get("task_id", ""))))
    duplicate_warning = str(result.get("duplicate_submit_warning", "")).strip()
    if duplicate_warning:
        print(f"WARNING: {duplicate_warning}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_submit_subagent(args: argparse.Namespace) -> int:
    config = build_config(args)
    subagent_prompt = read_optional_text(args.prompt, args.prompt_file)
    feedback_mode = args.feedback_mode
    spec = {
        "execution_mode": "codex_subagent",
        "task_id": args.task_id,
        "task_key": args.task_key or args.task_id,
        "workdir": args.workdir,
        "command": "",
        "codex_session_id": resolve_requested_codex_session_id(args.codex_session_id, feedback_mode=feedback_mode),
        "agent_name": args.agent_name or os.environ.get("CODEX_AGENT_NAME", ""),
        "proposal": getattr(args, "proposal", None),
        "closeout_proposal_dir": getattr(args, "closeout_proposal_dir", None),
        "project_history_file": getattr(args, "project_history_file", None),
        "no_inherit_proposal": getattr(args, "no_inherit_proposal", False),
        "allow_session_rebind": args.allow_session_rebind,
        "allow_duplicate_submit": getattr(args, "allow_duplicate_submit", False),
        "priority": args.priority,
        "gpu_slots": args.gpu_slots,
        "cpu_profile": getattr(args, "cpu_profile", "auto"),
        "cpu_threads": args.cpu_threads,
        "cpu_threads_min": args.cpu_threads_min,
        "cpu_threads_max": args.cpu_threads_max,
        "cpu_threads_mode": args.cpu_threads_mode,
        "cpu_workers": getattr(args, "cpu_workers", 0),
        "cpu_workers_min": getattr(args, "cpu_workers_min", 0),
        "cpu_workers_max": getattr(args, "cpu_workers_max", 0),
        "gpu_min_free_mb": args.gpu_min_free_mb,
        "gpu_max_util_percent": args.gpu_max_util_percent,
        "replace_existing": not args.no_replace_existing,
        "feedback_mode": feedback_mode,
        "depends_on": args.depends_on or [],
        "required_artifact_globs": args.required_artifact_glob or [],
        "required_report_conditions": args.required_report or [],
        "report_format": args.report_format,
        "report_keys": args.report_key or [],
        "report_contract": args.report_contract or "",
        "success_prompt": read_optional_text(args.success_prompt, args.success_prompt_file),
        "failure_prompt": read_optional_text(args.failure_prompt, args.failure_prompt_file),
        "task_note": args.task_note or "",
        "artifact_globs": args.artifact_glob or [],
        "env": {},
        "codex_exec_mode": args.codex_exec_mode,
        "resume_timeout_seconds": args.resume_timeout_seconds,
        "launch_grace_seconds": args.launch_grace_seconds,
        "prompt_max_chars": args.prompt_max_chars,
        "log_tail_lines": args.log_tail_lines,
        "log_tail_chars": args.log_tail_chars,
        "artifact_max_chars": args.artifact_max_chars,
        "artifact_max_lines": args.artifact_max_lines,
        "startup_failure_threshold_seconds": args.startup_failure_threshold_seconds,
        "fallback_provider": args.fallback_provider or "",
        "subagent_prompt": subagent_prompt,
        "subagent_model": args.model,
        "subagent_exec_mode": args.subagent_exec_mode,
        "subagent_timeout_seconds": args.subagent_timeout_seconds,
        "subagent_continue_attempts": args.subagent_continue_attempts,
    }
    spec = apply_local_submission_context(config, spec)
    state = submit_spec(config, spec, hold=args.hold)
    duplicate_warning = str(state.get("duplicate_submit_warning", "")).strip()
    if duplicate_warning:
        print(f"WARNING: {duplicate_warning}", file=sys.stderr)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def command_attach_pid(args: argparse.Namespace) -> int:
    config = build_config(args)
    success_prompt = read_optional_text(args.success_prompt, args.success_prompt_file)
    failure_prompt = read_optional_text(args.failure_prompt, args.failure_prompt_file)
    codex_session_id = resolve_requested_codex_session_id(
        args.codex_session_id,
        feedback_mode=args.feedback_mode,
        config=config,
        workdir=args.workdir,
        agent_name=args.agent_name or os.environ.get("CODEX_AGENT_NAME", ""),
    )
    agent_name = args.agent_name or os.environ.get("CODEX_AGENT_NAME", "")
    proposal_path, proposal_source = resolve_requested_proposal_path(
        config,
        raw_proposal=getattr(args, "proposal", None) if getattr(args, "proposal", None) is not None else MISSING,
        no_inherit_proposal=getattr(args, "no_inherit_proposal", False),
        codex_session_id=codex_session_id,
        workdir=args.workdir,
        remote_workdir="",
        agent_name=agent_name,
        environ=os.environ,
        allow_history=True,
    )
    closeout_proposal_dir, closeout_proposal_dir_source = resolve_requested_closeout_proposal_dir(
        config,
        raw_closeout_proposal_dir=(
            getattr(args, "closeout_proposal_dir", None) if getattr(args, "closeout_proposal_dir", None) is not None else MISSING
        ),
        codex_session_id=codex_session_id,
        workdir=args.workdir,
        remote_workdir="",
        agent_name=agent_name,
        environ=os.environ,
        allow_history=True,
    )
    project_history_file, project_history_file_source = resolve_requested_project_history_file(
        config,
        raw_project_history_file=(
            getattr(args, "project_history_file", None) if getattr(args, "project_history_file", None) is not None else MISSING
        ),
        codex_session_id=codex_session_id,
        workdir=args.workdir,
        remote_workdir="",
        agent_name=agent_name,
        environ=os.environ,
        allow_history=True,
    )
    state = attach_existing_pid(
        config,
        pid=args.pid,
        codex_session_id=codex_session_id,
        task_id=args.task_id or f"pid-{args.pid}",
        task_key=args.task_key or args.task_id or f"pid-{args.pid}",
        workdir=args.workdir,
        task_note=args.task_note or "",
        success_prompt=success_prompt,
        failure_prompt=failure_prompt,
        artifact_globs=args.artifact_glob or [],
        watch_log_path=args.watch_log_path or "",
        agent_name=agent_name,
        priority=args.priority,
        gpu_slots=args.gpu_slots,
        cpu_profile=getattr(args, "cpu_profile", "auto"),
        cpu_threads=args.cpu_threads,
        cpu_threads_min=args.cpu_threads_min,
        cpu_threads_max=args.cpu_threads_max,
        cpu_threads_mode=args.cpu_threads_mode,
        cpu_workers=getattr(args, "cpu_workers", 0),
        cpu_workers_min=getattr(args, "cpu_workers_min", 0),
        cpu_workers_max=getattr(args, "cpu_workers_max", 0),
        gpu_min_free_mb=args.gpu_min_free_mb,
        gpu_max_util_percent=args.gpu_max_util_percent,
        replace_existing=not args.no_replace_existing,
        feedback_mode=args.feedback_mode,
        depends_on=args.depends_on or [],
        report_format=args.report_format,
        report_keys=args.report_key or [],
        report_contract=args.report_contract or "",
        fallback_provider=args.fallback_provider or "",
        codex_exec_mode=args.codex_exec_mode,
        resume_timeout_seconds=args.resume_timeout_seconds,
        prompt_max_chars=args.prompt_max_chars,
        log_tail_lines=args.log_tail_lines,
        log_tail_chars=args.log_tail_chars,
        artifact_max_chars=args.artifact_max_chars,
        artifact_max_lines=args.artifact_max_lines,
        allow_session_rebind=args.allow_session_rebind,
        allow_duplicate_submit=getattr(args, "allow_duplicate_submit", False),
        proposal_path=proposal_path,
        proposal_source=proposal_source,
        proposal_owner=bool(proposal_path)
        and infer_proposal_owner(
            {
                "task_id": args.task_id or f"pid-{args.pid}",
                "task_key": args.task_key or args.task_id or f"pid-{args.pid}",
                "agent_name": agent_name,
                "task_note": args.task_note or "",
            }
        ),
        closeout_proposal_dir=closeout_proposal_dir,
        closeout_proposal_dir_source=closeout_proposal_dir_source,
        project_history_file=project_history_file,
        project_history_file_source=project_history_file_source,
    )
    duplicate_warning = str(state.get("duplicate_submit_warning", "")).strip()
    if duplicate_warning:
        print(f"WARNING: {duplicate_warning}", file=sys.stderr)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def command_submit_file(args: argparse.Namespace) -> int:
    config = build_config(args)
    payload = read_json(Path(args.spec_file).expanduser(), None)
    if payload is None:
        print(f"Could not read spec file: {args.spec_file}", file=sys.stderr)
        return 1
    specs = payload if isinstance(payload, list) else [payload]
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for item in specs:
        try:
            if not isinstance(item, dict):
                raise ValueError("Each spec entry must be an object.")
            item_payload = dict(item)
            if getattr(args, "proposal", None) is not None and extract_raw_proposal_value(item_payload) is MISSING:
                item_payload["proposal"] = getattr(args, "proposal", None)
            if getattr(args, "closeout_proposal_dir", None) is not None and extract_raw_closeout_proposal_dir(item_payload) is MISSING:
                item_payload["closeout_proposal_dir"] = getattr(args, "closeout_proposal_dir", None)
            if getattr(args, "project_history_file", None) is not None and extract_raw_project_history_file(item_payload) is MISSING:
                item_payload["project_history_file"] = getattr(args, "project_history_file", None)
            if getattr(args, "no_inherit_proposal", False) and "no_inherit_proposal" not in item_payload and extract_raw_proposal_value(item_payload) is MISSING:
                item_payload["no_inherit_proposal"] = True
            item_hold = args.hold or bool(item_payload.get("hold", False))
            resolved_item = apply_local_submission_context(config, item_payload)
            results.append(submit_spec(config, resolved_item, hold=item_hold))
        except Exception as exc:
            failures.append(str(exc))
    print(json.dumps({"results": results, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def command_status_result(args: argparse.Namespace) -> int:
    config = build_config(args)
    task_id = normalize_task_id(args.task_id)
    try:
        payload = build_task_result_payload(config, task_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def api_view_hooks() -> ApiViewHooks:
    return ApiViewHooks(
        active_task_statuses=ACTIVE_TASK_STATUSES,
        runnable_statuses=RUNNABLE_STATUSES,
        normalize_task_id=normalize_task_id,
        resolve_api_visible_task_id=resolve_api_visible_task_id,
        load_task_state=load_task_state,
        load_task_spec=load_task_spec,
        build_task_result_payload=build_task_result_payload,
        task_visible_to_api_token=task_visible_to_api_token,
        task_visible_in_api_queue=task_visible_in_api_queue,
        task_index_rows=task_index_rows,
        is_hidden_status=is_hidden_status,
        latest_task_states_by_key=latest_task_states_by_key,
        dependency_resolution=dependency_resolution,
        task_requested_cpu_budget=task_requested_cpu_budget,
        merged_spec_with_state=merged_spec_with_state,
        get_gpu_summary_table=get_gpu_summary_table,
        detect_gpu_count=detect_gpu_count,
        detect_default_cpu_thread_limit=detect_default_cpu_thread_limit,
        filter_dashboard_tasks=filter_dashboard_tasks,
        sort_dashboard_tasks=sort_dashboard_tasks,
        enrich_task_state=enrich_task_state,
        dashboard_issue_text=dashboard_issue_text,
        parse_gpu_id_list=parse_gpu_id_list,
        is_terminal_status=is_terminal_status,
        build_api_visibility_scope=lambda token_record, view: build_api_visibility_scope(token_record, view=view),
        is_public_queue_view=lambda token_record, view: is_public_queue_view(token_record, view=view),
        build_spec_from_submit_job_payload=build_spec_from_submit_job_payload,
        apply_api_token_submit_policy=lambda config, token_record, spec, payload: apply_api_token_submit_policy(
            config,
            token_record=token_record,
            spec=spec,
            payload=payload,
        ),
        submit_spec=lambda config, spec, hold: submit_spec(config, spec, hold=hold),
    )


def build_task_result_payload_for_api(config: AppConfig, task_id: str, token_record: dict[str, Any]) -> dict[str, Any]:
    return build_task_result_payload_for_api_impl(
        config,
        task_id,
        token_record,
        hooks=api_view_hooks(),
    )


def build_task_list_payload_for_api(
    config: AppConfig,
    token_record: dict[str, Any],
    *,
    status_filter: str = "all",
    sort_mode: str = "queue",
    limit: int = 30,
    view: str = "tasks",
) -> dict[str, Any]:
    return build_task_list_payload_for_api_impl(
        config,
        token_record,
        status_filter=status_filter,
        sort_mode=sort_mode,
        limit=limit,
        view=view,
        hooks=api_view_hooks(),
    )


def wait_for_result_payload(
    config: AppConfig,
    task_id: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
    token_record: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return wait_for_result_payload_impl(
        config,
        task_id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        token_record=token_record,
        hooks=api_view_hooks(),
    )


def command_wait_result(args: argparse.Namespace) -> int:
    config = build_config(args)
    payload = wait_for_result_payload(
        config,
        args.task_id,
        timeout_seconds=float(args.timeout_seconds),
        poll_seconds=float(args.poll_seconds),
    )
    if not payload:
        print(f"Task not found: {normalize_task_id(args.task_id)}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not bool(payload.get("result_ready", False)):
        return 1
    if args.expect_status and str(payload.get("status", "")) != args.expect_status:
        return 2
    return 0


def submit_job_for_api(config: AppConfig, payload: dict[str, Any], token_record: dict[str, Any]) -> dict[str, Any]:
    return submit_job_for_api_impl(
        config,
        payload,
        token_record,
        hooks=api_view_hooks(),
    )


def dispatcher_service_hooks() -> DispatcherServiceHooks:
    return DispatcherServiceHooks(
        dispatch_queued_tasks=dispatch_queued_tasks,
        process_followups=process_followups,
    )


def service_manager_hooks() -> ServiceManagerHooks:
    return ServiceManagerHooks(
        ensure_dir=ensure_dir,
        append_log=append_log,
    )


def current_system_user_name() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return str(os.environ.get("USER", "")).strip() or "root"


def current_system_group_name() -> str:
    try:
        return grp.getgrgid(os.getgid()).gr_name
    except Exception:
        return current_system_user_name()


def managed_service_specs(
    config: AppConfig,
    *,
    api_bind: str,
    api_port: int,
    dispatcher_mode: str,
    dispatcher_gpu_count: int,
    dispatcher_cpu_thread_limit: int,
    dispatcher_poll_seconds: float,
) -> dict[str, TaskboardServiceSpec]:
    return {
        "api": TaskboardServiceSpec(
            name="api",
            unit_name="codex-taskboard-api.service",
            description="codex-taskboard authenticated API server",
            exec_args=("api", "--bind", str(api_bind), "--port", str(int(api_port))),
            legacy_pid_files=(config.app_home / "serve-api.pid",),
            process_match_fragments=("codex-taskboard serve-api", "codex-taskboard service run api"),
            service_log_path=config.app_home / "service-api.log",
            after=("network-online.target",),
            wants=("network-online.target",),
            bind=str(api_bind),
            port=int(api_port),
        ),
        "dispatcher": TaskboardServiceSpec(
            name="dispatcher",
            unit_name="codex-taskboard-dispatcher.service",
            description="codex-taskboard dispatcher loop",
            exec_args=(
                "dispatcher",
                "--mode",
                str(dispatcher_mode),
                "--gpu-count",
                str(int(dispatcher_gpu_count)),
                "--cpu-thread-limit",
                str(int(dispatcher_cpu_thread_limit)),
                "--poll-seconds",
                format(float(dispatcher_poll_seconds), "g"),
            ),
            legacy_pid_files=(config.app_home / "serve.pid",),
            process_match_fragments=("codex-taskboard serve --mode", "codex-taskboard service run dispatcher"),
            service_log_path=config.app_home / "service-dispatcher.log",
            after=("network.target",),
            kill_mode="process",
        ),
    }


def serve_api_with_config(config: AppConfig, *, bind: str, port: int) -> int:
    if not load_api_token_registry(config):
        print(f"API token registry is empty: {api_token_registry_path(config)}", file=sys.stderr)
        return 1
    ensure_dir(config.app_home)
    api_log_path = config.app_home / "api-server.log"
    hooks = ApiServerHooks(
        default_poll_seconds=DEFAULT_API_POLL_SECONDS,
        resolve_token=lambda token: resolve_api_token(config, token),
        build_task_list_payload=lambda token_record, status_filter, sort_mode, limit, view: build_task_list_payload_for_api(
            config,
            token_record,
            status_filter=status_filter,
            sort_mode=sort_mode,
            limit=limit,
            view=view,
        ),
        build_task_result_payload=lambda task_id, token_record: build_task_result_payload_for_api(config, task_id, token_record),
        wait_for_result_payload=lambda task_id, timeout_seconds, poll_seconds, token_record: wait_for_result_payload(
            config,
            task_id,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            token_record=token_record,
        ),
        submit_job=lambda payload, token_record: submit_job_for_api(config, payload, token_record),
        append_log=lambda message: append_log(api_log_path, message),
        normalize_task_id=normalize_task_id,
        api_token_tenant=api_token_tenant,
    )
    return serve_api(bind=bind, port=port, hooks=hooks)



def serve_dispatcher_with_config(
    config: AppConfig,
    *,
    mode: str,
    max_running: int,
    dispatch_limit: int,
    gpu_count: int,
    cpu_thread_limit: int,
    poll_seconds: float,
    verbose: bool,
) -> int:
    return serve_dispatcher_loop(
        config,
        mode=mode,
        max_running=max_running,
        dispatch_limit=dispatch_limit,
        gpu_count_override=gpu_count,
        cpu_thread_limit=cpu_thread_limit,
        poll_seconds=poll_seconds,
        verbose=verbose,
        hooks=dispatcher_service_hooks(),
    )


def command_serve_api(args: argparse.Namespace) -> int:
    config = build_config(args)
    return serve_api_with_config(config, bind=args.bind, port=args.port)


def command_dispatch(args: argparse.Namespace) -> int:
    config = build_config(args)
    result = dispatch_queued_tasks(
        config,
        mode=args.mode,
        max_running=args.max_running,
        limit=args.limit,
        gpu_count_override=args.gpu_count,
        cpu_thread_limit=args.cpu_thread_limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["errors"] else 1


def command_serve(args: argparse.Namespace) -> int:
    config = build_config(args)
    return serve_dispatcher_with_config(
        config,
        mode=args.mode,
        max_running=args.max_running,
        dispatch_limit=args.dispatch_limit,
        gpu_count=args.gpu_count,
        cpu_thread_limit=args.cpu_thread_limit,
        poll_seconds=args.poll_seconds,
        verbose=args.verbose,
    )


def command_service_run(args: argparse.Namespace) -> int:
    config = build_config(args)
    specs = managed_service_specs(
        config,
        api_bind=getattr(args, "bind", DEFAULT_SERVICE_API_BIND),
        api_port=getattr(args, "port", DEFAULT_API_PORT),
        dispatcher_mode=getattr(args, "mode", DEFAULT_SERVICE_DISPATCHER_MODE),
        dispatcher_gpu_count=getattr(args, "gpu_count", DEFAULT_SERVICE_GPU_COUNT),
        dispatcher_cpu_thread_limit=getattr(args, "cpu_thread_limit", DEFAULT_CPU_THREAD_LIMIT),
        dispatcher_poll_seconds=getattr(args, "poll_seconds", DEFAULT_SERVICE_POLL_SECONDS),
    )
    if args.service_name == "api":
        spec = specs["api"]
        return run_managed_service(
            config,
            spec,
            hooks=service_manager_hooks(),
            details={
                "entrypoint_path": str(default_entrypoint_path()),
                "working_directory": str(repo_root()),
                "bind": args.bind,
                "port": int(args.port),
            },
            run=lambda: serve_api_with_config(config, bind=args.bind, port=args.port),
        )
    spec = specs["dispatcher"]
    return run_managed_service(
        config,
        spec,
        hooks=service_manager_hooks(),
        details={
            "entrypoint_path": str(default_entrypoint_path()),
            "working_directory": str(repo_root()),
            "mode": args.mode,
            "max_running": int(args.max_running),
            "dispatch_limit": int(args.dispatch_limit),
            "gpu_count": int(args.gpu_count),
            "cpu_thread_limit": int(args.cpu_thread_limit),
            "poll_seconds": float(args.poll_seconds),
            "verbose": bool(args.verbose),
        },
        run=lambda: serve_dispatcher_with_config(
            config,
            mode=args.mode,
            max_running=args.max_running,
            dispatch_limit=args.dispatch_limit,
            gpu_count=args.gpu_count,
            cpu_thread_limit=args.cpu_thread_limit,
            poll_seconds=args.poll_seconds,
            verbose=args.verbose,
        ),
    )


def command_service_doctor(args: argparse.Namespace) -> int:
    config = build_config(args)
    specs = managed_service_specs(
        config,
        api_bind=args.api_bind,
        api_port=args.api_port,
        dispatcher_mode=args.dispatcher_mode,
        dispatcher_gpu_count=args.dispatcher_gpu_count,
        dispatcher_cpu_thread_limit=args.dispatcher_cpu_thread_limit,
        dispatcher_poll_seconds=args.dispatcher_poll_seconds,
    )
    payload = build_service_doctor_payload(
        config,
        specs,
        user=args.user or current_system_user_name(),
        group=args.group or current_system_group_name(),
        working_directory=repo_root(),
        entrypoint_path=default_entrypoint_path(),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get("healthy", False)) else 1


def command_service_print_systemd(args: argparse.Namespace) -> int:
    config = build_config(args)
    specs = managed_service_specs(
        config,
        api_bind=args.api_bind,
        api_port=args.api_port,
        dispatcher_mode=args.dispatcher_mode,
        dispatcher_gpu_count=args.dispatcher_gpu_count,
        dispatcher_cpu_thread_limit=args.dispatcher_cpu_thread_limit,
        dispatcher_poll_seconds=args.dispatcher_poll_seconds,
    )
    selected_names = list(specs) if args.service_name == "all" else [args.service_name]
    rendered_units: list[str] = []
    for name in selected_names:
        spec = specs[name]
        unit_text = render_systemd_unit(
            config,
            spec,
            user=args.user or current_system_user_name(),
            group=args.group or current_system_group_name(),
            working_directory=repo_root(),
            entrypoint_path=default_entrypoint_path(),
        ).rstrip()
        if len(selected_names) == 1:
            rendered_units.append(unit_text)
        else:
            rendered_units.append(f"# {spec.unit_name}\n{unit_text}")
    print("\n\n".join(rendered_units))
    return 0


def command_status(args: argparse.Namespace) -> int:
    config = build_config(args)
    gpu_rows = get_gpu_summary_table()
    total_gpu_slots = detect_gpu_count() or len(gpu_rows)
    active_states = [item for item in iter_task_states(config) if str(item.get("status", "")) in ACTIVE_TASK_STATUSES]
    cpu_thread_limit = detect_default_cpu_thread_limit()
    active_cpu_threads = sum(task_requested_cpu_budget(merged_spec_with_state(config, item)) for item in active_states)
    if args.task_id:
        task_id = normalize_task_id(args.task_id)
        state = load_task_state(config, task_id)
        if not state:
            print(f"Task not found: {task_id}", file=sys.stderr)
            return 1
        state = enrich_task_state(
            config,
            state,
            gpu_rows=gpu_rows,
            total_gpu_slots=total_gpu_slots,
            active_cpu_threads=active_cpu_threads,
            cpu_thread_limit=cpu_thread_limit,
        )
        if args.json:
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return 0
        for key in [
            "task_id",
            "task_key",
            "client_task_id",
            "client_task_key",
            "owner_tenant",
            "owner_role",
            "owner_label",
            "submitted_via_api",
            "status",
            "lifecycle_state",
            "runtime_state",
            "phase",
            "automation_recommendation",
            "executor_name",
            "remote_workdir",
            "agent_name",
            "priority",
            "execution_mode",
            "watch_pid",
            "proposal_path",
            "proposal_source",
            "proposal_owner",
            "closeout_proposal_dir",
            "closeout_proposal_dir_source",
            "project_history_file",
            "project_history_file_source",
            "cpu_profile",
            "cpu_profile_resolved",
            "cpu_threads",
            "cpu_threads_mode",
            "cpu_threads_min",
            "cpu_threads_max",
            "assigned_cpu_threads",
            "cpu_thread_source",
            "cpu_workers",
            "cpu_workers_min",
            "cpu_workers_max",
            "assigned_cpu_workers",
            "cpu_worker_source",
            "cpu_budget",
            "cpu_retry_attempts",
            "cpu_retry_max_attempts",
            "cpu_retry_last_reason",
            "available_cpu_threads",
            "cpu_block_reason",
            "assigned_gpus",
            "dependency_state",
            "dependency_resolution",
            "artifact_state",
            "artifact_resolution",
            "report_state",
            "report_resolution",
            "blocked_reason",
            "cpu_block_reason",
            "gpu_block_reason",
            "eligible_gpu_ids",
            "why_started",
            "dispatch_diagnostics",
            "launch_diagnostics",
            "platform_recovery",
            "dispatch_gpu_snapshot",
            "launch_gpu_snapshot",
            "rejected_reason",
            "feedback_mode",
            "pending_feedback",
            "notification_signal",
            "followup_status",
            "followup_audit_status",
            "followup_entity_present",
            "followup_entity_key",
            "followup_last_signal",
            "followup_last_action",
            "followup_stopped_at",
            "subagent_session_id",
            "subagent_model",
            "subagent_continue_attempts",
            "subagent_recovered_with_continue",
            "depends_on",
            "submitted_at",
            "started_via_tmux_at",
            "started_at",
            "ended_at",
            "exit_code",
            "exit_signal",
            "failure_kind",
            "failure_summary",
            "failure_excerpt",
            "needs_attention",
            "attention_reason",
            "attention_message",
            "report_summary",
            "tmux_session_name",
            "codex_session_id",
            "resumed_session_id",
            "last_event_path",
            "last_message_path",
        ]:
            if key in state:
                print(f"{key}: {state[key]}")
        return 0
    states = [
        enrich_task_state(
            config,
            item,
            gpu_rows=gpu_rows,
            total_gpu_slots=total_gpu_slots,
            active_cpu_threads=active_cpu_threads,
            cpu_thread_limit=cpu_thread_limit,
        )
        for item in iter_task_states(config)[: args.limit]
    ]
    if args.json:
        print(json.dumps(states, ensure_ascii=False, indent=2))
        return 0
    for state in states:
        print(
            f"{state.get('task_id')} | status={state.get('status')} | phase={state.get('phase')} | "
            f"blocked_reason={state.get('blocked_reason', '')} | updated_at={state.get('updated_at')} | tmux={state.get('tmux_session_name')}"
        )
    return 0


def command_cancel(args: argparse.Namespace) -> int:
    config = build_config(args)
    task_id = normalize_task_id(args.task_id)
    state = load_task_state(config, task_id)
    if not state:
        print(f"Task not found: {task_id}", file=sys.stderr)
        return 1
    session_name = str(state.get("tmux_session_name", ""))
    if not session_name:
        print(f"Task {task_id} has no tmux session recorded.", file=sys.stderr)
        return 1
    if args.suppress_feedback:
        update_task_feedback_mode(config, task_id, "off")
    completed = run_subprocess(tmux_command(config, "kill-session", "-t", session_name), cwd=state.get("workdir"))
    append_log(task_runner_log_path(config, task_id), f"cancel returncode={completed.returncode}")
    if completed.returncode != 0:
        print(completed.stderr[-1000:] or completed.stdout[-1000:] or "failed to kill tmux session", file=sys.stderr)
        return 1
    print(f"Requested tmux session shutdown: {session_name}")
    return 0


def command_wait(args: argparse.Namespace) -> int:
    config = build_config(args)
    task_id = normalize_task_id(args.task_id)
    deadline = time.time() + args.timeout_seconds
    last_state: dict[str, Any] = {}
    while time.time() <= deadline:
        state = load_task_state(config, task_id)
        if state:
            last_state = state
            if is_terminal_status(str(state.get("status", ""))):
                if args.json:
                    print(json.dumps(state, ensure_ascii=False, indent=2))
                else:
                    print(f"{task_id} finished with status={state.get('status')} exit_code={state.get('exit_code')}")
                if args.expect_status and state.get("status") != args.expect_status:
                    return 2
                return 0
        time.sleep(args.poll_seconds)
    if args.json and last_state:
        print(json.dumps(last_state, ensure_ascii=False, indent=2))
    else:
        print(f"Timed out waiting for task: {task_id}", file=sys.stderr)
    return 1


def command_notify(args: argparse.Namespace) -> int:
    config = build_config(args)
    task_id = normalize_task_id(args.task_id)
    spec = apply_session_redirect_to_spec(config, load_task_spec(config, task_id), include_migrating=True)
    if not spec:
        print(f"Task spec not found: {task_id}", file=sys.stderr)
        return 1
    try:
        event_path = resolve_event_path(config, task_id, args.event_file)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    event = load_event(event_path)
    notification = resume_codex_session_with_prompt(
        config,
        spec,
        build_resume_prompt(
            spec,
            event,
            continuous_research_enabled=continuous_research_mode_enabled(
                config,
                codex_session_id=str(spec.get("codex_session_id", "")).strip(),
            ),
        ),
        output_last_message_path=str(task_last_message_path(config, task_id)),
        log_path=task_runner_log_path(config, task_id),
        min_idle_seconds=DEFAULT_NOTIFICATION_MIN_IDLE_SECONDS,
        feedback_source_kind="manual_notify",
        feedback_source_key=task_id,
        feedback_task_id=task_id,
        feedback_task_ids=[task_id],
    )
    merge_task_state(
        config,
        task_id,
        pending_feedback=False,
        notification_ok=notification.get("ok", False),
        resumed_session_id=notification.get("resumed_session_id", spec["codex_session_id"]),
        used_fallback_clone=notification.get("used_fallback_clone", False),
        notification_finished_at=notification.get("finished_at"),
        manual_notification_at=utc_now(),
        **platform_attention_updates_from_result(notification),
        manual_notification_summary={
            "ok": notification.get("ok", False),
            "original_session_id": notification.get("original_session_id"),
            "resumed_session_id": notification.get("resumed_session_id"),
            "used_fallback_clone": notification.get("used_fallback_clone", False),
            "fallback_provider": notification.get("fallback_provider", ""),
            "platform_error_kind": notification.get("platform_error_kind", ""),
            "platform_error_summary": notification.get("platform_error_summary", ""),
            "platform_error_retryable": notification.get("platform_error_retryable", False),
            "platform_error_needs_human_attention": notification.get("platform_error_needs_human_attention", False),
        },
    )
    print(json.dumps(notification, ensure_ascii=False, indent=2))
    return 0 if notification.get("ok", False) else 1


def sample_prompt_preview_spec() -> dict[str, Any]:
    return {
        "task_id": "prompt-preview-task",
        "workdir": "/home/Awei/project",
        "command": "python train.py --config configs/example.yaml --dataset sample-benchmark",
        "execution_mode": "shell",
        "success_prompt": "",
        "failure_prompt": "",
        "task_note": "prompt preview sample",
        "prompt_max_chars": 12000,
        "artifact_globs": [],
        "proposal_path": "/home/Awei/project/docs/PROPOSAL.md",
        "proposal_source": "explicit",
        "proposal_owner": True,
        "closeout_proposal_dir": "/home/Awei/project/docs/closeout",
        "closeout_proposal_dir_source": "explicit",
        "project_history_file": "/home/Awei/project/docs/HISTORY.md",
        "project_history_file_source": "explicit",
    }


def sample_prompt_preview_event() -> dict[str, Any]:
    return {
        "status": "completed",
        "command_log_path": "/tmp/taskboard-preview.log",
        "runner_log_path": "/tmp/taskboard-preview-runner.log",
        "feedback_data_path": "/tmp/taskboard-preview-feedback.json",
        "failure_kind": "completed",
        "failure_summary": "Preview task finished successfully.",
        "duration_seconds": 12,
        "artifact_context": [
            {"pattern": "stage_summary.json", "path": "/tmp/stage_summary.json", "summary": "preview"}
        ],
        "log_tail": "",
    }


def command_prompt_preview(args: argparse.Namespace) -> int:
    config = build_config(args)
    spec = sample_prompt_preview_spec()
    event = sample_prompt_preview_event()
    if args.task_id:
        task_id = normalize_task_id(args.task_id)
        loaded_spec = apply_session_redirect_to_spec(config, load_task_spec(config, task_id), include_migrating=True)
        if not loaded_spec:
            print(f"Task spec not found: {task_id}", file=sys.stderr)
            return 1
        spec = loaded_spec
        if args.scene in {"resume", "reflow-batch"}:
            try:
                event_path = resolve_event_path(config, task_id, args.event_file)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            event = load_event(event_path)
    scene = str(args.scene).strip()
    if scene == "planning":
        prompt = build_continuous_planning_prompt(spec, trigger_signal=args.trigger_signal)
    elif scene == "successor-bootstrap":
        prompt = build_successor_bootstrap_prompt(
            spec,
            predecessor_session_id=args.predecessor_session_id,
            trigger_signal=args.trigger_signal or "none",
        )
    elif scene == "execution":
        prompt = build_unified_execution_prompt(spec, trigger_signal=args.trigger_signal)
    elif scene == "closeout":
        prompt = build_continuous_transition_prompt(spec, trigger_signal=args.trigger_signal)
    elif scene == "reflow-batch":
        prompt = build_queued_feedback_batch_prompt(
            spec,
            [{"resume_spec": spec, "resume_event": event}],
            continuous_research_enabled=args.continuous,
        )
    elif scene == "protocol-repair":
        prompt = build_protocol_self_check_repair_prompt(
            spec,
            {"protocol_issue": "missing_protocol_footer", "protocol_footer": {}},
            continuous_research_enabled=args.continuous,
        )
    else:
        prompt = build_resume_prompt(spec, event, continuous_research_enabled=args.continuous)
    print(f"# prompt_source: {active_prompt_source()}")
    print(prompt)
    return 0


def safe_remove_task_dir(config: AppConfig, path: Path) -> None:
    resolved = path.resolve()
    allowed_roots = [root.resolve() for root in all_task_roots(config)]
    matched_root = None
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            matched_root = root
            break
        except ValueError:
            continue
    if matched_root is None:
        raise ValueError(f"Refusing to delete path outside known task roots: {resolved}")
    if resolved == matched_root:
        raise ValueError("Refusing to delete the entire tasks root.")
    task_id = resolved.name
    shutil.rmtree(resolved)
    remove_task_index_entry(config.app_home, task_id)


def command_cleanup(args: argparse.Namespace) -> int:
    config = build_config(args)
    removed: list[str] = []
    skipped: list[str] = []
    states = iter_all_task_states(config)
    target_states = states if not args.task_id else [load_task_state(config, normalize_task_id(args.task_id))]
    if args.task_id and not target_states[0]:
        print(f"Task not found: {normalize_task_id(args.task_id)}", file=sys.stderr)
        return 1
    for state in target_states:
        task_id = str(state.get("task_id", ""))
        if not task_id:
            continue
        session_name = str(state.get("tmux_session_name", ""))
        running = bool(session_name and tmux_session_exists(config, session_name))
        runner_alive = task_runner_process_alive(state)
        status = str(state.get("status", ""))
        if running or runner_alive:
            if args.kill_if_running:
                if running:
                    completed = run_subprocess(tmux_command(config, "kill-session", "-t", session_name), cwd=state.get("workdir"))
                    append_log(task_runner_log_path(config, task_id), f"cleanup_kill returncode={completed.returncode}")
                    time.sleep(1.0)
                running = bool(session_name and tmux_session_exists(config, session_name))
                runner_alive = task_runner_process_alive(state)
            if running or runner_alive:
                skipped.append(f"{task_id}:{'running' if running else 'runner_alive'}")
                continue
        if not args.include_nonterminal and not is_terminal_status(status):
            skipped.append(f"{task_id}:{status}")
            continue
        path = task_root(config, task_id)
        if not path.exists():
            skipped.append(f"{task_id}:missing")
            continue
        safe_remove_task_dir(config, path)
        removed.append(task_id)
    print(json.dumps({"removed": removed, "skipped": skipped}, ensure_ascii=False, indent=2))
    return 0 if not skipped else 1


def handle_task_feedback(
    config: AppConfig,
    *,
    task_id: str,
    spec: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    spec = apply_session_redirect_to_spec(config, spec, include_migrating=True)
    feedback_mode = str(spec.get("feedback_mode", "auto")).strip() or "auto"
    if feedback_mode == "auto":
        session_id = str(spec.get("codex_session_id", "")).strip()
        if session_id:
            queued_feedback_exists = bool(followup_path(config, queued_feedback_key_for(spec)).exists())
            queue_reason = "queue_already_open" if queued_feedback_exists else "session_coalescing_window"
            queued = queue_feedback_resume(
                config,
                task_id=task_id,
                spec=spec,
                event=event,
                reason=queue_reason,
                min_idle_seconds=DEFAULT_NOTIFICATION_COALESCE_SECONDS,
            )
            notification = {
                "attempted": False,
                "ok": False,
                "deferred": True,
                "deferred_reason": queue_reason,
                "queue_depth": queued["queue_depth"],
                "followup_key": queued["followup_key"],
                "coalesced": True,
                "finished_at": utc_now(),
            }
            merge_task_state(
                config,
                task_id,
                feedback_mode=feedback_mode,
                pending_feedback=True,
                notification_ok=False,
                notification_signal="",
                notification_finished_at=notification["finished_at"],
                notification_summary={
                    "ok": False,
                    "deferred": True,
                    "deferred_reason": notification["deferred_reason"],
                    "queue_depth": queued["queue_depth"],
                    "followup_key": queued["followup_key"],
                    "coalesced": True,
                },
            )
            return notification
        min_idle_seconds = DEFAULT_NOTIFICATION_MIN_IDLE_SECONDS
        notification = resume_codex_session(config, spec, event, min_idle_seconds=min_idle_seconds)
        notification_signal = str(notification.get("taskboard_signal", "") or "")
        if session_id and not notification.get("ok", False):
            queue_reason = str(notification.get("deferred_reason", "") or "resume_failed")
            retry_after_seconds = int(notification.get("retry_after_seconds", min_idle_seconds) or min_idle_seconds)
            queued = queue_feedback_resume(
                config,
                task_id=task_id,
                spec=spec,
                event=event,
                reason=queue_reason,
                min_idle_seconds=retry_after_seconds,
            )
            notification = {
                **notification,
                "deferred": True,
                "deferred_reason": queue_reason,
                "queue_depth": queued["queue_depth"],
                "followup_key": queued["followup_key"],
                "finished_at": str(notification.get("finished_at", "") or utc_now()),
            }
            merge_task_state(
                config,
                task_id,
                feedback_mode=feedback_mode,
                pending_feedback=True,
                notification_ok=False,
                notification_signal=notification_signal,
                notification_finished_at=notification["finished_at"],
                **platform_attention_updates_from_result(notification),
                notification_summary={
                    "ok": False,
                    "deferred": True,
                    "deferred_reason": queue_reason,
                    "queue_depth": queued["queue_depth"],
                    "followup_key": queued["followup_key"],
                    "attempted": notification.get("attempted", False),
                    "original_session_id": notification.get("original_session_id"),
                    "resumed_session_id": notification.get("resumed_session_id"),
                    "retry_after_seconds": notification.get("retry_after_seconds", retry_after_seconds),
                    "continue_attempts": notification.get("continue_attempts", 0),
                    "recovered_with_continue": notification.get("recovered_with_continue", False),
                    "platform_error_kind": notification.get("platform_error_kind", ""),
                    "platform_error_summary": notification.get("platform_error_summary", ""),
                    "platform_error_retryable": notification.get("platform_error_retryable", False),
                    "platform_error_needs_human_attention": notification.get("platform_error_needs_human_attention", False),
                },
            )
            return notification
        merge_task_state(
            config,
            task_id,
            feedback_mode=feedback_mode,
            pending_feedback=False,
            notification_ok=notification.get("ok", False),
            notification_signal=notification_signal,
            resumed_session_id=notification.get("resumed_session_id", spec["codex_session_id"]),
            used_fallback_clone=notification.get("used_fallback_clone", False),
            notification_finished_at=notification.get("finished_at"),
            notification_summary={
                "ok": notification.get("ok", False),
                "original_session_id": notification.get("original_session_id"),
                "resumed_session_id": notification.get("resumed_session_id"),
                "used_fallback_clone": notification.get("used_fallback_clone", False),
                "fallback_provider": notification.get("fallback_provider", ""),
                "taskboard_signal": notification_signal,
                "continue_attempts": notification.get("continue_attempts", 0),
                "recovered_with_continue": notification.get("recovered_with_continue", False),
                "platform_error_kind": notification.get("platform_error_kind", ""),
                "platform_error_summary": notification.get("platform_error_summary", ""),
                "platform_error_retryable": notification.get("platform_error_retryable", False),
                "platform_error_needs_human_attention": notification.get("platform_error_needs_human_attention", False),
            },
        )
        protocol_footer = notification.get("taskboard_protocol", {}) if isinstance(notification.get("taskboard_protocol", {}), dict) else {}
        protocol_issue = summarize_taskboard_protocol_issue(protocol_footer, signal_value=notification_signal)
        if notification.get("ok", False) and taskboard_protocol_requires_repair(protocol_footer, signal_value=notification_signal):
            current_state = load_task_state(config, task_id)
            current_summary = current_state.get("notification_summary", {}) if isinstance(current_state.get("notification_summary", {}), dict) else {}
            protocol_followup_scheduled = False
            if should_schedule_followup_for_spec(spec):
                schedule_protocol_self_check_repair(
                    config,
                    task_id=task_id,
                    spec=spec,
                    issue_summary=protocol_issue,
                    protocol_footer=protocol_footer,
                    observed_signal=notification_signal,
                    message_path=str(task_last_message_path(config, task_id)),
                )
                protocol_followup_scheduled = True
            merge_task_state(
                config,
                task_id,
                followup_status="scheduled" if protocol_followup_scheduled else str(current_state.get("followup_status", "")),
                followup_last_signal=notification_signal,
                followup_last_action=(
                    f"scheduled:{PROTOCOL_SELF_CHECK_REPAIR_REASON}"
                    if protocol_followup_scheduled
                    else "protocol_self_check_repair_needed_without_followup"
                ),
                followup_stopped_at="" if protocol_followup_scheduled else str(current_state.get("followup_stopped_at", "")),
                followup_last_message_path=str(task_last_message_path(config, task_id)),
                notification_signal=notification_signal,
                notification_summary={
                    **current_summary,
                    "taskboard_signal": notification_signal,
                    "protocol_repair_scheduled": protocol_followup_scheduled,
                    "protocol_issue": protocol_issue,
                },
            )
            notification["protocol_repair_scheduled"] = protocol_followup_scheduled
            notification["protocol_issue"] = protocol_issue
            return notification
        if notification_signal in STOP_FOLLOWUP_SIGNALS and not should_override_stop_signal_with_continuous_research(
            config,
            notification_signal,
            codex_session_id=str(spec.get("codex_session_id", "")).strip(),
        ):
            merge_task_state(
                config,
                task_id,
                followup_status="stopped",
                followup_last_signal=notification_signal,
                followup_last_action="resolved_notification_signal_stop",
                followup_stopped_at=utc_now(),
                followup_last_message_path=str(task_last_message_path(config, task_id)),
            )
            resolved_keys = resolve_followups_for_stop_signal(
                config,
                session_id=str(spec.get("codex_session_id", "")).strip(),
                agent_name=str(spec.get("agent_name", "")).strip(),
                signal_value=notification_signal,
                reason="resolved_notification_signal_stop",
                message_path=str(task_last_message_path(config, task_id)),
            )
            notification["stopped_followups"] = resolved_keys
            return notification
        if notification_signal in CONTINUOUS_RESEARCH_OVERRIDE_SIGNALS and should_override_stop_signal_with_continuous_research(
            config,
            notification_signal,
            codex_session_id=str(spec.get("codex_session_id", "")).strip(),
        ):
            continuous_followup_scheduled = should_schedule_followup_for_spec(spec)
            if continuous_followup_scheduled:
                schedule_continuous_transition_followup(
                    config,
                    task_id=task_id,
                    spec=spec,
                    trigger_signal=notification_signal,
                    message_path=str(task_last_message_path(config, task_id)),
                )
            merge_task_state(
                config,
                task_id,
                research_phase="closeout",
                followup_status="scheduled" if continuous_followup_scheduled else str(load_task_state(config, task_id).get("followup_status", "")),
                followup_last_signal=notification_signal,
                followup_last_action=(
                    f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}"
                    if continuous_followup_scheduled
                    else "continuous_research_override_without_followup"
                ),
                followup_stopped_at="" if continuous_followup_scheduled else str(load_task_state(config, task_id).get("followup_stopped_at", "")),
                followup_last_message_path=str(task_last_message_path(config, task_id)),
                notification_signal=notification_signal,
                notification_summary={
                    **(notification.get("notification_summary", {}) if isinstance(notification.get("notification_summary"), dict) else {}),
                    "ok": notification.get("ok", False),
                    "original_session_id": notification.get("original_session_id"),
                    "resumed_session_id": notification.get("resumed_session_id"),
                    "used_fallback_clone": notification.get("used_fallback_clone", False),
                    "fallback_provider": notification.get("fallback_provider", ""),
                    "taskboard_signal": notification_signal,
                    "continue_attempts": notification.get("continue_attempts", 0),
                    "recovered_with_continue": notification.get("recovered_with_continue", False),
                    "continuous_research_mode": True,
                    "continuous_override_signal": notification_signal,
                    "research_phase": "closeout",
                },
            )
            notification["continuous_research_mode"] = True
            notification["continuous_transition_followup_scheduled"] = continuous_followup_scheduled
            notification["research_phase"] = "closeout"
            return notification
        if notification.get("ok", False) and notification_signal == "none":
            current_state = load_task_state(config, task_id)
            current_summary = current_state.get("notification_summary", {}) if isinstance(current_state.get("notification_summary", {}), dict) else {}
            merge_task_state(
                config,
                task_id,
                followup_status="resolved",
                followup_last_signal=notification_signal,
                followup_last_action="resolved_notification_signal_none",
                followup_stopped_at=utc_now(),
                followup_last_message_path=str(task_last_message_path(config, task_id)),
                notification_signal=notification_signal,
                notification_summary={
                    **current_summary,
                    "taskboard_signal": notification_signal,
                    "resolved_signal_none": True,
                },
            )
            notification["resolved_signal_none"] = True
            return notification
        if notification.get("ok", False) and notification_signal in PARKED_IDLE_SIGNALS:
            session_id = str(spec.get("codex_session_id", "")).strip()
            evidence_token = continuous_research_session_evidence_token(config, session_id, spec=spec) if session_id else ""
            repeat_count = CONTINUOUS_RESEARCH_IDLE_LOOP_THRESHOLD
            immediate_watchdog_scheduled = False
            if session_id:
                previous_state = continuous_research_session_state(config, session_id)
                repeat_count = next_parked_idle_repeat_count(previous_state, evidence_token=evidence_token)
                park_continuous_research_session(
                    config,
                    codex_session_id=session_id,
                    waiting_state=notification_signal,
                    waiting_reason="agent_requested_parked_idle",
                    evidence_token=evidence_token,
                    last_signal=notification_signal,
                    stable_idle_repeat_count=repeat_count,
                    updated_by="feedback",
                    source="handle-task-feedback",
                )
                if should_schedule_immediate_parked_watchdog(
                    config,
                    session_id=session_id,
                    spec=spec,
                    repeat_count=repeat_count,
                ):
                    schedule_immediate_parked_watchdog(
                        config,
                        session_id=session_id,
                        spec=spec,
                        signal_value=notification_signal,
                    )
                    immediate_watchdog_scheduled = True
            merge_task_state(
                config,
                task_id,
                research_phase="execution",
                session_flow_state="parked_idle",
                followup_status="scheduled" if immediate_watchdog_scheduled else "resolved",
                followup_last_signal=notification_signal,
                followup_last_action=(
                    f"scheduled:{CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON}"
                    if immediate_watchdog_scheduled
                    else "resolved_parked_idle"
                ),
                followup_stopped_at="" if immediate_watchdog_scheduled else utc_now(),
                followup_last_message_path=str(task_last_message_path(config, task_id)),
                notification_signal=notification_signal,
                notification_summary={
                    **(notification.get("notification_summary", {}) if isinstance(notification.get("notification_summary"), dict) else {}),
                    "research_phase": "execution",
                    "session_flow_state": "parked_idle",
                    "taskboard_signal": notification_signal,
                    "waiting_evidence_token": evidence_token,
                    "immediate_parked_watchdog_scheduled": immediate_watchdog_scheduled,
                },
            )
            notification["research_phase"] = "execution"
            notification["session_flow_state"] = "parked_idle"
            notification["waiting_evidence_token"] = evidence_token
            notification["immediate_parked_watchdog_scheduled"] = immediate_watchdog_scheduled
            return notification
        if notification.get("ok", False) and notification_signal in INLINE_CONTINUE_SIGNALS:
            current_state = load_task_state(config, task_id)
            current_summary = current_state.get("notification_summary", {}) if isinstance(current_state.get("notification_summary", {}), dict) else {}
            session_id = str(spec.get("codex_session_id", "")).strip()
            evidence_token = clear_waiting_state_for_inline_continue(
                config,
                session_id=session_id,
                spec=spec,
                signal_value=notification_signal,
                updated_by="feedback",
                source="handle-task-feedback-inline-continue",
            )
            merge_task_state(
                config,
                task_id,
                research_phase="execution",
                session_flow_state="inline_continue",
                followup_status="resolved",
                followup_last_signal=notification_signal,
                followup_last_action="resolved_inline_continue_no_wake",
                followup_stopped_at=utc_now(),
                followup_last_message_path=str(task_last_message_path(config, task_id)),
                notification_signal=notification_signal,
                notification_summary={
                    **current_summary,
                    "research_phase": "execution",
                    "session_flow_state": "inline_continue",
                    "taskboard_signal": notification_signal,
                    "waiting_evidence_token": evidence_token,
                },
            )
            notification["research_phase"] = "execution"
            notification["session_flow_state"] = "inline_continue"
            notification["waiting_evidence_token"] = evidence_token
            notification["inline_continue_no_wake"] = True
            return notification
        if notification.get("ok", False) and notification_signal == MATERIALS_READY_FOR_PROPOSAL_SIGNAL:
            current_state = load_task_state(config, task_id)
            current_summary = current_state.get("notification_summary", {}) if isinstance(current_state.get("notification_summary", {}), dict) else {}
            proposal_materialization_followup_scheduled = False
            session_id = str(spec.get("codex_session_id", "")).strip()
            evidence_token = continuous_research_session_evidence_token(config, session_id, spec=spec) if session_id else ""
            if session_id:
                clear_continuous_research_session_waiting_state(
                    config,
                    codex_session_id=session_id,
                    evidence_token=evidence_token,
                    last_signal=notification_signal,
                    updated_by="feedback",
                    source=PROPOSAL_MATERIALIZATION_REASON,
                )
            if should_schedule_followup_for_spec(spec):
                schedule_continuous_transition_followup(
                    config,
                    task_id=task_id,
                    spec=spec,
                    trigger_signal=notification_signal,
                    message_path=str(task_last_message_path(config, task_id)),
                )
                proposal_materialization_followup_scheduled = True
            merge_task_state(
                config,
                task_id,
                research_phase="closeout",
                session_flow_state="proposal_materialization",
                followup_status="scheduled" if proposal_materialization_followup_scheduled else str(current_state.get("followup_status", "")),
                followup_last_signal=notification_signal,
                followup_last_action=(
                    f"scheduled:{CONTINUOUS_RESEARCH_TRANSITION_REASON}"
                    if proposal_materialization_followup_scheduled
                    else "proposal_materialization_without_followup"
                ),
                followup_stopped_at="" if proposal_materialization_followup_scheduled else str(current_state.get("followup_stopped_at", "")),
                followup_last_message_path=str(task_last_message_path(config, task_id)),
                notification_signal=notification_signal,
                notification_summary={
                    **current_summary,
                    "research_phase": "closeout",
                    "session_flow_state": "proposal_materialization",
                    "taskboard_signal": notification_signal,
                },
            )
            notification["research_phase"] = "closeout"
            notification["session_flow_state"] = "proposal_materialization"
            notification["proposal_materialization_followup_scheduled"] = proposal_materialization_followup_scheduled
            return notification
        if notification.get("ok", False) and notification_signal in LOCAL_MICROSTEP_BATCH_SIGNALS:
            current_state = load_task_state(config, task_id)
            current_summary = current_state.get("notification_summary", {}) if isinstance(current_state.get("notification_summary", {}), dict) else {}
            local_followup_scheduled = False
            session_id = str(spec.get("codex_session_id", "")).strip()
            evidence_token = continuous_research_session_evidence_token(config, session_id, spec=spec) if session_id else ""
            if session_id:
                clear_continuous_research_session_waiting_state(
                    config,
                    codex_session_id=session_id,
                    evidence_token=evidence_token,
                    last_signal=notification_signal,
                    stable_idle_repeat_count=1,
                    updated_by="feedback",
                    source="handle-task-feedback-local-microstep",
                )
            if should_schedule_followup_for_spec(spec):
                local_followup_scheduled = schedule_local_microstep_followup(config, task_id=task_id, spec=spec)
            merge_task_state(
                config,
                task_id,
                research_phase="execution",
                session_flow_state="local_active",
                followup_status="scheduled" if local_followup_scheduled else str(current_state.get("followup_status", "")),
                followup_last_signal=notification_signal,
                followup_last_action=(
                    f"scheduled:{LOCAL_MICROSTEP_BATCH_REASON}" if local_followup_scheduled else "local_microstep_signal_without_followup"
                ),
                followup_stopped_at="" if local_followup_scheduled else str(current_state.get("followup_stopped_at", "")),
                followup_last_message_path=str(task_last_message_path(config, task_id)),
                notification_signal=notification_signal,
                notification_summary={
                    **current_summary,
                    "research_phase": "execution",
                    "session_flow_state": "local_active",
                    "taskboard_signal": notification_signal,
                },
            )
            notification["research_phase"] = "execution"
            notification["session_flow_state"] = "local_active"
            notification["local_microstep_followup_scheduled"] = local_followup_scheduled
            return notification
        if notification.get("ok", False) and notification_signal in WAITING_ON_ASYNC_SIGNALS:
            current_state = load_task_state(config, task_id)
            current_summary = current_state.get("notification_summary", {}) if isinstance(current_state.get("notification_summary", {}), dict) else {}
            newer_async_task_exists = newer_task_exists_for_spec(config, source_task_id=task_id, spec=spec)
            waiting_followup_scheduled = False
            session_id = str(spec.get("codex_session_id", "")).strip()
            live_task_present = waiting_signal_has_live_task(
                config,
                session_id=session_id,
                source_task_id=task_id,
            )
            evidence_token = continuous_research_session_evidence_token(config, session_id, spec=spec) if session_id else ""
            guard_enabled, parked_waiting_state, guarded_evidence_token, repeat_count = parked_waiting_signal_guard_details(
                config,
                session_id=session_id,
                spec=spec,
                followup_last_signal=str(current_state.get("followup_last_signal", "")).strip(),
                newer_async_task_exists=newer_async_task_exists,
            )
            if guard_enabled:
                guard_parked_waiting_signal_without_live_task(
                    config,
                    session_id=session_id,
                    spec=spec,
                    parked_waiting_state=parked_waiting_state,
                    evidence_token=guarded_evidence_token,
                    repeat_count=repeat_count,
                    updated_by="feedback",
                    source="handle-task-feedback-guard-invalid-waiting-signal",
                )
                merge_task_state(
                    config,
                    task_id,
                    research_phase="execution",
                    session_flow_state="parked_idle",
                    followup_status="resolved",
                    followup_last_signal=notification_signal,
                    followup_last_action="guarded_invalid_waiting_signal_to_parked_idle",
                    followup_last_message_path=str(task_last_message_path(config, task_id)),
                    notification_signal=notification_signal,
                    notification_summary={
                        **current_summary,
                        "research_phase": "execution",
                        "session_flow_state": "parked_idle",
                        "taskboard_signal": notification_signal,
                        "guarded_invalid_waiting_signal": True,
                        "guarded_to": parked_waiting_state,
                        "waiting_evidence_token": guarded_evidence_token,
                    },
                )
                notification["research_phase"] = "execution"
                notification["session_flow_state"] = "parked_idle"
                notification["guarded_invalid_waiting_signal"] = True
                notification["guarded_to"] = parked_waiting_state
                notification["waiting_evidence_token"] = guarded_evidence_token
                return notification
            if session_id:
                clear_continuous_research_session_waiting_state(
                    config,
                    codex_session_id=session_id,
                    evidence_token=evidence_token,
                    last_signal=notification_signal,
                    updated_by="feedback",
                    source="handle-task-feedback-waiting-on-async",
                )
            if not newer_async_task_exists and not live_task_present and should_schedule_followup_for_spec(spec):
                waiting_followup_scheduled = schedule_waiting_on_async_watchdog(config, task_id=task_id, spec=spec)
            merge_task_state(
                config,
                task_id,
                research_phase="execution",
                session_flow_state="awaiting_async",
                followup_status=(
                    "scheduled"
                    if waiting_followup_scheduled
                    else ("resolved" if newer_async_task_exists else str(current_state.get("followup_status", "")))
                ),
                followup_last_signal=notification_signal,
                followup_last_action=(
                    f"scheduled:{WAITING_ON_ASYNC_REASON}"
                    if waiting_followup_scheduled
                    else (
                        "resolved_waiting_on_async_newer_task"
                        if newer_async_task_exists
                        else (
                            "resolved_waiting_on_async_live_task"
                            if live_task_present
                            else "waiting_on_async_without_followup"
                        )
                    )
                ),
                followup_stopped_at="" if waiting_followup_scheduled else str(current_state.get("followup_stopped_at", "")),
                followup_last_message_path=str(task_last_message_path(config, task_id)),
                notification_signal=notification_signal,
                notification_summary={
                    **current_summary,
                    "research_phase": "execution",
                    "session_flow_state": "awaiting_async",
                    "taskboard_signal": notification_signal,
                    "newer_async_task_exists": newer_async_task_exists,
                    "live_task_present": live_task_present,
                },
            )
            notification["research_phase"] = "execution"
            notification["session_flow_state"] = "awaiting_async"
            notification["waiting_on_async_watchdog_scheduled"] = waiting_followup_scheduled
            notification["newer_async_task_exists"] = newer_async_task_exists
            notification["live_task_present"] = live_task_present
            return notification
        if notification.get("ok", False) and should_schedule_followup_for_spec(spec):
            schedule_followup(config, task_id=task_id, spec=spec, reason="no_new_task_after_feedback")
        return notification

    pending_feedback = feedback_mode == "manual"
    notification = {
        "attempted": False,
        "ok": False,
        "skipped": True,
        "feedback_mode": feedback_mode,
        "finished_at": utc_now(),
    }
    merge_task_state(
        config,
        task_id,
        feedback_mode=feedback_mode,
        pending_feedback=pending_feedback,
        notification_ok=False,
        notification_finished_at=notification["finished_at"],
        notification_summary={
            "ok": False,
            "skipped": True,
            "feedback_mode": feedback_mode,
        },
    )
    return notification


def maybe_requeue_cpu_backoff(
    config: AppConfig,
    *,
    task_id: str,
    spec: dict[str, Any],
    event: dict[str, Any],
    event_path: Path,
) -> bool:
    if str(spec.get("execution_mode", "shell")).strip() != "shell":
        return False
    if coerce_non_negative_int(spec.get("gpu_slots", 0)) > 0:
        return False
    policy = resolve_cpu_thread_policy(spec)
    if str(policy.get("mode", "fixed")) != "adaptive":
        return False
    retry_reason = cpu_resource_retry_reason(event)
    if not retry_reason:
        return False
    duration_seconds = event.get("duration_seconds", None)
    startup_failure_threshold = int(spec.get("startup_failure_threshold_seconds", DEFAULT_STARTUP_FAILURE_SECONDS))
    if duration_seconds is not None and int(duration_seconds or 0) > startup_failure_threshold:
        return False
    attempts = coerce_non_negative_int(spec.get("cpu_retry_attempts", 0))
    max_attempts = max(1, coerce_non_negative_int(spec.get("cpu_retry_max_attempts", DEFAULT_CPU_RETRY_MAX_ATTEMPTS)) or DEFAULT_CPU_RETRY_MAX_ATTEMPTS)
    current_threads = max(
        coerce_non_negative_int(spec.get("assigned_cpu_threads", 0)),
        int(policy.get("assigned_threads", 0) or 0),
        int(policy.get("min_threads", 0) or 0),
    )
    min_threads = max(1, int(policy.get("min_threads", 0) or 1))
    if current_threads <= min_threads or attempts >= max_attempts:
        return False
    next_threads = next_cpu_backoff_threads(current_threads, min_threads)
    if next_threads >= current_threads:
        return False

    updated_spec = dict(spec)
    updated_spec["cpu_threads_mode"] = "adaptive"
    updated_spec["cpu_threads"] = min_threads
    updated_spec["cpu_threads_min"] = min_threads
    existing_max = coerce_non_negative_int(updated_spec.get("cpu_threads_max", 0))
    updated_spec["cpu_threads_max"] = next_threads if existing_max <= 0 else max(min_threads, min(existing_max, next_threads))
    updated_spec["assigned_cpu_threads"] = 0
    updated_spec["cpu_retry_attempts"] = attempts + 1
    updated_spec["cpu_retry_max_attempts"] = max_attempts
    updated_spec["cpu_retry_last_reason"] = retry_reason
    write_task_spec(config, task_id, updated_spec)

    event["cpu_backoff_retry_scheduled"] = True
    event["cpu_retry_reason"] = retry_reason
    event["cpu_retry_attempt"] = attempts + 1
    event["cpu_retry_max_attempts"] = max_attempts
    event["next_cpu_threads_max"] = updated_spec["cpu_threads_max"]
    event["notification_suppressed"] = True
    atomic_write_json(event_path, event)

    merge_task_state(
        config,
        task_id,
        status="queued",
        pid=0,
        started_at="",
        ended_at="",
        duration_seconds=0,
        exit_code=None,
        exit_signal="",
        cpu_threads=min_threads,
        cpu_threads_min=min_threads,
        cpu_threads_max=updated_spec["cpu_threads_max"],
        cpu_threads_mode="adaptive",
        assigned_cpu_threads=0,
        cpu_thread_source=str(policy.get("source", "") or "cpu_only_default"),
        cpu_retry_attempts=attempts + 1,
        cpu_retry_max_attempts=max_attempts,
        cpu_retry_last_reason=retry_reason,
        pending_feedback=False,
        notification_ok=False,
        needs_attention=False,
        attention_reason="",
        attention_message="",
        last_event_path=str(event_path),
        failure_kind=event.get("failure_kind", ""),
        failure_summary=event.get("failure_summary", ""),
        failure_excerpt=event.get("failure_excerpt", ""),
    )
    append_log(
        task_runner_log_path(config, task_id),
        f"cpu_backoff_requeue attempt={attempts + 1}/{max_attempts} reason={retry_reason} threads={current_threads}->{updated_spec['cpu_threads_max']}",
    )
    return True


def command_feedback_mode(args: argparse.Namespace) -> int:
    config = build_config(args)
    if not args.all and not args.task_id:
        print("Use --task-id or --all.", file=sys.stderr)
        return 1
    target_states = iter_all_task_states(config) if args.all else [load_task_state(config, normalize_task_id(args.task_id))]
    if not args.all and not target_states[0]:
        print(f"Task not found: {normalize_task_id(args.task_id)}", file=sys.stderr)
        return 1
    updated: list[str] = []
    for state in target_states:
        task_id = str(state.get("task_id", ""))
        if not task_id:
            continue
        update_task_feedback_mode(config, task_id, args.mode)
        updated.append(task_id)
    print(json.dumps({"updated": updated, "mode": args.mode}, ensure_ascii=False, indent=2))
    return 0


def command_priority(args: argparse.Namespace) -> int:
    config = build_config(args)
    if not args.task_id and not args.agent_name:
        print("Use --task-id or --agent-name.", file=sys.stderr)
        return 1
    target_states = iter_all_task_states(config)
    updated: list[dict[str, Any]] = []
    for state in target_states:
        task_id = str(state.get("task_id", ""))
        if not task_id:
            continue
        if args.task_id and task_id != normalize_task_id(args.task_id):
            continue
        if args.agent_name and str(state.get("agent_name", "")) != args.agent_name:
            continue
        new_priority = args.value if args.value is not None else int(state.get("priority", 0) or 0) + int(args.delta)
        update_task_priority(config, task_id, int(new_priority))
        updated.append({"task_id": task_id, "priority": int(new_priority)})
    print(json.dumps({"updated": updated}, ensure_ascii=False, indent=2))
    return 0 if updated else 1


def command_followup_stop(args: argparse.Namespace) -> int:
    config = build_config(args)
    updated: list[str] = []
    for followup in load_followups(config):
        if args.task_id and str(followup.get("task_id", "")) != normalize_task_id(args.task_id):
            continue
        if args.agent_name and str(followup.get("agent_name", "")) != args.agent_name:
            continue
        followup_key = str(followup.get("followup_key", "")).strip()
        is_queued_feedback = str(followup.get("followup_type", "")).strip() == "queued_feedback_resume"
        for task_id in followup_task_ids(followup):
            merge_task_state(
                config,
                task_id,
                pending_feedback=False if is_queued_feedback else bool(load_task_state(config, task_id).get("pending_feedback", False)),
                followup_status="stopped",
                followup_last_signal="MANUAL_STOP",
                followup_last_action="manual_followup_stop",
                followup_stopped_at=utc_now(),
                followup_last_message_path=str(followup_message_path(config, followup_key)),
            )
        resolve_followup(config, followup_key)
        updated.append(followup_key)
    if not updated and args.task_id:
        task_id = normalize_task_id(args.task_id)
        state = load_task_state(config, task_id)
        if state and str(state.get("followup_status", "")).strip() == "scheduled":
            merge_task_state(
                config,
                task_id,
                pending_feedback=False,
                followup_status="stopped",
                followup_last_signal="MANUAL_STOP",
                followup_last_action="manual_followup_stop_missing_entity",
                followup_stopped_at=utc_now(),
            )
            updated.append(f"state-only:{task_id}")
    print(json.dumps({"stopped_followups": updated}, ensure_ascii=False, indent=2))
    return 0 if updated else 1


def command_followup_reconcile(args: argparse.Namespace) -> int:
    config = build_config(args)
    reconciled: list[dict[str, Any]] = []
    now = utc_now()
    for state in iter_all_task_states(config):
        task_id = str(state.get("task_id", "")).strip()
        if not task_id:
            continue
        if args.task_id and task_id != normalize_task_id(args.task_id):
            continue
        if args.agent_name and str(state.get("agent_name", "")).strip() != args.agent_name:
            continue
        followup_status = str(state.get("followup_status", "")).strip()
        pending_feedback = bool(state.get("pending_feedback", False))
        followup_present, followup_key = followup_entity_info(config, task_id)
        audit_status = ""
        action = ""
        updates: dict[str, Any] = {}
        if followup_status == "scheduled" and not followup_present:
            if pending_feedback:
                audit_status = "scheduled_missing_entity_pending_feedback"
                action = "reconciled_missing_followup_entity_pending_feedback"
                updates = {
                    "pending_feedback": False,
                    "followup_status": "stopped",
                    "followup_last_action": action,
                    "followup_last_signal": str(state.get("followup_last_signal", "")).strip(),
                    "followup_stopped_at": now,
                }
            else:
                audit_status = "scheduled_missing_entity"
                action = "reconciled_missing_followup_entity"
                updates = {
                    "followup_status": "resolved",
                    "followup_last_action": action,
                    "followup_last_signal": str(state.get("followup_last_signal", "")).strip(),
                }
        elif followup_status in {"resolved", "stopped"} and followup_present:
            audit_status = "terminal_state_with_live_entity"
            action = "reconciled_terminal_state_with_live_entity"
            if not args.dry_run:
                resolve_followup(config, followup_key)
        if not action:
            continue
        before_status = followup_status
        before_pending = pending_feedback
        if not args.dry_run and updates:
            merge_task_state(config, task_id, **updates)
        reconciled.append(
            {
                "task_id": task_id,
                "agent_name": str(state.get("agent_name", "")).strip(),
                "audit_status": audit_status,
                "action": action,
                "before_followup_status": before_status,
                "before_pending_feedback": before_pending,
                "followup_entity_present": followup_present,
                "followup_entity_key": followup_key,
                "after_followup_status": updates.get("followup_status", before_status),
                "after_pending_feedback": updates.get("pending_feedback", before_pending),
            }
        )
    payload = {
        "dry_run": bool(args.dry_run),
        "reconciled_count": len(reconciled),
        "reconciled": reconciled,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if reconciled or args.dry_run else 1


def command_migrate_session(args: argparse.Namespace) -> int:
    config = build_config(args)
    try:
        payload = perform_session_cutover(
            config,
            from_session_id=str(args.from_session_id or "").strip(),
            to_session_id=str(args.to_session_id or "").strip(),
            interrupt_grace_seconds=int(args.interrupt_grace_seconds or 0),
            updated_by="cli",
            source="migrate-session",
            dry_run=bool(args.dry_run),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def migrate_task_dir(
    config: AppConfig,
    *,
    source_root: Path,
    task_dir: Path,
) -> dict[str, Any]:
    if task_dir.is_symlink():
        resolved_target = task_dir.resolve()
        if resolved_target.exists() and resolved_target.parent.resolve() == config.tasks_root.resolve():
            return {
                "task_id": task_dir.name,
                "active": True,
                "source_root": str(source_root),
                "destination": str(resolved_target),
                "bridge_mode": "already_linked",
                "legacy_stub": str(task_dir),
            }
    task_id = task_dir.name
    state_path = task_dir / "state.json"
    spec_path = task_dir / "spec.json"
    raw_state = load_raw_json_dict(state_path)
    raw_spec = load_raw_json_dict(spec_path)
    state = normalize_task_state_payload(raw_state if raw_state else {"task_id": task_id})
    spec = normalize_task_spec_payload(raw_spec if raw_spec else {"task_id": task_id, "command": "", "workdir": ""})

    command_log_path = task_dir / "command.log"
    recovered_workdir, recovered_command = extract_command_metadata_from_log(command_log_path)
    if recovered_workdir:
        state["workdir"] = recovered_workdir
        spec["workdir"] = recovered_workdir
    if recovered_command:
        state["command"] = recovered_command
        spec["command"] = recovered_command

    active = task_should_remain_live(state) or task_runner_process_alive(state) or (
        bool(state.get("tmux_session_name")) and tmux_session_exists(config, str(state.get("tmux_session_name")))
    )

    if active:
        destination = config.tasks_root / task_id
    else:
        destination = unique_archive_destination(config, source_root, task_id)
    if destination.exists():
        raise ValueError(f"Destination already exists: {destination}")

    old_spec_path = str(task_dir / "spec.json")
    old_task_dir = task_dir.resolve()
    ensure_dir(destination.parent)
    shutil.move(str(task_dir), str(destination))
    bridge_mode = "moved"
    legacy_stub = ""
    if active:
        os.symlink(str(destination), str(task_dir))
        bridge_mode = "symlink_bridge"
        legacy_stub = str(task_dir)

    migrated_state = normalize_task_state_payload(load_raw_json_dict(destination / "state.json") or state)
    migrated_spec = normalize_task_spec_payload(load_raw_json_dict(destination / "spec.json") or spec)
    if recovered_workdir:
        migrated_state["workdir"] = recovered_workdir
        migrated_spec["workdir"] = recovered_workdir
    if recovered_command:
        migrated_state["command"] = recovered_command
        migrated_spec["command"] = recovered_command
    if active and str(migrated_state.get("status", "")) not in ACTIVE_TASK_STATUSES:
        migrated_state["status"] = "running"
    migrated_state["paths"] = task_paths_for_root(destination.parent, destination.name)
    migrated_state["migrated_from"] = str(source_root)
    migrated_state["migrated_at"] = utc_now()
    migrated_state["legacy_spec_path"] = old_spec_path
    migrated_state["legacy_task_root"] = str(old_task_dir)
    migrated_state["legacy_bridge_mode"] = bridge_mode
    if legacy_stub:
        migrated_state["legacy_stub_path"] = legacy_stub
    migrated_spec["migrated_from"] = str(source_root)
    migrated_spec["migrated_at"] = utc_now()
    migrated_spec["legacy_task_root"] = str(old_task_dir)
    migrated_spec["legacy_bridge_mode"] = bridge_mode
    if legacy_stub:
        migrated_spec["legacy_stub_path"] = legacy_stub
    atomic_write_json(destination / "state.json", migrated_state)
    atomic_write_json(destination / "spec.json", migrated_spec)

    return {
        "task_id": task_id,
        "active": active,
        "source_root": str(source_root),
        "destination": str(destination),
        "bridge_mode": bridge_mode,
        "legacy_stub": legacy_stub,
    }


def command_migrate_legacy(args: argparse.Namespace) -> int:
    config = build_config(args)
    ensure_dir(config.app_home)
    ensure_dir(config.tasks_root)
    ensure_dir(archive_root(config))
    try:
        explicit_roots = resolve_legacy_root_args(args.legacy_root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if explicit_roots:
        legacy_roots = tuple(path for path in explicit_roots if path != config.tasks_root.resolve())
    elif args.all_discovered:
        legacy_roots = config.legacy_task_roots
    else:
        print("Use --legacy-root <path> (repeatable) or --all-discovered.", file=sys.stderr)
        return 1
    moved: list[dict[str, Any]] = []
    skipped: list[str] = []

    for root in legacy_roots:
        for task_dir in sorted(root.iterdir()):
            if not task_dir.is_dir():
                continue
            try:
                moved.append(migrate_task_dir(config, source_root=root, task_dir=task_dir))
            except Exception as exc:
                skipped.append(f"{task_dir.name}: {exc}")

    print(
        json.dumps(
            {
                "global_app_home": str(config.app_home),
                "legacy_roots": [str(path) for path in legacy_roots],
                "moved": moved,
                "skipped": skipped,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not skipped else 1


def command_ps_training(args: argparse.Namespace) -> int:
    processes = list_training_processes(args.limit)
    if args.json:
        print(json.dumps(processes, ensure_ascii=False, indent=2))
        return 0
    print("PID      PPID     STAT ETIME      CPU%   MEM%   GPU_MB  CMD")
    print("-" * 120)
    for proc in processes:
        print(
            f"{str(proc['pid']).ljust(8)} {str(proc['ppid']).ljust(8)} {str(proc['stat']).ljust(4)} "
            f"{str(proc['etime']).ljust(10)} {str(proc['cpu_percent']).rjust(5)} "
            f"{str(proc['mem_percent']).rjust(6)} {str(proc['gpu_memory_mb']).rjust(7)}  {proc['cmd'][:80]}"
        )
        if args.show_cwd and proc.get("cwd"):
            print(f"  cwd: {proc['cwd']}")
    if not processes:
        print("(no matching training processes)")
    return 0


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def dashboard_short_time(raw_value: str) -> str:
    return format_timestamp_for_display(raw_value, pattern="%m-%d %H:%M")


def dashboard_trim(text: Any, width: int) -> str:
    raw = str(text or "").replace("\n", " ").strip()
    if width <= 0:
        return ""
    if len(raw) <= width:
        return raw
    if width <= 3:
        return raw[:width]
    return raw[: width - 3] + "..."


def dashboard_process_panel_mode(mode: str, height: int) -> str:
    normalized = str(mode or "auto").strip().lower() or "auto"
    if normalized != "auto":
        return normalized
    if height >= 34:
        return "hybrid"
    if height >= 28:
        return "gpu"
    return "off"


def join_dashboard_columns(items: list[str], *, columns: int, width: int, gap: int = 3) -> list[str]:
    if not items:
        return []
    columns = max(1, columns)
    usable_width = max(20, width)
    column_width = max(18, (usable_width - gap * (columns - 1)) // columns)
    lines: list[str] = []
    for start in range(0, len(items), columns):
        chunk = items[start : start + columns]
        padded = [dashboard_trim(item, column_width).ljust(column_width) for item in chunk]
        lines.append((" " * gap).join(padded).rstrip())
    return lines


def build_gpu_snapshot_lines(gpu_rows: list[dict[str, Any]], *, width: int) -> list[str]:
    lines = ["GPU Snapshot"]
    if not gpu_rows:
        lines.append("  (nvidia-smi unavailable)")
        return lines
    cards: list[str] = []
    for row in gpu_rows:
        name = dashboard_trim(str(row.get("name", "")), 18)
        util = int(row.get("gpu_util_percent", 0) or 0)
        used = int(row.get("memory_used_mb", 0) or 0)
        total = int(row.get("memory_total_mb", 0) or 0)
        cards.append(f"GPU{int(row.get('index', 0))} {name} util={util:>3}% mem={used:>5}/{total:<5}MB")
    columns = min(len(cards), 2 if width >= 100 else 1)
    lines.extend(join_dashboard_columns(cards, columns=columns, width=width))
    return lines


def list_external_process_hints(limit: int, *, mode: str) -> list[dict[str, Any]]:
    normalized_mode = str(mode or "off").strip().lower()
    if normalized_mode == "off":
        return []
    completed = run_subprocess(
        ["ps", "-eo", "pid=,ppid=,stat=,etime=,%cpu=,%mem=,args="],
        timeout=20,
    )
    if completed.returncode != 0:
        return []
    gpu_table = get_gpu_process_table()
    processes: list[dict[str, Any]] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            cpu_percent = float(parts[4])
            mem_percent = float(parts[5])
        except ValueError:
            continue
        cmd = parts[6]
        seen_gpu = pid in gpu_table
        seen_training = looks_like_training_command(cmd)
        if normalized_mode == "gpu" and not seen_gpu:
            continue
        if normalized_mode == "training" and not seen_training:
            continue
        if normalized_mode == "hybrid" and not (seen_gpu or seen_training):
            continue
        source = "both" if seen_gpu and seen_training else "gpu" if seen_gpu else "train"
        processes.append(
            {
                "pid": pid,
                "ppid": ppid,
                "stat": parts[2],
                "etime": parts[3],
                "cpu_percent": cpu_percent,
                "mem_percent": mem_percent,
                "gpu_memory_mb": gpu_table.get(pid, 0),
                "cmd": cmd,
                "cwd": read_pid_cwd(pid),
                "proc_state": read_pid_state(pid),
                "source": source,
            }
        )
    processes.sort(key=lambda item: (-int(item["gpu_memory_mb"]), -float(item["cpu_percent"]), item["pid"]))
    return processes[:limit]


def build_process_hint_lines(processes: list[dict[str, Any]], *, width: int) -> list[str]:
    if not processes:
        return []
    lines = ["External Process Hints", "SRC   PID      ETIME      CPU%   GPU_MB  CMD"]
    for proc in processes:
        lines.append(
            f"{str(proc.get('source', '')).ljust(5)} "
            f"{str(proc['pid']).ljust(8)} "
            f"{str(proc['etime']).ljust(10)} "
            f"{str(round(float(proc['cpu_percent']), 1)).rjust(5)} "
            f"{str(proc['gpu_memory_mb']).rjust(7)}  "
            f"{dashboard_trim(proc['cmd'], max(24, width - 40))}"
        )
    lines.append("  note: heuristic panel only; scheduler truth comes from taskboard state plus GPU snapshot")
    return lines


def dashboard_issue_text(state: dict[str, Any]) -> str:
    return dashboard_issue_text_impl(state)


def build_dashboard_task_entries(
    config: AppConfig,
    states: list[dict[str, Any]],
    *,
    sort_mode: str,
    status_filter: str,
    agent_filter: str,
    limit: int,
    already_ordered: bool = False,
) -> list[dict[str, Any]]:
    return build_dashboard_task_entries_impl(
        config,
        states,
        sort_mode=sort_mode,
        status_filter=status_filter,
        agent_filter=agent_filter,
        limit=limit,
        already_ordered=already_ordered,
        hooks=task_dashboard_hooks(),
    )


def build_selected_task_detail_lines(
    config: AppConfig,
    state: dict[str, Any] | None,
    *,
    followup: dict[str, Any] | None,
    width: int,
    max_lines: int,
) -> list[str]:
    lines = ["Selected Task"]
    if not state:
        lines.append("  (no visible task selected)")
        return lines[:max_lines]
    assigned_gpus = ",".join(str(item) for item in parse_gpu_id_list(state.get("assigned_gpus", []))) or "-"
    cpu_profile = str(state.get("cpu_profile_resolved", state.get("cpu_profile", "auto")) or "auto")
    cpu_threads = int(state.get("assigned_cpu_threads", 0) or 0) or int(state.get("cpu_threads", 0) or 0)
    cpu_workers = int(state.get("assigned_cpu_workers", 0) or 0) or int(state.get("cpu_workers", 0) or 0)
    cpu_budget = cpu_threads + cpu_workers
    lines.append(
        dashboard_trim(
            f"  task={state.get('task_id')} | status={state.get('status')} | phase={state.get('phase')} | priority={state.get('priority')} | agent={state.get('agent_name')}",
            width - 1,
        )
    )
    lines.append(dashboard_trim(f"  cmd: {state.get('command', '')}", width - 1))
    lines.append(
        dashboard_trim(
            f"  cpu: profile={cpu_profile} mode={state.get('cpu_threads_mode', 'fixed')} budget={cpu_budget} threads={cpu_threads} workers={cpu_workers} retry={int(state.get('cpu_retry_attempts', 0) or 0)}/{int(state.get('cpu_retry_max_attempts', 0) or 0)}",
            width - 1,
        )
    )
    lines.append(
        dashboard_trim(
            f"  cpu-range: threads={int(state.get('cpu_threads_min', 0) or 0)}-{int(state.get('cpu_threads_max', 0) or 0)} workers={int(state.get('cpu_workers_min', 0) or 0)}-{int(state.get('cpu_workers_max', 0) or 0)} source={state.get('cpu_thread_source', '-')}/{state.get('cpu_worker_source', '-')}",
            width - 1,
        )
    )
    lines.append(
        dashboard_trim(
            f"  gpu: slots={int(state.get('gpu_slots', 0) or 0)} assigned={assigned_gpus} source={state.get('gpu_assignment_source', '') or '-'}",
            width - 1,
        )
    )
    if state.get("blocked_reason"):
        lines.append(dashboard_trim(f"  blocked: {state.get('blocked_reason')}", width - 1))
    followup_status = str(state.get("followup_status", "")).strip()
    followup_last_signal = str(state.get("followup_last_signal", "")).strip()
    if followup_status or followup_last_signal:
        lines.append(
            dashboard_trim(
                f"  followup-state: status={followup_status or '-'} signal={followup_last_signal or '-'} notification_signal={state.get('notification_signal', '') or '-'}",
                width - 1,
            )
        )
    session_id = str(state.get("codex_session_id", "")).strip()
    if session_id:
        session_mode = automation_mode_label(config, codex_session_id=session_id)
        backlog = reflow_backlog_summary(config, codex_session_id=session_id)
        lines.append(
            dashboard_trim(
                f"  automation: mode={session_mode} backlog_events={int(backlog.get('queue_depth', 0) or 0)} backlog_followups={int(backlog.get('followup_count', 0) or 0)}",
                width - 1,
            )
        )
    if state.get("attention_message"):
        lines.append(dashboard_trim(f"  note: {state.get('attention_message')}", width - 1))
    if state.get("failure_excerpt"):
        lines.append(dashboard_trim(f"  error: {str(state.get('failure_excerpt')).splitlines()[0]}", width - 1))
    elif state.get("report_summary"):
        lines.append(dashboard_trim(f"  report: {state.get('report_summary')}", width - 1))
    dependency_resolution = state.get("dependency_resolution", []) if isinstance(state.get("dependency_resolution", []), list) else []
    waiting = [str(item.get("task_key", "")) for item in dependency_resolution if not bool(item.get("satisfied", False))]
    if waiting:
        lines.append(dashboard_trim(f"  deps: waiting for {', '.join(waiting[:6])}", width - 1))
    if followup:
        next_followup_ts = float(followup.get("check_after_ts", 0) or 0)
        next_followup_at = (
            format_timestamp_for_display(next_followup_ts, pattern="%m-%d %H:%M:%S")
            if next_followup_ts > 0
            else "-"
        )
        lines.append(
            dashboard_trim(
                f"  followup: next={next_followup_at} nudges={int(followup.get('nudge_count', 0) or 0)} last_action={followup.get('last_action', '-')}",
                width - 1,
            )
        )
    return lines[:max_lines]


def collect_dashboard_snapshot(
    config: AppConfig,
    states: list[dict[str, Any]],
    *,
    height: int,
    process_panel_mode: str = "auto",
) -> dict[str, Any]:
    counts = Counter(str(item.get("status", "unknown")) for item in states)
    running_live = count_live_running_tasks(config, states)
    active_states = [item for item in states if str(item.get("status", "")) in ACTIVE_TASK_STATUSES]
    cpu_thread_limit = detect_default_cpu_thread_limit()
    active_cpu_threads = sum(task_requested_cpu_budget(merged_spec_with_state(config, item)) for item in active_states)
    gpu_rows = get_gpu_summary_table()
    total_gpu_slots = detect_gpu_count() or len(gpu_rows)
    followups = followup_map_by_task_id(config)
    pending_feedback_count = sum(1 for item in states if item.get("pending_feedback", False))
    latest_states_index = latest_task_states_by_key(states)
    enriched_states = [
        enrich_task_state(
            config,
            item,
            gpu_rows=gpu_rows,
            total_gpu_slots=total_gpu_slots,
            active_cpu_threads=active_cpu_threads,
            cpu_thread_limit=cpu_thread_limit,
            latest_states_by_key=latest_states_index,
        )
        for item in states
    ]
    resolved_process_panel_mode = dashboard_process_panel_mode(process_panel_mode, height)
    process_limit = 4 if height >= 34 else 2
    process_hints = list_external_process_hints(process_limit, mode=resolved_process_panel_mode)
    return {
        "counts": counts,
        "running_live": running_live,
        "active_states": active_states,
        "cpu_thread_limit": cpu_thread_limit,
        "active_cpu_threads": active_cpu_threads,
        "gpu_rows": gpu_rows,
        "total_gpu_slots": total_gpu_slots,
        "followups": followups,
        "pending_feedback_count": pending_feedback_count,
        "enriched_states": enriched_states,
        "process_hints": process_hints,
        "process_panel_mode": resolved_process_panel_mode,
    }


def build_dashboard_view_from_snapshot(
    config: AppConfig,
    snapshot: dict[str, Any],
    limit: int,
    *,
    width: int,
    height: int,
    sort_mode: str = "queue",
    status_filter: str = "all",
    agent_filter: str = "all",
    selected_task_id: str = "",
    selected_index: int | None = None,
) -> dict[str, Any]:
    counts = snapshot["counts"]
    running_live = int(snapshot["running_live"])
    active_states = list(snapshot["active_states"])
    cpu_thread_limit = int(snapshot["cpu_thread_limit"])
    active_cpu_threads = int(snapshot["active_cpu_threads"])
    gpu_rows = list(snapshot["gpu_rows"])
    total_gpu_slots = int(snapshot["total_gpu_slots"])
    followups = dict(snapshot["followups"])
    pending_feedback_count = int(snapshot["pending_feedback_count"])
    enriched_states = list(snapshot["enriched_states"])
    process_hints = list(snapshot["process_hints"])
    resolved_process_panel_mode = str(snapshot["process_panel_mode"])
    automation_mode_name = automation_mode_label(config)
    backlog_summary = reflow_backlog_summary(config, followups=list(followups.values()) if isinstance(followups, dict) else None)
    visible_states = sort_dashboard_tasks(
        config,
        filter_dashboard_tasks(config, enriched_states, status_filter=status_filter, agent_filter=agent_filter),
        sort_mode,
    )
    header_lines = [
        dashboard_trim(
            f"codex-taskboard | {format_timestamp_for_display(time.time(), pattern='%Y-%m-%d %H:%M:%S')} | app_home={config.app_home}",
            width - 1,
        ),
        dashboard_trim(
            f"[tasks {len(enriched_states)}] [visible {len(visible_states)}] [queued {counts.get('queued', 0) + counts.get('submitted', 0)}] [active {running_live}] [pending {pending_feedback_count}] [done {counts.get('completed', 0) + counts.get('observed_exit', 0)}] [failed {counts.get('failed', 0)}] [terminated {counts.get('terminated', 0)}] [launch {counts.get('launch_failed', 0)}]",
            width - 1,
        ),
        dashboard_trim(
            f"[gpu slots {sum(int(item.get('gpu_slots', 0) or 0) for item in active_states)}/{total_gpu_slots or '-'}] [cpu budget {active_cpu_threads}/{cpu_thread_limit}] [sort {sort_mode}] [filter {status_filter}] [agent {agent_filter}] [process {resolved_process_panel_mode}] [mode {automation_mode_name}] [backlog {int(backlog_summary.get('queue_depth', 0) or 0)}]",
            width - 1,
        ),
    ]
    gpu_lines = build_gpu_snapshot_lines(gpu_rows, width=width)
    process_lines = build_process_hint_lines(process_hints, width=width)
    task_entries = build_dashboard_task_entries(
        config,
        visible_states,
        sort_mode=sort_mode,
        status_filter="all",
        agent_filter="all",
        limit=limit,
        already_ordered=True,
    )
    normalized_selected_index = 0
    if task_entries:
        if selected_index is not None:
            normalized_selected_index = max(0, min(int(selected_index), len(task_entries) - 1))
            selected_entry = task_entries[normalized_selected_index]
        else:
            selected_entry = next((entry for entry in task_entries if entry["task_id"] == selected_task_id), task_entries[0])
            normalized_selected_index = next(
                (index for index, entry in enumerate(task_entries) if entry["task_id"] == selected_entry["task_id"]),
                0,
            )
    else:
        selected_entry = None
    selected_state = selected_entry["state"] if selected_entry else None
    detail_budget = 7 if height >= 32 else 5 if height >= 24 else 3
    detail_lines = build_selected_task_detail_lines(
        config,
        selected_state,
        followup=followups.get(selected_entry["task_id"]) if selected_entry else None,
        width=width,
        max_lines=detail_budget,
    )
    task_header = "TASK ID                      STATUS      PRI AGENT           GPU CPUB FBK     UPDATED     ISSUE"
    process_hint_note = []
    if not process_lines and resolved_process_panel_mode != "off":
        process_hint_note = ["External Process Hints", "  (no matching external GPU/training processes in current heuristic scan)"]
    return {
        "header_lines": header_lines,
        "gpu_lines": gpu_lines,
        "process_lines": process_lines or process_hint_note,
        "task_header": task_header,
        "task_entries": task_entries,
        "detail_lines": detail_lines,
        "selected_task_id": selected_entry["task_id"] if selected_entry else "",
        "selected_index": normalized_selected_index,
        "process_panel_mode": resolved_process_panel_mode,
        "automation_mode": automation_mode_name,
        "reflow_backlog": backlog_summary,
    }


def build_dashboard_view(
    config: AppConfig,
    states: list[dict[str, Any]],
    limit: int,
    *,
    width: int,
    height: int,
    sort_mode: str = "queue",
    status_filter: str = "all",
    agent_filter: str = "all",
    selected_task_id: str = "",
    process_panel_mode: str = "auto",
) -> dict[str, Any]:
    snapshot = collect_dashboard_snapshot(config, states, height=height, process_panel_mode=process_panel_mode)
    return build_dashboard_view_from_snapshot(
        config,
        snapshot,
        limit,
        width=width,
        height=height,
        sort_mode=sort_mode,
        status_filter=status_filter,
        agent_filter=agent_filter,
        selected_task_id=selected_task_id,
    )


def format_dashboard_task_entry(entry: dict[str, Any], *, selected: bool = False, width: int = 132) -> str:
    marker = ">" if selected else " "
    issue_width = max(8, width - 93)
    return (
        f"{marker}"
        f"{dashboard_trim(entry['task_id'], 28).ljust(28)} "
        f"{dashboard_trim(entry['status'], 11).ljust(11)} "
        f"{str(entry['priority']).rjust(3)} "
        f"{dashboard_trim(entry['agent_name'], 15).ljust(15)} "
        f"{dashboard_trim(entry['gpu_text'], 3).rjust(3)} "
        f"{dashboard_trim(entry['cpu_text'], 4).rjust(4)} "
        f"{dashboard_trim(entry['feedback_mode'], 7).ljust(7)} "
        f"{dashboard_trim(entry['updated_text'], 11).ljust(11)} "
        f"{dashboard_trim(entry['issue_text'], issue_width)}"
    ).rstrip()


def dashboard_lines_from_view(view: dict[str, Any], *, width: int) -> list[str]:
    lines = list(view["header_lines"])
    lines.append("")
    lines.extend(view["gpu_lines"])
    if view["process_lines"]:
        lines.append("")
        lines.extend(view["process_lines"])
    lines.extend(["", "Taskboard", view["task_header"]])
    for entry in view["task_entries"]:
        lines.append(format_dashboard_task_entry(entry, selected=entry["task_id"] == view["selected_task_id"], width=width))
    lines.extend(["", *view["detail_lines"]])
    return lines


def build_dashboard_lines(
    config: AppConfig,
    states: list[dict[str, Any]],
    limit: int,
    *,
    sort_mode: str = "queue",
    status_filter: str = "all",
    agent_filter: str = "all",
    selected_task_id: str = "",
    width: int = 132,
    height: int = 40,
    process_panel_mode: str = "auto",
) -> list[str]:
    view = build_dashboard_view(
        config,
        states,
        limit,
        width=width,
        height=height,
        sort_mode=sort_mode,
        status_filter=status_filter,
        agent_filter=agent_filter,
        selected_task_id=selected_task_id,
        process_panel_mode=process_panel_mode,
    )
    return dashboard_lines_from_view(view, width=width)


def init_dashboard_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    color_pairs = {
        1: (curses.COLOR_CYAN, -1),
        2: (curses.COLOR_GREEN, -1),
        3: (curses.COLOR_YELLOW, -1),
        4: (curses.COLOR_RED, -1),
        5: (curses.COLOR_MAGENTA, -1),
        6: (curses.COLOR_WHITE, -1),
        7: (curses.COLOR_BLUE, -1),
        8: (curses.COLOR_CYAN, -1),
        9: (curses.COLOR_GREEN, -1),
        10: (curses.COLOR_YELLOW, -1),
        11: (curses.COLOR_MAGENTA, -1),
    }
    for pair_id, (fg, bg) in color_pairs.items():
        curses.init_pair(pair_id, fg, bg)


def init_dashboard_input_tuning() -> None:
    set_escdelay = getattr(curses, "set_escdelay", None)
    if callable(set_escdelay):
        try:
            set_escdelay(25)
        except curses.error:
            pass


def safe_dashboard_addnstr(stdscr: Any, row: int, col: int, text: str, max_chars: int, attr: int = 0) -> None:
    try:
        max_y, max_x = stdscr.getmaxyx()
    except Exception:
        return
    if max_y <= 0 or max_x <= 0:
        return
    if row < 0 or row >= max_y or col < 0 or col >= max_x:
        return
    # Avoid the bottom-right cell because many curses backends report ERR there.
    available = max_x - col - (1 if row == max_y - 1 else 0)
    if available <= 0:
        return
    count = max(0, min(int(max_chars), available))
    if count <= 0:
        return
    clipped = dashboard_trim(str(text), count)
    if not clipped:
        return
    try:
        stdscr.addnstr(row, col, clipped, count, attr)
    except curses.error:
        if count <= 1:
            return
        try:
            stdscr.addnstr(row, col, dashboard_trim(clipped, count - 1), count - 1, attr)
        except curses.error:
            return


def status_color_attr(status: str) -> int:
    status = status.strip().lower()
    if status in {"running", "watching"}:
        return curses.color_pair(1) | curses.A_BOLD
    if status in {"completed", "observed_exit"}:
        return curses.color_pair(2) | curses.A_BOLD
    if status in {"queued", "submitted"}:
        return curses.color_pair(3) | curses.A_BOLD
    if status in {"failed", "launch_failed", "terminated"}:
        return curses.color_pair(4) | curses.A_BOLD
    return curses.color_pair(6)


def feedback_color_attr(feedback_mode: str) -> int:
    mode = feedback_mode.strip().lower()
    if mode == "auto":
        return curses.color_pair(2)
    if mode == "manual":
        return curses.color_pair(5)
    if mode == "off":
        return curses.color_pair(4)
    return curses.color_pair(6)


def agent_color_attr(agent_name: str) -> int:
    agent = agent_name.strip()
    if not agent:
        return curses.color_pair(6)
    palette = [7, 8, 9, 10, 11]
    index = sum(ord(ch) for ch in agent) % len(palette)
    return curses.color_pair(palette[index]) | curses.A_BOLD


def render_dashboard_task_entry(stdscr: Any, row: int, width: int, entry: dict[str, Any], *, selected: bool) -> None:
    line = format_dashboard_task_entry(entry, selected=selected, width=width)
    selected_attr = curses.A_REVERSE if selected else 0
    positions = [
        (0, 1, curses.color_pair(3) | curses.A_BOLD),
        (1, 29, curses.color_pair(6)),
        (30, 41, status_color_attr(entry["status"])),
        (42, 45, curses.color_pair(3)),
        (46, 61, agent_color_attr(entry["agent_name"])),
        (62, 65, curses.color_pair(1)),
        (66, 70, curses.color_pair(8)),
        (71, 78, feedback_color_attr(entry["feedback_mode"])),
        (79, 90, curses.color_pair(6)),
        (91, len(line), curses.color_pair(4) | curses.A_BOLD if any(token in entry["issue_text"].lower() for token in ("failed", "terminated", "blocked", "traceback", "attention")) else curses.color_pair(2) if entry["status"] in {"completed", "observed_exit"} else curses.color_pair(7)),
    ]
    for start, end, attr in positions:
        if start >= width - 1:
            break
        text = line[start:end]
        if not text:
            continue
        safe_dashboard_addnstr(stdscr, row, start, text, max(0, width - 1 - start), attr | selected_attr)


def render_dashboard_line(stdscr: Any, row: int, width: int, line: str) -> None:
    if line.startswith("codex-taskboard |"):
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(1) | curses.A_BOLD)
        return
    if line in {"GPU Snapshot", "External Process Hints", "Taskboard", "Selected Task"}:
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(1) | curses.A_BOLD)
        return
    if line.startswith("[tasks ") or line.startswith("[gpu slots "):
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(8) | curses.A_BOLD)
        return
    if line.startswith("SRC   PID") or line.startswith("TASK ID"):
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(6) | curses.A_BOLD)
        return
    if line.startswith("  note:"):
        attr = curses.color_pair(5) | curses.A_BOLD if "feedback pending" in line.lower() else curses.color_pair(4) | curses.A_BOLD
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), attr)
        return
    if line.startswith("  task="):
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(3) | curses.A_BOLD)
        return
    if line.startswith("  deps:"):
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(3) | curses.A_BOLD)
        return
    if line.startswith("  cpu:") or line.startswith("  gpu:"):
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(8))
        return
    if line.startswith("  report:"):
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(2))
        return
    if line.startswith("  signal:"):
        attr = curses.color_pair(2) | curses.A_BOLD if any(sig in line for sig in SUCCESS_TASKBOARD_SIGNALS) else curses.color_pair(5) | curses.A_BOLD
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), attr)
        return
    if line.startswith("  followup:"):
        attr = curses.color_pair(3) | curses.A_BOLD if "deferred_recent_activity=true" in line else curses.color_pair(8)
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), attr)
        return
    if line.startswith("  cmd:"):
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(7))
        return
    if line.startswith("GPU") and "util=" in line and "mem=" in line:
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(2) | curses.A_BOLD)
        return
    if line.startswith("both ") or line.startswith("gpu  ") or line.startswith("train"):
        safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(7))
        return
    safe_dashboard_addnstr(stdscr, row, 0, line, max(0, width - 1), curses.color_pair(6))


def read_dashboard_input_key(stdscr: Any, *, poll_ms: int) -> int:
    key = stdscr.getch()
    if key != 27:
        return key
    sequence: list[int] = []
    stdscr.nodelay(True)
    try:
        for _ in range(4):
            next_key = stdscr.getch()
            if next_key == -1:
                break
            sequence.append(next_key)
    finally:
        stdscr.nodelay(False)
        stdscr.timeout(poll_ms)
    if not sequence:
        return 27
    if sequence[:2] == [ord("["), ord("A")] or sequence[:2] == [ord("O"), ord("A")]:
        return curses.KEY_UP
    if sequence[:2] == [ord("["), ord("B")] or sequence[:2] == [ord("O"), ord("B")]:
        return curses.KEY_DOWN
    if sequence[:2] == [ord("["), ord("C")] or sequence[:2] == [ord("O"), ord("C")]:
        return curses.KEY_RIGHT
    if sequence[:2] == [ord("["), ord("D")] or sequence[:2] == [ord("O"), ord("D")]:
        return curses.KEY_LEFT
    if sequence[:3] == [ord("["), ord("5"), ord("~")]:
        return curses.KEY_PPAGE
    if sequence[:3] == [ord("["), ord("6"), ord("~")]:
        return curses.KEY_NPAGE
    if sequence[:2] == [ord("["), ord("H")] or sequence[:2] == [ord("O"), ord("H")]:
        return curses.KEY_HOME
    if sequence[:2] == [ord("["), ord("F")] or sequence[:2] == [ord("O"), ord("F")]:
        return curses.KEY_END
    return 27


def draw_dashboard(stdscr: Any, config: AppConfig, limit: int, refresh_seconds: float, process_panel_mode: str) -> None:
    init_dashboard_colors()
    init_dashboard_input_tuning()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    sort_modes = ["queue", "priority", "updated", "agent", "status"]
    status_filters = ["all", "active", "queued", "attention", "pending", "done"]
    process_panel_modes = ["auto", "off", "gpu", "hybrid", "training"]
    sort_index = 0
    status_filter_index = 0
    agent_filter = "all"
    selected_index = 0
    task_scroll = 0
    process_mode_index = process_panel_modes.index(process_panel_mode) if process_panel_mode in process_panel_modes else 0
    paused = False
    snapshot: dict[str, Any] | None = None
    snapshot_height = 0
    snapshot_process_mode = ""
    next_refresh_at = 0.0
    force_refresh = True
    last_input_at = 0.0
    input_poll_ms = 50
    show_backlog = False
    while True:
        height, width = stdscr.getmaxyx()
        width = max(2, width)
        current_process_mode = process_panel_modes[process_mode_index]
        if (
            force_refresh
            or snapshot is None
            or height != snapshot_height
            or current_process_mode != snapshot_process_mode
        ):
            states = iter_task_states(config)
            snapshot = collect_dashboard_snapshot(
                config,
                states,
                height=height,
                process_panel_mode=current_process_mode,
            )
            snapshot_height = height
            snapshot_process_mode = current_process_mode
            next_refresh_at = time.monotonic() + max(0.2, refresh_seconds)
            force_refresh = False
        view = build_dashboard_view_from_snapshot(
            config,
            snapshot,
            limit,
            width=width,
            height=height,
            sort_mode=sort_modes[sort_index],
            status_filter=status_filters[status_filter_index],
            agent_filter=agent_filter,
            selected_index=selected_index,
        )
        task_entries = view["task_entries"]
        selected_index = int(view.get("selected_index", 0) or 0)
        selected_task_id = str(view.get("selected_task_id", ""))
        top_lines = list(view["header_lines"]) + [""] + list(view["gpu_lines"])
        backlog_summary = view.get("reflow_backlog", {}) if isinstance(view.get("reflow_backlog", {}), dict) else {}
        if show_backlog:
            top_lines.extend(
                [
                    "",
                    "Reflow Backlog",
                    dashboard_trim(
                        "  followups={followups} events={events} oldest={oldest} latest={latest}".format(
                            followups=int(backlog_summary.get("followup_count", 0) or 0),
                            events=int(backlog_summary.get("queue_depth", 0) or 0),
                            oldest=str(backlog_summary.get("oldest_event_at", "") or "-"),
                            latest=str(backlog_summary.get("latest_event_at", "") or "-"),
                        ),
                        width - 1,
                    ),
                ]
            )
        if view["process_lines"]:
            top_lines.extend([""] + list(view["process_lines"]))
        detail_lines = ["", *view["detail_lines"]]
        fixed_height = len(top_lines) + 2 + len(detail_lines)
        task_area_height = max(1, height - 1 - fixed_height)
        if selected_index < task_scroll:
            task_scroll = selected_index
        elif selected_index >= task_scroll + task_area_height:
            task_scroll = selected_index - task_area_height + 1
        max_scroll = max(0, len(task_entries) - task_area_height)
        task_scroll = max(0, min(task_scroll, max_scroll))
        visible_entries = task_entries[task_scroll : task_scroll + task_area_height]
        stdscr.erase()
        row = 0
        for line in top_lines[: max(0, height - 1)]:
            render_dashboard_line(stdscr, row, width, line)
            row += 1
        if row < height - 1:
            render_dashboard_line(stdscr, row, width, "Taskboard")
            row += 1
        if row < height - 1:
            render_dashboard_line(stdscr, row, width, view["task_header"])
            row += 1
        for index, entry in enumerate(visible_entries):
            if row >= height - 1:
                break
            absolute_index = task_scroll + index
            render_dashboard_task_entry(stdscr, row, width, entry, selected=absolute_index == selected_index)
            row += 1
        for line in detail_lines:
            if row >= height - 1:
                break
            render_dashboard_line(stdscr, row, width, line)
            row += 1
        footer = (
            f"q quit | SPACE pause={'on' if paused else 'off'} | j/k move | PgUp/PgDn page | g/G top/bottom | "
            f"s sort | f filter | [/] agent | p process | +/- priority | c mode={view.get('automation_mode', 'managed')} | "
            f"b backlog={'on' if show_backlog else 'off'} | x clear-backlog"
        )
        safe_dashboard_addnstr(stdscr, height - 1, 0, dashboard_trim(footer, width - 1), max(0, width - 1), curses.color_pair(6) | curses.A_BOLD)
        stdscr.refresh()
        stdscr.timeout(input_poll_ms)
        key = read_dashboard_input_key(stdscr, poll_ms=input_poll_ms)
        if key == -1:
            now = time.monotonic()
            if not paused and now >= next_refresh_at:
                if now - last_input_at < 0.25:
                    next_refresh_at = now + 0.25
                else:
                    force_refresh = True
            continue
        if key == ord("q"):
            return
        last_input_at = time.monotonic()
        if key == ord(" "):
            paused = not paused
            continue
        if key == ord("r"):
            force_refresh = True
            continue
        if key == ord("c"):
            target_session_id, _ = resolve_continuous_research_target_session_id(config)
            toggle_automation_mode(
                config,
                codex_session_id=target_session_id,
                updated_by="dashboard",
                source="dashboard_hotkey",
            )
            force_refresh = True
            continue
        if key == ord("b"):
            show_backlog = not show_backlog
            continue
        if key == ord("x"):
            target_session_id, _ = resolve_continuous_research_target_session_id(config)
            if target_session_id:
                clear_reflow_backlog(config, codex_session_id=target_session_id)
                force_refresh = True
            continue
        if key in {ord("j"), curses.KEY_DOWN} and task_entries:
            selected_index = min(len(task_entries) - 1, selected_index + 1)
            continue
        if key in {ord("k"), curses.KEY_UP} and task_entries:
            selected_index = max(0, selected_index - 1)
            continue
        if key in {curses.KEY_NPAGE} and task_entries:
            selected_index = min(len(task_entries) - 1, selected_index + max(1, task_area_height - 1))
            continue
        if key in {curses.KEY_PPAGE} and task_entries:
            selected_index = max(0, selected_index - max(1, task_area_height - 1))
            continue
        if key in {ord("g"), curses.KEY_HOME} and task_entries:
            selected_index = 0
            continue
        if key in {ord("G"), curses.KEY_END} and task_entries:
            selected_index = len(task_entries) - 1
            continue
        if key == ord("s"):
            sort_index = (sort_index + 1) % len(sort_modes)
            task_scroll = 0
            selected_index = 0
            continue
        if key == ord("f"):
            status_filter_index = (status_filter_index + 1) % len(status_filters)
            selected_index = 0
            task_scroll = 0
            continue
        if key in {ord("]"), ord("[")}:
            enriched_states = snapshot.get("enriched_states", []) if isinstance(snapshot, dict) else []
            agent_names = sorted({str(state.get("agent_name", "")) for state in enriched_states if str(state.get("agent_name", ""))})
            options = ["all"] + agent_names
            current_index = options.index(agent_filter) if agent_filter in options else 0
            step = 1 if key == ord("]") else -1
            agent_filter = options[(current_index + step) % len(options)]
            selected_index = 0
            task_scroll = 0
            continue
        if key == ord("p"):
            process_mode_index = (process_mode_index + 1) % len(process_panel_modes)
            force_refresh = True
            continue
        if key in {ord("+"), ord("="), ord("-"), ord("_")} and selected_task_id:
            delta = 10 if key in {ord("+"), ord("=")} else -10
            update_task_priority(config, selected_task_id, current_task_priority(config, selected_task_id) + delta)
            force_refresh = True
            continue


def run_plain_dashboard(config: AppConfig, *, limit: int, refresh_seconds: float, process_panel_mode: str, once: bool) -> None:
    snapshot: dict[str, Any] | None = None
    snapshot_height = 0
    while True:
        width = shutil.get_terminal_size((132, 40)).columns
        height = shutil.get_terminal_size((132, 40)).lines
        if snapshot is None or snapshot_height != height:
            states = iter_task_states(config)
            snapshot = collect_dashboard_snapshot(config, states, height=height, process_panel_mode=process_panel_mode)
            snapshot_height = height
        view = build_dashboard_view_from_snapshot(
            config,
            snapshot,
            limit,
            width=width,
            height=height,
            sort_mode="queue",
            status_filter="all",
            agent_filter="all",
        )
        lines = dashboard_lines_from_view(view, width=width)
        if once or not sys.stdout.isatty():
            print("\n".join(lines))
            return
        print("\x1b[2J\x1b[H" + "\n".join(lines), end="", flush=True)
        time.sleep(max(0.2, refresh_seconds))
        states = iter_task_states(config)
        snapshot = collect_dashboard_snapshot(config, states, height=height, process_panel_mode=process_panel_mode)


def command_dashboard(args: argparse.Namespace) -> int:
    config = build_config(args)
    render_mode = str(getattr(args, "render_mode", "auto") or "auto").strip().lower()
    if render_mode == "auto":
        render_mode = "curses" if sys.stdout.isatty() and sys.stdin.isatty() and not args.once else "plain"
    try:
        if render_mode == "plain":
            run_plain_dashboard(
                config,
                limit=args.limit,
                refresh_seconds=args.refresh_seconds,
                process_panel_mode=getattr(args, "process_panel", "auto"),
                once=args.once or not sys.stdout.isatty(),
            )
            return 0
        curses.wrapper(draw_dashboard, config, args.limit, args.refresh_seconds, getattr(args, "process_panel", "auto"))
        return 0
    except curses.error as exc:
        if render_mode == "curses":
            print(f"[codex-taskboard] curses dashboard failed, falling back to plain mode: {exc}", file=sys.stderr)
            try:
                run_plain_dashboard(
                    config,
                    limit=args.limit,
                    refresh_seconds=args.refresh_seconds,
                    process_panel_mode=getattr(args, "process_panel", "auto"),
                    once=args.once or not sys.stdout.isatty(),
                )
                return 0
            except KeyboardInterrupt:
                return 130
        raise
    except KeyboardInterrupt:
        return 130


def command_run(args: argparse.Namespace) -> int:
    config = build_config(args)
    spec_path = Path(args.spec_file).expanduser().resolve()
    spec = read_json(spec_path, {})
    if not isinstance(spec, dict) or not spec:
        print(f"Invalid spec file: {spec_path}", file=sys.stderr)
        return 1
    task_id = str(spec["task_id"])
    ensure_task_layout(config, task_id)
    append_log(task_runner_log_path(config, task_id), "runner_started")
    merge_task_state(
        config,
        task_id,
        status="running",
        started_at=utc_now(),
        pid=os.getpid(),
        workdir=spec["workdir"],
        remote_workdir=spec.get("remote_workdir", ""),
        command=spec["command"],
        codex_session_id=spec["codex_session_id"],
        executor_name=spec.get("executor_name", ""),
    )
    command_log_path = task_command_log_path(config, task_id)
    runner_log_path = task_runner_log_path(config, task_id)
    last_message_path = task_last_message_path(config, task_id)
    started_ts = time.time()

    if spec.get("execution_mode") == "codex_subagent":
        with command_log_path.open("a", encoding="utf-8") as command_log:
            command_log.write(f"[{utc_now()}] subagent_started task_id={task_id}\n")
            command_log.write(f"workdir={spec['workdir']}\n")
            command_log.write(f"subagent_model={spec.get('subagent_model', 'gpt-5.4')}\n")
            command_log.write(f"subagent_prompt={spec.get('subagent_prompt', '')}\n\n")
            command_log.flush()
        subagent_result = run_codex_subagent(config, spec)
        ended_ts = time.time()
        event = create_event_payload(
            config,
            spec,
            status=subagent_result["status"],
            started_at=started_ts,
            ended_at=ended_ts,
            exit_code=subagent_result["returncode"],
            exit_signal="",
            launch_error="",
        )
        event.update(
            {
                "subagent_session_id": subagent_result["subagent_session_id"],
                "subagent_message_written": subagent_result["subagent_message_written"],
                "subagent_last_message_path": str(subagent_last_message_path(config, task_id)),
                "subagent_last_message_excerpt": subagent_result["subagent_last_message_excerpt"],
                "subagent_model": subagent_result["subagent_model"],
                "continue_attempts": subagent_result["continue_attempts"],
                "recovered_with_continue": subagent_result["recovered_with_continue"],
                "subagent_stdout_tail": subagent_result["stdout_tail"],
                "subagent_stderr_tail": subagent_result["stderr_tail"],
                "taskboard_signal": subagent_result["taskboard_signal"],
            }
        )
        needs_attention, attention_reason, attention_message = compute_attention(event, spec)
        event["needs_attention"] = needs_attention
        event["attention_reason"] = attention_reason
        event["attention_message"] = attention_message
        event_path = write_event(config, task_id, event)
        merge_task_state(
            config,
            task_id,
            status=subagent_result["status"],
            ended_at=event["ended_at"],
            duration_seconds=event["duration_seconds"],
            exit_code=subagent_result["returncode"],
            exit_signal="",
            failure_kind=event["failure_kind"],
            failure_summary=event["failure_summary"],
            failure_excerpt=event.get("failure_excerpt", ""),
            needs_attention=needs_attention,
            attention_reason=attention_reason,
            attention_message=attention_message,
            report_summary=event.get("report_summary", ""),
            structured_report=event.get("structured_report", {}),
            taskboard_signal=subagent_result.get("taskboard_signal", ""),
            assigned_gpus=event.get("assigned_gpus", []),
            dispatch_gpu_snapshot=event.get("dispatch_gpu_snapshot", []),
            launch_gpu_snapshot=event.get("launch_gpu_snapshot", []),
            rejected_reason=event.get("rejected_reason", ""),
            subagent_session_id=subagent_result["subagent_session_id"],
            subagent_message_written=subagent_result["subagent_message_written"],
            subagent_model=subagent_result["subagent_model"],
            subagent_continue_attempts=subagent_result["continue_attempts"],
            subagent_recovered_with_continue=subagent_result["recovered_with_continue"],
            subagent_last_message_path=str(subagent_last_message_path(config, task_id)),
            last_event_path=str(event_path),
            last_message_path=str(last_message_path),
        )
        append_log(
            runner_log_path,
            f"subagent_stopped status={subagent_result['status']} returncode={subagent_result['returncode']} session_id={subagent_result['subagent_session_id']}",
        )
        notification = handle_task_feedback(config, task_id=task_id, spec=spec, event=event)
        event["notification"] = notification
        atomic_write_json(event_path, event)
        append_log(runner_log_path, f"notification_done ok={notification.get('ok', False)} resumed_session_id={notification.get('resumed_session_id')}")
        return 0 if subagent_result["status"] == "completed" else 1

    if spec.get("execution_mode") == "external_pid":
        watch_pid = int(spec["watch_pid"])
        append_log(runner_log_path, f"watch_pid_started pid={watch_pid}")
        merge_task_state(
            config,
            task_id,
            status="watching",
            started_at=utc_now(),
            watch_pid=watch_pid,
            execution_mode="external_pid",
            attached_cmd=read_pid_cmdline(watch_pid),
        )
        with command_log_path.open("a", encoding="utf-8") as command_log:
            command_log.write(f"[{utc_now()}] attached_pid_start pid={watch_pid}\n")
            command_log.write(f"cwd={read_pid_cwd(watch_pid)}\n")
            command_log.write(f"cmd={read_pid_cmdline(watch_pid)}\n\n")
            command_log.flush()
        while pid_exists(watch_pid):
            time.sleep(float(spec.get("watch_poll_seconds", 2.0)))
        ended_ts = time.time()
        event = create_event_payload(
            config,
            spec,
            status="observed_exit",
            started_at=started_ts,
            ended_at=ended_ts,
            exit_code=None,
            exit_signal="",
            launch_error="",
        )
        needs_attention, attention_reason, attention_message = compute_attention(event, spec)
        event["needs_attention"] = needs_attention
        event["attention_reason"] = attention_reason
        event["attention_message"] = attention_message
        event_path = write_event(config, task_id, event)
        merge_task_state(
            config,
            task_id,
            status="observed_exit",
            ended_at=event["ended_at"],
            duration_seconds=event["duration_seconds"],
            exit_code=None,
            exit_signal="",
            failure_kind=event["failure_kind"],
            failure_summary=event["failure_summary"],
            failure_excerpt=event.get("failure_excerpt", ""),
            needs_attention=needs_attention,
            attention_reason=attention_reason,
            attention_message=attention_message,
            report_summary=event.get("report_summary", ""),
            structured_report=event.get("structured_report", {}),
            taskboard_signal=event.get("taskboard_signal", ""),
            assigned_gpus=event.get("assigned_gpus", []),
            dispatch_gpu_snapshot=event.get("dispatch_gpu_snapshot", []),
            launch_gpu_snapshot=event.get("launch_gpu_snapshot", []),
            rejected_reason=event.get("rejected_reason", ""),
            last_event_path=str(event_path),
            last_message_path=str(last_message_path),
        )
        append_log(runner_log_path, f"watch_pid_stopped pid={watch_pid} status=observed_exit")
        notification = handle_task_feedback(config, task_id=task_id, spec=spec, event=event)
        event["notification"] = notification
        atomic_write_json(event_path, event)
        append_log(runner_log_path, f"notification_done ok={notification.get('ok', False)} resumed_session_id={notification.get('resumed_session_id')}")
        return 0

    child: subprocess.Popen[Any] | None = None
    received_signal = {"name": ""}

    def handle_signal(sig: int, _frame: Any) -> None:
        received_signal["name"] = signal.Signals(sig).name
        append_log(runner_log_path, f"received_signal={received_signal['name']}")
        if child is None or child.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(child.pid), sig)
        except ProcessLookupError:
            return

    for sig_name in ("SIGTERM", "SIGINT", "SIGHUP"):
        if hasattr(signal, sig_name):
            signal.signal(getattr(signal, sig_name), handle_signal)

    env = os.environ.copy()
    for key in PROPOSAL_ENV_KEYS:
        env.pop(key, None)
    for key in CLOSEOUT_PROPOSAL_DIR_ENV_KEYS:
        env.pop(key, None)
    launch_ok, launch_gpu_snapshot, rejected_reason = prelaunch_gpu_recheck(spec)
    if launch_gpu_snapshot:
        merge_task_state(config, task_id, launch_gpu_snapshot=launch_gpu_snapshot)
    if not launch_ok:
        deferred_event = {
            "version": VERSION,
            "task_id": task_id,
            "status": "launch_deferred",
            "ended_at": utc_now(),
            "assigned_gpus": parse_gpu_id_list(spec.get("assigned_gpus", [])),
            "selected_gpu_ids": parse_gpu_id_list(spec.get("assigned_gpus", [])),
            "dispatch_gpu_snapshot": load_task_state(config, task_id).get("dispatch_gpu_snapshot", []),
            "launch_gpu_snapshot": launch_gpu_snapshot,
            "rejected_reason": rejected_reason,
        }
        event_path = write_event(config, task_id, deferred_event)
        merge_task_state(
            config,
            task_id,
            status="queued",
            pid=0,
            started_at="",
            launch_gpu_snapshot=launch_gpu_snapshot,
            rejected_reason=rejected_reason,
            last_event_path=str(event_path),
        )
        append_log(runner_log_path, f"launch_deferred reason={rejected_reason}")
        return 0
    merge_task_state(config, task_id, launch_gpu_snapshot=launch_gpu_snapshot, rejected_reason="")
    exit_code: int | None = None
    launch_error = ""
    status = "failed"
    try:
        with command_log_path.open("a", encoding="utf-8") as command_log:
            command_log.write(f"[{utc_now()}] task_started task_id={task_id}\n")
            command_log.write(f"workdir={spec['workdir']}\n")
            command_log.write(f"command={spec['command']}\n\n")
            command_log.flush()
            popen_command = ["bash", "-lc", spec["command"]]
            popen_env = env
            if str(spec.get("execution_mode", "shell")).strip() == "ssh_shell":
                popen_command = build_remote_ssh_command(spec)
            else:
                popen_env = env.copy()
                popen_env.update({str(key): str(value) for key, value in spec.get("env", {}).items()})
            child = subprocess.Popen(
                popen_command,
                cwd=spec["workdir"],
                env=popen_env,
                stdout=command_log,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            append_log(runner_log_path, f"child_started pid={child.pid}")
            exit_code = child.wait()
    except Exception as exc:
        launch_error = f"{type(exc).__name__}: {exc}"
        append_log(runner_log_path, f"launch_error={launch_error}")
    ended_ts = time.time()
    exit_signal = received_signal["name"]
    if launch_error:
        status = "launch_failed"
        if exit_code is None:
            exit_code = 127
    elif exit_signal:
        status = "terminated"
    elif exit_code == 0:
        status = "completed"
    elif exit_code is not None and exit_code < 0:
        try:
            exit_signal = signal.Signals(-exit_code).name
        except ValueError:
            exit_signal = f"SIG{-exit_code}"
        status = "terminated"
    else:
        status = "failed"
    event = create_event_payload(
        config,
        spec,
        status=status,
        started_at=started_ts,
        ended_at=ended_ts,
        exit_code=exit_code,
        exit_signal=exit_signal,
        launch_error=launch_error,
    )
    needs_attention, attention_reason, attention_message = compute_attention(event, spec)
    event["needs_attention"] = needs_attention
    event["attention_reason"] = attention_reason
    event["attention_message"] = attention_message
    event_path = write_event(config, task_id, event)
    if maybe_requeue_cpu_backoff(
        config,
        task_id=task_id,
        spec=spec,
        event=event,
        event_path=event_path,
    ):
        return 0
    merge_task_state(
        config,
        task_id,
        status=status,
        ended_at=event["ended_at"],
        duration_seconds=event["duration_seconds"],
        exit_code=exit_code,
        exit_signal=exit_signal,
        failure_kind=event["failure_kind"],
        failure_summary=event["failure_summary"],
        failure_excerpt=event.get("failure_excerpt", ""),
        needs_attention=needs_attention,
        attention_reason=attention_reason,
        attention_message=attention_message,
        report_summary=event.get("report_summary", ""),
        structured_report=event.get("structured_report", {}),
        taskboard_signal=event.get("taskboard_signal", ""),
        assigned_gpus=event.get("assigned_gpus", []),
        dispatch_gpu_snapshot=event.get("dispatch_gpu_snapshot", []),
        launch_gpu_snapshot=event.get("launch_gpu_snapshot", []),
        rejected_reason=event.get("rejected_reason", ""),
        last_event_path=str(event_path),
        last_message_path=str(last_message_path),
    )
    append_log(runner_log_path, f"task_stopped status={status} exit_code={exit_code} exit_signal={exit_signal}")
    notification = handle_task_feedback(config, task_id=task_id, spec=spec, event=event)
    event["notification"] = notification
    atomic_write_json(event_path, event)
    append_log(runner_log_path, f"notification_done ok={notification.get('ok', False)} resumed_session_id={notification.get('resumed_session_id')}")
    return 0 if status == "completed" else 1


def add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--app-home", default=os.environ.get("CODEX_TASKBOARD_HOME", str(Path.home() / ".local" / "state" / "codex-taskboard")))
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--tmux-bin", default=os.environ.get("TMUX_BIN", "tmux"))


def add_service_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-bind", default=DEFAULT_SERVICE_API_BIND)
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--dispatcher-mode", choices=["serial", "gpu-fill"], default=DEFAULT_SERVICE_DISPATCHER_MODE)
    parser.add_argument("--dispatcher-gpu-count", type=int, default=DEFAULT_SERVICE_GPU_COUNT)
    parser.add_argument("--dispatcher-cpu-thread-limit", type=int, default=DEFAULT_CPU_THREAD_LIMIT)
    parser.add_argument("--dispatcher-poll-seconds", type=float, default=DEFAULT_SERVICE_POLL_SECONDS)
    parser.add_argument("--user")
    parser.add_argument("--group")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tmux-backed task orchestration and dashboard for Codex agents.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local prerequisites and resolved paths.")
    add_config_args(doctor)
    doctor.set_defaults(func=command_doctor)

    list_threads = subparsers.add_parser("list-threads", help="List recent Codex session ids from the local state database.")
    add_config_args(list_threads)
    list_threads.add_argument("--limit", type=int, default=20)
    list_threads.add_argument("--search")
    list_threads.add_argument("--provider")
    list_threads.add_argument("--source")
    list_threads.add_argument("--include-archived", action="store_true")
    list_threads.add_argument("--json", action="store_true")
    list_threads.set_defaults(func=command_list_threads)

    current_thread = subparsers.add_parser("current-thread", help="Show the current Codex session id from the calling environment.")
    add_config_args(current_thread)
    current_thread.add_argument("--json", action="store_true")
    current_thread.set_defaults(func=command_current_thread)

    prompt_preview = subparsers.add_parser(
        "prompt-preview",
        help="Render one wake-up prompt scene using the active customizable prompt file.",
    )
    add_config_args(prompt_preview)
    prompt_preview.add_argument(
        "--scene",
        choices=["resume", "planning", "successor-bootstrap", "execution", "closeout", "reflow-batch", "protocol-repair"],
        default="resume",
    )
    prompt_preview.add_argument("--task-id")
    prompt_preview.add_argument("--event-file")
    prompt_preview.add_argument("--trigger-signal", default="")
    prompt_preview.add_argument("--predecessor-session-id", default="session-closeout-001")
    prompt_preview.add_argument("--continuous", action="store_true")
    prompt_preview.set_defaults(func=command_prompt_preview)

    automation_mode = subparsers.add_parser(
        "automation-mode",
        help="Inspect or switch the persistent taskboard automation mode between managed and continuous.",
    )
    add_config_args(automation_mode)
    automation_mode.add_argument(
        "action",
        nargs="?",
        choices=["status", "managed", "continuous", "toggle", "bind", "clear-session", "clear-all"],
        default="status",
    )
    automation_mode.add_argument("--session-id", dest="codex_session_id", default="")
    automation_mode.add_argument("--codex-session-id", dest="codex_session_id")
    automation_mode.add_argument("--json", action="store_true")
    automation_mode.set_defaults(func=command_automation_mode)

    backlog = subparsers.add_parser(
        "backlog",
        help="Inspect or clear queued reflow backlog for the bound Codex session.",
    )
    add_config_args(backlog)
    backlog.add_argument(
        "action",
        nargs="?",
        choices=["status", "show", "clear", "clear-all"],
        default="status",
    )
    backlog.add_argument("--session-id", dest="codex_session_id", default="")
    backlog.add_argument("--codex-session-id", dest="codex_session_id")
    backlog.add_argument("--json", action="store_true")
    backlog.set_defaults(func=command_backlog)

    ps_training = subparsers.add_parser("ps-training", help="List current Ubuntu training-like processes, including external ones not tracked by the taskboard.")
    add_config_args(ps_training)
    ps_training.add_argument("--limit", type=int, default=50)
    ps_training.add_argument("--json", action="store_true")
    ps_training.add_argument("--show-cwd", action="store_true")
    ps_training.set_defaults(func=command_ps_training)

    submit = subparsers.add_parser("submit", help="Submit one task and optionally hold it in the queue.")
    add_config_args(submit)
    submit.add_argument("--task-id", required=True)
    submit.add_argument("--task-key")
    submit.add_argument("--workdir", required=True)
    submit.add_argument("--command", required=True)
    submit.add_argument("--codex-session-id")
    submit.add_argument("--agent-name")
    submit.add_argument("--proposal")
    submit.add_argument("--closeout-proposal-dir")
    submit.add_argument("--project-history-file")
    submit.add_argument("--no-inherit-proposal", action="store_true")
    submit.add_argument("--priority", type=int, default=0)
    submit.add_argument("--gpu-slots", type=int)
    submit.add_argument("--cpu-profile", choices=CPU_PROFILE_CHOICES, default="auto")
    submit.add_argument("--cpu-threads", type=int, default=0)
    submit.add_argument("--cpu-threads-min", type=int, default=0)
    submit.add_argument("--cpu-threads-max", type=int, default=0)
    submit.add_argument("--cpu-threads-mode", choices=["fixed", "adaptive"], default="")
    submit.add_argument("--cpu-workers", type=int, default=0)
    submit.add_argument("--cpu-workers-min", type=int, default=0)
    submit.add_argument("--cpu-workers-max", type=int, default=0)
    submit.add_argument("--gpu-min-free-mb", type=int, default=0)
    submit.add_argument("--gpu-max-util-percent", type=int, default=0)
    submit.add_argument("--feedback-mode", choices=["auto", "manual", "off"], default="auto")
    submit.add_argument("--depends-on", action="append")
    submit.add_argument("--required-artifact-glob", action="append")
    submit.add_argument("--required-report", action="append")
    submit.add_argument("--report-format", choices=["auto", "json-line", "key-value", "artifact-json"], default="auto")
    submit.add_argument("--report-key", action="append")
    submit.add_argument("--report-contract")
    submit.add_argument("--success-prompt")
    submit.add_argument("--success-prompt-file")
    submit.add_argument("--failure-prompt")
    submit.add_argument("--failure-prompt-file")
    submit.add_argument("--task-note")
    submit.add_argument("--artifact-glob", action="append")
    submit.add_argument("--env", action="append")
    submit.add_argument("--codex-exec-mode", choices=["dangerous", "full-auto"], default="dangerous")
    submit.add_argument("--resume-timeout-seconds", type=int, default=7200)
    submit.add_argument("--launch-grace-seconds", type=int, default=0)
    submit.add_argument("--prompt-max-chars", type=int, default=12000)
    submit.add_argument("--log-tail-lines", type=int, default=80)
    submit.add_argument("--log-tail-chars", type=int, default=5000)
    submit.add_argument("--artifact-max-chars", type=int, default=1200)
    submit.add_argument("--artifact-max-lines", type=int, default=40)
    submit.add_argument("--startup-failure-threshold-seconds", type=int, default=DEFAULT_STARTUP_FAILURE_SECONDS)
    submit.add_argument("--fallback-provider")
    submit.add_argument("--allow-session-rebind", action="store_true")
    submit.add_argument("--allow-duplicate-submit", action="store_true")
    submit.add_argument("--no-replace-existing", action="store_true")
    submit.add_argument("--hold", action="store_true")
    submit.set_defaults(func=command_submit)

    submit_job = subparsers.add_parser("submit-job", help="Submit an agentless job that only reports structured result and exit status.")
    add_config_args(submit_job)
    submit_job.add_argument("--task-id", required=True)
    submit_job.add_argument("--task-key")
    submit_job.add_argument("--workdir", required=True)
    submit_job.add_argument("--command", required=True)
    submit_job.add_argument("--executor")
    submit_job.add_argument("--codex-session-id")
    submit_job.add_argument("--agent-name")
    submit_job.add_argument("--proposal")
    submit_job.add_argument("--closeout-proposal-dir")
    submit_job.add_argument("--project-history-file")
    submit_job.add_argument("--no-inherit-proposal", action="store_true")
    submit_job.add_argument("--priority", type=int, default=0)
    submit_job.add_argument("--gpu-slots", type=int)
    submit_job.add_argument("--assigned-gpus", action="append")
    submit_job.add_argument("--cpu-profile", choices=CPU_PROFILE_CHOICES, default="auto")
    submit_job.add_argument("--cpu-threads", type=int, default=0)
    submit_job.add_argument("--cpu-threads-min", type=int, default=0)
    submit_job.add_argument("--cpu-threads-max", type=int, default=0)
    submit_job.add_argument("--cpu-threads-mode", choices=["fixed", "adaptive"], default="")
    submit_job.add_argument("--cpu-workers", type=int, default=0)
    submit_job.add_argument("--cpu-workers-min", type=int, default=0)
    submit_job.add_argument("--cpu-workers-max", type=int, default=0)
    submit_job.add_argument("--gpu-min-free-mb", type=int, default=0)
    submit_job.add_argument("--gpu-max-util-percent", type=int, default=0)
    submit_job.add_argument("--feedback-mode", choices=["auto", "manual", "off"], default="off")
    submit_job.add_argument("--depends-on", action="append")
    submit_job.add_argument("--required-artifact-glob", action="append")
    submit_job.add_argument("--required-report", action="append")
    submit_job.add_argument("--report-format", choices=["auto", "json-line", "key-value", "artifact-json"], default="auto")
    submit_job.add_argument("--report-key", action="append")
    submit_job.add_argument("--report-contract")
    submit_job.add_argument("--success-prompt")
    submit_job.add_argument("--success-prompt-file")
    submit_job.add_argument("--failure-prompt")
    submit_job.add_argument("--failure-prompt-file")
    submit_job.add_argument("--task-note")
    submit_job.add_argument("--artifact-glob", action="append")
    submit_job.add_argument("--env", action="append")
    submit_job.add_argument("--codex-exec-mode", choices=["dangerous", "full-auto"], default="dangerous")
    submit_job.add_argument("--resume-timeout-seconds", type=int, default=7200)
    submit_job.add_argument("--launch-grace-seconds", type=int, default=0)
    submit_job.add_argument("--prompt-max-chars", type=int, default=12000)
    submit_job.add_argument("--log-tail-lines", type=int, default=80)
    submit_job.add_argument("--log-tail-chars", type=int, default=5000)
    submit_job.add_argument("--artifact-max-chars", type=int, default=1200)
    submit_job.add_argument("--artifact-max-lines", type=int, default=40)
    submit_job.add_argument("--startup-failure-threshold-seconds", type=int, default=DEFAULT_STARTUP_FAILURE_SECONDS)
    submit_job.add_argument("--fallback-provider")
    submit_job.add_argument("--allow-session-rebind", action="store_true")
    submit_job.add_argument("--allow-duplicate-submit", action="store_true")
    submit_job.add_argument("--no-replace-existing", action="store_true")
    submit_job.add_argument("--hold", action="store_true")
    submit_job.set_defaults(func=command_submit_job)

    bind_before_launch = subparsers.add_parser(
        "bind-before-launch",
        help="Submit a local CPU-only task before launch so it is bound to taskboard lifecycle from the start.",
    )
    add_config_args(bind_before_launch)
    bind_before_launch.add_argument("--task-id", required=True)
    bind_before_launch.add_argument("--task-key")
    bind_before_launch.add_argument("--workdir", required=True)
    bind_before_launch.add_argument("--command", required=True)
    bind_before_launch.add_argument("--codex-session-id")
    bind_before_launch.add_argument("--agent-name")
    bind_before_launch.add_argument("--proposal")
    bind_before_launch.add_argument("--closeout-proposal-dir")
    bind_before_launch.add_argument("--project-history-file")
    bind_before_launch.add_argument("--no-inherit-proposal", action="store_true")
    bind_before_launch.add_argument("--priority", type=int, default=0)
    bind_before_launch.add_argument("--cpu-profile", choices=CPU_PROFILE_CHOICES, default="auto")
    bind_before_launch.add_argument("--cpu-threads", type=int, default=0)
    bind_before_launch.add_argument("--cpu-threads-min", type=int, default=0)
    bind_before_launch.add_argument("--cpu-threads-max", type=int, default=0)
    bind_before_launch.add_argument("--cpu-threads-mode", choices=["fixed", "adaptive"], default="")
    bind_before_launch.add_argument("--cpu-workers", type=int, default=0)
    bind_before_launch.add_argument("--cpu-workers-min", type=int, default=0)
    bind_before_launch.add_argument("--cpu-workers-max", type=int, default=0)
    bind_before_launch.add_argument("--feedback-mode", choices=["auto", "manual", "off"], default="auto")
    bind_before_launch.add_argument("--depends-on", action="append")
    bind_before_launch.add_argument("--required-artifact-glob", action="append")
    bind_before_launch.add_argument("--required-report", action="append")
    bind_before_launch.add_argument("--report-format", choices=["auto", "json-line", "key-value", "artifact-json"], default="auto")
    bind_before_launch.add_argument("--report-key", action="append")
    bind_before_launch.add_argument("--report-contract")
    bind_before_launch.add_argument("--success-prompt")
    bind_before_launch.add_argument("--success-prompt-file")
    bind_before_launch.add_argument("--failure-prompt")
    bind_before_launch.add_argument("--failure-prompt-file")
    bind_before_launch.add_argument("--task-note")
    bind_before_launch.add_argument("--artifact-glob", action="append")
    bind_before_launch.add_argument("--env", action="append")
    bind_before_launch.add_argument("--codex-exec-mode", choices=["dangerous", "full-auto"], default="dangerous")
    bind_before_launch.add_argument("--resume-timeout-seconds", type=int, default=7200)
    bind_before_launch.add_argument("--launch-grace-seconds", type=int, default=0)
    bind_before_launch.add_argument("--prompt-max-chars", type=int, default=12000)
    bind_before_launch.add_argument("--log-tail-lines", type=int, default=80)
    bind_before_launch.add_argument("--log-tail-chars", type=int, default=5000)
    bind_before_launch.add_argument("--artifact-max-chars", type=int, default=1200)
    bind_before_launch.add_argument("--artifact-max-lines", type=int, default=40)
    bind_before_launch.add_argument("--startup-failure-threshold-seconds", type=int, default=DEFAULT_STARTUP_FAILURE_SECONDS)
    bind_before_launch.add_argument("--fallback-provider")
    bind_before_launch.add_argument("--allow-session-rebind", action="store_true")
    bind_before_launch.add_argument("--allow-duplicate-submit", action="store_true")
    bind_before_launch.add_argument("--no-replace-existing", action="store_true")
    bind_before_launch.add_argument("--hold", action="store_true")
    bind_before_launch.set_defaults(func=command_bind_before_launch)

    submit_subagent = subparsers.add_parser("submit-subagent", help="Submit a Codex child worker that auto-reports back to the parent Codex session.")
    add_config_args(submit_subagent)
    submit_subagent.add_argument("--task-id", required=True)
    submit_subagent.add_argument("--task-key")
    submit_subagent.add_argument("--workdir", required=True)
    submit_subagent.add_argument("--codex-session-id")
    submit_subagent.add_argument("--proposal")
    submit_subagent.add_argument("--closeout-proposal-dir")
    submit_subagent.add_argument("--project-history-file")
    submit_subagent.add_argument("--no-inherit-proposal", action="store_true")
    submit_subagent.add_argument("--prompt")
    submit_subagent.add_argument("--prompt-file")
    submit_subagent.add_argument("--model", default="gpt-5.4")
    submit_subagent.add_argument("--subagent-exec-mode", choices=["dangerous", "full-auto"], default="dangerous")
    submit_subagent.add_argument("--subagent-timeout-seconds", type=int, default=7200)
    submit_subagent.add_argument("--subagent-continue-attempts", type=int, default=3)
    submit_subagent.add_argument("--agent-name")
    submit_subagent.add_argument("--priority", type=int, default=0)
    submit_subagent.add_argument("--gpu-slots", type=int, default=0)
    submit_subagent.add_argument("--cpu-profile", choices=CPU_PROFILE_CHOICES, default="auto")
    submit_subagent.add_argument("--cpu-threads", type=int, default=0)
    submit_subagent.add_argument("--cpu-threads-min", type=int, default=0)
    submit_subagent.add_argument("--cpu-threads-max", type=int, default=0)
    submit_subagent.add_argument("--cpu-threads-mode", choices=["fixed", "adaptive"], default="")
    submit_subagent.add_argument("--cpu-workers", type=int, default=0)
    submit_subagent.add_argument("--cpu-workers-min", type=int, default=0)
    submit_subagent.add_argument("--cpu-workers-max", type=int, default=0)
    submit_subagent.add_argument("--gpu-min-free-mb", type=int, default=0)
    submit_subagent.add_argument("--gpu-max-util-percent", type=int, default=0)
    submit_subagent.add_argument("--feedback-mode", choices=["auto", "manual", "off"], default="auto")
    submit_subagent.add_argument("--depends-on", action="append")
    submit_subagent.add_argument("--required-artifact-glob", action="append")
    submit_subagent.add_argument("--required-report", action="append")
    submit_subagent.add_argument("--report-format", choices=["auto", "json-line", "key-value", "artifact-json"], default="auto")
    submit_subagent.add_argument("--report-key", action="append")
    submit_subagent.add_argument("--report-contract")
    submit_subagent.add_argument("--success-prompt")
    submit_subagent.add_argument("--success-prompt-file")
    submit_subagent.add_argument("--failure-prompt")
    submit_subagent.add_argument("--failure-prompt-file")
    submit_subagent.add_argument("--task-note")
    submit_subagent.add_argument("--artifact-glob", action="append")
    submit_subagent.add_argument("--codex-exec-mode", choices=["dangerous", "full-auto"], default="dangerous")
    submit_subagent.add_argument("--resume-timeout-seconds", type=int, default=7200)
    submit_subagent.add_argument("--launch-grace-seconds", type=int, default=0)
    submit_subagent.add_argument("--prompt-max-chars", type=int, default=12000)
    submit_subagent.add_argument("--log-tail-lines", type=int, default=80)
    submit_subagent.add_argument("--log-tail-chars", type=int, default=5000)
    submit_subagent.add_argument("--artifact-max-chars", type=int, default=1200)
    submit_subagent.add_argument("--artifact-max-lines", type=int, default=40)
    submit_subagent.add_argument("--startup-failure-threshold-seconds", type=int, default=DEFAULT_STARTUP_FAILURE_SECONDS)
    submit_subagent.add_argument("--fallback-provider")
    submit_subagent.add_argument("--allow-session-rebind", action="store_true")
    submit_subagent.add_argument("--allow-duplicate-submit", action="store_true")
    submit_subagent.add_argument("--no-replace-existing", action="store_true")
    submit_subagent.add_argument("--hold", action="store_true")
    submit_subagent.set_defaults(func=command_submit_subagent)

    attach_pid = subparsers.add_parser("attach-pid", help="Attach an existing external PID to the taskboard and wake Codex when it exits.")
    add_config_args(attach_pid)
    attach_pid.add_argument("--pid", type=int, required=True)
    attach_pid.add_argument("--task-id")
    attach_pid.add_argument("--task-key")
    attach_pid.add_argument("--workdir", required=True)
    attach_pid.add_argument("--codex-session-id")
    attach_pid.add_argument("--agent-name")
    attach_pid.add_argument("--proposal")
    attach_pid.add_argument("--closeout-proposal-dir")
    attach_pid.add_argument("--project-history-file")
    attach_pid.add_argument("--no-inherit-proposal", action="store_true")
    attach_pid.add_argument("--priority", type=int, default=0)
    attach_pid.add_argument("--gpu-slots", type=int)
    attach_pid.add_argument("--cpu-profile", choices=CPU_PROFILE_CHOICES, default="auto")
    attach_pid.add_argument("--cpu-threads", type=int, default=0)
    attach_pid.add_argument("--cpu-threads-min", type=int, default=0)
    attach_pid.add_argument("--cpu-threads-max", type=int, default=0)
    attach_pid.add_argument("--cpu-threads-mode", choices=["fixed", "adaptive"], default="")
    attach_pid.add_argument("--cpu-workers", type=int, default=0)
    attach_pid.add_argument("--cpu-workers-min", type=int, default=0)
    attach_pid.add_argument("--cpu-workers-max", type=int, default=0)
    attach_pid.add_argument("--gpu-min-free-mb", type=int, default=0)
    attach_pid.add_argument("--gpu-max-util-percent", type=int, default=0)
    attach_pid.add_argument("--feedback-mode", choices=["auto", "manual", "off"], default="auto")
    attach_pid.add_argument("--depends-on", action="append")
    attach_pid.add_argument("--report-format", choices=["auto", "json-line", "key-value", "artifact-json"], default="auto")
    attach_pid.add_argument("--report-key", action="append")
    attach_pid.add_argument("--report-contract")
    attach_pid.add_argument("--watch-log-path")
    attach_pid.add_argument("--success-prompt")
    attach_pid.add_argument("--success-prompt-file")
    attach_pid.add_argument("--failure-prompt")
    attach_pid.add_argument("--failure-prompt-file")
    attach_pid.add_argument("--task-note")
    attach_pid.add_argument("--artifact-glob", action="append")
    attach_pid.add_argument("--codex-exec-mode", choices=["dangerous", "full-auto"], default="dangerous")
    attach_pid.add_argument("--resume-timeout-seconds", type=int, default=7200)
    attach_pid.add_argument("--prompt-max-chars", type=int, default=12000)
    attach_pid.add_argument("--log-tail-lines", type=int, default=80)
    attach_pid.add_argument("--log-tail-chars", type=int, default=5000)
    attach_pid.add_argument("--artifact-max-chars", type=int, default=1200)
    attach_pid.add_argument("--artifact-max-lines", type=int, default=40)
    attach_pid.add_argument("--fallback-provider")
    attach_pid.add_argument("--allow-session-rebind", action="store_true")
    attach_pid.add_argument("--allow-duplicate-submit", action="store_true")
    attach_pid.add_argument("--no-replace-existing", action="store_true")
    attach_pid.set_defaults(func=command_attach_pid)

    submit_file = subparsers.add_parser("submit-file", help="Submit one or many tasks from JSON.")
    add_config_args(submit_file)
    submit_file.add_argument("--spec-file", required=True)
    submit_file.add_argument("--proposal")
    submit_file.add_argument("--closeout-proposal-dir")
    submit_file.add_argument("--project-history-file")
    submit_file.add_argument("--no-inherit-proposal", action="store_true")
    submit_file.add_argument("--hold", action="store_true")
    submit_file.set_defaults(func=command_submit_file)

    dispatch = subparsers.add_parser("dispatch", help="Start queued tasks up to max concurrency.")
    add_config_args(dispatch)
    dispatch.add_argument("--mode", choices=["serial", "gpu-fill"], default="serial")
    dispatch.add_argument("--max-running", type=int, default=0)
    dispatch.add_argument("--gpu-count", type=int, default=0)
    dispatch.add_argument("--cpu-thread-limit", type=int, default=0)
    dispatch.add_argument("--limit", type=int, default=100)
    dispatch.set_defaults(func=command_dispatch)

    serve = subparsers.add_parser("serve", help="Loop forever and keep dispatching queued tasks.")
    add_config_args(serve)
    serve.add_argument("--mode", choices=["serial", "gpu-fill"], default="serial")
    serve.add_argument("--max-running", type=int, default=0)
    serve.add_argument("--gpu-count", type=int, default=0)
    serve.add_argument("--cpu-thread-limit", type=int, default=0)
    serve.add_argument("--dispatch-limit", type=int, default=100)
    serve.add_argument("--poll-seconds", type=float, default=5.0)
    serve.add_argument("--verbose", action="store_true")
    serve.set_defaults(func=command_serve)

    serve_api = subparsers.add_parser("serve-api", help="Expose submit-job/status-result/wait-result over a small authenticated HTTP API.")
    add_config_args(serve_api)
    serve_api.add_argument("--bind", default=DEFAULT_API_BIND)
    serve_api.add_argument("--port", type=int, default=DEFAULT_API_PORT)
    serve_api.set_defaults(func=command_serve_api)

    service = subparsers.add_parser("service", help="Inspect or run the standardized production service entrypoints.")
    add_config_args(service)
    service_subparsers = service.add_subparsers(dest="service_command", required=True)

    service_run = service_subparsers.add_parser("run", help="Run the managed production service entrypoint with lock and drift guards.")
    service_run_subparsers = service_run.add_subparsers(dest="service_name", required=True)

    service_run_api = service_run_subparsers.add_parser("api", help="Run the managed API service entrypoint.")
    service_run_api.add_argument("--bind", default=DEFAULT_SERVICE_API_BIND)
    service_run_api.add_argument("--port", type=int, default=DEFAULT_API_PORT)
    service_run_api.set_defaults(func=command_service_run, service_name="api")

    service_run_dispatcher = service_run_subparsers.add_parser("dispatcher", help="Run the managed dispatcher service entrypoint.")
    service_run_dispatcher.add_argument("--mode", choices=["serial", "gpu-fill"], default=DEFAULT_SERVICE_DISPATCHER_MODE)
    service_run_dispatcher.add_argument("--max-running", type=int, default=0)
    service_run_dispatcher.add_argument("--gpu-count", type=int, default=DEFAULT_SERVICE_GPU_COUNT)
    service_run_dispatcher.add_argument("--cpu-thread-limit", type=int, default=DEFAULT_CPU_THREAD_LIMIT)
    service_run_dispatcher.add_argument("--dispatch-limit", type=int, default=100)
    service_run_dispatcher.add_argument("--poll-seconds", type=float, default=DEFAULT_SERVICE_POLL_SECONDS)
    service_run_dispatcher.add_argument("--verbose", action="store_true")
    service_run_dispatcher.set_defaults(func=command_service_run, service_name="dispatcher")

    service_doctor = service_subparsers.add_parser("doctor", help="Show service drift, legacy pid files, and systemd ownership diagnostics.")
    add_service_template_args(service_doctor)
    service_doctor.set_defaults(func=command_service_doctor)

    service_print = service_subparsers.add_parser("print-systemd", help="Render the canonical systemd unit files for API and dispatcher services.")
    add_service_template_args(service_print)
    service_print.add_argument("--service-name", choices=["api", "dispatcher", "all"], default="all")
    service_print.set_defaults(func=command_service_print_systemd)

    dashboard = subparsers.add_parser("dashboard", help="Show queued, running, and finished tasks in the terminal.")
    add_config_args(dashboard)
    dashboard.add_argument("--limit", type=int, default=30)
    dashboard.add_argument("--refresh-seconds", type=float, default=1.0)
    dashboard.add_argument("--render-mode", choices=["auto", "curses", "plain"], default="auto")
    dashboard.add_argument("--process-panel", choices=["auto", "off", "gpu", "hybrid", "training"], default="auto")
    dashboard.add_argument("--once", action="store_true")
    dashboard.set_defaults(func=command_dashboard)

    status = subparsers.add_parser("status", help="Inspect one task or list recent tasks.")
    add_config_args(status)
    status.add_argument("--task-id")
    status.add_argument("--limit", type=int, default=20)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    status_result = subparsers.add_parser("status-result", help="Return a structured status/result payload for one task.")
    add_config_args(status_result)
    status_result.add_argument("--task-id", required=True)
    status_result.set_defaults(func=command_status_result)

    wait = subparsers.add_parser("wait", help="Block until a task reaches a terminal state.")
    add_config_args(wait)
    wait.add_argument("--task-id", required=True)
    wait.add_argument("--timeout-seconds", type=int, default=3600)
    wait.add_argument("--poll-seconds", type=float, default=2.0)
    wait.add_argument("--expect-status")
    wait.add_argument("--json", action="store_true")
    wait.set_defaults(func=command_wait)

    wait_result = subparsers.add_parser("wait-result", help="Block until a task reaches a terminal state and return the structured result payload.")
    add_config_args(wait_result)
    wait_result.add_argument("--task-id", required=True)
    wait_result.add_argument("--timeout-seconds", type=float, default=3600)
    wait_result.add_argument("--poll-seconds", type=float, default=DEFAULT_API_POLL_SECONDS)
    wait_result.add_argument("--expect-status")
    wait_result.set_defaults(func=command_wait_result)

    cancel = subparsers.add_parser("cancel", help="Kill the tmux session for a task.")
    add_config_args(cancel)
    cancel.add_argument("--task-id", required=True)
    cancel.add_argument("--suppress-feedback", action="store_true")
    cancel.set_defaults(func=command_cancel)

    feedback_mode = subparsers.add_parser("feedback-mode", help="Switch automatic feedback behavior for one task or all tasks.")
    add_config_args(feedback_mode)
    feedback_mode.add_argument("--task-id")
    feedback_mode.add_argument("--all", action="store_true")
    feedback_mode.add_argument("--mode", choices=["auto", "manual", "off"], required=True)
    feedback_mode.set_defaults(func=command_feedback_mode)

    priority = subparsers.add_parser("priority", help="Adjust priority for one task or all tasks owned by one agent.")
    add_config_args(priority)
    priority.add_argument("--task-id")
    priority.add_argument("--agent-name")
    priority.add_argument("--value", type=int)
    priority.add_argument("--delta", type=int, default=0)
    priority.set_defaults(func=command_priority)

    followup_stop = subparsers.add_parser("followup-stop", help="Stop repeated follow-up nudges for one task or one agent.")
    add_config_args(followup_stop)
    followup_stop.add_argument("--task-id")
    followup_stop.add_argument("--agent-name")
    followup_stop.set_defaults(func=command_followup_stop)

    followup_reconcile = subparsers.add_parser("followup-reconcile", help="Reconcile follow-up state with on-disk follow-up entities.")
    add_config_args(followup_reconcile)
    followup_reconcile.add_argument("--task-id")
    followup_reconcile.add_argument("--agent-name")
    followup_reconcile.add_argument("--dry-run", action="store_true")
    followup_reconcile.set_defaults(func=command_followup_reconcile)

    migrate_session = subparsers.add_parser(
        "migrate-session",
        help="Manually cut over taskboard ownership from one Codex session to another.",
    )
    add_config_args(migrate_session)
    migrate_session.add_argument("--from-session-id", required=True)
    migrate_session.add_argument("--to-session-id", required=True)
    migrate_session.add_argument("--interrupt-grace-seconds", type=int, default=DEFAULT_SESSION_MIGRATION_INTERRUPT_GRACE_SECONDS)
    migrate_session.add_argument("--dry-run", action="store_true")
    migrate_session.set_defaults(func=command_migrate_session)

    migrate_legacy = subparsers.add_parser("migrate-legacy", help="Move legacy taskboard directories into the global taskboard root and archive.")
    add_config_args(migrate_legacy)
    migrate_legacy.add_argument("--legacy-root", action="append", help="Explicit legacy task root to migrate. Repeat for multiple roots.")
    migrate_legacy.add_argument("--all-discovered", action="store_true", help="Migrate every discovered legacy root. Use with care.")
    migrate_legacy.set_defaults(func=command_migrate_legacy)

    notify = subparsers.add_parser("notify", help="Replay the latest Codex wake-up for a task.")
    add_config_args(notify)
    notify.add_argument("--task-id", required=True)
    notify.add_argument("--event-file")
    notify.set_defaults(func=command_notify)

    cleanup = subparsers.add_parser("cleanup", help="Remove stored task artifacts.")
    add_config_args(cleanup)
    cleanup.add_argument("--task-id")
    cleanup.add_argument("--kill-if-running", action="store_true")
    cleanup.add_argument("--include-nonterminal", action="store_true")
    cleanup.set_defaults(func=command_cleanup)

    run = subparsers.add_parser("run", help="Internal worker entrypoint launched from tmux.")
    add_config_args(run)
    run.add_argument("--spec-file", required=True)
    run.set_defaults(func=command_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
