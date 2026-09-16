# Data quality and execution research release

This release fixes input reliability; it is not evidence of profitability.

- Candle validation rejects nonfinite values, inconsistent array lengths, stale
  closes (more than one interval plus 60 seconds), and gaps in retained history.
  Every unfinished or future bar is excluded. Missing bars are never fabricated.
- Watchlist confirmation requires unique closed 15-minute candle identities.
  Repeated scans replace the same candle observation; legacy observations without
  a candle identity cannot provide confirmation. The existing explosive-breakout
  exception remains unchanged.
- Before notifying, refresh the order book and repeat trigger checks; price the
  opportunity from that refreshed snapshot. This is not a guarantee of freshness
  inside the exchange, queue priority, or execution after notification.
- `execution_research` records a hypothetical 100,000 TRY immediate depth sweep
  for a triggered candidate and persists it with signal evidence. It includes
  both sides of available depth and a same-snapshot roundtrip cost. Unknown or
  insufficient depth stays explicit. This is research only: it does not resize
  trades, alter entry thresholds, or simulate a filled limit order.
- 0.11% per side is a scenario, not a verified account fee schedule. Only one
  supplied buy receipt showed this fee. Actual future sale fees and liquidity
  remain unknown. Buy fee is modeled as additional quote currency cost.

No paid service or order execution was introduced. The accumulation radar stays
in shadow mode. Tests cover data failures, repeated confirmations, and depth
costs alongside previous state, lifecycle, and notification regressions.

## Remaining evidence required

GitHub scheduled workflows can be delayed and do not provide continuous market
coverage. Historical tests and paper signals are not real fills. Validate the
release against live Paribu data, gather out-of-sample observations, and measure
missed signals, drawdown, fees and limit-order fill delay before sizing capital.
There is no implemented wallet attribution, whale-intent inference or news feed.
Passing software tests does not justify committing 100,000 TRY to each alert.
