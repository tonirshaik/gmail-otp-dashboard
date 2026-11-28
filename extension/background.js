const DASH = "@@ORIGIN@@";
const KEY = "otpTok";
const enc = new TextEncoder();

function b64u(buf) {
  let s = "";
  new Uint8Array(buf).forEach((b) => { s += String.fromCharCode(b); });
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function idb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open("otpdev", 1);
    r.onupgradeneeded = () => { r.result.createObjectStore("k"); };
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}

let keyP = null;
function devKey() {
  if (!keyP) {
    keyP = (async () => {
      const db = await idb();
      const got = await new Promise((res, rej) => {
        const q = db.transaction("k").objectStore("k").get("main");
        q.onsuccess = () => res(q.result);
        q.onerror = () => rej(q.error);
      });
      if (got && got.privateKey && got.publicKey) return got;
      const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
      await new Promise((res, rej) => {
        const tx = db.transaction("k", "readwrite");
        tx.objectStore("k").put(kp, "main");
        tx.oncomplete = () => res();
        tx.onerror = () => rej(tx.error);
      });
      return kp;
    })().catch((e) => { keyP = null; throw e; });
  }
  return keyP;
}

async function signHeaders(body) {
  const kp = await devKey();
  const ts = String(Math.floor(Date.now() / 1000));
  const nb = new Uint8Array(12);
  crypto.getRandomValues(nb);
  const nonce = Array.from(nb).map((b) => b.toString(16).padStart(2, "0")).join("");
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, kp.privateKey, enc.encode(ts + "." + nonce + "." + body));
  return { "X-Ext-Ts": ts, "X-Ext-Nonce": nonce, "X-Ext-Sig": b64u(sig) };
}

async function getTok() {
  try {
    const o = await chrome.storage.session.get(KEY);
    const t = o[KEY];
    if (t && t.token && t.exp * 1000 > Date.now()) return t;
    if (t) await chrome.storage.session.remove(KEY);
  } catch (e) {}
  return null;
}

async function api(host, mode, hint) {
  const t = await getTok();
  if (!t) return { ok: false, status: 401, data: { error: "locked" } };
  try {
    const body = JSON.stringify({ host: host, mode: mode || "full", hint: hint || "" });
    const sh = await signHeaders(body);
    const r = await fetch(DASH + "/api/ext/code", {
      method: "POST",
      credentials: "omit",
      headers: Object.assign({ "Content-Type": "application/json", "Authorization": "Bearer " + t.token }, sh),
      body: body
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
    const kp = await devKey();
    const pub = b64u(await crypto.subtle.exportKey("raw", kp.publicKey));
    const r = await fetch(DASH + "/api/ext/login", {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password, pub: pub })
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
    const m0 = String(msg.mode || "full");
    const mode0 = sender.tab ? (["full", "tfa", "email"].indexOf(m0) >= 0 ? m0 : "full") : m0;
    api(host.toLowerCase(), mode0, typeof msg.hint === "string" ? msg.hint.slice(0, 254) : "").then(send);
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

const ALARM = "otpExpire";
let baseP = null;
function baseImg() {
  if (!baseP) {
    baseP = fetch(chrome.runtime.getURL("icons/icon-192.png"))
      .then((r) => r.blob())
      .then((b) => createImageBitmap(b))
      .catch((e) => { baseP = null; throw e; });
  }
  return baseP;
}
function drawIcon(base, size, on) {
  const cv = new OffscreenCanvas(size, size);
  const g = cv.getContext("2d");
  g.drawImage(base, 0, 0, size, size);
  const r = size * 0.32, cx = size - r, cy = size - r;
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  g.fillStyle = on ? "#639922" : "#E24B4A";
  g.fill();
  g.lineWidth = Math.max(1, size * 0.07);
  g.strokeStyle = "#ffffff";
  g.stroke();
  const w = r * 1.0, h = r * 0.75;
  const by = cy - h / 2 + r * 0.2;
  g.fillStyle = "#ffffff";
  g.fillRect(cx - w / 2, by, w, h);
  const sr = w * 0.3;
  g.lineWidth = Math.max(1, size * 0.06);
  g.beginPath();
  g.moveTo(cx - sr, by);
  if (on) {
    g.lineTo(cx - sr, by - sr);
    g.arc(cx, by - sr, sr, Math.PI, 0);
    g.lineTo(cx + sr, by - sr * 0.5);
  } else {
    g.lineTo(cx - sr, by - sr * 0.4);
    g.arc(cx, by - sr * 0.4, sr, Math.PI, 0);
    g.lineTo(cx + sr, by);
  }
  g.stroke();
  return g.getImageData(0, 0, size, size);
}
async function setBadge(on) {
  try {
    chrome.action.setBadgeText({ text: "" });
    chrome.action.setTitle({ title: on ? "OTP Dashboard · আনলক" : "OTP Dashboard · লক" });
  } catch (e) {}
  try {
    const base = await baseImg();
    const imgs = {};
    [16, 32, 48, 128].forEach((n) => { imgs[n] = drawIcon(base, n, on); });
    await chrome.action.setIcon({ imageData: imgs });
  } catch (e) {}
}
async function refreshBadge() {
  const t = await getTok();
  setBadge(!!t);
  try {
    if (chrome.alarms) {
      if (t) chrome.alarms.create(ALARM, { when: t.exp * 1000 + 500 });
      else chrome.alarms.clear(ALARM);
    }
  } catch (e) {}
}
chrome.runtime.onInstalled.addListener(() => { refreshBadge(); });
chrome.runtime.onStartup.addListener(() => { refreshBadge(); });
if (chrome.alarms) {
  chrome.alarms.onAlarm.addListener((a) => { if (a.name === ALARM) refreshBadge(); });
}
chrome.storage.onChanged.addListener((ch, area) => {
  if (area === "session" && ch[KEY]) refreshBadge();
});
refreshBadge();
