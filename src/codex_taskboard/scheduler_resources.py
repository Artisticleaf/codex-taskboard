from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SchedulerResourceHooks:
    looks_like_training_command: Callable[[str], bool]
    shutil_which: Callable[[str], str]
    run_subprocess: Callable[..., Any]
    cpu_profile_choices: tuple[str, ...]
    cpu_thread_env_keys: tuple[str, ...]
    cpu_worker_env_keys: tuple[str, ...]
    cpu_resource_retry_patterns: tuple[str, ...]
    default_cpu_thread_limit: int
    default_cpu_only_threads: int
    default_gpu_task_cpu_threads: int
    default_subagent_cpu_threads: int
    default_generic_task_cpu_threads: int
    default_gpu_min_free_mb: int
    default_gpu_free_ratio: float
    default_gpu_max_util_percent: int


def detect_default_cpu_thread_limit(*, hooks: SchedulerResourceHooks) -> int:
    cpu_count = os.cpu_count() or hooks.default_cpu_thread_limit
    return max(1, min(hooks.default_cpu_thread_limit, int(cpu_count)))


def coerce_non_negative_int(raw_value: Any) -> int:
    try:
        return max(0, int(raw_value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_cpu_profile(raw_value: Any, *, hooks: SchedulerResourceHooks) -> str:
    value = str(raw_value or "").strip().lower()
    if value in hooks.cpu_profile_choices:
        return value
    return "auto"


def declared_cpu_profile(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> str:
    return normalize_cpu_profile(spec.get("cpu_profile", "auto"), hooks=hooks)


def resolved_cpu_profile(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> str:
    declared = declared_cpu_profile(spec, hooks=hooks)
    if declared != "auto":
        return declared
    execution_mode = str(spec.get("execution_mode", "shell")).strip() or "shell"
    command = str(spec.get("command_template", spec.get("command", "")))
    gpu_slots = coerce_non_negative_int(spec.get("gpu_slots", 0))
    if execution_mode == "codex_subagent":
        return "single"
    if gpu_slots > 0:
        return "gpu_feeder"
    if hooks.looks_like_training_command(command) or "python" in command:
        return "cpu_compute"
    return "single"


def extract_thread_limit_from_env(env: dict[str, Any], *, hooks: SchedulerResourceHooks) -> int:
    max_threads = 0
    for key in hooks.cpu_thread_env_keys:
        raw_value = str(env.get(key, "")).strip() if isinstance(env, dict) else ""
        if not raw_value:
            continue
        try:
            max_threads = max(max_threads, int(raw_value))
        except ValueError:
            continue
    return max_threads


def extract_inline_thread_limit(command: str, *, hooks: SchedulerResourceHooks) -> int:
    pattern = r"(?:^|\s)(?:export\s+)?(?:" + "|".join(hooks.cpu_thread_env_keys) + r")=([0-9]+)"
    matches = re.findall(pattern, command)
    if not matches:
        return 0
    max_threads = 0
    for raw_value in matches:
        try:
            max_threads = max(max_threads, int(raw_value))
        except ValueError:
            continue
    return max_threads


def command_sets_cpu_thread_limits(command: str, *, hooks: SchedulerResourceHooks) -> bool:
    return extract_inline_thread_limit(command, hooks=hooks) > 0


def extract_worker_limit_from_env(env: dict[str, Any], *, hooks: SchedulerResourceHooks) -> int:
    max_workers = 0
    for key in hooks.cpu_worker_env_keys:
        raw_value = str(env.get(key, "")).strip() if isinstance(env, dict) else ""
        if not raw_value:
            continue
        try:
            max_workers = max(max_workers, int(raw_value))
        except ValueError:
            continue
    return max_workers


def extract_inline_worker_limit(command: str) -> int:
    patterns = [
        r"(?:--(?:num[-_])?workers|--dataloader[-_]num[-_]workers)(?:=|\s+)(\d+)",
        r"\bnum_workers\s*=\s*(\d+)",
    ]
    max_workers = 0
    for pattern in patterns:
        matches = re.findall(pattern, command)
        for raw_value in matches:
            try:
                max_workers = max(max_workers, int(raw_value))
            except ValueError:
                continue
    return max_workers


def command_sets_cpu_worker_limits(command: str) -> bool:
    return extract_inline_worker_limit(command) > 0 or "{cpu_workers}" in str(command)


def command_uses_cpu_runtime_template(command: str) -> bool:
    text = str(command or "")
    return any(token in text for token in ("{cpu_threads}", "{cpu_workers}", "{cpu_profile}", "{cpu_budget}"))


def render_task_command_template(
    command_template: str,
    *,
    cpu_threads: int,
    cpu_workers: int,
    cpu_profile: str,
    cpu_budget: int,
) -> str:
    rendered = str(command_template or "")
    replacements = {
        "{cpu_threads}": str(max(0, int(cpu_threads or 0))),
        "{cpu_workers}": str(max(0, int(cpu_workers or 0))),
        "{cpu_profile}": str(cpu_profile or "auto"),
        "{cpu_budget}": str(max(0, int(cpu_budget or 0))),
    }
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered


def infer_default_cpu_threads(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> tuple[int, str]:
    profile = declared_cpu_profile(spec, hooks=hooks)
    execution_mode = str(spec.get("execution_mode", "shell")).strip() or "shell"
    command = str(spec.get("command", ""))
    gpu_slots = coerce_non_negative_int(spec.get("gpu_slots", 0))
    if profile == "single":
        return 1, "profile_single"
    if profile == "sidecar":
        return 0, "profile_sidecar"
    if profile == "gpu_feeder":
        return 2, "profile_gpu_feeder"
    if profile == "hybrid":
        return (4 if gpu_slots <= 0 else 2), "profile_hybrid"
    if profile == "cpu_compute":
        return hooks.default_cpu_only_threads, "profile_cpu_compute"
    if execution_mode == "codex_subagent":
        return hooks.default_subagent_cpu_threads, "subagent_default"
    if gpu_slots > 0:
        return hooks.default_gpu_task_cpu_threads, "gpu_default"
    if hooks.looks_like_training_command(command):
        return hooks.default_cpu_only_threads, "cpu_training_default"
    if "python" in command:
        return max(hooks.default_generic_task_cpu_threads, 4), "python_default"
    return hooks.default_generic_task_cpu_threads, "default"


def default_cpu_thread_mode(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> str:
    profile = declared_cpu_profile(spec, hooks=hooks)
    if profile == "sidecar":
        return "fixed"
    execution_mode = str(spec.get("execution_mode", "shell")).strip() or "shell"
    gpu_slots = coerce_non_negative_int(spec.get("gpu_slots", 0))
    if execution_mode == "shell" and gpu_slots <= 0:
        return "adaptive"
    return "fixed"


def resolve_cpu_thread_policy(spec: dict[str, Any], *, hooks: SchedulerResourceHooks, cpu_thread_limit: int = 0) -> dict[str, Any]:
    explicit_exact = coerce_non_negative_int(spec.get("cpu_threads", 0))
    explicit_min = coerce_non_negative_int(spec.get("cpu_threads_min", 0))
    explicit_max = coerce_non_negative_int(spec.get("cpu_threads_max", 0))
    assigned_threads = coerce_non_negative_int(spec.get("assigned_cpu_threads", 0))
    retry_attempts = coerce_non_negative_int(spec.get("cpu_retry_attempts", 0))
    mode_raw = str(spec.get("cpu_threads_mode", "")).strip().lower()
    env = spec.get("env", {})
    env_threads = extract_thread_limit_from_env(env if isinstance(env, dict) else {}, hooks=hooks)
    command = str(spec.get("command", ""))
    inline_threads = extract_inline_thread_limit(command, hooks=hooks)
    default_threads, default_source = infer_default_cpu_threads(spec, hooks=hooks)

    if mode_raw in {"fixed", "adaptive"}:
        mode = mode_raw
        if explicit_min > 0 or explicit_max > 0 or explicit_exact > 0:
            source = f"explicit_{mode_raw}_mode"
        elif env_threads > 0:
            source = "env"
        elif inline_threads > 0:
            source = "command"
        else:
            source = f"default_{mode_raw}_mode"
    elif explicit_min > 0 or explicit_max > 0:
        mode = "adaptive"
        source = "explicit_range"
    elif explicit_exact > 0:
        mode = "fixed"
        source = "explicit"
    elif env_threads > 0:
        mode = "fixed"
        source = "env"
    elif inline_threads > 0:
        mode = "fixed"
        source = "command"
    else:
        mode = default_cpu_thread_mode(spec, hooks=hooks)
        source = "cpu_only_default" if mode == "adaptive" else default_source

    if mode == "fixed":
        exact_threads = explicit_exact or env_threads or inline_threads or default_threads
        exact_threads = max(0, exact_threads)
        effective_assigned = assigned_threads or exact_threads
        return {
            "mode": "fixed",
            "source": source,
            "min_threads": exact_threads,
            "max_threads": exact_threads,
            "requested_threads": exact_threads,
            "assigned_threads": effective_assigned,
            "reservation_threads": effective_assigned,
            "effective_max_threads": exact_threads,
        }

    min_threads = explicit_min or explicit_exact or default_threads
    min_threads = max(1, min_threads)
    max_threads = max(explicit_max, min_threads) if explicit_max > 0 else 0
    if retry_attempts > 0 and max_threads > 0:
        effective_max_threads = max_threads
    else:
        effective_max_threads = max(min_threads, coerce_non_negative_int(cpu_thread_limit), assigned_threads, default_threads)
    effective_assigned = assigned_threads or min_threads
    return {
        "mode": "adaptive",
        "source": source,
        "min_threads": min_threads,
        "max_threads": max_threads,
        "requested_threads": min_threads,
        "assigned_threads": effective_assigned,
        "reservation_threads": assigned_threads or min_threads,
        "effective_max_threads": max(min_threads, effective_max_threads),
    }


def resolve_cpu_worker_policy(spec: dict[str, Any], *, hooks: SchedulerResourceHooks, cpu_thread_limit: int = 0) -> dict[str, Any]:
    explicit_exact = coerce_non_negative_int(spec.get("cpu_workers", 0))
    explicit_min = coerce_non_negative_int(spec.get("cpu_workers_min", 0))
    explicit_max = coerce_non_negative_int(spec.get("cpu_workers_max", 0))
    assigned_workers = coerce_non_negative_int(spec.get("assigned_cpu_workers", 0))
    env = spec.get("env", {})
    env_workers = extract_worker_limit_from_env(env if isinstance(env, dict) else {}, hooks=hooks)
    command = str(spec.get("command_template", spec.get("command", "")))
    inline_workers = extract_inline_worker_limit(command)

    if explicit_min > 0 or explicit_max > 0:
        mode = "adaptive"
        source = "explicit_range"
    elif explicit_exact > 0:
        mode = "fixed"
        source = "explicit"
    elif env_workers > 0:
        mode = "fixed"
        source = "env"
    elif inline_workers > 0:
        mode = "fixed"
        source = "command"
    else:
        return {
            "mode": "fixed",
            "source": "default",
            "min_workers": 0,
            "max_workers": 0,
            "requested_workers": 0,
            "assigned_workers": 0,
            "reservation_workers": 0,
            "effective_max_workers": 0,
        }

    if mode == "fixed":
        exact_workers = explicit_exact or env_workers or inline_workers
        exact_workers = max(0, exact_workers)
        effective_assigned = assigned_workers or exact_workers
        return {
            "mode": "fixed",
            "source": source,
            "min_workers": exact_workers,
            "max_workers": exact_workers,
            "requested_workers": exact_workers,
            "assigned_workers": effective_assigned,
            "reservation_workers": effective_assigned,
            "effective_max_workers": exact_workers,
        }

    min_workers = explicit_min or explicit_exact or env_workers or inline_workers
    min_workers = max(0, min_workers)
    max_workers = max(explicit_max, min_workers) if explicit_max > 0 else 0
    effective_max_workers = max(min_workers, coerce_non_negative_int(cpu_thread_limit), assigned_workers)
    if max_workers > 0:
        effective_max_workers = max(min_workers, max_workers, assigned_workers)
    effective_assigned = assigned_workers or min_workers
    return {
        "mode": "adaptive",
        "source": source,
        "min_workers": min_workers,
        "max_workers": max_workers,
        "requested_workers": min_workers,
        "assigned_workers": effective_assigned,
        "reservation_workers": effective_assigned,
        "effective_max_workers": max(min_workers, effective_max_workers),
    }


def resolve_cpu_threads(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> tuple[int, str]:
    policy = resolve_cpu_thread_policy(spec, hooks=hooks)
    return int(policy.get("reservation_threads", 0) or 0), str(policy.get("source", "") or "default")


def resolve_cpu_workers(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> tuple[int, str]:
    policy = resolve_cpu_worker_policy(spec, hooks=hooks)
    return int(policy.get("reservation_workers", 0) or 0), str(policy.get("source", "") or "default")


def task_requested_cpu_threads(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> int:
    policy = resolve_cpu_thread_policy(spec, hooks=hooks)
    return max(0, int(policy.get("reservation_threads", 0) or 0))


def task_requested_cpu_workers(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> int:
    policy = resolve_cpu_worker_policy(spec, hooks=hooks)
    return max(0, int(policy.get("reservation_workers", 0) or 0))


def task_requested_cpu_budget(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> int:
    return task_requested_cpu_threads(spec, hooks=hooks) + task_requested_cpu_workers(spec, hooks=hooks)


def select_cpu_resources_for_start(
    spec: dict[str, Any],
    *,
    hooks: SchedulerResourceHooks,
    available_cpu_threads: int,
    reserve_for_other_tasks: int = 0,
) -> dict[str, int | str]:
    available = max(0, int(available_cpu_threads or 0))
    profile = resolved_cpu_profile(spec, hooks=hooks)
    thread_policy = resolve_cpu_thread_policy(spec, hooks=hooks, cpu_thread_limit=available)
    worker_policy = resolve_cpu_worker_policy(spec, hooks=hooks, cpu_thread_limit=available)
    min_threads = max(0, int(thread_policy.get("min_threads", 0) or 0))
    min_workers = max(0, int(worker_policy.get("min_workers", 0) or 0))
    min_budget = min_threads + min_workers
    if available <= 0 or min_budget <= 0:
        return {
            "cpu_profile": profile,
            "assigned_cpu_threads": 0,
            "assigned_cpu_workers": 0,
            "assigned_cpu_budget": 0,
            "cpu_thread_source": str(thread_policy.get("source", "") or "default"),
            "cpu_worker_source": str(worker_policy.get("source", "") or "default"),
        }
    if available < min_budget:
        return {
            "cpu_profile": profile,
            "assigned_cpu_threads": 0,
            "assigned_cpu_workers": 0,
            "assigned_cpu_budget": 0,
            "cpu_thread_source": str(thread_policy.get("source", "") or "default"),
            "cpu_worker_source": str(worker_policy.get("source", "") or "default"),
        }

    max_threads = max(min_threads, int(thread_policy.get("effective_max_threads", 0) or 0))
    max_workers = max(min_workers, int(worker_policy.get("effective_max_workers", 0) or 0))
    fixed_budget = max(0, int(thread_policy.get("reservation_threads", min_threads) or min_threads)) + max(
        0,
        int(worker_policy.get("reservation_workers", min_workers) or min_workers),
    )
    adaptive = str(thread_policy.get("mode", "fixed")) == "adaptive" or str(worker_policy.get("mode", "fixed")) == "adaptive"
    reserved = max(0, int(reserve_for_other_tasks or 0))
    target_budget = min(available, fixed_budget)
    if adaptive:
        target_budget = max(min_budget, available - reserved)
    target_budget = min(target_budget, max_threads + max_workers)

    assigned_threads = min_threads
    assigned_workers = min_workers
    remaining = max(0, target_budget - min_budget)

    def allocate_threads_first() -> None:
        nonlocal assigned_threads, assigned_workers, remaining
        thread_headroom = max(0, max_threads - assigned_threads)
        grant_threads = min(remaining, thread_headroom)
        assigned_threads += grant_threads
        remaining -= grant_threads
        worker_headroom = max(0, max_workers - assigned_workers)
        grant_workers = min(remaining, worker_headroom)
        assigned_workers += grant_workers
        remaining -= grant_workers

    def allocate_workers_first() -> None:
        nonlocal assigned_threads, assigned_workers, remaining
        worker_headroom = max(0, max_workers - assigned_workers)
        grant_workers = min(remaining, worker_headroom)
        assigned_workers += grant_workers
        remaining -= grant_workers
        thread_headroom = max(0, max_threads - assigned_threads)
        grant_threads = min(remaining, thread_headroom)
        assigned_threads += grant_threads
        remaining -= grant_threads

    if profile == "single":
        remaining = 0
    elif profile == "cpu_compute":
        allocate_threads_first()
    elif profile == "gpu_feeder":
        allocate_workers_first()
    else:
        while remaining > 0:
            thread_headroom = max(0, max_threads - assigned_threads)
            worker_headroom = max(0, max_workers - assigned_workers)
            if thread_headroom <= 0 and worker_headroom <= 0:
                break
            if worker_headroom > thread_headroom:
                assigned_workers += 1
            elif thread_headroom > worker_headroom:
                assigned_threads += 1
            elif coerce_non_negative_int(spec.get("gpu_slots", 0)) > 0 and worker_headroom > 0:
                assigned_workers += 1
            else:
                assigned_threads += 1
            remaining -= 1

    assigned_threads = min(assigned_threads, available)
    assigned_workers = min(assigned_workers, max(0, available - assigned_threads))
    return {
        "cpu_profile": profile,
        "assigned_cpu_threads": assigned_threads,
        "assigned_cpu_workers": assigned_workers,
        "assigned_cpu_budget": assigned_threads + assigned_workers,
        "cpu_thread_source": str(thread_policy.get("source", "") or "default"),
        "cpu_worker_source": str(worker_policy.get("source", "") or "default"),
    }


def cpu_resource_retry_reason(event: dict[str, Any], *, hooks: SchedulerResourceHooks) -> str:
    haystack = "\n".join(
        [
            str(event.get("failure_summary", "")),
            str(event.get("failure_excerpt", "")),
            str(event.get("log_tail", "")),
            str(event.get("launch_error", "")),
        ]
    ).lower()
    for pattern in hooks.cpu_resource_retry_patterns:
        if pattern in haystack:
            return pattern
    return ""


def next_cpu_backoff_threads(current_threads: int, min_threads: int) -> int:
    current = max(0, int(current_threads or 0))
    minimum = max(1, int(min_threads or 1))
    if current <= minimum:
        return minimum
    next_threads = max(minimum, current // 2)
    if next_threads >= current:
        next_threads = max(minimum, current - 1)
    return max(minimum, next_threads)


def detect_gpu_count(*, hooks: SchedulerResourceHooks) -> int:
    if hooks.shutil_which("nvidia-smi") == "":
        return 0
    completed = hooks.run_subprocess(
        ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader,nounits"],
        timeout=15,
    )
    if completed.returncode != 0:
        return 0
    return len([line for line in completed.stdout.splitlines() if line.strip()])


def parse_gpu_id_list(raw_value: Any) -> list[int]:
    if isinstance(raw_value, list):
        items: list[str] = []
        for item in raw_value:
            text = str(item).strip()
            if not text:
                continue
            items.extend(part.strip() for part in text.split(","))
    else:
        raw_text = str(raw_value or "").strip()
        if not raw_text:
            return []
        items = [item.strip() for item in raw_text.split(",")]
    gpu_ids: list[int] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        try:
            gpu_ids.append(int(text))
        except ValueError:
            continue
    seen: set[int] = set()
    deduped: list[int] = []
    for gpu_id in gpu_ids:
        if gpu_id in seen:
            continue
        seen.add(gpu_id)
        deduped.append(gpu_id)
    return deduped


def extract_inline_cuda_visible_devices(command: str) -> list[int]:
    match = re.search(r"(?:^|\s)CUDA_VISIBLE_DEVICES=([0-9,\s]+)", command)
    if not match:
        return []
    return parse_gpu_id_list(match.group(1))


def command_sets_cuda_visible_devices(command: str) -> bool:
    return bool(extract_inline_cuda_visible_devices(command))


def task_requested_gpu_ids(spec: dict[str, Any]) -> list[int]:
    assigned = parse_gpu_id_list(spec.get("assigned_gpus", []))
    if assigned:
        return assigned
    env = spec.get("env", {})
    if isinstance(env, dict):
        visible = parse_gpu_id_list(env.get("CUDA_VISIBLE_DEVICES", ""))
        if visible:
            return visible
    return extract_inline_cuda_visible_devices(str(spec.get("command", "")))


def gpu_row_free_mb(row: dict[str, Any]) -> int:
    return max(0, int(row.get("memory_total_mb", 0) or 0) - int(row.get("memory_used_mb", 0) or 0))


def default_gpu_min_free_mb(row: dict[str, Any], *, hooks: SchedulerResourceHooks) -> int:
    total_mb = int(row.get("memory_total_mb", 0) or 0)
    if total_mb <= 0:
        return hooks.default_gpu_min_free_mb
    return max(hooks.default_gpu_min_free_mb, int(total_mb * hooks.default_gpu_free_ratio))


def task_gpu_min_free_mb(spec: dict[str, Any], row: dict[str, Any], *, hooks: SchedulerResourceHooks) -> int:
    configured = int(spec.get("gpu_min_free_mb", 0) or 0)
    if configured > 0:
        return configured
    return default_gpu_min_free_mb(row, hooks=hooks)


def task_gpu_max_util_percent(spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> int:
    configured = int(spec.get("gpu_max_util_percent", 0) or 0)
    if configured > 0:
        return configured
    return hooks.default_gpu_max_util_percent


def gpu_row_can_host_task(row: dict[str, Any], spec: dict[str, Any], *, hooks: SchedulerResourceHooks) -> bool:
    return gpu_row_free_mb(row) >= task_gpu_min_free_mb(spec, row, hooks=hooks) and int(row.get("gpu_util_percent", 0) or 0) <= task_gpu_max_util_percent(spec, hooks=hooks)


def select_gpu_ids_for_task(
    spec: dict[str, Any],
    *,
    hooks: SchedulerResourceHooks,
    total_gpu_slots: int,
    gpu_rows: list[dict[str, Any]],
    reserved_gpu_ids: set[int],
) -> tuple[list[int] | None, str]:
    gpu_slots = max(0, int(spec.get("gpu_slots", 0) or 0))
    if gpu_slots <= 0:
        return [], ""
    if total_gpu_slots <= 0:
        return None, "no_gpu_capacity"

    requested = task_requested_gpu_ids(spec)
    allowed = parse_gpu_id_list(spec.get("allowed_gpus", []))
    row_by_index = {int(row.get("index", -1)): row for row in gpu_rows}
    if requested:
        if len(requested) < gpu_slots:
            return None, "fixed_gpu_count_too_small"
        chosen = requested[:gpu_slots]
        if any(gpu_id in reserved_gpu_ids for gpu_id in chosen):
            return None, "fixed_gpus_reserved"
        if any(gpu_id not in row_by_index for gpu_id in chosen):
            return None, "fixed_gpu_missing"
        if any(not gpu_row_can_host_task(row_by_index[gpu_id], spec, hooks=hooks) for gpu_id in chosen):
            return None, "fixed_gpu_headroom_insufficient"
        return chosen, "fixed"

    if not gpu_rows:
        return None, "gpu_snapshot_unavailable"

    candidates = [
        row
        for row in gpu_rows
        if int(row.get("index", -1)) not in reserved_gpu_ids
        and (not allowed or int(row.get("index", -1)) in allowed)
        and gpu_row_can_host_task(row, spec, hooks=hooks)
    ]
    candidates.sort(key=lambda row: (-gpu_row_free_mb(row), int(row.get("gpu_util_percent", 0) or 0), int(row.get("index", 0) or 0)))
    if len(candidates) < gpu_slots:
        if allowed:
            return None, "allowed_gpu_headroom_insufficient"
        return None, "insufficient_gpu_headroom"
    return sorted(int(row["index"]) for row in candidates[:gpu_slots]), "scheduler"
