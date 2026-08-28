from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Optional
import logging
import os
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# LOGGING
# ============================================================

LOGGER = logging.getLogger("spot_scanner.market_data")

if not LOGGER.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


# ============================================================
# PUBLIC MARKET-DATA ENDPOINTS
# ============================================================

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"

# IMPORTANT:
# Keep this empty until the exact current Paribu public
# Order Book REST path is confirmed from Paribu's official
# API documentation. We intentionally do NOT guess it.
PARIBU_ORDERBOOK_URL = os.getenv(
    "PARIBU_ORDERBOOK_URL",
    "",
)

BINANCE_EXCHANGE_INFO_URL = (
    "https://api.binance.com/api/v3/exchangeInfo"
)

BINANCE_KLINES_URL = (
    "https://api.binance.com/api/v3/klines"
)

BYBIT_INSTRUMENTS_URL = (
    "https://api.bybit.com/v5/market/instruments-info"
)

BYBIT_KLINES_URL = (
    "https://api.bybit.com/v5/market/kline"
)

REQUEST_TIMEOUT = int(
    os.getenv(
        "MARKET_DATA_TIMEOUT",
        "15",
    )
)

MIN_CANDLES = 205

DEFAULT_CANDLE_LIMIT = 250

DEFAULT_ORDERBOOK_DEPTH = 50

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================
# EXCEPTIONS
# ============================================================

class ParibuDataError(Exception):
    """
    Compatibility exception.

    scanner.py currently imports this name, so it must remain
    available from market_data.py.
    """


class MarketDataError(ParibuDataError):
    pass


class NetworkError(ParibuDataError):
    pass


class APIError(ParibuDataError):
    pass


class SchemaError(ParibuDataError):
    pass


class CandleUnavailableError(ParibuDataError):
    pass


class OrderBookUnavailableError(ParibuDataError):
    pass


# ============================================================
# DECIMAL HELPERS
# ============================================================

def to_decimal(
    value: Any,
) -> Optional[Decimal]:
    """
    Safely convert API values to Decimal.

    None / invalid / non-finite values become None.
    """

    if value is None or value == "":
        return None

    try:
        result = Decimal(str(value))

        if not result.is_finite():
            return None

        return result

    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return None


# ============================================================
# SYMBOL HELPERS
# ============================================================

def normalize_symbol(
    symbol: str,
) -> str:
    """
    Normalize:
        BTC/TL  -> BTC_TL
        btc-tl  -> BTC_TL
    """

    return (
        str(symbol)
        .strip()
        .upper()
        .replace("/", "_")
        .replace("-", "_")
        .replace(" ", "")
    )


def base_asset(
    symbol: str,
) -> str:
    """
    BTC_TL -> BTC
    """

    normalized = normalize_symbol(
        symbol
    )

    if "_" in normalized:
        return normalized.split(
            "_",
            1,
        )[0]

    if normalized.endswith("TRY"):
        return normalized[:-3]

    if normalized.endswith("TL"):
        return normalized[:-2]

    return normalized


def is_tl_pair(
    symbol: str,
) -> bool:

    normalized = normalize_symbol(
        symbol
    )

    return (
        normalized.endswith("_TL")
        or normalized.endswith("_TRY")
    )


def binance_symbol_for(
    paribu_symbol: str,
) -> str:

    return (
        base_asset(
            paribu_symbol
        )
        + "USDT"
    )


def _first(
    data: dict[str, Any],
    names: tuple[str, ...],
) -> Any:

    for name in names:

        if name in data:

            return data[name]

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
        backoff_factor=0.35,
        status_forcelist=(
            408,
            425,
            429,
            500,
            502,
            503,
            504,
        ),
        allowed_methods=frozenset(
            {"GET"}
        ),
        raise_on_status=False,
        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=20,
        pool_maxsize=20,
    )

    session.mount(
        "https://",
        adapter,
    )

    session.mount(
        "http://",
        adapter,
    )

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
    params: Optional[
        dict[str, Any]
    ] = None,
) -> Any:

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

    except requests.RequestException as exc:

        raise NetworkError(
            f"GET failed: {url}: {exc}"
        ) from exc

    if response.status_code != 200:

        raise APIError(
            f"HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    try:

        return response.json()

    except ValueError as exc:

        raise SchemaError(
            f"Invalid JSON from {url}"
        ) from exc


# ============================================================
# PARIBU TICKER
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
    def spread_percent(
        self,
    ) -> Optional[Decimal]:

        if (
            self.bid is None
            or self.ask is None
        ):
            return None

        if (
            self.bid <= 0
            or self.ask <= 0
            or self.ask < self.bid
        ):
            return None

        return (
            (self.ask - self.bid)
            / self.bid
            * Decimal("100")
        )


def extract_ticker_records(
    payload: Any,
) -> dict[
    str,
    dict[str, Any],
]:

    if not isinstance(
        payload,
        dict,
    ):

        raise SchemaError(
            "Paribu ticker response is not an object."
        )

    for container_name in (
        "data",
        "result",
        "tickers",
        "markets",
    ):

        container = payload.get(
            container_name
        )

        if isinstance(
            container,
            dict,
        ):

            result = {
                str(key): value
                for key, value in container.items()
                if isinstance(value, dict)
            }

            if result:
                return result

        if isinstance(
            container,
            list,
        ):

            result = {}

            for item in container:

                if not isinstance(
                    item,
                    dict,
                ):

                    continue

                symbol = _first(
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

                    result[
                        str(symbol)
                    ] = item

            if result:
                return result

    direct = {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }

    if direct:

        return direct

    raise SchemaError(
        "Could not find Paribu ticker records."
    )


def fetch_tickers() -> list[Ticker]:

    payload = get_json(
        PARIBU_TICKER_URL
    )

    records = extract_ticker_records(
        payload
    )

    result: list[Ticker] = []

    for raw_symbol, raw_data in records.items():

        symbol = normalize_symbol(
            raw_symbol
        )

        if not is_tl_pair(symbol):
            continue

        last = to_decimal(
            _first(
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

        if (
            last is None
            or last <= 0
        ):
            continue

        bid = to_decimal(
            _first(
                raw_data,
                (
                    "bid",
                    "bestBid",
                    "best_bid",
                    "highestBid",
                ),
            )
        )

        ask = to_decimal(
            _first(
                raw_data,
                (
                    "ask",
                    "bestAsk",
                    "best_ask",
                    "lowestAsk",
                ),
            )
        )

        volume = to_decimal(
            _first(
                raw_data,
                (
                    "volume",
                    "vol",
                    "baseVolume",
                    "base_volume",
                ),
            )
        )

        quote_volume = to_decimal(
            _first(
                raw_data,
                (
                    "quoteVolume",
                    "quote_volume",
                    "volumeQuote",
                    "turnover",
                ),
            )
        )

        # Only use this as a fallback estimate.
        if (
            quote_volume is None
            and volume is not None
            and volume > 0
        ):

            quote_volume = (
                volume * last
            )

        change_percent = to_decimal(
            _first(
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

        result.append(
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

    if not result:

        raise SchemaError(
            "No usable Paribu TL markets found."
        )

    result.sort(
        key=lambda item: (
            item.quote_volume
            if item.quote_volume is not None
            else Decimal("0")
        ),
        reverse=True,
    )

    return result


def get_market_snapshot() -> dict[
    str,
    Ticker,
]:

    return {
        ticker.symbol: ticker
        for ticker in fetch_tickers()
    }


# ============================================================
# BINANCE SYMBOL REGISTRY
# ============================================================

@lru_cache(maxsize=1)
def get_binance_spot_symbols() -> frozenset[str]:

    payload = get_json(
        BINANCE_EXCHANGE_INFO_URL
    )

    symbols = payload.get(
        "symbols"
    )

    if not isinstance(
        symbols,
        list,
    ):

        raise SchemaError(
            "Invalid Binance exchangeInfo response."
        )

    valid: set[str] = set()

    for item in symbols:

        if not isinstance(
            item,
            dict,
        ):

            continue

        symbol = item.get(
            "symbol"
        )

        status = item.get(
            "status"
        )

        permissions = item.get(
            "permissions"
        )

        if (
            not symbol
            or status != "TRADING"
        ):

            continue

        # Some API responses expose permissions,
        # while some compatible responses may not.
        if (
            isinstance(
                permissions,
                list,
            )
            and permissions
            and "SPOT" not in permissions
        ):

            continue

        valid.add(
            str(symbol).upper()
        )

    if not valid:

        raise SchemaError(
            "Binance returned no active Spot symbols."
        )

    return frozenset(valid)


def is_binance_symbol_available(
    paribu_symbol: str,
) -> bool:

    return (
        binance_symbol_for(
            paribu_symbol
        )
        in get_binance_spot_symbols()
    )


# ============================================================
# BYBIT SYMBOL REGISTRY
# ============================================================

@lru_cache(maxsize=1)
def get_bybit_spot_symbols() -> frozenset[str]:

    payload = get_json(
        BYBIT_INSTRUMENTS_URL,
        params={
            "category": "spot",
            "limit": 1000,
        },
    )

    if payload.get(
        "retCode"
    ) not in (
        0,
        None,
    ):

        raise APIError(
            "Bybit instruments error: "
            f"{payload.get('retMsg')}"
        )

    result = payload.get(
        "result"
    )

    if not isinstance(
        result,
        dict,
    ):

        raise SchemaError(
            "Invalid Bybit instruments result."
        )

    items = result.get(
        "list",
        [],
    )

    if not isinstance(
        items,
        list,
    ):

        raise SchemaError(
            "Invalid Bybit Spot symbol list."
        )

    valid: set[str] = set()

    for item in items:

        if not isinstance(
            item,
            dict,
        ):

            continue

        symbol = item.get(
            "symbol"
        )

        status = item.get(
            "status"
        )

        if (
            symbol
            and status == "Trading"
        ):

            valid.add(
                str(symbol).upper()
            )

    return frozenset(valid)


def is_bybit_symbol_available(
    paribu_symbol: str,
) -> bool:

    candidate = (
        base_asset(
            paribu_symbol
        )
        + "USDT"
    )

    return (
        candidate
        in get_bybit_spot_symbols()
    )


# ============================================================
# CANDLE VALIDATION
# ============================================================

def validate_candles(
    df: pd.DataFrame,
) -> pd.DataFrame:

    required = (
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )

    missing = [
        column
        for column in required
        if column not in df.columns
    ]

    if missing:

        raise SchemaError(
            "Missing candle columns: "
            + ", ".join(missing)
        )

    for column in (
        "timestamp",
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
            "timestamp",
            "open",
            "high",
            "low",
            "close",
        )
    )

    df = df[
        (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
        & (df["high"] >= df["low"])
        & (
            df["high"]
            >= df[
                [
                    "open",
                    "close",
                ]
            ].max(axis=1)
        )
        & (
            df["low"]
            <= df[
                [
                    "open",
                    "close",
                ]
            ].min(axis=1)
        )
    ]

    df = (
        df.drop_duplicates(
            subset=["timestamp"],
            keep="last",
        )
        .sort_values(
            "timestamp"
        )
        .reset_index(
            drop=True
        )
    )

    if len(df) < MIN_CANDLES:

        raise CandleUnavailableError(
            f"Only {len(df)} valid candles; "
            f"{MIN_CANDLES} required."
        )

    return df


# ============================================================
# BINANCE CANDLES
# ============================================================

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


def fetch_binance_candles(
    symbol: str,
    resolution: str = "15m",
    limit: int = DEFAULT_CANDLE_LIMIT,
) -> pd.DataFrame:

    normalized = normalize_symbol(
        symbol
    )

    if resolution not in BINANCE_INTERVALS:

        raise ValueError(
            f"Unsupported Binance interval: "
            f"{resolution}"
        )

    provider_symbol = (
        binance_symbol_for(
            normalized
        )
    )

    # Important:
    # We check the symbol before asking for candles.
    if not is_binance_symbol_available(
        normalized
    ):

        raise CandleUnavailableError(
            f"{normalized}: "
            f"{provider_symbol} is not an active "
            f"Binance Spot symbol."
        )

    limit = max(
        MIN_CANDLES,
        min(
            int(limit),
            1000,
        ),
    )

    payload = get_json(
        BINANCE_KLINES_URL,
        params={
            "symbol": provider_symbol,
            "interval": BINANCE_INTERVALS[
                resolution
            ],
            "limit": limit,
        },
    )

    if not isinstance(
        payload,
        list,
    ):

        raise SchemaError(
            f"Invalid Binance klines response "
            f"for {provider_symbol}."
        )

    rows = []

    for row in payload:

        if (
            not isinstance(
                row,
                list,
            )
            or len(row) < 6
        ):

            continue

        o = to_decimal(row[1])
        h = to_decimal(row[2])
        l = to_decimal(row[3])
        c = to_decimal(row[4])
        v = to_decimal(row[5])

        if None in (
            o,
            h,
            l,
            c,
        ):

            continue

        rows.append(
            {
                "timestamp": int(
                    row[0]
                ),
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

    df = validate_candles(
        pd.DataFrame(rows)
    )

    # Do NOT remove the newest candle.
    # indicator_engine.py must use i = -2
    # to analyze the latest closed candle.
    df.attrs["source"] = "Binance"
    df.attrs["provider_symbol"] = (
        provider_symbol
    )
    df.attrs["paribu_symbol"] = (
        normalized
    )
    df.attrs["resolution"] = (
        resolution
    )

    return df


# ============================================================
# BYBIT CANDLES
# ============================================================

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


def fetch_bybit_candles(
    symbol: str,
    resolution: str = "15m",
    limit: int = DEFAULT_CANDLE_LIMIT,
) -> pd.DataFrame:

    normalized = normalize_symbol(
        symbol
    )

    if resolution not in BYBIT_INTERVALS:

        raise ValueError(
            f"Unsupported Bybit interval: "
            f"{resolution}"
        )

    provider_symbol = (
        base_asset(normalized)
        + "USDT"
    )

    # Pre-check pair before requesting candles.
    if not is_bybit_symbol_available(
        normalized
    ):

        raise CandleUnavailableError(
            f"{normalized}: "
            f"{provider_symbol} is not an active "
            f"Bybit Spot symbol."
        )

    limit = max(
        MIN_CANDLES,
        min(
            int(limit),
            1000,
        ),
    )

    payload = get_json(
        BYBIT_KLINES_URL,
        params={
            "category": "spot",
            "symbol": provider_symbol,
            "interval": BYBIT_INTERVALS[
                resolution
            ],
            "limit": limit,
        },
    )

    if payload.get(
        "retCode"
    ) not in (
        0,
        None,
    ):

        raise APIError(
            f"Bybit kline error: "
            f"{payload.get('retMsg')}"
        )

    result = payload.get(
        "result"
    )

    if not isinstance(
        result,
        dict,
    ):

        raise SchemaError(
            f"Invalid Bybit result for "
            f"{provider_symbol}."
        )

    raw_rows = result.get(
        "list"
    )

    if not isinstance(
        raw_rows,
        list,
    ):

        raise CandleUnavailableError(
            f"No Bybit candles for "
            f"{provider_symbol}."
        )

    rows = []

    for row in raw_rows:

        if (
            not isinstance(
                row,
                list,
            )
            or len(row) < 6
        ):

            continue

        ts = to_decimal(row[0])
        o = to_decimal(row[1])
        h = to_decimal(row[2])
        l = to_decimal(row[3])
        c = to_decimal(row[4])
        v = to_decimal(row[5])

        if None in (
            ts,
            o,
            h,
            l,
            c,
        ):

            continue

        rows.append(
            {
                "timestamp": int(ts),
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

    df = validate_candles(
        pd.DataFrame(rows)
    )

    df.attrs["source"] = "Bybit"
    df.attrs["provider_symbol"] = (
        provider_symbol
    )
    df.attrs["paribu_symbol"] = (
        normalized
    )
    df.attrs["resolution"] = (
        resolution
    )

    return df


# ============================================================
# UNIFIED CANDLE FETCH
# ============================================================

def fetch_candles(
    symbol: str,
    resolution: str = "15m",
    limit: int = DEFAULT_CANDLE_LIMIT,
) -> pd.DataFrame:

    normalized = normalize_symbol(
        symbol
    )

    errors: list[str] = []

    # Provider 1 — Binance
    try:

        return fetch_binance_candles(
            normalized,
            resolution,
            limit,
        )

    except Exception as exc:

        errors.append(
            f"Binance: {exc}"
        )

        LOGGER.warning(
            "%s %s Binance candle failure: %s",
            normalized,
            resolution,
            exc,
        )

    # Provider 2 — Bybit
    try:

        return fetch_bybit_candles(
            normalized,
            resolution,
            limit,
        )

    except Exception as exc:

        errors.append(
            f"Bybit: {exc}"
        )

        LOGGER.warning(
            "%s %s Bybit candle failure: %s",
            normalized,
            resolution,
            exc,
        )

    raise CandleUnavailableError(
        f"{normalized} {resolution}: "
        "all candle providers failed. "
        + " | ".join(errors)
    )


# ============================================================
# PARIBU ORDER BOOK
# ============================================================

def fetch_order_book(
    symbol: str,
    depth: int = DEFAULT_ORDERBOOK_DEPTH,
) -> dict[str, Any]:

    if not PARIBU_ORDERBOOK_URL:

        raise OrderBookUnavailableError(
            "PARIBU_ORDERBOOK_URL is not configured. "
            "Depth-based execution analysis is therefore "
            "disabled until the verified Paribu endpoint "
            "is supplied."
        )

    normalized = normalize_symbol(
        symbol
    )

    payload = get_json(
        PARIBU_ORDERBOOK_URL,
        params={
            "symbol": normalized.lower(),
            "limit": max(
                1,
                min(
                    int(depth),
                    1000,
                ),
            ),
        },
    )

    if not isinstance(
        payload,
        dict,
    ):

        raise SchemaError(
            f"Invalid Paribu order-book response "
            f"for {normalized}."
        )

    candidates = (
        payload,
        payload.get("data"),
        payload.get("result"),
        payload.get("orderBook"),
        payload.get("orderbook"),
    )

    for candidate in candidates:

        if not isinstance(
            candidate,
            dict,
        ):

            continue

        asks = candidate.get(
            "asks"
        )

        bids = candidate.get(
            "bids"
        )

        if (
            isinstance(asks, list)
            and isinstance(bids, list)
        ):

            return {
                "symbol": normalized,
                "asks": asks,
                "bids": bids,
                "timestamp": candidate.get(
                    "timestamp",
                    candidate.get("ts"),
                ),
                "source": "Paribu",
            }

    raise SchemaError(
        f"{normalized}: "
        "response did not contain asks/bids."
    )


# ============================================================
# LEVEL-1 EXECUTION SNAPSHOT
# ============================================================

def get_execution_snapshot(
    symbol: str,
    depth: int = DEFAULT_ORDERBOOK_DEPTH,
) -> dict[str, Any]:

    normalized = normalize_symbol(
        symbol
    )

    snapshot = (
        get_market_snapshot()
    )

    ticker = snapshot.get(
        normalized
    )

    if ticker is None:

        raise MarketDataError(
            f"{normalized}: "
            "not found in Paribu ticker."
        )

    result = {
        "symbol": normalized,
        "paribu_last": ticker.last,
        "paribu_bid": ticker.bid,
        "paribu_ask": ticker.ask,
        "spread_pct": ticker.spread_percent,
        "orderbook": None,
    }

    # Order book is optional until the verified endpoint
    # is configured.
    try:

        result["orderbook"] = (
            fetch_order_book(
                normalized,
                depth,
            )
        )

    except OrderBookUnavailableError as exc:

        result[
            "orderbook_error"
        ] = str(exc)

    return result


# ============================================================
# HEALTH CHECK
# ============================================================

def health_check() -> dict[str, Any]:

    started = time.time()

    snapshot = (
        get_market_snapshot()
    )

    result = {
        "paribu_ticker": True,
        "markets": len(snapshot),
        "binance_registry": False,
        "bybit_registry": False,
        "orderbook_configured": bool(
            PARIBU_ORDERBOOK_URL
        ),
    }

    try:

        get_binance_spot_symbols()

        result[
            "binance_registry"
        ] = True

    except Exception as exc:

        LOGGER.warning(
            "Binance registry failed: %s",
            exc,
        )

    try:

        get_bybit_spot_symbols()

        result[
            "bybit_registry"
        ] = True

    except Exception as exc:

        LOGGER.warning(
            "Bybit registry failed: %s",
            exc,
        )

    result[
        "seconds"
    ] = round(
        time.time() - started,
        2,
    )

    return result


# ============================================================
# COMMAND-LINE TEST
# ============================================================

if __name__ == "__main__":

    print(
        "=== PARIBU MARKET DATA HEALTH CHECK ==="
    )

    try:

        result = health_check()

        print(
            result
        )

    except Exception as exc:

        print(
            "HEALTH CHECK ERROR:",
            exc,
        )

        raise SystemExit(1)
