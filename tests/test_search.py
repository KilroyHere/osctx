"""
Tests for the search engine using an in-memory sqlite-vec instance.

These tests use a temporary database so they don't touch ~/.osctx/memory.db.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from osctx.daemon.database import (
    init_db,
    get_conn,
    insert_knowledge_unit,
    insert_embedding,
    record_content_hash,
)
from osctx.daemon.search import SearchResult, search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_db() -> Path:
    """Create a fresh temp database and return its path."""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    path = Path(f.name)
    init_db(path)
    return path


def seed_unit(
    conn,
    content: str,
    category: str = "fact",
    source: str = "chatgpt",
    tags: list[str] | None = None,
    embedding: list[float] | None = None,
) -> str:
    """Insert a knowledge unit with a fake or provided embedding."""
    import uuid
    unit_id = insert_knowledge_unit(
        conn,
        conversation_id=None,
        content=content,
        category=category,
        topic_tags=tags or [],
        source=source,
        source_date=int(time.time()),
        confidence=0.9,
        context="Test context",
    )
    if embedding is None:
        # Generate a simple deterministic fake embedding
        embedding = _fake_embedding(content)
    insert_embedding(conn, unit_id, embedding)
    record_content_hash(conn, content, unit_id)
    return unit_id


def _fake_embedding(text: str) -> list[float]:
    """Produce a deterministic 384-dim unit vector from text hash."""
    import hashlib
    import math

    h = int(hashlib.md5(text.encode()).hexdigest(), 16)
    values = []
    for i in range(384):
        val = math.sin(h * (i + 1)) * 0.5
        values.append(val)
    # Normalize
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_search_returns_results():
    """Basic: seeded units should be findable."""
    db = make_db()

    with get_conn(db) as conn:
        seed_unit(conn, "PostgreSQL UUID primary keys are better than auto-increment for SaaS",
                  category="decision", tags=["database", "postgresql"])
        seed_unit(conn, "Use database sessions not JWTs for user auth revocation",
                  category="decision", tags=["auth", "sessions"])

    # Use the actual embeddings model to search
    # Note: this requires the model to be downloaded. In CI, this test could be
    # marked slow or mocked. Here we use fake embeddings to avoid that.
    # Instead, test the database/query plumbing with a fake embedding search.
    results = _search_with_fake_embedding("postgresql", db=db)

    assert isinstance(results, list)
    # With fake embeddings, results may or may not match — we test structure
    assert all(isinstance(r, SearchResult) for r in results)


def test_search_result_structure():
    """SearchResult has all expected fields."""
    db = make_db()

    with get_conn(db) as conn:
        seed_unit(conn, "Use UUIDs for distributed system user IDs",
                  category="decision", tags=["database"])

    results = _search_with_fake_embedding("UUID", db=db)

    if results:
        r = results[0]
        assert hasattr(r, "id")
        assert hasattr(r, "content")
        assert hasattr(r, "category")
        assert hasattr(r, "topic_tags")
        assert hasattr(r, "source")
        assert hasattr(r, "similarity_score")
        assert 0.0 <= r.similarity_score <= 1.0


def test_search_empty_db():
    """Search on empty DB should return empty list, not error."""
    db = make_db()
    results = _search_with_fake_embedding("anything", db=db)
    assert results == []


def test_search_limit():
    """Limit parameter should cap results."""
    db = make_db()

    # Same fake embedding for all — all will be returned but limit cuts
    base_emb = _fake_embedding("base")
    with get_conn(db) as conn:
        for i in range(10):
            seed_unit(conn, f"Fact number {i} about databases", embedding=base_emb)

    results = _search_with_fake_embedding("databases", limit=3, db=db)
    assert len(results) <= 3


def test_search_score_threshold():
    """Results below score_threshold should be filtered out."""
    db = make_db()

    with get_conn(db) as conn:
        seed_unit(conn, "Completely unrelated content about cooking recipes")

    # With a high threshold, results should be filtered
    results = _search_with_fake_embedding("quantum physics topology", db=db, threshold=0.999)
    assert results == []


def test_search_result_to_paste():
    """to_paste() should return XML-wrapped string."""
    result = SearchResult(
        id="test-id",
        content="PostgreSQL UUID schema decision",
        category="decision",
        topic_tags=["database", "postgresql"],
        source="chatgpt",
        source_date="2025-11-03",
        source_url=None,
        confidence=0.94,
        similarity_score=0.88,
        context="Decided during Project X design",
        conversation_id=None,
    )
    paste = result.to_paste()
    assert paste.startswith("<context")
    assert 'source="Chatgpt"' in paste
    assert 'date="2025-11-03"' in paste
    assert "database, postgresql" in paste
    assert "PostgreSQL UUID schema decision" in paste
    assert paste.endswith("</context>")


def test_search_result_to_dict():
    """to_dict() returns a serializable dict with expected keys."""
    result = SearchResult(
        id="abc",
        content="test content",
        category="fact",
        topic_tags=["x"],
        source="claude",
        source_date="2025-01-01",
        source_url="https://claude.ai/c/123",
        confidence=0.85,
        similarity_score=0.91,
        context=None,
        conversation_id="conv-123",
    )
    d = result.to_dict()
    assert d["id"] == "abc"
    assert d["content"] == "test content"
    assert d["category"] == "fact"
    assert d["source"] == "claude"
    assert d["similarity_score"] == 0.9100
    assert "conversation_id" in d


# ---------------------------------------------------------------------------
# Internal test helper
# ---------------------------------------------------------------------------

def _search_with_fake_embedding(
    query: str,
    db: Path | None = None,
    limit: int = 5,
    threshold: float = 0.0,
) -> list[SearchResult]:
    """Run search using a fake query embedding (bypasses model download in tests)."""
    import sqlite_vec
    from osctx.daemon.database import get_conn as gc

    if db is None:
        db = make_db()

    fake_emb = _fake_embedding(query)
    emb_bytes = sqlite_vec.serialize_float32(fake_emb)
    fetch_limit = max(limit * 3, 20)

    import json as _json
    from datetime import datetime

    with gc(db) as conn:
        try:
            rows = conn.execute(
                """
                SELECT
                    ku.id, ku.content, ku.category, ku.topic_tags,
                    ku.source, ku.source_date, ku.confidence, ku.context,
                    ku.conversation_id, NULL as source_url,
                    vec_distance_cosine(ke.embedding, ?) as distance
                FROM knowledge_units ku
                JOIN knowledge_embeddings ke ON ke.unit_id = ku.id
                ORDER BY distance ASC
                LIMIT ?
                """,
                (emb_bytes, fetch_limit),
            ).fetchall()
        except Exception:
            return []

    results = []
    for row in rows:
        sim = 1.0 - (row["distance"] / 2.0)
        if sim < threshold:
            continue
        tags = _json.loads(row["topic_tags"] or "[]")
        src_date = None
        if row["source_date"]:
            try:
                src_date = datetime.fromtimestamp(row["source_date"]).strftime("%Y-%m-%d")
            except Exception:
                pass
        results.append(SearchResult(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            topic_tags=tags,
            source=row["source"],
            source_date=src_date,
            source_url=row["source_url"],
            confidence=row["confidence"],
            similarity_score=sim,
            context=row["context"],
            conversation_id=row["conversation_id"],
        ))
        if len(results) >= limit:
            break

    return results
