from __future__ import annotations

"""
market_data.py
Paribu Spot market scanner data layer.

Design:
- Paribu remains the source of truth for the executable/current TL price,
  bid/ask and market liquidity.
- Candles are fetched from reliable public market-data providers because
  Paribu's undocumented candle URL is not suitable as a production dependency.
- Candle provider order:
    1) KuCoin public market-data API (Used to bypass GitHub Actions geo-blocks)
- No trading is performed here.
- This module does NOT impose trading filters. Filtering belongs to scanner.py.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"
KUCOIN_BASE_URL = "https://api.kucoin.com"

REQUEST_TIMEOUT = 10

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# We need enough history for EMA200 + indicators.
MIN_VALID_CANDLES = 205


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ParibuDataError(Exception):
    pass

class ParibuConfigurationError(ParibuDataError):
    pass

class ParibuHTTPError(ParibuDataError):
    pass

class ParibuSchemaError(ParibuDataError):
    pass

class CandleUnavailableError(ParibuDataError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

def normalize_symbol(symbol: str) -> str:
    return (
        str(symbol)
        .strip()
        .upper()
        .replace("/", "_")
        .replace("-", "_")
        .replace(" ", "")
    )

def base_asset_from_paribu(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if "_" in normalized:
        return normalized.split("_", 1)[0]
    if normalized.endswith("TRY"):
        return normalized[:-3]
    if normalized.endswith("TL"):
        return normalized[:-2]
    return normalized

def is_tl_pair(symbol: str) -> bool:
    normalized = normalize_symbol(symbol)
    return (
        normalized.endswith("_TL")
        or normalized.endswith("_TRY")
    )

def first_value(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.35,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=20,
        pool_maxsize=20,
    )
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

def get_json(
    url: str,
    params: Optional[dict[str, Any]] = None,
) -> Any:
    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ParibuHTTPError(
            f"Connection failure: {exc}"
        ) from exc

    if response.status_code != 200:
        raise ParibuHTTPError(
            f"HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ParibuSchemaError(
            "Server returned invalid JSON."
        ) from exc


# ---------------------------------------------------------------------------
# Paribu ticker
# ---------------------------------------------------------------------------

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
        if self.bid <= 0 or self.ask <= 0:
            return None
        if self.ask < self.bid:
            return None
        return (
            (self.ask - self.bid)
            / self.bid
        ) * Decimal("100")

def extract_ticker_records(
    payload: Any,
) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ParibuSchemaError("Paribu ticker response is not a JSON object.")
    for container_name in ("data", "result", "tickers", "markets"):
        container = payload.get(container_name)
        if isinstance(container, dict):
            records = {
                str(key): value
                for key, value in container.items()
                if isinstance(value, dict)
            }
            if records:
                return records
        if isinstance(container, list):
            records: dict[str, dict[str, Any]] = {}
            for item in container:
                if not isinstance(item, dict):
                    continue
                symbol = first_value(item, ("symbol", "pair", "market", "market_symbol", "instrument"))
                if symbol:
                    records[str(symbol)] = item
            if records:
                return records

    direct = {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }
    if direct:
        return direct

    raise ParibuSchemaError("Could not find ticker records in Paribu response.")

def fetch_tickers() -> list[Ticker]:
    payload = get_json(PARIBU_TICKER_URL)
    records = extract_ticker_records(payload)
    tickers: list[Ticker] = []

    for raw_symbol, raw_data in records.items():
        symbol = normalize_symbol(raw_symbol)
        if not is_tl_pair(symbol):
            continue

        last = D(first_value(raw_data, ("last", "lastPrice", "last_price", "price", "close")))
        if last is None or last <= 0:
            continue

        bid = D(first_value(raw_data, ("bid", "bestBid", "best_bid")))
        ask = D(first_value(raw_data, ("ask", "bestAsk", "best_ask")))
        volume = D(first_value(raw_data, ("volume", "vol", "baseVolume", "base_volume")))
        quote_volume = D(first_value(raw_data, ("quoteVolume", "quote_volume", "volumeQuote", "turnover")))

        if quote_volume is None and volume is not None and volume > 0:
            quote_volume = volume * last

        change_percent = D(first_value(raw_data, ("changePercent", "change_percent", "percentChange", "percent_change", "percentage", "change")))

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
        key=lambda ticker: (
            ticker.quote_volume
            if ticker.quote_volume is not None
            else Decimal("0")
        ),
        reverse=True,
    )
    return tickers

def get_market_snapshot() -> dict[str, Ticker]:
    tickers = fetch_tickers()
    return {ticker.symbol: ticker for ticker in tickers}


# ---------------------------------------------------------------------------
# Candle providers (Switched to KuCoin for GitHub Actions bypass)
# ---------------------------------------------------------------------------

KUCOIN_INTERVALS = {
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1hour",
    "2h": "2hour",
    "4h": "4hour",
    "6h": "6hour",
    "8h": "8hour",
    "12h": "12hour",
    "1d": "1day",
    "1w": "1week",
}

def normalize_resolution(resolution: str) -> str:
    raw = str(resolution).strip().lower()
    aliases = {
        "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
        "60": "1h", "120": "2h", "240": "4h", "360": "6h", "720": "12h",
        "d": "1d", "day": "1d", "w": "1w", "week": "1w",
    }
    return aliases.get(raw, raw)

def validate_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 250
    return max(50, min(value, 1000))

def validate_candles(df: pd.DataFrame) -> pd.DataFrame:
    required = ("timestamp", "open", "high", "low", "close", "volume")
    for column in required:
        if column not in df.columns:
            raise ParibuSchemaError(f"Missing candle column: {column}")

    for column in ("open", "high", "low", "close", "volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=("open", "high", "low", "close")).copy()
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)].copy()
    df = df[(df["high"] >= df[["open", "close"]].max(axis=1)) & (df["low"] <= df[["open", "close"]].min(axis=1)) & (df["high"] >= df["low"])].copy()

    df = df.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)

    if len(df) < MIN_VALID_CANDLES:
        raise CandleUnavailableError(f"Only {len(df)} valid candles; {MIN_VALID_CANDLES} required.")

    return df

def _fetch_kucoin_candles(symbol: str, resolution: str, limit: int) -> pd.DataFrame:
    base_asset = base_asset_from_paribu(symbol)
    if not base_asset:
        raise CandleUnavailableError(f"Could not determine base asset from {symbol}.")

    interval = KUCOIN_INTERVALS.get(normalize_resolution(resolution), "15min")
    provider_symbol = f"{base_asset}-USDT"

    payload = get_json(
        f"{KUCOIN_BASE_URL}/api/v1/market/candles",
        params={
            "symbol": provider_symbol,
            "type": interval,
        },
    )

    if payload.get("code") != "200000":
        raise CandleUnavailableError(f"KuCoin error: {payload.get('msg')}")

    data = payload.get("data", [])
    if not isinstance(data, list) or not data:
        raise CandleUnavailableError(f"KuCoin returned no usable candles for {symbol}.")

    rows = []
    # KuCoin returns data reversed (newest first), we slice and reverse it
    for row in reversed(data[:validate_limit(limit)]):
        rows.append({
            "timestamp": int(row[0]) * 1000,
            "open": float(row[1]),
            "close": float(row[2]),
            "high": float(row[3]),
            "low": float(row[4]),
            "volume": float(row[5]),
        })

    df = validate_candles(pd.DataFrame(rows))
    df.attrs["source"] = "kucoin"
    df.attrs["provider_symbol"] = provider_symbol
    df.attrs["paribu_symbol"] = normalize_symbol(symbol)
    return df

def fetch_candles(symbol: str, resolution: str, limit: int = 250) -> pd.DataFrame:
    normalized_symbol = normalize_symbol(symbol)
    if not normalized_symbol:
        raise ParibuConfigurationError("Empty symbol.")

    try:
        return _fetch_kucoin_candles(normalized_symbol, resolution, limit)
    except Exception as exc:
        raise CandleUnavailableError(f"Failed to fetch from provider for {normalized_symbol}: {exc}")


# ---------------------------------------------------------------------------
# Order Book (Added to fix the import error in scanner.py)
# ---------------------------------------------------------------------------

def fetch_order_book(symbol: str, limit: int = 20) -> dict[str, list[Any]]:
    """
    Fetches order book data (bids/asks).
    """
    base_asset = base_asset_from_paribu(symbol)
    if not base_asset:
        return {"bids": [], "asks": []}
        
    kucoin_symbol = f"{base_asset}-USDT"
    url = f"{KUCOIN_BASE_URL}/api/v1/market/orderbook/level2_20"
    
    try:
        payload = get_json(url, params={"symbol": kucoin_symbol})
        if payload.get("code") == "200000":
            return payload.get("data", {"bids": [], "asks": []})
    except Exception:
        pass
        
    return {"bids": [], "asks": []}


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def health_check() -> dict[str, Any]:
    started = time.time()
    snapshot = get_market_snapshot()
    elapsed = time.time() - started
    return {
        "markets": len(snapshot),
        "seconds": round(elapsed, 2),
        "paribu_ticker": True,
        "candle_providers": ["kucoin"],
        "min_valid_candles": MIN_VALID_CANDLES,
    }

def candle_health_check(symbol: str = "BTC_TL", resolution: str = "15m") -> dict[str, Any]:
    started = time.time()
    df = fetch_candles(symbol, resolution, 250)
    elapsed = time.time() - started
    return {
        "paribu_symbol": normalize_symbol(symbol),
        "resolution": normalize_resolution(resolution),
        "candles": len(df),
        "source": df.attrs.get("source"),
        "provider_symbol": df.attrs.get("provider_symbol"),
        "seconds": round(elapsed, 2),
        "latest_timestamp": (int(df["timestamp"].iloc[-1]) if len(df) else None),
    }
