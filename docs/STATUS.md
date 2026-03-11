# OSCTX — Current Implementation Status

Last updated: 2026-03-11

---

## What's Built and Verified Working

### Backend (Python daemon)

| Module | File | Status | Notes |
|---|---|---|---|
| Data types | `parsers/base.py` | ✅ Done | `Message`, `Conversation` dataclasses |
| ChatGPT parser | `parsers/chatgpt.py` | ✅ Done | Tree traversal, all edge cases from DATA_SAMPLE.md |
| Gemini parser | `parsers/gemini.py` | ✅ Done | Takeout JSON, gmr: prefix variants |
| Database layer | `database.py` | ✅ Done | sqlite-vec, all tables, CRUD + `conversations.summary` column |
| Embeddings | `embeddings.py` | ✅ Done | e5-small-v2, lazy load, correct prefixes |
| Deduplication | `dedup.py` | ✅ Done | Level 1 (URL delta) + Level 2 (cosine) |
| Extraction | `extraction.py` | ✅ Done | 4 backends, chunking, rolling summary; units are 1-3 sentences with reasoning/tradeoffs |
| Search | `search.py` | ✅ Done | Semantic + hybrid BM25+semantic (RRF); both paths return `conversation_summary` |
| Ingestion queue | `ingestion.py` | ✅ Done | asyncio.Queue + disk persistence; stores conversation summary post-extraction |
| Hybrid search FTS5 | `database.py` | ✅ Done | `knowledge_fts` FTS5 table + insert trigger |
| Battery deferral | `ingestion.py` | ✅ Done | `extraction_on_battery=false` defers on battery (macOS `pmset`) |
| FastAPI daemon | `main.py` | ✅ Done | 8 endpoints: `/ingest`, `/ingest/bulk`, `/search`, `/search/hybrid`, `/status`, `/units`, `/units/{id}` (DELETE), `/ui` |

### Browser Extension (Chrome MV3)

| Component | File | Status | Notes |
|---|---|---|---|
| Manifest | `extension/manifest.json` | ✅ Done | MV3, host_permissions, `notifications` permission |
| Background worker | `extension/background.ts` | ✅ Done | Cmd+Shift+S save, Cmd+Shift+M search, offline retry loop |
| Notifications | `extension/content/utils.ts` | ✅ Done | On capture: "N messages queued"; 45s later: "+N units saved" |
| ChatGPT content script | `extension/content/chatgpt.ts` | ✅ Done | 3 capture triggers, attribute-based selectors |
| Claude content script | `extension/content/claude.ts` | ✅ Done | `data-test-render-count` + `data-is-streaming` — **live verified** |
| Gemini content script | `extension/content/gemini.ts` | ✅ Done | `user-query`/`model-response` custom elements, strips UI prefixes — **live verified** |
| Popup | `extension/popup.html + .ts` | ✅ Done | Dark theme, live stats, daemon status dot |
| Build | `extension/build.mjs` | ✅ Done | esbuild → `dist/` |

### Web UI (`localhost:8765/ui`)

| Component | Status | Notes |
|---|---|---|
| Search tab | ✅ Done | Semantic + hybrid, category/source filters, score display |
| Browse tab | ✅ Done | All units, grouped by category, server-side filtering via `/units` API |
| Recent tab | ✅ Done | Last 50 units by `created_at DESC` |
| Multi-select copy | ✅ Done | Shift/Cmd+click or when any card selected; copies as XML `<context>` blocks |
| Delete button | ✅ Done | ✕ on card hover → confirm → `DELETE /units/{id}` → removed from cache instantly |
| Keyboard nav | ✅ Done | `/` to focus, `↑↓` to move, `Enter` to copy |

### Raycast Extension

| Component | Status | Notes |
|---|---|---|
| Search Memory command | ✅ Done | Category icons, conversation summary in detail pane |
| Multi-select | ✅ Done | Cmd+D to toggle; floating "Copy N Selected" bar; `toPasteFormatMulti()` |
| Paste format | ✅ Done | XML `<context>` with summary + matched knowledge; multi outputs N blocks |

### CLI

| Command | Status | Notes |
|---|---|---|
| `osctx import` | ✅ Done | Works with and without daemon running |
| `osctx search` | ✅ Done | Falls back to direct DB if daemon down |
| `osctx status` | ✅ Done | `--watch` mode included |
| `osctx logs` | ✅ Done | Live daemon log tail |
| `osctx config` | ✅ Done | `--set`, `--get`, `--show` with key redaction |
| `osctx install` | ✅ Done | macOS launchd plist — **not yet tested on real login cycle** |
| `osctx doctor` | ✅ Done | Checks Python, packages, config, daemon, plist |
| `osctx mcp install` | ✅ Done | Writes Claude Desktop config entry |
| `osctx mcp uninstall` | ✅ Done | Removes entry from Claude Desktop config |

### MCP Server

| Component | Status | Notes |
|---|---|---|
| `search_knowledge` | ✅ Done | Semantic search, auto-triggered by Claude on memory questions |
| `get_by_topic` | ✅ Done | Exact tag match |
| `save_insight` | ✅ Done | Exact + cosine dedup before storing |
| `ingest_conversation` | ✅ Done | Sends current chat to daemon for full LLM extraction |

### Tests

| File | Status | Coverage |
|---|---|---|
| `tests/test_parsers.py` | ✅ Done | 15 tests — ChatGPT + Gemini parsers, all edge cases |
| `tests/test_search.py` | ✅ Done | Search structure, dedup, `to_paste()` with/without summary |
| `tests/test_extraction.py` | ✅ Done | 31 tests — all 4 backends mocked, chunking, rolling summary, dedup |

---

## Verified End-to-End

**Full pipeline test (2026-03-11):**
- Chrome extension captured ChatGPT/Claude/Gemini conversations
- Gemini `gemini-flash-latest` extracted knowledge units + conversation summary
- `intfloat/e5-small-v2` embedded all units
- MCP server connected to Claude Desktop (handshake verified via logs)
- Raycast multi-select copy produced correct XML with conversation summary
- Delete button removed unit from DB and UI cache immediately
- Recent tab showed newly captured units sorted by time

**Known working config:**
```json
{
  "extraction_backend": "gemini",
  "gemini_api_key": "...",
  "gemini_model": "gemini-flash-latest"
}
```

---

## What's Not Built

| Component | Priority | Notes |
|---|---|---|
| `osctx install` real test | Medium | launchd plist untested on real login cycle |
| Daemon auto-start from CLI | Medium | `osctx search` should start daemon if not running |
| Claude.ai export importer | Medium | No parser for Claude's own export format |
| Popup live extraction feedback | Low | Show unit delta in popup after capture |
| Format toggle (XML/MD/Plain) | Low | Currently XML only |
| Conversation-centric view | Low | Browse by conversation, not just category |
| `osctx export` command | Low | Dump knowledge base to JSON/Markdown |
| Perplexity / Notion importers | Future | — |
| Cross-device sync | Future | — |

---

## Known Issues

1. **`osctx install` untested** — launchd plist is correct but path resolution at login is unverified.

2. **`extraction_on_battery` config key partially ignored** — `pmset` check works on macOS but logs at INFO on Linux/CI where `pmset` doesn't exist; silently falls back to always-extract.

---

## Environment

- Python: 3.11+ (tested on 3.13 via Homebrew)
- Virtual env: `.venv/` (in project root)
- Run daemon: `.venv/bin/uvicorn osctx.daemon.main:app --port 8765`
- Run CLI: `.venv/bin/osctx <command>`
- Run tests: `.venv/bin/pytest`
- DB: `~/.osctx/memory.db`
- Config: `~/.osctx/config.json`
- Logs: `~/.osctx/daemon.log`
