from market_data import get_market_data
# استدعاء دالة الفحص من ملف scanner (تأكد من توافق الأسماء لاحقاً حسب محتوى ملفك)

def main():
    print("=" * 60)
    print("🚀 بدء تشغيل ماسح سوق Paribu للعملات الرقمية (Spot)...")
    print("=" * 60)
    
    raw_data = get_market_data()
    
    if not raw_data:
        print("❌ لم يتم استلام أي بيانات من السوق.")
        return

    # معالجة البيانات حسب شكل استجابة Paribu
    markets = []
    if isinstance(raw_data, dict):
        markets = raw_data.get('data', []) or raw_data.get('ticker', []) or list(raw_data.values())
    elif isinstance(raw_data, list):
        markets = raw_data

    print(f"📊 إجمالي الأزواج المتاحة في السوق: عن طريق التحديث النشط")
    print(f"🔍 جاري تحليل وتصفية الفرص الحقيقية...")
    print("-" * 60)

    # نموذج تجريبي لفحص العملات المرتبطة بـ TL (الليرة التركية)
    count = 0
    for item in markets:
        if isinstance(item, dict):
            # محاولة قراءة رمز العملة والسعر
            symbol = item.get('symbol', item.get('code', 'UNKNOWN'))
            last_price = item.get('last', item.get('price', 0))
            
            # التركيز على أزواج الليرة التركية TL
            if 'TL' in str(symbol).upper() or 'TRY' in str(symbol).upper():
                count += 1
                print(f"🔹 زوج العملة: {symbol} | السعر الحالي: {last_price}")
                # هنا سيقوم المحرك بتقييم العملة لاحقاً

    print("-" * 60)
    print(f"✅ تم فحص الأزواج المرتبطة بالليرة التركية بنجاح. العدد الكلي: {count}")
    print("=" * 60)

if __name__ == "__main__":
    main()
