"""Offline precursor research from saved Paribu snapshots; never entry signals.

No exchange/network/Telegram calls, production imports, or production writes.
Replay hypotheses are deliberately frozen, not optimized on photographed winners.
Ticker observations do not establish executable prices or authentic OHLC candles.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess


@dataclass(frozen=True)
class Protocol:
    version: str = "precursor_research_v1_20261029"
    min_quote_volume_tl: float = 5_000_000
    max_gap_seconds: int = 1200
    history_seconds: int = 3600
    minimum_history_seconds: int = 3000
    max_recent_gain_pct: float = 2.30
    max_compression_pct: float = 2.0
    max_breakout_step_pct: float = 0.75
    episode_cooldown_seconds: int = 21600
    surge_label_pct: float = 5.0
    adverse_label_pct: float = -2.0
    retrospective_boundary: int = 1792789200  # 24 October 00:00 Europe/Istanbul
    prospective_start: int = 1793224800  # 29 October 01:00 Europe/Istanbul; after initial pinned evidence


RULES = ("hot_once", "persistent_heat", "compression_heat", "recorded_ready")
EXCLUDED = {"USDT_TL", "USDC_TL", "BTC_TL"}


def number(value):
    if type(value) is bool:
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def positive(value):
    n = number(value)
    return n if n is not None and n > 0 else None


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo)


def extract(repo, pin, since, cache):
    """Each row uses the FIRST completed saved state for that ticker timestamp.

    Reject unmatched/stale quote/run pairs. Keep only one state in memory. The
    cache is input evidence, not production scanner state. Its pin is immutable.
    """
    meta = cache.with_suffix(".metadata.json")
    if cache.exists() and meta.exists():
        old = json.loads(meta.read_text())
        if old.get("pinned_main") == pin and old.get("since") == since:
            digest = hashlib.sha256(cache.read_bytes()).hexdigest()
            if digest == old.get("cache_sha256"):
                return old
    commits = git(repo, "log", "--first-parent", "--reverse", "--format=%H",
                  "--since=" + iso(since - 3600), pin, "--", "scanner_state.json").decode().splitlines()
    counts, seen = Counter(), set()
    first = last = None
    with cache.open("w") as out:
        for index, sha in enumerate(commits):
            state = json.loads(git(repo, "show", sha + ":scanner_state.json"))
            counts["states_read"] += 1
            radar = state.get("ticker_radar") or {}
            quote_at = radar.get("observed_at")
            if type(quote_at) is not int or quote_at < since or quote_at in seen:
                counts["missing_old_or_duplicate_ticker"] += 1
                continue
            matches = [r for r in state.get("scan_diagnostics", [])
                       if r.get("status") == "completed"
                       and abs(r.get("started_at", 0) - quote_at) <= 180
                       and quote_at <= r.get("finished_at", 0) <= quote_at + 300]
            if not matches:
                counts["unmatched_completed_run"] += 1
                continue
            run = min(matches, key=lambda r: abs(r["started_at"] - quote_at))
            decisions = {s: {k: d[k] for k in ("stage", "reason", "score", "spread_pct", "imbalance") if k in d}
                         for s, d in run.get("symbols", {}).items() if isinstance(d, dict)}
            discoveries = {t["symbol"]: {k: t[k] for k in
                           ("reason", "observed_at", "score", "rsi", "return_3", "volume_ratio",
                            "atr_pct", "closed_candle_at", "authentic_only") if k in t}
                           for t in run.get("transitions", [])
                           if t.get("stage") == "discovery" and "symbol" in t}
            markets = {}
            for symbol, value in radar.get("markets", {}).items():
                price, volume = positive(value.get("last")), positive(value.get("quote_volume"))
                if price is not None:
                    markets[symbol] = [price, volume]
            row = {"quote_at": quote_at, "available_at": run["finished_at"],
                   "state_sha": sha, "code_sha": run.get("code_sha"), "run_id": str(run["run_id"]),
                   "btc_ok": run.get("btc_ok") is True, "markets": markets,
                   "decisions": decisions, "discoveries": discoveries}
            out.write(json.dumps(row, separators=(",", ":")) + "\n")
            counts["paired_snapshots"] += 1
            seen.add(quote_at)
            first = quote_at if first is None else min(first, quote_at)
            last = quote_at if last is None else max(last, quote_at)
            if index and index % 400 == 0:
                print(json.dumps({"extracted_states": index, "paired": counts["paired_snapshots"]}), flush=True)
    result = {"pinned_main": pin, "since": since, "counts": dict(counts),
              "first_quote_at": first, "last_quote_at": last,
              "cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest()}
    meta.write_text(json.dumps(result, indent=2))
    return result


def features(history, at, price, volume, protocol=Protocol()):
    """Read past quotes only; appending any future quote cannot change this row."""
    past = [x for x in history if 0 < at - x[0] <= protocol.history_seconds]
    if not past or not positive(price) or not positive(volume):
        return None
    previous = past[-1]
    if at - previous[0] > protocol.max_gap_seconds:
        return None
    hot = previous[2] is not None and price > previous[1] and volume > previous[2]
    result = {"hot_once": bool(hot), "persistent_heat": False, "compression_heat": False,
              "feature_max_quote_at": at, "history_quotes": len(past),
              "rolling_volume_basis": "24h_turnover_change_proxy_not_executed_buy_flow"}
    gaps = [b[0] - a[0] for a, b in zip(past, past[1:])]
    if at - past[0][0] < protocol.minimum_history_seconds or max(gaps, default=0) > protocol.max_gap_seconds:
        return result
    recent_gain = (price / past[0][1] - 1) * 100
    width = (max(x[1] for x in past) / min(x[1] for x in past) - 1) * 100
    prior_hot = (len(past) >= 2 and past[-2][2] is not None and previous[2] is not None
                 and previous[1] > past[-2][1] and previous[2] > past[-2][2])
    step = (price / previous[1] - 1) * 100
    persistent = bool(hot and prior_hot and 0 < recent_gain <= protocol.max_recent_gain_pct)
    recent = [x[1] for x in past if at - x[0] <= 1200]
    compressed = bool(persistent and width <= protocol.max_compression_pct and recent
                      and price > max(recent) and step <= protocol.max_breakout_step_pct)
    result.update(persistent_heat=persistent, compression_heat=compressed,
                  recent_gain_pct=recent_gain, prior_range_pct=width, last_step_pct=step)
    return result


def classify_path(series, prediction_at, anchor, hours, protocol=Protocol()):
    """Labels use later quotes ONLY after the candidate is frozen.

    Missing data/cadence gaps are unknown, never a failed or successful signal.
    Sampled extrema remain restricted to the requested horizon; a late terminal
    quote cannot count as a surge inside that horizon.
    """
    due = prediction_at + hours * 3600
    times = [x[0] for x in series]
    terminal_index = bisect.bisect_left(times, due)
    if terminal_index == len(series):
        return {"status": "right_censored"}
    terminal_at, terminal_price = series[terminal_index]
    if terminal_at - due > protocol.max_gap_seconds:
        return {"status": "missing_terminal_quote"}
    start_index = bisect.bisect_right(times, prediction_at)
    observed = series[start_index:terminal_index + 1]
    cadence = [prediction_at] + [x[0] for x in observed]
    if any(b - a > protocol.max_gap_seconds for a, b in zip(cadence, cadence[1:])):
        return {"status": "quote_gap"}
    path = [(at, (price / anchor - 1) * 100) for at, price in observed if at <= due]
    if not path:
        return {"status": "missing_path"}
    surge = next((at for at, change in path if change >= protocol.surge_label_pct), None)
    adverse = next((at for at, change in path if change <= protocol.adverse_label_pct), None)
    return {"status": "observed", "terminal_at": terminal_at,
            "terminal_delay_seconds": terminal_at - due,
            "last_change_pct": (terminal_price / anchor - 1) * 100,
            "sampled_high_pct": max(x[1] for x in path), "sampled_low_pct": min(x[1] for x in path),
            "surge_5pct": surge is not None, "surge_first_at": surge,
            "adverse_2pct": adverse is not None,
            "surge_before_adverse": surge is not None and (adverse is None or surge < adverse),
            "lead_seconds_to_sampled_surge": surge - prediction_at if surge is not None else None}


def discovery_status(row, symbol, available_at):
    d = row.get("discoveries", {}).get(symbol, {})
    candle = d.get("closed_candle_at")
    if d.get("authentic_only") is not True or type(candle) is not int or candle + 900 > available_at:
        return "not_revalidated_or_missing_candle_evidence"
    if d.get("reason") == "passed_pending_trigger":
        return "discovery_passed_not_ready_or_risk_approved"
    return "discovery_rejected:" + str(d.get("reason", "unknown"))


def freeze_events(rows, protocol=Protocol(), continuity=None):
    histories, last_event = defaultdict(deque), {}
    if continuity:
        for symbol, values in continuity.get("history_by_symbol", {}).items():
            histories[symbol] = deque(tuple(x) for x in values)
        last_event.update({(rule, symbol): at for rule, symbol, at
                           in continuity.get("last_events", [])})
    previous_quote_at = (continuity or {}).get("last_quote_at", 0)
    events = []
    for row in sorted(rows, key=lambda x: x["quote_at"]):
        at, available_at = row["quote_at"], row["available_at"]
        if at <= previous_quote_at:
            continue
        for symbol, (price, volume) in sorted(row["markets"].items()):
            history = histories[symbol]
            feat = features(history, at, price, volume, protocol)
            decision = row.get("decisions", {}).get(symbol, {})
            eligible = (symbol.endswith("_TL") and symbol not in EXCLUDED and volume is not None
                        and volume >= protocol.min_quote_volume_tl and row.get("btc_ok") is True)
            for rule in RULES:
                qualifies = (decision.get("stage") == "shadow" if rule == "recorded_ready"
                             else bool(eligible and feat and feat.get(rule)))
                key = (rule, symbol)
                cooldown = 0 if rule == "recorded_ready" else protocol.episode_cooldown_seconds
                if not qualifies or available_at - last_event.get(key, -10**12) < cooldown:
                    continue
                last_event[key] = available_at
                events.append({"id": f"{rule}:{symbol}:{row['run_id']}", "rule": rule, "symbol": symbol,
                               "prediction_at": available_at, "reference_quote_at": at,
                               "reference_last": price, "run_id": row["run_id"], "state_sha": row["state_sha"],
                               "decision": decision, "discovery_status": discovery_status(row, symbol, available_at),
                               "features": feat, "mode": "offline_hypothesis_not_entry_signal"})
            history.append((at, price, volume))
            while history and at - history[0][0] > protocol.history_seconds:
                history.popleft()
    if continuity is not None:
        last_quote_at = max([previous_quote_at] + [r["quote_at"] for r in rows])
        continuity["history_by_symbol"] = {s: list(v) for s, v in histories.items() if v
                                           and last_quote_at - v[-1][0] <= protocol.history_seconds}
        continuity["last_events"] = [[rule, symbol, at] for (rule, symbol), at in last_event.items()
                                     if last_quote_at - at <= protocol.episode_cooldown_seconds]
        continuity["last_quote_at"] = last_quote_at
    return events


def summarize(events):
    out = {}
    for hours in (1, 4, 6):
        paths = [e["outcomes"][str(hours)] for e in events]
        observed = [x for x in paths if x["status"] == "observed"]
        n = len(observed)
        out[str(hours)] = {"events": len(events), "observed": n,
                           "unknown": len(paths) - n,
                           "status_counts": dict(Counter(x["status"] for x in paths)),
                           "sampled_surge_5pct": sum(x["surge_5pct"] for x in observed),
                           "no_sampled_surge_5pct": sum(not x["surge_5pct"] for x in observed),
                           "adverse_2pct": sum(x["adverse_2pct"] for x in observed),
                           "surge_before_adverse": sum(x["surge_before_adverse"] for x in observed),
                           "mean_last_change_pct": statistics.mean(x["last_change_pct"] for x in observed) if n else None,
                           "median_lead_minutes_when_hit": statistics.median(x["lead_seconds_to_sampled_surge"] / 60
                               for x in observed if x["surge_5pct"]) if any(x["surge_5pct"] for x in observed) else None}
    return out


def replay(rows, protocol=Protocol(), continuity=None):
    old_pending = json.loads(json.dumps((continuity or {}).get("pending_events", [])))
    events = old_pending + freeze_events(rows, protocol, continuity)
    paths = defaultdict(list)
    for symbol, values in (continuity or {}).get("price_history_by_symbol", {}).items():
        paths[symbol].extend(tuple(x) for x in values)
    for row in sorted(rows, key=lambda x: x["quote_at"]):
        for symbol, (price, _) in row["markets"].items():
            paths[symbol].append((row["quote_at"], price))
    for symbol, values in paths.items():
        paths[symbol] = sorted(dict(values).items())
    for event in events:
        event["outcomes"] = {str(h): classify_path(paths[event["symbol"]], event["prediction_at"],
                                                  event["reference_last"], h, protocol) for h in (1, 4, 6)}
        event["period"] = ("prospective" if event["prediction_at"] >= protocol.prospective_start else
                           "late_retrospective" if event["prediction_at"] >= protocol.retrospective_boundary else
                           "early_retrospective")
    if continuity is not None:
        last = continuity["last_quote_at"]
        continuity["pending_events"] = [e for e in events if e["prediction_at"]
                                        + 21600 + protocol.max_gap_seconds > last]
        continuity["price_history_by_symbol"] = {
            s: [x for x in values if last - x[0] <= 21600 + 2 * protocol.max_gap_seconds]
            for s, values in paths.items()}
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--pin", required=True)
    parser.add_argument("--since", default="2026-10-13T21:00:00+00:00")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=Path, help="Resume saved research state; never a production state file")
    args = parser.parse_args()
    repo, output = args.repo.resolve(), args.output.resolve()
    if output == repo or repo in output.parents:
        parser.error("Output must be outside the production checkout")
    output.mkdir(parents=True, exist_ok=True)
    pin = git(repo, "rev-parse", args.pin + "^{commit}").decode().strip()
    continuity = json.loads(args.seed.read_text()) if args.seed else {}
    protocol = asdict(Protocol())
    protocol_hash = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.seed and continuity.get("protocol_sha256") != protocol_hash:
        parser.error("Seed protocol differs: do not retune an ongoing evaluation")
    if args.seed and continuity.get("research_code_sha256") != code_hash:
        parser.error("Research code changed: start a separately versioned evaluation")
    if args.seed and subprocess.run(["git", "merge-base", "--is-ancestor",
                                    continuity["last_pinned_main"], pin], cwd=repo,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        parser.error("New pin does not extend the saved main history")
    since = (continuity["last_quote_at"] + 1 if args.seed
             else int(datetime.fromisoformat(args.since).timestamp()))
    cache = output / "snapshot-evidence.jsonl"
    metadata = extract(repo, pin, since, cache)
    rows = [json.loads(line) for line in cache.open()]
    events = replay(rows, continuity=continuity)
    continuity.update(protocol_sha256=protocol_hash, protocol_version=Protocol().version,
                      research_code_sha256=code_hash,
                      last_pinned_main=pin, note="Incremental research only; merge episode IDs before cumulative counts")
    (output / "continuation.json").write_text(json.dumps(continuity, separators=(",", ":")))
    summaries = {period: {rule: summarize([e for e in events if e["rule"] == rule
                 and (period == "all" or e["period"] == period)]) for rule in RULES}
                 for period in ("all", "early_retrospective", "late_retrospective", "prospective")}
    result = {"schema_version": 1, "protocol": protocol, "protocol_sha256": protocol_hash,
              "incremental": bool(args.seed), "evidence": metadata,
              "observed_checkout_sha_count": len({r.get("code_sha") for r in rows}),
              "code_history_note": "Checkout SHA changes may be state-only commits; unique checkout hashes are not unique source-code regimes.",
              "summaries": summaries, "production_changed": False,
              "performance_improvement_proven": False, "independent_future_validation_complete": False,
              "limitations": ["Retrospective rules informed by prior cases: time splits are not independent validation.",
                "Rolling 24h turnover differences are activity proxies, not interval trade volume or executed buying.",
                "All-market cohorts include non-rallies; user cases never determine eligibility or labels.",
                "Six-hour episodes overlap across rules; hit rates do not establish statistical superiority.",
                "Ticker last snapshots are not executable prices, true intrabar extrema, fills or PnL.",
                "Missing/gapped/right-censored outcomes remain unknown.",
                "Liquidity and BTC screens are retained; research tags do not bypass spread, book, candles, risk or READY.",
                "Older code/provenance regimes are distinct; quotes are not a candle-authenticity replay.",
                "No transaction-cost profitability conclusion or production calibration is approved."]}
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with (output / "episodes.jsonl").open("w") as out:
        for event in events:
            out.write(json.dumps(event, separators=(",", ":")) + "\n")
    print(json.dumps({"evidence": metadata, "event_counts": dict(Counter(e["rule"] for e in events)),
                      "all_six_hour_summaries": {k: v["6"] for k, v in summaries["all"].items()}}, indent=2))


if __name__ == "__main__":
    main()
