# OSCTX — Module Interface Contracts

This document defines the exact public interface of every module.
A new session implementing any single module only needs to read this file + CONSTRAINTS.md.
Never change a public interface without updating this document.

---

## Data Types — `osctx/daemon/parsers/base.py`

```python
@dataclass
class Message:
    role: Literal["user", "assistant"]   # raises ValueError if invalid
    content: str                          # raises ValueError if empty/whitespace
    timestamp: float | None = None        # unix timestamp

@dataclass
class Conversation:
    id: str              # source-specific ID (e.g. ChatGPT conv ID)
    source: str          # 'chatgpt' | 'claude' | 'gemini' | 'manual'
    title: str | None
    messages: list[Message]             # raises ValueError if empty
    url: str | None = None
    create_time: float | None = None    # unix timestamp

    # Properties
    first_message_content: str          # messages[0].content
    message_count: int                  # len(messages)

    # Methods
    def to_text(self) -> str            # "User: ...\n\nAssistant: ...\n\n..."
```

**No external dependencies. No config keys.**

---

## Parsers

### `osctx/daemon/parsers/chatgpt.py`

```python
def parse_chatgpt_export(path: str) -> list[Conversation]
```
- Input: absolute path to `conversations.json` from ChatGPT data export
- Output: list of `Conversation` objects (skips conversations with no usable messages)
- On bad individual conversation: logs warning, continues (never aborts)
- On bad file: raises `ValueError` or `json.JSONDecodeError`
- Skips: system messages, tool messages, null messages, non-text content
- Branching: always follows last child (most recent branch)

```python
def parse_conversation(raw: dict[str, Any]) -> Conversation | None
```
- Input: single conversation dict from `conversations.json`
- Output: `Conversation` or `None` if no usable messages

### `osctx/daemon/parsers/gemini.py`

```python
def parse_gemini_export(path: str) -> list[Conversation]
```
- Input: absolute path to `Gemini Apps Activity.json` from Google Takeout
- Output: list of `Conversation` objects
- Handles both `gmr:`-prefixed and plain key variants

---

## Database — `osctx/daemon/database.py`

### Constants
```python
OSCTX_DIR: Path   # Path.home() / ".osctx"
DB_PATH: Path     # OSCTX_DIR / "memory.db"
```

### Connection
```python
def init_db(db_path: Path = DB_PATH) -> None
# Creates all tables. Safe to call multiple times.

@contextmanager
def get_conn(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]
# Commits on exit, rolls back on exception.
# conn.row_factory = sqlite3.Row (access by column name)
```

### Conversations
```python
def conversation_id_for(url: str | None, first_message: str) -> str
# Returns SHA256(f"{url or ''}::{first_message[:200]}")

def upsert_conversation(
    conn, *, conv_id, source, url, title, captured_at: int, messages: list[dict], status="pending"
) -> bool
# Returns True if inserted, False if already exists

def set_conversation_status(conn, conv_id: str, status: str) -> None
# status: 'pending' | 'processing' | 'done' | 'failed'

def update_conversation_summary(conn, conv_id: str, summary: str) -> None
# Stores the LLM-generated summary for a conversation (UPDATE conversations SET summary=?)

def get_pending_conversations(conn, limit: int = 50) -> list[sqlite3.Row]
# Returns conversations WHERE status='pending' ORDER BY captured_at ASC
```

### Conversation State (delta dedup)
```python
def url_hash(url: str) -> str
# SHA256 of normalized URL (strips query params + fragments)

def get_conversation_state(conn, u_hash: str) -> sqlite3.Row | None
# Row fields: url_hash, last_msg_count, last_captured, conversation_id

def upsert_conversation_state(
    conn, *, u_hash, msg_count: int, captured_at: int, conv_id: str | None = None
) -> None
```

### Knowledge Units
```python
def insert_knowledge_unit(
    conn, *,
    conversation_id: str | None,
    content: str,
    category: str,         # 'decision'|'fact'|'solution'|'code_pattern'|'preference'|'reference'
    topic_tags: list[str],
    source: str,
    source_date: int | None,  # unix timestamp
    confidence: float,
    context: str | None = None,
    similar_to_id: str | None = None,
) -> str   # returns UUID

def insert_embedding(conn, unit_id: str, embedding: list[float]) -> None
# embedding must be exactly 384 floats

def content_hash_exists(conn, content: str) -> str | None
# Returns unit_id if SHA256(content) already stored, else None

def record_content_hash(conn, content: str, unit_id: str) -> None
```

### Stats
```python
def get_stats(conn) -> dict[str, Any]
# Returns:
# {
#   "knowledge_units": int,
#   "conversations": int,
#   "pending_extraction": int,
#   "by_source": {"chatgpt": int, ...},
#   "by_category": {"fact": int, ...},
# }
```

---

## Embeddings — `osctx/daemon/embeddings.py`

```python
MODEL_NAME = "intfloat/e5-small-v2"
EMBEDDING_DIM = 384

def encode_query(text: str) -> list[float]
# Prepends "query: ", returns 384-dim normalized vector

def encode_passage(text: str) -> list[float]
# Prepends "passage: ", returns 384-dim normalized vector

def encode_batch(texts: list[str], *, is_query: bool = False) -> list[list[float]]
# Batch encode. is_query=True → "query: " prefix, False → "passage: " prefix
# Returns list of 384-dim normalized vectors, same order as input
# Returns [] for empty input
```

**Lazy loading:** model is not downloaded/loaded until first encode call.
**No config keys. No internal osctx imports.**

---

## Deduplication — `osctx/daemon/dedup.py`

### Level 1 — Conversation delta
```python
@dataclass
class DeltaResult:
    should_process: bool
    delta_messages: list[dict]    # new messages only (messages[last_count:])
    all_messages: list[dict]      # full message list
    is_first_capture: bool

def check_conversation_delta(conn, raw_url: str, messages: list[dict]) -> DeltaResult
# If url never seen → DeltaResult(should_process=True, delta=all, is_first=True)
# If len(messages) <= last_msg_count → DeltaResult(should_process=False)
# If len(messages) > last_msg_count → DeltaResult(should_process=True, delta=new only)

def update_conversation_state(conn, raw_url: str, msg_count: int, conv_id: str | None = None) -> None
# Call after successful processing to update the stored count
```

### Level 2 — Knowledge unit similarity
```python
class DedupDecision(NamedTuple):
    action: str         # 'skip' | 'store_linked' | 'store'
    similar_to_id: str | None   # unit_id when action='store_linked', else None

THRESHOLD_HARD = 0.97   # cosine similarity → skip
THRESHOLD_SOFT = 0.90   # cosine similarity → store with similar_to_id link

def check_unit_dedup(
    conn,
    content: str,
    embedding: list[float],
    hard_threshold: float = THRESHOLD_HARD,
    soft_threshold: float = THRESHOLD_SOFT,
) -> DedupDecision
# First checks exact content hash (O(1)), then nearest-neighbor similarity.
# Similarity formula: 1.0 - (vec_distance_cosine / 2.0)

def finalize_unit_storage(conn, content: str, unit_id: str) -> None
# Records content hash. Call after insert_knowledge_unit succeeds.
```

---

## Extraction — `osctx/daemon/extraction.py`

```python
@dataclass
class ExtractedUnit:
    content: str       # 1-3 sentences; includes reasoning (why/because/tradeoffs) for decisions/solutions; self-contained without original conversation
    category: str      # 'decision'|'fact'|'solution'|'code_pattern'|'preference'|'reference'
    topic_tags: list[str]   # 1-4 tags
    confidence: float       # 0.0-1.0
    context: str            # one sentence explaining why this is worth keeping

async def extract_from_messages(
    messages: list[dict],   # [{"role": "user"|"assistant", "content": str}, ...]
    config: dict[str, Any] | None = None,
) -> list[ExtractedUnit]
# Chunks messages internally (6000 token hard cap, 4 chars/token estimate)
# Uses rolling summary between chunks (NOT raw overlap)
# Filters confidence < 0.7
# Deduplicates by exact content within call
# Returns [] if nothing worth extracting

async def summarize_conversation(
    messages: list[dict],
    config: dict[str, Any] | None = None,
) -> str
# Generates a rich 2-3 paragraph summary of an entire conversation.
# Captures: (1) core topic, (2) key conclusions/decisions, (3) nuances/caveats.
# Truncates to 12,000 chars if needed (keeps first 4k + last 8k).
# Never raises — returns "" on failure.
# Uses same backend as extract_from_messages.
```

### Config keys read by extract_from_messages:
```
extraction_backend   "anthropic" | "openai" | "gemini" | "ollama"
anthropic_api_key    used when backend="anthropic"
openai_api_key       used when backend="openai"
gemini_api_key       used when backend="gemini"
gemini_model         default: "gemini-flash-latest"
ollama_model         default: "llama3.2:3b"
ollama_base_url      default: "http://localhost:11434"
```

### Backend structured output approach:
- `anthropic`: `tool_use` mode → guaranteed JSON
- `openai`: `tool_calls` → guaranteed JSON
- `gemini`: `response_schema` + `response_mime_type="application/json"` → guaranteed JSON
- `ollama`: `format=json` hint + regex strip + parse → best-effort

---

## Search — `osctx/daemon/search.py`

```python
@dataclass
class SearchResult:
    id: str
    content: str
    category: str | None
    topic_tags: list[str]
    source: str
    source_date: str | None      # "YYYY-MM-DD" for display, or None
    source_url: str | None
    confidence: float | None
    similarity_score: float      # 0.0-1.0 (higher = more similar)
    context: str | None
    conversation_id: str | None
    conversation_summary: str | None = None  # Full LLM-generated conversation summary

    def to_paste(self) -> str
    # Returns XML context block for pasting into AI chat.
    # If conversation_summary present:
    #   <context source="Chatgpt" date="2025-11-03" topic="database, postgresql">
    #   ## Conversation Summary
    #   [summary]
    #
    #   ## Matched Knowledge
    #   [content]
    #   [context if present]
    #   </context>
    # Without summary: same but without the Summary/Matched Knowledge headers.

    def to_dict(self) -> dict[str, Any]
    # All fields as JSON-serializable dict. similarity_score rounded to 4 decimals.
    # Includes conversation_summary key.

def search(
    query: str,
    limit: int = 5,
    score_threshold: float = 0.5,
    db_path: Path = DB_PATH,
) -> list[SearchResult]
# Pure semantic search. Results sorted by similarity descending.
# Filters out results below score_threshold.
# Excludes soft-duplicate units (WHERE similar_to_id IS NULL) — only canonical units shown.
# Joins conversations table to populate source_url and conversation_summary.

def search_hybrid(
    query: str,
    limit: int = 5,
    db_path: Path = DB_PATH,
) -> list[SearchResult]
# BM25 + semantic, merged via RRF. Falls back to pure semantic if FTS5 unavailable.
```

---

## Ingestion — `osctx/daemon/ingestion.py`

### Pydantic request models
```python
class MessageIn(BaseModel):
    role: str      # validated: must be "user" or "assistant"
    content: str

class IngestRequest(BaseModel):
    source: str                  # "chatgpt" | "claude" | "gemini"
    url: str | None = None
    captured_at: int | None = None   # unix timestamp; defaults to now()
    messages: list[MessageIn]
    title: str | None = None

class BulkIngestRequest(BaseModel):
    file_path: str    # absolute path to export file
    source: str = "chatgpt"
```

### Public functions
```python
def enqueue_ingest(req: IngestRequest, db_path: Path = DB_PATH) -> dict[str, Any]
# Synchronous. Validates, dedup-checks, and enqueues.
# Returns immediately (processing is async in background worker).
# Return shapes:
#   {"status": "queued", "conversation_id": str, "new_messages": int, "queue_depth": int}
#   {"status": "duplicate", "reason": "no new messages since last capture"}
#   {"status": "ignored", "reason": "no messages"}

def enqueue_bulk(file_path: str, source: str, db_path: Path = DB_PATH) -> dict[str, Any]
# Returns:
#   {"status": "queued", "conversations_queued": int, "queue_depth": int}
#   {"status": "error", "reason": str}

async def background_worker(config: dict[str, Any]) -> None
# Long-running asyncio task. Started in FastAPI lifespan.
# Processes _queue (asyncio.Queue) items.
# On CancelledError: saves queue to disk and returns.
# On startup: requeues conversations stuck in 'processing' state from prior crash.
```

### Queue persistence
```python
QUEUE_FILE = Path.home() / ".osctx" / "queue.json"

def _load_queue_from_disk() -> None   # called at daemon startup
def _save_queue_to_disk() -> None     # called at daemon shutdown
```

---

## FastAPI Daemon — `osctx/daemon/main.py`

### Endpoints
```
POST   /ingest                          Body: IngestRequest          → 202 queued, 200 duplicate/ignored
POST   /ingest/bulk                     Body: BulkIngestRequest      → 202 queued, 400 error
GET    /search?q=&limit=5&hybrid=       Query params                 → {"results": [...], "query": str}
GET    /search/hybrid?q=&limit=5        Query params                 → {"results": [...], "query": str}
GET    /status                                                        → stats dict (get_stats + queue_depth)
GET    /units?category=&source=&limit=  Query params (all optional)  → {"units": [...], "total": int}
DELETE /units/{unit_id}                 Path param                   → {"deleted": unit_id} | 404
GET    /ui                                                            → HTML (search.html)
```

### Config loaded from `~/.osctx/config.json` at startup
Full schema in DECISIONS.md. Stored as `app.state.config`.

### Run command
```bash
uvicorn osctx.daemon.main:app --host 127.0.0.1 --port 8765
```

---

## MCP Server — `osctx/mcp_server/server.py`

Three tools exposed to Claude Desktop via the MCP protocol.

```python
# Run: python -m osctx.mcp_server.server
# Install: osctx mcp install

async def search_knowledge(query: str, limit: int = 5) -> list[dict]
# Semantic search over knowledge units.
# Returns list of SearchResult.to_dict() — all fields, similarity_score rounded to 4 decimals.

async def get_by_topic(topic: str) -> list[dict]
# Exact tag match: WHERE topic_tags LIKE '%"topic"%'
# Returns all matching knowledge_units rows as dicts, ordered by created_at DESC.

async def save_insight(content: str, topic: str) -> str
# Deduplicates first (content_hash_exists). If duplicate: returns "Already stored."
# Otherwise: encode_passage() → insert_knowledge_unit(category="fact", source="claude_desktop",
#   confidence=1.0) → insert_embedding() → record_content_hash()
# Returns "Saved <unit_id>"
```

### `osctx/cli/mcp_install.py`

```python
CLAUDE_CONFIG: Path   # ~/Library/Application Support/Claude/claude_desktop_config.json

def install() -> None
# Reads existing config (or starts empty), sets mcpServers["osctx"] entry,
# writes back. Points to sys.executable + ["-m", "osctx.mcp_server.server"].

def uninstall() -> None
# Removes mcpServers["osctx"] from config. No-op if not present.
```

### CLI commands added

```
osctx mcp install    → register in Claude Desktop config
osctx mcp uninstall  → remove from Claude Desktop config
```

---

## CLI — `osctx/cli/main.py`

```
osctx import <file> [--source chatgpt|gemini]
osctx search <query> [--limit N]
osctx status [--watch]
osctx config --set key=value | --get key | --show
osctx install
osctx uninstall
osctx doctor
```

All commands fall back to direct DB access if daemon is not running (except install/uninstall/doctor).

---

## Data Flow Summary

```
Browser extension POST
        │
        ▼
POST /ingest
        │
        ▼
enqueue_ingest()
  └─ check_conversation_delta()     # Level 1 dedup — skip if no new messages
  └─ push to asyncio.Queue
        │
        ▼ (background worker)
_process_item()
  └─ upsert_conversation()          # store raw messages
  └─ extract_from_messages()        # LLM → list[ExtractedUnit] (delta only)
  └─ summarize_conversation()       # LLM → full conversation summary (2-3 paragraphs)
  └─ update_conversation_summary()  # store summary in conversations.summary
  └─ encode_batch()                 # embed all units at once
  └─ for each unit:
       └─ check_unit_dedup()        # Level 2 dedup
       └─ insert_knowledge_unit()   # store unit
       └─ insert_embedding()        # store vector
       └─ finalize_unit_storage()   # record content hash
  └─ set_conversation_status(done)
  └─ update_conversation_state()    # update last_msg_count

GET /search?q=...
        │
        ▼
search()
  └─ encode_query()                 # embed query with "query: " prefix
  └─ vec_distance_cosine SQL join   # nearest neighbors
  └─ filter by score_threshold
  └─ return list[SearchResult]
```
