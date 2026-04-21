from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from codex_taskboard.api_access import normalize_task_id, parse_boolish


@dataclass(frozen=True)
class ApiAuthHooks:
    read_json: Callable[[Path, Any], Any]
    api_token_registry_path: Callable[[Any], Path]


def hash_api_token_value(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def normalize_api_token_hash(raw_hash: Any) -> str:
    text = str(raw_hash or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1]
    if re.fullmatch(r"[0-9a-f]{64}", text):
        return text
    return ""


def normalize_api_token_registry_payload(raw: Any) -> dict[str, dict[str, Any]]:
    entries = raw.get("tokens", raw) if isinstance(raw, dict) else raw
    normalized: dict[str, dict[str, Any]] = {}
    if isinstance(entries, list):
        iterator = entries
    elif isinstance(entries, dict):
        iterator = []
        for token, value in entries.items():
            item = dict(value) if isinstance(value, dict) else {}
            item.setdefault("token", token)
            iterator.append(item)
    else:
        return normalized
    for raw_item in iterator:
        if not isinstance(raw_item, dict):
            continue
        token = str(raw_item.get("token", "")).strip()
        token_hash = normalize_api_token_hash(raw_item.get("token_hash", ""))
        if not token_hash and token:
            token_hash = hash_api_token_value(token)
        if not token_hash:
            continue
        executor = normalize_task_id(str(raw_item.get("executor", "")).strip())
        role = str(raw_item.get("role", "user")).strip().lower() or "user"
        if role not in {"admin", "user"}:
            role = "user"
        tenant = normalize_task_id(str(raw_item.get("tenant", "")).strip()) or executor or f"token-{token_hash[:12]}"
        normalized[token_hash] = {
            "token_hash": token_hash,
            "executor": executor,
            "tenant": tenant,
            "role": role,
            "default_feedback_mode": str(raw_item.get("default_feedback_mode", "off")).strip() or "off",
            "agent_name": str(raw_item.get("agent_name", "")).strip(),
            "allow_submit_job": parse_boolish(raw_item.get("allow_submit_job", True), default=True),
            "allow_read_results": parse_boolish(raw_item.get("allow_read_results", True), default=True),
            "allow_read_all_tasks": parse_boolish(raw_item.get("allow_read_all_tasks", role == "admin"), default=(role == "admin")),
            "allow_read_global_queue": parse_boolish(raw_item.get("allow_read_global_queue", role == "admin"), default=(role == "admin")),
            "allow_session_feedback": parse_boolish(raw_item.get("allow_session_feedback", False), default=False),
            "allow_dangerous_codex_exec": parse_boolish(raw_item.get("allow_dangerous_codex_exec", False), default=False),
        }
    return normalized


def load_api_token_registry(config: Any, *, hooks: ApiAuthHooks) -> dict[str, dict[str, Any]]:
    return normalize_api_token_registry_payload(hooks.read_json(hooks.api_token_registry_path(config), {}))


def resolve_api_token(config: Any, token: str, *, hooks: ApiAuthHooks) -> dict[str, Any] | None:
    token_text = str(token or "").strip()
    if not token_text:
        return None
    token_hash = hash_api_token_value(token_text)
    for record in load_api_token_registry(config, hooks=hooks).values():
        stored_hash = normalize_api_token_hash(record.get("token_hash", ""))
        if stored_hash and hmac.compare_digest(stored_hash, token_hash):
            return dict(record)
    return None
