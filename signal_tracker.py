from __future__ import annotations

"""Chronological paper tracking for every Telegram entry signal."""

from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import time

from pandas.api.types import is_bool

from market_data import fetch_candles, get_order_book
from risk_engine import breakeven_trigger_price, protected_breakeven_stop

MAX_SIGNAL_AGE_SECONDS = 6 * 60 * 60
SHADOW_ENTRY_TTL_SECONDS = 90 * 60
RISK_BUDGET_PCT = Decimal("2.00")
CANDLE_SECONDS = 15 * 60
SIMULATION_VERSION = "ohlc_penetration_v2"


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
    evidence = evidence or {}
    shadow = bool(evidence.get("shadow_mode"))
    _signals(state).append({
        "id": f"{symbol}:{opened_at}", "symbol": symbol,
        "opened_at": opened_at, "entry": str(entry), "stop": str(stop),
        "tp1": str(tp1), "tp2": str(tp2), "score": score, "setup": setup,
        "risk_budget_pct": str(RISK_BUDGET_PCT),
        "status": "PENDING_ENTRY" if shadow else "OPEN",
        "tp1_notified": False, "events": [],
        "tracking_mode": "shadow_limit_simulation" if shadow else "paper_price_observation",
        "fill_confirmed": False,
        "fill_estimated": False,
        "simulation_version": SIMULATION_VERSION if shadow else None,
        "evidence": evidence,
        "breakeven_armed": False,
        "breakeven_stop": None,
    })


def _event(signal: dict[str, Any], kind: str, price: Decimal, at: int) -> dict[str, Any]:
    event = {"kind": kind, "price": str(price), "at": int(at), "delivered": False}
    if signal.get("tracking_mode") == "shadow_limit_simulation":
        event["simulation_version"] = signal.get("simulation_version")
        event["execution_basis"] = "estimated_not_exchange_confirmed"
    signal.setdefault("events", []).append(event)
    return {"signal": signal, "event": event}


def _expire_entry(signal: dict[str, Any], entry: Decimal, deadline: int) -> dict[str, Any]:
    signal["status"] = "ENTRY_EXPIRED"
    payload = _event(signal, "ENTRY_EXPIRED", entry, deadline)
    payload["event"]["price_basis"] = "unfilled_limit_reference"
    return payload


def _skip_candle(signal: dict[str, Any], at: int, reason: str) -> None:
    # Keep evidence of incomplete observations across runs. Skipped rows must
    # never silently turn into a clean performance sample on the next scan.
    signal["tracking_incomplete"] = True
    signal["tracking_data_error"] = reason
    skipped = signal.setdefault("skipped_tracking_candles", {})
    skipped[str(at)] = reason


def update_active_signals(state: dict[str, Any], now: Optional[int] = None) -> list[dict[str, Any]]:
    """Replay authentic closed bars in event time; OHLC fills are estimates.

    Only bars wholly inside the pending order's lifetime can estimate a fill.
    Delayed scans may replay pre-deadline bars, but never post-deadline prices.
    No queue position, executed quantity, or exchange fill is inferred from OHLC.
    """
    checked_at = int(time.time() if now is None else now)
    emitted: list[dict[str, Any]] = []
    for signal in _signals(state):
        if signal.get("status") not in {"PENDING_ENTRY", "OPEN", "TP1"}:
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
        entry_deadline = opened_at + SHADOW_ENTRY_TTL_SECONDS
        if checked_at < opened_at:
            continue
        try:
            candles = fetch_candles(str(signal["symbol"]), "15m", 250)
            # A candle timestamp is its OPEN time. Use only complete bars wholly
            # after entry and ending within the observation lifetime. A partial
            # entry/expiry candle cannot tell us which side of the boundary hit.
            future = candles[
                (candles["timestamp"] >= opened_at)
                & (candles["timestamp"] + CANDLE_SECONDS <= min(checked_at, expires_at))
            ].sort_values("timestamp").drop_duplicates("timestamp")
            future = future[future["timestamp"] > signal.get("last_processed_candle", -1)]
            signal["tracking_data_error"] = None
        except Exception:
            future = None
            signal["tracking_data_error"] = "candles_unavailable"
            signal["tracking_incomplete"] = True

        if future is not None:
            for row in future.itertuples(index=False):
                candle_open = int(row.timestamp)
                candle_at = candle_open + CANDLE_SECONDS
                # Evaluate expiry BEFORE authenticity, prices, or fill checks.
                # A bar closing exactly at the deadline covers only earlier
                # trades; a straddling bar cannot localize a pre-expiry fill.
                if signal.get("status") == "PENDING_ENTRY":
                    if candle_open >= entry_deadline:
                        emitted.append(_expire_entry(signal, entry, entry_deadline))
                        break
                    if candle_at > entry_deadline:
                        _skip_candle(signal, candle_open, "entry_expiry_boundary_ambiguous")
                        continue

                authentic = getattr(row, "is_authentic", None)
                if not is_bool(authentic) or not bool(authentic):
                    _skip_candle(signal, candle_open, "inauthentic_or_unverified_candle")
                    continue

                low, high = _d(row.low), _d(row.high)
                if low is None or high is None or low <= 0 or high < low:
                    _skip_candle(signal, candle_open, "invalid_candle_prices")
                    continue

                if signal.get("status") == "PENDING_ENTRY":
                    volume = _d(getattr(row, "volume", None))
                    if volume is None or volume <= 0:
                        _skip_candle(signal, candle_open, "no_positive_trade_volume")
                        continue
                    signal["last_processed_candle"] = candle_open
                    # Candle volume is NOT volume at/below the order. Even a
                    # large volume cannot make a touch-only bar a valid fill.
                    if low < entry <= high:
                        signal["fill_confirmed"] = False
                        signal["fill_estimated"] = True
                        signal["simulation_version"] = SIMULATION_VERSION
                        signal["filled_at"] = candle_at
                        signal["fill_evidence"] = {
                            "model": SIMULATION_VERSION,
                            "basis": "authentic_positive_volume_price_penetration",
                            "candle_open": candle_open,
                            "candle_close": candle_at,
                            "low": str(low), "high": str(high),
                            "volume": str(volume),
                            "time_basis": "bar_close_upper_bound_not_exact_fill_time",
                            "quantity_basis": "unknown_no_queue_or_partial_fill_model",
                        }
                        signal["status"] = "OPEN"
                        # Conservative OHLC treatment: if the fill candle also
                        # pierced the stop, count the stop; do not award upside
                        # targets on the ambiguous fill candle.
                        if low <= stop:
                            signal["status"] = "STOP"
                            emitted.append(_event(signal, "STOP", stop, candle_at))
                            break
                    # Pending orders cannot earn TP/STOP or arm breakeven.
                    # Neither can the ambiguous fill bar earn upside targets.
                    continue
                signal["last_processed_candle"] = candle_open

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
                            signal["breakeven_armed_at"] = candle_at

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

        if signal.get("status") == "PENDING_ENTRY" and checked_at - opened_at >= SHADOW_ENTRY_TTL_SECONDS:
            emitted.append(_expire_entry(signal, entry, entry_deadline))
        elif signal.get("status") in {"OPEN", "TP1"} and checked_at - opened_at >= MAX_SIGNAL_AGE_SECONDS:
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
        evidence = signal.get("evidence")
        if isinstance(evidence, dict) and evidence.get("shadow_mode") is True:
            # Shadow observations are research-only and must never leak into
            # Telegram if production mode is enabled later.
            continue
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
