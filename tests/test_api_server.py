import http.client
import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from codex_taskboard.api_server import (
    ApiServerHooks,
    build_api_handler,
    extract_api_bearer_token,
    read_api_json_body,
    write_api_json_response,
)


class FakeHandler:
    def __init__(self, *, headers: dict[str, str] | None = None, body: bytes = b"") -> None:
        self.headers = headers or {}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status_codes: list[int] = []
        self.response_headers: list[tuple[str, str]] = []
        self.headers_finished = False

    def send_response(self, status_code: int) -> None:
        self.status_codes.append(status_code)

    def send_header(self, name: str, value: str) -> None:
        self.response_headers.append((name, value))

    def end_headers(self) -> None:
        self.headers_finished = True


def default_hooks(
    *,
    logs: list[str] | None = None,
    list_payload=None,
    result_payload=None,
    wait_payload=None,
    submit_result=None,
) -> ApiServerHooks:
    recorded_logs = logs if logs is not None else []
    return ApiServerHooks(
        default_poll_seconds=2.0,
        resolve_token=lambda token: {"tenant": "docker-a"} if token == "secret" else None,
        build_task_list_payload=list_payload or (lambda token_record, status_filter, sort_mode, limit, view: {"summary": {}, "tasks": []}),
        build_task_result_payload=result_payload or (lambda task_id, token_record: {"task_id": task_id}),
        wait_for_result_payload=wait_payload or (lambda task_id, timeout_seconds, poll_seconds, token_record: {"task_id": task_id}),
        submit_job=submit_result or (lambda payload, token_record: {"task_id": str(payload.get("task_id", ""))}),
        append_log=recorded_logs.append,
        normalize_task_id=lambda raw_value: str(raw_value or "").strip().lower(),
        api_token_tenant=lambda token_record: str(token_record.get("tenant", "")),
    )


class ApiServerHelpersTests(unittest.TestCase):
    def test_extract_api_bearer_token_prefers_authorization_header(self) -> None:
        handler = FakeHandler(
            headers={
                "Authorization": "Bearer secret-token",
                "X-Taskboard-Token": "fallback-token",
            }
        )

        token = extract_api_bearer_token(handler)

        self.assertEqual(token, "secret-token")

    def test_read_api_json_body_rejects_non_object_payload(self) -> None:
        handler = FakeHandler(headers={"Content-Length": "2"}, body=b"[]")

        with self.assertRaisesRegex(ValueError, "JSON object"):
            read_api_json_body(handler)

    def test_write_api_json_response_writes_utf8_json(self) -> None:
        handler = FakeHandler()

        write_api_json_response(handler, 202, {"ok": True, "value": "demo"})

        self.assertEqual(handler.status_codes, [202])
        self.assertIn(("Content-Type", "application/json; charset=utf-8"), handler.response_headers)
        self.assertTrue(handler.headers_finished)
        self.assertEqual(json.loads(handler.wfile.getvalue().decode("utf-8")), {"ok": True, "value": "demo"})


class ApiServerRoutingTests(unittest.TestCase):
    def _serve(self, hooks: ApiServerHooks) -> tuple[ThreadingHTTPServer, threading.Thread]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_api_handler(hooks))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        thread.start()
        return server, thread

    def _request(
        self,
        server: ThreadingHTTPServer,
        *,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, object]]:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        return response.status, payload

    def test_get_queue_delegates_to_task_list_builder(self) -> None:
        calls: list[tuple[dict[str, str], str, str, int, str]] = []

        def build_list_payload(
            token_record: dict[str, str],
            status_filter: str,
            sort_mode: str,
            limit: int,
            view: str,
        ) -> dict[str, object]:
            calls.append((token_record, status_filter, sort_mode, limit, view))
            return {"summary": {"view": view}, "tasks": [{"task_id": "demo"}]}

        hooks = default_hooks(list_payload=build_list_payload)
        server, thread = self._serve(hooks)
        try:
            status, payload = self._request(
                server,
                method="GET",
                path="/queue?limit=7&sort=updated",
                headers={"Authorization": "Bearer secret"},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["view"], "queue")
        self.assertEqual(calls, [({"tenant": "docker-a"}, "queued", "updated", 7, "queue")])

    def test_wait_result_missing_task_returns_404_and_logs_denial(self) -> None:
        logs: list[str] = []
        hooks = default_hooks(logs=logs, wait_payload=lambda task_id, timeout_seconds, poll_seconds, token_record: None)
        server, thread = self._serve(hooks)
        try:
            status, payload = self._request(
                server,
                method="GET",
                path="/wait-result?task_id=Demo-Task",
                headers={"X-Taskboard-Token": "secret"},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Task not found: demo-task")
        self.assertTrue(any("result_denied path=/wait-result tenant=docker-a task_id=demo-task" in line for line in logs))
