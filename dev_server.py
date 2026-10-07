#!/usr/bin/env python3
"""Local development server for Qishi Note Vault.

Serves the static frontend and forwards /v2/proxy/qishi-note-vault/* requests
to the same Python handler used on TOS. This is only a development helper.
"""

from __future__ import annotations

import argparse
import http.client
import http.server
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from qishi_note_vault import server  # noqa: E402


class DevelopmentHandler(http.server.SimpleHTTPRequestHandler):
    api_host = "127.0.0.1"
    api_port = 0

    def _proxy_api(self) -> bool:
        if not self.path.startswith(f"/v2/proxy/{server.APP_ID}"):
            return False
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else b""
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "connection"}
        }
        if length:
            headers["Content-Length"] = str(length)
        connection = http.client.HTTPConnection(self.api_host, self.api_port, timeout=15)
        connection.request(self.command, self.path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        connection.close()
        return True

    def _dispatch(self) -> None:
        if not self._proxy_api():
            super().do_GET() if self.command == "GET" else self.send_error(405)

    def do_GET(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_OPTIONS(self) -> None:
        self._dispatch()

    def log_message(self, format: str, *args: object) -> None:
        print(f"[dev] {self.address_string()} {format % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Qishi Note Vault development server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--data-dir", default=str(ROOT / "build" / "dev-data"))
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    server.configure_service(data_dir)
    api_server = server.ThreadedTCPServer(("127.0.0.1", 0), server.NoteVaultHandler)
    api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
    api_thread.start()
    DevelopmentHandler.api_port = int(api_server.server_address[1])

    web_server = http.server.ThreadingHTTPServer(
        (args.host, args.port),
        lambda *handler_args, **handler_kwargs: DevelopmentHandler(
            *handler_args,
            directory=str(ROOT / "frontend"),
            **handler_kwargs,
        ),
    )
    print(f"Development UI: http://{args.host}:{args.port}/")
    print(f"Data directory: {data_dir}")
    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        web_server.server_close()
        api_server.shutdown()
        api_thread.join(timeout=5)
        api_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
