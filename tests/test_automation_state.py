from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_taskboard.automation_state import (
    AutomationStateHooks,
    clear_continuous_research_mode_session,
    continuous_research_enabled_session_ids,
    human_guidance_mode_active,
    load_continuous_research_mode,
    load_human_guidance_mode,
    set_continuous_research_mode,
    set_human_guidance_mode,
)


@dataclass(frozen=True)
class DummyConfig:
    app_home: Path


def parse_boolish(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_timestamp_to_unix(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    return datetime.fromisoformat(text).timestamp()


def format_unix_timestamp(value: float) -> str:
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()


def make_hooks() -> AutomationStateHooks:
    return AutomationStateHooks(
        read_json=read_json,
        atomic_write_json=atomic_write_json,
        normalize_timestamp_fields=lambda payload: dict(payload),
        parse_boolish=parse_boolish,
        current_thread_info=lambda _config, environ=None: {"current_codex_session_id": "session-current", "resolved_from": "env"},
        utc_now=lambda: "2026-04-21T10:00:00+00:00",
        canonicalize_taskboard_signal=lambda value: str(value or "").strip().upper(),
        parse_timestamp_to_unix=parse_timestamp_to_unix,
        format_unix_timestamp=format_unix_timestamp,
        retry_after_seconds_from_target=lambda target: max(1, int(round(float(target) - datetime.now(tz=timezone.utc).timestamp()))),
        continuous_research_mode_filename="continuous.json",
        human_guidance_mode_filename="human.json",
        default_human_guidance_lease_seconds=900,
        continuous_research_idle_loop_threshold=3,
        continuous_research_override_signals={"CLOSEOUT_READY", "STOP_AUTOMATION"},
    )


def test_continuous_research_state_round_trip(tmp_path: Path) -> None:
    config = DummyConfig(tmp_path)
    hooks = make_hooks()

    payload = set_continuous_research_mode(
        config,
        hooks=hooks,
        enabled=True,
        codex_session_id="session-a",
        updated_by="test",
        source="unit",
    )
    assert payload["enabled"] is True
    assert payload["target_codex_session_id"] == "session-a"
    assert continuous_research_enabled_session_ids(config, hooks=hooks) == ["session-a"]

    cleared = clear_continuous_research_mode_session(config, hooks=hooks, codex_session_id="session-a")
    assert cleared["sessions"] == {}
    assert load_continuous_research_mode(config, hooks=hooks, codex_session_id="session-a")["enabled"] is False


def test_human_guidance_state_round_trip(tmp_path: Path) -> None:
    config = DummyConfig(tmp_path)
    hooks = make_hooks()

    payload = set_human_guidance_mode(
        config,
        hooks=hooks,
        active=True,
        codex_session_id="session-h",
        lease_seconds=600,
        reason="manual steer",
    )
    assert payload["active"] is True
    assert human_guidance_mode_active(config, hooks=hooks, codex_session_id="session-h") is True

    loaded = load_human_guidance_mode(config, hooks=hooks, codex_session_id="session-h")
    assert loaded["target_session_state"]["reason"] == "manual steer"
