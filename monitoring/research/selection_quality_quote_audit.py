"""Temporary offline audit; no trading, HTTP, or production-state writes."""
from collections import Counter
from datetime import datetime, timezone
import bisect
import json
import os
from pathlib import Path
import statistics
import subprocess

ROOT = Path(os.environ.get("PARIBU_AUDIT_OUTPUT", "/tmp/paribu-selection-audit-20261028"))
ROOT.mkdir(parents=True, exist_ok=True)
REPO = Path(os.environ.get("PARIBU_AUDIT_REPO", str(Path.cwd())))
PIN = '3bc53d6c6cae6a13ac1dfdd00e52f58646472e60'
START = int(datetime(2026, 10, 26, 7, tzinfo=timezone.utc).timestamp())
FOCUS = {'BONK_TL', 'ETHFI_TL', 'RVN_TL', 'MEGA_TL', 'PEPE_TL'}

def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO, text=True)

def valid(value):
    try:
        value = float(value)
        return value if 0 < value < float('inf') else None
    except (ValueError, TypeError):
        return None

commits = git('rev-list', '--max-count=380', PIN).splitlines()
quotes, runs, signals = {}, {}, {}
for sha in commits:
    state = json.loads(git('show', sha + ':scanner_state.json'))
    ticker = state.get('ticker_radar', {})
    at = ticker.get('observed_at', 0)
    if at >= START and at not in quotes:
        quotes[at] = {'state_sha': sha, 'markets': ticker.get('markets', {})}
    for run in state.get('scan_diagnostics', []):
        if run.get('status') == 'completed' and run.get('started_at', 0) >= START:
            runs.setdefault(str(run['run_id']), {'state_sha': sha, **run})
    for signal in state.get('active_signals', []):
        if signal.get('opened_at', 0) >= START:
            signals.setdefault(signal['id'], signal)
times = sorted(quotes)
ordered = sorted(runs.values(), key=lambda r: r['started_at'])

def measurement(symbol, started):
    """Sampled last-price changes, deliberately not executable returns."""
    pos = bisect.bisect_right(times, started) - 1
    if pos < 0 or started - times[pos] > 180:
        return {'status': 'missing_reference_quote'}
    anchor_at = times[pos]
    anchor = valid(quotes[anchor_at]['markets'].get(symbol, {}).get('last'))
    if anchor is None:
        return {'status': 'missing_reference_quote'}
    result = {'reference_at': anchor_at, 'reference_last': anchor, 'horizons': {}}
    for hours in (1, 4, 6):
        end_at = started + hours * 3600
        end_pos = bisect.bisect_left(times, end_at)
        if end_pos >= len(times) or times[end_pos] - end_at > 1200:
            result['horizons'][str(hours)] = {'status': 'right_censored_or_missing'}
            continue
        observations = [(at, valid(quotes[at]['markets'].get(symbol, {}).get('last')))
                        for at in times[pos:end_pos + 1] if at >= started]
        observations = [(at, price) for at, price in observations if price is not None]
        exit_price = valid(quotes[times[end_pos]]['markets'].get(symbol, {}).get('last'))
        if exit_price is None or not observations:
            result['horizons'][str(hours)] = {'status': 'missing_quotes'}
            continue
        gaps = [b[0] - a[0] for a, b in zip(observations, observations[1:])]
        result['horizons'][str(hours)] = {
            'status': 'observed', 'observed_until': times[end_pos],
            'quote_samples': len(observations), 'max_sample_gap_seconds': max(gaps, default=0),
            'last_change_pct': (exit_price / anchor - 1) * 100,
            'sampled_high_change_pct': (max(p for _, p in observations) / anchor - 1) * 100,
            'sampled_low_change_pct': (min(p for _, p in observations) / anchor - 1) * 100,
        }
    return result

accepted = []
for signal in sorted(signals.values(), key=lambda s: s['opened_at']):
    candidates = [r for r in ordered if r['started_at'] <= signal['opened_at'] <= r.get('finished_at', 0)]
    run = next((r for r in candidates if r.get('symbols', {}).get(signal['symbol'], {}).get('stage') == 'shadow'), None)
    row = {'signal': signal, 'run_id': run['run_id'] if run else None}
    if run:
        row['discovery_evidence'] = [t for t in run.get('transitions', [])
            if t.get('symbol') == signal['symbol'] and t.get('stage') == 'discovery']
        row['sampled_quote_path'] = measurement(signal['symbol'], run['started_at'])
    accepted.append(row)

# Fixed rule, independent of future prices: first non-sent high-score observation per symbol
# per six hours. Some observations are unassessed after the per-run send cap; they are not all proven rejections. Episodes may overlap sent episodes and are not independent.
rejected, last = [], {}
for run in ordered:
    for symbol, decision in run.get('symbols', {}).items():
        if not isinstance(decision, dict) or decision.get('stage') in {'shadow', 'notification'}:
            continue
        if decision.get('score', 0) < 80:
            continue
        at = run['started_at']
        if at - last.get(symbol, 0) < 21600:
            continue
        last[symbol] = at
        rejected.append({'symbol': symbol, 'run_id': run['run_id'], 'started_at': at,
                         'decision': decision, 'sampled_quote_path': measurement(symbol, at)})

def summarize(items):
    out = {}
    for hours in ('1', '4', '6'):
        measured = [x.get('sampled_quote_path', {}).get('horizons', {}).get(hours, {}) for x in items]
        observed = [x['last_change_pct'] for x in measured if x.get('status') == 'observed']
        out[hours] = {'observed_events': len(observed), 'unobserved_events': len(items)-len(observed),
                      'mean_sampled_last_change_pct': statistics.mean(observed) if observed else None,
                      'median_sampled_last_change_pct': statistics.median(observed) if observed else None}
    return out

output = {
    'pinned_main': PIN, 'run_count': len(ordered), 'quote_snapshots': len(times),
    'window_utc': [datetime.fromtimestamp(times[i], timezone.utc).isoformat() for i in (0, -1)],
    'sent_signals': accepted, 'non_sent_high_score_episodes': rejected,
    'summaries': {'sent': summarize(accepted), 'not_sent_high_score': summarize(rejected)},
    'limitations': [
        'Sampled ticker last prices are not executable bids, fills, exact intrabar MFE/MAE, or PnL.',
        'The sample is bounded; rejected episodes may overlap sent episodes and are not independent.',
        'No threshold, score weight, stop, target, or strategy was tuned on these outcomes.',
        'No transaction costs or order-book reconstruction are invented.',
        'Right-censored outcomes remain missing, never zero or assumed successful.',
        'Photographed winners are a selected subset, not a representative calibration sample.',
        'Signal tracking incomplete flags and model fill estimates are preserved.',
    ],
}
ROOT.joinpath('comparison.json').write_text(json.dumps(output, ensure_ascii=False, indent=2))
print(json.dumps({'window':output['window_utc'], 'runs':len(ordered), 'quotes':len(times),
                  'signals':len(accepted), 'non_sent_episodes':len(rejected),
                  'summaries':output['summaries']}, ensure_ascii=False))
for event in accepted:
    s = event['signal']
    print(json.dumps({'id':s['id'], 'run':event['run_id'], 'status':s['status'],
                      'tracking_incomplete':s.get('tracking_incomplete', False),
                      'discovery':event.get('discovery_evidence'),
                      'path':event.get('sampled_quote_path')}, ensure_ascii=False))
