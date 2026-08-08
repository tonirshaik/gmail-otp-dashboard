import os
import imaplib
import email
import re
import time
import json
import threading
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response
from pymongo import MongoClient
from flask_cors import CORS

app = Flask(__name__)
CORS(app, supports_credentials=True)

# Session Secure Key & Master Password
app.secret_key = os.environ.get("SECRET_KEY", "your_secret_session_key_123")
MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "12342")
MONGO_URI = os.environ.get("MONGO_URI", "")

# MongoDB Connection
db = None
accounts_collection = None

if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI)
        db = client['gmail_otp_db']
        accounts_collection = db['accounts']
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

def check_gmail(account, mail_data):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
        mail.login(account['email'], account['password'])

        found_otp = False

        # ১. প্রথমে Inbox চেক করা হবে
        if not found_otp:
            try:
                status, _ = mail.select("inbox")
                if status == 'OK':
                    status, messages = mail.search(None, 'ALL')
                    if status == 'OK' and messages[0]:
                        email_ids = messages[0].split()
                        recent_ids = email_ids[-7:] if len(email_ids) >= 7 else email_ids

                        for e_id in reversed(recent_ids):
                            _, msg_data = mail.fetch(e_id, '(RFC822)')
                            for response_part in msg_data:
                                if isinstance(response_part, tuple):
                                    msg = email.message_from_bytes(response_part[1])
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
                                    full_text = subject + " " + body

                                    otp = extract_otp(full_text)
                                    if otp != "Code not found":
                                        print(f"[INBOX] Found OTP: {otp} from {account['email']}")
                                        mail_data.append({
                                            "email": account['email'],
                                            "sender": sender,
                                            "subject": subject,
                                            "code": otp,
                                            "time": msg_dt.strftime("%I:%M %p"),
                                            "timestamp": msg_dt.timestamp()
                                        })
                                        found_otp = True
                                        break
                            if found_otp:
                                break
            except Exception as e:
                print(f"[INBOX ERROR] {account['email']}: {e}")

        # ২. Inbox এ না পাওয়া গেলে Spam চেক করা হবে
        if not found_otp:
            spam_folders = ["[Gmail]/Spam", "Spam"]
            for folder in spam_folders:
                try:
                    status, _ = mail.select(folder)
                    if status == 'OK':
                        status, messages = mail.search(None, 'ALL')
                        if status == 'OK' and messages[0]:
                            email_ids = messages[0].split()
                            recent_ids = email_ids[-5:] if len(email_ids) >= 5 else email_ids

                            for e_id in reversed(recent_ids):
                                _, msg_data = mail.fetch(e_id, '(RFC822)')
                                for response_part in msg_data:
                                    if isinstance(response_part, tuple):
                                        msg = email.message_from_bytes(response_part[1])
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
                                        full_text = subject + " " + body

                                        otp = extract_otp(full_text)
                                        if otp != "Code not found":
                                            print(f"[SPAM] MATCHED OTP: {otp} from {account['email']}")
                                            mail_data.append({
                                                "email": account['email'],
                                                "sender": sender,
                                                "subject": subject,
                                                "code": otp,
                                                "time": msg_dt.strftime("%I:%M %p"),
                                                "timestamp": msg_dt.timestamp()
                                            })
                                            found_otp = True
                                            break
                                if found_otp:
                                    break
                        if found_otp:
                            break
                except Exception as e:
                    pass

        if not found_otp:
            mail_data.append({
                "email": account['email'],
                "sender": "N/A",
                "subject": "No recent OTP emails found",
                "code": "No emails found",
                "time": "N/A",
                "timestamp": 0
            })

        mail.logout()
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
    if password == MASTER_PASSWORD:
        session['logged_in'] = True
        return jsonify({"success": True})
    return jsonify({"error": "Wrong password!"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('logged_in', None)
    return jsonify({"success": True})

@app.route('/api/fetch-otps')
def fetch_otps():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401

    all_otps = get_latest_otps()
    return jsonify(all_otps)

@app.route('/api/add-account', methods=['POST'])
def add_account():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401

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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port, debug=True)
