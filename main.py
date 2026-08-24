import os
import requests
from market_data import get_market_data

def send_telegram_message(message):
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')

    if not token or not chat_id:
        print("⚠️ بيانات التيليجرام غير متوفرة.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("✅ تم إرسال التنبيه إلى تيليجرام بنجاح!")
        else:
            print(f"❌ فشل إرسال التيليجرام: {response.text}")
    except Exception as e:
        print(f"❌ خطأ في الاتصال بتيليجرام: {e}")

def evaluate_coin(symbol, price, volume):
    score = 50
    try:
        vol_val = float(volume) if volume else 0
        if vol_val > 1000000:
            score += 25
        elif vol_val > 100000:
            score += 15
        else:
            score += 10
    except:
        score += 10

    score = min(max(score, 0), 100)

    if score >= 75:
        rating = "EXCEPTIONAL (فرصة استثنائية)"
    elif score >= 60:
        rating = "STRONG_ENTRY (دخول قوي)"
    else:
        rating = "GOOD (للمراقبة)"

    return score, rating

def main():
    print("*" * 65)
    print("🚀 بدء تشغيل محرك التداول الاحترافي لـ Paribu...")
    print("*" * 65)

    raw_data = get_market_data()

    if not raw_data:
        print("❌ لم يتم استلام أي بيانات من السوق.")
        return

    markets = []
    if isinstance(raw_data, dict):
        if 'data' in raw_data and isinstance(raw_data['data'], list):
            markets = raw_data['data']
        elif 'ticker' in raw_data and isinstance(raw_data['ticker'], list):
            markets = raw_data['ticker']
        else:
            for k, v in raw_data.items():
                if isinstance(v, dict):
                    v['symbol'] = k
                    markets.append(v)
                else:
                    markets.append({'symbol': k, 'price': v})
    elif isinstance(raw_data, list):
        markets = raw_data

    # ---------------------------------------------------------
    # 1. فلتر أمان البيتكوين (BTC Filter) لحماية رأس المال
    # ---------------------------------------------------------
    btc_percent_change = 0.0
    for m in markets:
        symbol = m.get('symbol', '').upper()
        if symbol == 'BTC_TL' or symbol == 'BTC':
            try:
                # استخراج نسبة التغير اليومية للبيتكوين
                btc_percent_change = float(m.get('percentChange', 0))
            except:
                pass
            break

    # إذا كان البيتكوين هابطاً بأكثر من 3%، نوقف الشراء!
    if btc_percent_change < -3.0:
        warning_msg = (
            "⚠️ تحذير نظام الأمان ⚠️\n\n"
            "البيتكوين يشهد هبوطاً حاداً اليوم (سوق دموي).\n"
            "تم إيقاف التوصيات مؤقتاً لحماية رأس مالك من الانهيار المفاجئ!"
        )
        print("تم تفعيل فلتر الأمان بسبب هبوط البيتكوين.")
        send_telegram_message(warning_msg)
        return  # إيقاف البرنامج هنا وعدم إرسال عملات

    # ---------------------------------------------------------
    # 2. معالجة العملات وإرسال التقرير مع الأهداف
    # ---------------------------------------------------------
    msg = "🤖 تقرير السوق الاحترافي (Paribu - TL)\n\n"
    recommendations_found = False

    for market in markets:
        symbol = market.get('symbol', 'UNKNOWN')
        
        # التركيز على أزواج الليرة التركية فقط وتجاهل العملات الأخرى
        if not symbol.endswith('_TL'):
            continue

        price = market.get('price') or market.get('last')
        volume = market.get('volume') or market.get('volumeQuote')

        if not price:
            continue

        score, rating = evaluate_coin(symbol, price, volume)

        # نعرض فقط العملات ذات الدخول القوي (تقييم 60 فما فوق)
        if score >= 60:
            recommendations_found = True
            try:
                current_price = float(price)
                
                # معادلات الأهداف ووقف الخسارة
                tp1 = current_price * 1.03  # هدف أول (+3%)
                tp2 = current_price * 1.05  # هدف ثاني (+5%)
                sl  = current_price * 0.96  # وقف خسارة (-4%)

                msg += f"⭐ العملة: {symbol}\n"
                msg += f"💰 سعر الدخول: {current_price:.3f} TL\n"
                msg += f"🎯 هدف أول (+3%): {tp1:.3f}\n"
                msg += f"🚀 هدف ثاني (+5%): {tp2:.3f}\n"
                msg += f"🛑 وقف خسارة (-4%): {sl:.3f}\n"
                msg += f"📊 قوة الزخم: {score}/100\n"
                msg += f"➔ {rating}\n"
                msg += f"-----------------------\n"
            except Exception as e:
                continue

    # إرسال الرسالة النهائية
    if recommendations_found:
        send_telegram_message(msg)
    else:
        print("لا توجد فرص قوية حالياً.")

if __name__ == "__main__":
    main()
