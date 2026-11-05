const DASH = "@@ORIGIN@@";

async function api(host) {
  try {
    const r = await fetch(DASH + "/api/ext/code", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host: host })
    });
    let data = {};
    try { data = await r.json(); } catch (e) {}
    return { ok: r.ok, status: r.status, data: data };
  } catch (e) {
    return { ok: false, status: 0, data: { error: "offline" } };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, send) => {
  if (!msg || msg.type !== "get") return;
  let host = "";
  if (sender.tab && sender.tab.url) {
    try { host = new URL(sender.tab.url).hostname; } catch (e) {}
  } else if (typeof msg.host === "string") {
    host = msg.host;
  }
  if (!host) {
    send({ ok: false, status: 0, data: { error: "no host" } });
    return;
  }
  api(host.toLowerCase()).then(send);
  return true;
});
