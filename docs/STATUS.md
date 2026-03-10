# OSCTX — Current Implementation Status

Last updated: 2026-03-10

---

## What's Built and Verified Working

### Backend (Python daemon)

| Module | File | Status | Notes |
|---|---|---|---|
| Data types | `parsers/base.py` | ✅ Done | `Message`, `Conversation` dataclasses |
| ChatGPT parser | `parsers/chatgpt.py` | ✅ Done | Tree traversal, all edge cases from DATA_SAMPLE.md |
| Gemini parser | `parsers/gemini.py` | ✅ Done | Takeout JSON, gmr: prefix variants |
| Database layer | `database.py` | ✅ Done | sqlite-vec, all tables, CRUD |
| Embeddings | `embeddings.py` | ✅ Done | e5-small-v2, lazy load, correct prefixes |
| Deduplication | `dedup.py` | ✅ Done | Level 1 (URL delta) + Level 2 (cosine) |
| Extraction | `extraction.py` | ✅ Done | Anthropic/OpenAI/Gemini/Ollama backends, chunking |
| Search | `search.py` | ✅ Done | Semantic search working; hybrid BM25 scaffold present |
| Ingestion queue | `ingestion.py` | ✅ Done | asyncio.Queue + disk persistence |
| FastAPI daemon | `main.py` | ✅ Done | All 6 endpoints, lifespan management |
| Search UI | `ui/search.html` | ✅ Done | Dark theme, keyboard nav, XML paste |

### CLI

| Command | Status | Notes |
|---|---|---|
| `osctx import` | ✅ Done | Works with and without daemon running |
| `osctx search` | ✅ Done | Falls back to direct DB if daemon down |
| `osctx status` | ✅ Done | `--watch` mode included |
| `osctx config` | ✅ Done | `--set`, `--get`, `--show` with key redaction |
| `osctx install` | ✅ Done | macOS launchd plist — **not yet tested on a real install** |
| `osctx uninstall` | ✅ Done | Untested |
| `osctx doctor` | ✅ Done | Checks Python, packages, config, daemon, plist |

### Tests

| Test file | Status | Coverage |
|---|---|---|
| `tests/test_parsers.py` | ✅ Done | 15 tests, all chatgpt + gemini edge cases |
| `tests/test_search.py` | ✅ Done | Search structure, dedup, to_paste, to_dict |
| `tests/test_extraction.py` | ❌ Missing | LLM extraction with mocked backends |

---

## Verified End-to-End

**Full pipeline test (2026-03-10):**
- POST conversation to `/ingest`
- Gemini `gemini-flash-latest` extracted 4 knowledge units
- `intfloat/e5-small-v2` embedded all units
- `/search?q=vector+similarity+distance` returned 4 ranked results (similarity scores 0.91–0.94)
- `to_paste()` XML format correct

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

| Component | Phase | Priority |
|---|---|---|
| Browser extension | Phase 1 | HIGH — closes capture loop |
| Raycast extension | Phase 2 | HIGH — closes retrieval loop |
| `tests/test_extraction.py` | Phase 3 | Medium |
| `osctx install` real test | Phase 2 | Medium |
| MCP server | Phase 5 | Low |
| Claude export (no official API) | Phase 2 | Low |
| Perplexity / Notion importers | Phase 6 | Future |
| Cross-device sync | Phase 6 | Future |

---

## Known Issues / Tech Debt

1. **`gemini_api_key` not in `main.py` DEFAULT_CONFIG** — `extraction.py` has it, `main.py` doesn't. When loading config, the key must come from `~/.osctx/config.json`, not the defaults in `main.py`. Works correctly in practice but inconsistent.

2. **`osctx install` untested** — the launchd plist is written correctly but has not been tested through a full login cycle. The uvicorn command path resolution may need verification.

3. **Hybrid search untested** — `search_hybrid()` falls back to pure semantic if FTS5 table missing. FTS5 table is never created — needs a migration in `database.py` before Phase 6 uses it.

4. **`extraction_on_battery` config key ignored** — defined in config schema, never read in code.

5. **No automatic nightly backup** — defined in spec, not implemented.

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
