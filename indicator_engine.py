from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from candle_data import Candle, get_recent_candles, ParibuCandleError


# ============================================================
# Technical Indicator Engine
# Spot Scanner project
#
# Computes real technical indicators using Decimal precision.
# ============================================================


@dataclass(frozen=True)
class IndicatorResult:
    symbol: str
    resolution: str
    current_close: Decimal
    rsi_14: Optional[Decimal]
    atr_14: Optional[Decimal]
    ema_9: Optional[Decimal]
    ema_21: Optional[Decimal]
    is_above_ema9: Optional[bool]
    is_above_ema21: Optional[bool]


# ------------------------------------------------------------
# Core Calculations (Strict Decimal Math)
# ------------------------------------------------------------

def calculate_rsi(candles: list[Candle], period: int = 14) -> Optional[Decimal]:
    """
    Calculate Wilder's Relative Strength Index (RSI) using Decimal.
    """
    if len(candles) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(candles)):
        change = candles[i].close - candles[i - 1].close
        if change > 0:
            gains.append(change)
            losses.append(Decimal("0"))
        else:
            gains.append(Decimal("0"))
            losses.append(abs(change))

    # Initial averages (SMA for the first period)
    avg_gain = sum(gains[:period], Decimal("0")) / Decimal(period)
    avg_loss = sum(losses[:period], Decimal("0")) / Decimal(period)

    # Wilder's smoothing for the remaining periods
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * Decimal(period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + losses[i]) / Decimal(period)

    if avg_loss == 0:
        return Decimal("100")

    rs = avg_gain / avg_loss
    rsi = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    return rsi


def calculate_atr(candles: list[Candle], period: int = 14) -> Optional[Decimal]:
    """
    Calculate Average True Range (ATR) using Wilder's smoothing method.
    """
    if len(candles) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    # Initial ATR (SMA of True Ranges)
    atr = sum(true_ranges[:period], Decimal("0")) / Decimal(period)

    # Wilder's smoothing
    for i in range(period, len(true_ranges)):
        atr = (atr * Decimal(period - 1) + true_ranges[i]) / Decimal(period)

    return atr


def calculate_ema(candles: list[Candle], period: int = 9) -> Optional[Decimal]:
    """
    Calculate Exponential Moving Average (EMA).
    """
    if len(candles) < period:
        return None

    closes = [c.close for c in candles]
    multiplier = Decimal("2") / Decimal(period + 1)

    # Initial EMA is the Simple Moving Average (SMA) of the first period
    sma = sum(closes[:period], Decimal("0")) / Decimal(period)
    ema = sma

    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


# ------------------------------------------------------------
# High-Level Indicator Analysis
# ------------------------------------------------------------

def analyze_symbol(symbol: str, resolution: str = "15", limit: int = 50) -> Optional[IndicatorResult]:
    """
    Fetch recent candles and compute all necessary indicators for a given symbol.
    """
    try:
        candles = get_recent_candles(symbol, resolution=resolution, limit=limit)
    except ParibuCandleError:
        return None

    if not candles:
        return None

    current_close = candles[-1].close

    rsi = calculate_rsi(candles, period=14)
    atr = calculate_atr(candles, period=14)
    ema9 = calculate_ema(candles, period=9)
    ema21 = calculate_ema(candles, period=21)

    is_above_ema9 = (current_close > ema9) if ema9 is not None else None
    is_above_ema21 = (current_close > ema21) if ema21 is not None else None

    return IndicatorResult(
        symbol=symbol,
        resolution=resolution,
        current_close=current_close,
        rsi_14=rsi,
        atr_14=atr,
        ema_9=ema9,
        ema_21=ema21,
        is_above_ema9=is_above_ema9,
        is_above_ema21=is_above_ema21,
    )


# ------------------------------------------------------------
# Diagnostic Test
# ------------------------------------------------------------

def run_connection_test() -> None:
    symbol = "BTC_TL"
    print("=" * 70)
    print("TECHNICAL INDICATOR ENGINE TEST")
    print("=" * 70)
    print(f"Analyzing {symbol} on 15m resolution...")

    result = analyze_symbol(symbol, resolution="15", limit=50)
    if not result:
        print("Failed to retrieve or analyze candles.")
        return

    print("Status: OK")
    print(f"Current Close: {result.current_close}")
    print(f"RSI (14):     {result.rsi_14.quantize(Decimal('0.01')) if result.rsi_14 else 'N/A'}")
    print(f"ATR (14):     {result.atr_14.quantize(Decimal('0.0001')) if result.atr_14 else 'N/A'}")
    print(f"EMA (9):      {result.ema_9}")
    print(f"EMA (21):     {result.ema_21}")
    print(f"Above EMA 9:  {result.is_above_ema9}")
    print(f"Above EMA 21: {result.is_above_ema21}")
    print("=" * 70)


if __name__ == "__main__":
    run_connection_test()
