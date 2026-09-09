from __future__ import annotations

"""Production entry point for Paribu Scanner + optional AI Manager."""

import os

from scanner import run_scanner


def main() -> None:
    print("🚀 MAIN: started")

    # ---------------- Scanner ----------------
    print("🔎 MAIN: starting scanner...")

    run_scanner()

    print("✅ MAIN: scanner finished")

    # ---------------- AI Manager ----------------
    # AI failure must NEVER stop the scanner.
    run_ai_test = (
        os.getenv("RUN_AI_MANAGER_TEST", "false")
        .strip()
        .lower()
        == "true"
    )

    if not run_ai_test:
        print("ℹ️ MAIN: AI Manager test disabled")
        return

    print("🧠 MAIN: starting AI Manager test...")

    try:
        from ai_manager import ask_manager

        answer = ask_manager(
            "أنت المدير الذكي للنظام. "
            "أكد باختصار أنك تعمل وأنك لن تنفذ "
            "أي قرار مالي أو تداول تلقائياً."
        )

        print("🤖 AI Manager:")
        print(answer)

    except Exception as exc:
        # مهم:
        # عطل OpenAI أو نفاد الرصيد لا يجب أن يعطل Scanner.
        print(
            "⚠️ MAIN: AI Manager unavailable, "
            "but Scanner remains operational."
        )
        print(
            f"⚠️ AI Manager error: "
            f"{type(exc).__name__}: {exc}"
        )


if __name__ == "__main__":
    main()
