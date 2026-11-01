"""Describe sampled price surges and link original HOT records, read-only.

This is a retrospective event census, never a predictor, recommendation or
trade simulator. Native intrabar extrema are outside its coverage.
"""

import argparse
from collections import Counter, deque
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import subprocess


PROTOCOL = {
    "version": "sampled_price_surge_census_v1_20261101",
    "purpose": "descriptive_event_label_not_predictor",
    "threshold_pct": "5", "lookback_seconds": 21600,
    "minimum_baseline_span_seconds": 20400, "maximum_quote_gap_seconds": 1200,
    "event_separation_seconds": 21600,
    "reference": "minimum positive recorded last price in prior six hours, excluding current quote",
    "crossing": "current >= 1.05*reference and previous < the same frozen threshold",
    "hot_link": "earliest original HOT recorded in preceding six hours; excludes event quote",
    "unknown": "missing baseline, gaps and unavailable original HOT records stay unclassified",
    "scope": "all positive-price markets in verified cached ticker/run pairs; no photo selection",
    "interpretation": "first sampled threshold crossing, not true pump onset, execution or intrabar high",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, action="append", required=True)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--source-cache", type=Path)
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.output.resolve()
    if out == repo or repo in out.parents:
        parser.error("Output must be outside the bot checkout")
    out.mkdir(parents=True, exist_ok=True)
    protocol_hash = digest(json.dumps(PROTOCOL, sort_keys=True, separators=(",", ":")).encode())
    (out / "census-protocol.json").write_text(json.dumps({
        "protocol": PROTOCOL, "sha256": protocol_hash,
        "recorded_before_label_execution_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    seed = json.loads(args.seed.read_text()) if args.seed else {}
    if seed and seed.get("protocol_sha256") != protocol_hash:
        parser.error("Different protocol; use a separately versioned census")
    previous_cutoff = seed.get("last_quote_at", 0)
    rows, cache_sources = {}, []
    for path in args.cache:
        raw = path.read_bytes()
        metadata = json.loads(path.with_suffix(".metadata.json").read_text())
        assert digest(raw) == metadata["cache_sha256"]
        n = 0
        for line in raw.splitlines():
            r = json.loads(line); n += 1
            at = r["quote_at"]
            assert at <= r["available_at"] <= at + 300
            if at in rows:
                assert rows[at]["run_id"] == r["run_id"]
                assert rows[at]["markets"] == r["markets"]
            elif at > previous_cutoff:
                rows[at] = r
        cache_sources.append({"pinned_main": metadata["pinned_main"],
                              "sha256": metadata["cache_sha256"], "rows": n})
    ordered = [rows[t] for t in sorted(rows)]
    assert ordered and len({r["run_id"] for r in ordered}) == len(ordered)
    windows = {s: deque((int(t), D(str(p))) for t, p in points)
               for s, points in seed.get("price_history_by_symbol", {}).items()}
    refs = {int(k): v for k, v in seed.get("source_references", {}).items()}
    last_event = seed.get("last_event_by_symbol", {})
    counts, events, universe = Counter(), [], set()
    for r in ordered:
        at = r["quote_at"]
        refs[at] = {k: r[k] for k in ("state_sha", "run_id", "available_at", "code_sha")}
        for symbol, values in r["markets"].items():
            price = D(str(values[0]))
            if not price.is_finite() or price <= 0:
                counts["invalid_market_quote"] += 1
                continue
            universe.add(symbol)
            history = windows.setdefault(symbol, deque())
            while history and at - history[0][0] > 21600:
                history.popleft()
            if not history or at - history[0][0] < 20400:
                counts["market_windows_without_sufficient_baseline"] += 1
            elif any(b[0] - a[0] > 1200 for a, b in zip(history, list(history)[1:])) or at - history[-1][0] > 1200:
                counts["market_windows_with_quote_gaps"] += 1
            else:
                counts["classifiable_market_windows"] += 1
                ref_at, ref_price = min(history, key=lambda x: x[1])
                threshold = ref_price * D("1.05")
                crossed = price >= threshold and history[-1][1] < threshold
                if crossed and at - last_event.get(symbol, 0) >= 21600:
                    event = {
                        "id": f"{symbol}:{at}:{ref_at}", "symbol": symbol,
                        "crossing_quote_at": at, "crossing_available_at": r["available_at"],
                        "reference_quote_at": ref_at, "reference_last": str(ref_price),
                        "crossing_last": str(price), "threshold_last": str(threshold),
                        "crossing_change_pct": str((price / ref_price - 1) * 100),
                        "state_sha": r["state_sha"], "run_id": r["run_id"],
                        "reported_code_sha": r.get("code_sha"),
                        "crossing_decision": r["decisions"].get(symbol),
                        "crossing_discovery": r["discoveries"].get(symbol),
                    }
                    events.append(event); last_event[symbol] = at
            history.append((at, price))
    assert len({e["id"] for e in events}) == len(events)
    needed = sorted({t for e in events for t in refs if 0 < e["crossing_quote_at"] - t <= 21600})
    print(json.dumps({"paired_quotes": len(ordered), "markets": len(universe),
                      "sampled_events": len(events), "source_states_to_link": len(needed)}), flush=True)
    meta_cache = json.loads(args.source_cache.read_text()) if args.source_cache and args.source_cache.exists() else {}
    cache_path = out / "original-hot-source-cache.json"
    for i, at in enumerate(needed):
        key = str(at); source = refs[at]
        prior = meta_cache.get(key)
        if prior and prior.get("state_sha") == source["state_sha"]:
            continue
        try:
            state = json.loads(subprocess.check_output([
                "git", "-C", str(repo), "show", source["state_sha"] + ":scanner_state.json"], stderr=subprocess.PIPE))
            radar = state.get("ticker_radar", {})
            assert radar.get("observed_at") == at
            runs = [x for x in state.get("scan_diagnostics", [])
                    if str(x.get("run_id")) == source["run_id"] and x.get("status") == "completed"]
            assert len(runs) == 1 and runs[0]["finished_at"] == source["available_at"]
            run = runs[0]; hot = radar.get("hot_symbols")
            hot = hot if isinstance(hot, list) and all(isinstance(s, str) for s in hot) else None
            meta_cache[key] = {
                "state_sha": source["state_sha"], "run_id": source["run_id"],
                "hot_symbols": hot, "hot_evidence_known": hot is not None,
                "btc_ok": run.get("btc_ok"),
                "hot_decisions": {s: run.get("symbols", {}).get(s) for s in (hot or [])},
                "hot_discoveries": {t["symbol"]: t for t in run.get("transitions", [])
                                    if t.get("stage") == "discovery" and t.get("symbol") in (hot or [])},
                "logged_ready_timestamps": {**state.get("sent_signals", {}), **state.get("shadow_sent_signals", {})},
            }
        except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as exc:
            meta_cache[key] = {"state_sha": source["state_sha"], "hot_evidence_known": False,
                               "hot_symbols": None, "error": type(exc).__name__}
        if i and i % 150 == 0:
            cache_path.write_text(json.dumps(meta_cache, separators=(",", ":")))
            print(json.dumps({"original_states_linked": i, "of": len(needed)}), flush=True)
    cache_path.write_text(json.dumps(meta_cache, separators=(",", ":")))
    for e in events:
        lookback = [(t, meta_cache.get(str(t), {})) for t in needed
                    if 0 < e["crossing_quote_at"] - t <= 21600]
        hot = [(t, m) for t, m in lookback if e["symbol"] in (m.get("hot_symbols") or [])]
        unknown = sum(not m.get("hot_evidence_known") for _, m in lookback)
        e["hot_source_unknown_rows"] = unknown
        if hot:
            at, m = hot[0]
            r = rows.get(at)
            e["hot_status"] = "original_hot_recorded_before_sampled_crossing"
            e["first_hot"] = {
                "quote_at": at, "available_at": refs[at]["available_at"],
                "lead_seconds_to_sampled_crossing": e["crossing_quote_at"] - refs[at]["available_at"],
                "state_sha": m["state_sha"], "run_id": m["run_id"], "btc_ok": m.get("btc_ok"),
                "ticker_values": r["markets"].get(e["symbol"]) if r else None,
                "decision": m.get("hot_decisions", {}).get(e["symbol"]),
                "discovery": m.get("hot_discoveries", {}).get(e["symbol"]),
            }
            assert e["first_hot"]["lead_seconds_to_sampled_crossing"] > 0
        else:
            e["hot_status"] = "unknown_hot_coverage" if unknown else "no_original_hot_in_six_hour_lookback"
            e["first_hot"] = None
        e["logged_ready_timestamps_in_lookback"] = sorted({m.get("logged_ready_timestamps", {}).get(e["symbol"])
            for _, m in lookback if type(m.get("logged_ready_timestamps", {}).get(e["symbol"])) is int
            and 0 < e["crossing_quote_at"] - m["logged_ready_timestamps"][e["symbol"]] <= 21600})
        e["ready_delivery_independently_verified"] = False
    grouped = Counter()
    for e in events:
        decision = (e.get("first_hot") or {}).get("decision") or {}
        grouped[decision.get("reason", e["hot_status"])] += 1
    cutoff = ordered[-1]["quote_at"]
    continuation = {
        "protocol_sha256": protocol_hash, "last_quote_at": cutoff,
        "last_event_by_symbol": last_event,
        "price_history_by_symbol": {s: [[t, str(p)] for t, p in h if cutoff - t <= 21600]
                                    for s, h in windows.items()},
        "source_references": {str(t): v for t, v in refs.items() if cutoff - t <= 21600},
    }
    (out / "census-continuation.json").write_text(json.dumps(continuation, separators=(",", ":")))
    packet = {
        "protocol": PROTOCOL, "protocol_sha256": protocol_hash,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_caches": cache_sources, "paired_quotes": len(ordered), "markets": sorted(universe),
        "quote_window": [ordered[0]["quote_at"], cutoff], "window_accounting": dict(counts),
        "event_count": len(events), "hot_status_counts": dict(Counter(e["hot_status"] for e in events)),
        "first_hot_reason_counts": dict(grouped), "events": events,
        "predictive_improvement_proven": False, "profit_or_execution_proven": False,
        "complete_intrabar_or_exchange_wide_coverage": False,
        "limitations": ["Quote-screen labels are not native candle events or pump onsets.",
                        "Historical model/source regimes differ; this is not independent future validation.",
                        "First-gate refusal does not prove all later gates passed.",
                        "HOT can precede a drawdown; lead time is not an executable gain.",
                        "Recorded READY timestamps can reflect historical paper-only policy; verify delivery separately.",
                        "Missing original HOT records are unknown, never assumed absent."],
    }
    (out / "census-evidence.json").write_text(json.dumps(packet, separators=(",", ":")))
    print(json.dumps({"events": len(events), "hot_status": packet["hot_status_counts"],
                      "rejection_groups": packet["first_hot_reason_counts"],
                      "protocol_sha256": protocol_hash}), flush=True)


if __name__ == "__main__":
    main()
