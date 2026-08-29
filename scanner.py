import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time
import logging
from datetime import datetime

# ==========================================
# ⚙️ إعدادات البوت الأساسية (Configuration)
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

PARIBU_FEE_RATE = 0.003       # رسوم باريبو التقريبية (0.3% - عدلها حسب مستوى حسابك)
EXPECTED_SLIPPAGE = 0.0015    # الانزلاق السعري المتوقع (0.15%)
MIN_NET_PROFIT = 0.012        # أقل صافي ربح مقبول بعد الرسوم (1.2%)
MIN_REAL_RR = 1.2             # أقل نسبة مخاطرة للعائد مقبولة
MIN_SCORE_THRESHOLD = 70      # الحد الأدنى للتقييم بعد العقوبات
TOP_N_SIGNALS = 3             # إرسال أفضل X إشارات فقط

# إعدادات Binance
exchange = ccxt.binance()
TIMEFRAME = '15m'
LIMIT = 100

# ==========================================
# 📡 جلب بيانات Paribu (الأسعار المحلية)
# ==========================================
def fetch_paribu_tickers():
    try:
        url = "https://www.paribu.com/ticker"
        response = requests.get(url, timeout=10)
        data = response.json()
        paribu_data = {}
        for pair, info in data.items():
            if pair.endswith('_TL'):
                symbol = pair.replace('_TL', '')
                paribu_data[symbol] = {
                    'ask': float(info['lowestAsk']),
                    'bid': float(info['highestBid']),
                    'last': float(info['last'])
                }
        return paribu_data
    except Exception as e:
        logging.error(f"❌ خطأ في جلب بيانات Paribu: {e}")
        return {}

# ==========================================
# 🧠 التحليل الفني وحساب المقاومات والدعوم
# ==========================================
def analyze_market(symbol_usdt):
    try:
        bars = exchange.fetch_ohlcv(symbol_usdt, TIMEFRAME, limit=LIMIT)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # المؤشرات الأساسية
        df['ema21'] = ta.ema(df['close'], length=21)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        macd = ta.macd(df['close'])
        df['macd'] = macd['MACD_12_26_9']
        df['macd_signal'] = macd['MACDs_12_26_9']
        
        df['vol_sma'] = df['volume'].rolling(window=20).mean()
        df['vol_multiplier'] = df['volume'] / df['vol_sma']

        # 🧱 تحديد المقاومة والدعم الفعلي (Price Action)
        # المقاومة: أعلى قمة في آخر 40 شمعة
        df['resistance'] = df['high'].rolling(window=40).max()
        # الدعم: أدنى قاع في آخر 20 شمعة
        df['support'] = df['low'].rolling(window=20).min()
        
        return df.iloc[-1]
    
    except Exception as e:
        logging.warning(f"⚠️ تجاوز {symbol_usdt} بسبب خطأ: {e}")
        return None

# ==========================================
# ⚖️ نظام التقييم وحساب الـ Targets الواقعية
# ==========================================
def process_opportunity(coin, ta_data, paribu_data):
    close_usdt = ta_data['close']
    support_usdt = ta_data['support']
    resistance_usdt = ta_data['resistance']
    atr_usdt = ta_data['atr']
    
    # حساب المسافات المئوية من الشارت العالمي (Binance)
    dist_to_support_pct = (close_usdt - support_usdt) / close_usdt
    dist_to_res_pct = (resistance_usdt - close_usdt) / close_usdt
    atr_pct = atr_usdt / close_usdt
    
    # الأسعار المحلية (Paribu)
    ask_try = paribu_data['ask']
    bid_try = paribu_data['bid']
    spread_pct = (ask_try - bid_try) / bid_try
    
    # 🛑 الوقف (SL): تحت الدعم الفعلي بمسافة نصف ATR لحماية من ضرب الوقف الوهمي
    sl_try = ask_try * (1 - dist_to_support_pct - (atr_pct * 0.5))
    
    # 🎯 الهدف (TP1): قبل المقاومة الفعلية بمسافة ربع ATR لضمان التنفيذ
    tp1_try = ask_try * (1 + dist_to_res_pct - (atr_pct * 0.25))
    
    # 💰 الحسابات المالية (رسوم + انزلاق)
    total_fees_and_slippage = (PARIBU_FEE_RATE * 2) + EXPECTED_SLIPPAGE
    
    gross_profit_pct = (tp1_try - ask_try) / ask_try
    net_profit_pct = gross_profit_pct - total_fees_and_slippage
    
    risk_pct = ((ask_try - sl_try) / ask_try) + total_fees_and_slippage
    
    # 📐 R:R الحقيقي
    real_rr = net_profit_pct / risk_pct if risk_pct > 0 else 0

    # ------------------------------------------
    # 🛡️ فلترة أولية صارمة
    # ------------------------------------------
    # يجب أن يكون الاتجاه صاعد، وفوق EMA21، وهناك مساحة للمقاومة
    if close_usdt < ta_data['ema21'] or dist_to_res_pct < 0.01:
        return None
    
    if net_profit_pct < MIN_NET_PROFIT or real_rr < MIN_REAL_RR:
        return None # لا تستحق المخاطرة
        
    # ------------------------------------------
    # 🏆 نظام التقييم والعقوبات (Scoring & Penalties)
    # ------------------------------------------
    score = 80 # التقييم الأساسي المبدئي
    penalties = []
    
    # 1. عقوبة التشبع الشرائي (RSI)
    if ta_data['rsi'] > 65:
        penalty = (ta_data['rsi'] - 65) * 1.5
        score -= penalty
        penalties.append(f"RSI عالي (-{penalty:.1f})")
        
    # 2. عقوبة ضعف السيولة (Volume)
    if ta_data['vol_multiplier'] < 1.0:
        penalty = (1.0 - ta_data['vol_multiplier']) * 30
        score -= penalty
        penalties.append(f"ضعف سيولة (-{penalty:.1f})")
        
    # 3. عقوبة السبريد العالي (Paribu)
    if spread_pct > 0.004: # أعلى من 0.4%
        penalty = (spread_pct - 0.004) * 10000
        score -= penalty
        penalties.append(f"سبريد مرتفع (-{penalty:.1f})")

    # 🌟 مكافآت
    if real_rr > 2.0: score += 10
    if ta_data['macd'] > ta_data['macd_signal']: score += 5
    
    # منع التقييم السلبي
    score = max(0, min(100, score))
    
    if score < MIN_SCORE_THRESHOLD:
        return None

    # تقييم القوة كرسالة
    if score >= 90: strength = "🔥 EXCEPTIONAL"
    elif score >= 80: strength = "🟢 STRONG"
    else: strength = "🟡 GOOD"

    return {
        'coin': f"{coin}_TL",
        'score': score,
        'strength': strength,
        'ask': ask_try,
        'resistance_try': ask_try * (1 + dist_to_res_pct), # المقاومة كقيمة
        'tp1': tp1_try,
        'sl': sl_try,
        'net_profit': net_profit_pct,
        'real_rr': real_rr,
        'spread': spread_pct,
        'rsi': ta_data['rsi'],
        'vol_multi': ta_data['vol_multiplier'],
        'penalties': " | ".join(penalties) if penalties else "لا يوجد عقوبات"
    }

# ==========================================
# 🚀 محرك المسح الرئيسي (Main Scanner)
# ==========================================
def run_scanner():
    logging.info("بدء جلب بيانات Paribu...")
    paribu_data = fetch_paribu_tickers()
    if not paribu_data:
        return "⚠️ تعذر الاتصال بـ Paribu."

    coins_to_scan = [c for c in paribu_data.keys() if c not in ['USDT', 'USDC']]
    valid_signals = []

    logging.info(f"جاري مسح {len(coins_to_scan)} عملة فنيًا...")
    
    for coin in coins_to_scan:
        symbol_usdt = f"{coin}/USDT"
        ta_data = analyze_market(symbol_usdt)
        
        if ta_data is not None:
            signal = process_opportunity(coin, ta_data, paribu_data)
            if signal:
                valid_signals.append(signal)
        
        time.sleep(0.1) # احترام حدود الـ API

    # 🧹 ترتيب الفرص حسب التقييم (Score) وأخذ أفضل 3 فقط
    valid_signals = sorted(valid_signals, key=lambda x: x['score'], reverse=True)
    top_signals = valid_signals[:TOP_N_SIGNALS]

    # ==========================================
    # 📨 تنسيق رسالة التلغرام (النسخة الاحترافية)
    # ==========================================
    if not top_signals:
        return "📭 لا توجد فرص تتوافق مع معايير المخاطرة/العائد والمقاومات حالياً."

    msg = f"🔥 Paribu — أفضل {len(top_signals)} فرص Spot\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "مبنية على دعوم ومقاومات فعلية بعد حساب الرسوم.\n\n"

    for idx, s in enumerate(top_signals, 1):
        msg += f"🎯 SPOT ENTRY #{idx}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🪙 {s['coin']}\n"
        msg += f"💪 القوة: {s['strength']}\n"
        msg += f"📊 Score: {s['score']:.0f}/100\n"
        msg += f"📉 العقوبات: {s['penalties']}\n\n"

        msg += f"💵 السعر (Ask): {s['ask']:.6g}\n"
        msg += f"🧱 أقرب مقاومة: {s['resistance_try']:.6g}\n"
        msg += f"🎯 الهدف الفعلي: {s['tp1']:.6g}\n"
        msg += f"🛑 الوقف (أسفل الدعم): {s['sl']:.6g}\n\n"

        msg += f"📐 R:R الحقيقي: 1:{s['real_rr']:.2f}\n"
        msg += f"💰 صافي الربح المتوقع: +{s['net_profit']*100:.2f}%\n"
        msg += f"📏 السبريد: {s['spread']*100:.2f}%\n\n"

        msg += f"📈 RSI: {s['rsi']:.1f}\n"
        msg += f"💧 حجم التداول: {s['vol_multi']:.2f}x\n\n"

    msg += "⚠️ الأهداف ديناميكية بناءً على المقاومات، وليست نسب ثابتة."
    
    return msg

# للتجربة المحلية
if __name__ == "__main__":
    result = run_scanner()
    print(result)
