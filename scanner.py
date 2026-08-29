from __future__ import annotations

import html
import json
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import requests

from market_data import (
    ParibuDataError,
    Ticker,
    fetch_candles,
    get_market_snapshot,
    get_effective_spread,
)

from indicator_engine import calculate_indicators


# ============================================================
# Paribu Spot Scanner
# ============================================================
# IMPORTANT:
# - This scanner NEVER executes trades.
# - Paribu is the source of truth for executable TL pricing.
# - Technical candles come from market_data.py.
# - No KuCoin order book is used to judge a Paribu trade.
# - The scanner uses Paribu Bid/Ask for the local spread.
# ============================================================


STATE_FILE = Path(
    os.getenv("SCANNER_STATE_FILE", "scanner_state.json")
)

MAX_SIGNALS_PER_RUN = int(
    os.getenv("MAX_SIGNALS_PER_RUN", "10")
)

MIN_SCORE = int(
    os.getenv("MIN_SCORE", "75")
)

MIN_QUOTE_VOLUME_TL = Decimal(
    os.getenv("MIN_QUOTE_VOLUME_TL", "500000")
)

# ✨ تم خفض الحد الأقصى للسبريد إلى 0.30% لضمان تنفيذ أفضل
MAX_ALLOWED_SPREAD_PCT = Decimal(
    os.getenv("MAX_ALLOWED_SPREAD_PCT", "0.30")
)

MAX_EFFECTIVE_SPREAD_PCT = Decimal(
    os.getenv("MAX_EFFECTIVE_SPREAD_PCT", "2.00")
)

MIN_REQUIRED_TP1_PCT = Decimal(
    os.getenv("MIN_REQUIRED_TP1_PCT", "1.80")
)

MIN_NET_TP1_PCT = Decimal(
    os.getenv("MIN_NET_TP1_PCT", "1.20")
)

MIN_REQUIRED_RR = Decimal(
    os.getenv("MIN_REQUIRED_RR", "1.70")
)

# Minimum TL volume required in the order book for depth validation
MIN_ORDERBOOK_DEPTH_TL = Decimal(
    os.getenv("MIN_ORDERBOOK_DEPTH_TL", "2000")
)

# Default Paribu taker assumption. Change via environment variable
# to match the user's actual fee tier.
TAKER_FEE_PCT = Decimal(
    os.getenv("PARIBU_TAKER_FEE_PCT", "0.28")
)

COOLDOWN_SECONDS = int(
    os.getenv("SIGNAL_COOLDOWN_SECONDS", str(4 * 60 * 60))
)

CANDLE_LIMIT = int(
    os.getenv("CANDLE_LIMIT", "250")
)

MAX_TECHNICAL_MARKETS = int(
    os.getenv("MAX_TECHNICAL_MARKETS", "100")
)

REQUEST_TIMEOUT = int(
    os.getenv("TELEGRAM_TIMEOUT", "15")
)

# ✨ NEW: Minimum volume ratio (current volume vs average volume)
MIN_VOLUME_RATIO = Decimal(
    os.getenv("MIN_VOLUME_RATIO", "0.5")
)

LOGGER = logging.getLogger("spot_scanner")

if not LOGGER.handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class ExecutionResult:
    is_executable: bool
    reject_reason: Optional[str]
    entry_price: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    rr_ratio: Decimal
    spread_pct: Decimal
    tp1_pct: Decimal
    net_tp1_pct: Decimal


@dataclass
class ScanStats:
    total_markets: int = 0
    liquidity_pass: int = 0
    liquidity_fail: int = 0
    spread_pass: int = 0
    spread_fail: int = 0
    depth_pass: int = 0
    depth_fail: int = 0
    technical_attempted: int = 0
    candle_success: int = 0
    candle_fail: int = 0
    indicator_success: int = 0
    indicator_fail: int = 0
    execution_attempted: int = 0
    execution_pass: int = 0
    execution_fail: int = 0
    score_fail: int = 0
    tp1_fail: int = 0
    rr_fail: int = 0
    other_execution_fail: int = 0
    candidates_before_ranking: int = 0
    volume_ratio_fail: int = 0          # ✨ NEW
    reject_reasons: dict[str, int] = field(default_factory=dict)

    def add_reason(self, reason: str) -> None:
        key = reason.strip() or "Unknown"
        self.reject_reasons[key] = self.reject_reasons.get(key, 0) + 1


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
    reason: str


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        LOGGER.error("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            LOGGER.error(
                "Telegram HTTP %s: %s",
                response.status_code,
                response.text[:300],
            )
            return False
        return True
    except requests.RequestException as exc:
        LOGGER.error("Telegram error: %s", exc)
        return False


# ============================================================
# STATE / COOLDOWN
# ============================================================

def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"sent_signals": {}}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except Exception as exc:
        LOGGER.warning("Could not load state: %s", exc)
        return {"sent_signals": {}}

    if not isinstance(state, dict):
        return {"sent_signals": {}}

    sent = state.get("sent_signals")
    if not isinstance(sent, dict):
        state["sent_signals"] = {}

    return state


def save_state(state: dict[str, Any]) -> None:
    temporary = STATE_FILE.with_suffix(".tmp")

    try:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(
                state,
                file,
                indent=2,
                ensure_ascii=False,
            )
        temporary.replace(STATE_FILE)
    except Exception as exc:
        LOGGER.error("Could not save state: %s", exc)


def cooldown_allowed(symbol: str, state: dict[str, Any]) -> bool:
    sent = state.setdefault("sent_signals", {})
    value = sent.get(symbol)

    if value is None:
        return True

    try:
        last_time = int(value)
    except (TypeError, ValueError):
        return True

    return (time.time() - last_time) >= COOLDOWN_SECONDS


# ============================================================
# INDICATOR COMPATIBILITY
# ============================================================

def _to_decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    if value is None:
        return default
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            return default
        return result
    except (InvalidOperation, ValueError, TypeError):
        return default


def indicator_to_dict(result: Any, df: Any) -> Optional[dict[str, Any]]:
    if result is None:
        return None

    if isinstance(result, dict):
        data = dict(result)
        if "close" not in data and "current_close" in data:
            data["close"] = data["current_close"]
        if "rsi" not in data and "rsi14" in data:
            data["rsi"] = data["rsi14"]
        if "atr" not in data and "atr14" in data:
            data["atr"] = data["atr14"]
        if "volume_ratio" not in data:
            data["volume_ratio"] = data.get("vol_ratio", 0)
        data.setdefault("is_valid_setup", True)
        return data

    if hasattr(result, "current_close"):
        data = {
            "close": getattr(result, "current_close", None),
            "rsi": getattr(result, "rsi14", None),
            "atr": getattr(result, "atr14", None),
            "volume_ratio": getattr(result, "volume_ratio", None),
            "is_uptrend": getattr(result, "is_uptrend", False),
            "is_above_ema21": getattr(result, "is_above_ema21", False),
            "pullback": getattr(result, "is_pullback", False),
            "breakout": getattr(result, "breakout", False),
            "bullish_candle": getattr(result, "is_bullish_candle", False),
            "macd_bullish": False,
            "reason": "technical confirmation",
            "is_valid_setup": getattr(result, "valid", True),
            "data_source": getattr(df, "attrs", {}).get("source", "unknown"),
        }

        macd_line = _to_decimal(getattr(result, "macd_line", None))
        macd_signal = _to_decimal(getattr(result, "macd_signal", None))
        if macd_line is not None and macd_signal is not None:
            data["macd_bullish"] = macd_line > macd_signal

        return data

    try:
        import pandas as pd

        if isinstance(result, pd.DataFrame):
            if len(result) < 2:
                return None

            row = result.iloc[-2]

            def get_col(*names: str, default: Any = None) -> Any:
                for name in names:
                    if name in row.index:
                        return row[name]
                return default

            close = _to_decimal(get_col("close", "Close"))
            rsi = _to_decimal(get_col("RSI14", "RSI_14", default=50))
            atr = _to_decimal(get_col("ATR14", "ATR_14", default=0))
            volume = _to_decimal(get_col("volume", default=0))
            vol_sma = _to_decimal(get_col("VOL_SMA20", "VOL_SMA_20", default=0))
            ema21 = _to_decimal(get_col("EMA21", "EMA_21", default=close or 0))
            ema50 = _to_decimal(get_col("EMA50", "EMA_50", default=close or 0))
            ema200 = _to_decimal(get_col("EMA200", "EMA_200", default=close or 0))
            macd = _to_decimal(get_col("MACD", "MACD_line", default=0))
            macd_signal = _to_decimal(get_col("MACD_SIGNAL", "MACD_signal", default=0))

            if close is None or close <= 0 or atr is None or atr <= 0:
                return None

            volume_ratio = (
                volume / vol_sma
                if volume is not None and vol_sma is not None and vol_sma > 0
                else Decimal("0")
            )

            low_window = result["low"].iloc[-5:-1]
            recent_low = _to_decimal(low_window.min()) if len(low_window) else None

            return {
                "close": close,
                "rsi": rsi or Decimal("50"),
                "atr": atr,
                "volume_ratio": volume_ratio,
                "is_uptrend": bool(close > (ema50 or close) > (ema200 or close)),
                "is_above_ema21": bool(close > (ema21 or close)),
                "pullback": bool(
                    recent_low is not None
                    and ema21 is not None
                    and recent_low <= ema21
                    and close > ema21
                ),
                "breakout": False,
                "bullish_candle": bool(
                    close > _to_decimal(get_col("open", default=close))
                ),
                "macd_bullish": bool(
                    macd is not None
                    and macd_signal is not None
                    and macd > macd_signal
                ),
                "reason": "technical confirmation",
                "is_valid_setup": True,
                "data_source": getattr(df, "attrs", {}).get("source", "unknown"),
            }
    except Exception as exc:
        LOGGER.debug("Indicator DataFrame normalization failed: %s", exc)

    return None


# ============================================================
# SCORE
# ============================================================

def score_candidate(
    indicators: dict[str, Any],
    ticker: Ticker,
) -> tuple[int, str, list[str]]:

    score = 0
    reasons: list[str] = []

    rsi = _to_decimal(indicators.get("rsi"), Decimal("50")) or Decimal("50")
    volume_ratio = _to_decimal(
        indicators.get("volume_ratio"), Decimal("0")
    ) or Decimal("0")

    is_uptrend = bool(indicators.get("is_uptrend", False))
    above_ema21 = bool(indicators.get("is_above_ema21", False))
    pullback = bool(indicators.get("pullback", False))
    breakout = bool(indicators.get("breakout", False))
    bullish_candle = bool(indicators.get("bullish_candle", False))
    macd_bullish = bool(indicators.get("macd_bullish", False))

    if is_uptrend:
        score += 15
        reasons.append("اتجاه صاعد")
    if above_ema21:
        score += 10
        reasons.append("فوق EMA21")

    if Decimal("52") <= rsi <= Decimal("66"):
        score += 15
        reasons.append("RSI صحي")
    elif Decimal("48") <= rsi < Decimal("52"):
        score += 9
    elif Decimal("66") < rsi <= Decimal("70"):
        score += 8

    if macd_bullish:
        score += 10
        reasons.append("MACD داعم")

    if volume_ratio >= Decimal("2"):
        score += 15
        reasons.append("حجم قوي")
    elif volume_ratio >= Decimal("1.5"):
        score += 12
        reasons.append("حجم مرتفع")
    elif volume_ratio >= Decimal("1.15"):
        score += 8
    elif volume_ratio >= Decimal("1"):
        score += 5

    if pullback:
        score += 10
        reasons.append("Pullback")
    if breakout:
        score += 5
        reasons.append("Breakout")
    elif bullish_candle:
        score += 3
        reasons.append("شمعة إيجابية")

    spread = ticker.spread_percent
    if spread is not None:
        if spread <= Decimal("0.30"):
            score += 10
            reasons.append("سبريد محلي ممتاز")
        elif spread <= Decimal("0.50"):
            score += 7
        elif spread <= Decimal("0.80"):
            score += 4

    volume_tl = ticker.quote_volume or Decimal("0")
    if volume_tl >= Decimal("3000000"):
        score += 10
    elif volume_tl >= Decimal("1500000"):
        score += 7
    elif volume_tl >= Decimal("750000"):
        score += 4

    score = min(max(score, 0), 100)

    if score >= 90:
        strength = "🔥 EXCEPTIONAL"
    elif score >= 85:
        strength = "🟢 STRONG"
    elif score >= 78:
        strength = "🟡 GOOD"
    else:
        strength = "🔵 WATCH"

    return score, strength, reasons


# ============================================================
# EXECUTION (DIRECT PARIBU LEVELS)
# ============================================================

def evaluate_trade(
    ticker: Ticker,
    indicators: dict[str, Any],
) -> ExecutionResult:
    """
    Evaluates the execution purely on Paribu prices.
    Takes ATR percentage from the candle source (e.g. Binance)
    and applies it to Paribu Ask to build precise local levels.
    """
    
    if ticker.bid is None or ticker.ask is None or ticker.ask <= 0:
        return ExecutionResult(
            False, "بيانات Paribu Bid/Ask غير متوفرة",
            ticker.last, Decimal("0"), Decimal("0"), Decimal("0"),
            Decimal("0"), Decimal("99"), Decimal("0"), Decimal("0")
        )

    entry = ticker.ask
    spread = ticker.spread_percent

    if spread is None:
        return ExecutionResult(
            False, "السبريد المحلي غير متوفر", entry,
            Decimal("0"), Decimal("0"), Decimal("0"),
            Decimal("0"), Decimal("99"), Decimal("0"), Decimal("0")
        )

    if spread > MAX_ALLOWED_SPREAD_PCT:
        return ExecutionResult(
            False, f"Spread {spread:.2f}% > {MAX_ALLOWED_SPREAD_PCT}%", entry,
            Decimal("0"), Decimal("0"), Decimal("0"),
            Decimal("0"), spread, Decimal("0"), Decimal("0")
        )

    close = _to_decimal(indicators.get("close"))
    atr = _to_decimal(indicators.get("atr"))

    if close is None or close <= 0 or atr is None or atr <= 0:
        return ExecutionResult(
            False, "بيانات الشموع المرجعية غير صالحة", entry,
            Decimal("0"), Decimal("0"), Decimal("0"),
            Decimal("0"), spread, Decimal("0"), Decimal("0")
        )

    # Calculate ATR as a percentage from the global source
    atr_pct = atr / close

    # Calculate local risk based on ATR% (min 1.2%, max 6.0%)
    risk_pct = max(
        Decimal("0.012"),
        min(atr_pct * Decimal("1.35"), Decimal("0.06"))
    )

    risk_amount = entry * risk_pct
    stop_loss = entry - risk_amount

    if stop_loss <= 0:
        return ExecutionResult(
            False, "حساب وقف الخسارة غير صالح", entry,
            Decimal("0"), Decimal("0"), Decimal("0"),
            Decimal("0"), spread, Decimal("0"), Decimal("0")
        )

    tp1 = entry + (risk_amount * Decimal("1.60"))
    tp2 = entry + (risk_amount * Decimal("2.40"))

    tp1_pct = (tp1 - entry) / entry * Decimal("100")

    if tp1_pct < MIN_REQUIRED_TP1_PCT:
        return ExecutionResult(
            False, f"TP1 فقط {tp1_pct:.2f}% < {MIN_REQUIRED_TP1_PCT}%", entry,
            stop_loss, tp1, tp2, Decimal("0"), spread, tp1_pct, Decimal("0")
        )

    # Cost = 2 * Taker Fee + half of the spread (assuming limit on sell if possible, or market hit)
    estimated_round_trip_cost = (TAKER_FEE_PCT * Decimal("2")) + (spread / Decimal("2"))
    net_tp1_pct = tp1_pct - estimated_round_trip_cost

    if net_tp1_pct < MIN_NET_TP1_PCT:
        return ExecutionResult(
            False, f"صافي TP1 {net_tp1_pct:.2f}% منخفض", entry,
            stop_loss, tp1, tp2, Decimal("0"), spread, tp1_pct, net_tp1_pct
        )

    risk_dist = entry - stop_loss
    reward_dist = tp1 - entry

    if risk_dist <= 0 or reward_dist <= 0:
        return ExecutionResult(
            False, "مسافة المخاطرة/المكافأة غير صالحة", entry,
            stop_loss, tp1, tp2, Decimal("0"), spread, tp1_pct, net_tp1_pct
        )

    rr = reward_dist / risk_dist

    if rr < MIN_REQUIRED_RR:
        return ExecutionResult(
            False, f"R:R {rr:.2f} < {MIN_REQUIRED_RR}", entry,
            stop_loss, tp1, tp2, rr, spread, tp1_pct, net_tp1_pct
        )

    return ExecutionResult(
        True, None, entry, stop_loss, tp1, tp2, rr, spread, tp1_pct, net_tp1_pct
    )


# ============================================================
# FORMATTING
# ============================================================

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


def fmt_price(price: Decimal) -> str:
    return format(
        price.quantize(price_step(price)),
        "f",
    )


def format_signal_message(
    opportunity: Opportunity,
    rank: int,
) -> str:

    return (
        f"🎯 <b>SPOT ENTRY #{rank}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{html.escape(opportunity.symbol)}</b>\n"
        f"💪 <b>القوة:</b> {opportunity.strength}\n"
        f"📊 <b>Score:</b> {opportunity.score}/100\n"
        f"🧩 <b>Setup:</b> {opportunity.setup}\n"
        f"📡 <b>مصدر الشموع:</b> {html.escape(opportunity.data_source)}\n\n"
        f"💵 <b>سعر الشراء (Ask):</b> <code>{fmt_price(opportunity.entry_price)}</code>\n"
        f"🛑 <b>وقف الخسارة:</b> <code>{fmt_price(opportunity.stop_loss)}</code>\n"
        f"🎯 <b>TP1:</b> <code>{fmt_price(opportunity.tp1)}</code> (+{opportunity.tp1_pct}%)\n"
        f"🚀 <b>TP2:</b> <code>{fmt_price(opportunity.tp2)}</code>\n"
        f"📐 <b>R:R:</b> 1:{opportunity.rr}\n"
        f"💰 <b>صافي TP1 (بعد الرسوم):</b> +{opportunity.net_tp1_pct}%\n"
        f"📏 <b>السبريد:</b> {opportunity.spread_pct}%\n\n"
        f"📈 <b>RSI:</b> {opportunity.rsi}\n"
        f"📊 <b>ATR:</b> {opportunity.atr_pct}%\n"
        f"💧 <b>حجم التداول:</b> {opportunity.volume_ratio}x\n"
        f"🧠 <b>السبب:</b> {html.escape(opportunity.reason)}\n\n"
        "⚠️ <b>Spot فقط — التنفيذ يدوي، ولا يوجد تنفيذ تلقائي.</b>"
    )


def format_no_signal_report(stats: ScanStats) -> str:
    reasons = ""
    if stats.reject_reasons:
        top = sorted(
            stats.reject_reasons.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        reasons = "\n".join(
            f"• {html.escape(reason)}: {count}"
            for reason, count in top
        )

    if not reasons:
        reasons = "• لم تصل أي عملة إلى مرحلة التنفيذ."

    return (
        "🔍 <b>Paribu — التقرير الدوري</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 الأزواج: {stats.total_markets}\n"
        f"💧 اجتازت السيولة: {stats.liquidity_pass}\n"
        f"❌ رفض السيولة: {stats.liquidity_fail}\n"
        f"📏 اجتازت السبريد: {stats.spread_pass}\n"
        f"❌ رفض السبريد: {stats.spread_fail}\n"
        f"🧱 اجتازت عمق الدفتر: {stats.depth_pass}\n"
        f"❌ رفض عمق الدفتر: {stats.depth_fail}\n"
        f"🧪 المحاولات الفنية: {stats.technical_attempted}\n"
        f"🕯️ نجاح الشموع: {stats.candle_success}\n"
        f"❌ أخطاء الشموع: {stats.candle_fail}\n"
        f"📐 نجاح المؤشرات: {stats.indicator_success}\n"
        f"❌ فشل المؤشرات: {stats.indicator_fail}\n"
        f"⚙️ محاولات التنفيذ: {stats.execution_attempted}\n"
        f"✅ تنفيذ مقبول: {stats.execution_pass}\n"
        f"❌ تنفيذ مرفوض: {stats.execution_fail}\n"
        f"⭐ فشل Score: {stats.score_fail}\n"
        f"🎯 فشل TP1: {stats.tp1_fail}\n"
        f"📐 فشل R:R: {stats.rr_fail}\n"
        f"📉 فشل حجم التداول: {stats.volume_ratio_fail}\n"    # ✨ NEW
        f"⚠️ رفض تنفيذ آخر: {stats.other_execution_fail}\n\n"
        "🔎 <b>أكثر أسباب الرفض:</b>\n"
        f"{reasons}\n\n"
        "💡 لا يتم إجبار النظام على إعطاء صفقة ضعيفة."
    )


# ============================================================
# SCAN
# ============================================================

def run_scanner() -> None:
    LOGGER.info("Starting Paribu Spot Scanner...")

    try:
        snapshot = get_market_snapshot()
    except ParibuDataError as exc:
        LOGGER.error("Could not fetch Paribu market data: %s", exc)
        send_telegram_message(
            "🚨 <b>SCANNER ERROR</b>\n\n"
            + html.escape(str(exc))
        )
        return

    stats = ScanStats(
        total_markets=len(snapshot)
    )

    state = load_state()
    candidates: list[Opportunity] = []

    tickers = sorted(
        snapshot.values(),
        key=lambda ticker: (
            ticker.quote_volume
            if ticker.quote_volume is not None
            else Decimal("0")
        ),
        reverse=True,
    )

    for ticker in tickers:
        if (
            ticker.quote_volume is None
            or ticker.quote_volume < MIN_QUOTE_VOLUME_TL
        ):
            stats.liquidity_fail += 1
            continue

        stats.liquidity_pass += 1

        if ticker.spread_percent is None:
            stats.spread_fail += 1
            stats.add_reason("Bid/Ask غير متوفر")
            continue

        if ticker.spread_percent > MAX_ALLOWED_SPREAD_PCT:
            stats.spread_fail += 1
            stats.add_reason(
                f"Spread {ticker.spread_percent:.2f}%"
            )
            continue

        stats.spread_pass += 1

        # Check effective order book depth and spread with safe fallback
        effective_spread, eff_ask, eff_bid = None, None, None
        try:
            effective_spread, eff_ask, eff_bid = get_effective_spread(
                ticker.symbol, min_volume_tl=MIN_ORDERBOOK_DEPTH_TL
            )
        except Exception as exc:
            LOGGER.debug("get_effective_spread exception for %s: %s", ticker.symbol, exc)

        # Fallback mechanism if orderbook data is unavailable
        if effective_spread is None:
            base_spread = ticker.spread_percent or Decimal("0.50")
            effective_spread = base_spread + Decimal("0.35")
            LOGGER.debug("Using fallback effective spread for %s: %s%%", ticker.symbol, effective_spread)

        if effective_spread > MAX_EFFECTIVE_SPREAD_PCT:
            stats.depth_fail += 1
            stats.add_reason("عمق دفتر الأوامر غير كافٍ أو السبريد الفعلي مرتفع")
            continue

        stats.depth_pass += 1

        if stats.technical_attempted >= MAX_TECHNICAL_MARKETS:
            break

        stats.technical_attempted += 1

        try:
            df = fetch_candles(
                ticker.symbol,
                resolution="15m",
                limit=CANDLE_LIMIT,
            )
            stats.candle_success += 1
        except Exception as exc:
            stats.candle_fail += 1
            stats.add_reason("فشل جلب الشموع")
            LOGGER.debug("Candle failure %s: %s", ticker.symbol, exc)
            continue

        try:
            raw_indicators = calculate_indicators(df)
            indicators = indicator_to_dict(
                raw_indicators,
                df,
            )
        except Exception as exc:
            stats.indicator_fail += 1
            stats.add_reason("فشل حساب المؤشرات")
            LOGGER.debug("Indicator error %s: %s", ticker.symbol, exc)
            continue

        if indicators is None:
            stats.indicator_fail += 1
            stats.add_reason("مخرجات المؤشرات غير صالحة")
            continue

        if not indicators.get("is_valid_setup", True):
            stats.indicator_fail += 1
            stats.add_reason("Setup المؤشرات غير صالح")
            continue

        stats.indicator_success += 1

        # ✨ NEW: Volume Ratio filter
        volume_ratio = _to_decimal(indicators.get("volume_ratio"), Decimal("0")) or Decimal("0")
        if volume_ratio < MIN_VOLUME_RATIO:
            stats.volume_ratio_fail += 1
            stats.add_reason(f"Volume ratio {volume_ratio:.2f}x < {MIN_VOLUME_RATIO}")
            continue

        rsi = _to_decimal(indicators.get("rsi"), Decimal("50")) or Decimal("50")

        if rsi >= Decimal("75"):
            stats.add_reason("RSI مرتفع")
            continue

        is_uptrend = bool(indicators.get("is_uptrend", False))
        above_ema21 = bool(indicators.get("is_above_ema21", False))
        pullback = bool(indicators.get("pullback", False))
        breakout = bool(indicators.get("breakout", False))
        bullish_candle = bool(indicators.get("bullish_candle", False))

        if not (
            (is_uptrend and above_ema21)
            or pullback
            or breakout
            or (bullish_candle and above_ema21)
        ):
            stats.add_reason("Setup غير واضح")
            continue

        score, strength, reasons = score_candidate(indicators, ticker)

        if score < MIN_SCORE:
            stats.score_fail += 1
            stats.add_reason(f"Score {score} < {MIN_SCORE}")
            continue

        stats.execution_attempted += 1

        execution = evaluate_trade(
            ticker=ticker,
            indicators=indicators,
        )

        if not execution.is_executable:
            stats.execution_fail += 1
            reason = execution.reject_reason or "سبب غير معروف"
            stats.add_reason(reason)

            if reason.startswith("TP1"):
                stats.tp1_fail += 1
            elif "R:R" in reason:
                stats.rr_fail += 1
            else:
                stats.other_execution_fail += 1

            LOGGER.info("Rejected %s: %s", ticker.symbol, reason)
            continue

        stats.execution_pass += 1
        stats.candidates_before_ranking += 1

        if not cooldown_allowed(ticker.symbol, state):
            stats.add_reason("Cooldown")
            continue

        atr = _to_decimal(indicators.get("atr"), Decimal("0")) or Decimal("0")
        reference_close = _to_decimal(indicators.get("close"), ticker.last) or ticker.last

        atr_pct = (
            atr / reference_close * Decimal("100")
            if reference_close > 0 and atr > 0
            else Decimal("0")
        )

        setup = "BREAKOUT" if breakout else "PULLBACK" if pullback else "MOMENTUM"
        data_source = str(indicators.get("data_source", df.attrs.get("source", "unknown"))).upper()
        reason_text = " | ".join(reasons[:8]) if reasons else "technical confirmation"

        candidates.append(
            Opportunity(
                symbol=ticker.symbol,
                score=score,
                strength=strength,
                setup=setup,
                data_source=data_source,
                current_price=ticker.last,
                paribu_bid=ticker.bid,
                paribu_ask=ticker.ask,
                spread_pct=execution.spread_pct.quantize(Decimal("0.01")),
                entry_price=execution.entry_price,
                stop_loss=execution.stop_loss,
                tp1=execution.tp1,
                tp2=execution.tp2,
                rr=execution.rr_ratio.quantize(Decimal("0.01")),
                tp1_pct=execution.tp1_pct.quantize(Decimal("0.01")),
                net_tp1_pct=execution.net_tp1_pct.quantize(Decimal("0.01")),
                rsi=rsi.quantize(Decimal("0.1")),
                atr_pct=atr_pct.quantize(Decimal("0.01")),
                volume_ratio=volume_ratio.quantize(Decimal("0.01")),
                reason=reason_text,
            )
        )

    candidates.sort(
        key=lambda opportunity: (
            opportunity.score,
            opportunity.net_tp1_pct,
            opportunity.rr,
            opportunity.volume_ratio,
        ),
        reverse=True,
    )

    final = candidates[:MAX_SIGNALS_PER_RUN]

    # ============================================================
    # RE-FETCH LIVE PRICES BEFORE SENDING
    # ============================================================
    if final:
        LOGGER.info("Refreshing live prices for final candidates...")
        try:
            latest_snapshot = get_market_snapshot()
        except Exception as exc:
            LOGGER.warning(f"Could not refresh live snapshot: {exc}")
            latest_snapshot = {}

        updated_final = []
        for opp in final:
            if opp.symbol in latest_snapshot:
                fresh_ticker = latest_snapshot[opp.symbol]
                fresh_ask = fresh_ticker.ask
                fresh_bid = fresh_ticker.bid
                fresh_spread = fresh_ticker.spread_percent

                if fresh_ask is None or fresh_bid is None or fresh_spread is None or fresh_ask <= 0:
                    updated_final.append(opp)
                    continue

                # Keep the same absolute risk and reward distances
                old_risk_dist = opp.entry_price - opp.stop_loss
                old_tp1_dist = opp.tp1 - opp.entry_price
                old_tp2_dist = opp.tp2 - opp.entry_price

                new_entry = fresh_ask
                new_stop = new_entry - old_risk_dist
                new_tp1 = new_entry + old_tp1_dist
                new_tp2 = new_entry + old_tp2_dist

                if new_stop <= 0:
                    updated_final.append(opp)
                    continue

                new_tp1_pct = (new_tp1 - new_entry) / new_entry * Decimal("100")
                estimated_cost = (TAKER_FEE_PCT * Decimal("2")) + (fresh_spread / Decimal("2"))
                new_net_tp1_pct = new_tp1_pct - estimated_cost

                # Re-validate minimums with updated price
                if new_tp1_pct < MIN_REQUIRED_TP1_PCT or new_net_tp1_pct < MIN_NET_TP1_PCT:
                    LOGGER.info(f"Drop {opp.symbol} after refresh: TP1% or net TP1% too low")
                    continue

                new_rr = (new_tp1 - new_entry) / (new_entry - new_stop)
                if new_rr < MIN_REQUIRED_RR:
                    LOGGER.info(f"Drop {opp.symbol} after refresh: RR too low")
                    continue

                updated_opp = Opportunity(
                    symbol=opp.symbol,
                    score=opp.score,
                    strength=opp.strength,
                    setup=opp.setup,
                    data_source=opp.data_source,
                    current_price=fresh_ticker.last,
                    paribu_bid=fresh_bid,
                    paribu_ask=fresh_ask,
                    spread_pct=fresh_spread.quantize(Decimal("0.01")),
                    entry_price=new_entry,
                    stop_loss=new_stop,
                    tp1=new_tp1,
                    tp2=new_tp2,
                    rr=new_rr.quantize(Decimal("0.01")),
                    tp1_pct=new_tp1_pct.quantize(Decimal("0.01")),
                    net_tp1_pct=new_net_tp1_pct.quantize(Decimal("0.01")),
                    rsi=opp.rsi,
                    atr_pct=opp.atr_pct,
                    volume_ratio=opp.volume_ratio,
                    reason=opp.reason,
                )
                updated_final.append(updated_opp)
            else:
                updated_final.append(opp)

        final = updated_final[:MAX_SIGNALS_PER_RUN]

    if not final:
        save_state(state)
        send_telegram_message(format_no_signal_report(stats))
        LOGGER.info("No executable opportunities.")
        return

    header = (
        "🔥 <b>Paribu — فرص Spot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"تم العثور على <b>{len(final)}</b> فرصة قابلة للتنفيذ.\n"
        "مرتبة حسب القوة والقابلية للتنفيذ."
    )

    send_telegram_message(header)

    for rank, opportunity in enumerate(final, start=1):
        if not send_telegram_message(format_signal_message(opportunity, rank)):
            LOGGER.error("Telegram failed for %s", opportunity.symbol)
            continue

        state.setdefault("sent_signals", {})[opportunity.symbol] = int(time.time())
        save_state(state)
        LOGGER.info("Signal sent: %s score=%s", opportunity.symbol, opportunity.score)


if __name__ == "__main__":
    try:
        run_scanner()
    except Exception as exc:
        LOGGER.exception("FATAL SCANNER ERROR")
        send_telegram_message(
            "🚨 <b>SPOT SCANNER FATAL ERROR</b>\n\n"
            f"<code>{html.escape(str(exc))}</code>"
        )
        raise
