// Shared utilities for all OSCTX content scripts

export const DAEMON_URL = "http://localhost:8765";
const PENDING_KEY = "osctx_pending";
const POLL_DELAY_MS = 45_000;

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

function notify(title: string, message: string): void {
  chrome.notifications.create({
    type: "basic",
    iconUrl: chrome.runtime.getURL("icons/icon48.png"),
    title,
    message,
  });
}

// POST to daemon. On failure, buffer to chrome.storage.local for retry.
// Shows a notification on capture and a follow-up once extraction completes.
export async function postToOsctx(payload: IngestPayload): Promise<void> {
  if (payload.messages.length === 0) return;

  // Snapshot current units count so we can compute the delta later
  let baselineUnits = 0;
  try {
    const s = await fetch(`${DAEMON_URL}/status`).then((r) => r.json());
    baselineUnits = s.knowledge_units ?? 0;
  } catch { /* daemon offline — will be caught below */ }

  try {
    const resp = await fetch(`${DAEMON_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (resp.ok) {
      const data = await resp.json();
      console.log(`[OSCTX] ${data.status}`, data);

      if (data.status === "queued") {
        const source = payload.source.charAt(0).toUpperCase() + payload.source.slice(1);
        notify(
          "OSCTX: Capturing conversation",
          `${data.new_messages} messages from ${source} queued for extraction`
        );

        // Poll once after extraction should be done
        setTimeout(async () => {
          try {
            const s = await fetch(`${DAEMON_URL}/status`).then((r) => r.json());
            const added = (s.knowledge_units ?? 0) - baselineUnits;
            if (added > 0) {
              notify(
                `OSCTX: Saved ${added} knowledge unit${added !== 1 ? "s" : ""}`,
                `From ${source}${payload.title ? ": " + payload.title : ""}`
              );
            }
          } catch { /* daemon went offline during extraction */ }
        }, POLL_DELAY_MS);
      }
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
