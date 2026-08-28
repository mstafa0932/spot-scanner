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
    1) Binance public market-data API
    2) Binance main API
    3) Bybit Spot public API
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

# Binance recommends data-api.binance.vision for public market data.
BINANCE_BASE_URLS = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api-gcp.binance.com",
)

BYBIT_BASE_URL = "https://api.bybit.com"

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
        raise ParibuSchemaError(
            "Paribu ticker response is not a JSON object."
        )

    # Prefer known containers before treating every top-level dict as a pair.
    for container_name in (
        "data",
        "result",
        "tickers",
        "markets",
    ):

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

                symbol = first_value(
                    item,
                    (
                        "symbol",
                        "pair",
                        "market",
                        "market_symbol",
                        "instrument",
                    ),
                )

                if symbol:
                    records[str(symbol)] = item

            if records:
                return records

    # Current/legacy Paribu ticker can also be pair -> object.
    direct = {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }

    if direct:
        return direct

    raise ParibuSchemaError(
        "Could not find ticker records in Paribu response."
    )


def fetch_tickers() -> list[Ticker]:

    payload = get_json(PARIBU_TICKER_URL)
    records = extract_ticker_records(payload)

    tickers: list[Ticker] = []

    for raw_symbol, raw_data in records.items():

        symbol = normalize_symbol(raw_symbol)

        if not is_tl_pair(symbol):
            continue

        last = D(
            first_value(
                raw_data,
                (
                    "last",
                    "lastPrice",
                    "last_price",
                    "price",
                    "close",
                ),
            )
        )

        if last is None or last <= 0:
            continue

        bid = D(
            first_value(
                raw_data,
                (
                    "bid",
                    "bestBid",
                    "best_bid",
                ),
            )
        )

        ask = D(
            first_value(
                raw_data,
                (
                    "ask",
                    "bestAsk",
                    "best_ask",
                ),
            )
        )

        volume = D(
            first_value(
                raw_data,
                (
                    "volume",
                    "vol",
                    "baseVolume",
                    "base_volume",
                ),
            )
        )

        quote_volume = D(
            first_value(
                raw_data,
                (
                    "quoteVolume",
                    "quote_volume",
                    "volumeQuote",
                    "turnover",
                ),
            )
        )

        # If Paribu gives base volume but not quote volume,
        # estimate quote turnover from last price.
        if (
            quote_volume is None
            and volume is not None
            and volume > 0
        ):
            quote_volume = volume * last

        change_percent = D(
            first_value(
                raw_data,
                (
                    "changePercent",
                    "change_percent",
                    "percentChange",
                    "percent_change",
                    "percentage",
                    "change",
                ),
            )
        )

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
        raise ParibuSchemaError(
            "No usable Paribu TL markets found."
        )

    # Highest available turnover first.
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
    """
    Returns the complete currently visible Paribu TL market snapshot.

    IMPORTANT:
    No price/volume/trend filter is applied here.
    """

    tickers = fetch_tickers()

    return {
        ticker.symbol: ticker
        for ticker in tickers
    }


# ---------------------------------------------------------------------------
# Candle providers
# ---------------------------------------------------------------------------

BINANCE_INTERVALS = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "1w": "1w",
}


BYBIT_INTERVALS = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "2h": "120",
    "4h": "240",
    "6h": "360",
    "12h": "720",
    "1d": "D",
    "1w": "W",
}


def normalize_resolution(resolution: str) -> str:

    raw = str(resolution).strip().lower()

    aliases = {
        "1": "1m",
        "3": "3m",
        "5": "5m",
        "15": "15m",
        "30": "30m",
        "60": "1h",
        "120": "2h",
        "240": "4h",
        "360": "6h",
        "720": "12h",
        "d": "1d",
        "day": "1d",
        "w": "1w",
        "week": "1w",
    }

    return aliases.get(raw, raw)


def validate_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 250

    # Both providers support at least this range.
    return max(50, min(value, 1000))


def validate_candles(df: pd.DataFrame) -> pd.DataFrame:

    required = (
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )

    for column in required:
        if column not in df.columns:
            raise ParibuSchemaError(
                f"Missing candle column: {column}"
            )

    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
    ):
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(
        subset=(
            "open",
            "high",
            "low",
            "close",
        )
    ).copy()

    df = df[
        (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
    ].copy()

    # Remove impossible OHLC rows.
    df = df[
        (df["high"] >= df[["open", "close"]].max(axis=1))
        & (df["low"] <= df[["open", "close"]].min(axis=1))
        & (df["high"] >= df["low"])
    ].copy()

    # De-duplicate timestamps and sort oldest -> newest.
    df = (
        df.drop_duplicates(
            subset=["timestamp"],
            keep="last",
        )
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    if len(df) < MIN_VALID_CANDLES:
        raise CandleUnavailableError(
            f"Only {len(df)} valid candles; "
            f"{MIN_VALID_CANDLES} required."
        )

    return df


def _parse_binance_klines(
    payload: Any,
    symbol: str,
) -> pd.DataFrame:

    if not isinstance(payload, list):
        raise ParibuSchemaError(
            f"Binance returned invalid kline payload for {symbol}."
        )

    rows = []

    for row in payload:

        if not isinstance(row, (list, tuple)):
            continue

        if len(row) < 6:
            continue

        timestamp = row[0]

        o = D(row[1])
        h = D(row[2])
        l = D(row[3])
        c = D(row[4])
        v = D(row[5])

        if None in (o, h, l, c):
            continue

        rows.append(
            {
                "timestamp": int(timestamp),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": (
                    float(v)
                    if v is not None
                    else 0.0
                ),
            }
        )

    if not rows:
        raise CandleUnavailableError(
            f"Binance returned no usable candles for {symbol}."
        )

    return validate_candles(
        pd.DataFrame(rows)
    )


def _fetch_binance_candles(
    symbol: str,
    resolution: str,
    limit: int,
) -> pd.DataFrame:

    base_asset = base_asset_from_paribu(symbol)

    if not base_asset:
        raise CandleUnavailableError(
            f"Could not determine base asset from {symbol}."
        )

    interval = BINANCE_INTERVALS.get(
        normalize_resolution(resolution)
    )

    if interval is None:
        raise ParibuConfigurationError(
            f"Unsupported candle resolution: {resolution}"
        )

    binance_symbol = f"{base_asset}USDT"

    errors = []

    for base_url in BINANCE_BASE_URLS:

        url = (
            f"{base_url}"
            "/api/v3/klines"
        )

        try:

            payload = get_json(
                url,
                params={
                    "symbol": binance_symbol,
                    "interval": interval,
                    "limit": validate_limit(limit),
                },
            )

            df = _parse_binance_klines(
                payload,
                binance_symbol,
            )

            df.attrs["source"] = "binance"
            df.attrs["provider_symbol"] = binance_symbol
            df.attrs["paribu_symbol"] = normalize_symbol(symbol)

            return df

        except Exception as exc:
            errors.append(
                f"{base_url}: {exc}"
            )

    raise CandleUnavailableError(
        "Binance candle sources failed for "
        f"{binance_symbol}. "
        + " | ".join(errors[-3:])
    )


def _parse_bybit_klines(
    payload: Any,
    symbol: str,
) -> pd.DataFrame:

    if not isinstance(payload, dict):
        raise ParibuSchemaError(
            f"Bybit returned invalid payload for {symbol}."
        )

    if payload.get("retCode") not in (0, None):
        raise CandleUnavailableError(
            f"Bybit error {payload.get('retCode')}: "
            f"{payload.get('retMsg')}"
        )

    result = payload.get("result")

    if not isinstance(result, dict):
        raise ParibuSchemaError(
            f"Bybit result is invalid for {symbol}."
        )

    rows_raw = result.get("list")

    if not isinstance(rows_raw, list):
        raise CandleUnavailableError(
            f"Bybit returned no candle list for {symbol}."
        )

    rows = []

    for row in rows_raw:

        if not isinstance(row, (list, tuple)):
            continue

        if len(row) < 6:
            continue

        # Bybit:
        # [startTime, open, high, low, close, volume, turnover]
        timestamp = D(row[0])
        o = D(row[1])
        h = D(row[2])
        l = D(row[3])
        c = D(row[4])
        v = D(row[5])

        if None in (
            timestamp,
            o,
            h,
            l,
            c,
        ):
            continue

        rows.append(
            {
                "timestamp": int(timestamp),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": (
                    float(v)
                    if v is not None
                    else 0.0
                ),
            }
        )

    if not rows:
        raise CandleUnavailableError(
            f"Bybit returned no usable candles for {symbol}."
        )

    return validate_candles(
        pd.DataFrame(rows)
    )


def _fetch_bybit_candles(
    symbol: str,
    resolution: str,
    limit: int,
) -> pd.DataFrame:

    base_asset = base_asset_from_paribu(symbol)

    if not base_asset:
        raise CandleUnavailableError(
            f"Could not determine base asset from {symbol}."
        )

    interval = BYBIT_INTERVALS.get(
        normalize_resolution(resolution)
    )

    if interval is None:
        raise ParibuConfigurationError(
            f"Unsupported candle resolution: {resolution}"
        )

    provider_symbol = f"{base_asset}USDT"

    payload = get_json(
        f"{BYBIT_BASE_URL}/v5/market/kline",
        params={
            "category": "spot",
            "symbol": provider_symbol,
            "interval": interval,
            "limit": validate_limit(limit),
        },
    )

    df = _parse_bybit_klines(
        payload,
        provider_symbol,
    )

    df.attrs["source"] = "bybit"
    df.attrs["provider_symbol"] = provider_symbol
    df.attrs["paribu_symbol"] = normalize_symbol(symbol)

    return df


# ---------------------------------------------------------------------------
# Public candle function used by scanner.py
# ---------------------------------------------------------------------------

def fetch_candles(
    symbol: str,
    resolution: str,
    limit: int = 250,
) -> pd.DataFrame:
    """
    Fetch candles for a Paribu TL pair.

    Example:
        fetch_candles("MAGIC_TL", "15m", 250)

    The function automatically tries:
        Binance -> Binance fallback -> Bybit

    The returned DataFrame is always chronological (oldest -> newest).
    The newest candle may still be open; indicator_engine.py already uses
    the last CLOSED candle.
    """

    normalized_symbol = normalize_symbol(symbol)

    if not normalized_symbol:
        raise ParibuConfigurationError(
            "Empty symbol."
        )

    errors = []

    # Provider 1: Binance
    try:
        return _fetch_binance_candles(
            normalized_symbol,
            resolution,
            limit,
        )

    except Exception as exc:
        errors.append(
            f"Binance: {exc}"
        )

    # Provider 2: Bybit Spot
    try:
        return _fetch_bybit_candles(
            normalized_symbol,
            resolution,
            limit,
        )

    except Exception as exc:
        errors.append(
            f"Bybit: {exc}"
        )

    raise CandleUnavailableError(
        f"No candle provider succeeded for "
        f"{normalized_symbol} {resolution}. "
        + " | ".join(errors)
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def health_check() -> dict[str, Any]:

    started = time.time()

    snapshot = get_market_snapshot()

    elapsed = time.time() - started

    return {
        "markets": len(snapshot),
        "seconds": round(
            elapsed,
            2,
        ),
        "paribu_ticker": True,
        "candle_providers": [
            "binance",
            "bybit",
        ],
        "min_valid_candles": MIN_VALID_CANDLES,
    }


def candle_health_check(
    symbol: str = "BTC_TL",
    resolution: str = "15m",
) -> dict[str, Any]:

    started = time.time()

    df = fetch_candles(
        symbol,
        resolution,
        250,
    )

    elapsed = time.time() - started

    return {
        "paribu_symbol": normalize_symbol(symbol),
        "resolution": normalize_resolution(resolution),
        "candles": len(df),
        "source": df.attrs.get("source"),
        "provider_symbol": df.attrs.get(
            "provider_symbol"
        ),
        "seconds": round(
            elapsed,
            2,
        ),
        "latest_timestamp": (
            int(df["timestamp"].iloc[-1])
            if len(df)
            else None
        ),
    }
