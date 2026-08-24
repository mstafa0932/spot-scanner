import os
import requests
from market_data import get_market_data

def send_telegram_message(message):
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    
    if not token or not chat_id:
        print("⚠️ بيانات تليجرام غير متوفرة.")
        return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("✅ تم إرسال التنبيه إلى تليجرام بنجاح!")
        else:
            print(f"❌ فشل إرسال تليجرام: {response.text}")
    except Exception as e:
        print(f"❌ خطأ في الاتصال بتليجرام: {e}")

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
        rating = "GOOD (مستقرة للمراقبة)"
        
    return score, rating

def main():
    print("=" * 65)
    print("🚀 بدء تشغيل محرك تقييم العملات الرقمية وتليجرام...")
    print("=" * 65)
    
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

    report_lines = ["🤖 تقرير فحص سوق Paribu (الليرة التركية)\n"]
    count = 0
    strong_count = 0

    for item in markets:
        if isinstance(item, dict):
            symbol = item.get('symbol', item.get('code', item.get('name', 'UNKNOWN')))
            sym_upper = str(symbol).upper()
            
            if 'TL' in sym_upper or 'TRY' in sym_upper:
                count += 1
                price = item.get('last', item.get('price', item.get('bid', 0)))
                volume = item.get('volume', item.get('v', 0))
                
                score, rating = evaluate_coin(symbol, price, volume)
                
                if score >= 60:
                    strong_count += 1
                    line = f"🌟 العملة: {symbol}\n💰 السعر: {price}\n📊 التقييم: {score}/100\n➔ {rating}\n-------------------\n"
                    report_lines.append(line)

    report_text = "\n".join(report_lines)
    if len(report_text) > 4000:
        report_text = report_text[:4000] + "\n... (تم اختصار التقرير لطوله)"

    print(f"📊 إجمالي الأزواج المفحوصة: {count} | الفرص القوية المرسلة: {strong_count}")
    
    send_telegram_message(report_text)
    print("=" * 65)

if __name__ == "__main__":
    main()
