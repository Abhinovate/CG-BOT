from flask import Flask, request
import re
import sqlite3
import requests
import os
from dateparser.search import search_dates

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────
DB_PATH = "payments.db"
VERIFY_TOKEN = "cgbot_secure_2026"          # Use this in Meta webhook settings
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "YOUR_TOKEN_HERE")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "981713728364176")

# ── Database ──────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount REAL,
                due_date TEXT,
                status TEXT
            )
        """)
        conn.commit()

def save_payment(amount, date):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO payments (amount, due_date, status) VALUES (?, ?, ?)",
            (amount, date.strftime("%Y-%m-%d"), "pending")
        )
        conn.commit()

# ── AI Extraction ─────────────────────────────────────────
def extract_payment(text):
    amount = None
    match = re.search(
        r'(?:₹|rs\.?|inr)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)',
        text, re.IGNORECASE
    )
    if match:
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            pass

    date = None
    found = search_dates(text, settings={'PREFER_DATES_FROM': 'future'})
    if found:
        date = found[0][1]

    return amount, date

# ── WhatsApp Reply ─────────────────────────────────────────
def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    response = requests.post(url, headers=headers, json=payload)
    print("Reply sent:", response.status_code, response.text)

# ── Routes ─────────────────────────────────────────────────
@app.route("/")
def home():
    return "✅ CG BOT is running!"

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()
    print("Incoming:", data)

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        message = value["messages"][0]

        from_number = message["from"]
        text = message["text"]["body"]
        print(f"Message from {from_number}: {text}")

        # Try to extract payment info
        amount, date = extract_payment(text)

        if amount and date:
            save_payment(amount, date)
            reply = f"✅ Got it! I've saved a payment of ₹{amount} due on {date.strftime('%d %b %Y')}."
        else:
            reply = (
                "👋 Hi! I'm CG Bot.\n\n"
                "You can tell me things like:\n"
                "• 'Pay ₹500 on Friday'\n"
                "• 'Remind me ₹2000 on 25th March'\n\n"
                "How can I help you today?"
            )

        send_whatsapp_message(from_number, reply)

    except (KeyError, IndexError) as e:
        print("Could not parse message:", e)

    return "ok", 200

# ── Run ────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
