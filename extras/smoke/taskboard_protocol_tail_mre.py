#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from codex_taskboard.cli import (
    AppConfig,
    build_config,
    current_thread_info,
    extract_taskboard_protocol_footer,
    extract_taskboard_signal,
    iter_all_task_states,
    latest_local_assistant_message_for_session,
    load_continuous_research_mode,
    load_human_guidance_mode,
    parse_timestamp_to_unix,
    taskboard_protocol_requires_repair,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Minimal reproducible inspection for taskboard protocol-tail parsing. "
            "This script is read-only."
        )
    )
    parser.add_argument(
        "--app-home",
        default=os.environ.get("CODEX_TASKBOARD_HOME", str(Path.home() / ".local" / "state" / "codex-taskboard")),
    )
    parser.add_argument(
        "--codex-home",
        default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
    )
    parser.add_argument(
        "--codex-bin",
        default=os.environ.get("CODEX_BIN", "codex"),
    )
    parser.add_argument(
        "--tmux-bin",
        default=os.environ.get("TMUX_BIN", "tmux"),
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="Override the inspected Codex session id. Defaults to current-thread resolution.",
    )
    parser.add_argument(
        "--message-tail-lines",
        type=int,
        default=12,
        help="How many lines of the latest assistant message tail to print.",
    )
    return parser.parse_args()


def build_local_config(args: argparse.Namespace) -> AppConfig:
    return build_config(
        argparse.Namespace(
            app_home=args.app_home,
            codex_home=args.codex_home,
            codex_bin=args.codex_bin,
            tmux_bin=args.tmux_bin,
        )
    )


def load_followups_for_session(config: AppConfig, session_id: str) -> list[dict[str, Any]]:
    if not session_id or not config.followups_root.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(config.followups_root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(payload.get("codex_session_id", "")).strip() != session_id:
            continue
        payload["_path"] = str(path)
        items.append(payload)
    items.sort(key=lambda item: parse_timestamp_to_unix(item.get("updated_at")) or 0.0, reverse=True)
    return items


def load_tasks_for_session(config: AppConfig, session_id: str) -> list[dict[str, Any]]:
    if not session_id:
        return []
    items = [state for state in iter_all_task_states(config) if str(state.get("codex_session_id", "")).strip() == session_id]
    items.sort(key=lambda item: parse_timestamp_to_unix(item.get("updated_at")) or 0.0, reverse=True)
    return items


def summarize_message(text: str, tail_lines: int) -> dict[str, Any]:
    footer = extract_taskboard_protocol_footer(text)
    signal = extract_taskboard_signal(text)
    lines = [line for line in text.splitlines() if line.strip()]
    tail = lines[-max(1, tail_lines) :] if lines else []
    return {
        "line_count": len(lines),
        "tail_lines": tail,
        "taskboard_signal": signal,
        "protocol_footer": footer,
        "needs_repair": taskboard_protocol_requires_repair(footer, signal_value=signal),
    }


def sample_protocol_cases() -> list[dict[str, Any]]:
    samples = {
        "commentary_only": "continuing local inspection\n",
        "footer_only_none": "\n".join(
            [
                "TASKBOARD_PROTOCOL_ACK=TBP1",
                "CURRENT_STEP_CLASS=inline_now",
                "TASKBOARD_SELF_CHECK=pass",
                "LIVE_TASK_STATUS=none",
                "FINAL_SIGNAL=none",
            ]
        ),
        "footer_only_waiting": "\n".join(
            [
                "TASKBOARD_PROTOCOL_ACK=TBP1",
                "CURRENT_STEP_CLASS=async_task",
                "TASKBOARD_SELF_CHECK=pass",
                "LIVE_TASK_STATUS=awaiting",
                "FINAL_SIGNAL=WAITING_ON_ASYNC",
            ]
        ),
        "signal_only": "TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH\n",
        "broken_footer": "\n".join(
            [
                "TASKBOARD_PROTOCOL_ACK=TBP1",
                "CURRENT_STEP_CLASS=inline_now",
                "FINAL_SIGNAL=none",
            ]
        ),
    }
    rows: list[dict[str, Any]] = []
    for name, text in samples.items():
        footer = extract_taskboard_protocol_footer(text)
        signal = extract_taskboard_signal(text)
        rows.append(
            {
                "sample": name,
                "taskboard_signal": signal,
                "protocol_footer": footer,
                "needs_repair": taskboard_protocol_requires_repair(footer, signal_value=signal),
            }
        )
    return rows


def main() -> int:
    args = parse_args()
    config = build_local_config(args)
    current = current_thread_info(config, os.environ) or {}
    session_id = str(args.session_id or current.get("current_codex_session_id") or "").strip()
    continuous = load_continuous_research_mode(config)
    human_guidance = load_human_guidance_mode(config)
    tasks = load_tasks_for_session(config, session_id)
    followups = load_followups_for_session(config, session_id)
    last_message = latest_local_assistant_message_for_session(config, session_id) if session_id else ""
    last_message_summary = summarize_message(last_message, args.message_tail_lines) if last_message else {}

    payload = {
        "current_thread": current,
        "inspected_session_id": session_id,
        "continuous_mode_target": str(continuous.get("target_codex_session_id", "")).strip(),
        "continuous_mode_enabled_sessions": [
            str(item).strip() for item in continuous.get("enabled_sessions", []) if str(item).strip()
        ],
        "human_guidance_target": str(human_guidance.get("target_codex_session_id", "")).strip(),
        "task_count_for_session": len(tasks),
        "followup_count_for_session": len(followups),
        "recent_tasks": [
            {
                "task_id": str(item.get("task_id", "")).strip(),
                "status": str(item.get("status", "")).strip(),
                "updated_at": item.get("updated_at"),
                "followup_status": str(item.get("followup_status", "")).strip(),
                "followup_last_action": str(item.get("followup_last_action", "")).strip(),
            }
            for item in tasks[:5]
        ],
        "recent_followups": [
            {
                "followup_key": str(item.get("followup_key", "")).strip(),
                "followup_type": str(item.get("followup_type", "")).strip(),
                "reason": str(item.get("reason", "")).strip(),
                "updated_at": item.get("updated_at"),
                "path": str(item.get("_path", "")).strip(),
            }
            for item in followups[:5]
        ],
        "latest_assistant_message": last_message_summary,
        "mre_samples": sample_protocol_cases(),
        "diagnosis": {
            "current_session_matches_continuous_target": session_id
            and session_id == str(continuous.get("target_codex_session_id", "")).strip(),
            "current_session_has_taskboard_entities": bool(tasks or followups),
            "latest_message_has_parseable_signal": bool(last_message_summary.get("taskboard_signal")),
            "latest_message_needs_protocol_repair": bool(last_message_summary.get("needs_repair", False)),
            "note": (
                "Taskboard parses protocol tail when it resumes a bound session or processes followup/task feedback. "
                "It does not continuously consume arbitrary VSCode chat turns from unrelated sessions."
            ),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
