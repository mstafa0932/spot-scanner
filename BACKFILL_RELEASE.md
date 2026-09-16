# Paribu REST candle backfill

This release changes candle acquisition only. Entry strategy, volatility filter,
paper execution and circuit-breaker strategy are unchanged.

Closed candles must lie on the UTC epoch interval grid. Conflicting duplicate
bars and fractional timestamps are rejected; identical duplicates collapse.
Internal gaps and missing recent closed bars (60-second publication grace) are
requested again from the same Paribu chart/history REST endpoint, with at most
four additional requests per symbol/timeframe. Recovery stops early on success.
Backfill requests disable transport-level retries so the four-attempt budget is
real. Normal initial fetch retains its existing transport retry policy.

Only validated, actually returned missing rows are merged. No interpolation,
timestamp rounding, price invention, overwriting existing bars, or other exchange
is used. More than 100 missing bars rejects immediately to bound recovery work.
Empty initial data and off-grid data also reject without inventing an anchor.

Logs emit CANDLE_BACKFILL_START, CANDLE_BACKFILL_ATTEMPT (success, partial,
no_recovery, or error), and CANDLE_BACKFILL_RESULT. scanner_state.json contains
candle_gap_history: remaining timestamps, last observation and report per series.
First observation is explicitly unknown relative to past runs; subsequent runs
classify gaps as new, continuing, mixed or clear relative to persisted evidence.
An unresolved BTC series continues to fail the existing BTC gate, blocking new
entry alerts. Missing bars are not proof of exchange outage or absence of trades.

Tests include recovery on the fourth attempt, persistent gaps, connection errors,
partial input integrity, UTC alignment, duplicate conflicts, JSON history across
runs, retry bounds and BTC rejection. Live recovery success is a separate gate:
the endpoint may still not supply the requested bars, in which case entry stays
blocked. Existing tracking continues to run using its existing data safeguards.
