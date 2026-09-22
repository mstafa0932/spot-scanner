"""Paribu candle repair with provenance-aware synthetic warm-up rows.

Policy:
- Genuine Paribu recovery is always attempted first.
- Small isolated historical gaps may be represented by synthetic flat candles
  (O=H=L=C=previous close, volume=0) only to preserve calculation continuity.
- Synthetic rows are explicitly tagged and must never masquerade as exchange data.
- Large or consecutive outages fail closed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable

import pandas as pd
from pandas.api.types import is_bool

LOGGER = logging.getLogger("paribu_momentum_watcher.candle_backfill")

MAX_REQUESTS = 4
MAX_MISSING = 100
MAX_SYNTHETIC_TOTAL = 8
MAX_CONSECUTIVE_SYNTHETIC = 2
PUBLICATION_GRACE_SECONDS = 60

_history: dict[str, dict] = {}
_REQUIRED = ("timestamp", "open", "high", "low", "close", "volume")


def bind_history(state: dict) -> None:
    """Reuse scanner_state.json persistence; no transient repair state."""
    global _history
    history = state.setdefault("candle_gap_history", {})
    if not isinstance(history, dict):
        raise ValueError("Invalid candle gap history")
    _history = history


def utc(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def spans(missing, interval):
    groups = []
    for ts in sorted(missing):
        if groups and ts == groups[-1][1] + interval:
            groups[-1][1] = ts
        else:
            groups.append([ts, ts])
    return groups


def recent_contiguous(frame: pd.DataFrame, interval: int) -> pd.DataFrame:
    """Compatibility helper: newest uninterrupted suffix."""
    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    if ordered.empty:
        return ordered
    diffs = ordered["timestamp"].diff()
    breaks = diffs[diffs.ne(interval)].index.tolist()
    return ordered.iloc[breaks[-1]:].reset_index(drop=True) if breaks else ordered


def _expected_last_closed_open(now_s: int, interval_s: int) -> int:
    # Do not require a just-closed bar until publication grace has elapsed.
    return ((int(now_s) - PUBLICATION_GRACE_SECONDS) // interval_s - 1) * interval_s


def _max_true_run(mask: pd.Series) -> int:
    best = current = 0
    for value in mask.astype(bool).tolist():
        if value:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _authentic_tail_count(frame: pd.DataFrame) -> int:
    if "is_authentic" not in frame.columns:
        return 0
    count = 0
    for value in reversed(frame["is_authentic"].tolist()):
        if not is_bool(value) or not bool(value):
            break
        count += 1
    return count


def recent_authentic(
    frame: pd.DataFrame,
    bars: int,
    interval_seconds: int | None = None,
) -> bool:
    """Require the newest bars to be genuine and time-grid contiguous."""
    if bars <= 0 or len(frame) < bars or "is_authentic" not in frame.columns:
        return False
    tail = frame.tail(bars)
    if not all(is_bool(value) and bool(value) for value in tail["is_authentic"]):
        return False
    if len(tail) > 1:
        diffs = tail["timestamp"].diff().dropna()
        if interval_seconds is None:
            if diffs.empty:
                return False
            interval_seconds = int(diffs.iloc[-1])
        if interval_seconds <= 0 or not bool(diffs.eq(interval_seconds).all()):
            return False
    return True


def _regularize_with_synthetic_rows(
    frame: pd.DataFrame,
    *,
    interval_s: int,
    target_last_ts: int,
    minimum_rows: int,
) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("No Paribu candles available to anchor series")

    attrs = dict(frame.attrs)
    x = frame.loc[:, list(_REQUIRED)].copy()

    for column in _REQUIRED:
        x[column] = pd.to_numeric(x[column], errors="coerce")

    if x[list(_REQUIRED)].isna().any().any():
        raise ValueError("Non-numeric or missing OHLCV value")
    if (x["timestamp"] % 1 != 0).any():
        raise ValueError("Fractional candle timestamp")

    x["timestamp"] = x["timestamp"].astype("int64")
    if not bool(x["timestamp"].mod(interval_s).eq(0).all()):
        raise ValueError("Candle is off the UTC interval grid")
    if bool((x["volume"] < 0).any()):
        raise ValueError("Negative candle volume")

    duplicates = x[x.duplicated("timestamp", keep=False)]
    if not duplicates.empty:
        for timestamp, group in duplicates.groupby("timestamp"):
            unique_rows = group[["open", "high", "low", "close", "volume"]].drop_duplicates()
            if len(unique_rows) != 1:
                raise ValueError(f"Conflicting duplicate candle at timestamp={timestamp}")

    x = (
        x.drop_duplicates("timestamp", keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    invalid_ohlc = (
        (x["high"] < x[["open", "close"]].max(axis=1))
        | (x["low"] > x[["open", "close"]].min(axis=1))
        | (x["low"] > x["high"])
    )
    if bool(invalid_ohlc.any()):
        raise ValueError("Invalid OHLC relationship")

    first_ts = int(x["timestamp"].iloc[0])
    target_last_ts = max(int(target_last_ts), int(x["timestamp"].iloc[-1]))
    grid = pd.Index(
        range(first_ts, target_last_ts + interval_s, interval_s),
        name="timestamp",
    )
    regular = x.set_index("timestamp").reindex(grid)

    authentic = regular["close"].notna()
    synthetic = ~authentic
    synthetic_count = int(synthetic.sum())
    max_synthetic_run = _max_true_run(synthetic)

    if synthetic_count > MAX_SYNTHETIC_TOTAL:
        raise ValueError(
            f"Synthetic candle limit exceeded: {synthetic_count}>{MAX_SYNTHETIC_TOTAL}"
        )
    if max_synthetic_run > MAX_CONSECUTIVE_SYNTHETIC:
        raise ValueError(
            "Consecutive synthetic candle limit exceeded: "
            f"{max_synthetic_run}>{MAX_CONSECUTIVE_SYNTHETIC}"
        )

    previous_close = regular["close"].ffill()
    if bool(previous_close[synthetic].isna().any()):
        raise ValueError("Cannot forward-fill without a previous real close")

    for column in ("open", "high", "low", "close"):
        regular.loc[synthetic, column] = previous_close[synthetic]
    regular.loc[synthetic, "volume"] = 0.0
    regular["is_authentic"] = authentic.astype(bool)
    regular["data_quality"] = "PARIBU"
    regular.loc[synthetic, "data_quality"] = "SYNTHETIC_FFILL"
    regular = regular.reset_index()

    if len(regular) < minimum_rows:
        raise ValueError(
            f"Only {len(regular)} canonical candles; {minimum_rows} required"
        )

    regular.attrs.update(attrs)
    regular.attrs["candle_quality"] = {
        "synthetic_count": synthetic_count,
        "synthetic_timestamps": [
            int(v)
            for v in regular.loc[~regular["is_authentic"], "timestamp"].tolist()
        ],
        "max_consecutive_synthetic": max_synthetic_run,
        "authentic_tail": _authentic_tail_count(regular),
    }
    return regular


def repair(
    frame: pd.DataFrame,
    interval: int,
    now: int,
    request_range: Callable[[int, int], pd.DataFrame],
    label: str,
    minimum_contiguous: int = 205,
) -> pd.DataFrame:
    """Recover real rows first; use bounded tagged synthetic rows only as fallback."""
    if frame.empty:
        raise ValueError("No valid rows to anchor backfill")

    original_attrs = dict(frame.attrs)
    x = frame.sort_values("timestamp").copy()
    first = int(x["timestamp"].iloc[0])
    latest_actual = int(x["timestamp"].iloc[-1])
    expected_last = _expected_last_closed_open(now, interval)
    last = max(latest_actual, expected_last)

    actual = set(int(t) for t in x["timestamp"].tolist())
    expected_count = (last - first) // interval + 1
    if expected_count > len(actual) + MAX_MISSING:
        missing_count = expected_count - len(actual)
        report = {
            "market": label,
            "requests": 0,
            "errors": [],
            "missing_before": missing_count,
            "missing_after": missing_count,
            "status": "gap_limit_exceeded",
            "window_utc": [utc(first), utc(last)],
        }
        LOGGER.warning("CANDLE_REPAIR_RESULT %s", json.dumps(report))
        raise ValueError("Backfill gap limit exceeded: " + json.dumps(report))

    expected = set(range(first, last + interval, interval))
    missing = expected - actual
    previous = _history.get(label)
    previous_missing = (
        set(previous.get("missing_timestamps", []))
        if isinstance(previous, dict)
        else set()
    )
    continuing = missing & previous_missing
    newly_observed = missing - previous_missing

    report = {
        "market": label,
        "requests": 0,
        "errors": [],
        "recovered": 0,
        "missing_before": len(missing),
        "classification": (
            "first_observation"
            if previous is None
            else "mixed"
            if continuing and newly_observed
            else "continuing"
            if continuing
            else "new"
            if missing
            else "clear"
        ),
        "continuing_count": len(continuing),
        "new_count": len(newly_observed),
        "window_utc": [utc(first), utc(last)],
        "gaps_before_utc": [[utc(a), utc(b)] for a, b in spans(missing, interval)],
    }
    LOGGER.info("CANDLE_REPAIR_START %s", json.dumps(report))

    for attempt in range(1, MAX_REQUESTS + 1):
        if not missing:
            break
        start, end = min(missing), max(missing)
        report["requests"] += 1
        detail = {
            "market": label,
            "attempt": attempt,
            "from_utc": utc(start),
            "to_utc": utc(end),
            "recovered": 0,
        }
        try:
            rows = request_range(start - interval, end + interval)
            if rows is None or rows.empty:
                recovered = pd.DataFrame(columns=x.columns)
            else:
                recovered = rows[rows["timestamp"].astype("int64").isin(missing)].copy()

            if not recovered.empty:
                x = pd.concat([x, recovered], ignore_index=True)
                x = (
                    x.sort_values("timestamp")
                    .drop_duplicates("timestamp", keep="first")
                    .reset_index(drop=True)
                )
                recovered_ts = set(int(t) for t in recovered["timestamp"].tolist())
                missing -= recovered_ts

            detail.update(
                recovered=len(recovered),
                result=(
                    "success"
                    if not missing
                    else "partial"
                    if len(recovered)
                    else "no_recovery"
                ),
            )
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)[:240]
            report["errors"].append(error)
            detail.update(result="error", error=error)
        detail["missing_after"] = len(missing)
        LOGGER.info("CANDLE_REPAIR_ATTEMPT %s", json.dumps(detail))

    result = _regularize_with_synthetic_rows(
        x,
        interval_s=interval,
        target_last_ts=last,
        minimum_rows=minimum_contiguous,
    )
    quality = result.attrs["candle_quality"]
    report.update(
        recovered=report["missing_before"] - len(missing),
        missing_after_real_recovery=len(missing),
        missing_after=len(missing),
        gaps_after_utc=[[utc(a), utc(b)] for a, b in spans(missing, interval)],
        synthetic_count=quality["synthetic_count"],
        synthetic_timestamps=quality["synthetic_timestamps"],
        max_consecutive_synthetic=quality["max_consecutive_synthetic"],
        authentic_tail=quality["authentic_tail"],
        status=(
            "complete_real"
            if quality["synthetic_count"] == 0
            else "synthetic_fallback"
        ),
    )

    _history[label] = {
        "observed_at": int(now),
        "missing_timestamps": sorted(missing),
        "report": report,
    }

    result.attrs.update(original_attrs)
    result.attrs["backfill"] = report
    result.attrs["candle_quality"] = quality
    LOGGER.log(
        logging.INFO if quality["synthetic_count"] == 0 else logging.WARNING,
        "CANDLE_REPAIR_RESULT %s",
        json.dumps(report),
    )
    return result
