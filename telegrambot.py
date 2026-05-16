

import os
import re
import time
import tempfile
import logging
import threading
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
class TelegramSender(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.queue = []
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.proxies = {
            "http": "http://proxy.server:3128",
            "https": "http://proxy.server:3128"
        }

    def run(self):
        """Main loop: send queued messages one by one with retries."""
        while True:
            with self.lock:
                while not self.queue:
                    self.condition.wait()
                chat_id, text, parse_mode = self.queue.pop(0)
            self._send_with_retry(chat_id, text, parse_mode)

    def _send_with_retry(self, chat_id, text, parse_mode):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        delays = [2, 4, 8, 16, 32, 64, 128, 256, 300, 300]  # up to 10 retries
        for attempt, delay in enumerate(delays, start=1):
            try:
                resp = requests.post(url, json=payload, proxies=self.proxies, timeout=15)
                if resp.status_code == 200:
                    logger.info("Telegram message sent (attempt %d)", attempt)
                    return
                elif resp.status_code >= 500:
                    logger.warning("5xx (attempt %d): %s – retrying in %ds", attempt, resp.text, delay)
                else:
                    logger.error("Client error: %s", resp.text)
                    return
            except Exception as e:
                logger.warning("Network error (attempt %d): %s – retrying in %ds", attempt, e, delay)
            time.sleep(delay)
        logger.error("Failed to send message after %d retries", len(delays))

    def send(self, chat_id, text, parse_mode=None):
        with self.lock:
            self.queue.append((chat_id, text, parse_mode))
            self.condition.notify()


sender = TelegramSender()
sender.start()


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
def send_voice(chat_id, text):
    """Generate voice in a separate thread, then send via Telegram."""
    def _generate_and_send():
        clean = clean_for_voice(text)
        clean = clean[:4000]
        if not clean.strip():
            sender.send(chat_id, "⚠️ Voice briefing is empty after cleaning.")
            return
        mp3_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tts = gTTS(text=clean, lang="en", slow=False)
                tts.save(tmp.name)
                mp3_path = tmp.name

            # Upload voice (with a few retries)
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
            proxies = {"http": "http://proxy.server:3128", "https": "http://proxy.server:3128"}
            for attempt in range(1, 4):
                try:
                    with open(mp3_path, "rb") as audio:
                        resp = requests.post(url, data={"chat_id": chat_id},
                                             files={"voice": audio},
                                             proxies=proxies, timeout=20)
                    if resp.status_code == 200:
                        logger.info("Voice sent (attempt %d)", attempt)
                        break
                    elif resp.status_code >= 500:
                        logger.warning("Voice 5xx (attempt %d): %s", attempt, resp.text)
                    else:
                        logger.error("Voice client error: %s", resp.text)
                        break
                except Exception as e:
                    logger.warning("Voice network error (attempt %d): %s", attempt, e)
                time.sleep(5)
        except Exception as e:
            logger.exception("Voice generation failed")
            sender.send(chat_id, f"❌ Voice failed: {str(e)[:100]}")
        finally:
            if mp3_path and os.path.exists(mp3_path):
                os.unlink(mp3_path)

    threading.Thread(target=_generate_and_send, daemon=True).start()


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



