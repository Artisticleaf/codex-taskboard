from __future__ import annotations

from types import SimpleNamespace

from codex_taskboard.scheduler_resources import (
    SchedulerResourceHooks,
    detect_gpu_count,
    parse_gpu_id_list,
    resolve_cpu_thread_policy,
    resolve_cpu_worker_policy,
    select_cpu_resources_for_start,
    select_gpu_ids_for_task,
)


def make_hooks() -> SchedulerResourceHooks:
    return SchedulerResourceHooks(
        looks_like_training_command=lambda command: "train" in str(command),
        shutil_which=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else "",
        run_subprocess=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="0\n1\n", stderr=""),
        cpu_profile_choices=("auto", "single", "sidecar", "cpu_compute", "gpu_feeder", "hybrid"),
        cpu_thread_env_keys=("OMP_NUM_THREADS", "MKL_NUM_THREADS"),
        cpu_worker_env_keys=("NUM_WORKERS",),
        cpu_resource_retry_patterns=("resource temporarily unavailable",),
        default_cpu_thread_limit=40,
        default_cpu_only_threads=8,
        default_gpu_task_cpu_threads=4,
        default_subagent_cpu_threads=1,
        default_generic_task_cpu_threads=2,
        default_gpu_min_free_mb=4096,
        default_gpu_free_ratio=0.25,
        default_gpu_max_util_percent=85,
    )


def test_parse_gpu_id_list_dedupes_and_ignores_invalid() -> None:
    assert parse_gpu_id_list("0, 2,2, x, 5") == [0, 2, 5]
    assert parse_gpu_id_list(["1,2", "2", 3]) == [1, 2, 3]


def test_cpu_policy_and_assignment_prefers_workers_for_gpu_feeder() -> None:
    hooks = make_hooks()
    spec = {
        "gpu_slots": 1,
        "cpu_profile": "gpu_feeder",
        "cpu_threads_min": 2,
        "cpu_threads_max": 4,
        "cpu_workers_min": 1,
        "cpu_workers_max": 3,
    }

    thread_policy = resolve_cpu_thread_policy(spec, hooks=hooks, cpu_thread_limit=8)
    worker_policy = resolve_cpu_worker_policy(spec, hooks=hooks, cpu_thread_limit=8)
    assigned = select_cpu_resources_for_start(spec, hooks=hooks, available_cpu_threads=8, reserve_for_other_tasks=2)

    assert thread_policy["min_threads"] == 2
    assert worker_policy["min_workers"] == 1
    assert assigned["assigned_cpu_workers"] >= 1
    assert assigned["assigned_cpu_budget"] >= 3


def test_gpu_selection_uses_scheduler_or_fixed_mapping() -> None:
    hooks = make_hooks()
    gpu_rows = [
        {"index": 0, "memory_total_mb": 24000, "memory_used_mb": 1000, "gpu_util_percent": 10},
        {"index": 1, "memory_total_mb": 24000, "memory_used_mb": 2000, "gpu_util_percent": 20},
        {"index": 2, "memory_total_mb": 24000, "memory_used_mb": 22000, "gpu_util_percent": 95},
    ]

    selected, reason = select_gpu_ids_for_task(
        {"gpu_slots": 2},
        hooks=hooks,
        total_gpu_slots=3,
        gpu_rows=gpu_rows,
        reserved_gpu_ids={2},
    )
    assert selected == [0, 1]
    assert reason == "scheduler"

    fixed_selected, fixed_reason = select_gpu_ids_for_task(
        {"gpu_slots": 1, "assigned_gpus": [1]},
        hooks=hooks,
        total_gpu_slots=3,
        gpu_rows=gpu_rows,
        reserved_gpu_ids=set(),
    )
    assert fixed_selected == [1]
    assert fixed_reason == "fixed"


def test_detect_gpu_count_uses_nvidia_smi_output() -> None:
    assert detect_gpu_count(hooks=make_hooks()) == 2
