from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import List

import requests

from scanner import MarketScanner, Opportunity


# ============================================================
# Main Orchestrator & Notification Engine
# Spot Scanner project
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send_telegram_message(text: str) -> None:
    """Send alert message via Telegram Bot if credentials are configured."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Telegram credentials not found. Printing to console only.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"[!] Telegram API Error: {response.text}")
        else:
            print("[+] Alert successfully sent to Telegram.")
    except Exception as exc:
        print(f"[!] Failed to send Telegram notification: {exc}")


def format_opportunity_message(opportunities: List[Opportunity]) -> str:
    """Format the top opportunities into a clean, professional Telegram message."""
    if not opportunities:
        return "🔍 *Spot Market Scan Complete*\n\nNo opportunities passed the strict hard filters during this run."

    lines = ["🚀 *Paribu Spot Scanner - Top Opportunities* 🚀\n"]
    
    for i, opp in enumerate(opportunities, 1):
        lines.append(f"*{i}. Symbol:* `{opp.symbol}` (Score: *{opp.score}/100*)")
        lines.append(f"   • *Current Price:* `{opp.current_price}`")
        lines.append(f"   • *Entry:* `{opp.entry_price}`")
        lines.append(f"   • *Stop Loss:* `{opp.stop_loss}`")
        lines.append(f"   • *TP 1 (R:R 1.5):* `{opp.tp_1}`")
        lines.append(f"   • *TP 2 (R:R 2.5):* `{opp.tp_2}`")
        lines.append(f"   • *Reason:* {opp.reason}")
        lines.append("")

    lines.append(f"🕒 *Scan Time (UTC):* `{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}`")
    return "\n".join(lines)


def main() -> None:
    print("=" * 70)
    print("STARTING SPOT SCANNER PIPELINE")
    print("=" * 70)

    # Initialize scanner for top 3 opportunities
    scanner = MarketScanner(top_n=3)
    
    try:
        top_opportunities = scanner.scan_market()
    except Exception as exc:
        print(f"[!] Error during market scan: {exc}")
        return

    # Format message
    message = format_opportunity_message(top_opportunities)

    # Output to console
    print("\n" + "=" * 50)
    print("GENERATED REPORT:")
    print("=" * 50)
    print(message)
    print("=" * 50 + "\n")

    # Send notification
    send_telegram_message(message)


if __name__ == "__main__":
    main()
