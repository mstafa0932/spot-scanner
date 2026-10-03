from __future__ import annotations

"""Summarize scanner Data Health with the correct conditional denominator.

This module is observational only.  It never fetches market data and never
changes scanner state, thresholds, or trading decisions.
"""

from collections import Counter
from typing import Any


DATA_FAILURE_PREFIXES = (
    "candle_error:",
    "non_paribu_candles",
    "synthetic_candles_present",
    "recent_15m_integrity_failed",
    "recent_1h_integrity_failed",
    "recent_4h_integrity_failed",
    "indicators_unavailable",
)


def _is_data_failure(stage: str, reason: str) -> bool:
    if stage != "data":
        return False
    return reason.startswith(DATA_FAILURE_PREFIXES)


def summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    """Return denominator-safe health fields from one persisted diagnostic run."""
    symbols = run.get("symbols", {})
    if not isinstance(symbols, dict):
        symbols = {}

    reasons: Counter[str] = Counter()
    data_failures = 0
    for decision in symbols.values():
        if not isinstance(decision, dict):
            continue
        stage = str(decision.get("stage", ""))
        reason = str(decision.get("reason", ""))
        if _is_data_failure(stage, reason):
            data_failures += 1
            reasons[reason] += 1

    # A market reaches candle evaluation only after passing the book gates and
    # being selected by technical capacity.  Therefore universe is NOT the
    # denominator for data_valid.
    funnel = run.get("funnel", {})
    if not isinstance(funnel, dict):
        funnel = {}
    data_valid = int(funnel.get("data_valid", 0) or 0)
    attempted = data_valid + data_failures
    return {
        "started_at": run.get("started_at"),
        "run_id": run.get("run_id"),
        "universe": int(funnel.get("universe", 0) or 0),
        "book_approved": int(funnel.get("book_approved", funnel.get("book", 0)) or 0),
        "data_attempted": attempted,
        "data_valid": data_valid,
        "data_failed": data_failures,
        "data_valid_rate": (data_valid / attempted) if attempted else None,
        "failure_reasons": dict(reasons),
    }


def summarize_history(state: dict[str, Any], limit: int = 20) -> dict[str, Any]:
    history = state.get("scan_diagnostics", [])
    if not isinstance(history, list):
        history = []
    rows = [summarize_run(run) for run in history[-limit:] if isinstance(run, dict)]
    totals: Counter[str] = Counter()
    attempted = valid = failed = 0
    for row in rows:
        attempted += row["data_attempted"]
        valid += row["data_valid"]
        failed += row["data_failed"]
        totals.update(row["failure_reasons"])
    return {
        "runs": rows,
        "totals": {
            "runs": len(rows),
            "data_attempted": attempted,
            "data_valid": valid,
            "data_failed": failed,
            "data_valid_rate": (valid / attempted) if attempted else None,
            "failure_reasons": dict(totals),
        },
    }
