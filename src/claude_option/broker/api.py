"""Local broker API: HTTP/1.1, bearer-token authenticated, never reachable off this machine.

Transport:
  unix  (Linux/macOS default) a Unix domain socket, 0600 inside a 0700 directory.
  tcp   (Windows default)     127.0.0.1 only, on a random free port unless configured.

Either way every request needs the random token from api.token (private to this
user), and requests that carry a browser Origin header are refused, so a web page
cannot reach the API even by guessing the port. The broker writes endpoint.json
next to the token so clients (hook, CLI, VS Code mod) know which transport to use.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from ..utils.security import constant_time_equal, ensure_private_dir, write_private_file
from .manager import QuestionBroker
from .models import UnsupportedQuestion, ValidationError

log = logging.getLogger(__name__)
MAX_BODY = 512 * 1024


class _TcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False     # never share a port with another process


if hasattr(socket, "AF_UNIX") and hasattr(socketserver, "UnixStreamServer"):
    class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
        allow_reuse_address = True
else:  # Windows Python has no Unix-socket server
    _UnixServer = None


def make_handler(broker: QuestionBroker, token: str, status_provider: Callable[[], dict], long_poll_max: int,
                 on_shutdown: Callable[[], None] | None = None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "claude-option-broker"

        def address_string(self) -> str:  # Unix sockets have no peer address
            return "local"

        def log_message(self, fmt, *args):
            log.debug("api: " + fmt, *args)

        # -- helpers
        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer ") or not constant_time_equal(header[7:].strip(), token):
                self._send(401, {"error": "unauthorized"})
                return False
            return True

        def _read_raw_body(self) -> bool:
            """Consume the whole request body before answering, so bytes a route did not need are
            never parsed as the next request on a kept-alive connection. False: connection closed."""
            self._raw = b""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY or self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                self._send(413 if length > MAX_BODY else 400, {"error": "bad request body"})
                return False
            if length:
                self._raw = self.rfile.read(length)
            return True

        def _body(self) -> dict:
            data = json.loads(self._raw or b"{}")
            if not isinstance(data, dict):
                raise ValidationError("body must be a JSON object")
            return data

        def _dispatch(self, method: str) -> None:
            if not self._read_raw_body():
                return
            if self.headers.get("Origin"):        # a browser page; the API is for local programs only
                self._send(403, {"error": "forbidden"})
                return
            if not self._authorized():
                return
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                status, payload = self._route(method, parts, query)
            except UnsupportedQuestion as exc:
                status, payload = 422, {"error": str(exc), "unsupported": True}
            except ValidationError as exc:
                status, payload = 400, {"error": str(exc)}
            except KeyError:
                status, payload = 404, {"error": "not found"}
            except json.JSONDecodeError:
                status, payload = 400, {"error": "invalid JSON"}
            except Exception:  # pragma: no cover - logged, never leaks internals
                log.exception("api error")
                status, payload = 500, {"error": "internal error"}
            self._send(status, payload)

        def _route(self, method: str, parts: list[str], query: dict) -> tuple[int, dict]:
            if parts[:1] != ["v1"]:
                raise KeyError(self.path)
            rest = parts[1:]
            if method == "GET" and rest == ["health"]:
                return 200, {"ok": True, **status_provider()}
            if method == "POST" and rest == ["shutdown"] and on_shutdown is not None:
                on_shutdown()
                return 200, {"stopping": True}
            if method == "POST" and rest == ["questions", "ensure"]:
                b = self._body()
                state = broker.ensure_question(
                    session_id=str(b.get("session_id") or ""), tool_use_id=str(b.get("tool_use_id") or ""),
                    questions=b.get("questions"), project_root=b.get("project_root"), agent_id=b.get("agent_id"))
                return 200, state
            if method == "GET" and rest == ["lookup"]:
                state = broker.find_request(query.get("session_id", ""), query.get("tool_use_id", ""))
                if state is None:
                    raise KeyError("lookup")
                return 200, state
            if method == "GET" and len(rest) == 2 and rest[0] == "requests":
                return 200, broker.get_state(rest[1])
            if method == "GET" and len(rest) == 3 and rest[0] == "requests" and rest[2] == "wait":
                since = int(query.get("since", "0"))
                timeout = min(float(query.get("timeout", "20")), float(long_poll_max))
                return 200, broker.wait(rest[1], since, timeout)
            if method == "POST" and len(rest) == 3 and rest[0] == "requests" and rest[2] == "cancel":
                b = self._body()
                return 200, broker.cancel(rest[1], str(b.get("reason") or "cancelled"), source=str(b.get("source") or "vscode"))
            if method == "POST" and len(rest) == 3 and rest[0] == "sessions" and rest[2] == "cancel":
                b = self._body()
                return 200, {"cancelled": broker.cancel_session(rest[1], str(b.get("reason") or "session ended"))}
            if method == "POST" and rest == ["select"]:
                b = self._body()
                option_ids = b.get("option_ids") or []
                if not isinstance(option_ids, list) or not all(isinstance(o, str) for o in option_ids):
                    raise ValidationError("option_ids must be a list of strings")
                result = broker.select(str(b.get("question_id") or ""), option_ids,
                                       source=str(b.get("source") or "vscode"), selected_by=b.get("selected_by"),
                                       custom_text=b.get("custom_text"))
                return 200, {"won": result.won, "reason": result.reason, "state": result.state}
            raise KeyError(self.path)

        def do_GET(self):  # noqa: N802
            self._dispatch("GET")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

    return Handler


class BrokerApiServer:
    def __init__(self, broker: QuestionBroker, run_dir: Path, token: str,
                 status_provider: Callable[[], dict], long_poll_max: int = 25, *,
                 transport: str = "unix", port: int = 0, on_shutdown: Callable[[], None] | None = None):
        self.run_dir = Path(run_dir)
        self.transport = transport
        self.socket_path = self.run_dir / "broker.sock"
        self.endpoint_path = self.run_dir / "endpoint.json"
        ensure_private_dir(self.run_dir)
        handler = make_handler(broker, token, status_provider, long_poll_max, on_shutdown)
        if transport == "unix":
            if _UnixServer is None:
                raise RuntimeError("Unix sockets are not available on this platform; use api_transport: tcp")
            if self.socket_path.exists():
                self.socket_path.unlink()
            old_umask = os.umask(0o177)
            try:
                self._server = _UnixServer(str(self.socket_path), handler)
            finally:
                os.umask(old_umask)
            os.chmod(self.socket_path, 0o600)
            endpoint = {"transport": "unix", "socket": str(self.socket_path)}
        elif transport == "tcp":
            self._server = _TcpServer(("127.0.0.1", int(port)), handler)
            self.port = self._server.server_address[1]
            endpoint = {"transport": "tcp", "host": "127.0.0.1", "port": self.port,
                        "url": f"http://127.0.0.1:{self.port}"}
        else:
            raise ValueError(f"unknown transport {transport!r}")
        self.endpoint = endpoint
        write_private_file(self.endpoint_path, json.dumps(endpoint))

    def serve_forever(self) -> None:
        self._server.serve_forever(poll_interval=0.5)

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        for path in (self.endpoint_path, self.socket_path if self.transport == "unix" else None):
            try:
                if path is not None:
                    path.unlink()
            except FileNotFoundError:
                pass
