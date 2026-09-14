from __future__ import annotations

"""Chronological paper tracking for every Telegram entry signal."""

from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import time

from market_data import fetch_candles, get_order_book

MAX_SIGNAL_AGE_SECONDS = 6 * 60 * 60
RISK_BUDGET_PCT = Decimal("2.00")


def _d(value: Any) -> Optional[Decimal]:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _signals(state: dict[str, Any]) -> list[dict[str, Any]]:
    value = state.get("active_signals")
    if not isinstance(value, list):
        value = []
        state["active_signals"] = value
    return value


def register_signal(state: dict[str, Any], *, symbol: str, entry: Any, stop: Any,
                    tp1: Any, tp2: Any, score: int, setup: str,
                    now: Optional[int] = None) -> None:
    opened_at = int(now or time.time())
    _signals(state).append({
        "id": f"{symbol}:{opened_at}", "symbol": symbol,
        "opened_at": opened_at, "entry": str(entry), "stop": str(stop),
        "tp1": str(tp1), "tp2": str(tp2), "score": score, "setup": setup,
        "risk_budget_pct": str(RISK_BUDGET_PCT), "status": "OPEN",
        "tp1_notified": False, "events": [],
    })


def _event(signal: dict[str, Any], kind: str, price: Decimal, at: int) -> dict[str, Any]:
    event = {"kind": kind, "price": str(price), "at": int(at)}
    signal.setdefault("events", []).append(event)
    return {"signal": signal, "event": event}


def update_active_signals(state: dict[str, Any], now: Optional[int] = None) -> list[dict[str, Any]]:
    """Emit newly observed events; ambiguous candles are counted as STOP first."""
    checked_at = int(now or time.time())
    emitted: list[dict[str, Any]] = []
    for signal in _signals(state):
        if signal.get("status") not in {"OPEN", "TP1"}:
            continue
        entry, stop, tp1, tp2 = map(_d, (signal.get("entry"), signal.get("stop"),
                                         signal.get("tp1"), signal.get("tp2")))
        if any(x is None for x in (entry, stop, tp1, tp2)):
            signal["status"] = "INVALID"
            continue
        opened_at = int(signal.get("opened_at", 0) or 0)
        try:
            candles = fetch_candles(str(signal["symbol"]), "15m", 250)
            future = candles[candles["timestamp"] > opened_at].sort_values("timestamp")
        except Exception:
            future = None

        if future is not None:
            for row in future.itertuples(index=False):
                low, high, candle_at = _d(row.low), _d(row.high), int(row.timestamp)
                if low is None or high is None:
                    continue
                if low <= stop:  # conservative when stop and target share a candle
                    signal["status"] = "STOP"
                    emitted.append(_event(signal, "STOP", stop, candle_at))
                    break
                if high >= tp2:
                    if not signal.get("tp1_notified"):
                        signal["tp1_notified"] = True
                        emitted.append(_event(signal, "TP1", tp1, candle_at))
                    signal["status"] = "TP2"
                    emitted.append(_event(signal, "TP2", tp2, candle_at))
                    break
                if high >= tp1 and not signal.get("tp1_notified"):
                    signal["tp1_notified"] = True
                    signal["status"] = "TP1"
                    emitted.append(_event(signal, "TP1", tp1, candle_at))

        if signal.get("status") in {"OPEN", "TP1"}:
            try:
                bid = _d(get_order_book(str(signal["symbol"]), 5).best_bid)
            except Exception:
                bid = None
            if bid is not None and bid <= stop:
                signal["status"] = "STOP"
                emitted.append(_event(signal, "STOP", bid, checked_at))
            elif bid is not None and bid >= tp2:
                if not signal.get("tp1_notified"):
                    signal["tp1_notified"] = True
                    emitted.append(_event(signal, "TP1", tp1, checked_at))
                signal["status"] = "TP2"
                emitted.append(_event(signal, "TP2", bid, checked_at))
            elif bid is not None and bid >= tp1 and not signal.get("tp1_notified"):
                signal["tp1_notified"] = True
                signal["status"] = "TP1"
                emitted.append(_event(signal, "TP1", bid, checked_at))

        if signal.get("status") in {"OPEN", "TP1"} and checked_at - opened_at >= MAX_SIGNAL_AGE_SECONDS:
            signal["status"] = "EXPIRED"
            emitted.append(_event(signal, "EXPIRED", entry, checked_at))

    state["active_signals"] = [s for s in _signals(state)
                               if checked_at - int(s.get("opened_at", 0) or 0) <= 14 * 86400]
    return emitted
