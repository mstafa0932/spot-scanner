# Score trajectory — diagnostic journal

Scope: Paribu EARLY WATCH, 2026-10-09 to 2026-10-14. Diagnostic only. No strategy, threshold, or SHADOW_MODE changes.

## Measurement contract (registered before prospective sample)
- T0 = UTC timestamp of the first verified EARLY_WATCH for a distinct episode, with its logged score.
- Compare the nearest *observed* scan at T0+2h (target tolerance ±15 min); report missing observations as UNVERIFIED, never impute values.
- delta_score = score(T0+2h) - score(T0). For each observation record elapsed time, score, **all observable blockers** (a single logged decision is not proof it is the only blocker), spread, imbalance, RSI and provenance/run ID. Use NA for unavailable fields.
- Analyze 10–15 distinct new episodes if available; if fewer, report actual n. A repeat EARLY WATCH for the same episode is not a new independent case.
- Prespecified *descriptive* bands: median delta < -15 = A (decline); > +5 = B (increase); otherwise C (mixed/stable). These bands **do not prove** filter correctness, missed profit, or architectural fault; those require outcome and counterfactual price/risk analysis. Do not infer bot inactivity from low alert count alone.

## Four existing cases (latest observed score, not necessarily at +2h)
| Symbol | T0 score | Latest observed score | Raw delta | Latest blocker | T+2h matched? | Outcome |
|---|---:|---:|---:|---|---|---|
| PUMP_TL | 81 | 40 | -41 | RSI outside watch range | UNVERIFIED | PENDING |
| ETH_TL | 69 | 39 | -30 | RSI outside watch range | UNVERIFIED | PENDING |
| MINA_TL | 66 | 70 | +4 | 4h clearly weak | UNVERIFIED | PENDING |
| XAI_TL | 70 | 72 | +2 | volume not expanding | UNVERIFIED | PENDING |

Latest observations from GitHub Actions run 37988617072 (2026-10-09 20:41 UTC); initial scores from Telegram alerts. These raw deltas are NOT standardized +2h deltas.

## Prospective observations
| Symbol | T0 UTC | Scan UTC | Elapsed min | Score T0 | Score scan | Delta | All observable blockers | Spread % | Imbalance x | RSI 15m | Run ID | Notes |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---|---|

## Data integrity
- A scan may log only the first decisive rejection; write `additional blockers unobserved` instead of inventing a full blocker set.
- Periodic observations are not guaranteed by this file. Append only after actual GitHub log verification.
