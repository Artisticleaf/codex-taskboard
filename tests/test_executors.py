from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from codex_taskboard.executors import (
    ExecutorRegistryHooks,
    load_executor_registry,
    map_host_gpus_to_executor_visible_gpus,
    normalize_posix_workdir,
    resolve_executor,
    validate_remote_workdir,
)


@dataclass(frozen=True)
class DummyConfig:
    app_home: Path


def parse_gpu_id_list(raw: object) -> list[int]:
    if isinstance(raw, list):
        return [int(item) for item in raw]
    return []


def normalize_task_id(value: str) -> str:
    return str(value or "").strip().replace(" ", "-").lower()


def make_hooks() -> ExecutorRegistryHooks:
    return ExecutorRegistryHooks(
        read_json=lambda path, default: json.loads(path.read_text(encoding="utf-8")) if path.exists() else default,
        parse_gpu_id_list=parse_gpu_id_list,
        normalize_task_id=normalize_task_id,
    )


def test_normalize_posix_workdir_normalizes_relative_and_slashes() -> None:
    assert normalize_posix_workdir("workspace/project") == "/workspace/project"
    assert normalize_posix_workdir("/workspace/../tmp/job") == "/tmp/job"


def test_load_and_resolve_executor_registry() -> None:
    with pytest.raises(ValueError, match="Unknown executor"):
        resolve_executor(DummyConfig(Path("/tmp/missing")), "missing", hooks=make_hooks())


def test_resolve_executor_returns_normalized_executor(tmp_path: Path) -> None:
    config = DummyConfig(tmp_path)
    registry_path = tmp_path / "executors.json"
    registry_path.write_text(
        json.dumps(
            {
                "executors": {
                    "JU Rootless": {
                        "type": "ssh",
                        "ssh_target": "ju@127.0.0.1",
                        "remote_workdir": "workspace/project",
                        "remote_workdir_prefix": "/workspace",
                        "remote_codex_home": "home/ju/.codex",
                        "host_gpu_ids": [0, 2],
                        "remote_gpu_ids": [3, 5],
                        "default_env": {"HF_HOME": "/workspace/.cache/hf"},
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    registry = load_executor_registry(config, hooks=make_hooks())
    assert sorted(registry) == ["ju-rootless"]
    assert registry["ju-rootless"]["remote_workdir"] == "/workspace/project"
    assert registry["ju-rootless"]["remote_codex_home"] == "/home/ju/.codex"

    executor = resolve_executor(config, "JU Rootless", hooks=make_hooks())
    assert executor["ssh_target"] == "ju@127.0.0.1"
    assert executor["host_gpu_ids"] == [0, 2]
    assert executor["remote_gpu_ids"] == [3, 5]


def test_map_host_gpus_to_executor_visible_gpus_returns_empty_on_partial_miss() -> None:
    spec = {
        "executor_host_gpu_ids": [0, 2],
        "executor_remote_gpu_ids": [3, 5],
    }
    assert map_host_gpus_to_executor_visible_gpus(spec, [0, 2], parse_gpu_id_list=parse_gpu_id_list) == [3, 5]
    assert map_host_gpus_to_executor_visible_gpus(spec, [0, 1], parse_gpu_id_list=parse_gpu_id_list) == []


def test_validate_remote_workdir_rejects_escape() -> None:
    validate_remote_workdir("/workspace/project/run-1", "/workspace")
    with pytest.raises(ValueError, match="remote workdir must stay under executor prefix"):
        validate_remote_workdir("/tmp/outside", "/workspace")
