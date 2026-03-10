# OSCTX — Next Steps

Ordered by priority. Each task has enough detail to implement without reading other files.
Read INTERFACES.md and CONSTRAINTS.md before implementing any task.

---

## Task 1: Browser Extension (Phase 1 — HIGH PRIORITY)

**Why first:** Without this, every capture requires a manual curl. This closes the capture loop.

**Files to create:**
```
extension/
├── manifest.json
├── background.ts        (service worker)
├── popup.html
├── popup.ts
└── content/
    ├── utils.ts         (shared POST + inactivity timer)
    ├── chatgpt.ts
    ├── claude.ts
    └── gemini.ts
```

**manifest.json:**
```json
{
  "manifest_version": 3,
  "name": "OSCTX",
  "version": "0.1.0",
  "permissions": ["activeTab", "storage"],
  "host_permissions": [
    "https://chat.openai.com/*",
    "https://claude.ai/*",
    "https://gemini.google.com/*"
  ],
  "background": { "service_worker": "background.js" },
  "content_scripts": [
    { "matches": ["https://chat.openai.com/*"], "js": ["content/chatgpt.js"] },
    { "matches": ["https://claude.ai/*"], "js": ["content/claude.js"] },
    { "matches": ["https://gemini.google.com/*"], "js": ["content/gemini.js"] }
  ],
  "action": { "default_popup": "popup.html" },
  "commands": {
    "save-chat": { "suggested_key": {"mac": "Command+Shift+S"}, "description": "Save current chat" },
    "retrieve": { "suggested_key": {"mac": "Command+Shift+M"}, "description": "Open search" }
  }
}
```

**utils.ts — shared logic:**
```typescript
const DAEMON_URL = "http://localhost:8765";

export interface Message { role: "user" | "assistant"; content: string; }

export interface IngestPayload {
  source: "chatgpt" | "claude" | "gemini";
  url: string;
  captured_at: number;
  messages: Message[];
  title?: string;
}

export async function postToOsctx(payload: IngestPayload): Promise<void> {
  try {
    await fetch(`${DAEMON_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // Daemon not running — buffer for retry
    const stored = await chrome.storage.local.get("osctx_pending");
    const pending: IngestPayload[] = stored["osctx_pending"] || [];
    pending.push(payload);
    await chrome.storage.local.set({ "osctx_pending": pending });
  }
}

export function startInactivityTimer(
  onInactive: () => void,
  timeoutMs: number = 5 * 60 * 1000
): MutationObserver {
  let timer: ReturnType<typeof setTimeout>;
  const reset = () => { clearTimeout(timer); timer = setTimeout(onInactive, timeoutMs); };
  reset();
  const observer = new MutationObserver(reset);
  observer.observe(document.body, { childList: true, subtree: true });
  return observer;
}
```

**chatgpt.ts — selector strategy:**
```typescript
// Primary: attribute-based (survives class renames)
// NEVER use class-based selectors

function extractMessages(): Message[] {
  const turns = document.querySelectorAll('[data-message-author-role]');
  if (turns.length === 0) return [];
  return Array.from(turns)
    .map(el => ({
      role: el.getAttribute('data-message-author-role') as "user" | "assistant",
      content: el.querySelector('[data-message-text-content]')?.textContent?.trim()
        ?? el.textContent?.trim() ?? "",
    }))
    .filter(m => m.content.length > 0 && (m.role === "user" || m.role === "assistant"));
}
```

**claude.ts — selector strategy:**
```typescript
function extractMessages(): Message[] {
  // Primary
  let turns = document.querySelectorAll('[data-testid*="message"]');
  // Fallback
  if (turns.length === 0) turns = document.querySelectorAll('div.font-claude-message, [data-testid="user-message"]');
  // ...
}
```

**background.ts — service worker:**
```typescript
// Retry buffered payloads every 60s
setInterval(async () => {
  const stored = await chrome.storage.local.get("osctx_pending");
  const pending = stored["osctx_pending"] || [];
  if (!pending.length) return;
  const failed = [];
  for (const payload of pending) {
    try {
      await fetch("http://localhost:8765/ingest", { method: "POST", ... });
    } catch { failed.push(payload); }
  }
  await chrome.storage.local.set({ "osctx_pending": failed });
}, 60_000);

// Cmd+Shift+M → open search UI
chrome.commands.onCommand.addListener((command) => {
  if (command === "retrieve") {
    chrome.tabs.create({ url: "http://localhost:8765/ui" });
  }
});
```

**Build setup:**
```json
// package.json
{
  "scripts": { "build": "tsc && cp manifest.json dist/" },
  "devDependencies": { "typescript": "^5.0.0", "@types/chrome": "^0.0.260" }
}
```

**Load in Chrome:** Settings → Extensions → Load unpacked → select `extension/dist/`

**Validation test:**
1. Open ChatGPT, have a 3-message conversation
2. Press Cmd+Shift+S (or wait 5 minutes)
3. `curl http://localhost:8765/status` → `knowledge_units` increases
4. Repeat with claude.ai

---

## Task 2: Raycast Extension (Phase 2)

**Why next:** Closes the retrieval loop. Without it, search requires opening a browser tab.

**File:** `raycast-extension/src/search-memory.tsx`

```typescript
import { Action, ActionPanel, List, showHUD, Clipboard } from "@raycast/api";
import { useState, useEffect } from "react";
import fetch from "node-fetch";

interface Result {
  id: string; content: string; category: string;
  topic_tags: string[]; source: string; source_date: string;
  similarity_score: number; context: string;
}

export default function SearchMemory() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Result[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!query) { setResults([]); return; }
    const timer = setTimeout(async () => {
      setIsLoading(true);
      try {
        const resp = await fetch(`http://localhost:8765/search?q=${encodeURIComponent(query)}&limit=5`);
        const data = await resp.json() as { results: Result[] };
        setResults(data.results);
      } catch { setResults([]); }
      setIsLoading(false);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  return (
    <List isLoading={isLoading} onSearchTextChange={setQuery} searchBarPlaceholder="Search memory…" throttle>
      {results.map(r => (
        <List.Item
          key={r.id}
          title={r.content}
          subtitle={`${r.source} · ${r.source_date}`}
          accessories={[{ text: `${Math.round(r.similarity_score * 100)}%` }]}
          actions={
            <ActionPanel>
              <Action title="Copy Context" onAction={async () => {
                const tags = r.topic_tags.join(", ");
                const source = r.source.charAt(0).toUpperCase() + r.source.slice(1);
                let text = `<context source="${source}" date="${r.source_date}" topic="${tags}">\n${r.content}`;
                if (r.context) text += `\n${r.context}`;
                text += "\n</context>";
                await Clipboard.copy(text);
                await showHUD("Context copied");
              }} />
            </ActionPanel>
          }
        />
      ))}
    </List>
  );
}
```

**package.json:**
```json
{
  "name": "osctx-raycast",
  "dependencies": { "@raycast/api": "*", "node-fetch": "^3.0.0" },
  "scripts": { "build": "ray build -e dist", "dev": "ray develop" }
}
```

---

## Task 3: Fix `tests/test_extraction.py` (Phase 3)

**File:** `tests/test_extraction.py`

**Pattern — mock the anthropic client:**
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from osctx.daemon.extraction import extract_from_messages, ExtractedUnit

MOCK_UNITS = [
    {
        "content": "Use UUIDs not auto-increment for SaaS user IDs.",
        "category": "decision",
        "topic_tags": ["database", "postgresql"],
        "confidence": 0.95,
        "context": "Security and distributed systems rationale."
    }
]

@pytest.mark.asyncio
async def test_extract_anthropic_backend():
    mock_response = MagicMock()
    mock_response.content = [MagicMock(
        type="tool_use",
        name="extract_knowledge",
        input={"units": MOCK_UNITS}
    )]

    with patch("anthropic.AsyncAnthropic") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        config = {"extraction_backend": "anthropic", "anthropic_api_key": "test-key"}
        messages = [
            {"role": "user", "content": "Should I use UUIDs?"},
            {"role": "assistant", "content": "Yes, use UUIDs for SaaS."},
        ]
        result = await extract_from_messages(messages, config=config)

    assert len(result) == 1
    assert result[0].category == "decision"
    assert result[0].confidence == 0.95

@pytest.mark.asyncio
async def test_extract_filters_low_confidence():
    low_conf = [{**MOCK_UNITS[0], "confidence": 0.5}]
    # ... mock returns low confidence unit → result should be []

@pytest.mark.asyncio
async def test_extract_empty_conversation():
    result = await extract_from_messages([], config={"extraction_backend": "anthropic", "anthropic_api_key": "x"})
    assert result == []

@pytest.mark.asyncio
async def test_chunking_long_conversation():
    # 200 messages → should chunk without error
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 100} for i in range(200)]
    # With mocked extract_fn that returns []...
```

---

## Task 4: Fix Known Issues in STATUS.md

### 4a: Add `gemini_api_key` to `main.py` DEFAULT_CONFIG
In `osctx/daemon/main.py`, add to `_DEFAULT_CONFIG`:
```python
"gemini_api_key": "",
"gemini_model": "gemini-flash-latest",
```

### 4b: Test `osctx install` on real login cycle
Run `osctx install`, log out, log back in, verify daemon is running on port 8765.
Fix whatever path resolution issue appears in the plist.

### 4c: Respect `extraction_on_battery` config key
In `ingestion.py._process_item()`, before calling `extract_from_messages()`:
```python
if not config.get("extraction_on_battery", False):
    # Check if on battery power
    import subprocess
    result = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True)
    if "Battery Power" in result.stdout and "discharging" in result.stdout.lower():
        # Defer extraction — re-queue or skip
        set_conversation_status(conn, conv_id, "pending")
        return
```

---

## Task 5: MCP Server (Phase 5)

**File:** `osctx/mcp_server/server.py`

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
    from osctx.daemon.embeddings import encode_passage
    import uuid, time
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
      "command": "/Users/kilroyhere/Projects/osctx/.venv/bin/python",
      "args": ["-m", "osctx.mcp_server.server"],
      "env": {}
    }
  }
}
```

**Install command to add to CLI:**
```
osctx mcp install   → writes claude_desktop_config.json
```

---

## Testing Checklist Before Each Phase Ships

### Phase 1 (Browser Extension)
- [ ] ChatGPT: Cmd+Shift+S captures conversation, knowledge units appear in search
- [ ] Claude.ai: same
- [ ] Pressing Cmd+Shift+S twice on same conversation → second returns `{"status": "duplicate"}`
- [ ] Daemon down → payload stored in chrome.storage.local → appears after daemon restart

### Phase 2 (Raycast)
- [ ] Search returns results within 500ms
- [ ] Enter copies XML-wrapped context to clipboard
- [ ] Empty query shows empty state (not error)
- [ ] Daemon down shows error message (not crash)

### Phase 3 (Stability)
- [ ] `osctx doctor` passes on clean install
- [ ] Daemon survives system sleep/wake
- [ ] Queue survives daemon restart
- [ ] `pytest` passes with no real API calls

### Phase 5 (MCP)
- [ ] Claude Desktop can call `search_knowledge` and get results
- [ ] `save_insight` appears in subsequent searches
