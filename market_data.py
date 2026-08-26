from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import time
import requests
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PARIBU_TICKER_URL = "https://www.paribu.com/ticker"
PARIBU_CHART_URL = "https://www.paribu.com/api/v1/chart/ohlc"
REQUEST_TIMEOUT = 15

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

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
    def spread_percent(self) -> Optional[Decimal]:
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask <= 0:
            return None
        return ((self.ask - self.bid) / self.bid) * Decimal("100")

def _create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3, connect=3, read=3, status=3, backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"})
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Connection": "keep-alive"
    })
    return session

_SESSION = _create_session()

def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except Exception:
        return None

def is_tl_pair(symbol: str) -> bool:
    s = symbol.strip().upper().replace("-", "").replace("/", "")
    return s.endswith("_TL") or s.endswith("_TRY")

def fetch_tickers(tl_only: bool = True) -> list[Ticker]:
    res = _SESSION.get(PARIBU_TICKER_URL, timeout=REQUEST_TIMEOUT)
    if res.status_code != 200:
        return []
    payload = res.json()
    result = []
    
    for k, v in payload.items():
        if not isinstance(v, dict):
            continue
        symbol = k.strip().upper()
        if tl_only and not is_tl_pair(symbol):
            continue
        last = _decimal(v.get("last"))
        if not last or last <= 0:
            continue
        
        result.append(Ticker(
            symbol=symbol,
            last=last,
            bid=_decimal(v.get("bid")),
            ask=_decimal(v.get("ask")),
            open_24h=_decimal(v.get("open")),
            high_24h=_decimal(v.get("high")),
            low_24h=_decimal(v.get("low")),
            volume=_decimal(v.get("volume")),
            quote_volume=_decimal(v.get("volumeQuote") or v.get("quoteVolume")),
            change_percent=_decimal(v.get("change")),
            timestamp=v.get("timestamp")
        ))
    return sorted(result, key=lambda x: x.quote_volume or Decimal("0"), reverse=True)

def get_market_snapshot() -> dict[str, Ticker]:
    tickers = fetch_tickers(tl_only=True)
    return {t.symbol: t for t in tickers}

def fetch_candles(symbol: str, resolution: str = "15", limit: int = 250) -> pd.DataFrame:
    """جلب بيانات الشموع وتأمين 200+ شمعة للمحرك الفني"""
    formatted_symbol = symbol.lower().replace("_", "")
    params = {"symbol": formatted_symbol, "period": resolution, "limit": limit}
    try:
        res = _SESSION.get(PARIBU_CHART_URL, params=params, timeout=REQUEST_TIMEOUT)
        if res.status_code == 200:
            data = res.json()
            raw_candles = data if isinstance(data, list) else data.get("data", [])
            if raw_candles and len(raw_candles) >= 50:
                df = pd.DataFrame(raw_candles)
                cols_map = {'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume',
                            'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'}
                df = df.rename(columns=cols_map)
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                return df[['open', 'high', 'low', 'close', 'volume']].dropna()
    except Exception:
        pass
    return pd.DataFrame()
