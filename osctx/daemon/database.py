"""
Database layer for OSCTX.

Single SQLite file at ~/.osctx/memory.db with sqlite-vec extension for vector search.
All CRUD operations live here. No ORM — raw sqlite3 for simplicity and performance.

sqlite-vec is loaded as a runtime extension. Install: pip install sqlite-vec
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import sqlite_vec

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

OSCTX_DIR = Path.home() / ".osctx"
DB_PATH = OSCTX_DIR / "memory.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    url         TEXT,
    title       TEXT,
    captured_at INTEGER NOT NULL,
    raw_json    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'processing', 'done', 'failed'))
);

CREATE TABLE IF NOT EXISTS knowledge_units (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id),
    content         TEXT NOT NULL,
    category        TEXT
        CHECK(category IN ('decision', 'fact', 'solution', 'code_pattern', 'preference', 'reference')),
    topic_tags      TEXT NOT NULL DEFAULT '[]',
    source          TEXT NOT NULL,
    source_date     INTEGER,
    confidence      REAL,
    similar_to_id   TEXT REFERENCES knowledge_units(id),
    context         TEXT,
    created_at      INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS conversation_state (
    url_hash        TEXT PRIMARY KEY,
    last_msg_count  INTEGER NOT NULL,
    last_captured   INTEGER NOT NULL,
    conversation_id TEXT
);

CREATE TABLE IF NOT EXISTS content_hashes (
    hash        TEXT PRIMARY KEY,
    unit_id     TEXT REFERENCES knowledge_units(id),
    created_at  INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_ku_category ON knowledge_units(category);
CREATE INDEX IF NOT EXISTS idx_ku_source ON knowledge_units(source);
CREATE INDEX IF NOT EXISTS idx_ku_date ON knowledge_units(source_date DESC);
CREATE INDEX IF NOT EXISTS idx_conv_status ON conversations(status);
"""

_EMBEDDINGS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_embeddings USING vec0(
    unit_id     TEXT PRIMARY KEY,
    embedding   float[384]
);
"""


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a connection with sqlite-vec loaded and WAL mode enabled."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Create all tables. Safe to call multiple times (IF NOT EXISTS)."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(_EMBEDDINGS_TABLE)
        conn.commit()


@contextmanager
def get_conn(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context manager yielding an open connection. Commits on exit, rolls back on error."""
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def conversation_id_for(url: str | None, first_message: str) -> str:
    """Stable SHA256-based ID for a conversation."""
    raw = f"{url or ''}::{first_message[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()


def upsert_conversation(
    conn: sqlite3.Connection,
    *,
    conv_id: str,
    source: str,
    url: str | None,
    title: str | None,
    captured_at: int,
    messages: list[dict[str, Any]],
    status: str = "pending",
) -> bool:
    """Insert conversation if not already present. Returns True if inserted."""
    existing = conn.execute(
        "SELECT id FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if existing:
        return False

    conn.execute(
        """
        INSERT INTO conversations (id, source, url, title, captured_at, raw_json, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (conv_id, source, url, title, captured_at, json.dumps(messages), status),
    )
    return True


def set_conversation_status(
    conn: sqlite3.Connection, conv_id: str, status: str
) -> None:
    conn.execute(
        "UPDATE conversations SET status = ? WHERE id = ?", (status, conv_id)
    )


def get_pending_conversations(
    conn: sqlite3.Connection, limit: int = 50
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM conversations WHERE status = 'pending' ORDER BY captured_at ASC LIMIT ?",
        (limit,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Conversation state (delta dedup)
# ---------------------------------------------------------------------------

def url_hash(url: str) -> str:
    """Normalize URL and hash it. Strips query params and fragments."""
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url)
    normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return hashlib.sha256(normalized.encode()).hexdigest()


def get_conversation_state(
    conn: sqlite3.Connection, u_hash: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM conversation_state WHERE url_hash = ?", (u_hash,)
    ).fetchone()


def upsert_conversation_state(
    conn: sqlite3.Connection,
    *,
    u_hash: str,
    msg_count: int,
    captured_at: int,
    conv_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO conversation_state (url_hash, last_msg_count, last_captured, conversation_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(url_hash) DO UPDATE SET
            last_msg_count = excluded.last_msg_count,
            last_captured = excluded.last_captured,
            conversation_id = COALESCE(excluded.conversation_id, conversation_id)
        """,
        (u_hash, msg_count, captured_at, conv_id),
    )


# ---------------------------------------------------------------------------
# Knowledge units
# ---------------------------------------------------------------------------

def insert_knowledge_unit(
    conn: sqlite3.Connection,
    *,
    conversation_id: str | None,
    content: str,
    category: str,
    topic_tags: list[str],
    source: str,
    source_date: int | None,
    confidence: float,
    context: str | None = None,
    similar_to_id: str | None = None,
) -> str:
    """Insert a knowledge unit and return its UUID."""
    unit_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO knowledge_units
            (id, conversation_id, content, category, topic_tags, source,
             source_date, confidence, context, similar_to_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            unit_id,
            conversation_id,
            content,
            category,
            json.dumps(topic_tags),
            source,
            source_date,
            confidence,
            context,
            similar_to_id,
        ),
    )
    return unit_id


def insert_embedding(
    conn: sqlite3.Connection, unit_id: str, embedding: list[float]
) -> None:
    """Store a 384-dim embedding for a knowledge unit."""
    conn.execute(
        "INSERT OR REPLACE INTO knowledge_embeddings (unit_id, embedding) VALUES (?, ?)",
        (unit_id, sqlite_vec.serialize_float32(embedding)),
    )


def content_hash_exists(conn: sqlite3.Connection, content: str) -> str | None:
    """Return unit_id if this content hash already exists, else None."""
    h = hashlib.sha256(content.encode()).hexdigest()
    row = conn.execute(
        "SELECT unit_id FROM content_hashes WHERE hash = ?", (h,)
    ).fetchone()
    return row["unit_id"] if row else None


def record_content_hash(
    conn: sqlite3.Connection, content: str, unit_id: str
) -> None:
    h = hashlib.sha256(content.encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO content_hashes (hash, unit_id) VALUES (?, ?)",
        (h, unit_id),
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    total_units = conn.execute("SELECT COUNT(*) FROM knowledge_units").fetchone()[0]
    total_convs = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE status = 'pending'"
    ).fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) as n FROM knowledge_units GROUP BY source"
    ).fetchall()
    by_category = conn.execute(
        "SELECT category, COUNT(*) as n FROM knowledge_units GROUP BY category"
    ).fetchall()

    return {
        "knowledge_units": total_units,
        "conversations": total_convs,
        "pending_extraction": pending,
        "by_source": {r["source"]: r["n"] for r in by_source},
        "by_category": {r["category"]: r["n"] for r in by_category},
    }
