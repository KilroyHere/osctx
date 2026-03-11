"""
OSCTX Daemon — FastAPI application.

Endpoints:
  POST /ingest              — Receives raw chat dump from extension
  POST /ingest/bulk         — Receives file path for bulk imports
  GET  /search?q=&limit=5   — Semantic search
  GET  /search/hybrid?q=    — BM25 + vector rerank (Phase 6+)
  GET  /status              — Health check + stats
  GET  /ui                  — Serves the minimal search HTML

Run with: uvicorn osctx.daemon.main:app --port 8765
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database import DB_PATH, OSCTX_DIR, get_stats, init_db, get_conn
from .ingestion import (
    BulkIngestRequest,
    IngestRequest,
    _load_queue_from_disk,
    background_worker,
    enqueue_bulk,
    enqueue_ingest,
)
from .search import SearchResult, search, search_hybrid

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

CONFIG_PATH = OSCTX_DIR / "config.json"

_DEFAULT_CONFIG: dict[str, Any] = {
    "extraction_backend": "anthropic",
    "anthropic_api_key": "",
    "openai_api_key": "",
    "gemini_api_key": "",
    "gemini_model": "gemini-flash-latest",
    "ollama_model": "llama3.2:3b",
    "ollama_base_url": "http://localhost:11434",
    "extraction_on_battery": False,
    "dedup_threshold_hard": 0.97,
    "dedup_threshold_soft": 0.90,
    "search_result_limit": 5,
    "search_score_threshold": 0.5,
    "auto_start": True,
    "backup_enabled": True,
}


def load_config() -> dict[str, Any]:
    config = dict(_DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            overrides = json.loads(CONFIG_PATH.read_text())
            config.update(overrides)
        except Exception as exc:
            logging.warning("Failed to load config: %s", exc)
    return config


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    log_path = OSCTX_DIR / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        logging.FileHandler(log_path),
    ]
    if sys.stdout.isatty():
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

_worker_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger = logging.getLogger(__name__)

    config = load_config()
    app.state.config = config

    # Initialize DB
    init_db(DB_PATH)
    logger.info("Database initialized at %s", DB_PATH)

    # Restore persisted queue
    _load_queue_from_disk()

    # Start background worker
    global _worker_task
    _worker_task = asyncio.create_task(background_worker(config))
    logger.info("OSCTX daemon started on port 8765")

    yield

    # Shutdown
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    logger.info("OSCTX daemon stopped")


app = FastAPI(title="OSCTX Daemon", version="0.1.0", lifespan=lifespan)
_UI_DIR = Path(__file__).parent / "ui"
app.mount("/static", StaticFiles(directory=str(_UI_DIR)), name="static")

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/ingest")
async def ingest(req: IngestRequest) -> JSONResponse:
    """Receive a raw chat dump from the browser extension or CLI."""
    config = app.state.config
    result = enqueue_ingest(req, db_path=DB_PATH)
    status_code = 202 if result["status"] == "queued" else 200
    return JSONResponse(content=result, status_code=status_code)


@app.post("/ingest/bulk")
async def ingest_bulk(req: BulkIngestRequest) -> JSONResponse:
    """Enqueue all conversations from a bulk import file."""
    result = enqueue_bulk(req.file_path, req.source, db_path=DB_PATH)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["reason"])
    return JSONResponse(content=result, status_code=202)


@app.get("/search")
async def search_endpoint(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=5, ge=1, le=50),
    hybrid: bool = Query(default=False),
) -> JSONResponse:
    """Semantic search over the knowledge base."""
    config = app.state.config
    effective_limit = min(limit, config.get("search_result_limit", 5))
    threshold = config.get("search_score_threshold", 0.5)

    if hybrid:
        results = search_hybrid(q, limit=effective_limit, db_path=DB_PATH)
    else:
        results = search(q, limit=effective_limit, score_threshold=threshold, db_path=DB_PATH)

    return JSONResponse(content={"results": [r.to_dict() for r in results], "query": q})


@app.get("/search/hybrid")
async def search_hybrid_endpoint(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=5, ge=1, le=50),
) -> JSONResponse:
    """Hybrid BM25 + semantic search (Phase 6+)."""
    results = search_hybrid(q, limit=limit, db_path=DB_PATH)
    return JSONResponse(content={"results": [r.to_dict() for r in results], "query": q})


@app.get("/status")
async def status() -> JSONResponse:
    """Health check and statistics."""
    from .ingestion import _queue

    with get_conn(DB_PATH) as conn:
        stats = get_stats(conn)

    stats["queue_depth"] = _queue.qsize()
    stats["db_path"] = str(DB_PATH)
    stats["status"] = "ok"
    return JSONResponse(content=stats)


@app.get("/units")
async def units(
    category: str | None = None,
    source: str | None = None,
    limit: int = 500,
) -> JSONResponse:
    """Return all knowledge units, optionally filtered by category and/or source."""
    with get_conn() as conn:
        query = "SELECT * FROM knowledge_units WHERE 1=1"
        params: list = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if source:
            query += " AND source LIKE ?"
            params.append(f"%{source}%")
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    import json as _json
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["topic_tags"] = _json.loads(d.get("topic_tags") or "[]")
        except Exception:
            pass
        results.append(d)
    return JSONResponse(content={"units": results, "total": len(results)})


@app.get("/ui", response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    """Serve the minimal search UI."""
    html_path = Path(__file__).parent / "ui" / "search.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text())
    return HTMLResponse(content="<h1>OSCTX</h1><p>search.html not found</p>", status_code=500)
