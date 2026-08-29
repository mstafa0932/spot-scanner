from __future__ import annotations

"""
market_data.py
Paribu Spot market scanner data layer.

Design:
- Paribu remains the source of truth for the executable/current TL price,
  bid/ask and market liquidity.
- Candles are fetched primarily from Binance Data API (data-api.binance.vision)
  to bypass geo-blocks, with KuCoin as a resilient fallback.
- No trading is performed here.
- This module does NOT impose trading filters. Filtering belongs to scanner.py.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import time
import logging

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"
BINANCE_DATA_BASE = "https://data-api.binance.vision"
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

        bid = D(first_value(raw_data, ("bid", "bestBid", "best_bid", "highestBid", "highest_bid")))
        ask = D(first_value(raw_data, ("ask", "bestAsk", "best_ask", "lowestAsk", "lowest_ask")))
        
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
# Candle validation and normalization
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


# ---------------------------------------------------------------------------
# Candle Providers (Binance Primary + KuCoin Fallback)
# ---------------------------------------------------------------------------

def _fetch_binance_candles(symbol: str, resolution: str, limit: int) -> pd.DataFrame:
    base_asset = base_asset_from_paribu(symbol)
    if not base_asset:
        raise CandleUnavailableError(f"Could not determine base asset from {symbol}.")

    binance_symbol = f"{base_asset}USDT"
    interval = normalize_resolution(resolution)

    payload = get_json(
        f"{BINANCE_DATA_BASE}/api/v3/klines",
        params={
            "symbol": binance_symbol,
            "interval": interval,
            "limit": validate_limit(limit),
        },
    )

    if not isinstance(payload, list):
        raise CandleUnavailableError(f"Binance returned unexpected data for {symbol}.")

    rows = []
    for item in payload:
        rows.append({
            "timestamp": int(item[0]),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })

    df = validate_candles(pd.DataFrame(rows))
    df.attrs["source"] = "binance"
    df.attrs["provider_symbol"] = binance_symbol
    df.attrs["paribu_symbol"] = normalize_symbol(symbol)
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

    # 1) Primary: Binance Data API
    try:
        return _fetch_binance_candles(normalized_symbol, resolution, limit)
    except Exception as exc:
        logger.warning(f"Binance fetch failed for {normalized_symbol}: {exc}", exc_info=True)

    # 2) Fallback: KuCoin (with small delay to avoid rate limit)
    time.sleep(0.2)
    try:
        return _fetch_kucoin_candles(normalized_symbol, resolution, limit)
    except Exception as exc:
        logger.error(f"KuCoin fetch failed for {normalized_symbol}: {exc}", exc_info=True)

    raise CandleUnavailableError(f"All providers failed for {normalized_symbol}.")


# ---------------------------------------------------------------------------
# Order Book & Liquidity (Paribu)
# ---------------------------------------------------------------------------

def get_paribu_orderbook(symbol: str) -> Optional[dict[str, Any]]:
    """
    Fetch the actual Paribu order book for the given symbol.
    The endpoint expects the market in the format 'btc-tl'.
    """
    base = base_asset_from_paribu(symbol).lower()
    formatted_symbol = f"{base}-tl"
    url = "https://v4.paribu.com/market/board"
    
    try:
        payload = get_json(url, params={"market": formatted_symbol})
        if payload and payload.get("success") and "payload" in payload:
            return payload["payload"]
    except Exception as exc:
        logger.debug(f"Paribu orderbook fetch failed for {symbol}: {exc}")
    
    return None

def get_effective_spread(
    symbol: str, 
    min_volume_tl: Decimal = Decimal("17000")
) -> tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
    """
    Calculates the effective spread, ask, and bid by traversing the Paribu order book 
    until the required min_volume_tl is satisfied.
    Returns: (effective_spread_pct, effective_ask, effective_bid)
    """
    orderbook = get_paribu_orderbook(symbol)
    if not orderbook or "asks" not in orderbook or "bids" not in orderbook:
        return None, None, None

    asks = orderbook["asks"]
    bids = orderbook["bids"]

    def calculate_effective_price(order_list: list[Any], target_volume: Decimal) -> Decimal:
        cumulative_volume_tl = Decimal("0")
        effective_price = Decimal("0")
        
        for item in order_list:
            if len(item) < 2:
                continue
            price = D(item[0])
            amount = D(item[1])
            
            if price is None or amount is None:
                continue
                
            level_value_tl = price * amount
            cumulative_volume_tl += level_value_tl
            
            if cumulative_volume_tl >= target_volume:
                effective_price = price
                break
                
        return effective_price

    effective_ask = calculate_effective_price(asks, min_volume_tl)
    effective_bid = calculate_effective_price(bids, min_volume_tl)

    if effective_ask <= Decimal("0") or effective_bid <= Decimal("0"):
        return None, None, None

    effective_spread_pct = ((effective_ask - effective_bid) / effective_bid) * Decimal("100")

    return effective_spread_pct, effective_ask, effective_bid


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
        "candle_providers": ["binance", "kucoin"],
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
