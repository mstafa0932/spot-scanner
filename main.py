from __future__ import annotations

import os
import requests

from scanner import (
    MarketScanner,
    Opportunity,
    ScanStats,
)


TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
)


def send_telegram(
    text: str,
) -> bool:

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "[TELEGRAM ERROR] "
            "Missing Telegram secrets."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        if response.status_code == 200:

            print(
                "[TELEGRAM] Sent."
            )

            return True

        print(
            "[TELEGRAM ERROR]",
            response.status_code,
            response.text[:500],
        )

        return False

    except requests.RequestException as exc:

        print(
            "[TELEGRAM ERROR]",
            exc,
        )

        return False


def format_opportunity(
    opp: Opportunity,
    rank: int,
) -> str:

    return (
        f"🎯 SPOT OPPORTUNITY #{rank}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {opp.symbol}\n"
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
        f"⚠️ Spot فقط — لا يوجد تنفيذ تلقائي."
    )


def format_no_signal_report(
    stats: ScanStats,
) -> str:
    # تنسيق قسم التنبيهات المبكرة إذا كانت متوفرة في كائن الإحصائيات أو المراقبة
    early_alerts_text = ""
    watchlist = getattr(stats, "early_watch_list", [])
    if watchlist:
        early_alerts_text = "\n🔍 **قائمة المراقبة المبكرة:**\n"
        sorted_watchlist = sorted(watchlist, key=lambda x: x.get("score", 0), reverse=True)
        for item in sorted_watchlist[:5]:
            early_alerts_text += f"• `{item['symbol']}` ➔ {item['reason']} (النقاط: {item.get('score', 'N/A')})\n"
        early_alerts_text += "━━━━━━━━━━━━━━━━━━━━\n"

    return (
        "🔍 Paribu — التقرير الدوري\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ البوت يعمل.\n"
        "⚠️ لا توجد توصية دخول مطابقة حاليًا.\n\n"
        f"{early_alerts_text}"
        f"📊 الأزواج المفحوصة: "
        f"{stats.total_markets}\n\n"

        f"💧 اجتازت السيولة: "
        f"{stats.liquidity_pass}\n"
        f"❌ رفض السيولة: "
        f"{stats.liquidity_fail}\n\n"

        f"📏 اجتازت السبريد: "
        f"{stats.spread_pass}\n"
        f"❌ رفض السبريد: "
        f"{stats.spread_fail}\n\n"

        f"🕯️ نجاح بيانات الشموع: "
        f"{stats.candle_success}\n"
        f"❌ أخطاء الشموع: "
        f"{stats.candle_fail}\n\n"

        f"📐 نجاح التحليل الفني: "
        f"{stats.indicator_success}\n"
        f"❌ فشل المؤشرات: "
        f"{stats.indicator_fail}\n\n"

        f"🚫 RSI مرتفع: "
        f"{stats.rsi_fail}\n"
        f"🚫 اتجاه ضعيف: "
        f"{stats.trend_fail}\n"
        f"🚫 Anti-FOMO: "
        f"{stats.fomo_fail}\n"
        f"🚫 تقلب غير مناسب: "
        f"{stats.volatility_fail}\n"
        f"🚫 لا توجد Setup: "
        f"{stats.setup_fail}\n"
        f"🚫 R:R غير مناسب: "
        f"{stats.rr_fail}\n"
        f"🚫 التقييم أقل من الحد: "
        f"{stats.score_fail}\n\n"

        f"🎯 المرشحون قبل الترتيب: "
        f"{stats.candidates_before_rank}\n\n"

        "💡 لا يتم إجبار النظام على فتح توصية؛ "
        "الصمت عن الصفقة أفضل من إشارة ضعيفة."
    )


def main() -> None:

    print(
        "=" * 70
    )

    print(
        "PARIBU SPOT SCANNER"
    )

    print(
        "=" * 70
    )

    scanner = MarketScanner(
        top_n=10
    )

    try:

        opportunities, stats = (
            scanner.scan_market()
        )

    except Exception as exc:

        message = (
            "🚨 SPOT SCANNER ERROR\n\n"
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        print(message)

        send_telegram(message)

        raise

    if not opportunities:

        report = (
            format_no_signal_report(
                stats
            )
        )

        print(report)

        send_telegram(report)

        return

    header = (
        "🔥 Paribu — فرص Spot\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"تم العثور على "
        f"{len(opportunities)} "
        "فرصة مرتبة.\n"
        "الأفضل أولًا:"
    )

    print(header)

    send_telegram(header)

    for rank, opportunity in enumerate(
        opportunities,
        start=1,
    ):

        message = format_opportunity(
            opportunity,
            rank,
        )

        print(
            "\n" + message
        )

        send_telegram(
            message
        )


if __name__ == "__main__":
    main()
