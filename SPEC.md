# OSCTX — Technical Specification

## System Architecture

```
╔═══════════════════════════════════════════════════════════════╗
║  CAPTURE LAYER                                                 ║
║  ┌─────────────────────────┐   ┌──────────────────────────┐  ║
║  │  Browser Extension      │   │  Bulk Import CLI         │  ║
║  │  (TypeScript, MV3)      │   │  (Python script)         │  ║
║  │  • beforeunload event   │   │  • ChatGPT JSON export   │  ║
║  │  • 5min inactivity      │   │  • Gemini Takeout        │  ║
║  │  • Cmd+Shift+S hotkey   │   │  • Drop files, walk away │  ║
║  │  • POST to :8765        │   │                          │  ║
║  └────────────┬────────────┘   └──────────────┬───────────┘  ║
╚═══════════════╪════════════════════════════════╪══════════════╝
                │                                │
                ▼                                ▼
╔═══════════════════════════════════════════════════════════════╗
║  BRAIN (Python FastAPI Daemon — port 8765)                     ║
║  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐ ║
║  │  Ingestion   │  │  Extraction  │  │  Search             │ ║
║  │  Queue       │→ │  Engine      │→ │  Engine             │ ║
║  │              │  │  (LLM-based) │  │  (vector + BM25)    │ ║
║  └──────────────┘  └──────────────┘  └──────────┬──────────┘ ║
║                                                  │            ║
║  ┌───────────────────────────────────────────────▼──────────┐ ║
║  │  sqlite-vec  (~/.osctx/memory.db)                        │ ║
║  │  • Knowledge Units (text + metadata)                     │ ║
║  │  • Vector embeddings (e5-small-v2, 384-dim)              │ ║
║  │  • Source conversations (raw, for drill-down)            │ ║
║  └──────────────────────────────────────────────────────────┘ ║
╚═══════════════════════════════════════════════════════════════╝
                │                    │
                ▼                    ▼
╔═══════════════════╗  ╔═════════════════════════════════════╗
║  RETRIEVAL A      ║  ║  RETRIEVAL B                        ║
║  REST /search     ║  ║  MCP Server (Phase 5)               ║
║  → Raycast ext    ║  ║  • search_knowledge tool            ║
║  → HTML overlay   ║  ║  • get_by_topic tool                ║
║  → Any app        ║  ║  • save_insight tool                ║
╚═══════════════════╝  ╚═════════════════════════════════════╝
```

## Design Principles

1. **Dumb pipe, smart brain.** Extension has no intelligence — captures raw text, POSTs it. All logic in daemon.
2. **One file, own your data.** Everything in `~/.osctx/memory.db`. Backup = `cp`. No cloud.
3. **Async by default.** `/ingest` returns 202 immediately. Processing in background.
4. **Graceful degradation.** LLM API down → raw chunks stored, extraction queued. Product still useful.
5. **Platform-first Mac, portable by design.** Raycast for overlay. Daemon runs on any POSIX.

---

## Component Specifications

### 4.1 Browser Extension

**Tech:** TypeScript, Manifest V3, Chrome/Brave/Arc compatible

**Permissions:**
```json
{
  "permissions": ["activeTab", "storage"],
  "host_permissions": [
    "https://chat.openai.com/*",
    "https://claude.ai/*",
    "https://gemini.google.com/*"
  ]
}
```

**Capture strategy (priority order):**
1. `beforeunload` event → dump + POST
2. 5-minute inactivity timer → POST if new messages since last POST
3. `Cmd+Shift+S` manual hotkey

**Content script selectors:**
| Platform | Primary selector | Fallback |
|---|---|---|
| ChatGPT | `[data-message-author-role]` | structural traversal |
| Claude.ai | `[data-testid*="message"]` | `div.font-claude-message` |
| Gemini | `.model-response-text`, `.user-query-text` | attribute preferred |

**Rule:** Never use class-based selectors with random hashes. Always target semantic attributes.

**POST payload:**
```json
{
  "source": "chatgpt",
  "url": "https://chat.openai.com/c/abc123",
  "captured_at": 1709123456,
  "messages": [
    {"role": "user", "content": "How should I structure the database..."},
    {"role": "assistant", "content": "For this use case, I'd recommend..."}
  ]
}
```

**`Cmd+Shift+M`:** Opens `localhost:8765/ui` in current tab.

---

### 4.2 The Daemon

**Tech:** Python 3.11+, FastAPI, uvicorn, sqlite-vec, sentence-transformers, anthropic SDK

**Process management:** launchd plist at `~/Library/LaunchAgents/com.osctx.daemon.plist`. Logs to `~/.osctx/daemon.log`.

**Endpoints:**
```
POST /ingest              — Receives raw chat dump from extension
POST /ingest/bulk         — Receives file path for bulk imports
GET  /search?q=&limit=5   — Semantic search, returns Knowledge Units
GET  /search/hybrid?q=    — BM25 + vector rerank (Phase 3)
GET  /status              — Health check, returns stats
GET  /ui                  — Serves the minimal search HTML
```

**Queue persistence:** `asyncio.Queue` backed by `~/.osctx/queue.json` (persists across restarts).

---

### 4.3 Raycast Extension

**Single command:** Search Memory

**Result display:**
```
┌─────────────────────────────────────────────────────┐
│ 🔍 database schema project x                         │
├─────────────────────────────────────────────────────┤
│ ● PostgreSQL schema for Project X auth system        │
│   ChatGPT · Nov 3, 2025 · confidence: 0.94          │
│   "users table with UUID pk, email unique index..."  │
│                                                      │
│ ● Decision: use UUIDs not auto-increment             │
│   ChatGPT · Nov 3, 2025 · confidence: 0.89          │
└─────────────────────────────────────────────────────┘
```

**On Enter:** Copies XML-wrapped content to clipboard, closes Raycast.

---

## Database Schema

**File:** `~/.osctx/memory.db`

```sql
-- Raw conversation storage (source of truth)
CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,  -- SHA256(url + first_message)
    source      TEXT NOT NULL,     -- 'chatgpt', 'claude', 'gemini', 'manual'
    url         TEXT,
    title       TEXT,
    captured_at INTEGER NOT NULL,  -- unix timestamp
    raw_json    TEXT NOT NULL,     -- original messages array
    status      TEXT DEFAULT 'pending'  -- 'pending', 'processing', 'done', 'failed'
);

-- Extracted knowledge units (what gets searched)
CREATE TABLE knowledge_units (
    id              TEXT PRIMARY KEY,   -- UUID
    conversation_id TEXT REFERENCES conversations(id),
    content         TEXT NOT NULL,
    category        TEXT,               -- 'decision', 'fact', 'code', 'preference', 'solution'
    topic_tags      TEXT,               -- JSON array: ["database", "postgresql"]
    source          TEXT NOT NULL,
    source_date     INTEGER,            -- unix timestamp
    confidence      REAL,               -- 0.0–1.0
    similar_to_id   TEXT REFERENCES knowledge_units(id),  -- soft dedup link
    created_at      INTEGER DEFAULT (unixepoch())
);

-- Vector embeddings (sqlite-vec extension)
CREATE VIRTUAL TABLE knowledge_embeddings USING vec0(
    unit_id     TEXT PRIMARY KEY,
    embedding   float[384]              -- e5-small-v2 produces 384-dim
);

-- Dedup: track per-URL capture state for delta detection
CREATE TABLE conversation_state (
    url_hash        TEXT PRIMARY KEY,   -- SHA256(url)
    last_msg_count  INTEGER NOT NULL,
    last_captured   INTEGER NOT NULL,   -- unix timestamp
    conversation_id TEXT
);

-- Content-level dedup
CREATE TABLE content_hashes (
    hash        TEXT PRIMARY KEY,       -- SHA256 of normalized content
    unit_id     TEXT REFERENCES knowledge_units(id),
    created_at  INTEGER DEFAULT (unixepoch())
);

-- Indexes
CREATE INDEX idx_ku_category ON knowledge_units(category);
CREATE INDEX idx_ku_source ON knowledge_units(source);
CREATE INDEX idx_ku_date ON knowledge_units(source_date DESC);
CREATE INDEX idx_conv_status ON conversations(status);
```

---

## Extraction Engine

### Extraction Prompt (Section 6.1)

```
You are a knowledge extraction engine. Your only job is to identify
durable, reusable knowledge from an AI conversation.

EXTRACT these types of knowledge units:
- Decisions: Choices made that will affect future work ("decided to use UUIDs")
- Facts: Technical truths established in the conversation
- Solutions: Specific problems that were solved, with the solution
- Code patterns: Reusable code structures, not one-off snippets
- Preferences: User's stated preferences or constraints
- References: Books, tools, services the user should look up again

DO NOT EXTRACT:
- Small talk or meta-conversation about the AI
- Hypotheticals that were not adopted
- Information the user already clearly knew (they stated it as fact)
- Anything with a confidence below 0.7

OUTPUT FORMAT: A JSON array only. No preamble. No explanation.
If nothing is worth extracting, return [].

Each item:
{
  "content": "The specific knowledge unit, written as a standalone sentence",
  "category": "decision|fact|solution|code_pattern|preference|reference",
  "topic_tags": ["tag1", "tag2"],  // 1-4 specific topics
  "confidence": 0.0-1.0,
  "context": "One sentence explaining why this is worth keeping"
}

CONVERSATION:
{conversation_text}
```

### Extraction Model

| Option | Cost/10k convs | Quality | Latency | Use |
|---|---|---|---|---|
| Claude Haiku 3.5 | ~$2 | Excellent | ~1s | Default |
| GPT-4o-mini | ~$1.50 | Very good | ~1.2s | Alternative |
| Ollama llama3.2:3b | Free | Good (70-75%) | 1-2s M-series | Local |

**Default:** Claude Haiku 3.5 via `tool_use` mode for structured JSON output.

### Chunking Strategy

Chunk conversations > 6000 tokens:
- Hard cap: 6000 tokens per chunk
- Rolling summary state as context (NOT raw overlap)
- Topic-shift detection: user message after assistant response that doesn't reference prior exchange

### Deduplication

**Level 1 (pre-extraction):** SHA256(url + first_message_content). Check `conversation_state` table. Track `last_msg_count` — only re-process if count has increased.

**Level 2 (post-extraction):**
| Cosine similarity | Action |
|---|---|
| > 0.97 | Skip — near-identical unit exists |
| 0.90–0.97 | Store with `similar_to_id` link |
| < 0.90 | Store normally |

---

## Retrieval Layer

### Semantic Search

```python
async def semantic_search(query: str, limit: int = 5) -> list[KnowledgeUnit]:
    query_embedding = embedder.encode(query)
    results = db.execute("""
        SELECT ku.*, vec_distance_cosine(ke.embedding, ?) as distance
        FROM knowledge_units ku
        JOIN knowledge_embeddings ke ON ke.unit_id = ku.id
        ORDER BY distance ASC
        LIMIT ?
    """, [query_embedding.tolist(), limit])
    return [KnowledgeUnit(**r) for r in results]
```

### Response Format

```json
{
  "id": "uuid",
  "content": "PostgreSQL schema: users (uuid pk, email unique, created_at timestamp)",
  "category": "decision",
  "topic_tags": ["database", "postgresql", "project-x"],
  "source": "chatgpt",
  "source_date": "2025-11-03",
  "source_url": "https://chat.openai.com/c/abc123",
  "confidence": 0.94,
  "similarity_score": 0.91,
  "context": "Decided during Project X database design session"
}
```

### Paste Format (XML-wrapped)

```xml
<context source="ChatGPT" date="2025-11-03" topic="database, postgresql">
PostgreSQL schema: users (uuid pk, email unique, created_at timestamp)
Decided during Project X database design session.
</context>
```

---

## File Structure (Appendix A)

```
osctx/
├── daemon/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, lifespan events
│   ├── ingestion.py         # /ingest endpoint, queue management
│   ├── extraction.py        # LLM extraction pipeline, prompt management
│   ├── embeddings.py        # e5-small-v2 wrapper, batch encoding
│   ├── search.py            # semantic search, hybrid search
│   ├── database.py          # sqlite-vec connection, schema, CRUD
│   ├── dedup.py             # hash-based and semantic dedup logic
│   ├── parsers/
│   │   ├── chatgpt.py       # conversations.json tree parser
│   │   ├── gemini.py        # Takeout JSON parser
│   │   └── base.py          # shared Message, Conversation dataclasses
│   └── ui/
│       └── search.html      # minimal search UI served by daemon
├── cli/
│   ├── main.py              # osctx CLI entry point (Typer)
│   ├── install.py           # osctx install, osctx uninstall
│   └── doctor.py            # osctx doctor, osctx stats
├── mcp_server/
│   └── server.py            # MCP server (Phase 5)
├── extension/               # Chrome extension
│   ├── manifest.json
│   ├── background.ts
│   ├── content/
│   │   ├── chatgpt.ts
│   │   ├── claude.ts
│   │   └── gemini.ts
│   └── popup.html
├── raycast-extension/
│   ├── package.json
│   └── src/
│       └── search-memory.tsx
├── tests/
│   ├── test_parsers.py
│   ├── test_extraction.py
│   └── test_search.py
├── pyproject.toml
├── README.md
└── CHANGELOG.md
```

---

## First 10 Commands (Appendix B)

```bash
# 1. Clone and install
git clone https://github.com/you/osctx && cd osctx
pip install -e .

# 2. Configure your API key
osctx config --set anthropic_api_key=sk-ant-...

# 3. Import your ChatGPT history
osctx import ~/Downloads/chatgpt-export/conversations.json

# 4. Watch extraction
osctx status --watch

# 5. Search from terminal
osctx search "database schema"

# 6. Start daemon permanently
osctx install

# 7. Verify
osctx doctor

# 8. Load Chrome extension (from osctx/extension/dist/)
# 9. Import Raycast extension (from osctx/raycast-extension/)

# 10. Press Cmd+Shift+S in ChatGPT, then Cmd+Shift+M in Claude
```
