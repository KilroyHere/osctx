# OSCTX — Universal Memory for AI

You talk to ChatGPT, Claude, and Gemini every day. Every insight, decision, and solution evaporates when you close the tab. OSCTX fixes that.

It runs silently in the background, captures your AI conversations, extracts the knowledge that matters, and makes it searchable in under 500ms — ready to paste into your next conversation as grounded context.

---

## How It Works

```
AI chat tab  →  browser extension  →  POST /ingest  →  LLM extraction  →  sqlite-vec
                                                                               ↓
Raycast / localhost:8765/ui  ←────────────────────────── semantic search ──────┘
```

Three components:

1. **Capture** — Chrome extension monitors ChatGPT, Claude.ai, and Gemini. Triggers on tab close, 5-minute inactivity, or `Cmd+Shift+S`. Buffers offline.
2. **Brain** — FastAPI daemon on port 8765. Extracts knowledge units via LLM (Anthropic/Gemini/OpenAI/Ollama), embeds with `intfloat/e5-small-v2`, stores in a single SQLite file with `sqlite-vec`.
3. **Retrieval** — Raycast extension or `localhost:8765/ui`. Copies XML-wrapped context ready to paste.

---

## What Gets Extracted

The LLM reads your conversations and pulls out:

- **decisions** — "Use UUIDs, not auto-increment, for SaaS user IDs"
- **facts** — "PostgreSQL JSONB outperforms EAV tables for sparse attributes"
- **solutions** — "Fixed N+1 with `select_related('author__profile')`"
- **code_patterns** — reusable snippets and idioms
- **preferences** — your stated tool/language/library preferences
- **references** — papers, docs, links worth keeping

Each unit gets a confidence score, topic tags, and a one-sentence context note. Low-confidence units (< 0.7) are discarded. Near-duplicates (cosine similarity > 0.97) are skipped.

---

## Current Status

**Backend: fully working.** Tested end-to-end with Gemini extraction and e5-small-v2 embeddings.

| Component | Status |
|---|---|
| FastAPI daemon (all 6 endpoints) | ✅ Done |
| ChatGPT + Gemini export parsers | ✅ Done |
| LLM extraction (Anthropic / Gemini / OpenAI / Ollama) | ✅ Done |
| Semantic search with sqlite-vec | ✅ Done |
| Two-level deduplication | ✅ Done |
| CLI (`import`, `search`, `status`, `config`, `install`, `doctor`) | ✅ Done |
| Search UI at `localhost:8765/ui` | ✅ Done |
| Browser extension (Chrome) | 🔲 Next |
| Raycast extension | 🔲 Next |
| MCP server (for Claude Desktop) | 🔲 Planned |

---

## Install

**Requirements:** Python 3.11+, macOS (launchd daemon). Linux works without auto-start.

```bash
git clone https://github.com/you/osctx
cd osctx
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gemini]"    # or [openai] for OpenAI backend
```

Configure:

```bash
osctx config --set extraction_backend=gemini
osctx config --set gemini_api_key=YOUR_KEY
```

Start daemon (auto-start on login):

```bash
osctx install          # writes launchd plist, starts daemon
osctx doctor           # verify everything is working
```

Or run manually:

```bash
.venv/bin/uvicorn osctx.daemon.main:app --host 127.0.0.1 --port 8765
```

---

## Import Your History

ChatGPT: Settings → Data Controls → Export Data → upload `conversations.json`
```bash
osctx import ~/Downloads/conversations.json --source chatgpt
```

Gemini (Google Takeout): request takeout at takeout.google.com, select Gemini Apps Activity
```bash
osctx import ~/Downloads/Takeout/Gemini/Gemini\ Apps\ Activity.json --source gemini
```

---

## Search

```bash
osctx search "postgres indexing strategy"
```

Or open `http://localhost:8765/ui` in your browser. Paste results are XML-wrapped for easy context injection:

```xml
<context source="Chatgpt" date="2025-11-03" topic="database, postgresql">
Use partial indexes for soft-delete patterns. WHERE deleted_at IS NULL on a
btree index cuts query time by 90% on tables with < 5% deleted rows.
Context: Optimization discussion for a SaaS analytics backend.
</context>
```

---

## Single-Chat Capture (Without Extension)

```bash
curl -s http://localhost:8765/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "source": "chatgpt",
    "url": "https://chat.openai.com/c/my-conv-id",
    "captured_at": '"$(date +%s)"',
    "messages": [
      {"role": "user", "content": "How do I fix N+1 queries in Django?"},
      {"role": "assistant", "content": "Use select_related() for FK and prefetch_related() for M2M..."}
    ]
  }'
```

Check it worked:

```bash
curl -s http://localhost:8765/status | python3 -m json.tool
osctx search "django queries"
```

---

## Storage

Everything lives in `~/.osctx/`:

```
~/.osctx/
├── memory.db       # SQLite + sqlite-vec (vectors + knowledge units)
├── config.json     # API keys and settings (chmod 600)
├── queue.json      # Persisted extraction queue (survives crashes)
├── daemon.log      # Daemon logs
└── backups/        # Manual backups
```

No cloud. No separate vector database process. No Redis. One file.

---

## Configuration

```bash
osctx config --show                          # view all (keys redacted)
osctx config --set extraction_backend=gemini
osctx config --set search_result_limit=10
```

Full config schema:

```json
{
  "extraction_backend": "gemini",
  "gemini_api_key": "",
  "gemini_model": "gemini-flash-latest",
  "anthropic_api_key": "",
  "openai_api_key": "",
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

---

## Development

```bash
.venv/bin/pytest          # run tests (no API calls — all mocked)
osctx doctor              # check environment
```

Key docs for contributors:

- [`INTERFACES.md`](INTERFACES.md) — exact public API of every module
- [`CONSTRAINTS.md`](CONSTRAINTS.md) — hard rules (import graph, test rules, selector rules)
- [`DECISIONS.md`](DECISIONS.md) — tech stack decisions, locked
- [`NEXT.md`](NEXT.md) — prioritized next tasks with implementation detail
- [`STATUS.md`](STATUS.md) — what's built, what's not, known issues

**Note for Homebrew Python users:** sqlite-vec requires `conn.enable_load_extension(True)` before loading. This is already handled in `database.py` but worth knowing if you see `not authorized` errors.

---

## Why Not Just Use [Tool X]?

- **Mem0 / mem.ai** — cloud, opinionated, not yours
- **Obsidian + plugins** — manual, copy-paste, no auto-capture
- **Notion AI** — requires you to manually save to Notion first
- **Browser history** — can't search by meaning, no extraction

OSCTX is local-first, works across all AI tools, extracts semantic knowledge (not raw transcripts), and takes zero manual effort after install.
