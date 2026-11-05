const DASH = "@@ORIGIN@@";
const box = document.getElementById("box");
const hostEl = document.getElementById("host");
let tab = null, host = "", foot = null, tfaItems = [], emailCard = null, curSig = "", busy = false, lastSync = 0;
const NOPAGE = "এই পাতায় কাজ করে না। কোনো https সাইটে খুলুন।";

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function note(text, withOpen) {
  box.textContent = "";
  box.appendChild(el("div", "note", text));
  if (withOpen) {
    const b = el("button", "btn primary", "ড্যাশবোর্ড খুলুন");
    b.addEventListener("click", () => chrome.tabs.create({ url: DASH }));
    box.appendChild(b);
  }
}
function fmt(c) {
  return /^\d{6}$/.test(c) ? c.slice(0, 3) + " " + c.slice(3) : c;
}
function ago(s) {
  if (s < 45) return "এইমাত্র";
  if (s < 3600) return Math.max(1, Math.floor(s / 60)) + " মিনিট আগে";
  if (s < 86400) return Math.floor(s / 3600) + " ঘণ্টা আগে";
  return Math.floor(s / 86400) + " দিন আগে";
}
function flash(btn, text) {
  const old = btn.dataset.t || btn.textContent;
  btn.dataset.t = old;
  btn.textContent = text;
  setTimeout(() => { btn.textContent = old; }, 1200);
}
function fillIt(code, btn) {
  chrome.tabs.sendMessage(tab.id, { type: "fill", code: code }, (r) => {
    if (chrome.runtime.lastError) { flash(btn, "পেজ রিলোড করুন"); return; }
    flash(btn, r && r.ok ? "ভরা হয়েছে" : "ঘর পাইনি");
  });
}
function card(title, sub, code, expired) {
  const c = el("div", "card");
  c.appendChild(el("div", "ctitle", title));
  const s = el("div", "csub", sub);
  c.appendChild(s);
  const codeEl = el("div", "code" + (expired ? " dim" : ""), fmt(code));
  c.appendChild(codeEl);
  const o = { node: c, sub: s, codeEl: codeEl, code: code };
  const row = el("div", "row");
  const cp = el("button", "btn", "কপি");
  cp.addEventListener("click", () => {
    navigator.clipboard.writeText(o.code).then(() => flash(cp, "কপি হয়েছে"), () => flash(cp, "হয়নি"));
  });
  const fl = el("button", "btn primary", "অটো-ফিল");
  fl.addEventListener("click", () => fillIt(o.code, fl));
  row.appendChild(cp);
  row.appendChild(fl);
  c.appendChild(row);
  return o;
}
function sig(d) {
  return (d.email ? d.email.email + "|" + d.email.code : "") + "#" + (d.tfa || []).map((t) => t.id).join(",");
}
function emailSub(e) {
  return e.email + " · " + (e.expired ? "মেয়াদ শেষ · " : "") + ago(e.age);
}
function endAt(t, t1) {
  return t1 + (t.remaining_ms != null ? t.remaining_ms : (t.remaining || 30) * 1000);
}
function render(d, t1) {
  box.textContent = "";
  tfaItems = [];
  emailCard = null;
  curSig = sig(d);
  let n = 0;
  if (d.email) {
    const e = d.email;
    const c = card(e.service || "Email code", emailSub(e), e.code, e.expired);
    emailCard = c;
    box.appendChild(c.node);
    n++;
  }
  (d.tfa || []).forEach((t) => {
    const c = card(t.name + " 2FA", "", t.code, false);
    c.sub.textContent = (t.label ? t.label + " · " : "");
    const left = el("span", "", "");
    c.sub.appendChild(left);
    tfaItems.push({ id: t.id, c: c, left: left, next: t.next_code || "", per: (t.period || 30) * 1000, at: endAt(t, t1) });
    box.appendChild(c.node);
    n++;
  });
  if (!n) note("এই সাইটের জন্য কোনো কোড পাওয়া যায়নি।");
}
function patchTfa(list, t1) {
  (list || []).forEach((t) => {
    const it = tfaItems.find((x) => x.id === t.id);
    if (!it) return;
    it.next = t.next_code || "";
    it.per = (t.period || 30) * 1000;
    it.at = endAt(t, t1);
    if (it.c.code !== t.code) { it.c.code = t.code; it.c.codeEl.textContent = fmt(t.code); }
  });
}
function patch(d, t1) {
  if (d.email && emailCard) {
    emailCard.sub.textContent = emailSub(d.email);
    emailCard.codeEl.classList.toggle("dim", !!d.email.expired);
  }
  patchTfa(d.tfa, t1);
}

function setFoot(on) {
  if (!on) { if (foot) { foot.remove(); foot = null; } return; }
  if (foot) return;
  foot = el("div", "foot");
  const row = el("div", "frow");
  if (host) {
    const rf = el("button", "btn ref", "↻ রিফ্রেশ");
    rf.addEventListener("click", () => load("refresh", rf));
    row.appendChild(rf);
  }
  const lk = el("button", "btn", "লক করুন");
  lk.addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "lock" });
    setFoot(false);
    tfaItems = [];
    emailCard = null;
    if (!host) note(NOPAGE); else showLogin("");
  });
  row.appendChild(lk);
  foot.appendChild(row);
  foot.appendChild(el("div", "fnote", "আনলক আছে · ২৪ ঘণ্টার মধ্যে শেষ হবে"));
  document.body.appendChild(foot);
}
function afterUnlock() {
  if (!host) { note(NOPAGE); setFoot(true); } else load("full");
}
function showLogin(msg) {
  tfaItems = [];
  emailCard = null;
  box.textContent = "";
  const c = el("div", "card");
  c.appendChild(el("div", "ctitle", "লগইন করুন"));
  c.appendChild(el("div", "csub wrap", "মাস্টার পাসওয়ার্ড একবার দিন। ২৪ ঘণ্টা আর চাইবে না। পাসওয়ার্ড সেভ হয় না।"));
  const inp = document.createElement("input");
  inp.type = "password";
  inp.className = "pw";
  inp.placeholder = "মাস্টার পাসওয়ার্ড";
  inp.autocomplete = "off";
  const err = el("div", "err", msg || "");
  const b = el("button", "btn primary", "আনলক করুন");
  const go = async () => {
    const pw = inp.value;
    if (!pw) { inp.focus(); return; }
    b.disabled = true;
    err.textContent = "";
    const r = await chrome.runtime.sendMessage({ type: "login", password: pw });
    inp.value = "";
    b.disabled = false;
    if (r && r.ok) { afterUnlock(); return; }
    if (r && r.error) err.textContent = r.error;
    else if (r && r.status === 0) err.textContent = "ড্যাশবোর্ড সার্ভারে পৌঁছানো যায়নি।";
    else err.textContent = "পাসওয়ার্ড ভুল।";
  };
  b.addEventListener("click", go);
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  c.appendChild(inp);
  c.appendChild(err);
  c.appendChild(b);
  box.appendChild(c);
  setTimeout(() => { try { inp.focus(); } catch (e) {} }, 30);
}

async function load(mode, btn) {
  if (busy) return;
  busy = true;
  let msg = "";
  if (btn) {
    btn.dataset.t = btn.dataset.t || btn.textContent;
    btn.disabled = true;
    btn.textContent = "চেক হচ্ছে…";
  }
  try {
    const r = await chrome.runtime.sendMessage({ type: "get", host: host, mode: mode });
    const t1 = Date.now();
    if (!r || r.status === 0) { if (mode === "full") note("ড্যাশবোর্ড সার্ভারে পৌঁছানো যায়নি।"); else msg = "সার্ভার পাইনি"; return; }
    if (r.status === 401) { setFoot(false); showLogin(""); return; }
    if (r.status === 429) { if (mode === "full") note("একটু পরে আবার চেষ্টা করুন।"); else msg = "একটু পরে চাপুন"; return; }
    if (!r.ok) { if (mode === "full") note((r.data && r.data.error) || "কোড আনা যায়নি।"); else msg = "হয়নি"; return; }
    setFoot(true);
    if (mode === "tfa") patchTfa(r.data.tfa, t1);
    else if (sig(r.data) === curSig) patch(r.data, t1);
    else render(r.data, t1);
    if (btn) msg = "হয়েছে";
  } finally {
    busy = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = msg || btn.dataset.t;
      if (msg) setTimeout(() => { btn.textContent = btn.dataset.t; }, 1200);
    }
  }
}

setInterval(() => {
  if (!tfaItems.length) return;
  const now = Date.now();
  let sync = false;
  tfaItems.forEach((it) => {
    if (now >= it.at) {
      if (it.next) {
        it.c.code = it.next;
        it.c.codeEl.textContent = fmt(it.next);
        it.next = "";
        it.at += it.per;
      }
      sync = true;
    }
    const s = Math.max(0, Math.ceil((it.at - now) / 1000));
    it.left.textContent = "বদলাবে " + s + " সেকেন্ডে";
  });
  if (sync && !busy && now - lastSync > 2000) { lastSync = now; load("tfa"); }
}, 250);

(async () => {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  tab = tabs[0];
  let u = null;
  try { u = new URL(tab.url); } catch (e) {}
  if (!u || u.protocol !== "https:") {
    note(NOPAGE);
    const st = await chrome.runtime.sendMessage({ type: "status" });
    setFoot(!!(st && st.unlocked));
    return;
  }
  host = u.hostname;
  hostEl.textContent = host;
  load("full");
})();
