"""Telegram update handling: commands, pairing, option callbacks and free-text replies."""
from __future__ import annotations

import datetime as dt
import logging
import os

from ..broker.manager import QuestionBroker
from ..broker.models import ValidationError
from .api import TelegramApi
from .keyboards import SOURCE_LABEL, esc
from .notifications import TelegramSync
from .pairing import attempt_pairing, get_owner

log = logging.getLogger(__name__)

PAIR_COMMAND = (r".venv\Scripts\python -m claude_option.cli pair-code" if os.name == "nt"
                else ".venv/bin/python -m claude_option.cli pair-code")

COMMANDS = [
    ("start", "Welcome and pairing instructions"),
    ("pair", "Pair this chat: /pair CODE"),
    ("help", "Available commands"),
    ("ping", "Check bot connectivity"),
    ("status", "Bot, broker and pairing status"),
    ("pending", "Unanswered questions"),
    ("history", "Recent decisions"),
    ("id", "Show the paired owner's Telegram IDs"),
]


class UpdateHandler:
    def __init__(self, broker: QuestionBroker, api: TelegramApi, sync: TelegramSync, *, max_pair_attempts: int = 5,
                 status_provider=None):
        self.broker = broker
        self.db = broker.db
        self.api = api
        self.sync = sync
        self.max_pair_attempts = max_pair_attempts
        self.status_provider = status_provider or (lambda: {})

    def handle(self, update: dict) -> None:
        if "callback_query" in update:
            self._callback(update["callback_query"])
        elif "message" in update:
            self._message(update["message"])

    # ------------------------------------------------------------------ helpers

    def _is_owner_chat(self, user_id: int | None, chat: dict) -> bool:
        owner = get_owner(self.db)
        return (owner is not None and chat.get("type") == "private"
                and user_id == owner.telegram_user_id and chat.get("id") == owner.telegram_chat_id)

    def _reply(self, chat_id: int, text: str) -> None:
        try:
            self.api.send_message(chat_id, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("reply failed: %s", exc)

    # ------------------------------------------------------------------ messages

    def _message(self, msg: dict) -> None:
        chat = msg.get("chat") or {}
        user = msg.get("from") or {}
        if chat.get("type") != "private":
            return  # never act in groups or channels
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            cmd = cmd[1:].split("@", 1)[0].lower()
            self._command(cmd, arg.strip(), msg)
            return
        reply_to = msg.get("reply_to_message")
        if reply_to and self._is_owner_chat(user.get("id"), chat):
            self._free_text_answer(chat["id"], reply_to.get("message_id"), text)

    def _command(self, cmd: str, arg: str, msg: dict) -> None:
        chat, user = msg["chat"], msg.get("from") or {}
        owner = get_owner(self.db)
        if cmd == "start":
            if owner is None:
                self._reply(chat["id"],
                            "👋 <b>Claude Option Sync</b>\n\nThis bot is not paired yet. On the computer running "
                            "Claude Code, run:\n<code>" + PAIR_COMMAND + "</code>\n"
                            "then send <code>/pair CODE</code> here within 5 minutes.")
            elif self._is_owner_chat(user.get("id"), chat):
                self._reply(chat["id"], "👋 You are paired. Claude Code questions will appear here. /help lists commands.")
            else:
                self._reply(chat["id"], "This is a private bot.")
            return
        if cmd == "pair":
            ok, text = attempt_pairing(self.db, arg, user_id=user.get("id"), chat_id=chat["id"],
                                       chat_type=chat.get("type", ""), username=user.get("username"),
                                       max_attempts=self.max_pair_attempts)
            self._reply(chat["id"], ("✅ " if ok else "⚠️ ") + esc(text))
            if ok:
                self.sync.sync_all()   # deliver anything already waiting
            return
        if cmd == "help":
            self._reply(chat["id"], "\n".join(f"/{c} — {esc(d)}" for c, d in COMMANDS))
            return
        if not self._is_owner_chat(user.get("id"), chat):
            self._reply(chat["id"], "This command is only available to the paired owner.")
            return
        if cmd == "ping":
            self._reply(chat["id"], "🏓 pong")
        elif cmd == "id":
            self._reply(chat["id"], f"Owner user ID: <code>{owner.telegram_user_id}</code>\n"
                                    f"Owner chat ID: <code>{owner.telegram_chat_id}</code>\n"
                                    f"Paired: {_ts(owner.paired_at)}")
        elif cmd == "status":
            counts = self.broker.counts()
            st = self.status_provider()
            self._reply(chat["id"],
                        "<b>Status</b>\n"
                        f"Bot: @{esc(st.get('bot_username', '?'))} online\n"
                        f"Broker: running since {_ts(st.get('started_at'))}\n"
                        f"Owner: paired {_ts(owner.paired_at)}\n"
                        f"Pending: {counts.get('PENDING', 0)} · Answered: {counts.get('ANSWERED', 0)} · "
                        f"Expired: {counts.get('EXPIRED', 0)} · Cancelled: {counts.get('CANCELLED', 0)}")
        elif cmd == "pending":
            pending = self.broker.pending_requests()
            if not pending:
                self._reply(chat["id"], "No pending questions.")
                return
            lines = ["<b>Pending questions</b>"]
            for s in pending:
                for q in s["questions"]:
                    if q["status"] == "PENDING":
                        lines.append(f"• <code>{q['question_id']}</code> {esc(q['question'])} "
                                     f"({esc(s.get('project_name') or '?')})")
            self._reply(chat["id"], "\n".join(lines))
            self.sync.sync_all()
        elif cmd == "history":
            rows = self.broker.recent_history(10)
            if not rows:
                self._reply(chat["id"], "No decisions yet.")
                return
            lines = ["<b>Recent decisions</b>"]
            for r in rows:
                if "answer_text" in r:
                    lines.append(f"• {_ts(r['resolved_at'])} {esc(r['question_text'])} → <b>{esc(r['answer_text'])}</b> "
                                 f"({SOURCE_LABEL.get(r['selected_source'], r['selected_source'])})")
                else:
                    lines.append(f"• {_ts(r['resolved_at'])} request <code>{r['request_id']}</code> {r['status'].lower()}")
            self._reply(chat["id"], "\n".join(lines))
        else:
            self._reply(chat["id"], "Unknown command. /help lists commands.")

    def _free_text_answer(self, chat_id: int, message_id: int | None, text: str) -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT question_id FROM telegram_messages WHERE telegram_chat_id = ?"
                               " AND telegram_message_id = ?", (chat_id, message_id)).fetchone()
        if row is None:
            return
        result = self.broker.select(row["question_id"], [], source="telegram", selected_by=str(chat_id),
                                    custom_text=text[:2000])
        if not result.won:
            self._reply(chat_id, "⚠️ That question was already closed; your reply was not used.")
        self.sync.sync_question(row["question_id"])

    # ------------------------------------------------------------------ callbacks

    def _callback(self, cq: dict) -> None:
        cid = cq["id"]
        user = cq.get("from") or {}
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        if not self._is_owner_chat(user.get("id"), chat):
            self.api.answer_callback(cid, "Not authorized.", show_alert=True)
            log.warning("rejected callback from unauthorized user/chat")
            return
        data = cq.get("data") or ""
        kind, _, ref = data.partition("|")
        question_id = self._question_for(kind, ref)
        if question_id is None:
            self.api.answer_callback(cid, "This question no longer exists.")
            return
        # The callback must come from the very message we sent for this question.
        with self.db.connect() as conn:
            m = conn.execute("SELECT telegram_message_id FROM telegram_messages WHERE question_id = ?",
                             (question_id,)).fetchone()
        if m is None or m["telegram_message_id"] != msg.get("message_id"):
            self.api.answer_callback(cid, "Stale button.")
            return
        try:
            if kind == "s":
                result = self.broker.select(question_id, [ref], source="telegram", selected_by=str(user.get("id")))
                self.api.answer_callback(cid, self._result_text(result, question_id))
            elif kind == "t":
                self.broker.toggle_draft(question_id, ref, "telegram")
                self.api.answer_callback(cid)
            elif kind == "u":
                draft = self.broker.get_draft(question_id, "telegram")
                if not draft:
                    self.api.answer_callback(cid, "Select at least one option first.")
                    return
                result = self.broker.select(question_id, draft, source="telegram", selected_by=str(user.get("id")))
                self.api.answer_callback(cid, self._result_text(result, question_id))
            else:
                self.api.answer_callback(cid, "Unknown action.")
                return
        except ValidationError as exc:
            self.api.answer_callback(cid, str(exc))
        self.sync.sync_question(question_id)

    def _question_for(self, kind: str, ref: str) -> str | None:
        with self.db.connect() as conn:
            if kind in ("s", "t"):
                row = conn.execute("SELECT question_id FROM question_options WHERE option_id = ?", (ref,)).fetchone()
                return row["question_id"] if row else None
            if kind == "u":
                row = conn.execute("SELECT question_id FROM question_items WHERE question_id = ?", (ref,)).fetchone()
                return row["question_id"] if row else None
        return None

    @staticmethod
    def _result_text(result, question_id: str) -> str:
        if result.won:
            return "✅ Selected"
        q = next((item for item in result.state["questions"] if item["question_id"] == question_id), None)
        if result.reason == "already_answered" and q and q["answer"]:
            src = SOURCE_LABEL.get(q["answer"]["source"], q["answer"]["source"])
            return f"Already answered {src}: {q['answer']['text']}"[:190]
        return f"Not accepted: question {result.reason.replace('_', ' ')}"


def _ts(value) -> str:
    if not value:
        return "?"
    return dt.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
