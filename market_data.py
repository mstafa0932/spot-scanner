from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from functools import lru_cache
import os
import time
import logging

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
# ENDPOINTS
# ============================================================

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"

# IMPORTANT:
# Set this ONLY after verifying Paribu's current API documentation.
#
# Example:
# export PARIBU_ORDERBOOK_URL="https://<verified-paribu-endpoint>"
#
# Do NOT guess this URL.
PARIBU_ORDERBOOK_URL = os.getenv(
    "PARIBU_ORDERBOOK_URL",
    "",
)

BINANCE_INFO_URL = (
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
    "spot-scanner/2.0 "
    "(public-market-data-only)"
)


# ============================================================
# EXCEPTIONS
# ============================================================

class MarketDataError(Exception):
    """Base market-data exception."""


class NetworkError(MarketDataError):
    pass


class APIError(MarketDataError):
    pass


class SchemaError(MarketDataError):
    pass


class MissingConfigurationError(MarketDataError):
    pass


class CandleUnavailableError(MarketDataError):
    pass


class OrderBookUnavailableError(MarketDataError):
    pass


# ============================================================
# DECIMAL
# ============================================================

def to_decimal(
    value: Any,
) -> Optional[Decimal]:

    if value is None or value == "":
        return None

    try:

        result = Decimal(
            str(value)
        )

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
# SYMBOLS
# ============================================================

def normalize_symbol(
    symbol: str,
) -> str:

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


def to_binance_spot_symbol(
    paribu_symbol: str,
) -> str:

    return (
        base_asset(paribu_symbol)
        + "USDT"
    )


# ============================================================
# HTTP
# ============================================================

def create_session() -> requests.Session:

    session = requests.Session()

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
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

    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
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
            f"HTTP {response.status_code} "
            f"from {url}: "
            f"{response.text[:500]}"
        )

    try:

        return response.json()

    except ValueError as exc:

        raise SchemaError(
            f"Invalid JSON from {url}"
        ) from exc


# ============================================================
# TICKER
# ============================================================

@dataclass(frozen=True)
class Ticker:

    symbol: str

    last: Decimal

    bid: Optional[Decimal]

    ask: Optional[Decimal]

    volume: Optional[Decimal]

    quote_volume: Optional[Decimal]

    change_percent: Optional[Decimal]

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
            (
                self.ask
                - self.bid
            )
            / self.bid
            * Decimal("100")
        )


def _first(
    data: dict[str, Any],
    names: tuple[str, ...],
) -> Any:

    for name in names:

        if name in data:

            return data[name]

    return None


def _extract_ticker_records(
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
            "Paribu ticker response "
            "is not an object."
        )

    for key in (
        "data",
        "result",
        "tickers",
        "markets",
    ):

        container = payload.get(key)

        if isinstance(
            container,
            dict,
        ):

            result = {
                str(k): v
                for k, v in container.items()
                if isinstance(v, dict)
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
                    ),
                )

                if symbol:

                    result[
                        str(symbol)
                    ] = item

            if result:
                return result

    # Legacy/direct form:
    direct = {
        str(k): v
        for k, v in payload.items()
        if isinstance(v, dict)
    }

    if direct:
        return direct

    raise SchemaError(
        "No ticker records found."
    )


def fetch_tickers() -> list[Ticker]:

    payload = get_json(
        PARIBU_TICKER_URL
    )

    records = (
        _extract_ticker_records(
            payload
        )
    )

    result: list[Ticker] = []

    for raw_symbol, raw in records.items():

        symbol = normalize_symbol(
            raw_symbol
        )

        if not is_tl_pair(symbol):
            continue

        last = to_decimal(
            _first(
                raw,
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

        bid = to_decimal(
            _first(
                raw,
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
                raw,
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
                raw,
                (
                    "volume",
                    "vol",
                    "baseVolume",
                ),
            )
        )

        quote_volume = to_decimal(
            _first(
                raw,
                (
                    "quoteVolume",
                    "quote_volume",
                    "turnover",
                    "volumeQuote",
                ),
            )
        )

        # Fallback estimate only.
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
                raw,
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
            "No usable Paribu TL tickers."
        )

    result.sort(
        key=lambda x: (
            x.quote_volume
            if x.quote_volume is not None
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
# BINANCE SYMBOL VALIDATION
# ============================================================

@lru_cache(maxsize=1)
def get_binance_spot_symbols() -> frozenset[str]:

    payload = get_json(
        BINANCE_INFO_URL
    )

    symbols = payload.get(
        "symbols"
    )

    if not isinstance(
        symbols,
        list,
    ):

        raise SchemaError(
            "Invalid Binance exchangeInfo."
        )

    valid = set()

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

        is_spot = (
            isinstance(
                permissions,
                list,
            )
            and (
                "SPOT"
                in permissions
            )
        )

        if (
            symbol
            and status == "TRADING"
            and is_spot
        ):

            valid.add(
                str(symbol).upper()
            )

    return frozenset(
        valid
    )


def is_binance_spot_symbol_available(
    paribu_symbol: str,
) -> bool:

    candidate = (
        to_binance_spot_symbol(
            paribu_symbol
        )
    )

    return (
        candidate
        in get_binance_spot_symbols()
    )


# ============================================================
# BYBIT SYMBOL VALIDATION
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

        raise SchemaError(
            "Bybit instruments-info error: "
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
            "Invalid Bybit instruments response."
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
            "Invalid Bybit spot symbol list."
        )

    valid = set()

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

    return frozenset(
        valid
    )


def is_bybit_spot_symbol_available(
    paribu_symbol: str,
) -> bool:

    candidate = (
        base_asset(paribu_symbol)
        + "USDT"
    )

    return (
        candidate
        in get_bybit_spot_symbols()
    )


# ============================================================
# CANDLE VALIDATION
# ============================================================

def _validate_candles(
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
                ["open", "close"]
            ].max(axis=1)
        )
        & (
            df["low"]
            <= df[
                ["open", "close"]
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

    binance_symbol = (
        to_binance_spot_symbol(
            normalized
        )
    )

    if not is_binance_spot_symbol_available(
        normalized
    ):

        raise CandleUnavailableError(
            f"{normalized}: "
            f"{binance_symbol} is not a "
            f"currently tradable Binance Spot symbol."
        )

    interval = BINANCE_INTERVALS.get(
        resolution
    )

    if interval is None:

        raise ValueError(
            f"Unsupported Binance interval: "
            f"{resolution}"
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
            "symbol": binance_symbol,
            "interval": interval,
            "limit": limit,
        },
    )

    if not isinstance(
        payload,
        list,
    ):

        raise SchemaError(
            f"Invalid Binance kline response "
            f"for {binance_symbol}."
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

    df = _validate_candles(
        pd.DataFrame(rows)
    )

    # Keep the current candle.
    # indicator_engine.py must use -2.
    df.attrs["source"] = "Binance"
    df.attrs["provider_symbol"] = (
        binance_symbol
    )
    df.attrs["paribu_symbol"] = (
        normalized
    )
    df.attrs["resolution"] = resolution

    return df


# ============================================================
# BYBIT CANDLES FALLBACK
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

    provider_symbol = (
        base_asset(normalized)
        + "USDT"
    )

    if not is_bybit_spot_symbol_available(
        normalized
    ):

        raise CandleUnavailableError(
            f"{normalized}: "
            f"{provider_symbol} is not an active "
            f"Bybit Spot symbol."
        )

    interval = BYBIT_INTERVALS.get(
        resolution
    )

    if interval is None:

        raise ValueError(
            f"Unsupported Bybit interval: "
            f"{resolution}"
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
            "interval": interval,
            "limit": limit,
        },
    )

    if payload.get(
        "retCode"
    ) not in (
        0,
        None,
    ):

        raise CandleUnavailableError(
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
            f"Invalid Bybit result "
            f"for {provider_symbol}."
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

    df = _validate_candles(
        pd.DataFrame(rows)
    )

    df.attrs["source"] = "Bybit"
    df.attrs["provider_symbol"] = (
        provider_symbol
    )
    df.attrs["paribu_symbol"] = (
        normalized
    )
    df.attrs["resolution"] = resolution

    return df


# ============================================================
# PUBLIC CANDLE FUNCTION
# ============================================================

def fetch_candles(
    symbol: str,
    resolution: str = "15m",
    limit: int = DEFAULT_CANDLE_LIMIT,
) -> pd.DataFrame:

    normalized = normalize_symbol(
        symbol
    )

    errors = []

    # --------------------------------------------------------
    # Binance first
    # --------------------------------------------------------

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
            "%s %s Binance candles failed: %s",
            normalized,
            resolution,
            exc,
        )

    # --------------------------------------------------------
    # Bybit fallback
    # --------------------------------------------------------

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
            "%s %s Bybit candles failed: %s",
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

        raise MissingConfigurationError(
            "PARIBU_ORDERBOOK_URL is not configured. "
            "Verify the current Paribu API documentation "
            "and set the exact endpoint as a GitHub Secret."
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
            f"Invalid Paribu order book "
            f"response for {normalized}."
        )

    # Accept direct or wrapped order-book structures.
    candidates = [
        payload,
        payload.get("data"),
        payload.get("result"),
        payload.get("orderBook"),
        payload.get("orderbook"),
    ]

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
                    candidate.get(
                        "ts"
                    ),
                ),
            }

    raise SchemaError(
        f"{normalized}: "
        "Paribu response did not contain "
        "asks/bids."
    )


def best_bid_ask(
    order_book: dict[str, Any],
) -> tuple[
    Decimal,
    Decimal,
]:

    bids = order_book.get(
        "bids",
        []
    )

    asks = order_book.get(
        "asks",
        []
    )

    if not bids or not asks:

        raise OrderBookUnavailableError(
            "Order book is empty."
        )

    parsed_bids = []
    parsed_asks = []

    for row in bids:

        if (
            isinstance(row, (list, tuple))
            and len(row) >= 2
        ):

            price = to_decimal(
                row[0]
            )

            qty = to_decimal(
                row[1]
            )

            if (
                price is not None
                and qty is not None
                and price > 0
                and qty > 0
            ):

                parsed_bids.append(
                    (price, qty)
                )

    for row in asks:

        if (
            isinstance(row, (list, tuple))
            and len(row) >= 2
        ):

            price = to_decimal(
                row[0]
            )

            qty = to_decimal(
                row[1]
            )

            if (
                price is not None
                and qty is not None
                and price > 0
                and qty > 0
            ):

                parsed_asks.append(
                    (price, qty)
                )

    if (
        not parsed_bids
        or not parsed_asks
    ):

        raise OrderBookUnavailableError(
            "No valid bid/ask levels."
        )

    best_bid = max(
        parsed_bids,
        key=lambda x: x[0],
    )[0]

    best_ask = min(
        parsed_asks,
        key=lambda x: x[0],
    )[0]

    if best_ask <= best_bid:

        raise OrderBookUnavailableError(
            "Invalid order book: "
            "best ask <= best bid."
        )

    return (
        best_bid,
        best_ask,
    )


def order_book_spread_pct(
    order_book: dict[str, Any],
) -> Decimal:

    best_bid, best_ask = (
        best_bid_ask(
            order_book
        )
    )

    return (
        (best_ask - best_bid)
        / best_bid
        * Decimal("100")
    )


# ============================================================
# DEPTH / VWAP
# ============================================================

def buy_vwap(
    order_book: dict[str, Any],
    trade_size_tl: Decimal,
) -> tuple[
    Decimal,
    Decimal,
]:

    if trade_size_tl <= 0:

        raise ValueError(
            "trade_size_tl must be > 0."
        )

    asks = []

    for row in order_book.get(
        "asks",
        [],
    ):

        if (
            not isinstance(row, (list, tuple))
            or len(row) < 2
        ):
            continue

        price = to_decimal(
            row[0]
        )

        quantity = to_decimal(
            row[1]
        )

        if (
            price is not None
            and quantity is not None
            and price > 0
            and quantity > 0
        ):

            asks.append(
                (
                    price,
                    quantity,
                )
            )

    asks.sort(
        key=lambda x: x[0]
    )

    remaining = trade_size_tl
    spent = Decimal("0")
    quantity_bought = Decimal("0")

    for price, available_qty in asks:

        level_value = (
            price
            * available_qty
        )

        used_value = min(
            remaining,
            level_value,
        )

        used_qty = (
            used_value / price
        )

        spent += used_value
        quantity_bought += used_qty
        remaining -= used_value

        if remaining <= 0:
            break

    if remaining > 0:

        raise OrderBookUnavailableError(
            "Insufficient Paribu ask-side "
            "liquidity for requested trade size."
        )

    if quantity_bought <= 0:

        raise OrderBookUnavailableError(
            "Could not calculate entry VWAP."
        )

    return (
        spent / quantity_bought,
        quantity_bought,
    )


def sell_vwap_to_target(
    order_book: dict[str, Any],
    target_price: Decimal,
    quantity: Decimal,
) -> Optional[Decimal]:

    if (
        target_price <= 0
        or quantity <= 0
    ):
        return None

    bids = []

    for row in order_book.get(
        "bids",
        [],
    ):

        if (
            not isinstance(row, (list, tuple))
            or len(row) < 2
        ):
            continue

        price = to_decimal(
            row[0]
        )

        qty = to_decimal(
            row[1]
        )

        if (
            price is not None
            and qty is not None
            and price > 0
            and qty > 0
        ):

            bids.append(
                (
                    price,
                    qty,
                )
            )

    bids.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    remaining = quantity
    proceeds = Decimal("0")
    sold = Decimal("0")

    for price, available_qty in bids:

        if price < target_price:
            continue

        used_qty = min(
            remaining,
            available_qty,
        )

        proceeds += (
            used_qty
            * price
        )

        sold += used_qty
        remaining -= used_qty

        if remaining <= 0:
            break

    if sold <= 0:
        return None

    return (
        proceeds / sold
    )


# ============================================================
# COMPLETE EXECUTION SNAPSHOT
# ============================================================

def get_execution_snapshot(
    symbol: str,
    trade_size_tl: Decimal,
    depth: int = DEFAULT_ORDERBOOK_DEPTH,
) -> dict[str, Any]:

    normalized = normalize_symbol(
        symbol
    )

    snapshot = get_market_snapshot()

    ticker = snapshot.get(
        normalized
    )

    if ticker is None:

        raise MarketDataError(
            f"{normalized}: "
            "not found in Paribu ticker."
        )

    order_book = fetch_order_book(
        normalized,
        depth,
    )

    best_bid, best_ask = (
        best_bid_ask(
            order_book
        )
    )

    spread_pct = (
        (best_ask - best_bid)
        / best_bid
        * Decimal("100")
    )

    entry_vwap, quantity = (
        buy_vwap(
            order_book,
            trade_size_tl,
        )
    )

    return {
        "symbol": normalized,
        "paribu_last": ticker.last,
        "paribu_bid": best_bid,
        "paribu_ask": best_ask,
        "spread_pct": spread_pct,
        "entry_vwap": entry_vwap,
        "quantity": quantity,
        "order_book": order_book,
        "source": "Paribu",
    }


# ============================================================
# HEALTH / DIAGNOSTICS
# ============================================================

def health_check(
    sample_symbol: str = "BTC_TL",
) -> dict[str, Any]:

    result = {
        "timestamp": int(
            time.time()
        ),
        "paribu_ticker": False,
        "binance_symbol_registry": False,
        "bybit_symbol_registry": False,
        "orderbook_configured": bool(
            PARIBU_ORDERBOOK_URL
        ),
        "markets": 0,
    }

    snapshot = get_market_snapshot()

    result["paribu_ticker"] = True
    result["markets"] = len(
        snapshot
    )

    try:
        get_binance_spot_symbols()
        result[
            "binance_symbol_registry"
        ] = True
    except Exception as exc:
        LOGGER.warning(
            "Binance symbol registry failed: %s",
            exc,
        )

    try:
        get_bybit_spot_symbols()
        result[
            "bybit_symbol_registry"
        ] = True
    except Exception as exc:
        LOGGER.warning(
            "Bybit symbol registry failed: %s",
            exc,
        )

    return result


def candle_health_check(
    symbol: str = "BTC_TL",
    resolution: str = "15m",
) -> dict[str, Any]:

    df = fetch_candles(
        symbol,
        resolution,
        DEFAULT_CANDLE_LIMIT,
    )

    return {
        "symbol": symbol,
        "resolution": resolution,
        "candles": len(df),
        "source": df.attrs.get(
            "source"
        ),
        "provider_symbol": df.attrs.get(
            "provider_symbol"
        ),
        "last_timestamp": int(
            df["timestamp"].iloc[-1]
        ),
    }


if __name__ == "__main__":

    print(
        "=== MARKET DATA HEALTH CHECK ==="
    )

    try:

        print(
            health_check()
        )

    except Exception as exc:

        print(
            "HEALTH CHECK ERROR:",
            exc,
        )
