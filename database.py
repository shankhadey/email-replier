"""
SQLite persistence for processed emails and the review queue.
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

DB_FILE = Path("gmail_replier.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_emails (
                message_id TEXT PRIMARY KEY,
                thread_id  TEXT,
                processed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id  TEXT UNIQUE,
                thread_id   TEXT,
                sender      TEXT,
                subject     TEXT,
                snippet     TEXT,
                body        TEXT,
                draft_reply TEXT,
                classification TEXT,
                status      TEXT DEFAULT 'pending',
                action_taken TEXT,
                created_at  TEXT,
                updated_at  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                message    TEXT,
                created_at TEXT
            )
        """)
        conn.commit()


def mark_processed(message_id: str, thread_id: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_emails (message_id, thread_id, processed_at) VALUES (?,?,?)",
            (message_id, thread_id, datetime.utcnow().isoformat()),
        )
        conn.commit()


def is_processed(message_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_emails WHERE message_id=?", (message_id,)
        ).fetchone()
    return row is not None


def add_to_review_queue(
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
            (message_id, thread_id, sender, subject, snippet, body, draft_reply, classification, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                message_id, thread_id, sender, subject, snippet, body,
                draft_reply, json.dumps(classification), "pending", now, now,
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_pending_queue() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue WHERE status='pending' ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_all_queue(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_queue_item(item_id: int, status: str, action_taken: Optional[str] = None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE review_queue SET status=?, action_taken=?, updated_at=? WHERE id=?",
            (status, action_taken, datetime.utcnow().isoformat(), item_id),
        )
        conn.commit()


def update_draft_reply(item_id: int, draft_reply: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE review_queue SET draft_reply=?, updated_at=? WHERE id=?",
            (draft_reply, datetime.utcnow().isoformat(), item_id),
        )
        conn.commit()


def get_queue_item(item_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM review_queue WHERE id=?", (item_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def log_event(event_type: str, message: str):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_log (event_type, message, created_at) VALUES (?,?,?)",
            (event_type, message, now),
        )
        # Keep only the 200 most recent events
        conn.execute("""
            DELETE FROM activity_log
            WHERE id NOT IN (SELECT id FROM activity_log ORDER BY id DESC LIMIT 200)
        """)
        conn.commit()


def get_recent_events(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def _row_to_dict(row) -> dict:
    d = dict(row)
    if "classification" in d and d["classification"]:
        try:
            d["classification"] = json.loads(d["classification"])
        except Exception:
            pass
    return d
