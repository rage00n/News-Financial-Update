

import os
import re
import time
import tempfile
import logging
import requests
from flask import Flask, request, jsonify
from gtts import gTTS

from analysis import run_full_scan, fetch_news, run_combined

# ============================================================
#  CONFIG – Replace with your NEW bot token (revoke old one!)
# ============================================================
TELEGRAM_BOT_TOKEN = "7970870938:AAF70HMmmw8ACbsuFi_ynWG0pZszrLQEikA"

# ============================================================
#  Logging
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


# ============================================================
#  Background Telegram sender with strong retry/backoff
# ============================================================



# ============================================================
#  Voice text cleaner (ultra‑safe for gTTS)
# ============================================================
def clean_for_voice(text):
    text = text.encode("ascii", errors="ignore").decode("ascii")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,!?")
    text = ''.join(ch if ch in allowed else ' ' for ch in text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ============================================================
#  Voice memo generation and sending (background)
# ============================================================


# Simple function to send a Telegram message (no proxy, no splitting)
def send_telegram_message(chat_id, text, parse_mode=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp
    except Exception as e:
        print(f"Telegram send error: {e}")
        return None

# Simple voice sender (placeholder for now)
def send_voice(chat_id, text):
    # Just send the text for now – voice can be added later
    send_telegram_message(chat_id, "Voice generation is not available yet.")

# ============================================================
#  Webhook – command router
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json()
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        if text.startswith("/scan") and not text.startswith("/scans"):
            try:
                briefing = run_full_scan()
                sender.send(chat_id, briefing)
            except Exception as e:
                sender.send(chat_id, f"❌ Scan failed: {str(e)[:200]}")

        elif text.startswith("/news"):
            try:
                news = fetch_news()
                sender.send(chat_id, news, parse_mode="Markdown")
            except Exception as e:
                sender.send(chat_id, f"❌ News fetch failed: {str(e)[:200]}")

        elif text.startswith("/voice"):
            try:
                combined = run_combined()
                sender.send(chat_id, combined)          # text first
                send_voice(chat_id, combined)           # then voice
            except Exception as e:
                sender.send(chat_id, f"❌ Voice failed: {str(e)[:200]}")

        elif text.startswith("/scans"):
            try:
                combined = run_combined()
                sender.send(chat_id, combined, parse_mode="Markdown")
            except Exception as e:
                sender.send(chat_id, f"❌ Combined scan failed: {str(e)[:200]}")

        elif text.startswith("/start"):
            sender.send(chat_id,
                        "👋 Hi! Use:\n"
                        "/scan – extreme‑ticker analysis\n"
                        "/news – latest tech/Singapore headlines\n"
                        "/scans – both together\n"
                        "/voice – same as /scans, but read aloud")

        else:
            sender.send(chat_id, "Unknown command. Try /scan, /news, /scans, or /voice.")

    return jsonify({"status": "ok"})



