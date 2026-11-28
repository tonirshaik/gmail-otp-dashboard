const DASH = "@@ORIGIN@@";
const box = document.getElementById("box");
const hostEl = document.getElementById("host");
let tab = null, host = "", pageHint = "", pageKind = "full", noBox = false, foot = null, tfaItems = [], emailCard = null, curSig = "", busy = false, lastSync = 0;
const NOPAGE = "এই পাতায় কাজ করে না। কোনো https সাইটে খুলুন।";

const NOBOX = "এই পাতায় কোড বসানোর ঘর নেই, তাই কোনো Gmail খোঁজা হয়নি। কোডের ঘর আছে এমন পাতায় এক্সটেনশন চাপুন।";
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
function setCode(node, code) {
  node.classList.remove("chg");
  void node.offsetWidth;
  node.classList.add("chg");
  node.textContent = "";
  const s = String(code || "");
  const split = /^\d{6}$/.test(s) ? 3 : 0;
  for (let i = 0; i < s.length; i++) {
    if (split && i === split) node.appendChild(el("span", "gap", ""));
    node.appendChild(el("span", "dg", s.charAt(i)));
  }
}
function icon(kind) {
  const NS = "http://www.w3.org/2000/svg";
  const P = {
    mail: ["M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-14a2 2 0 0 1-2-2z", "M3 7l9 6l9-6"],
    shield: ["M12 3a12 12 0 0 0 8.5 3a12 12 0 0 1-8.5 15a12 12 0 0 1-8.5-15a12 12 0 0 0 8.5-3", "M11 11a1 1 0 1 0 2 0a1 1 0 0 0-2 0", "M12 12l0 2.5"],
    lock: ["M5 13a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-10a2 2 0 0 1-2-2z", "M11 16a1 1 0 1 0 2 0a1 1 0 0 0-2 0", "M8 11v-4a4 4 0 1 1 8 0v4"]
  };
  const w = el("div", "ic " + (P[kind] ? kind : "mail"));
  const sv = document.createElementNS(NS, "svg");
  sv.setAttribute("viewBox", "0 0 24 24");
  sv.setAttribute("width", "18");
  sv.setAttribute("height", "18");
  sv.setAttribute("fill", "none");
  sv.setAttribute("stroke", "currentColor");
  sv.setAttribute("stroke-width", "2");
  sv.setAttribute("stroke-linecap", "round");
  sv.setAttribute("stroke-linejoin", "round");
  (P[kind] || P.mail).forEach(function (d) {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d);
    sv.appendChild(p);
  });
  w.appendChild(sv);
  return w;
}
function skel() {
  box.textContent = "";
  curSig = "";
  const c = el("div", "card");
  const hd = el("div", "hd");
  hd.appendChild(el("div", "sk sk-ic"));
  const tx = el("div", "htx");
  tx.appendChild(el("div", "sk sk-l1"));
  tx.appendChild(el("div", "sk sk-l2"));
  hd.appendChild(tx);
  c.appendChild(hd);
  c.appendChild(el("div", "sk sk-code"));
  c.appendChild(el("div", "sk sk-btn"));
  box.appendChild(c);
}
function eyeSvg(off) {
  const NS = "http://www.w3.org/2000/svg";
  const sv = document.createElementNS(NS, "svg");
  sv.setAttribute("viewBox", "0 0 24 24");
  sv.setAttribute("width", "18");
  sv.setAttribute("height", "18");
  sv.setAttribute("fill", "none");
  sv.setAttribute("stroke", "currentColor");
  sv.setAttribute("stroke-width", "2");
  sv.setAttribute("stroke-linecap", "round");
  sv.setAttribute("stroke-linejoin", "round");
  const D = ["M10 12a2 2 0 1 0 4 0a2 2 0 0 0-4 0", "M21 12c-2.4 4-5.4 6-9 6c-3.6 0-6.6-2-9-6c2.4-4 5.4-6 9-6c3.6 0 6.6 2 9 6"];
  if (off) D.push("M3 3l18 18");
  D.forEach(function (d) {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d);
    sv.appendChild(p);
  });
  return sv;
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
function card(title, sub, code, expired, kind) {
  const c = el("div", "card");
  const hd = el("div", "hd");
  hd.appendChild(icon(kind));
  const tx = el("div", "htx");
  tx.appendChild(el("div", "ctitle", title));
  const s = el("div", "csub", sub);
  tx.appendChild(s);
  hd.appendChild(tx);
  c.appendChild(hd);
  const codeEl = el("div", "code" + (expired ? " dim" : ""));
  setCode(codeEl, code);
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
    const c = card(e.service || "Email code", emailSub(e), e.code, e.expired, "mail");
    emailCard = c;
    c.codeEl.classList.add("em");
    box.appendChild(c.node);
    n++;
  }
  (d.tfa || []).forEach((t) => {
    const c = card(t.name + " 2FA", "", t.code, false, "shield");
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
    if (it.c.code !== t.code) { it.c.code = t.code; setCode(it.c.codeEl, t.code); }
  });
}
function patch(d, t1) {
  if (d.email && emailCard) {
    emailCard.sub.textContent = emailSub(d.email);
    emailCard.codeEl.classList.toggle("dim", !!d.email.expired);
  }
  patchTfa(d.tfa, t1);
}

function setStatus(on) {
  hostEl.textContent = "";
  hostEl.appendChild(el("span", "dot " + (on ? "on" : "off")));
  if (tab && tab.favIconUrl && /^(https?|data):/.test(tab.favIconUrl)) {
    const fi = document.createElement("img");
    fi.className = "fav";
    fi.alt = "";
    fi.src = tab.favIconUrl;
    fi.addEventListener("error", () => fi.remove());
    hostEl.appendChild(fi);
  }
  hostEl.appendChild(document.createTextNode((on ? "আনলক" : "লক") + (host ? " · " + host : "")));
}
function setFoot(on) {
  setStatus(on);
  if (!on) { if (foot) { foot.remove(); foot = null; } return; }
  if (foot) return;
  foot = el("div", "foot");
  const row = el("div", "frow");
  if (host && !noBox) {
    const rf = el("button", "btn ref", "↻ রিফ্রেশ");
    rf.addEventListener("click", () => load(pageKind === "email" ? "refresh_email" : pageKind === "tfa" ? "tfa" : "refresh", rf));
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
  if (!host) { note(NOPAGE); setFoot(true); } else if (noBox) { note(NOBOX); setFoot(true); } else load(pageKind);
}
function showLogin(msg) {
  tfaItems = [];
  emailCard = null;
  box.textContent = "";
  const c = el("div", "card");
  const lh = el("div", "hd");
  lh.appendChild(icon("lock"));
  lh.appendChild(el("div", "ctitle", "লগইন করুন"));
  c.appendChild(lh);
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
    b.textContent = "আনলক হচ্ছে…";
    err.textContent = "";
    const r = await chrome.runtime.sendMessage({ type: "login", password: pw });
    inp.value = "";
    b.disabled = false;
    b.textContent = "আনলক করুন";
    if (r && r.ok) { note("কোড খোঁজা হচ্ছে… একটু অপেক্ষা করুন"); afterUnlock(); return; }
    if (r && r.error) err.textContent = r.error;
    else if (r && r.status === 0) err.textContent = "ড্যাশবোর্ড সার্ভারে পৌঁছানো যায়নি।";
    else err.textContent = "পাসওয়ার্ড ভুল।";
    pwrap.classList.remove("shake");
    void pwrap.offsetWidth;
    pwrap.classList.add("shake");
  };
  b.addEventListener("click", go);
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  const pwrap = el("div", "pwrap");
  pwrap.appendChild(inp);
  const eye = el("button", "eye");
  eye.type = "button";
  eye.setAttribute("aria-label", "পাসওয়ার্ড দেখুন বা লুকান");
  eye.appendChild(eyeSvg(false));
  eye.addEventListener("click", () => {
    const show = inp.type === "password";
    inp.type = show ? "text" : "password";
    eye.textContent = "";
    eye.appendChild(eyeSvg(show));
    inp.focus();
  });
  pwrap.appendChild(eye);
  inp.addEventListener("input", () => pwrap.classList.remove("shake"));
  c.appendChild(pwrap);
  c.appendChild(err);
  c.appendChild(b);
  box.appendChild(c);
  setTimeout(() => { try { inp.focus(); } catch (e) {} }, 30);
}

async function load(mode, btn) {
  if (busy) return;
  busy = true;
  const quiet = mode === "refresh" || mode === "refresh_email" || (mode === "tfa" && tfaItems.length > 0);
  let msg = "";
  if (!quiet && !tfaItems.length && !emailCard) skel();
  if (btn) {
    btn.dataset.t = btn.dataset.t || btn.textContent;
    btn.disabled = true;
    btn.textContent = "চেক হচ্ছে…";
  }
  try {
    const r = await chrome.runtime.sendMessage({ type: "get", host: host, mode: mode, hint: pageHint });
    const t1 = Date.now();
    if (!r || r.status === 0) { if (!quiet) note("ড্যাশবোর্ড সার্ভারে পৌঁছানো যায়নি।"); else msg = "সার্ভার পাইনি"; return; }
    if (r.status === 401) { setFoot(false); showLogin(""); return; }
    if (r.status === 429) { if (!quiet) note("একটু পরে আবার চেষ্টা করুন।"); else msg = "একটু পরে চাপুন"; return; }
    if (!r.ok) { if (!quiet) note((r.data && r.data.error) || "কোড আনা যায়নি।"); else msg = "হয়নি"; return; }
    setFoot(true);
    if (mode === "tfa" && tfaItems.length) patchTfa(r.data.tfa, t1);
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
        setCode(it.c.codeEl, it.next);
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
  let hasBox = false;
  try { const er = await chrome.tabs.sendMessage(tab.id, { type: "email" }); pageHint = (er && er.email) || ""; pageKind = (er && er.kind) || "full"; hasBox = Boolean(er && er.box); } catch (e) {}
  if (!hasBox) {
    noBox = true;
    note(NOBOX);
    const st2 = await chrome.runtime.sendMessage({ type: "status" });
    setFoot(Boolean(st2 && st2.unlocked));
    return;
  }
  load(pageKind);
})();
