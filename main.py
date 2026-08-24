import os
import requests
from market_data import get_market_data

def send_telegram_message(message):
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def calculate_dynamic_targets(entry_price, high_24h, low_24h):
    """
    حساب الأهداف ووقف الخسارة برمجياً بناءً على التذبذب اليومي (Volatility)
    وليس مجرد نسب ثابتة عمياء.
    """
    daily_range = high_24h - low_24h
    if daily_range <= 0 or daily_range > (entry_price * 0.5): 
        # حماية من البيانات الخاطئة، استخدام نسب افتراضية ذكية
        return entry_price * 1.03, entry_price * 1.06, entry_price * 0.95
    
    # تحديد الأهداف بناءً على قوة المدى اليومي للعملة
    tp1 = entry_price + (daily_range * 0.35)
    tp2 = entry_price + (daily_range * 0.65)
    sl = entry_price - (daily_range * 0.30)
    return tp1, tp2, sl

def advanced_evaluate_coin(price, high, low, volume, percent_change):
    """
    محرك التقييم الاحترافي (Scoring Engine 2.0)
    يحلل: السيولة + مسار الشمعة + التذبذب + التشبع الشرائي
    """
    score = 0.0
    
    # 1. تحليل السيولة (Volume) - أقصى نقطة 30
    if volume > 5000000: score += 30
    elif volume > 1000000: score += 20
    elif volume > 250000: score += 10
    
    # 2. تحليل شكل الشمعة والزخم (Price Action) - أقصى نقطة 40
    # أين يقع السعر الحالي بالنسبة للقمة والقاع؟ (1.0 يعني عند القمة تماماً)
    daily_range = high - low
    if daily_range > 0:
        candle_position = (price - low) / daily_range
        if candle_position >= 0.8: score += 40      # اختراق قوي للقمم
        elif candle_position >= 0.6: score += 25    # إيجابية ممتازة
        elif candle_position >= 0.4: score += 10    # تجميع في المنتصف
        else: score -= 15                           # ضعف بيعي
        
    # 3. تحليل الاتجاه وتجنب التشبع (Trend & Overbought) - أقصى نقطة 30
    if 2.0 <= percent_change <= 8.0:
        score += 30 # صعود صحي وفي بداية الترند
    elif 8.0 < percent_change <= 15.0:
        score += 15 # صعود قوي (يجب الحذر قليلاً)
    elif percent_change > 15.0:
        score -= 20 # خطر التعلق في القمة (تشبع شرائي)
    elif percent_change < 0:
        score -= 30 # مسار هابط، نبتعد عنه في المضاربة
        
    # ضبط النتيجة لتكون من 100
    score = min(max(score, 0), 100)
    
    if score >= 85: rating = "💎 EXCEPTIONAL (اختراق ذهبي)"
    elif score >= 70: rating = "🔥 STRONG_ENTRY (جاهزة للانطلاق)"
    else: rating = "👀 MONITOR (تحت المراقبة)"
        
    return score, rating

def main():
    raw_data = get_market_data()
    if not raw_data:
        return

    # توحيد شكل البيانات القادمة من Paribu
    markets = []
    if isinstance(raw_data, dict):
        if 'data' in raw_data: markets = raw_data['data']
        elif 'ticker' in raw_data: markets = raw_data['ticker']
        else:
            for k, v in raw_data.items():
                if isinstance(v, dict):
                    v['symbol'] = k
                    markets.append(v)
    elif isinstance(raw_data, list):
        markets = raw_data

    # --- فلتر حماية رأس المال بناءً على البيتكوين ---
    for m in markets:
        sym = m.get('symbol', '').upper()
        if sym in ['BTC_TL', 'BTC']:
            try:
                btc_change = float(m.get('percentChange', 0))
                if btc_change <= -4.0:
                    send_telegram_message("🚨 **تنبيه أمني:** انهيار في البيتكوين! تم إيقاف الصفقات اللحظية لحماية رأس المال.")
                    return
            except: pass
            break

    # --- مسح السوق واستخراج الفرص الذهبية ---
    msg = "📊 **رادار السيولة المتقدم (الذكاء الاصطناعي)**\n\n"
    found = False

    for market in markets:
        symbol = market.get('symbol', 'UNKNOWN')
        if not symbol.endswith('_TL'):
            continue

        try:
            # استخراج أدق التفاصيل من API بمرونة عالية
            price = float(market.get('last') or market.get('price', 0))
            high = float(market.get('high24hr') or market.get('high', price))
            low = float(market.get('low24hr') or market.get('low', price))
            volume = float(market.get('volumeQuote') or market.get('volume', 0))
            change = float(market.get('percentChange', 0))

            if price <= 0: continue

            # إرسال البيانات للمحرك الاحترافي
            score, rating = advanced_evaluate_coin(price, high, low, volume, change)

            # تصفية قاسية: نأخذ فقط الفرص القوية جداً (70 فأكثر)
            if score >= 70:
                found = True
                tp1, tp2, sl = calculate_dynamic_targets(price, high, low)
                
                msg += f"العملة: #{symbol}\n"
                msg += f"التقييم: {rating} ({int(score)}/100)\n"
                msg += f"💰 الدخول: {price:.4f} ₺\n"
                msg += f"🎯 هدف 1: {tp1:.4f} ₺\n"
                msg += f"🚀 هدف 2: {tp2:.4f} ₺\n"
                msg += f"🛑 الوقف: {sl:.4f} ₺\n"
                msg += f"📈 التغير: +{change:.2f}%\n"
                msg += "━━━━━━━━━━━━━━\n"
        except Exception:
            continue

    if found:
        send_telegram_message(msg)

if __name__ == "__main__":
    main()
