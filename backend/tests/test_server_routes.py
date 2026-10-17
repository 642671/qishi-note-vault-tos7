from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from qishi_note_vault import server


class ServerRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.instance = server.ThreadedTCPServer(("127.0.0.1", 0), server.NoteVaultHandler)
        cls.host, cls.port = cls.instance.server_address
        cls.thread = threading.Thread(target=cls.instance.serve_forever, daemon=True)
        cls.thread.start()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        server.configure_service(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.instance.shutdown()
        cls.thread.join(timeout=5)
        cls.instance.server_close()

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
    ) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        payload = json.dumps(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        data = json.loads(raw.decode("utf-8")) if raw else {}
        connection.close()
        return response.status, data

    def request_raw(
        self,
        method: str,
        path: str,
        body: bytes,
        content_type: str,
    ) -> tuple[int, bytes, str]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request(method, path, body=body, headers={"Content-Type": content_type})
        response = connection.getresponse()
        data = response.read()
        response_type = response.getheader("Content-Type", "")
        connection.close()
        return response.status, data, response_type

    def multipart(self, fields: dict[str, str], filename: str, data: bytes) -> tuple[bytes, str]:
        boundary = "----qishi-note-vault-test"
        chunks = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                    "Content-Type: image/png\r\n\r\n"
                ).encode("utf-8"),
                data,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def test_note_lifecycle(self) -> None:
        status, created = self.request(
            "POST",
            "/v2/proxy/qishi-note-vault/notes",
            {"title": "First note", "body": "# Hello", "tags": ["test"]},
        )
        self.assertEqual(status, 201)
        note_id = created["note"]["id"]

        status, listed = self.request("GET", "/v2/proxy/qishi-note-vault/notes?query=Hello")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["items"]), 1)

        status, updated = self.request(
            "PUT",
            f"/v2/proxy/qishi-note-vault/notes/{note_id}",
            {"title": "Updated", "body": "Body", "tags": ["test", "python"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["note"]["title"], "Updated")

        status, tags = self.request("GET", "/v2/proxy/qishi-note-vault/tags")
        self.assertEqual(status, 200)
        self.assertEqual({item["name"] for item in tags["items"]}, {"python", "test"})

        status, _ = self.request(
            "POST",
            f"/v2/proxy/qishi-note-vault/notes/{note_id}/trash",
            {},
        )
        self.assertEqual(status, 200)
        status, trashed = self.request("GET", "/v2/proxy/qishi-note-vault/notes?view=trash")
        self.assertEqual(len(trashed["items"]), 1)

        status, _ = self.request(
            "POST",
            f"/v2/proxy/qishi-note-vault/notes/{note_id}/restore",
            {},
        )
        self.assertEqual(status, 200)
        status, active = self.request("GET", "/v2/proxy/qishi-note-vault/notes")
        self.assertEqual(len(active["items"]), 1)

    def test_health_and_stats(self) -> None:
        status, health = self.request("GET", "/v2/proxy/qishi-note-vault/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["app"], "qishi-note-vault")

        for path in (
            "/health",
            "/qishi-note-vault/health",
            "/qishi-note-vault/api/health",
            "/qishi-note-vault/v2/proxy/qishi-note-vault/health",
        ):
            status, health = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertEqual(health["app"], "qishi-note-vault")

        status, stats = self.request("GET", "/v2/proxy/qishi-note-vault/stats")
        self.assertEqual(status, 200)
        self.assertIn("total", stats["stats"])

    def test_attachment_upload_and_download(self) -> None:
        status, created = self.request(
            "POST",
            "/v2/proxy/qishi-note-vault/notes",
            {"title": "Image note", "body": "Body", "tags": []},
        )
        self.assertEqual(status, 201)
        note_id = created["note"]["id"]
        image = b"\x89PNG\r\n\x1a\nimage-data"
        body, content_type = self.multipart({"note_id": note_id}, "image.png", image)

        status, raw, _ = self.request_raw(
            "POST",
            "/v2/proxy/qishi-note-vault/attachments",
            body,
            content_type,
        )
        self.assertEqual(status, 201)
        attachment_id = json.loads(raw.decode("utf-8"))["attachment"]["id"]

        status, downloaded, downloaded_type = self.request_raw(
            "GET",
            f"/v2/proxy/qishi-note-vault/attachments/{attachment_id}",
            b"",
            "application/json",
        )
        self.assertEqual(status, 200)
        self.assertEqual(downloaded, image)
        self.assertEqual(downloaded_type, "image/png")


class ServerShutdownTests(unittest.TestCase):
    def test_stop_runtime_has_bounded_wait(self) -> None:
        class SlowServer:
            def __init__(self) -> None:
                self.closed = False

            def shutdown(self) -> None:
                time.sleep(0.3)

            def server_close(self) -> None:
                self.closed = True

        class Worker:
            def join(self, timeout: float | None = None) -> None:
                if timeout:
                    time.sleep(min(timeout, 0.01))

        server_instance = SlowServer()
        worker = Worker()
        started = time.monotonic()
        server.stop_server_runtime(server_instance, worker, timeout=0.05)
        elapsed = time.monotonic() - started

        self.assertTrue(server_instance.closed)
        self.assertLess(elapsed, 0.25)


class CorsHeaderTests(unittest.TestCase):
    """预检与跨源响应头的契约。

    实机（TOS 7.0.1298）上平台的 TOSDaemon 对 /v2/ 代理请求回的是字面量
    ``Access-Control-Allow-Origin: same-origin``——那不是合法取值，任何浏览器都
    不会认为它匹配自己的源。应用不能跟着犯同一个错，所以这里把回源头钉死：
    有 Origin 就原样回显并带 Vary: Origin，没有 Origin 就一个 CORS 头都不发。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.instance = server.ThreadedTCPServer(("127.0.0.1", 0), server.NoteVaultHandler)
        cls.host, cls.port = cls.instance.server_address
        cls.thread = threading.Thread(target=cls.instance.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.instance.shutdown()
        cls.thread.join(timeout=5)
        cls.instance.server_close()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        server.configure_service(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def raw(self, method: str, path: str, headers: dict | None = None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        status = response.status
        got = {key.lower(): value for key, value in response.getheaders()}
        response.read()
        connection.close()
        return status, got

    def test_preflight_allows_the_platform_header_set(self) -> None:
        status, got = self.raw(
            "OPTIONS",
            "/v2/proxy/qishi-note-vault/health",
            {
                "Origin": "https://tos.example:5443",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type, x-csrf-token",
            },
        )
        self.assertEqual(status, 204)
        self.assertEqual(got.get("access-control-allow-origin"), "https://tos.example:5443")
        self.assertEqual(got.get("access-control-allow-credentials"), "true")
        self.assertEqual(got.get("vary"), "Origin")
        methods = got.get("access-control-allow-methods", "")
        for method in ("GET", "POST", "PUT", "DELETE", "OPTIONS"):
            self.assertIn(method, methods)
        allowed = got.get("access-control-allow-headers", "")
        for header in ("Content-Type", "X-Csrf-Token", "Cookie"):
            self.assertIn(header, allowed)

    def test_same_origin_request_sends_no_cors_headers(self) -> None:
        status, got = self.raw("GET", "/health")
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", got)
        self.assertNotIn("access-control-allow-credentials", got)

    def test_binary_response_also_carries_echoed_origin(self) -> None:
        # 附件与备份导出走 _send_bytes，跨源读取同样需要 CORS，不能只给 JSON 加。
        status, got = self.raw("GET", "/backup/export", {"Origin": "https://a.example"})
        self.assertEqual(status, 200)
        self.assertEqual(got.get("access-control-allow-origin"), "https://a.example")
        self.assertEqual(got.get("vary"), "Origin")


if __name__ == "__main__":
    unittest.main()
