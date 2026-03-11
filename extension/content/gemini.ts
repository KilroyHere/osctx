// OSCTX content script for gemini.google.com
// Selector strategy: attribute-based ONLY — never class names

import { postToOsctx, startInactivityTimer, type Message } from "./utils";

const SOURCE = "gemini" as const;

// Gemini prepends UI chrome like "You said\n\n", "Gemini said\n\n", "Show thinking\n"
// to all messages in innerText. Strip these so only the raw message is stored.
function stripGeminiPrefix(text: string): string {
  return text
    .replace(/^Show thinking\s*\n+/i, "")
    .replace(/^You said\s*\n+/i, "")
    .replace(/^Gemini said\s*\n+/i, "")
    .trim();
}

function extractMessages(): Message[] {
  const messages: Message[] = [];

  // Gemini uses custom elements: user-query and model-response
  // These are interleaved in DOM order matching the conversation flow.
  const allTurns = document.querySelectorAll<HTMLElement>("user-query, model-response");

  if (allTurns.length > 0) {
    for (const el of allTurns) {
      const tag = el.tagName.toLowerCase();
      const isUser = tag === "user-query";
      const content = stripGeminiPrefix(el.innerText.trim());
      if (content.length > 0) {
        messages.push({ role: isUser ? "user" : "assistant", content });
      }
    }
    return messages;
  }

  // Fallback: data-test-id based (older Gemini versions)
  const fallbackTurns = document.querySelectorAll<HTMLElement>(
    '[data-test-id="user-prompt"], [data-test-id="model-response"]'
  );
  if (fallbackTurns.length > 0) {
    for (const el of fallbackTurns) {
      const testId = el.getAttribute("data-test-id") ?? "";
      const isUser = testId === "user-prompt";
      const content = stripGeminiPrefix(el.innerText.trim());
      if (content.length > 0) {
        messages.push({ role: isUser ? "user" : "assistant", content });
      }
    }
    return messages;
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
