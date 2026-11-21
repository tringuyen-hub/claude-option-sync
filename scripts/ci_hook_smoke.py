"""Smoke test: run the PreToolUse hook exactly as .claude/settings.json declares it, against the
running broker, and check it returns the broker's answer (non-ASCII included).

Needs a running broker (`cli start --no-telegram` is enough). Creates one question in that
broker's database. Exit code 0 on success.

    python scripts/ci_hook_smoke.py [path/to/settings.json]
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from claude_option.config import load_settings
from claude_option.hooks.protocol import BrokerClient

QUESTION = "Kiểm tra: chọn một phương án?"
QUESTIONS = [{"question": QUESTION, "header": "Smoke", "multiSelect": False,
              "options": [{"label": "Một ✓", "description": "first"}, {"label": "Hai", "description": "second"}]}]


def main() -> int:
    settings = load_settings()
    settings_json = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.project_root / ".claude" / "settings.json"
    entry = json.loads(settings_json.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    hook = next(h for e in entry if e.get("matcher") == "AskUserQuestion" for h in e["hooks"])
    argv = [hook["command"], *hook.get("args", [])]

    client = BrokerClient.from_settings(settings)
    session, tool_use = f"smoke-{uuid.uuid4().hex[:8]}", f"toolu_{uuid.uuid4().hex[:12]}"
    status, state = client.ensure({"session_id": session, "tool_use_id": tool_use, "questions": QUESTIONS})
    assert status == 200, state
    q = state["questions"][0]

    def answer_later():
        time.sleep(1.0)
        client.select(q["question_id"], [q["options"][0]["option_id"]], source="telegram")

    threading.Thread(target=answer_later, daemon=True).start()
    event = {"session_id": session, "tool_use_id": tool_use, "tool_name": "AskUserQuestion",
             "hook_event_name": "PreToolUse", "tool_input": {"questions": QUESTIONS}, "cwd": str(settings.project_root)}
    t0 = time.time()
    proc = subprocess.run(argv, input=json.dumps(event, ensure_ascii=False).encode("utf-8"),
                          capture_output=True, timeout=60)
    print(f"hook: {argv!r} exit={proc.returncode} in {time.time() - t0:.1f}s")
    if proc.returncode != 0:
        print(proc.stderr.decode("utf-8", "replace"))
        return 1
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    answers = out["updatedInput"]["answers"]
    print("decision:", out["permissionDecision"], "answers:", json.dumps(answers))
    ok = out["permissionDecision"] == "allow" and answers == {QUESTION: "Một ✓"}
    print("OK" if ok else "UNEXPECTED RESULT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
