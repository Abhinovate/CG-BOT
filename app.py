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
DB_PATH         = "payments.db"
VERIFY_TOKEN    = os.environ.get("VERIFY_TOKEN", "cgbot_secure_2026")
WHATSAPP_TOKEN  = os.environ.get("WHATSAPP_TOKEN", "YOUR_TOKEN_HERE")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "981713728364176")
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")   # ← Add this in Railway

# ── Database ──────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
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

# ── Memory ─────────────────────────────────────────────────
def save_message(user_id, role, content):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO memory (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content)
        )
        conn.commit()

def get_history(user_id, limit=8):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT role, content FROM memory WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    rows.reverse()
    return rows  # list of (role, content) tuples

def get_user_payments(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT amount, due_date, note, status FROM payments "
            "WHERE user_id=? AND status='pending' ORDER BY due_date ASC LIMIT 5",
            (user_id,)
        ).fetchall()
    return rows

# ── Payments ───────────────────────────────────────────────
def save_payment(user_id, amount, date, note=""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO payments (user_id, amount, due_date, note, status) VALUES (?,?,?,?,?)",
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
        found = search_dates(
            text,
            settings={'PREFER_DATES_FROM': 'future', 'RETURN_AS_TIMEZONE_AWARE': False}
        )
        if found:
            date = found[0][1]
    except Exception as e:
        print("Date parse error:", e)
    return amount, date

# ── Gemini AI ──────────────────────────────────────────────
def ask_gemini(user_id, user_message):
    if not GEMINI_API_KEY:
        return None

    history = get_history(user_id)
    payments = get_user_payments(user_id)

    payment_summary = ""
    if payments:
        lines = [f"Rs.{p[0]} due {p[1]}" for p in payments]
        payment_summary = "\nUser pending payments:\n" + "\n".join(lines)

    system_text = f"""You are CG Bot, a smart WhatsApp AI assistant for Indian users built by THE-THIRD()EYE, Dharwad, India.

Your job:
- Help track billing and payments
- Answer any general questions helpfully  
- Assist with daily tasks and reminders
- Be a friendly business communication tool

Rules:
- Keep replies SHORT — this is WhatsApp, not email. Max 3-4 lines usually.
- Use simple English. Mix Hindi words naturally when appropriate (bhai, kal, theek hai, etc.)
- Be warm like a smart helpful friend
- Never use markdown symbols like ** or ## — plain text only
- Use emojis naturally but don't overdo it
- Today: {datetime.now().strftime('%d %b %Y, %A')}
{payment_summary}"""

    # Build Gemini contents from history
    contents = []
    for role, content in history:
        gemini_role = "user" if role == "user" else "model"
        contents.append({
            "role": gemini_role,
            "parts": [{"text": content}]
        })
    contents.append({
        "role": "user",
        "parts": [{"text": user_message}]
    })

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    )

    payload = {
        "system_instruction": {"parts": [{"text": system_text}]},
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 300,
            "temperature": 0.75
        }
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:
            print("Gemini error:", r.status_code, r.text)
            return None
    except Exception as e:
        print("Gemini exception:", e)
        return None

# ── Fallback Smart Reply ───────────────────────────────────
def smart_reply(user_id, text):
    text_lower = text.lower().strip()
    if any(w in text_lower for w in ["hi","hello","hey","hlo","hii","namaste"]):
        payments = get_user_payments(user_id)
        if payments:
            p = payments[0]
            return f"👋 Welcome back!\n\nReminder: Rs.{p[0]} is due on {p[1]}.\n\nHow can I help you?"
        return "👋 Hi! I'm CG Bot.\n\nTry:\n• 'Pay Rs.500 on Friday'\n• 'Show my payments'\n• Ask me anything!"
    if any(w in text_lower for w in ["help","commands"]):
        return (
            "CG Bot Commands:\n\n"
            "💰 'Pay Rs.500 on Friday'\n"
            "📋 'Show my payments'\n"
            "❓ Ask any question\n\n"
            "Built by THE-THIRD()EYE 🚀"
        )
    return "👋 Hi! Type 'help' to see what I can do, or just ask me anything!"

# ── WhatsApp Sender ────────────────────────────────────────
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
    r = requests.post(url, headers=headers, json=payload)
    print("Reply sent:", r.status_code, r.text)

# ── Routes ─────────────────────────────────────────────────
@app.route("/")
def home():
    return "CG BOT v2.0 - Powered by Gemini AI + THE-THIRD()EYE"

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()
    print("Incoming:", json.dumps(data, indent=2))

    try:
        value = data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return "ok", 200

        message = value["messages"][0]

        if message.get("type") != "text":
            send_whatsapp_message(
                message["from"],
                "I can only read text messages right now. Please type your message!"
            )
            return "ok", 200

        from_number = message["from"]
        text        = message["text"]["body"].strip()
        print(f"From {from_number}: {text}")

        save_message(from_number, "user", text)

        # Payment detection
        amount, date = extract_payment(text)
        payment_keywords = any(
            w in text.lower()
            for w in ["pay","payment","due","remind","₹","rs","inr","amount"]
        )

        if amount and date and payment_keywords:
            save_payment(from_number, amount, date, text)
            reply = (
                f"✅ Payment saved!\n\n"
                f"💰 Amount: Rs.{amount:,.0f}\n"
                f"📅 Due: {date.strftime('%d %b %Y (%A)')}\n\n"
                f"Type 'show payments' to see all dues."
            )

        elif any(w in text.lower() for w in ["show payments","my payments","pending","dues"]):
            payments = get_user_payments(from_number)
            if not payments:
                reply = "✅ No pending payments! All clear 🎉"
            else:
                lines = [f"• Rs.{p[0]:,.0f} — {p[1]}" for p in payments]
                reply = "📋 Pending payments:\n\n" + "\n".join(lines)

        else:
            reply = ask_gemini(from_number, text)
            if not reply:
                reply = smart_reply(from_number, text)

        save_message(from_number, "assistant", reply)
        send_whatsapp_message(from_number, reply)

    except (KeyError, IndexError) as e:
        print("Parse error:", e)

    return "ok", 200

# ── Run ────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
