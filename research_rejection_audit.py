"""Read-only, offline evidence ledger for scanner rejections.

Inputs: GitHub scanner_state.json snapshots downloaded by the operator.
No HTTP, Paribu calls, Telegram, imports from the production scanner, or state writes.
Counts are diagnostic decisions, NEVER fills, trading PnL, or missed READYs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable


def _snapshot_runs(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], int]:
    seen: dict[str, dict[str, Any]] = {}
    snapshots = 0
    for path in paths:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("scan_diagnostics"), list):
            raise ValueError(f"Invalid scan_diagnostics in {path}")
        snapshots += 1
        for row in state["scan_diagnostics"]:
            if not isinstance(row, dict):
                raise ValueError(f"Non-object scanner diagnostic in {path}")
            if row.get("status") != "completed":
                continue
            # Never mix other market/trading modes into the shadow cohort.
            if row.get("shadow_mode") is not True:
                raise ValueError(f"Non-shadow scan in {path}")
            run_id = str(row.get("run_id") or "")
            started_at = row.get("started_at")
            if not run_id or type(started_at) is not int or started_at <= 0:
                raise ValueError(f"Missing run identity/time in {path}")
            if not isinstance(row.get("symbols"), dict):
                raise ValueError(f"Missing per-symbol decisions in {path}")
            if run_id in seen:
                previous = seen[run_id]
                # The same completed run must not be silently re-attributed.
                if (previous["started_at"] != started_at
                        or previous["symbols"] != row["symbols"]):
                    raise ValueError(f"Conflicting duplicate scan {run_id}")
            else:
                seen[run_id] = row
    return sorted(seen.values(), key=lambda x: (x["started_at"], str(x["run_id"]))), snapshots


def _numeric_score(value: Any) -> int | None:
    # A boolean, malformed value or NaN must not be counted as a score.
    if type(value) not in (int, float) or isinstance(value, bool):
        return None
    if not (0 <= value <= 100) or int(value) != value:
        return None
    return int(value)


def analyze_snapshots(
    paths: Iterable[Path], symbols: Iterable[str] | None = None
) -> dict[str, Any]:
    runs, input_count = _snapshot_runs(paths)
    selected = (
        sorted({str(s).strip().upper().replace("/", "_") for s in symbols if str(s).strip()})
        if symbols is not None else
        sorted({symbol for row in runs for symbol in row["symbols"]})
    )
    cases: dict[str, Any] = {}
    for symbol in selected:
        decisions = [
            (run, run["symbols"].get(symbol))
            for run in runs
            if isinstance(run["symbols"].get(symbol), dict)
        ]
        by_stage = Counter(str(d.get("stage") or "unknown") for _, d in decisions)
        by_reason = Counter(str(d.get("reason") or "unknown") for _, d in decisions)
        skipped_then_evaluated = skipped_then_universe = skipped_twice = 0
        early_to_late = []
        last_early: tuple[int, int, str] | None = None
        for index, (run, decision) in enumerate(decisions):
            stage = decision.get("stage")
            reason = str(decision.get("reason") or "")
            score = _numeric_score(decision.get("score"))
            if (stage == "trigger" and score is not None
                    and 70 <= score <= 79 and reason.startswith("score ")):
                last_early = (run["started_at"], score, str(run["run_id"]))
            if (stage == "discovery" and reason == "RSI outside watch range"
                    and score is not None and score >= 80 and last_early is not None
                    and 0 < run["started_at"] - last_early[0] <= 24 * 3600):
                early_to_late.append({
                    "early_run_id": last_early[2],
                    "early_score": last_early[1],
                    "later_run_id": str(run["run_id"]),
                    "later_score": score,
                    "later_rsi": decision.get("rsi"),
                })
                # Count a unique transition once, not every repeated RSI rejection.
                last_early = None
            # Every scanner run represents one scheduling opportunity, including
            # runs in which a symbol has only an explicit capacity decision.
        for i in range(len(runs) - 1):
            prior = runs[i]["symbols"].get(symbol)
            nxt = runs[i + 1]["symbols"].get(symbol)
            if not isinstance(prior, dict) or prior.get("reason") != "not_evaluated_capacity":
                continue
            if not isinstance(nxt, dict):
                continue
            if nxt.get("reason") == "not_evaluated_capacity":
                skipped_twice += 1
            elif nxt.get("stage") == "universe":
                skipped_then_universe += 1
            else:
                skipped_then_evaluated += 1
        scored = [
            (_numeric_score(d.get("score")), run)
            for run, d in decisions
        ]
        scored = [(score, run) for score, run in scored if score is not None]
        last_record = decisions[-1] if decisions else None
        cases[symbol] = {
            "observed_decisions": len(decisions),
            "stage_counts": dict(sorted(by_stage.items())),
            "reason_counts": dict(sorted(by_reason.items())),
            "max_recorded_score": max((value for value, _ in scored), default=None),
            "last_run_id": str(last_record[0]["run_id"]) if last_record else None,
            "last_reason": str(last_record[1].get("reason")) if last_record else None,
            "capacity_skips_evaluated_next": skipped_then_evaluated,
            "capacity_skips_universe_excluded_next": skipped_then_universe,
            "capacity_skips_repeated_next": skipped_twice,
            "early_70_79_to_later_rsi_rejection": early_to_late,
        }
    return {
        "mode": "offline_shadow_diagnostics_only",
        "input_snapshots": input_count,
        "distinct_completed_runs": len(runs),
        "first_started_at_utc": (
            datetime.fromtimestamp(runs[0]["started_at"], timezone.utc).isoformat()
            if runs else None
        ),
        "last_started_at_utc": (
            datetime.fromtimestamp(runs[-1]["started_at"], timezone.utc).isoformat()
            if runs else None
        ),
        "warnings": [
            "Neither high/low excursions nor screenshot rallies prove executed trades",
            "A 70-79 to high-RSI transition is research evidence, not a lost READY",
            "The per-run decisions are bounded by existing API and coverage capacity",
            "Historical data may have incomplete/synthetic-candle research measurement bias",
        ],
        "symbols": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", nargs="+", type=Path, help="local scanner_state.json snapshots")
    parser.add_argument("--symbols", nargs="*", default=None, help="tickers, e.g. ATH_TL DOT_TL")
    args = parser.parse_args()
    report = analyze_snapshots(args.snapshots, args.symbols)
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
