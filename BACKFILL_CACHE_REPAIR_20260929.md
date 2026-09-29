# Bounded recovery-cache repair

PR #25 correctly persists failures and prevents repeated immediate recovery of unchanged gaps. Its negative cache, however, has no expiry and treats any attempted request with zero recovered rows as evidence that the data is unavailable. A transient timeout, 429, or invalid provenance can therefore suppress later healthy recovery while the missing timestamps remain unchanged. Refreshing observed_at on every skipped pulse also makes that timestamp unsuitable as a cache clock.

## Changes

- Cache only a validated response that adds no missing candle. Never cache transport, rate-limit, schema, or provenance errors as evidence of missing source data.
- Expire that evidence 900 seconds after the actual attempt. A cache hit preserves the original attempt and expiry. This is an operational request cooldown, not a trading parameter.
- Revalidate legacy entries without an explicit expiry once. Preserve the existing one-request large-gap and four-request ordinary repair bounds.
- Keep PR #25's process-level backfill block after HTTP 429.
- Validate initial rows before either suffix acceptance or canonicalization. Explicit synthetic/foreign provenance and nonfinite OHLCV cannot be promoted to authentic data. Genuine untagged initial exchange rows retain the existing Paribu reader contract.
- Reject conflicting duplicates in recovered rows before deduplication. Persist the failed recovery through the existing failure path.

No RSI, score, volume, spread, imbalance, indicator length, order-book capacity, signal lifecycle, Telegram or Shadow setting is changed.

## Evidence

All 13 new regression cases failed against the deployed PR #25 code before this patch. The full suite then passed 232 tests. The cases cover both recovery paths, cache expiry after a hit and JSON restart, transient timeout/429/provenance recovery, legacy cache migration, invalid initial rows and conflicting recovery duplicates.

Source data can still be incomplete. A successful repair of caching is not proof that Paribu supplies the missing candles, that a READY should exist, or that the strategy is profitable. Live verification must distinguish restored request eligibility from actual candle recovery.
