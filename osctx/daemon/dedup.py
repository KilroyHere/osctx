"""
Two-level deduplication.

Level 1 — Conversation-level (pre-extraction):
  Uses conversation_state table. Tracks last_msg_count per URL hash.
  If new message count <= last count → skip entirely.
  If new count > last count → process only delta (new messages since last count).

Level 2 — Knowledge unit-level (post-extraction):
  Computes cosine similarity of new unit embedding against all existing embeddings.
  > 0.97 → skip (near-duplicate)
  0.90–0.97 → store with similar_to_id pointing to closest match
  < 0.90 → store normally
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import NamedTuple

from .database import (
    content_hash_exists,
    get_conversation_state,
    record_content_hash,
    upsert_conversation_state,
    url_hash,
)


# ---------------------------------------------------------------------------
# Level 1: Conversation-level dedup
# ---------------------------------------------------------------------------

@dataclass
class DeltaResult:
    should_process: bool
    delta_messages: list[dict]    # only the new messages since last capture
    all_messages: list[dict]       # full message list (for conversation record)
    is_first_capture: bool


def check_conversation_delta(
    conn: sqlite3.Connection,
    raw_url: str,
    messages: list[dict],
) -> DeltaResult:
    """Check if this conversation has new content since last capture.

    Returns a DeltaResult indicating whether to process and which messages are new.
    """
    u_hash = url_hash(raw_url)
    state = get_conversation_state(conn, u_hash)
    msg_count = len(messages)

    if state is None:
        # First time seeing this URL
        return DeltaResult(
            should_process=True,
            delta_messages=messages,
            all_messages=messages,
            is_first_capture=True,
        )

    last_count = state["last_msg_count"]

    if msg_count <= last_count:
        # No new messages
        return DeltaResult(
            should_process=False,
            delta_messages=[],
            all_messages=messages,
            is_first_capture=False,
        )

    # There are new messages — return only the delta
    delta = messages[last_count:]
    return DeltaResult(
        should_process=True,
        delta_messages=delta,
        all_messages=messages,
        is_first_capture=False,
    )


def update_conversation_state(
    conn: sqlite3.Connection,
    raw_url: str,
    msg_count: int,
    conv_id: str | None = None,
) -> None:
    """Update the state for this URL after successful processing."""
    u_hash = url_hash(raw_url)
    upsert_conversation_state(
        conn,
        u_hash=u_hash,
        msg_count=msg_count,
        captured_at=int(time.time()),
        conv_id=conv_id,
    )


# ---------------------------------------------------------------------------
# Level 2: Knowledge unit-level dedup
# ---------------------------------------------------------------------------

class DedupDecision(NamedTuple):
    action: str         # 'skip' | 'store_linked' | 'store'
    similar_to_id: str | None  # set when action == 'store_linked'


THRESHOLD_HARD = 0.97   # skip
THRESHOLD_SOFT = 0.90   # store with link


def check_unit_dedup(
    conn: sqlite3.Connection,
    content: str,
    embedding: list[float],
    hard_threshold: float = THRESHOLD_HARD,
    soft_threshold: float = THRESHOLD_SOFT,
) -> DedupDecision:
    """Check a candidate knowledge unit against existing units.

    First checks exact content hash (O(1)), then semantic similarity.
    """
    # Fast path: exact content hash
    existing_id = content_hash_exists(conn, content)
    if existing_id:
        return DedupDecision(action="skip", similar_to_id=existing_id)

    # Semantic similarity check
    nearest = _find_nearest(conn, embedding)
    if nearest is None:
        return DedupDecision(action="store", similar_to_id=None)

    unit_id, similarity = nearest

    if similarity >= hard_threshold:
        return DedupDecision(action="skip", similar_to_id=unit_id)
    elif similarity >= soft_threshold:
        return DedupDecision(action="store_linked", similar_to_id=unit_id)
    else:
        return DedupDecision(action="store", similar_to_id=None)


def _find_nearest(
    conn: sqlite3.Connection,
    embedding: list[float],
) -> tuple[str, float] | None:
    """Find the nearest neighbor embedding. Returns (unit_id, cosine_similarity) or None."""
    import sqlite_vec

    try:
        rows = conn.execute(
            """
            SELECT unit_id, vec_distance_cosine(embedding, ?) as distance
            FROM knowledge_embeddings
            ORDER BY distance ASC
            LIMIT 1
            """,
            (sqlite_vec.serialize_float32(embedding),),
        ).fetchall()
    except Exception:
        # knowledge_embeddings may be empty — vec0 raises on empty table in some versions
        return None

    if not rows:
        return None

    row = rows[0]
    # vec_distance_cosine returns distance (0=identical, 2=opposite)
    # Convert to similarity: similarity = 1 - (distance / 2)
    distance = row["distance"]
    similarity = 1.0 - (distance / 2.0)
    return row["unit_id"], similarity


def finalize_unit_storage(
    conn: sqlite3.Connection,
    content: str,
    unit_id: str,
) -> None:
    """Record content hash after successful unit insertion."""
    record_content_hash(conn, content, unit_id)
