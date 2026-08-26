from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import os
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


PARIBU_TICKER_URL = "https://www.paribu.com/ticker"

# تم ضبط الرابط البرمجي هنا مباشرة لضمان عدم حدوث أي أخطاء في الـ Secrets أو الأقواس
PARIBU_CANDLES_URL_TEMPLATE = "https://www.paribu.com/api/v1/chart/ohlc?symbol={symbol}&period={resolution}&limit={limit}"

REQUEST_TIMEOUT = 15

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class ParibuDataError(Exception):
    pass


class ParibuConfigurationError(ParibuDataError):
    pass


class ParibuHTTPError(ParibuDataError):
    pass


class ParibuSchemaError(ParibuDataError):
    pass


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

        return ((self.ask - self.bid) / self.bid) * Decimal("100")


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


def normalize_symbol(symbol: str) -> str:
    return (
        str(symbol)
        .strip()
        .upper()
        .replace("/", "_")
        .replace("-", "_")
    )


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


def extract_ticker_records(
    payload: Any,
) -> dict[str, dict[str, Any]]:

    if not isinstance(payload, dict):
        raise ParibuSchemaError(
            "Paribu ticker response is not a JSON object."
        )

    direct = {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }

    if direct:
        return direct

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

            records = {}

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

    raise ParibuSchemaError(
        "Could not find ticker records in Paribu response."
    )


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
            f"{response.text[:400]}"
        )

    try:
        return response.json()

    except ValueError as exc:

        raise ParibuSchemaError(
            "Paribu returned invalid JSON."
        ) from exc


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

        if quote_volume is None and volume is not None:

            if volume > 0 and last > 0:

                quote_volume = (
                    volume * last
                )

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

    return {
        ticker.symbol: ticker
        for ticker in tickers
    }


def unwrap_candles(payload: Any) -> list[Any]:

    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):

        for key in (
            "data",
            "result",
            "candles",
            "items",
            "rows",
        ):

            value = payload.get(key)

            if isinstance(value, list):
                return value

            if isinstance(value, dict):

                for nested_key in (
                    "data",
                    "candles",
                    "items",
                    "rows",
                ):

                    nested = value.get(nested_key)

                    if isinstance(nested, list):
                        return nested

    raise ParibuSchemaError(
        "No candle array found in Paribu response."
    )


def parse_candle(
    row: Any,
) -> Optional[dict[str, Any]]:

    if isinstance(row, dict):

        return {
            "timestamp": first_value(
                row,
                (
                    "timestamp",
                    "time",
                    "ts",
                    "openTime",
                    "open_time",
                ),
            ),
            "open": first_value(
                row,
                ("open", "o"),
            ),
            "high": first_value(
                row,
                ("high", "h"),
            ),
            "low": first_value(
                row,
                ("low", "l"),
            ),
            "close": first_value(
                row,
                ("close", "c"),
            ),
            "volume": first_value(
                row,
                ("volume", "v", "vol"),
            ),
        }

    if isinstance(row, (list, tuple)):

        if len(row) < 5:
            return None

        return {
            "timestamp": row[0],
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume": row[5] if len(row) > 5 else None,
        }

    return None


def fetch_candles(
    symbol: str,
    resolution: str,
    limit: int = 250,
) -> pd.DataFrame:

    if not PARIBU_CANDLES_URL_TEMPLATE:

        raise ParibuConfigurationError(
            "PARIBU_CANDLES_URL_TEMPLATE is not configured."
        )

    url = PARIBU_CANDLES_URL_TEMPLATE.format(
        symbol=normalize_symbol(symbol),
        resolution=resolution,
        limit=limit,
    )

    payload = get_json(url)

    rows = unwrap_candles(payload)

    parsed = []

    for row in rows:

        candle = parse_candle(row)

        if candle is None:
            continue

        o = D(candle["open"])
        h = D(candle["high"])
        l = D(candle["low"])
        c = D(candle["close"])
        v = D(candle["volume"])

        if None in (o, h, l, c):
            continue

        parsed.append(
            {
                "timestamp": candle["timestamp"],
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v) if v is not None else 0.0,
            }
        )

    if len(parsed) < 205:

        raise ParibuSchemaError(
            f"{symbol} {resolution}: "
            f"only {len(parsed)} valid candles. "
            f"At least 205 are required."
        )

    df = pd.DataFrame(parsed)

    df = df.dropna(
        subset=(
            "open",
            "high",
            "low",
            "close",
        )
    ).reset_index(drop=True)

    return df


def health_check() -> dict[str, Any]:

    started = time.time()

    snapshot = get_market_snapshot()

    elapsed = time.time() - started

    return {
        "markets": len(snapshot),
        "seconds": round(elapsed, 2),
        "candle_endpoint_configured": bool(
            PARIBU_CANDLES_URL_TEMPLATE
        ),
    }
