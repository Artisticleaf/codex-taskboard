#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import sys
from datetime import timedelta

import torch
import torch.distributed as dist


def sanitize(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-," else "_" for ch in text).strip("_")


def rank_zero() -> bool:
    return int(os.environ.get("RANK", "0") or 0) == 0


def fail(message: str) -> int:
    if rank_zero():
        print("RESULT=failed", flush=True)
        print(f"ERROR={sanitize(message)}", flush=True)
    raise RuntimeError(message)


def main() -> int:
    expected_world_size = int(os.environ.get("TASKBOARD_EXPECTED_GPUS", "4") or 4)
    visible_gpus = [item.strip() for item in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",") if item.strip()]
    rank = int(os.environ.get("RANK", "0") or 0)
    local_rank = int(os.environ.get("LOCAL_RANK", "0") or 0)
    world_size = int(os.environ.get("WORLD_SIZE", "1") or 1)

    if world_size != expected_world_size:
        return fail(f"expected world_size={expected_world_size}, got {world_size}")
    if len(visible_gpus) != expected_world_size:
        return fail(f"expected {expected_world_size} visible gpus, got {len(visible_gpus)} from CUDA_VISIBLE_DEVICES")
    if not torch.cuda.is_available():
        return fail("torch cuda is not available")
    if torch.cuda.device_count() != expected_world_size:
        return fail(f"expected torch.cuda.device_count={expected_world_size}, got {torch.cuda.device_count()}")

    torch.cuda.set_device(local_rank)
    dist.init_process_group(backend="nccl", timeout=timedelta(minutes=2))
    try:
        value = torch.tensor([rank + 1], device="cuda", dtype=torch.float32)
        dist.all_reduce(value)
        expected_sum = expected_world_size * (expected_world_size + 1) // 2
        if int(value.item()) != expected_sum:
            return fail(f"unexpected all_reduce result {value.item()} expected {expected_sum}")
        dist.barrier()
        if rank_zero():
            print("RESULT=ok", flush=True)
            print(f"WORLD_SIZE={world_size}", flush=True)
            print(f"VISIBLE_GPUS={','.join(visible_gpus)}", flush=True)
            print(f"LOCAL_DEVICE_COUNT={torch.cuda.device_count()}", flush=True)
            print(f"HOSTNAME={sanitize(socket.gethostname())}", flush=True)
            print(f"SUM_CHECK={expected_sum}", flush=True)
        dist.barrier()
    finally:
        dist.destroy_process_group()
    return 0


if __name__ == "__main__":
    sys.exit(main())
