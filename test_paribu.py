import os
import requests

url = "https://api.paribu.com/orderbook"
params = {"symbol": "btc_tl", "limit": 10}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

token = os.environ.get("TELEGRAM_BOT_TOKEN")
chat_id = os.environ.get("TELEGRAM_CHAT_ID")

try:
    res = requests.get(url, params=params, headers=headers, timeout=10)
    data = res.json()
    msg = f"Status: {res.status_code}\nData: {str(data)[:300]}"
except Exception as e:
    msg = f"Error: {e}"

if token and chat_id:
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": msg},
    )
