# Independent additional review of spot-scanner PR47

**Verdict: APPROVE for exact head `0549a7c05c9a6187f178627c507447d7b9be6071`.** No blocking security, lifecycle-integrity, or regression finding was identified in the reviewed change.

Completed: 2026-10-18T08:52:23Z. This was a separate delegated AI review; I did not implement the PR. It is neither a human review nor an external Codex Security review. The coordinating agent verified a concurrent GitHub merge of PR47 at 08:49:28Z under the connected mstafa0932 account, whose executor was not established. The merge occurred while this review was in progress, so this report is an additional post-merge review, not evidence of an independent approval before that merge. No GitHub approval, comment, message, order, merge, or operational scan was performed by this reviewer.

## Exact scope and integrity

- Repository: `mstafa0932/spot-scanner`; detached read-only review worktree: `/workspace/scratch/a5bccd2d0857/zero-volume-review`.
- Reviewed base: `7875e4e0407554f30b1f2a7382fc7cb7704efd6d`.
- Regression-test commit / pre-fix tracker: `0df69169a23087e806289c10cc47467e5ae29e05`.
- Reviewed fixed head: `0549a7c05c9a6187f178627c507447d7b9be6071`.
- Exact base-to-head diff changes only `signal_tracker.py` and `test_signal_tracker.py`: 33 insertions, 4 deletions. The final fix commit itself changes only `signal_tracker.py`.
- No applicable `AGENTS.md` was found in the worktree or its ancestor directories. `git diff --check` passed. The detached worktree was clean both before and after review, and HEAD remained the pinned SHA. No fetch, checkout, branch, implementation, fixture, or operational-state mutation was performed.

## Reasoning and findings

The prior native-data reader distinguishes exchange-published zero-trade records from local synthesized rows. `market_data.py` retains actual native OHLCV, rejects invalid native provenance/values and nonzero-volume underflow, and sets `exchange_zero_volume` from authentic provenance plus zero volume. A provider-authentic record therefore does not establish that its carried price traded in that interval. The new lifecycle gate uses actual finite positive volume, rather than trusting the informational `exchange_zero_volume` flag.

The moved gate at `signal_tracker.py:156` now precedes every candle-driven state transition: pending-entry penetration/fill, fill-candle stop, OPEN/TP1 stop and targets, and breakeven arming. Authenticity still precedes the volume check, so the existing rejection of local synthetic/unverified data remains intact. For positive-volume candles, the lifecycle branches and conservative stop-before-upside/fill-bar treatment are unchanged.

Pending-entry expiry still runs before authenticity, volume, prices, or fill checks. A candle ending at the deadline remains eligible for replay; a straddling candle remains ambiguous; a candle starting at/after the deadline cannot produce a fill. Final expiry also remains effective with only zero-volume, empty, or unavailable observations.

Skipped rows do not advance `last_processed_candle`; their reason and `tracking_incomplete` persist. A later genuine positive-volume row is still evaluated and advances the watermark. Repeated scans and JSON round trips did not duplicate price or expiry events in the cases exercised. A skipped row can be recovered if corrected before the watermark advances; after a later accepted row advances it, older corrections are not replayed. That is the existing watermark policy, and the retained incomplete flag prevents such a history from becoming a clean research sample.

Fresh order-book bid observation is a separate, unchanged path. It can still produce a paper STOP/target after a candle is skipped; the event then uses the bid and current observation time, not carried OHLC. Independent tests verified this distinction. Shadow events remain marked estimated, never exchange-confirmed, and `deliver_pending_events` continues to block shadow lifecycle notifications. The diff adds no order-execution, network, credential, or messaging capability. Existing `main`/`run_scanner` shadow guards and alert opt-ins were inspected, and corresponding repository tests passed.

## Independent reproduction and validation

All test invocations used the shared test interpreter with a clean `env -i`, no service credentials, `SHADOW_MODE=true`, both Telegram alert flags false, bytecode/cache writing disabled, plugin autoload disabled, and test-only state/archive paths outside the worktree. The test suite invokes scanner functions only through its mocked fixtures; no operational `scanner.py` or `main.py` entrypoint was run with real configuration.

Network namespaces are unavailable in this container (`unshare --net` returned `Operation not permitted`). Instead, the external review runner loaded an inherited libseccomp filter denying socket creation and network syscalls, plus io_uring setup/entry/registration. Before importing repository tests it independently verified that IPv4, IPv6, and UNIX socket creation return `EPERM`. The restriction was active for every reported test run and inherited by any child process.

| Check | Result |
| --- | --- |
| Full repository suite at pinned head | **322 passed in 10.46s** |
| New committed regression against pinned pre-fix tracker, loaded in memory from Git | **Expected failure: 1 failed in 0.09s**; authentic carried `97` with volume `0` generated `STOP` at candle close `2800`, price `98` |
| Same committed regression at fixed head | **1 passed in 0.85s** |
| Separate independent lifecycle and hostile-input cases | **171 passed in 2.38s** |
| Exact-head, clean-worktree and diff-whitespace checks after tests | Passed |

The 171 additional cases cover ordinary and shadow OPEN/TP1, pending entry, zero/negative/missing/NaN/infinite/non-numeric/nullable/boolean volume, flat and adverse OHLC, synthetic provenance with positive volume, already-armed breakeven, positive-volume stops/targets/breakeven, pending fill with conservative stop, repeat/restart/cursor behavior, later real events after skipped rows, exact and straddling expiry, active expiry reference prices, independent fresh-bid events, shadow non-delivery, and actual recorded native BTC zero-trade records passed through the real native fetch/parser path with HTTP mocked. The repository's 322-test suite additionally covers native-data provenance, other symbols/timeframes, touch-only fill rejection, unavailable data, and delivery safeguards.

Local versions: Python 3.12.14, pandas 2.2.3, requests 2.34.2, pytest 8.4.2. Python 3.10 was not independently executed here. The caller's reported CI success was not substituted for local validation.

## Reproduction commands and review-only files

The review-only runner and extra tests are outside the repository:

- `/workspace/scratch/a5bccd2d0857/pr47_review_checks/deny_network.py`
- `/workspace/scratch/a5bccd2d0857/pr47_review_checks/test_independent_pr47.py`

```bash
cd /workspace/scratch/a5bccd2d0857/zero-volume-review
git rev-parse HEAD
git diff 7875e4e0407554f30b1f2a7382fc7cb7704efd6d 0549a7c05c9a6187f178627c507447d7b9be6071 -- signal_tracker.py test_signal_tracker.py
git diff --check 7875e4e0407554f30b1f2a7382fc7cb7704efd6d 0549a7c05c9a6187f178627c507447d7b9be6071
git status --porcelain=v1 --untracked-files=all

review_run=(env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONHASHSEED=0 SHADOW_MODE=true TELEGRAM_READY_ALERTS=false TELEGRAM_EARLY_WATCH_ALERTS=false SCANNER_STATE_FILE=/workspace/scratch/a5bccd2d0857/pr47_review_checks/isolated_state.json NEAR_MISS_ARCHIVE_FILE=/workspace/scratch/a5bccd2d0857/pr47_review_checks/isolated_archive.jsonl /workspace/scratch/a5bccd2d0857/test-venv/bin/python /workspace/scratch/a5bccd2d0857/pr47_review_checks/deny_network.py)

"${review_run[@]}" . -q -p no:cacheprovider --basetemp=/workspace/scratch/a5bccd2d0857/pr47_review_checks/full-pytest-tmp
"${review_run[@]}" --tracker-ref 0df69169a23087e806289c10cc47467e5ae29e05 test_signal_tracker.py::test_open_signal_ignores_published_zero_volume_candle -q -p no:cacheprovider --basetemp=/workspace/scratch/a5bccd2d0857/pr47_review_checks/pre-fix-tmp
"${review_run[@]}" test_signal_tracker.py::test_open_signal_ignores_published_zero_volume_candle -q -p no:cacheprovider --basetemp=/workspace/scratch/a5bccd2d0857/pr47_review_checks/head-regression-tmp
"${review_run[@]}" /workspace/scratch/a5bccd2d0857/pr47_review_checks/test_independent_pr47.py -q -p no:cacheprovider --basetemp=/workspace/scratch/a5bccd2d0857/pr47_review_checks/independent-tmp
```

## Scope limits and nonblocking observations

The change prevents future candle-derived misclassification; it does not remove or reclassify previously recorded terminal events, change simulation version/cohort, or migrate historical state. Any historical sample contaminated before this fix still needs separate retrospective treatment before being relied on.

Routing authentic zero-trade records through `_skip_candle` also marks those signals incomplete and excludes them from the report's complete execution sample. This is conservative and visible, but can lower sample completeness on quiet markets even though the provider record itself is authentic. That reporting policy is not evidence of a new traded price event and is not a blocker for this fix.

This review establishes behavior for the exact pinned source and exercised offline cases. It does not attest current production state, freshness/quality of an actual live order book, execution or profitability, or unrelated pre-existing code paths. No live endpoint was queried.
