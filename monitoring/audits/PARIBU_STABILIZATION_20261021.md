# Paribu stabilization audit — 21 October 2026

Status: preserved engineering evidence for calibration/stability work. This report does not authorize a strategy, production-setting, merge, or deployment change.

## Scope and result

- 143 completed production scanner cycles were reviewed through the saved audit window.
- Capacity selection was reconstructed identically in 143/143 cycles.
- All 66 markets deferred as `coverage:not_evaluated_capacity` in run 39572138807 received order-book evaluation in the next run, 39573417314; start-to-start gap was 605 seconds.
- `MAX_ORDERBOOK_MARKETS=80` and `MAX_TECHNICAL_MARKETS=60` were unchanged. The maximum observed technical evaluation count was 49.
- No permanent market starvation was established. A per-cycle capacity exclusion is not, by itself, a software defect.
- 110 technical evaluations for the six focused cases matched authentic closed Paribu candles.
- 858 RSI/ATR comparisons matched without using future candles.
- Five reconstructed technical-build windows for not-evaluated markets are not READY evidence: they had no recent confirmations and no preserved contemporaneous order book proving the remaining gates.

## Focused cases and evidence limits

The six-case review separates historical technical reconstruction from recommendation eligibility. Reconstructed technical indicators alone cannot prove that spread, imbalance, liquidity, BTC regime, confirmations, risk/reward, or delivery gates would have passed at decision time.

No new Paribu snapshot download or full-day re-analysis is required unless materially new evidence appears.

## ONDO_TL manual order evidence

User image `1001126046` records a manual Paribu limit order at 22.93 TL and a queue-log time of 16:14:58 local time. It is evidence that the user placed an order; it is not independent exchange evidence of average fill price, filled quantity, or the complete execution timeline.

The shadow model excluded the partially open issue candle and required `low < entry <= high` on later closed candles. Later complete candles were below the 22.94 reference, so the model recorded `ENTRY_EXPIRED`. That result must not be used to deny the user's manual purchase, and the manual order must not be promoted to a model-confirmed fill. This is a measurement-model limitation, not a basis for changing fill rules from one case.

## Confirmed audit-label defect and PR #49

- Defect: the existing explosive-breakout confirmation path could send READY with one confirmation while persisting `confirmation_2_of_2`. ONDO run 39605507651 exhibited the mismatch.
- Repair branch: `fix/confirmation-lifecycle-audit-20261022`.
- PR: #49.
- Reviewed head: `02f7fc57b0d121cebcdc71ca80faea72a9286e18`.
- Scope: audit labeling only. The existing explosive rule, thresholds, capacity, risk controls, targets, stops, and delivery policy are unchanged.
- Regression: records `confirmation_explosive` with the actual count/mode, preserves expiry and persistence/reload behavior, and verifies READY does not repeat.
- CI run 39678435163 passed 352 tests on Python 3.10 and 352 tests on Python 3.12.
- Security evidence supplied with the audit reports no new Bandit findings; two pre-existing LOW findings remain documented.
- PR #49 remains open and unmerged. No independent final review or explicit user merge approval has been recorded.

## Current constraints

`SHADOW_MODE=True`; no live trading, account orders, test Telegram messages, strategy/threshold/capacity changes, merge, or deployment. A final independent review and explicit user approval are required before any merge.
