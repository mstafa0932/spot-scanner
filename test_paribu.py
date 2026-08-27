import requests

# تجربة Endpoint دفتر الأوامر
url = "https://api.paribu.com/orderbook"
params = {"symbol": "btc_tl", "limit": 10}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

try:
    response = requests.get(url, params=params, headers=headers, timeout=10)
    print("Status Code:", response.status_code)
    if response.status_code == 200:
        data = response.json()
        print("JSON Keys:", list(data.keys()) if isinstance(data, dict) else "List Response")
        # طباعة عينة من أفضل Ask و Bid
        if isinstance(data, dict):
            print("Sample Ask:", data.get("asks", [])[:1])
            print("Sample Bid:", data.get("bids", [])[:1])
    else:
        print("Error Response:", response.text[:200])
except Exception as e:
    print("Connection Error:", e)
