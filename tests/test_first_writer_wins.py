import time

import pytest

from claude_option.broker.models import ValidationError

from .conftest import MULTI, QUESTIONS


def _ids(state, qi=0):
    q = state["questions"][qi]
    return q["question_id"], [o["option_id"] for o in q["options"]]


def test_first_selection_wins_and_second_gets_winner(broker):
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    qid, opts = _ids(s)
    first = broker.select(qid, [opts[1]], source="telegram", selected_by="42")
    second = broker.select(qid, [opts[0]], source="vscode")
    assert first.won and not second.won
    assert second.reason == "already_answered"
    assert second.state["answers"] == {"Which implementation should I use?": "C++"}
    assert second.state["questions"][0]["answer"]["source"] == "telegram"
    assert second.state["status"] == "ANSWERED"


def test_option_from_other_question_rejected(broker):
    a = broker.ensure_question(session_id="s", tool_use_id="t1", questions=QUESTIONS)
    b = broker.ensure_question(session_id="s", tool_use_id="t2", questions=QUESTIONS)
    qa, _ = _ids(a)
    _, ob = _ids(b)
    with pytest.raises(ValidationError):
        broker.select(qa, [ob[0]], source="vscode")
    assert broker.get_state(a["request_id"])["status"] == "PENDING"


def test_single_select_rejects_multiple(broker):
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    qid, opts = _ids(s)
    with pytest.raises(ValidationError):
        broker.select(qid, opts[:2], source="vscode")


def test_multiple_questions_resolve_independently(broker):
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=MULTI)
    q1, o1 = _ids(s, 0)
    q2, o2 = _ids(s, 1)
    r1 = broker.select(q1, [o1[2]], source="vscode")
    assert r1.won and r1.state["status"] == "PENDING"
    assert r1.state["answers"] == {}
    r2 = broker.select(q2, [o2[2], o2[0]], source="telegram")   # click order differs from option order
    assert r2.won and r2.state["status"] == "ANSWERED"
    assert r2.state["answers"] == {
        "Which implementation should I use?": "Hybrid",
        "Which features should I enable?": "Logging, Tracing",
    }


def test_multiselect_drafts_do_not_commit(broker):
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=MULTI)
    q2, o2 = _ids(s, 1)
    assert broker.toggle_draft(q2, o2[0], "telegram") == [o2[0]]
    assert broker.toggle_draft(q2, o2[1], "telegram") == [o2[0], o2[1]]
    assert broker.toggle_draft(q2, o2[0], "telegram") == [o2[1]]
    assert broker.get_state(s["request_id"])["questions"][1]["status"] == "PENDING"


def test_custom_text_answer(broker):
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    qid, _ = _ids(s)
    r = broker.select(qid, [], source="telegram", custom_text="Rust, actually")
    assert r.won and r.state["answers"] == {"Which implementation should I use?": "Rust, actually"}


def test_expired_request_rejects_selection_and_never_invents_answer(broker):
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS, timeout_seconds=1)
    qid, opts = _ids(s)
    time.sleep(1.1)
    r = broker.select(qid, [opts[0]], source="vscode")
    assert not r.won
    assert r.state["status"] == "EXPIRED"
    assert r.state["answers"] == {}
    assert r.state["questions"][0]["answer"] is None


def test_cancelled_request_rejects_selection(broker):
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    broker.cancel(s["request_id"], "interrupted")
    qid, opts = _ids(s)
    r = broker.select(qid, [opts[0]], source="telegram")
    assert not r.won and r.reason == "cancelled"


def test_audit_trail(broker, db):
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    qid, opts = _ids(s)
    broker.select(qid, [opts[0]], source="vscode")
    broker.select(qid, [opts[1]], source="telegram")
    with db.connect() as conn:
        kinds = [r["event_type"] for r in conn.execute("SELECT event_type FROM audit_events ORDER BY event_id")]
    assert kinds == ["request_created", "selection_committed", "selection_rejected"]
