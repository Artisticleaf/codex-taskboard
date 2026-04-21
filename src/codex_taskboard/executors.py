from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ExecutorRegistryHooks:
    read_json: Callable[[Path, Any], Any]
    parse_gpu_id_list: Callable[[Any], list[int]]
    normalize_task_id: Callable[[str], str]


def executor_registry_path(config: Any) -> Path:
    return Path(config.app_home) / "executors.json"


def normalize_posix_workdir(raw_path: str) -> str:
    text = str(raw_path or "").strip()
    if not text:
        return ""
    normalized = posixpath.normpath(text)
    if not normalized.startswith("/"):
        normalized = "/" + normalized.lstrip("/")
    return normalized


def normalize_executor_registry_payload(raw: Any, *, hooks: ExecutorRegistryHooks) -> dict[str, dict[str, Any]]:
    if isinstance(raw, dict) and "executors" in raw:
        raw = raw.get("executors", {})
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_value in raw.items():
        name = hooks.normalize_task_id(str(raw_name))
        if not name or not isinstance(raw_value, dict):
            continue
        payload = dict(raw_value)
        default_env = payload.get("default_env", {})
        if not isinstance(default_env, dict):
            default_env = {}
        normalized[name] = {
            "name": name,
            "type": str(payload.get("type", "ssh")).strip() or "ssh",
            "ssh_target": str(payload.get("ssh_target", "")).strip(),
            "ssh_identity_file": str(payload.get("ssh_identity_file", "")).strip(),
            "ssh_options": [str(item) for item in payload.get("ssh_options", []) if str(item).strip()],
            "remote_workdir": normalize_posix_workdir(str(payload.get("remote_workdir", "")).strip()),
            "remote_workdir_prefix": normalize_posix_workdir(str(payload.get("remote_workdir_prefix", "")).strip()),
            "remote_home": normalize_posix_workdir(str(payload.get("remote_home", "")).strip()),
            "remote_codex_home": normalize_posix_workdir(str(payload.get("remote_codex_home", "")).strip()),
            "remote_codex_bin": str(payload.get("remote_codex_bin", "codex")).strip() or "codex",
            "host_gpu_ids": hooks.parse_gpu_id_list(payload.get("host_gpu_ids", payload.get("assigned_gpus", []))),
            "remote_gpu_ids": hooks.parse_gpu_id_list(payload.get("remote_gpu_ids", [])),
            "default_feedback_mode": str(payload.get("default_feedback_mode", "off")).strip() or "off",
            "default_agent_name": str(payload.get("default_agent_name", name)).strip() or name,
            "default_env": {str(key): str(value) for key, value in default_env.items()},
        }
    return normalized


def load_executor_registry(config: Any, *, hooks: ExecutorRegistryHooks) -> dict[str, dict[str, Any]]:
    return normalize_executor_registry_payload(hooks.read_json(executor_registry_path(config), {}), hooks=hooks)


def resolve_executor(config: Any, executor_name: str, *, hooks: ExecutorRegistryHooks) -> dict[str, Any]:
    name = hooks.normalize_task_id(str(executor_name))
    registry = load_executor_registry(config, hooks=hooks)
    executor = registry.get(name)
    if not executor:
        raise ValueError(f"Unknown executor: {executor_name}")
    if str(executor.get("type", "ssh")) != "ssh":
        raise ValueError(f"Unsupported executor type for {executor_name}: {executor.get('type')}")
    if not str(executor.get("ssh_target", "")).strip():
        raise ValueError(f"Executor {executor_name} is missing ssh_target")
    return executor


def validate_remote_workdir(remote_workdir: str, remote_prefix: str) -> None:
    normalized_workdir = normalize_posix_workdir(remote_workdir)
    normalized_prefix = normalize_posix_workdir(remote_prefix)
    if normalized_prefix and not (
        normalized_workdir == normalized_prefix
        or normalized_workdir.startswith(normalized_prefix.rstrip("/") + "/")
    ):
        raise ValueError(f"remote workdir must stay under executor prefix {normalized_prefix}: {normalized_workdir}")


def executor_gpu_map(spec: dict[str, Any], *, parse_gpu_id_list: Callable[[Any], list[int]]) -> dict[int, int]:
    host_gpu_ids = parse_gpu_id_list(spec.get("executor_host_gpu_ids", []))
    remote_gpu_ids = parse_gpu_id_list(spec.get("executor_remote_gpu_ids", []))
    mapping: dict[int, int] = {}
    for index, host_gpu_id in enumerate(host_gpu_ids):
        if index >= len(remote_gpu_ids):
            break
        mapping[host_gpu_id] = remote_gpu_ids[index]
    return mapping


def map_host_gpus_to_executor_visible_gpus(
    spec: dict[str, Any],
    host_gpu_ids: list[int],
    *,
    parse_gpu_id_list: Callable[[Any], list[int]],
) -> list[int]:
    requested = parse_gpu_id_list(host_gpu_ids)
    if not requested:
        return []
    mapping = executor_gpu_map(spec, parse_gpu_id_list=parse_gpu_id_list)
    if not mapping:
        return requested
    visible: list[int] = []
    for host_gpu_id in requested:
        if host_gpu_id not in mapping:
            return []
        visible.append(mapping[host_gpu_id])
    return visible
