"""Experimental, observation-only accumulation hypotheses; never trading signals.

No network, Telegram, wallet attribution, or changes to entry gates. All thresholds
are unvalidated research defaults. Order-book imbalance is NOT executed buying.
"""
from __future__ import annotations

from copy import deepcopy
import math

INTERVAL = 900
HORIZONS = (3600, 14400, 86400)
MAX_EVENTS = 200


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate(frame, book, now):
    """Return (hypothesis, reason); require contiguous, recent CLOSED 15m bars."""
    if frame.attrs.get("source") != "PARIBU" or frame.attrs.get("resolution") != "15m":
        return None, "source_or_timeframe"
    if len(frame) < 49:
        return None, "insufficient_history"
    rows = frame.tail(49).to_dict("records")
    for row in rows:
        for key in ("timestamp", "open", "high", "low", "close", "volume"):
            value = number(row.get(key))
            if value is None or value < 0 or (key != "volume" and value == 0):
                return None, "invalid_candles"
            row[key] = value
        if not (row["low"] <= min(row["open"], row["close"]) <=
                max(row["open"], row["close"]) <= row["high"]):
            return None, "invalid_ohlc"
    times = [r["timestamp"] for r in rows]
    if any(b - a != INTERVAL for a, b in zip(times, times[1:])):
        return None, "candle_gap"
    if times[-1] + INTERVAL > now:
        return None, "open_candle"
    if now - (times[-1] + INTERVAL) > 1200:
        return None, "stale_candles"

    bid, ask = number(book.best_bid), number(book.best_ask)
    imbalance = number(book.imbalance_ratio)
    bid_depth, ask_depth = number(book.bid_notional), number(book.ask_notional)
    if any(x is None for x in (bid, ask, imbalance, bid_depth, ask_depth)):
        return None, "invalid_book"
    if bid <= 0 or ask < bid or imbalance < 1.20 or min(bid_depth, ask_depth) < 50000:
        return None, "weak_book"
    spread = (ask / bid - 1) * 100
    if spread > 0.40:
        return None, "wide_spread"

    close = rows[-1]["close"]
    returns = [(close / rows[-1 - n]["close"] - 1) * 100 for n in (3, 12, 48)]
    if any(abs(r) >= bound for r, bound in zip(returns, (3.20, 6.0, 18.0))):
        return None, "extended_or_falling"
    recent = rows[-8:]
    floor = min(r["low"] for r in recent)
    ceiling = max(r["high"] for r in recent)
    width = (ceiling / floor - 1) * 100
    if width < 0.30 or width > 4.0 or close < (floor + ceiling) / 2:
        return None, "no_compression_or_weak_close"
    baseline = sum(r["volume"] for r in rows[-23:-3]) / 20
    ratio = (sum(r["volume"] for r in rows[-3:]) / 3) / baseline if baseline > 0 else 0
    if ratio < 1.50:
        return None, "no_volume_expansion"
    if any(r["volume"] < baseline for r in rows[-3:]):
        return None, "isolated_volume_spike"
    if ask > ceiling * 1.005 or ask < floor or abs(ask / close - 1) > 0.015:
        return None, "live_price_outside_setup"
    return {
        "candle_at": int(times[-1]), "reference_ask": str(book.best_ask),
        "range_low": floor, "range_high": ceiling, "range_pct": round(width, 4),
        "volume_ratio": round(ratio, 4), "book_imbalance": imbalance,
        "spread_pct": round(spread, 4), "returns_pct": returns,
        "evidence": ["price_compression", "sustained_volume_expansion", "bid_heavy_book"],
        "limitations": ["book_orders_can_be_cancelled", "no_executed_buy_side_data",
                        "no_whale_or_news_data", "not_an_entry_signal"],
    }, "qualifies"


def advance(previous, observations, snapshot, now, btc_ok, btc_reason):
    """Transactional shadow state. Outcomes are sampled last prices, NOT fills.

    Missing horizon observations remain missing; no backfilled wins. Only samples
    within 30 minutes of a horizon qualify. A completed run that cannot observe a
    symbol breaks its confirmation streak.
    """
    state = deepcopy(previous) if isinstance(previous, dict) and previous.get("version") == 1 else {}
    events = state.get("events", [])
    events = [e for e in events if 0 <= now - e["observed_at"] <= 7 * 86400][-MAX_EVENTS:]
    for event in events:
        ticker = snapshot.get(event["symbol"])
        price = number(ticker.last) if ticker is not None else None
        age = now - event["observed_at"]
        if price is not None and price > 0 and 0 < age <= 88200:
            change = (price / float(event["reference_ask"]) - 1) * 100
            event["sampled_max_return_pct"] = max(event.get("sampled_max_return_pct", change), change)
            event["sampled_min_return_pct"] = min(event.get("sampled_min_return_pct", change), change)
            event["last_sample_at"] = now
            event["last_sample_price"] = price
            if price < event["range_low"] and "invalidated_at" not in event:
                event["invalidated_at"] = now
            for horizon in HORIZONS:
                if horizon <= age <= horizon + 1800:
                    event.setdefault("outcomes", {}).setdefault(str(horizon), {
                        "sampled_at": now, "actual_age_seconds": age,
                        "last_price": price, "return_pct": change,
                    })
        if age >= 86400:
            event["status"] = "observation_complete"

    old_watches = state.get("watches", {})
    watches, decisions = {}, {}
    for symbol, frame, book in observations:
        hypothesis, reason = evaluate(frame, book, now)
        decisions[symbol] = reason
        if hypothesis is None:
            continue
        previous_watch = old_watches.get(symbol, {})
        contiguous = 0 < now - previous_watch.get("last_seen", 0) <= 2700
        distinct = hypothesis["candle_at"] > previous_watch.get("candle_at", 0)
        confirmations = previous_watch.get("confirmations", 1) if contiguous else 1
        first_seen = previous_watch.get("first_seen", now) if contiguous else now
        if contiguous and distinct and now - previous_watch["last_seen"] >= 300:
            confirmations += 1
        watch = {**hypothesis, "first_seen": first_seen, "last_seen": now,
                 "confirmations": min(confirmations, 100)}
        watches[symbol] = watch
        recent_event = any(e["symbol"] == symbol and now - e["observed_at"] < 86400 for e in events)
        if confirmations >= 2 and now - first_seen >= 600 and not recent_event:
            events.append({**hypothesis, "id": f"{symbol}:{now}", "symbol": symbol,
                           "observed_at": now, "confirmations": confirmations,
                           "status": "observing", "mode": "shadow_only",
                           "btc_ok": bool(btc_ok), "btc_reason": btc_reason,
                           "outcomes": {}})
            decisions[symbol] = "shadow_hypothesis_recorded"
    return {"version": 1, "mode": "shadow_only", "updated_at": now,
            "watches": watches, "events": events[-MAX_EVENTS:], "decisions": decisions}
