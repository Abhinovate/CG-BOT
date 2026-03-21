from flask import Flask, request
import re
import sqlite3
import requests
import os
import json
from datetime import datetime
from dateparser.search import search_dates

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────
DB_PATH = "payments.db"
VERIFY_TOKEN        = os.environ.get("VERIFY_TOKEN", "cgbot_secure_2026")
WHATSAPP_TOKEN      = os.environ.get("WHATSAPP_TOKEN", "YOUR_TOKEN_HERE")
PHONE_NUMBER_ID     = os.environ.get("PHONE_NUMBER_ID", "981713728364176")
CLAUDE_API_KEY      = os.environ.get("CLAUDE_API_KEY", "")   # Add this in Railway

# ── Database ──────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        # Payments table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   TEXT,
                amount    REAL,
                due_date  TEXT,
                note      TEXT,
                status    TEXT DEFAULT 'pending',
                created   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Conversation memory table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   TEXT,
                role      TEXT,
                content   TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

# ── Memory Functions ──────────────────────────────────────
def save_message(user_id, role, content):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO memory (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content)
        )
        conn.commit()

def get_history(user_id, limit=10):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT role, content FROM memory WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    rows.reverse()
    return [{"role": r[0], "content": r[1]} for r in rows]

def get_user_payments(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT amount, due_date, note, status FROM payments WHERE user_id=? ORDER BY due_date ASC LIMIT 5",
            (user_id,)
        ).fetchall()
    return rows

# ── Payment Functions ─────────────────────────────────────
def save_payment(user_id, amount, date, note=""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO payments (user_id, amount, due_date, note, status) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, date.strftime("%Y-%m-%d"), note, "pending")
        )
        conn.commit()

def extract_payment(text):
    amount = None
    match = re.search(
        r'(?:rs\.?|inr|₹)?\s*([0-9,]+(?:\.[0-9]{1,2})?)',
        text, re.IGNORECASE
    )
    if match:
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            pass

    date = None
    try:
        found = search_dates(text, settings={'PREFER_DATES_FROM': 'future', 'RETURN_AS_TIMEZONE_AWARE': False})
        if found:
            date = found[0][1]
    except Exception as e:
        print("Date parse error:", e)

    return amount, date

# ── Claude AI ─────────────────────────────────────────────
def ask_claude(user_id, user_message):
    if not CLAUDE_API_KEY:
        return None  # Fall back to rule-based if no key

    history = get_history(user_id)
    payments = get_user_payments(user_id)

    payment_summary = ""
    if payments:
        lines = [f"₹{p[0]} due {p[1]} ({p[3]})" for p in payments]
        payment_summary = "User's saved payments:\n" + "\n".join(lines)

    system_prompt = f"""You are CG Bot — a smart WhatsApp AI assistant focused on:
1. Billing and payment tracking
2. Daily task reminders
3. Business communication help
4. General assistance for Indian users

{payment_summary}

Rules:
- Keep replies SHORT (under 200 chars when possible) — this is WhatsApp
- Use simple English, mix Hindi words naturally if user uses them
- For payments: extract amount + date and confirm saving
- For greetings: be warm and brief
- Always end with a helpful follow-up question or tip
- Today's date: {datetime.now().strftime('%d %b %Y')}
- You are built by THE-THIRD()EYE for Indian market"""

    messages = history + [{"role": "user", "content": user_message}]

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "system": system_prompt,
                "messages": messages
            },
            timeout=10
        )
        if response.status_code == 200:
            return response.json()["content"][0]["text"]
        else:
            print("Claude error:", response.status_code, response.text)
            return None
    except Exception as e:
        print("Claude exception:", e)
        return None

# ── Smart Reply (fallback if no Claude key) ───────────────
def smart_reply(user_id, text):
    text_lower = text.lower().strip()

    # Greetings
    if any(w in text_lower for w in ["hi", "hello", "hey", "hlo", "namaste", "hii"]):
        payments = get_user_payments(user_id)
        if payments:
            next_p = payments[0]
            return f"👋 Welcome back!\n\nReminder: ₹{next_p[0]} is due on {next_p[1]}.\n\nSend me a payment to track or ask anything!"
        return "👋 Hi! I'm CG Bot — your WhatsApp billing assistant.\n\nTry: 'Pay ₹500 on Friday'\nOr ask me anything!"

    # Show payments
    if any(w in text_lower for w in ["list", "show", "pending", "dues", "payments", "due"]):
        payments = get_user_payments(user_id)
        if not payments:
            return "✅ No pending payments! You're all clear."
        lines = [f"• ₹{p[0]} — {p[1]} ({p[3]})" for p in payments]
        return "📋 Your pending payments:\n\n" + "\n".join(lines)

    # Help
    if any(w in text_lower for w in ["help", "what can", "commands"]):
        return (
            "🤖 CG Bot Commands:\n\n"
            "💰 Track: 'Pay ₹500 on Friday'\n"
            "📋 View: 'Show my payments'\n"
            "🔔 Remind: 'Remind ₹2000 on 25 March'\n"
            "❓ Ask: Any question!\n\n"
            "Powered by THE-THIRD()EYE 🚀"
        )

    # Default
    return (
        "👋 Hi! I'm CG Bot.\n\n"
        "Try:\n"
        "• 'Pay ₹500 on Friday'\n"
        "• 'Show my payments'\n"
        "• 'Help'\n\n"
        "How can I help you today?"
    )

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
    return "✅ CG BOT v2.0 is running! Powered by THE-THIRD()EYE"

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
    print("Incoming:", json.dumps(data, indent=2))

    try:
        entry   = data["entry"][0]
        changes = entry["changes"][0]
        value   = changes["value"]

        # Ignore status updates
        if "messages" not in value:
            return "ok", 200

        message = value["messages"][0]

        # Only handle text messages
        if message.get("type") != "text":
            send_whatsapp_message(
                message["from"],
                "Sorry, I can only read text messages for now. Please type your message! 📝"
            )
            return "ok", 200

        from_number = message["from"]
        text        = message["text"]["body"]
        print(f"Message from {from_number}: {text}")

        # Save user message to memory
        save_message(from_number, "user", text)

        # Try to extract payment info first
        amount, date = extract_payment(text)
        payment_keywords = any(w in text.lower() for w in ["pay", "payment", "due", "remind", "₹", "rs", "inr"])

        if amount and date and payment_keywords:
            save_payment(from_number, amount, date, text)
            reply = f"✅ Saved!\n\n💰 Amount: ₹{amount:,.0f}\n📅 Due: {date.strftime('%d %b %Y')}\n\nI'll help you track this. Send 'show payments' to see all dues."
        else:
            # Try Claude AI first
            reply = ask_claude(from_number, text)
            if not reply:
                # Fall back to smart rule-based reply
                reply = smart_reply(from_number, text)

        # Save bot reply to memory
        save_message(from_number, "assistant", reply)

        send_whatsapp_message(from_number, reply)

    except (KeyError, IndexError) as e:
        print("Could not parse message:", e)

    return "ok", 200

# ── Run ────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
