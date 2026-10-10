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

from dataclasses import dataclass, replace
from datetime import datetime, timezone
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
from near_miss_queue import enqueue_near_miss
from signal_tracker import register_signal, update_active_signals, deliver_pending_events
from execution_research import evaluate_depth
from candle_backfill import bind_history, recent_authentic
from coverage_scheduler import CoverageCycle
from research_cohort import ensure_cohort
from risk_engine import build_risk_plan, build_risk_plan_result


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
SHADOW_MODE = os.getenv("SHADOW_MODE", "true").strip().lower() == "true"
TELEGRAM_READY_ALERTS = os.getenv("TELEGRAM_READY_ALERTS", "false").strip().lower() == "true"
TELEGRAM_EARLY_WATCH_ALERTS = os.getenv("TELEGRAM_EARLY_WATCH_ALERTS", "false").strip().lower() == "true"
EXPECTED_RUN_INTERVAL_SECONDS = max(
    300, int(os.getenv("EXPECTED_RUN_INTERVAL_SECONDS", "600"))
)
CADENCE_LATE_AFTER_SECONDS = max(
    EXPECTED_RUN_INTERVAL_SECONDS,
    int(os.getenv("CADENCE_LATE_AFTER_SECONDS", str(EXPECTED_RUN_INTERVAL_SECONDS * 2))),
)

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
EXECUTION_FOLLOWUP_TTL_SECONDS = max(
    EXPECTED_RUN_INTERVAL_SECONDS * 2,
    int(os.getenv("EXECUTION_FOLLOWUP_TTL_SECONDS", str(2 * 60 * 60))),
)

# No spam: at most one strong alert in this global cooldown window.
GLOBAL_ALERT_COOLDOWN_SECONDS = max(
    60 * 60,
    int(os.getenv("GLOBAL_ALERT_COOLDOWN_SECONDS", str(3 * 60 * 60))),
)
SYMBOL_ALERT_COOLDOWN_SECONDS = max(
    60 * 60,
    int(os.getenv("SYMBOL_ALERT_COOLDOWN_SECONDS", str(12 * 60 * 60))),
)
MAX_DAILY_ALERTS = max(1, min(3, int(os.getenv("MAX_DAILY_ALERTS", "3"))))
RISK_BUDGET_PCT = Decimal(os.getenv("RISK_BUDGET_PCT", "1.00"))

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
    candle_at: int = 0


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
    bid_wall_share: Decimal
    ask_wall_share: Decimal
    volume_ratio: Decimal
    rsi: Decimal
    confirmations: int
    recent_return_3: Decimal
    btc_reason: str
    reasons: list[str]


# ---------------------------------------------------------------------------
# Utilities / state
# ---------------------------------------------------------------------------


def _cadence_health(runs: list[Any], now: int) -> tuple[Optional[int], str]:
    starts: list[int] = []
    for row in runs:
        if not isinstance(row, dict):
            continue
        try:
            started_at = int(row.get("started_at", 0) or 0)
        except (TypeError, ValueError):
            continue
        if started_at > 0:
            starts.append(started_at)
    if not starts:
        return None, "first_observation"
    gap = max(0, int(now) - max(starts))
    return gap, ("ok" if gap <= CADENCE_LATE_AFTER_SECONDS else "late")


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


def _hot_radar_order(
    tickers: list[Any],
    state: dict[str, Any],
    coverage: CoverageCycle,
    now: int,
) -> tuple[list[Any], dict[str, Any]]:
    """Bound priority boosts while preserving market coverage and entry gates.

    Rolling 24h volume differences are a ranking proxy, not interval trade flow.
    Only fresh snapshots are comparable. Pending confirmations get a bounded
    priority when another CLOSED 15m candle could exist; no confirmation is
    granted here. At least half of every budget remains ordinary fair coverage.
    """
    base_order = coverage.order(tickers, "orderbooks", lambda item: item.symbol)
    previous = state.get("ticker_radar", {})
    if not isinstance(previous, dict):
        previous = {}
    previous_markets = previous.get("markets", {}) if isinstance(previous, dict) else {}
    if not isinstance(previous_markets, dict):
        previous_markets = {}

    current_markets: dict[str, dict[str, Any]] = {}
    hot_rows: list[tuple[Decimal, Decimal, Decimal, Decimal, Any]] = []
    previous_at = previous.get("observed_at")
    comparable = (
        type(previous_at) is int
        and 0 < now - previous_at <= CADENCE_LATE_AFTER_SECONDS
    )

    def universe_eligible(ticker):
        volume = dec(getattr(ticker, "quote_volume", None))
        return (ticker.symbol not in {"USDT_TL", "USDC_TL", "BTC_TL"}
                and volume is not None and volume >= MIN_QUOTE_VOLUME_TL)

    for ticker in tickers:
        symbol = str(getattr(ticker, "symbol", ""))
        last = dec(getattr(ticker, "last", None))
        quote_volume = dec(getattr(ticker, "quote_volume", None))
        if not symbol or last is None or last <= 0:
            continue

        current_markets[symbol] = {
            "last": str(last),
            "quote_volume": str(quote_volume) if quote_volume is not None else None,
        }

        if not comparable or not universe_eligible(ticker):
            continue

        prior = previous_markets.get(symbol)
        if not isinstance(prior, dict):
            continue
        prior_last = dec(prior.get("last"))
        prior_quote = dec(prior.get("quote_volume"))
        if (
            prior_last is None
            or prior_last <= 0
            or prior_quote is None
            or prior_quote <= 0
            or quote_volume is None
            or quote_volume <= 0
        ):
            continue

        price_delta_pct = pct(last, prior_last)
        quote_delta = quote_volume - prior_quote
        if price_delta_pct <= 0 or quote_delta <= 0:
            continue

        quote_delta_pct = quote_delta / prior_quote * Decimal("100")
        heat = price_delta_pct * quote_delta_pct
        hot_rows.append((heat, price_delta_pct, quote_delta_pct, quote_volume, ticker))

    hot_rows.sort(
        key=lambda row: (row[0], row[1], row[2], row[3]),
        reverse=True,
    )
    hot_symbols = [row[4].symbol for row in hot_rows]
    confirmation_symbols = []
    early_watch_symbols = []
    execution_followup_symbols = []
    lifecycles = state.get("candidate_lifecycle", {})
    if not isinstance(lifecycles, dict):
        lifecycles = {}
    latest_closed_open = (now // 900 - 1) * 900
    for ticker in base_order:
        lifecycle = lifecycles.get(ticker.symbol, {})
        watch = _watchlist(state).get(ticker.symbol, {})
        if (not universe_eligible(ticker) or not isinstance(lifecycle, dict)
                or not isinstance(watch, dict)
                or lifecycle.get("current_state") != "confirmation_1_of_2"):
            continue
        observations = _recent_observations(watch, now)
        if observations and max(int(o["candle_at"]) for o in observations) < latest_closed_open:
            confirmation_symbols.append(ticker.symbol)

    # Follow-up watches cannot consume every priority slot either. This is a
    # scheduling quota, not an RSI/score/liquidity or execution parameter.
    confirmation_quota = max(0, min(MAX_ORDERBOOK_MARKETS // 2,
                                    max(1, MAX_ORDERBOOK_MARKETS // 4)))
    confirmation_symbols = confirmation_symbols[:confirmation_quota]

    # EARLY WATCH is informational, but it must actually be followed. Give a
    # bounded scheduling priority when a new closed 15m candle can exist.
    # This never counts as a READY confirmation and never bypasses any gate.
    followups = _early_watch_followups(state)
    for ticker in base_order:
        item = followups.get(ticker.symbol, {})
        if not universe_eligible(ticker) or not isinstance(item, dict):
            continue
        try:
            last_seen = int(item.get("last_seen", 0) or 0)
            last_candle_at = int(item.get("last_candle_at", 0) or 0)
        except (TypeError, ValueError):
            continue
        if (
            last_seen > 0
            and 0 <= now - last_seen <= WATCHLIST_TTL_SECONDS
            and last_candle_at > 0
            and last_candle_at < latest_closed_open
        ):
            early_watch_symbols.append(ticker.symbol)

    early_watch_quota = max(
        0,
        min(
            MAX_ORDERBOOK_MARKETS // 2,
            max(1, MAX_ORDERBOOK_MARKETS // 8),
        ),
    )
    early_watch_symbols = early_watch_symbols[:early_watch_quota]

    execution_followups = _execution_followups(state)
    for ticker in base_order:
        item = execution_followups.get(ticker.symbol, {})
        if not universe_eligible(ticker) or not isinstance(item, dict):
            continue
        try:
            first_seen = int(item.get("first_seen", 0) or 0)
            last_seen = int(item.get("last_seen", 0) or 0)
            started_at = first_seen or last_seen
        except (TypeError, ValueError):
            continue
        if (
            started_at > 0
            and last_seen > 0
            and 0 <= now - started_at <= EXECUTION_FOLLOWUP_TTL_SECONDS
        ):
            execution_followup_symbols.append(ticker.symbol)
    execution_followup_quota = max(
        0, min(MAX_ORDERBOOK_MARKETS // 2, max(1, MAX_ORDERBOOK_MARKETS // 8))
    )
    execution_followup_symbols = execution_followup_symbols[:execution_followup_quota]

    priorities = list(dict.fromkeys(
        confirmation_symbols + early_watch_symbols + execution_followup_symbols + hot_symbols
    ))
    eligible = [ticker for ticker in tickers if universe_eligible(ticker)]
    ordered, selected_priority = coverage.prioritize(
        eligible, "orderbooks", lambda item: item.symbol,
        priorities, MAX_ORDERBOOK_MARKETS,
    )
    # Keep excluded symbols for the normal universe-rejection diagnostics.
    ordered.extend(ticker for ticker in base_order if not universe_eligible(ticker))

    state["ticker_radar"] = {
        "version": 2,
        "observed_at": int(now),
        "previous_observed_at": previous_at,
        "snapshot_comparable": comparable,
        "volume_basis": "rolling_24h_difference_not_trade_flow",
        "markets": current_markets,
        "hot_symbols": hot_symbols,
        "hot_metrics": {
            row[4].symbol: {
                "price_delta_pct": str(row[1]),
                "quote_volume_delta_pct": str(row[2]),
                "heat": str(row[0]),
            }
            for row in hot_rows
        },
    }
    return ordered, {
        "eligible_count": len(hot_symbols),
        "hot_symbols": hot_symbols,
        "confirmation_symbols": confirmation_symbols,
        "early_watch_symbols": early_watch_symbols,
        "execution_followup_symbols": execution_followup_symbols,
        "priority_symbols": priorities,
        "selected_priority_symbols": selected_priority,
        "snapshot_comparable": comparable,
        "priority_budget": MAX_ORDERBOOK_MARKETS // 2,
    }


def _empty_state() -> dict[str, Any]:
    return {
        "sent_signals": {},
        "watchlist": {},
        "early_watch_followups": {},
        "execution_followups": {},
        "near_misses": [],
        "near_miss_queue": {},
        "active_signals": [],
        "daily_alerts": [],
        "last_alert_at": 0,
        "candle_gap_history": {},
        "shadow_sent_signals": {},
        "shadow_daily_alerts": [],
        "shadow_last_alert_at": 0,
        "coverage_scheduler": {},
        "ticker_radar": {},
        "candidate_lifecycle": {},
        "directive_009_runs": [],
    }


def load_state() -> dict[str, Any]:
    """Load state and migrate away old top-level timestamp keys."""
    if not STATE_FILE.exists():
        return _empty_state()

    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Scanner state must be an object")
        for key, expected in (("sent_signals", dict), ("watchlist", dict),
                              ("early_watch_followups", dict), ("execution_followups", dict),
                              ("near_misses", list), ("near_miss_queue", dict),
                              ("active_signals", list),
                              ("daily_alerts", list), ("candle_gap_history", dict),
                              ("shadow_sent_signals", dict), ("shadow_daily_alerts", list),
                              ("coverage_scheduler", dict), ("ticker_radar", dict),
                              ("research_cohort", dict),
                              ("research_cohort_history", list), ("candidate_lifecycle", dict),
                              ("directive_009_runs", list)):
            if key in raw and not isinstance(raw[key], expected):
                raise ValueError("Invalid state field: " + key)

        state = _empty_state()

        if isinstance(raw.get("sent_signals"), dict):
            state["sent_signals"] = raw["sent_signals"]
        if isinstance(raw.get("watchlist"), dict):
            state["watchlist"] = raw["watchlist"]
        if isinstance(raw.get("early_watch_followups"), dict):
            state["early_watch_followups"] = raw["early_watch_followups"]
        if isinstance(raw.get("execution_followups"), dict):
            state["execution_followups"] = raw["execution_followups"]
        if isinstance(raw.get("near_misses"), list):
            state["near_misses"] = raw["near_misses"]
        if isinstance(raw.get("near_miss_queue"), dict):
            state["near_miss_queue"] = raw["near_miss_queue"]
        if isinstance(raw.get("active_signals"), list):
            state["active_signals"] = raw["active_signals"]
        if isinstance(raw.get("daily_alerts"), list):
            state["daily_alerts"] = raw["daily_alerts"]
        if isinstance(raw.get("candle_gap_history"), dict):
            state["candle_gap_history"] = raw["candle_gap_history"]
        if isinstance(raw.get("shadow_sent_signals"), dict):
            state["shadow_sent_signals"] = raw["shadow_sent_signals"]
        if isinstance(raw.get("shadow_daily_alerts"), list):
            state["shadow_daily_alerts"] = raw["shadow_daily_alerts"]
        if isinstance(raw.get("coverage_scheduler"), dict):
            state["coverage_scheduler"] = raw["coverage_scheduler"]
        if isinstance(raw.get("ticker_radar"), dict):
            state["ticker_radar"] = raw["ticker_radar"]
        for key in ("research_cohort", "research_cohort_history", "execution_research",
                    "candidate_lifecycle", "directive_009_runs"):
            if key in raw:
                state[key] = raw[key]
        # Research state is isolated from trading signal/watchlist state.
        if isinstance(raw.get("accumulation_radar"), dict):
            state["accumulation_radar"] = raw["accumulation_radar"]
        if isinstance(raw.get("scan_diagnostics"), list):
            state["scan_diagnostics"] = raw["scan_diagnostics"][-12:]

        state["last_alert_at"] = int(raw.get("last_alert_at", 0) or 0)
        state["shadow_last_alert_at"] = int(raw.get("shadow_last_alert_at", 0) or 0)
        if state["last_alert_at"] < 0 or state["shadow_last_alert_at"] < 0:
            raise ValueError("Invalid last alert time")

        return state

    except Exception as exc:
        LOGGER.error("State load failed; refusing to reset alert history: %s", type(exc).__name__)
        raise RuntimeError("Scanner state unreadable; original file preserved") from exc


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
        raise RuntimeError("Scanner state could not be persisted") from exc


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
            LOGGER.error("Telegram HTTP %s", response.status_code)
            return False
        try:
            payload = response.json()
        except ValueError:
            LOGGER.error("Telegram returned invalid JSON")
            return False
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            LOGGER.error("Telegram did not confirm delivery")
            return False
        return True
    except requests.RequestException as exc:
        # Request exceptions can include the bot token in their URL.
        LOGGER.error("Telegram request failed: %s", type(exc).__name__)
        return False


def send_shadow_ready_alert(opportunity: TriggeredOpportunity) -> bool:
    """Send a confirmed READY alert while keeping the release in shadow mode."""
    if not TELEGRAM_READY_ALERTS:
        return False
    return send_telegram(format_opportunity(opportunity))


def _early_watch_mtf_ok(
    book: OrderBookSnapshot,
    tech_15: IndicatorResult,
    score: int,
    btc_ok: bool,
    discovery_reason: str,
) -> bool:
    """Informational cohort: all current short-term guards pass except 1h/4h trend."""
    if not btc_ok or discovery_reason not in {"1h clearly weak", "4h clearly weak"}:
        return False
    if book.spread_percent > MAX_SPREAD_PCT or book.imbalance_ratio < MIN_WATCH_IMBALANCE:
        return False
    if not (WATCH_RSI_LOW <= tech_15.rsi14 <= WATCH_RSI_HIGH):
        return False
    atr_pct = tech_15.atr14 / tech_15.current_close * Decimal("100")
    if atr_pct < MIN_ATR_PCT or atr_pct > MAX_ATR_PCT:
        return False
    fomo_ok, _ = anti_fomo_ok(tech_15)
    if not fomo_ok:
        return False
    if not (tech_15.is_above_ema21 and tech_15.macd_histogram > 0):
        return False
    if tech_15.volume_ratio < MIN_WATCH_VOLUME_RATIO:
        return False
    if score < DISCOVERY_MIN_SCORE:
        return False
    return True


def format_early_watch(
    *,
    symbol: str,
    score: int,
    reason: str,
    book: OrderBookSnapshot,
    tech_15: IndicatorResult,
    btc_reason: str,
) -> str:
    return (
        "👀 <b>PARIBU — EARLY WATCH / مراقبة مبكرة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{html.escape(symbol)}</b>\n"
        f"⭐ <b>الدرجة الحالية:</b> {score}/100\n"
        f"⏳ <b>سبب عدم READY:</b> {html.escape(reason)}\n"
        f"• Spread: {book.spread_percent:.2f}%\n"
        f"• Order Book: {book.imbalance_ratio:.2f}x\n"
        f"• Volume Ratio 15m: {tech_15.volume_ratio:.2f}x\n"
        f"• RSI 15m: {tech_15.rsi14:.1f}\n"
        f"• حركة آخر 3 شموع: {tech_15.recent_return_3:+.2f}%\n"
        f"₿ <b>BTC:</b> {html.escape(btc_reason)}\n\n"
        "⚠️ <b>ليست توصية دخول ولا READY.</b>\n"
        "انتظر فقط رسالة READY المؤكدة 2/2 قبل أي تنفيذ يدوي."
    )


def send_early_watch_alert(
    *,
    symbol: str,
    score: int,
    reason: str,
    book: OrderBookSnapshot,
    tech_15: IndicatorResult,
    btc_reason: str,
) -> bool:
    if not TELEGRAM_EARLY_WATCH_ALERTS:
        return False
    return send_telegram(format_early_watch(
        symbol=symbol,
        score=score,
        reason=reason,
        book=book,
        tech_15=tech_15,
        btc_reason=btc_reason,
    ))


# ---------------------------------------------------------------------------
# BTC regime
# ---------------------------------------------------------------------------


def btc_gate() -> tuple[bool, Optional[IndicatorResult], str]:
    """Block alerts only on confirmed BTC weakness; scanning continues."""
    try:
        df_15 = fetch_candles("BTC_TL", "15m", CANDLE_LIMIT)
        df_1h = fetch_candles("BTC_TL", "1h", CANDLE_LIMIT)
        if not _all_candles_authentic(df_15, df_1h):
            return False, None, "BTC synthetic candles present"
        if not recent_authentic(df_15, 16, 900):
            return False, None, "BTC recent 4h candle integrity failed"

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


def _btc_gate_reason_code(reason: str) -> str:
    if reason == "BTC synthetic candles present":
        return "synthetic_candles_present"
    if reason == "BTC recent 4h candle integrity failed":
        return "recent_4h_integrity_failed"
    if reason == "BTC indicators unavailable":
        return "indicators_unavailable"
    if reason == "BTC 15m source is not Paribu":
        return "source_15m_not_paribu"
    if reason == "BTC 1h source is not Paribu":
        return "source_1h_not_paribu"
    if reason.startswith("BTC 3C drop "):
        return "regime_3c_drop"
    if reason.startswith("BTC 15m below EMA21 "):
        return "ema21_15m_bearish"
    if reason.startswith("BTC 1h below EMA21 "):
        return "ema21_1h_bearish"
    if reason.startswith("BTC confirmed weakness "):
        return "regime_bearish"
    if reason == "BTC bullish":
        return "regime_bullish"
    if reason.startswith("BTC neutral/acceptable "):
        return "regime_neutral"
    if reason.startswith("BTC gate error:"):
        return "gate_error"
    return "unknown"


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

    if (
        tech.is_pullback
        and constructive
        and tech.rsi14 <= Decimal("60")
        and bool(getattr(tech, "mean_touch", False))
    ):
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


def _run_id() -> str:
    return str(os.getenv("GITHUB_RUN_ID") or f"local-{int(time.time())}")


def _candidate_lifecycle_update(
    state: dict[str, Any],
    *,
    symbol: str,
    score: Optional[int],
    lifecycle_state: str,
    reason: Optional[str] = None,
    now: Optional[int] = None,
) -> dict[str, Any]:
    """Persist one logical candidate across scheduled runs without changing strategy."""
    observed_at = int(time.time() if now is None else now)
    run_id = _run_id()
    lifecycles = state.setdefault("candidate_lifecycle", {})
    if not isinstance(lifecycles, dict):
        lifecycles = {}
        state["candidate_lifecycle"] = lifecycles

    item = lifecycles.get(symbol)
    terminal = {"rejected", "expired", "shadow_entry"}
    if not isinstance(item, dict) or str(item.get("current_state")) in terminal:
        candidate_id = f"{symbol}:{run_id}"
        item = {
            "candidate_id": candidate_id,
            "symbol": symbol,
            "first_seen_run": run_id,
            "first_seen_at": datetime.fromtimestamp(observed_at, tz=timezone.utc).isoformat(),
            "max_score": int(score or 0),
            "current_state": lifecycle_state,
            "history": [],
        }
        lifecycles[symbol] = item

    if score is not None:
        item["max_score"] = max(int(item.get("max_score", 0) or 0), int(score))
    item["current_state"] = lifecycle_state
    history = item.setdefault("history", [])
    if not isinstance(history, list):
        history = []
        item["history"] = history
    event = {
        "run_id": run_id,
        "ts": datetime.fromtimestamp(observed_at, tz=timezone.utc).isoformat(),
        "score": int(score) if score is not None else None,
        "state": lifecycle_state,
    }
    if reason:
        event["reason"] = reason
    # Idempotent within a run/state pair.
    if not history or history[-1].get("run_id") != run_id or history[-1].get("state") != lifecycle_state:
        history.append(event)
    item["history"] = history[-64:]
    return item


def _candle_synthetic_rows(*frames: Any) -> int:
    total = 0
    for frame in frames:
        try:
            if "is_authentic" in frame.columns:
                total += int((~frame["is_authentic"].astype(bool)).sum())
        except Exception:
            continue
    return total


def _candle_error_reason_code(exc: Exception) -> str:
    """Classify candle-fetch failures without changing scanner behavior."""
    message = f"{type(exc).__name__}: {exc}".lower()
    if "backfill gap limit exceeded" in message:
        return "backfill_gap_limit_exceeded"
    if "consecutive synthetic candle limit exceeded" in message:
        return "consecutive_synthetic_limit_exceeded"
    if "synthetic candle limit exceeded" in message:
        return "synthetic_candle_limit_exceeded"
    if "only " in message and "closed paribu candles" in message:
        return "insufficient_history"
    if "no valid rows to anchor backfill" in message or "no paribu candles available" in message:
        return "empty_history"
    if "gaps or irregular candle spacing" in message:
        return "irregular_spacing"
    if "stale candles" in message:
        return "stale_candles"
    if "http 429" in message or "too many requests" in message:
        return "http_429"
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if "invalid json" in message:
        return "invalid_json"
    if "paribuschemaerror" in message or "schema" in message:
        return "schema_error"
    if "paribu chart unavailable" in message:
        return "chart_unavailable"
    if "candleunavailableerror" in message:
        return "candle_unavailable_other"
    return "unexpected_error"


def _all_candles_authentic(*frames: Any) -> bool:
    """Fail closed unless every candle used by indicators is genuine Paribu data."""
    for frame in frames:
        try:
            if "is_authentic" not in frame.columns or frame.empty:
                return False
            if not bool(frame["is_authentic"].astype(bool).all()):
                return False
        except Exception:
            return False
    return True


def _watchlist(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("watchlist")
    if not isinstance(value, dict):
        value = {}
        state["watchlist"] = value
    return value


def _early_watch_followups(state: dict[str, Any]) -> dict[str, Any]:
    """Persist EARLY WATCH follow-up scheduling without granting READY confirmations."""
    value = state.get("early_watch_followups")
    if not isinstance(value, dict):
        value = {}
        state["early_watch_followups"] = value
    return value


def _record_early_watch_followup(
    state: dict[str, Any],
    *,
    symbol: str,
    now: int,
    candle_at: int,
    score: int,
    reason: str,
) -> dict[str, Any]:
    followups = _early_watch_followups(state)
    item = followups.get(symbol)
    if not isinstance(item, dict):
        item = {
            "symbol": symbol,
            "first_seen": int(now),
            "max_score": int(score),
        }
        followups[symbol] = item
    item["last_seen"] = int(now)
    item["last_candle_at"] = int(candle_at)
    item["last_score"] = int(score)
    item["max_score"] = max(int(item.get("max_score", 0) or 0), int(score))
    item["last_reason"] = str(reason)
    return item


def _execution_followups(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("execution_followups")
    if not isinstance(value, dict):
        value = {}
        state["execution_followups"] = value
    return value


def _record_execution_followup(
    state: dict[str, Any],
    *,
    symbol: str,
    now: int,
    reason: str,
    spread_pct: Decimal | None = None,
    imbalance: Decimal | None = None,
    best_ask: Decimal | None = None,
) -> None:
    item = _execution_followups(state).get(symbol)
    if not isinstance(item, dict):
        item = {
            "symbol": symbol,
            "first_seen": int(now),
            "checks": 0,
            "reason_counts": {},
            "history": [],
        }
        _execution_followups(state)[symbol] = item

    item["last_seen"] = int(now)
    item["last_reason"] = str(reason)
    item["checks"] = int(item.get("checks", 0) or 0) + 1

    counts = item.get("reason_counts")
    if not isinstance(counts, dict):
        counts = {}
        item["reason_counts"] = counts
    counts[str(reason)] = int(counts.get(str(reason), 0) or 0) + 1

    # Keep a bounded forensic trace of the execution window. This is
    # observability only: it does not relax spread/imbalance/READY gates.
    history = item.get("history")
    if not isinstance(history, list):
        history = []
        item["history"] = history
    event = {"at": int(now), "reason": str(reason)}
    if spread_pct is not None:
        event["spread_pct"] = str(spread_pct)
    if imbalance is not None:
        event["imbalance"] = str(imbalance)
    if best_ask is not None:
        event["best_ask"] = str(best_ask)
    history.append(event)
    del history[:-12]


def _prune_execution_followups(state: dict[str, Any], now: int) -> None:
    followups = _execution_followups(state)
    for symbol, item in list(followups.items()):
        try:
            # TTL is a fixed episode lifetime. Repeated transient rejections
            # must not renew it forever and monopolize the priority budget.
            first_seen = int(item.get("first_seen", 0) or 0)
            last_seen = int(item.get("last_seen", 0) or 0)
            started_at = first_seen or last_seen
        except (AttributeError, TypeError, ValueError):
            started_at = 0
        if started_at <= 0 or now - started_at > EXECUTION_FOLLOWUP_TTL_SECONDS:
            followups.pop(symbol, None)


def _prune_early_watch_followups(state: dict[str, Any], now: int) -> None:
    followups = _early_watch_followups(state)
    stale: list[str] = []
    for symbol, item in followups.items():
        try:
            last_seen = int(item.get("last_seen", 0))
        except (AttributeError, TypeError, ValueError):
            stale.append(symbol)
            continue
        if last_seen <= 0 or now - last_seen > WATCHLIST_TTL_SECONDS:
            stale.append(symbol)
    for symbol in stale:
        followups.pop(symbol, None)


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

    observation = {
        "time": now,
        "candle_at": candidate.candle_at,
        "price": str(current_price),
        "score": candidate.score,
        "imbalance": str(candidate.book.imbalance_ratio),
        "volume_ratio": str(candidate.tech_15.volume_ratio),
        "spread_pct": str(candidate.book.spread_percent),
        "bid_wall": str(candidate.book.largest_bid_wall_share),
        "ask_wall": str(candidate.book.largest_ask_wall_share),
        "rsi": str(candidate.tech_15.rsi14),
        "recent_return_3": str(candidate.tech_15.recent_return_3),
        "setup": candidate.setup,
    }

    # Legacy observations have no candle identity and cannot prove confirmation.
    history = [o for o in history if isinstance(o, dict)
               and o.get("candle_at") and o.get("candle_at") != candidate.candle_at]
    history.append(observation)
    item["history"] = history[-WATCH_HISTORY_LIMIT:]
    item["last_seen"] = now
    item["confirmations"] = len(_recent_observations(item, now))
    item["max_score"] = max(int(item.get("max_score", 0) or 0), candidate.score)
    item["last_score"] = candidate.score
    item["last_reason"] = " | ".join(candidate.reasons[:8])

    return item


def _recent_observations(item: dict[str, Any], now: int) -> list[dict[str, Any]]:
    history = item.get("history")
    if not isinstance(history, list):
        return []

    recent: list[dict[str, Any]] = []
    seen = set()
    for obs in history:
        try:
            ts = int(obs.get("time", 0))
            candle_at = int(obs.get("candle_at", 0))
        except (AttributeError, TypeError, ValueError):
            continue
        if candle_at > 0 and candle_at not in seen and 0 <= now - ts <= 120 * 60:
            recent.append(obs)
            seen.add(candle_at)
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

    # A dominant nearby sell wall can absorb a short scalp. This is only an
    # order-book proxy, not proof of a whale, so it blocks only extreme cases.
    if (
        candidate.book.largest_ask_wall_share >= Decimal("0.45")
        and candidate.book.largest_ask_wall_share
        > candidate.book.largest_bid_wall_share * Decimal("1.80")
    ):
        return False, "dominant sell wall near price", Decimal("0"), 0

    if candidate.tech_15.volume_ratio < MIN_ALERT_VOLUME_RATIO:
        return False, (
            f"volume ratio {candidate.tech_15.volume_ratio:.2f} < {MIN_ALERT_VOLUME_RATIO}"
        ), Decimal("0"), 0

    if not (ALERT_RSI_LOW <= candidate.tech_15.rsi14 <= ALERT_RSI_HIGH):
        return False, f"RSI {candidate.tech_15.rsi14:.1f} outside alert range", Decimal("0"), 0

    if candidate.setup == "PULLBACK":
        if candidate.tech_15.rsi14 > Decimal("60"):
            return False, "pullback RSI above 60", Decimal("0"), 0
        if not bool(getattr(candidate.tech_15, "mean_touch", False)):
            return False, "pullback lacks EMA/VWAP interaction", Decimal("0"), 0

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


def _global_alert_allowed(
    state: dict[str, Any], now: int, shadow: bool = False
) -> bool:
    last_key = "shadow_last_alert_at" if shadow else "last_alert_at"
    daily_key = "shadow_daily_alerts" if shadow else "daily_alerts"
    try:
        last_alert = int(state.get(last_key, 0) or 0)
    except (TypeError, ValueError):
        last_alert = 0
    recent: list[int] = []
    for value in state.get(daily_key, []):
        try:
            stamp = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= now - stamp < 24 * 60 * 60:
            recent.append(stamp)
    state[daily_key] = recent
    return len(recent) < MAX_DAILY_ALERTS and now - last_alert >= GLOBAL_ALERT_COOLDOWN_SECONDS


def _symbol_alert_allowed(
    state: dict[str, Any], symbol: str, now: int, shadow: bool = False
) -> bool:
    sent = state.get("shadow_sent_signals" if shadow else "sent_signals")
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
    quote_volume = candidate.ticker.quote_volume or Decimal("0")
    plan = build_risk_plan(
        book=candidate.book,
        tech=candidate.tech_15,
        setup=candidate.setup,
        atr_multiplier=Decimal(os.getenv("ATR_STOP_MULTIPLIER_V2", "1.75")),
        max_risk_pct=Decimal(os.getenv("MAX_STOP_PCT_V2", "4.00")),
        min_rr=Decimal(os.getenv("MIN_REWARD_RISK", "1.50")),
        tp1_pct=TP1_PCT,
        tp2_pct=TP2_PCT,
    )
    if plan is None:
        return None

    reasons = list(candidate.reasons)
    reasons.append(f"limit-entry reference; TP1 R/R={plan.reward_risk_tp1:.2f}")
    if plan.target_adjusted_for_wall and plan.sell_wall_price is not None:
        reasons.append(
            f"TP1 adjusted before sell wall {plan.sell_wall_price} "
            f"(share {plan.sell_wall_share * 100:.1f}%)"
        )

    return TriggeredOpportunity(
        symbol=candidate.symbol,
        score=candidate.score,
        setup=candidate.setup,
        entry=plan.entry,
        stop=plan.stop,
        tp1=plan.tp1,
        tp2=plan.tp2,
        risk_pct=plan.risk_pct,
        quote_volume=quote_volume,
        spread_pct=candidate.book.spread_percent,
        imbalance=candidate.book.imbalance_ratio,
        avg_imbalance=avg_imbalance,
        bid_wall_share=candidate.book.largest_bid_wall_share,
        ask_wall_share=candidate.book.largest_ask_wall_share,
        volume_ratio=candidate.tech_15.volume_ratio,
        rsi=candidate.tech_15.rsi14,
        confirmations=confirmations,
        recent_return_3=candidate.tech_15.recent_return_3,
        btc_reason=btc_reason,
        reasons=reasons,
    )

def format_opportunity(opp: TriggeredOpportunity) -> str:
    return (
        "🚨 <b>PARIBU — فرصة أصبحت جاهزة للمراجعة</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{html.escape(opp.symbol)}</b>\n"
        f"⭐ <b>الدرجة:</b> {opp.score}/100\n"
        f"🧩 <b>الحالة:</b> {html.escape(opp.setup)}\n"
        f"👀 <b>تمت مراقبتها عبر:</b> {opp.confirmations} فحص/فحوص\n\n"
        f"💵 <b>دخول LIMIT مرجعي:</b> <code>{fmt(opp.entry)}</code>\n"
        f"🛑 <b>وقف خسارة:</b> <code>{fmt(opp.stop)}</code> "
        f"(-{opp.risk_pct:.2f}%)\n"
        f"🧮 <b>حد مخاطرة الحساب:</b> {RISK_BUDGET_PCT:.2f}% كحد أقصى\n"
        f"📐 <b>حجم المركز:</b> (رأس المال × {RISK_BUDGET_PCT:.2f}%) ÷ {opp.risk_pct:.2f}%\n"
        f"🎯 <b>هدف 1:</b> <code>{fmt(opp.tp1)}</code> (+{pct(opp.tp1, opp.entry):.2f}%)\n"
        f"🚀 <b>هدف 2:</b> <code>{fmt(opp.tp2)}</code> (+{pct(opp.tp2, opp.entry):.2f}%)\n\n"
        "💧 <b>السيولة والزخم:</b>\n"
        f"• حجم تداول TL: {opp.quote_volume:,.0f}\n"
        f"• Volume Ratio: {opp.volume_ratio:.2f}x\n"
        f"• Order Book الآن: {opp.imbalance:.2f}x\n"
        f"• متوسط Order Book أثناء المراقبة: {opp.avg_imbalance:.2f}x\n"
        f"• أكبر جدار شراء: {opp.bid_wall_share * 100:.1f}% من العمق\n"
        f"• أكبر جدار بيع: {opp.ask_wall_share * 100:.1f}% من العمق\n"
        f"• Spread: {opp.spread_pct:.2f}%\n"
        f"• RSI 15m: {opp.rsi:.1f}\n"
        f"• حركة آخر 3 شموع: {opp.recent_return_3:+.2f}%\n\n"
        f"₿ <b>BTC:</b> {html.escape(opp.btc_reason)}\n"
        f"🧠 <b>أسباب الاختيار:</b> {html.escape(' | '.join(opp.reasons[:8]))}\n\n"
        "⚠️ <b>Spot فقط — التنفيذ يدوي.</b>\n"
        "⚠️ هذه ليست ضمان ربح؛ هي تنبيه بأن شروط الزخم والسيولة اكتملت."
    )


def format_signal_event(payload: dict[str, Any]) -> str:
    signal, event = payload["signal"], payload["event"]
    labels = {
        "TP1": "✅ تحقق الهدف الأول — راجع جني جزء من الربح وحرّك الوقف",
        "TP2": "🏁 تحقق الهدف الثاني — راجع إغلاق الباقي",
        "STOP": "🛑 تحقق حد الإلغاء/وقف الخسارة — لا تبقَ في الصفقة",
        "EXPIRED": "⌛ انتهت صلاحية الإشارة دون حسم — ألغِها",
        "ENTRY_EXPIRED": "⌛ انتهت صلاحية أمر LIMIT الافتراضي دون تنفيذ",
    }
    kind = str(event.get("kind", ""))
    price_label = "سعر الإشارة المرجعي" if kind == "EXPIRED" else "المستوى المرصود"
    event_id = f"{signal.get('id', '')}:{kind}:{event.get('at', '')}"
    observed_time = datetime.fromtimestamp(int(event["at"]), tz=timezone.utc).isoformat()
    return (
        f"<b>{labels.get(kind, kind)}</b>\n"
        f"🪙 <b>{html.escape(str(signal.get('symbol', '')))}</b>\n"
        f"{price_label}: <code>{html.escape(str(event.get('price', '')))}</code>\n"
        f"وقت الرصد/الانتهاء (UTC): {observed_time}\n"
        "متابعة سعرية افتراضية، وليست إثبات تنفيذ صفقة أو ربح بعد الرسوم.\n"
        f"معرّف الحدث: <code>{html.escape(event_id)}</code>\n"
        "قد يكون الإشعار متأخرًا؛ تحقق من سعر Paribu الفعلي. التنفيذ يدوي."
    )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def run_scanner() -> None:
    # Guard the public function too: callers may import it instead of main.py.
    if not SHADOW_MODE or os.getenv("SHADOW_MODE", "true").strip().lower() != "true":
        raise RuntimeError("This release requires SHADOW_MODE=true")
    now = int(time.time())
    state = load_state()
    cohort = ensure_cohort(state, now)
    LOGGER.info("RESEARCH_COHORT | id=%s | code=%s | shadow=%s", cohort["id"], os.getenv("GITHUB_SHA"), SHADOW_MODE)
    bind_history(state)
    shadow_mode = SHADOW_MODE
    diagnostics = {"started_at": now, "status": "running", "symbols": {}, "shadow_mode": shadow_mode,
                   "research_cohort": cohort["id"], "code_sha": os.getenv("GITHUB_SHA"),
                   "run_id": os.getenv("GITHUB_RUN_ID"), "transitions": []}
    funnel = {"book_approved": 0, "data_valid": 0, "discovery_passed": 0,
              "early_watch": 0, "early_watch_alerts": 0,
              "confirmed": 0, "limit_simulated": 0}
    obs = {"liq": 0, "spread": 0, "book": 0, "tech": 0, "score": 0, "exec": 0}
    candle_stats = {"complete": 0, "synthetic": 0, "error": 0}
    observations = []

    def note(symbol, stage, reason, **metrics):
        decision = {"stage": stage, "reason": reason, "observed_at": int(time.time()), **metrics}
        diagnostics["symbols"][symbol] = decision
        diagnostics["transitions"].append({"symbol": symbol, **decision})
        LOGGER.info("Scan decision | %s | %s | %s", symbol, stage, reason)
        if metrics and stage in {"book", "discovery", "trigger", "execution", "risk"}:
            LOGGER.info(
                "Scan metrics | %s | %s | %s",
                symbol,
                stage,
                json.dumps(metrics, sort_keys=True, default=str, separators=(",", ":")),
            )

    def finish_diagnostics(status):
        diagnostics["status"] = status
        diagnostics["finished_at"] = int(time.time())
        history = state.setdefault("scan_diagnostics", [])
        if history and isinstance(history[-1], dict):
            previous_started = history[-1].get("started_at")
            if isinstance(previous_started, (int, float)):
                diagnostics["seconds_since_previous_run"] = now - previous_started
        history.append(diagnostics)
        state["scan_diagnostics"] = history[-12:]

    _prune_watchlist(state, now)
    _prune_early_watch_followups(state, now)
    _prune_execution_followups(state, now)

    new_events = update_active_signals(state, now)
    if new_events:
        save_state(state)  # Keep pending observations before attempting delivery.
    if not shadow_mode:
        if deliver_pending_events(state, send_telegram, format_signal_event):
            save_state(state)
    elif new_events:
        diagnostics["shadow_pending_events"] = len(new_events)

    # Near-miss outcome evaluation is intentionally isolated from the scanner.
    # check_near_misses.py owns candle-based maturation and archive updates.

    try:
        snapshot = get_market_snapshot()
    except ParibuDataError as exc:
        LOGGER.error("Paribu snapshot failed: %s", exc)
        finish_diagnostics("snapshot_failed")
        save_state(state)
        raise RuntimeError("Paribu snapshot unavailable; scan incomplete") from exc

    btc_ok, _btc_15, btc_reason = btc_gate()
    diagnostics.update(btc_ok=btc_ok, btc_reason=btc_reason)

    tickers = sorted(
        snapshot.values(),
        key=lambda item: item.quote_volume or Decimal("0"),
        reverse=True,
    )

    discovered: list[Candidate] = []
    orderbook_checked = 0
    technical_checked = 0
    coverage = CoverageCycle(state.setdefault("coverage_scheduler", {}), snapshot)
    orderbook_order, hot_radar = _hot_radar_order(tickers, state, coverage, now)
    hot_symbol_set = set(hot_radar["hot_symbols"])
    hot_orderbook_selected = 0
    book_candidates = []

    # Pre-fill so symbols beyond capacity are not confused with rejected setups.
    for ticker in tickers:
        diagnostics["symbols"][ticker.symbol] = {"stage": "coverage", "reason": "not_evaluated_capacity",
                                                  "observed_at": now}

    for ticker in orderbook_order:
        if ticker.symbol in {"USDT_TL", "USDC_TL", "BTC_TL"}:
            note(ticker.symbol, "universe", "excluded_base_asset")
            continue

        if ticker.quote_volume is None or ticker.quote_volume < MIN_QUOTE_VOLUME_TL:
            note(ticker.symbol, "universe", "quote_volume_below_minimum", quote_volume=str(ticker.quote_volume))
            continue

        if orderbook_checked >= MAX_ORDERBOOK_MARKETS:
            # Continue cheap universe checks for the remaining markets, so a
            # low-volume exclusion is not mislabeled as an API-capacity miss.
            continue
        orderbook_checked += 1
        obs["liq"] += 1
        coverage.attempted("orderbooks", ticker.symbol)
        if ticker.symbol in hot_symbol_set:
            hot_orderbook_selected += 1

        try:
            book = get_order_book(ticker.symbol, ORDERBOOK_DEPTH)
        except Exception as exc:
            note(ticker.symbol, "data", "orderbook_error:" + type(exc).__name__)
            continue

        # Cheap rejection before candle calls.
        if book.spread_percent > MAX_SPREAD_PCT:
            if ticker.symbol in hot_symbol_set or ticker.symbol in _execution_followups(state):
                _record_execution_followup(
                    state, symbol=ticker.symbol, now=now, reason="spread_too_high",
                    spread_pct=book.spread_percent, imbalance=book.imbalance_ratio,
                    best_ask=book.best_ask,
                )
            note(ticker.symbol, "book", "spread_too_high", spread_pct=str(book.spread_percent))
            enqueue_near_miss(
                state,
                symbol=ticker.symbol,
                rejected_price=book.best_ask,
                rejected_stage="book:spread_too_high",
            )
            continue
        obs["spread"] += 1
        if book.imbalance_ratio < MIN_WATCH_IMBALANCE:
            if ticker.symbol in hot_symbol_set or ticker.symbol in _execution_followups(state):
                _record_execution_followup(
                    state, symbol=ticker.symbol, now=now, reason="imbalance_too_low",
                    spread_pct=book.spread_percent, imbalance=book.imbalance_ratio,
                    best_ask=book.best_ask,
                )
            note(ticker.symbol, "book", "imbalance_too_low", imbalance=str(book.imbalance_ratio))
            enqueue_near_miss(
                state,
                symbol=ticker.symbol,
                rejected_price=book.best_ask,
                rejected_stage="book:imbalance_too_low",
            )
            continue

        obs["book"] += 1
        # Do not clear a transient execution follow-up merely because one book
        # snapshot recovered. Keep its bounded priority through candle/data
        # validation; otherwise a temporary synthetic-candle failure can erase
        # a correctly detected hot candidate before the next scan.
        book_candidates.append((ticker, book))
        funnel["book_approved"] += 1

    # Independent fairness at the second budget prevents the same book-approved
    # markets from repeatedly losing the last technical-analysis slots.
    technical_order, technical_priority = coverage.prioritize(
        book_candidates, "technicals", lambda item: item[0].symbol,
        hot_radar["priority_symbols"], MAX_TECHNICAL_MARKETS,
    )
    for ticker, book in technical_order:
        if technical_checked >= MAX_TECHNICAL_MARKETS:
            note(ticker.symbol, "coverage", "not_evaluated_technical_capacity")
            continue
        technical_checked += 1
        coverage.attempted("technicals", ticker.symbol)

        candle_frames: dict[str, Any] = {}
        candle_fetch_failed = False
        for timeframe in ("15m", "1h", "4h"):
            try:
                candle_frames[timeframe] = fetch_candles(
                    ticker.symbol, timeframe, CANDLE_LIMIT
                )
            except Exception as exc:
                candle_stats["error"] += 1
                note(
                    ticker.symbol,
                    "data",
                    "candle_error:" + _candle_error_reason_code(exc),
                    timeframe=timeframe,
                    error_type=type(exc).__name__,
                    error_detail=str(exc)[:240],
                )
                candle_fetch_failed = True
                break
        if candle_fetch_failed:
            continue

        df_15 = candle_frames["15m"]
        df_1h = candle_frames["1h"]
        df_4h = candle_frames["4h"]

        candle_stats["synthetic"] += _candle_synthetic_rows(df_15, df_1h, df_4h)
        if not all(
            str(frame.attrs.get("source", "")).upper() == "PARIBU"
            for frame in (df_15, df_1h, df_4h)
        ):
            note(ticker.symbol, "data", "non_paribu_candles")
            continue

        # Trading decisions are fail-closed: no synthetic row may influence
        # indicators, score, confirmation, or BTC regime decisions.
        if not _all_candles_authentic(df_15, df_1h, df_4h):
            note(ticker.symbol, "data", "synthetic_candles_present")
            continue
        if not recent_authentic(df_15, 4, 900):
            note(ticker.symbol, "data", "recent_15m_integrity_failed")
            continue
        if not recent_authentic(df_1h, 2, 3600):
            note(ticker.symbol, "data", "recent_1h_integrity_failed")
            continue
        if not recent_authentic(df_4h, 1, 14400):
            note(ticker.symbol, "data", "recent_4h_integrity_failed")
            continue

        funnel["data_valid"] += 1
        candle_stats["complete"] += 1

        # Keep an execution follow-up until it hands over to a strategy watch
        # or passes the final refreshed quote. A preliminary book recovery
        # must not erase its bounded priority or restart its fixed TTL.

        # Collect existing data only. Evaluate AFTER the normal alert path, with
        # no extra requests or interference with entry thresholds/cooldowns.
        observations.append((ticker.symbol, df_15, book))

        tech_15 = analyze_symbol(df_15)
        tech_1h = analyze_symbol(df_1h)
        tech_4h = analyze_symbol(df_4h)
        if tech_15 is None or tech_1h is None or tech_4h is None:
            note(ticker.symbol, "data", "indicators_unavailable")
            continue

        obs["tech"] += 1
        score, reasons = score_candidate(ticker, book, tech_15, tech_1h, tech_4h)
        if score >= DISCOVERY_MIN_SCORE:
            obs["score"] += 1
        close = dec(getattr(tech_15, "current_close", None))
        atr = dec(getattr(tech_15, "atr14", None))
        indicator_metrics = {
            "score": score, "rsi": str(tech_15.rsi14),
            "return_3": str(tech_15.recent_return_3),
            "volume_ratio": str(tech_15.volume_ratio),
            "atr_pct": str(atr / close * 100) if close and close > 0 and atr is not None else None,
            "macd_histogram": str(getattr(tech_15, "macd_histogram", "")),
            "closed_candle_at": int(df_15["timestamp"].iloc[-1]),
            "authentic_only": True,
        }
        ok, discovery_reason = discovery_ok(
            ticker, book, tech_15, tech_1h, tech_4h, score
        )
        if not ok:
            _execution_followups(state).pop(ticker.symbol, None)
            note(ticker.symbol, "discovery", discovery_reason, **indicator_metrics)
            if _early_watch_mtf_ok(book, tech_15, score, btc_ok, discovery_reason):
                funnel["early_watch"] += 1
                _record_early_watch_followup(
                    state,
                    symbol=ticker.symbol,
                    now=now,
                    candle_at=int(df_15["timestamp"].iloc[-1]),
                    score=score,
                    reason=discovery_reason,
                )
                _candidate_lifecycle_update(
                    state,
                    symbol=ticker.symbol,
                    score=score,
                    lifecycle_state="early_watch",
                    reason=discovery_reason,
                    now=now,
                )
                recorded = record_near_miss(
                    state,
                    symbol=ticker.symbol,
                    gate="EARLY_WATCH_MTF",
                    reason=discovery_reason,
                    reference_price=book.best_ask,
                    score=score,
                    spread_pct=book.spread_percent,
                    imbalance=book.imbalance_ratio,
                    rsi_15m=tech_15.rsi14,
                    volume_ratio_15m=tech_15.volume_ratio,
                )
                enqueue_near_miss(
                    state,
                    symbol=ticker.symbol,
                    rejected_price=book.best_ask,
                    rejected_stage="early_watch_mtf:" + discovery_reason.replace(" ", "_"),
                    now=now,
                )
                if recorded:
                    sent_watch = send_early_watch_alert(
                        symbol=ticker.symbol,
                        score=score,
                        reason=discovery_reason,
                        book=book,
                        tech_15=tech_15,
                        btc_reason=btc_reason,
                    )
                    if sent_watch:
                        funnel["early_watch_alerts"] += 1
                    LOGGER.info(
                        "EARLY_WATCH | %s | reason=%s | score=%d | spread=%s | imbalance=%s | "
                        "volume_ratio=%s | rsi=%s | sent=%s",
                        ticker.symbol,
                        discovery_reason,
                        score,
                        book.spread_percent,
                        book.imbalance_ratio,
                        tech_15.volume_ratio,
                        tech_15.rsi14,
                        sent_watch,
                    )
            continue

        note(ticker.symbol, "discovery", "passed_pending_trigger", **indicator_metrics)
        _early_watch_followups(state).pop(ticker.symbol, None)

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
            candle_at=int(df_15["timestamp"].iloc[-1]),
        )

        discovered.append(candidate)
        funnel["discovery_passed"] += 1
        _update_watchlist(state, candidate, now)
        _candidate_lifecycle_update(state, symbol=candidate.symbol, score=candidate.score,
                                    lifecycle_state="discovery", now=now)

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
    telegram_alert_sent = False

    for candidate in discovered:
        item = _watchlist(state).get(candidate.symbol)
        if not isinstance(item, dict):
            continue

        ready, trigger_reason, avg_imbalance, confirmations = trigger_check(
            candidate, item, btc_ok, btc_reason, now
        )

        if not ready:
            note(candidate.symbol, "trigger", trigger_reason, score=candidate.score)
            lifecycle_state = ("confirmation_1_of_2" if trigger_reason.startswith("watching: confirmations 1/")
                               else "rejected")
            _candidate_lifecycle_update(state, symbol=candidate.symbol, score=candidate.score,
                                        lifecycle_state=lifecycle_state, reason=trigger_reason, now=now)
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
            if 70 <= candidate.score <= 79 and trigger_reason.startswith("score "):
                enqueue_near_miss(
                    state,
                    symbol=candidate.symbol,
                    rejected_price=candidate.book.best_ask,
                    rejected_stage="score_70_79",
                )

            # A candidate that has already cleared the alert score is not dead
            # merely because a time-sensitive trigger gate is temporarily out
            # of range (RLC_TL: score 83, then RSI 69.3). Preserve bounded
            # priority so the next CLOSED 15m candle re-evaluates the full
            # trigger. This changes scheduling only: no score/RSI/book gate is
            # relaxed and no READY confirmation is granted here.
            if candidate.score >= NEAR_MISS_MIN_SCORE:
                _record_early_watch_followup(
                    state,
                    symbol=candidate.symbol,
                    now=now,
                    candle_at=candidate.candle_at,
                    score=candidate.score,
                    reason=trigger_reason,
                )
            _execution_followups(state).pop(candidate.symbol, None)
            continue

        if not _global_alert_allowed(state, now, shadow_mode):
            note(candidate.symbol, "cooldown", "global_limit_or_cooldown")
            LOGGER.info("Strong candidate %s ready, global alert cooldown active", candidate.symbol)
            continue

        if not _symbol_alert_allowed(state, candidate.symbol, now, shadow_mode):
            note(candidate.symbol, "cooldown", "symbol_cooldown")
            continue

        # Re-read the book immediately before pricing a notification. A scan can
        # take minutes; its earlier book must not masquerade as an executable quote.
        try:
            candidate = replace(candidate, book=get_order_book(candidate.symbol))
        except ParibuDataError:
            _record_execution_followup(
                state, symbol=candidate.symbol, now=int(time.time()),
                reason="fresh_book_unavailable",
            )
            note(candidate.symbol, "execution", "fresh_book_unavailable")
            continue
        ready, trigger_reason, avg_imbalance, confirmations = trigger_check(
            candidate, item, btc_ok, btc_reason, int(time.time())
        )
        if not ready:
            _record_execution_followup(
                state, symbol=candidate.symbol, now=int(time.time()),
                reason="fresh_book_rejected: " + trigger_reason,
                spread_pct=candidate.book.spread_percent,
                imbalance=candidate.book.imbalance_ratio,
                best_ask=candidate.book.best_ask,
            )
            note(candidate.symbol, "execution", "fresh_book_rejected: " + trigger_reason)
            continue
        _execution_followups(state).pop(candidate.symbol, None)
        funnel["confirmed"] += 1
        _candidate_lifecycle_update(state, symbol=candidate.symbol, score=candidate.score,
                                    lifecycle_state="confirmation_2_of_2", reason="READY", now=now)
        opp = build_opportunity(candidate, avg_imbalance, confirmations, btc_reason)
        if opp is None:
            risk_result = build_risk_plan_result(
                book=candidate.book,
                tech=candidate.tech_15,
                setup=candidate.setup,
                atr_multiplier=Decimal(os.getenv("ATR_STOP_MULTIPLIER_V2", "1.75")),
                max_risk_pct=Decimal(os.getenv("MAX_STOP_PCT_V2", "4.00")),
                min_rr=Decimal(os.getenv("MIN_REWARD_RISK", "1.50")),
                tp1_pct=TP1_PCT,
                tp2_pct=TP2_PCT,
            )
            risk_reason = risk_result.rejection_reason or "opportunity_unavailable"
            note(candidate.symbol, "risk", risk_reason)
            _candidate_lifecycle_update(state, symbol=candidate.symbol, score=candidate.score,
                                        lifecycle_state="rejected", reason=risk_reason, now=now)
            if risk_reason in {"tp1_blocked_by_wall", "rr_below_min"}:
                enqueue_near_miss(
                    state,
                    symbol=candidate.symbol,
                    rejected_price=candidate.book.best_ask,
                    rejected_stage=f"execution:{risk_reason}",
                )
            continue

        obs["exec"] += 1
        depth_scenario = evaluate_depth(candidate.book)
        state["execution_research"] = {"symbol": candidate.symbol, "observed_at": int(time.time()),
                                       **depth_scenario}
        evidence = {
            "btc_reason": btc_reason,
            "spread_pct": str(opp.spread_pct),
            "imbalance": str(opp.imbalance),
            "volume_ratio": str(opp.volume_ratio),
            "quote_volume_tl": str(opp.quote_volume),
            "bid_wall_share": str(opp.bid_wall_share),
            "ask_wall_share": str(opp.ask_wall_share),
            "execution_research": depth_scenario,
            "shadow_mode": shadow_mode,
        }

        if shadow_mode:
            note(candidate.symbol, "shadow", "paper_signal_recorded")
            _candidate_lifecycle_update(state, symbol=candidate.symbol, score=candidate.score,
                                        lifecycle_state="shadow_entry", reason="paper_signal_recorded", now=now)
            if TELEGRAM_READY_ALERTS:
                telegram_alert_sent = send_shadow_ready_alert(opp)
                if telegram_alert_sent:
                    LOGGER.info(
                        "SHADOW READY Telegram alert sent: %s score=%d confirmations=%d",
                        candidate.symbol, candidate.score, confirmations,
                    )
                else:
                    LOGGER.error("SHADOW READY Telegram alert failed: %s", candidate.symbol)
            sent = True
            placed_at = int(time.time())
            state["shadow_last_alert_at"] = placed_at
            state.setdefault("shadow_daily_alerts", []).append(placed_at)
            state.setdefault("shadow_sent_signals", {})[candidate.symbol] = placed_at
            funnel["limit_simulated"] += 1
            register_signal(
                state, symbol=opp.symbol, entry=opp.entry, stop=opp.stop,
                tp1=opp.tp1, tp2=opp.tp2, score=opp.score,
                setup=opp.setup, now=placed_at, evidence=evidence,
            )
            LOGGER.info(
                "SHADOW signal: %s score=%d confirmations=%d entry=%s stop=%s tp1=%s",
                candidate.symbol, candidate.score, confirmations,
                opp.entry, opp.stop, opp.tp1,
            )
            break

        if send_telegram(format_opportunity(opp)):
            note(candidate.symbol, "notification", "entry_alert_sent")
            telegram_alert_sent = True
            sent = True
            state["last_alert_at"] = now
            state.setdefault("daily_alerts", []).append(now)
            state.setdefault("sent_signals", {})[candidate.symbol] = now
            register_signal(
                state, symbol=opp.symbol, entry=opp.entry, stop=opp.stop,
                tp1=opp.tp1, tp2=opp.tp2, score=opp.score,
                setup=opp.setup, now=now, evidence=evidence,
            )
            LOGGER.info(
                "ALERT sent: %s score=%d confirmations=%d volume=%.2fx imbalance=%.2f",
                candidate.symbol,
                candidate.score,
                confirmations,
                candidate.tech_15.volume_ratio,
                candidate.book.imbalance_ratio,
            )
            break
        else:
            note(candidate.symbol, "notification", "telegram_failed")

    if os.getenv("SHADOW_RADAR_ENABLED", "true").lower() == "true":
        try:
            from accumulation_radar import advance
            state["accumulation_radar"] = advance(
                state.get("accumulation_radar"), observations, snapshot, now, btc_ok, btc_reason, cohort["id"]
            )
            diagnostics["radar_status"] = "shadow_only"
        except Exception as exc:
            # Radar failures must never prevent persistence of actual signals.
            diagnostics["radar_status"] = "error:" + type(exc).__name__
            LOGGER.warning("Shadow radar failed: %s", type(exc).__name__)
    else:
        diagnostics["radar_status"] = "disabled"
    funnel.update(universe=len(snapshot), orderbooks_attempted=orderbook_checked,
                  technicals_attempted=technical_checked,
                  universe_excluded=sum(d["stage"] == "universe" for d in diagnostics["symbols"].values()))
    diagnostics.update(markets=len(snapshot), orderbooks_checked=orderbook_checked,
                       technical_checked=technical_checked, discovered=len(discovered),
                       signal_recorded=sent, alert_sent=telegram_alert_sent, funnel=funnel)
    diagnostics["coverage_generation"] = coverage.state["generation"]
    diagnostics["orderbook_markets_seen"] = len(coverage.state["orderbooks"])
    diagnostics["hot_radar"] = {
        "version": 2,
        "eligible_count": hot_radar["eligible_count"],
        "selected_orderbooks": hot_orderbook_selected,
        "hot_symbols": hot_radar["hot_symbols"],
        "snapshot_comparable": hot_radar["snapshot_comparable"],
        "priority_budget": hot_radar["priority_budget"],
        "confirmation_symbols": hot_radar["confirmation_symbols"],
        "selected_priority_symbols": hot_radar["selected_priority_symbols"],
        "technical_priority_symbols": technical_priority,
    }
    finish_diagnostics("completed")

    try:
        from research_report import build_report
        state["research_summary"] = build_report(state, now=int(time.time()))
    except Exception as exc:
        state.pop("research_summary", None)
        LOGGER.warning("Research report unavailable: %s", type(exc).__name__)

    # No Telegram empty reports. GitHub log is enough.
    LOGGER.info(
        "Run complete | markets=%d | discovered=%d | watchlist=%d | btc_ok=%s | shadow=%s | signal=%s",
        len(snapshot),
        len(discovered),
        len(_watchlist(state)),
        btc_ok,
        shadow_mode,
        sent,
    )
    stage_drops = {}
    for decision in diagnostics["symbols"].values():
        if isinstance(decision, dict):
            key = f"{decision.get('stage', 'unknown')}:{decision.get('reason', 'unknown')}"
            stage_drops[key] = stage_drops.get(key, 0) + 1

    runs = state.setdefault("directive_009_runs", [])
    if not isinstance(runs, list):
        runs = []
        state["directive_009_runs"] = runs
    cadence_gap_seconds, cadence_status = _cadence_health(runs, now)

    run_record = {
        "run_id": _run_id(),
        "started_at": now,
        "finished_at": int(time.time()),
        "btc_ok": bool(btc_ok),
        "btc_reason": _btc_gate_reason_code(btc_reason),
        "candles_complete": candle_stats["complete"],
        "candles_synthetic": candle_stats["synthetic"],
        "candles_error": candle_stats["error"],
        "cadence_expected_seconds": EXPECTED_RUN_INTERVAL_SECONDS,
        "cadence_late_after_seconds": CADENCE_LATE_AFTER_SECONDS,
        "cadence_gap_seconds": cadence_gap_seconds,
        "cadence_status": cadence_status,
        "hot_radar_eligible": hot_radar["eligible_count"],
        "hot_radar_selected": hot_orderbook_selected,
        "funnel": {
            "universe": len(snapshot), "liq": obs["liq"], "spread": obs["spread"],
            "book": obs["book"], "tech": obs["tech"], "score": obs["score"],
            "early_watch": funnel["early_watch"],
            "early_watch_alerts": funnel["early_watch_alerts"],
            "candidates": len(discovered), "confirmed": funnel["confirmed"],
            "exec": obs["exec"], "limit": funnel["limit_simulated"],
        },
        "stage_drops": stage_drops,
    }
    runs.append(run_record)
    state["directive_009_runs"] = runs[-32:]

    # Persist first; only then claim state_saved=OK in the observability line.
    save_state(state)
    print(
        "[INFRA_HEALTH] "
        f"run_id={_run_id()} btc_ok={btc_ok} btc_reason={_btc_gate_reason_code(btc_reason)} "
        f"candles_complete={candle_stats['complete']} "
        f"candles_synthetic={candle_stats['synthetic']} "
        f"candles_error={candle_stats['error']} "
        f"cadence_expected_seconds={EXPECTED_RUN_INTERVAL_SECONDS} "
        f"cadence_gap_seconds={cadence_gap_seconds if cadence_gap_seconds is not None else 'na'} "
        f"cadence_status={cadence_status} state_saved=OK",
        flush=True,
    )
    print(
        "[FUNNEL_BEHAVIOR] "
        f"run_id={_run_id()} universe={len(snapshot)} liq={obs['liq']} spread={obs['spread']} "
        f"book={obs['book']} tech={obs['tech']} data_valid={funnel['data_valid']} "
        f"score={obs['score']} early_watch={funnel['early_watch']} "
        f"watch_alerts={funnel['early_watch_alerts']} candidates={len(discovered)} "
        f"confirmed={funnel['confirmed']} exec={obs['exec']} limit={funnel['limit_simulated']} "
        f"stage_drops={json.dumps(stage_drops, sort_keys=True, separators=(',', ':'))}",
        flush=True,
    )


if __name__ == "__main__":
    if not SHADOW_MODE:
        raise RuntimeError("This release requires SHADOW_MODE=true")
    run_scanner()
