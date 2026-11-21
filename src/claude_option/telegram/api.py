"""Minimal Telegram Bot API client. The token is held in memory only and never logged."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    def __init__(self, method: str, description: str, error_code: int | None = None):
        super().__init__(f"{method}: {description}")
        self.description = description
        self.error_code = error_code


class TelegramApi:
    def __init__(self, token: str, timeout: float = 35):
        self._base = f"https://api.telegram.org/bot{token}/"
        self._client = httpx.Client(timeout=timeout)

    def __repr__(self) -> str:  # never reveal the token
        return "TelegramApi(<redacted>)"

    def call(self, method: str, _http_timeout: float | None = None, **params):
        payload = {k: v for k, v in params.items() if v is not None}
        try:
            r = self._client.post(self._base + method, json=payload,
                                  timeout=_http_timeout if _http_timeout is not None else httpx.USE_CLIENT_DEFAULT)
        except httpx.HTTPError as exc:
            # str(exc) may contain the URL (and therefore the token): report the type only.
            raise TelegramError(method, f"network error ({type(exc).__name__})") from None
        try:
            data = r.json()
        except ValueError:
            raise TelegramError(method, f"HTTP {r.status_code}") from None
        if not data.get("ok"):
            raise TelegramError(method, str(data.get("description")), data.get("error_code"))
        return data["result"]

    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        return self.call("getUpdates", _http_timeout=timeout + 10, offset=offset, timeout=timeout,
                         allowed_updates=["message", "callback_query"])

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         reply_markup=reply_markup, link_preview_options={"is_disabled": True})

    def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict | None:
        try:
            return self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML",
                             reply_markup=reply_markup or {"inline_keyboard": []},
                             link_preview_options={"is_disabled": True})
        except TelegramError as exc:
            if "message is not modified" in exc.description:
                return None
            raise

    def answer_callback(self, callback_id: str, text: str | None = None, show_alert: bool = False) -> None:
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=show_alert)
        except TelegramError as exc:
            log.info("answerCallbackQuery failed: %s", exc.description)

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])
