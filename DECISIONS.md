# OSCTX — Technical Decisions

Flat list. No prose. These are locked — do not deviate.

## Language & Framework
- Language: Python 3.11+
- Web framework: FastAPI with uvicorn
- Daemon port: 8765
- CLI: Typer (NOT click, NOT argparse)

## Storage
- Vector store: sqlite-vec loaded as SQLite extension (NOT chromadb, NOT qdrant, NOT FAISS)
- Database file: `~/.osctx/memory.db` — single file, everything in one place
- Config file: `~/.osctx/config.json` — human-readable JSON
- Queue persistence: `~/.osctx/queue.json`
- Log file: `~/.osctx/daemon.log`
- Backup dir: `~/.osctx/backups/`

## Embeddings
- Model: `intfloat/e5-small-v2` (NOT all-MiniLM-L6-v2)
- Dimensions: 384
- Lazy loading: model loads on first encode call, NOT on import
- Prefix: e5 models require "query: " prefix for queries, "passage: " for documents

## Extraction LLM
- Default backend: Claude Haiku 3.5 (`claude-haiku-4-5-20251001`) via anthropic SDK
- Also supported: Gemini (`gemini-flash-latest`) via google-genai SDK — **verified working**
- Also supported: OpenAI GPT-4o-mini, Ollama llama3.2:3b (local)
- Structured output: tool_use (anthropic), tool_calls (openai), response_schema (gemini), format=json (ollama)
- Config key: `extraction_backend` = `"anthropic"` | `"openai"` | `"gemini"` | `"ollama"`
- Gemini model: `gemini-flash-latest` — earlier versions (gemini-2.0-flash, gemini-2.0-flash-lite) are 404 for new accounts

## Deduplication
- Level 1 (conversation-level, pre-extraction):
  - Track per-URL state in `conversation_state` table
  - Key: SHA256(normalized_url) — strip query params and fragments
  - Value: `last_msg_count` (int), `last_captured` (unix timestamp)
  - On capture: if `len(messages) <= last_msg_count` → skip entirely
  - If `len(messages) > last_msg_count` → process only delta (new messages since last_msg_count)
- Level 2 (knowledge unit-level, post-extraction):
  - Compute embedding of new unit
  - Query nearest neighbor in `knowledge_embeddings`
  - cosine similarity > 0.97 → skip (near-duplicate)
  - cosine similarity 0.90–0.97 → store with `similar_to_id` pointing to existing unit
  - cosine similarity < 0.90 → store normally

## Chunking
- Token estimation: 1 token ≈ 4 chars (fast approximation, not tiktoken)
- Hard cap: 6000 tokens per chunk
- Overlap strategy: rolling summary state (NOT raw text overlap)
  - After each chunk, ask LLM: "Summarize what was decided/established in 2-3 sentences"
  - Prepend that summary to the next chunk as `[Context from earlier in conversation: ...]`
- Topic-shift detection: user message that doesn't reference previous exchange AND current chunk > 2000 tokens

## Search
- Phase 0-5: Pure semantic (cosine similarity on e5-small-v2 embeddings)
- Phase 6+: Hybrid (BM25 via SQLite FTS5 + semantic, merged with Reciprocal Rank Fusion)
- Default limit: 5 results
- Score threshold: return results with similarity_score > 0.5 only

## Capture (Browser Extension)
- Strategy 1: `beforeunload` event → dump + POST
- Strategy 2: 5-minute inactivity timer (reset on any new message detected via MutationObserver)
- Strategy 3: `Cmd+Shift+S` manual override hotkey
- DOM selectors: attribute-based ONLY (data-*, aria-*, role). NEVER class-based
- Offline behavior: store payload in `chrome.storage.local`, retry every 60s

## Retrieval
- Mac primary: Raycast extension (Search Memory command)
- Cross-platform fallback: `localhost:8765/ui` served as static HTML by daemon
- Paste format: XML-wrapped `<context source="" date="" topic="">` block
- `Cmd+Shift+M`: opens `localhost:8765/ui` in current tab

## Process Management (macOS)
- launchd plist: `~/Library/LaunchAgents/com.osctx.daemon.plist`
- Start on login: `RunAtLoad = true`
- Restart on crash: `KeepAlive = true`
- Python path: resolved at install time, written into plist

## Config Schema
```json
{
  "extraction_backend": "anthropic",
  "anthropic_api_key": "",
  "openai_api_key": "",
  "gemini_api_key": "",
  "gemini_model": "gemini-flash-latest",
  "ollama_model": "llama3.2:3b",
  "ollama_base_url": "http://localhost:11434",
  "extraction_on_battery": false,
  "dedup_threshold_hard": 0.97,
  "dedup_threshold_soft": 0.90,
  "search_result_limit": 5,
  "search_score_threshold": 0.5,
  "auto_start": true,
  "backup_enabled": true
}
```

## Dependencies (pyproject.toml)
Core (always installed):
- fastapi, uvicorn[standard], anthropic, sentence-transformers, sqlite-vec, typer[all], httpx, pydantic>=2.0

Optional:
- openai>=1.50.0 (for openai backend): `pip install "osctx[openai]"`
- google-genai>=1.0.0 (for gemini backend): `pip install "osctx[gemini]"`

Dev:
- pytest>=8.0, pytest-asyncio>=0.24, pytest-mock>=3.14

## Environment
- Virtual env: `/Users/kilroyhere/Projects/osctx/.venv`
- Run daemon: `.venv/bin/uvicorn osctx.daemon.main:app --port 8765`
- Run CLI: `.venv/bin/osctx <command>`
- Run tests: `.venv/bin/pytest`
- sqlite-vec requires `conn.enable_load_extension(True)` before `sqlite_vec.load(conn)` on Homebrew Python

## What NOT to use
- chromadb — requires separate process
- qdrant — overkill for personal tool
- langchain — adds abstraction without value here
- celery / redis — asyncio.Queue is sufficient
- SQLAlchemy — raw sqlite3 + sqlite-vec is simpler and faster
- tiktoken — overkill, use char-count approximation
