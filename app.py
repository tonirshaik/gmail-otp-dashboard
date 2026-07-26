import os
import imaplib
import email
import re
import json
from datetime import datetime
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD", "12345")
ACCOUNTS_FILE = 'accounts.json'

def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, 'w') as f:
            json.dump([], f)
            
    try:
        with open(ACCOUNTS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading accounts: {e}")
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
        return jsonify({"success": True})
    return jsonify({"error": "Wrong password!"}), 401

@app.route('/api/fetch-otps')
def fetch_otps():
    user_pass = request.headers.get("X-Master-Password")
    if user_pass != MASTER_PASSWORD:
        return jsonify({"error": "Unauthorized Access"}), 401

    accounts = load_accounts()
    all_codes = []
    
    for acc in accounts:
        codes = check_gmail(acc)
        all_codes.extend(codes)
        
    return jsonify(all_codes)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port, debug=True)