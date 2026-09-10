# -*- coding: utf-8 -*-
from __future__ import annotations

"""Paribu Spot Momentum Watcher.

Goal:
- Discover liquid Paribu markets with improving momentum and buy-side flow.
- Persist a watchlist across runs.
- Monitor candidates over multiple scans.
- Send Telegram only when a strong trigger is confirmed.
- Spot only, manual execution only. No automatic orders.

The scanner intentionally avoids "perfect setup" logic that can produce zero
signals for days. It also avoids chasing pumps: strong anti-FOMO and BTC hard
risk checks remain in place.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
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
from near_miss import record_near_miss, update_near_miss_outcomes


LOGGER = logging.getLogger("paribu_momentum_watcher")
if not LOGGER.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STATE_FILE = Path(os.getenv("SCANNER_STATE_FILE", "scanner_state.json"))
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ENV = "TELEGRAM_CHAT_ID"

# Universe / execution quality
MIN_QUOTE_VOLUME_TL = Decimal(os.getenv("MIN_QUOTE_VOLUME_TL", "5000000"))
MAX_SPREAD_PCT = Decimal(os.getenv("MAX_ALLOWED_SPREAD_PCT", "0.40"))
MAX_ORDERBOOK_MARKETS = max(20, int(os.getenv("MAX_ORDERBOOK_MARKETS", "80")))
MAX_TECHNICAL_MARKETS = max(10, int(os.getenv("MAX_TECHNICAL_MARKETS", "60")))
ORDERBOOK_DEPTH = max(5, min(int(os.getenv("ORDERBOOK_DEPTH", "20")), 20))
CANDLE_LIMIT = max(205, int(os.getenv("CANDLE_LIMIT", "250")))

# Discovery -> Watchlist -> Trigger
DISCOVERY_MIN_SCORE = max(0, min(100, int(os.getenv("DISCOVERY_MIN_SCORE", "66"))))
ALERT_MIN_SCORE = max(0, min(100, int(os.getenv("ALERT_MIN_SCORE", "80"))))
NEAR_MISS_MIN_SCORE = max(0, min(100, int(os.getenv("NEAR_MISS_MIN_SCORE", "74"))))

MIN_WATCH_VOLUME_RATIO = Decimal(os.getenv("MIN_WATCH_VOLUME_RATIO", "1.05"))
MIN_ALERT_VOLUME_RATIO = Decimal(os.getenv("MIN_ALERT_VOLUME_RATIO", "1.30"))
MIN_WATCH_IMBALANCE = Decimal(os.getenv("MIN_WATCH_IMBALANCE", "0.85"))
MIN_ALERT_IMBALANCE = Decimal(os.getenv("MIN_ALERT_IMBALANCE", "1.12"))
MIN_AVG_IMBALANCE = Decimal(os.getenv("MIN_AVG_IMBALANCE", "1.06"))

# Candidate must usually survive at least 2 scans (~30-60 min on current cron).
MIN_CONFIRMATIONS = max(2, int(os.getenv("MIN_CONFIRMATIONS", "2")))
WATCHLIST_TTL_SECONDS = max(
    2 * 60 * 60,
    int(os.getenv("WATCHLIST_TTL_SECONDS", str(8 * 60 * 60))),
)
WATCH_HISTORY_LIMIT = max(3, int(os.getenv("WATCH_HISTORY_LIMIT", "8")))

# No spam: at most one strong alert in this global cooldown window.
GLOBAL_ALERT_COOLDOWN_SECONDS = max(
    60 * 60,
    int(os.getenv("GLOBAL_ALERT_COOLDOWN_SECONDS", str(18 * 60 * 60))),
)
SYMBOL_ALERT_COOLDOWN_SECONDS = max(
    60 * 60,
    int(os.getenv("SYMBOL_ALERT_COOLDOWN_SECONDS", str(24 * 60 * 60))),
)

# Anti-FOMO
MAX_RETURN_3 = Decimal(os.getenv("MAX_RETURN_3", "3.20"))
MAX_RETURN_12 = Decimal(os.getenv("MAX_RETURN_12", "8.50"))
MAX_RETURN_48 = Decimal(os.getenv("MAX_RETURN_48", "18.00"))

# Momentum ranges
WATCH_RSI_LOW = Decimal(os.getenv("WATCH_RSI_LOW", "45"))
WATCH_RSI_HIGH = Decimal(os.getenv("WATCH_RSI_HIGH", "72"))
ALERT_RSI_LOW = Decimal(os.getenv("ALERT_RSI_LOW", "49"))
ALERT_RSI_HIGH = Decimal(os.getenv("ALERT_RSI_HIGH", "68"))

# Short scalp risk plan (manual execution)
MIN_ATR_PCT = Decimal(os.getenv("MIN_ATR_PCT", "0.20"))
MAX_ATR_PCT = Decimal(os.getenv("MAX_ATR_PCT", "5.00"))
MIN_STOP_PCT = Decimal(os.getenv("MIN_STOP_PCT", "1.00"))
MAX_STOP_PCT = Decimal(os.getenv("MAX_STOP_PCT", "1.80"))
ATR_STOP_MULTIPLIER = Decimal(os.getenv("ATR_STOP_MULTIPLIER", "1.15"))
TP1_PCT = Decimal(os.getenv("TP1_PCT", "1.50"))
TP2_PCT = Decimal(os.getenv("TP2_PCT", "2.30"))

# BTC: block only real/confirmed weakness.
BTC_HARD_BEAR_RSI = Decimal(os.getenv("BTC_HARD_BEAR_RSI", "37"))
BTC_MAX_3CANDLE_DROP_PCT = Decimal(os.getenv("BTC_MAX_3CANDLE_DROP_PCT", "-2.00"))
BTC_MAX_15M_EMA21_DISTANCE_BEARISH_PCT = Decimal(
    os.getenv("BTC_MAX_15M_EMA21_DISTANCE_BEARISH_PCT", "-1.10")
)
BTC_MAX_1H_EMA21_DISTANCE_BEARISH_PCT = Decimal(
    os.getenv("BTC_MAX_1H_EMA21_DISTANCE_BEARISH_PCT", "-2.00")
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    symbol: str
    score: int
    ticker: Ticker
    book: OrderBookSnapshot
    tech_15: IndicatorResult
    tech_1h: IndicatorResult
    tech_4h: IndicatorResult
    setup: str
    reasons: list[str]


@dataclass(frozen=True)
class TriggeredOpportunity:
    symbol: str
    score: int
    setup: str
    entry: Decimal
    stop: Decimal
    tp1: Decimal
    tp2: Decimal
    risk_pct: Decimal
    quote_volume: Decimal
    spread_pct: Decimal
    imbalance: Decimal
    avg_imbalance: Decimal
    volume_ratio: Decimal
    rsi: Decimal
    confirmations: int
    recent_return_3: Decimal
    btc_reason: str
    reasons: list[str]


# ---------------------------------------------------------------------------
# Utilities / state
# ---------------------------------------------------------------------------


def dec(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def pct(a: Decimal, b: Decimal) -> Decimal:
    if b == 0:
        return Decimal("0")
    return (a / b - Decimal("1")) * Decimal("100")


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


def _empty_state() -> dict[str, Any]:
    return {
        "sent_signals": {},
        "watchlist": {},
        "near_misses": [],
        "last_alert_at": 0,
    }


def load_state() -> dict[str, Any]:
    """Load state and migrate away old top-level timestamp keys."""
    if not STATE_FILE.exists():
        return _empty_state()

    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _empty_state()

        state = _empty_state()

        if isinstance(raw.get("sent_signals"), dict):
            state["sent_signals"] = raw["sent_signals"]
        if isinstance(raw.get("watchlist"), dict):
            state["watchlist"] = raw["watchlist"]
        if isinstance(raw.get("near_misses"), list):
            state["near_misses"] = raw["near_misses"]

        try:
            state["last_alert_at"] = int(raw.get("last_alert_at", 0) or 0)
        except (TypeError, ValueError):
            state["last_alert_at"] = 0

        return state

    except Exception as exc:
        LOGGER.warning("State load failed: %s", exc)
        return _empty_state()


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
            LOGGER.error("Telegram HTTP %s: %s", response.status_code, response.text[:400])
            return False
        return True
    except requests.RequestException as exc:
        LOGGER.error("Telegram request failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# BTC regime
# ---------------------------------------------------------------------------


def btc_gate() -> tuple[bool, Optional[IndicatorResult], str]:
    """Block alerts only on confirmed BTC weakness; scanning continues."""
    try:
        df_15 = fetch_candles("BTC_TL", "15m", CANDLE_LIMIT)
        df_1h = fetch_candles("BTC_TL", "1h", CANDLE_LIMIT)
        tech_15 = analyze_symbol(df_15)
        tech_1h = analyze_symbol(df_1h)

        if tech_15 is None or tech_1h is None:
            return False, None, "BTC indicators unavailable"

        if str(df_15.attrs.get("source", "")).upper() != "PARIBU":
            return False, None, "BTC 15m source is not Paribu"
        if str(df_1h.attrs.get("source", "")).upper() != "PARIBU":
            return False, None, "BTC 1h source is not Paribu"

        d15 = pct(tech_15.current_close, tech_15.ema21)
        d1h = pct(tech_1h.current_close, tech_1h.ema21)

        if tech_15.recent_return_3 <= BTC_MAX_3CANDLE_DROP_PCT:
            return False, tech_15, f"BTC 3C drop {tech_15.recent_return_3:.2f}%"

        if d15 <= BTC_MAX_15M_EMA21_DISTANCE_BEARISH_PCT:
            return False, tech_15, f"BTC 15m below EMA21 {d15:.2f}%"

        if d1h <= BTC_MAX_1H_EMA21_DISTANCE_BEARISH_PCT:
            return False, tech_15, f"BTC 1h below EMA21 {d1h:.2f}%"

        if (
            tech_15.rsi14 < BTC_HARD_BEAR_RSI
            and tech_15.recent_return_3 < 0
            and tech_15.current_close < tech_15.ema21
        ):
            return False, tech_15, (
                f"BTC confirmed weakness RSI={tech_15.rsi14:.1f} | "
                f"3C={tech_15.recent_return_3:.2f}%"
            )

        if tech_15.is_uptrend and tech_1h.is_uptrend:
            return True, tech_15, "BTC bullish"

        return True, tech_15, f"BTC neutral/acceptable | RSI={tech_15.rsi14:.1f}"

    except Exception as exc:
        return False, None, f"BTC gate error: {exc}"


# ---------------------------------------------------------------------------
# Candidate quality
# ---------------------------------------------------------------------------


def anti_fomo_ok(tech: IndicatorResult) -> tuple[bool, str]:
    if tech.recent_return_3 >= MAX_RETURN_3:
        return False, f"3C already +{tech.recent_return_3:.2f}%"
    if tech.recent_return_12 >= MAX_RETURN_12:
        return False, f"12C already +{tech.recent_return_12:.2f}%"
    if tech.recent_return_48 >= MAX_RETURN_48:
        return False, f"48C already +{tech.recent_return_48:.2f}%"
    return True, "OK"


def setup_type(tech: IndicatorResult) -> tuple[bool, str]:
    constructive = bool(
        tech.is_above_ema9
        and tech.is_above_ema21
        and tech.ema21 >= tech.ema50
        and tech.macd_histogram > 0
        and tech.macd_line > tech.macd_signal
    )

    if tech.breakout and tech.volume_ratio >= Decimal("1.20"):
        return True, "BREAKOUT"

    if tech.is_pullback and constructive:
        return True, "PULLBACK"

    recovery = bool(
        constructive
        and tech.is_bullish_candle
        and tech.volume_ratio >= Decimal("1.20")
        and tech.recent_return_3 < Decimal("2.60")
    )
    if recovery:
        return True, "RECOVERY"

    return False, "NO_TRIGGER_SETUP"


def score_candidate(
    ticker: Ticker,
    book: OrderBookSnapshot,
    tech_15: IndicatorResult,
    tech_1h: IndicatorResult,
    tech_4h: IndicatorResult,
) -> tuple[int, list[str]]:
    """Balanced score for short Paribu momentum opportunities."""
    score = 0
    reasons: list[str] = []

    quote_volume = ticker.quote_volume or Decimal("0")

    # 1) Local liquidity + buy-side flow: 35
    if quote_volume >= Decimal("25000000"):
        score += 8
        reasons.append("very strong TL liquidity")
    elif quote_volume >= Decimal("10000000"):
        score += 6
        reasons.append("strong TL liquidity")
    elif quote_volume >= MIN_QUOTE_VOLUME_TL:
        score += 4

    if book.imbalance_ratio >= Decimal("1.35"):
        score += 15
        reasons.append("strong buy-side order book")
    elif book.imbalance_ratio >= Decimal("1.15"):
        score += 12
        reasons.append("buy-side order book")
    elif book.imbalance_ratio >= Decimal("1.00"):
        score += 8
    elif book.imbalance_ratio >= MIN_WATCH_IMBALANCE:
        score += 4

    if tech_15.volume_ratio >= Decimal("2.00"):
        score += 12
        reasons.append("volume expansion >=2x")
    elif tech_15.volume_ratio >= Decimal("1.50"):
        score += 10
        reasons.append("volume expansion >=1.5x")
    elif tech_15.volume_ratio >= MIN_ALERT_VOLUME_RATIO:
        score += 8
    elif tech_15.volume_ratio >= MIN_WATCH_VOLUME_RATIO:
        score += 4

    # 2) Momentum: 25
    constructive_15 = bool(
        tech_15.is_above_ema9
        and tech_15.is_above_ema21
        and tech_15.ema21 >= tech_15.ema50
    )
    if tech_15.is_uptrend:
        score += 8
        reasons.append("15m uptrend")
    elif constructive_15:
        score += 6
        reasons.append("15m constructive")

    if tech_15.macd_histogram > 0 and tech_15.macd_line > tech_15.macd_signal:
        score += 7
        reasons.append("15m MACD positive")

    if Decimal("50") <= tech_15.rsi14 <= Decimal("64"):
        score += 6
        reasons.append("healthy RSI")
    elif Decimal("46") <= tech_15.rsi14 <= Decimal("68"):
        score += 4

    if Decimal("0.10") <= tech_15.recent_return_3 <= Decimal("2.30"):
        score += 4
        reasons.append("price momentum without chase")

    # 3) Higher timeframe context: 20
    # 1h no longer has to be a perfect full uptrend.
    if tech_1h.is_uptrend:
        score += 10
        reasons.append("1h uptrend")
    elif tech_1h.current_close >= tech_1h.ema21 * Decimal("0.995"):
        score += 7
        reasons.append("1h not weak")
    elif tech_1h.current_close >= tech_1h.ema21 * Decimal("0.985"):
        score += 3

    if tech_4h.current_close >= tech_4h.ema50:
        score += 6
        reasons.append("4h above EMA50")
    elif tech_4h.current_close >= tech_4h.ema50 * Decimal("0.98"):
        score += 3

    if tech_1h.macd_histogram >= 0:
        score += 4
        reasons.append("1h momentum stable")

    # 4) Setup: 15
    if tech_15.breakout:
        score += 9
        reasons.append("breakout")
    elif tech_15.is_pullback:
        score += 7
        reasons.append("controlled pullback")
    elif tech_15.is_bullish_candle and constructive_15:
        score += 5
        reasons.append("bullish recovery")

    if tech_15.volume_ratio >= Decimal("1.50") and tech_15.is_bullish_candle:
        score += 4
        reasons.append("bullish candle + volume")

    # 5) Spread quality: 5
    if book.spread_percent <= Decimal("0.20"):
        score += 5
    elif book.spread_percent <= Decimal("0.30"):
        score += 4
    elif book.spread_percent <= MAX_SPREAD_PCT:
        score += 2

    return max(0, min(score, 100)), reasons


def discovery_ok(
    ticker: Ticker,
    book: OrderBookSnapshot,
    tech_15: IndicatorResult,
    tech_1h: IndicatorResult,
    tech_4h: IndicatorResult,
    score: int,
) -> tuple[bool, str]:
    """Loose enough to create a useful watchlist, still rejects bad structure."""
    if ticker.quote_volume is None or ticker.quote_volume < MIN_QUOTE_VOLUME_TL:
        return False, "low liquidity"

    if book.spread_percent > MAX_SPREAD_PCT:
        return False, "spread too high"

    if book.imbalance_ratio < MIN_WATCH_IMBALANCE:
        return False, "order book strongly sell-side"

    if not (WATCH_RSI_LOW <= tech_15.rsi14 <= WATCH_RSI_HIGH):
        return False, "RSI outside watch range"

    atr_pct = tech_15.atr14 / tech_15.current_close * Decimal("100")
    if atr_pct < MIN_ATR_PCT or atr_pct > MAX_ATR_PCT:
        return False, "ATR outside range"

    fomo_ok, fomo_reason = anti_fomo_ok(tech_15)
    if not fomo_ok:
        return False, f"anti-FOMO: {fomo_reason}"

    # We allow 1h neutral, but not clearly broken.
    if tech_1h.current_close < tech_1h.ema21 * Decimal("0.985"):
        return False, "1h clearly weak"

    if tech_4h.current_close < tech_4h.ema50 * Decimal("0.97"):
        return False, "4h clearly weak"

    constructive = bool(
        tech_15.is_above_ema21
        and tech_15.macd_histogram > 0
    )
    if not constructive:
        return False, "15m momentum not constructive"

    if tech_15.volume_ratio < MIN_WATCH_VOLUME_RATIO:
        return False, "volume not expanding"

    if score < DISCOVERY_MIN_SCORE:
        return False, f"discovery score {score} < {DISCOVERY_MIN_SCORE}"

    return True, "OK"


# ---------------------------------------------------------------------------
# Watchlist persistence and flow confirmation
# ---------------------------------------------------------------------------


def _watchlist(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("watchlist")
    if not isinstance(value, dict):
        value = {}
        state["watchlist"] = value
    return value


def _prune_watchlist(state: dict[str, Any], now: int) -> None:
    watch = _watchlist(state)
    stale: list[str] = []
    for symbol, item in watch.items():
        try:
            last_seen = int(item.get("last_seen", 0))
        except (AttributeError, TypeError, ValueError):
            stale.append(symbol)
            continue
        if now - last_seen > WATCHLIST_TTL_SECONDS:
            stale.append(symbol)

    for symbol in stale:
        watch.pop(symbol, None)


def _update_watchlist(
    state: dict[str, Any],
    candidate: Candidate,
    now: int,
) -> dict[str, Any]:
    watch = _watchlist(state)
    symbol = candidate.symbol
    current_price = dec(candidate.book.best_ask) or candidate.tech_15.current_close

    item = watch.get(symbol)
    if not isinstance(item, dict):
        item = {
            "symbol": symbol,
            "first_seen": now,
            "last_seen": now,
            "confirmations": 0,
            "max_score": 0,
            "history": [],
        }
        watch[symbol] = item

    history = item.get("history")
    if not isinstance(history, list):
        history = []
        item["history"] = history

    # Consecutive means observations are reasonably close, not necessarily every run.
    try:
        previous_seen = int(item.get("last_seen", 0))
    except (TypeError, ValueError):
        previous_seen = 0

    if previous_seen and now - previous_seen <= 90 * 60:
        confirmations = int(item.get("confirmations", 0) or 0) + 1
    else:
        confirmations = 1

    observation = {
        "time": now,
        "price": str(current_price),
        "score": candidate.score,
        "imbalance": str(candidate.book.imbalance_ratio),
        "volume_ratio": str(candidate.tech_15.volume_ratio),
        "spread_pct": str(candidate.book.spread_percent),
        "rsi": str(candidate.tech_15.rsi14),
        "recent_return_3": str(candidate.tech_15.recent_return_3),
        "setup": candidate.setup,
    }

    history.append(observation)
    item["history"] = history[-WATCH_HISTORY_LIMIT:]
    item["last_seen"] = now
    item["confirmations"] = confirmations
    item["max_score"] = max(int(item.get("max_score", 0) or 0), candidate.score)
    item["last_score"] = candidate.score
    item["last_reason"] = " | ".join(candidate.reasons[:8])

    return item


def _recent_observations(item: dict[str, Any], now: int) -> list[dict[str, Any]]:
    history = item.get("history")
    if not isinstance(history, list):
        return []

    recent: list[dict[str, Any]] = []
    for obs in history:
        try:
            ts = int(obs.get("time", 0))
        except (AttributeError, TypeError, ValueError):
            continue
        if 0 <= now - ts <= 120 * 60:
            recent.append(obs)
    return recent


def _avg_decimal(observations: list[dict[str, Any]], key: str) -> Decimal:
    values: list[Decimal] = []
    for obs in observations:
        value = dec(obs.get(key))
        if value is not None:
            values.append(value)
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def trigger_check(
    candidate: Candidate,
    item: dict[str, Any],
    btc_ok: bool,
    btc_reason: str,
    now: int,
) -> tuple[bool, str, Decimal, int]:
    if not btc_ok:
        return False, f"BTC blocked: {btc_reason}", Decimal("0"), 0

    if candidate.score < ALERT_MIN_SCORE:
        return False, f"score {candidate.score} < {ALERT_MIN_SCORE}", Decimal("0"), 0

    if candidate.book.spread_percent > MAX_SPREAD_PCT:
        return False, "spread too high", Decimal("0"), 0

    if candidate.book.imbalance_ratio < MIN_ALERT_IMBALANCE:
        return False, (
            f"buy flow not strong enough: {candidate.book.imbalance_ratio:.2f}"
        ), Decimal("0"), 0

    if candidate.tech_15.volume_ratio < MIN_ALERT_VOLUME_RATIO:
        return False, (
            f"volume ratio {candidate.tech_15.volume_ratio:.2f} < {MIN_ALERT_VOLUME_RATIO}"
        ), Decimal("0"), 0

    if not (ALERT_RSI_LOW <= candidate.tech_15.rsi14 <= ALERT_RSI_HIGH):
        return False, f"RSI {candidate.tech_15.rsi14:.1f} outside alert range", Decimal("0"), 0

    fomo_ok, fomo_reason = anti_fomo_ok(candidate.tech_15)
    if not fomo_ok:
        return False, f"anti-FOMO: {fomo_reason}", Decimal("0"), 0

    setup_ok, setup = setup_type(candidate.tech_15)
    if not setup_ok:
        return False, "setup not ready", Decimal("0"), 0

    # Higher-timeframe safety: neutral is accepted, clear weakness is not.
    if candidate.tech_1h.current_close < candidate.tech_1h.ema21 * Decimal("0.995"):
        return False, "1h still weak", Decimal("0"), 0

    if candidate.tech_4h.current_close < candidate.tech_4h.ema50 * Decimal("0.98"):
        return False, "4h still weak", Decimal("0"), 0

    observations = _recent_observations(item, now)
    avg_imbalance = _avg_decimal(observations, "imbalance")
    confirmations = len(observations)

    # A truly explosive breakout can alert immediately; otherwise require persistence.
    explosive_now = bool(
        candidate.score >= 88
        and setup == "BREAKOUT"
        and candidate.book.imbalance_ratio >= Decimal("1.25")
        and candidate.tech_15.volume_ratio >= Decimal("1.80")
        and candidate.tech_15.recent_return_3 < Decimal("2.80")
    )

    if not explosive_now:
        if confirmations < MIN_CONFIRMATIONS:
            return False, (
                f"watching: confirmations {confirmations}/{MIN_CONFIRMATIONS}"
            ), avg_imbalance, confirmations

        if avg_imbalance < MIN_AVG_IMBALANCE:
            return False, (
                f"buy flow not persistent: avg imbalance {avg_imbalance:.2f}"
            ), avg_imbalance, confirmations

        # Need either persistent volume or a strong current acceleration.
        avg_volume = _avg_decimal(observations, "volume_ratio")
        if avg_volume < Decimal("1.15") and candidate.tech_15.volume_ratio < Decimal("1.60"):
            return False, (
                f"volume expansion not persistent: avg {avg_volume:.2f}x"
            ), avg_imbalance, confirmations

    return True, "READY", avg_imbalance, max(confirmations, 1)


# ---------------------------------------------------------------------------
# Alert / risk plan
# ---------------------------------------------------------------------------


def _global_alert_allowed(state: dict[str, Any], now: int) -> bool:
    try:
        last_alert = int(state.get("last_alert_at", 0) or 0)
    except (TypeError, ValueError):
        last_alert = 0
    return now - last_alert >= GLOBAL_ALERT_COOLDOWN_SECONDS


def _symbol_alert_allowed(state: dict[str, Any], symbol: str, now: int) -> bool:
    sent = state.get("sent_signals")
    if not isinstance(sent, dict):
        return True
    try:
        last = int(sent.get(symbol, 0) or 0)
    except (TypeError, ValueError):
        return True
    return now - last >= SYMBOL_ALERT_COOLDOWN_SECONDS


def build_opportunity(
    candidate: Candidate,
    avg_imbalance: Decimal,
    confirmations: int,
    btc_reason: str,
) -> Optional[TriggeredOpportunity]:
    entry = dec(candidate.book.best_ask)
    quote_volume = candidate.ticker.quote_volume or Decimal("0")
    if entry is None or entry <= 0:
        return None

    atr_pct = candidate.tech_15.atr14 / candidate.tech_15.current_close * Decimal("100")
    if atr_pct <= 0:
        return None

    risk_pct = max(MIN_STOP_PCT, atr_pct * ATR_STOP_MULTIPLIER)
    risk_pct = min(risk_pct, MAX_STOP_PCT)

    # If swing low is nearby, prefer it, but never widen beyond MAX_STOP_PCT.
    swing_low = dec(candidate.tech_15.swing_low)
    if swing_low is not None and Decimal("0") < swing_low < entry:
        swing_risk = pct(entry, swing_low)
        if MIN_STOP_PCT <= swing_risk <= MAX_STOP_PCT:
            risk_pct = swing_risk

    stop = entry * (Decimal("1") - risk_pct / Decimal("100"))
    tp1 = entry * (Decimal("1") + TP1_PCT / Decimal("100"))
    tp2 = entry * (Decimal("1") + TP2_PCT / Decimal("100"))

    if not (stop < entry < tp1 < tp2):
        return None

    return TriggeredOpportunity(
        symbol=candidate.symbol,
        score=candidate.score,
        setup=candidate.setup,
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        risk_pct=risk_pct,
        quote_volume=quote_volume,
        spread_pct=candidate.book.spread_percent,
        imbalance=candidate.book.imbalance_ratio,
        avg_imbalance=avg_imbalance,
        volume_ratio=candidate.tech_15.volume_ratio,
        rsi=candidate.tech_15.rsi14,
        confirmations=confirmations,
        recent_return_3=candidate.tech_15.recent_return_3,
        btc_reason=btc_reason,
        reasons=candidate.reasons,
    )


def format_opportunity(opp: TriggeredOpportunity) -> str:
    return (
        "🚨 <b>PARIBU — فرصة أصبحت جاهزة للمراجعة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{html.escape(opp.symbol)}</b>\n"
        f"⭐ <b>الدرجة:</b> {opp.score}/100\n"
        f"🧩 <b>الحالة:</b> {html.escape(opp.setup)}\n"
        f"👀 <b>تمت مراقبتها عبر:</b> {opp.confirmations} فحص/فحوص\n\n"
        f"💵 <b>دخول تقريبي:</b> <code>{fmt(opp.entry)}</code>\n"
        f"🛑 <b>وقف خسارة:</b> <code>{fmt(opp.stop)}</code> "
        f"(-{opp.risk_pct:.2f}%)\n"
        f"🎯 <b>هدف 1:</b> <code>{fmt(opp.tp1)}</code> (+{TP1_PCT:.2f}%)\n"
        f"🚀 <b>هدف 2:</b> <code>{fmt(opp.tp2)}</code> (+{TP2_PCT:.2f}%)\n\n"
        "💧 <b>السيولة والزخم:</b>\n"
        f"• حجم تداول TL: {opp.quote_volume:,.0f}\n"
        f"• Volume Ratio: {opp.volume_ratio:.2f}x\n"
        f"• Order Book الآن: {opp.imbalance:.2f}x\n"
        f"• متوسط Order Book أثناء المراقبة: {opp.avg_imbalance:.2f}x\n"
        f"• Spread: {opp.spread_pct:.2f}%\n"
        f"• RSI 15m: {opp.rsi:.1f}\n"
        f"• حركة آخر 3 شموع: {opp.recent_return_3:+.2f}%\n\n"
        f"₿ <b>BTC:</b> {html.escape(opp.btc_reason)}\n"
        f"🧠 <b>أسباب الاختيار:</b> {html.escape(' | '.join(opp.reasons[:8]))}\n\n"
        "⚠️ <b>Spot فقط — التنفيذ يدوي.</b>\n"
        "⚠️ هذه ليست ضمان ربح؛ هي تنبيه بأن شروط الزخم والسيولة اكتملت."
    )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def run_scanner() -> None:
    now = int(time.time())
    state = load_state()
    _prune_watchlist(state, now)

    # Follow previous near-misses first; persistence is handled by workflow.
    near_result = update_near_miss_outcomes(state)
    LOGGER.info(
        "Near-Miss tracking: checked=%d updated=%d errors=%d",
        near_result["checked"],
        near_result["updated"],
        near_result["errors"],
    )

    try:
        snapshot = get_market_snapshot()
    except ParibuDataError as exc:
        LOGGER.error("Paribu snapshot failed: %s", exc)
        save_state(state)
        return

    btc_ok, _btc_15, btc_reason = btc_gate()

    tickers = sorted(
        snapshot.values(),
        key=lambda item: item.quote_volume or Decimal("0"),
        reverse=True,
    )

    discovered: list[Candidate] = []
    orderbook_checked = 0
    technical_checked = 0

    for ticker in tickers:
        if ticker.symbol in {"USDT_TL", "USDC_TL", "BTC_TL"}:
            continue

        if ticker.quote_volume is None or ticker.quote_volume < MIN_QUOTE_VOLUME_TL:
            continue

        if orderbook_checked >= MAX_ORDERBOOK_MARKETS:
            break
        orderbook_checked += 1

        try:
            book = get_order_book(ticker.symbol, ORDERBOOK_DEPTH)
        except Exception:
            continue

        # Cheap rejection before candle calls.
        if book.spread_percent > MAX_SPREAD_PCT:
            continue
        if book.imbalance_ratio < MIN_WATCH_IMBALANCE:
            continue

        if technical_checked >= MAX_TECHNICAL_MARKETS:
            break
        technical_checked += 1

        try:
            df_15 = fetch_candles(ticker.symbol, "15m", CANDLE_LIMIT)
            df_1h = fetch_candles(ticker.symbol, "1h", CANDLE_LIMIT)
            df_4h = fetch_candles(ticker.symbol, "4h", CANDLE_LIMIT)
        except Exception:
            continue

        if not all(
            str(frame.attrs.get("source", "")).upper() == "PARIBU"
            for frame in (df_15, df_1h, df_4h)
        ):
            continue

        tech_15 = analyze_symbol(df_15)
        tech_1h = analyze_symbol(df_1h)
        tech_4h = analyze_symbol(df_4h)
        if tech_15 is None or tech_1h is None or tech_4h is None:
            continue

        score, reasons = score_candidate(ticker, book, tech_15, tech_1h, tech_4h)
        ok, discovery_reason = discovery_ok(
            ticker, book, tech_15, tech_1h, tech_4h, score
        )
        if not ok:
            continue

        setup_ok, setup = setup_type(tech_15)
        if not setup_ok:
            setup = "WATCHING"

        candidate = Candidate(
            symbol=ticker.symbol,
            score=score,
            ticker=ticker,
            book=book,
            tech_15=tech_15,
            tech_1h=tech_1h,
            tech_4h=tech_4h,
            setup=setup,
            reasons=reasons + [f"discovery: {discovery_reason}"],
        )

        discovered.append(candidate)
        _update_watchlist(state, candidate, now)

    # Highest quality first. Only one alert can be sent.
    discovered.sort(
        key=lambda c: (
            c.score,
            c.book.imbalance_ratio,
            c.tech_15.volume_ratio,
            c.ticker.quote_volume or Decimal("0"),
        ),
        reverse=True,
    )

    sent = False

    for candidate in discovered:
        item = _watchlist(state).get(candidate.symbol)
        if not isinstance(item, dict):
            continue

        ready, trigger_reason, avg_imbalance, confirmations = trigger_check(
            candidate, item, btc_ok, btc_reason, now
        )

        if not ready:
            # Keep evidence on good-but-not-ready candidates without Telegram spam.
            if candidate.score >= NEAR_MISS_MIN_SCORE:
                record_near_miss(
                    state,
                    symbol=candidate.symbol,
                    gate="TRIGGER",
                    reason=trigger_reason,
                    reference_price=candidate.book.best_ask,
                    score=candidate.score,
                    spread_pct=candidate.book.spread_percent,
                    imbalance=candidate.book.imbalance_ratio,
                    rsi_15m=candidate.tech_15.rsi14,
                    volume_ratio_15m=candidate.tech_15.volume_ratio,
                )
            continue

        if not _global_alert_allowed(state, now):
            LOGGER.info("Strong candidate %s ready, global alert cooldown active", candidate.symbol)
            continue

        if not _symbol_alert_allowed(state, candidate.symbol, now):
            continue

        opp = build_opportunity(candidate, avg_imbalance, confirmations, btc_reason)
        if opp is None:
            continue

        if send_telegram(format_opportunity(opp)):
            sent = True
            state["last_alert_at"] = now
            sent_signals = state.setdefault("sent_signals", {})
            sent_signals[candidate.symbol] = now
            LOGGER.info(
                "ALERT sent: %s score=%d confirmations=%d volume=%.2fx imbalance=%.2f",
                candidate.symbol,
                candidate.score,
                confirmations,
                candidate.tech_15.volume_ratio,
                candidate.book.imbalance_ratio,
            )
            break

    # No Telegram empty reports. GitHub log is enough.
    LOGGER.info(
        "Run complete | markets=%d | discovered=%d | watchlist=%d | btc_ok=%s | alert_sent=%s",
        len(snapshot),
        len(discovered),
        len(_watchlist(state)),
        btc_ok,
        sent,
    )

    save_state(state)


if __name__ == "__main__":
    run_scanner()
