from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BYBIT_KLINES_URL = "https://api.bybit.com/v5/market/kline"


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
    """
    Fetches ticker data from Paribu and maps valid pairs ending with _TL to Ticker objects.
    """
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


def _fetch_binance_candles(symbol: str, resolution: str, limit: int) -> pd.DataFrame:
    # Convert Paribu symbol (e.g. XRP_TL) to Binance format (XRPUSDT)
    base_currency = symbol.split("_")[0]
    binance_symbol = f"{base_currency}USDT"

    params = {
        "symbol": binance_symbol,
        "interval": resolution,
        "limit": limit,
    }

    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list) or not data:
        raise ValueError(f"Invalid candle data format from Binance for {binance_symbol}")

    rows = []
    for candle in data:
        rows.append({
            "timestamp": int(candle[0]),
            "open": float(candle[1]),
            "high": float(candle[2]),
            "low": float(candle[3]),
            "close": float(candle[4]),
            "volume": float(candle[5]),
        })

    df = pd.DataFrame(rows)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _fetch_bybit_candles(symbol: str, resolution: str, limit: int) -> pd.DataFrame:
    # Convert Paribu symbol to Bybit format (XRPUSDT)
    base_currency = symbol.split("_")[0]
    bybit_symbol = f"{base_currency}USDT"

    # Map resolutions to Bybit v5 intervals ('15m' -> '15')
    interval_map = {
        "1m": "1",
        "3m": "3",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "4h": "240",
        "1d": "D",
    }
    bybit_interval = interval_map.get(resolution, "15")

    params = {
        "category": "spot",
        "symbol": bybit_symbol,
        "interval": bybit_interval,
        "limit": min(limit, 1000),
    }

    resp = requests.get(BYBIT_KLINES_URL, params=params, timeout=10)
    resp.raise_for_status()
    result = resp.json()

    if result.get("retCode") != 0:
        raise ValueError(f"Bybit API error: {result.get('retMsg')}")

    list_data = result.get("result", {}).get("list", [])
    if not isinstance(list_data, list) or not list_data:
        raise ValueError(f"No candle data returned from Bybit for {bybit_symbol}")

    # Bybit returns data in reverse chronological order (newest first), so we reverse it
    list_data.reverse()

    rows = []
    for candle in list_data:
        # Bybit v5 kline format: [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
        rows.append({
            "timestamp": int(candle[0]),
            "open": float(candle[1]),
            "high": float(candle[2]),
            "low": float(candle[3]),
            "close": float(candle[4]),
            "volume": float(candle[5]),
        })

    df = pd.DataFrame(rows)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def fetch_candles(symbol: str, resolution: str = "15m", limit: int = 250) -> pd.DataFrame:
    """
    Fetches candles with automatic fallback from Binance to Bybit to bypass regional IP restrictions.
    """
    try:
        return _fetch_binance_candles(symbol, resolution, limit)
    except Exception as binance_exc:
        LOGGER.debug(f"Binance candle fetch failed for {symbol} ({binance_exc}), falling back to Bybit...")
        try:
            return _fetch_bybit_candles(symbol, resolution, limit)
        except Exception as bybit_exc:
            raise RuntimeError(f"Failed to fetch candles for {symbol} from both Binance and Bybit. Binance error: {binance_exc} | Bybit error: {bybit_exc}")
