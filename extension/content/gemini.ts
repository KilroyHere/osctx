// OSCTX content script for gemini.google.com
// Selector strategy: attribute-based ONLY — never class names

import { postToOsctx, startInactivityTimer, type Message } from "./utils";

const SOURCE = "gemini" as const;

function extractMessages(): Message[] {
  const messages: Message[] = [];

  // Gemini uses model-response and user-query elements with data attributes
  // Primary selectors based on Gemini's known data attributes
  const userTurns = document.querySelectorAll<HTMLElement>(
    '[data-test-id="user-prompt"], user-query, [class*="user-query"]'
  );
  const assistantTurns = document.querySelectorAll<HTMLElement>(
    '[data-test-id="model-response"], model-response, [class*="model-response"]'
  );

  // Interleave by DOM order
  const allTurns = document.querySelectorAll<HTMLElement>(
    '[data-test-id="user-prompt"], [data-test-id="model-response"], user-query, model-response'
  );

  if (allTurns.length > 0) {
    for (const el of allTurns) {
      const testId = el.getAttribute("data-test-id") ?? el.tagName.toLowerCase();
      const isUser = testId === "user-prompt" || testId === "user-query";
      const content = el.innerText.trim();
      if (content.length > 0) {
        messages.push({ role: isUser ? "user" : "assistant", content });
      }
    }
    return messages;
  }

  // Fallback: aria-label based identification
  const promptEls = document.querySelectorAll<HTMLElement>('[aria-label="You"]');
  const responseEls = document.querySelectorAll<HTMLElement>('[aria-label="Gemini"]');

  // Merge and sort by DOM position
  const combined: Array<{ el: HTMLElement; role: "user" | "assistant" }> = [
    ...Array.from(promptEls).map((el) => ({ el, role: "user" as const })),
    ...Array.from(responseEls).map((el) => ({ el, role: "assistant" as const })),
  ].sort((a, b) =>
    a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1
  );

  for (const { el, role } of combined) {
    const content = el.innerText.trim();
    if (content.length > 0) messages.push({ role, content });
  }

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
