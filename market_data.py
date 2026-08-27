from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"
KUCOIN_KLINES_URL = "https://api.kucoin.com/api/v1/market/candles"


class ParibuDataError(Exception):
    """Raised when Paribu data cannot be fetched or parsed."""
    pass


@dataclass
class Ticker:
    symbol: str
    last: Optional[Decimal]
    bid: Optional[Decimal]
    ask: Optional[Decimal]
    volume: Optional[Decimal]
    spread_percent: Optional[Decimal]


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def get_market_snapshot() -> Dict[str, Ticker]:
    try:
        response = requests.get(PARIBU_TICKER_URL, timeout=15)
        response.raise_for_status()
        raw_data = response.json()
    except requests.RequestException as exc:
        raise ParibuDataError(f"Failed to fetch Paribu ticker endpoint: {exc}") from exc

    market_data = raw_data.get("result", raw_data)
    if not isinstance(market_data, dict):
        raise ParibuDataError("Unexpected data format received from Paribu.")

    snapshot: Dict[str, Ticker] = {}

    for raw_symbol, info in market_data.items():
        if not raw_symbol.endswith("_TL"):
            continue

        if not isinstance(info, dict):
            continue

        last = _to_decimal(info.get("last"))
        bid = _to_decimal(info.get("highestBid"))
        ask = _to_decimal(info.get("lowestAsk"))
        volume = _to_decimal(info.get("volume"))

        spread_percent = None
        if bid and ask and ask > 0 and bid > 0 and ask >= bid:
            spread_percent = ((ask - bid) / ask) * Decimal("100")

        snapshot[raw_symbol] = Ticker(
            symbol=raw_symbol,
            last=last,
            bid=bid,
            ask=ask,
            volume=volume,
            spread_percent=spread_percent,
        )

    return snapshot


def fetch_candles(symbol: str, resolution: str = "15m", limit: int = 250) -> pd.DataFrame:
    base_currency = symbol.split("_")[0]
    kucoin_symbol = f"{base_currency}-USDT"

    resolution_map = {
        "1m": "1min",
        "3m": "3min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1hour",
        "4h": "4hour",
        "1d": "1day",
    }
    k_type = resolution_map.get(resolution, "15min")

    params = {
        "symbol": kucoin_symbol,
        "type": k_type,
    }

    try:
        resp = requests.get(KUCOIN_KLINES_URL, params=params, timeout=10)
        resp.raise_for_status()
        result = resp.json()

        if result.get("code") != "200000":
            raise ValueError(result.get("msg", "Unknown error"))

        data = result.get("data", [])
        if not isinstance(data, list) or not data:
            raise ValueError("Empty candle data")

        rows = []
        for candle in reversed(data[:limit]):
            rows.append({
                "timestamp": int(candle[0]) * 1000,
                "open": float(candle[1]),
                "high": float(candle[3]),
                "low": float(candle[4]),
                "close": float(candle[2]),
                "volume": float(candle[5]),
            })

        df = pd.DataFrame(rows)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df
    except Exception as exc:
        # تسجيل الصامت للعملات الغيرعنصرة لتجنب إزعاج السجلات
        raise ValueError(f"Unsupported or unavailable pair on KuCoin: {kucoin_symbol}") from exc
