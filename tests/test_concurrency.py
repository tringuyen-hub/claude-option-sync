"""Many threads and many processes race to answer; exactly one may win."""
import multiprocessing as mp
import threading

from claude_option.broker.database import Database
from claude_option.broker.manager import QuestionBroker

from .conftest import QUESTIONS


def test_threads_race_one_winner(broker):
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    q = s["questions"][0]
    barrier = threading.Barrier(32)
    results = []

    def go(i):
        barrier.wait()
        r = broker.select(q["question_id"], [q["options"][i % 3]["option_id"]],
                          source="telegram" if i % 2 else "vscode")
        results.append(r)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r.won for r in results) == 1
    finals = {tuple(sorted(r.state["answers"].items())) for r in results}
    assert len(finals) == 1


def _proc_select(db_path, question_id, option_id, source, start, out):
    broker = QuestionBroker(Database(db_path))
    start.wait()
    r = broker.select(question_id, [option_id], source=source)
    out.put((r.won, r.state["answers"]))


def test_processes_race_one_winner(tmp_path):
    db_path = tmp_path / "race.db"
    broker = QuestionBroker(Database(db_path))
    s = broker.ensure_question(session_id="s", tool_use_id="t", questions=QUESTIONS)
    q = s["questions"][0]
    ctx = mp.get_context("spawn")
    start, out = ctx.Event(), ctx.Queue()
    procs = [ctx.Process(target=_proc_select, args=(db_path, q["question_id"], q["options"][i % 3]["option_id"],
                                                     "telegram" if i % 2 else "vscode", start, out))
             for i in range(8)]
    for p in procs:
        p.start()
    start.set()
    results = [out.get(timeout=30) for _ in procs]
    for p in procs:
        p.join()
    assert sum(won for won, _ in results) == 1
    assert len({tuple(sorted(a.items())) for _, a in results}) == 1
