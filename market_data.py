from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURATION
# ============================================================

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"
BINANCE_CANDLES_URL = "https://api.binance.com/api/v3/klines"

REQUEST_TIMEOUT = 15
DEFAULT_CANDLE_LIMIT = 250

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================
# EXCEPTIONS
# ============================================================

class ParibuDataError(Exception):
    """Base exception for market-data errors."""


class ParibuHTTPError(ParibuDataError):
    """HTTP/network/API error."""


class ParibuSchemaError(ParibuDataError):
    """Unexpected API response format."""


class BinanceDataError(ParibuDataError):
    """Binance market-data error."""


# ============================================================
# DECIMAL HELPERS
# ============================================================

def D(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            return None
        return result
    except (InvalidOperation, ValueError, TypeError):
        return None


# ============================================================
# HTTP SESSION
# ============================================================

def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Connection": "keep-alive",
        }
    )
    return session


SESSION = create_session()


# ============================================================
# BASIC HELPERS
# ============================================================

def normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().replace("/", "_").replace("-", "_")


def base_asset(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if "_" not in normalized:
        raise ParibuSchemaError(f"Invalid trading symbol: {symbol}")
    return normalized.split("_", 1)[0]


def is_tl_pair(symbol: str) -> bool:
    normalized = normalize_symbol(symbol)
    return normalized.endswith("_TL") or normalized.endswith("_TRY")


def first_value(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


# ============================================================
# DATA MODELS
# ============================================================

@dataclass(frozen=True)
class Ticker:
    symbol: str
    last: Decimal
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    volume: Optional[Decimal] = None
    quote_volume: Optional[Decimal] = None
    change_percent: Optional[Decimal] = None

    @property
    def spread_percent(self) -> Optional[Decimal]:
        if self.bid is None or self.ask is None:
            return None
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            return None
        return ((self.ask - self.bid) / self.bid) * Decimal("100")


# ============================================================
# GENERIC JSON & PARIBU TICKERS
# ============================================================

def get_json(url: str, params: Optional[dict[str, Any]] = None) -> Any:
    try:
        response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise ParibuHTTPError(f"Connection failure: {exc}") from exc

    if response.status_code != 200:
        raise ParibuHTTPError(f"HTTP {response.status_code}: {response.text[:500]}")

    try:
        return response.json()
    except ValueError as exc:
        raise ParibuSchemaError("API returned invalid JSON.") from exc


def fetch_tickers() -> list[Ticker]:
    payload = get_json(PARIBU_TICKER_URL)
    if not isinstance(payload, dict):
        raise ParibuSchemaError("Paribu ticker response is not a JSON object.")

    tickers: list[Ticker] = []
    for raw_symbol, raw_data in payload.items():
        if not isinstance(raw_data, dict):
            continue
        symbol = normalize_symbol(raw_symbol)
        if not is_tl_pair(symbol):
            continue

        last = D(first_value(raw_data, ("last", "lastPrice", "price", "close")))
        if last is None or last <= 0:
            continue

        bid = D(first_value(raw_data, ("bid", "lowestAsk", "bestBid")))
        ask = D(first_value(raw_data, ("ask", "highestBid", "bestAsk")))
        volume = D(first_value(raw_data, ("volume", "vol")))
        quote_volume = D(first_value(raw_data, ("quoteVolume", "volumeQuote", "turnover")))

        if quote_volume is None and volume is not None and volume > 0:
            quote_volume = volume * last

        change_percent = D(first_value(raw_data, ("changePercent", "percentChange", "percentage", "change")))

        tickers.append(
            Ticker(
                symbol=symbol,
                last=last,
                bid=bid,
                ask=ask,
                volume=volume,
                quote_volume=quote_volume,
                change_percent=change_percent,
            )
        )

    if not tickers:
        raise ParibuSchemaError("No usable Paribu TL markets found.")

    tickers.sort(
        key=lambda t: (t.quote_volume if t.quote_volume is not None else Decimal("0")),
        reverse=True,
    )
    return tickers


def get_market_snapshot() -> dict[str, Ticker]:
    return {ticker.symbol: ticker for ticker in fetch_tickers()}


# ============================================================
# BINANCE DATA
# ============================================================

def binance_symbol_for(symbol: str) -> str:
    asset = base_asset(symbol)
    return f"{asset}USDT"


def fetch_binance_candles(symbol: str, resolution: str, limit: int = DEFAULT_CANDLE_LIMIT) -> pd.DataFrame:
    if limit < 205:
        raise ValueError("At least 205 candles are required.")

    binance_symbol = binance_symbol_for(symbol)
    params = {"symbol": binance_symbol, "interval": resolution, "limit": limit}

    try:
        response = SESSION.get(BINANCE_CANDLES_URL, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise BinanceDataError(f"{binance_symbol}: Binance connection error: {exc}") from exc

    if response.status_code != 200:
        raise BinanceDataError(f"{binance_symbol}: Binance HTTP {response.status_code}: {response.text[:300]}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise BinanceDataError(f"{binance_symbol}: invalid Binance JSON.") from exc

    if not isinstance(payload, list):
        raise BinanceDataError(f"{binance_symbol}: unexpected Binance response.")

    parsed: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            continue

        open_price = D(row[1])
        high = D(row[2])
        low = D(row[3])
        close = D(row[4])
        volume = D(row[5])

        if open_price is None or high is None or low is None or close is None:
            continue

        parsed.append(
            {
                "timestamp": row[0],
                "open": float(open_price),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume) if volume is not None else 0.0,
            }
        )

    if len(parsed) < 205:
        raise BinanceDataError(f"{symbol} {resolution}: only {len(parsed)} valid Binance candles.")

    df = pd.DataFrame(parsed)
    df = df.dropna(subset=("open", "high", "low", "close")).reset_index(drop=True)
    df.attrs["source"] = "Binance"
    df.attrs["symbol"] = binance_symbol
    df.attrs["interval"] = resolution
    return df


def fetch_candles(symbol: str, resolution: str, limit: int = DEFAULT_CANDLE_LIMIT) -> pd.DataFrame:
    return fetch_binance_candles(symbol=symbol, resolution=resolution, limit=limit)
