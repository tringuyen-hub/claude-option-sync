import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from claude_option.broker.api import BrokerApiServer
from claude_option.broker.database import Database
from claude_option.broker.manager import QuestionBroker
from claude_option.hooks.protocol import BrokerClient
from claude_option.utils.platform import IS_WINDOWS
from claude_option.utils.security import load_or_create_api_token

QUESTIONS = [
    {
        "question": "Which implementation should I use?",
        "header": "Approach",
        "multiSelect": False,
        "options": [
            {"label": "Python", "description": "Python implementation"},
            {"label": "C++", "description": "Native C++ implementation"},
            {"label": "Hybrid", "description": "Hybrid implementation"},
        ],
    }
]

MULTI = [
    QUESTIONS[0],
    {
        "question": "Which features should I enable?",
        "header": "Features",
        "multiSelect": True,
        "options": [
            {"label": "Logging", "description": "Structured logs"},
            {"label": "Metrics", "description": "Prometheus"},
            {"label": "Tracing", "description": "OpenTelemetry"},
        ],
    },
]


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture
def broker(db):
    return QuestionBroker(db, timeout_seconds=600)


TRANSPORTS = ["tcp"] if IS_WINDOWS else ["unix", "tcp"]


@pytest.fixture(params=TRANSPORTS)
def api(request, tmp_path, broker):
    """A live broker API on a background thread, over each transport this OS supports."""
    # Unix sockets need a short path (macOS allows 104 bytes; pytest's tmp_path there is longer).
    run = (Path(tempfile.mkdtemp(dir="/tmp")) if request.param == "unix" else tmp_path) / "run"
    token = load_or_create_api_token(run / "api.token")
    stopped = threading.Event()
    server = BrokerApiServer(broker, run, token, lambda: {"test": True, "pid": 0}, long_poll_max=5,
                             transport=request.param, on_shutdown=stopped.set)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    client = BrokerClient(run / "endpoint.json", run / "api.token")
    client.server, client.stopped, client.run_dir = server, stopped, run
    yield client
    server.shutdown()
    if request.param == "unix":
        shutil.rmtree(run.parent, ignore_errors=True)
