"""All-original-HOT comparison cohort; sampled quotes, never trade returns."""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

PROTOCOL = {
    "version": "original_hot_control_cohort_v1_20261101",
    "eligibility": "first original production HOT per market, separated by six hours, regardless of outcome",
    "prediction_clock": "finished_at of the matched original completed scan",
    "horizon_seconds": 21600, "maximum_quote_gap_seconds": 1200,
    "terminal_tolerance_seconds": 1200, "surge_pct": 5.0, "adverse_pct": -2.0,
    "surge_window": "strictly after availability, at or before six hours",
    "reference": "recorded ticker last, not an executable entry",
    "unknown": "right-censored, missing terminal, or gapped path; never counted as failure or zero return",
    "purpose": "audit comparison cohort, not an entry model or validation of relaxed filters",
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--cache", type=Path, action="append", required=True)
    p.add_argument("--source-cache", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seed", type=Path)
    a = p.parse_args()
    out = a.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    if out == a.repo.resolve() or a.repo.resolve() in out.parents:
        p.error("Output must be outside the production checkout")
    h = hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (out / "hot-controls-protocol.json").write_text(json.dumps({"protocol": PROTOCOL, "sha256": h,
        "recorded_before_cohort_execution_utc": datetime.now(timezone.utc).isoformat()}, indent=2))
    seed = json.loads(a.seed.read_text()) if a.seed else {}
    if seed and seed.get("protocol_sha256") != h:
        p.error("Changed control protocol")
    rows = {}
    for path in a.cache:
        raw = path.read_bytes(); meta = json.loads(path.with_suffix(".metadata.json").read_text())
        assert hashlib.sha256(raw).hexdigest() == meta["cache_sha256"]
        for line in raw.splitlines():
            r = json.loads(line)
            if r["quote_at"] <= seed.get("last_quote_at", 0):
                continue
            if r["quote_at"] in rows:
                assert rows[r["quote_at"]]["run_id"] == r["run_id"]
            rows[r["quote_at"]] = r
    ordered = [rows[t] for t in sorted(rows)]
    assert ordered
    original = json.loads(a.source_cache.read_text()) if a.source_cache.exists() else {}
    for r in ordered:
        key = str(r["quote_at"])
        if key in original and original[key].get("state_sha") == r["state_sha"]:
            continue
        state = json.loads(subprocess.check_output(["git", "-C", str(a.repo), "show", r["state_sha"] + ":scanner_state.json"]))
        radar = state["ticker_radar"]; assert radar["observed_at"] == r["quote_at"]
        run = next(x for x in state["scan_diagnostics"] if str(x.get("run_id")) == r["run_id"])
        assert run["status"] == "completed" and run["finished_at"] == r["available_at"]
        hot = radar.get("hot_symbols")
        assert isinstance(hot, list)
        original[key] = {"state_sha": r["state_sha"], "run_id": r["run_id"], "hot_symbols": hot,
                         "hot_evidence_known": True, "btc_ok": run.get("btc_ok"),
                         "hot_decisions": {s: run.get("symbols", {}).get(s) for s in hot}}
    a.source_cache.write_text(json.dumps(original, separators=(",", ":")))
    paths = {s: [(int(t), float(v)) for t, v in values]
             for s, values in seed.get("price_history_by_symbol", {}).items()}
    episodes = list(seed.get("pending_episodes", []))
    previous = seed.get("last_sampled_hot_by_symbol", {})
    unknown_sources = 0
    for r in ordered:
        for s, pair in r["markets"].items():
            paths.setdefault(s, []).append((r["quote_at"], float(pair[0])))
        source = original[str(r["quote_at"])]
        if not source.get("hot_evidence_known"):
            unknown_sources += 1
            continue
        at = r["available_at"]
        for s in source.get("hot_symbols", []):
            if s not in r["markets"] or at - previous.get(s, 0) < 21600:
                continue
            pair = r["markets"][s]
            episodes.append({"id": f"{s}:{r['quote_at']}", "symbol": s, "prediction_at": at,
                             "quote_at": r["quote_at"], "reference_last": pair[0], "turnover_tl": pair[1],
                             "state_sha": r["state_sha"], "run_id": r["run_id"],
                             "reported_code_sha": r.get("code_sha"), "btc_ok": source.get("btc_ok"),
                             "decision": source.get("hot_decisions", {}).get(s)})
            previous[s] = at
    last = ordered[-1]["quote_at"]
    times = {s: [t for t, _ in values] for s, values in paths.items()}
    for e in episodes:
        t, base = e["prediction_at"], float(e["reference_last"])
        horizon = t + 21600; series = paths.get(e["symbol"], []); ts = times.get(e["symbol"], [])
        if last < horizon:
            e["outcome"] = {"status": "right_censored"}; continue
        terminal = bisect_left(ts, horizon)
        if terminal >= len(ts) or ts[terminal] > horizon + 1200:
            e["outcome"] = {"status": "missing_terminal"}; continue
        start = bisect_right(ts, t)
        points = series[start:terminal + 1]
        stamps = [t] + [at for at, _ in points]
        if any(b - a > 1200 for a, b in zip(stamps, stamps[1:])):
            e["outcome"] = {"status": "gapped_path"}; continue
        strict = [(at, (price / base - 1) * 100) for at, price in points if at <= horizon]
        e["outcome"] = {"status": "observed", "terminal_at": ts[terminal],
                        "terminal_delay_seconds": ts[terminal] - horizon,
                        "sampled_surge_5pct": any(change >= 5 for _, change in strict),
                        "sampled_adverse_2pct": any(change <= -2 for _, change in strict),
                        "sampled_high_pct": max((change for _, change in strict), default=None),
                        "sampled_low_pct": min((change for _, change in strict), default=None),
                        "last_change_pct": (points[-1][1] / base - 1) * 100}
    assert len(episodes) == len({e["id"] for e in episodes})
    groups = defaultdict(Counter)
    for e in episodes:
        d = e.get("decision") or {}; reason = d.get("reason", "unknown")
        if reason.startswith("score ") or reason.startswith("discovery score "):
            reason = "score_below_threshold"
        if reason.startswith("anti-FOMO:"):
            reason = "anti_FOMO"
        if reason.startswith("watching: confirmations"):
            reason = "awaiting_confirmation"
        g = groups[reason]; g["episodes"] += 1
        status = e["outcome"]["status"]; g[status] += 1
        if status == "observed":
            g["sampled_surge_5pct"] += int(e["outcome"]["sampled_surge_5pct"])
            g["no_sampled_surge_5pct"] += int(not e["outcome"]["sampled_surge_5pct"])
            g["sampled_adverse_2pct"] += int(e["outcome"]["sampled_adverse_2pct"])
    packet = {"protocol": PROTOCOL, "protocol_sha256": h,
              "quote_window": [ordered[0]["quote_at"], last], "paired_quotes": len(ordered),
              "source_unknown_rows": unknown_sources, "episode_count": len(episodes),
              "groups": dict(groups), "episodes": episodes,
              "predictive_improvement_proven": False, "trade_or_pnl_proven": False,
              "limitations": ["Historical cohort, mixed source-code regimes, not independent future validation.",
                              "Ticker last is not executable bid/ask or an intrabar extreme.",
                              "Conditioned on original HOT; not the full exchange opportunity universe.",
                              "First refusal and later outcome do not prove that bypassing that refusal is safe."]}
    (out / "hot-controls-evidence.json").write_text(json.dumps(packet, separators=(",", ":")))
    (out / "hot-controls-continuation.json").write_text(json.dumps({
        "protocol_sha256": h, "last_quote_at": last, "last_sampled_hot_by_symbol": previous,
        "pending_episodes": [e for e in episodes if e["prediction_at"] + 21600 + 1200 > last],
        "price_history_by_symbol": {s: [[t, v] for t, v in series if last - t <= 24000]
                                    for s, series in paths.items()},
    }, separators=(",", ":")))
    print(json.dumps({"episodes": len(episodes), "unknown_source_rows": unknown_sources,
                      "groups": dict(groups), "protocol_sha256": h}), flush=True)


if __name__ == "__main__":
    main()
