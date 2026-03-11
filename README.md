# OSCTX — Universal Memory for AI

You talk to ChatGPT, Claude, and Gemini every day. Every insight, decision, and solution evaporates when you close the tab. OSCTX fixes that.

It runs silently in the background, captures your AI conversations, extracts the knowledge that matters, and makes it searchable in under 500ms — ready to paste into your next conversation as grounded context.

---

## How It Works

```
AI chat tab  →  browser extension  →  POST /ingest  →  LLM extraction  →  sqlite-vec
                                                                               ↓
Raycast / localhost:8765/ui  ←────────────────────────── semantic search ──────┘
                                                                               ↓
Claude Desktop (MCP)  ←──────────────────────── search_knowledge tool ─────────┘
```

Three components:

1. **Capture** — Chrome extension monitors ChatGPT, Claude.ai, and Gemini. Triggers on tab close, 5-minute inactivity, or `Cmd+Shift+S`. Buffers offline.
2. **Brain** — FastAPI daemon on port 8765. Extracts knowledge units via LLM (Anthropic/Gemini/OpenAI/Ollama), embeds with `intfloat/e5-small-v2`, stores in a single SQLite file with `sqlite-vec`.
3. **Retrieval** — Raycast extension, `localhost:8765/ui` browser UI, or Claude Desktop MCP tools.

---

## What Gets Extracted

Each unit is 1–3 self-contained sentences including the reasoning:

- **decisions** — "Chose UUIDs over auto-increment because the system generates IDs across multiple services without a central coordinator, avoiding tight coupling on a single sequence generator."
- **facts** — "PostgreSQL JSONB outperforms EAV tables for sparse attributes because JSONB uses a binary format with indexable keys, while EAV requires a JOIN per attribute."
- **solutions** — "Fixed N+1 with `select_related('author__profile')` — without it, Django issues one query per object in the loop; select_related collapses them into a single JOIN."
- **code_patterns** — reusable snippets and idioms
- **preferences** — your stated tool/language/library preferences
- **references** — papers, docs, links worth keeping

Low-confidence units (< 0.7) are discarded. Near-duplicates (cosine similarity > 0.97) are skipped.

---

## Current Status

| Component | Status |
|---|---|
| FastAPI daemon (6 endpoints) | ✅ Done |
| ChatGPT + Gemini export parsers | ✅ Done |
| LLM extraction (Anthropic / Gemini / OpenAI / Ollama) | ✅ Done |
| Conversation summaries (2–3 paragraphs, per-conversation) | ✅ Done |
| Semantic + hybrid (BM25+vector) search | ✅ Done |
| Two-level deduplication | ✅ Done |
| CLI (`import`, `search`, `status`, `logs`, `config`, `install`, `doctor`) | ✅ Done |
| Browser UI at `localhost:8765/ui` (search + browse + multi-select) | ✅ Done |
| Browser extension (Chrome — ChatGPT, Claude.ai, Gemini) | ✅ Done |
| Raycast extension (search, detail view, multi-select copy) | ✅ Done |
| MCP server for Claude Desktop (search, save, ingest conversation) | ✅ Done |

---

## Quick Setup (fresh clone)

### 1 — Python environment

```bash
git clone https://github.com/you/osctx
cd osctx
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gemini]"    # swap 'gemini' for 'openai' or 'anthropic'
```

### 2 — Configure your LLM key

```bash
osctx config --set extraction_backend=gemini
osctx config --set gemini_api_key=YOUR_KEY
# or: osctx config --set extraction_backend=anthropic
#     osctx config --set anthropic_api_key=YOUR_KEY
```

### 3 — Start the daemon

```bash
# One-time, in a terminal (keep it running):
.venv/bin/uvicorn osctx.daemon.main:app --host 127.0.0.1 --port 8765

# Or auto-start on login (macOS):
osctx install

# Check health:
osctx logs          # status + live log tail
osctx doctor        # dependency check
```

### 4 — Browser extension (Chrome)

1. Open `chrome://extensions`, enable **Developer mode**
2. Click **Load unpacked** → select the `extension/` folder
3. Pin the extension. Click it on any ChatGPT/Claude/Gemini page to capture.

### 5 — Raycast extension (optional)

```bash
cd raycast-extension
npm install
npm run build
```

Open Raycast → Extensions → + Import Extension → select `raycast-extension/`. Search with `Search Memory`.

### 6 — Claude Desktop MCP (optional)

```bash
pip install -e ".[mcp]"
osctx mcp install       # writes Claude Desktop config
# Quit and reopen Claude Desktop
```

---

## Import Existing History

**ChatGPT:** Settings → Data Controls → Export Data → download `conversations.json`
```bash
osctx import ~/Downloads/conversations.json --source chatgpt
```

**Gemini:** [takeout.google.com](https://takeout.google.com) → select "Gemini Apps Activity" → download
```bash
osctx import ~/Downloads/Takeout/Gemini/Gemini\ Apps\ Activity.json --source gemini
```

Monitor extraction progress:
```bash
osctx logs
```

---

## Search & Retrieval

**Browser UI** — open `http://localhost:8765/ui`:
- **Search tab**: semantic + hybrid search with category/source filters
- **Browse tab**: all units grouped by category (Decisions, Solutions, Facts…)
- Click any card to copy as `<context>` XML. Shift+click or Cmd+click to multi-select.

**CLI:**
```bash
osctx search "postgres indexing strategy"
```

**Raycast** — `Search Memory`, then:
- `Enter` → copy top result as context
- `Cmd+D` → toggle select, build a batch
- `Cmd+Enter` when items selected → copy all selected

Paste format:
```xml
<context source="Chatgpt" date="2025-11-03" topic="database, postgresql">
## Conversation Summary
Optimized a SaaS analytics backend — explored indexing strategies for soft-delete patterns.

## Matched Knowledge
Chose partial indexes (WHERE deleted_at IS NULL) over full btree indexes because query
time drops 90% on tables where < 5% of rows are deleted, avoiding index bloat.
</context>
```

---

## Claude Desktop (MCP)

After `osctx mcp install` and restarting Claude Desktop, four tools are available:

| Tool | When Claude uses it |
|---|---|
| `search_knowledge` | Automatically when you ask about past decisions, projects, preferences |
| `get_by_topic` | "Show me everything about postgres" |
| `save_insight` | "Remember this", "Note that", mid-conversation facts |
| `ingest_conversation` | "Save this chat to memory", "Extract knowledge from this conversation" |

Example prompts:
> "What do I know about authentication strategies?"
> "Save this conversation to memory"
> "Do I have any notes on React performance?"

---

## Monitoring

```bash
osctx logs             # status + live daemon log (Ctrl+C to stop)
osctx status           # snapshot stats
osctx status --watch   # refresh every 3s
curl http://localhost:8765/status | python3 -m json.tool
```

MCP server logs (Claude Desktop):
```bash
tail -f ~/Library/Logs/Claude/mcp-server-osctx.log
```

---

## Storage

Everything lives in `~/.osctx/`:

```
~/.osctx/
├── memory.db       # SQLite + sqlite-vec (vectors + knowledge units)
├── config.json     # API keys and settings
├── queue.json      # Persisted extraction queue (survives crashes)
├── daemon.log      # Daemon logs
└── backups/        # Manual backups
```

No cloud. No separate vector database. No Redis. One file.

---

## Configuration

```bash
osctx config --show
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
source .venv/bin/activate
.venv/bin/pytest          # all tests (no API calls — all mocked)
osctx doctor              # check environment
```

Key docs:
- [`docs/INTERFACES.md`](docs/INTERFACES.md) — public API of every module
- [`docs/STATUS.md`](docs/STATUS.md) — what's built, what's not, known issues
- [`docs/CONSTRAINTS.md`](docs/CONSTRAINTS.md) — hard rules (import graph, test rules)
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — tech stack decisions, locked
- [`docs/NEXT.md`](docs/NEXT.md) — prioritized next tasks

**Note for Homebrew Python users:** `sqlite-vec` requires `conn.enable_load_extension(True)` before loading. Already handled in `database.py` — mention if you see `not authorized` errors.

---

## Why Not Just Use [Tool X]?

- **Mem0 / mem.ai** — cloud, opinionated, not yours
- **Obsidian + plugins** — manual, copy-paste, no auto-capture
- **Notion AI** — requires you to manually save to Notion first
- **Browser history** — can't search by meaning, no extraction

OSCTX is local-first, works across all AI tools, extracts semantic knowledge (not raw transcripts), and takes zero manual effort after setup.
