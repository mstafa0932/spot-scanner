# Paribu — multi-cause audit of user-documented surges, 11–25 October 2026

**Purpose:** group all *recoverable and documented* user-supplied cases from the last 15 calendar days by real scanner stage, isolate proven defects from justified rejections, and prioritize **non-strategy** calibration. This report is not a list of all actual Paribu market gainers, nor a backtest of executable trades.

## Sources and coverage

- Private ChatGPT Library `/Paribu Bot Research/2026-10-25 Chat Handoff`: 194 integrity-verified curated trading screenshots, manifest + 21 case-group rows + 47 historical/tracked symbols. The collection includes older dates, comparisons, repeat screenshots and confirmed positive READY controls. **Not all 194 photos are separate last-15-day rallies, and not all 47 symbols are missing recommendations.**
- Contemporary scanner run IDs and immutable source blob references, with time-aligned case-specific analyses: [Issue #50](https://github.com/mstafa0932/spot-scanner/issues/50), [Issue #59](https://github.com/mstafa0932/spot-scanner/issues/59).
- Concrete source: `scanner.py`, `risk_engine.py`, `market_data.py`, `coverage_scheduler.py`; PRs [#49](https://github.com/mstafa0932/spot-scanner/pull/49), [#51](https://github.com/mstafa0932/spot-scanner/pull/51), [#52](https://github.com/mstafa0932/spot-scanner/pull/52), [#53](https://github.com/mstafa0932/spot-scanner/pull/53), [#54](https://github.com/mstafa0932/spot-scanner/pull/54), [#55](https://github.com/mstafa0932/spot-scanner/pull/55), [#58](https://github.com/mstafa0932/spot-scanner/pull/58).
- Most recent pinned operational 12-scan sample: `main/scanner_state.json` blob `49d20502b842ab52c555469420b95f905f3f897a`, 25 Oct **21:16–23:06 TRT**, 235 markets per scan (2,820 **repeated market-scan first-rejection decisions**). That later window is not a pre-rally explanation for earlier pictures.

**Decision hierarchy:** photographed rise ≠ radar detection ≠ READY eligibility ≠ successful Telegram acceptance ≠ actual fill ≠ profitable execution. For a genuine missed READY establish authentic fully closed Paribu 15m/1h/4h candles, pair, prevailing BTC regime, concurrent fresh orderbook, all score/RSI/EMA/MACD/ATR/setup/liquidity/spread/book/risk/confirmation checks, signal cooldown and Telegram response at the *same earlier timestamp*. Do not substitute later screenshots.

## Root-cause families: NOT one defect

| Priority | Family | Time-aligned examples | Assessment / safe work |
|---|---|---|---|
| P0 engineering | Lost bounded post-risk followup | **PEPE_TL**, 23 Oct, score 90/88 correctly rejected by `rr_below_min` / `risk_out_of_range`; followup priority was erased | **Confirmed implementation defect**, distinct from correct risk rejection; isolated [PR #54](https://github.com/mstafa0932/spot-scanner/pull/54), regression reproduced fixed-TTL / spread→RSI / never-bypass READY. Unmerged pending independent exact-head review |
| P1 visibility | High-score candidate veto reported only as `setup not ready` | **SAHARA_TL** at 11:14 TRT 24 Oct, score 89, reference ~0.3958 TL and real volume 2.35x but no qualifying BREAKOUT/PULLBACK/RECOVERY setup | **Proven missing predicate-level evidence**, not proof the setup rule was misimplemented. Diagnostic-only proposed [PR #60](https://github.com/mstafa0932/spot-scanner/pull/60) records already-computed setup predicate snapshot on future vetoes, no rule or alert changed. Cannot retroactively recreate 24 Oct predicates from a later candle |
| P1 controls | Underlying market/execution gates | **MASK, RED, ATOM, MEGA, ZKC, LIT, JASMY, SENT, OXT, ANIME, SAND** (liquidity, sometimes crossing into spread/book); **BELG, ANIME, BEAM, WAVES_TL, LRC, DYM, MINA, RAD** (wide spread/weak book); **BONK, ATH, DOT, DASH, GRASS, XCN, W, ZRO** (momentum/RSI/4h/score) | **Distinct stage-based rejections**, generally code-consistent in checked windows. No evidence that weakening hard gates improves executed, after-cost results. Preserve all gates |
| P1 observations | Missing/unreliable research outcomes | Historical near-miss dataset before 18 Oct, plus **OM_TL** short canonical 1h history | **Research-data** defect in historical retry/provenance measurement addressed in unmerged PR #51. Not a confirmed missed real READY. Fail closed on insufficient/unauthentic candles |
| P2 prospective scheduling | High-score MACD-negative discovery rejection not getting extra priority | **BONK_TL**, 23 Oct score 87/85/86 with 15m MACD negative; [PR #58](https://github.com/mstafa0932/spot-scanner/pull/58) proposes bounded scheduling rechecks | **Hypothesis, not proven lost valid READY**: 19:13 skip had no NEW closed 15m bar; 19:23 and 19:33 rescanned new closed bars with negative MACD |
| P2 ordinary rotation | Coverage capacity deferment | **BEAM** 20:43 Oct23 and multiple recent pairs | Recorded skip ≠ qualifying lost candle. BEAM 20:43 no newer closed 15m bar; 20:53 actually evaluated, RSI 68.22 >68. Recent 12-scan universe had no consecutive orderbook-capacity deferrals. Don't raise polling limits |
| P2 accounting | READY confirmation/lifecycle annotation and Telegram expected silence | **ONDO**, **ALT**, **ETHFI** positive READY controls; **RLC** earlier lifecycle followup corrected 10 Oct; Telegram EARLY WATCH deliberately silent | **Not a global Telegram outage**. PR #49 for lifecycle logging still unreviewed; HEAD main has EARLY WATCH returning false. Validate future READY delivery with explicit acceptance rather than infer from missing alerts |

### Comparison across named symbols (case-specific rather than asserting all qualify)

| Symbol(s) | Relevant 11–25 Oct evidence | Classification |
|---|---|---|
| XCN_TL | 18 Oct score 78 and weak 4h; on 25 Oct score 87 confirmation 1/2 but later volume, momentum and spread rejects | Time-dependent technical + trigger gates; no proved valid missing READY |
| ALT_TL | 19 Oct real **SHADOW READY** score 89, 2 confirmations; entry fill unconfirmed | Positive control, **not wholly missed** |
| ONDO_TL | 21 Oct SHADOW READY/Telegram score 89; later different low-liquidity periods | Positive control, not a permanently undetected coin |
| GRASS_TL | 25 Oct 08:15 1/2 confirmations at score 86, next CLOSED bar evaluated at score 59 negative MACD | Genuine deterioration after first confirmation |
| LIT_TL | 21–23 Oct below 5m TL quote turnover in checked windows | Liquidity / native zero-volume evidence |
| W_TL | Weak 4h/RSI/book and other gates across historical windows | Conditional safety gates |
| DYM_TL | Spread ~1% vs 0.40%; also weak book | Price-impact gate |
| DOT_TL | Alternating capacity, MACD, high RSI and weak book in 23 Oct runs | Multiple technical/book conditions |
| DASH_TL | Weak volume/negative 15m momentum with exchange-published zero-volume bars | Not a proven synthetic-data implementation fault |
| ATOM_TL | Quote turnover ~2.27–2.36m TL below 5m (23 Oct) | Liquidity eligibility |
| ATH_TL | 02:33 score72 and 03:32 score77 below READY 80, later score84/RSI ~79–83 above watch/alert range | Early-to-overheated cohort; retrospective research, not proven missed READY |
| NMR_TL | Repeated weak book imbalance | Execution gate |
| MINA_TL | Wide spread/weak book with ordinary capacity deferrals | Multiple book gates |
| SAND_TL | Liquidity/book/RSI in different runs | Time-dependent stage transitions |
| RAD_TL | Spread above limit in recorded sample | Execution gate |
| MASK_TL | Before ~13:41 22 Oct turnover <5m, later spread 0.9666%, book imbalance weak | Liquidity → spread/book; rally screenshot ~13:33 already preceded liquidity crossing |
| PEPE_TL | 23 Oct risk-stage TTL followup implementation defect; many later actual RSI/book rejections | **Proven followup bug**, no proven safe missing READY |
| JASMY_TL | Below liquidity then RSI ~82.5 or book rejection | Liquidity → overheat |
| SENT_TL | Borderline liquidity then spread/book/score | Multi-stage conditional rejects |
| BONK_TL | High score 87/85/86 but MACD histogram remained negative in new closed bars | Correct 15m predicate; prospective followup hypothesis only |
| MEGA_TL | ~3.29–3.67m TL turnover in Oct23 12-run sample | Liquidity |
| ZKC_TL | ~2.14–2.64m TL turnover in Oct23 sample | Liquidity |
| WAVES_TL vs WAVES_USDT | TL scanner book spread 0.55–0.99%, weak imbalance; screenshot **USDT** pair | Distinct markets; invalid to compare books |
| BEAM_TL | 20:33 score84, refreshed buy flow 1.051<1.12; 20:43 no new closed bar; 20:53 score86 RSI68.2238>68; later risk stop too wide | Book → RSI → risk in time |
| BELG_TL | Deduplicated 43 runs Oct24–25: 24 spread rejects, 2 weak book, 17 capacity, zero READY; +~20% screenshot not verified fill | Wide spread / thin bid side |
| SAHARA_TL | 11:14 Oct24 score89 but no valid setup; 12:04/12:14 book flow 0.93/0.97; subsequent RSI >72 or score <80 | Multiple distinct pattern/book/RSI gates and missing veto diagnostics |
| RED_TL | 24 Oct prior 12 runs 2.48–2.69m TL turnover; later spread/book failures | Liquidity first; no technical evaluation before crossing |
| ANIME_TL | 24 Oct liquidity crossed 5m, observed spread 1.3158% then ~0.65%; later 25 Oct many one-tick-equivalent ~0.68% spreads | Liquidity → **microstructure**; do not raise cap |
| OXT_TL | 25 Oct before screenshot 10/12 below liquidity; later spread 0.6237%, imbalance 0.641<0.85 at 10:15 | Liquidity → spread/book, not a Telegram bug |
| LRC_TL | 25 Oct spread 0.47–0.73%, ATR 0.191%, very low volume ratio 0.04365 and many native zero-volume bars | Spread + volatility/volume gate |
| WLD_TL | Historical high-score? at least 1/2 confirmation only; RSI/momentum/book inconsistent | Not proved READY |
| ARB_TL, INJ_TL, ZRO_TL | Incomplete/bounded older examples with book, candle and technical reasons in Oct23 forensic inventory | No full pre-rally eligible-stage reconstruction |
| VVV_TL, NEAR_TL, SOL_TL | Older screenshots with insufficient exact contemporaneous ready-stage books and run linkage in recovered materials | **Unresolved**; do not assign fabricated bug |
| XTZ_TL | Some stage rejects but historical paper READY/TP1 research record (execution not independently confirmed) | Mixed control, not wholly missed |

**Older/outside 11–25 Oct date window:** BTC (regime reference, not an altcoin pick), ETH, PUMP, XAI, TIA, CTSI, OGN, ETHFI (recorded positive READY 6 Oct), RLC (prior 5–8 Oct followup defect fixed Oct10), plus older standalone images. Preserve these as background/context; never silently count them as Oct11–25 missing surges. Some date windows overlap or are incomplete; names in the case archive are not a claim of exhaustive Paribu 15-day market coverage.

## Cross-cohort reality check: scan decisions, not missing profits

From pinned 25 Oct 12-cycle shadow state: **1,163** low liquidity; **661** capacity; **271** wide spread; **261** 4h weakness; **228** low book imbalance; **76** RSI out of watch; **40** negative/nonconstructive 15m; **37** nonexpanding volume; **28** ATR range; 2 data gaps for **OM_TL** (106 and 107 true 1h candles vs 205 required). Zero READY confirmations or NEAR/READY alerts in those 12 cycles. `coverage:not_evaluated_capacity` alternated with evaluation; max adjacent consecutive orderbook coverage skips **1** across 235 markets. No proof that a qualifying READY was lost to capacity in that sample. These counts are **first recorded reason** only, not independent co-occurring failures.

Historical Oct18–22 near-miss research had 2,121 measured out of 2,125 archived rejects; four incomplete all **OM_TL** canonical history shortages. For 832 *measured* spread rejects, 178 had 4h high-based MFE >=1.5%; for 1,164 *measured* weak-book rejects, 353 had the same threshold. Neither set proves a fill, after-fee profitability, TP-versus-stop order, or better safety from weakening the filter. Selected upward photographs bias any ranking; use same-period non-rallies and holdouts.

## Bounded calibration actions (without changing parameters, strategy or trade permissions)

1. **P0 release correctness:** complete *independent exact-head* review of PR #54 (post-risk followup), then integrate/test with current main in isolation, explicitly check Telegram delivery credentials and get owner signoff. Do not merge based solely on green pytest.
2. **P1 forensic predicates:** maintain new DRAFT PR #60 for future high-score `setup not ready` cases; record missing predicate values as null. Do not retroactively assign SAHARA a fabricated missing condition; do not use this diagnostic field to change READY or scoring.
3. **P1 research bias/data quality:** independently review PR #51 measured/inauthentic-candle filters and timeout retries and #52 liquidity logs; retain source provenance and missingness flags. Do not recalculate old outcomes as genuine fills.
4. **P2 measure before tuning:** collect a time-matched, prospective winner + nonwinner cohort for ATH/BONK/SAHARA/GRASS/BEAM, include closed-bar ages, setup-veto bitfields, fresh books, fill depth, fees, slippage, MFE/MAE and stop-first OHLC ambiguity. No threshold changes from selected winners.
5. **P2 safe scheduling:** for PR #58 compare actual *new closed bars delayed* and eventual eligible READY vs non-rally outcomes. Avoid counting skips on a **same closed candle** as a missed confirmation.
6. **Guardrails:** preserve `SHADOW_MODE=true`, no live trading; original RSI/ATR/EMA/MACD/5m liquidity/0.40% spread/book/BTC/score/R:R/confirmations/anti-FOMO filters; real Paribu-only authentic closed candles; Telegram **NEAR READY/READY only**, never broad EARLY WATCH. Branches are research-only pending review and explicit approval.

**Conclusion:** multiple independent causes dominate. The robustly proven alert-path-related code defect is *followup retention* after genuine risk rejection (PR #54), **not** a demonstrated common rejection-logic or Telegram-delivery failure across every spike. Other operational/research defects affect observability and statistical trustworthiness. A high score or post-hoc green candle must never bypass market or risk checks.
