# Early-detection shadow research — 29 Oct to 5 Nov 2026

**Status: experimental branch only. Unmerged. No live deployment, no trades, no Telegram.**

Owner goal: diagnose pre-pump blindness separately from entry eligibility in
Paribu spot, hand a falsifiable report and potential safe change to the agent.
This is **not** a claim to predict pumps or an approved READY strategy.

## Confirmed investigation

- Main scanner selects up to 80 order books and 60 technical evaluations, with
  first-failing-gate logging. Its experimental `accumulation_radar` only sees
  candles after spread and imbalance checks, so excluded coins cannot be
  investigated by that detector at those moments.
- On 29 Oct, HYPE_TL had 12:42 TRT low volume ratio (~0.53), 13:03
  ATR 0.190%, 13:43 ATR 0.168% and low volume ratio, 13:53 book
  imbalance ~0.529, then by 14:13 score 88 and RSI ~91.84.
  This demonstrates changing gates, not a verified missed safe fill.
- REEF_TL repeatedly had spreads far above 0.40% and T_TL failed
  book imbalance or the READY score threshold.
- SFA_TL repeatedly had spread over 0.40% before an observed
  later RSI/anti-chase rejection. Such price excursions alone do not
  show fills were possible or that any relaxed strategy would profit.
- Prior BONK, BEAM, MASK, XCN, ALT and other documented cases have
  separate time-specific rejected stages; combine only event-time
  comparisons, not screenshots with later price information.

## Experimental implementation (offline only)

`research_early_detector.py` assesses independent **observation-only** 
price/volume conditions, without using live orderbook/RSI/ATR/READY thresholds
as *prerequisites for observing*. It still checks authentic fully closed 15m
candles, continuity, freshness, volume persistence, and avoids chasing >3.2%
recent moves. All parameters are unvalidated research hypotheses.

- Input: JSON `{"observations":[{"symbol":"PAIR_TL",
  "observed_at":<epoch_seconds>,"candles":[23 authentic Paribu 15m OHLCV rows],
  "future_prices":[{"timestamp":<epoch_seconds>,"last":<price>}]}]}`.
  Each OHLCV row requires `authentic=true` and UNIX seconds at candle **open**.
- Run offline: `python research_early_detector.py path/to/input.json --output path/to/report.json`.
  This does not fetch market data. The branch does **not** run a parallel
  continuous exchange watcher.
- Decision: `early_volume_watch` or a specific rejection. Neither is
  READY, a trade recommendation, nor an executed paper order.
- Optional future 1h and 4h last-price observations are marked
  `marks_not_fills` (missing stays null); no entry, stop, profit, cost-adjusted
  P&L, orderbook execution, or predictive win-rate claims follow.
- The script retains no-rally controls and deduplicates same-symbol/same-bar
  evaluations; it never uses future prices to choose a candidate.

## Valid seven-day evaluation protocol

Period: 29 Oct through 5 Nov 2026, Türkiye time. Each observed coin/screenshot
must be matched to **pre-move** scanner time, last fully closed authentic 15m,
1h and 4h bars, first rejection gate, spread, turnover, imbalance and BTC regime.
Label missing historical book, uncertain screenshot time and capacity skips
as unknown rather than simulated READY.

1. Build independent event-time datasets including *all* observed candidates
   plus random non-rally controls with identical coverage, avoiding overlap
   bias from repeated 10-minute checks.
2. Replay early-detection hypotheses on an offline dataset; mark later OHLC
   moves and adverse excursions separately. For a plausible paper entry,
   add separately verified bid/ask, fee, depth, slippage, fill and stop/target
   ordering scenarios. Report infeasible fills explicitly.
3. Compare original shadow READY/NEAR READY outcomes against independent
   watch leads. Compute opportunities with lead time, missed eligible
   instances, false watches, drawdowns, missing data and sampling bias.
4. Apply any proposed candidate logic to an **unseen chronological holdout**;
   do not use that holdout to choose thresholds.
5. Deliver a detailed written agent handoff on 5 Nov: source references,
   per-coin timeline, totals and denominators, wins/losses and controls,
   execution-adjusted uncertainty, root cause confidence, smallest safe
   proposed patch and its exact acceptance tests.

**No evidence means "not established", not "passed".** If no complete
replayable snapshots are available, the report must say the simulation was
not executed and must not invent trades, percentages or results.

## Release constraints

- main, workflows, secrets, Paribu endpoint budgets and production state
  must remain unchanged. Keep `SHADOW_MODE=true`.
- Never relax liquidity, spread, RSI, EMA, ATR, MACD, BTC, score, anti-FOMO,
  risk, or confirmation gates merely to make alerts.
- New code must pass tests and independent review, owner must explicitly
  authorize merge/deployment. Draft PR is for inspection only.
- User-supplied screenshots are case evidence, not validated trade fills,
  and are not copied into the public repository.
