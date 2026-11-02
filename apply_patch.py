#!/usr/bin/env python3
import re
import sys
from pathlib import Path

ROOT = Path.cwd()
FILES = {
    "html": ROOT / "templates" / "index.html",
    "css": ROOT / "static" / "midnight.css",
    "js": ROOT / "static" / "app.js",
    "py": ROOT / "app.py",
}
for key, p in FILES.items():
    if not p.exists():
        sys.exit(f"ERROR: {p} not found.")

raw = {k: p.read_bytes().decode("utf-8") for k, p in FILES.items()}
text = {k: v.replace("\r\n", "\n") for k, v in raw.items()}
crlf = {k: ("\r\n" in v) for k, v in raw.items()}


def sub_once(key, pattern, repl, flags=re.DOTALL, what=""):
    new, n = re.subn(pattern, lambda m: repl(m) if callable(repl) else repl, text[key], count=1, flags=flags)
    if n != 1:
        sys.exit(f"ERROR: could not find target for: {what or pattern[:60]} (in {FILES[key].name}). Nothing was changed.")
    text[key] = new


# ---------------- index.html
sub_once("html", r'<i class="fa-solid fa-user-plus"></i> Add Gmail Account',
         '<i class="fa-solid fa-gear"></i> Settings', what="settings title")
sub_once("html", r'[ \t]*<form id="addAccountForm">.*?</form>\n?', "", what="old addAccountForm")
sub_once("html", r'[ \t]*<button type="button" class="btn btn-logout" onclick="logout\(\)">.*?</button>\n?', "", what="logout button")
sub_once("html", r'[ \t]*<button type="button" class="nav-logout" onclick="logout\(\)">.*?</button>\n?', "", what="nav logout button")
sub_once("html", r'(<button[^>]*id="tfaServiceBtn".*?</button>)',
         lambda m: m.group(1)
         + '\n                <button type="button" class="svc-open-btn" id="accAddBtn" title="Add a Gmail account" aria-label="Add account">'
         '<i class="fa-solid fa-plus"></i><span>Add<span class="svc-more"> Account</span></span></button>',
         what="tfaServiceBtn")
MODAL = '''    <!-- Add Gmail account (Accounts page Add button) -->
    <div id="addAccModal" class="modal-overlay hidden">
        <div class="modal-box">
            <div class="popup-title"><i class="fa-solid fa-user-plus"></i> Add Gmail Account</div>
            <form id="addAccountForm">
                <div class="input-group">
                    <input type="email" id="accEmail" placeholder="Gmail Address" required autocomplete="off">
                </div>
                <div class="input-group">
                    <input type="password" id="accPassword" placeholder="16-digit App Password" required autocomplete="off">
                </div>
                <div class="input-group">
                    <input type="password" id="accSettingsPw" placeholder="Settings password" required autocomplete="off">
                </div>
                <div id="addAccError" class="msg-status" style="color: var(--accent-red); margin: 0 0 8px; font-size: 13px;"></div>
                <button type="submit" class="btn btn-primary" id="addAccSubmit">
                    <i class="fa-solid fa-floppy-disk"></i> Save Account
                </button>
            </form>
            <button type="button" class="btn btn-cancel" id="addAccCancelBtn">Cancel</button>
        </div>
    </div>

'''
sub_once("html", r'([ \t]*<!-- Toast Notification -->)', lambda m: MODAL + m.group(1), what="Toast Notification marker")

# ---------------- midnight.css
sub_once("css", r'/\* phone: settings opens as a sheet.*?\n\s*\}\s*\n\}\n', "", what="phone settings sheet block")
CSS_ADD = '''
/* Settings = full app page (like Accounts / 2FA Services) */
.settings-popup {
  position: fixed;
  inset: 0;
  width: auto;
  max-width: none;
  max-height: none;
  margin: 0;
  border: 0;
  border-radius: 0;
  box-shadow: none !important;
  background: var(--bg-color);
  overflow-y: auto;
  overscroll-behavior: contain;
  z-index: 300;
  animation: none;
  padding: max(22px, env(safe-area-inset-top)) 16px calc(116px + env(safe-area-inset-bottom));
}
.settings-auth,
.settings-content { max-width: 720px; margin: 0 auto; }
.settings-popup .popup-title {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -.4px;
  text-transform: none;
  color: var(--text-main);
  margin-bottom: 16px;
}
.settings-popup .popup-title i { color: var(--m-accent); }
@media (min-width: 1024px) {
  .settings-popup { left: 252px; padding: 36px 44px 48px; }
  .settings-popup .popup-title { font-size: 28px; }
}
'''
text["css"] = text["css"].rstrip("\n") + "\n" + CSS_ADD

# ---------------- app.js
JS_NEW = (
    'const addAccModal=document.getElementById("addAccModal"),addAccError=document.getElementById("addAccError");'
    'function closeAddAcc(){addAccModal.classList.add("hidden"),addAccountForm.reset(),addAccError.style.display="none"}'
    'document.getElementById("accAddBtn").addEventListener("click",()=>{addAccError.style.display="none",addAccModal.classList.remove("hidden"),setTimeout(()=>document.getElementById("accEmail").focus(),50)}),'
    'document.getElementById("addAccCancelBtn").addEventListener("click",closeAddAcc),'
    'addAccModal.addEventListener("click",e=>{e.target===addAccModal&&closeAddAcc()}),'
    'document.addEventListener("keydown",e=>{"Escape"===e.key&&!addAccModal.classList.contains("hidden")&&(e.stopPropagation(),closeAddAcc())},!0),'
    'addAccountForm.addEventListener("submit",async e=>{e.preventDefault();'
    'let t=document.getElementById("addAccSubmit"),a=e=>{addAccError.innerText=e,addAccError.style.display="block"};'
    'addAccError.style.display="none",t.disabled=!0;'
    'try{let n=await fetch("/api/add-account",{method:"POST",headers:{"Content-Type":"application/json"},'
    'body:JSON.stringify({email:document.getElementById("accEmail").value,password:document.getElementById("accPassword").value,settings_password:document.getElementById("accSettingsPw").value})}),'
    'i=await n.json().catch(()=>({}));'
    'n.ok?(closeAddAcc(),showToast(i.message||"Account added!"),loadAccountList(),loadAccountCount()):a(i.error||"Failed")}'
    'catch(l){a("Connection error")}finally{t.disabled=!1}});'
)
sub_once("js",
         r'addAccountForm\.addEventListener\("submit",async e=>\{e\.preventDefault\(\);let t=document\.getElementById\("accEmail"\)\.value.*?showMessage\("Connection error",!0\)\}\}\);',
         lambda m: JS_NEW, what="old addAccountForm submit handler")

# ---------------- app.py
PY_NEW = '''    if not _rate_ok("add-account", 10, 60):
        return jsonify({"error": "Too many tries. Wait a minute."}), 429

    data = _json_body()
    # App Password and Settings password are different: settings password is required every time here
    bad = _settings_pw_check({"password": data.get('settings_password')})
    if bad is not None:
        return bad
    email_input = str(data.get('email') or '').strip().lower()'''
sub_once("py",
         r"""    if not session\.get\('settings_unlocked'\):\s*\n\s*return jsonify\(\{"error": "Settings locked\. Unlock first\."\}\), 403\s*\n\s*\n\s*data = _json_body\(\)\s*\n\s*email_input = str\(data\.get\('email'\) or ''\)\.strip\(\)\.lower\(\)""",
         lambda m: PY_NEW, what="add_account() header")

for key, p in FILES.items():
    out = text[key]
    if crlf[key]:
        out = out.replace("\n", "\r\n")
    p.write_bytes(out.encode("utf-8"))
    print("updated:", p.relative_to(ROOT))
print("DONE")
