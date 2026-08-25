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
# ============================================================

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"
REQUEST_TIMEOUT = 15

# Real Chrome Browser User-Agent to prevent Cloudflare/Paribu 403 blocks
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
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
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask <= 0:
            return None
        return self.ask - self.bid

    @property
    def spread_percent(self) -> Optional[Decimal]:
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask <= 0:
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
    if value is None or value == "":
        if allow_none:
            return None
        raise ParibuSchemaError(f"Required numeric field '{field_name}' is missing.")

    if isinstance(value, bool):
        raise ParibuSchemaError(f"Invalid boolean value for numeric field '{field_name}'.")

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ParibuSchemaError(f"Invalid numeric value for '{field_name}': {value!r}") from exc

    if not number.is_finite():
        raise ParibuSchemaError(f"Non-finite numeric value for '{field_name}': {value!r}")

    return number


def _integer(value: Any, field_name: str) -> Optional[int]:
    if value is None or value == "":
        return None

    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ParibuSchemaError(f"Invalid integer value for '{field_name}': {value!r}") from exc


def _first_value(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def _extract_ticker_map(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ParibuSchemaError("Paribu ticker response is not a JSON object.")

    direct_records: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            direct_records[str(key)] = value

    if direct_records:
        return direct_records

    for container_key in ("data", "result", "tickers", "markets"):
        container = payload.get(container_key)
        if isinstance(container, list):
            records: dict[str, dict[str, Any]] = {}
            for item in container:
                if isinstance(item, dict):
                    symbol = _first_value(
                        item,
                        ("symbol", "pair", "market", "market_symbol", "instrument"),
                    )
                    if symbol:
                        records[str(symbol)] = item
            if records:
                return records
        elif isinstance(container, dict):
            records = {
                str(k): v for k, v in container.items() if isinstance(v, dict)
            }
            if records:
                return records

    raise ParibuSchemaError("Could not find ticker records in Paribu response.")


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def is_tl_pair(symbol: str) -> bool:
    normalized = symbol.strip().upper().replace("-", "_").replace("/", "_")
    return normalized.endswith("_TL") or normalized.endswith("_TRY")


def base_asset(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("-", "_").replace("/", "_")
    if "_" in normalized:
        return normalized.rsplit("_", 1)[0]
    return normalized


def _parse_ticker(symbol: str, raw: dict[str, Any]) -> Optional[Ticker]:
    last_raw = _first_value(raw, ("last", "lastPrice", "last_price", "price", "close"))
    last = _decimal(last_raw, field_name=f"{symbol}.last", allow_none=False)
    if last <= 0:
        return None

    return Ticker(
        symbol=_normalize_symbol(symbol),
        last=last,
        bid=_decimal(_first_value(raw, ("bid", "bestBid", "best_bid")), field_name=f"{symbol}.bid"),
        ask=_decimal(_first_value(raw, ("ask", "bestAsk", "best_ask")), field_name=f"{symbol}.ask"),
        open_24h=_decimal(_first_value(raw, ("open", "open24h", "open_24h", "openPrice")), field_name=f"{symbol}.open_24h"),
        high_24h=_decimal(_first_value(raw, ("high", "high24h", "high_24h", "highPrice")), field_name=f"{symbol}.high_24h"),
        low_24h=_decimal(_first_value(raw, ("low", "low24h", "low_24h", "lowPrice")), field_name=f"{symbol}.low_24h"),
        volume=_decimal(_first_value(raw, ("volume", "vol", "baseVolume", "base_volume")), field_name=f"{symbol}.volume"),
        quote_volume=_decimal(_first_value(raw, ("quoteVolume", "quote_volume", "volumeQuote", "amount", "turnover")), field_name=f"{symbol}.quote_volume"),
        change_percent=_decimal(_first_value(raw, ("change", "changePercent", "change_percent", "percentage", "percentChange", "percent_change")), field_name=f"{symbol}.change_percent"),
        timestamp=_integer(_first_value(raw, ("timestamp", "time", "ts", "updatedAt", "updated_at")), field_name=f"{symbol}.timestamp"),
    )


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def fetch_raw_tickers() -> Any:
    try:
        response = _SESSION.get(PARIBU_TICKER_URL, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise ParibuHTTPError(f"Could not connect to Paribu: {exc}") from exc

    if response.status_code != 200:
        raise ParibuHTTPError(
            f"Paribu returned HTTP {response.status_code}: {response.text[:300]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ParibuJSONError("Paribu returned a non-JSON response.") from exc


def fetch_tickers(*, tl_only: bool = True) -> list[Ticker]:
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
            continue
        if ticker is not None:
            result.append(ticker)

    if not result:
        raise ParibuSchemaError("Paribu returned no usable ticker records.")

    result.sort(
        key=lambda item: (
            item.quote_volume if item.quote_volume is not None else Decimal("0")
        ),
        reverse=True,
    )
    return result


def fetch_ticker(symbol: str) -> Ticker:
    target = _normalize_symbol(symbol)
    all_tickers = fetch_tickers(tl_only=False)
    for ticker in all_tickers:
        if ticker.symbol == target:
            return ticker
    raise ParibuDataError(f"Market '{target}' was not found in Paribu ticker data.")


def decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    if exponent >= 0:
        return 0
    return -exponent


def price_precision(ticker: Ticker) -> int:
    return decimal_places(ticker.last)


def get_market_snapshot() -> dict[str, Ticker]:
    tickers = fetch_tickers(tl_only=True)
    return {ticker.symbol: ticker for ticker in tickers}


def get_market_data() -> dict[str, Ticker]:
    """Compatibility export for main.py."""
    return get_market_snapshot()


# ------------------------------------------------------------
# Diagnostic test
# ------------------------------------------------------------

def run_connection_test() -> None:
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
    print("\nTop markets by available quote volume:")
    for ticker in tickers[:10]:
        volume_text = (
            str(ticker.quote_volume) if ticker.quote_volume is not None else "N/A"
        )
        print(f"{ticker.symbol:15} last={ticker.last} volume={volume_text}")
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
