from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, List

from market_data import get_market_snapshot, Ticker
from indicator_engine import analyze_symbol, IndicatorResult


# ============================================================
# Spot Scanner & Ranking Engine
# Spot Scanner project
# ============================================================


@dataclass(frozen=True)
class Opportunity:
    symbol: str
    score: int
    current_price: Decimal
    rsi: Optional[Decimal]
    atr: Optional[Decimal]
    entry_price: Decimal
    stop_loss: Decimal
    tp_1: Decimal
    tp_2: Decimal
    reason: str


class MarketScanner:
    def __init__(self, top_n: int = 3):
        self.top_n = top_n

    def scan_market(self) -> List[Opportunity]:
        """
        Scans all Paribu TL pairs, applies hard filters, computes scores,
        and returns the top N best opportunities.
        """
        market_snapshot = get_market_snapshot()
        candidates: List[Opportunity] = []

        print(f"[*] Starting market scan across {len(market_snapshot)} pairs...")

        for symbol, ticker in market_snapshot.items():
            # 1. Basic Ticker Hard Filters
            if ticker.quote_volume is None or ticker.quote_volume < Decimal("500000"):
                # Skip low volume markets (< 500k TL volume)
                continue

            if ticker.spread_percent is not None and ticker.spread_percent > Decimal("1.5"):
                # Skip wide spreads (> 1.5%)
                continue

            # 2. Fetch Candle and Technical Indicators (15m timeframe)
            ind_result = analyze_symbol(symbol, resolution="15", limit=50)
            if not ind_result or ind_result.rsi_14 is None or ind_result.atr_14 is None:
                continue

            # 3. Strict Hard Filters (Anti-FOMO & Overbought checks)
            # Hard Filter: Reject if RSI is in extreme overbought zone
            if ind_result.rsi_14 > Decimal("75"):
                continue

            # Hard Filter: Reject if price is way below short-term trend without momentum
            if ind_result.is_above_ema9 is False and ind_result.is_above_ema21 is False:
                # Weak downward structure, skip for long scanner
                continue

            # 4. Scoring Engine (100-Point Scale)
            score = 50  # Base score

            # RSI scoring (Prefer healthy bullish momentum between 50 and 70)
            rsi = ind_result.rsi_14
            if Decimal("50") <= rsi <= Decimal("68"):
                score += 20
            elif Decimal("40") <= rsi < Decimal("50"):
                score += 10
            elif rsi > Decimal("72"):
                score -= 15  # Approaching overbought

            # Trend scoring (EMA alignment)
            if ind_result.is_above_ema9:
                score += 15
            if ind_result.is_above_ema21:
                score += 15

            # Clamp score between 0 and 100
            score = max(0, min(100, score))

            # Minimum score threshold to qualify as a strong candidate
            if score < 70:
                continue

            # 5. Dynamic Risk/Reward & Precise Decimal Pricing Engine
            price = ind_result.current_close
            atr = ind_result.atr_14

            # Stop loss placed below 1.5 * ATR
            stop_loss = price - (atr * Decimal("1.5"))
            
            # Targets based on multiples of ATR risk
            risk = price - stop_loss
            tp_1 = price + (risk * Decimal("1.5"))  # R:R 1.5
            tp_2 = price + (risk * Decimal("2.5"))  # R:R 2.5

            # Ensure strict Decimal preservation (no blind rounding)
            candidates.append(
                Opportunity(
                    symbol=symbol,
                    score=score,
                    current_price=price,
                    rsi=rsi,
                    atr=atr,
                    entry_price=price,
                    stop_loss=stop_loss,
                    tp_1=tp_1,
                    tp_2=tp_2,
                    reason=f"RSI: {rsi.quantize(Decimal('0.1'))} | Trend: Bullish EMA Alignment"
                )
            )

        # 6. Ranking Engine: Sort by score descending and take Top N
        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates[:self.top_n]


def run_scanner_test() -> None:
    print("=" * 70)
    print("SPOT SCANNER & RANKING ENGINE TEST")
    print("=" * 70)
    
    scanner = MarketScanner(top_n=3)
    top_opportunities = scanner.scan_market()

    print(f"\nScan Complete. Top Opportunities Found: {len(top_opportunities)}")
    print("-" * 70)

    if not top_opportunities:
        print("No opportunities passed the strict hard filters at this moment.")
    else:
        for i, opp in enumerate(top_opportunities, 1):
            print(f"#{i} | Symbol: {opp.symbol:12} | Score: {opp.score}/100")
            print(f"     Entry:      {opp.entry_price}")
            print(f"     Stop Loss:  {opp.stop_loss}")
            print(f"     TP 1:       {opp.tp_1}")
            print(f"     TP 2:       {opp.tp_2}")
            print(f"     Reason:     {opp.reason}")
            print("-" * 70)


if __name__ == "__main__":
    run_scanner_test()
