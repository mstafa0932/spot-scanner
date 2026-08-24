from market_data import get_market_data

def evaluate_coin(symbol, price, volume):
    score = 50  # النقطة الأساسية
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
        rating = "EXCEPTIONAL 🚀 (فرصة استثنائية)"
    elif score >= 60:
        rating = "STRONG_ENTRY 🔥 (دخول قوي)"
    else:
        rating = "GOOD 📈 (مستقرة للمراقبة)"
        
    return score, rating

def main():
    print("=" * 65)
    print("🚀 بدء تشغيل محرك تقييم العملات الرقمية (نظام الـ 100 نقطة)...")
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
            # تحويل مفاتيح القاموس إلى رموز
            for k, v in raw_data.items():
                if isinstance(v, dict):
                    v['symbol'] = k
                    markets.append(v)
                else:
                    markets.append({'symbol': k, 'price': v})
    elif isinstance(raw_data, list):
        markets = raw_data

    print(f"📊 إجمالي الأزواج المتاحة في السوق: {len(markets)}")
    print("🔍 جاري تصفية وتقييم أزواج الليرة التركية (TL/TRY)...")
    print("-" * 65)

    count = 0
    for item in markets:
        if isinstance(item, dict):
            symbol = item.get('symbol', item.get('code', item.get('name', 'UNKNOWN')))
            sym_upper = str(symbol).upper()
            
            # البحث عن أزواج الليرة التركية
            if 'TL' in sym_upper or 'TRY' in sym_upper:
                count += 1
                price = item.get('last', item.get('price', item.get('bid', 0)))
                volume = item.get('volume', item.get('v', 0))
                
                score, rating = evaluate_coin(symbol, price, volume)
                
                print(f"🌟 العملة: {symbol} | السعر: {price}")
                print(f"   التقييم: {score}/100 ➔ {rating}")
                print("-" * 45)

    print("-" * 65)
    print(f"✅ انتهى الفحص. إجمالي أزواج الليرة التركية المكتشفة: {count}")
    print("=" * 65)

if __name__ == "__main__":
    main()
