import os
import imaplib
import email
import re
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from pymongo import MongoClient

app = Flask(__name__)

# Session Secure Key & Master Password
app.secret_key = os.environ.get("SECRET_KEY", "your_secret_session_key_123")
MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "12345")
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
    match = re.search(r'\b\d{4,8}\b', text)
    if match:
        return match.group(0)
    return "Code not found"

def check_gmail(account):
    mail_data = []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(account['email'], account['password'])
        mail.select("inbox")

        status, messages = mail.search(None, 'ALL')
        email_ids = messages[0].split()

        recent_ids = email_ids[-2:] if len(email_ids) >= 2 else email_ids
        
        for e_id in reversed(recent_ids):
            _, msg_data = mail.fetch(e_id, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject = msg.get("Subject", "No Subject")
                    sender = msg.get("From", "Unknown Sender")
                    
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
                            "time": datetime.now().strftime("%I:%M %p")
                        })
                        break
        mail.logout()
    except Exception as e:
        print(f"Error reading {account['email']}: {e}")
    
    return mail_data

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    password = data.get('password')
    if password == MASTER_PASSWORD:
        session['logged_in'] = True  # সেশন সেভ হলো
        return jsonify({"success": True})
    return jsonify({"error": "Wrong password!"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('logged_in', None)
    return jsonify({"success": True})

@app.route('/api/fetch-otps')
def fetch_otps():
    # সেশন চেক (পাসওয়ার্ড কোনো হেডারে পাঠানো লাগবে না)
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401

    accounts = load_accounts()
    all_codes = []
    
    for acc in accounts:
        codes = check_gmail(acc)
        all_codes.extend(codes)
        
    return jsonify(all_codes)

@app.route('/api/add-account', methods=['POST'])
def add_account():
    # সেশন চেক (পাসওয়ার্ড কোনো হেডারে পাঠানো লাগবে না)
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized Access"}), 401

    data = request.json or {}
    email_input = data.get('email')
    password_input = data.get('password')

    if not email_input or not password_input:
        return jsonify({"error": "Email and App Password required"}), 400

    if accounts_collection is None:
        return jsonify({"error": "Database Not Connected!"}), 500

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
