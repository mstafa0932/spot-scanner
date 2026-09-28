# Hot Radar scheduling repair and recorded-decision review

Review date: 2026-09-28. Base: `9c760f5b63a0dfe184cbcb8239294aab96d24a5d`.
The source code is unchanged from the earlier snapshot `069579c57c76933e2ed4095a2f51fb0ced3d9647`; intervening commits changed state/archive only.

## Implemented repair

PR #22 already introduced a cheap ranking layer using the existing Paribu ticker request. This patch repairs that scheduler; it does not add another scanner.

Three defects were reproduced before editing:

1. Unbounded hot priority could alternate between hot groups indefinitely, starving other markets. In an eight-market/two-slot test, only four markets were attempted after ten cycles.
2. Missing, old, future and equal-time snapshots still generated priority boosts.
3. A pending 1/2 confirmation had no follow-up priority and could lose both book and technical capacity to other markets.

The corrected scheduler reserves at least half of each existing budget for least-recently-attempted coverage. At the production 80-book cap, at most 40 slots receive priority, including at most 20 pending-confirmation follow-ups. Remaining priority slots serve hot markets. Unused priority slots return to normal coverage; all slots can still be used. Priority carries through the separate 60-market technical budget, again reserving at least half for fair coverage.

Pending follow-ups require an existing 1/2 lifecycle, unexpired observations and the possibility of a newer closed 15m candle. Priority itself never supplies confirmation or bypasses data, book, strategy, BTC, cooldown or execution checks. Previously inspected hot symbols may be revisited when fresh motion is observed; quotas prevent monopolization.

Only snapshots separated by a positive interval no greater than the existing cadence-late tolerance are compared. Universe exclusions and the existing minimum TL volume still apply. No additional API requests were introduced by the priority calculation. Existing pre-alert refreshes are unchanged.

The ticker supplies a rolling 24h volume, not executed volume over the last ten minutes. Its difference is explicitly labeled as a ranking proxy. Falling rolling volume does not prove an absence of current trading. The heat score is not a buy recommendation, measured acceleration, or evidence of profitability.

Decision records now retain ATR percentage, MACD histogram and closed-candle identity alongside score, RSI and volume ratio for future forensic analysis. Historical missing fields are not reconstructed or invented.

## Evidence retained

`audit_evidence/paribu_decisions_20260928.json` and its `_update.json` supplement contain 111 distinct recorded runs, spanning 26 September 04:42:35 through 28 September 08:47:53, Europe/Istanbul. The supplement overlaps the first file: deduplicate by `run_id`. Every row identifies a pinned source-state commit and the code SHA reported by that run.

These are actual recorded decisions, **not** a counterfactual OHLCV/order-book replay. The set includes irregular historical cadence, missing observations and different code versions. Counts below are descriptive event counts, not independent trading samples, false-negative rates, or performance comparisons.

| Symbol | Capacity | Data | Book | Universe | Discovery/trigger |
|---|---:|---:|---:|---:|---:|
| QNT_TL | 20 | 8 | 75 | 0 | 8 |
| GRT_TL | 39 | 11 | 30 | 31 | 0 |
| W_TL | 53 | 0 | 48 | 5 | 5 |
| AUDIO_TL | 28 | 7 | 76 | 0 | 0 |
| PUMP_TL | 34 | 11 | 55 | 0 | 11 |
| JASMY_TL | 51 | 4 | 43 | 8 | 5 |
| IMX_TL | 19 | 0 | 15 | 77 | 0 |

Examples: QNT had 66 spread rejections; AUDIO 58. PUMP had 54 imbalance rejections. GRT had both insufficient volume and candle failures. A capacity event means unexamined, not rejected or proven profitable.

W reached score 94 and `watching: confirmations 1/2` at 27 September 22:51:08 Turkey time (run 36345834837). It was not evaluated at 22:56 due to capacity, then failed imbalance at 23:06 (0.51227), and later spread at 23:26 (1.12374%). This demonstrates changing gates, not a proven missed winning trade. The 22:56 scan also did not yet have a new closed 15m candle, so merely promoting it would not prove a second independent confirmation.

A complete replay needs point-in-time candle provenance, contemporaneous books, ticker snapshots and availability times for each pulse. Later candles cannot establish what was available earlier, and later books cannot establish earlier executable prices. No profitability or hypothetical fills are inferred from these records.

## Backfill: why requests can be zero

`candle_backfill.repair` checks `expected_count > len(actual) + MAX_MISSING` before the request loop. `MAX_MISSING=100`. Therefore missing counts of 119 or 169 cause `gap_limit_exceeded` with `requests=0` by design. This is a local guard, not evidence of a Paribu 429, timeout or exhausted four-request budget.

That branch precedes the usual suffix recovery and history persistence. It can therefore omit these failures from `candle_gap_history`; the run diagnostics/logs remain the evidence. This patch does not lift the guard or mislabel the missing timestamps as no-trade intervals. A safe follow-on data change would require bounded retrieval of genuine recent rows, validation against the current closed-candle boundary, and tests for stale, sparse, incomplete and synthetic responses.

The available evidence does not establish whether each absent Paribu candle represents no trading or failed retrieval. Absence of a candle, unsuccessful backfill, and a zero rolling-volume delta are insufficient proof of no trading. The current release continues to fail closed; no candles are fabricated or promoted to authentic by this patch.

## Indicator length and confirmation constraints

The shared indicator function computes EMA200 and requires 205 rows for all timeframes. The 1h score uses an uptrend that includes EMA200. The 4h scanner references close/EMA50, so a separate 4h interface is worth studying, but simply reducing rows changes the EMA initialization and can change decisions. The 205-row requirement is unchanged here.

`MIN_CONFIRMATIONS=2` is unchanged, but current strategy code already has an `explosive_now` breakout exception that may allow one observation. Thus it was inaccurate to describe the existing strategy as universally requiring 2/2. This exception was not introduced or widened by the scheduler repair. Observations are deduplicated by closed-candle identity, not merely by scanner invocation.

An EARLY WATCH is distinct from READY. This patch adds no new Telegram message type, no new entry rule and no strategy threshold change. Telegram READY configuration and SHADOW_MODE=true remain unchanged; execution stays manual.

## Validation and limits

The upstream baseline passed 185 tests. Six new cases reproduced scheduling failures before the fix. After repair, 198 tests passed locally, including:

- persistent-heat starvation, stale/missing/future snapshots and pending-confirmation scheduling;
- all 234 markets attempted under fixed 80-slot capacity across repeated cycles and serialized restarts;
- bounded confirmation/hot allocations and unchanged universe exclusions;
- priority carried into technical analysis while the same rejection still prevents a signal;
- existing simulator, authentic-data, risk, Telegram and lifecycle regression tests.

AST comparison confirms every strategy/gate function and all top-level parameters in scanner.py are unchanged. Only priority construction and its use/diagnostics inside run_scanner change.

At the reviewed live run 36383437441 (28 September 08:47:53 Turkey), BTC was `regime_bearish`, data-valid was 5/18 book-approved markets, and zero signals were produced. A scheduler improvement cannot turn those rejections into valid entries. The available execution sample remains insufficient to establish a trading edge. CI and subsequent live verification are recorded in the pull request, separately from these local results.
