"""Message text and inline keyboards for one question. Pure functions of broker state."""
from __future__ import annotations

import hashlib
import html
import json

LETTERS = "ABCDEFGHIJ"
SOURCE_LABEL = {"telegram": "through Telegram", "vscode": "in VS Code", "hook": "in Claude Code"}

# Callback data (<= 64 bytes): "s|<option_id>" select, "t|<option_id>" toggle, "u|<question_id>" submit.


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def letter(pos: int) -> str:
    return LETTERS[pos] if pos < len(LETTERS) else str(pos + 1)


def render_question(state: dict, q: dict, draft: list[str] | None = None) -> tuple[str, dict | None]:
    total = len(state["questions"])
    lines = ["🤖 <b>Claude Code needs your input</b>", ""]
    head = f"<b>{esc(q['header'])}</b> · " if q.get("header") else ""
    counter = f"Question {q['position'] + 1}/{total}" if total > 1 else "Question"
    lines.append(f"{head}{counter}{' (choose any)' if q['multi_select'] else ''}:")
    lines.append(f"<b>{esc(q['question'])}</b>")
    lines.append("")
    if state.get("project_name"):
        lines.append(f"Project: <code>{esc(state['project_name'])}</code>")
    lines.append(f"Session: <code>{esc(state['session_id'][:8])}</code> · Request: <code>{esc(q['question_id'])}</code>")
    lines.append("")
    answer = q.get("answer")
    chosen = set(answer["option_ids"]) if answer else set()
    lines.append("Options:")
    for o in q["options"]:
        mark = "✅ " if o["option_id"] in chosen else ""
        label = f"<b>{letter(o['position'])}. {esc(o['label'])}</b>"
        if q["status"] != "PENDING" and o["option_id"] not in chosen:
            label = f"<s>{letter(o['position'])}. {esc(o['label'])}</s>"
        desc = f"\n    <i>{esc(o['description'])}</i>" if o.get("description") else ""
        lines.append(f"{mark}{label}{desc}")
    lines.append("")

    status = q["status"]
    if status == "ANSWERED" and answer:
        src = SOURCE_LABEL.get(answer["source"], answer["source"])
        if answer["is_custom"]:
            lines.append(f"✅ <b>Answered {src}:</b> {esc(answer['text'])}")
        else:
            picked = ", ".join(f"{letter(_pos(q, oid))}. {esc(lbl)}" for oid, lbl in zip(answer["option_ids"], answer["labels"]))
            lines.append(f"✅ <b>Selected {src}:</b> {picked}")
        return "\n".join(lines), None
    if status == "EXPIRED" or state["status"] == "EXPIRED":
        lines.append("⌛ <b>Expired</b> — no option was selected.")
        return "\n".join(lines), None
    if status == "CANCELLED" or state["status"] == "CANCELLED":
        lines.append(f"✖️ <b>Cancelled</b> — {esc(state.get('status_reason') or 'no answer needed')}.")
        return "\n".join(lines), None

    draft = draft or []
    if q["multi_select"]:
        lines.append("Toggle options, then press <b>Submit</b>. Or reply to this message with your own answer.")
        rows = [[{"text": f"{'☑' if o['option_id'] in draft else '☐'} {letter(o['position'])}. {o['label']}",
                  "callback_data": f"t|{o['option_id']}"}] for o in q["options"]]
        rows.append([{"text": f"✅ Submit selection ({len(draft)})", "callback_data": f"u|{q['question_id']}"}])
    else:
        lines.append("Choose one option below. Or reply to this message with your own answer.")
        rows = [[{"text": f"{letter(o['position'])}. {o['label']}", "callback_data": f"s|{o['option_id']}"}]
                for o in q["options"]]
    return "\n".join(lines), {"inline_keyboard": rows}


def _pos(q: dict, option_id: str) -> int:
    for o in q["options"]:
        if o["option_id"] == option_id:
            return o["position"]
    return 0


def fingerprint(text: str, markup: dict | None) -> str:
    return hashlib.sha256((text + json.dumps(markup, sort_keys=True)).encode()).hexdigest()[:16]
