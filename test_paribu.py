import requests

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
        if isinstance(data, list):
            print("List Length:", len(data))
            print("Sample Element:", data[0] if len(data) > 0 else "Empty")
        elif isinstance(data, dict):
            print("Dict Keys:", list(data.keys()))
            print("Sample Ask:", data.get("asks", [])[:1])
            print("Sample Bid:", data.get("bids", [])[:1])
except Exception as e:
    print("Error:", e)
