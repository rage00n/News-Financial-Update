import time
import requests
from analysis import run_full_scan, fetch_news, run_combined

TOKEN = "YOUR_TOKEN"   # same token

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
        send_message(chat_id, "👋 Hi! Use /scan, /news, or /scans.")
    elif text == "/scan":
        send_message(chat_id, run_full_scan())
    elif text == "/news":
        send_message(chat_id, fetch_news(), parse_mode="Markdown")
    elif text == "/scans":
        send_message(chat_id, run_combined(), parse_mode="Markdown")
    else:
        send_message(chat_id, "Unknown command. Try /scan, /news, or /scans.")

def main():
    last_update_id = 0
    while True:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_update_id+1}&timeout=10"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            updates = resp.json()["result"]
            for upd in updates:
                process_update(upd)
                last_update_id = upd["update_id"]
        time.sleep(1)

if __name__ == "__main__":
    main()