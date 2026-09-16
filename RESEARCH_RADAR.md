# Accumulation radar — shadow research only

This addition does **not** change entry thresholds, Bitcoin gates, stops, targets,
alert cooldowns, Telegram formatting, scheduling, or trade execution. It has no
network client, wallet attribution, news integration, or Telegram sender.
There is no supported switch to turn these hypotheses into entry alerts.

## What is measured

After the normal entry-alert path, reuse the already fetched Paribu data. Require
49 consecutive closed 15-minute candles (no gaps/duplicates), no more than 20
minutes stale relative to candle close. The last 8 candles must span 0.3–4%,
with a close in the upper half. The last 3 volumes average at least 1.5 times
the preceding 20-bar mean; each of the 3 must reach that baseline. Absolute
45-minute/3-hour/12-hour moves must stay below 3.2/6/18%.

Require spread <=0.4%, bid/ask notional ratio >=1.2 and >=50,000 TL on each
side of the sampled book. These totals are over the fetched levels, NOT a
guarantee of executable depth near price. The live ask must remain in/near the
range and within 1.5% of the latest close. Two qualifying observations on
different closed candles, spanning >=10 minutes, are needed. A missing or
failed observation, or gap >45 minutes, resets confirmation. Limit to one
record per symbol per 24 hours. All thresholds are experimental, not optimized.

Book imbalance is resting orders, not executed buyer aggression or proof of
whales. No assumption is made about the identity or intent of participants.
BTC context is stored even if bearish; bearish research events are NOT trades.

## Coverage limitations

The unchanged scanner's volume, book-quality, capacity (80 books/60 technical
markets), and data-availability gates still constrain radar coverage. It does
not watch every coin continuously. GitHub scheduling may be delayed; this
release does not claim to improve cadence or capture every sudden move.

## State and diagnostics

`scanner_state.json` retains `accumulation_radar` and the latest 12
`scan_diagnostics` runs. Each evaluated symbol records its last gate/reason;
untouched symbols are explicitly `not_evaluated_capacity`. Per-symbol decisions
also appear in Actions logs. This is the first failing gate, not all failed gates.
Run timing, actual cadence, coverage counts and BTC reason are included.

Radar has at most 200 events retained for seven days and current watches only.
Its exceptions are isolated; original entry state is still saved. Set environment
variable `SHADOW_RADAR_ENABLED=false` to disable research. No credentials needed.
Rollback by reverting the research commit, not by restoring an old state file.

## Evaluation, not profit claims

Events store a reference ask and observe later ticker LAST prices around 1h,
4h and 24h, allowing up to 30 minutes of sampling delay. Actual sample time and
age are recorded. Missed horizons stay missing. Sampled max/min returns are
not intraperiod extrema, not fills, and exclude fees/slippage. Range-floor
invalidation is marked at the first sampled breach, which can miss an earlier
breach. No TP/STOP success is inferred from these observations.

Before considering user alerts, collect at least 50 independent symbol/day
events across at least 14 days as an initial review (not statistical proof),
including failures, coverage gaps, BTC regimes and tokens that rallied without
a radar event. Compare lead time, false alerts and drawdown against a volume-only
baseline. Keep a separate later holdout period. Simulate fees, spread and
slippage conservatively before any entry-strategy proposal. No auto-tuning.

Tests are deterministic/offline; they validate software behavior, not market
profitability. Existing entry decisions and protection settings remain intact.
