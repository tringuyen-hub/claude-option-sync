"""Telegram handlers and sync against a recording fake Bot API (no network)."""
import itertools

import pytest

from claude_option.telegram.handlers import UpdateHandler
from claude_option.telegram.notifications import TelegramSync
from claude_option.telegram.pairing import attempt_pairing, create_pairing_code

from .conftest import MULTI, QUESTIONS

OWNER, CHAT = 1001, 1001


class FakeApi:
    def __init__(self):
        self.sent, self.edits, self.answers = [], [], []
        self._ids = itertools.count(500)

    def send_message(self, chat_id, text, reply_markup=None):
        mid = next(self._ids)
        self.sent.append({"chat_id": chat_id, "message_id": mid, "text": text, "markup": reply_markup})
        return {"message_id": mid}

    def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, "markup": reply_markup})

    def answer_callback(self, cid, text=None, show_alert=False):
        self.answers.append(text)


@pytest.fixture
def tg(broker, db):
    api = FakeApi()
    sync = TelegramSync(broker, api)
    handler = UpdateHandler(broker, api, sync)
    code, _ = create_pairing_code(db)
    assert attempt_pairing(db, code, user_id=OWNER, chat_id=CHAT, chat_type="private", username="me")[0]
    return api, sync, handler


def _cb(data, message_id, user=OWNER, chat=CHAT, chat_type="private"):
    return {"callback_query": {"id": "cb", "data": data, "from": {"id": user},
                               "message": {"message_id": message_id, "chat": {"id": chat, "type": chat_type}}}}


def test_question_sent_with_identical_options(broker, tg):
    api, sync, _ = tg
    s = broker.ensure_question(session_id="sess-abc", tool_use_id="t", questions=QUESTIONS, project_root="/w/DemoApp")
    sync.sync_all()
    assert len(api.sent) == 1
    msg = api.sent[0]
    buttons = [row[0] for row in msg["markup"]["inline_keyboard"]]
    assert [b["text"] for b in buttons] == ["A. Python", "B. C++", "C. Hybrid"]
    assert [b["callback_data"] for b in buttons] == [f"s|{o['option_id']}" for o in s["questions"][0]["options"]]
    assert "Which implementation should I use?" in msg["text"] and "DemoApp" in msg["text"]
    sync.sync_all()
    assert len(api.sent) == 1   # no duplicates


def test_telegram_click_wins_and_message_is_finalized(broker, tg):
    api, sync, handler = tg
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    sync.sync_all()
    mid = api.sent[0]["message_id"]
    opt = s["questions"][0]["options"][1]["option_id"]
    handler.handle(_cb(f"s|{opt}", mid))
    st = broker.get_state(s["request_id"])
    assert st["answers"] == {"Which implementation should I use?": "C++"}
    assert st["questions"][0]["answer"]["source"] == "telegram"
    last = api.edits[-1]
    assert last["markup"] is None and "Selected through Telegram" in last["text"]


def test_vscode_win_edits_telegram_and_stale_click_rejected(broker, tg):
    api, sync, handler = tg
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    sync.sync_all()
    mid = api.sent[0]["message_id"]
    q = s["questions"][0]
    broker.select(q["question_id"], [q["options"][0]["option_id"]], source="vscode")
    sync.sync_all()
    assert "Selected in VS Code" in api.edits[-1]["text"] and api.edits[-1]["markup"] is None
    handler.handle(_cb(f"s|{q['options'][2]['option_id']}", mid))     # stale button
    assert "Already answered in VS Code: Python" in api.answers[-1]
    assert broker.get_state(s["request_id"])["answers"] == {"Which implementation should I use?": "Python"}


def test_unauthorized_callback_rejected(broker, tg):
    api, sync, handler = tg
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    sync.sync_all()
    opt = s["questions"][0]["options"][0]["option_id"]
    handler.handle(_cb(f"s|{opt}", api.sent[0]["message_id"], user=666, chat=666))
    assert api.answers[-1] == "Not authorized."
    assert broker.get_state(s["request_id"])["status"] == "PENDING"


def test_callback_from_wrong_message_rejected(broker, tg):
    api, sync, handler = tg
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    sync.sync_all()
    opt = s["questions"][0]["options"][0]["option_id"]
    handler.handle(_cb(f"s|{opt}", 99999))
    assert broker.get_state(s["request_id"])["status"] == "PENDING"


def test_multiselect_toggle_then_submit(broker, tg):
    api, sync, handler = tg
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=MULTI)
    sync.sync_all()
    assert len(api.sent) == 2
    mid = api.sent[1]["message_id"]
    q2 = s["questions"][1]
    o = [x["option_id"] for x in q2["options"]]
    handler.handle(_cb(f"t|{o[2]}", mid))
    handler.handle(_cb(f"t|{o[0]}", mid))
    assert broker.get_state(s["request_id"])["questions"][1]["status"] == "PENDING"
    assert "☑ A. Logging" in str(api.edits[-1]["markup"])
    handler.handle(_cb(f"u|{q2['question_id']}", mid))
    st = broker.get_state(s["request_id"])
    assert st["questions"][1]["answer"]["text"] == "Logging, Tracing"
    assert st["status"] == "PENDING"     # question 1 still open


def test_reply_gives_custom_answer(broker, tg):
    api, sync, handler = tg
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    sync.sync_all()
    handler.handle({"message": {"chat": {"id": CHAT, "type": "private"}, "from": {"id": OWNER}, "text": "Use Go",
                                "reply_to_message": {"message_id": api.sent[0]["message_id"]}}})
    assert broker.get_state(s["request_id"])["answers"] == {"Which implementation should I use?": "Use Go"}


def test_expiry_removes_keyboard(broker, tg):
    api, sync, _ = tg
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    sync.sync_all()
    broker._close(s["request_id"], "EXPIRED", "timeout", "test")
    sync.sync_all()
    assert "Expired" in api.edits[-1]["text"] and api.edits[-1]["markup"] is None


def test_privileged_commands_owner_only(broker, tg):
    api, _, handler = tg
    handler.handle({"message": {"chat": {"id": 5, "type": "private"}, "from": {"id": 5}, "text": "/pending"}})
    assert "only available to the paired owner" in api.sent[-1]["text"]
    handler.handle({"message": {"chat": {"id": CHAT, "type": "private"}, "from": {"id": OWNER}, "text": "/id"}})
    assert str(OWNER) in api.sent[-1]["text"]


def test_group_messages_ignored(broker, tg):
    api, _, handler = tg
    n = len(api.sent)
    handler.handle({"message": {"chat": {"id": -5, "type": "group"}, "from": {"id": OWNER}, "text": "/status"}})
    assert len(api.sent) == n
