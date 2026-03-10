# OSCTX — Development Constraints

Hard rules. Every line of code must follow these.
When in doubt, check here before implementing.

---

## The Absolute Rules

### 1. Never change a public interface without updating INTERFACES.md
If a function signature, endpoint, or return shape changes, INTERFACES.md must be updated in the same commit.

### 2. Never add a dependency not in DECISIONS.md
Before using any new library, add it to DECISIONS.md and pyproject.toml.
Approved core deps: fastapi, uvicorn, anthropic, sentence-transformers, sqlite-vec, typer, httpx, pydantic, google-genai.
Banned: chromadb, qdrant, langchain, celery, redis, SQLAlchemy, tiktoken, pandas.

### 3. Never use class-based CSS selectors in the browser extension
Only `data-*`, `aria-*`, `role`, and semantic attributes. Class names with random hashes break on every deploy.

### 4. Never block the ingest endpoint
`POST /ingest` must return within 50ms. All processing is async in `background_worker`.
The endpoint validates, dedup-checks, and enqueues only.

### 5. Never import extraction or embeddings at module top level
Both load large models and make network calls. They must import lazily (inside functions).
`embeddings.py` loads e5-small-v2 on first encode call. Keep it that way.

### 6. Never write raw class names in DOM selectors (extension)
❌ `document.querySelectorAll('.prose')` — breaks on deploy
✅ `document.querySelectorAll('[data-message-author-role]')` — survives DOM changes

### 7. Never store plaintext API keys anywhere except `~/.osctx/config.json`
config.json has `chmod 600` set by the CLI. Never log API keys. Never include them in error messages.

---

## Architecture Constraints

### Single database file
Everything in `~/.osctx/memory.db`. No second database process. No ChromaDB. No Redis.
The sqlite-vec extension provides vector search within the same file.
Backup = `cp ~/.osctx/memory.db ~/.osctx/backups/memory.db.bak`.

### Async boundary
The daemon is async (FastAPI + uvicorn). The database layer is synchronous sqlite3.
Do NOT make database functions async. Use `get_conn()` context manager, which is synchronous.
The background worker calls synchronous DB functions from inside async context — this is fine for sqlite3.

### Config always flows down
Config is loaded once at daemon startup into `app.state.config`.
Passed explicitly to `background_worker(config)` → `_process_item(item, config)` → `extract_from_messages(messages, config)`.
Never read config.json inside individual modules. Never use global config state in modules below `main.py`.
Exception: `cli/main.py` reads config directly (no daemon running in CLI context).

### Queue item schema
Items pushed to `asyncio.Queue` and persisted to `queue.json` must match exactly:
```python
{
    "conv_id": str,
    "source": str,
    "url": str | None,
    "title": str | None,
    "captured_at": int,
    "messages": list[dict],        # full message list
    "delta_messages": list[dict],  # new messages only
    "db_path": str,                # str(Path) — must be resolvable after restart
}
```

### Embedding dimensions
Always 384. Model is `intfloat/e5-small-v2`. Never change without migrating the database.
Vector table schema: `embedding float[384]`.

### Cosine distance → similarity conversion
sqlite-vec's `vec_distance_cosine` returns distance (0=identical, 2=opposite).
Always convert: `similarity = 1.0 - (distance / 2.0)`.
Never use raw distance as similarity.

---

## What Each Module Is Allowed To Import

| Module | May import from osctx | May NOT import |
|---|---|---|
| `parsers/base.py` | Nothing | Anything |
| `parsers/chatgpt.py` | `parsers/base.py` | database, embeddings, extraction |
| `parsers/gemini.py` | `parsers/base.py` | database, embeddings, extraction |
| `database.py` | Nothing | parsers, embeddings, extraction |
| `embeddings.py` | Nothing | database, parsers, extraction |
| `dedup.py` | `database.py` | parsers, embeddings, extraction |
| `extraction.py` | Nothing | database, parsers, embeddings, dedup |
| `search.py` | `database.py`, `embeddings.py` | parsers, extraction, dedup |
| `ingestion.py` | `database.py`, `dedup.py`, `embeddings.py`, `extraction.py`, `parsers/*` | search |
| `main.py` | `database.py`, `ingestion.py`, `search.py` | parsers (indirectly via ingestion) |
| `cli/main.py` | `database.py`, `search.py`, `parsers/*` (fallback only) | extraction, embeddings |

---

## Testing Constraints

### Tests must not hit real APIs
All LLM extraction tests must mock the backend calls.
Use `pytest-mock` or monkeypatching.

### Tests must not use `~/.osctx/memory.db`
All tests that need a database must create a temp file:
```python
import tempfile
f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
db_path = Path(f.name)
init_db(db_path)
```

### Parser tests must cover all edge cases from DATA_SAMPLE.md
- null message nodes
- multimodal parts (dicts in parts[])
- tool role messages
- branching (multiple children → follow last)
- system messages

### The embedding model is NOT mocked in tests
`tests/test_search.py` uses `_fake_embedding()` (deterministic MD5-based unit vectors)
to avoid downloading the model in CI. This tests the DB/query plumbing, not retrieval quality.
Real retrieval quality is tested manually with the running daemon.

---

## Browser Extension Constraints

### Manifest V3
No background pages. Use service workers.
`chrome.storage.local` for offline payload buffering.

### Capture strategy (ordered by priority)
1. `beforeunload` → POST immediately
2. 5-minute inactivity timer (MutationObserver reset on new message) → POST if delta
3. `Cmd+Shift+S` manual override

### Payload must match IngestRequest schema exactly
```typescript
interface Payload {
  source: "chatgpt" | "claude" | "gemini";
  url: string;
  captured_at: number;   // Math.floor(Date.now() / 1000)
  messages: Array<{ role: "user" | "assistant"; content: string }>;
  title?: string;
}
```

### Content scripts are platform-specific files
- `content/chatgpt.ts` — only runs on `chat.openai.com`
- `content/claude.ts` — only runs on `claude.ai`
- `content/gemini.ts` — only runs on `gemini.google.com`
- `content/utils.ts` — shared POST logic and inactivity timer

### Offline handling
If `fetch` to `localhost:8765/ingest` fails (connection refused):
- Store payload in `chrome.storage.local` under key `"osctx_pending"`
- Service worker retries every 60 seconds

---

## CLI Constraints

### Always fall back to direct DB if daemon is down
`osctx import` and `osctx search` must work without the daemon running.
Fall back to direct database access (no extraction in fallback — import stores as 'pending').

### Config file permissions
`config.json` must always be written with `chmod 0o600`.
Do this in `_save_config()`. Never relax this.

### `osctx doctor` must not fix problems
It reports only. Never auto-install, never auto-modify. Tell the user what command to run.

---

## Anti-Patterns

❌ `conn.execute(f"SELECT ... WHERE id = '{user_input}'")` — SQL injection
✅ `conn.execute("SELECT ... WHERE id = ?", (user_input,))`

❌ `model = SentenceTransformer(MODEL_NAME)` at module top level
✅ Load inside function on first call (lazy loading pattern in embeddings.py)

❌ `import osctx.daemon.extraction` from `search.py`
✅ Keep the import graph acyclic. search.py never imports extraction.py.

❌ Changing `IngestRequest` fields without updating the browser extension payload format
✅ IngestRequest and the extension payload are a contract. Change both or neither.

❌ Using `asyncio.sleep()` in tests to wait for extraction
✅ Test extraction synchronously by calling `extract_from_messages()` directly with a mocked backend.

❌ `git commit -m "wip"` or `git commit --amend` on pushed commits
✅ Descriptive commit messages. New commits only.
