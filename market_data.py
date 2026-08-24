import requests
from typing import Any


PARIBU_TICKERS_URL = "https://www.paribu.com/spot/market/tickers"

TIMEOUT = 15


def get_market_data() -> Any:
    """
    جلب بيانات السوق العامة من Paribu.
    
    لا يحتاج:
    - API Key
    - كلمة مرور
    - صلاحية تداول
    - صلاحية سحب
    """

    response = requests.get(
        PARIBU_TICKERS_URL,
        timeout=TIMEOUT,
        headers={
            "User-Agent": "spot-scanner/1.0"
        }
    )

    response.raise_for_status()

    return response.json()


def print_market_summary(data: Any) -> None:
    """عرض ملخص البيانات التي وصلت من Paribu."""

    print("=" * 50)
    print("PARIBU MARKET DATA")
    print("=" * 50)

    if isinstance(data, dict):
        print("Response type: dictionary")
        print("Keys:", list(data.keys())[:20])

    elif isinstance(data, list):
        print("Response type: list")
        print("Number of items:", len(data))

        for item in data[:5]:
            print(item)

    else:
        print("Unknown response type:", type(data))

    print("=" * 50)


if __name__ == "__main__":

    try:
        market_data = get_market_data()

        print("✅ Successfully connected to Paribu.")
        print_market_summary(market_data)

    except requests.exceptions.Timeout:
        print("❌ Connection timed out.")

    except requests.exceptions.RequestException as error:
        print("❌ Paribu connection error:")
        print(error)

    except Exception as error:
        print("❌ Unexpected error:")
        print(error)
