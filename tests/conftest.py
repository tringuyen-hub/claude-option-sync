import threading

import pytest

from claude_option.broker.api import BrokerApiServer
from claude_option.broker.database import Database
from claude_option.broker.manager import QuestionBroker
from claude_option.hooks.protocol import BrokerClient
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


@pytest.fixture
def api(tmp_path, broker):
    run = tmp_path / "run"
    token = load_or_create_api_token(run / "api.token")
    server = BrokerApiServer(broker, run / "broker.sock", token, lambda: {"test": True}, long_poll_max=5)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield BrokerClient(run / "broker.sock", run / "api.token")
    server.shutdown()
