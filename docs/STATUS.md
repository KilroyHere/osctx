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
| Database layer | `database.py` | ✅ Done | sqlite-vec, all tables, CRUD + `conversations.summary` column |
| Embeddings | `embeddings.py` | ✅ Done | e5-small-v2, lazy load, correct prefixes |
| Deduplication | `dedup.py` | ✅ Done | Level 1 (URL delta) + Level 2 (cosine) |
| Extraction | `extraction.py` | ✅ Done | Anthropic/OpenAI/Gemini/Ollama backends, chunking, `summarize_conversation()`; units are 1-3 sentences with reasoning |
| Search | `search.py` | ✅ Done | Semantic search; dedup filter (`similar_to_id IS NULL`); returns `conversation_summary` |
| Ingestion queue | `ingestion.py` | ✅ Done | asyncio.Queue + disk persistence; stores conversation summary post-extraction |
| FastAPI daemon | `main.py` | ✅ Done | All 6 endpoints, lifespan management; full config defaults including Gemini keys |
| Search UI | `ui/search.html` | ✅ Done | Dark theme, keyboard nav, XML paste |

### Browser Extension (Chrome MV3)

| Component | File | Status | Notes |
|---|---|---|---|
| Manifest | `extension/manifest.json` | ✅ Done | MV3, host_permissions incl. localhost |
| Background worker | `extension/background.ts` | ✅ Done | Cmd+Shift+S save, Cmd+Shift+M search, offline retry loop |
| ChatGPT content script | `extension/content/chatgpt.ts` | ✅ Done | 3 capture triggers, attribute-based selectors |
| Claude content script | `extension/content/claude.ts` | ✅ Done | `data-test-render-count` wrappers + `data-is-streaming` role detection — **live verified** |
| Gemini content script | `extension/content/gemini.ts` | ✅ Done | `user-query`/`model-response` custom elements, strips "You said"/"Gemini said" prefixes — **live verified** |
| Popup | `extension/popup.html + .ts` | ✅ Done | Dark theme, stats, daemon status dot |
| Build | `extension/build.mjs` | ✅ Done | esbuild, bundles to `dist/` |

### Raycast Extension

| Component | File | Status | Notes |
|---|---|---|---|
| Search Memory command | `raycast-extension/src/search-memory.tsx` | ✅ Done | Category icons/colors, conversation summary in detail + paste |
| Multi-select | `search-memory.tsx` | ✅ Done | Cmd+D to toggle, floating "Copy N Selected" bar, `toPasteFormatMulti()` |
| Action flow | — | ✅ Done | Enter → detail view; Cmd+Enter → copy current; Cmd+D → toggle select |
| Paste format | `toPasteFormat()` / `toPasteFormatMulti()` | ✅ Done | XML `<context>` with summary + matched knowledge sections; multi outputs N blocks |

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
| `tests/test_search.py` | ✅ Done | Search structure, dedup, to_paste (with summary), to_dict |
| `tests/test_extraction.py` | ✅ Done | LLM extraction with mocked backends (all 4), chunking, summarize_conversation |

---

## Verified End-to-End

**Full pipeline test (2026-03-10):**
- Chrome extension captured ChatGPT conversation via button click
- Gemini `gemini-flash-latest` extracted 7 knowledge units + conversation summary
- `intfloat/e5-small-v2` embedded all units
- Raycast `Search Memory` returned results with similarity scores, conversation summary in detail pane
- Copy as Context produced correct `<context>` XML with `## Conversation Summary` + `## Matched Knowledge` sections
- Dedup filter confirmed: soft-duplicate units excluded from search results

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
| MCP server | Phase 5 | **HIGH** — enables Claude Desktop to query memory mid-conversation |
| `osctx install` real test | — | Medium — launchd plist untested on real login cycle |
| Hybrid search FTS5 table | Phase 6 | Low — scaffold exists, migration not yet written |
| `extraction_on_battery` config key | — | Low — defined but ignored |
| Cross-device sync | Phase 6 | Future |
| Perplexity / Notion importers | Phase 6 | Future |

---

## Known Issues / Tech Debt

1. **`osctx install` untested** — the launchd plist is written correctly but has not been tested through a full login cycle. The uvicorn command path resolution may need verification.

2. **Hybrid search FTS5 table missing** — `search_hybrid()` falls back to pure semantic if FTS5 table missing. FTS5 table is never created — needs a migration in `database.py` before Phase 6 uses it.

3. **`extraction_on_battery` config key ignored** — defined in config schema, never read in code.

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
