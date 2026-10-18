# PR48 independent review — APPROVE

- Repository/PR: `mstafa0932/spot-scanner`, PR48
- Exact HEAD: `e4a6617099475407169d2d9593ab06e06c22e48a`
- Exact tree: `c08b5ad0626c41e61c4c4f0979624d636db6541f`
- Base: `ec8eb22d55af892717ae830913afca56636c4573` (merged PR46)
- Reviewer: a separate Codex AI agent that did not implement PR48. This is an independent AI review, not a human or external security audit.

No merge-blocking security or correctness finding was identified in this bounded notification-policy review. Approval applies only to the identifiers above. CI was pending when the task was assigned; this report does not claim CI success or authorize bypassing it.

## Findings

Read the complete three-file diff (`scanner.py`, `test_telegram_delivery.py`, `tests/test_near_ready_policy.py`) and relevant unchanged data, trigger, risk, cooldown, watch, persistence, and READY context. Reused the prior PR46 review; did not repeat exhaustive native-candle matrices. No applicable `AGENTS.md` was found.

- Generic EARLY WATCH sending now returns false regardless of the legacy flag. Internal tracking remains intact. The NEAR READY flag defaults to the existing legacy opt-in, with explicit new-flag values taking precedence.
- The scheduled NEAR READY path follows existing authenticity/source/discovery checks, reruns the unchanged trigger against a freshly fetched book, requires exactly one missing closed-candle confirmation, checks both subsequent persistence gates, and requires the existing risk plan. Freshness is checked before and after the book request. No RSI, ATR, liquidity, score, spread, imbalance, risk, capacity, confirmation, or SHADOW gate was relaxed.
- READY processing has priority. Near notices do not add confirmations, register signals, or change READY delivery/cooldown keys. The shallow-copy call to the global-limit helper is safe here because that helper replaces its daily list rather than mutating the shared list.
- At most one optional near candidate is attempted per run. A separate global 15-minute attempt timer and per-watch attempt marker are checkpointed before POST. Restart/ambiguous-send suppression does not depend on saving a successful-send marker afterward. Malformed persisted attempt counters fail closed without replacing the original state; future counters suppress optional sending.
- The new tests use explicitly generated offline fixtures. Their Paribu/authentic metadata exercises control flow; it is not evidence of historical prices or real market qualification. Production source/authenticity handling was unchanged.

## Commands and results

Working directory: `/workspace/scratch/a5bccd2d0857/near-ready-policy`.

- `git rev-parse HEAD HEAD^{tree}` matched the pinned identifiers before and after review.
- `git diff --stat` and complete `git diff --no-ext-diff --unified=30/25` between the pinned base and HEAD: exactly three files, 236 insertions and 18 deletions.
- `git diff --check ec8eb22d55af892717ae830913afca56636c4573 e4a6617099475407169d2d9593ab06e06c22e48a` passed.
- **12 independent focused probes passed:** four legacy/new flag combinations; three malformed-counter loads preserving file bytes; future-counter restart suppression; simulated atomic-replace failure preventing POST and preserving original state followed by safe retry; READY priority after collecting a higher-ranked near candidate; and separate weak average-imbalance/volume rejection cases with exactly one confirmation missing.
- **349 tests passed in 13.74s**, Python **3.12.14**. Full repository suite run once, without a directory filter. No CI rerun. **Zero attempted network operations** under the blocking audit hook.

The full-suite invocation ran after the focused probes in the same clean process. Its environment and runner were:

```bash
env -i PATH=/usr/bin:/bin LANG=C.UTF-8 SHADOW_MODE=true \
  TELEGRAM_READY_ALERTS=false TELEGRAM_EARLY_WATCH_ALERTS=false \
  TELEGRAM_NEAR_READY_ALERTS=false PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /workspace/scratch/a5bccd2d0857/test-venv/bin/python -B - <<'PY'
import os, sys, tempfile
from pathlib import Path
blocked = []
def audit(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo',
                 'socket.gethostbyname', 'socket.sendto', 'socket.sendmsg'}:
        blocked.append(event)
        raise OSError('independent review: network disabled')
sys.addaudithook(audit)
with tempfile.TemporaryDirectory(prefix='pr48-independent-',
                                dir='/workspace/scratch/a5bccd2d0857') as task_tmp:
    os.environ['SCANNER_STATE_FILE'] = str(Path(task_tmp)/'isolated-scanner-state.json')
    import pytest
    result = pytest.main(['-q', '-p', 'no:cacheprovider', '--basetemp',
                          str(Path(task_tmp)/'pytest')])
    print('Blocked network events:', len(blocked), flush=True)
    raise SystemExit(result)
PY
```

## Limits and integrity

Optional near delivery can be missed after checkpointing and before POST; this is the stated at-most-once tradeoff. Review covers the local atomic-file/process-restart boundary, not power-loss durability, concurrent writers, or remote workflow persistence guarantees. It does not establish market profitability or production readiness.

No production scanner/main invocation, live state mutation, real Telegram/order request, GitHub write, or merge occurred. All execution used isolated temporary state and a credential-free environment. Tracked source remained identical to the pinned HEAD (`git diff --quiet` passed). Pre-existing untracked bytecode-cache directories were left untouched. Only this report was added outside the repository.
