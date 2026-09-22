"""Read-only quality report. Price observations are never treated as real fills."""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time


def is_shadow(signal):
    evidence = signal.get("evidence") or {}
    return (signal.get("tracking_mode") == "shadow_limit_simulation"
            or (isinstance(evidence, dict) and evidence.get("shadow_mode") is True))


def execution_sample(signals, cohort):
    """Separate the repaired model's complete estimates from legacy observations."""
    current = [s for s in signals if cohort and s.get("research_cohort") == cohort
               and s.get("simulation_version") == "ohlc_penetration_v2" and is_shadow(s)]
    complete = [s for s in current if not s.get("tracking_incomplete") and not s.get("model_transition_from")]
    estimated = [s for s in complete if s.get("fill_estimated") is True and not s.get("fill_confirmed")]
    return {"cohort": cohort, "current_records": len(current),
            "excluded_legacy_records": len(signals) - len(current),
            "incomplete_records": len(current) - len(complete),
            "estimated_fill_status_counts": dict(Counter(s.get("status", "unknown") for s in estimated)),
            "pending_entries": sum(s.get("status") == "PENDING_ENTRY" for s in complete),
            "expired_entries": sum(s.get("status") == "ENTRY_EXPIRED" for s in complete),
            "confirmed_exchange_fills": 0,
            "basis": "ohlc_scenarios_not_realized_pnl"}


def dec(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def net_scenario(entry, exit_price, fee_pct, slippage_pct):
    """Explicit hypothetical costs per side; never substitute zero for unknown."""
    values = [dec(x) for x in (entry, exit_price, fee_pct, slippage_pct)]
    if any(x is None for x in values):
        return None
    entry, exit_price, fee, slip = values
    if entry <= 0 or exit_price <= 0 or not (0 <= fee <= 10 and 0 <= slip <= 10):
        return None
    f, s = fee / 100, slip / 100
    return float((exit_price * (1-s) * (1-f) / (entry * (1+s) * (1+f)) - 1) * 100)


def build_report(state, now=None, fee_pct=None, slippage_pct=None):
    now = int(time.time() if now is None else now)
    diagnostics = state.get("scan_diagnostics", [])
    latest = diagnostics[-1] if diagnostics else {}
    decisions = latest.get("symbols", {})
    counts = Counter(x.get("reason", "unknown") for x in decisions.values())
    warnings = []
    last_finished = latest.get("finished_at")
    if not last_finished or now - last_finished > 2700:
        warnings.append("no_recent_completed_scan")
    if latest.get("status") != "completed":
        warnings.append("scan_not_completed")
    book_capacity = counts.get("not_evaluated_capacity", 0)
    technical_capacity = counts.get("not_evaluated_technical_capacity", 0)
    if book_capacity or technical_capacity:
        warnings.append("universe_not_fully_evaluated")
    signals = state.get("active_signals", [])
    pending = sum(event.get("delivered") is False for s in signals if not is_shadow(s)
                  for event in s.get("events", []))
    shadow_events = sum(len(s.get("events", [])) for s in signals if is_shadow(s))
    if pending:
        warnings.append("lifecycle_notifications_pending")
    radar = state.get("accumulation_radar", {})
    if str(latest.get("radar_status", "")).startswith("error"):
        warnings.append("radar_error")
    retained_events = radar.get("events", [])
    cohort = state.get("research_cohort", {}).get("id")
    events = [e for e in retained_events if not cohort or e.get("research_cohort") == cohort]
    horizons = {}
    for horizon in (3600, 14400, 86400):
        matured = [e for e in events if now - e.get("observed_at", now) >= horizon]
        observed, net = [], []
        invalid = 0
        for event in matured:
            sample = event.get("outcomes", {}).get(str(horizon))
            if sample is None:
                continue
            # Revalidate time and prices rather than trusting a stored percentage.
            age = sample.get("sampled_at", 0) - event["observed_at"]
            entry, price = dec(event.get("reference_ask")), dec(sample.get("last_price"))
            if not horizon <= age <= horizon+1800 or sample["sampled_at"] > now or entry is None or price is None or min(entry, price) <= 0:
                invalid += 1
                continue
            observed.append(float((price/entry-1)*100))
            result = net_scenario(entry, price, fee_pct, slippage_pct)
            if result is not None:
                net.append(result)
        horizons[str(horizon)] = {
            "matured_events": len(matured), "valid_samples": len(observed),
            "missing_or_invalid_samples": len(matured)-len(observed),
            "invalid_samples": invalid,
            "mean_price_change_pct": sum(observed)/len(observed) if observed else None,
            "mean_cost_scenario_pct": sum(net)/len(net) if net else None,
        }
    if not events:
        warnings.append("no_radar_sample_yet")
    if fee_pct is None or slippage_pct is None:
        warnings.append("trading_costs_unspecified")
    return {
        "version": 2, "generated_at": now, "mode": "research_only",
        "profitability_proven": False, "automatic_promotion_allowed": False,
        "warnings": warnings,
        "coverage": {"snapshot_markets": latest.get("markets"),
                     "orderbooks_checked": latest.get("orderbooks_checked"),
                     "technical_checked": latest.get("technical_checked"),
                     "unexamined_capacity": book_capacity + technical_capacity,
                     "unexamined_orderbook_capacity": book_capacity,
                     "unexamined_technical_capacity": technical_capacity},
        "rejection_reasons": dict(counts),
        "paper_signal_status_counts": dict(Counter(s.get("status", "unknown") for s in signals)),
        "paper_signal_status_counts_basis": "inventory_only_mixed_models_not_performance",
        "research_cohort": state.get("research_cohort"),
        "execution_sample": execution_sample(signals, cohort),
        "funnel": latest.get("funnel", {}),
        "funnel_order": ["universe", "orderbooks_attempted", "book_approved", "technicals_attempted",
                         "data_valid", "discovery_passed", "confirmed", "limit_simulated"],
        "pending_lifecycle_notifications": pending,
        "shadow_events_retained": shadow_events,
        "legacy_radar_events_excluded": len(retained_events)-len(events),
        "radar_events_retained": len(events), "horizon_samples": horizons,
        "cost_assumptions_per_side": {"fee_pct": fee_pct, "slippage_pct": slippage_pct},
        "limitations": ["not_actual_trades", "last_price_is_not_executable_bid",
                        "sampled_prices_miss_intraperiod_moves", "bounded_retained_sample",
                        "cost_scenario_does_not_model_order_size_or_market_impact"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="scanner_state.json")
    parser.add_argument("--fee-pct", type=float)
    parser.add_argument("--slippage-pct", type=float)
    args = parser.parse_args()
    if (args.fee_pct is None) != (args.slippage_pct is None):
        parser.error("Supply both fee and slippage, or leave both unknown")
    if args.fee_pct is not None and net_scenario(100, 100, args.fee_pct, args.slippage_pct) is None:
        parser.error("Costs must be finite percentages between 0 and 10 per side")
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    print(json.dumps(build_report(state, fee_pct=args.fee_pct, slippage_pct=args.slippage_pct),
                     ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
