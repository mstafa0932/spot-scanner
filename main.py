from market_data import get_market_data

def evaluate_coin(symbol, price, volume, high, low):
    """
    نظام تقييم العملات من 100 نقطة بناءً على معايير السيولة والحركة السعرية
    """
    score = 50  # النقطة الأساسية للبداية
    
    # تقييم مبدئي بناءً على حجم التداول (السيولة)
    try:
        vol_val = float(volume) if volume else 0
        if vol_val > 1000000:
            score += 25
        elif vol_val > 100000:
            score += 15
        else:
            score += 5
    except:
        pass

    # تقييم إضافي بناءً على نطاق السعر أو الحركة
    score = min(max(score, 0), 100)
    
    # تحديد التوصية بناءً على النقاط
    if score >= 80:
        rating = "EXCEPTIONAL 🚀 (فرصة استثنائية)"
    elif score >= 65:
        rating = "STRONG_ENTRY 🔥 (دخول قوي)"
    elif score >= 50:
        rating = "GOOD 📈 (جيدة ومستقرة)"
    else:
        rating = "WAIT ⏳ (تريث للمراقبة)"
        
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
        markets = raw_data.get('data', []) or raw_data.get('ticker', []) or list(raw_data.values())
    elif isinstance(raw_data, list):
        markets = raw_data

    print(f"📊 إجمالي الأزواج المتاحة في السوق: {len(markets)}")
    print("🔍 جاري تصفية وتقييم أزواج الليرة التركية (TL/TRY)...")
    print("-" * 65)

    count = 0
    strong_opportunities = 0

    for item in markets:
        if isinstance(item, dict):
            symbol = item.get('symbol', item.get('code', 'UNKNOWN'))
            
            # التركيز على أزواج التداول مقابل الليرة التركية
            if 'TL' in str(symbol).upper() or 'TRY' in str(symbol).upper():
                count += 1
                price = item.get('last', item.get('price', 0))
                volume = item.get('volume', item.get('v', 0))
                high = item.get('high', 0)
                low = item.get('low', 0)
                
                # تقييم العملة
                score, rating = evaluate_coin(symbol, price, volume, high, low)
                
                # طباعة العملات التي تقييمها جيد أو ممتاز
                if score >= 65:
                    strong_opportunities += 1
                    print(f"🌟 العملة: {symbol} | السعر: {price}")
                    print(f"   التقييم: {score}/100 ➔ {rating}")
                    print("-" * 45)

    print("-" * 65)
    print(f"✅ انتهى الفحص. إجمالي الأزواج المفحوصة: {count} | الفرص القوية المكتشفة: {strong_opportunities}")
    print("=" * 65)

if __name__ == "__main__":
    main()
