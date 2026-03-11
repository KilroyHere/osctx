// OSCTX content script for chat.openai.com and chatgpt.com
// Selector strategy: attribute-based ONLY — never class names

import { postToOsctx, startInactivityTimer, type Message } from "./utils";

const SOURCE = "chatgpt" as const;

function extractMessages(): Message[] {
  // Primary: data-message-author-role is stable across ChatGPT deploys
  const turns = document.querySelectorAll<HTMLElement>("[data-message-author-role]");
  if (turns.length === 0) return [];

  const messages: Message[] = [];
  for (const el of turns) {
    const role = el.getAttribute("data-message-author-role");
    if (role !== "user" && role !== "assistant") continue;

    // Prefer the explicit text-content attribute; fall back to innerText
    const textEl =
      el.querySelector<HTMLElement>("[data-message-text-content='true']") ??
      el.querySelector<HTMLElement>("[data-testid='conversation-turn-content']") ??
      el;

    const content = textEl.innerText.trim();
    if (content.length > 0) {
      messages.push({ role, content });
    }
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

// 1. Keyboard command via background worker
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "OSCTX_SAVE") captureAndSend();
});

// 2. Tab close / navigation away
window.addEventListener("pagehide", captureAndSend);

// 3. 5-minute inactivity
let stopTimer = startInactivityTimer(captureAndSend);

// Restart timer if the SPA navigates to a new conversation
const navObserver = new MutationObserver(() => {
  const newUrl = location.href;
  if (newUrl !== lastUrl) {
    lastUrl = newUrl;
    stopTimer();
    stopTimer = startInactivityTimer(captureAndSend);
  }
});
let lastUrl = location.href;
navObserver.observe(document.body, { childList: true, subtree: true });
