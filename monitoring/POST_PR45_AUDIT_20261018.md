# Post-PR45 workflow and lifecycle audit —18 October2026

Verified actual main run39157119966 (workflow_dispatch) and post-PR48 push run39157435501. SHADOW_MODE remained true. No trading or test recommendation was sent.

## Lifecycle step skipped intentionally

The main workflow has `if: github.event_name == 'push'` on `Validate signal lifecycle`. The scheduled controller uses workflow_dispatch, so that step is intentionally skipped on routine scans. Scanner and state persistence run on main. Full tests run for code pushes and separately in pull-request CI. No workflow defect is established by the skip; the guard was not changed.

PR48's main push confirms the distinction: Validate signal lifecycle succeeded with349 tests, followed by successful scanner and state persistence.

## Actual native data and decisions

Run39157119966:234 markets;80 books attempted;27 technical/data-valid; zero local synthetic and zero candle errors; BTC neutral/acceptable;595-second cadence gap; state persisted (2,843,963-byte valid JSON). Native source logs explicitly name /v1/chart/ohlc and closed records.

| Case | Actual decision around22:05–22:06 local |
| --- | --- |
| NMR |RSI30.5133 outside45–72 discovery range; native15m/1h/4h complete|
| MINA |Book imbalance0.6938 below0.85; no candle fetch for it on this scan|
| SAND |Spread0.6897% above0.40%; no candle fetch for it on this scan|
| RAD |Spread0.5602% above0.40%; radar price snapshot rose1.198%, which does not override book quality|
| ALT |15m momentum not constructive; score45, volume ratio0.6425; native frames complete|
| BTC |Intentionally excluded as a recommendation asset; original candles independently power a passing BTC regime gate|

Across32 retained persisted summaries after PR46 and before PR48, candle errors/synthetic counts were zero and cadence_status was ok. Latest12 case diagnostics show at most one consecutive capacity skip for MINA/SAND/RAD. A skipped capacity slot is not a market rejection; old technical-attempt timestamps for these cases were explained by failing the preliminary book. We did not infer healthy candles for markets not fetched.

Historical NMR rejection on17October explicitly names synthetic BTC data, a data problem addressed by PR45 rather than a market signal. MINA/RAD old orphan lifecycle records now expire with preserved history under PR46. SAND's13:35Z discovery was rejected by genuine BTC weakness. No retained fully qualified READY lost during the audited current window was established.

## Reviews and safe fixes

Formal GitHub reviews on PR45 are empty. A separate non-implementing AI reviewer first rejected the old head, reproduced provenance/volume-underflow issues, and approved exact0f0cc5e after fixes,321 independent tests,39 probes and18 capture replays. Its report was saved in commitfec3adf at03:48:26Z, before merge03:49:13Z. It is not a human or external Codex Security audit.

PR47 blocks candle-derived fill/STOP/TP inference from original zero-volume provider records. PR46 preserves live follow-ups while expiring orphan episodes. These previously reviewed fixes remain integrated. PR48 notification-policy regression tests, CI and separate review were completed and the reviewed change merged conditionally at19:09:05Z. Its first production push tested349 and persisted with30 valid technical markets, zero synthetic/error candles, generic watches suppressed.

No strategy thresholds, real-trading switch, source provenance protections or capacity limits were weakened. Offline generated fixtures exercise code behavior; they are not claimed as real market data.

[XCN diagnostic report](cases/XCN_TL_20261018.md) adds exact alert timing, closed-candle replay and quota analysis. [Case evidence](audit_evidence/xcn_20261018_case.json) and native capture bundle are retained. Readiness remains unaccepted pending stable post-final-change operation and organic READY delivery/deduplication evidence. The scheduled engineering automation is enabled; actual unattended code changes or delivery success must be established from logs, not assumed.
