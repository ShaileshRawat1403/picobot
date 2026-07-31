const API = "http://127.0.0.1:18792/api/browser-bridge";
const pairingInput = document.getElementById("pairing-code");
const shareButton = document.getElementById("share");
const refreshButton = document.getElementById("refresh");
const status = document.getElementById("status");

function setStatus(message) { status.textContent = message; }
function setBusy(value) { shareButton.disabled = value; refreshButton.disabled = value; }

async function currentSnapshot() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab || typeof tab.id !== "number") throw new Error("Open the tab you want to share, then try again.");
  const [{result}] = await chrome.scripting.executeScript({
    target: {tabId: tab.id},
    func: () => ({url: location.href, title: document.title || location.hostname, visible_text: document.body?.innerText || ""})
  });
  return {tab_id: tab.id, ...result};
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Pico Browser Bridge is unavailable.");
  return payload;
}

async function restore() {
  const {share} = await chrome.storage.session.get("share");
  if (share) {
    pairingInput.hidden = true;
    shareButton.hidden = true;
    refreshButton.hidden = false;
    setStatus(`Sharing ${share.domain} with Pico. Refresh only while this same tab is active.`);
  }
}

shareButton.addEventListener("click", async () => {
  const pairingCode = pairingInput.value.trim();
  if (!pairingCode) { setStatus("Paste the short-lived session code from Pico Operations first."); return; }
  setBusy(true);
  try {
    const snapshot = await currentSnapshot();
    const result = await request("/share", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({pairing_code: pairingCode, ...snapshot})});
    const share = {id: result.share.id, token: result.bridge_token, tab_id: snapshot.tab_id, domain: result.share.domain};
    await chrome.storage.session.set({share});
    await restore();
  } catch (error) { setStatus(error.message); }
  finally { setBusy(false); }
});

refreshButton.addEventListener("click", async () => {
  setBusy(true);
  try {
    const {share} = await chrome.storage.session.get("share");
    if (!share) throw new Error("This browser session is no longer paired. Share the tab again.");
    const snapshot = await currentSnapshot();
    if (snapshot.tab_id !== share.tab_id) throw new Error("Switch back to the exact tab you shared before refreshing it.");
    const result = await request("/refresh", {method: "POST", headers: {"Content-Type": "application/json", "X-Pico-Bridge-Token": share.token}, body: JSON.stringify({share_id: share.id, ...snapshot})});
    setStatus(`Snapshot refreshed for ${result.share.domain}.`);
  } catch (error) { setStatus(error.message); }
  finally { setBusy(false); }
});

restore();
