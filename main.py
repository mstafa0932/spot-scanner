from __future__ import annotations

"""Single production entry point for the Paribu Spot Sniper + AI Manager."""

import os

from scanner import run_scanner
from ai_manager import ask_manager


if __name__ == "__main__":
    # 1. تشغيل الـScanner كما هو
    run_scanner()

    # 2. اختبار المدير الذكي فقط إذا كان مفعلاً
    if os.getenv("RUN_AI_MANAGER_TEST", "false").lower() == "true":
        answer = ask_manager(
            "أنت الآن تعمل كمدير أعمال ذكي. "
            "أعطني رسالة اختبار قصيرة تؤكد أنك تعمل، "
            "واذكر أنك لن تنفذ أي قرار مالي تلقائياً."
        )
        print("\n🤖 AI Manager:")
        print(answer)
