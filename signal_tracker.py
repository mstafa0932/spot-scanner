from __future__ import annotations

"""Chronological paper tracking for every Telegram entry signal."""

from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import time

from market_data import fetch_candles, get_order_book
from risk_engine import breakeven_trigger_price, protected_breakeven_stop

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
                    now: Optional[int] = None, evidence: Optional[dict] = None) -> None:
    opened_at = int(time.time() if now is None else now)
    _signals(state).append({
        "id": f"{symbol}:{opened_at}", "symbol": symbol,
        "opened_at": opened_at, "entry": str(entry), "stop": str(stop),
        "tp1": str(tp1), "tp2": str(tp2), "score": score, "setup": setup,
        "risk_budget_pct": str(RISK_BUDGET_PCT), "status": "OPEN",
        "tp1_notified": False, "events": [],
        "tracking_mode": "paper_price_observation", "fill_confirmed": False,
        "evidence": evidence or {},
        "breakeven_armed": False,
        "breakeven_stop": None,
    })


def _event(signal: dict[str, Any], kind: str, price: Decimal, at: int) -> dict[str, Any]:
    event = {"kind": kind, "price": str(price), "at": int(at), "delivered": False}
    signal.setdefault("events", []).append(event)
    return {"signal": signal, "event": event}


def update_active_signals(state: dict[str, Any], now: Optional[int] = None) -> list[dict[str, Any]]:
    """Emit newly observed events; ambiguous candles are counted as STOP first."""
    checked_at = int(time.time() if now is None else now)
    emitted: list[dict[str, Any]] = []
    for signal in _signals(state):
        if signal.get("status") not in {"OPEN", "TP1"}:
            continue
        entry, stop, tp1, tp2 = map(_d, (signal.get("entry"), signal.get("stop"),
                                         signal.get("tp1"), signal.get("tp2")))
        if any(x is None for x in (entry, stop, tp1, tp2)):
            signal["status"] = "INVALID"
            continue
        if not (0 < stop < entry < tp1 < tp2):
            signal["status"] = "INVALID"
            continue
        opened_at = int(signal.get("opened_at", 0) or 0)
        expires_at = opened_at + MAX_SIGNAL_AGE_SECONDS
        if checked_at < opened_at:
            continue
        try:
            candles = fetch_candles(str(signal["symbol"]), "15m", 250)
            # A candle timestamp is its OPEN time. Use only complete bars wholly
            # after entry and ending within the observation lifetime. A partial
            # entry/expiry candle cannot tell us which side of the boundary hit.
            future = candles[
                (candles["timestamp"] >= opened_at)
                & (candles["timestamp"] + 900 <= min(checked_at, expires_at))
            ].sort_values("timestamp").drop_duplicates("timestamp")
            future = future[future["timestamp"] > signal.get("last_processed_candle", -1)]
            signal["tracking_data_error"] = None
        except Exception:
            future = None
            signal["tracking_data_error"] = "candles_unavailable"

        if future is not None:
            for row in future.itertuples(index=False):
                low, high = _d(row.low), _d(row.high)
                candle_at = int(row.timestamp) + 900
                if low is None or high is None or low <= 0 or high < low:
                    continue
                signal["last_processed_candle"] = int(row.timestamp)

                active_stop = _d(signal.get("breakeven_stop")) or stop
                if low <= active_stop:  # conservative if stop and upside trigger share a candle
                    signal["status"] = "STOP"
                    emitted.append(_event(signal, "STOP", active_stop, candle_at))
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

                # Arm breakeven only after the candle survived the original/effective
                # stop. Intrabar ordering is unknowable from OHLC, so the new stop
                # becomes effective from the next observation.
                if not signal.get("breakeven_armed"):
                    trigger = breakeven_trigger_price(entry, tp1)
                    if high >= trigger:
                        evidence = signal.get("evidence") if isinstance(signal.get("evidence"), dict) else {}
                        spread_pct = _d(evidence.get("spread_pct")) or Decimal("0")
                        be_stop = protected_breakeven_stop(entry, spread_pct=spread_pct)
                        if stop < be_stop < tp1:
                            signal["breakeven_armed"] = True
                            signal["breakeven_stop"] = str(be_stop)
                            emitted.append(_event(signal, "BREAKEVEN_ARMED", be_stop, candle_at))

        if signal.get("status") in {"OPEN", "TP1"} and checked_at <= expires_at:
            try:
                bid = _d(get_order_book(str(signal["symbol"]), 5).best_bid)
            except Exception:
                bid = None
            active_stop = _d(signal.get("breakeven_stop")) or stop
            if bid is not None and bid > 0 and bid <= active_stop:
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
            payload = _event(signal, "EXPIRED", entry, expires_at)
            payload["event"]["price_basis"] = "entry_reference_not_current_price"
            emitted.append(payload)

    state["active_signals"] = [s for s in _signals(state)
                               if checked_at - int(s.get("opened_at", 0) or 0) <= 14 * 86400]
    return emitted


def deliver_pending_events(state: dict[str, Any], sender, formatter) -> int:
    """Retry explicit pending events; legacy events are not sent again.

    Delivery is at-least-once: a crash after send but before state persistence
    can duplicate a message. Event IDs make duplicates identifiable. Failed
    delivery never rolls back a price observation or marks it as delivered.
    """
    delivered = 0
    for signal in _signals(state):
        for event in signal.get("events", []):
            if event.get("delivered") is not False:
                continue
            payload = {"signal": signal, "event": event}
            try:
                success = sender(formatter(payload))
            except Exception:
                success = False
            if not success:
                # Preserve chronology; do not deliver TP2 ahead of pending TP1.
                break
            event["delivered"] = True
            delivered += 1
    return delivered
