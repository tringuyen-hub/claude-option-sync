"""Local broker API: HTTP/1.1 over a Unix domain socket, bearer-token authenticated.

The socket lives in a 0700 directory and is itself 0600, so only this user can
connect; the token is a second factor against confused-deputy access.
"""
from __future__ import annotations

import json
import logging
import os
import socketserver
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from ..utils.security import constant_time_equal, ensure_private_dir
from .manager import QuestionBroker
from .models import UnsupportedQuestion, ValidationError

log = logging.getLogger(__name__)
MAX_BODY = 512 * 1024


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(broker: QuestionBroker, token: str, status_provider: Callable[[], dict], long_poll_max: int):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "claude-option-broker"

        def address_string(self) -> str:  # Unix sockets have no peer address
            return "unix"

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

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValidationError("body too large")
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValidationError("body must be a JSON object")
            return data

        def _dispatch(self, method: str) -> None:
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
    def __init__(self, broker: QuestionBroker, socket_path: Path, token: str,
                 status_provider: Callable[[], dict], long_poll_max: int = 25):
        self.socket_path = Path(socket_path)
        ensure_private_dir(self.socket_path.parent)
        if self.socket_path.exists():
            self.socket_path.unlink()
        old_umask = os.umask(0o177)
        try:
            self._server = _Server(str(self.socket_path),
                                   make_handler(broker, token, status_provider, long_poll_max))
        finally:
            os.umask(old_umask)
        os.chmod(self.socket_path, 0o600)

    def serve_forever(self) -> None:
        self._server.serve_forever(poll_interval=0.5)

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
