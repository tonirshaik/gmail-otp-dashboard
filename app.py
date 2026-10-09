import os
import imaplib
import email
import re
import time
import json
import threading
import hmac
import math
import socket
import select
import queue
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime, parseaddr
from email.header import decode_header, make_header
import html as html_lib
from zoneinfo import ZoneInfo
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response
from pymongo import MongoClient
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
CORS(app, supports_credentials=True)
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

if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI)
        db = client['gmail_otp_db']
        accounts_collection = db['accounts']
        passkeys_collection = db['passkeys']
        security_collection = db['security']   # login lock-er state (shob worker-e eki)
        print("MongoDB Connected Successfully!")
    except Exception as e:
        print(f"MongoDB Connection Error: {e}")

def load_accounts():
    if accounts_collection is not None:
        try:
            accounts = list(accounts_collection.find({}, {'_id': 0}))
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

def register_fail(bucket):
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
            if fails >= MAX_FAILS:
                security_collection.update_one(
                    {"_id": bucket},
                    {"$set": {"locked_until": now + LOCK_SECONDS, "fails": 0}},
                )
                return LOCK_SECONDS, 0
            return 0, MAX_FAILS - fails
        except Exception as e:
            print(f"Lock write error (memory fallback): {e}")
    with _fail_lock:
        st = _mem_get(bucket)
        st["fails"] += 1
        st["last"] = now
        if st["fails"] >= MAX_FAILS:
            st["locked_until"] = now + LOCK_SECONDS
            st["fails"] = 0
            return LOCK_SECONDS, 0
        return 0, MAX_FAILS - st["fails"]

def clear_fails(bucket):
    if security_collection is not None:
        try:
            security_collection.delete_one({"_id": bucket})
        except Exception as e:
            print(f"Lock clear error: {e}")
    with _fail_lock:
        _fail_state.pop(bucket, None)

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
_BN = {ord(c): str(i) for i, c in enumerate("০১২৩৪৫৬৭৮৯")}

_STRONG_KW = re.compile(r"""(?ix)
    \botp\b | \bpass\s?code\b | \bpin\b | ওটিপি | পিন |
    one[-\s]?time(?:\s+(?:password|passcode|pin|code))? |
    (?:verification|verify|confirmation|security|authentication|authorization|authenticator|
       access|login|log[-\s]?in|sign[-\s]?in|activation|registration|reset|recovery|
       temporary|temp|2fa|two[-\s]?factor|mfa)\s*(?:code|pin|passcode|password|key|token) |
    (?:ভেরিফিকেশন|ভেরিফাই|যাচাই|নিরাপত্তা|সিকিউরিটি|লগইন)\s*কোড | ভেরিফিকেশন
""")
_WEAK_KW = re.compile(r"(?i)\bcode\b|\btoken\b|কোড|\bcódigo\b|\bcodice\b|\bkod\b")

_NEG = re.compile(
    r"(?i)\b(?:order|invoice|tracking|receipt|transaction|txn|amount|price|total|balance|zip|postal|"
    r"phone|tel|call|mobile|a/c|street|st\.|avenue|ave|parkway|road|rd\.|suite|floor|apt|copyright|"
    r"usd|bdt|inr|eur|tk|ref|reference|payment|paid|due|bill|flight|booking|ticket|version|build)\b|[$€£৳%©]")
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
    ("spaced",  re.compile(_LB + r"(\d(?:[ \t]\d){3,7})(?![\w@.]|[ \t]\d)")),
    ("grouped", re.compile(_LB + r"(\d{3,4}[ -]\d{3,4})(?![\w@]|[.,:/-]\d|[ -]\d)")),
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
    r"(?i:\b(?:otp|pass\s?code|pin|code|verification\s+code|security\s+code)\b)\s*(?:is\b)?\s*[:=\-–]?\s*\b([A-Z]{4,10})\b")
_LETTER_STOP = {"CODE", "EMAIL", "YOUR", "THIS", "THAT", "WITH", "FROM", "HERE", "LOGIN", "VERIFY",
                "VALID", "EXPIRES", "GOOGLE", "GITHUB", "ACCOUNT", "PLEASE", "ENTER", "COPY", "PASTE",
                "SIGN", "NOTE", "HELLO", "DEAR", "THANK", "THANKS", "TEAM", "EXPIRE", "MINUTES"}


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
    if kind in ("spaced", "grouped"):
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
    score -= min(n_neg, 2) if strong_hit else min(3 * n_neg, 9)

    if not strong_hit and _MARKETING.search(t[max(0, s - 80):min(len(t), e + 80)]):
        score -= 5

    if kind == "digits":
        if len(text) == 4 and 1990 <= int(text) <= 2035 and kw_val < 7:
            score -= 4
        if re.fullmatch(r"(\d)\1+", text):
            score -= 2 if strong_hit else 8
    return score


def extract_otp(text):
    if not text:
        return "Code not found"

    t = text.translate(_ZW).translate(_BN)
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)      # link-er bhitorer token bad
    t = re.sub(r"\S+@\S+\.\S+", " ", t)               # email address bad
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{2,}", "\n", t)

    kws = _find_keywords(t)
    if not kws and not _INTENT.search(t):
        return "Code not found"
    has_intent = bool(_INTENT.search(t))

    cands = []
    for kind, rx in _CAND:
        for m in rx.finditer(t):
            cands.append((m.start(1), m.end(1), kind, m.group(1)))
    wide = [(s, e) for s, e, k, _ in cands if k in ("spaced", "grouped", "dashed")]
    cands = [c for c in cands
             if c[2] in ("spaced", "grouped", "dashed")
             or not any(ws <= c[0] and c[1] <= we for ws, we in wide)]

    best = None   # (score, -start, output)
    for s, e, kind, raw in cands:
        sc = _score_candidate(t, kws, s, e, kind, raw, has_intent)
        out = re.sub(r"\s+", "", raw) if kind in ("spaced", "grouped") else raw
        if kind == "grouped":
            out = out.replace(" ", "")
        if sc >= 6 and (best is None or (sc, -s) > (best[0], best[1])):
            best = (sc, -s, out)

    # keyword-er thik pashe shudhu bornomala-r code (jemon  "code: ABCDEF")
    for m in _LETTERS.finditer(t):
        word = m.group(1)
        s = m.start(1)
        if word in _LETTER_STOP:
            continue
        if _PROMO_BEFORE.search(t[max(0, m.start() - 40):s]):
            continue
        if _MARKETING.search(t[max(0, s - 80):s + 80]):
            continue
        if best is None or (8, -s) > (best[0], best[1]):
            best = (8, -s, word)

    return best[2] if best else "Code not found"


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

def extract_ttl(text):
    """Email-er text theke code-er meyad (sekende) ber kore. Na pele None.
    Ekadhik meyad paile shobcheye chhotota (nirapod dik)."""
    if not text:
        return None
    t = text.translate(_ZW).translate(_BN)
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)
    t = re.sub(r"\s+", " ", t)
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


def get_email_texts(msg):
    """Email theke text/plain ar html-text, dutoi ber kore (jeta-te code pawa jay)."""
    plain, html_body = "", ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="ignore")
        except Exception:
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


def get_email_body(msg):
    texts = get_email_texts(msg)
    return texts[0] if texts else ""


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
OTP_TTL_SECONDS = 120   # 2 minutes -- shudhu tokhon, jokhon email-e meyad lekha nai

def fetch_email_info(mail, uid):
    """Ekta email download kore OTP, sender, subject, time ber kore."""
    _, msg_data = mail.uid('fetch', uid, '(BODY.PEEK[])')
    for part in msg_data or []:
        if isinstance(part, tuple):
            msg = email.message_from_bytes(part[1])
            subject = decode_hdr(msg.get("Subject"), "No Subject")
            raw_from = decode_hdr(msg.get("From"), "Unknown Sender")
            name, addr = parseaddr(raw_from)
            sender = name or addr or raw_from
            date_hdr = msg.get("Date")

            msg_dt = datetime.now()
            epoch = time.time()          # asol (timezone-thik) unix time, expiry hisabe lage
            if date_hdr:
                try:
                    parsed_dt = parsedate_to_datetime(date_hdr)
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                    epoch = parsed_dt.timestamp()
                    msg_dt = parsed_dt.astimezone(ZoneInfo("Asia/Dhaka")).replace(tzinfo=None)
                except:
                    pass

            otp = "Code not found"
            ttl = None
            bodies = get_email_texts(msg) or [""]
            for body in bodies:
                full = subject + "\n" + body
                otp = extract_otp(full)
                if otp != "Code not found":
                    ttl = extract_ttl(full)          # jei text-e code pelam, shekhane meyad khuji
                    break
            if otp != "Code not found" and ttl is None:
                for body in bodies:                  # oi text-e na thakle onno text-e (plain/html) dekhi
                    ttl = extract_ttl(subject + "\n" + body)
                    if ttl:
                        break
            return {
                "sender": sender,
                "subject": subject,
                "code": None if otp == "Code not found" else otp,
                "time": msg_dt.strftime("%I:%M %p"),
                "timestamp": epoch,
                "ttl_seconds": ttl,                  # None hole default meyad
            }
    return None

def scan_folder(mail, acct_email, folder, limit):
    """Folder-er shesh `limit`-ta email dekhe, OTP thakle result dey."""
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
        if info["code"]:
            return info
    return None

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
            mail_data.append({
                "email": account['email'],
                "sender": result["sender"],
                "subject": result["subject"],
                "code": result["code"],
                "time": result["time"],
                "timestamp": result["timestamp"],
                "age_seconds": max(0, int(time.time() - result["timestamp"])),
                "ttl_seconds": result.get("ttl_seconds") or OTP_TTL_SECONDS,
                "ttl_from_email": bool(result.get("ttl_seconds")),
            })
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

REFRESH_DEADLINE = 25      # sekend. Gunicorn-er default 30s timeout-er age e shesh korte hobe

def get_latest_otps():
    accounts = load_accounts()
    all_codes = []
    threads = []
    lock = threading.Lock()

    def worker(acc):
        local_data = []
        check_gmail(acc, local_data)
        if local_data:
            with lock:
                all_codes.extend(local_data)

    for acc in accounts:
        t = threading.Thread(target=worker, args=(acc,), daemon=True)
        threads.append(t)
        t.start()

    deadline = time.time() + REFRESH_DEADLINE
    for t in threads:
        t.join(max(0, deadline - time.time()))

    with lock:
        results = list(all_codes)

    # Deadline-er moddhe jara sesh kore ni tader "Timeout" card dekhai
    done = {r["email"] for r in results}
    for acc in accounts:
        if acc["email"] not in done:
            results.append({
                "email": acc["email"],
                "sender": "Timeout - Refresh again",
                "subject": "Took too long",
                "code": "Error",
                "time": "N/A",
                "timestamp": 0,
            })

    results.sort(key=lambda x: x['timestamp'], reverse=True)
    return results

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
            accs = {x["email"]: x for x in load_accounts()}
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
    secs = lock_remaining('login')
    if secs:
        return locked_response(secs)
    data = request.json or {}
    password = data.get('password')
    if password and pw_equal(password, MASTER_PASSWORD):
        clear_fails('login')
        session['logged_in'] = True
        _mark_active()
        return jsonify({"success": True})
    return wrong_password_response('login', "Wrong password!")

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('logged_in', None)
    session.pop('settings_unlocked', None)
    return jsonify({"success": True})

@app.route('/api/fetch-otps')
def fetch_otps():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401

    all_otps = get_latest_otps()
    return jsonify(all_otps)

@app.route('/api/refresh-one', methods=['POST'])
def refresh_one():
    """Shudhu ekta Gmail account abar check kore."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    email_in = str((request.json or {}).get('email', '')).strip().lower()
    acc = next((x for x in load_accounts() if str(x.get('email', '')).lower() == email_in), None)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    out = []
    check_gmail(acc, out)
    if not out:
        return jsonify({"error": "No result"}), 500
    return jsonify(out[0])

@app.route('/api/push-status')
def push_status():
    return jsonify({"enabled": ENABLE_PUSH})

@app.route('/api/stream')
def stream():
    """Server-Sent Events: notun code ashle sathe sathe browser-e pathay."""
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not ENABLE_PUSH:
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

    data = request.json or {}
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
    return jsonify({"message": "Account removed"})

@app.route('/api/add-account', methods=['POST'])
def add_account():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    if not session.get('settings_unlocked'):
        return jsonify({"error": "Settings locked. Unlock first."}), 403

    data = request.json or {}
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

    accounts_collection.insert_one({
        "email": email_input,
        "password": password_input,
        "created_at": datetime.now()
    })

    return jsonify({"message": "Account verified and added!"})

# ---------------- Settings unlock (server-side) ----------------
@app.route('/api/settings-unlock', methods=['POST'])
def settings_unlock():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    secs = lock_remaining('settings')
    if secs:
        return locked_response(secs)
    data = request.json or {}
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
            credential=request.get_json(),
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
    body = request.get_json() or {}
    purpose = body.get('purpose', 'login')
    credential = body.get('credential') or {}
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


if ENABLE_PUSH:
    start_push()


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
