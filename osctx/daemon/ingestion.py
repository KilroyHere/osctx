"""
Ingestion queue and processing pipeline.

Flow:
  POST /ingest → validate → dedup check → enqueue → 202 Accepted
  Background worker → pull from queue → extract → embed → store

Queue persists to ~/.osctx/queue.json so restarts don't lose work.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

from .database import (
    DB_PATH,
    conversation_id_for,
    get_conn,
    get_pending_conversations,
    init_db,
    insert_embedding,
    insert_knowledge_unit,
    set_conversation_status,
    upsert_conversation,
)
from .dedup import (
    DedupDecision,
    check_conversation_delta,
    check_unit_dedup,
    finalize_unit_storage,
    update_conversation_state,
)
from .embeddings import encode_batch, encode_passage
from .extraction import extract_from_messages

logger = logging.getLogger(__name__)

OSCTX_DIR = Path.home() / ".osctx"
QUEUE_FILE = OSCTX_DIR / "queue.json"


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class MessageIn(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("user", "assistant"):
            raise ValueError(f"role must be 'user' or 'assistant', got {v!r}")
        return v


class IngestRequest(BaseModel):
    source: str                    # 'chatgpt' | 'claude' | 'gemini'
    url: str | None = None
    captured_at: int | None = None
    messages: list[MessageIn]
    title: str | None = None


class BulkIngestRequest(BaseModel):
    file_path: str
    source: str = "chatgpt"


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


def _load_queue_from_disk() -> None:
    """Restore queued items from disk on startup."""
    if not QUEUE_FILE.exists():
        return
    try:
        items = json.loads(QUEUE_FILE.read_text())
        for item in items:
            _queue.put_nowait(item)
        logger.info("Restored %d items from queue file", len(items))
        QUEUE_FILE.unlink()  # Clear after loading
    except Exception as exc:
        logger.warning("Failed to load queue from disk: %s", exc)


def _save_queue_to_disk() -> None:
    """Persist queued items to disk on shutdown."""
    items: list[dict] = []
    while not _queue.empty():
        try:
            items.append(_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    if items:
        OSCTX_DIR.mkdir(parents=True, exist_ok=True)
        QUEUE_FILE.write_text(json.dumps(items))
        logger.info("Saved %d queue items to disk", len(items))


# ---------------------------------------------------------------------------
# Ingestion entry point
# ---------------------------------------------------------------------------

def enqueue_ingest(req: IngestRequest, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Validate, dedup-check, and enqueue an ingest request.

    Returns a response dict immediately (async processing happens in background).
    """
    if not req.messages:
        return {"status": "ignored", "reason": "no messages"}

    messages = [m.model_dump() for m in req.messages]
    url = req.url or ""
    captured_at = req.captured_at or int(time.time())

    with get_conn(db_path) as conn:
        delta = check_conversation_delta(conn, url, messages)

    if not delta.should_process:
        return {"status": "duplicate", "reason": "no new messages since last capture"}

    conv_id = conversation_id_for(url, messages[0]["content"] if messages else "")

    item = {
        "conv_id": conv_id,
        "source": req.source,
        "url": url,
        "title": req.title,
        "captured_at": captured_at,
        "messages": delta.all_messages,
        "delta_messages": delta.delta_messages,
        "db_path": str(db_path),
    }
    _queue.put_nowait(item)

    return {
        "status": "queued",
        "conversation_id": conv_id,
        "new_messages": len(delta.delta_messages),
        "queue_depth": _queue.qsize(),
    }


def enqueue_bulk(file_path: str, source: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    """Enqueue all conversations from a bulk import file."""
    from .parsers.chatgpt import parse_chatgpt_export
    from .parsers.gemini import parse_gemini_export

    path = Path(file_path).expanduser()
    if not path.exists():
        return {"status": "error", "reason": f"File not found: {file_path}"}

    if source == "chatgpt":
        conversations = parse_chatgpt_export(str(path))
    elif source == "gemini":
        conversations = parse_gemini_export(str(path))
    else:
        return {"status": "error", "reason": f"Unknown source: {source}"}

    queued = 0
    for conv in conversations:
        messages = [{"role": m.role, "content": m.content} for m in conv.messages]
        conv_id = conversation_id_for(conv.url or f"{conv.source}:{conv.id}", messages[0]["content"])
        item = {
            "conv_id": conv_id,
            "source": conv.source,
            "url": conv.url,
            "title": conv.title,
            "captured_at": int(conv.create_time or time.time()),
            "messages": messages,
            "delta_messages": messages,
            "db_path": str(db_path),
        }
        _queue.put_nowait(item)
        queued += 1

    return {
        "status": "queued",
        "conversations_queued": queued,
        "queue_depth": _queue.qsize(),
    }


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

async def _process_item(item: dict[str, Any], config: dict[str, Any]) -> None:
    """Process a single queued item: extract → embed → store."""
    db_path = Path(item["db_path"])
    conv_id = item["conv_id"]
    messages = item["messages"]
    delta_messages = item["delta_messages"]

    try:
        # Insert conversation record
        with get_conn(db_path) as conn:
            inserted = upsert_conversation(
                conn,
                conv_id=conv_id,
                source=item["source"],
                url=item.get("url"),
                title=item.get("title"),
                captured_at=item["captured_at"],
                messages=messages,
                status="processing",
            )

        # Extract knowledge units from delta messages only
        units = await extract_from_messages(delta_messages, config=config)
        logger.info("Extracted %d units from conv %s", len(units), conv_id[:8])

        # Embed all units in one batch
        if units:
            contents = [u.content for u in units]
            embeddings = encode_batch(contents, is_query=False)

            with get_conn(db_path) as conn:
                for unit, embedding in zip(units, embeddings):
                    decision: DedupDecision = check_unit_dedup(conn, unit.content, embedding)

                    if decision.action == "skip":
                        logger.debug("Skipping duplicate unit: %s…", unit.content[:60])
                        continue

                    similar_id = decision.similar_to_id if decision.action == "store_linked" else None
                    unit_id = insert_knowledge_unit(
                        conn,
                        conversation_id=conv_id,
                        content=unit.content,
                        category=unit.category,
                        topic_tags=unit.topic_tags,
                        source=item["source"],
                        source_date=item["captured_at"],
                        confidence=unit.confidence,
                        context=unit.context,
                        similar_to_id=similar_id,
                    )
                    insert_embedding(conn, unit_id, embedding)
                    finalize_unit_storage(conn, unit.content, unit_id)

        # Mark done and update conversation state
        with get_conn(db_path) as conn:
            set_conversation_status(conn, conv_id, "done")
            url = item.get("url", "")
            if url:
                update_conversation_state(conn, url, len(messages), conv_id)

    except Exception as exc:
        logger.error("Failed to process conv %s: %s", conv_id[:8], exc, exc_info=True)
        with get_conn(db_path) as conn:
            set_conversation_status(conn, conv_id, "failed")


async def background_worker(config: dict[str, Any]) -> None:
    """Long-running asyncio task that processes the ingestion queue."""
    logger.info("Background worker started")

    # On startup: process any conversations stuck in 'processing' state from previous crash
    try:
        with get_conn() as conn:
            stuck = get_pending_conversations(conn)
            if stuck:
                logger.info("Requeuing %d conversations from previous session", len(stuck))
                for row in stuck:
                    msgs = json.loads(row["raw_json"])
                    _queue.put_nowait({
                        "conv_id": row["id"],
                        "source": row["source"],
                        "url": row["url"],
                        "title": row["title"],
                        "captured_at": row["captured_at"],
                        "messages": msgs,
                        "delta_messages": msgs,
                        "db_path": str(DB_PATH),
                    })
    except Exception as exc:
        logger.warning("Could not requeue pending conversations: %s", exc)

    while True:
        try:
            item = await _queue.get()
            await _process_item(item, config)
            _queue.task_done()
        except asyncio.CancelledError:
            logger.info("Background worker shutting down")
            _save_queue_to_disk()
            return
        except Exception as exc:
            logger.error("Unexpected error in background worker: %s", exc, exc_info=True)
