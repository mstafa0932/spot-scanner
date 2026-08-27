from __future__ import annotations

import os
import html
import requests

from scanner import (
    MarketScanner,
    ScanStats,
    Opportunity,
)


def send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[TELEGRAM ERROR] Missing bot token or chat ID.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        print(f"[TELEGRAM ERROR] Failed to send message: {exc}")


def format_opportunity(opp: Opportunity, rank: int) -> str:
    # استخدام القيمة المخزنة مباشرة في كلاس الفرصة مع ضمان السلامة
    source_name = getattr(opp, "data_source", "BINANCE").upper()

    return (
        f"🎯 <b>SPOT OPPORTUNITY #{rank}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{opp.symbol}</b>\n"
        f"📡 مصدر الشموع: {source_name}\n"
        f"💪 القوة: {opp.strength}\n"
        f"📊 التقييم: {opp.score}/100\n"
        f"🧩 النوع: {opp.setup}\n\n"
        f"💵 الدخول: {opp.entry_price}\n"
        f"🛑 وقف الخسارة: {opp.stop_loss}\n"
        f"🎯 TP1: {opp.tp_1}\n"
        f"🚀 TP2: {opp.tp_2}\n"
        f"📐 R:R: 1:{opp.rr}\n\n"
        f"📈 RSI: {opp.rsi}\n"
        f"📊 ATR: {opp.atr_percent}%\n"
        f"💧 الحجم: {opp.volume_ratio}x المتوسط\n"
        f"🧠 السبب: {opp.reason}\n\n"
        "⚠️ Spot فقط — لا يوجد تنفيذ تلقائي."
    )


def format_no_signal_report(stats: ScanStats) -> str:
    return (
        "🔍 <b>Paribu — تقرير فحص السوق الدوري</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 إجمالي الأزواج: {stats.total_markets}\n"
        f"💧 اجتازت السيولة: {stats.liquidity_pass}\n"
        f"⚙️ الفحص الفني: {stats.technical_attempted}\n"
        f"🟢 نجاح الشموع: {stats.candle_success} | ❌ خطأ الشموع: {stats.candle_fail}\n"
        f"📉 فشل الترند: {stats.trend_fail}\n"
        f"📈 فشل RSI: {stats.rsi_fail}\n"
        f"🏃 فشل FOMO: {stats.fomo_fail}\n"
        f"🌊 فشل التقلب: {stats.volatility_fail}\n"
        f"📐 فشل الأهداف/RR: {stats.rr_fail}\n"
        f"⭐ فشل النقاط: {stats.score_fail}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 النتيجة: تم فحص السوق كاملاً، ولا توجد فرص مطابقة للشروط حالياً."
    )


def main():
    scanner = MarketScanner()

    try:
        opportunities, stats = scanner.scan_market()

        if opportunities:
            header = (
                "🔥 <b>Paribu — فرص Spot</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"تم العثور على {len(opportunities)} فرصة مرتبة.\n"
                "الأفضل أولًا:"
            )

            print(header)
            send_telegram(header)

            for rank, opportunity in enumerate(opportunities, start=1):
                message = format_opportunity(opportunity, rank)
                print("\n" + message)
                send_telegram(message)
        else:
            report = format_no_signal_report(stats)
            print(report)
            send_telegram(report)

    except Exception as exc:
        # استخدام html.escape لتجنب انهيار إرسال التنبيه عند وجود رموز خاصة في رسالة الخطأ
        safe_error = html.escape(str(exc))
        error_msg = f"⚠️ <b>[CRITICAL ERROR]</b> توقف ماسح السوق بسبب استثناء غير معالج:\n<code>{safe_error}</code>"
        print(error_msg)
        send_telegram(error_msg)


if __name__ == "__main__":
    main()
