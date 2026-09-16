# Engineering review — 2026-09-16

## Scope and evidence

No claim of a universal/best/profitable bot. No automatic orders, no expansion
into derivatives, and no reduction of entry thresholds. The shadow radar stays
research-only. Software tests are not market-performance evidence.

Primary references consulted (not copied strategies):

* Freqtrade: https://www.freqtrade.io/en/stable/lookahead-analysis/ — future
  candles can invalidate backtests. Added a prefix-invariance test for the
  current indicator calculations. This is not a full Freqtrade backtest.
* VeighNa (Chinese): https://www.vnpy.com/docs/cn/community/app/cta_backtester.html
  — explicit slippage/commission inputs matter. Costs are unknown by default;
  no zero-fee profitability assumption.
* QuantConnect: https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/key-concepts
  — execution and displayed prices differ. Added optional per-side hypothetical
  costs, not a fill simulator or order-size-dependent market impact model.
* bitFlyer (Japanese): https://lightning.bitflyer.com/docs?lang=ja — distinguishes
  public book, executions, exchange status, realtime APIs and rate limits.
  These capabilities do NOT imply identical Paribu endpoints.
* Upbit (Korean): https://docs.upbit.com/kr/reference/websocket-guide — connection
  keepalive/reconnection needs explicit design for streaming feeds. No Upbit
  feed substituted for Paribu; no new streaming deployment in this change.
* GitHub: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
  — scheduled runs can be delayed/dropped. Existing cadence is not real time.
* Nansen: https://docs.nansen.ai/api/smart-money — onchain netflows and DEX
  trades are different datasets. No integration without matching contracts,
  chain coverage, access permissions and an approved cost budget.

## Corrections implemented

1. Existing malformed/unreadable state now stops the run without resetting
   history. State-write and snapshot failures cause a failed run rather than
   a misleading successful job. First use with no state remains supported.
2. Target/stop tracking uses only CLOSED 15m bars wholly inside the signal's
   lifetime. No post-expiry quote can become a target hit. Bars straddling entry
   or expiry are excluded because intrabar order is unknowable. This can miss
   real touches; it avoids assigning out-of-lifetime moves to the signal.
3. A processed-bar cursor avoids replaying complete historical bars. In a bar
   touching both stop and target, STOP still wins conservatively. Observation
   time for a bar is its close, not a claim of exact execution time.
4. New lifecycle events are pending until Telegram succeeds and retry on later
   runs, including terminal signals. Legacy events without a delivery flag are
   not replayed. Pending observations are saved before sending; delivery marks
   are saved after sending. A crash between send and persistence can duplicate
   a message: this is NOT exactly-once delivery. IDs expose duplicates. Pending
   events share the existing 14-day signal retention window.
5. New entry records include BTC/book/volume evidence and explicitly record
   that a real fill has not been confirmed. No historical fills are invented.
6. `research_summary` in scanner state reports capacity gaps, pending lifecycle
   delivery, retained radar samples and missing/invalid horizons. It never
   promotes a strategy or declares profitability. CLI:

   `python research_report.py --state scanner_state.json`

   Optional scenario (illustrative inputs, NOT a statement of Paribu fees):

   `python research_report.py --fee-pct 0.2 --slippage-pct 0.1`

   Fees/slippage are applied to both sides. Last-price outcomes are not
   executable bids. Missing horizons are neither wins nor losses. No Sharpe,
   win rate, or actual PnL is claimed from these observations.

## Still needed before entry-strategy changes

* Full-size data-quality/coverage audit: why some Paribu candles have gaps,
  whether gaps are inactive intervals or transport failures. Never fabricate
  traded volume to fill holes.
* Reliable always-on data capture if faster detection is needed. Requires a
  hosting choice, supported Paribu feeds and rate-limit verification; no paid
  infrastructure provisioned. Current scheduled scanner remains in use.
* Independent evaluation/holdout data and a volume-only baseline. Reconstruct
  longer history from state commits or export it: radar retention is bounded.
* Real account fee tier, intended order sizes and executable liquidity before
  net-profit simulation; no account secrets required for this research release.
* External whale/news feed only after coverage/cost checks; no inference of a
  trader's intent from deposits, resting orders, or a wallet label alone.

## Deployment and rollback

CI runs offline tests on Python 3.10 and 3.12 before merge. New writes are to the
existing scanner state only. Review the first production run's job outcome,
diagnostics and research summary after merge. Revert the code commit if needed;
do not restore an old state file and lose alerts. Existing completed signals
are not re-scored by these changes.
