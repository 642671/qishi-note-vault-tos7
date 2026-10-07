"""HTTP-over-Unix-socket API for Qishi Note Vault."""

from __future__ import annotations

import json
import os
import signal
import socketserver
import sys
import threading
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .database import NoteNotFoundError
from .notes import AttachmentError, NoteService, default_data_dir

APP_ID = "qishi-note-vault"
SOCKET_DIR = "/var/api"
SOCKET_PATH = os.path.join(SOCKET_DIR, f"{APP_ID}.sock")
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
SERVICE: NoteService | None = None


def log(level: str, message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] [qishi-note-vault] {message}", flush=True)


def configure_service(data_dir: Path | None = None) -> NoteService:
    global SERVICE
    SERVICE = NoteService(data_dir or default_data_dir())
    return SERVICE


def service() -> NoteService:
    global SERVICE
    if SERVICE is None:
        configure_service()
    assert SERVICE is not None
    return SERVICE


class NoteVaultHandler(BaseHTTPRequestHandler):
    server_version = f"qishi-note-vault/{__version__}"
    timeout = 30

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _origin(self) -> str:
        return self.headers.get("Origin") or "null"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", self._origin())
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self,
        status: int,
        data: bytes,
        content_type: str,
        *,
        filename: str = "",
        download: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            disposition = "attachment" if download else "inline"
            safe_name = filename.replace('"', "")[:180]
            self.send_header("Content-Disposition", f'{disposition}; filename="{safe_name}"')
        self.end_headers()
        self.wfile.write(data)

    def _normalized_path(self) -> tuple[list[str], dict[str, list[str]]]:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        proxy_prefix = f"/v2/proxy/{APP_ID}"
        if path.startswith(proxy_prefix):
            path = path[len(proxy_prefix) :]
        segments = [part for part in path.split("/") if part]
        return segments, parse_qs(parsed.query)

    def _read_body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise AttachmentError("invalid Content-Length") from exc
        if length < 0 or length > limit:
            raise AttachmentError("request body is too large")
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict[str, Any]:
        raw = self._read_body(MAX_JSON_BYTES)
        try:
            payload = json.loads(raw.decode("utf-8") if raw else "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AttachmentError("request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise AttachmentError("JSON body must be an object")
        return payload

    def _read_multipart(self) -> tuple[dict[str, str], bytes, str, str]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise AttachmentError("multipart/form-data is required")
        raw = self._read_body(MAX_UPLOAD_BYTES)
        message = BytesParser(policy=email_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
        )
        fields: dict[str, str] = {}
        filename = ""
        file_content_type = "application/octet-stream"
        file_data = b""
        for part in message.iter_parts():
            disposition = part.get("Content-Disposition", "")
            if "form-data" not in disposition:
                continue
            name = part.get_param("name", header="content-disposition") or ""
            part_filename = part.get_filename() or ""
            payload = part.get_payload(decode=True) or b""
            if name == "file":
                filename = part_filename
                file_content_type = part.get_content_type()
                file_data = payload
            elif name:
                fields[name] = payload.decode("utf-8", "replace")
        if not filename:
            raise AttachmentError("multipart field 'file' is required")
        return fields, file_data, filename, file_content_type

    def _handle_error(self, status: int, error: str) -> None:
        self._send_json(status, {"ok": False, "error": error})

    def _dispatch_get(self, segments: list[str], query: dict[str, list[str]]) -> None:
        if not segments or segments[0] in {"info", "health", "healthz", "ping"}:
            action = segments[0] if segments else "info"
            if action in {"health", "healthz", "ping"}:
                payload = {
                    "status": "ok",
                    "app": APP_ID,
                    "version": __version__,
                    "python": sys.version.split()[0],
                }
            else:
                payload = {
                    "status": "ok",
                    "app": APP_ID,
                    "version": __version__,
                    "stats": service().database.get_stats(),
                }
            self._send_json(200, payload)
            return

        if segments[0] == "notes" and len(segments) == 1:
            payload = service().list_notes(
                query=(query.get("query") or [""])[0],
                tag=(query.get("tag") or [""])[0],
                view=(query.get("view") or ["active"])[0],
                limit=int((query.get("limit") or ["200"])[0]),
                offset=int((query.get("offset") or ["0"])[0]),
            )
            self._send_json(200, {"ok": True, **payload})
            return

        if segments[0] == "notes" and len(segments) == 2:
            self._send_json(200, {"ok": True, "note": service().get_note(segments[1])})
            return

        if segments == ["tags"]:
            self._send_json(200, {"ok": True, "items": service().database.list_tags()})
            return

        if segments == ["stats"]:
            self._send_json(200, {"ok": True, "stats": service().database.get_stats()})
            return

        if segments == ["backup", "export"]:
            data = service().export_backup()
            self._send_bytes(
                200,
                data,
                "application/zip",
                filename=f"{APP_ID}-backup.zip",
                download=True,
            )
            return

        if segments[0] == "attachments" and len(segments) == 2:
            record, data = service().read_attachment(segments[1])
            self._send_bytes(
                200,
                data,
                record["content_type"],
                filename=record["filename"],
            )
            return

        self._handle_error(404, "not found")

    def _dispatch_post(self, segments: list[str]) -> None:
        if segments == ["notes"]:
            payload = self._read_json()
            self._send_json(201, {"ok": True, "note": service().create_note(payload)})
            return

        if segments == ["backup", "import"]:
            fields, data, filename, file_content_type = self._read_multipart()
            del fields, filename, file_content_type
            self._send_json(200, {"ok": True, **service().import_backup(data)})
            return

        if segments == ["attachments"]:
            fields, data, filename, file_content_type = self._read_multipart()
            note_id = fields.get("note_id", "")
            if not note_id:
                raise AttachmentError("note_id is required")
            record = service().save_attachment(
                note_id=note_id,
                filename=filename,
                content_type=file_content_type,
                data=data,
            )
            self._send_json(201, {"ok": True, "attachment": record})
            return

        if len(segments) == 3 and segments[0] == "notes":
            note_id, action = segments[1], segments[2]
            payload = self._read_json()
            if action == "restore":
                note = service().update_note(note_id, {"trashed": False, "archived": False})
            elif action == "pin":
                note = service().update_note(note_id, {"pinned": bool(payload.get("value"))})
            elif action == "archive":
                note = service().update_note(note_id, {"archived": bool(payload.get("value"))})
            elif action == "trash":
                note = service().delete_note(note_id, permanent=False)
            else:
                self._handle_error(404, "not found")
                return
            self._send_json(200, {"ok": True, "note": note})
            return

        self._handle_error(404, "not found")

    def _dispatch_put(self, segments: list[str]) -> None:
        if len(segments) == 2 and segments[0] == "notes":
            payload = self._read_json()
            self._send_json(200, {"ok": True, "note": service().update_note(segments[1], payload)})
            return
        self._handle_error(404, "not found")

    def _dispatch_delete(self, segments: list[str], query: dict[str, list[str]]) -> None:
        if len(segments) == 2 and segments[0] == "notes":
            permanent = (query.get("permanent") or ["false"])[0].lower() == "true"
            result = service().delete_note(segments[1], permanent=permanent)
            self._send_json(200, {"ok": True, "result": result})
            return
        if len(segments) == 2 and segments[0] == "attachments":
            result = service().delete_attachment(segments[1])
            self._send_json(200, {"ok": True, "attachment": result})
            return
        self._handle_error(404, "not found")

    def _handle_request(self, method: str) -> None:
        try:
            segments, query = self._normalized_path()
            if method == "GET":
                self._dispatch_get(segments, query)
            elif method == "POST":
                self._dispatch_post(segments)
            elif method == "PUT":
                self._dispatch_put(segments)
            elif method == "DELETE":
                self._dispatch_delete(segments, query)
            else:
                self._handle_error(405, "method not allowed")
        except NoteNotFoundError:
            self._handle_error(404, "not found")
        except (AttachmentError, ValueError) as exc:
            self._handle_error(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive last resort
            log("ERROR", f"Unhandled request failure: {exc}")
            self._handle_error(500, "internal server error")

    def do_GET(self) -> None:
        self._handle_request("GET")

    def do_POST(self) -> None:
        self._handle_request("POST")

    def do_PUT(self) -> None:
        self._handle_request("PUT")

    def do_DELETE(self) -> None:
        self._handle_request("DELETE")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self._origin())
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Csrf-Token, Cookie")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 100


UnixServerBase = getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)


class ThreadedUnixHTTPServer(socketserver.ThreadingMixIn, UnixServerBase):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 100


def create_server() -> ThreadedUnixHTTPServer:
    if not hasattr(socketserver, "UnixStreamServer"):
        raise RuntimeError("Unix sockets are required to run the production service")
    os.makedirs(SOCKET_DIR, exist_ok=True)
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    server = ThreadedUnixHTTPServer(SOCKET_PATH, NoteVaultHandler)
    os.chmod(SOCKET_PATH, 0o660)
    return server


def main() -> int:
    configure_service()
    server = create_server()
    stop_event = threading.Event()

    def request_stop(signum: int, frame: Any) -> None:
        log("INFO", f"Received signal {signum}; stopping service")
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker = threading.Thread(target=server.serve_forever, name="http-server", daemon=True)
    worker.start()
    log("INFO", f"Service started on {SOCKET_PATH} with Python {sys.version.split()[0]}")
    try:
        while not stop_event.wait(1.0):
            pass
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        log("INFO", "Service stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
