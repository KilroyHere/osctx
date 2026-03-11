// OSCTX background service worker (Manifest V3)
// Responsibilities:
//   1. Forward keyboard commands to the active tab's content script
//   2. Open search UI on Cmd+Shift+M
//   3. Retry failed POSTs from chrome.storage.local every 60s

const DAEMON_URL = "http://localhost:8765";
const PENDING_KEY = "osctx_pending";
const RETRY_INTERVAL_MS = 60_000;

// ── Command handler ────────────────────────────────────────────────────────

chrome.commands.onCommand.addListener(async (command) => {
  if (command === "save-chat") {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.id) {
      chrome.tabs.sendMessage(tab.id, { type: "OSCTX_SAVE" }).catch(() => {
        // Content script not injected on this page — ignore
      });
    }
  }

  if (command === "open-search") {
    chrome.tabs.create({ url: `${DAEMON_URL}/ui` });
  }
});

// ── Retry loop ─────────────────────────────────────────────────────────────

async function retryPending(): Promise<void> {
  const stored = await chrome.storage.local.get(PENDING_KEY);
  const pending: unknown[] = stored[PENDING_KEY] ?? [];
  if (pending.length === 0) return;

  const failed: unknown[] = [];
  for (const payload of pending) {
    try {
      const resp = await fetch(`${DAEMON_URL}/ingest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) failed.push(payload);
    } catch {
      failed.push(payload);
    }
  }

  await chrome.storage.local.set({ [PENDING_KEY]: failed });
  if (failed.length < pending.length) {
    console.log(`[OSCTX] Flushed ${pending.length - failed.length} pending item(s)`);
  }
}

// Run retry loop on service worker startup and every 60s
retryPending();
setInterval(retryPending, RETRY_INTERVAL_MS);

// ── Popup message handler ──────────────────────────────────────────────────
// Receives { type: "OSCTX_GET_STATUS" } from popup, replies with daemon status

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "OSCTX_GET_STATUS") {
    fetch(`${DAEMON_URL}/status`)
      .then((r) => r.json())
      .then((data) => sendResponse({ online: true, stats: data }))
      .catch(() => sendResponse({ online: false }));
    return true; // keep channel open for async response
  }
});
