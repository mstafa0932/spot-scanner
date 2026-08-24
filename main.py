from market_data import get_market_data

def main():
    print("=" * 50)
    print("جاري الاتصال بسوق Paribu وجلب بيانات العملات...")
    print("=" * 50)
    
    data = get_market_data()
    
    if data:
        print("✅ تم بنجاح استلام البيانات من السوق!")
        # طباعة عدد الأزواج المتاحة كبداية للتأكد
        if isinstance(data, list):
            print(f"📊 عدد أزواج العملات المتاحة: {len(data)}")
        else:
            print("📦 نوع البيانات المستلمة:", type(data))
    else:
        print("❌ لم يتم استلام أي بيانات، تحقق من الاتصال.")

    print("=" * 50)

if __name__ == "__main__":
    main()
