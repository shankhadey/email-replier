"""
SQLite persistence for processed emails, review queue, activity log,
and multi-user tables (users, tokens, configs, contacts).
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_FILE = Path("gmail_replier.db")

# Default config values mirroring config.py DEFAULTS (authoritative copy here)
CONFIG_DEFAULTS = {
    "poll_interval_minutes": 30,
    "poll_start_hour": 0,
    "poll_end_hour": 23,
    "autonomy_level": 1,
    "anthropic_model": "claude-sonnet-4-6",
    "low_confidence_threshold": 0.70,
    "user_timezone": "America/Chicago",
    "lookback_hours": 72,
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        # WAL mode for concurrent reads during scheduler polls
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        # ── New multi-user tables ──────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id           TEXT PRIMARY KEY,
                email             TEXT NOT NULL UNIQUE,
                display_name      TEXT,
                service_start_epoch INTEGER,
                user_params       TEXT,
                setup_status      TEXT NOT NULL DEFAULT 'pending',
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_tokens (
                user_id    TEXT PRIMARY KEY,
                token_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_configs (
                user_id     TEXT PRIMARY KEY,
                config_json TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_contacts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           TEXT NOT NULL,
                email             TEXT NOT NULL,
                name              TEXT,
                relationship_type TEXT,
                formality_level   TEXT,
                interaction_count INTEGER DEFAULT 0,
                last_contact_at   TEXT,
                UNIQUE(user_id, email),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ── Existing tables: migrate to add user_id if not present ─────────
        _migrate_existing_tables(conn)

        # ── Indexes ────────────────────────────────────────────────────────
        conn.execute("CREATE INDEX IF NOT EXISTS idx_processed_user ON processed_emails(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_review_user ON review_queue(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_user ON user_contacts(user_id)")
        conn.commit()


def _migrate_existing_tables(conn: sqlite3.Connection):
    """
    Migrate pre-existing single-user tables to include user_id.
    Safe to call on fresh DBs (tables don't exist yet) and on already-migrated DBs.
    """
    # processed_emails
    if not _table_has_column(conn, "processed_emails", "user_id"):
        old_exists = _table_exists(conn, "processed_emails")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_emails_new (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL DEFAULT 'legacy',
                message_id   TEXT NOT NULL,
                thread_id    TEXT,
                processed_at TEXT,
                UNIQUE(user_id, message_id)
            )
        """)
        if old_exists:
            conn.execute("""
                INSERT OR IGNORE INTO processed_emails_new (user_id, message_id, thread_id, processed_at)
                SELECT 'legacy', message_id, thread_id, processed_at FROM processed_emails
            """)
            conn.execute("DROP TABLE processed_emails")
        conn.execute("ALTER TABLE processed_emails_new RENAME TO processed_emails")
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_emails (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL DEFAULT 'legacy',
                message_id   TEXT NOT NULL,
                thread_id    TEXT,
                processed_at TEXT,
                UNIQUE(user_id, message_id)
            )
        """)

    # review_queue
    if not _table_has_column(conn, "review_queue", "user_id"):
        old_exists = _table_exists(conn, "review_queue")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_queue_new (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL DEFAULT 'legacy',
                message_id     TEXT,
                thread_id      TEXT,
                sender         TEXT,
                subject        TEXT,
                snippet        TEXT,
                body           TEXT,
                draft_reply    TEXT,
                classification TEXT,
                status         TEXT DEFAULT 'pending',
                action_taken   TEXT,
                created_at     TEXT,
                updated_at     TEXT,
                UNIQUE(user_id, message_id)
            )
        """)
        if old_exists:
            conn.execute("""
                INSERT OR IGNORE INTO review_queue_new
                (user_id, id, message_id, thread_id, sender, subject, snippet, body,
                 draft_reply, classification, status, action_taken, created_at, updated_at)
                SELECT 'legacy', id, message_id, thread_id, sender, subject, snippet, body,
                       draft_reply, classification, status, action_taken, created_at, updated_at
                FROM review_queue
            """)
            conn.execute("DROP TABLE review_queue")
        conn.execute("ALTER TABLE review_queue_new RENAME TO review_queue")
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_queue (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL DEFAULT 'legacy',
                message_id     TEXT,
                thread_id      TEXT,
                sender         TEXT,
                subject        TEXT,
                snippet        TEXT,
                body           TEXT,
                draft_reply    TEXT,
                classification TEXT,
                status         TEXT DEFAULT 'pending',
                action_taken   TEXT,
                created_at     TEXT,
                updated_at     TEXT,
                UNIQUE(user_id, message_id)
            )
        """)

    # activity_log
    if not _table_has_column(conn, "activity_log", "user_id"):
        old_exists = _table_exists(conn, "activity_log")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log_new (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL DEFAULT 'legacy',
                event_type TEXT,
                message    TEXT,
                created_at TEXT
            )
        """)
        if old_exists:
            conn.execute("""
                INSERT OR IGNORE INTO activity_log_new (user_id, id, event_type, message, created_at)
                SELECT 'legacy', id, event_type, message, created_at FROM activity_log
            """)
            conn.execute("DROP TABLE activity_log")
        conn.execute("ALTER TABLE activity_log_new RENAME TO activity_log")
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL DEFAULT 'legacy',
                event_type TEXT,
                message    TEXT,
                created_at TEXT
            )
        """)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


# ── User management ────────────────────────────────────────────────────────────

def get_user(user_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def upsert_user(user_id: str, email: str, display_name: str) -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, email, display_name, created_at, updated_at) VALUES (?,?,?,?,?)",
            (user_id, email, display_name, now, now),
        )
        conn.execute(
            "UPDATE users SET email=?, display_name=?, updated_at=? WHERE user_id=?",
            (email, display_name, now, user_id),
        )
        conn.commit()


def set_service_start_epoch(user_id: str, epoch: int) -> None:
    """Set service_start_epoch only if it hasn't been set yet (first login)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET service_start_epoch=?, updated_at=? WHERE user_id=? AND service_start_epoch IS NULL",
            (epoch, datetime.utcnow().isoformat(), user_id),
        )
        conn.commit()


def set_setup_status(user_id: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET setup_status=?, updated_at=? WHERE user_id=?",
            (status, datetime.utcnow().isoformat(), user_id),
        )
        conn.commit()


def get_all_users_with_tokens() -> list[str]:
    """Return user_ids that have stored tokens (for scheduler restart)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM user_tokens").fetchall()
    return [r["user_id"] for r in rows]


# ── Token storage ──────────────────────────────────────────────────────────────

def save_token(user_id: str, token_dict: dict) -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_tokens (user_id, token_json, updated_at) VALUES (?,?,?)",
            (user_id, json.dumps(token_dict), now),
        )
        conn.commit()


def load_token(user_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT token_json FROM user_tokens WHERE user_id=?", (user_id,)
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["token_json"])


# ── Per-user config ────────────────────────────────────────────────────────────

def load_user_config(user_id: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT config_json FROM user_configs WHERE user_id=?", (user_id,)
        ).fetchone()
    stored = json.loads(row["config_json"]) if row else {}
    return {**CONFIG_DEFAULTS, **stored}


def save_user_config(user_id: str, config: dict) -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_configs (user_id, config_json, updated_at) VALUES (?,?,?)",
            (user_id, json.dumps(config), now),
        )
        conn.commit()


# ── Per-user params ────────────────────────────────────────────────────────────

def load_user_params(user_id: str) -> dict:
    """Return per-user behavior params; falls back to behavior_params.json."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_params FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
    if row and row["user_params"]:
        return json.loads(row["user_params"])
    from params import load_params
    return load_params()


def save_user_params(user_id: str, params: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET user_params=?, updated_at=? WHERE user_id=?",
            (json.dumps(params), datetime.utcnow().isoformat(), user_id),
        )
        conn.commit()


# ── Contacts ───────────────────────────────────────────────────────────────────

def upsert_contact(
    user_id: str,
    email: str,
    name: Optional[str],
    relationship_type: Optional[str],
    formality_level: Optional[str],
    interaction_count: int = 0,
    last_contact_at: Optional[str] = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_contacts
                (user_id, email, name, relationship_type, formality_level, interaction_count, last_contact_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(user_id, email) DO UPDATE SET
                name=excluded.name,
                relationship_type=excluded.relationship_type,
                formality_level=excluded.formality_level,
                interaction_count=excluded.interaction_count,
                last_contact_at=excluded.last_contact_at
            """,
            (user_id, email, name, relationship_type, formality_level, interaction_count, last_contact_at),
        )
        conn.commit()


def get_contacts(user_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_contacts WHERE user_id=? ORDER BY interaction_count DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Email processing ───────────────────────────────────────────────────────────

def mark_processed(user_id: str, message_id: str, thread_id: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_emails (user_id, message_id, thread_id, processed_at) VALUES (?,?,?,?)",
            (user_id, message_id, thread_id, datetime.utcnow().isoformat()),
        )
        conn.commit()


def is_processed(user_id: str, message_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_emails WHERE user_id=? AND message_id=?",
            (user_id, message_id),
        ).fetchone()
    return row is not None


# ── Review queue ───────────────────────────────────────────────────────────────

def add_to_review_queue(
    user_id: str,
    message_id: str,
    thread_id: str,
    sender: str,
    subject: str,
    snippet: str,
    body: str,
    draft_reply: str,
    classification: dict,
) -> int:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT OR REPLACE INTO review_queue
            (user_id, message_id, thread_id, sender, subject, snippet, body,
             draft_reply, classification, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id, message_id, thread_id, sender, subject, snippet, body,
                draft_reply, json.dumps(classification), "pending", now, now,
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_pending_queue(user_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue WHERE user_id=? AND status='pending' ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_all_queue(user_id: str, limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_queue_item(user_id: str, item_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM review_queue WHERE id=? AND user_id=?", (item_id, user_id)
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_queue_item(user_id: str, item_id: int, status: str, action_taken: Optional[str] = None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE review_queue SET status=?, action_taken=?, updated_at=? WHERE id=? AND user_id=?",
            (status, action_taken, datetime.utcnow().isoformat(), item_id, user_id),
        )
        conn.commit()


def update_draft_reply(user_id: str, item_id: int, draft_reply: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE review_queue SET draft_reply=?, updated_at=? WHERE id=? AND user_id=?",
            (draft_reply, datetime.utcnow().isoformat(), item_id, user_id),
        )
        conn.commit()


# ── Activity log ───────────────────────────────────────────────────────────────

def log_event(user_id: str, event_type: str, message: str):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_log (user_id, event_type, message, created_at) VALUES (?,?,?,?)",
            (user_id, event_type, message, now),
        )
        # Keep only the 200 most recent events per user
        conn.execute("""
            DELETE FROM activity_log
            WHERE user_id=? AND id NOT IN (
                SELECT id FROM activity_log WHERE user_id=? ORDER BY id DESC LIMIT 200
            )
        """, (user_id, user_id))
        conn.commit()


def get_recent_events(user_id: str, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Internal helpers ───────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    d = dict(row)
    if "classification" in d and d["classification"]:
        try:
            d["classification"] = json.loads(d["classification"])
        except Exception:
            pass
    return d
