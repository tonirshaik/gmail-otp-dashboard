const DASH = "@@ORIGIN@@";
const box = document.getElementById("box");
const hostEl = document.getElementById("host");
let tab = null, host = "", ends = [];

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
async function load() {
  const r = await chrome.runtime.sendMessage({ type: "get", host: host });
  if (!r || r.status === 0) { note("ড্যাশবোর্ড সার্ভারে পৌঁছানো যায়নি।"); return; }
  if (r.status === 401) { note("এক্সটেনশন লক করা আছে। ড্যাশবোর্ডে লগইন করুন, তারপর আবার খুলুন।", true); return; }
  if (r.status === 429) { note("একটু পরে আবার চেষ্টা করুন।"); return; }
  if (!r.ok) { note((r.data && r.data.error) || "কোড আনা যায়নি।"); return; }
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
  if (!u || u.protocol !== "https:") { note("এই পাতায় কাজ করে না। কোনো https সাইটে খুলুন।"); return; }
  host = u.hostname;
  hostEl.textContent = host;
  load();
})();
