from __future__ import annotations

"""
scanner.py
Professional Paribu Spot scanner (Sniper Mode).

Important:
- No ccxt.
- No pandas_ta.
- Uses the project's existing market_data.py for market prices and candles.
- Uses indicator_engine.py for technical calculations.
- Paribu is the execution-price source: Ask/Bid/last.
- External candles are used only for technical context.
- No order is ever placed automatically.
"""

import html
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

from market_data import (
    ParibuDataError,
    Ticker,
    fetch_candles,
    get_market_snapshot,
)

from indicator_engine import (
    analyze_symbol,
    calculate_indicators,
)


# ============================================================
# CONFIGURATION (Sniper Mode Applied)
# ============================================================

STATE_FILE = Path(
    os.getenv(
        "SCANNER_STATE_FILE",
        "scanner_state.json",
    )
)

MAX_SIGNALS_PER_RUN = int(
    os.getenv(
        "MAX_SIGNALS_PER_RUN",
        "2",
    )
)

MIN_SCORE = int(
    os.getenv(
        "MIN_SCORE",
        "88",
    )
)

MIN_QUOTE_VOLUME_TL = Decimal(
    os.getenv(
        "MIN_QUOTE_VOLUME_TL",
        "5000000",
    )
)

MAX_ALLOWED_SPREAD_PCT = Decimal(
    os.getenv(
        "MAX_ALLOWED_SPREAD_PCT",
        "0.80",
    )
)

MIN_TP1_PCT = Decimal(
    os.getenv(
        "MIN_TP1_PCT",
        "1.80",
    )
)

MIN_NET_TP1_PCT = Decimal(
    os.getenv(
        "MIN_NET_TP1_PCT",
        "1.00",
    )
)

MIN_RR = Decimal(
    os.getenv(
        "MIN_RR",
        "1.50",
    )
)

TAKER_FEE_PCT = Decimal(
    os.getenv(
        "PARIBU_TAKER_FEE_PCT",
        "0.28",
    )
)

SLIPPAGE_PCT = Decimal(
    os.getenv(
        "EXPECTED_SLIPPAGE_PCT",
        "0.15",
    )
)

CANDLE_LIMIT = int(
    os.getenv(
        "CANDLE_LIMIT",
        "250",
    )
)

MAX_TECHNICAL_MARKETS = int(
    os.getenv(
        "MAX_TECHNICAL_MARKETS",
        "100",
    )
)

COOLDOWN_SECONDS = int(
    os.getenv(
        "SIGNAL_COOLDOWN_SECONDS",
        str(4 * 60 * 60),
    )
)

ORDERBOOK_DEPTH = int(
    os.getenv(
        "ORDERBOOK_DEPTH",
        "20",
    )
)


# ============================================================
# LOGGING
# ============================================================

LOGGER = logging.getLogger("spot_scanner")

if not LOGGER.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class ScanStats:
    total_markets: int = 0

    liquidity_pass: int = 0
    liquidity_fail: int = 0

    spread_pass: int = 0
    spread_fail: int = 0

    technical_attempted: int = 0

    candle_success: int = 0
    candle_fail: int = 0

    indicator_success: int = 0
    indicator_fail: int = 0

    setup_pass: int = 0
    setup_fail: int = 0

    score_pass: int = 0
    score_fail: int = 0

    execution_attempted: int = 0
    execution_pass: int = 0
    execution_fail: int = 0

    tp1_fail: int = 0
    rr_fail: int = 0

    rejection_reasons: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.rejection_reasons is None:
            self.rejection_reasons = {}

    def reject(self, reason: str) -> None:
        assert self.rejection_reasons is not None
        self.rejection_reasons[reason] = (
            self.rejection_reasons.get(reason, 0) + 1
        )


@dataclass(frozen=True)
class Opportunity:
    symbol: str
    score: int
    strength: str
    setup: str

    data_source: str

    current_price: Decimal
    paribu_bid: Decimal
    paribu_ask: Decimal
    spread_pct: Decimal

    entry_price: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal

    rr: Decimal
    tp1_pct: Decimal
    net_tp1_pct: Decimal

    rsi: Decimal
    atr_pct: Decimal
    volume_ratio: Decimal
    quote_volume: Decimal

    resistance: Optional[Decimal]
    reason: str


@dataclass(frozen=True)
class TechnicalSnapshot:
    close: Decimal
    rsi: Decimal
    atr: Decimal
    volume_ratio: Decimal

    ema9: Optional[Decimal]
    ema21: Optional[Decimal]
    ema50: Optional[Decimal]
    ema200: Optional[Decimal]

    macd: Optional[Decimal]
    macd_signal: Optional[Decimal]

    is_uptrend: bool
    is_above_ema21: bool
    is_pullback: bool
    is_breakout: bool
    is_bullish_candle: bool

    resistance_48: Optional[Decimal]
    resistance_96: Optional[Decimal]
    swing_low: Optional[Decimal]

    recent_return_3: Optional[Decimal]
    recent_return_12: Optional[Decimal]

    source: str


# ============================================================
# BASIC HELPERS
# ============================================================

def decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    if value is None:
        return default

    try:
        result = Decimal(str(value))
        if result.is_finite():
            return result
    except (InvalidOperation, ValueError, TypeError):
        pass

    return default


def clamp_decimal(
    value: Decimal,
    minimum: Decimal,
    maximum: Decimal,
) -> Decimal:
    return max(
        minimum,
        min(value, maximum),
    )


def price_step(price: Decimal) -> Decimal:
    if price >= Decimal("1000"):
        return Decimal("0.01")
    if price >= Decimal("100"):
        return Decimal("0.01")
    if price >= Decimal("1"):
        return Decimal("0.0001")
    if price >= Decimal("0.01"):
        return Decimal("0.000001")
    if price >= Decimal("0.0001"):
        return Decimal("0.00000001")
    return Decimal("0.0000000001")


def format_price(price: Decimal) -> str:
    step = price_step(price)
    return format(
        price.quantize(step),
        "f",
    )


def strength(score: int) -> str:
    if score >= 90:
        return "🔥 EXCEPTIONAL"
    if score >= 85:
        return "🟢 STRONG"
    if score >= 78:
        return "🟡 GOOD"
    return "🔵 WATCH"


# ============================================================
# STATE
# ============================================================

def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"sent_signals": {}}

    try:
        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(data, dict):
            return {"sent_signals": {}}

        sent = data.get(
            "sent_signals",
            {},
        )

        if not isinstance(sent, dict):
            sent = {}

        data["sent_signals"] = sent
        return data

    except Exception as exc:
        LOGGER.warning(
            "State load failed: %s",
            exc,
        )
        return {"sent_signals": {}}


def save_state(state: dict[str, Any]) -> None:
    temp = STATE_FILE.with_suffix(".tmp")

    try:
        temp.write_text(
            json.dumps(
                state,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp.replace(STATE_FILE)

    except Exception as exc:
        LOGGER.error(
            "State save failed: %s",
            exc,
        )


def cooldown_allowed(
    symbol: str,
    state: dict[str, Any],
) -> bool:

    sent = state.setdefault(
        "sent_signals",
        {},
    )

    raw = sent.get(symbol)

    if raw is None:
        return True

    try:
        timestamp = int(raw)

    except (
        TypeError,
        ValueError,
    ):
        return True

    return (
        time.time() - timestamp
        >= COOLDOWN_SECONDS
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(
    message: str,
) -> bool:

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:
        LOGGER.error(
            "Telegram credentials are missing."
        )
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        if response.status_code != 200:
            LOGGER.error(
                "Telegram HTTP %s: %s",
                response.status_code,
                response.text[:500],
            )
            return False

        return True

    except requests.RequestException as exc:
        LOGGER.error(
            "Telegram request failed: %s",
            exc,
        )
        return False


# ============================================================
# INDICATOR COMPATIBILITY
# ============================================================

def get_attr(
    obj: Any,
    names: tuple[str, ...],
    default: Any = None,
) -> Any:

    if isinstance(
        obj,
        dict,
    ):
        for name in names:
            if name in obj:
                return obj[name]
        return default

    for name in names:
        if hasattr(obj, name):
            return getattr(
                obj,
                name,
            )

    return default


def latest_closed_row(
    df: pd.DataFrame,
) -> Optional[pd.Series]:

    if len(df) >= 2:
        return df.iloc[-2]

    if len(df) == 1:
        return df.iloc[-1]

    return None


def make_technical_snapshot(
    df: pd.DataFrame,
) -> Optional[TechnicalSnapshot]:

    if len(df) < 2:
        return None

    try:

        result = analyze_symbol(df)

        if result is not None:

            close = decimal(get_attr(result, ("current_close", "close")))
            rsi = decimal(get_attr(result, ("rsi14", "rsi_14", "rsi")))
            atr = decimal(get_attr(result, ("atr14", "atr_14", "atr")))
            volume_ratio = decimal(get_attr(result, ("volume_ratio",)))

            if (
                close is not None
                and rsi is not None
                and atr is not None
                and volume_ratio is not None
            ):

                return TechnicalSnapshot(
                    close=close,
                    rsi=rsi,
                    atr=atr,
                    volume_ratio=volume_ratio,
                    ema9=decimal(get_attr(result, ("ema9", "EMA9", "EMA_9"))),
                    ema21=decimal(get_attr(result, ("ema21", "EMA21", "EMA_21"))),
                    ema50=decimal(get_attr(result, ("ema50", "EMA50", "EMA_50"))),
                    ema200=decimal(get_attr(result, ("ema200", "EMA200", "EMA_200"))),
                    macd=decimal(get_attr(result, ("macd_line", "MACD", "macd"))),
                    macd_signal=decimal(get_attr(result, ("macd_signal", "MACD_SIGNAL", "signal"))),
                    is_uptrend=bool(get_attr(result, ("is_uptrend",), False)),
                    is_above_ema21=bool(get_attr(result, ("is_above_ema21",), False)),
                    is_pullback=bool(get_attr(result, ("is_pullback",), False)),
                    is_breakout=bool(get_attr(result, ("breakout",), False)),
                    is_bullish_candle=bool(get_attr(result, ("is_bullish_candle",), False)),
                    resistance_48=decimal(get_attr(result, ("resistance_48",))),
                    resistance_96=decimal(get_attr(result, ("resistance_96",))),
                    swing_low=decimal(get_attr(result, ("swing_low",))),
                    recent_return_3=decimal(get_attr(result, ("recent_return_3",))),
                    recent_return_12=decimal(get_attr(result, ("recent_return_12",))),
                    source=str(df.attrs.get("source", "unknown")),
                )

    except Exception as exc:
        LOGGER.debug("analyze_symbol compatibility path failed: %s", exc)

    try:

        calculated = calculate_indicators(df)

        if not isinstance(calculated, pd.DataFrame):
            return None

        row = latest_closed_row(calculated)

        if row is None:
            return None

        close = decimal(row.get("close"))
        rsi = decimal(row.get("RSI14", row.get("RSI_14")))
        atr = decimal(row.get("ATR14", row.get("ATR_14")))
        volume = decimal(row.get("volume"), Decimal("0"))
        volume_sma = decimal(row.get("VOL_SMA20", row.get("VOL_SMA_20")), Decimal("0"))

        if (close is None or close <= 0 or rsi is None or atr is None or atr <= 0):
            return None

        volume_ratio = (volume / volume_sma if volume_sma and volume_sma > 0 else Decimal("0"))
        ema9 = decimal(row.get("EMA9", row.get("EMA_9")))
        ema21 = decimal(row.get("EMA21", row.get("EMA_21")))
        ema50 = decimal(row.get("EMA50", row.get("EMA_50")))
        ema200 = decimal(row.get("EMA200", row.get("EMA_200")))
        macd = decimal(row.get("MACD", row.get("MACD_line")))
        macd_signal = decimal(row.get("MACD_SIGNAL", row.get("MACD_signal")))

        prev_3 = decimal(calculated["close"].iloc[-5] if len(calculated) >= 5 else None)
        prev_12 = decimal(calculated["close"].iloc[-14] if len(calculated) >= 14 else None)

        recent_return_3 = ((close / prev_3 - Decimal("1")) * Decimal("100") if prev_3 and prev_3 > 0 else None)
        recent_return_12 = ((close / prev_12 - Decimal("1")) * Decimal("100") if prev_12 and prev_12 > 0 else None)

        is_uptrend = bool(ema50 and ema200 and close > ema50 and ema50 > ema200)
        is_above_ema21 = bool(ema21 and close > ema21)

        recent_lows = calculated["low"].iloc[-6:-1]
        is_pullback = bool(ema21 and not recent_lows.empty and Decimal(str(recent_lows.min())) <= ema21 and close > ema21)

        candle = calculated.iloc[-2]
        candle_open = decimal(candle.get("open"), close)
        candle_high = decimal(candle.get("high"), close)
        candle_low = decimal(candle.get("low"), close)

        body = abs(close - candle_open)
        lower_wick = (min(candle_open, close) - candle_low)

        bullish_pinbar = bool(body > 0 and lower_wick >= body * Decimal("2"))
        bullish_candle = (close > candle_open or bullish_pinbar)

        resistance_48 = None
        resistance_96 = None
        swing_low = None

        if len(calculated) >= 50:
            resistance_48 = decimal(calculated["high"].iloc[-50:-2].max())
        if len(calculated) >= 98:
            resistance_96 = decimal(calculated["high"].iloc[-98:-2].max())
        if len(calculated) >= 26:
            swing_low = decimal(calculated["low"].iloc[-26:-2].min())

        previous_close = decimal(calculated["close"].iloc[-3])
        breakout = bool(resistance_48 and previous_close and close > resistance_48 and previous_close <= resistance_48)

        return TechnicalSnapshot(
            close=close,
            rsi=rsi,
            atr=atr,
            volume_ratio=volume_ratio,
            ema9=ema9,
            ema21=ema21,
            ema50=ema50,
            ema200=ema200,
            macd=macd,
            macd_signal=macd_signal,
            is_uptrend=is_uptrend,
            is_above_ema21=is_above_ema21,
            is_pullback=is_pullback,
            is_breakout=breakout,
            is_bullish_candle=bullish_candle,
            resistance_48=resistance_48,
            resistance_96=resistance_96,
            swing_low=swing_low,
            recent_return_3=recent_return_3,
            recent_return_12=recent_return_12,
            source=str(df.attrs.get("source", "unknown")),
        )

    except Exception as exc:
        LOGGER.debug("DataFrame indicator fallback failed: %s", exc)
        return None


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    tech: TechnicalSnapshot,
    ticker: Ticker,
) -> tuple[int, list[str]]:

    score = 0
    reasons: list[str] = []

    # Trend: 25
    if tech.is_uptrend:
        score += 15
        reasons.append("اتجاه صاعد")
    if tech.is_above_ema21:
        score += 10
        reasons.append("فوق EMA21")

    # RSI: 15
    if Decimal("52") <= tech.rsi <= Decimal("65"):
        score += 15
        reasons.append("RSI صحي")
    elif Decimal("48") <= tech.rsi < Decimal("52"):
        score += 9
    elif Decimal("65") < tech.rsi <= Decimal("70"):
        score += 7
    elif tech.rsi > Decimal("70"):
        score -= 4

    # MACD: 10
    if (tech.macd is not None and tech.macd_signal is not None and tech.macd > tech.macd_signal):
        score += 10
        reasons.append("MACD داعم")

    # Volume: 15
    if tech.volume_ratio >= Decimal("2"):
        score += 15
        reasons.append("حجم قوي")
    elif tech.volume_ratio >= Decimal("1.5"):
        score += 12
        reasons.append("حجم مرتفع")
    elif tech.volume_ratio >= Decimal("1.15"):
        score += 8
    elif tech.volume_ratio >= Decimal("1"):
        score += 5
    else:
        score -= 3

    # Setup / price action: 15
    if tech.is_pullback:
        score += 10
        reasons.append("Pullback")
    if tech.is_breakout:
        score += 5
        reasons.append("Breakout")
    elif tech.is_bullish_candle:
        score += 4
        reasons.append("شمعة إيجابية")

    # Local execution: 20
    spread = ticker.spread_percent
    if spread is not None:
        if spread <= Decimal("0.20"):
            score += 10
            reasons.append("سبريد محلي ممتاز")
        elif spread <= Decimal("0.40"):
            score += 8
        elif spread <= Decimal("0.60"):
            score += 5
        elif spread <= MAX_ALLOWED_SPREAD_PCT:
            score += 2

    if (ticker.quote_volume is not None and ticker.quote_volume >= Decimal("3000000")):
        score += 10
    elif (ticker.quote_volume is not None and ticker.quote_volume >= Decimal("1500000")):
        score += 7
    elif (ticker.quote_volume is not None and ticker.quote_volume >= Decimal("750000")):
        score += 4

    score = max(0, min(score, 100))
    return score, reasons


# ============================================================
# LEVELS / EXECUTION (Sniper Mode Applied)
# ============================================================

def calculate_local_levels(
    tech: TechnicalSnapshot,
    ticker: Ticker,
) -> Optional[tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]]:

    entry = ticker.ask if ticker.ask is not None and ticker.ask > 0 else ticker.last
    if entry <= 0:
        return None

    atr_pct = tech.atr / tech.close if tech.close > 0 else Decimal("0")
    if atr_pct <= 0:
        return None

    risk_pct = clamp_decimal(
        atr_pct * Decimal("2.50"),
        Decimal("0.025"),
        Decimal("0.08"),
    )

    stop = entry * (Decimal("1") - risk_pct)
    if stop <= 0 or stop >= entry:
        return None

    resistance_candidates = [
        value for value in (tech.resistance_48, tech.resistance_96)
        if value is not None and value > tech.close
    ]
    resistance = min(resistance_candidates) if resistance_candidates else None

    minimum_tp1 = entry * (Decimal("1") + (MIN_TP1_PCT / Decimal("100")))
    
    atr_tp1 = entry * (Decimal("1") + risk_pct * Decimal("1.50"))

    if resistance is not None:
        resistance_ratio = (resistance / tech.close - Decimal("1"))
        structural_tp1 = entry * (Decimal("1") + resistance_ratio)
        tp1 = max(minimum_tp1, min(structural_tp1, atr_tp1 * Decimal("1.35")))
        tp1 = max(tp1, minimum_tp1)
    else:
        tp1 = max(minimum_tp1, atr_tp1)

    atr_tp2 = entry * (Decimal("1") + risk_pct * Decimal("2.50"))
    if tech.resistance_96 is not None and (tech.resistance_96 > tech.close):
        structural_tp2 = entry * (Decimal("1") + (tech.resistance_96 / tech.close - Decimal("1")))
        tp2 = max(atr_tp2, structural_tp2)
    else:
        tp2 = atr_tp2

    max_tp2 = entry * Decimal("1.25")
    tp2 = min(tp2, max_tp2)
    
    if tp1 >= tp2:
        tp2 = max(atr_tp2, tp1 * Decimal("1.03"))

    return (entry, stop, tp1, tp2, atr_pct * Decimal("100"), resistance if resistance is not None else Decimal("0"), risk_pct * Decimal("100"))


def evaluate_execution(
    tech: TechnicalSnapshot,
    ticker: Ticker,
    entry: Decimal,
    stop: Decimal,
    tp1: Decimal,
    tp2: Decimal,
) -> tuple[bool, str, Decimal, Decimal, Decimal]:

    if ticker.bid is None or ticker.ask is None:
        return (False, "Paribu Bid/Ask غير متوفر", Decimal("0"), Decimal("0"), Decimal("0"))

    spread = ticker.spread_percent
    if spread is None:
        return (False, "Spread غير صالح", Decimal("0"), Decimal("0"), Decimal("0"))

    if spread > MAX_ALLOWED_SPREAD_PCT:
        return (False, f"Spread {spread:.2f}% > الحد", spread, Decimal("0"), Decimal("0"))

    gross_tp1_pct = ((tp1 - entry) / entry * Decimal("100"))
    if gross_tp1_pct < MIN_TP1_PCT:
        return (False, f"TP1 {gross_tp1_pct:.2f}% < الحد الأدنى", spread, gross_tp1_pct, Decimal("0"))

    total_cost_pct = (TAKER_FEE_PCT * Decimal("2") + SLIPPAGE_PCT)
    net_tp1_pct = (gross_tp1_pct - total_cost_pct)

    if net_tp1_pct < MIN_NET_TP1_PCT:
        return (False, f"صافي TP1 {net_tp1_pct:.2f}% غير كافٍ", spread, gross_tp1_pct, net_tp1_pct)

    risk = (entry - stop)
    reward = (tp1 - entry)

    if risk <= 0 or reward <= 0:
        return (False, "المخاطرة/العائد غير صالح", spread, gross_tp1_pct, net_tp1_pct)

    rr = (reward / risk)
    if rr < MIN_RR:
        return (False, f"R:R {rr:.2f} < {MIN_RR}", spread, gross_tp1_pct, net_tp1_pct)

    return (True, "OK", spread, gross_tp1_pct, net_tp1_pct)


# ============================================================
# OPPORTUNITY BUILD (Sniper Mode Applied)
# ============================================================

def build_opportunity(
    ticker: Ticker,
    tech: TechnicalSnapshot,
    score: int,
    reasons: list[str],
) -> Optional[Opportunity]:

    levels = calculate_local_levels(tech, ticker)
    if levels is None:
        return None

    (entry, stop, tp1, tp2, atr_pct, resistance, _risk_pct) = levels

    (ok, reject_reason, spread, tp1_pct, net_tp1_pct) = evaluate_execution(
        tech, ticker, entry, stop, tp1, tp2
    )

    if not ok:
        LOGGER.info("Rejected %s: %s", ticker.symbol, reject_reason)
        return None

    risk = entry - stop
    reward = tp1 - entry
    rr = (reward / risk if risk > 0 else Decimal("0"))

    setup = ("BREAKOUT" if tech.is_breakout else "PULLBACK" if tech.is_pullback else "MOMENTUM")

    return Opportunity(
        symbol=ticker.symbol,
        score=int(score),
        strength=strength(score),
        setup=setup,
        data_source=tech.source,
        current_price=ticker.last,
        paribu_bid=ticker.bid,
        paribu_ask=ticker.ask,
        spread_pct=spread.quantize(Decimal("0.01")),
        entry_price=entry,
        stop_loss=stop,
        tp1=tp1,
        tp2=tp2,
        rr=rr.quantize(Decimal("0.01")),
        tp1_pct=tp1_pct.quantize(Decimal("0.01")),
        net_tp1_pct=net_tp1_pct.quantize(Decimal("0.01")),
        rsi=tech.rsi.quantize(Decimal("0.1")),
        atr_pct=atr_pct.quantize(Decimal("0.01")),
        volume_ratio=tech.volume_ratio.quantize(Decimal("0.01")),
        quote_volume=ticker.quote_volume if ticker.quote_volume is not None else Decimal("0"),
        resistance=(resistance if resistance > 0 else None),
        reason=" | ".join(reasons[:8]),
    )


# ============================================================
# TELEGRAM FORMATTING (Sniper Mode Applied)
# ============================================================

def format_opportunity(
    opp: Opportunity,
    rank: int,
) -> str:

    resistance_text = (format_price(opp.resistance) if opp.resistance is not None else "غير محددة")
    vol_m = (opp.quote_volume / Decimal("1000000")).quantize(Decimal("0.01"))

    return (
        f"🎯 <b>SPOT ENTRY #{rank}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{html.escape(opp.symbol)}</b>\n"
        f"💪 <b>القوة:</b> {opp.strength}\n"
        f"📊 <b>Score:</b> {opp.score}/100\n"
        f"🧩 <b>Setup:</b> {opp.setup}\n"
        f"📡 <b>الشموع:</b> {html.escape(opp.data_source)}\n\n"

        f"💵 <b>Paribu Ask:</b> <code>{format_price(opp.paribu_ask)}</code>\n"
        f"💵 <b>الدخول:</b> <code>{format_price(opp.entry_price)}</code>\n"
        f"🛑 <b>وقف الخسارة:</b> <code>{format_price(opp.stop_loss)}</code>\n"
        f"🎯 <b>TP1:</b> <code>{format_price(opp.tp1)}</code>\n"
        f"🚀 <b>TP2:</b> <code>{format_price(opp.tp2)}</code>\n"
        f"🧱 <b>المقاومة المرجعية:</b> <code>{resistance_text}</code>\n\n"

        f"📐 <b>R:R:</b> 1:{opp.rr}\n"
        f"📈 <b>TP1:</b> +{opp.tp1_pct}%\n"
        f"💰 <b>صافي TP1:</b> +{opp.net_tp1_pct}%\n"
        f"📏 <b>Spread Paribu:</b> {opp.spread_pct}%\n\n"

        f"📊 <b>RSI:</b> {opp.rsi}\n"
        f"📊 <b>ATR:</b> {opp.atr_pct}%\n"
        f"💧 <b>الحجم:</b> {opp.volume_ratio}x المتوسط\n"
        f"🌊 <b>السيولة اليومية:</b> {vol_m} مليون ₺\n"
        f"🧠 <b>السبب:</b> {html.escape(opp.reason)}\n\n"

        "⚠️ <b>Spot فقط — التنفيذ يدوي.</b>"
    )


def format_no_signal_report(stats: ScanStats) -> str:
    reasons = sorted((stats.rejection_reasons or {}).items(), key=lambda item: item[1], reverse=True)
    reason_text = "لا توجد بيانات كافية"
    if reasons:
        reason_text = "\n".join(f"• {html.escape(reason)}: {count}" for reason, count in reasons[:6])

    return (
        "🔍 <b>Paribu — التقرير الدوري (وضع القناص)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 الأزواج: {stats.total_markets}\n"
        f"💧 اجتازت السيولة: {stats.liquidity_pass}\n"
        f"❌ رفض السيولة: {stats.liquidity_fail}\n"
        f"📏 اجتازت السبريد: {stats.spread_pass}\n"
        f"❌ رفض السبريد: {stats.spread_fail}\n"
        f"🧪 المحاولات الفنية: {stats.technical_attempted}\n"
        f"⭐ اجتاز Score: {stats.score_pass}\n"
        f"❌ فشل Score: {stats.score_fail}\n\n"
        "🔎 <b>أكثر أسباب الرفض:</b>\n"
        f"{reason_text}\n\n"
        "💡 <b>تم تفعيل فلتر الحماية والسيولة الصارم.</b>"
    )


# ============================================================
# MAIN SCAN (Sniper Mode Applied)
# ============================================================

def run_scanner() -> None:

    LOGGER.info("Starting professional Paribu Spot Scanner (Sniper Mode)...")
    stats = ScanStats()

    try:
        snapshot = get_market_snapshot()
    except ParibuDataError as exc:
        LOGGER.exception("Paribu market snapshot failed.")
        send_telegram_message(f"🚨 <b>PARIBU SCANNER ERROR</b>\n\n<code>{html.escape(str(exc))}</code>")
        return

    stats.total_markets = len(snapshot)
    state = load_state()

    # ==========================================================
    # فلتر حماية رأس المال (Bitcoin Health Check)
    # ==========================================================
    btc_is_safe = True
    try:
        btc_df = fetch_candles("btc_tl", resolution="15m", limit=100)
        btc_tech = make_technical_snapshot(btc_df)
        
        if btc_tech is not None and btc_tech.ema50 is not None:
            if btc_tech.close < btc_tech.ema50:
                btc_is_safe = False
                LOGGER.info("Safety Filter Activated: BTC is below EMA50. Halting altcoin signals.")
                
    except Exception as exc:
        LOGGER.warning("Could not verify BTC health, proceeding with caution: %s", exc)

    if not btc_is_safe:
        send_telegram_message(
            "🛡️ <b>وضع القناص (Sniper Mode) قيد الانتظار</b>\n\n"
            "البيتكوين (BTC) يتداول في مسار سلبي تحت خط الدعم (EMA50).\n"
            "تم حجب جميع إشارات الشراء للعملات البديلة مؤقتاً لحماية رأس مالك."
        )
        return
    # ==========================================================

    tickers = sorted(
        snapshot.values(),
        key=lambda item: (item.quote_volume if item.quote_volume is not None else Decimal("0")),
        reverse=True,
    )

    candidates: list[Opportunity] = []

    for ticker in tickers:

        if (ticker.quote_volume is None or ticker.quote_volume < MIN_QUOTE_VOLUME_TL):
            stats.liquidity_fail += 1
            stats.reject("سيولة أقل من الحد")
            continue

        stats.liquidity_pass += 1

        if ticker.spread_percent is None:
            stats.spread_fail += 1
            stats.reject("Spread غير متوفر")
            continue

        if (ticker.spread_percent > MAX_ALLOWED_SPREAD_PCT):
            stats.spread_fail += 1
            stats.reject(f"Spread {ticker.spread_percent:.2f}%")
            continue

        stats.spread_pass += 1

        if (stats.technical_attempted >= MAX_TECHNICAL_MARKETS):
            break

        stats.technical_attempted += 1

        try:
            df = fetch_candles(ticker.symbol, resolution="15m", limit=CANDLE_LIMIT)
            stats.candle_success += 1
        except Exception as exc:
            stats.candle_fail += 1
            stats.reject("فشل جلب الشموع")
            LOGGER.debug("%s candle failure: %s", ticker.symbol, exc)
            continue

        tech = make_technical_snapshot(df)

        if tech is None:
            stats.indicator_fail += 1
            stats.reject("فشل المؤشرات")
            continue

        stats.indicator_success += 1

        if tech.rsi >= Decimal("75"):
            stats.reject("RSI مرتفع")
            continue

        if (tech.recent_return_3 is not None and tech.recent_return_3 >= Decimal("4")):
            stats.reject("Anti-FOMO 3 شموع")
            continue

        if (tech.recent_return_12 is not None and tech.recent_return_12 >= Decimal("10")):
            stats.reject("Anti-FOMO 12 شمعة")
            continue

        setup_ok = (
            (tech.is_uptrend and tech.is_above_ema21)
            or tech.is_pullback
            or tech.is_breakout
            or (tech.is_bullish_candle and tech.is_above_ema21)
        )

        if not setup_ok:
            stats.setup_fail += 1
            stats.reject("Setup غير واضح")
            continue

        stats.setup_pass += 1

        score, reasons = calculate_score(tech, ticker)

        if score < MIN_SCORE:
            stats.score_fail += 1
            stats.reject(f"Score {score} < {MIN_SCORE}")
            continue

        stats.score_pass += 1
        stats.execution_attempted += 1

        opportunity = build_opportunity(ticker, tech, score, reasons)

        if opportunity is None:
            stats.execution_fail += 1
            stats.reject("المستويات/التنفيذ غير اقتصادي")
            continue

        stats.execution_pass += 1

        if not cooldown_allowed(ticker.symbol, state):
            stats.reject("Cooldown")
            continue

        candidates.append(opportunity)

    candidates.sort(
        key=lambda item: (item.score, item.net_tp1_pct, item.rr, item.volume_ratio),
        reverse=True,
    )

    selected = candidates[:max(1, MAX_SIGNALS_PER_RUN)]

    if not selected:
        report = format_no_signal_report(stats)
        LOGGER.info("No executable opportunities.")
        send_telegram_message(report)
        save_state(state)
        return

    header = (
        "🔥 <b>Paribu — أفضل فرص Spot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"تم العثور على <b>{len(selected)}</b> فرصة قابلة للتنفيذ.\n"
        "مرتبة حسب السيولة، القوة، والقابلية الاقتصادية."
    )

    send_telegram_message(header)

    for rank, opportunity in enumerate(selected, start=1):
        message = format_opportunity(opportunity, rank)
        if send_telegram_message(message):
            state["sent_signals"][opportunity.symbol] = int(time.time())
            save_state(state)
            LOGGER.info("Signal sent: %s score=%s", opportunity.symbol, opportunity.score)

    LOGGER.info("Scan finished. Selected=%d candidates=%d", len(selected), len(candidates))


if __name__ == "__main__":
    run_scanner()
