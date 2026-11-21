"""Client for the broker's local API (used by the PreToolUse hook and the CLI).

The running broker writes endpoint.json; the client follows it, so the same code
works over a Unix socket (Linux/macOS) and over 127.0.0.1 TCP (Windows).
"""
from __future__ import annotations

import json as _json
from pathlib import Path

import httpx


class BrokerUnavailable(RuntimeError):
    pass


class BrokerClient:
    def __init__(self, endpoint_path: Path, token_path: Path):
        self.endpoint_path = Path(endpoint_path)
        self.token_path = Path(token_path)

    @classmethod
    def from_settings(cls, settings) -> "BrokerClient":
        return cls(settings.endpoint_path, settings.api_token_path)

    def _client(self, timeout: float) -> httpx.Client:
        try:
            endpoint = _json.loads(self.endpoint_path.read_text(encoding="utf-8"))
            token = self.token_path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            raise BrokerUnavailable("broker is not running") from None
        headers = {"Authorization": f"Bearer {token}"}
        if endpoint.get("transport") == "unix":
            if not Path(endpoint["socket"]).exists():
                raise BrokerUnavailable("broker is not running")
            transport = httpx.HTTPTransport(uds=endpoint["socket"])
            return httpx.Client(transport=transport, base_url="http://broker", headers=headers, timeout=timeout)
        if endpoint.get("transport") == "tcp":
            # trust_env=False: never route the local API through an HTTP(S)_PROXY
            return httpx.Client(base_url=f"http://127.0.0.1:{int(endpoint['port'])}", headers=headers,
                                timeout=timeout, trust_env=False)
        raise BrokerUnavailable(f"unknown broker transport {endpoint.get('transport')!r}")

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
