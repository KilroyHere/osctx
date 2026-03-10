"""
Semantic search over the knowledge base.

Phase 0-5: Pure cosine similarity on e5-small-v2 embeddings via sqlite-vec.
Phase 6+: Hybrid BM25 + semantic with Reciprocal Rank Fusion (scaffold included).

Public interface:
  search(query, limit, db_path) -> list[SearchResult]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlite_vec

from .database import DB_PATH, get_conn
from .embeddings import encode_query


@dataclass
class SearchResult:
    id: str
    content: str
    category: str | None
    topic_tags: list[str]
    source: str
    source_date: str | None        # ISO date string for display
    source_url: str | None
    confidence: float | None
    similarity_score: float
    context: str | None
    conversation_id: str | None

    def to_paste(self) -> str:
        """Format as XML-wrapped context for pasting into AI chat."""
        tags_str = ", ".join(self.topic_tags) if self.topic_tags else ""
        date_str = self.source_date or "unknown"
        source_str = self.source.capitalize()

        lines = [
            f'<context source="{source_str}" date="{date_str}" topic="{tags_str}">',
            self.content,
        ]
        if self.context:
            lines.append(self.context)
        lines.append("</context>")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "topic_tags": self.topic_tags,
            "source": self.source,
            "source_date": self.source_date,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "similarity_score": round(self.similarity_score, 4),
            "context": self.context,
            "conversation_id": self.conversation_id,
        }


def _unix_to_date(ts: int | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return None


def search(
    query: str,
    limit: int = 5,
    score_threshold: float = 0.5,
    db_path: Path = DB_PATH,
) -> list[SearchResult]:
    """Semantic search over knowledge units.

    Args:
        query: Natural language search query.
        limit: Max results to return.
        score_threshold: Minimum cosine similarity (0-1). Results below this are excluded.
        db_path: Path to the database file.

    Returns:
        List of SearchResult sorted by similarity descending.
    """
    embedding = encode_query(query)
    embedding_bytes = sqlite_vec.serialize_float32(embedding)

    with get_conn(db_path) as conn:
        # Fetch more than limit to allow filtering by threshold
        fetch_limit = max(limit * 3, 20)

        rows = conn.execute(
            """
            SELECT
                ku.id,
                ku.content,
                ku.category,
                ku.topic_tags,
                ku.source,
                ku.source_date,
                ku.confidence,
                ku.context,
                ku.conversation_id,
                c.url as source_url,
                vec_distance_cosine(ke.embedding, ?) as distance
            FROM knowledge_units ku
            JOIN knowledge_embeddings ke ON ke.unit_id = ku.id
            LEFT JOIN conversations c ON c.id = ku.conversation_id
            ORDER BY distance ASC
            LIMIT ?
            """,
            (embedding_bytes, fetch_limit),
        ).fetchall()

    results: list[SearchResult] = []
    for row in rows:
        # Convert distance to similarity: sim = 1 - dist/2
        similarity = 1.0 - (row["distance"] / 2.0)
        if similarity < score_threshold:
            continue

        tags_raw = row["topic_tags"]
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags = []

        results.append(SearchResult(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            topic_tags=tags,
            source=row["source"],
            source_date=_unix_to_date(row["source_date"]),
            source_url=row["source_url"],
            confidence=row["confidence"],
            similarity_score=similarity,
            context=row["context"],
            conversation_id=row["conversation_id"],
        ))

        if len(results) >= limit:
            break

    return results


# ---------------------------------------------------------------------------
# Phase 6+ scaffold: Hybrid search with BM25
# ---------------------------------------------------------------------------

def _rrf_merge(
    semantic: list[tuple[str, float]],
    bm25: list[tuple[str, float]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of two ranked lists.

    Args:
        semantic: List of (unit_id, score) sorted by score desc.
        bm25: List of (unit_id, score) sorted by score desc.
        k: RRF constant (60 is standard).

    Returns:
        Merged list of (unit_id, rrf_score) sorted by rrf_score desc.
    """
    scores: dict[str, float] = {}
    for rank, (uid, _) in enumerate(semantic):
        scores[uid] = scores.get(uid, 0.0) + 1.0 / (k + rank + 1)
    for rank, (uid, _) in enumerate(bm25):
        scores[uid] = scores.get(uid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def search_hybrid(
    query: str,
    limit: int = 5,
    db_path: Path = DB_PATH,
) -> list[SearchResult]:
    """Hybrid BM25 + semantic search. Requires FTS5 table (Phase 6+).

    Falls back to pure semantic search if FTS5 table doesn't exist.
    """
    try:
        return _hybrid_search_impl(query, limit, db_path)
    except Exception:
        return search(query, limit, db_path=db_path)


def _hybrid_search_impl(
    query: str,
    limit: int,
    db_path: Path,
) -> list[SearchResult]:
    embedding = encode_query(query)
    embedding_bytes = sqlite_vec.serialize_float32(embedding)
    candidate_limit = max(limit * 4, 20)

    with get_conn(db_path) as conn:
        # Semantic candidates
        sem_rows = conn.execute(
            """
            SELECT ku.id, vec_distance_cosine(ke.embedding, ?) as distance
            FROM knowledge_units ku
            JOIN knowledge_embeddings ke ON ke.unit_id = ku.id
            ORDER BY distance ASC
            LIMIT ?
            """,
            (embedding_bytes, candidate_limit),
        ).fetchall()
        semantic = [(r["id"], 1.0 - r["distance"] / 2.0) for r in sem_rows]

        # BM25 candidates
        try:
            bm25_rows = conn.execute(
                """
                SELECT ku.id, bm25(knowledge_fts) as score
                FROM knowledge_units ku
                JOIN knowledge_fts ON knowledge_fts.rowid = ku.rowid
                WHERE knowledge_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (query, candidate_limit),
            ).fetchall()
            bm25 = [(r["id"], -r["score"]) for r in bm25_rows]  # bm25() returns negative
        except Exception:
            bm25 = []

        merged = _rrf_merge(semantic, bm25)
        top_ids = [uid for uid, _ in merged[:candidate_limit]]

        if not top_ids:
            return []

        placeholders = ",".join("?" * len(top_ids))
        rows = conn.execute(
            f"""
            SELECT ku.*, c.url as source_url
            FROM knowledge_units ku
            LEFT JOIN conversations c ON c.id = ku.conversation_id
            WHERE ku.id IN ({placeholders})
            """,
            top_ids,
        ).fetchall()

    row_by_id = {r["id"]: r for r in rows}
    results: list[SearchResult] = []
    for uid, rrf_score in merged[:limit]:
        row = row_by_id.get(uid)
        if not row:
            continue
        tags = json.loads(row["topic_tags"] or "[]")
        results.append(SearchResult(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            topic_tags=tags,
            source=row["source"],
            source_date=_unix_to_date(row["source_date"]),
            source_url=row["source_url"],
            confidence=row["confidence"],
            similarity_score=rrf_score,
            context=row["context"],
            conversation_id=row["conversation_id"],
        ))

    return results
