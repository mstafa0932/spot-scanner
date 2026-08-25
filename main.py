from __future__ import annotations

import requests
from typing import List

from scanner import MarketScanner, Opportunity

# ============================================================
# Telegram Notification & Pipeline Orchestrator
# Spot Scanner project
# ============================================================

# بيانات تليجرام الخاصة بك (مدمجة وجاهزة)
TELEGRAM_BOT_TOKEN = "8857594281:AAFobeDoL90hynOWLwPuFR9S1Y7WSkOcQc"
TELEGRAM_CHAT_ID = "306099591"


def send_telegram_message(message: str) -> None:
    """
    Sends a formatted Markdown message to Telegram chat via Bot API.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Telegram credentials are missing.")
        return

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
        else:
            print(f"[!] Telegram API error: {response.status_code} - {response.text}")
    except Exception as exc:
        print(f"[!] Exception sending Telegram message: {exc}")


def format_opportunity_message(opp: Opportunity, rank: int) -> str:
    """
    Formats a single Opportunity object into a clean Telegram Markdown message.
    """
    msg = (
        f"🎯 *SPOT OPPORTUNITY #{rank}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔹 *Symbol:* `{opp.symbol}`\n"
        f"📊 *Score:* `{opp.score}/100`\n"
        f"💡 *Reason:* {opp.reason}\n\n"
        f"💵 *Entry Price:* `{opp.entry_price}`\n"
        f"🛑 *Stop Loss:* `{opp.stop_loss}`\n"
        f"🎯 *Target 1 (TP1):* `{opp.tp_1}`\n"
        f"🚀 *Target 2 (TP2):* `{opp.tp_2}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    return msg


def run_pipeline() -> None:
    print("=" * 60)
    print("STARTING SPOT SCANNER PIPELINE")
    print("=" * 60)

    # تشغيل الفحص للبحث عن أفضل 3 فرص
    scanner = MarketScanner(top_n=3)
    try:
        opportunities: List[Opportunity] = scanner.scan_market()
    except Exception as e:
        print(f"[!] Error during market scan: {e}")
        return

    print("\n" + "=" * 60)
    print("GENERATED REPORT:")
    print("=" * 60)

    if not opportunities:
        summary_msg = "🔍 *Spot Market Scan Complete*\n\nNo opportunities passed the strict hard filters during this run. The market might be overbought or lacking momentum."
        print(summary_msg)
        send_telegram_message(summary_msg)
    else:
        intro_msg = f"🔍 *Spot Market Scan Complete*\nFound *{len(opportunities)}* high-probability opportunity(ies):"
        print(intro_msg)
        send_telegram_message(intro_msg)

        for rank, opp in enumerate(opportunities, 1):
            formatted_msg = format_opportunity_message(opp, rank)
            print(formatted_msg)
            print("-" * 40)
            send_telegram_message(formatted_msg)


if __name__ == "__main__":
    run_pipeline()
