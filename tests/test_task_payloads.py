from __future__ import annotations

from codex_taskboard.task_payloads import (
    TaskPayloadHooks,
    normalize_task_spec_payload,
    normalize_task_state_payload,
)


def normalize_task_id(value: str) -> str:
    return str(value or "").strip().replace(" ", "-").lower()


def normalize_timestamp_fields(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    normalized["timestamp_marker"] = True
    return normalized


def make_hooks() -> TaskPayloadHooks:
    return TaskPayloadHooks(
        normalize_task_id=normalize_task_id,
        normalize_timestamp_fields=normalize_timestamp_fields,
    )


def test_normalize_task_spec_payload_applies_defaults() -> None:
    normalized = normalize_task_spec_payload(
        {
            "task_id": "Demo Task",
            "command": "python train.py",
            "execution_mode": "codex_subagent",
        },
        version=7,
        default_cpu_retry_max_attempts=5,
        default_startup_failure_seconds=123,
        hooks=make_hooks(),
    )

    assert normalized["version"] == 7
    assert normalized["task_key"] == "demo-task"
    assert normalized["command_template"] == "python train.py"
    assert normalized["require_signal_to_unblock"] is True
    assert normalized["cpu_retry_max_attempts"] == 5
    assert normalized["startup_failure_threshold_seconds"] == 123
    assert normalized["executor_remote_codex_bin"] == "codex"
    assert normalized["timestamp_marker"] is True


def test_normalize_task_state_payload_applies_defaults() -> None:
    normalized = normalize_task_state_payload(
        {
            "task_id": "Demo Task",
            "execution_mode": "codex_subagent",
        },
        version=9,
        default_cpu_retry_max_attempts=4,
        hooks=make_hooks(),
    )

    assert normalized["version"] == 9
    assert normalized["task_key"] == "demo-task"
    assert normalized["feedback_mode"] == "auto"
    assert normalized["require_signal_to_unblock"] is True
    assert normalized["cpu_retry_max_attempts"] == 4
    assert normalized["notification_signal"] == ""
    assert normalized["timestamp_marker"] is True
