import threading
import time
import requests
from flask import Flask
from analysis import run_full_scan, fetch_news, run_combined

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ============================================================
#  Flask health-check + optional webhook route (not used now)
# ============================================================
app = Flask(__name__)

@app.route("/ping")
def ping():
    return "ok", 200

# Keep the old webhook route if you ever want to switch back
@app.route("/webhook", methods=["POST"])
def webhook():
    return {"status": "ok"}, 200


# ============================================================
#  Telegram helpers
# ============================================================
def send_message(chat_id, text, parse_mode=None):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    requests.post(url, json=payload, timeout=10)


def process_update(update):
    msg = update.get("message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    if text == "/start":
        send_message(chat_id, "👋 Hi! Use /scan, /news, /scans.")
    elif text == "/scan":
        send_message(chat_id, run_full_scan())
    elif text == "/news":
        send_message(chat_id, fetch_news(), parse_mode="Markdown")
    elif text == "/scans":
        send_message(chat_id, run_combined(), parse_mode="Markdown")
    else:
        send_message(chat_id, "Unknown command. Try /scan, /news, or /scans.")


# ============================================================
#  Polling loop
# ============================================================
def polling_loop():
    last_update_id = 0
    while True:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_update_id+1}&timeout=10"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                updates = resp.json()["result"]
                for upd in updates:
                    process_update(upd)
                    last_update_id = upd["update_id"]
        except Exception as e:
            print(f"Polling error: {e}")
        time.sleep(1)


# ============================================================
#  Main: start Flask in a thread, then run polling
# ============================================================
def start_flask():
    app.run(host="0.0.0.0", port=10000, debug=False, use_reloader=False)


if __name__ == "__main__":
    # Start Flask in a daemon thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Give Flask a moment to start
    time.sleep(1)

    # Run the polling loop (this blocks the main thread)
    polling_loop()