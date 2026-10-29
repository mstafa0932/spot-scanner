"""Offline early-momentum observation research; never a trade or READY signal.

Inputs must be historical snapshots of *authentic* closed Paribu 15m candles.
This module deliberately does not import the live scanner, use the network,
touch scanner_state.json, or send alerts. Thresholds are experimental.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

CANDLE_SECONDS = 900
MIN_HISTORY = 23
FOLLOWUP_HORIZONS = (3600, 14400)
FOLLOWUP_TOLERANCE = 1800


def _number(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def evaluate(symbol: str, candles: list[dict[str, Any]], now: int) -> dict[str, Any]:
    """Observe possible pre-breakout volume, without liquidity/RSI/ATR/READY gates.

    Rejections are retained as controls. A positive observation is NOT permission
    to buy: live spread, depth, fees, fillability and loss outcomes are unknown.
    """
    record: dict[str, Any] = {
        "symbol": symbol, "observed_at": now, "candidate": False,
        "decision": "insufficient_history", "mode": "research_only",
        "is_ready": False, "is_trade": False,
    }
    if not isinstance(candles, list) or len(candles) < MIN_HISTORY:
        return record

    bars = candles[-MIN_HISTORY:]
    cleaned = []
    for raw in bars:
        if not isinstance(raw, dict) or raw.get("authentic") is not True:
            record["decision"] = "unauthenticated_candle"
            return record
        ts = _number(raw.get("timestamp"))
        o, h, l, c, v = (_number(raw.get(k)) for k in ("open", "high", "low", "close", "volume"))
        if (ts is None or ts != int(ts) or None in (o, h, l, c, v)
                or min(o, h, l, c) <= 0 or v < 0
                or l > min(o, c) or h < max(o, c)):
            record["decision"] = "invalid_ohlcv"
            return record
        cleaned.append((int(ts), o, h, l, c, v))
    times = [b[0] for b in cleaned]
    if any(b - a != CANDLE_SECONDS for a, b in zip(times, times[1:])):
        record["decision"] = "noncontiguous_candles"
        return record
    last_open = times[-1]
    if last_open + CANDLE_SECONDS > now:
        record["decision"] = "unclosed_candle"
        return record
    if now - (last_open + CANDLE_SECONDS) > 1200:
        record["decision"] = "stale_candle"
        return record

    baseline = sum(b[5] for b in cleaned[-23:-3]) / 20
    if baseline <= 0:
        record["decision"] = "no_volume_baseline"
        return record
    recent = cleaned[-3:]
    ratio = (sum(b[5] for b in recent) / 3) / baseline
    expanding = sum(b[5] > baseline for b in recent)
    movement = (cleaned[-1][4] / cleaned[-4][4] - 1) * 100
    record.update(
        closed_candle_at=last_open, reference_close=cleaned[-1][4],
        volume_ratio=round(ratio, 6),
        rising_volume_bars=expanding, change_3bars_pct=round(movement, 6),
    )
    if movement >= 3.2 or movement < -1.0:
        record["decision"] = "extended_or_falling"
    elif ratio < 1.3 or expanding < 2:
        record["decision"] = "volume_not_sustained"
    else:
        record["candidate"] = True
        record["decision"] = "early_volume_watch"
    return record


def mark_followups(record: dict[str, Any], future_prices: list[dict[str, Any]]) -> dict[str, Any]:
    """Later LAST-price marks, not backtested fills, trades or realized P&L."""
    result = dict(record)
    result["marks_not_fills"] = {}
    reference = _number(record.get("reference_close"))
    for horizon in FOLLOWUP_HORIZONS:
        mark = None
        if reference is not None and reference > 0:
            valid = []
            for item in future_prices:
                ts = _number(item.get("timestamp")) if isinstance(item, dict) else None
                price = _number(item.get("last")) if isinstance(item, dict) else None
                if (ts is not None and price is not None and price > 0
                        and record["observed_at"] + horizon <= ts
                        <= record["observed_at"] + horizon + FOLLOWUP_TOLERANCE):
                    valid.append((ts, price))
            if valid:
                at, price = min(valid)
                mark = {"at": at, "last": price,
                        "return_pct_before_costs": round((price / reference - 1) * 100, 6)}
        result["marks_not_fills"][str(horizon)] = mark
    return result


def replay(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """No data download. Decisions use only the supplied as-of candle history.

    Deduplicate repeated runs evaluating the same symbol/closed 15m candle.
    Keep rejected controls rather than studying only historical winners.
    """
    decisions = []
    seen: set[tuple[str, int]] = set()
    for row in observations:
        symbol = str(row["symbol"])
        now = int(row["observed_at"])
        decision = evaluate(symbol, row["candles"], now)
        key = (symbol, int(decision.get("closed_candle_at", now)))
        if key in seen:
            continue
        seen.add(key)
        decisions.append(mark_followups(decision, row.get("future_prices", [])))
    return {
        "mode": "offline_research_only",
        "no_trade_execution": True,
        "no_ready_or_telegram": True,
        "warning": "Post-observation last-price marks are not executions, profits, or validated predictions.",
        "records": len(decisions),
        "early_watches": sum(bool(x["candidate"]) for x in decisions),
        "controls": sum(not x["candidate"] for x in decisions),
        "decisions": decisions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="JSON file: {'observations': [...]} with event-time candle snapshots")
    parser.add_argument("--output", help="Optional output JSON path; never production state")
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = replay(data["observations"])
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
