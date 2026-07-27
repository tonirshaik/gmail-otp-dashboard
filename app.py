import os
import imaplib
import email
import re
import time
import json
import threading
from datetime import datetime
from email.utils import parsedate_to_datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response
from pymongo import MongoClient

app = Flask(__name__)

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
    gcode = re.search(r'G-\d{4,8}', text, re.IGNORECASE)
    if gcode:
        return gcode.group(0)

    digit_code = re.search(r'\b\d{4,12}\b', text)
    if digit_code:
        return digit_code.group(0)

    alphanumeric_code = re.search(r'\b[A-Z0-9]{4,10}\b', text, re.IGNORECASE)
    if alphanumeric_code:
        return alphanumeric_code.group(0)

    return "Code not found"

def check_gmail(account, mail_data):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
        mail.login(account['email'], account['password'])
        
        folders_to_try = ["[Gmail]/Spam", "Spam", "[Gmail]/All Mail", "inbox"]
        
        found_otp = False
        for folder in folders_to_try:
            if found_otp:
                break
            try:
                status, _ = mail.select(folder)
                if status != 'OK':
                    continue
                
                status, messages = mail.search(None, 'ALL')
                if status != 'OK' or not messages[0]:
                    continue
                    
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
                                    msg_dt = parsedate_to_datetime(date_hdr)
                                except Exception:
                                    pass
                            
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        break
                            else:
                                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

                            keywords = ["code", "otp", "verification", "pin", "verify", "password", "login"]
                            if any(kw in subject.lower() or kw in body.lower() for kw in keywords):
                                otp = extract_otp(subject + " " + body)
                                
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
            except Exception:
                continue

        # যদি কোনো ইমেইলে কিওয়ার্ড না মিলে, তবুও জিমেইল অ্যাকাউন্টটি ড্যাশবোর্ডে দেখানোর জন্য ডিফল্ট অবজেক্ট পাঠানো হবে
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
    # পূর্বে এখানে [:2] দেওয়া ছিল যা কেটে দেওয়া হয়েছে, যাতে সব জিমেইলের বক্স দেখায়
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

# Real-time Server-Sent Events (SSE) Route
@app.route('/api/stream')
def stream():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401

    def event_stream():
        last_data_signature = None
        while True:
            try:
                all_otps = get_latest_otps()
                data_signature = json.dumps(all_otps, sort_keys=True)
                
                if data_signature != last_data_signature:
                    last_data_signature = data_signature
                    yield f"data: {data_signature}\n\n"
                else:
                    yield ": ping\n\n"
            except Exception as e:
                print(f"SSE Error: {e}")
            
            time.sleep(3)

    return Response(event_stream(), mimetype="text/event-stream")

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
