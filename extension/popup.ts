const DAEMON_URL = "http://localhost:8765";

const dot = document.getElementById("dot")!;
const statusLabel = document.getElementById("status-label")!;
const statsEl = document.getElementById("stats")!;
const statUnits = document.getElementById("stat-units")!;
const statConvs = document.getElementById("stat-convs")!;
const statPending = document.getElementById("stat-pending")!;
const btnSave = document.getElementById("btn-save") as HTMLButtonElement;
const btnSearch = document.getElementById("btn-search") as HTMLAnchorElement;
const msg = document.getElementById("msg")!;

btnSearch.href = `${DAEMON_URL}/ui`;

// Check daemon status via background worker
chrome.runtime.sendMessage({ type: "OSCTX_GET_STATUS" }, (response) => {
  if (!response || !response.online) {
    dot.classList.add("offline");
    statusLabel.textContent = "Daemon offline — start with: osctx install";
    btnSave.disabled = true;
    return;
  }

  dot.classList.add("online");
  const s = response.stats;
  statusLabel.textContent = "Daemon running";
  statsEl.classList.add("visible");
  statUnits.textContent = String(s.knowledge_units ?? 0);
  statConvs.textContent = String(s.conversations ?? 0);
  statPending.textContent = String(s.pending_extraction ?? 0);
});

// Save current chat
btnSave.addEventListener("click", async () => {
  btnSave.disabled = true;
  msg.textContent = "";

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    msg.textContent = "No active tab";
    btnSave.disabled = false;
    return;
  }

  try {
    await chrome.tabs.sendMessage(tab.id, { type: "OSCTX_SAVE" });
    msg.textContent = "Saved ✓";
    setTimeout(() => { msg.textContent = ""; }, 2000);
  } catch {
    msg.style.color = "#ef5350";
    msg.textContent = "Not supported on this page";
  }

  btnSave.disabled = false;
});
