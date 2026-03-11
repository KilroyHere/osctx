"""
OSCTX MCP Server — exposes personal knowledge base to Claude Desktop.

Tools:
  search_knowledge(query, limit=5)  — semantic search over knowledge units
  get_by_topic(topic)               — fetch all units tagged with a topic
  save_insight(content, topic)      — store a new insight from Claude Desktop

Run:
  python -m osctx.mcp_server.server

Install into Claude Desktop:
  osctx mcp install
"""

from __future__ import annotations

import time

from mcp.server.fastmcp import FastMCP

from osctx.daemon.database import (
    content_hash_exists,
    get_conn,
    insert_embedding,
    insert_knowledge_unit,
    record_content_hash,
)
from osctx.daemon.embeddings import encode_passage
from osctx.daemon.search import search

mcp = FastMCP("osctx")


@mcp.tool()
async def search_knowledge(query: str, limit: int = 5) -> list[dict]:
    """Search your personal knowledge base built from AI conversations."""
    results = search(query, limit=limit)
    return [r.to_dict() for r in results]


@mcp.tool()
async def get_by_topic(topic: str) -> list[dict]:
    """Retrieve all knowledge units tagged with a specific topic."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_units WHERE topic_tags LIKE ? ORDER BY created_at DESC",
            (f'%"{topic}"%',),
        ).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
async def save_insight(content: str, topic: str) -> str:
    """Save a new insight directly from this Claude Desktop conversation."""
    with get_conn() as conn:
        if content_hash_exists(conn, content):
            return "Already stored."
        embedding = encode_passage(content)
        unit_id = insert_knowledge_unit(
            conn,
            conversation_id=None,
            content=content,
            category="fact",
            topic_tags=[topic],
            source="claude_desktop",
            source_date=int(time.time()),
            confidence=1.0,
        )
        insert_embedding(conn, unit_id, embedding)
        record_content_hash(conn, content, unit_id)
    return f"Saved {unit_id}"


if __name__ == "__main__":
    mcp.run()
