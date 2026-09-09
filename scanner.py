# -*- coding: utf-8 -*-
from __future__ import annotations

"""Paribu-only Spot Sniper Scanner.

The scanner has one non-negotiable rule:
NO TELEGRAM BUY SIGNAL IS SENT unless every hard gate passes.

This is not a promise of profit. No market system can honestly guarantee that.
It is a deterministic guarantee that the sent signal satisfied the configured
Paribu-data and risk gates at the moment of final validation.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Any
import html
import json
import logging
import os
import time

import requests

from market_data import (
    ParibuDataError,
    Ticker,
    OrderBookSnapshot,
    fetch_candles,
    get_market_snapshot,
    get_order_book,
)
from indicator_engine import IndicatorResult, analyze_symbol

# Near-Miss observation layer.
# هذه الطبقة للتسجيل والدراسة فقط ولا تتجاوز أي Gate.
from near_miss import record_near_miss, update_near_miss_outcomes


LOGGER = logging.getLogger("paribu_sniper")
if not LOGGER.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


# ---------------------------- configuration ----------------------------

STATE_FILE = Path(os.getenv("SCANNER_STATE_FILE", "scanner_state.json"))
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ENV = "TELEGRAM_CHAT_ID"

MAX_SIGNALS_PER_RUN = max(1, int(os.getenv("MAX_SIGNALS_PER_RUN", "2")))
MIN_SCORE = int(os.getenv("MIN_SCORE", "90"))
MIN_QUOTE_VOLUME_TL = Decimal(os.getenv("MIN_QUOTE_VOLUME_TL", "5000000"))
MAX_SPREAD_PCT = Decimal(os.getenv("MAX_ALLOWED_SPREAD_PCT", "0.35"))
MIN_ORDERBOOK_IMBALANCE = Decimal(os.getenv("MIN_ORDERBOOK_IMBALANCE", "1.08"))
MIN_VOLUME_RATIO = Decimal(os.getenv("MIN_VOLUME_RATIO", "1.15"))
MAX_ENTRY_GAP_FROM_CLOSED_PCT = Decimal(
    os.getenv("MAX_ENTRY_GAP_FROM_CLOSED_PCT", "1.20")
)
MIN_RESISTANCE_ROOM_PCT = Decimal(
    os.getenv("MIN_RESISTANCE_ROOM_PCT", "2.20")
)
MIN_TP1_PCT = Decimal(os.getenv("MIN_TP1_PCT", "2.00"))
MIN_NET_TP1_PCT = Decimal(os.getenv("MIN_NET_TP1_PCT", "1.40"))
MIN_RR = Decimal(os.getenv("MIN_RR", "1.80"))
TAKER_FEE_PCT = Decimal(os.getenv("PARIBU_TAKER_FEE_PCT", "0.28"))
EXPECTED_SLIPPAGE_PCT = Decimal(
    os.getenv("EXPECTED_SLIPPAGE_PCT", "0.15")
)

# Hard FOMO limits on 15m candles.
MAX_RETURN_3 = Decimal("3.00")
MAX_RETURN_12 = Decimal("8.00")
MAX_RETURN_48 = Decimal("16.00")

# Volatility bounds.
MIN_ATR_PCT = Decimal("0.20")
MAX_ATR_PCT = Decimal("5.00")
ATR_STOP_MULTIPLIER = Decimal("1.35")
MIN_RISK_PCT = Decimal("1.20")
MAX_RISK_PCT = Decimal("5.00")

# Multi-timeframe requirements.
MAX_1H_DISTANCE_FROM_EMA21_PCT = Decimal("4.00")
MAX_4H_DISTANCE_FROM_EMA50_PCT = Decimal("8.00")

CANDLE_LIMIT = max(205, int(os.getenv("CANDLE_LIMIT", "250")))
MAX_ORDERBOOK_MARKETS = max(
    10, int(os.getenv("MAX_ORDERBOOK_MARKETS", "80"))
)
MAX_TECHNICAL_MARKETS = max(
    5, int(os.getenv("MAX_TECHNICAL_MARKETS", "60"))
)
ORDERBOOK_DEPTH = max(
    5,
    min(int(os.getenv("ORDERBOOK_DEPTH", "20")), 20),
)
COOLDOWN_SECONDS = max(
    0,
    int(
        os.getenv(
            "SIGNAL_COOLDOWN_SECONDS",
            str(4 * 60 * 60),
        )
    ),
)

# Near-Miss:
# هذه الدرجة لا تسمح بإرسال BUY.
# هي فقط تحدد الفرص التي تستحق المتابعة والدراسة.
NEAR_MISS_MIN_SCORE = max(
    0,
    min(
        100,
        int(os.getenv("NEAR_MISS_MIN_SCORE", "70")),
    ),
)


# ---------------------------- data models ----------------------------


@dataclass
class ScanStats:
    total_markets: int = 0
    liquidity_pass: int = 0
    liquidity_fail: int = 0
    orderbook_pass: int = 0
    orderbook_fail: int = 0
    spread_pass: int = 0
    spread_fail: int = 0
    technical_attempted: int = 0
    candles_pass: int = 0
    candles_fail: int = 0
    indicator_pass: int = 0
    indicator_fail: int = 0
    btc_gate_pass: int = 0
    btc_gate_fail: int = 0
    mtf_pass: int = 0
    mtf_fail: int = 0
    setup_pass: int = 0
    setup_fail: int = 0
    score_pass: int = 0
    score_fail: int = 0
    execution_pass: int = 0
    execution_fail: int = 0
    final_validation_pass: int = 0
    final_validation_fail: int = 0
    execution_rejections: dict[str, int] | None = None
    reasons: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.execution_rejections is None:
            self.execution_rejections = {}
        if self.reasons is None:
            self.reasons = {}

    def reject_execution(self, reason: str) -> None:
        assert self.execution_rejections is not None
        self.execution_rejections[reason] = (
            self.execution_rejections.get(reason, 0) + 1
        )
        self.reject(f"Execution: {reason}")

    def reject(self, reason: str) -> None:
        assert self.reasons is not None
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


@dataclass(frozen=True)
class TradeLevels:
    entry: Decimal
    stop: Decimal
    tp1: Decimal
    tp2: Decimal
    rr: Decimal
    tp1_pct: Decimal
    net_tp1_pct: Decimal
    resistance: Optional[Decimal]
    risk_pct: Decimal


@dataclass(frozen=True)
class Opportunity:
    symbol: str
    score: int
    strength: str
    setup: str
    source: str
    entry: Decimal
    bid: Decimal
    ask: Decimal
    spread_pct: Decimal
    orderbook_imbalance: Decimal
    closed_price_15m: Decimal
    rsi_15m: Decimal
    atr_pct_15m: Decimal
    volume_ratio_15m: Decimal
    resistance: Optional[Decimal]
    stop: Decimal
    tp1: Decimal
    tp2: Decimal
    rr: Decimal
    tp1_pct: Decimal
    net_tp1_pct: Decimal
    reason: str
    close_timestamp_15m: int
    close_timestamp_1h: int
    close_timestamp_4h: int
    validation_passes: int


# ---------------------------- utilities ----------------------------


def dec(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def strength(score: int) -> str:
    if score >= 95:
        return "🔥 A+"
    if score >= 92:
        return "🟢 A"
    return "🟡 A-"


def price_step(price: Decimal) -> Decimal:
    if price >= 100:
        return Decimal("0.01")
    if price >= 1:
        return Decimal("0.0001")
    if price >= Decimal("0.01"):
        return Decimal("0.000001")
    if price >= Decimal("0.0001"):
        return Decimal("0.00000001")
    return Decimal("0.0000000001")


def fmt(price: Decimal) -> str:
    return format(price.quantize(price_step(price)), "f")


def pct(a: Decimal, b: Decimal) -> Decimal:
    return (a / b - Decimal("1")) * Decimal("100")


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"sent_signals": {}}

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            return {"sent_signals": {}}

        if not isinstance(data.get("sent_signals"), dict):
            data["sent_signals"] = {}

        return data

    except Exception as exc:
        LOGGER.warning("State load failed: %s", exc)
        return {"sent_signals": {}}


def save_state(state: dict[str, Any]) -> None:
    temporary = STATE_FILE.with_suffix(".tmp")

    try:
        temporary.write_text(
            json.dumps(
                state,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(STATE_FILE)

    except Exception as exc:
        LOGGER.error("State save failed: %s", exc)


def cooldown_allowed(
    symbol: str,
    state: dict[str, Any],
) -> bool:

    raw = state.setdefault(
        "sent_signals",
        {},
    ).get(symbol)

    if raw is None:
        return True

    try:
        return (
            time.time() - int(raw)
            >= COOLDOWN_SECONDS
        )
    except (TypeError, ValueError):
        return True


def send_telegram(message: str) -> bool:
    token = os.getenv(TELEGRAM_TOKEN_ENV)
    chat_id = os.getenv(TELEGRAM_CHAT_ENV)

    if not token or not chat_id:
        LOGGER.error("Telegram credentials are missing")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )

        if response.status_code != 200:
            LOGGER.error(
                "Telegram HTTP %s: %s",
                response.status_code,
                response.text[:400],
            )
            return False

        return True

    except requests.RequestException as exc:
        LOGGER.error(
            "Telegram request failed: %s",
            exc,
        )
        return False


# ---------------------------- scoring ----------------------------


def score_opportunity(
    tech: IndicatorResult,
    ticker: Ticker,
    book: OrderBookSnapshot,
    mtf_1h: IndicatorResult,
    mtf_4h: IndicatorResult,
) -> tuple[int, list[str], dict[str, int]]:

    """Return score plus a category-by-category breakdown for diagnostics."""

    score = 0
    reasons: list[str] = []

    breakdown = {
        "Trend": 0,
        "Momentum": 0,
        "Volume": 0,
        "Setup": 0,
        "Execution": 0,
    }

    # Trend alignment: 25
    if tech.is_uptrend:
        score += 15
        breakdown["Trend"] += 15
        reasons.append("15m اتجاه صاعد")

    elif (
        tech.is_above_ema21
        and tech.ema21 >= tech.ema50
        and tech.is_above_ema9
        and tech.macd_histogram > 0
    ):
        score += 10
        breakdown["Trend"] += 10
        reasons.append("15m بنية صعودية/استرداد")

    if mtf_1h.is_uptrend:
        score += 6
        breakdown["Trend"] += 6
        reasons.append("1h اتجاه صاعد")

    if mtf_4h.current_close > mtf_4h.ema50:
        score += 4
        breakdown["Trend"] += 4
        reasons.append("4h فوق EMA50")

    # Momentum: 20
    if Decimal("52") <= tech.rsi14 <= Decimal("64"):
        score += 12
        breakdown["Momentum"] += 12
        reasons.append("RSI 15m صحي")

    elif Decimal("49") <= tech.rsi14 < Decimal("52"):
        score += 8
        breakdown["Momentum"] += 8

    elif Decimal("64") < tech.rsi14 <= Decimal("68"):
        score += 7
        breakdown["Momentum"] += 7

    if (
        tech.macd_line > tech.macd_signal
        and tech.macd_histogram > 0
    ):
        score += 8
        breakdown["Momentum"] += 8
        reasons.append("MACD + Histogram داعمان")

    # Volume: 15
    if tech.volume_ratio >= Decimal("2.0"):
        score += 15
        breakdown["Volume"] += 15
        reasons.append("حجم قوي")

    elif tech.volume_ratio >= Decimal("1.5"):
        score += 12
        breakdown["Volume"] += 12
        reasons.append("حجم مرتفع")

    elif tech.volume_ratio >= MIN_VOLUME_RATIO:
        score += 9
        breakdown["Volume"] += 9
        reasons.append("حجم فوق المتوسط")

    # Setup: 15
    if tech.is_pullback:
        score += 10
        breakdown["Setup"] += 10
        reasons.append("Pullback منضبط")

    if tech.breakout:
        score += 5
        breakdown["Setup"] += 5
        reasons.append("Breakout مؤكد بالحجم")

    elif tech.is_bullish_candle:
        score += 3
        breakdown["Setup"] += 3
        reasons.append("شمعة مغلقة إيجابية")

    # Execution: 25
    if book.spread_percent <= Decimal("0.20"):
        score += 10
        breakdown["Execution"] += 10
        reasons.append("Spread Paribu ممتاز")

    elif book.spread_percent <= MAX_SPREAD_PCT:
        score += 7
        breakdown["Execution"] += 7

    if book.imbalance_ratio >= Decimal("1.30"):
        score += 10
        breakdown["Execution"] += 10
        reasons.append("دفتر الطلبات يميل للشراء")

    elif book.imbalance_ratio >= MIN_ORDERBOOK_IMBALANCE:
        score += 7
        breakdown["Execution"] += 7
        reasons.append("دفتر الطلبات مقبول")

    if (
        ticker.quote_volume is not None
        and ticker.quote_volume >= Decimal("10000000")
    ):
        score += 5
        breakdown["Execution"] += 5
        reasons.append("سيولة محلية قوية")

    elif (
        ticker.quote_volume is not None
        and ticker.quote_volume >= MIN_QUOTE_VOLUME_TL
    ):
        score += 3
        breakdown["Execution"] += 3

    return (
        max(0, min(score, 100)),
        reasons,
        breakdown,
    )


def format_score_diagnostic(
    score: int,
    breakdown: dict[str, int],
) -> str:

    parts = []

    maxima = {
        "Trend": 25,
        "Momentum": 20,
        "Volume": 15,
        "Setup": 15,
        "Execution": 25,
    }

    for name in (
        "Trend",
        "Momentum",
        "Volume",
        "Setup",
        "Execution",
    ):
        value = breakdown.get(name, 0)
        parts.append(
            f"{name} {value}/{maxima[name]}"
        )

    return (
        f"Score {score}/100 | "
        + " | ".join(parts)
    )


# ---------------------------- hard gates ----------------------------


BTC_15M_EMA_TOLERANCE_PCT = Decimal("0.35")
BTC_1H_EMA_TOLERANCE_PCT = Decimal("0.75")

BTC_MAX_3CANDLE_DROP_PCT = Decimal("-2.00")
BTC_MAX_12CANDLE_DROP_PCT = Decimal("-4.50")
BTC_MAX_48CANDLE_DROP_PCT = Decimal("-8.00")

BTC_MAX_15M_EMA21_DISTANCE_BEARISH_PCT = Decimal("-1.00")
BTC_MAX_1H_EMA21_DISTANCE_BEARISH_PCT = Decimal("-2.00")

BTC_HARD_BEAR_RSI = Decimal("38.00")
BTC_CAUTION_RSI = Decimal("45.00")


def _btc_regime(
    btc_15: IndicatorResult,
    btc_1h: IndicatorResult,
) -> tuple[bool, str]:

    """Adaptive BTC gate: block only on confirmed weakness."""

    if (
        btc_15.current_close <= 0
        or btc_15.ema21 <= 0
        or btc_1h.current_close <= 0
        or btc_1h.ema21 <= 0
    ):
        return False, "BTC بيانات المؤشر غير صالحة"

    btc_15_ema_distance = pct(
        btc_15.current_close,
        btc_15.ema21,
    )

    btc_1h_ema_distance = pct(
        btc_1h.current_close,
        btc_1h.ema21,
    )

    if btc_15.recent_return_3 <= BTC_MAX_3CANDLE_DROP_PCT:
        return (
            False,
            f"BTC هبوط قوي خلال 3 شموع: "
            f"{btc_15.recent_return_3:.2f}%",
        )

    if btc_15.recent_return_12 <= BTC_MAX_12CANDLE_DROP_PCT:
        return (
            False,
            f"BTC هبوط قوي خلال 12 شمعة: "
            f"{btc_15.recent_return_12:.2f}%",
        )

    if btc_15.recent_return_48 <= BTC_MAX_48CANDLE_DROP_PCT:
        return (
            False,
            f"BTC هبوط قوي خلال 48 شمعة: "
            f"{btc_15.recent_return_48:.2f}%",
        )

    if (
        btc_15_ema_distance
        <= BTC_MAX_15M_EMA21_DISTANCE_BEARISH_PCT
    ):
        return (
            False,
            f"BTC 15m تحت EMA21 بقوة: "
            f"{btc_15_ema_distance:.2f}%",
        )

    if (
        btc_1h_ema_distance
        <= BTC_MAX_1H_EMA21_DISTANCE_BEARISH_PCT
    ):
        return (
            False,
            f"BTC 1h تحت EMA21 بقوة: "
            f"{btc_1h_ema_distance:.2f}%",
        )

    if (
        btc_15.rsi14 < BTC_HARD_BEAR_RSI
        and btc_15.recent_return_3 < Decimal("0")
        and btc_15.current_close < btc_15.ema21
    ):
        return (
            False,
            f"BTC ضعف هبوطي مؤكد: "
            f"RSI={btc_15.rsi14:.1f} | "
            f"3C={btc_15.recent_return_3:.2f}%",
        )

    if (
        btc_15.is_uptrend
        and btc_1h.is_uptrend
        and btc_15.current_close >= btc_15.ema21
        and btc_1h.current_close >= btc_1h.ema21
    ):
        return (
            True,
            "BTC bullish — السماح الكامل بالفحص",
        )

    if (
        btc_15_ema_distance
        >= -BTC_15M_EMA_TOLERANCE_PCT
        and btc_1h_ema_distance
        >= -BTC_1H_EMA_TOLERANCE_PCT
    ):
        if btc_15.rsi14 < BTC_CAUTION_RSI:
            return (
                True,
                f"BTC neutral/cautious — "
                f"RSI={btc_15.rsi14:.1f} "
                f"— الفحص مسموح بحذر",
            )

        return (
            True,
            "BTC neutral/mixed — السماح بالفحص مع حماية",
        )

    if (
        btc_15_ema_distance
        > BTC_MAX_15M_EMA21_DISTANCE_BEARISH_PCT
        and btc_1h_ema_distance
        > BTC_MAX_1H_EMA21_DISTANCE_BEARISH_PCT
        and btc_15.rsi14 >= BTC_HARD_BEAR_RSI
    ):
        return (
            True,
            f"BTC mixed but acceptable — "
            f"RSI={btc_15.rsi14:.1f}",
        )

    return (
        False,
        "BTC regime ضعيف أكثر من الحد المسموح للحماية",
    )


def btc_gate() -> tuple[
    bool,
    Optional[IndicatorResult],
    str,
]:

    """Read BTC candles from Paribu and apply the adaptive regime gate."""

    try:
        btc_15_df = fetch_candles(
            "BTC_TL",
            "15m",
            CANDLE_LIMIT,
        )

        btc_1h_df = fetch_candles(
            "BTC_TL",
            "1h",
            CANDLE_LIMIT,
        )

        btc_15 = analyze_symbol(btc_15_df)
        btc_1h = analyze_symbol(btc_1h_df)

        if btc_15 is None or btc_1h is None:
            return (
                False,
                None,
                "BTC indicators unavailable",
            )

        source_15 = str(
            btc_15_df.attrs.get("source", "")
        ).upper()

        source_1h = str(
            btc_1h_df.attrs.get("source", "")
        ).upper()

        if source_15 != "PARIB
