const DASH = "@@ORIGIN@@";
const box = document.getElementById("box");
const hostEl = document.getElementById("host");
let tab = null, host = "", ends = [], foot = null;
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
  c.appendChild(el("div", "code" + (expired ? " dim" : ""), fmt(code)));
  const row = el("div", "row");
  const cp = el("button", "btn", "কপি");
  cp.addEventListener("click", () => {
    navigator.clipboard.writeText(code).then(() => flash(cp, "কপি হয়েছে"), () => flash(cp, "হয়নি"));
  });
  const fl = el("button", "btn primary", "অটো-ফিল");
  fl.addEventListener("click", () => fillIt(code, fl));
  row.appendChild(cp);
  row.appendChild(fl);
  c.appendChild(row);
  return { node: c, sub: s };
}
function render(d) {
  box.textContent = "";
  ends = [];
  let n = 0;
  if (d.email) {
    const e = d.email;
    const c = card(e.service || "Email code", e.email + " · " + (e.expired ? "মেয়াদ শেষ · " : "") + ago(e.age), e.code, e.expired);
    box.appendChild(c.node);
    n++;
  }
  (d.tfa || []).forEach((t) => {
    const c = card(t.name + " 2FA", t.label || "", t.code, false);
    c.sub.textContent = (t.label ? t.label + " · " : "");
    const left = el("span", "", "");
    c.sub.appendChild(left);
    ends.push({ at: Date.now() + (t.remaining || 30) * 1000, node: left });
    box.appendChild(c.node);
    n++;
  });
  if (!n) note("এই সাইটের জন্য কোনো কোড পাওয়া যায়নি।");
}

function setFoot(on) {
  if (!on) { if (foot) { foot.remove(); foot = null; } return; }
  if (foot) return;
  foot = el("div", "foot");
  const lk = el("button", "btn", "লক করুন");
  lk.addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "lock" });
    setFoot(false);
    ends = [];
    if (!host) note(NOPAGE); else showLogin("");
  });
  foot.appendChild(lk);
  foot.appendChild(el("div", "fnote", "আনলক আছে · ২৪ ঘণ্টার মধ্যে শেষ হবে"));
  document.body.appendChild(foot);
}
function afterUnlock() {
  if (!host) { note(NOPAGE); setFoot(true); } else load();
}
function showLogin(msg) {
  ends = [];
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

async function load() {
  const r = await chrome.runtime.sendMessage({ type: "get", host: host });
  if (!r || r.status === 0) { note("ড্যাশবোর্ড সার্ভারে পৌঁছানো যায়নি।"); return; }
  if (r.status === 401) { setFoot(false); showLogin(""); return; }
  if (r.status === 429) { note("একটু পরে আবার চেষ্টা করুন।"); return; }
  if (!r.ok) { note((r.data && r.data.error) || "কোড আনা যায়নি।"); return; }
  setFoot(true);
  render(r.data);
}
setInterval(() => {
  if (!ends.length) return;
  const now = Date.now();
  let reload = false;
  ends.forEach((x) => {
    const s = Math.max(0, Math.ceil((x.at - now) / 1000));
    x.node.textContent = "বদলাবে " + s + " সেকেন্ডে";
    if (s <= 0) reload = true;
  });
  if (reload) { ends = []; load(); }
}, 1000);

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
  load();
})();
