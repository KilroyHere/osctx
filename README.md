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

- **decisions** — "Chose UUIDs over auto-increment because the system generates IDs across multiple services without a central coordinator, avoiding tight coupling on a single sequence generator."
- **facts** — "PostgreSQL JSONB outperforms EAV tables for sparse attributes because JSONB uses a binary format with indexable keys, while EAV requires a JOIN per attribute."
- **solutions** — "Fixed N+1 with `select_related('author__profile')` — without it, Django issues one query per object in the loop; select_related collapses them into a single JOIN."
- **code_patterns** — reusable snippets and idioms
- **preferences** — your stated tool/language/library preferences
- **references** — papers, docs, links worth keeping

Each unit is 1–3 self-contained sentences including the reasoning — not bare facts. Units get a confidence score, topic tags, and a one-sentence context note. Low-confidence units (< 0.7) are discarded. Near-duplicates (cosine similarity > 0.97) are skipped.

---

## Current Status

**Backend: fully working.** Tested end-to-end with Gemini extraction and e5-small-v2 embeddings.

| Component | Status |
|---|---|
| FastAPI daemon (all 6 endpoints) | ✅ Done |
| ChatGPT + Gemini export parsers | ✅ Done |
| LLM extraction (Anthropic / Gemini / OpenAI / Ollama) | ✅ Done |
| Conversation summaries (2-3 paragraph, per-conversation) | ✅ Done |
| Semantic search with sqlite-vec | ✅ Done |
| Two-level deduplication | ✅ Done |
| CLI (`import`, `search`, `status`, `config`, `install`, `doctor`) | ✅ Done |
| Search UI at `localhost:8765/ui` | ✅ Done |
| Browser extension (Chrome — ChatGPT, Claude.ai, Gemini verified) | ✅ Done |
| Raycast extension (search, detail view, multi-select copy) | ✅ Done |
| MCP server (for Claude Desktop) | ✅ Done |

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

Or open `http://localhost:8765/ui` in your browser, or use the Raycast extension (`Search Memory`). Paste results are XML-wrapped for easy context injection:

```xml
<context source="Chatgpt" date="2025-11-03" topic="database, postgresql">
## Conversation Summary
Optimized a SaaS analytics backend — explored indexing strategies for soft-delete patterns and established guidelines for partial index usage.

## Matched Knowledge
Chose partial indexes (WHERE deleted_at IS NULL) over full btree indexes because query time drops 90% on tables where < 5% of rows are deleted, avoiding index bloat on the common fast path.
</context>
```

In Raycast, press `Cmd+D` to select multiple results, then copy them all as a batch into your next conversation.

---

## Claude Desktop (MCP)

Connect osctx directly to Claude Desktop so Claude can search your memory mid-conversation.

```bash
pip install -e ".[mcp]"   # if not already installed
osctx mcp install         # writes to ~/Library/Application Support/Claude/claude_desktop_config.json
# Restart Claude Desktop
```

Three tools become available to Claude:

- **`search_knowledge`** — semantic search over everything you've captured
- **`get_by_topic`** — fetch all units tagged with a specific topic
- **`save_insight`** — store a new insight directly from the conversation

Example usage in Claude Desktop:
> "Search my memory for postgres indexing strategies"
> "Save this insight: use partial indexes for soft-delete patterns, topic: database"

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
  "extraction_backend": "anthropic",
  "anthropic_api_key": "",
  "gemini_api_key": "",
  "gemini_model": "gemini-flash-latest",
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

- [`docs/INTERFACES.md`](docs/INTERFACES.md) — exact public API of every module
- [`docs/CONSTRAINTS.md`](docs/CONSTRAINTS.md) — hard rules (import graph, test rules, selector rules)
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — tech stack decisions, locked
- [`docs/NEXT.md`](docs/NEXT.md) — prioritized next tasks with implementation detail
- [`docs/STATUS.md`](docs/STATUS.md) — what's built, what's not, known issues

**Note for Homebrew Python users:** sqlite-vec requires `conn.enable_load_extension(True)` before loading. This is already handled in `database.py` but worth knowing if you see `not authorized` errors.

---

## Why Not Just Use [Tool X]?

- **Mem0 / mem.ai** — cloud, opinionated, not yours
- **Obsidian + plugins** — manual, copy-paste, no auto-capture
- **Notion AI** — requires you to manually save to Notion first
- **Browser history** — can't search by meaning, no extraction

OSCTX is local-first, works across all AI tools, extracts semantic knowledge (not raw transcripts), and takes zero manual effort after install.
