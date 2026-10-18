# Independent AI security re-review — spot-scanner PR #45

Date: 2026-10-18 UTC.  
Reviewer: separately delegated Codex AI review agent `/root/pr45_independent_security_review`. I did not author the implementation or regression tests. I reported the original findings and independently reviewed the implementer's remediation. This is a separate AI review pass within the same session, not a human, organizationally independent audit, or external Codex Security service review.

**Decision: APPROVE the code at exactly `0f0cc5e18ad1d2afd02f6fb36d32de688f1ecdce`.** Both findings from the original-head review are resolved. No unresolved merge-blocking security finding was reproduced in the reviewed PR scope. This is a code-review decision, not a GitHub approval submitted under the user's identity, a merge/deployment action, or permission for real trading. `SHADOW_MODE=true` remains required.

## Exact target and scope

| Item | Pinned value |
| --- | --- |
| PR | https://github.com/mstafa0932/spot-scanner/pull/45 |
| Reviewed head | `0f0cc5e18ad1d2afd02f6fb36d32de688f1ecdce` |
| Reviewed tree | `00fb6f46261469796ab621283b89c4a2058695c0` |
| Previously rejected head | `e26b0f8d8b8c0e238eea813458b6f16f5699b180` |
| Main observed in GitHub PR metadata | `a58ed7abc40f47a101a4ac78a3457e30eb038f40` |
| Actual merge base with observed main | `b9fd98205377bac01404463e8ce5adab21215946` |
| Full authored PR scope | 32 files, 2,596 additions, 69 deletions |
| Remediation delta from rejected head | Only `market_data.py` and `test_native_paribu_candles.py`; 67 additions, 4 deletions |

The original report, `pr45_independent_review.md`, remains a valid **REQUEST_CHANGES** decision for the older `e26b0f8...` head. This separate report supersedes that decision only for the new exact head above. Main's later state updates are outside the authored PR delta; preserve the latest scanner state and alert history during any later merge. Recheck head and required checks immediately before merging.

I inspected the complete remediation diff, the updated parser in context, and its existing validation, repair, source, and authenticity boundaries. I carried forward the full original review of all 32 changed files and checked that only the two listed files changed afterward. No applicable `AGENTS.md` was found during the original review. I made no production/source, state, GitHub, messaging, or trading changes. The tracked checkout remained clean; the same pre-existing untracked Python cache directories remained.

## Original findings and resolution

| Finding | Original severity | Resolution in reviewed head |
| --- | --- | --- |
| R1: envelope source/authenticity/quality fields discarded before promoting rows to authentic Paribu | Medium; merge-blocking | Resolved. `market_data.py:554-580` constrains the root, message, display, payload, and cursor structure and permits only null/absent `meta`. Unsupported provenance fields fail before authenticity is assigned. |
| R2: positive finite volume underflows to float zero and is labeled as a published no-trade interval | Low | Resolved. `market_data.py:604-606` rejects a nonzero Decimal volume whose float representation is zero. |

I reran the original bypasses, expanded to all five envelope boundaries: root, `payload`, `message`, `message.display`, and `meta`. Foreign-source, false-authenticity, and synthetic-quality declarations are rejected at every location. The `1e-400` case now raises `ParibuSchemaError` rather than returning an exchange-zero-volume row. Exact zero and a representable positive subnormal remain correctly distinguishable.

The stricter envelope intentionally fails closed on future schema/metadata extensions. All 18 recorded real-response fixtures still pass; I found no regression for the supported observed envelope. Continued availability of the evolving web-client interface remains an operational limitation.

## Independent verification

### Full suite

From `/workspace/scratch/a5bccd2d0857/spot-scanner`, I ran the following against the clean exact head, using Python 3.12.14:

```bash
env -i PATH=/usr/bin:/bin SHADOW_MODE=true PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /workspace/scratch/a5bccd2d0857/test-venv/bin/python - <<'PY'
import sys
def prohibit_network(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo'}:
        raise RuntimeError('Independent re-review prohibits network access')
sys.addaudithook(prohibit_network)
import pytest
raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider']))
PY
```

**Result: 321 passed in 4.43 seconds; exit 0.** The environment contained no inherited user credentials. Network access was denied by the audit hook. Scanner/main exercises were the suite's isolated mocked and guard tests, not production scanner runs. No live orders or messages were sent.

The 16 remediation regressions can also be selected directly:

```bash
SHADOW_MODE=true /workspace/scratch/a5bccd2d0857/test-venv/bin/python -m pytest -q \
  test_native_paribu_candles.py \
  -k 'native_provenance_metadata_cannot_be_discarded or positive_native_volume_cannot_underflow'
```

These are included in the independently passing 321-test run. I did not rely on the implementer's reported pre-fix red run for my decision; the original report already contains my independently reproduced original failures.

### Separate adversarial probes and capture replay

I executed independent in-memory probes in addition to the repository tests:

- All **18 original native captures** returned 250 closed, contiguous, authentic provider rows with no backfill request and no local synthetic rows. All returned OHLCV floats matched the captures exactly under the existing Decimal-to-float representation (`pd.testing.assert_frame_equal(..., check_exact=True, check_dtype=False)`). All yielded usable indicators. The returned frames collectively contained 237 provider-zero-volume rows; captures overlap, so this is not a count of unique historical intervals.
- A **39-case rejection matrix** passed: 15 provenance fields across five envelope boundaries, seven malformed cursor values, six malformed display objects, five non-null metadata values, and six hostile volume values. Every case raised a `ParibuDataError` subclass without accepting a frame.
- Volumes `0`, `-0`, and `0E-999` remained zero-volume records. `5e-324` and `1e-100` remained positive, with `exchange_zero_volume=False`.
- A local unrecovered gap remained `SYNTHETIC_FFILL`, `is_authentic=False`, and `exchange_zero_volume=False`. The provider-zero-volume count stayed zero for that generated gap fixture. Existing scanner/BTC tests verify rejection before indicators and confirmation.
- A native HTTP 429 caused the first read to fail and blocked a second market's native read. Only one underlying call occurred, at the native endpoint, without legacy retry.
- `git diff --check` on the full pinned authored delta passed. `git diff --quiet` and `git diff --cached --quiet` passed after review.

The following minimal probe reproduces the repaired boundaries offline:

```bash
env -i PATH=/usr/bin:/bin SHADOW_MODE=true PYTHONDONTWRITEBYTECODE=1 \
  /workspace/scratch/a5bccd2d0857/test-venv/bin/python - <<'PY'
import copy
import json
from pathlib import Path
from unittest.mock import patch
import market_data as md
from candle_backfill import bind_history

record = json.loads(Path(
    'audit_evidence/native_ohlc_20261017/btc_tl_15m_recorded_300.json'
).read_text())['request']
now = (record['params']['to'] + 1) // 1000

def rejected(data):
    bind_history({})
    md._BACKFILL_RATE_LIMITED = False
    with patch.object(md.time, 'time', return_value=now), \
         patch.object(md, 'get_json', return_value=data):
        try:
            md.fetch_candles('BTC_TL')
        except md.ParibuSchemaError:
            return
    raise AssertionError('unsafe acceptance')

for location in ('root', 'payload', 'message', 'display', 'meta'):
    for field, value in (('source', 'OTHER_EXCHANGE'),
                         ('is_authentic', False),
                         ('data_quality', 'SYNTHETIC_FFILL')):
        data = copy.deepcopy(record['payload'])
        if location == 'root':
            target = data
        elif location == 'display':
            target = data['message']['display']
        elif location == 'meta':
            data['meta'] = {}
            target = data['meta']
        else:
            target = data[location]
        target[field] = value
        rejected(data)
data = copy.deepcopy(record['payload'])
data['payload']['candles'][270]['volume'] = '1e-400'
rejected(data)
print('15 provenance cases and positive-volume underflow rejected')
PY
```

### GitHub checks observed separately

Read-only GitHub queries associated [Research regression tests run 39094757437](https://github.com/mstafa0932/spot-scanner/actions/runs/39094757437) with the exact reviewed head and reported the run **completed / success**. Both `tests (3.10)` and `tests (3.12)`, including their pytest steps, completed successfully. The legacy combined-status endpoint returned no statuses; I used the workflow run/jobs to establish these test results. This review approval is based on code inspection and my independent local probes, not CI alone. The parent must still verify any other required repository checks and head stability before merging.

## Full PR safety assessment retained after remediation

- The public native HTTPS endpoint and encoded request parameters remain fixed. The maximum response row count is 500; the configured requests timeout is 12 seconds; native transport retry total is zero. Recovery remains bounded to four targeted requests or one recent-window probe after the initial read. A 429 prevents later native calls in the process. There are no new credentials, order submissions, or external write paths.
- Newest-first recovery and complete-workload negative caching are unchanged from the original review. Unqueried clusters are not cached; legacy/native source keys are isolated; TTL expiry and failure retry behavior remain covered by passing tests.
- Closed-candle filtering, millisecond/grid validation, duplicate conflict checks, numeric/OHLCV constraints, minimum 205-row history, continuity, freshness, and source/authenticity checks remain active. Existing tests and the earlier independent numeric/timestamp probes cover these paths. Invalid recovery rows cannot be promoted by this remediation.
- Provider-published zero-volume intervals remain explicitly distinguishable from local synthetic fills. Accepted provider records do not establish that a trade occurred. Local synthetic rows remain blocked before scanner indicators, BTC gating, and confirmations.
- `SHADOW_MODE=true` guards, RSI, ATR, liquidity, risk, spread, imbalance, confirmation and capacity rules, main entry point, indicator/risk engines, dependencies, workflows, Telegram delivery, and execution paths are unchanged by the remediation. Across the full PR, the only scanner edit is five diagnostic lines; the substantive production scope is the market-data reader and bounded backfill changes.

## Files and limitations

Full PR files examined in the original review and retained in this decision: `market_data.py`, `candle_backfill.py`, `scanner.py`; five changed test/preflight files (`test_backfill_cache_expiry.py`, `test_candle_backfill.py`, `test_native_paribu_candles.py`, `test_paribu.py`, `test_recorded_market_cases.py`); two engineering reports; `audit_evidence/market_cases_20261017.json`; the native source contract, live-validation summary, legacy comparison, and 18 native response captures. I read the complete two-file remediation delta and updated parser/tests in this re-review. Relevant unchanged guards, entry point, indicator/risk code, and tests were read during the original pass. The full suite provides broader regression evidence; this was not a complete line-by-line audit of every unchanged file or dependency.

No public endpoint/web-client re-download or production acceptance run occurred during this review. The supplied market captures and historical diagnostic metadata were replayed and checked offline, not independently authenticated as capture provenance. Tests do not prove ongoing source availability, profitability, production deployment, or alert delivery.

The shared pre-existing transport follows redirects, buffers responses before row-count validation, inherits requests environment behavior in production, and uses connect/read timeouts rather than a strict whole-response deadline. These remain pre-existing limitations, not new findings introduced by this PR. Generic float precision remains an existing representation choice; the new false-zero classification case is now rejected. Stricter schema validation can interrupt reads on future benign provider schema changes; this is the intended fail-closed tradeoff for the observed contract.

Approval applies only to the exact head and reviewed scope above. Keep SHADOW mode enabled, recheck required CI and the expected head before any separately authorized merge, preserve current state/history, and evaluate genuine main-branch runtime evidence after release without duplicating scanners. A different production-code head requires another review of its delta.
