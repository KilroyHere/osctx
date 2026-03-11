# OSCTX — Next Steps

Ordered by priority. Each task has enough detail to implement without reading other files.
Read INTERFACES.md and CONSTRAINTS.md before implementing any task.

**Current state:** All planned phases complete — browser extension, Raycast, extraction, tests, MCP server, FTS5 hybrid search, battery deferral. Only remaining item is a real-machine launchd test.

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
