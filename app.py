import re
import dateparser
import sqlite3
from datetime import datetime
from dateparser.search import search_dates


DB_PATH = "payments.db"

# Ensure table exists at startup
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL,
            due_date TEXT,
            status TEXT
        )
        """)
        conn.commit()

def extract_payment(text):
    # Improved amount extraction: supports commas, decimals, and currency words
    amount_match = re.search(r'(?:₹|rs\.?|inr)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)', text, re.IGNORECASE)
    amount = None
    if amount_match:
        amt_str = amount_match.group(1).replace(",", "")
        try:
            amount = float(amt_str)
        except ValueError:
            amount = None

    # Extract date using dateparser's search_dates
    date = None
    found_dates = search_dates(text, settings={'PREFER_DATES_FROM': 'future'})
    if found_dates:
        # Pick the first detected date
        date = found_dates[0][1]

    return amount, date

def save_payment(amount, date):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO payments (amount, due_date, status)
        VALUES (?, ?, ?)
        """, (amount, date.strftime("%Y-%m-%d"), "pending"))
        conn.commit()

def main():
    print("AI Billing System. Type 'exit' to quit.")
    init_db()
    while True:
        try:
            message = input("Enter payment message (or type exit): ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if message.strip().lower() == "exit":
            break

        amount, date = extract_payment(message)

        if not amount:
            print("❌ Could not detect a valid amount. Please include a number, e.g., 'Pay ₹500 on Friday'.")
            continue
        if not date:
            print("❌ Could not detect a valid date. Please include a date, e.g., 'Pay ₹500 on Friday'.")
            continue

        try:
            save_payment(amount, date)
            print(f"✅ Saved: ₹{amount} due on {date.strftime('%Y-%m-%d')}")
        except Exception as e:
            print(f"❌ Error saving payment: {e}")

if __name__ == "__main__":
    main()
        