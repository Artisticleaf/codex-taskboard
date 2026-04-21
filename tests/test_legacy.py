import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from codex_taskboard.cli import (
    AppConfig,
    LEGACY_READS_ENV,
    LEGACY_TASK_ROOT_ENV,
    build_config as build_runtime_config,
    command_migrate_legacy,
    discover_legacy_task_roots,
    iter_all_task_states,
    load_task_state,
    migrate_task_dir,
    resolve_legacy_root_args,
    task_runner_process_alive,
)


def build_config(app_home: Path) -> AppConfig:
    codex_home = app_home / "codex-home"
    return AppConfig(
        app_home=app_home,
        tasks_root=app_home / "tasks",
        locks_root=app_home / "locks",
        followups_root=app_home / "followups",
        legacy_task_roots=tuple(),
        tmux_socket_path=app_home / "tmux" / "default",
        codex_home=codex_home,
        threads_db_path=codex_home / "state_5.sqlite",
        thread_manifest_path=codex_home / "thread_sync_manifest.jsonl",
        sync_script_path=codex_home / "scripts" / "sync_codex_threads.py",
        codex_bin="codex",
        tmux_bin="tmux",
    )


class LegacyCompatibilityTests(unittest.TestCase):
    def test_discover_legacy_task_roots_includes_tmux_task_codex_wakeup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home_root = Path(tmpdir)
            user_home = home_root / "alice"
            app_home = user_home / ".local" / "state" / "codex-taskboard"
            (app_home / "tasks").mkdir(parents=True, exist_ok=True)
            legacy_tasks = user_home / ".codex" / "tmux-task-codex-wakeup" / "tasks"
            legacy_tasks.mkdir(parents=True, exist_ok=True)

            discovered = discover_legacy_task_roots(
                app_home,
                codex_home=user_home / ".codex",
                home_root=home_root,
            )

            self.assertIn(legacy_tasks.resolve(), discovered)
            self.assertNotIn((app_home / "tasks").resolve(), discovered)

    def test_build_config_accepts_legacy_root_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "app-home"
            codex_home = Path(tmpdir) / "codex-home"
            legacy_tasks = Path(tmpdir) / "legacy" / "tasks"
            legacy_tasks.mkdir(parents=True, exist_ok=True)
            args = Namespace(
                app_home=str(app_home),
                codex_home=str(codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
            )

            with patch.dict(os.environ, {LEGACY_TASK_ROOT_ENV: str(legacy_tasks)}, clear=False):
                config = build_runtime_config(args)

            self.assertIn(legacy_tasks.resolve(), config.legacy_task_roots)

    def test_resolve_legacy_root_args_normalizes_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_root = Path(tmpdir) / "legacy" / "tasks"
            legacy_root.mkdir(parents=True, exist_ok=True)

            resolved = resolve_legacy_root_args([str(legacy_root.parent), str(legacy_root)])

            self.assertEqual(resolved, (legacy_root.resolve(),))

    def test_task_runner_process_alive_accepts_legacy_task_bridge_runner(self) -> None:
        state = {
            "task_id": "legacy-running-task",
            "pid": 424242,
            "paths": {
                "spec_path": "/tmp/legacy-running-task/spec.json",
            },
        }

        with patch("codex_taskboard.cli.pid_exists", return_value=True), patch(
            "codex_taskboard.cli.read_pid_cmdline",
            return_value="python3 /home/alice/.codex/skills/tmux-task-codex-wakeup/scripts/task_bridge.py run --spec-file /tmp/legacy-running-task/spec.json",
        ):
            self.assertTrue(task_runner_process_alive(state))

    def test_load_task_state_reconciles_stale_running_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = build_config(Path(tmpdir))
            task_dir = config.tasks_root / "stale-task"
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "task_id": "stale-task",
                        "task_key": "stale-task",
                        "status": "running",
                        "tmux_session_name": "ctb-stale-task",
                        "pid": 999999,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.tmux_session_exists", return_value=False):
                state = load_task_state(config, "stale-task")

            self.assertEqual(state["status"], "terminated")
            self.assertTrue(state["needs_attention"])
            self.assertEqual(state["attention_reason"], "stale_state:supervisor_missing")

    def test_migrate_active_legacy_task_creates_symlink_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "app"
            config = build_config(app_home)
            source_root = Path(tmpdir) / "legacy" / "tasks"
            task_dir = source_root / "legacy-live"
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "task_id": "legacy-live",
                        "task_key": "legacy-live",
                        "status": "running",
                        "started_at": "2026-03-19T00:00:00Z",
                        "tmux_session_name": "cxwake-legacy-live",
                        "pid": 10101,
                        "paths": {
                            "task_root": str(task_dir),
                            "spec_path": str(task_dir / "spec.json"),
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (task_dir / "spec.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "task_id": "legacy-live",
                        "task_key": "legacy-live",
                        "workdir": str(app_home),
                        "command": "sleep 10",
                        "codex_session_id": "session-1",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch("codex_taskboard.cli.tmux_session_exists", return_value=True):
                result = migrate_task_dir(config, source_root=source_root, task_dir=task_dir)

            destination = config.tasks_root / "legacy-live"
            self.assertEqual(result["bridge_mode"], "symlink_bridge")
            self.assertTrue(destination.exists())
            self.assertTrue(task_dir.is_symlink())
            self.assertEqual(task_dir.resolve(), destination.resolve())
            migrated_state = json.loads((destination / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(migrated_state["paths"]["task_root"], str(destination))
            self.assertEqual(migrated_state["legacy_bridge_mode"], "symlink_bridge")

    def test_iter_all_task_states_dedupes_legacy_symlink_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "app"
            config = build_config(app_home)
            config = AppConfig(
                app_home=config.app_home,
                tasks_root=config.tasks_root,
                locks_root=config.locks_root,
                followups_root=config.followups_root,
                legacy_task_roots=(Path(tmpdir) / "legacy" / "tasks",),
                tmux_socket_path=config.tmux_socket_path,
                codex_home=config.codex_home,
                threads_db_path=config.threads_db_path,
                thread_manifest_path=config.thread_manifest_path,
                sync_script_path=config.sync_script_path,
                codex_bin=config.codex_bin,
                tmux_bin=config.tmux_bin,
            )
            destination = config.tasks_root / "dup-task"
            destination.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "task_id": "dup-task",
                "task_key": "dup-task",
                "status": "completed",
            }
            (destination / "state.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            (destination / "spec.json").write_text("{}\n", encoding="utf-8")
            legacy_root = config.legacy_task_roots[0]
            legacy_root.mkdir(parents=True, exist_ok=True)
            os.symlink(str(destination), str(legacy_root / "dup-task"))

            with patch.dict(os.environ, {LEGACY_READS_ENV: "1"}, clear=False):
                states = iter_all_task_states(config)

            self.assertEqual([state["task_id"] for state in states], ["dup-task"])

    def test_iter_all_task_states_ignores_legacy_roots_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "app"
            config = build_config(app_home)
            legacy_root = Path(tmpdir) / "legacy" / "tasks"
            legacy_root.mkdir(parents=True, exist_ok=True)
            config = AppConfig(
                app_home=config.app_home,
                tasks_root=config.tasks_root,
                locks_root=config.locks_root,
                followups_root=config.followups_root,
                legacy_task_roots=(legacy_root,),
                tmux_socket_path=config.tmux_socket_path,
                codex_home=config.codex_home,
                threads_db_path=config.threads_db_path,
                thread_manifest_path=config.thread_manifest_path,
                sync_script_path=config.sync_script_path,
                codex_bin=config.codex_bin,
                tmux_bin=config.tmux_bin,
            )
            task_dir = legacy_root / "legacy-only"
            task_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "task_id": "legacy-only",
                "task_key": "legacy-only",
                "status": "completed",
            }
            (task_dir / "state.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            (task_dir / "spec.json").write_text("{}\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                states = iter_all_task_states(config)

            self.assertEqual(states, [])

    def test_command_migrate_legacy_requires_explicit_root_or_all_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_home = Path(tmpdir) / "app"
            codex_home = Path(tmpdir) / "codex-home"
            args = Namespace(
                app_home=str(app_home),
                codex_home=str(codex_home),
                codex_bin="codex",
                tmux_bin="tmux",
                legacy_root=None,
                all_discovered=False,
            )

            self.assertEqual(command_migrate_legacy(args), 1)


if __name__ == "__main__":
    unittest.main()
