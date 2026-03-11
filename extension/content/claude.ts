// OSCTX content script for claude.ai
// Selector strategy: attribute-based ONLY — never class names

import { postToOsctx, startInactivityTimer, type Message } from "./utils";

const SOURCE = "claude" as const;

function extractMessages(): Message[] {
  const messages: Message[] = [];

  // Each conversation turn is a direct child wrapper with data-test-render-count.
  // Role detection:
  //   user  → wrapper contains [data-testid="user-message"]
  //   assistant → wrapper contains [data-is-streaming] (no user-message inside)
  const turns = document.querySelectorAll<HTMLElement>("[data-test-render-count]");

  if (turns.length > 0) {
    for (const el of turns) {
      const isUser = !!el.querySelector('[data-testid="user-message"]');
      const isAssistant = !!el.querySelector("[data-is-streaming]");
      if (!isUser && !isAssistant) continue;

      // For user turns, extract from the user-message child for clean text
      const textEl = isUser
        ? (el.querySelector<HTMLElement>('[data-testid="user-message"]') ?? el)
        : el;

      const content = textEl.innerText.trim();
      if (content.length > 0) {
        messages.push({ role: isUser ? "user" : "assistant", content });
      }
    }
    return messages;
  }

  // Fallback: positional inference if neither selector matched
  const allTurns = document.querySelectorAll<HTMLElement>("[data-message-index]");
  allTurns.forEach((el, i) => {
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
