"""Versioned schema migrations. Each entry runs once, in order, in a transaction."""
from __future__ import annotations

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE sessions (
            session_id    TEXT PRIMARY KEY,
            project_name  TEXT,
            project_root  TEXT,
            created_at    REAL NOT NULL,
            last_seen_at  REAL NOT NULL
        );

        CREATE TABLE question_requests (
            request_id      TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            session_id      TEXT NOT NULL REFERENCES sessions(session_id),
            tool_use_id     TEXT NOT NULL,
            agent_id        TEXT,
            question_hash   TEXT NOT NULL,
            source_tool     TEXT NOT NULL DEFAULT 'AskUserQuestion',
            original_input  TEXT NOT NULL,
            created_at      REAL NOT NULL,
            expires_at      REAL NOT NULL,
            status          TEXT NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN ('PENDING','ANSWERED','EXPIRED','CANCELLED')),
            status_reason   TEXT,
            resolved_at     REAL,
            version         INTEGER NOT NULL DEFAULT 1,
            UNIQUE (session_id, tool_use_id)
        );
        CREATE INDEX idx_requests_status ON question_requests(status, expires_at);
        CREATE INDEX idx_requests_session ON question_requests(session_id, created_at);

        CREATE TABLE question_items (
            question_id   TEXT PRIMARY KEY,
            request_id    TEXT NOT NULL REFERENCES question_requests(request_id) ON DELETE CASCADE,
            position      INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            header        TEXT,
            multi_select  INTEGER NOT NULL DEFAULT 0,
            status        TEXT NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN ('PENDING','ANSWERED','EXPIRED','CANCELLED')),
            UNIQUE (request_id, position)
        );
        CREATE INDEX idx_items_request ON question_items(request_id);

        CREATE TABLE question_options (
            option_id   TEXT PRIMARY KEY,
            question_id TEXT NOT NULL REFERENCES question_items(question_id) ON DELETE CASCADE,
            position    INTEGER NOT NULL,
            label       TEXT NOT NULL,
            description TEXT,
            UNIQUE (question_id, position)
        );
        CREATE INDEX idx_options_question ON question_options(question_id);

        CREATE TABLE question_answers (
            question_id         TEXT PRIMARY KEY REFERENCES question_items(question_id) ON DELETE CASCADE,
            selected_option_ids TEXT NOT NULL,   -- JSON array
            selected_labels     TEXT NOT NULL,   -- JSON array
            answer_text         TEXT NOT NULL,   -- what Claude receives
            is_custom           INTEGER NOT NULL DEFAULT 0,
            selected_by         TEXT,
            selected_source     TEXT NOT NULL CHECK (selected_source IN ('telegram','vscode','hook')),
            resolved_at         REAL NOT NULL
        );

        CREATE TABLE question_drafts (
            question_id TEXT NOT NULL REFERENCES question_items(question_id) ON DELETE CASCADE,
            source      TEXT NOT NULL,
            option_ids  TEXT NOT NULL,           -- JSON array
            updated_at  REAL NOT NULL,
            PRIMARY KEY (question_id, source)
        );

        CREATE TABLE telegram_messages (
            question_id         TEXT PRIMARY KEY REFERENCES question_items(question_id) ON DELETE CASCADE,
            telegram_chat_id    INTEGER NOT NULL,
            telegram_message_id INTEGER,
            delivery_status     TEXT NOT NULL,   -- SENDING, SENT, FAILED, FINAL
            rendered_state      TEXT,            -- fingerprint of what the message shows now
            attempts            INTEGER NOT NULL DEFAULT 0,
            updated_at          REAL NOT NULL
        );
        CREATE INDEX idx_tg_message ON telegram_messages(telegram_chat_id, telegram_message_id);

        CREATE TABLE owner (
            id               INTEGER PRIMARY KEY CHECK (id = 1),
            telegram_user_id INTEGER NOT NULL,
            telegram_chat_id INTEGER NOT NULL,
            username         TEXT,
            paired_at        REAL NOT NULL
        );

        CREATE TABLE pairing_codes (
            code_hash  TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            used_at    REAL,
            attempts   INTEGER NOT NULL DEFAULT 0,
            status     TEXT NOT NULL CHECK (status IN ('ACTIVE','USED','REVOKED','EXPIRED'))
        );

        CREATE TABLE audit_events (
            event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id  TEXT,
            question_id TEXT,
            event_type  TEXT NOT NULL,
            source      TEXT,
            created_at  REAL NOT NULL,
            details     TEXT
        );
        CREATE INDEX idx_audit_request ON audit_events(request_id);

        CREATE TABLE kv (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
]


def migrate(conn) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = row[0] or 0
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in [s.strip() for s in sql.split(";") if s.strip()]:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        current = version
    return current
