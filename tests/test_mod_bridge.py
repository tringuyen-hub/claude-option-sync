"""The Unix-socket API exactly as the mod uses it (ensure, wait, select, cancel), plus session isolation."""
import threading
import time

import httpx

from .conftest import QUESTIONS


def test_auth_required(api):
    transport = httpx.HTTPTransport(uds=str(api.socket_path))
    with httpx.Client(transport=transport, base_url="http://broker") as c:
        assert c.get("/v1/health").status_code == 401
        assert c.get("/v1/health", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_socket_is_private(api):
    assert (api.socket_path.stat().st_mode & 0o777) == 0o600
    assert (api.socket_path.parent.stat().st_mode & 0o777) == 0o700
    assert (api.token_path.stat().st_mode & 0o777) == 0o600


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
