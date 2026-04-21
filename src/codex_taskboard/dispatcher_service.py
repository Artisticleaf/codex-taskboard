from __future__ import annotations

import json
import signal
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DispatcherServiceHooks:
    dispatch_queued_tasks: Callable[..., dict[str, Any]]
    process_followups: Callable[[Any], Any]


def serve_dispatcher_loop(
    config: Any,
    *,
    mode: str,
    max_running: int,
    dispatch_limit: int,
    gpu_count_override: int,
    cpu_thread_limit: int,
    poll_seconds: float,
    verbose: bool,
    hooks: DispatcherServiceHooks,
) -> int:
    keep_running = {"value": True}

    def stop(_sig: int, _frame: Any) -> None:
        keep_running["value"] = False

    previous_handlers: list[tuple[int, Any]] = []
    for sig_name in ("SIGINT", "SIGTERM"):
        if not hasattr(signal, sig_name):
            continue
        signum = getattr(signal, sig_name)
        previous_handlers.append((signum, signal.getsignal(signum)))
        signal.signal(signum, stop)

    try:
        while keep_running["value"]:
            result = hooks.dispatch_queued_tasks(
                config,
                mode=mode,
                max_running=max_running,
                limit=dispatch_limit,
                gpu_count_override=gpu_count_override,
                cpu_thread_limit=cpu_thread_limit,
            )
            followup_result = hooks.process_followups(config)
            if verbose:
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if followup_result:
                    print(json.dumps({"followups": followup_result}, ensure_ascii=False), flush=True)
            time.sleep(max(0.0, float(poll_seconds)))
    finally:
        for signum, handler in previous_handlers:
            try:
                signal.signal(signum, handler)
            except Exception:
                pass
    return 0
