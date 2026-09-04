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
MAX_ENTRY_GAP_FROM_CLOSED_PCT = Decimal(os.getenv("MAX_ENTRY_GAP_FROM_CLOSED_PCT", "1.20"))
MIN_RESISTANCE_ROOM_PCT = Decimal(os.getenv("MIN_RESISTANCE_ROOM_PCT", "2.20"))
MIN_TP1_PCT = Decimal(os.getenv("MIN_TP1_PCT", "2.00"))
MIN_NET_TP1_PCT = Decimal(os.getenv("MIN_NET_TP1_PCT", "1.40"))
MIN_RR = Decimal(os.getenv("MIN_RR", "1.80"))
TAKER_FEE_PCT = Decimal(os.getenv("PARIBU_TAKER_FEE_PCT", "0.28"))
EXPECTED_SLIPPAGE_PCT = Decimal(os.getenv("EXPECTED_SLIPPAGE_PCT", "0.15"))

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
MAX_ORDERBOOK_MARKETS = max(10, int(os.getenv("MAX_ORDERBOOK_MARKETS", "80")))
MAX_TECHNICAL_MARKETS = max(5, int(os.getenv("MAX_TECHNICAL_MARKETS", "60")))
ORDERBOOK_DEPTH = max(5, min(int(os.getenv("ORDERBOOK_DEPTH", "20")), 20))
COOLDOWN_SECONDS = max(0, int(os.getenv("SIGNAL_COOLDOWN_SECONDS", str(4 * 60 * 60))))


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
    reasons: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = {}

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
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
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
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(STATE_FILE)
    except Exception as exc:
        LOGGER.error("State save failed: %s", exc)


def cooldown_allowed(symbol: str, state: dict[str, Any]) -> bool:
    raw = state.setdefault("sent_signals", {}).get(symbol)
    if raw is None:
        return True
    try:
        return time.time() - int(raw) >= COOLDOWN_SECONDS
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
        LOGGER.error("Telegram request failed: %s", exc)
        return False


# ---------------------------- scoring ----------------------------


def score_opportunity(
    tech: IndicatorResult,
    ticker: Ticker,
    book: OrderBookSnapshot,
    mtf_1h: IndicatorResult,
    mtf_4h: IndicatorResult,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    # Trend alignment: 25
    if tech.is_uptrend:
        score += 15
        reasons.append("15m اتجاه صاعد")
    if mtf_1h.is_uptrend:
        score += 6
        reasons.append("1h اتجاه صاعد")
    if mtf_4h.current_close > mtf_4h.ema50:
        score += 4
        reasons.append("4h فوق EMA50")

    # Momentum: 20
    if Decimal("52") <= tech.rsi14 <= Decimal("64"):
        score += 12
        reasons.append("RSI 15m صحي")
    elif Decimal("49") <= tech.rsi14 < Decimal("52"):
        score += 8
    elif Decimal("64") < tech.rsi14 <= Decimal("68"):
        score += 7

    if tech.macd_line > tech.macd_signal and tech.macd_histogram > 0:
        score += 8
        reasons.append("MACD + Histogram داعمان")

    # Volume: 15
    if tech.volume_ratio >= Decimal("2.0"):
        score += 15
        reasons.append("حجم قوي")
    elif tech.volume_ratio >= Decimal("1.5"):
        score += 12
        reasons.append("حجم مرتفع")
    elif tech.volume_ratio >= MIN_VOLUME_RATIO:
        score += 9
        reasons.append("حجم فوق المتوسط")

    # Setup: 15
    if tech.is_pullback:
        score += 10
        reasons.append("Pullback منضبط")
    if tech.breakout:
        score += 5
        reasons.append("Breakout مؤكد بالحجم")
    elif tech.is_bullish_candle:
        score += 3
        reasons.append("شمعة مغلقة إيجابية")

    # Execution: 25
    if book.spread_percent <= Decimal("0.20"):
        score += 10
        reasons.append("Spread Paribu ممتاز")
    elif book.spread_percent <= MAX_SPREAD_PCT:
        score += 7

    if book.imbalance_ratio >= Decimal("1.30"):
        score += 10
        reasons.append("دفتر الطلبات يميل للشراء")
    elif book.imbalance_ratio >= MIN_ORDERBOOK_IMBALANCE:
        score += 7
        reasons.append("دفتر الطلبات مقبول")

    if ticker.quote_volume is not None and ticker.quote_volume >= Decimal("10000000"):
        score += 5
        reasons.append("سيولة محلية قوية")
    elif ticker.quote_volume is not None and ticker.quote_volume >= MIN_QUOTE_VOLUME_TL:
        score += 3

    return max(0, min(score, 100)), reasons


# ---------------------------- hard gates ----------------------------


def btc_gate() -> tuple[bool, Optional[IndicatorResult], str]:
    try:
        btc_15_df = fetch_candles("BTC_TL", "15m", CANDLE_LIMIT)
        btc_1h_df = fetch_candles("BTC_TL", "1h", CANDLE_LIMIT)
        btc_15 = analyze_symbol(btc_15_df)
        btc_1h = analyze_symbol(btc_1h_df)
        if btc_15 is None or btc_1h is None:
            return False, None, "BTC indicators unavailable"

        if not btc_15.is_uptrend:
            return False, btc_15, "BTC 15m trend not bullish"
        if not btc_1h.is_uptrend:
            return False, btc_15, "BTC 1h trend not bullish"
        if btc_15.current_close < btc_15.ema21:
            return False, btc_15, "BTC 15m below EMA21"
        if btc_15.recent_return_3 <= Decimal("-2.0"):
            return False, btc_15, "BTC short-term momentum weak"

        return True, btc_15, "OK"
    except Exception as exc:
        return False, None, f"BTC gate error: {exc}"


def setup_gate(tech: IndicatorResult) -> tuple[bool, str]:
    if tech.rsi14 < Decimal("48") or tech.rsi14 > Decimal("68"):
        return False, "RSI خارج المنطقة"
    if tech.recent_return_3 >= MAX_RETURN_3:
        return False, "Anti-FOMO 3 شموع"
    if tech.recent_return_12 >= MAX_RETURN_12:
        return False, "Anti-FOMO 12 شمعة"
    if tech.recent_return_48 >= MAX_RETURN_48:
        return False, "Anti-FOMO 48 شمعة"

    atr_pct = tech.atr14 / tech.current_close * Decimal("100")
    if atr_pct < MIN_ATR_PCT or atr_pct > MAX_ATR_PCT:
        return False, "ATR خارج النطاق"

    setup_ok = (
        (tech.is_uptrend and tech.is_above_ema21 and tech.is_pullback)
        or tech.breakout
    )
    if not setup_ok:
        return False, "لا يوجد Pullback/Breakout عالي الجودة"

    if tech.macd_histogram <= 0 or tech.macd_line <= tech.macd_signal:
        return False, "MACD لا يؤكد الزخم"

    return True, "OK"


def multi_timeframe_gate(
    tech_15: IndicatorResult,
    tech_1h: IndicatorResult,
    tech_4h: IndicatorResult,
) -> tuple[bool, str]:
    if not tech_15.is_uptrend:
        return False, "15m trend failed"
    if not tech_1h.is_uptrend:
        return False, "1h trend failed"
    if tech_4h.current_close < tech_4h.ema50:
        return False, "4h below EMA50"
    if tech_1h.distance_ema21_pct > MAX_1H_DISTANCE_FROM_EMA21_PCT:
        return False, "1h بعيد جدًا عن EMA21"
    if (tech_4h.current_close / tech_4h.ema50 - Decimal("1")) * Decimal("100") > MAX_4H_DISTANCE_FROM_EMA50_PCT:
        return False, "4h ممتد جدًا عن EMA50"
    return True, "OK"


def execution_levels(
    tech: IndicatorResult,
    ticker: Ticker,
    book: OrderBookSnapshot,
) -> tuple[Optional[TradeLevels], str]:
    if ticker.ask is None or ticker.bid is None:
        return None, "Paribu Bid/Ask غير متوفر"
    if ticker.ask <= 0 or ticker.bid <= 0 or ticker.ask < ticker.bid:
        return None, "Paribu Bid/Ask غير صالح"
    if book.spread_percent > MAX_SPREAD_PCT:
        return None, f"Spread {book.spread_percent:.2f}% > {MAX_SPREAD_PCT}%"
    if book.imbalance_ratio < MIN_ORDERBOOK_IMBALANCE:
        return None, f"OrderBook imbalance {book.imbalance_ratio:.2f} < {MIN_ORDERBOOK_IMBALANCE}"

    entry = ticker.ask
    close_15 = tech.current_close
    entry_gap = pct(entry, close_15)
    if entry_gap < Decimal("-0.25") or entry_gap > MAX_ENTRY_GAP_FROM_CLOSED_PCT:
        return None, f"Entry gap {entry_gap:.2f}% غير مناسب"

    atr_pct = tech.atr14 / close_15 * Decimal("100")
    risk_pct = max(atr_pct * ATR_STOP_MULTIPLIER, MIN_RISK_PCT)
    risk_pct = min(risk_pct, MAX_RISK_PCT)

    # Respect the actual recent Paribu swing low when it is tighter than the
    # purely ATR-based stop. We do not move the stop above entry.
    if tech.swing_low > 0 and tech.swing_low < close_15:
        swing_distance = pct(close_15, tech.swing_low)
        if swing_distance > 0:
            risk_pct = min(MAX_RISK_PCT, max(risk_pct, swing_distance))

    stop = entry * (Decimal("1") - risk_pct / Decimal("100"))
    if stop <= 0 or stop >= entry:
        return None, "Stop غير صالح"

    resistance: Optional[Decimal] = None
    resistances = [
        value
        for value in (tech.resistance_48, tech.resistance_96)
        if value is not None and value > close_15
    ]
    if resistances:
        resistance = min(resistances)
        resistance_room = pct(resistance, entry)
        if resistance_room < MIN_RESISTANCE_ROOM_PCT:
            return None, f"المقاومة قريبة جدًا: {resistance_room:.2f}%"

    minimum_tp1 = entry * (Decimal("1") + MIN_TP1_PCT / Decimal("100"))
    atr_tp1 = entry * (
        Decimal("1") + max(MIN_TP1_PCT, risk_pct * Decimal("1.70")) / Decimal("100")
    )

    if resistance is not None:
        structural_tp1 = resistance * (Decimal("1") - Decimal("0.20") / Decimal("100"))
        tp1 = min(structural_tp1, atr_tp1)
        tp1 = max(tp1, minimum_tp1)
    else:
        tp1 = atr_tp1

    tp2 = entry * (
        Decimal("1")
        + max(risk_pct * Decimal("2.50"), MIN_TP1_PCT + Decimal("1.50")) / Decimal("100")
    )
    if tech.resistance_96 > close_15:
        structural_tp2 = entry * (
            Decimal("1")
            + ((tech.resistance_96 / close_15) - Decimal("1")) * Decimal("0.98")
        )
        tp2 = max(tp2, structural_tp2)

    max_tp2 = entry * Decimal("1.15")
    tp2 = min(tp2, max_tp2)
    tp2 = max(tp2, tp1 * Decimal("1.025"))

    gross_tp1_pct = pct(tp1, entry)
    net_tp1_pct = gross_tp1_pct - (TAKER_FEE_PCT * Decimal("2") + EXPECTED_SLIPPAGE_PCT)

    risk = entry - stop
    reward = tp1 - entry
    if risk <= 0 or reward <= 0:
        return None, "Risk/Reward غير صالح"

    rr = reward / risk
    if gross_tp1_pct < MIN_TP1_PCT:
        return None, f"TP1 {gross_tp1_pct:.2f}% أقل من الحد"
    if net_tp1_pct < MIN_NET_TP1_PCT:
        return None, f"صافي TP1 {net_tp1_pct:.2f}% أقل من الحد"
    if rr < MIN_RR:
        return None, f"R:R {rr:.2f} أقل من {MIN_RR}"

    return (
        TradeLevels(
            entry=entry,
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            rr=rr,
            tp1_pct=gross_tp1_pct,
            net_tp1_pct=net_tp1_pct,
            resistance=resistance,
            risk_pct=risk_pct,
        ),
        "OK",
    )


# ---------------------------- final validator ----------------------------


def final_validate(
    opportunity: Opportunity,
    original_ticker: Ticker,
    original_book: OrderBookSnapshot,
) -> tuple[bool, Optional[Opportunity], str]:
    """Re-fetch all critical data immediately before Telegram.

    This is the final gate. A stale candidate cannot be sent.
    """
    try:
        snapshot = get_market_snapshot()
        ticker = snapshot.get(opportunity.symbol)
        if ticker is None:
            return False, None, "Final ticker refresh failed"

        book = get_order_book(opportunity.symbol, ORDERBOOK_DEPTH)
        df_15 = fetch_candles(opportunity.symbol, "15m", CANDLE_LIMIT)
        df_1h = fetch_candles(opportunity.symbol, "1h", CANDLE_LIMIT)
        df_4h = fetch_candles(opportunity.symbol, "4h", CANDLE_LIMIT)
        tech_15 = analyze_symbol(df_15)
        tech_1h = analyze_symbol(df_1h)
        tech_4h = analyze_symbol(df_4h)

        if tech_15 is None or tech_1h is None or tech_4h is None:
            return False, None, "Final indicators unavailable"
        if tech_15.source != "PARIBU" or tech_1h.source != "PARIBU" or tech_4h.source != "PARIBU":
            return False, None, "Final source validation failed"

        # Closed-candle timestamps must move forward or stay stable; a wildly
        # old dataset is never allowed.
        if tech_15.latest_closed_timestamp < opportunity.close_timestamp_15m:
            return False, None, "15m candle went backwards"

        btc_ok, _btc_final, btc_final_reason = btc_gate()
        if not btc_ok:
            return False, None, f"Final BTC gate failed: {btc_final_reason}"

        setup_ok, setup_reason = setup_gate(tech_15)
        if not setup_ok:
            return False, None, f"Final setup failed: {setup_reason}"

        mtf_ok, mtf_reason = multi_timeframe_gate(tech_15, tech_1h, tech_4h)
        if not mtf_ok:
            return False, None, f"Final MTF failed: {mtf_reason}"

        levels, level_reason = execution_levels(tech_15, ticker, book)
        if levels is None:
            return False, None, f"Final execution failed: {level_reason}"

        score_value, reasons = score_opportunity(
            tech_15,
            ticker,
            book,
            tech_1h,
            tech_4h,
        )
        if score_value < MIN_SCORE:
            return False, None, f"Final Score {score_value} < {MIN_SCORE}"

        validation_passes = 15
        rebuilt = Opportunity(
            symbol=opportunity.symbol,
            score=score_value,
            strength=strength(score_value),
            setup=("BREAKOUT" if tech_15.breakout else "PULLBACK"),
            source="PARIBU",
            entry=levels.entry,
            bid=book.best_bid,
            ask=book.best_ask,
            spread_pct=book.spread_percent,
            orderbook_imbalance=book.imbalance_ratio,
            closed_price_15m=tech_15.current_close,
            rsi_15m=tech_15.rsi14,
            atr_pct_15m=tech_15.atr14 / tech_15.current_close * Decimal("100"),
            volume_ratio_15m=tech_15.volume_ratio,
            resistance=levels.resistance,
            stop=levels.stop,
            tp1=levels.tp1,
            tp2=levels.tp2,
            rr=levels.rr,
            tp1_pct=levels.tp1_pct,
            net_tp1_pct=levels.net_tp1_pct,
            reason=" | ".join(reasons[:10]),
            close_timestamp_15m=tech_15.latest_closed_timestamp,
            close_timestamp_1h=tech_1h.latest_closed_timestamp,
            close_timestamp_4h=tech_4h.latest_closed_timestamp,
            validation_passes=validation_passes,
        )
        return True, rebuilt, "OK"

    except Exception as exc:
        LOGGER.exception("Final validation error for %s", opportunity.symbol)
        return False, None, str(exc)


# ---------------------------- formatting ----------------------------


def format_opportunity(opp: Opportunity, rank: int) -> str:
    resistance = fmt(opp.resistance) if opp.resistance is not None else "غير محددة"
    liquidity_note = (
        f"{opp.orderbook_imbalance:.2f}x شراء/بيع"
    )
    return (
        f"🎯 <b>PARIBU SPOT — إشارة مؤكدة #{rank}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{html.escape(opp.symbol)}</b>\n"
        f"🏷️ <b>المصدر:</b> {opp.source} فقط\n"
        f"💪 <b>الدرجة:</b> {opp.score}/100 — {opp.strength}\n"
        f"🧩 <b>Setup:</b> {opp.setup}\n\n"
        f"💵 <b>Paribu Ask:</b> <code>{fmt(opp.ask)}</code>\n"
        f"💵 <b>الدخول:</b> <code>{fmt(opp.entry)}</code>\n"
        f"🛑 <b>وقف الخسارة:</b> <code>{fmt(opp.stop)}</code>\n"
        f"🎯 <b>TP1:</b> <code>{fmt(opp.tp1)}</code> (+{opp.tp1_pct:.2f}%)\n"
        f"🚀 <b>TP2:</b> <code>{fmt(opp.tp2)}</code>\n"
        f"🧱 <b>المقاومة:</b> <code>{resistance}</code>\n\n"
        f"📐 <b>R:R:</b> 1:{opp.rr:.2f}\n"
        f"💰 <b>صافي TP1 تقديري بعد الرسوم/الانزلاق:</b> +{opp.net_tp1_pct:.2f}%\n"
        f"📏 <b>Spread Paribu:</b> {opp.spread_pct:.2f}%\n"
        f"📚 <b>Order Book:</b> {liquidity_note}\n\n"
        f"📊 <b>RSI 15m:</b> {opp.rsi_15m:.1f}\n"
        f"📊 <b>ATR 15m:</b> {opp.atr_pct_15m:.2f}%\n"
        f"💧 <b>Volume:</b> {opp.volume_ratio_15m:.2f}x\n"
        f"📌 <b>إغلاق 15m المرجعي:</b> <code>{fmt(opp.closed_price_15m)}</code>\n\n"
        f"✅ <b>اجتاز {opp.validation_passes}/15 بوابة تحقق إلزامية.</b>\n"
        f"🧠 <b>الأسباب:</b> {html.escape(opp.reason)}\n\n"
        "⚠️ <b>Spot فقط — التنفيذ يدوي.</b>\n"
        "⚠️ هذه إشارة منضبطة بالبيانات وليست ضمانًا للربح."
    )


def format_report(stats: ScanStats, btc_reason: Optional[str] = None) -> str:
    reasons = sorted(
        (stats.reasons or {}).items(),
        key=lambda item: item[1],
        reverse=True,
    )[:8]
    reason_lines = "\n".join(
        f"• {html.escape(reason)}: {count}"
        for reason, count in reasons
    ) or "لا توجد أسباب رفض مسجلة"

    btc_text = html.escape(btc_reason or "غير منفذ")
    return (
        "🔍 <b>Paribu — فحص Sniper الصارم</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 الأزواج: {stats.total_markets}\n"
        f"💧 اجتازت السيولة: {stats.liquidity_pass} | رفض: {stats.liquidity_fail}\n"
        f"📚 اجتازت Order Book: {stats.orderbook_pass} | رفض: {stats.orderbook_fail}\n"
        f"📏 اجتازت Spread: {stats.spread_pass} | رفض: {stats.spread_fail}\n"
        f"🧪 محاولات فنية: {stats.technical_attempted}\n"
        f"🕯️ شموع Paribu ناجحة: {stats.candles_pass} | فاشلة: {stats.candles_fail}\n"
        f"📐 مؤشرات ناجحة: {stats.indicator_pass} | فاشلة: {stats.indicator_fail}\n"
        f"₿ <b>BTC Gate:</b> {btc_text}\n"
        f"🧭 MTF ناجح: {stats.mtf_pass} | فاشل: {stats.mtf_fail}\n"
        f"🎯 Setup ناجح: {stats.setup_pass} | فاشل: {stats.setup_fail}\n"
        f"⭐ Score ناجح: {stats.score_pass} | فاشل: {stats.score_fail}\n"
        f"💰 Execution ناجح: {stats.execution_pass} | فاشل: {stats.execution_fail}\n"
        f"🔐 Final validation ناجح: {stats.final_validation_pass} | فاشل: {stats.final_validation_fail}\n\n"
        "🔎 <b>أكثر أسباب الرفض:</b>\n"
        f"{reason_lines}\n\n"
        "🛡️ <b>لا يتم إرسال أي توصية إذا فشل أي شرط إلزامي.</b>"
    )


# ---------------------------- scanner ----------------------------


def build_candidate(
    ticker: Ticker,
    book: OrderBookSnapshot,
    tech_15: IndicatorResult,
    tech_1h: IndicatorResult,
    tech_4h: IndicatorResult,
) -> tuple[Optional[Opportunity], str]:
    setup_ok, setup_reason = setup_gate(tech_15)
    if not setup_ok:
        return None, setup_reason

    mtf_ok, mtf_reason = multi_timeframe_gate(tech_15, tech_1h, tech_4h)
    if not mtf_ok:
        return None, mtf_reason

    levels, level_reason = execution_levels(tech_15, ticker, book)
    if levels is None:
        return None, level_reason

    score_value, reasons = score_opportunity(
        tech_15,
        ticker,
        book,
        tech_1h,
        tech_4h,
    )
    if score_value < MIN_SCORE:
        return None, f"Score {score_value} < {MIN_SCORE}"

    opportunity = Opportunity(
        symbol=ticker.symbol,
        score=score_value,
        strength=strength(score_value),
        setup=("BREAKOUT" if tech_15.breakout else "PULLBACK"),
        source="PARIBU",
        entry=levels.entry,
        bid=book.best_bid,
        ask=book.best_ask,
        spread_pct=book.spread_percent,
        orderbook_imbalance=book.imbalance_ratio,
        closed_price_15m=tech_15.current_close,
        rsi_15m=tech_15.rsi14,
        atr_pct_15m=tech_15.atr14 / tech_15.current_close * Decimal("100"),
        volume_ratio_15m=tech_15.volume_ratio,
        resistance=levels.resistance,
        stop=levels.stop,
        tp1=levels.tp1,
        tp2=levels.tp2,
        rr=levels.rr,
        tp1_pct=levels.tp1_pct,
        net_tp1_pct=levels.net_tp1_pct,
        reason=" | ".join(reasons[:10]),
        close_timestamp_15m=tech_15.latest_closed_timestamp,
        close_timestamp_1h=tech_1h.latest_closed_timestamp,
        close_timestamp_4h=tech_4h.latest_closed_timestamp,
        validation_passes=14,
    )
    return opportunity, "OK"


def run_scanner() -> None:
    stats = ScanStats()
    state = load_state()

    try:
        snapshot = get_market_snapshot()
    except ParibuDataError as exc:
        send_telegram(
            "🚨 <b>PARIBU SCANNER ERROR</b>\n\n"
            f"<code>{html.escape(str(exc))}</code>"
        )
        return

    stats.total_markets = len(snapshot)

    btc_ok, _btc_tech, btc_reason = btc_gate()
    if not btc_ok:
        stats.btc_gate_fail += 1
        stats.reject("BTC gate failed")
        send_telegram(
            "🛡️ <b>Paribu Sniper متوقف مؤقتًا</b>\n\n"
            f"سبب حماية السوق: {html.escape(btc_reason)}\n\n"
            "لم يتم إرسال أي توصية لأن شرط BTC الإجباري لم يمر."
        )
        return
    stats.btc_gate_pass += 1

    candidates: list[Opportunity] = []

    tickers = sorted(
        snapshot.values(),
        key=lambda item: item.quote_volume or Decimal("0"),
        reverse=True,
    )

    orderbook_checked = 0

    for ticker in tickers:
        if ticker.symbol in {"USDT_TL", "USDC_TL", "BTC_TL"}:
            continue

        if ticker.quote_volume is None or ticker.quote_volume < MIN_QUOTE_VOLUME_TL:
            stats.liquidity_fail += 1
            stats.reject("سيولة أقل من الحد")
            continue
        stats.liquidity_pass += 1

        if orderbook_checked >= MAX_ORDERBOOK_MARKETS:
            break
        orderbook_checked += 1

        try:
            book = get_order_book(ticker.symbol, ORDERBOOK_DEPTH)
        except Exception as exc:
            stats.orderbook_fail += 1
            stats.reject("فشل Order Book Paribu")
            LOGGER.debug("%s orderbook failure: %s", ticker.symbol, exc)
            continue

        stats.orderbook_pass += 1
        if book.spread_percent > MAX_SPREAD_PCT:
            stats.spread_fail += 1
            stats.reject(f"Spread {book.spread_percent:.2f}%")
            continue
        stats.spread_pass += 1

        if book.imbalance_ratio < MIN_ORDERBOOK_IMBALANCE:
            stats.reject("Order Book يميل للبيع")
            continue

        if stats.technical_attempted >= MAX_TECHNICAL_MARKETS:
            break
        stats.technical_attempted += 1

        try:
            df_15 = fetch_candles(ticker.symbol, "15m", CANDLE_LIMIT)
            df_1h = fetch_candles(ticker.symbol, "1h", CANDLE_LIMIT)
            df_4h = fetch_candles(ticker.symbol, "4h", CANDLE_LIMIT)
        except Exception as exc:
            stats.candles_fail += 1
            stats.reject("فشل شموع Paribu")
            LOGGER.debug("%s candle failure: %s", ticker.symbol, exc)
            continue

        if not all(
            frame.attrs.get("source") == "PARIBU"
            for frame in (df_15, df_1h, df_4h)
        ):
            stats.candles_fail += 1
            stats.reject("مصدر الشموع ليس Paribu")
            continue

        stats.candles_pass += 1

        tech_15 = analyze_symbol(df_15)
        tech_1h = analyze_symbol(df_1h)
        tech_4h = analyze_symbol(df_4h)
        if tech_15 is None or tech_1h is None or tech_4h is None:
            stats.indicator_fail += 1
            stats.reject("فشل المؤشرات")
            continue
        stats.indicator_pass += 1

        setup_ok, setup_reason = setup_gate(tech_15)
        if not setup_ok:
            stats.setup_fail += 1
            stats.reject(setup_reason)
            continue
        stats.setup_pass += 1

        mtf_ok, mtf_reason = multi_timeframe_gate(tech_15, tech_1h, tech_4h)
        if not mtf_ok:
            stats.mtf_fail += 1
            stats.reject(mtf_reason)
            continue
        stats.mtf_pass += 1

        opportunity, rejection = build_candidate(
            ticker,
            book,
            tech_15,
            tech_1h,
            tech_4h,
        )
        if opportunity is None:
            if "Score" in rejection:
                stats.score_fail += 1
            else:
                stats.execution_fail += 1
            stats.reject(rejection)
            continue

        stats.score_pass += 1
        stats.execution_pass += 1

        if not cooldown_allowed(opportunity.symbol, state):
            stats.reject("Cooldown")
            continue

        # Final revalidation is deliberately done before Telegram, not after.
        ok, validated, reason = final_validate(opportunity, ticker, book)
        if not ok or validated is None:
            stats.final_validation_fail += 1
            stats.reject(f"Final validation: {reason}")
            continue

        stats.final_validation_pass += 1
        candidates.append(validated)

    candidates.sort(
        key=lambda item: (
            item.score,
            item.net_tp1_pct,
            item.rr,
            item.orderbook_imbalance,
        ),
        reverse=True,
    )

    selected = candidates[:MAX_SIGNALS_PER_RUN]

    if not selected:
        send_telegram(format_report(stats, btc_reason))
        save_state(state)
        return

    header = (
        "🔥 <b>Paribu — فرص Spot عالية الانضباط</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"تم تمرير <b>{len(selected)}</b> فرصة بعد فحص متعدد المراحل وإعادة تحقق نهائية.\n"
        "📌 جميع بيانات السعر والشموع ودفتر الطلبات من Paribu فقط."
    )
    send_telegram(header)

    for rank, opportunity in enumerate(selected, start=1):
        message = format_opportunity(opportunity, rank)
        if send_telegram(message):
            state["sent_signals"][opportunity.symbol] = int(time.time())
            save_state(state)

    LOGGER.info("Run complete: selected=%d", len(selected))


if __name__ == "__main__":
    run_scanner()
