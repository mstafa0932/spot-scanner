from typing import List
import requests
import pandas as pd

from market_data import fetch_tickers, base_asset
from indicator_engine import analyze_symbol, IndicatorResult

class Opportunity:
    def __init__(self, symbol: str, score: int, reason: str, entry_price: str, stop_loss: str, tp_1: str, tp_2: str, is_super_signal: bool):
        self.symbol = symbol
        self.score = score
        self.reason = reason
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.tp_1 = tp_1
        self.tp_2 = tp_2
        self.is_super_signal = is_super_signal

def get_klines_df(base_coin: str, timeframe: str = '1h', limit: int = 250) -> pd.DataFrame:
    """جلب شموع التداول الفنية التاريخية للتحليل"""
    pair = f"{base_coin}USDT"
    url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval={timeframe}&limit={limit}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code != 200:
            return pd.DataFrame()
        raw = res.json()
        if not isinstance(raw, list) or len(raw) < 200:
            return pd.DataFrame()
        
        df = pd.DataFrame(raw).iloc[:, :6]
        df.columns = ['time', 'open', 'high', 'low', 'close', 'volume']
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df
    except Exception:
        return pd.DataFrame()

class MarketScanner:
    def __init__(self, top_n: int = 10):
        self.top_n = top_n

    def scan_market(self) -> List[Opportunity]:
        print("[*] Fetching Paribu markets...")
        try:
            tickers = fetch_tickers(tl_only=True)
            print(f"[*] Loaded {len(tickers)} Paribu TL markets.")
        except Exception as e:
            print(f"[!] Error fetching Paribu tickers: {e}")
            return []

        opportunities = []

        for ticker in tickers:
            try:
                coin = base_asset(ticker.symbol)
                if coin in ["USDT", "USDC", "TRY", "TL"]:
                    continue

                # جلب الشموع وتحليل الفلاتر
                df = get_klines_df(coin, timeframe='1h', limit=250)
                if df.empty:
                    continue

                ind: IndicatorResult = analyze_symbol(df)
                if not ind.valid:
                    continue

                score = 0
                reasons = []

                # 1. فلتر الاتجاه الصاعد
                if not ind.is_uptrend:
                    continue
                score += 30
                reasons.append("Uptrend (EMA50>200)")

                # 2. نمط التصحيح أو الشمعة الانعكاسية
                if ind.is_pullback:
                    score += 25
                    reasons.append("Pullback to EMA21")
                elif ind.is_bullish_pinbar:
                    score += 25
                    reasons.append("Bullish Pinbar Reversal")
                else:
                    continue

                # 3. تأكيد السيولة والزخم
                if ind.is_high_volume:
                    score += 20
                    reasons.append("High Volume")

                if ind.macd_line > ind.macd_signal:
                    score += 15
                    reasons.append("MACD Bullish")

                if 40 <= ind.rsi_14 <= 65:
                    score += 10
                    reasons.append("Healthy RSI")

                if score < 70:
                    continue

                # حساب الأسعار بالليرة التركية من منصة باريبو مباشرة
                entry = float(ticker.last)
                
                # حساب الوقف والفرق المستهدف بناءً على تذبذب ATR
                atr_pct = (ind.atr_14 / ind.current_close) if ind.current_close > 0 else 0.02
                risk_amount = entry * (atr_pct * 1.5)
                
                sl = max(0.0, entry - risk_amount)
                tp1 = entry + (risk_amount * 1.5)
                tp2 = entry + (risk_amount * 3.0)

                fmt = "{:.4f}" if entry < 1 else "{:.2f}"

                opp = Opportunity(
                    symbol=ticker.symbol,
                    score=score,
                    reason=" | ".join(reasons),
                    entry_price=fmt.format(entry),
                    stop_loss=fmt.format(sl),
                    tp_1=fmt.format(tp1),
                    tp_2=fmt.format(tp2),
                    is_super_signal=(score >= 85)
                )
                opportunities.append(opp)

            except Exception:
                continue

        opportunities.sort(key=lambda x: x.score, reverse=True)
        return opportunities[:self.top_n]
