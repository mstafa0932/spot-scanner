"""Replay captured initial scores/discovery; never run the scanner or notify.

Frozen RECOVERY structure is reported as features only. Full hypothetical
confirmations, trigger/risk eligibility, future outcomes and PnL are not inferred.
"""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from verify_public_capture_20261101 import read_capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--state-sha", required=True)
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if not all(len(s) == 40 and all(c in "0123456789abcdef" for c in s)
               for s in (args.state_sha, args.code_sha)):
        raise ValueError("Require immutable state/code SHAs")
    hashes = {}
    for name in ("scanner.py", "indicator_engine.py", "market_data.py", "candle_backfill.py", "risk_engine.py"):
        original = subprocess.check_output(["git", "-C", str(repo), "show", args.code_sha + ":" + name])
        if (repo / name).read_bytes() != original:
            raise ValueError("Local replay source differs from actual checkout: " + name)
        hashes[name] = hashlib.sha256(original).hexdigest()
    # Even an accidental future call into a provider/sender fails offline.
    import requests
    def blocked(*args, **kwargs):
        raise RuntimeError("Network prohibited during archive replay")
    requests.sessions.Session.request = blocked
    sys.path.insert(0, str(repo))
    import pandas as pd
    import scanner
    from market_data import Ticker, OrderBookSnapshot
    records, verification = read_capture(args.artifact, args.run, args.code_sha)
    raw = subprocess.check_output(["git", "-C", str(repo), "show", args.state_sha + ":scanner_state.json"])
    state = json.loads(raw)
    matches = [r for r in state["scan_diagnostics"] if r.get("run_id") == args.run]
    if len(matches) != 1 or matches[0]["status"] != "completed":
        raise ValueError("Missing unique original completed diagnostics")
    diagnostics = matches[0]
    tickers, books, frames = {}, {}, defaultdict(dict)
    for r in records:
        if not diagnostics["started_at"] <= r["available_at"] <= diagnostics["finished_at"]:
            raise ValueError("Record outside original scan availability")
        p = r["payload"]
        if r["kind"] == "tickers":
            tickers = {s: Ticker(s, **{k: D(v) if v is not None else None for k, v in values.items()})
                       for s, values in p.items()}
        elif r["kind"] == "book" and p["stage"] == "initial":
            values = {k: (p[k] if k == "timestamp" else D(p[k]))
                      for k in ("best_bid", "best_ask", "spread_percent", "bid_notional", "ask_notional",
                                "imbalance_ratio", "top3_bid_share", "top3_ask_share",
                                "largest_bid_wall_share", "largest_ask_wall_share", "timestamp")}
            books[p["symbol"]] = OrderBookSnapshot(p["symbol"],
                tuple((D(a), D(b)) for a, b in p["bids"]),
                tuple((D(a), D(b)) for a, b in p["asks"]), **values)
        elif r["kind"] == "candles":
            frame = pd.DataFrame(p["rows"])
            # market_data builds native OHLCV float64 columns. Python's
            # float(str(float_value)) restores the original float values
            # before the unchanged indicator code receives these columns.
            for column in ("open", "high", "low", "close", "volume"):
                frame[column] = frame[column].map(float)
            frame.attrs.update(p["attributes"])
            frames[p["symbol"]][p["resolution"]] = frame
    results = []
    for symbol in verification["markets_with_three_native_frames"]:
        f = frames[symbol]
        tech = {tf: scanner.analyze_symbol(frame) for tf, frame in f.items()}
        if any(t is None for t in tech.values()):
            results.append({"symbol": symbol, "status": "indicators_unavailable"})
            continue
        original = next(x for x in diagnostics["transitions"] if x.get("symbol") == symbol
                        and x.get("stage") == "discovery" and "score" in x)
        score, _ = scanner.score_candidate(tickers[symbol], books[symbol], tech["15m"], tech["1h"], tech["4h"])
        passed, reason = scanner.discovery_ok(tickers[symbol], books[symbol], tech["15m"], tech["1h"], tech["4h"], score)
        # run_scanner labels discovery_ok's successful "OK" return as
        # "passed_pending_trigger"; this is not a READY/trigger decision.
        original_reason = "OK" if original["reason"] == "passed_pending_trigger" else original["reason"]
        if score != original["score"] or reason != original_reason:
            raise ValueError(f"Replayed original score/discovery differs for {symbol}: "
                             f"{score}/{reason} versus {original['score']}/{original['reason']}")
        rows15 = f["15m"].to_dict("records")
        lows = [D(str(r["low"])) for r in rows15[-3:]]
        base = sum(D(str(r["volume"])) for r in rows15[-23:-3]) / D(20)
        volumes = [D(str(r["volume"])) for r in rows15[-3:]]
        ratio = sum(volumes) / D(3) / base if base > 0 else None
        h4 = f["4h"].to_dict("records")[-2:]
        setup_ok, setup = scanner.setup_type(tech["15m"])
        pattern = {"setup_is_existing_recovery": bool(setup_ok and setup == "RECOVERY"),
                   "three_lows_nondecreasing_with_strict_rise": lows[0] <= lows[1] <= lows[2] and lows[0] < lows[2],
                   "two_of_three_volumes_above_prior_twenty": base > 0 and sum(v > base for v in volumes) >= 2,
                   "three_bar_volume_ratio_at_least_1_30": ratio is not None and ratio >= D("1.30"),
                   "two_closed_four_hour_lows_and_closes_nondecreasing":
                       D(str(h4[1]["low"])) >= D(str(h4[0]["low"]))
                       and D(str(h4[1]["close"])) >= D(str(h4[0]["close"]))}
        results.append({"symbol": symbol, "status": "exact_original_score_and_discovery_reproduced",
                        "score": score, "discovery_passed": passed, "discovery_reason": reason,
                        "setup": setup, "frozen_pattern_features": pattern,
                        "all_frozen_structure_features": all(pattern.values()),
                        "entry_risk_confirmations_and_outcome": "NOT_EVALUATED_NO_ENTRY_CLAIM"})
    output = {"schema": "public_capture_original_discovery_replay_v1", "run_id": args.run,
              "actual_code_sha": args.code_sha, "state_sha": args.state_sha,
              "source_sha256": hashes, "input_verification": verification,
              "pattern_protocol_sha256": "6c99f4c9daf4a131faebfc7bd0234fe179f10f83d2e4fe642d97d81c73d8333d",
              "results": results, "statuses": dict(Counter(r["status"] for r in results)),
              "pattern_feature_positive": sum(r.get("all_frozen_structure_features", False) for r in results),
              "complete_strategy_comparison_or_predictive_improvement_proven": False,
              "notify": False, "is_ready": False, "is_trade": False, "pnl": "NOT_CALCULATED"}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: output[k] for k in ("run_id", "statuses", "pattern_feature_positive", "complete_strategy_comparison_or_predictive_improvement_proven")}))


if __name__ == "__main__":
    main()
