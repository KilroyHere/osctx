# OSCTX — Next Steps

Ordered by priority. Each task has enough detail to implement without reading other files.
Read INTERFACES.md and CONSTRAINTS.md before implementing any task.

**Current state:** Browser extension (Phase 1), Raycast extension (Phase 2), extraction tests (Phase 3), and known bug fixes (Phase 4) are all complete. Next up is the MCP server.

---

## Task 1: MCP Server (Phase 5 — HIGH PRIORITY)

**Why first:** Enables Claude Desktop to query your memory mid-conversation without leaving the chat. This is the primary use case for the tool.

**File to create:** `osctx/mcp_server/server.py`

```python
from mcp.server.fastmcp import FastMCP
from osctx.daemon.search import search, SearchResult
from osctx.daemon.database import get_conn, insert_knowledge_unit, record_content_hash
from osctx.daemon.embeddings import encode_passage

mcp = FastMCP("osctx")

@mcp.tool()
async def search_knowledge(query: str, limit: int = 5) -> list[dict]:
    """Search your personal knowledge base built from AI conversations."""
    results = search(query, limit=limit)
    return [r.to_dict() for r in results]

@mcp.tool()
async def get_by_topic(topic: str) -> list[dict]:
    """Retrieve all knowledge units tagged with a specific topic."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_units WHERE topic_tags LIKE ?",
            (f'%"{topic}"%',)
        ).fetchall()
    return [dict(r) for r in rows]

@mcp.tool()
async def save_insight(content: str, topic: str) -> str:
    """Save a new insight directly from this conversation."""
    import time
    embedding = encode_passage(content)
    with get_conn() as conn:
        unit_id = insert_knowledge_unit(
            conn, conversation_id=None, content=content,
            category="fact", topic_tags=[topic], source="claude_desktop",
            source_date=int(time.time()), confidence=1.0,
        )
        from osctx.daemon.database import insert_embedding
        insert_embedding(conn, unit_id, embedding)
        record_content_hash(conn, content, unit_id)
    return f"Saved insight {unit_id}"

if __name__ == "__main__":
    mcp.run()
```

**Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):**
```json
{
  "mcpServers": {
    "osctx": {
      "command": "/path/to/osctx/.venv/bin/python",
      "args": ["-m", "osctx.mcp_server.server"],
      "env": {}
    }
  }
}
```

**CLI command to add:**
```
osctx mcp install   → writes claude_desktop_config.json entry
```

**Validation test:**
1. Add server to Claude Desktop config
2. Start a new Claude Desktop conversation
3. Ask Claude to "search my memory for [topic]" — it should call `search_knowledge`
4. Ask Claude to "save this insight: [text]" — it should call `save_insight`
5. Verify the saved insight appears in subsequent searches

---

## Task 2: Test `osctx install` on Real Login Cycle

**Why:** The launchd plist is written correctly but has never been tested through a real login cycle.

**Steps:**
1. `osctx install` (writes plist to `~/Library/LaunchAgents/`)
2. Log out and log back in
3. `curl http://localhost:8765/status` — should return stats
4. Fix whatever path resolution issue appears

**Likely failure point:** the uvicorn path in the plist may not resolve correctly when PATH is not set at login.

---

## Task 3: Hybrid Search FTS5 Table (Phase 6)

**Why:** `search_hybrid()` exists but always falls back to pure semantic because the FTS5 table is never created.

**File:** `osctx/daemon/database.py`

Add to `init_db()`:
```python
conn.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
    USING fts5(content, topic_tags, tokenize='porter ascii')
""")
# Populate from existing rows
conn.execute("""
    INSERT INTO knowledge_fts(rowid, content, topic_tags)
    SELECT rowid, content, topic_tags FROM knowledge_units
    ON CONFLICT DO NOTHING
""")
```

Add trigger to keep FTS in sync:
```python
conn.execute("""
    CREATE TRIGGER IF NOT EXISTS knowledge_fts_insert
    AFTER INSERT ON knowledge_units BEGIN
        INSERT INTO knowledge_fts(rowid, content, topic_tags)
        VALUES (new.rowid, new.content, new.topic_tags);
    END
""")
```

Also write a migration for existing DBs (FTS not created on first `init_db`).

---

## Task 4: Respect `extraction_on_battery` Config Key

**Why:** Config key is defined and documented but never read.

**File:** `osctx/daemon/ingestion.py` — in `_process_item()`, before calling `extract_from_messages()`:

```python
if not config.get("extraction_on_battery", False):
    import subprocess
    result = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True)
    if "Battery Power" in result.stdout:
        set_conversation_status(conn, conv_id, "pending")
        logger.info("On battery, deferring extraction for %s", conv_id)
        return
```

---

## Testing Checklist

### Phase 5 (MCP) — not yet done
- [ ] Claude Desktop can call `search_knowledge` and get results
- [ ] `save_insight` appears in subsequent searches
- [ ] `osctx mcp install` writes correct config

### Already passing
- [x] `pytest` passes (31 extraction + 15 parser + search tests, no real API calls)
- [x] Browser extension: ChatGPT, Claude.ai, Gemini capture verified live
- [x] Dedup: second capture of same conversation returns `{"status": "duplicate"}`
- [x] Raycast: search, detail view, single copy, multi-select copy all working
- [x] Queue survives daemon restart (disk persistence)
