from __future__ import annotations

"""Deterministic technical analysis from CLOSED Paribu candles only."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional
import pandas as pd

MIN_ROWS = 205


@dataclass(frozen=True)
class IndicatorResult:
    current_close: Decimal
    ema9: Decimal
    ema21: Decimal
    ema50: Decimal
    ema200: Decimal
    rsi14: Decimal
    macd_line: Decimal
    macd_signal: Decimal
    macd_histogram: Decimal
    atr14: Decimal
    volume_ratio: Decimal
    recent_return_3: Decimal
    recent_return_12: Decimal
    recent_return_48: Decimal
    distance_ema9_pct: Decimal
    distance_ema21_pct: Decimal
    swing_low: Decimal
    resistance_48: Decimal
    resistance_96: Decimal
    is_uptrend: bool
    is_above_ema9: bool
    is_above_ema21: bool
    is_pullback: bool
    is_bullish_candle: bool
    breakout: bool
    valid: bool
    source: str = "unknown"
    latest_closed_timestamp: int = 0


def _d(value: object) -> Optional[Decimal]:
    try:
        value_decimal = Decimal(str(value))
        return value_decimal if value_decimal.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    required = ("timestamp", "open", "high", "low", "close", "volume")
    if any(column not in x.columns for column in required):
        return pd.DataFrame()

    for column in required:
        x[column] = pd.to_numeric(x[column], errors="coerce")
    x = x.dropna(subset=required).copy()
    x = x.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    if len(x) < MIN_ROWS:
        return x

    for span, name in ((9, "EMA9"), (21, "EMA21"), (50, "EMA50"), (200, "EMA200")):
        x[name] = x["close"].ewm(span=span, adjust=False, min_periods=span).mean()

    delta = x["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    x["RSI14"] = 100 - (100 / (1 + rs))
    x.loc[(avg_loss == 0) & (avg_gain > 0), "RSI14"] = 100
    x.loc[(avg_gain == 0) & (avg_loss > 0), "RSI14"] = 0

    ema12 = x["close"].ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = x["close"].ewm(span=26, adjust=False, min_periods=26).mean()
    x["MACD"] = ema12 - ema26
    x["MACD_SIGNAL"] = x["MACD"].ewm(span=9, adjust=False, min_periods=9).mean()
    x["MACD_HIST"] = x["MACD"] - x["MACD_SIGNAL"]

    previous_close = x["close"].shift(1)
    true_range = pd.concat(
        [
            x["high"] - x["low"],
            (x["high"] - previous_close).abs(),
            (x["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["ATR14"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    x["VOL_SMA20"] = x["volume"].rolling(20, min_periods=20).mean()
    x["VOLUME_RATIO"] = x["volume"] / x["VOL_SMA20"]
    return x


def analyze_symbol(df: pd.DataFrame) -> Optional[IndicatorResult]:
    x = calculate_indicators(df)
    if len(x) < MIN_ROWS:
        return None

    # market_data.py guarantees the returned dataset contains CLOSED candles.
    i = -1
    row = x.iloc[i]

    fields = {
        "close": _d(row.get("close")),
        "ema9": _d(row.get("EMA9")),
        "ema21": _d(row.get("EMA21")),
        "ema50": _d(row.get("EMA50")),
        "ema200": _d(row.get("EMA200")),
        "rsi": _d(row.get("RSI14")),
        "macd": _d(row.get("MACD")),
        "macd_signal": _d(row.get("MACD_SIGNAL")),
        "macd_hist": _d(row.get("MACD_HIST")),
        "atr": _d(row.get("ATR14")),
        "volume_ratio": _d(row.get("VOLUME_RATIO")),
    }

    if any(value is None for value in fields.values()):
        return None

    close = fields["close"]
    ema9 = fields["ema9"]
    ema21 = fields["ema21"]
    ema50 = fields["ema50"]
    ema200 = fields["ema200"]
    rsi = fields["rsi"]
    macd = fields["macd"]
    macd_signal = fields["macd_signal"]
    macd_hist = fields["macd_hist"]
    atr = fields["atr"]
    volume_ratio = fields["volume_ratio"]

    if close <= 0 or atr <= 0 or ema21 <= 0 or ema50 <= 0 or ema200 <= 0:
        return None
    if volume_ratio < 0:
        return None

    def pct_back(n: int) -> Decimal:
        idx = i - n
        if abs(idx) >= len(x):
            return Decimal("0")
        previous = _d(x["close"].iloc[idx])
        if previous is None or previous <= 0:
            return Decimal("0")
        return (close / previous - Decimal("1")) * Decimal("100")

    recent_return_3 = pct_back(3)
    recent_return_12 = pct_back(12)
    recent_return_48 = pct_back(48)

    distance_ema9_pct = (close / ema9 - Decimal("1")) * Decimal("100")
    distance_ema21_pct = (close / ema21 - Decimal("1")) * Decimal("100")

    uptrend = bool(close > ema50 > ema200)
    above_ema9 = bool(close > ema9)
    above_ema21 = bool(close > ema21)

    recent_slice = x.iloc[max(0, len(x) - 6) : len(x)]
    recent_low = _d(recent_slice["low"].min()) if not recent_slice.empty else None
    pullback = bool(
        recent_low is not None
        and recent_low <= ema21
        and above_ema21
        and abs(distance_ema21_pct) <= Decimal("2.0")
    )

    opening = _d(row.get("open"))
    low = _d(row.get("low"))
    if opening is None or low is None:
        return None

    body = abs(close - opening)
    lower_wick = min(opening, close) - low
    bullish_pinbar = body > 0 and lower_wick >= body * Decimal("2")
    bullish_candle = bool(close > opening or bullish_pinbar)

    # Structural levels use PRIOR CLOSED candles only; the current candle
    # cannot define its own resistance.
    resistance48 = _d(x["high"].iloc[-49:-1].max()) or Decimal("0")
    resistance96 = _d(x["high"].iloc[-97:-1].max()) or Decimal("0")
    swing_low = _d(x["low"].iloc[-25:-1].min()) or close

    previous_close = _d(x["close"].iloc[-2]) or close
    breakout = bool(
        resistance48 > 0
        and close > resistance48
        and previous_close <= resistance48
        and volume_ratio >= Decimal("1.30")
    )

    latest_timestamp = int(row.get("timestamp"))
    source = str(df.attrs.get("source", "unknown"))

    if source != "PARIBU":
        # A non-Paribu source is never accepted as valid scanner input.
        return None

    return IndicatorResult(
        current_close=close,
        ema9=ema9,
        ema21=ema21,
        ema50=ema50,
        ema200=ema200,
        rsi14=rsi,
        macd_line=macd,
        macd_signal=macd_signal,
        macd_histogram=macd_hist,
        atr14=atr,
        volume_ratio=volume_ratio,
        recent_return_3=recent_return_3,
        recent_return_12=recent_return_12,
        recent_return_48=recent_return_48,
        distance_ema9_pct=distance_ema9_pct,
        distance_ema21_pct=distance_ema21_pct,
        swing_low=swing_low,
        resistance_48=resistance48,
        resistance_96=resistance96,
        is_uptrend=uptrend,
        is_above_ema9=above_ema9,
        is_above_ema21=above_ema21,
        is_pullback=pullback,
        is_bullish_candle=bullish_candle,
        breakout=breakout,
        valid=True,
        source=source,
        latest_closed_timestamp=latest_timestamp,
    )
