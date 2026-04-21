from __future__ import annotations

import re
from typing import Any


RUNNABLE_STATUSES = {"queued", "submitted"}


def normalize_task_id(raw_value: str) -> str:
    value = str(raw_value or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-.")


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


def api_token_is_admin(token_record: dict[str, Any]) -> bool:
    return str(token_record.get("role", "user")).strip().lower() == "admin"


def api_token_tenant(token_record: dict[str, Any]) -> str:
    return normalize_task_id(str(token_record.get("tenant", "")).strip())


def task_owner_tenant(state: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    spec = spec or {}
    return normalize_task_id(str(state.get("owner_tenant", spec.get("owner_tenant", ""))).strip())


def api_client_task_id(state: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    spec = spec or {}
    return normalize_task_id(str(state.get("client_task_id", spec.get("client_task_id", ""))).strip())


def api_client_task_key(state: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    spec = spec or {}
    return normalize_task_id(str(state.get("client_task_key", spec.get("client_task_key", ""))).strip())


def task_visible_to_api_token(state: dict[str, Any], spec: dict[str, Any] | None, token_record: dict[str, Any]) -> bool:
    if not parse_boolish(token_record.get("allow_read_results", True), default=True):
        return False
    if api_token_is_admin(token_record) and parse_boolish(token_record.get("allow_read_all_tasks", True), default=True):
        return True
    owner_tenant = task_owner_tenant(state, spec)
    if owner_tenant:
        return owner_tenant == api_token_tenant(token_record)
    token_executor = normalize_task_id(str(token_record.get("executor", "")).strip())
    task_executor = normalize_task_id(str(state.get("executor_name", (spec or {}).get("executor_name", ""))).strip())
    if token_executor and task_executor:
        return token_executor == task_executor
    return False


def api_token_can_read_global_queue(token_record: dict[str, Any]) -> bool:
    if api_token_is_admin(token_record) and parse_boolish(token_record.get("allow_read_all_tasks", True), default=True):
        return True
    return parse_boolish(token_record.get("allow_read_global_queue", False), default=False)


def task_visible_in_api_queue(state: dict[str, Any], spec: dict[str, Any] | None, token_record: dict[str, Any]) -> bool:
    if str(state.get("status", (spec or {}).get("status", ""))).strip() not in RUNNABLE_STATUSES:
        return False
    if api_token_can_read_global_queue(token_record):
        return True
    return task_visible_to_api_token(state, spec, token_record)


def apply_api_token_ownership(spec: dict[str, Any], token_record: dict[str, Any]) -> dict[str, Any]:
    updated = dict(spec)
    updated["owner_tenant"] = api_token_tenant(token_record)
    updated["owner_role"] = str(token_record.get("role", "user")).strip().lower() or "user"
    updated["owner_label"] = str(token_record.get("agent_name", "")).strip() or updated["owner_tenant"]
    updated["submitted_via_api"] = True
    return updated


def namespace_task_identifier(raw_value: str, owner_tenant: str) -> str:
    tenant = normalize_task_id(owner_tenant)
    value = normalize_task_id(raw_value)
    if not tenant:
        return value
    if not value:
        return tenant
    prefix = f"{tenant}."
    if value == tenant or value.startswith(prefix):
        return value
    return f"{prefix}{value}"


def apply_api_task_namespace(spec: dict[str, Any], token_record: dict[str, Any]) -> dict[str, Any]:
    updated = dict(spec)
    owner_tenant = normalize_task_id(str(updated.get("owner_tenant", "")).strip())
    if api_token_is_admin(token_record) or not owner_tenant:
        return updated
    client_task_id = normalize_task_id(str(updated.get("client_task_id", updated.get("task_id", ""))).strip())
    client_task_key = normalize_task_id(str(updated.get("client_task_key", updated.get("task_key", client_task_id))).strip())
    if client_task_id:
        updated["client_task_id"] = client_task_id
        updated["task_id"] = namespace_task_identifier(client_task_id, owner_tenant)
    if client_task_key:
        updated["client_task_key"] = client_task_key
        updated["task_key"] = namespace_task_identifier(client_task_key, owner_tenant)
    depends_on = updated.get("depends_on", [])
    if isinstance(depends_on, list) and depends_on:
        updated["depends_on"] = [namespace_task_identifier(str(item), owner_tenant) for item in depends_on if normalize_task_id(str(item))]
    return updated


def build_api_visibility_scope(token_record: dict[str, Any], *, view: str) -> str:
    if api_token_is_admin(token_record) and parse_boolish(token_record.get("allow_read_all_tasks", True), default=True):
        return "all"
    if str(view or "").strip().lower() == "queue" and api_token_can_read_global_queue(token_record):
        return "global_queue"
    return "tenant"


def is_public_queue_view(token_record: dict[str, Any], *, view: str) -> bool:
    return build_api_visibility_scope(token_record, view=view) == "global_queue"
