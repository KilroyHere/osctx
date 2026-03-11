# OSCTX — Next Steps

Ordered by priority. Each task has enough detail to implement without reading other files.
Read INTERFACES.md and CONSTRAINTS.md before implementing any task.

**Current state:** Core pipeline complete (capture → extract → embed → search). Daemon, browser extension, Raycast, MCP server, web UI all working. Roadmap below covers quality and growth items.

---

## Task 1: Daemon auto-start from CLI

**Why:** If `osctx search` or `osctx status` silently fail when the daemon isn't running, users get confused. Auto-starting removes a setup step.

**Steps:**
1. In `osctx/cli/main.py`, add a helper `ensure_daemon_running()` that checks `GET /status` and if it fails, spawns `uvicorn osctx.daemon.main:app --host 127.0.0.1 --port 8765` via `subprocess.Popen` with stdout/stderr to a log file.
2. Call it at the top of `search`, `status`, and `import` commands.
3. Wait up to 3s for the port to become available before continuing.

---

## Task 2: Claude.ai Conversation Export Importer

**Why:** The irony of a Claude-centric tool with no Claude export importer is notable.

**Steps:**
1. Download a Claude conversation export from claude.ai/settings (JSON format).
2. Inspect the shape — likely `[{id, name, created_at, messages: [{role, content}]}]`.
3. Add `osctx/daemon/parsers/claude_export.py` following the same pattern as `chatgpt.py`.
4. Add `--source claude` option to `osctx import`.
5. Test with a real export file.

---

## Task 3: Popup live extraction feedback

**Why:** After clicking Capture in the extension popup, users see "Saved ✓" but don't know how many units were extracted. The notification (already added to utils.ts) shows after 45s, but the popup could also update.

**Steps:**
1. After capture, the popup currently closes. Instead, keep it open briefly and show unit count delta.
2. Poll `GET /status` every 3s for up to 60s. When `knowledge_units` increases, show "+ N units extracted".
3. Fall back to "Extraction queued" if nothing changes after 60s.

---

## Task 4: `osctx install` real launchd test

**Why:** The launchd plist is written correctly but has never been tested through a real login cycle.

**Steps:**
1. `osctx install` (writes plist to `~/Library/LaunchAgents/`)
2. Log out and log back in
3. `curl http://localhost:8765/status` — should return stats
4. Fix whatever path resolution issue appears

**Likely failure point:** the uvicorn path in the plist may not resolve correctly when PATH is not set at login.

---

## Task 5: XML / Markdown / Plain format toggle in UI and Raycast

**Why:** Some users want plain text context blocks, not XML.

**Steps:**
1. Add `to_markdown()` and `to_plain()` methods to `SearchResult` alongside `to_paste()`.
2. In Raycast, add a "Copy as Markdown" action (Cmd+Shift+C) next to the existing XML action.
3. In the web UI, add a format toggle button (XML / MD / Plain) in the multi-select bar.

---

## Task 6: Conversation-centric browse view

**Why:** You can browse by category but can't see "all units from last Tuesday's chat about indexing."

**Steps:**
1. Add `GET /conversations` endpoint returning all rows from `conversations` table with unit counts.
2. Add a "Conversations" tab in the web UI listing conversations by date with a unit count badge.
3. Clicking a conversation shows all its extracted units.

---

## Task 7: Export command

**Why:** No way to get your knowledge base out in a portable format.

**Steps:**
1. Add `osctx export [--format json|csv|markdown] [--output FILE]` to the CLI.
2. JSON: array of knowledge_unit rows with topic_tags parsed.
3. Markdown: one section per category, each unit as a bullet.

---

## Task 8: Perplexity importer

**Why:** Perplexity is widely used for research and would be high-value.

**Steps:**
1. Find Perplexity export format (likely from browser history or manual copy-paste).
2. Add `osctx/daemon/parsers/perplexity.py`.
3. Support `osctx import --source perplexity`.

---

## Testing Checklist

### Already passing
- [x] `pytest` passes (31 extraction + 15 parser + search tests, no real API calls)
- [x] Browser extension: ChatGPT, Claude.ai, Gemini capture verified live
- [x] Post-capture notification fires 45s after capture with unit delta
- [x] Delete button in web UI removes unit immediately
- [x] Recent tab shows last 50 units sorted by created_at
- [x] Dedup: second capture of same conversation returns `{"status": "duplicate"}`
- [x] Raycast: search, detail view, single copy, multi-select copy all working
- [x] Queue survives daemon restart (disk persistence)
- [ ] MCP: Claude Desktop calls `search_knowledge` and returns results (needs live test)
- [ ] MCP: `save_insight` deduplicates near-identical insights (needs live test)
- [ ] `osctx install` verified on a fresh login cycle
