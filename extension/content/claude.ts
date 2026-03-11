// OSCTX content script for claude.ai
// Selector strategy: attribute-based ONLY — never class names

import { postToOsctx, startInactivityTimer, type Message } from "./utils";

const SOURCE = "claude" as const;

function extractMessages(): Message[] {
  const messages: Message[] = [];

  // User messages: data-testid="user-message" is stable
  const userTurns = document.querySelectorAll<HTMLElement>('[data-testid="user-message"]');
  // Assistant messages: wrapped in elements with data-testid starting with "message"
  // that are NOT the user message — use the fieldset/article pattern
  const allTurns = document.querySelectorAll<HTMLElement>(
    '[data-testid="user-message"], [data-testid="ai-message"]'
  );

  if (allTurns.length > 0) {
    for (const el of allTurns) {
      const testId = el.getAttribute("data-testid") ?? "";
      const role: "user" | "assistant" = testId === "user-message" ? "user" : "assistant";
      const content = el.innerText.trim();
      if (content.length > 0) messages.push({ role, content });
    }
    return messages;
  }

  // Fallback: conversation turns identified by aria roles
  // Claude wraps each turn in a section/article with role="presentation" or similar
  // Use positional inference: odd = user, even = assistant (if no data-testid available)
  const turns = document.querySelectorAll<HTMLElement>(
    '[data-test-render-count], [data-message-index]'
  );
  turns.forEach((el, i) => {
    const content = el.innerText.trim();
    if (content.length > 0) {
      messages.push({ role: i % 2 === 0 ? "user" : "assistant", content });
    }
  });

  return messages;
}

function captureAndSend(): void {
  const messages = extractMessages();
  if (messages.length === 0) return;

  postToOsctx({
    source: SOURCE,
    url: location.href,
    captured_at: Math.floor(Date.now() / 1000),
    messages,
    title: document.title || undefined,
  });
}

// ── Triggers ───────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "OSCTX_SAVE") captureAndSend();
});

window.addEventListener("pagehide", captureAndSend);

let stopTimer = startInactivityTimer(captureAndSend);

let lastUrl = location.href;
const navObserver = new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    stopTimer();
    stopTimer = startInactivityTimer(captureAndSend);
  }
});
navObserver.observe(document.body, { childList: true, subtree: true });
