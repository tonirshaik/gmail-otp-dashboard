const DASH = "@@ORIGIN@@";
const KEY = "otpTok";

async function getTok() {
  try {
    const o = await chrome.storage.session.get(KEY);
    const t = o[KEY];
    if (t && t.token && t.exp * 1000 > Date.now()) return t;
    if (t) await chrome.storage.session.remove(KEY);
  } catch (e) {}
  return null;
}

async function api(host) {
  const t = await getTok();
  if (!t) return { ok: false, status: 401, data: { error: "locked" } };
  try {
    const r = await fetch(DASH + "/api/ext/code", {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + t.token },
      body: JSON.stringify({ host: host })
    });
    let data = {};
    try { data = await r.json(); } catch (e) {}
    if (r.status === 401) { try { await chrome.storage.session.remove(KEY); } catch (e) {} }
    return { ok: r.ok, status: r.status, data: data };
  } catch (e) {
    return { ok: false, status: 0, data: { error: "offline" } };
  }
}

async function login(password) {
  try {
    const r = await fetch(DASH + "/api/ext/login", {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password })
    });
    let data = {};
    try { data = await r.json(); } catch (e) {}
    if (r.ok && data.token) {
      await chrome.storage.session.set({ [KEY]: { token: data.token, exp: data.expires_at } });
      return { ok: true, exp: data.expires_at };
    }
    return { ok: false, status: r.status, error: data.error || "" };
  } catch (e) {
    return { ok: false, status: 0, error: "" };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, send) => {
  if (!msg) return;
  if (msg.type === "get") {
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
  }
  if (sender.tab) return;
  if (msg.type === "login") {
    login(String(msg.password || "")).then(send);
    return true;
  }
  if (msg.type === "lock") {
    chrome.storage.session.remove(KEY).then(() => send({ ok: true }), () => send({ ok: false }));
    return true;
  }
  if (msg.type === "status") {
    getTok().then((t) => send({ unlocked: !!t }));
    return true;
  }
});
