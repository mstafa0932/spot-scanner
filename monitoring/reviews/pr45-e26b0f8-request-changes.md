# Independent AI security review of spot-scanner PR #45

Date: 2026-10-18 UTC  
Reviewer identity: separately delegated Codex AI review agent (`/root/pr45_independent_security_review`). I did not implement the reviewed change. This is an independent review pass within the same AI-assisted session, not a human review, organizationally independent audit, or review by an external Codex Security service. I started with the code and diff and did not treat author reports or passing CI as approval.

**Decision: REQUEST_CHANGES for exactly `e26b0f8d8b8c0e238eea813458b6f16f5699b180`.** One blocking provenance finding and one low-severity numeric classification finding are reproduced below. This decision does not authorize merge, deployment, live trading, or a different commit.

## Pinned scope

| Item | Exact value |
| --- | --- |
| Repository / PR | `mstafa0932/spot-scanner` / https://github.com/mstafa0932/spot-scanner/pull/45 |
| Reviewed head | `e26b0f8d8b8c0e238eea813458b6f16f5699b180` |
| Feature branch | `feat/recent-candle-gap-priority-20261016` |
| Current main observed through GitHub PR metadata | `a58ed7abc40f47a101a4ac78a3457e30eb038f40` |
| Actual merge base of that main and reviewed head | `b9fd98205377bac01404463e8ce5adab21215946` |
| Authored PR delta | 32 changed files; 2,533 additions, 69 deletions |

`git merge-base a58ed7abc40f47a101a4ac78a3457e30eb038f40 e26b0f8d8b8c0e238eea813458b6f16f5699b180` returned the merge base above. The changes between this merge base and observed main affect only `scanner_state.json` and `near_misses_archive.jsonl`. Those current-main state updates are not authored PR changes and must be preserved during any later merge. Main advances independently; this report pins the stated main SHA rather than claiming to cover later state commits.

No applicable `AGENTS.md` was found in the workspace ancestry or repository. I made no implementation changes, GitHub review submission, merge, approval under the user's identity, external message, order submission, or production scanner run. The existing untracked Python cache directories were present before my review. The implementation agent subsequently added regression tests in the shared checkout; the 305-test run below preceded those test edits, and this report covers the original committed head only.

## Findings

### R1 — Medium / merge-blocking: envelope provenance is discarded before every row is marked authentic

**Locations:** `market_data.py:554-559`, `market_data.py:568-586`; downstream promotion at `market_data.py:650-652` and `candle_backfill.py:629-630` / `candle_backfill.py:238-240`.

The new native parser rejects additional fields on individual candle rows, but it does not constrain additional fields on the response root, `payload`, or `message`, and it ignores `meta`. A response explicitly marked `source="OTHER_EXCHANGE"`, `is_authentic=False`, and `data_quality="SYNTHETIC_FFILL"` at any of these envelope levels is accepted. The parser unconditionally assigns `source="PARIBU"`, and canonicalization assigns `is_authentic=True` to its rows.

Using a committed BTC response, I reproduced all four locations independently: the function returned 250 rows, every row was authentic, source was `PARIBU`, and `analyze_symbol` returned indicators. The row-level version of the same defect correctly raised `ParibuSchemaError`.

This is a new native-parser boundary gap. It does not demonstrate control of Paribu's TLS endpoint by an attacker. It demonstrates that a future endpoint/schema change which marks an entire result as synthetic or externally sourced bypasses the explicit fail-closed provenance contract. The downstream scanner's existing source and authenticity gates cannot recover the discarded information. In SHADOW mode this can corrupt indicator, confirmation, and research decisions; it does not create a real order path.

**Required resolution:** define and validate the supported envelope structure before assigning provenance, including the root, `payload`, `message`, optional `message.display`, and `meta`. Reject unsupported or contradictory provenance/schema fields at those levels. Retain acceptance of all captured valid responses and provider-published zero-volume records. Add regressions for source, authenticity, and quality flags at every supported envelope boundary. Merely checking row fields again is insufficient.

### R2 — Low / nonblocking by itself: a positive finite volume can be relabeled as a published no-trade interval

**Locations:** `market_data.py:573-584`, `market_data.py:669-674`.

`to_decimal("1e-400")` is finite and positive, but conversion to `float` produces `0.0`. The final `volume.eq(0)` check then marks the record `exchange_zero_volume=True`, with `zero_volume_semantics="exchange_published_no_trade_interval"`. I reproduced this on a recent closed row of the committed BTC fixture. The function returned 250 authentic rows and usable indicators.

This is a new parser/classification issue, not an observed exchange response and not evidence of a material trading loss. The quantity is microscopic, so severity is low, but the new diagnostic incorrectly claims that a nonzero provider volume was zero.

**Recommended resolution:** reject positive nonzero volume that underflows during the representation conversion, or preserve exact provider zero-ness separately and avoid deriving no-trade provenance from a lossy float alone. Add a regression for this case. Float overflow is already rejected downstream.

## Exact minimal reproduction for both findings

Run from the reviewed repository with the existing isolated venv. This performs no network request and imports only market data, candle repair, and indicators:

```bash
env -i PATH=/usr/bin:/bin SHADOW_MODE=true PYTHONDONTWRITEBYTECODE=1 \
  /workspace/scratch/a5bccd2d0857/test-venv/bin/python - <<'PY'
import copy
import json
from pathlib import Path
from unittest.mock import patch
import market_data as md
from candle_backfill import bind_history
from indicator_engine import analyze_symbol

record = json.loads(Path(
    'audit_evidence/native_ohlc_20261017/btc_tl_15m_recorded_300.json'
).read_text())['request']
now = (record['params']['to'] + 1) // 1000

def read(data):
    bind_history({})
    md._BACKFILL_RATE_LIMITED = False
    with patch.object(md.time, 'time', return_value=now), \
         patch.object(md, 'get_json', return_value=data):
        return md.fetch_candles('BTC_TL')

for location in ('root', 'payload', 'message', 'meta'):
    data = copy.deepcopy(record['payload'])
    if location == 'root':
        target = data
    elif location == 'meta':
        data['meta'] = {}
        target = data['meta']
    else:
        target = data[location]
    target.update(source='OTHER_EXCHANGE', is_authentic=False,
                  data_quality='SYNTHETIC_FFILL')
    frame = read(data)
    print(location, len(frame), bool(frame.is_authentic.all()),
          frame.attrs['source'], analyze_symbol(frame) is not None)

data = copy.deepcopy(record['payload'])
data['payload']['candles'][270]['volume'] = '1e-400'
stamp = data['payload']['candles'][270]['date'] // 1000
frame = read(data)
row = frame[frame.timestamp.eq(stamp)].iloc[0]
print('positive volume underflow', row.volume, bool(row.exchange_zero_volume))
PY
```

Observed output on the reviewed head:

```text
root 250 True PARIBU True
payload 250 True PARIBU True
message 250 True PARIBU True
meta 250 True PARIBU True
positive volume underflow 0.0 True
```

For R1, a fixed parser must reject the contradictory envelope rather than return these frames. For R2, it must not classify the positive provider value as a zero-volume interval.

## Verification performed

The full existing test suite passed independently with a clean environment, `SHADOW_MODE=true`, third-party pytest plugin autoload disabled, and a Python audit hook rejecting socket connections and DNS lookups:

```bash
env -i PATH=/usr/bin:/bin SHADOW_MODE=true PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /workspace/scratch/a5bccd2d0857/test-venv/bin/python - <<'PY'
import sys
def prohibit_network(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo'}:
        raise RuntimeError('Independent review prohibits network access')
sys.addaudithook(prohibit_network)
import pytest
raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider']))
PY
```

Result: **305 passed in 4.66 seconds**, exit 0. Tests which exercise scanner/main do so through their isolated mocks and guard tests; no scanner/main ran with the user's environment or secrets. Tracked implementation and state files remained unchanged by this review. `git diff --check` on the pinned authored delta passed.

Additional independent mutation probes confirmed rejection of row-level foreign-source fields, nonfinite prices, boolean volume, finite Decimal volume overflowing to float infinity, fractional milliseconds, off-grid timestamps, conflicting duplicates, more than 500 rows, and empty candle arrays. Open-only and future-only replies failed. A finite positive price that underflows to zero was removed and any replacement was explicitly inauthentic; a stale tail that required local filling was also inauthentic. The scanner rejects those frames before indicators. Already returned open rows can legitimately become closed as the frozen clock advances; that was not misreported as stale acceptance.

The 18 recorded native market responses replay through the original tests, including BTC/NMR/MINA/SAND at all three scanner timeframes and ALT/RAD at 15m. Common closed native/legacy OHLCV values match in the included comparisons. The deterministic HTTP fixtures in gap regressions are visibly identified as generated fixtures; I did not treat them as historical market data.

## Boundaries that held in this review

- The authored production delta is limited to `market_data.py`, the newest-first/cache-completeness changes in `candle_backfill.py`, and five candle diagnostic lines in `scanner.py`.
- Native requests use a fixed public HTTPS URL, encoded query parameters, at most 500 returned rows, and a configured 12-second requests timeout. A prepared request using a URL-like symbol remained on `web.paribu.com`. Both adapters used for native reads have transport retry total 0. There are at most four targeted repair callbacks after an initial read, or one bounded recent-window callback for large outages. Native HTTP 429 latches off later native reads without legacy fallback. These are bounds on application calls/rows, not a strict response-byte or wall-clock bound.
- The newest gaps are tried first. New full-history negative entries are created only after every cluster fits the four-request budget, was queried, produced no recovery, and produced no errors. Legacy/native keys are separated; TTL tests verify cache expiry does not slide on hits. More than four clusters remain retryable on the next cycle.
- Provider-published zero-volume records and local `SYNTHETIC_FFILL` are separate concepts. Native zero-volume records are intentionally accepted provider rows; they are not proof that a trade occurred. Local synthetic rows retain `is_authentic=False` and remain blocked by both scanner and BTC paths. R1 and R2 identify the remaining provenance edge cases.
- `main.py` and `run_scanner()` retain their SHADOW guards, including direct-call/environment mismatch checks. `main.py`, `indicator_engine.py`, `risk_engine.py`, execution logic, scheduling capacity, requirements, workflows, alert delivery, RSI/ATR/liquidity/risk/spread/imbalance rules, minimum history, and closed-candle confirmation rules have no authored changes in this PR. The scanner's five added lines only record candle-source diagnostics. No new credential handling or trade/order request was found.
- All 24 JSON evidence files in the checkout parsed, including the newly added fixtures/reports. A field-name scan found no token, password, secret, authorization, cookie, or API-key fields. This supports a bounded exposure check, not a comprehensive secret audit of all repository history.

## Files examined

Reviewed every file in the 32-file authored delta:

- Production: `market_data.py`, `candle_backfill.py`, `scanner.py`.
- Tests/preflight: `test_backfill_cache_expiry.py`, `test_candle_backfill.py`, `test_native_paribu_candles.py`, `test_paribu.py`, `test_recorded_market_cases.py`.
- Reports: `ENGINEERING_AUDIT_20261017.md`, `NATIVE_CANDLE_FIX_20261017.md`.
- Evidence: `audit_evidence/market_cases_20261017.json`; `audit_evidence/native_ohlc_20261017/source_contract.json`, `live_reader_validation.json`, `legacy_15m_comparison.json`; the 18 native response captures: `{alt,btc,mina,nmr,rad,sand}_tl_15m_recorded_300.json` and `{btc,mina,nmr,sand}_tl_{15m,1h,4h}_current_300.json`.

Read surrounding validation, repair, provenance gates, scanner thresholds and entry guards, plus relevant unchanged guard/confirmation tests, `main.py`, `indicator_engine.py`, `risk_engine.py`, and `requirements.txt`. Used the full offline suite for wider regression evidence; this was not a line-by-line audit of every unchanged source file or dependency.

## Limitations and next review gate

This review did not re-fetch the public exchange endpoint, web client, live market data, or original diagnostic workflows. Original-response origin and live-validation metadata are supplied evidence, not independently cryptographically verified capture provenance. The observed endpoint is a web-client interface and can change. Offline tests cannot establish continuing availability, market profitability, live alert delivery, or production deployment success.

The shared pre-existing `get_json` transport follows redirects, buffers responses before row-count validation, inherits requests' environment behavior, and uses a connect/read timeout rather than a strict whole-response deadline. These are pre-existing transport limitations; this PR does not introduce them or add credentials. They were not inflated into new PR security findings. Similarly, numeric float precision and direct `analyze_symbol` not independently enforcing authenticity predate this PR; the scanner checks authenticity before calling it. R2 concerns the newly introduced provider-zero-volume label specifically.

Resolve R1, address R2, obtain passing checks on the resulting exact head, and request a new independent review of that diff/head. The current passing suite and this report do **not** approve the original code. Do not transfer a later approval to a different commit. Preserve SHADOW mode and the latest production state throughout any later approved release.
