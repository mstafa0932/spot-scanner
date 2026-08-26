import os
import requests
from scanner import MarketScanner

def send_telegram_msg(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
    except Exception as e:
        print(f"[!] Failed to send Telegram notification: {e}")

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[!] Telegram credentials missing in environment variables.")
        return

    print("[*] Starting Paribu Spot Scanner Pipeline...")
    scanner = MarketScanner(top_n=3)
    opportunities, stats = scanner.scan_market()

    if opportunities:
        message = "🚨 *فرصة تداول جديدة على Paribu* 🚨\n\n"
        for opp in opportunities:
            message += (
                f"📌 *العملة:* `{opp.symbol}`\n"
                f"🎯 *النقاط:* {opp.score}/100\n"
                f"💡 *السبب:* {opp.reason}\n"
                f"💵 *سعر الدخول:* `{opp.entry_price}` TL\n"
                f"🛑 *وقف الخسارة:* `{opp.stop_loss}` TL\n"
                f"🎯 *الهدف الأول:* `{opp.tp_1}` TL\n"
                f"🚀 *الهدف الثاني:* `{opp.tp_2}` TL\n"
                f"-----------------------------------\n"
            )
    else:
        message = (
            "🔍 *تقرير الفحص الدوري - Paribu*\n\n"
            "✅ *حالة النظام:* البوت يعمل بنجاح.\n"
            "⚠️ *النتيجة:* لا توجد صفقات مطابقة للشروط حالياً.\n\n"
            "📊 *تفاصيل الفحص:*\n"
            f"• الأزواج الممسوحة: `{stats['total']}`\n"
            f"• اجتازت السيولة والـ Spread: `{stats['liquidity_pass']}`\n"
            f"• اكملت تحليل المؤشرات: `{stats['valid_candles']}`\n"
            f"• حققت تقييم الدخول (65+ نقطة): `{stats['passed_score']}`\n\n"
            "💡 *السبب:* لم تتجاوز أي عملة التقييم المطلوب (65/100) لحماية رأس المال."
        )

    send_telegram_msg(token, chat_id, message)
    print("[+] Telegram notification sent successfully.")

if __name__ == "__main__":
    main()
