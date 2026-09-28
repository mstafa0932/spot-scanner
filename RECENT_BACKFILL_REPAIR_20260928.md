# Bounded genuine recovery for large candle gaps

This is a data-recovery repair following PR #23. It does not change indicator lengths, strategy, entry gates, risk, Telegram configuration or SHADOW_MODE.

## Defect

The historical missing-candle guard (`MAX_MISSING=100`) returned before making any recovery request. Observed examples included SKY with 119 missing bars and AXL with 169; subsequent runs also showed WLFI and ARG. `requests=0` was a local early exit, not proof of a network rate limit. This branch also failed before persisting gap history.

## Behavior

The full-history guard remains at 100. Instead of attempting a large historical repair, this branch now examines only the required most recent window (205 bars in production, bounded to at most 500).

- If that window is already genuine, complete and current, it is returned without a request. Broken older history is excluded.
- Otherwise exactly one request uses the existing Paribu REST callback and non-retrying backfill transport, restricted to the recent window.
- Returned timestamps must cover every interval through the expected latest closed candle, using the existing publication grace. An open/future bar cannot replace a missing closed bar.
- Existing and returned rows must have finite, positive valid OHLC prices, nonnegative volume, grid-aligned timestamps and nonconflicting duplicate values.
- Explicit synthetic or invalid provenance and explicit non-Paribu sources are rejected. Untagged rows can only come through the existing Paribu reader contract.
- Canonicalization runs only after complete timestamp coverage is established; it has no gap to interpolate. Returned rows are all genuine.
- Empty, stale, partial, invalid, conflicting or failed responses keep the market rejected. Missing candles remain an unverified cause; they are not relabeled as no trading.

The attempt and its result are logged. A bounded recent-window gap record is saved even on failure, with `missing_timestamps_scope=recent_probe_window` to avoid pretending that an entire unbounded historical outage was enumerated. `missing_before`/`missing_after` refer to the original historical range, while `returned_window_missing` refers to the tested recent window; null means the window could not be validated.

Recovery success uses `status=authentic_recent_window`. Failure retains `gap_limit_exceeded` and records the actual single request plus its error. A successful subset window is not a claim that every old gap was recovered.

## Validation

Sixteen new regression cases cover successful real recovery, empty/partial/stale responses, synthetic and string provenance flags, malformed OHLC, nonfinite values, off-grid timestamps, conflicting duplicates, transport failure, an enormous old gap with a valid recent window, source/price preservation through market_data, stale contiguous history, unfinished bars, and an explicit foreign source.

All 214 tests pass locally. The existing backfill transport still has zero internal retries. The large-gap failure test now asserts one request of exactly the 205-bar range instead of allowing any unbounded history request.

This repair cannot recover trades or candles that Paribu never provides. It removes an avoidable early exit and preserves fail-closed acceptance. Local/CI success does not establish trading profitability; production verification is recorded separately in the PR and user report.
