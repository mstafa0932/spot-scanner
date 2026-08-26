from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import pandas as pd


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

    atr14: Decimal

    volume_ratio: Decimal

    recent_return_3: Decimal
    recent_return_12: Decimal

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


def calculate_indicators(
    df: pd.DataFrame,
) -> pd.DataFrame:

    x = df.copy()

    required = (
        "open",
        "high",
        "low",
        "close",
        "volume",
    )

    for column in required:
        x[column] = pd.to_numeric(
            x[column],
            errors="coerce",
        )

    x = x.dropna(
        subset=(
            "open",
            "high",
            "low",
            "close",
        )
    ).reset_index(drop=True)

    if len(x) < 205:
        return x

    # EMA
    x["EMA9"] = (
        x["close"]
        .ewm(
            span=9,
            adjust=False,
        )
        .mean()
    )

    x["EMA21"] = (
        x["close"]
        .ewm(
            span=21,
            adjust=False,
        )
        .mean()
    )

    x["EMA50"] = (
        x["close"]
        .ewm(
            span=50,
            adjust=False,
        )
        .mean()
    )

    x["EMA200"] = (
        x["close"]
        .ewm(
            span=200,
            adjust=False,
        )
        .mean()
    )

    # RSI Wilder-style EMA smoothing
    delta = x["close"].diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = (
        gain.ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        )
        .mean()
    )

    avg_loss = (
        loss.ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        )
        .mean()
    )

    rs = (
        avg_gain
        / avg_loss.replace(0, pd.NA)
    )

    x["RSI14"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    # MACD
    ema12 = (
        x["close"]
        .ewm(
            span=12,
            adjust=False,
        )
        .mean()
    )

    ema26 = (
        x["close"]
        .ewm(
            span=26,
            adjust=False,
        )
        .mean()
    )

    x["MACD"] = ema12 - ema26

    x["MACD_SIGNAL"] = (
        x["MACD"]
        .ewm(
            span=9,
            adjust=False,
        )
        .mean()
    )

    # ATR
    previous_close = x["close"].shift(1)

    true_range = pd.concat(
        [
            x["high"] - x["low"],
            (
                x["high"]
                - previous_close
            ).abs(),
            (
                x["low"]
                - previous_close
            ).abs(),
        ],
        axis=1,
    ).max(axis=1)

    x["ATR14"] = (
        true_range
        .ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        )
        .mean()
    )

    # Volume
    x["VOL_SMA20"] = (
        x["volume"]
        .rolling(20)
        .mean()
    )

    return x


def analyze_symbol(
    df: pd.DataFrame,
) -> Optional[IndicatorResult]:

    x = calculate_indicators(df)

    if len(x) < 205:
        return None

    # Last candle may still be open.
    # Use the last CLOSED candle.
    i = -2

    row = x.iloc[i]

    try:

        close = Decimal(
            str(row["close"])
        )

        ema9 = Decimal(
            str(row["EMA9"])
        )

        ema21 = Decimal(
            str(row["EMA21"])
        )

        ema50 = Decimal(
            str(row["EMA50"])
        )

        ema200 = Decimal(
            str(row["EMA200"])
        )

        rsi = Decimal(
            str(row["RSI14"])
        )

        macd = Decimal(
            str(row["MACD"])
        )

        macd_signal = Decimal(
            str(row["MACD_SIGNAL"])
        )

        atr = Decimal(
            str(row["ATR14"])
        )

        vol_sma = Decimal(
            str(row["VOL_SMA20"])
        )

    except Exception:
        return None

    if atr <= 0:
        return None

    if vol_sma <= 0:
        return None

    previous_3 = Decimal(
        str(
            x["close"]
            .iloc[i - 3]
        )
    )

    previous_12 = Decimal(
        str(
            x["close"]
            .iloc[i - 12]
        )
    )

    recent_return_3 = (
        close / previous_3
        - 1
    ) * 100

    recent_return_12 = (
        close / previous_12
        - 1
    ) * 100

    distance_ema9 = (
        close / ema9
        - 1
    ) * 100

    distance_ema21 = (
        close / ema21
        - 1
    ) * 100

    volume = Decimal(
        str(row["volume"])
    )

    volume_ratio = (
        volume / vol_sma
    )

    is_uptrend = (
        close > ema50
        and ema50 > ema200
    )

    is_above_ema9 = (
        close > ema9
    )

    is_above_ema21 = (
        close > ema21
    )

    recent_low = Decimal(
        str(
            x["low"]
            .iloc[
                i - 3:
                i + 1
            ].min()
        )
    )

    is_pullback = (
        recent_low <= ema21
        and close > ema21
    )

    candle_open = Decimal(
        str(row["open"])
    )

    candle_high = Decimal(
        str(row["high"])
    )

    candle_low = Decimal(
        str(row["low"])
    )

    body = abs(
        close - candle_open
    )

    bullish_candle = (
        close > candle_open
    )

    lower_wick = (
        min(
            candle_open,
            close,
        )
        - candle_low
    )

    bullish_pinbar = (
        body > 0
        and lower_wick
        >= body * Decimal("2")
    )

    is_bullish_candle = (
        bullish_candle
        or bullish_pinbar
    )

    resistance_48 = Decimal(
        str(
            x["high"]
            .iloc[
                i - 48:
                i
            ].max()
        )
    )

    resistance_96 = Decimal(
        str(
            x["high"]
            .iloc[
                i - 96:
                i
            ].max()
        )
    )

    swing_low = Decimal(
        str(
            x["low"]
            .iloc[
                i - 24:
                i
            ].min()
        )
    )

    previous_close = Decimal(
        str(
            x["close"]
            .iloc[i - 1]
        )
    )

    breakout = (
        close > resistance_48
        and previous_close
        <= resistance_48
    )

    return IndicatorResult(

        current_close=close,

        ema9=ema9,
        ema21=ema21,
        ema50=ema50,
        ema200=ema200,

        rsi14=rsi,

        macd_line=macd,
        macd_signal=macd_signal,

        atr14=atr,

        volume_ratio=volume_ratio,

        recent_return_3=recent_return_3,
        recent_return_12=recent_return_12,

        distance_ema9_pct=distance_ema9,
        distance_ema21_pct=distance_ema21,

        swing_low=swing_low,

        resistance_48=resistance_48,
        resistance_96=resistance_96,

        is_uptrend=is_uptrend,
        is_above_ema9=is_above_ema9,
        is_above_ema21=is_above_ema21,

        is_pullback=is_pullback,

        is_bullish_candle=is_bullish_candle,

        breakout=breakout,

        valid=True,
    )
