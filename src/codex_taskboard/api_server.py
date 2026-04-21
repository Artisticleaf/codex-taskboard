from __future__ import annotations

import json
import signal
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class ApiServerHooks:
    default_poll_seconds: float
    resolve_token: Callable[[str], dict[str, Any] | None]
    build_task_list_payload: Callable[[dict[str, Any], str, str, int, str], dict[str, Any]]
    build_task_result_payload: Callable[[str, dict[str, Any]], dict[str, Any]]
    wait_for_result_payload: Callable[[str, float, float, dict[str, Any]], dict[str, Any] | None]
    submit_job: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    append_log: Callable[[str], None]
    normalize_task_id: Callable[[str], str]
    api_token_tenant: Callable[[dict[str, Any]], str]


def extract_api_bearer_token(handler: BaseHTTPRequestHandler) -> str:
    auth = str(handler.headers.get("Authorization", "")).strip()
    if auth.lower().startswith("bearer "):
        return auth.split(None, 1)[1].strip()
    return str(handler.headers.get("X-Taskboard-Token", "")).strip()


def read_api_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw_length = str(handler.headers.get("Content-Length", "0")).strip() or "0"
    try:
        length = max(0, int(raw_length))
    except ValueError as exc:
        raise ValueError("Invalid Content-Length header") from exc
    raw_body = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def write_api_json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_result_denied_response(
    handler: BaseHTTPRequestHandler,
    hooks: ApiServerHooks,
    *,
    path: str,
    token_record: dict[str, Any],
    task_id: str,
) -> None:
    normalized_task_id = hooks.normalize_task_id(task_id)
    hooks.append_log(
        f"result_denied path={path} tenant={hooks.api_token_tenant(token_record)} task_id={normalized_task_id}"
    )
    write_api_json_response(handler, 404, {"ok": False, "error": f"Task not found: {normalized_task_id}"})


def build_api_handler(hooks: ApiServerHooks) -> type[BaseHTTPRequestHandler]:
    class TaskboardApiHandler(BaseHTTPRequestHandler):
        server_version = "codex-taskboard-api/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            hooks.append_log(format % args)

        def _authenticate(self) -> dict[str, Any] | None:
            token = extract_api_bearer_token(self)
            token_record = hooks.resolve_token(token)
            if token_record is None:
                write_api_json_response(self, 401, {"ok": False, "error": "unauthorized"})
                return None
            return token_record

        def do_GET(self) -> None:  # noqa: N802
            token_record = self._authenticate()
            if token_record is None:
                return
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            task_id = str((query.get("task_id") or [""])[0]).strip()
            if parsed.path in {"/tasks", "/queue"}:
                try:
                    raw_limit = (query.get("limit") or [30])[0] or 30
                    limit = int(raw_limit)
                except ValueError:
                    write_api_json_response(self, 400, {"ok": False, "error": "invalid limit"})
                    return
                default_status = "queued" if parsed.path == "/queue" else "all"
                status_filter = str((query.get("status") or [default_status])[0]).strip() or default_status
                sort_mode = str((query.get("sort") or ["queue"])[0]).strip() or "queue"
                try:
                    payload = hooks.build_task_list_payload(
                        token_record,
                        status_filter,
                        sort_mode,
                        limit,
                        "queue" if parsed.path == "/queue" else "tasks",
                    )
                except ValueError as exc:
                    write_api_json_response(self, 400, {"ok": False, "error": str(exc)})
                    return
                write_api_json_response(self, 200, {"ok": True, **payload})
                return
            if parsed.path == "/status-result":
                if not task_id:
                    write_api_json_response(self, 400, {"ok": False, "error": "missing task_id"})
                    return
                try:
                    payload = hooks.build_task_result_payload(task_id, token_record)
                except (ValueError, PermissionError):
                    _write_result_denied_response(
                        self,
                        hooks,
                        path="/status-result",
                        token_record=token_record,
                        task_id=task_id,
                    )
                    return
                write_api_json_response(self, 200, {"ok": True, "result": payload})
                return
            if parsed.path == "/wait-result":
                if not task_id:
                    write_api_json_response(self, 400, {"ok": False, "error": "missing task_id"})
                    return
                try:
                    timeout_seconds = float((query.get("timeout_seconds") or [3600])[0] or 3600)
                    poll_seconds = float((query.get("poll_seconds") or [hooks.default_poll_seconds])[0] or hooks.default_poll_seconds)
                except ValueError:
                    write_api_json_response(self, 400, {"ok": False, "error": "invalid timeout_seconds or poll_seconds"})
                    return
                payload = hooks.wait_for_result_payload(task_id, timeout_seconds, poll_seconds, token_record)
                if not payload:
                    _write_result_denied_response(
                        self,
                        hooks,
                        path="/wait-result",
                        token_record=token_record,
                        task_id=task_id,
                    )
                    return
                write_api_json_response(self, 200, {"ok": True, "result": payload})
                return
            write_api_json_response(self, 404, {"ok": False, "error": f"unknown endpoint: {parsed.path}"})

        def do_POST(self) -> None:  # noqa: N802
            token_record = self._authenticate()
            if token_record is None:
                return
            parsed = urlparse(self.path)
            if parsed.path != "/submit-job":
                write_api_json_response(self, 404, {"ok": False, "error": f"unknown endpoint: {parsed.path}"})
                return
            try:
                payload = read_api_json_body(self)
                result = hooks.submit_job(payload, token_record)
            except ValueError as exc:
                write_api_json_response(self, 400, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                write_api_json_response(self, 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                return
            write_api_json_response(self, 200, {"ok": True, "result": result})

    return TaskboardApiHandler


def serve_api(
    *,
    bind: str,
    port: int,
    hooks: ApiServerHooks,
    install_signal_handlers: bool = True,
) -> int:
    server = ThreadingHTTPServer((bind, port), build_api_handler(hooks))
    hooks.append_log(f"api_server_started bind={bind} port={port}")
    keep_running = {"value": True}

    def stop(_sig: int, _frame: Any) -> None:
        keep_running["value"] = False
        # HTTPServer.shutdown() must run off-thread; calling it from the signal
        # handler on the serve_forever thread can deadlock systemd stop/restart.
        threading.Thread(target=server.shutdown, daemon=True).start()

    if install_signal_handlers:
        for sig_name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, sig_name):
                signal.signal(getattr(signal, sig_name), stop)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        hooks.append_log(f"api_server_stopped bind={bind} port={port}")
    return 0
