from typing import List
import time

# استدعاء دوال جلب البيانات والمحرك التحليلي الذي صنعناه
from market_data import get_markets, get_candles # تأكد أن أسماء دوال الجلب تتطابق مع ملف market_data.py لديك
from indicator_engine import analyze_symbol, IndicatorResult

class Opportunity:
    def __init__(self, symbol: str, score: int, reason: str, entry_price: float, stop_loss: float, tp_1: float, tp_2: float, is_super_signal: bool):
        self.symbol = symbol
        self.score = score
        self.reason = reason
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.tp_1 = tp_1
        self.tp_2 = tp_2
        self.is_super_signal = is_super_signal

class MarketScanner:
    def __init__(self, top_n: int = 10):
        self.top_n = top_n

    def scan_market(self) -> List[Opportunity]:
        print(f"[*] Fetching market pairs...")
        try:
            symbols = get_markets()
            print(f"[*] Found {len(symbols)} pairs to scan.")
        except Exception as e:
            print(f"[!] Error fetching markets: {e}")
            return []

        opportunities = []

        for symbol in symbols:
            try:
                # جلب الشموع (على سبيل المثال فريم الساعة أو الـ 4 ساعات)
                df = get_candles(symbol, timeframe='1h', limit=250)
                
                if df is None or df.empty:
                    continue

                # تمرير البيانات للمحرك التحليلي
                ind: IndicatorResult = analyze_symbol(df)

                if not ind.valid:
                    continue

                score = 0
                reasons = []

                # ==========================================
                # 1. الفلاتر الصارمة (Hard Filters)
                # ==========================================
                
                # يجب أن تكون العملة في اتجاه صاعد واضح (استبعاد السقوط الحر)
                if not ind.is_uptrend:
                    continue
                score += 30
                reasons.append("Uptrend (EMA50>200)")

                # يجب أن يكون هناك تصحيح سعري أو شمعة انعكاسية (استبعاد الشراء من القمة)
                if ind.is_pullback:
                    score += 25
                    reasons.append("Pullback to EMA21")
                elif ind.is_bullish_pinbar:
                    score += 25
                    reasons.append("Bullish Pinbar Reversal")
                else:
                    # إذا كانت العملة صاعدة بقوة بدون تصحيح، نتجاهلها لتجنب الـ FOMO
                    continue

                # ==========================================
                # 2. فلاتر التأكيد (Confirmation Filters)
                # ==========================================
                
                # تأكيد السيولة
                if ind.is_high_volume:
                    score += 20
                    reasons.append("High Volume Break")

                # تأكيد الزخم
                if ind.macd_line > ind.macd_signal:
                    score += 15
                    reasons.append("MACD Bullish")

                if 40 <= ind.rsi_14 <= 65:
                    score += 10
                    reasons.append("Healthy RSI")

                # ==========================================
                # 3. اتخاذ القرار وحساب المستويات (R:R)
                # ==========================================
                
                # يجب أن تحقق العملة درجة نجاح عالية
                if score < 70:
                    continue

                # الدخول مع الإغلاق الحالي لأننا في منطقة تصحيح أصلاً
                entry = ind.current_close
                
                # وقف الخسارة يُحسب بناءً على تذبذب السوق (ATR) لحمايتك من ضرب الوقف الوهمي
                risk_distance = ind.atr_14 * 1.5
                sl = entry - risk_distance

                # حساب الأهداف بناءً على نسبة مخاطرة حقيقية
                risk = entry - sl
                tp1 = entry + (risk * 1.5)  # ربح 1.5 مقابل كل 1 خسارة
                tp2 = entry + (risk * 3.0)  # ربح 3.0 مقابل كل 1 خسارة

                # تنسيق الأسعار
                entry_str = f"{entry:.4f}" if entry < 1 else f"{entry:.2f}"
                sl_str = f"{sl:.4f}" if sl < 1 else f"{sl:.2f}"
                tp1_str = f"{tp1:.4f}" if tp1 < 1 else f"{tp1:.2f}"
                tp2_str = f"{tp2:.4f}" if tp2 < 1 else f"{tp2:.2f}"

                is_super = score >= 85

                opp = Opportunity(
                    symbol=symbol,
                    score=score,
                    reason=" | ".join(reasons),
                    entry_price=entry_str,
                    stop_loss=sl_str,
                    tp_1=tp1_str,
                    tp_2=tp2_str,
                    is_super_signal=is_super
                )
                
                opportunities.append(opp)
                
            except Exception as e:
                # تخطي العملة في حال وجود خطأ في البيانات
                continue

        # ترتيب الفرص من الأقوى للأضعف
        opportunities.sort(key=lambda x: x.score, reverse=True)
        
        # إرجاع أفضل N فرص (الحد الأقصى 10 كما حددنا)
        return opportunities[:self.top_n]
