import os
import requests
from scanner import MarketScanner

def send_telegram_msg(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[!] Failed to send Telegram notification: {e}")

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    print("[*] Starting Paribu Spot Scanner Pipeline...")
    scanner = MarketScanner(top_n=3)
    opportunities = scanner.scan_market()

    if not opportunities:
        print("[INFO] Scan finished cleanly. No qualified opportunities found in this run.")
        return

    if not token or not chat_id:
        print("[!] Telegram credentials missing in environment variables.")
        return

    message = "🚨 *فرصة تداول جديدة على Paribu* 🚨\n\n"
    for opp in opportunities:
        message += (
            f"📌 *العملة:* `{opp.symbol}`\n"
            f"🎯 *النقاط:* {opp.score}/100\n"
            f"💡 *السبب:* {opp.reason}\n"
            f"💵 *سعر الدخول:* `{opp.entry_price}` TL\n"
            f"🛑 *وقف الخسارة:* `{opp.stop_loss}` TL\n"
            f"🎯 *الهدف الأول:* `{opp.tp_1}` TL\n"
            f"🚀 *الهدف الثاني:* `{opp.tp_2}` TL\n"
            f"-----------------------------------\n"
        )
    
    send_telegram_msg(token, chat_id, message)
    print(f"[+] Sent {len(opportunities)} opportunities to Telegram.")

if __name__ == "__main__":
    main()

