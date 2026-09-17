"""Bounded repair using real rows supplied by the same Paribu REST endpoint."""
import json
import logging
from datetime import datetime, timezone
import pandas as pd

LOGGER = logging.getLogger("paribu_momentum_watcher.candle_backfill")
MAX_REQUESTS = 4
MAX_MISSING = 100
_history = {}


def bind_history(state):
    """Reuse scanner_state.json persistence; no separate transient disk file."""
    global _history
    history = state.setdefault("candle_gap_history", {})
    if not isinstance(history, dict):
        raise ValueError("Invalid candle gap history")
    _history = history


def utc(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def spans(missing, interval):
    groups = []
    for ts in sorted(missing):
        if groups and ts == groups[-1][1] + interval:
            groups[-1][1] = ts
        else:
            groups.append([ts, ts])
    return groups


def recent_contiguous(frame, interval):
    """Return only the newest uninterrupted suffix; never manufactures a row."""
    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    if ordered.empty:
        return ordered
    diffs = ordered["timestamp"].diff()
    breaks = diffs[diffs.ne(interval)].index.tolist()
    return ordered.iloc[breaks[-1]:].reset_index(drop=True) if breaks else ordered


def repair(frame, interval, now, request_range, label, minimum_contiguous=205):
    """No interpolation, cross-exchange data, overwrite, or timestamp rounding."""
    report = {"market": label, "requests": 0, "errors": [], "recovered": 0}
    if frame.empty:
        raise ValueError("No valid rows to anchor backfill")
    frame = frame.sort_values("timestamp").copy()
    first = int(frame.timestamp.iloc[0])
    # Freeze the target for the call, with 60 seconds publication grace.
    last = max(int(frame.timestamp.iloc[-1]), ((now - 60) // interval - 1) * interval)
    actual = set(int(t) for t in frame.timestamp)
    expected_count = (last - first) // interval + 1
    report["window_utc"] = [utc(first), utc(last)]
    if expected_count > len(actual) + MAX_MISSING:
        report.update(status="gap_limit_exceeded", missing_before=expected_count-len(actual),
                      missing_after=expected_count-len(actual))
        LOGGER.warning("CANDLE_BACKFILL %s", json.dumps(report))
        raise ValueError("Backfill gap limit exceeded: " + json.dumps(report))
    expected = set(range(first, last + interval, interval))
    missing = expected - actual
    previous = _history.get(label)
    previous_missing = set(previous.get("missing_timestamps", [])) if isinstance(previous, dict) else set()
    continuing = missing & previous_missing
    newly_observed = missing - previous_missing
    report.update(missing_before=len(missing),
                  classification="first_observation" if previous is None else
                  ("mixed" if continuing and newly_observed else
                   "continuing" if continuing else "new" if missing else "clear"),
                  continuing_count=len(continuing), new_count=len(newly_observed),
                  gaps_before_utc=[[utc(a), utc(b)] for a, b in spans(missing, interval)])
    LOGGER.info("CANDLE_BACKFILL_START %s", json.dumps(report))
    suffix = recent_contiguous(frame, interval)
    report["recent_contiguous_before"] = len(suffix)
    # The strategy only needs a recent, closed, uninterrupted calculation
    # window. Older unavailable archive rows stay recorded but cannot block
    # forever once a fresh minimum window exists.
    historical_only = bool(missing) and max(missing) < int(suffix.timestamp.iloc[0])
    if len(suffix) >= minimum_contiguous and historical_only:
        report.update(missing_after=len(missing), recovered=0,
                      gaps_after_utc=report["gaps_before_utc"],
                      recent_contiguous_after=len(suffix),
                      status="accepted_recent_window")
        _history[label] = {"observed_at": now, "missing_timestamps": sorted(missing),
                           "report": report}
        LOGGER.info("CANDLE_BACKFILL_RESULT %s", json.dumps(report))
        suffix.attrs["backfill"] = report
        return suffix
    for attempt in range(1, MAX_REQUESTS + 1):
        if not missing:
            break
        # One bounded request covers the remaining gap envelope each attempt.
        start, end = min(missing), max(missing)
        report["requests"] += 1
        detail = {"market": label, "attempt": attempt, "from_utc": utc(start),
                  "to_utc": utc(end), "recovered": 0}
        try:
            rows = request_range(start - interval, end + interval)
            recovered = rows[rows.timestamp.isin(missing)].copy()
            if not recovered.empty:
                frame = pd.concat([frame, recovered], ignore_index=True)
                missing -= set(int(t) for t in recovered.timestamp)
            detail.update(recovered=len(recovered),
                          result="success" if not missing else "partial" if len(recovered) else "no_recovery")
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)[:240]
            report["errors"].append(error)
            detail.update(result="error", error=error)
        detail["missing_after"] = len(missing)
        LOGGER.info("CANDLE_BACKFILL_ATTEMPT %s", json.dumps(detail))
    suffix = recent_contiguous(frame, interval)
    historical_only = bool(missing) and max(missing) < int(suffix.timestamp.iloc[0])
    status = ("complete" if not missing else
              "accepted_recent_window" if len(suffix) >= minimum_contiguous and historical_only else
              "unresolved")
    report.update(missing_after=len(missing), recovered=report["missing_before"]-len(missing),
                  gaps_after_utc=[[utc(a), utc(b)] for a, b in spans(missing, interval)],
                  recent_contiguous_after=len(suffix), status=status)
    _history[label] = {"observed_at": now, "missing_timestamps": sorted(missing),
                       "report": report}
    LOGGER.log(logging.WARNING if missing else logging.INFO,
               "CANDLE_BACKFILL_RESULT %s", json.dumps(report))
    if status == "unresolved":
        raise ValueError("Unresolved Paribu candles: " + json.dumps(report))
    frame = suffix if status == "accepted_recent_window" else frame.sort_values("timestamp").reset_index(drop=True)
    frame.attrs["backfill"] = report
    return frame
