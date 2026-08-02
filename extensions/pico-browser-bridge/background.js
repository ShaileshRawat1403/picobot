const API = "http://127.0.0.1:18792/api/browser-bridge";

async function getShare() {
  const {share} = await chrome.storage.session.get("share");
  return share || null;
}

async function requestCommand(share) {
  const response = await fetch(`${API}/commands/next?share_id=${encodeURIComponent(share.id)}`, {
    headers: {"X-Pico-Bridge-Token": share.token}
  });
  if (!response.ok) throw new Error("Pico command queue unavailable");
  return (await response.json()).command;
}

async function executeCommand(command, share) {
  if (command.tab_id !== share.tab_id) throw new Error("Pico command targeted a different tab");
  if (command.operation === "navigate") {
    await chrome.tabs.update(command.tab_id, {url: command.payload.url});
    return "Navigation dispatched to the shared tab.";
  }
  const [{result}] = await chrome.scripting.executeScript({
    target: {tabId: command.tab_id},
    func: (operation, payload) => {
      const element = document.querySelector(payload.selector);
      if (!element) return {ok: false, detail: "Target was not found on the shared tab."};
      if (operation === "click") {
        element.click();
        return {ok: true, detail: "Click dispatched to the shared tab."};
      }
      if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element.isContentEditable)) {
        return {ok: false, detail: "Target is not an editable field."};
      }
      if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
        const setter = Object.getOwnPropertyDescriptor(element.constructor.prototype, "value")?.set;
        if (setter) setter.call(element, payload.text); else element.value = payload.text;
      } else {
        element.textContent = payload.text;
      }
      element.dispatchEvent(new Event("input", {bubbles: true}));
      element.dispatchEvent(new Event("change", {bubbles: true}));
      return {ok: true, detail: "Non-sensitive text entered on the shared tab."};
    },
    args: [command.operation, command.payload]
  });
  if (!result?.ok) throw new Error(result?.detail || "Browser action was refused by the shared tab.");
  return result.detail;
}

async function pollCommands() {
  const share = await getShare();
  if (!share) return;
  let command;
  try {
    command = await requestCommand(share);
  } catch {
    return;
  }
  if (!command) return;
  let success = true;
  let resultSummary = "Browser command completed.";
  let failureCategory = null;
  try {
    resultSummary = await executeCommand(command, share);
  } catch (error) {
    success = false;
    resultSummary = error?.message || "Browser command failed.";
    failureCategory = "browser_execution";
  }
  await fetch(`${API}/commands/${encodeURIComponent(command.id)}/result`, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Pico-Bridge-Token": share.token},
    body: JSON.stringify({share_id: share.id, success, result_summary: resultSummary, failure_category: failureCategory})
  }).catch(() => {});
}

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("pico-command-poll", {periodInMinutes: 0.5}));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("pico-command-poll", {periodInMinutes: 0.5}));
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "pico-command-poll") pollCommands(); });
chrome.storage.session.onChanged.addListener(() => pollCommands());
