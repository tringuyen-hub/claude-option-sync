"""Client for the broker's Unix-socket API (used by the PreToolUse hook and the CLI)."""
from __future__ import annotations

from pathlib import Path

import httpx


class BrokerUnavailable(RuntimeError):
    pass


class BrokerClient:
    def __init__(self, socket_path: Path, token_path: Path):
        self.socket_path = Path(socket_path)
        self.token_path = Path(token_path)

    def _client(self, timeout: float) -> httpx.Client:
        if not self.socket_path.exists() or not self.token_path.exists():
            raise BrokerUnavailable("broker is not running")
        token = self.token_path.read_text().strip()
        transport = httpx.HTTPTransport(uds=str(self.socket_path))
        return httpx.Client(transport=transport, base_url="http://broker",
                            headers={"Authorization": f"Bearer {token}"}, timeout=timeout)

    def request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None,
                timeout: float = 10) -> tuple[int, dict]:
        try:
            with self._client(timeout) as c:
                r = c.request(method, path, json=json, params=params)
                return r.status_code, r.json()
        except (httpx.TransportError, OSError) as exc:
            raise BrokerUnavailable(str(exc)) from exc

    def health(self) -> dict:
        status, body = self.request("GET", "/v1/health", timeout=3)
        if status != 200:
            raise BrokerUnavailable(f"health status {status}")
        return body

    def lookup(self, session_id: str, tool_use_id: str) -> dict | None:
        status, body = self.request("GET", "/v1/lookup", params={"session_id": session_id, "tool_use_id": tool_use_id})
        return body if status == 200 else None

    def ensure(self, payload: dict) -> tuple[int, dict]:
        return self.request("POST", "/v1/questions/ensure", json=payload)

    def wait(self, request_id: str, since: int, timeout: float = 20) -> dict:
        status, body = self.request("GET", f"/v1/requests/{request_id}/wait",
                                    params={"since": since, "timeout": timeout}, timeout=timeout + 10)
        if status != 200:
            raise BrokerUnavailable(f"wait status {status}")
        return body

    def select(self, question_id: str, option_ids: list[str], source: str, custom_text: str | None = None) -> dict:
        _, body = self.request("POST", "/v1/select", json={"question_id": question_id, "option_ids": option_ids,
                                                           "source": source, "custom_text": custom_text})
        return body
