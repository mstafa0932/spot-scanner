from __future__ import annotations

import os
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
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        print(f"[TELEGRAM ERROR] Failed to send message: {exc}")


def format_opportunity(opp: Opportunity, rank: int) -> str:
    return (
        f"🎯 **SPOT OPPORTUNITY #{rank}**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 **{opp.symbol}**\n"
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
        "🔍 **Paribu — تقرير فحص السوق الدوري**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 إجمالي الأزواج المنسوخة: {stats.total_markets}\n"
        f"💧 تجاوزت شرط السيولة: {stats.liquidity_pass}\n"
        f"⚙️ تم فحصها فنياً: {stats.technical_attempted}\n"
        f"📉 استُبعدت لضعف الترند: {stats.trend_fail}\n"
        f"⚠️ استُبعدت لعدم استيفاء النقاط/الهدف: {stats.score_fail + stats.rr_fail}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 النتيجة: تم فحص السوق كاملاً بنجاح، ولا توجد فرص تنطبق عليها الشروط الصارمة حالياً."
    )


def main():
    scanner = MarketScanner()
    opportunities, stats = scanner.scan_market()

    if opportunities:
        header = (
            "🔥 **Paribu — فرص Spot**\n"
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


if __name__ == "__main__":
    main()
