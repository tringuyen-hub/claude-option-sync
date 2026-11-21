import pytest

from claude_option.broker.models import UnsupportedQuestion, ValidationError

from .conftest import MULTI, QUESTIONS


def test_ensure_is_idempotent(broker):
    a = broker.ensure_question(session_id="s1", tool_use_id="t1", questions=QUESTIONS, project_root="/p/demo-app")
    b = broker.ensure_question(session_id="s1", tool_use_id="t1", questions=QUESTIONS)
    assert a["request_id"] == b["request_id"]
    assert a["created"] and not b["created"]
    assert a["project_name"] == "demo-app"


def test_options_preserve_order_and_ids_are_stable(broker):
    s = broker.ensure_question(session_id="s1", tool_use_id="t1", questions=QUESTIONS)
    labels = [o["label"] for o in s["questions"][0]["options"]]
    assert labels == ["Python", "C++", "Hybrid"]
    again = broker.get_state(s["request_id"])
    assert [o["option_id"] for o in again["questions"][0]["options"]] == [o["option_id"] for o in s["questions"][0]["options"]]


def test_same_questions_in_other_tool_call_is_a_new_request(broker):
    a = broker.ensure_question(session_id="s1", tool_use_id="t1", questions=QUESTIONS)
    b = broker.ensure_question(session_id="s1", tool_use_id="t2", questions=QUESTIONS)
    assert a["request_id"] != b["request_id"]


def test_same_key_different_questions_rejected(broker):
    broker.ensure_question(session_id="s1", tool_use_id="t1", questions=QUESTIONS)
    with pytest.raises(ValidationError):
        broker.ensure_question(session_id="s1", tool_use_id="t1", questions=MULTI)


def test_unsupported_kinds_are_not_mirrored(broker):
    with pytest.raises(UnsupportedQuestion):
        broker.ensure_question(session_id="s", tool_use_id="t",
                               questions=[{"question": "Name?", "header": "N", "kind": "text", "options": []}])


def test_original_questions_preserved(broker):
    qs = [dict(QUESTIONS[0], options=[dict(o, preview="code") for o in QUESTIONS[0]["options"]])]
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=qs)
    assert s["original_questions"] == qs
