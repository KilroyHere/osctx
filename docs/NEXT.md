# OSCTX — Next Steps

Ordered by priority. Each task has enough detail to implement without reading other files.
Read INTERFACES.md and CONSTRAINTS.md before implementing any task.

**Current state:** Browser extension (Phase 1), Raycast extension (Phase 2), extraction tests (Phase 3), known bug fixes (Phase 4), and MCP server (Phase 5) are all complete.

---

## Task 1: Test `osctx install` on Real Login Cycle

**Why:** The launchd plist is written correctly but has never been tested through a real login cycle.

**Steps:**
1. `osctx install` (writes plist to `~/Library/LaunchAgents/`)
2. Log out and log back in
3. `curl http://localhost:8765/status` — should return stats
4. Fix whatever path resolution issue appears

**Likely failure point:** the uvicorn path in the plist may not resolve correctly when PATH is not set at login.

---

## Task 2: Hybrid Search FTS5 Table (Phase 6)

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

## Task 3: Respect `extraction_on_battery` Config Key

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

### Already passing
- [x] `pytest` passes (31 extraction + 15 parser + search tests, no real API calls)
- [x] Browser extension: ChatGPT, Claude.ai, Gemini capture verified live
- [x] Dedup: second capture of same conversation returns `{"status": "duplicate"}`
- [x] Raycast: search, detail view, single copy, multi-select copy all working
- [x] Queue survives daemon restart (disk persistence)
- [ ] MCP: Claude Desktop calls `search_knowledge` and returns results (needs live test)
- [ ] MCP: `save_insight` appears in subsequent searches (needs live test)
- [ ] MCP: `osctx mcp install` verified on a machine with Claude Desktop installed
