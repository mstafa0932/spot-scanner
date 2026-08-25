from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

import requests


# ============================================================
# Candle Data Fetcher Engine
# Spot Scanner project
# ============================================================


class ParibuCandleError(Exception):
    """Custom exception for candle fetching errors."""
    pass


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def get_recent_candles(
    symbol: str, resolution: str = "15", limit: int = 50
) -> List[Candle]:
    """
    Fetch OHLCV candle data for a given symbol from Paribu public chart endpoint.
    """
    clean_symbol = symbol.lower()
    url = f"https://www.paribu.com/config/chart/history?symbol={clean_symbol}&resolution={resolution}&from=0&to=9999999999"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            raise ParibuCandleError(f"HTTP Error {response.status_code}")

        data = response.json()
        if not isinstance(data, dict) or data.get("s") != "ok":
            raise ParibuCandleError(f"Invalid API response structure for {symbol}")

        timestamps = data.get("t", [])
        opens = data.get("o", [])
        highs = data.get("h", [])
        lows = data.get("l", [])
        closes = data.get("c", [])
        volumes = data.get("v", [])

        candles: List[Candle] = []
        count = len(timestamps)

        start_index = max(0, count - limit)

        for i in range(start_index, count):
            candles.append(
                Candle(
                    timestamp=int(timestamps[i]),
                    open=Decimal(str(opens[i])),
                    high=Decimal(str(highs[i])),
                    low=Decimal(str(lows[i])),
                    close=Decimal(str(closes[i])),
                    volume=Decimal(str(volumes[i])),
                )
            )

        return candles

    except Exception as exc:
        raise ParibuCandleError(f"Failed to fetch candles for {symbol}: {exc}")
