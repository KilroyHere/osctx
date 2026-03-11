"""
OSCTX MCP Server — exposes personal knowledge base to Claude Desktop.

Tools:
  search_knowledge(query, limit=5)       — semantic search over knowledge units
  get_by_topic(topic)                    — fetch all units tagged with a topic
  save_insight(content, topic)           — store a new insight from Claude Desktop
  ingest_conversation(messages, title)   — send current chat to daemon for full extraction

Run:
  python -m osctx.mcp_server.server

Install into Claude Desktop:
  osctx mcp install
"""

from __future__ import annotations

import time
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

from osctx.daemon.database import (
    DB_PATH,
    content_hash_exists,
    get_conn,
    insert_embedding,
    insert_knowledge_unit,
    record_content_hash,
)
from osctx.daemon.dedup import check_unit_dedup
from osctx.daemon.embeddings import encode_passage
from osctx.daemon.search import search

mcp = FastMCP("osctx")

DAEMON_URL = "http://localhost:8765"


@mcp.tool()
async def search_knowledge(query: str, limit: int = 5) -> list[dict]:
    """Search the user's personal long-term memory built from past AI conversations.
    Use this proactively whenever the user asks about something they may have
    discussed, decided, or learned before — even if they don't explicitly ask
    to search memory. Good triggers: technical questions, project context,
    preferences, past decisions, 'what did we decide about X', 'do I have notes on Y'."""
    results = search(query, limit=limit)
    return [r.to_dict() for r in results]


@mcp.tool()
async def get_by_topic(topic: str) -> list[dict]:
    """Retrieve all saved knowledge units tagged with a specific topic from the
    user's personal memory. Use when the user asks about a specific subject area
    or tag (e.g. 'show me everything about postgres', 'what do I know about auth')."""
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_units WHERE topic_tags LIKE ? ORDER BY created_at DESC",
            (f'%"{topic}"%',),
        ).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
async def save_insight(content: str, topic: str) -> str:
    """Save a single insight, fact, or decision into the user's personal memory.
    Use when the user says 'remember this', 'save this', 'note that', or when
    an important decision or preference is established mid-conversation."""
    embedding = encode_passage(content)
    with get_conn(DB_PATH) as conn:
        if content_hash_exists(conn, content):
            return "Already stored."
        decision = check_unit_dedup(conn, content, embedding)
        if decision.action == "skip":
            return "Already stored (near-duplicate)."
        unit_id = insert_knowledge_unit(
            conn,
            conversation_id=None,
            content=content,
            category="fact",
            topic_tags=[topic],
            source="claude_desktop",
            source_date=int(time.time()),
            confidence=1.0,
            similar_to_id=decision.similar_to_id,
        )
        insert_embedding(conn, unit_id, embedding)
        record_content_hash(conn, content, unit_id)
    return f"Saved {unit_id}"


@mcp.tool()
async def ingest_conversation(
    messages: list[dict],
    title: Optional[str] = None,
) -> str:
    """Send this entire conversation to the osctx daemon for full LLM extraction
    and storage into long-term memory. Use when the user says 'save this chat',
    'add this conversation to memory', 'extract knowledge from this chat', or
    similar. Pass ALL messages so far as [{"role": "user"|"assistant", "content": str}, ...].
    Requires the daemon to be running (port 8765)."""
    payload: dict = {
        "source": "claude_desktop",
        "messages": messages,
        "captured_at": int(time.time()),
    }
    if title:
        payload["title"] = title

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{DAEMON_URL}/ingest", json=payload)
            data = resp.json()
    except httpx.ConnectError:
        return "Error: daemon not running. Start it with: uvicorn osctx.daemon.main:app --host 127.0.0.1 --port 8765"
    except Exception as exc:
        return f"Error: {exc}"

    status = data.get("status", "unknown")
    if status == "queued":
        return (
            f"Queued for extraction — {data.get('new_messages', 0)} messages. "
            f"Knowledge units will be ready in ~30 seconds."
        )
    if status == "duplicate":
        return f"No new messages since last capture: {data.get('reason', '')}"
    return f"Status: {status}"


if __name__ == "__main__":
    mcp.run()
