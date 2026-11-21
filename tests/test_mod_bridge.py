"""The local API exactly as the mod uses it (ensure, wait, select, cancel), over each transport,
plus authentication, browser-origin refusal, shutdown and session isolation."""
import json
import threading
import time

import httpx

from claude_option.utils.platform import is_private

from .conftest import QUESTIONS


def _raw(api, path, headers=None):
    ep = json.loads(api.endpoint_path.read_text())
    if ep["transport"] == "unix":
        c = httpx.Client(transport=httpx.HTTPTransport(uds=ep["socket"]), base_url="http://broker")
    else:
        c = httpx.Client(base_url=ep["url"], trust_env=False)
    with c:
        return c.get(path, headers=headers or {})


def test_endpoint_file_describes_transport(api):
    ep = json.loads(api.endpoint_path.read_text())
    assert ep["transport"] == api.server.transport
    if ep["transport"] == "tcp":
        assert ep["host"] == "127.0.0.1" and ep["port"] > 0
        assert api.server._server.server_address[0] == "127.0.0.1"   # loopback only


def test_auth_required(api):
    assert _raw(api, "/v1/health").status_code == 401
    assert _raw(api, "/v1/health", {"Authorization": "Bearer nope"}).status_code == 401
    assert api.health()["test"] is True


def test_browser_origin_refused_even_with_token(api):
    token = api.token_path.read_text().strip()
    r = _raw(api, "/v1/health", {"Authorization": f"Bearer {token}", "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_files_are_private(api):
    assert is_private(api.token_path)
    assert is_private(api.endpoint_path)
    assert is_private(api.run_dir)
    if api.server.transport == "unix":
        assert is_private(api.server.socket_path)


def test_shutdown_endpoint(api):
    status, body = api.request("POST", "/v1/shutdown", json={})
    assert status == 200 and body["stopping"] and api.stopped.is_set()


def test_ensure_wait_select_flow(api):
    status, s = api.ensure({"session_id": "S", "tool_use_id": "T", "questions": QUESTIONS, "project_root": "/w/p"})
    assert status == 200 and s["status"] == "PENDING"
    q = s["questions"][0]
    t0 = time.monotonic()
    timed_out = api.wait(s["request_id"], s["version"], timeout=1)
    assert timed_out["version"] == s["version"] and time.monotonic() - t0 >= 0.9

    def later():
        time.sleep(0.3)
        api.select(q["question_id"], [q["options"][0]["option_id"]], "telegram")

    threading.Thread(target=later).start()
    t0 = time.monotonic()
    done = api.wait(s["request_id"], s["version"], timeout=5)
    assert time.monotonic() - t0 < 2              # event-driven, not a full timeout
    assert done["status"] == "ANSWERED"
    lose = api.select(q["question_id"], [q["options"][1]["option_id"]], "vscode")
    assert lose["won"] is False and lose["state"]["answers"] == done["answers"]


def test_unsupported_returns_422(api):
    status, body = api.ensure({"session_id": "S", "tool_use_id": "T2",
                               "questions": [{"question": "Q?", "header": "h", "kind": "number", "options": []}]})
    assert status == 422 and body["unsupported"]


def test_session_isolation(api, broker):
    _, a = api.ensure({"session_id": "A", "tool_use_id": "T", "questions": QUESTIONS})
    _, b = api.ensure({"session_id": "B", "tool_use_id": "T", "questions": QUESTIONS})
    assert a["request_id"] != b["request_id"]
    qa = a["questions"][0]
    api.select(qa["question_id"], [qa["options"][0]["option_id"]], "vscode")
    assert broker.get_state(b["request_id"])["status"] == "PENDING"
    assert api.lookup("B", "T")["request_id"] == b["request_id"]
    api.request("POST", "/v1/sessions/B/cancel", json={"reason": "ended"})
    assert broker.get_state(b["request_id"])["status"] == "CANCELLED"
    assert broker.get_state(a["request_id"])["status"] == "ANSWERED"


def test_unread_body_does_not_poison_keepalive_connection(api):
    """Two requests on one kept-alive connection; the first carries a body its route ignores."""
    token = api.token_path.read_text().strip()
    ep = json.loads(api.endpoint_path.read_text())
    kw = ({"transport": httpx.HTTPTransport(uds=ep["socket"]), "base_url": "http://broker"}
          if ep["transport"] == "unix" else {"base_url": ep["url"], "trust_env": False})
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, **kw) as c:
        assert c.request("GET", "/v1/health", content=b'{"ignored": true}').status_code == 200
        assert c.get("/v1/health").status_code == 200
        assert c.post("/v1/shutdown", json={}).status_code == 200
        assert c.get("/v1/health").status_code == 200
