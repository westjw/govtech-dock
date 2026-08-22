/* The whole reason this is an extension and not a bookmarklet: a page on https
   cannot reach http://127.0.0.1 (Chrome blocks fetch and script tags both,
   verified the hard way), but an extension's service worker with a host
   permission can. The content script asks; this fetches; the admin answers.

   activeTab + on-click injection means the extension can read NOTHING until
   you click it, and then only the tab you clicked on. Same line the
   bookmarklet drew: reading a page you opened, never harvesting a site. */
const API = "http://127.0.0.1:8787";

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg.kind !== "api") return;
  fetch(API + msg.path, {
    method: msg.body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: msg.body ? JSON.stringify(msg.body) : undefined,
  })
    .then((r) => r.json())
    .then((data) => respond({ ok: true, data }))
    .catch((e) => respond({ ok: false, error: String(e) }));
  return true;                       // keep the channel open for the async reply
});

chrome.action.onClicked.addListener((tab) => {
  chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["capture.js"] });
});
