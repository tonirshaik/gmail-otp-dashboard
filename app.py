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
    # ১. সবার আগে বিশুদ্ধ ডিজিট কোড খোঁজা (GitHub, Microsoft এর জন্য)
    digits = re.findall(r'\b\d{5,8}\b', text)
    for d in digits:
        return d

    # ২. স্পেস বা ফাঁকা ফাঁকা থাকা কোড ধরার লজিক
    spaced_code = re.search(r'\b([A-Z0-9]\s+[A-Z0-9](?:\s+[A-Z0-9]){3,10})\b', text, re.IGNORECASE)
    if spaced_code:
        clean_spaced = re.sub(r'\s+', ' ', spaced_code.group(0))
        if any(c.isdigit() for c in clean_spaced.replace(' ', '')):
            return clean_spaced

    # ৩. G- দিয়ে শুরু কোড
    gcode = re.search(r'\bG-[A-Z0-9]{4,10}\b', text, re.IGNORECASE)
    if gcode:
        return gcode.group(0)

    # ৪. নির্দিষ্ট কিওয়ার্ডের পরে থাকা কোড
    after_keyword = re.search(r'(?:code|pin|otp|verification|password|verify|auth|token)[:\s#]+([A-Z0-9\-]{4,12})', text, re.IGNORECASE)
    if after_keyword:
        code_val = after_keyword.group(1).strip()
        ignore_list = ['code', 'pin', 'otp', 'password', 'none', 'your', 'is', 'the', 'and', 'for', 'with']
        if code_val.lower() not in ignore_list:
            if not (code_val.isdigit() and len(code_val) == 4 and 2000 <= int(code_val) <= 2030):
                return code_val

    # ৫. সাধারণ আলফানিউমারিক কোড
    words = re.findall(r'\b[A-Z0-9]{4,10}\b', text, re.IGNORECASE)
    ignore_words = {
        'code', 'pin', 'otp', 'verify', 'true', 'false', 'your', 'this',
        'that', 'with', 'from', 'http', 'https', 'gmail', 'google',
        'html', '3dhttp', 'github', 'microsoft', 'facebook', 'apple',
        'amazon', 'twitter', 'linkedin', 'please', 'click', 'here',
        'sign', 'link', 'copy', 'paste', 'enter', 'valid', 'expire',
        'minute', 'hour', 'have', 'donot', 'sudo'
    }

    for w in words:
        w_lower = w.lower()
        if w_lower in ignore_words:
            continue
        if w.isdigit() and len(w) == 4 and 2000 <= int(w) <= 2030:
            continue
        if any(c.isdigit() for c in w):
            return w
        if len(w) >= 6 and not w.isalpha():
            return w

    # ৬. ৪ ডিজিটের কোড (সবশেষে)
    digits = re.findall(r'\b\d{4}\b', text)
    for d in digits:
        if 2000 <= int(d) <= 2030:
            continue
        return d

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
