import requests

def get_market_data():
    # وضع معلومات متصفح وهمي لمنع الحظر من منصة Paribu
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    url = "https://www.paribu.com/ticker"
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        print(f"📡 حالة الاتصال بـ Paribu: {response.status_code}")
        
        if response.status_code == 200:
            try:
                return response.json()
            except Exception as json_err:
                print(f"❌ الخطأ في قراءة بيانات الـ JSON: {json_err}")
                print(f"📄 جزء من النص المستلم: {response.text[:300]}")
                return None
        else:
            print(f"❌ المنصة استجابت بكود خطأ: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ حدث استثناء أثناء الاتصال: {e}")
        return None
