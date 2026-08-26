from dataclasses import dataclass
from decimal import Decimal
from market_data import get_market_snapshot, fetch_candles
from indicator_engine import analyze_symbol

@dataclass
class Opportunity:
    symbol: str
    score: int
    reason: str
    entry_price: float
    stop_loss: float
    tp_1: float
    tp_2: float

class MarketScanner:
    def __init__(self, top_n: int = 3):
        self.top_n = top_n

    def scan_market(self) -> list[Opportunity]:
        snapshot = get_market_snapshot()
        opportunities = []

        total_markets = len(snapshot)
        passed_liquidity = 0
        valid_candles = 0
        passed_score = 0

        print(f"[DIAGNOSTIC] Starting scan across {total_markets} TRY markets...")

        for symbol, ticker in snapshot.items():
            # فلتر السيولة والـ Spread
            if ticker.quote_volume is None or ticker.quote_volume < Decimal("100000"):
                continue
            if ticker.spread_percent is not None and ticker.spread_percent > Decimal("1.5"):
                continue
            passed_liquidity += 1

            # جلب الشموع وتحليلها
            df = fetch_candles(symbol, resolution="15", limit=250)
            ind = analyze_symbol(df)

            if not ind.valid:
                continue
            valid_candles += 1

            # حساب نقاط الجودة (Scoring / 100)
            score = 0
            reasons = []

            if ind.is_uptrend:
                score += 30
                reasons.append("اتجاه صاعد")
            if ind.is_above_ema9 and ind.is_above_ema21:
                score += 20
                reasons.append("فوق EMA9/21")
            if 40 <= ind.rsi_14 <= 65:
                score += 20
                reasons.append(f"RSI متوازن ({ind.rsi_14:.1f})")
            if ind.is_pullback:
                score += 15
                reasons.append("تصحيح مثالي")
            if ind.is_high_volume:
                score += 15
                reasons.append("فوليوم مرتفع")

            # الشرط الصارم للتأهل: 65+ نقطة
            if score >= 65:
                passed_score += 1
                entry = ind.current_close
                atr = ind.atr_14 if ind.atr_14 > 0 else (entry * 0.02)
                sl = max(entry - (1.5 * atr), ind.current_low * 0.99)
                risk = entry - sl
                tp1 = entry + (1.5 * risk)
                tp2 = entry + (3.0 * risk)

                opportunities.append(Opportunity(
                    symbol=symbol,
                    score=score,
                    reason=", ".join(reasons),
                    entry_price=round(entry, 4),
                    stop_loss=round(sl, 4),
                    tp_1=round(tp1, 4),
                    tp_2=round(tp2, 4)
                ))

        print(f"[DIAGNOSTIC] Liquidity Pass: {passed_liquidity} | Valid Candles: {valid_candles} | High Score Pass: {passed_score}")

        opportunities.sort(key=lambda x: x.score, reverse=True)
        return opportunities[:self.top_n]
