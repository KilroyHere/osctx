// Shared utilities for all OSCTX content scripts

export const DAEMON_URL = "http://localhost:8765";
const PENDING_KEY = "osctx_pending";

export interface Message {
  role: "user" | "assistant";
  content: string;
}

export interface IngestPayload {
  source: "chatgpt" | "claude" | "gemini";
  url: string;
  captured_at: number;
  messages: Message[];
  title?: string;
}

// POST to daemon. On failure, buffer to chrome.storage.local for retry.
export async function postToOsctx(payload: IngestPayload): Promise<void> {
  if (payload.messages.length === 0) return;

  try {
    const resp = await fetch(`${DAEMON_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (resp.ok) {
      const data = await resp.json();
      console.log(`[OSCTX] ${data.status}`, data);
    }
  } catch {
    // Daemon not running — buffer for background worker retry
    console.log("[OSCTX] Daemon unreachable, buffering payload");
    const stored = await chrome.storage.local.get(PENDING_KEY);
    const pending: IngestPayload[] = stored[PENDING_KEY] ?? [];
    pending.push(payload);
    await chrome.storage.local.set({ [PENDING_KEY]: pending });
  }
}

// Start a 5-minute inactivity timer reset by DOM mutations.
// Returns a cleanup function.
export function startInactivityTimer(
  onInactive: () => void,
  timeoutMs: number = 5 * 60 * 1000
): () => void {
  let timer: ReturnType<typeof setTimeout>;

  const reset = (): void => {
    clearTimeout(timer);
    timer = setTimeout(onInactive, timeoutMs);
  };

  reset();

  const observer = new MutationObserver(reset);
  observer.observe(document.body, { childList: true, subtree: true });

  return () => {
    clearTimeout(timer);
    observer.disconnect();
  };
}
