def format_no_signal_report(stats) -> str:
    return (
        "🔍 Paribu — تقرير فحص السوق الدوري\n"
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

    # إذا وُجدت فرص، يتم إرسال تفاصيل كل فرصة
    if opportunities:
        header = (
            "🔥 Paribu — فرص Spot\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"تم العثور على {len(opportunities)} فرصة مرتبة.\n"
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
            print("\n" + message)
            send_telegram(message)

    # إذا لم توجد أي فرصة، يتم إرسال تقرير ملخص الفحص الدوري
    else:
        report = format_no_signal_report(stats)
        print(report)
        send_telegram(report)


if __name__ == "__main__":
    main()
