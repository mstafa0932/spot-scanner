# ZRO_TL — user-supplied screenshots (2026-11-01)

Source: four screenshots supplied in conversation; private images deliberately not uploaded to GitHub. Screenshot filenames: 1001142366.jpg, 1001143356.jpg, 1001143355.jpg, 1001142367.jpg. These are observations, not as-of market data or execution records.

Telegram screenshot: Paribu Scanner, ZRO_TL, RECOVERY, score 86/100, 2 confirmations, reference LIMIT 90.1075 TL, stop 87.2199 TL (-3.20%), TP1 94.4388 TL (+4.81%), TP2 95.8826 TL (+6.41%), 24h ticker volume 19,094,572 TL, volume ratio 1.77x, order book 1.21x, spread 0.12%, RSI15m 63.3; displayed message clock 12:08 and date November 1. Message ID and authoritative run timestamp not yet verified.

Paribu ZRO/TL 4h chart screenshot shows selected candle labelled 2026-11-01 15:00 with OHLC open 92.85, high 95.99, low 91.99, close 95.00 and volume 81.61K (units as displayed). Another screenshot selected 2026-10-31 23:00 candle, OHLC 90.00/90.29/89.08/90.20, volume 24.08K. Other screenshot shows last 90.20 and high 90.43. The screenshots alone do not establish actual capture time or whether selected candle was closed when viewed.

The displayed high 95.99 exceeds both targets; this is target-price touch evidence only, NOT evidence of fill at LIMIT 90.1075, not proof of orderbook bid at exit, and not realized or net PnL. TP1/TP2 price-level observations should be counted as one unique signal event, not two trades. No user trade or fill asserted. The +6.53% difference between 95.99 and reference LIMIT is a non-executable price comparison, not a return.

Follow-up for Nov 5 report: cross-check original Telegram emission and GitHub run/state SHAs, closed native candles, chronological as-of bid/ask and L2 at entry/exit, stop/TP ordering and any SHADOW fill state; distinguish observation, simulated trade and executable fill. Keep pre/post PR64 stratification and avoid retrospective cherry-picking.



## Verified original ZRO emission and later native books (1 November)

Original run: https://github.com/mstafa0932/spot-scanner/actions/runs/41242032868. At 2026-11-01 00:08:06 TRT the SHADOW READY/RECOVERY alert for ZRO_TL was sent with score 86, two distinct closed-15m confirmations, and Telegram accepted message_id=1127. Discovery score was 89; the fresh-book final score was 86. Prior run 41241243648 at 23:58:38 TRT recorded 1/2 confirmations, score 85. The 15m candle opens were 23:30 and 23:45 TRT. Source trigger SHA ed27da92bfeb3174b477b7bd5b4531d4ea82e68a; resulting state commit 606adbff2f9133672516f39cce10f4c17eec0fd9.

Verified later native ZRO_TL best bid/ask (TRT): 19:08:55 94.53/94.68; 20:09:11 94.49/94.66; 21:09:13 92.98/93.14; 22:09:19 90.63/90.82; 23:19:23 89.50/89.56. The first two bid quotes exceeded TP1 94.4388, but not TP2 95.8826. The screenshot high 95.99 touches TP2 only as a chart observation, not as a verified executable bid. Entry LIMIT 90.1075 fill, exit fill, fees, slippage and net PnL remain UNKNOWN; this is one verified delivered signal, not two trades.

Five subsequent ZIP artifacts were verified with the exact hash-pinned verifier (SHA256 57197a2c847965602689bedc0f4d93b1c91411028b327af68865715aa318cd7a), all 776 record digests and 123 selected complete market-run frames passed. The four necessary RECOVERY structural checks were jointly true in 0/123 selected observations; the fifth original setup-type, unchanged trigger/risk/confirmations and later outcomes have NOT been replayed. This is not a full-market result or a strategy verdict. See monitoring/research/nov5-native-increment-20261101-2320.json. Private screenshots were not uploaded.
