# P0 shadow simulator and coverage repair

This branch is a separately versioned research change. Keep the deployed baseline
and its data unchanged through 2026-09-22 15:59 Europe/Istanbul. The workflow still
sets `SHADOW_MODE=true`; a push to this feature branch runs tests, not the scanner.
Manual workflow dispatch on a feature branch also runs tests only. Both scanner
entry points and the public `run_scanner()` function reject non-Shadow mode.
No strategy threshold or indicator implementation is changed.

## Execution semantics

- A closed 15-minute bar must be entirely after order creation and within the
  entry's 90-minute lifetime to estimate a fill. Its interval is half-open:
  `[bar_open, bar_close)`. Closing exactly at the deadline is eligible; opening
  at the deadline is not. A bar straddling the deadline is ambiguous and skipped.
- Expiry is checked before prices, provenance, and fill logic for each bar.
  A delayed poll may replay a fully pre-deadline bar: expiring all orders based
  only on the poll's wall clock would incorrectly discard that evidence.
- Only an explicit boolean `is_authentic=True` is eligible for candle-driven
  fills, TP, STOP, or breakeven. Missing provenance is not treated as genuine.
  Skipped rows and unavailable data mark the observation as incomplete.
- A pending buy requires `low < entry <= high` and known positive candle volume.
  Total candle volume never stands in for volume at/below entry. Touch-only bars
  and gaps entirely below entry do not establish a fill in this model.
- OHLC penetration is only an estimate: `fill_estimated=True`,
  `fill_confirmed=False`, `simulation_version=ohlc_penetration_v2`.
  The evidence records the bar interval, prices, volume, and unknown queue/size.
  `filled_at` is a conservative bar-close timestamp, not an exact trade time.
- Pending orders never earn TP/STOP or arm breakeven. On an ambiguous fill bar,
  a stop is counted conservatively; upside targets are not awarded.
- A separate genuine current bid can still support a price observation for an
  already open simulated position. It does not establish an entry fill.
- Legacy records are not rewritten into v2 results. Separate model versions,
  legacy observations, estimated entries, and incomplete observations when
  reporting performance; a status count alone is not a validated trade sample.

## Coverage diagnosis and repair

The 80-book and 60-technical limits are local budgets in `scanner.py`; the
234-market snapshot is not evidence that all markets reached technical analysis.
The fixed descending-volume order could repeatedly exclude the same markets.
There is no evidence in that capacity counter of an exchange-imposed rate limit.

The new scheduler preserves both budgets and serial requests. It rotates
least-recently-attempted markets separately for book and technical work. Failed
attempts also consume their turn. Volume rank remains the tie-breaker; final
candidate ranking and all strategy/book gates are unchanged. State survives
restart, and cheap universe exclusions are evaluated for all markets even after
the book budget is exhausted. Reports distinguish book from technical capacity.

This improves coverage across cycles, not full coverage every cycle. Revisit
latency changes are part of a new research cohort and must not be pooled with
the frozen baseline. No unsupported API throughput or safe concurrency limit
is assumed. Existing HTTP retry and Retry-After handling are unchanged.

## Verification

`python -m pytest -q`: 165 tests passed locally (Python 3.12).

35 new simulator cases cover touches, penetration, deadline boundaries, delayed
polls, synthetic/missing/malformed provenance, nonpositive volume, no targets
before fill, stop-first handling, restart persistence, and unavailable feeds.
The 11 touch/expiry/provenance regression cases were also run against the
original tracker and all failed as expected, before passing on the repair.

10 new coverage cases include budget-bounded rotation across 234 markets,
independent technical fairness, restart persistence, invalid state, scanner
integration, and separate capacity counts. Existing tracker fixtures now declare
the actual authentic/volume input contract instead of omitting provenance.

18 additional measurement/safety cases cover premature Near-Miss checkpoints,
bid-side observations, distinct rejection reasons, strict boolean provenance,
research cohort persistence, exclusion of legacy/incomplete execution samples,
synthetic radar inputs, public-entry-point Shadow enforcement, Shadow notification
isolation, and fair retry rotation after failed quote requests.

## Measurement separation and diagnostics

- Each run, new simulated signal, Near-Miss record, and radar event carries a
  research cohort. Old observations stay in state and are excluded from the new
  execution performance sample. Version transitions are marked incomplete.
- The per-run funnel follows the actual book-before-technicals pipeline. Each
  recorded decision also retains its timestamp in an ordered transition list.
- `signal_recorded` is separate from `alert_sent`: a Shadow record is not a
  delivered Telegram alert. Shadow events never enter the notification backlog.
- Near-Miss checkpoints require reaching the horizon, use sampled bid prices
  for new records, and preserve legacy ask-based records separately. Failed
  quote requests consume a scheduling turn so other symbols are still checked.
- These are sampled quote observations, not exact intrabar MFE/MAE or fills.
  Unknown fees and slippage remain unknown. Cohort/version labels must be kept
  when extracting the baseline and when analyzing the repaired model.

## Post-freeze measurement requirements

- Do not tune RSI, spread, or volume thresholds from this patch's test results.
  Unit tests do not establish market profitability or realistic full fills.
- Preserve baseline run logs/state history before constructing the funnel.
  The actual cheap-book gate precedes technical analysis; a post-mortem must
  reflect that order rather than inventing unobserved downstream decisions.
- Exact MFE/MAE from a rejection timestamp requires complete, timestamped data
  covering that window. A 15-minute bar crossing rejection cannot distinguish
  earlier highs/lows; label such a window incomplete, not exact.
- `MFE - costs` is an ex-post best-excursion scenario, not executable Net Edge
  or expectancy. Net results require a predefined exit, modeled size/fills,
  bid-side exit evidence, commissions, and slippage without double counting.
- OHLC does not provide volume-at-price, queue position, or partial fills. Those
  remain unknown here and must not be invented or promoted to confirmed fills.
- No Challenger is activated by this branch. A changed strategy needs independent
  shadow evidence after measurement quality and sample adequacy are established.
