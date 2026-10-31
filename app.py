import os
import base64
import struct
import imaplib
import email
import re
import time
import json
import threading
import hmac
import hashlib
import math
import socket
import unicodedata
import select
import queue
import uuid
import gzip
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime, parseaddr
from email.header import decode_header, make_header
import html as html_lib
from urllib.parse import urlparse, parse_qs, unquote
from zoneinfo import ZoneInfo
from flask import Flask, render_template, jsonify, request, session, Response, send_from_directory
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from webauthn import (
    generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response,
    options_to_json,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    PublicKeyCredentialDescriptor, AuthenticatorSelectionCriteria,
    AuthenticatorAttachment, ResidentKeyRequirement, UserVerificationRequirement,
)

app = Flask(__name__)

# ---------- App version (home page-er title-er pashe + "New version available" banner-e dekhay) ----------
# Niyom (MAJOR.MINOR.PATCH) -- auto: main-e merge holei GitHub Action VERSION file bere dey, hate bodlano lage na.
#   1.0.0 -> prothom release        1.0.1 -> chhoto bug fix
#   1.1.0 -> notun feature          2.0.0 -> boro poriborton
try:                                    # VERSION file-ta Action nije bodlay (scripts/bump_version.py)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'VERSION')) as _vf:
        APP_VERSION = _vf.read().strip() or "0.0.0"
except OSError:
    APP_VERSION = "0.0.0"
app.jinja_env.globals['app_version'] = APP_VERSION
# Shudhu nijer site (ORIGIN) cookie-shoho API dakte parbe. Age shob site-ke allow kora chilo.
# ORIGIN niche (Passkey config) thake -- ekhane age-i pore nei.
CORS(app, supports_credentials=True, origins=[os.environ.get("ORIGIN", "https://gmail-otp-dashboard.onrender.com")])
# Render proxy-r pichone thake, tai asol client IP pete lage (lockout-er jonno)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# Session Secure Key & Passwords -- shob Render-er Environment theke ashbe, code-e kono default nai
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    print("WARNING: SECRET_KEY set kora nai! Ekta temporary key use hocche (restart dile sobai logout hobe).")
    SECRET_KEY = os.urandom(32).hex()
app.secret_key = SECRET_KEY

MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "")
SETTINGS_PASSWORD = os.environ.get("SETTINGS_PASSWORD", "")
if not MASTER_PASSWORD:
    print("WARNING: MASTER_PASSWORD set kora nai! Login bondho thakbe.")
if not SETTINGS_PASSWORD:
    print("WARNING: SETTINGS_PASSWORD set kora nai! Settings unlock / delete bondho thakbe.")
MONGO_URI = os.environ.get("MONGO_URI", "")

# Passkey (fingerprint / face) config
RP_ID = os.environ.get("RP_ID", "gmail-otp-dashboard.onrender.com")
RP_NAME = "Gmail OTP Dashboard"
ORIGIN = os.environ.get("ORIGIN", "https://gmail-otp-dashboard.onrender.com")
OWNER_ID = b"owner"

# Web Push (notification -- tab / app bondho thakleo). Shob Render-er Environment theke:
#   VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY  (generate_vapid_keys.py chalaye pabe), VAPID_SUBJECT (mailto:tomar@email)
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "").strip() or ORIGIN
try:
    from pywebpush import webpush, WebPushException
except Exception:                       # pywebpush install na thakle push bondho, baki app thik cholbe
    webpush = None
    WebPushException = Exception

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# MongoDB Connection
db = None
accounts_collection = None
passkeys_collection = None
security_collection = None
prefs_collection = None
push_collection = None
totp_collection = None
svc_collection = None

if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI)
        db = client['gmail_otp_db']
        accounts_collection = db['accounts']
        passkeys_collection = db['passkeys']
        security_collection = db['security']   # login lock-er state (shob worker-e eki)
        prefs_collection = db['prefs']         # pin-er moto preference (shob device-e eki)
        push_collection = db['push_subs']      # notification subscription (device-wise)
        totp_collection = db['totp']           # Gmail-wise 2FA (Authenticator) secret -- ENCRYPTED
        svc_collection = db['totp_services']   # onno shob service-er (social media / website / app) 2FA secret -- ENCRYPTED
        print("MongoDB Connected Successfully!")
    except Exception as e:
        print(f"MongoDB Connection Error: {e}")

def _json_body():
    """Request body JSON object (dict). Body na thakle / list / vul type hole {} -- tokhon 500 na diye normal 400 error jay."""
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}

def load_accounts():
    if accounts_collection is not None:
        try:
            accounts = list(accounts_collection.find({}, {'_id': 0}))
            for a in accounts:
                a['password'] = pw_decrypt(a.get('email'), a.get('password'))
            return accounts
        except Exception as e:
            print(f"Error loading accounts from Mongo: {e}")
            return []
    return []

# ======================= Password check + lockout =======================
# 3 bar vul password dile 10 minute lock.
# Lock "global" -- IP/worker jai hok, lock thakle sothik password-o kaj korbe na.
# State MongoDB-te thake (jate gunicorn-er ek-er besi worker/restart-e-o lock thake).
# Fingerprint/Face login lock-er bhitore pore na.
MAX_FAILS = 3
# Login lock ekhon IP onujayi: onno keu vul password dile tumi lock hobe na. Shudhu ekoi IP theke 3 bar vul hole oi IP 10 min lock.
# Backstop: onek IP ghure ghure guess korle thamate, shob IP mila LOGIN_GLOBAL_MAX bar vul hole shobar login 10 min lock (passkey cholbe).
LOGIN_GLOBAL_MAX = 30
LOCK_SECONDS = 10 * 60
_fail_state = {}                 # Mongo na thakle / error hole fallback (shudhu ei process-e)
_fail_lock = threading.Lock()

def pw_equal(given, expected):
    if not expected:             # env set kora nai -> kono password-i match korbe na
        return False
    try:
        return hmac.compare_digest(str(given).encode("utf-8"), expected.encode("utf-8"))
    except Exception:
        return False

def _mem_get(bucket):
    return _fail_state.setdefault(bucket, {"fails": 0, "last": 0, "locked_until": 0})

def lock_remaining(bucket):
    """Lock thakle koto sekend baki, nahole 0."""
    now = time.time()
    if security_collection is not None:
        try:
            doc = security_collection.find_one({"_id": bucket})
            if not doc:
                return 0
            if doc.get("locked_until", 0) > now:
                return int(doc["locked_until"] - now) + 1
            if doc.get("locked_until") or now - doc.get("last", 0) > LOCK_SECONDS:
                security_collection.delete_one({"_id": bucket})   # lock sesh -> notun kore shuru
            return 0
        except Exception as e:
            print(f"Lock read error (memory fallback): {e}")
    with _fail_lock:
        st = _mem_get(bucket)
        if st["locked_until"] > now:
            return int(st["locked_until"] - now) + 1
        if st["locked_until"] or now - st["last"] > LOCK_SECONDS:
            st.update({"fails": 0, "last": 0, "locked_until": 0})
        return 0

def register_fail(bucket, max_fails=MAX_FAILS):
    """Vul password gona hoy. (lock-er sekend, baki chance) ferot dey."""
    now = time.time()
    if security_collection is not None:
        try:
            security_collection.update_one(
                {"_id": bucket},
                {"$inc": {"fails": 1}, "$set": {"last": now}, "$setOnInsert": {"locked_until": 0}},
                upsert=True,
            )
            doc = security_collection.find_one({"_id": bucket}) or {}
            fails = doc.get("fails", 1)
            if fails >= max_fails:
                security_collection.update_one(
                    {"_id": bucket},
                    {"$set": {"locked_until": now + LOCK_SECONDS, "fails": 0}},
                )
                return LOCK_SECONDS, 0
            return 0, max_fails - fails
        except Exception as e:
            print(f"Lock write error (memory fallback): {e}")
    with _fail_lock:
        st = _mem_get(bucket)
        st["fails"] += 1
        st["last"] = now
        if st["fails"] >= max_fails:
            st["locked_until"] = now + LOCK_SECONDS
            st["fails"] = 0
            return LOCK_SECONDS, 0
        return 0, max_fails - st["fails"]

def clear_fails(bucket):
    if security_collection is not None:
        try:
            security_collection.delete_one({"_id": bucket})
        except Exception as e:
            print(f"Lock clear error: {e}")
    with _fail_lock:
        _fail_state.pop(bucket, None)

def _login_bucket():
    """Ei client-er IP-r login lock bucket (ProxyFix-er pore asol IP)."""
    return "login:" + str(request.remote_addr or "unknown")[:60]

def locked_response(secs):
    mins = max(1, math.ceil(secs / 60))
    return jsonify({
        "error": f"Too many wrong attempts. Try again in {mins} minute(s).",
        "locked": True,
        "retry_after": secs,
    }), 429

def wrong_password_response(bucket, msg):
    locked, left = register_fail(bucket)
    if locked:
        return locked_response(locked)
    return jsonify({"error": f"{msg} ({left} attempt(s) left)"}), 401


# ======================= OTP / Code extractor (v2) =======================
# Ekta email-er text theke shob dhoroner code (OTP, PIN, verification code,
# security code, login code, passcode ...) khuje ber kore.
# Prottek candidate-ke "score" deya hoy: kache keyword ache kina, nijer line-e
# ache kina, address/order/price er moto jinisher kache ache kina.

_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad"), None)
# Bangla, Hindi, Arabic, Thai, Tamil ... shob lipir digit -> 0-9
_DIGIT_BASES = (0x0660, 0x06F0, 0x07C0, 0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66,
                0x0CE6, 0x0D66, 0x0DE6, 0x0E50, 0x0ED0, 0x0F20, 0x1040, 0x1090, 0x17E0, 0x1810, 0xFF10)
_BN = {base + i: str(i) for base in _DIGIT_BASES for i in range(10)}

_CJK_PUNCT = {ord(c): c + " " for c in "，。、；：！？｡､"}

def _normalize(text):
    """Invisible char bad, fullwidth (１２３ / ：) -> normal, onno lipir digit -> 0-9,
    ar non-English lekha (验证码123456) r sathe digit lege thakle majhe space dey."""
    t = text.translate(_ZW).translate(_CJK_PUNCT)          # ， 。 ： -> ", " ". " ": " (porer digit alada thakbe)
    t = unicodedata.normalize("NFKC", t).translate(_BN)
    return re.sub(r"(?<=[^\x00-\x7f])(?=[0-9])|(?<=[0-9])(?=[^\x00-\x7f])", " ", t)

# Ek-ek character-er line (HTML-er prottek digit alada box/cell) -> ek line-e jora lage
_SINGLE_CHAR_LINES = re.compile(r"(?m)^[A-Za-z0-9](?:\n[A-Za-z0-9]){3,9}$")

_STRONG_KW = re.compile(r"""(?ix)
    \botp\b | \bpass\s?code\b | \bpin\b | ওটিপি | পিন |
    one[-\s]?time(?:\s+(?:password|passcode|pin|code))? |
    (?:verification|verify|confirmation|security|authentication|authorization|authenticator|
       access|login|log[-\s]?in|sign[-\s]?in|activation|registration|reset|recovery|
       temporary|temp|2fa|two[-\s]?factor|mfa)\s*(?:code|pin|passcode|password|key|token) |
    (?:ভেরিফিকেশন|ভেরিফাই|যাচাই|নিরাপত্তা|সিকিউরিটি|লগইন)\s*কোড | ভেরিফিকেশন |
    (?:validation|single[-\s]?use|launch|magic|secret|unlock|guard|device|app|sms|text|mobile|email|e-mail|
       account|sign[-\s]?up|signup|one[-\s]?time|use[-\s]?once|temporary)[-\s]?(?:code|pin|passcode|password) |
    (?:ওটিপি|ওয়ান[-\s]?টাইম\s*পাসওয়ার্ড) |
    c[oó]digo\s+de\s+\w+ | clave\s+(?:de\s+)?(?:un\s+solo\s+uso|verificaci[oó]n|acceso|seguridad) |
    code\s+de\s+(?:v[ée]rification|s[ée]curit[ée]|confirmation|connexion|validation) |
    mot\s+de\s+passe\s+(?:unique|[àa]\s+usage\s+unique) | einmalpasswort |
    (?:best[äa]tigungs|sicherheits|verifizierungs|einmal|anmelde|login|sms|tan)[-\s]?code |
    codice\s+(?:di\s+)?(?:verifica|sicurezza|conferma|accesso|monouso) |
    (?:doğrulama|güvenlik|onay|giriş|tek\s+kullanımlık)\s+(?:kodu|şifresi|şifre) |
    kode\s+(?:verifikasi|otp|keamanan|konfirmasi|masuk|sekali\s+pakai|pin) |
    mã\s+(?:xác\s+(?:minh|thực)|otp|bảo\s+mật|pin) |
    код\s+(?:подтверждения|безопасности|верификации|входа|авторизации|активации|проверки) |
    одноразовый\s+(?:код|пароль) |
    (?:验证|驗證|校验|校驗|动态|動態|认证|認證|确认|確認|安全|登录|登錄)[码碼] |
    (?:確認|認証|認證|ワンタイム|セキュリティ|ログイン)\s*(?:コード|番号|パスワード) |
    인증\s*(?:번호|코드) | (?:확인|보안|일회용)\s*(?:코드|번호|비밀번호) |
    ओटीपी | (?:सत्यापन|वेरिफिकेशन|वेरिफाई|सुरक्षा)\s*कोड | वन[-\s]?टाइम\s*पासवर्ड |
    رمز\s*(?:التحقق|التأكيد|الأمان|الدخول|المرور|التفعيل) |
    كلمة\s*(?:المرور|السر)\s*لمرة\s*واحدة
""")
_WEAK_KW = re.compile(r"(?i)\bcode\b|\btoken\b|কোড|\bcódigo\b|\bcodice\b|\bkod\b|\bcodigo\b|\bкод\b|\bkode\b|\bkodu\b|\bmã\b|验证|驗證|コード|코드|कोड|رمز")

_NEG = re.compile(
    r"(?i)\b(?:order|invoice|tracking|receipt|transaction|txn|amount|price|total|balance|zip|postal|"
    r"a/c|street|st\.|avenue|ave|parkway|road|rd\.|suite|floor|apt|copyright|"
    r"usd|bdt|inr|eur|tk|ref|reference|payment|paid|due|bill|flight|booking|ticket|version|build)\b|[$€£৳%©]")
# phone / call / tel / mobile: shudhu code-er thik pashe thakle (phone: 01712.., Call 555-0100) shastir karon.
# Dur-e thakle ("verify your phone number", "Call us if ...") code-er score kombe na.
_NEG_PHONE = re.compile(r"(?i)\b(?:phone|tel|call|mobile)\b")
_PROMO_BEFORE = re.compile(
    r"(?i)(?:promo(?:tion(?:al)?)?|coupon|discount|referral|invite|gift|voucher|zip|postal|post|area|"
    r"country|dial|error|status|tracking|response|order|invoice|reference|ref|case|ticket|booking|"
    r"flight|customer|member)\s*(?:code|number|no\.?|id|#)?\s*[:#-]?\s*$")
_MARKETING = re.compile(
    r"(?i)discount|coupon|promo|checkout|\bsale\b|\bdeal\b|% off|\boff\b|free shipping|\bsave\b|"
    r"shop now|buy now|cashback|voucher")
_INTENT = re.compile(
    r"(?i)sign[-\s]?in|log[-\s]?in|\bverify\b|verification|\bconfirm|authenticate|do not share|"
    r"don't share|never share|expires? in|valid for|will expire|within \d+ minutes?|শেয়ার করবেন না")
_AFTER = re.compile(r"(?i)^\s*(?:is|as|are|was)\b[^\n]{0,70}?\b(?:code|otp|pin|passcode|password|কোড)\b")
_LABEL = re.compile(r"(?i)(?:\bis|\bare|:|=|#|[-–—])\s*$")
_IMPER = re.compile(r"(?i)\b(?:enter|type|input|use|submit|provide)\b[^\n]{0,40}$")

_LB = r"(?<![\w#$@.,/+=&?-])"
_CAND = [
    ("spaced_alnum", re.compile(_LB + r"([A-Za-z0-9](?:[ \t][A-Za-z0-9]){3,9})(?![\w@.]|[ \t][A-Za-z0-9])")),
    ("spaced",  re.compile(_LB + r"(\d(?:[ \t]\d){3,7})(?![\w@.]|[ \t]\d)")),
    ("grouped", re.compile(_LB + r"(\d{2,4}(?:[ -]\d{2,4}){1,3})(?![\w@]|[.,:/-]\d|[ -]\d)")),
    ("grouped_alnum", re.compile(_LB + r"((?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d|[A-Z]{4,5}-[A-Z]{4,5})[A-Za-z0-9]{3,6}(?:-[A-Za-z0-9]{3,6}){1,3})(?![\w@]|-)")),
    ("dashed",  re.compile(_LB + r"([A-Za-z]{1,3}-(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{4,10})(?![\w@]|-)")),
    ("digits",  re.compile(_LB + r"(\d{4,10})(?![\w@]|[.,:/-]\d)")),
    ("alnum",   re.compile(_LB + r"((?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{4,12})(?![\w@]|[.,:/-]\w)")),
]
# CSS / unit jinish (48px, 12pt, 300ms ...) kokhono code na
_UNIT = re.compile(
    r"(?i)^\d+(?:px|pt|em|rem|ex|ch|vh|vw|vmin|vmax|cm|mm|pc|ms|dpi|dppx|khz|hz|kb|mb|gb|tb|st|nd|rd|th|am|pm)$")
_CSS_CTX = re.compile(
    r"(?i)(?:font(?:-size|-family|-weight)?|padding|margin|width|height|line-height|border|background|color|letter-spacing)\s*:")

_LETTERS = re.compile(
    r"(?i:\b(?:otp|pass\s?code|pin|code|verification\s+code|security\s+code)\b)\s*(?:is\b)?\s*[:=\-–]?\s*\b([A-Za-z]{4,10})\b")
_COMMON_WORDS = set("""valid invalid expired incorrect required sent ready available below above attached active correct wrong
used needed missing pending confirmed verified approved denied rejected blocked locked disabled enabled changed updated shown
displayed provided generated included please enter following another different unique secure private personal temporary usable
unused about after again also been before being could each every first from have here into just like made make many more most
must never only other over same should some still such than that their them then there these they this those through under
until very what when where which while will with would your email phone number account password login verify verification
security confirm confirmation google apple microsoft facebook instagram telegram tiktok whatsapp twitter github amazon paypal
linkedin sorry thanks thank hello regards cheers welcome access request requested code codes token unavailable allowed
complete completed failed success successful check click paste copy type typed write written""".split())

_LETTER_STOP = {"CODE", "EMAIL", "YOUR", "THIS", "THAT", "WITH", "FROM", "HERE", "LOGIN", "VERIFY",
                "VALID", "EXPIRES", "GOOGLE", "GITHUB", "ACCOUNT", "PLEASE", "ENTER", "COPY", "PASTE",
                "SIGN", "NOTE", "HELLO", "DEAR", "THANK", "THANKS", "TEAM", "EXPIRE", "MINUTES", "NUMBER", "BELOW", "ABOVE", "SENT", "ONLY", "USING", "AGAIN", "NEVER",
                "SHARE", "VALID", "WITHIN", "ABOUT", "WHICH", "THEN", "WILL", "HAVE", "BEEN", "THEY"}


_LABEL_PREFIXES = {"G", "FB", "F", "IG", "TG", "WA", "TT", "X", "M", "MS", "GH", "AP", "LI"}

def _clean_output(kind, raw):
    """Candidate -> dashboard-e dekhanor code."""
    if kind in ("spaced", "spaced_alnum"):
        return re.sub(r"\s+", "", raw)
    if kind == "grouped":                                  # 482-913 / 482 913 -> 482913
        return re.sub(r"\D", "", raw)
    if kind == "dashed":                                   # G-482913 / FB-84921 -> 482913
        pre, _, rest = raw.partition("-")
        if pre.upper() in _LABEL_PREFIXES and rest.isdigit():
            return rest
    return raw


def _cand_ok(kind, raw):
    if kind == "grouped":
        n = len(re.sub(r"\D", "", raw))
        if n < 4 or n > 10:
            return False
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):          # tarikh (2026-10-09)
            return False
    return True


def _find_keywords(t):
    kws, strong_spans = [], []
    for m in _STRONG_KW.finditer(t):
        kws.append((m.start(), m.end(), True))
        strong_spans.append((m.start(), m.end()))
    for m in _WEAK_KW.finditer(t):
        if any(a <= m.start() and m.end() <= b for a, b in strong_spans):
            continue
        kws.append((m.start(), m.end(), False))
    return kws


# Thikana-r sonkha (1600 Amphitheatre Parkway / CA 94043) kokhono code na
_STREET_AFTER = re.compile(
    r"^\s+(?:[A-Z][\w.'-]*\s+){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Parkway|Pkwy|Way|Blvd|Boulevard|Lane|Ln|Drive|Dr|"
    r"Court|Ct|Plaza|Square|Sq|Highway|Hwy|Suite|Floor)\b")
_US_STATES = ("AL AK AZ AR CA CO CT DE FL GA IL IA KS KY LA MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH PA RI SC "
              "SD TN TX UT VT VA WA WV WI WY DC").split()
_ZIP_BEFORE = re.compile(r"\b(?:" + "|".join(_US_STATES) + r")\s+$")


def _score_candidate(t, kws, s, e, kind, text, has_intent):
    if kind in ("alnum", "dashed") and _UNIT.match(text):
        return -99
    if _CSS_CTX.search(t[max(0, s - 60):min(len(t), e + 30)]):
        return -99                                   # CSS-er bhitore, email-er code na

    line_start = t.rfind("\n", 0, s) + 1
    nl = t.find("\n", e)
    line_end = len(t) if nl == -1 else nl
    before_line = t[line_start:s]
    after_line = t[e:line_end]
    own_line = re.fullmatch(r"[\W_]*" + re.escape(text) + r"[\W_]*", t[line_start:line_end]) is not None

    if kind == "digits":
        if _STREET_AFTER.match(after_line):
            return -99
        if len(text) == 5 and _ZIP_BEFORE.search(before_line):
            return -99

    # ager text ta jodi promo/order/zip er moto hoy, ta code na
    if _PROMO_BEFORE.search(before_line[-40:]):
        return -99

    # ---- keyword kotota kache ----
    kw_val, strong_hit = 0, False
    for ks, ke, strong in kws:
        if ke > s:
            continue
        d = s - ke
        between_nl = t.count("\n", ke, s)
        if ks >= line_start:                         # ek-i line-e
            v = (7 if strong else 4) if d <= 30 else ((5 if strong else 2) if d <= 80 else 0)
        elif between_nl <= 1 and d <= 40:            # ager line-er shesh-e
            v = 6 if strong else 3
        elif own_line and d <= 250:                  # code nijer line-e, keyword upore
            v = 5 if strong else 3
        else:
            v = 0
        if v > kw_val:
            kw_val, strong_hit = v, strong

    if _AFTER.match(after_line):                     # "123456 is your ... code"
        if 7 > kw_val:
            kw_val, strong_hit = 7, True

    had_kw = kw_val > 0                              # asol keyword (code/otp...) paoa gelo, intent-er fallback na
    if kw_val == 0 and has_intent:                   # keyword nai, kintu sign-in/verify jatiyo kotha ache
        stripped = before_line.rstrip().lower()
        if own_line or stripped.endswith(("use", "enter", "type", "is", ":")) or (kind == "digits" and len(text) == 6):
            kw_val = 4

    if kw_val == 0:
        return -99

    score = kw_val
    if _LABEL.search(before_line):
        score += 2
    if _IMPER.search(before_line):
        score += 2
    if own_line:
        score += 2
    if kind in ("spaced", "spaced_alnum", "grouped", "grouped_alnum"):
        score += 2
    if not before_line.strip() and t[:line_start].rstrip().endswith(":"):
        score += 2                       # "Code:" ager line-e, code porer line-e
    if has_intent:
        score += 1
    if kind == "digits" and len(text) == 6:
        score += 1

    # ---- shastir hisab ----
    window = t[max(0, s - 45):min(len(t), e + 45)]
    n_neg = len(_NEG.findall(window))
    n_neg += len(_NEG_PHONE.findall(t[max(0, s - 14):s] + " " + t[e:e + 4]))
    if strong_hit:
        score -= min(n_neg, 2)
    elif had_kw and kw_val >= 4 and _LABEL.search(before_line):
        score -= min(n_neg, 1)       # "Your code is 482913. Order total $45" -- code-er thik age label, tai pashe order/total thakleo kom shasti
    else:
        score -= min(3 * n_neg, 9)

    if not strong_hit and _MARKETING.search(t[max(0, s - 80):min(len(t), e + 80)]):
        score -= 5

    if kind == "digits":
        if len(text) == 4 and 1990 <= int(text) <= 2035 and kw_val < 7:
            score -= 4
        if re.fullmatch(r"(\d)\1+", text):
            score -= 2 if strong_hit else 8
    return score


def extract_otp_scored(text):
    """(code, score) ferot dey. Code na pele ("Code not found", 0). Score jototuku boro, toto nishchit."""
    if not text:
        return "Code not found", 0

    t = _normalize(text)
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)      # link-er bhitorer token bad
    t = re.sub(r"\S+@\S+\.\S+", " ", t)               # email address bad
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{2,}", "\n", t)
    t = _SINGLE_CHAR_LINES.sub(lambda m: m.group(0).replace("\n", " "), t)

    kws = _find_keywords(t)
    if not kws and not _INTENT.search(t):
        return "Code not found", 0
    has_intent = bool(_INTENT.search(t))

    cands = []
    for kind, rx in _CAND:
        for m in rx.finditer(t):
            cands.append((m.start(1), m.end(1), kind, m.group(1)))
    cands = [c for c in cands if _cand_ok(c[2], c[3])]
    wide = [(s, e) for s, e, k, _ in cands if k in ("spaced", "spaced_alnum", "grouped", "grouped_alnum", "dashed")]
    cands = [c for c in cands
             if c[2] in ("spaced", "spaced_alnum", "grouped", "grouped_alnum", "dashed")
             or not any(ws <= c[0] and c[1] <= we for ws, we in wide)]

    best = None   # (score, -start, output)
    accepted = []  # (score, output) -- rival code ache kina dekhar jonno
    for s, e, kind, raw in cands:
        sc = _score_candidate(t, kws, s, e, kind, raw, has_intent)
        out = _clean_output(kind, raw)
        if sc >= 6:
            accepted.append((sc, out))
        if sc >= 6 and (best is None or (sc, -s) > (best[0], best[1])):
            best = (sc, -s, out)

    # keyword-er thik pashe shudhu bornomala-r code (jemon  "code: ABCDEF")
    for m in _LETTERS.finditer(t):
        word = m.group(1)
        s = m.start(1)
        low = word.islower() or word.istitle()
        if word.upper() in _LETTER_STOP:
            continue
        if low:
            gap = m.group(0)[:m.start(1) - m.start()]
            if (len(word) < 5 or word.lower() in _COMMON_WORDS or gap.count("\n") > 1
                    or not re.search(r"(?i)\bis\b|[:=]", gap)):
                continue
        if _PROMO_BEFORE.search(t[max(0, m.start() - 40):s]):
            continue
        if _MARKETING.search(t[max(0, s - 80):s + 80]):
            continue
        lsc = 6 if low else 8                       # lowercase-only: kom nishchit -> card-e Verify sotorko-chinho
        if best is None or (lsc, -s) > (best[0], best[1]):
            best = (lsc, -s, word)

    if not best:
        return "Code not found", 0
    score = best[0]
    # ekoi email-e ar ekta alada code kache-kachi score pele (jemon "code" ar "backup code") nishchit kom
    if any(o != best[2] and sc >= score - 2 for sc, o in accepted):
        score -= 5
    return best[2], score


# ======================= Code-er asol meyad (email theke pora) =======================
# Email-e \"valid for 10 minutes\" / \"expires in 5 minutes\" / \"১০ মিনিটের মধ্যে\" lekha thakle
# server seta pore prottek code-er nijer meyad boshay. Na thakle default (OTP_TTL_SECONDS).
TTL_MIN_SECONDS = 30            # eta-r kom hole dhore nebo email-e vul/onno kichu lekha
TTL_MAX_SECONDS = 24 * 3600     # eta-r besi hole dhore nebo OTP-er meyad na

_TTL_NUM = r"(\d{1,4}(?:\.\d+)?)"
_TTL_UNIT = r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|সেকেন্ড|মিনিট|ঘণ্টা|ঘন্টা)"
_TTL_PATTERNS = [
    # valid for 10 minutes / good for 10 minutes / active for the next 10 min
    re.compile(r"(?i)\b(?:valid|good|active|usable|available|effective)\s+(?:for|within|during)\s+(?:the\s+)?(?:next\s+|only\s+|about\s+|approximately\s+)*"
               + _TTL_NUM + r"[\s-]*" + _TTL_UNIT),
    # expires in 5 minutes / will expire after 5 min / expiring within 5 minutes
    re.compile(r"(?i)\bexpir(?:es?|ed|ing|ation|y)\b[^\n.]{0,25}?\b(?:in|after|within)\s+(?:the\s+)?(?:next\s+|about\s+|approximately\s+)*"
               + _TTL_NUM + r"[\s-]*" + _TTL_UNIT),
    # expiration time: 10 minutes
    re.compile(r"(?i)\bexpir(?:ation|y)(?:\s+time)?\s*[:=-]\s*" + _TTL_NUM + r"[\s-]*" + _TTL_UNIT),
    # use it within 10 minutes / enter within the next 10 minutes
    re.compile(r"(?i)\bwithin\s+(?:the\s+)?(?:next\s+)?" + _TTL_NUM + r"[\s-]*" + _TTL_UNIT),
    # validity: 10 minutes / 10 minutes validity
    re.compile(r"(?i)\bvalidity(?:\s+period)?\s*[:=-]?\s*" + _TTL_NUM + r"[\s-]*" + _TTL_UNIT),
    # (Bangla) মেয়াদ ১০ মিনিট / বৈধ থাকবে ১০ মিনিট / কার্যকর থাকবে ...
    re.compile(r"(?:মেয়াদ|বৈধ|কার্যকর|ভ্যালিড)[^\n]{0,30}?" + _TTL_NUM + r"\s*" + _TTL_UNIT),
    # (Bangla) ১০ মিনিটের মধ্যে / ১০ মিনিট পর্যন্ত
    re.compile(_TTL_NUM + r"\s*" + _TTL_UNIT + r"(?:ের)?\s*(?:মধ্যে|পর্যন্ত|পরে)"),
]

def _unit_seconds(unit):
    u = unit.lower()
    if u.startswith(("sec", "সেকেন্ড")):
        return 1
    if u.startswith(("min", "মিনিট")):
        return 60
    return 3600          # hour / hr / ঘণ্টা / ঘন্টা

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
              "sixty": 60, "ninety": 90}

def _words_to_digits(t):
    t = re.sub(r"(?i)\bhalf an hour\b", "30 minutes", t)
    t = re.sub(r"(?i)\b(?:an|a)\s+(hour|minute)\b", r"1 \1", t)
    return re.sub(r"(?i)\b(" + "|".join(_NUM_WORDS) + r")\b(?=[\s-]*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b)",
                  lambda m: str(_NUM_WORDS[m.group(1).lower()]), t)


def extract_ttl(text):
    """Email-er text theke code-er meyad (sekende) ber kore. Na pele None.
    Ekadhik meyad paile shobcheye chhotota (nirapod dik)."""
    if not text:
        return None
    t = _normalize(text)
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)
    t = re.sub(r"\s+", " ", t)
    t = _words_to_digits(t)
    found = []
    for rx in _TTL_PATTERNS:
        for m in rx.finditer(t):
            try:
                secs = int(round(float(m.group(1)) * _unit_seconds(m.group(2))))
            except (ValueError, IndexError):
                continue
            if TTL_MIN_SECONDS <= secs <= TTL_MAX_SECONDS:
                found.append(secs)
    return min(found) if found else None


def html_to_text(html_body):
    t = re.sub(r"(?s)<!--.*?-->", " ", html_body)          # HTML comment (mso CSS ityadi) bad
    t = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</(?:p|div|tr|li|h[1-6]|table|td|th)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_lib.unescape(t).replace("\xa0", " ")
    return t


def _decode_part(part):
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    for cs in (part.get_content_charset(), "utf-8", "latin-1"):
        try:
            return payload.decode(cs or "utf-8", errors="ignore")
        except (LookupError, TypeError):
            continue
    return ""


def get_email_texts(msg):
    """Email theke text/plain ar html-text, dutoi ber kore (jeta-te code pawa jay)."""
    plain, html_body = "", ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            decoded = _decode_part(part)
        except Exception:
            continue
        if not decoded:
            continue
        if ctype == "text/plain" and not plain:
            plain = decoded
        elif ctype == "text/html" and not html_body:
            html_body = decoded
    texts = []
    if plain.strip():
        texts.append(plain)
    if html_body.strip():
        texts.append(html_to_text(html_body))
    return texts


def decode_hdr(value, default=""):
    if not value:
        return default
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


# Ager email-er result jomiye rakha hoy, jate bar bar same email download na hoy.
# Shudhu notun email ashle seta-i download hobe.
EMAIL_CACHE = {}

# Koyta shesh email dekhe code khujbe (kom dile download kom hobe)
INBOX_SCAN_LIMIT = 5
SPAM_SCAN_LIMIT = 3
EMAIL_CACHE_MAX = 3000

# Home-e code koto sekende porjonto dekhabe (client eta use kore). Total Gmail list-e shesh code always thake.
CONF_HIGH_SCORE = 7       # er niche score hole (ba rival code thakle) card-e Verify sotorko-chinho dekhay
OTP_TTL_SECONDS = 120   # 2 minutes -- shudhu tokhon, jokhon email-e meyad lekha nai

# ======================= Service-er nam (GitHub, Google ...) =======================
_GENERIC_NAME = re.compile(
    r"(?i)\b(?:no[-\s]?reply|noreply|do[-\s]?not[-\s]?reply|notifications?|security|support|team|accounts?|"
    r"alerts?|verification|verify|mailer|info|hello|service|services|login|auth|otp|codes?)\b")
_SLD = {"co", "com", "org", "net", "gov", "edu", "ac", "or", "ne"}
_BRAND = {"github": "GitHub", "linkedin": "LinkedIn", "paypal": "PayPal", "openai": "OpenAI", "youtube": "YouTube",
          "whatsapp": "WhatsApp", "tiktok": "TikTok", "facebookmail": "Facebook", "accountprotection": "Microsoft",
          "microsoftonline": "Microsoft", "amazonses": "Amazon", "bkash": "bKash", "nagad": "Nagad"}

def _domain_label(addr):
    host = (addr or "").split("@")[-1].lower().strip("<> ")
    parts = [x for x in host.split(".") if x]
    if len(parts) < 2:
        return ""
    i = -3 if (len(parts) >= 3 and parts[-2] in _SLD and len(parts[-1]) == 2) else -2
    return parts[i]

def service_name(display_name, addr):
    """Sender theke chhoto, poriskar service-er nam (noreply@... na)."""
    name = re.split(r"(?i)\s+via\s+", str(display_name or ""))[0]
    name = re.sub(r"[\"'<>@]", " ", name)
    name = _GENERIC_NAME.sub(" ", name)
    name = re.sub(r"[\s|:;,\-–—]+", " ", name).strip()
    if name and "." not in name and 2 <= len(name) <= 30:
        return name
    label = _domain_label(addr)
    if label and label not in ("gmail", "googlemail", "outlook", "hotmail", "yahoo"):
        return _BRAND.get(label, label.capitalize())
    return str(display_name or addr or "").strip()[:30]

# ======================= Sender-er site domain (favicon-er jonno) =======================
# Free mail (gmail.com ...) ba bulk-mail service (amazonses.com ...) hole khali -- tader icon sender-er icon na.
_FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "ymail.com",
              "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com", "mail.com", "zoho.com"}
_BULK_MAIL = {"amazonses.com", "sendgrid.net", "mailgun.org", "mandrillapp.com", "mcsv.net", "rsgsv.net",
              "sparkpostmail.com", "postmarkapp.com", "mailjet.com", "sendinblue.com", "brevo.com", "cmail19.com"}
_DOMAIN_ALIAS = {"facebookmail.com": "facebook.com", "accountprotection.microsoft.com": "microsoft.com",
                 "microsoftonline.com": "microsoft.com", "googlemail.com": "google.com"}

def site_domain(addr):
    """noreply@mail.github.com -> github.com. Icon dekhano jabe na hole \"\"."""
    host = (addr or "").split("@")[-1].lower().strip("<> ")
    parts = [x for x in host.split(".") if x]
    if len(parts) < 2:
        return ""
    n = 3 if (len(parts) >= 3 and parts[-2] in _SLD and len(parts[-1]) == 2) else 2
    dom = ".".join(parts[-n:])
    dom = _DOMAIN_ALIAS.get(dom, dom)
    if dom in _FREE_MAIL or dom in _BULK_MAIL:
        return ""
    return dom if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", dom) else ""

# ======================= Magic link (code-er bodole "Verify" button) =======================
LINK_TTL_SECONDS = 600       # link-er meyad email-e na thakle 10 minute dhori
_A_TAG = re.compile(r"""(?is)<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""")
_BARE_URL = re.compile(r"""https?://[^\s<>"')\]]+""")
_LINK_TEXT_OK = re.compile(
    r"(?i)verify|confirm|sign[-\s]?in|log[-\s]?in|activate|approve|magic|it'?s me|complete (?:sign|log|reg)|"
    r"authenticate|ভেরিফাই|যাচাই|কনফার্ম|লগইন")
_LINK_URL_OK = re.compile(r"(?i)verif|confirm|magic|activate|sign-?in|log-?in|passwordless|[?&/]token=|[?&]otp=")
_LINK_GENERIC_TEXT = re.compile(r"(?i)^\s*(?:click here|here|continue|get started|open|go|proceed)\W*$")
_LINK_BAD_TEXT = re.compile(
    r"(?i)unsubscribe|privacy|terms|policy|help|support|preferences|manage|browser|forgot|not me|wasn'?t me|"
    r"report|didn'?t request|did not request|opt[-\s]?out|secure your account|change password")
_LINK_BAD_URL = re.compile(r"(?i)unsubscribe|opt-?out|preferences|privacy|terms|policy|/help|/support")

def _clean_anchor(inner):
    t = re.sub(r"(?s)<[^>]+>", " ", inner)
    return re.sub(r"\s+", " ", html_lib.unescape(t)).strip()

def get_email_links(msg):
    """HTML/plain email theke [(url, anchor-text)] -- shudhu https."""
    out = []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            body = _decode_part(part)
        except Exception:
            continue
        if not body:
            continue
        if ctype == "text/html":
            for m in _A_TAG.finditer(body):
                out.append((html_lib.unescape(m.group(1)).strip(), _clean_anchor(m.group(2))))
        else:
            for m in _BARE_URL.finditer(body):
                out.append((m.group(0).rstrip(".,;"), ""))
    return [(u, t) for u, t in out if u.lower().startswith("https://") and len(u) <= 2000]

def pick_magic_link(links, subject, text):
    """Verify/sign-in jatiyo email-er shobcheye bhalo link. Nai hole None."""
    if _MARKETING.search(subject or ""):
        return None
    body = text
    for _, anchor in links:                       # button-er lekha nijei "Verify" -- take intent hishebe gona hobe na
        if anchor:
            body = body.replace(anchor, " ")
    blob = subject + "\n" + body
    if not (_INTENT.search(blob) or _STRONG_KW.search(blob)):
        return None
    best, best_score = None, 0
    for url, anchor in links:
        if _LINK_BAD_TEXT.search(anchor) or _LINK_BAD_URL.search(url):
            continue
        sc = 0
        if _LINK_TEXT_OK.search(anchor):
            sc += 3
        if _LINK_URL_OK.search(url):
            sc += 2
        if _LINK_GENERIC_TEXT.match(anchor):
            sc += 1
        if sc >= 3 and sc > best_score:
            best, best_score = url, sc
    return best


# ======================= Phishing check (SPF / DKIM / DMARC) =======================
# Gmail nijer receive-kora prottek email-e "Authentication-Results" header boshay. Shetar upor vorsha kora jay,
# kintu SHUDHU Gmail-er nijer header-ta (shobcheye upore, authserv-id = *.google.com).
# Sender nijer toiri kora nokol "Authentication-Results: ... spf=pass" header niche thakte pare -- seta ignore kori.
_AUTH_RES = re.compile(r"(?i)(?<![\w.])(spf|dkim|dmarc)\s*=\s*([a-z]+)")
_SPF_BAD = {"fail", "softfail", "permerror"}
_DKIM_BAD = {"fail", "permerror"}
_DMARC_BAD = {"fail"}

def check_email_auth(msg):
    """{'status': 'fail' | 'ok' | 'unknown', 'reasons': [...], 'spf', 'dkim', 'dmarc'}
    fail    = SPF / DKIM / DMARC-er kono ekta fail (lal sotorkota dekhabo)
    unknown = Gmail-er header paoa gelo na (kichu dekhabo na)"""
    try:
        headers = msg.get_all("Authentication-Results") or []
        if not headers:
            return {"status": "unknown", "reasons": []}
        top = re.sub(r"\s+", " ", str(headers[0]))                 # shobcheye upore-rta = Gmail-er nijer
        authserv = top.split(";", 1)[0].strip().lower()
        if not (authserv == "google.com" or authserv.endswith(".google.com")):
            return {"status": "unknown", "reasons": []}

        spf = dmarc = None
        dkim_all = []
        for m in _AUTH_RES.finditer(top):
            k, v = m.group(1).lower(), m.group(2).lower()
            if k == "spf" and spf is None:
                spf = v
            elif k == "dkim":
                dkim_all.append(v)
            elif k == "dmarc" and dmarc is None:
                dmarc = v
        # Ekadhik DKIM signature thakle: ekta-o pass hole thik (mailing list / forward-er extra signature fail korte pare)
        if "pass" in dkim_all:
            dkim = "pass"
        else:
            dkim = next((v for v in dkim_all if v in _DKIM_BAD), dkim_all[0] if dkim_all else None)

        reasons = []
        if spf in _SPF_BAD:
            reasons.append("SPF " + spf)
        if dkim in _DKIM_BAD:
            reasons.append("DKIM " + dkim)
        if dmarc in _DMARC_BAD:
            reasons.append("DMARC " + dmarc)
        return {"status": "fail" if reasons else "ok", "reasons": reasons, "spf": spf, "dkim": dkim, "dmarc": dmarc}
    except Exception as e:
        print(f"[AUTH] parse error: {type(e).__name__}")
        return {"status": "unknown", "reasons": []}


def fetch_email_info(mail, uid):
    """Ekta email download kore OTP, sender, subject, time ber kore."""
    _, msg_data = mail.uid('fetch', uid, '(INTERNALDATE BODY.PEEK[])')
    for part in msg_data or []:
        if isinstance(part, tuple):
            msg = email.message_from_bytes(part[1])
            internal = None              # Gmail-e pouchanor asol somoy -- sender nokol korte pare na (Date header pare)
            try:
                m_id = re.search(rb'INTERNALDATE "([^"]+)"', part[0])
                if m_id:
                    internal = datetime.strptime(m_id.group(1).decode("ascii").strip(), "%d-%b-%Y %H:%M:%S %z").timestamp()
            except Exception:
                internal = None
            subject = decode_hdr(msg.get("Subject"), "No Subject")
            raw_from = decode_hdr(msg.get("From"), "Unknown Sender")
            name, addr = parseaddr(raw_from)
            sender = name or addr or raw_from
            date_hdr = msg.get("Date")

            epoch = time.time()          # asol (timezone-thik) unix time, expiry hisabe lage
            if internal:
                epoch = internal
            elif date_hdr:               # INTERNALDATE na pele Date header, kintu bhobishyoter tarikh hole ekhon-ke dhori
                try:
                    parsed_dt = parsedate_to_datetime(date_hdr)
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                    epoch = min(parsed_dt.timestamp(), time.time())
                except Exception:
                    pass
            msg_dt = datetime.fromtimestamp(epoch, ZoneInfo("Asia/Dhaka")).replace(tzinfo=None)

            otp, score = "Code not found", 0
            ttl = None
            bodies = get_email_texts(msg) or [""]
            for body in bodies:
                full = subject + "\n" + body
                otp, score = extract_otp_scored(full)
                if otp != "Code not found":
                    ttl = extract_ttl(full)          # jei text-e code pelam, shekhane meyad khuji
                    break
            link = None
            if otp == "Code not found":              # code nai -> "Verify" button-er magic link ache kina
                try:
                    link = pick_magic_link(get_email_links(msg), subject, "\n".join(bodies))
                except Exception:
                    link = None
            if (otp != "Code not found" or link) and ttl is None:
                for body in bodies:                  # oi text-e na thakle onno text-e (plain/html) dekhi
                    ttl = extract_ttl(subject + "\n" + body)
                    if ttl:
                        break
            auth = check_email_auth(msg)
            return {
                "sender": sender,
                "service": service_name(name, addr),
                "domain": site_domain(addr),
                "subject": subject,
                "code": None if otp == "Code not found" else otp,
                "confidence": "high" if score >= CONF_HIGH_SCORE else "low",
                "link": link,
                "time": msg_dt.strftime("%I:%M %p"),
                "timestamp": epoch,
                "ttl_seconds": ttl,                  # None hole default meyad
                "auth_fail": auth["status"] == "fail",              # SPF/DKIM/DMARC fail -> card-e lal sotorkota
                "auth_detail": ", ".join(auth.get("reasons", [])),  # jemon "SPF fail, DKIM fail"
            }
    return None

def scan_folder(mail, acct_email, folder, limit):
    """Folder-er shesh `limit`-ta email dekhe, shobcheye notun code/link-wala email-ta dey."""
    status, _ = mail.select(folder, readonly=True)   # readonly: email 'read' hoye jabe na
    if status != 'OK':
        return None
    status, data = mail.uid('search', None, 'ALL')
    if status != 'OK' or not data or not data[0]:
        return None

    uids = data[0].split()[-limit:]
    for uid in reversed(uids):
        key = (acct_email, folder, uid)
        info = EMAIL_CACHE.get(key)
        if info is None:
            info = fetch_email_info(mail, uid)
            if info is None:
                continue
            if len(EMAIL_CACHE) > EMAIL_CACHE_MAX:
                EMAIL_CACHE.clear()
            EMAIL_CACHE[key] = info
        if info["code"] or info.get("link"):
            return info
    return None

def make_entry(info):
    """Email info -> client-er jonno ekta code/link entry."""
    is_link = bool(info.get("link")) and not info.get("code")
    ttl = info.get("ttl_seconds") or (LINK_TTL_SECONDS if is_link else OTP_TTL_SECONDS)
    return {
        "code": ("link-" + hashlib.sha1(info["link"].encode()).hexdigest()[:8]) if is_link else info["code"],
        "kind": "link" if is_link else "code",
        "url": info["link"] if is_link else None,
        "sender": info["sender"],
        "service": info.get("service") or info["sender"],
        "domain": info.get("domain", ""),
        "subject": info["subject"],
        "confidence": "high" if is_link else info.get("confidence", "high"),   # link-er nijosso note ache
        "time": info["time"],
        "timestamp": info["timestamp"],
        "age_seconds": max(0, int(time.time() - info["timestamp"])),
        "ttl_seconds": ttl,
        "auth_fail": bool(info.get("auth_fail")),
        "auth_detail": info.get("auth_detail", ""),
    }

def _imap_login(account):
    """Connect + login. Network-er temporary shomossa hole ekbar retry kore.
    Password vul hole retry kore lav nai, tai sathe sathe error dey."""
    last = None
    for _ in range(2):
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
            mail.login(account['email'], account['password'])
            return mail
        except (OSError, imaplib.IMAP4.abort) as e:      # timeout / connection drop
            last = e
            time.sleep(0.5)
        # imaplib.IMAP4.error (login reject) ekhane catch hoy na -> shoja bahire jabe
    raise last

def _error_reason(e):
    """Card-e dekhanor jonno chhoto karon."""
    msg = str(e).upper()
    if isinstance(e, (socket.timeout, TimeoutError)) or "TIMED OUT" in msg:
        return "Timeout - Refresh again"
    if any(k in msg for k in ("AUTHENTICATIONFAILED", "INVALID CREDENTIALS", "APPLICATION-SPECIFIC",
                              "WEBLOGIN", "ALERT", "LOGIN FAILED")):
        return "Login failed - check App Password"
    return "Connection Error"

def check_gmail(account, mail_data):
    try:
        mail = _imap_login(account)

        inbox_res, spam_res = None, None

        # 1. Inbox
        try:
            inbox_res = scan_folder(mail, account['email'], "inbox", INBOX_SCAN_LIMIT)
        except Exception as e:
            print(f"[INBOX ERROR] {account['email']}: {e}")

        # 2. Spam (inbox-e code thakleo dekhbo, jate spam-e notun code ashle miss na hoy)
        for folder in ["[Gmail]/Spam", "Spam"]:
            try:
                spam_res = scan_folder(mail, account['email'], folder, SPAM_SCAN_LIMIT)
            except Exception:
                spam_res = None
            if spam_res:
                break

        # 3. Duto-r moddhe jeta shobcheye notun (timestamp boro) seta
        result, label = None, "INBOX"
        if inbox_res and spam_res:
            if spam_res["timestamp"] > inbox_res["timestamp"]:
                result, label = spam_res, "SPAM"
            else:
                result = inbox_res
        elif spam_res:
            result, label = spam_res, "SPAM"
        else:
            result = inbox_res

        if result:
            print(f"[{label}] Found OTP for {account['email']}")      # code log-e rakhi na
            mail_data.append({"email": account['email'], **make_entry(result)})
        else:
            mail_data.append({
                "email": account['email'],
                "sender": "N/A",
                "subject": "No recent OTP emails found",
                "code": "No emails found",
                "time": "N/A",
                "timestamp": 0
            })

        try:
            mail.logout()
        except Exception:
            pass
    except Exception as e:
        print(f"Error reading {account['email']}: {e}")
        mail_data.append({
            "email": account['email'],
            "sender": _error_reason(e),
            "subject": "Failed to login/fetch",
            "code": "Error",
            "time": "N/A",
            "timestamp": 0
        })

# ======================= Live push (IMAP IDLE + SSE) =======================
# ENABLE_PUSH=1 dile chalu hoy. Gmail-er INBOX "IDLE" mode-e dhore rakhe; notun mail ashle
# server sathe sathe browser-e (SSE) jani dey, refresh chapte hoy na.
# ZARURI: Render Start Command-e gthread worker lagbe, ar worker 1 ta:
#   gunicorn app:app --worker-class gthread --workers 1 --threads 16 --timeout 120
# (--preload dewa jabe na). Sync worker-e ENABLE_PUSH=1 dile app atke jete pare.
ENABLE_PUSH = os.environ.get("ENABLE_PUSH", "0").strip().lower() in ("1", "true", "yes", "on")
IDLE_MAX_WAIT = 300          # 5 minute por por IDLE notun kore shuru (Gmail connection bachay)
MAX_SUBSCRIBERS = 8          # ekshathe koyta browser stream dhorbe
STREAM_MAX_SECONDS = 600     # 10 min por stream bondho; browser nije abar connect kore (login abar check hoy)

_subs = set()
_subs_lock = threading.Lock()

def broadcast_item(item):
    msg = json.dumps(item, ensure_ascii=False)
    with _subs_lock:
        for q in list(_subs):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

def publish_scan(acc):
    """Account-ta abar scan kore notun result shobaike pathay."""
    out = []
    check_gmail(acc, out)
    if out and out[0].get("code") != "Error":      # temporary error diye thik code muche felbo na
        broadcast_item(out[0])
        try:
            notify_push(out[0])                       # tab bondho thakleo phone-e notification
        except Exception as e:
            print(f"[WEBPUSH] notify error: {e}")

def _sock_read(sock, timeout):
    """Data thakle bytes, timeout hole None. Connection bondho hole error."""
    pending = getattr(sock, "pending", None)
    if not (pending and pending() > 0):
        ready, _, _ = select.select([sock], [], [], timeout)
        if not ready:
            return None
    data = sock.recv(4096)
    if not data:
        raise ConnectionError("IMAP connection closed")
    return data

class _Lines:
    """Socket theke line-by-line pori (imaplib-er file buffer bypass kore)."""
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def readline(self, timeout):
        end = time.time() + timeout
        while b"\r\n" not in self.buf:
            left = end - time.time()
            if left <= 0:
                return None
            chunk = _sock_read(self.sock, left)
            if chunk is None:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line

def _idle_cycle(lines, sock, tag, max_wait):
    """Ekbar IDLE: notun mail (EXISTS) ashle True, max_wait pore False."""
    sock.sendall(tag + b" IDLE\r\n")
    while True:                                     # server-er "+ idling" opekkha
        line = lines.readline(15)
        if line is None:
            raise TimeoutError("IDLE not accepted")
        if line.startswith(b"+"):
            break
        if line.startswith(tag):
            raise RuntimeError("IDLE refused")
    new_mail = False
    end = time.time() + max_wait
    while True:
        left = end - time.time()
        if left <= 0:
            break
        line = lines.readline(left)
        if line is None:
            break
        if line.endswith(b"EXISTS"):
            new_mail = True
            break
    sock.sendall(b"DONE\r\n")
    while True:                                     # server-er "OK IDLE terminated"
        line = lines.readline(15)
        if line is None:
            raise TimeoutError("IDLE did not terminate")
        if line.startswith(tag):
            break
        if line.endswith(b"EXISTS"):
            new_mail = True
    return new_mail

def watch_account(acc, stop_event, holder):
    backoff = 5
    n = 0
    while not stop_event.is_set():
        mail = None
        try:
            mail = _imap_login(acc)
            holder["mail"] = mail
            typ, _ = mail.select("inbox", readonly=True)
            if typ != "OK":
                raise RuntimeError("cannot select inbox")
            sock = mail.sock
            lines = _Lines(sock)
            backoff = 5
            print(f"[PUSH] watching {acc['email']}")
            while not stop_event.is_set():
                n += 1
                if _idle_cycle(lines, sock, ("I%d" % n).encode(), IDLE_MAX_WAIT) and not stop_event.is_set():
                    time.sleep(1)                   # mail puropuri index hoye jak
                    publish_scan(acc)
        except Exception as e:
            if stop_event.is_set():
                break
            reason = _error_reason(e)
            print(f"[PUSH] {acc['email']}: {reason} ({type(e).__name__})")
            # Password vul hole 30 min pore abar; nahole dhire dhire barano
            backoff = 1800 if reason.startswith("Login failed") else min(backoff * 2, 300)
        finally:
            try:
                if mail is not None:
                    mail.shutdown()
            except Exception:
                pass
        stop_event.wait(backoff)

_watchers = {}      # email -> {"thread", "stop", "password", "holder"}

def _stop_watcher(w):
    w["stop"].set()
    try:
        m = w["holder"].get("mail")
        if m is not None:
            m.shutdown()                            # IDLE-e atke thakle connection kete dey
    except Exception:
        pass

def _push_supervisor():
    """Prottek 30 sekende account list milay: notun hole watcher shuru, delete/password-badol hole bondho."""
    while True:
        try:
            # shudhu pin kora account gulo watch hobe -- shob Gmail-e ekshathe connection khola hobe na
            pinned = {_norm_email(e) for e in _read_pins()} if prefs_collection is not None else set()
            accs = {x["email"]: x for x in load_accounts() if _norm_email(x["email"]) in pinned}
            for email, w in list(_watchers.items()):
                if email not in accs or accs[email]["password"] != w["password"] or not w["thread"].is_alive():
                    _stop_watcher(w)
                    _watchers.pop(email, None)
            for email, acc in accs.items():
                if email not in _watchers:
                    ev, holder = threading.Event(), {}
                    t = threading.Thread(target=watch_account, args=(acc, ev, holder), daemon=True)
                    _watchers[email] = {"thread": t, "stop": ev, "password": acc["password"], "holder": holder}
                    t.start()
        except Exception as e:
            print(f"[PUSH] supervisor error: {e}")
        time.sleep(30)

def start_push():
    threading.Thread(target=_push_supervisor, daemon=True).start()
    print("[PUSH] live push chalu. Start Command: gunicorn app:app --worker-class gthread --workers 1 --threads 16 --timeout 120")

# ======================= Card-er ⏳: shudhu ekta account-e on-demand live watch =======================
# ⏳ chaple server shudhu oi ekta Gmail-e ekta IMAP IDLE connection khole (max WAIT_WATCH_SECONDS).
# Notun mail ashle oi account-ta ekbar scan kore SSE diye browser-e pathay. Shesh hole / cancel hole connection bondho.
# Pin kora account-er permanent watcher (ENABLE_PUSH=1) already chalu thakle notun connection khola hoy na.
WAIT_WATCH_MAX = 3          # ekshathe max koyta account-e on-demand watch
WAIT_WATCH_SECONDS = 130    # browser-er 120s wait-er cheye ektu beshi; tarpor nije bondho hoy
_waiters = {}               # email(lower) -> {"stop": Event, "holder": {"mail": ...}}
_waiters_lock = threading.Lock()

def _wait_watch_run(acc, mail, stop_event, deadline):
    key = _norm_email(acc["email"])
    try:
        # IMAP IDLE can notify only for the selected folder, and switching folders
        # while reading the raw socket can miss buffered IMAP responses. Poll both
        # folders with normal imaplib commands instead; this is reliable for Spam too.
        folders = (("INBOX", INBOX_SCAN_LIMIT), ("[Gmail]/Spam", SPAM_SCAN_LIMIT))
        last_seen = {}
        # Establish a baseline before waiting, so old codes are not repeatedly shown.
        for folder, limit in folders:
            try:
                info = scan_folder(mail, acc["email"], folder, limit)
                last_seen[folder] = info.get("timestamp", 0) if info else 0
            except Exception as e:
                print(f"[WAIT BASELINE] {acc['email']} {folder}: {e}")
                last_seen[folder] = 0

        while not stop_event.is_set() and time.time() < deadline:
            found_new = []
            for folder, limit in folders:
                if stop_event.is_set() or time.time() >= deadline:
                    break
                try:
                    info = scan_folder(mail, acc["email"], folder, limit)
                    if not info:
                        continue
                    ts = info.get("timestamp", 0)
                    if ts > last_seen.get(folder, 0):
                        last_seen[folder] = ts
                        found_new.append((ts, info))
                except Exception as e:
                    print(f"[WAIT SCAN] {acc['email']} {folder}: {e}")

            # Check the alternate Gmail folder name if the standard Spam folder
            # name is not available on this account.
            if not found_new and not stop_event.is_set() and time.time() < deadline:
                try:
                    info = scan_folder(mail, acc["email"], "Spam", SPAM_SCAN_LIMIT)
                    if info:
                        ts = info.get("timestamp", 0)
                        if ts > last_seen.get("Spam", 0):
                            last_seen["Spam"] = ts
                            found_new.append((ts, info))
                except Exception as e:
                    print(f"[WAIT SCAN] {acc['email']} Spam: {e}")

            for ts, info in sorted(found_new, key=lambda pair: pair[0]):
                item = {"email": acc["email"], **make_entry(info)}
                broadcast_item(item)
                try:
                    notify_push(item)
                except Exception as e:
                    print(f"[WEBPUSH] notify error: {e}")

            # Short polling interval gives Inbox and Spam equal attention without
            # holding an IDLE session on only one folder.
            stop_event.wait(min(5, max(0, deadline - time.time())))
    except Exception as e:
        if not stop_event.is_set():
            print(f"[WAIT] {acc['email']}: {_error_reason(e)} ({type(e).__name__})")
    finally:
        try:
            mail.shutdown()
        except Exception:
            pass
        with _waiters_lock:
            w = _waiters.get(key)
            if w and w["stop"] is stop_event:
                _waiters.pop(key, None)

@app.route('/api/watch-start', methods=['POST'])
def watch_start():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    body = _json_body()
    key = _norm_email(body.get('email'))
    acc = next((x for x in load_accounts() if _norm_email(x.get('email')) == key), None)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    w = _watchers.get(acc["email"])
    if w and w["thread"].is_alive():                # pin-er permanent watcher already ache
        return jsonify({"ok": True, "via": "pinned"})
    try:
        seconds = int(body.get('seconds') or WAIT_WATCH_SECONDS)
    except (TypeError, ValueError):
        seconds = WAIT_WATCH_SECONDS
    seconds = max(10, min(seconds, WAIT_WATCH_SECONDS))
    with _waiters_lock:
        old = _waiters.pop(key, None)
        if old:
            _stop_watcher(old)
        if len(_waiters) >= WAIT_WATCH_MAX:
            return jsonify({"error": "Too many accounts are being watched. Cancel one first."}), 429
    mail = None
    try:
        mail = _imap_login(acc)
        typ, _ = mail.select("inbox", readonly=True)
        if typ != "OK":
            raise RuntimeError("cannot select inbox")
    except Exception as e:
        try:
            if mail is not None:
                mail.shutdown()
        except Exception:
            pass
        return jsonify({"error": _error_reason(e)}), 502
    ev = threading.Event()
    with _waiters_lock:
        _waiters[key] = {"stop": ev, "holder": {"mail": mail}}
    deadline = time.time() + seconds
    threading.Thread(target=_wait_watch_run, args=(acc, mail, ev, deadline), daemon=True).start()
    return jsonify({"ok": True, "via": "wait"})

@app.route('/api/watch-stop', methods=['POST'])
def watch_stop():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    key = _norm_email(_json_body().get('email'))
    with _waiters_lock:
        w = _waiters.pop(key, None)
    if w:
        _stop_watcher(w)
    return jsonify({"ok": True})

# ======================= PWA files (service worker + manifest) =======================
# sw.js root-e thakte hoy (/sw.js), nahole shob page-er scope pay na.
@app.route('/sw.js')
def service_worker():
    resp = send_from_directory(os.path.join(app.root_path, 'static'), 'sw.js', mimetype='application/javascript')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

@app.route('/manifest.webmanifest')
def web_manifest():
    resp = send_from_directory(os.path.join(app.root_path, 'static'), 'manifest.webmanifest', mimetype='application/manifest+json')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

# ======================= Web Push (notun code ashle phone/computer-e notification) =======================
# Server-er IMAP IDLE watcher (ENABLE_PUSH=1) notun code dhorle ekhane ashe, ar subscribe kora shob device-e pathay.
# Payload end-to-end encrypted -- Google/Apple/Mozilla push service poreo na.
# Default-e notification-e code DEKHANO HOY NA ("New code for a@gmail.com"); device-wise "show code" chalu korle dekhay.
PUSH_MAX_SUBS = 10          # koyta device subscribe korte pare
PUSH_TTL = 300              # sekend: device offline thakle push service eto khon dhore rakhe
PUSH_MAX_AGE = 180          # sekend: er cheye purono code-er notification pathabo na
_PUSH_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
_notified = {}              # "email|timestamp" -> kokhon pathano hoyeche (ekoi code-er duibar notification na)
_notified_lock = threading.Lock()

def push_ready():
    return bool(webpush is not None and VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and push_collection is not None)

def _push_id(endpoint):
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()

def _valid_subscription(sub):
    """Browser-er subscription object thik ache kina (ar shudhu cheno push service-er URL)."""
    if not isinstance(sub, dict):
        return None
    ep = sub.get("endpoint")
    keys = sub.get("keys") or {}
    if not isinstance(ep, str) or not ep.startswith("https://") or len(ep) > 2000:
        return None
    host = (urlparse(ep).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _PUSH_HOSTS):
        return None
    p256, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(p256, str) and isinstance(auth, str) and 0 < len(p256) < 200 and 0 < len(auth) < 100):
        return None
    return {"endpoint": ep, "keys": {"p256dh": p256, "auth": auth}}

def _send_one(doc, payload):
    """'ok' | 'gone' (subscription mora, delete hoyeche) | 'error'."""
    try:
        webpush(
            subscription_info=doc["subscription"],
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},       # prottekbar notun dict (library eta-te kichu jog kore)
            ttl=PUSH_TTL,
            headers={"Urgency": "high"},
            timeout=10,
        )
        return "ok"
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):                       # device unsubscribe / app muche felse
            try:
                push_collection.delete_one({"_id": doc["_id"]})
            except Exception:
                pass
            return "gone"
        print(f"[WEBPUSH] send failed (HTTP {status}): {type(e).__name__}")
    except Exception as e:
        print(f"[WEBPUSH] error: {type(e).__name__}: {e}")
    return "error"

def _push_worker(item):
    try:
        docs = list(push_collection.find({}))
    except Exception as e:
        print(f"[WEBPUSH] read error: {e}")
        return
    email_addr = str(item.get("email", ""))
    service = item.get("service") or item.get("sender") or "New code"
    is_link = item.get("kind") == "link"
    for doc in docs:
        if item.get("auth_fail"):
            body = f"\u26a0 Suspicious email for {email_addr} - don't use this code"   # code kokhono dekhai na
        elif is_link:
            body = f"Verify link for {email_addr}"
        elif doc.get("show_code"):
            body = f"Code {item.get('code')} · {email_addr}"
        else:
            body = f"New code for {email_addr}"
        _send_one(doc, {
            "title": service, "body": body, "tag": "otp-" + email_addr.lower(),
            "url": "/?action=find", "ts": int(time.time() * 1000),
        })

def notify_push(item):
    """Notun, fresh code/link hole shob subscribed device-e notification pathay (background thread-e)."""
    if not push_ready():
        return
    code = item.get("code")
    if not code or code in ("No emails found", "Error", "Code not found"):
        return
    age = item.get("age_seconds", 10 ** 9)
    ttl = item.get("ttl_seconds") or OTP_TTL_SECONDS
    if age > PUSH_MAX_AGE or age >= ttl:
        return
    key = f"{item.get('email')}|{item.get('timestamp')}"
    now = time.time()
    with _notified_lock:
        for k, t in list(_notified.items()):
            if now - t > 3600:
                _notified.pop(k, None)
        if key in _notified:
            return
        _notified[key] = now
    threading.Thread(target=_push_worker, args=(dict(item),), daemon=True).start()

@app.route('/api/push/config')
def push_config():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    ready = push_ready()
    return jsonify({"enabled": ready, "live": ENABLE_PUSH, "public_key": VAPID_PUBLIC_KEY if ready else ""})

@app.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not push_ready():
        return jsonify({"error": "Notifications are not set up on the server"}), 503
    data = _json_body()
    sub = _valid_subscription(data.get('subscription'))
    if not sub:
        return jsonify({"error": "Invalid subscription"}), 400
    sid = _push_id(sub["endpoint"])
    try:
        exists = push_collection.find_one({"_id": sid}, {"_id": 1})
        if not exists and push_collection.count_documents({}) >= PUSH_MAX_SUBS:
            return jsonify({"error": "Too many devices have alerts on. Turn one off first."}), 400
        push_collection.update_one(
            {"_id": sid},
            {"$set": {"subscription": sub, "show_code": bool(data.get("show_code"))},
             "$setOnInsert": {"created_at": datetime.now()}},
            upsert=True,
        )
        return jsonify({"success": True})
    except Exception as e:
        print(f"Push subscribe error: {e}")
        return jsonify({"error": "Could not save alerts setting"}), 500

@app.route('/api/push/prefs', methods=['POST'])
def push_prefs():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if push_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    data = _json_body()
    ep = data.get('endpoint')
    if not isinstance(ep, str) or not ep:
        return jsonify({"error": "Endpoint required"}), 400
    try:
        res = push_collection.update_one({"_id": _push_id(ep)}, {"$set": {"show_code": bool(data.get("show_code"))}})
        if res.matched_count == 0:
            return jsonify({"error": "This device is not subscribed"}), 404
        return jsonify({"success": True})
    except Exception as e:
        print(f"Push prefs error: {e}")
        return jsonify({"error": "Could not save"}), 500

@app.route('/api/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if push_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    ep = _json_body().get('endpoint')
    if not isinstance(ep, str) or not ep:
        return jsonify({"error": "Endpoint required"}), 400
    try:
        push_collection.delete_one({"_id": _push_id(ep)})
        return jsonify({"success": True})
    except Exception as e:
        print(f"Push unsubscribe error: {e}")
        return jsonify({"error": "Could not save"}), 500

@app.route('/api/push/test', methods=['POST'])
def push_test():
    """Ei device-e ekta test notification (setup thik ache kina dekhar jonno)."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not push_ready():
        return jsonify({"error": "Notifications are not set up on the server"}), 503
    ep = _json_body().get('endpoint')
    if not isinstance(ep, str) or not ep:
        return jsonify({"error": "Endpoint required"}), 400
    doc = push_collection.find_one({"_id": _push_id(ep)})
    if not doc:
        return jsonify({"error": "This device is not subscribed"}), 404
    result = _send_one(doc, {"title": "Test notification", "body": "Alerts are working on this device.",
                             "tag": "otp-test", "url": "/", "ts": int(time.time() * 1000)})
    if result == "ok":
        return jsonify({"success": True})
    if result == "gone":
        return jsonify({"error": "This device's subscription expired. Turn alerts off and on again."}), 410
    return jsonify({"error": "Could not reach the push service"}), 502


# ======================= Idle timeout (server-side) =======================
# Kono kaj na korle IDLE_TIMEOUT sekend por session shesh. Browser-er auto-lock (5 min) er sathe mil rekhe.
# "Kaj kora" mane: login howa ba kono authenticated request (browser kaj korle prottek ~1 min-e /api/ping pathay).
# Background stream (/api/stream) kaj hishebe gona hoy na, nahole ekta khola tab kokhono shesh hobe na.
IDLE_TIMEOUT = int(os.environ.get("IDLE_TIMEOUT_SECONDS", "300"))
_last_user_activity = time.time()                 # SSE stream idle hole bondho korar jonno (ekjon-er app)
_IDLE_SKIP = ('/api/push-status',)                # login lage na, check-er dorkar nai
_IDLE_PUBLIC = ('/api/login', '/api/logout',      # session shesh hole-o ei gulo cholte hobe, nahole abar login kora jabe na
                '/api/passkey/auth/options', '/api/passkey/auth/verify')
_IDLE_BACKGROUND = ('/api/stream',)               # background connection, "kaj kora" na

def _mark_active():
    global _last_user_activity
    now = time.time()
    session['last_active'] = now
    _last_user_activity = now

@app.before_request
def enforce_idle_timeout():
    path = request.path
    if not path.startswith('/api/') or path in _IDLE_SKIP:
        return None
    if not session.get('logged_in'):
        return None
    last = session.get('last_active')
    if last is not None and time.time() - last > IDLE_TIMEOUT:
        session.clear()                           # login + settings unlock dutoi gelo
        if path in _IDLE_PUBLIC:
            return None
        return jsonify({"error": "Session expired (inactive). Please login again.", "expired": True}), 401
    if path not in _IDLE_BACKGROUND:
        _mark_active()                            # purono session-e last_active na thakleo ekhane boshe jabe
    return None

# ---------------- Response compression (gzip) -- page ~450 KB theke ~100 KB hoye nambe ----------------
_GZIP_TYPES = ('text/html', 'text/css', 'text/plain', 'text/javascript', 'application/javascript', 'application/json',
               'application/manifest+json', 'image/svg+xml')
_GZIP_CACHE = {}      # sha1(body) -> gzip bytes  (boro page ekbar-i compress hoy, protibar na)

@app.after_request
def gzip_response(resp):
    try:
        if resp.status_code != 200:
            return resp
        if resp.direct_passthrough:
            if request.path.startswith('/static/') and request.path.endswith(('.js', '.css', '.svg')):
                resp.direct_passthrough = False                                         # chhoto static file -- compress kora jay
                resp.make_sequence()
            else:
                return resp
        if resp.is_streamed:                                                            # SSE / stream-e hat dei na
            return resp
        if 'gzip' not in request.headers.get('Accept-Encoding', '').lower():
            return resp
        if resp.headers.get('Content-Encoding'):                                        # proxy age-i compress korle skip
            return resp
        ctype = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if ctype not in _GZIP_TYPES:
            return resp
        data = resp.get_data()
        if len(data) < 700:
            return resp
        key = hashlib.sha1(data).hexdigest() if len(data) > 50000 else None
        packed = _GZIP_CACHE.get(key) if key else None
        if packed is None:
            packed = gzip.compress(data, compresslevel=6, mtime=0)
            if key:
                if len(_GZIP_CACHE) > 8:
                    _GZIP_CACHE.clear()
                _GZIP_CACHE[key] = packed
        resp.set_data(packed)
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers['Content-Length'] = str(len(packed))
        resp.headers.add('Vary', 'Accept-Encoding')
    except Exception as e:
        print(f"[GZIP] skipped: {type(e).__name__}")
    return resp

# ---------------- Static file cache: URL-e file-er hash (?v=...) -> 1 bochor cache, file bodlale URL bodlay ----------------
_ASSET_V = {}         # filename -> (mtime, hash)

def asset_v(name):
    """static/<name>-er content hash (10 char). File bodlale hash bodlay, tai purono cache aponei bad."""
    path = os.path.join(app.root_path, 'static', name)
    try:
        mt = os.path.getmtime(path)
        hit = _ASSET_V.get(name)
        if hit and hit[0] == mt:
            return hit[1]
        with open(path, 'rb') as f:
            h = hashlib.sha1(f.read()).hexdigest()[:10]
        _ASSET_V[name] = (mt, h)
        return h
    except OSError:
        return '0'

app.jinja_env.globals['asset_v'] = asset_v

@app.after_request
def static_cache_headers(resp):
    # Shudhu ?v=<hash> shoho URL immutable. Bina ?v= static file age-r moto (ETag diye check) thake.
    if request.path.startswith('/static/') and request.args.get('v') and resp.status_code == 200:
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/ping', methods=['POST'])
def ping():
    """Browser-e kaj hole session jiye rakhe (idle timeout reset)."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    return jsonify({"ok": True})

@app.route('/api/login', methods=['POST'])
def login():
    if not MASTER_PASSWORD:
        return jsonify({"error": "Server not configured (MASTER_PASSWORD missing)"}), 503
    bucket = _login_bucket()
    secs = max(lock_remaining(bucket), lock_remaining('login-all'))
    if secs:
        return locked_response(secs)
    data = _json_body()
    password = data.get('password')
    if password and pw_equal(password, MASTER_PASSWORD):
        clear_fails(bucket)                           # shudhu nijer IP-r hishab muchhi, global hishab na
        session['logged_in'] = True
        _mark_active()
        return jsonify({"success": True})
    all_locked, _ = register_fail('login-all', LOGIN_GLOBAL_MAX)
    locked, left = register_fail(bucket)
    if locked or all_locked:
        return locked_response(locked or all_locked)
    return jsonify({"error": f"Wrong password! ({left} attempt(s) left)"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('logged_in', None)
    session.pop('settings_unlocked', None)
    return jsonify({"success": True})

@app.route('/api/refresh-one', methods=['POST'])
def refresh_one():
    """Shudhu ekta Gmail account abar check kore."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    email_in = str(_json_body().get('email', '')).strip().lower()
    acc = next((x for x in load_accounts() if str(x.get('email', '')).lower() == email_in), None)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    out = []
    check_gmail(acc, out)
    if not out:
        return jsonify({"error": "No result"}), 500
    return jsonify(out[0])

# ======================= Card-er "Latest email" button: sesh email-ta pora =======================
# Inbox ar Spam-er shesh email-er moddhe je-ta notun seta dekhay. Email "read" hoye jay na (readonly + BODY.PEEK).
# Shudhu text dekhano hoy (HTML render na) -- jate tracking pixel / script / nokol link kaj na kore.
MAIL_VIEW_MAX_CHARS = 30000
MAIL_FETCH_BYTES = 500000        # boro attachment-wala email-e shob download na kore, shuru-r ongsho-i jothesto

def _last_in_folder(mail, folder):
    """(uid, Gmail-e pouchanor samoy/epoch) -- folder-er shobcheye notun email. Nai hole None."""
    status, _ = mail.select(folder, readonly=True)
    if status != 'OK':
        return None
    status, data = mail.uid('search', None, 'ALL')
    if status != 'OK' or not data or not data[0]:
        return None
    uid = data[0].split()[-1]
    ts = 0
    try:
        _, md = mail.uid('fetch', uid, '(INTERNALDATE)')
        raw = b" ".join((p[0] if isinstance(p, tuple) else p) for p in (md or []) if isinstance(p, (bytes, tuple))).decode("ascii", "ignore")
        m = re.search(r'INTERNALDATE "([^"]+)"', raw)
        if m:
            ts = datetime.strptime(m.group(1).strip(), "%d-%b-%Y %H:%M:%S %z").timestamp()
    except Exception:
        ts = 0
    return uid, ts

def _spam_folder(mail):
    """Spam folder select kore tar nam dey (Gmail-er bhasha-bhede nam alada hote pare). Na pele None."""
    for fname in ("[Gmail]/Spam", "Spam"):
        try:
            if mail.select(fname, readonly=True)[0] == 'OK':
                return fname
        except Exception:
            pass
    return None

def _select_view_folder(mail, folder_req):
    """'inbox' / 'spam' -> select kore folder-er nam dey. Na pele None."""
    if folder_req == 'inbox':
        return 'inbox' if mail.select('inbox', readonly=True)[0] == 'OK' else None
    return _spam_folder(mail)

def _fetch_msg(mail, uid, nbytes):
    """(email message, Gmail-e pouchanor epoch) -- BODY.PEEK, tai email \"read\" hoy na."""
    _, md = mail.uid('fetch', uid, '(INTERNALDATE BODY.PEEK[]<0.%d>)' % nbytes)
    msg, ts = None, 0
    for part in md or []:
        if isinstance(part, tuple) and msg is None:
            msg = email.message_from_bytes(part[1])
    try:
        raw = b" ".join((p[0] if isinstance(p, tuple) else p) for p in (md or []) if isinstance(p, (bytes, tuple))).decode("ascii", "ignore")
        m = re.search(r'INTERNALDATE "([^"]+)"', raw)
        if m:
            ts = datetime.strptime(m.group(1).strip(), "%d-%b-%Y %H:%M:%S %z").timestamp()
    except Exception:
        ts = 0
    return msg, ts

def _tidy_text(t):
    t = t.translate(_ZW).replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()

def _mail_display_text(msg):
    """Pora-r jonno email-er text. HTML-only email-e link-er thikana hariye jay, tai \"text <url>\" hishebe rakhi."""
    plain, html_body = "", ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or part.get_filename():
            continue
        try:
            d = _decode_part(part)
        except Exception:
            continue
        if not d:
            continue
        if ctype == "text/plain" and not plain:
            plain = d
        elif ctype == "text/html" and not html_body:
            html_body = d
    if len(plain.strip()) >= 40 or not html_body.strip():
        return plain
    def repl(m):
        url = html_lib.unescape(m.group(1)).strip()
        text = _clean_anchor(m.group(2))
        if not url.lower().startswith(("https://", "http://")) or len(url) > 2000:
            return text
        return (text + " " if text else "") + "\u27e8" + url + "\u27e9"   # < > dile html_to_text muche felto
    t = html_to_text(_A_TAG.sub(repl, html_body))
    return t.replace("\u27e8", "<").replace("\u27e9", ">")

@app.route('/api/latest-mail', methods=['POST'])
def latest_mail():
    """Ekta Gmail-er shesh email (sender, subject, shomoy, SPF/DKIM, text) -- card-er envelope button-er jonno."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    email_in = str(_json_body().get('email', '')).strip().lower()
    acc = next((x for x in load_accounts() if str(x.get('email', '')).lower() == email_in), None)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    mail = None
    try:
        mail = _imap_login(acc)
        body_in = _json_body()
        folder_req = str(body_in.get('folder', '')).strip().lower()
        uid_req = str(body_in.get('uid', '')).strip()
        if folder_req or uid_req:
            # List theke nirdishto ekta email (Inbox / Spam)
            if folder_req not in ('inbox', 'spam') or not re.fullmatch(r'\d{1,12}', uid_req):
                return jsonify({"error": "Bad request"}), 400
            fname = _select_view_folder(mail, folder_req)
            if not fname:
                return jsonify({"error": "Could not read the email"}), 404
            label, uid, ts = folder_req.upper(), uid_req.encode(), 0
        else:
            cands = []                                      # (samoy, label, folder, uid)
            r = _last_in_folder(mail, "inbox")
            if r:
                cands.append((r[1], "INBOX", "inbox", r[0]))
            for fname in ("[Gmail]/Spam", "Spam"):
                try:
                    r = _last_in_folder(mail, fname)
                except Exception:
                    r = None
                if r:
                    cands.append((r[1], "SPAM", fname, r[0]))
                    break
            if not cands:
                return jsonify({"found": False})
            ts, label, fname, uid = max(cands, key=lambda c: c[0])
            mail.select(fname, readonly=True)

        msg, ts_fetched = _fetch_msg(mail, uid, MAIL_FETCH_BYTES)
        if not ts:
            ts = ts_fetched
        if msg is None:
            return jsonify({"error": "Could not read the email"}), 502

        subject = decode_hdr(msg.get("Subject"), "(No subject)")
        name, addr = parseaddr(decode_hdr(msg.get("From"), ""))
        received = ts
        if not received:                                    # INTERNALDATE na pele Date header
            try:
                received = parsedate_to_datetime(msg.get("Date")).timestamp()
            except Exception:
                received = time.time()
        shown = datetime.fromtimestamp(received, ZoneInfo("Asia/Dhaka")).strftime("%d %b %Y, %I:%M %p")

        body = _tidy_text(_mail_display_text(msg))
        code, code_score = None, 0                           # email-e code thakle alada kore dekhanor jonno
        for t in (get_email_texts(msg) or [""]):
            c, sc = extract_otp_scored(subject + "\n" + t)
            if c != "Code not found":
                code, code_score = c, sc
                break
        truncated = len(body) > MAIL_VIEW_MAX_CHARS
        if truncated:
            body = body[:MAIL_VIEW_MAX_CHARS]

        attachments = []
        for p in msg.walk():
            fn = p.get_filename()
            if fn:
                attachments.append(decode_hdr(fn)[:80])

        auth = check_email_auth(msg)
        return jsonify({
            "found": True,
            "folder": label,
            "subject": subject,
            "from_name": name,
            "from_addr": addr,
            "sender_domain": site_domain(addr),             # link-er domain-er sathe milanor jonno ("" hole milabo na)
            "code": code,
            "code_confidence": "high" if code_score >= CONF_HIGH_SCORE else "low",
            "time": shown,
            "timestamp": received,
            "body": body,
            "truncated": truncated,
            "attachments": attachments[:5],
            "auth": {"status": auth["status"], "spf": auth.get("spf"), "dkim": auth.get("dkim"), "dmarc": auth.get("dmarc")},
            "auth_fail": auth["status"] == "fail",
            "auth_detail": ", ".join(auth.get("reasons", [])),
        })
    except Exception as e:
        print(f"[LATEST-MAIL] {acc['email']}: {type(e).__name__}")
        return jsonify({"error": _error_reason(e)}), 502
    finally:
        try:
            if mail is not None:
                mail.logout()
        except Exception:
            pass

# ======================= Latest email popup: Inbox / Spam-er shesh 3 ta email =======================
MAIL_LIST_COUNT = 3
MAIL_LIST_FETCH_BYTES = 60000    # list-e shudhu sender/subject/code lage, tai shuru-r ongsho-i jothesto

@app.route('/api/mail-list', methods=['POST'])
def mail_list():
    """Inbox ba Spam-er shesh 3 ta email-er shongkhipto talika (notun age)."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    data_in = _json_body()
    email_in = str(data_in.get('email', '')).strip().lower()
    folder_req = str(data_in.get('folder', '')).strip().lower()
    if folder_req not in ('inbox', 'spam'):
        return jsonify({"error": "Bad request"}), 400
    acc = next((x for x in load_accounts() if str(x.get('email', '')).lower() == email_in), None)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    mail = None
    try:
        mail = _imap_login(acc)
        items = []
        fname = _select_view_folder(mail, folder_req)
        if fname:
            status, data = mail.uid('search', None, 'ALL')
            uids = data[0].split()[-MAIL_LIST_COUNT:] if status == 'OK' and data and data[0] else []
            for uid in reversed(uids):                      # notun age
                try:
                    msg, ts = _fetch_msg(mail, uid, MAIL_LIST_FETCH_BYTES)
                except Exception:
                    continue
                if msg is None:
                    continue
                subject = decode_hdr(msg.get("Subject"), "(No subject)")
                name, addr = parseaddr(decode_hdr(msg.get("From"), ""))
                if not ts:
                    try:
                        ts = parsedate_to_datetime(msg.get("Date")).timestamp()
                    except Exception:
                        ts = time.time()
                code = None
                try:
                    for t in (get_email_texts(msg) or [""]):
                        c, _sc = extract_otp_scored(subject + "\n" + t)
                        if c != "Code not found":
                            code = c
                            break
                    auth = check_email_auth(msg)
                except Exception:
                    auth = {"status": "unknown"}
                bad = auth.get("status") == "fail"
                items.append({
                    "uid": uid.decode(),
                    "from_name": name,
                    "from_addr": addr,
                    "subject": subject,
                    "timestamp": ts,
                    "time": datetime.fromtimestamp(ts, ZoneInfo("Asia/Dhaka")).strftime("%d %b, %I:%M %p"),
                    "code": None if bad else code,           # sondehojonok email-er code dekhai na
                    "auth_fail": bad,
                })
        return jsonify({"folder": folder_req.upper(), "items": items})
    except Exception as e:
        print(f"[MAIL-LIST] {acc['email']}: {type(e).__name__}")
        return jsonify({"error": _error_reason(e)}), 502
    finally:
        try:
            if mail is not None:
                mail.logout()
        except Exception:
            pass

# ======================= Email search: header-only, readonly =======================
@app.route('/api/mail-search', methods=['POST'])
def mail_search():
    """Inbox / Spam-e sender ba subject diye purono email khuje (shudhu header fetch)."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not _rate_ok("mail-search", 20, 60):
        return jsonify({"error": "Too many searches. Please wait a minute and try again."}), 429

    data_in = _json_body()
    email_in = str(data_in.get('email', '')).strip().lower()
    query = re.sub(r"[\\x00-\\x1f\\x7f]", "", str(data_in.get('query', ''))).strip()
    folder_req = str(data_in.get('folder', '')).strip().lower()
    if folder_req not in ('inbox', 'spam') or not query or len(query) > 60:
        return jsonify({"error": "Enter a search term (up to 60 characters)."}), 400

    acc = next((x for x in load_accounts() if str(x.get('email', '')).lower() == email_in), None)
    if not acc:
        return jsonify({"error": "Account not found"}), 404

    mail = None
    try:
        mail = _imap_login(acc)
        fname = _select_view_folder(mail, folder_req)
        items = []
        if fname:
            if any(ord(ch) > 127 for ch in query):
                # imaplib defaults to ASCII. Gmail supports UTF8=ACCEPT for X-GM-RAW.
                try:
                    mail.enable('UTF8=ACCEPT')
                except Exception:
                    pass
                mail._encoding = 'utf-8'
                # Replace IMAP/Gmail query delimiters from user input before building raw query.
                safe_query = query.replace('\\', ' ').replace('"', ' ')
                raw_query = '{from:(' + safe_query + ') subject:(' + safe_query + ')}'
                status, data = mail.uid('search', None, 'X-GM-RAW', '"' + raw_query + '"')
            else:
                quoted = '"' + query + '"'
                status, data = mail.uid('search', None, 'OR', 'FROM', quoted, 'SUBJECT', quoted)

            uids = data[0].split()[-15:] if status == 'OK' and data and data[0] else []
            for uid in reversed(uids):
                try:
                    fetch_status, md = mail.uid(
                        'fetch', uid,
                        '(INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])'
                    )
                    if fetch_status != 'OK':
                        continue
                    msg = None
                    ts = 0
                    raw_meta = b' '.join(
                        (part[0] if isinstance(part, tuple) else part)
                        for part in (md or []) if isinstance(part, (bytes, tuple))
                    ).decode('ascii', 'ignore')
                    m = re.search(r'INTERNALDATE "([^"]+)"', raw_meta)
                    if m:
                        try:
                            ts = datetime.strptime(m.group(1).strip(), "%d-%b-%Y %H:%M:%S %z").timestamp()
                        except Exception:
                            ts = 0
                    for part in md or []:
                        if isinstance(part, tuple) and len(part) > 1:
                            msg = email.message_from_bytes(part[1])
                            break
                    if msg is None:
                        continue
                    subject = decode_hdr(msg.get("Subject"), "(No subject)")
                    from_name, from_addr = parseaddr(decode_hdr(msg.get("From"), ""))
                    if not ts:
                        try:
                            ts = parsedate_to_datetime(msg.get("Date")).timestamp()
                        except Exception:
                            ts = time.time()
                    items.append({
                        "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                        "from_name": from_name,
                        "from_addr": from_addr,
                        "subject": subject,
                        "timestamp": ts,
                        "time": datetime.fromtimestamp(ts, ZoneInfo("Asia/Dhaka")).strftime("%d %b, %I:%M %p"),
                    })
                except Exception:
                    continue
        return _no_store(jsonify({"folder": folder_req.upper(), "items": items}))
    except Exception as e:
        print(f"[MAIL-SEARCH] {acc['email']}: {type(e).__name__}")
        return jsonify({"error": _error_reason(e)}), 502
    finally:
        try:
            if mail is not None:
                mail.logout()
        except Exception:
            pass

# ======================= 2FA (Authenticator / TOTP) =======================
# Gmail-er "Authenticator app" 2FA secret. Secret MongoDB-te shudhu ENCRYPTED obosthay thake (Fernet/AES).
# Chabi (TOTP_ENC_KEY) shudhu Render-er Environment-e thake -- code ba database-e na.
# Browser-e secret kokhono ferot jay na; browser shudhu 6 ongker code pay.
# Chabi baniye nite:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:                      # install na thakle shudhu 2FA bondho, baki app thik cholbe
    Fernet = None
    InvalidToken = Exception

TOTP_ENC_KEY = os.environ.get("TOTP_ENC_KEY", "").strip()
_totp_fernet = None
if not TOTP_ENC_KEY:
    print("WARNING: TOTP_ENC_KEY set kora nai! 2FA (Authenticator) feature bondho thakbe.")
elif Fernet is None:
    print("WARNING: 'cryptography' install nai! 2FA feature bondho thakbe.")
else:
    try:
        _totp_fernet = Fernet(TOTP_ENC_KEY.encode())
    except Exception:
        print("WARNING: TOTP_ENC_KEY thik nai (Fernet key hote hobe). 2FA feature bondho thakbe.")

# ======================= Gmail App Password encryption (database-e) =======================
# App Password database-e plaintext na, "enc1:..." hishebe thake (TOTP_ENC_KEY diye Fernet/AES).
# Email-o bhitore bandha thake, tai ek account-er password onno account-e ulte-palte boshale kaj korbe na.
# TOTP_ENC_KEY na thakle age-r moto plaintext-e thake (app bondho hoy na). Chabi hariye felle login hobe na -- chabi safe rakho.
_PW_PREFIX = "enc1:"

def pw_encrypt(email_addr, plain):
    if _totp_fernet is None or not plain:
        return plain
    blob = json.dumps({"e": str(email_addr or "").lower(), "p": plain}).encode()
    return _PW_PREFIX + _totp_fernet.encrypt(blob).decode()

def pw_decrypt(email_addr, stored):
    """Encrypted hole plaintext ferot dey. Plaintext (purono) hole jemon ache temon. Pore na gele "" (login fail hobe)."""
    if not isinstance(stored, str) or not stored.startswith(_PW_PREFIX):
        return stored
    if _totp_fernet is None:
        print("[SEC] App Password encrypted kintu TOTP_ENC_KEY nai -- decrypt kora gelo na")
        return ""
    try:
        d = json.loads(_totp_fernet.decrypt(stored[len(_PW_PREFIX):].encode()).decode())
        if d.get("e") != str(email_addr or "").lower():
            raise ValueError("mismatch")
        return d["p"]
    except Exception as e:
        print(f"[SEC] App Password decrypt hoyni ({type(e).__name__}) -- TOTP_ENC_KEY age-r motoi ache to?")
        return ""

def ensure_accounts_unique_index():
    """accounts.email unique -- duto request ekshathe ashleo ekoi Gmail duibar dhukbe na.
    Database-e age-i duplicate thakle index banano jay na: tokhon shudhu warning dei, app cholte thake."""
    if accounts_collection is None:
        return
    try:
        dups = [d["_id"] for d in accounts_collection.aggregate([
            {"$group": {"_id": "$email", "n": {"$sum": 1}}}, {"$match": {"n": {"$gt": 1}}}])]
        if dups:
            print(f"[DB] accounts-e duplicate email ache ({len(dups)} ta), unique index banano holo na. Age duplicate muchhe felo.")
            return
        accounts_collection.create_index("email", unique=True, name="email_unique")
    except Exception as e:
        print(f"[DB] unique index error: {type(e).__name__}")

def migrate_account_passwords():
    """Purono plaintext App Password gulo ekbar encrypt kore felbe (kichu bodlay na, shudhu database-er value)."""
    if accounts_collection is None or _totp_fernet is None:
        return
    try:
        n = 0
        for d in accounts_collection.find({}, {"email": 1, "password": 1}):
            pw, em = d.get("password"), d.get("email")
            if em and isinstance(pw, str) and pw and not pw.startswith(_PW_PREFIX):
                r = accounts_collection.update_one({"_id": d["_id"], "password": pw},
                                                   {"$set": {"password": pw_encrypt(em, pw)}})
                n += r.modified_count
        if n:
            print(f"[SEC] {n} ta App Password encrypt kora holo")
    except Exception as e:
        print(f"[SEC] password migrate error: {type(e).__name__}")

_TOTP_ALGOS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}

def parse_totp_input(raw):
    """Base32 secret ba otpauth://totp/... link theke {secret, digits, period, algo, issuer}. Vul hole ValueError."""
    raw = str(raw or "").strip()
    if not raw or len(raw) > 2000:
        raise ValueError("Paste the secret key or the otpauth:// link")
    digits, period, algo, issuer, secret = 6, 30, "SHA1", "", raw
    if raw.lower().startswith("otpauth://"):
        u = urlparse(raw)
        if u.netloc.lower() != "totp":
            raise ValueError("Only time-based (TOTP) links are supported")
        q = {k.lower(): v[0] for k, v in parse_qs(u.query).items() if v}
        secret = q.get("secret", "")
        algo = q.get("algorithm", "SHA1").upper()
        try:
            digits = int(q.get("digits", 6))
            period = int(q.get("period", 30))
        except ValueError:
            raise ValueError("The link has wrong digits/period values")
        issuer = (q.get("issuer") or unquote(u.path.lstrip("/")).split(":")[0])[:60]
    secret = re.sub(r"[\s\-]", "", secret).rstrip("=").upper()
    if algo not in _TOTP_ALGOS:
        raise ValueError("Unsupported algorithm")
    if digits not in (6, 7, 8) or not (15 <= period <= 120):
        raise ValueError("Unsupported digits/period")
    if not re.fullmatch(r"[A-Z2-7]{16,128}", secret):
        raise ValueError("The secret key looks wrong (use letters A-Z and digits 2-7, at least 16 characters)")
    return {"secret": secret, "digits": digits, "period": period, "algo": algo, "issuer": issuer}

def totp_at(secret_b32, digits, period, algo, t):
    """RFC 6238. (code, bakI sekend) dey."""
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    mac = hmac.new(key, struct.pack(">Q", int(t // period)), _TOTP_ALGOS[algo]).digest()
    off = mac[-1] & 0x0F
    num = struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF
    return str(num % (10 ** digits)).zfill(digits), period - int(t % period)

_rl_hits = {}
_rl_lock = threading.Lock()
def _rate_ok(name, limit, window):
    """Chhoto in-memory rate limit (IP + name). limit-er beshi hole False."""
    now = time.time()
    key = f"{request.remote_addr}:{name}"
    with _rl_lock:
        hits = [x for x in _rl_hits.get(key, []) if now - x < window]
        ok = len(hits) < limit
        if ok:
            hits.append(now)
        _rl_hits[key] = hits
        if len(_rl_hits) > 2000:
            for k in [k for k, v in _rl_hits.items() if not v or now - v[-1] > window]:
                _rl_hits.pop(k, None)
    return ok

def _totp_ready():
    return _totp_fernet is not None and totp_collection is not None

def _totp_off():
    return jsonify({"error": "2FA is not set up on the server (TOTP_ENC_KEY missing)"}), 503

def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp

def _settings_pw_check(data):
    """Prottek bar settings password jachai kore (session unlock-er upor nirvor kore na).
    Thik hole None, nahole (response, status) ferot dey. Vul-er hishab 'settings' bucket-e jay."""
    secs = lock_remaining('settings')
    if secs:
        return locked_response(secs)
    pw = str((data or {}).get('password', '')).strip()
    if pw and pw_equal(pw, SETTINGS_PASSWORD):
        clear_fails('settings')
        return None
    if not pw:
        return jsonify({"error": "Settings password required", "need_password": True}), 403
    return wrong_password_response('settings', "Incorrect password")

@app.route('/api/totp/status')
def totp_status():
    """Kon kon Gmail-er 2FA secret jora ache (shudhu email-er talika, secret na)."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not _totp_ready():
        return _no_store(jsonify({"enabled": False, "emails": []}))
    try:
        emails = [d["email"] for d in totp_collection.find({}, {"_id": 0, "email": 1})]
    except Exception as e:
        print(f"[TOTP] status: {type(e).__name__}")
        return jsonify({"error": "Could not load 2FA list"}), 500
    return _no_store(jsonify({"enabled": True, "emails": emails}))

@app.route('/api/totp/add', methods=['POST'])
def totp_add():
    """Ekta Gmail-er 2FA secret (key / otpauth link) encrypt kore rakhe. Settings unlock lagbe."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not session.get('settings_unlocked'):
        return jsonify({"error": "Settings locked. Unlock first."}), 403
    if not _totp_ready():
        return _totp_off()
    if not _rate_ok("totp-add", 10, 60):
        return jsonify({"error": "Too many tries. Wait a minute."}), 429
    data = _json_body()
    email_in = str(data.get('email', '')).strip().lower()
    try:
        already = totp_collection.find_one({"email": email_in}, {"_id": 1}) is not None
    except Exception as e:
        print(f"[TOTP] add-check: {type(e).__name__}")
        return jsonify({"error": "Could not save. Try again."}), 500
    if already:                                   # purono key bodlano -> ei request-ei password lagbe
        bad = _settings_pw_check(data)
        if bad is not None:
            return bad
    if not any(str(x.get('email', '')).lower() == email_in for x in load_accounts()):
        return jsonify({"error": "Account not found"}), 404
    try:
        p = parse_totp_input(data.get('secret'))
        code, left = totp_at(p["secret"], p["digits"], p["period"], p["algo"], time.time())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({"error": "The secret key looks wrong"}), 400
    blob = _totp_fernet.encrypt(json.dumps({"e": email_in, "s": p["secret"]}).encode()).decode()
    try:
        totp_collection.update_one(
            {"email": email_in},
            {"$set": {"email": email_in, "secret_enc": blob, "digits": p["digits"], "period": p["period"],
                      "algo": p["algo"], "issuer": p["issuer"], "updated_at": datetime.now()},
             "$setOnInsert": {"created_at": datetime.now()}},
            upsert=True)
    except Exception as e:
        print(f"[TOTP] add: {type(e).__name__}")
        return jsonify({"error": "Could not save. Try again."}), 500
    # Prothom code sathe-i dei, jate Google-er "Verify" ghor-e sathe sathe bosano jay
    return _no_store(jsonify({"message": "2FA added", "code": code, "remaining": left,
                              "period": p["period"], "digits": p["digits"]}))

@app.route('/api/totp/code', methods=['POST'])
def totp_code():
    """Ekta Gmail-er ekhonkar 2FA code. Secret kokhono ferot dey na."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not _totp_ready():
        return _totp_off()
    if not _rate_ok("totp-code", 60, 60):
        return jsonify({"error": "Too many requests. Wait a moment."}), 429
    email_in = str(_json_body().get('email', '')).strip().lower()
    try:
        doc = totp_collection.find_one({"email": email_in}, {"_id": 0})
    except Exception as e:
        print(f"[TOTP] code: {type(e).__name__}")
        return jsonify({"error": "Could not load 2FA. Try again."}), 500
    if not doc:
        return jsonify({"error": "No 2FA set for this Gmail", "not_set": True}), 404
    try:
        plain = json.loads(_totp_fernet.decrypt(doc["secret_enc"].encode()).decode())
        if plain.get("e") != email_in:                    # onno account-er secret ulte-palte rakha hole code dei na
            raise ValueError("mismatch")
        now = time.time()
        code, left = totp_at(plain["s"], doc["digits"], doc["period"], doc["algo"], now)
    except Exception:
        return jsonify({"error": "Could not read the saved 2FA key (is TOTP_ENC_KEY the same as before?)"}), 500
    return _no_store(jsonify({"code": code, "remaining": left, "period": doc["period"],
                              "digits": doc["digits"], "issuer": doc.get("issuer", ""),
                              "server_time": int(now)}))

@app.route('/api/totp/delete', methods=['POST'])
def totp_delete():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not session.get('settings_unlocked'):
        return jsonify({"error": "Settings locked. Unlock first."}), 403
    if totp_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    bad = _settings_pw_check_json_body()   # session unlock thakleo password lagbe
    if bad is not None:
        return bad
    email_in = str(_json_body().get('email', '')).strip().lower()
    try:
        r = totp_collection.delete_one({"email": email_in})
    except Exception as e:
        print(f"[TOTP] delete: {type(e).__name__}")
        return jsonify({"error": "Could not remove. Try again."}), 500
    if r.deleted_count == 0:
        return jsonify({"error": "No 2FA set for this Gmail"}), 404
    return jsonify({"message": "2FA removed"})

# ---------------- 2FA Services (social media / website / app -- Gmail chhara) ----------------
def _svc_ready():
    return _totp_fernet is not None and svc_collection is not None

def _svc_clean(data):
    name = re.sub(r"\s+", " ", str(data.get('name', ''))).strip()
    label = re.sub(r"\s+", " ", str(data.get('label', ''))).strip()
    if not name:
        raise ValueError("Enter the service name")
    if len(name) > 40:
        raise ValueError("Service name is too long (max 40)")
    if len(label) > 60:
        raise ValueError("Account name is too long (max 60)")
    return name, label

def _svc_public(doc, now):
    """Ekta service-er ekhonkar code. Secret kokhono ferot dey na."""
    out = {"id": doc["sid"], "name": doc.get("name", ""), "label": doc.get("label", ""),
           "period": doc.get("period", 30), "digits": doc.get("digits", 6),
           "pinned": bool(doc.get("pinned"))}
    try:
        plain = json.loads(_totp_fernet.decrypt(doc["secret_enc"].encode()).decode())
        if plain.get("i") != doc["sid"]:                  # onno service-er secret ulte-palte rakha hole code dei na
            raise ValueError("mismatch")
        code, left = totp_at(plain["s"], doc["digits"], doc["period"], doc["algo"], now)
        out.update({"code": code, "remaining": left})
    except Exception:
        out.update({"code": "", "remaining": out["period"], "unreadable": True})
    return out

@app.route('/api/svc/list')
def svc_list():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not _svc_ready():
        return _totp_off()
    if not _rate_ok("svc-list", 120, 60):
        return jsonify({"error": "Too many requests. Wait a moment."}), 429
    try:
        docs = list(svc_collection.find({}, {"_id": 0}))
    except Exception as e:
        print(f"[SVC] list: {type(e).__name__}")
        return jsonify({"error": "Could not load services. Try again."}), 500
    docs.sort(key=lambda d: (not d.get("pinned"), str(d.get("name", "")).lower(), d.get("created_at") or datetime.min))
    now = time.time()
    return _no_store(jsonify({"services": [_svc_public(d, now) for d in docs], "server_time": int(now)}))

@app.route('/api/svc/add', methods=['POST'])
def svc_add():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not _svc_ready():
        return _totp_off()
    if not _rate_ok("svc-add", 15, 60):
        return jsonify({"error": "Too many tries. Wait a minute."}), 429
    data = _json_body()
    bad = _settings_pw_check(data)                          # prottek bar settings password lagbe
    if bad is not None:
        return bad
    try:
        name, label = _svc_clean(data)
        p = parse_totp_input(data.get('secret'))
        totp_at(p["secret"], p["digits"], p["period"], p["algo"], time.time())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({"error": "The secret key looks wrong"}), 400
    try:
        if svc_collection.count_documents({}) >= 500:
            return jsonify({"error": "Too many services saved (max 500)"}), 400
        sid = uuid.uuid4().hex
        blob = _totp_fernet.encrypt(json.dumps({"i": sid, "s": p["secret"]}).encode()).decode()
        now_dt = datetime.now()
        svc_collection.insert_one({"sid": sid, "name": name, "label": label, "secret_enc": blob,
                                   "digits": p["digits"], "period": p["period"], "algo": p["algo"],
                                   "issuer": p["issuer"], "created_at": now_dt, "updated_at": now_dt})
    except Exception as e:
        print(f"[SVC] add: {type(e).__name__}")
        return jsonify({"error": "Could not save. Try again."}), 500
    return _no_store(jsonify({"message": "Service added", "id": sid}))

@app.route('/api/svc/update', methods=['POST'])
def svc_update():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not _svc_ready():
        return _totp_off()
    if not _rate_ok("svc-update", 20, 60):
        return jsonify({"error": "Too many tries. Wait a minute."}), 429
    data = _json_body()
    bad = _settings_pw_check(data)
    if bad is not None:
        return bad
    sid = str(data.get('id', '')).strip()
    try:
        doc = svc_collection.find_one({"sid": sid}, {"_id": 0, "sid": 1})
    except Exception as e:
        print(f"[SVC] update-find: {type(e).__name__}")
        return jsonify({"error": "Could not save. Try again."}), 500
    if not doc:
        return jsonify({"error": "Service not found"}), 404
    new_secret = str(data.get('secret', '')).strip()
    fields = {"updated_at": datetime.now()}
    try:
        fields["name"], fields["label"] = _svc_clean(data)
        if new_secret:                                      # khali thakle purono key-i thake
            p = parse_totp_input(new_secret)
            totp_at(p["secret"], p["digits"], p["period"], p["algo"], time.time())
            fields.update({"secret_enc": _totp_fernet.encrypt(json.dumps({"i": sid, "s": p["secret"]}).encode()).decode(),
                           "digits": p["digits"], "period": p["period"], "algo": p["algo"], "issuer": p["issuer"]})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({"error": "The secret key looks wrong"}), 400
    try:
        svc_collection.update_one({"sid": sid}, {"$set": fields})
    except Exception as e:
        print(f"[SVC] update: {type(e).__name__}")
        return jsonify({"error": "Could not save. Try again."}), 500
    return _no_store(jsonify({"message": "Service updated"}))

@app.route('/api/svc/pin', methods=['POST'])
def svc_pin():
    """Service-ke list-er upore pin / unpin. Secret na, tai password lage na (Gmail pin-er moto)."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if svc_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    if not _rate_ok("svc-pin", 60, 60):
        return jsonify({"error": "Too many requests. Wait a moment."}), 429
    data = _json_body()
    sid = str(data.get('id', '')).strip()
    pinned = bool(data.get('pinned'))
    try:
        r = svc_collection.update_one({"sid": sid}, {"$set": {"pinned": pinned}})
    except Exception as e:
        print(f"[SVC] pin: {type(e).__name__}")
        return jsonify({"error": "Could not save pin. Try again."}), 500
    if r.matched_count == 0:
        return jsonify({"error": "Service not found"}), 404
    return _no_store(jsonify({"pinned": pinned}))

@app.route('/api/svc/delete', methods=['POST'])
def svc_delete():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if svc_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    data = _json_body()
    bad = _settings_pw_check(data)
    if bad is not None:
        return bad
    sid = str(data.get('id', '')).strip()
    try:
        r = svc_collection.delete_one({"sid": sid})
    except Exception as e:
        print(f"[SVC] delete: {type(e).__name__}")
        return jsonify({"error": "Could not remove. Try again."}), 500
    if r.deleted_count == 0:
        return jsonify({"error": "Service not found"}), 404
    return jsonify({"message": "Service removed"})

@app.route('/api/push-status')
def push_status():
    return jsonify({"enabled": ENABLE_PUSH})

@app.route('/api/stream')
def stream():
    """Server-Sent Events: notun code ashle sathe sathe browser-e pathay."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not ENABLE_PUSH and not _waiters:             # ENABLE_PUSH=0 hole shudhu ⏳ watch cholar somoy
        return jsonify({"error": "Push disabled"}), 503
    q = queue.Queue(maxsize=100)
    with _subs_lock:
        if len(_subs) >= MAX_SUBSCRIBERS:
            return jsonify({"error": "Too many live connections"}), 503
        _subs.add(q)

    def gen():
        started = time.time()
        try:
            yield "retry: 3000\n\n"
            yield "event: hello\ndata: {}\n\n"
            while time.time() - started < STREAM_MAX_SECONDS:
                if time.time() - _last_user_activity > IDLE_TIMEOUT:
                    yield "event: expired\ndata: {}\n\n"      # kew kaj kortese na -> stream bondho
                    break
                if not ENABLE_PUSH and not _waiters:         # ⏳ watch shesh -> stream-o bondho
                    break
                try:
                    msg = q.get(timeout=15)
                    yield f"event: otp\ndata: {msg}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"             # connection jiye rakhe
        finally:
            with _subs_lock:
                _subs.discard(q)

    return Response(gen(), mimetype='text/event-stream',
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ======================= Pin (shob device-e eki) =======================
# Pin-er list MongoDB-te thake ({_id: "pins", emails: [...]}), tai phone ar computer-e eki dekhay.
# Poro-poro toggle hoy ($addToSet / $pull), puro list overwrite hoy na -- dui device ekshathe
# alada pin dilew keu karo pin muche felbe na.
PINS_ID = "pins"

def _norm_email(v):
    return str(v or "").strip().lower()

def _read_pins():
    doc = prefs_collection.find_one({"_id": PINS_ID}) or {}
    return [e for e in doc.get("emails", []) if isinstance(e, str)]

@app.route('/api/pins', methods=['GET'])
def get_pins():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if prefs_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    try:
        return jsonify({"pins": _read_pins()})
    except Exception as e:
        print(f"Pins read error: {e}")
        return jsonify({"error": "Could not load pins"}), 500

@app.route('/api/pins/toggle', methods=['POST'])
def toggle_pin_api():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if prefs_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    data = _json_body()
    em = _norm_email(data.get('email'))
    if not em or len(em) > 320:
        return jsonify({"error": "Email required"}), 400
    pinned = bool(data.get('pinned'))
    try:
        if pinned:
            prefs_collection.update_one({"_id": PINS_ID}, {"$addToSet": {"emails": em}}, upsert=True)
        else:
            prefs_collection.update_one({"_id": PINS_ID}, {"$pull": {"emails": em}})
        return jsonify({"pins": _read_pins()})
    except Exception as e:
        print(f"Pins write error: {e}")
        return jsonify({"error": "Could not save pin"}), 500

@app.route('/api/pins/merge', methods=['POST'])
def merge_pins_api():
    """Ek-bar: purono browser-er local pin gulo server-e tule dey (kichu muche na)."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if prefs_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    raw = _json_body().get('emails')
    known = {_norm_email(a.get('email')) for a in load_accounts()}
    emails = []
    if isinstance(raw, list):
        for x in raw[:200]:
            em = _norm_email(x)
            if em and em in known and em not in emails:     # shudhu ashol account-er pin
                emails.append(em)
    try:
        if emails:
            prefs_collection.update_one({"_id": PINS_ID}, {"$addToSet": {"emails": {"$each": emails}}}, upsert=True)
        return jsonify({"pins": _read_pins()})
    except Exception as e:
        print(f"Pins merge error: {e}")
        return jsonify({"error": "Could not save pins"}), 500

@app.route('/api/accounts-list')
def accounts_list():
    """Shudhu Gmail address-er list (Gmail check chhara). Check na kora account popup-e dekhate."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if accounts_collection is None:
        return jsonify({"emails": []})
    try:
        docs = accounts_collection.find({}, {'_id': 0, 'email': 1})
        return jsonify({"emails": [d['email'] for d in docs if d.get('email')]})
    except Exception as e:
        print(f"Accounts list error: {e}")
        return jsonify({"emails": []})

@app.route('/api/accounts-count')
def accounts_count():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if accounts_collection is None:
        return jsonify({"count": 0})
    try:
        return jsonify({"count": accounts_collection.count_documents({})})
    except Exception as e:
        print(f"Count error: {e}")
        return jsonify({"count": 0})

@app.route('/api/delete-account', methods=['POST'])
def delete_account():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if accounts_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500

    data = _json_body()
    email_input = str(data.get('email', '')).strip()
    if not email_input:
        return jsonify({"error": "Email required"}), 400

    # Protibar settings password ba fingerprint/face lagbe
    if data.get('use_passkey'):
        verified_at = session.pop('delete_auth_at', 0)
        if not verified_at or (time.time() - verified_at) > 60:
            return jsonify({"error": "Fingerprint verification expired. Try again."}), 401
    else:
        secs = lock_remaining('settings')
        if secs:
            return locked_response(secs)
        pw = str(data.get('password', '')).strip()
        if not pw_equal(pw, SETTINGS_PASSWORD):
            return wrong_password_response('settings', "Incorrect password")
        clear_fails('settings')

    result = accounts_collection.delete_one({"email": email_input})
    if result.deleted_count == 0:
        return jsonify({"error": "Account not found"}), 404
    if totp_collection is not None:
        try:
            totp_collection.delete_one({"email": email_input.lower()})
        except Exception as e:
            print(f"2FA cleanup error: {type(e).__name__}")
    if prefs_collection is not None:
        try:
            prefs_collection.update_one({"_id": PINS_ID}, {"$pull": {"emails": _norm_email(email_input)}})
        except Exception as e:
            print(f"Pin cleanup error: {e}")
    return jsonify({"message": "Account removed"})

@app.route('/api/add-account', methods=['POST'])
def add_account():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not session.get('settings_unlocked'):
        return jsonify({"error": "Settings locked. Unlock first."}), 403

    data = _json_body()
    email_input = str(data.get('email') or '').strip().lower()
    password_input = re.sub(r"\s+", "", str(data.get('password') or ''))   # App Password-er majher space bad

    if not email_input or not password_input:
        return jsonify({"error": "Email and App Password required"}), 400
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email_input):
        return jsonify({"error": "Enter a valid email address"}), 400

    if accounts_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500

    if accounts_collection.find_one({"email": email_input}):
        return jsonify({"error": "Account already exists!"}), 400

    # Save korar age Gmail-e login kore dekhi, jate vul password save na hoy
    try:
        test_mail = _imap_login({"email": email_input, "password": password_input})
        try:
            test_mail.logout()
        except Exception:
            pass
    except Exception as e:
        reason = _error_reason(e)
        if reason.startswith("Login failed"):
            return jsonify({"error": "Login failed. Check the email and App Password (2-Step Verification must be on)."}), 400
        return jsonify({"error": f"Could not verify with Gmail ({reason}). Please try again."}), 400

    try:
        accounts_collection.insert_one({
            "email": email_input,
            "password": pw_encrypt(email_input, password_input),
            "created_at": datetime.now()
        })
    except DuplicateKeyError:                       # check-er pore, insert-er age onno request dhukiye felechhe
        return jsonify({"error": "Account already exists!"}), 400

    return jsonify({"message": "Account verified and added!"})

# ======================= Backup / Import / Bulk add =======================
# Backup file: shob Gmail + App Password ekta password diye ENCRYPT kora (PBKDF2-SHA256 + Fernet/AES).
# File-er password server-e save hoy na -- bhule gele file khola jabe na.
import concurrent.futures

BACKUP_KDF_ITER = 600000
BACKUP_MAX_ACCOUNTS = 500        # ekta backup file-e max
BULK_VERIFY_MAX = 30             # paste kora list-e Gmail-e login kore dekhbe, tai ekbare max 30
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_BULK_LINE_RE = re.compile(r"^\s*([^\s,;:|]+@[^\s,;:|]+)[\s,;:|]+(.+?)\s*$")

def _backup_key(password, salt, iters):
    raw = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iters, dklen=32)
    return base64.urlsafe_b64encode(raw)

def _verify_gmail_login(email_addr, pw):
    """Thik hole None, nahole chhoto karon (string)."""
    try:
        m = _imap_login({"email": email_addr, "password": pw})
        try:
            m.logout()
        except Exception:
            pass
        return None
    except Exception as e:
        reason = _error_reason(e)
        if reason.startswith("Login failed"):
            return "Login failed - check App Password"
        return "Could not verify with Gmail - try again"

def _save_accounts(items, verify):
    """items = [(email, app_password)]. Already thaka account skip kore. Password kokhono response-e jay na."""
    seen, clean = set(), []
    invalid = 0
    for em, pw in items:
        em = str(em or '').strip().lower()
        pw = re.sub(r"\s+", "", str(pw or ''))
        if not _EMAIL_RE.fullmatch(em) or not pw:
            invalid += 1
            continue
        if em in seen:
            continue
        seen.add(em)
        clean.append((em, pw))

    existing = {d.get('email') for d in accounts_collection.find({}, {'_id': 0, 'email': 1})}
    todo = [(em, pw) for em, pw in clean if em not in existing]
    skipped = [em for em, _ in clean if em in existing]

    failed, ok = [], []
    if verify and todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futs = {pool.submit(_verify_gmail_login, em, pw): (em, pw) for em, pw in todo}
            for f in concurrent.futures.as_completed(futs):
                em, pw = futs[f]
                try:
                    err = f.result()
                except Exception:
                    err = "Could not verify with Gmail - try again"
                if err:
                    failed.append({"email": em, "reason": err})
                else:
                    ok.append((em, pw))
    else:
        ok = todo

    added = 0
    for em, pw in ok:
        try:
            accounts_collection.insert_one({"email": em, "password": pw_encrypt(em, pw), "created_at": datetime.now()})
            added += 1
        except DuplicateKeyError:
            skipped.append(em)
        except Exception as e:
            print(f"Bulk insert error: {type(e).__name__}")
            failed.append({"email": em, "reason": "Could not save"})
    return {"added": added, "skipped": skipped, "failed": failed, "invalid": invalid}

# ---------- Backup-e 2FA (Gmail TOTP + 2FA Services) ----------
def _export_2fa():
    """DB-r encrypted secret server-e decrypt kore plain list dey (backup-password diye abar encrypt hobe)."""
    totp_out, svc_out, bad = [], [], 0
    if _totp_fernet is None:
        return totp_out, svc_out, 0
    if totp_collection is not None:
        for d in totp_collection.find({}, {'_id': 0}):
            try:
                plain = json.loads(_totp_fernet.decrypt(d["secret_enc"].encode()).decode())
                if plain.get("e") != d.get("email"):
                    raise ValueError("mismatch")
                totp_out.append({"email": d["email"], "secret": plain["s"], "digits": d["digits"],
                                 "period": d["period"], "algo": d["algo"], "issuer": d.get("issuer", "")})
            except Exception:
                bad += 1
    if svc_collection is not None:
        for d in svc_collection.find({}, {'_id': 0}):
            try:
                plain = json.loads(_totp_fernet.decrypt(d["secret_enc"].encode()).decode())
                if plain.get("i") != d.get("sid"):
                    raise ValueError("mismatch")
                svc_out.append({"name": d.get("name", ""), "label": d.get("label", ""), "secret": plain["s"],
                                "digits": d["digits"], "period": d["period"], "algo": d["algo"],
                                "issuer": d.get("issuer", "")})
            except Exception:
                bad += 1
    return totp_out, svc_out, bad


def _clean_totp_entry(e):
    """Backup-er ekta 2FA entry thik ache kina dekhe; thik hole dict, nahole None."""
    try:
        if not isinstance(e, dict):
            return None
        secret = re.sub(r"[\s\-]", "", str(e.get("secret", ""))).rstrip("=").upper()
        digits, period, algo = int(e.get("digits", 6)), int(e.get("period", 30)), str(e.get("algo", "SHA1")).upper()
        if algo not in _TOTP_ALGOS or digits not in (6, 7, 8) or not (15 <= period <= 120):
            return None
        if not re.fullmatch(r"[A-Z2-7]{16,128}", secret):
            return None
        totp_at(secret, digits, period, algo, time.time())          # decode hocche kina
        return {"secret": secret, "digits": digits, "period": period, "algo": algo,
                "issuer": str(e.get("issuer", ""))[:60]}
    except Exception:
        return None


def _import_2fa(payload, selected_set):
    """selected_set = None hole shob. Gmail-er 2FA shudhu sei account-er jonno, je account ekhon DB-te ache.
    Age theke thaka 2FA key KOKHONO overwrite hoy na. Notun TOTP_ENC_KEY diye encrypt hoy."""
    res = {"totp_added": 0, "totp_skipped": 0, "svc_added": 0, "svc_skipped": 0, "twofa_failed": 0}
    if _totp_fernet is None or totp_collection is None:
        res["twofa_failed"] = len(payload.get("totp", []) or []) + len(payload.get("services", []) or [])
        return res
    accounts = {str(d.get("email", "")).lower() for d in accounts_collection.find({}, {'_id': 0, 'email': 1})}

    for t in (payload.get("totp") or [])[:BACKUP_MAX_ACCOUNTS]:
        em = str((t or {}).get("email", "")).strip().lower() if isinstance(t, dict) else ""
        if not em or em not in accounts or (selected_set is not None and em not in selected_set):
            continue
        c = _clean_totp_entry(t)
        if not c:
            res["twofa_failed"] += 1
            continue
        blob = _totp_fernet.encrypt(json.dumps({"e": em, "s": c["secret"]}).encode()).decode()
        now = datetime.now()
        try:
            r = totp_collection.update_one(                         # upsert + $setOnInsert = already thakle chhuye na
                {"email": em},
                {"$setOnInsert": {"email": em, "secret_enc": blob, "digits": c["digits"], "period": c["period"],
                                  "algo": c["algo"], "issuer": c["issuer"], "created_at": now, "updated_at": now}},
                upsert=True)
            if r.upserted_id is not None:
                res["totp_added"] += 1
            else:
                res["totp_skipped"] += 1
        except Exception as ex:
            print(f"[BACKUP] totp import: {type(ex).__name__}")
            res["twofa_failed"] += 1

    if svc_collection is not None:
        seen = set()
        try:
            count = svc_collection.count_documents({})
            for d in svc_collection.find({}, {'_id': 0}):
                try:
                    plain = json.loads(_totp_fernet.decrypt(d["secret_enc"].encode()).decode())
                    seen.add((str(d.get("name", "")).lower(), str(d.get("label", "")).lower(), plain["s"]))
                except Exception:
                    pass
        except Exception:
            count = 0
        for s in (payload.get("services") or [])[:500]:
            c = _clean_totp_entry(s)
            name = re.sub(r"\s+", " ", str((s or {}).get("name", ""))).strip()[:40] if isinstance(s, dict) else ""
            label = re.sub(r"\s+", " ", str((s or {}).get("label", ""))).strip()[:60] if isinstance(s, dict) else ""
            if not c or not name:
                res["twofa_failed"] += 1
                continue
            key = (name.lower(), label.lower(), c["secret"])
            if key in seen:
                res["svc_skipped"] += 1
                continue
            if count >= 500:
                res["twofa_failed"] += 1
                continue
            try:
                sid = uuid.uuid4().hex
                blob = _totp_fernet.encrypt(json.dumps({"i": sid, "s": c["secret"]}).encode()).decode()
                now = datetime.now()
                svc_collection.insert_one({"sid": sid, "name": name, "label": label, "secret_enc": blob,
                                           "digits": c["digits"], "period": c["period"], "algo": c["algo"],
                                           "issuer": c["issuer"], "created_at": now, "updated_at": now})
                seen.add(key)
                count += 1
                res["svc_added"] += 1
            except Exception as ex:
                print(f"[BACKUP] svc import: {type(ex).__name__}")
                res["twofa_failed"] += 1
    return res


def _bulk_guard():
    """logged_in + settings unlock + DB. Thik hole None, nahole (response, status)."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not session.get('settings_unlocked'):
        return jsonify({"error": "Settings locked. Unlock first."}), 403
    if accounts_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    return None

@app.route('/api/bulk-add', methods=['POST'])
def bulk_add():
    bad = _bulk_guard()
    if bad:
        return bad
    if not _rate_ok('bulk-add', 6, 600):
        return jsonify({"error": "Too many tries. Wait a few minutes."}), 429
    text = str(_json_body().get('text') or '')
    if len(text) > 20000:
        return jsonify({"error": "List is too long"}), 400
    items, bad_lines = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = _BULK_LINE_RE.match(line)
        if m:
            items.append((m.group(1), m.group(2)))
        else:
            bad_lines.append(n)
    if not items and not bad_lines:
        return jsonify({"error": "Paste at least one line: email and App Password"}), 400
    if len(items) > BULK_VERIFY_MAX:
        return jsonify({"error": f"Add up to {BULK_VERIFY_MAX} accounts at once (each one is checked with Gmail). Use a backup file for more."}), 400
    res = _save_accounts(items, verify=True)
    res["bad_lines"] = bad_lines
    return _no_store(jsonify(res))

@app.route('/api/backup/export', methods=['POST'])
def backup_export():
    bad = _bulk_guard()
    if bad:
        return bad
    if Fernet is None:
        return jsonify({"error": "Backup is not available (cryptography is not installed on the server)"}), 503
    data = _json_body()
    pwbad = _settings_pw_check(data)          # prottek bar settings password
    if pwbad:
        return pwbad
    if not _rate_ok('backup-export', 5, 600):
        return jsonify({"error": "Too many tries. Wait a few minutes."}), 429
    bpw = str(data.get('backup_password') or '')
    if len(bpw) < 8:
        return jsonify({"error": "Backup password must be at least 8 characters"}), 400
    if len(bpw) > 200:
        return jsonify({"error": "Backup password is too long"}), 400
    try:
        docs = list(accounts_collection.find({}, {'_id': 0, 'email': 1, 'password': 1}))
        accounts = []
        for d in docs:
            plain = pw_decrypt(d.get('email'), d.get('password'))   # backup-e plaintext jay (backup password diye encrypt hoy)
            if d.get('email') and plain:
                accounts.append({"email": d['email'], "password": plain})
        totp_out, svc_out, bad2fa = _export_2fa()
        payload = json.dumps({"app": "gmail-otp-dashboard", "exported_at": datetime.now(timezone.utc).isoformat(),
                              "accounts": accounts, "totp": totp_out, "services": svc_out}).encode('utf-8')
        salt = os.urandom(16)
        token = Fernet(_backup_key(bpw, salt, BACKUP_KDF_ITER)).encrypt(payload).decode()
        blob = {"v": 1, "kdf": "pbkdf2-sha256", "iter": BACKUP_KDF_ITER,
                "salt": base64.b64encode(salt).decode(), "data": token}
    except Exception as e:
        print(f"Backup export error: {type(e).__name__}")
        return jsonify({"error": "Could not create the backup"}), 500
    resp = Response(json.dumps(blob).encode('utf-8'), mimetype='application/octet-stream')
    resp.headers['Content-Disposition'] = 'attachment; filename="otp-backup-%s.otpbak"' % datetime.now().strftime('%Y%m%d-%H%M')
    resp.headers['X-Backup-Count'] = str(len(accounts))
    resp.headers['X-Backup-2FA'] = str(len(totp_out) + len(svc_out))
    resp.headers['X-Backup-2FA-Failed'] = str(bad2fa)
    return _no_store(resp)

@app.route('/api/backup/preview', methods=['POST'])
def backup_preview():
    # Backup file decrypt kore shudhu email list pathay
    # App Password / password response-e kono obosthay jay na
    bad = _bulk_guard()
    if bad:
        return bad
    if Fernet is None:
        return jsonify({"error": "Backup is not available (cryptography is not installed on the server)"}), 503
    if (request.content_length or 0) > 2 * 1024 * 1024:
        return jsonify({"error": "File is too big"}), 413
    if not _rate_ok("backup-preview", 12, 600):
        return jsonify({"error": "Too many tries. Wait a few minutes."}), 429
    data = _json_body()
    bpw = str(data.get("backup_password") or "")
    try:
        blob = json.loads(str(data.get("file") or ""))
        if blob.get("v") != 1:
            raise ValueError("version")
        iters = int(blob["iter"])
        if not (100000 <= iters <= 2000000):
            raise ValueError("iter")
        salt = base64.b64decode(blob["salt"])
        token = str(blob["data"]).encode()
    except Exception:
        return jsonify({"error": "This is not a valid backup file"}), 400
    if not bpw:
        return jsonify({"error": "Enter the backup password"}), 400
    try:
        payload = json.loads(Fernet(_backup_key(bpw, salt, iters)).decrypt(token).decode("utf-8"))
        emails_in_file = [
            str(a.get("email", "")).strip().lower()
            for a in payload.get("accounts", [])
            if isinstance(a, dict) and a.get("email")
        ]
    except InvalidToken:
        return jsonify({"error": "Wrong backup password, or the file is damaged"}), 400
    except Exception:
        return jsonify({"error": "Could not read the backup file"}), 400
    if len(emails_in_file) > BACKUP_MAX_ACCOUNTS:
        return jsonify({"error": "Too many accounts in this file"}), 400
    existing = {d.get("email") for d in accounts_collection.find({}, {"_id": 0, "email": 1})}
    items = [{"email": em, "exists": em in existing} for em in emails_in_file]
    exported_at = payload.get("exported_at", "")
    return _no_store(jsonify({
        "items": items, "exported_at": exported_at,
        "totp_count": len(payload.get("totp") or []),
        "svc_count": len(payload.get("services") or []),
    }))


@app.route('/api/backup/import', methods=['POST'])
def backup_import():
    bad = _bulk_guard()
    if bad:
        return bad
    if Fernet is None:
        return jsonify({"error": "Backup is not available (cryptography is not installed on the server)"}), 503
    if (request.content_length or 0) > 2 * 1024 * 1024:
        return jsonify({"error": "File is too big"}), 413
    if not _rate_ok('backup-import', 8, 600):
        return jsonify({"error": "Too many tries. Wait a few minutes."}), 429
    data = _json_body()
    bpw = str(data.get('backup_password') or '')
    try:
        blob = json.loads(str(data.get('file') or ''))
        if blob.get('v') != 1:
            raise ValueError("version")
        iters = int(blob['iter'])
        if not (100000 <= iters <= 2000000):
            raise ValueError("iter")
        salt = base64.b64decode(blob['salt'])
        token = str(blob['data']).encode()
    except Exception:
        return jsonify({"error": "This is not a valid backup file"}), 400
    if not bpw:
        return jsonify({"error": "Enter the backup password"}), 400
    try:
        payload = json.loads(Fernet(_backup_key(bpw, salt, iters)).decrypt(token).decode('utf-8'))
        all_items = [(a.get('email'), a.get('password')) for a in payload.get('accounts', []) if isinstance(a, dict)]
    except InvalidToken:
        return jsonify({"error": "Wrong backup password, or the file is damaged"}), 400
    except Exception:
        return jsonify({"error": "Could not read the backup file"}), 400
    if len(all_items) > BACKUP_MAX_ACCOUNTS:
        return jsonify({"error": "Too many accounts in this file"}), 400
    sel_set = None
    selected = data.get("selected_emails")   # None mane shob, list mane shudhu oi gulo
    if isinstance(selected, list) and selected:
        sel_set = {str(e).strip().lower() for e in selected}
        items = [(em, pw) for em, pw in all_items if str(em or '').strip().lower() in sel_set]
    else:
        items = all_items
    res = _save_accounts(items, verify=False)      # backup-er account age-i verify kora, tai ekhane abar login check na
    res["total"] = len(all_items)
    if data.get("restore_2fa"):
        try:
            res.update(_import_2fa(payload, sel_set))
        except Exception as e:
            print(f"[BACKUP] 2fa import: {type(e).__name__}")
            res["twofa_failed"] = res.get("twofa_failed", 0) + 1
    return _no_store(jsonify(res))

# ---------------- Settings unlock (server-side) ----------------
@app.route('/api/settings-unlock', methods=['POST'])
def settings_unlock():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    secs = lock_remaining('settings')
    if secs:
        return locked_response(secs)
    data = _json_body()
    pw = str(data.get('password', '')).strip()
    if pw_equal(pw, SETTINGS_PASSWORD):
        clear_fails('settings')
        session['settings_unlocked'] = True
        return jsonify({"success": True})
    return wrong_password_response('settings', "Incorrect password")


# ---------------- Passkey: fingerprint / face ----------------
def _json_response(text):
    return Response(text, mimetype='application/json')

@app.route('/api/passkey/register/options', methods=['POST'])
def passkey_register_options():
    if not (session.get('logged_in') and session.get('settings_unlocked')):
        return jsonify({"error": "Unlock settings first"}), 401
    if passkeys_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500

    existing = list(passkeys_collection.find({}, {'_id': 0, 'credential_id': 1}))
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=OWNER_ID,
        user_name="owner",
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p['credential_id']))
            for p in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    session['reg_challenge'] = bytes_to_base64url(options.challenge)
    return _json_response(options_to_json(options))

@app.route('/api/passkey/register/verify', methods=['POST'])
def passkey_register_verify():
    if not (session.get('logged_in') and session.get('settings_unlocked')):
        return jsonify({"error": "Unlock settings first"}), 401
    challenge = session.pop('reg_challenge', None)
    if not challenge or passkeys_collection is None:
        return jsonify({"error": "Registration expired. Try again."}), 400
    try:
        v = verify_registration_response(
            credential=_json_body(),
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
            require_user_verification=True,
        )
    except Exception as e:
        print(f"Passkey register error: {e}")
        return jsonify({"error": "Verification failed"}), 400

    passkeys_collection.insert_one({
        "credential_id": bytes_to_base64url(v.credential_id),
        "public_key": bytes_to_base64url(v.credential_public_key),
        "sign_count": v.sign_count,
        "created_at": datetime.now(),
    })
    return jsonify({"success": True})

@app.route('/api/passkey/auth/options', methods=['POST'])
def passkey_auth_options():
    if passkeys_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500
    creds = list(passkeys_collection.find({}, {'_id': 0, 'credential_id': 1}))
    if not creds:
        return jsonify({"error": "No fingerprint registered yet"}), 404
    options = generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c['credential_id']))
            for c in creds
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session['auth_challenge'] = bytes_to_base64url(options.challenge)
    return _json_response(options_to_json(options))

@app.route('/api/passkey/auth/verify', methods=['POST'])
def passkey_auth_verify():
    body = _json_body()
    purpose = body.get('purpose', 'login')
    credential = body.get('credential')
    if not isinstance(credential, dict):
        credential = {}
    challenge = session.pop('auth_challenge', None)
    if not challenge or passkeys_collection is None:
        return jsonify({"error": "Expired. Try again."}), 400
    if purpose in ('settings', 'delete') and not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401

    stored = passkeys_collection.find_one({"credential_id": credential.get('id')})
    if not stored:
        return jsonify({"error": "Unknown passkey"}), 401
    try:
        v = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
            credential_public_key=base64url_to_bytes(stored['public_key']),
            credential_current_sign_count=stored.get('sign_count', 0),
            require_user_verification=True,
        )
    except Exception as e:
        print(f"Passkey auth error: {e}")
        return jsonify({"error": "Verification failed"}), 401

    passkeys_collection.update_one(
        {"credential_id": stored['credential_id']},
        {"$set": {"sign_count": v.new_sign_count}},
    )
    session['logged_in'] = True
    _mark_active()
    if purpose == 'settings':
        session['settings_unlocked'] = True
    elif purpose == 'delete':
        session['delete_auth_at'] = time.time()
    return jsonify({"success": True})


ensure_accounts_unique_index()
migrate_account_passwords()

if ENABLE_PUSH:
    start_push()


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
