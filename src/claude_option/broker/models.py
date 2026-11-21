"""Validation of AskUserQuestion input and constants."""
from __future__ import annotations

import json
from dataclasses import dataclass

PENDING, ANSWERED, EXPIRED, CANCELLED = "PENDING", "ANSWERED", "EXPIRED", "CANCELLED"
FINAL_STATES = {ANSWERED, EXPIRED, CANCELLED}
SOURCES = {"telegram", "vscode", "hook"}
MAX_QUESTIONS = 10   # the installed tool schema allows 1-4; we accept a little more defensively
MAX_OPTIONS = 10


class ValidationError(ValueError):
    pass


class UnsupportedQuestion(ValueError):
    """The question uses a form we cannot safely mirror (text/number kinds, no options)."""


@dataclass
class SelectResult:
    won: bool
    reason: str
    state: dict


def validate_questions(questions) -> list[dict]:
    if not isinstance(questions, list) or not questions:
        raise ValidationError("questions must be a non-empty list")
    if len(questions) > MAX_QUESTIONS:
        raise ValidationError(f"at most {MAX_QUESTIONS} questions")
    seen = set()
    for q in questions:
        if not isinstance(q, dict):
            raise ValidationError("each question must be an object")
        kind = q.get("kind", "choice")
        if kind not in (None, "choice"):
            raise UnsupportedQuestion(f"question kind {kind!r} is not mirrored")
        text = q.get("question")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("question text is required")
        if text in seen:
            raise UnsupportedQuestion("duplicate question texts cannot be answered independently")
        seen.add(text)
        options = q.get("options")
        if not isinstance(options, list) or not options:
            raise UnsupportedQuestion("question without options is not mirrored")
        if len(options) > MAX_OPTIONS:
            raise ValidationError(f"at most {MAX_OPTIONS} options")
        labels = set()
        for o in options:
            if not isinstance(o, dict) or not isinstance(o.get("label"), str) or not o["label"].strip():
                raise ValidationError("each option needs a label")
            if o["label"] in labels:
                raise ValidationError("duplicate option labels")
            labels.add(o["label"])
    return questions


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
