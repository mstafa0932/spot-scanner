from __future__ import annotations

"""Single production entry point for the Paribu Spot Sniper + AI Manager."""

import os

from scanner import run_scanner
from ai_manager import ask_manager


if __name__ == "__main__":
    print("🚀 MAIN: started", flush=True)

    print("🔎 MAIN: starting scanner...", flush=True)
    run_scanner()
    print("✅ MAIN: scanner finished", flush=True)

    print("🤖 MAIN: forcing AI Manager test...", flush=True)

    try:
        answer = ask_manager(
            "أنت الآن مدير تداول ذكي. "
            "أعطني اختبار اتصال قصير يؤكد أنك تعمل."
        )

        print("✅ MAIN: AI Manager returned successfully", flush=True)
        print("🤖 AI Manager:", flush=True)
        print(answer, flush=True)

    except Exception as e:
        print(f"❌ MAIN: AI Manager error: {type(e).__name__}: {e}", flush=True)
        raise

    print("🏁 MAIN: finished", flush=True)
