import os
import imaplib
import email
import re
import time
import json
import threading
import hmac
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response
from pymongo import MongoClient
from flask_cors import CORS
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

# Session Secure Key & Master Password
app.secret_key = os.environ.get("SECRET_KEY", "your_secret_session_key_123")
MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "12342")
MONGO_URI = os.environ.get("MONGO_URI", "")
# Settings password ekhon server-e check hoy (age index.html-e hardcode chilo)
SETTINGS_PASSWORD = os.environ.get("SETTINGS_PASSWORD", "889900")

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

if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI)
        db = client['gmail_otp_db']
        accounts_collection = db['accounts']
        passkeys_collection = db['passkeys']
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

def extract_otp(text):
    if not text:
        return "Code not found"

    text = re.sub(r'\s+', ' ', text)  # normalize spaces

    # ---------- Helper ----------
    def is_year(s):
        return s.isdigit() and len(s) == 4 and 2000 <= int(s) <= 2035

    def is_valid_code(code):
        if not code:
            return False
        code = code.strip()
        if len(code) < 4 or len(code) > 10:
            return False
        if is_year(code):
            return False
        if code.isalpha():
            return False
        ignore = {
            'code', 'pin', 'otp', 'password', 'none', 'your', 'is', 'the',
            'and', 'for', 'with', 'from', 'http', 'https', 'gmail', 'google',
            'html', 'github', 'microsoft', 'facebook', 'apple', 'amazon',
            'twitter', 'linkedin', 'please', 'click', 'here', 'sign', 'link',
            'copy', 'paste', 'enter', 'valid', 'expire', 'minute', 'hour',
            'have', 'donot', 'sudo', 'true', 'false', 'verify', 'token',
            'number', 'order', 'invoice', 'reference', 'zip', 'tracking'
        }
        if code.lower() in ignore:
            return False
        return True

    def clean_code(raw):
        raw = raw.strip()
        raw = re.split(r'\s+(?:to|for|is|and|or|the|a|an|in|on|at|by|will|has)\b', raw, flags=re.IGNORECASE)[0]
        clean = re.sub(r'\s+', '', raw)
        clean = re.sub(r'[^A-Z0-9\-]+$', '', clean, flags=re.IGNORECASE)
        return clean

    def has_bad_context(code, window=55):
        pos = text.lower().find(code.lower()) if code else -1
        if pos == -1:
            # try finding digits only version
            pos = text.find(code)
        if pos == -1:
            return False
        context = text[max(0, pos - window):pos + window].lower()
        bad_words = [
            'order', 'invoice', 'tracking', 'reference', 'receipt',
            'transaction', 'amount', 'price', 'zip code', 'postal code',
            'order id', 'order number', 'invoice number', 'tracking number',
            'ref no', 'ref:', 'txn', 'payment', 'total', 'bdt', 'usd', 'inr',
            'error code', 'status code', 'promo code', 'coupon code'
        ]
        return any(bw in context for bw in bad_words)

    # ---------- 1. Keyword-based (most reliable) ----------
    keyword_patterns = [
        # Strong OTP phrases
        r'(?:your\s+)?(?:otp|verification\s*code|security\s*code|auth(?:entication)?\s*code|one[-\s]?time\s*(?:password|code)|login\s*code|access\s*code)[\s:#\-]*(?:is[\s:#\-]*)?([A-Z0-9][A-Z0-9\s\-]{2,14})',
        # "code is XXX" / "code: XXX" / "code XXX"
        r'(?<![a-z])(?:code|otp|pin)[\s:#\-]+(?:is[\s:#\-]*)?([A-Z0-9][A-Z0-9\s\-]{2,14})',
        # "enter/use/type the code"
        r'(?:enter|use|type)\s+(?:the\s+)?(?:code|otp|pin)[\s:#\-]*([A-Z0-9][A-Z0-9\s\-]{2,14})',
        # "PIN is" / "PIN:"
        r'(?<![a-z])pin[\s:#\-]+(?:is[\s:#\-]*)?([A-Z0-9][A-Z0-9\s\-]{2,10})',
    ]

    for pat in keyword_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            clean = clean_code(m.group(1))
            if is_valid_code(clean) and any(c.isdigit() for c in clean):
                if not has_bad_context(clean):
                    return clean

    # ---------- 2. G- codes (Google style) ----------
    gcode = re.search(r'\bG-[A-Z0-9]{4,10}\b', text, re.IGNORECASE)
    if gcode:
        return gcode.group(0)

    # ---------- 3. Spaced digit codes like "1 2 3 4 5 6" ----------
    spaced = re.search(r'\b(\d(?:\s+\d){3,7})\b', text)
    if spaced:
        clean_spaced = re.sub(r'\s+', '', spaced.group(0))
        if is_valid_code(clean_spaced) and not has_bad_context(clean_spaced):
            return clean_spaced

    # ---------- 4. Prefer pure 6-digit codes ----------
    six_digits = re.findall(r'(?<!\d)\d{6}(?!\d)', text)
    for d in six_digits:
        if is_valid_code(d) and not has_bad_context(d):
            return d

    # ---------- 5. 4 / 7 / 8 digit (skip lonely 5-digit) ----------
    other_digits = re.findall(r'(?<!\d)\d{4,8}(?!\d)', text)
    for d in other_digits:
        if not is_valid_code(d):
            continue
        if has_bad_context(d):
            continue
        if len(d) == 5:
            pos = text.find(d)
            if pos == -1:
                continue
            context = text[max(0, pos - 40):pos + 40].lower()
            strong = ['otp', 'verification code', 'security code', 'login code',
                      'access code', 'one-time', 'onetime', 'one time', 'auth code']
            if not any(kw in context for kw in strong):
                continue
        return d

    # ---------- 6. Alphanumeric (must have letter + digit) ----------
    alphanum = re.findall(r'\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{5,10}\b', text, re.IGNORECASE)
    for w in alphanum:
        if is_valid_code(w) and not has_bad_context(w):
            return w

    return "Code not found"


# HTML থেকে টেক্সট বের করার ফাংশন
def get_email_body(msg):
    body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and not body:
                try:
                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                except:
                    pass
            elif ctype == "text/html" and not html_body:
                try:
                    html_body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                except:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                decoded = payload.decode('utf-8', errors='ignore')
                if msg.get_content_type() == "text/html":
                    html_body = decoded
                else:
                    body = decoded
        except:
            pass

    # যদি text/plain খালি থাকে, তবে html_body থেকে ট্যাগ বাদ দিয়ে টেক্সট নেওয়া হবে
    if not body.strip() and html_body:
        body = re.sub(r'<[^>]+>', ' ', html_body)
        body = re.sub(r'&nbsp;', ' ', body)
        body = re.sub(r'&amp;', '&', body)
        body = re.sub(r'&lt;', '<', body)
        body = re.sub(r'&gt;', '>', body)
        body = re.sub(r'\s+', ' ', body).strip()

    return body

# Ager email-er result jomiye rakha hoy, jate bar bar same email download na hoy.
# Shudhu notun email ashle seta-i download hobe.
EMAIL_CACHE = {}
EMAIL_CACHE_MAX = 3000

def fetch_email_info(mail, uid):
    """Ekta email download kore OTP, sender, subject, time ber kore."""
    _, msg_data = mail.uid('fetch', uid, '(BODY.PEEK[])')
    for part in msg_data or []:
        if isinstance(part, tuple):
            msg = email.message_from_bytes(part[1])
            subject = msg.get("Subject", "No Subject")
            sender = msg.get("From", "Unknown Sender")
            date_hdr = msg.get("Date")

            msg_dt = datetime.now()
            if date_hdr:
                try:
                    parsed_dt = parsedate_to_datetime(date_hdr)
                    if parsed_dt.tzinfo:
                        msg_dt = parsed_dt.astimezone(ZoneInfo("Asia/Dhaka")).replace(tzinfo=None)
                    else:
                        msg_dt = parsed_dt
                except:
                    pass

            body = get_email_body(msg)
            otp = extract_otp(subject + " " + body)
            return {
                "sender": sender,
                "subject": subject,
                "code": None if otp == "Code not found" else otp,
                "time": msg_dt.strftime("%I:%M %p"),
                "timestamp": msg_dt.timestamp(),
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

def check_gmail(account, mail_data):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
        mail.login(account['email'], account['password'])

        result = None
        label = "INBOX"

        # 1. Prothome Inbox
        try:
            result = scan_folder(mail, account['email'], "inbox", 7)
        except Exception as e:
            print(f"[INBOX ERROR] {account['email']}: {e}")

        # 2. Inbox-e na pele Spam
        if not result:
            label = "SPAM"
            for folder in ["[Gmail]/Spam", "Spam"]:
                try:
                    result = scan_folder(mail, account['email'], folder, 5)
                except Exception:
                    result = None
                if result:
                    break

        if result:
            print(f"[{label}] Found OTP: {result['code']} from {account['email']}")
            mail_data.append({
                "email": account['email'],
                "sender": result["sender"],
                "subject": result["subject"],
                "code": result["code"],
                "time": result["time"],
                "timestamp": result["timestamp"],
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
            "sender": "Connection Error",
            "subject": "Failed to login/fetch",
            "code": "Error",
            "time": "N/A",
            "timestamp": 0
        })

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
        t = threading.Thread(target=worker, args=(acc,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    all_codes.sort(key=lambda x: x['timestamp'], reverse=True)
    return all_codes

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    password = data.get('password')
    if password and hmac.compare_digest(str(password), MASTER_PASSWORD):
        session['logged_in'] = True
        return jsonify({"success": True})
    return jsonify({"error": "Wrong password!"}), 401

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
        pw = str(data.get('password', '')).strip()
        if not hmac.compare_digest(pw, SETTINGS_PASSWORD):
            return jsonify({"error": "Incorrect password"}), 401

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
    email_input = data.get('email')
    password_input = data.get('password')

    if not email_input or not password_input:
        return jsonify({"error": "Email and App Password required"}), 400

    if accounts_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 200

    existing = accounts_collection.find_one({"email": email_input})
    if existing:
        return jsonify({"error": "Account already exists!"}), 400

    accounts_collection.insert_one({
        "email": email_input,
        "password": password_input,
        "created_at": datetime.now()
    })

    return jsonify({"message": "Account added permanently to Database!"})

# ---------------- Settings unlock (server-side) ----------------
@app.route('/api/settings-unlock', methods=['POST'])
def settings_unlock():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401
    data = request.json or {}
    pw = str(data.get('password', '')).strip()
    if hmac.compare_digest(pw, SETTINGS_PASSWORD):
        session['settings_unlocked'] = True
        return jsonify({"success": True})
    return jsonify({"error": "Incorrect password"}), 401


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
    if purpose == 'settings':
        session['settings_unlocked'] = True
    elif purpose == 'delete':
        session['delete_auth_at'] = time.time()
    return jsonify({"success": True})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
