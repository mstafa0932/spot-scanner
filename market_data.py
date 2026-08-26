from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"
BINANCE_EXCHANGE_INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"
BINANCE_CANDLES_URL = "https://data-api.binance.vision/api/v3/klines"

REQUEST_TIMEOUT = 15

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class ParibuDataError(Exception):
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
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask <= 0:
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
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Connection": "keep-alive",
    })
    return session


SESSION = create_session()


def normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().replace("/", "_").replace("-", "_")


def is_tl_pair(symbol: str) -> bool:
    normalized = normalize_symbol(symbol)
    return normalized.endswith("_TL") or normalized.endswith("_TRY")


def first_value(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def extract_ticker_records(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ParibuSchemaError("Paribu ticker response is not a JSON object.")
    
    direct = {str(key): value for key, value in payload.items() if isinstance(value, dict)}
    if direct:
        return direct

    for container_name in ("data", "result", "tickers", "markets"):
        container = payload.get(container_name)
        if isinstance(container, dict):
            return {str(key): value for key, value in container.items() if isinstance(value, dict)}
    
    raise ParibuSchemaError("Could not find ticker records in Paribu response.")


def get_json(url: str, params: Optional[dict[str, Any]] = None) -> Any:
    try:
        response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise ParibuHTTPError(f"Connection failure: {exc}") from exc

    if response.status_code == 429:
        time.sleep(2)
        response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)

    if response.status_code != 200:
        raise ParibuHTTPError(f"HTTP {response.status_code}: {response.text[:400]}")

    try:
        return response.json()
    except ValueError as exc:
        raise ParibuSchemaError("Returned invalid JSON.") from exc


def fetch_tickers() -> list[Ticker]:
    payload = get_json(PARIBU_TICKER_URL)
    records = extract_ticker_records(payload)
    tickers: list[Ticker] = []

    for raw_symbol, raw_data in records.items():
        symbol = normalize_symbol(raw_symbol)
        if not is_tl_pair(symbol):
            continue

        last = D(first_value(raw_data, ("last", "lastPrice", "price", "close")))
        if last is None or last <= 0:
            continue

        bid = D(first_value(raw_data, ("bid", "bestBid")))
        ask = D(first_value(raw_data, ("ask", "bestAsk")))
        volume = D(first_value(raw_data, ("volume", "vol", "baseVolume")))
        quote_volume = D(first_value(raw_data, ("quoteVolume", "turnover")))

        if quote_volume is None and volume is not None and volume > 0 and last > 0:
            quote_volume = volume * last

        change_percent = D(first_value(raw_data, ("changePercent", "percentChange", "change")))

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
        key=lambda ticker: ticker.quote_volume if ticker.quote_volume is not None else Decimal("0"),
        reverse=True,
    )
    return tickers


def get_market_snapshot() -> dict[str, Ticker]:
    return {ticker.symbol: ticker for ticker in fetch_tickers()}


def get_binance_trading_pairs() -> set[str]:
    """تحميل exchangeInfo مرة واحدة لمعرفة الأزواج المتاحة وحالتها TRADING"""
    try:
        data = get_json(BINANCE_EXCHANGE_INFO_URL)
        symbols_set = set()
        for s in data.get("symbols", []):
            if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT":
                symbols_set.add(s.get("baseAsset").upper())
        return symbols_set
    except Exception:
        return set()


def fetch_candles(symbol: str, resolution: str, limit: int = 250) -> pd.DataFrame:
    """جلب الكلينز مع استبعاد الشمعة الحالية المفتوحة واعتماد الشموع المغلقة فقط"""
    base_coin = symbol.split("_")[0].upper()
    binance_symbol = f"{base_coin}USDT"

    params = {
        "symbol": binance_symbol,
        "interval": resolution,
        "limit": limit + 1  # زيادة طلب شمعة إضافية لاستبعاد المفتوحة
    }

    payload = get_json(BINANCE_CANDLES_URL, params=params)
    if not isinstance(payload, list):
        raise ParibuSchemaError(f"Invalid candle response for {binance_symbol}")

    parsed = []
    for row in payload:
        if len(row) < 6:
            continue
        # Binance klines format: [Open time, Open, High, Low, Close, Volume, Close time, ...]
        # نحتفظ بالشموع السابقة ونستبعد الأخيرة إن لم تكن مغلقة تماماً
        o, h, l, c, v = D(row[1]), D(row[2]), D(row[3]), D(row[4]), D(row[5])
        if None in (o, h, l, c):
            continue

        parsed.append({
            "timestamp": row[0],
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v) if v is not None else 0.0,
        })

    # استبعاد الشمعة الأخيرة (الحالية المفتوحة) والاعتِماد على الشموع المغلقة السابقة
    if len(parsed) > 1:
        parsed.pop()

    if len(parsed) < 205:
        raise ParibuSchemaError(f"{symbol} {resolution}: only {len(parsed)} closed candles found. At least 205 required.")

    df = pd.DataFrame(parsed)
    return df.dropna(subset=("open", "high", "low", "close")).reset_index(drop=True)


def health_check() -> dict[str, Any]:
    started = time.time()
    snapshot = get_market_snapshot()
    binance_pairs = get_binance_trading_pairs()
    elapsed = time.time() - started
    return {
        "paribu_markets": len(snapshot),
        "binance_trading_base_assets": len(binance_pairs),
        "seconds": round(elapsed, 2),
        "candle_endpoint_configured": True,
    }
