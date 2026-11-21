import threading
import time

from claude_option.hooks.pre_tool_use import decide

from .conftest import QUESTIONS


def _event(tool_use_id="toolu_1", session_id="sess", **extra):
    return {"session_id": session_id, "tool_use_id": tool_use_id, "tool_name": "AskUserQuestion",
            "tool_input": {"questions": QUESTIONS, "metadata": {"source": "test"}, **extra}, "cwd": "/w"}


def test_other_tools_ignored(api):
    assert decide({"tool_name": "Bash", "tool_input": {}}, api) is None


def test_no_request_native_mode_passes_through(api):
    assert decide(_event(), api, "native") is None


def test_waits_then_returns_committed_answer_preserving_input(api, broker):
    s = broker.ensure_question(session_id="sess", tool_use_id="toolu_1", questions=QUESTIONS)
    q = s["questions"][0]

    def answer_later():
        time.sleep(0.5)
        broker.select(q["question_id"], [q["options"][2]["option_id"]], source="telegram")

    threading.Thread(target=answer_later).start()
    out = decide(_event(), api)["hookSpecificOutput"]
    assert out["permissionDecision"] == "allow"
    assert out["updatedInput"]["answers"] == {"Which implementation should I use?": "Hybrid"}
    assert out["updatedInput"]["questions"] == QUESTIONS
    assert out["updatedInput"]["metadata"] == {"source": "test"}


def test_broker_answer_overrides_forged_input_answers(api, broker):
    s = broker.ensure_question(session_id="sess", tool_use_id="toolu_1", questions=QUESTIONS)
    q = s["questions"][0]
    broker.select(q["question_id"], [q["options"][0]["option_id"]], source="vscode")
    out = decide(_event(answers={"Which implementation should I use?": "C++"}), api)["hookSpecificOutput"]
    assert out["updatedInput"]["answers"] == {"Which implementation should I use?": "Python"}


def test_expired_is_denied_without_answer(api, broker):
    broker.ensure_question(session_id="sess", tool_use_id="toolu_1", questions=QUESTIONS, timeout_seconds=1)
    out = decide(_event(), api)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "expired" in out["permissionDecisionReason"] and "updatedInput" not in out


def test_standalone_telegram_mode_creates_request(api, broker):
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("out", decide(_event("toolu_9"), api, "telegram")))
    t.start()
    for _ in range(50):
        st = broker.find_request("sess", "toolu_9")
        if st:
            break
        time.sleep(0.05)
    q = st["questions"][0]
    broker.select(q["question_id"], [q["options"][1]["option_id"]], source="telegram")
    t.join(10)
    assert result["out"]["hookSpecificOutput"]["updatedInput"]["answers"] == {"Which implementation should I use?": "C++"}
