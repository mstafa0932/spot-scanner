from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# Paribu Public Market Data
# Spot Scanner project
#
# IMPORTANT:
# - Public market data only
# - No API key
# - No trading
# - No withdrawals
# - Prices use Decimal, not float
# ============================================================

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"

REQUEST_TIMEOUT = 15

USER_AGENT = (
    "spot-scanner/1.0 "
    "(public-market-data; no-trading; contact: repository-owner)"
)


# ------------------------------------------------------------
# Data model
# ------------------------------------------------------------

@dataclass(frozen=True)
class Ticker:
    symbol: str

    last: Decimal

    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None

    open_24h: Optional[Decimal] = None
    high_24h: Optional[Decimal] = None
    low_24h: Optional[Decimal] = None

    volume: Optional[Decimal] = None
    quote_volume: Optional[Decimal] = None

    change_percent: Optional[Decimal] = None

    timestamp: Optional[int] = None

    @property
    def spread(self) -> Optional[Decimal]:
        """Absolute bid/ask spread."""

        if self.bid is None or self.ask is None:
            return None

        if self.bid <= 0 or self.ask <= 0:
            return None

        return self.ask - self.bid

    @property
    def spread_percent(self) -> Optional[Decimal]:
        """Spread as a percentage of bid."""

        if self.bid is None or self.ask is None:
            return None

        if self.bid <= 0 or self.ask <= 0:
            return None

        return ((self.ask - self.bid) / self.bid) * Decimal("100")


# ------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------

class ParibuDataError(Exception):
    """Base exception for Paribu market-data problems."""


class ParibuHTTPError(ParibuDataError):
    """HTTP/API failure."""


class ParibuJSONError(ParibuDataError):
    """Invalid JSON / unexpected response."""


class ParibuSchemaError(ParibuDataError):
    """Unexpected data structure."""


# ------------------------------------------------------------
# HTTP client
# ------------------------------------------------------------

def _create_session() -> requests.Session:
    """
    Create a requests session with conservative retries.

    Retries are used only for transient connection/server errors.
    """

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
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
    )

    return session


_SESSION = _create_session()


# ------------------------------------------------------------
# Conversion helpers
# ------------------------------------------------------------

def _decimal(
    value: Any,
    *,
    field_name: str,
    allow_none: bool = True,
) -> Optional[Decimal]:
    """
    Convert API numeric values to Decimal safely.
    """

    if value is None or value == "":
        if allow_none:
            return None

        raise ParibuSchemaError(
            f"Required numeric field '{field_name}' is missing."
        )

    if isinstance(value, bool):
        raise ParibuSchemaError(
            f"Invalid boolean value for numeric field '{field_name}'."
        )

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ParibuSchemaError(
            f"Invalid numeric value for '{field_name}': {value!r}"
        ) from exc

    if not number.is_finite():
        raise ParibuSchemaError(
            f"Non-finite numeric value for '{field_name}': {value!r}"
        )

    return number


def _integer(value: Any, field_name: str) -> Optional[int]:
    if value is None or value == "":
        return None

    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ParibuSchemaError(
            f"Invalid integer value for '{field_name}': {value!r}"
        ) from exc


def _first_value(
    data: dict[str, Any],
    names: tuple[str, ...],
) -> Any:
    """
    Return the first existing field from a list of aliases.
    """

    for name in names:
        if name in data:
            return data[name]

    return None


# ------------------------------------------------------------
# Response extraction
# ------------------------------------------------------------

def _extract_ticker_map(payload: Any) -> dict[str, dict[str, Any]]:
    """
    Normalize the most common ticker response shapes into:

        {
            "BTC_TL": {...},
            "ETH_TL": {...},
            ...
        }

    The current public Paribu /ticker endpoint has historically
    returned a top-level mapping keyed by market symbol.
    """

    if not isinstance(payload, dict):
        raise ParibuSchemaError(
            "Paribu ticker response is not a JSON object."
        )

    # Direct top-level symbol -> ticker mapping.
    direct_records: dict[str, dict[str, Any]] = {}

    for key, value in payload.items():
        if isinstance(value, dict):
            direct_records[str(key)] = value

    if direct_records:
        return direct_records

    # Be defensive in case the endpoint wraps the records.
    for container_key in ("data", "result", "tickers", "markets"):
        container = payload.get(container_key)

        if isinstance(container, list):
            records: dict[str, dict[str, Any]] = {}

            for item in container:
                if not isinstance(item, dict):
                    continue

                symbol = _first_value(
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

        elif isinstance(container, dict):
            records = {
                str(k): v
                for k, v in container.items()
                if isinstance(v, dict)
            }

            if records:
                return records

    raise ParibuSchemaError(
        "Could not find ticker records in Paribu response."
    )


# ------------------------------------------------------------
# Symbol helpers
# ------------------------------------------------------------

def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def is_tl_pair(symbol: str) -> bool:
    """
    Accept Paribu's common TL notation.

    Examples:
        BTC_TL
        ETH_TL
        BTC/TRY
        BTC_TRY
    """

    normalized = (
        symbol.strip()
        .upper()
        .replace("-", "_")
        .replace("/", "_")
    )

    return (
        normalized.endswith("_TL")
        or normalized.endswith("_TRY")
    )


def base_asset(symbol: str) -> str:
    """
    Extract base asset from symbols such as BTC_TL.
    """

    normalized = (
        symbol.strip()
        .upper()
        .replace("-", "_")
        .replace("/", "_")
    )

    if "_" in normalized:
        return normalized.rsplit("_", 1)[0]

    return normalized


# ------------------------------------------------------------
# Ticker normalization
# ------------------------------------------------------------

def _parse_ticker(
    symbol: str,
    raw: dict[str, Any],
) -> Optional[Ticker]:
    """
    Convert one raw ticker object into our internal Ticker model.
    """

    last_raw = _first_value(
        raw,
        (
            "last",
            "lastPrice",
            "last_price",
            "price",
            "close",
        ),
    )

    last = _decimal(
        last_raw,
        field_name=f"{symbol}.last",
        allow_none=False,
    )

    if last <= 0:
        return None

    bid = _decimal(
        _first_value(raw, ("bid", "bestBid", "best_bid")),
        field_name=f"{symbol}.bid",
    )

    ask = _decimal(
        _first_value(raw, ("ask", "bestAsk", "best_ask")),
        field_name=f"{symbol}.ask",
    )

    open_24h = _decimal(
        _first_value(
            raw,
            (
                "open",
                "open24h",
                "open_24h",
                "openPrice",
            ),
        ),
        field_name=f"{symbol}.open_24h",
    )

    high_24h = _decimal(
        _first_value(
            raw,
            (
                "high",
                "high24h",
                "high_24h",
                "highPrice",
            ),
        ),
        field_name=f"{symbol}.high_24h",
    )

    low_24h = _decimal(
        _first_value(
            raw,
            (
                "low",
                "low24h",
                "low_24h",
                "lowPrice",
            ),
        ),
        field_name=f"{symbol}.low_24h",
    )

    volume = _decimal(
        _first_value(
            raw,
            (
                "volume",
                "vol",
                "baseVolume",
                "base_volume",
            ),
        ),
        field_name=f"{symbol}.volume",
    )

    quote_volume = _decimal(
        _first_value(
            raw,
            (
                "quoteVolume",
                "quote_volume",
                "volumeQuote",
                "amount",
                "turnover",
            ),
        ),
        field_name=f"{symbol}.quote_volume",
    )

    change_percent = _decimal(
        _first_value(
            raw,
            (
                "change",
                "changePercent",
                "change_percent",
                "percentage",
                "percentChange",
                "percent_change",
            ),
        ),
        field_name=f"{symbol}.change_percent",
    )

    timestamp = _integer(
        _first_value(
            raw,
            (
                "timestamp",
                "time",
                "ts",
                "updatedAt",
                "updated_at",
            ),
        ),
        field_name=f"{symbol}.timestamp",
    )

    return Ticker(
        symbol=_normalize_symbol(symbol),
        last=last,
        bid=bid,
        ask=ask,
        open_24h=open_24h,
        high_24h=high_24h,
        low_24h=low_24h,
        volume=volume,
        quote_volume=quote_volume,
        change_percent=change_percent,
        timestamp=timestamp,
    )


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def fetch_raw_tickers() -> Any:
    """
    Fetch the raw public ticker JSON from Paribu.

    No API key or account permission is used.
    """

    try:
        response = _SESSION.get(
            PARIBU_TICKER_URL,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ParibuHTTPError(
            f"Could not connect to Paribu: {exc}"
        ) from exc

    if response.status_code != 200:
        raise ParibuHTTPError(
            f"Paribu returned HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ParibuJSONError(
            "Paribu returned a non-JSON response."
        ) from exc


def fetch_tickers(
    *,
    tl_only: bool = True,
) -> list[Ticker]:
    """
    Fetch and normalize all available tickers.

    By default, only TL/TRY pairs are returned because our
    trading system is designed around Paribu TL markets.
    """

    payload = fetch_raw_tickers()

    ticker_map = _extract_ticker_map(payload)

    result: list[Ticker] = []

    for raw_symbol, raw_ticker in ticker_map.items():

        symbol = _normalize_symbol(raw_symbol)

        if tl_only and not is_tl_pair(symbol):
            continue

        try:
            ticker = _parse_ticker(symbol, raw_ticker)
        except ParibuSchemaError:
            # One malformed market must not destroy the entire scan.
            continue

        if ticker is not None:
            result.append(ticker)

    if not result:
        raise ParibuSchemaError(
            "Paribu returned no usable ticker records."
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


def fetch_ticker(symbol: str) -> Ticker:
    """
    Fetch one ticker from the complete public ticker snapshot.
    """

    target = _normalize_symbol(symbol)

    all_tickers = fetch_tickers(tl_only=False)

    for ticker in all_tickers:
        if ticker.symbol == target:
            return ticker

    raise ParibuDataError(
        f"Market '{target}' was not found in Paribu ticker data."
    )


# ------------------------------------------------------------
# Price precision
# ------------------------------------------------------------

def decimal_places(value: Decimal) -> int:
    """
    Return the number of decimal places actually represented
    by a Decimal value.

    Example:
        Decimal("0.00072") -> 5
        Decimal("96.12")   -> 2
    """

    exponent = value.as_tuple().exponent

    if exponent >= 0:
        return 0

    return -exponent


def price_precision(ticker: Ticker) -> int:
    """
    Infer observed price precision from the live ticker.

    IMPORTANT:
    This is observed precision, not the exchange's official
    order tick-size rule. We will add official market rules
    later before any execution-related functionality.
    """

    return decimal_places(ticker.last)


# ------------------------------------------------------------
# Snapshot helper
# ------------------------------------------------------------

def get_market_snapshot() -> dict[str, Ticker]:
    """
    Return a clean dictionary:

        {
            "BTC_TL": Ticker(...),
            "ETH_TL": Ticker(...),
            ...
        }
    """

    tickers = fetch_tickers(tl_only=True)

    return {
        ticker.symbol: ticker
        for ticker in tickers
    }


# ------------------------------------------------------------
# Diagnostic test
# ------------------------------------------------------------

def run_connection_test() -> None:
    """
    Simple diagnostic test for GitHub Actions.
    """

    started = time.time()

    tickers = fetch_tickers(tl_only=True)

    elapsed = time.time() - started

    print("=" * 70)
    print("PARIBU PUBLIC MARKET DATA TEST")
    print("=" * 70)

    print("Status: OK")
    print(f"Endpoint: {PARIBU_TICKER_URL}")
    print(f"TL markets loaded: {len(tickers)}")
    print(f"Elapsed: {elapsed:.2f}s")

    print()
    print("Top markets by available quote volume:")

    for ticker in tickers[:10]:
        volume_text = (
            str(ticker.quote_volume)
            if ticker.quote_volume is not None
            else "N/A"
        )

        print(
            f"{ticker.symbol:15} "
            f"last={ticker.last} "
            f"volume={volume_text}"
        )

    print("=" * 70)


if __name__ == "__main__":
    try:
        run_connection_test()

    except ParibuDataError as exc:
        print("ERROR:", exc)
        raise SystemExit(1)

    except Exception as exc:
        print("UNEXPECTED ERROR:", exc)
        raise SystemExit(1)
