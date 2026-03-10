# OSCTX — Claude Code Guide

Universal memory layer for AI conversations. Captures chats, extracts knowledge units via LLM, stores in sqlite-vec, serves via REST + MCP.

## Start here

- **What's built / what's not**: `docs/STATUS.md`
- **What to build next** (prioritized, with implementation detail): `docs/NEXT.md`
- **Tech stack decisions** (locked, don't relitigate): `docs/DECISIONS.md`
- **Public API of every module**: `docs/INTERFACES.md`
- **Hard rules** (import graph, test rules, selector rules): `docs/CONSTRAINTS.md`
- **Full technical spec**: `docs/SPEC.md`
- **DB schema + payload shapes**: `docs/DATA_SAMPLE.md`

## Environment

- Python 3.13, venv at `.venv/`
- Activate: `source .venv/bin/activate`
- Run tests: `.venv/bin/pytest`
- Start daemon: `.venv/bin/uvicorn osctx.daemon.main:app --host 127.0.0.1 --port 8765`
- sqlite-vec requires `conn.enable_load_extension(True)` before loading (Homebrew Python restriction) — already done in `database.py`

## Extraction backend

Four backends supported: `gemini`, `anthropic`, `openai`, `ollama`. Set whichever API key you have:
```bash
osctx config --set extraction_backend=gemini   # or anthropic / openai / ollama
osctx config --set gemini_api_key=YOUR_KEY     # key name matches backend
```
Default model for Gemini: `gemini-flash-latest`.

## Key files

```
osctx/daemon/
  main.py          # FastAPI app, 6 endpoints, lifespan worker
  database.py      # sqlite-vec schema + all CRUD
  extraction.py    # LLM backends (Anthropic/Gemini/OpenAI/Ollama)
  embeddings.py    # e5-small-v2, lazy load, query:/passage: prefixes
  dedup.py         # L1 (URL+count delta) + L2 (cosine similarity)
  ingestion.py     # queue, background worker, crash recovery
  search.py        # semantic search, SearchResult.to_paste() → XML
  parsers/
    chatgpt.py     # tree-traversal parser for conversations.json
    gemini.py      # Takeout JSON parser
osctx/cli/main.py  # Typer CLI: import search status config install doctor
docs/              # all design + context docs
```

## Known issues (from STATUS.md)

1. `gemini_api_key` not in `_DEFAULT_CONFIG` in `main.py` — add it
2. `osctx install` (launchd plist) untested
3. Hybrid search FTS5 table not yet created
4. `extraction_on_battery` config key ignored
