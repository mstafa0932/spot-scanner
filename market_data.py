from __future__ import annotations

"""Paribu-only market data layer.

All trading decisions are based on Paribu data only:
- ticker: https://api.paribu.com/market/ticker
- order book: https://api.paribu.com/orderbook
- candles: https://web.paribu.com/chart/history

No Binance, KuCoin, Bybit, or other exchange is used by this module.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import logging
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger("spot_scanner.market_data")

PARIBU_TICKER_URL = "https://api.paribu.com/market/ticker"
PARIBU_ORDERBOOK_URL = "https://api.paribu.com/orderbook"
PARIBU_CHART_HISTORY_URL = "https://web.paribu.com/chart/history"

REQUEST_TIMEOUT = 12
MIN_VALID_CANDLES = 205
DEFAULT_CANDLE_LIMIT = 250

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

INTERVALS = {
    "15m": ("900", 900),
    "1h": ("3600", 3600),
    "4h": ("14400", 14400),
    "1d": ("1D", 86400),
}


class ParibuDataError(Exception):
    """Base exception for Paribu market-data failures."""


class ParibuHTTPError(ParibuDataError):
    """HTTP/network/data endpoint failure."""


class ParibuSchemaError(ParibuDataError):
    """Unexpected Paribu JSON structure."""


class CandleUnavailableError(ParibuDataError):
    """Not enough valid Paribu candles were returned."""


class OrderBookUnavailableError(ParibuDataError):
    """Paribu order book was unavailable or invalid."""


def to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
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


def is_tl_pair(symbol: str) -> bool:
    normalized = normalize_symbol(symbol)
    return normalized.endswith("_TL") or normalized.endswith("_TRY")


def _first(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def _make_session() -> requests.Session:
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
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        }
    )
    return session


SESSION = _make_session()


def get_json(url: str, params: Optional[dict[str, Any]] = None) -> Any:
    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ParibuHTTPError(f"GET failed: {url}: {exc}") from exc

    if response.status_code != 200:
        raise ParibuHTTPError(
            f"HTTP {response.status_code} from {url}: {response.text[:300]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ParibuSchemaError(f"Invalid JSON from {url}") from exc


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
        return (self.ask - self.bid) / self.bid * Decimal("100")


@dataclass(frozen=True)
class OrderBookSnapshot:
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    best_bid: Decimal
    best_ask: Decimal
    spread_percent: Decimal
    bid_notional: Decimal
    ask_notional: Decimal
    imbalance_ratio: Decimal
    timestamp: Optional[str] = None


# ------------------------- ticker -------------------------


def _extract_ticker_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if isinstance(payload, dict):
        for key in ("data", "result", "tickers", "markets"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                rows = []
                for key_name, item in value.items():
                    if isinstance(item, dict):
                        row = dict(item)
                        row.setdefault("market", key_name)
                        rows.append(row)
                if rows:
                    return rows

        rows = []
        for key_name, item in payload.items():
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("market", key_name)
                rows.append(row)
        if rows:
            return rows

    raise ParibuSchemaError("Could not find Paribu ticker records")


def fetch_tickers() -> list[Ticker]:
    payload = get_json(PARIBU_TICKER_URL)
    records = _extract_ticker_records(payload)
    result: list[Ticker] = []

    for raw in records:
        symbol = normalize_symbol(
            _first(raw, ("market", "symbol", "pair", "market_symbol", "instrument"))
            or ""
        )
        if not is_tl_pair(symbol):
            continue

        last = to_decimal(_first(raw, ("last", "lastPrice", "last_price", "price", "close")))
        if last is None or last <= 0:
            continue

        # The current Paribu public ticker model exposes 24h fields, while
        # bid/ask are obtained from the official order-book endpoint.
        volume = to_decimal(_first(raw, ("volume", "vol", "baseVolume", "base_volume")))
        quote_volume = to_decimal(
            _first(raw, ("pair_volume", "quoteVolume", "quote_volume", "volumeQuote", "turnover", "totalVolume"))
        )
        if quote_volume is None and volume is not None and volume > 0:
            quote_volume = volume * last

        change = to_decimal(
            _first(raw, ("percentage", "changePercent", "change_percent", "percentChange", "percent_change", "change"))
        )

        bid = to_decimal(_first(raw, ("bid", "bestBid", "best_bid")))
        ask = to_decimal(_first(raw, ("ask", "bestAsk", "best_ask")))

        result.append(
            Ticker(
                symbol=symbol,
                last=last,
                bid=bid,
                ask=ask,
                volume=volume,
                quote_volume=quote_volume,
                change_percent=change,
            )
        )

    if not result:
        raise ParibuSchemaError("No usable Paribu TL ticker markets found")

    result.sort(key=lambda t: t.quote_volume or Decimal("0"), reverse=True)
    return result


def get_market_snapshot() -> dict[str, Ticker]:
    return {ticker.symbol: ticker for ticker in fetch_tickers()}


def get_single_ticker(symbol: str) -> Ticker:
    normalized = normalize_symbol(symbol)
    snapshot = get_market_snapshot()
    ticker = snapshot.get(normalized)
    if ticker is None:
        raise ParibuSchemaError(f"Paribu ticker not found: {normalized}")
    return ticker


# ------------------------- order book -------------------------


def _unwrap_orderbook(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("payload"), dict):
            return payload["payload"]
        if isinstance(payload.get("data"), dict):
            return payload["data"]
        if isinstance(payload.get("result"), dict):
            return payload["result"]
        if "bids" in payload or "asks" in payload:
            return payload
    raise ParibuSchemaError("Unexpected Paribu order-book response")


def _parse_book_side(raw_side: Any) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(raw_side, list):
        return tuple()

    rows: list[tuple[Decimal, Decimal]] = []
    for item in raw_side:
        price: Optional[Decimal] = None
        amount: Optional[Decimal] = None

        if isinstance(item, (list, tuple)) and len(item) >= 2:
            price = to_decimal(item[0])
            amount = to_decimal(item[1])
        elif isinstance(item, dict):
            price = to_decimal(_first(item, ("price", "p")))
            amount = to_decimal(_first(item, ("amount", "a", "quantity", "q")))

        if price is None or amount is None or price <= 0 or amount <= 0:
            continue
        rows.append((price, amount))

    return tuple(rows)


def get_order_book(symbol: str, depth: int = 20) -> OrderBookSnapshot:
    normalized = normalize_symbol(symbol).lower()
    depth = max(1, min(int(depth), 20))
    payload = get_json(
        PARIBU_ORDERBOOK_URL,
        params={"market": normalized, "depth": depth},
    )
    book = _unwrap_orderbook(payload)

    bids = _parse_book_side(book.get("bids"))
    asks = _parse_book_side(book.get("asks"))

    if not bids or not asks:
        raise OrderBookUnavailableError(f"Empty Paribu order book for {normalized}")

    bids = tuple(sorted(bids, key=lambda x: x[0], reverse=True)[:depth])
    asks = tuple(sorted(asks, key=lambda x: x[0])[:depth])

    best_bid = bids[0][0]
    best_ask = asks[0][0]
    if best_ask <= 0 or best_bid <= 0 or best_ask < best_bid:
        raise OrderBookUnavailableError(f"Invalid bid/ask for {normalized}")

    spread = (best_ask - best_bid) / best_bid * Decimal("100")
    bid_notional = sum((price * amount for price, amount in bids), Decimal("0"))
    ask_notional = sum((price * amount for price, amount in asks), Decimal("0"))
    imbalance = (
        bid_notional / ask_notional
        if ask_notional > 0
        else Decimal("0")
    )

    return OrderBookSnapshot(
        symbol=normalize_symbol(symbol),
        bids=bids,
        asks=asks,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_percent=spread,
        bid_notional=bid_notional,
        ask_notional=ask_notional,
        imbalance_ratio=imbalance,
        timestamp=str(book.get("timestamp")) if book.get("timestamp") is not None else None,
    )


# ------------------------- candles -------------------------


def _interval_config(resolution: str) -> tuple[str, int]:
    normalized = str(resolution).strip().lower()
    if normalized not in INTERVALS:
        raise ValueError(f"Unsupported Paribu interval: {resolution}")
    return INTERVALS[normalized]


def _extract_chart_arrays(payload: Any) -> tuple[list[Any], list[Any], list[Any], list[Any], list[Any], list[Any]]:
    if not isinstance(payload, dict):
        raise ParibuSchemaError("Paribu chart response is not an object")

    # Official Paribu chart history model uses o/h/l/c/v/t.
    source = payload
    for key in ("data", "result", "payload"):
        if isinstance(payload.get(key), dict) and any(k in payload[key] for k in ("o", "h", "l", "c", "v", "t")):
            source = payload[key]
            break

    opens = source.get("o", [])
    highs = source.get("h", [])
    lows = source.get("l", [])
    closes = source.get("c", [])
    volumes = source.get("v", [])
    timestamps = source.get("t", [])

    if not all(isinstance(x, list) for x in (opens, highs, lows, closes, volumes, timestamps)):
        raise ParibuSchemaError("Paribu chart response is missing candle arrays")

    return opens, highs, lows, closes, volumes, timestamps


def _drop_open_candle(df: pd.DataFrame, interval_seconds: int) -> pd.DataFrame:
    if df.empty:
        return df

    now = int(time.time())
    last_ts = int(df["timestamp"].iloc[-1])
    # Paribu timestamps in the official model are epoch seconds.
    if last_ts + interval_seconds > now + 5:
        return df.iloc[:-1].copy()
    return df


def validate_candles(df: pd.DataFrame, resolution: str) -> pd.DataFrame:
    required = ("timestamp", "open", "high", "low", "close", "volume")
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ParibuSchemaError("Missing candle columns: " + ", ".join(missing))

    x = df.copy()
    for column in required:
        x[column] = pd.to_numeric(x[column], errors="coerce")

    x = x.dropna(subset=required).copy()
    x = x[
        (x["timestamp"] > 0)
        & (x["open"] > 0)
        & (x["high"] > 0)
        & (x["low"] > 0)
        & (x["close"] > 0)
        & (x["volume"] >= 0)
    ].copy()
    x = x[
        (x["high"] >= x[["open", "close"]].max(axis=1))
        & (x["low"] <= x[["open", "close"]].min(axis=1))
        & (x["high"] >= x["low"])
    ].copy()
    x = x.drop_duplicates(subset=["timestamp"], keep="last")
    x = x.sort_values("timestamp").reset_index(drop=True)

    _, interval_seconds = _interval_config(resolution)
    x = _drop_open_candle(x, interval_seconds)

    if len(x) < MIN_VALID_CANDLES:
        raise CandleUnavailableError(
            f"Only {len(x)} closed Paribu candles for {resolution}; "
            f"{MIN_VALID_CANDLES} required"
        )

    x.attrs.update(
        source="PARIBU",
        provider="Paribu",
        resolution=str(resolution).lower(),
    )
    return x


def fetch_candles(
    symbol: str,
    resolution: str = "15m",
    limit: int = DEFAULT_CANDLE_LIMIT,
) -> pd.DataFrame:
    normalized = normalize_symbol(symbol).lower()
    period, _interval_seconds = _interval_config(resolution)
    requested_limit = max(MIN_VALID_CANDLES, min(int(limit), 500))

    # IMPORTANT: current Paribu chart/history rejects intraday values such as
    # period=15/60/240 with: "invalid period". Current-compatible intraday
    # requests use TradingView-style numeric resolution in seconds plus `to`.
    # We intentionally NEVER send period=15, period=60, or period=240.
    interval_seconds = _interval_seconds
    end_ms = int(time.time() * 1000)
    end_s = int(time.time())

    variants: list[dict[str, Any]] = []
    if resolution.lower() == "1d":
        variants.append({
            "symbol": normalized,
            "period": "1D",
            "type": "basic",
        })
    else:
        variants.extend((
            {
                "symbol": normalized,
                "resolution": str(interval_seconds),
                "to": end_ms,
            },
            {
                "symbol": normalized,
                "resolution": str(interval_seconds),
                "to": end_s,
            },
        ))

    last_error: Optional[Exception] = None
    payload = None
    for request_params in variants:
        try:
            payload = get_json(PARIBU_CHART_HISTORY_URL, params=request_params)
            break
        except ParibuDataError as exc:
            last_error = exc
            LOGGER.warning(
                "Paribu chart request failed for %s %s with params=%s: %s",
                normalized, resolution, request_params, exc,
            )

    if payload is None:
        raise CandleUnavailableError(
            f"Paribu chart unavailable for {normalized} {resolution}: {last_error}"
        )
    opens, highs, lows, closes, volumes, timestamps = _extract_chart_arrays(payload)

    length = min(
        len(opens),
        len(highs),
        len(lows),
        len(closes),
        len(volumes),
        len(timestamps),
    )

    if length <= 0:
        raise CandleUnavailableError(f"Paribu returned no candles for {normalized}")

    rows: list[dict[str, Any]] = []
    for i in range(length):
        timestamp = to_decimal(timestamps[i])
        opening = to_decimal(opens[i])
        high = to_decimal(highs[i])
        low = to_decimal(lows[i])
        close = to_decimal(closes[i])
        volume = to_decimal(volumes[i])
        if None in (timestamp, opening, high, low, close, volume):
            continue
        rows.append(
            {
                "timestamp": int(timestamp),
                "open": float(opening),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
            }
        )

    df = validate_candles(pd.DataFrame(rows), resolution)
    if len(df) > requested_limit:
        df = df.tail(requested_limit).reset_index(drop=True)
        df.attrs.update(source="PARIBU", provider="Paribu", resolution=resolution.lower())

    if len(df) < MIN_VALID_CANDLES:
        raise CandleUnavailableError(
            f"Paribu returned only {len(df)} usable closed candles for {normalized}"
        )

    return df


def candle_health_check(
    symbol: str = "BTC_TL",
    resolution: str = "15m",
    limit: int = DEFAULT_CANDLE_LIMIT,
) -> dict[str, Any]:
    df = fetch_candles(symbol, resolution, limit)
    return {
        "symbol": normalize_symbol(symbol),
        "resolution": resolution,
        "source": df.attrs.get("source"),
        "provider": df.attrs.get("provider"),
        "candles": len(df),
        "latest_closed_timestamp": int(df["timestamp"].iloc[-1]),
    }


def health_check() -> dict[str, Any]:
    started = time.time()
    snapshot = get_market_snapshot()
    elapsed = round(time.time() - started, 2)
    return {
        "markets": len(snapshot),
        "seconds": elapsed,
        "ticker_source": "PARIBU",
        "candle_source": "PARIBU",
        "orderbook_source": "PARIBU",
        "min_valid_candles": MIN_VALID_CANDLES,
    }
