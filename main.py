from __future__ import annotations
import os
import requests
from typing import List
from scanner import MarketScanner, Opportunity

# ============================================================ #
# Telegram Notification & Pipeline Orchestrator                #
# Spot Scanner Project (Advanced Engine)                       #
# ============================================================ #

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def send_telegram_message(message: str) -> bool:
    """إرسال التنبيهات إلى تليجرام مع التأكد من الأمان"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Telegram credentials are missing in Environment Variables.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[+] Telegram alert sent successfully.")
            return True
        print(f"[!] Telegram API error: {response.status_code} - {response.text}")
        return False
    except requests.RequestException as exc:
        print(f"[!] Telegram connection error: {exc}")
        return False

def format_opportunity_message(opp: Opportunity, rank: int) -> str:
    """تنسيق رسالة الفرصة بشكل احترافي مع تمييز الإشارات الذهبية"""
    header = "🌟 *SUPER SPOT SIGNAL* 🌟" if opp.is_super_signal else "🔥 *SPOT ENTRY SIGNAL*"
    
    return (
        f"{header} *#{rank}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔹 *Symbol:* `{opp.symbol}`\n"
        f"📊 *Score:* `{opp.score}/100`\n"
        f"💡 *Reason:* {opp.reason}\n\n"
        f"💵 *Entry:* `{opp.entry_price}`\n"
        f"🛑 *Stop Loss:* `{opp.stop_loss}`\n"
        f"🎯 *TP1:* `{opp.tp_1}`\n"
        f"🚀 *TP2:* `{opp.tp_2}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *Spot Trading — Strict Filters Active*"
    )

def run_pipeline() -> None:
    """المحرك الرئيسي لتشغيل البوت وإرسال النتائج"""
    print("=" * 60)
    print("STARTING ADVANCED SPOT SCANNER PIPELINE")
    print("=" * 60)

    # طلب فحص أفضل 10 فرص كحد أقصى
    scanner = MarketScanner(top_n=10)

    try:
        opportunities: List[Opportunity] = scanner.scan_market()
    except Exception as exc:
        print(f"[!] Error during market scan: {exc}")
        return

    print("\n" + "=" * 60)
    print("SCAN COMPLETE")
    print("=" * 60)

    # حالة عدم وجود فرص تطابق الفلاتر الصارمة
    if not opportunities:
        print("[INFO] No qualified opportunities found during this run.")
        summary_msg = (
            "🔍 *Spot Market Scan Complete*\n\n"
            "No opportunities passed the strict institutional filters during this run."
        )
        send_telegram_message(summary_msg)
        return

    # حالة العثور على فرص مطابقة
    print(f"[+] Found {len(opportunities)} qualified opportunity(ies).")
    for rank, opp in enumerate(opportunities, 1):
        message = format_opportunity_message(opp, rank)
        print("\n" + message)
        print("-" * 40)
        send_telegram_message(message)

if __name__ == "__main__":
    run_pipeline()
