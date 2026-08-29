import os
import time
import logging
import requests
import ccxt
import pandas as pd
import pandas_ta as ta

# ==========================================
# ⚙️ إعدادات البوت الأساسية (Configuration)
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

PARIBU_FEE_RATE = 0.003       # رسوم باريبو (0.3% شراء + 0.3% بيع = 0.6% إجمالي)
EXPECTED_SLIPPAGE = 0.0015    # الانزلاق السعري المتوقع (0.15%)
MIN_NET_PROFIT = 0.012        # أدنى صافي ربح مقبول بعد الرسوم (1.2%)
MIN_REAL_RR = 1.2             # أدنى نسبة مخاطرة إلى عائد حقيقية
MIN_SCORE_THRESHOLD = 70      # الحد الأدنى للتقييم النهائي
TOP_N_SIGNALS = 3             # إرسال أفضل 3 فرص فقط

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

exchange = ccxt.binance({'enableRateLimit': True})
TIMEFRAME = '15m'
LIMIT = 100

# ==========================================
# 📡 جلب الأسعار اللحظية من Paribu
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
# 🧠 التحليل الفني واستخراج الدعوم والمقاومات
# ==========================================
def analyze_market(symbol_usdt):
    try:
        bars = exchange.fetch_ohlcv(symbol_usdt, TIMEFRAME, limit=LIMIT)
        if not bars or len(bars) < 50:
            return None

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # المؤشرات الفنية
        df['ema21'] = ta.ema(df['close'], length=21)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

        macd = ta.macd(df['close'])
        if macd is not None and not macd.empty:
            df['macd'] = macd['MACD_12_26_9']
            df['macd_signal'] = macd['MACDs_12_26_9']
        else:
            df['macd'] = 0
            df['macd_signal'] = 0

        df['vol_sma'] = df['volume'].rolling(window=20).mean()
        df['vol_multiplier'] = df['volume'] / df['vol_sma']

        # 🧱 تحديد المقاومة والدعم الفعليين من الشموع السابقة (Price Action)
        # استخدام shift(1) لتجنب تأثر النطاق بالشمعة الحالية
        df['resistance'] = df['high'].shift(1).rolling(window=40).max()
        df['support'] = df['low'].shift(1).rolling(window=20).min()

        return df.iloc[-1]

    except Exception as e:
        logging.warning(f"⚠️ تجاوز {symbol_usdt} بسبب خطأ: {e}")
        return None

# ==========================================
# ⚖️ تقييم الفرصة وحساب الأهداف الحقيقية
# ==========================================
def process_opportunity(coin, ta_data, paribu_data):
    close_usdt = ta_data['close']
    support_usdt = ta_data['support']
    resistance_usdt = ta_data['resistance']
    atr_usdt = ta_data['atr']

    if pd.isna(support_usdt) or pd.isna(resistance_usdt) or pd.isna(atr_usdt):
        return None

    # مسافة المقاومة والدعم كنسب مئوية
    dist_to_support_pct = (close_usdt - support_usdt) / close_usdt
    atr_pct = atr_usdt / close_usdt

    if resistance_usdt > close_usdt:
        dist_to_res_pct = (resistance_usdt - close_usdt) / close_usdt
    else:
        # في حال الاختراق السعري للقمة السابقة، يوضع هدف توسعي
        dist_to_res_pct = atr_pct * 2.0

    # الأسعار المحلية على Paribu
    ask_try = paribu_data['ask']
    bid_try = paribu_data['bid']
    if ask_try <= 0 or bid_try <= 0:
        return None

    spread_pct = (ask_try - bid_try) / bid_try

    # 🛑 الوقف (SL): أسفل الدعم الفعلي + مسافة أمان نصف ATR
    sl_try = ask_try * (1 - dist_to_support_pct - (atr_pct * 0.5))

    # 🎯 الهدف (TP1): قبل المقاومة الفعلية بمسافة ربع ATR لضمان التنفيذ
    tp1_try = ask_try * (1 + dist_to_res_pct - (atr_pct * 0.25))

    # 💰 الحسابات المالية الدقيقة (رسوم دخول وخروج + انزلاق)
    total_fees_and_slippage = (PARIBU_FEE_RATE * 2) + EXPECTED_SLIPPAGE

    gross_profit_pct = (tp1_try - ask_try) / ask_try
    net_profit_pct = gross_profit_pct - total_fees_and_slippage

    risk_pct = ((ask_try - sl_try) / ask_try) + total_fees_and_slippage

    # 📐 R:R الحقيقي
    real_rr = net_profit_pct / risk_pct if risk_pct > 0 else 0

    # ------------------------------------------
    # 🛡️ الفلاتر الأساسية لضبط الجودة
    # ------------------------------------------
    if close_usdt < ta_data['ema21'] or dist_to_res_pct < 0.01:
        return None

    if net_profit_pct < MIN_NET_PROFIT or real_rr < MIN_REAL_RR:
        return None

    # ------------------------------------------
    # 🏆 نظام النقاط والعقوبات (Scoring System)
    # ------------------------------------------
    score = 80.0
    penalties = []

    # 1. عقوبة التشبع الشرائي (RSI)
    rsi_val = ta_data['rsi']
    if rsi_val > 65:
        pen = (rsi_val - 65) * 1.5
        score -= pen
        penalties.append(f"RSI مرتفع (-{pen:.1f})")

    # 2. عقوبة ضعف السيولة (Volume)
    vol_mult = ta_data['vol_multiplier']
    if vol_mult < 1.0:
        pen = (1.0 - vol_mult) * 30
        score -= pen
        penalties.append(f"سيولة ضعيفة (-{pen:.1f})")

    # 3. عقوبة اتساع السبريد في Paribu
    if spread_pct > 0.004:
        pen = (spread_pct - 0.004) * 10000
        score -= pen
        penalties.append(f"سبريد عالي (-{pen:.1f})")

    # 🌟 مكافآت الجودة
    if real_rr >= 2.0:
        score += 10
    if ta_data['macd'] > ta_data['macd_signal']:
        score += 5

    score = max(0.0, min(100.0, score))

    if score < MIN_SCORE_THRESHOLD:
        return None

    if score >= 90:
        strength = "🔥 EXCEPTIONAL"
    elif score >= 80:
        strength = "🟢 STRONG"
    else:
        strength = "🟡 GOOD"

    return {
        'coin': f"{coin}_TL",
        'score': score,
        'strength': strength,
        'ask': ask_try,
        'resistance_try': ask_try * (1 + dist_to_res_pct),
        'tp1': tp1_try,
        'sl': sl_try,
        'net_profit': net_profit_pct,
        'real_rr': real_rr,
        'spread': spread_pct,
        'rsi': rsi_val,
        'vol_multi': vol_mult,
        'penalties': " | ".join(penalties) if penalties else "لا يوجد"
    }

# ==========================================
# 📤 إرسال التنبيه عبر التلغرام
# ==========================================
def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info("ℹ️ لم يتم ضبط بيانات التلغرام في البيئة، سيتم اكتفاء بالطابعة.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            logging.info("✅ تم إرسال التتقرير إلى التلغرام بنجاح.")
        else:
            logging.error(f"❌ فشل إرسال التلغرام: {res.text}")
    except Exception as e:
        logging.error(f"❌ خطأ أثناء الاتصال بالتلغرام: {e}")

# ==========================================
# 🚀 المحرك الرئيسي (Main Scanner)
# ==========================================
def run_scanner():
    logging.info("بدء جلب أسعار Paribu المحلية...")
    paribu_data = fetch_paribu_tickers()
    if not paribu_data:
        logging.error("❌ تعذر الاتصال بـ Paribu.")
        return

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

        time.sleep(0.05)

    # ترتيب الصفقات حسب الـ Score واختيار أفضل TOP 3
    valid_signals = sorted(valid_signals, key=lambda x: x['score'], reverse=True)
    top_signals = valid_signals[:TOP_N_SIGNALS]

    if not top_signals:
        msg = "📭 لا توجد فرص تتوافق مع معايير المقاومات، الرسوم، ونسبة R:R الحقيقية حالياً."
        logging.info(msg)
        return

    msg = f"🔥 <b>Paribu — أفضل {len(top_signals)} فرص Spot</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "🎯 أهداف مبنية على المقاومات الفعلية وصافي الربح بعد الرسوم.\n\n"

    for idx, s in enumerate(top_signals, 1):
        msg += f"🎯 <b>SPOT ENTRY #{idx}</b>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🪙 <b>{s['coin']}</b>\n"
        msg += f"💪 القوة: {s['strength']}\n"
        msg += f"📊 Score: {s['score']:.0f}/100\n"
        msg += f"📉 العقوبات: {s['penalties']}\n\n"

        msg += f"💵 سعر الشراء (Ask): <code>{s['ask']:.6g}</code>\n"
        msg += f"🧱 أقرب مقاومة: <code>{s['resistance_try']:.6g}</code>\n"
        msg += f"🎯 الهدف الفعلي (TP1): <code>{s['tp1']:.6g}</code>\n"
        msg += f"🛑 الوقف (أسفل الدعم): <code>{s['sl']:.6g}</code>\n\n"

        msg += f"📐 R:R الحقيقي: <b>1:{s['real_rr']:.2f}</b>\n"
        msg += f"💰 صافي الربح المتوقع: <b>+{s['net_profit']*100:.2f}%</b>\n"
        msg += f"📏 السبريد: {s['spread']*100:.2f}%\n\n"

        msg += f"📈 RSI: {s['rsi']:.1f}\n"
        msg += f"💧 حجم التداول: {s['vol_multi']:.2f}x\n\n"

    msg += "⚠️ الأهداف ديناميكية وتحترم هيكل السعر والمقاومات اللحظية."

    print(msg)
    send_telegram_message(msg)

if __name__ == "__main__":
    run_scanner()
