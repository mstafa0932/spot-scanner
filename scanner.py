# =========================
# BTC REGIME GATE
# =========================

BTC_15M_EMA_TOLERANCE_PCT = Decimal("0.35")
BTC_1H_EMA_TOLERANCE_PCT = Decimal("0.75")

# هبوط قوي خلال الفترات القصيرة/المتوسطة/الأطول
BTC_MAX_3CANDLE_DROP_PCT = Decimal("-2.00")
BTC_MAX_12CANDLE_DROP_PCT = Decimal("-4.50")
BTC_MAX_48CANDLE_DROP_PCT = Decimal("-8.00")

# لا نسمح بفجوة هبوطية كبيرة عن EMA21
BTC_MAX_15M_EMA21_DISTANCE_BEARISH_PCT = Decimal("-1.00")
BTC_MAX_1H_EMA21_DISTANCE_BEARISH_PCT = Decimal("-2.00")

# RSI وحده لا يكفي لإيقاف السوق.
# نستخدمه فقط عندما يترافق مع ضعف سعري واضح.
BTC_HARD_BEAR_RSI = Decimal("38.00")
BTC_CAUTION_RSI = Decimal("45.00")


def _btc_regime(
    btc_15: IndicatorResult,
    btc_1h: IndicatorResult,
) -> tuple[bool, str]:
    """
    بوابة BTC متكيفة:

    1) توقف كامل عند وجود هبوط سعري قوي.
    2) توقف عند ابتعاد السعر بقوة تحت EMA21.
    3) RSI المنخفض وحده لا يوقف الفحص.
    4) RSI < 38 يوقف فقط إذا ترافق مع ضعف سعري وهيكلي.
    5) الحالة المحايدة/المختلطة تسمح بفحص العملات.
    6) الحالة الصاعدة تمنح السماح الكامل.
    """

    if (
        btc_15.current_close <= 0
        or btc_15.ema21 <= 0
        or btc_1h.current_close <= 0
        or btc_1h.ema21 <= 0
    ):
        return False, "BTC بيانات المؤشر غير صالحة"

    btc_15_ema_distance = pct(
        btc_15.current_close,
        btc_15.ema21,
    )

    btc_1h_ema_distance = pct(
        btc_1h.current_close,
        btc_1h.ema21,
    )

    # ---------------------------------
    # 1) HARD BEARISH: هبوط سعري قوي
    # ---------------------------------

    if btc_15.recent_return_3 <= BTC_MAX_3CANDLE_DROP_PCT:
        return (
            False,
            f"BTC هبوط قوي خلال 3 شموع: "
            f"{btc_15.recent_return_3:.2f}%",
        )

    if btc_15.recent_return_12 <= BTC_MAX_12CANDLE_DROP_PCT:
        return (
            False,
            f"BTC هبوط قوي خلال 12 شمعة: "
            f"{btc_15.recent_return_12:.2f}%",
        )

    if btc_15.recent_return_48 <= BTC_MAX_48CANDLE_DROP_PCT:
        return (
            False,
            f"BTC هبوط قوي خلال 48 شمعة: "
            f"{btc_15.recent_return_48:.2f}%",
        )

    # ---------------------------------
    # 2) HARD BEARISH: كسر EMA21 بقوة
    # ---------------------------------

    if btc_15_ema_distance <= BTC_MAX_15M_EMA21_DISTANCE_BEARISH_PCT:
        return (
            False,
            f"BTC 15m تحت EMA21 بقوة: "
            f"{btc_15_ema_distance:.2f}%",
        )

    if btc_1h_ema_distance <= BTC_MAX_1H_EMA21_DISTANCE_BEARISH_PCT:
        return (
            False,
            f"BTC 1h تحت EMA21 بقوة: "
            f"{btc_1h_ema_distance:.2f}%",
        )

    # ---------------------------------
    # 3) RSI منخفض جدًا + تأكيد هبوطي
    # ---------------------------------
    #
    # لا نوقف السوق بسبب RSI=41 أو RSI=42 وحده.
    # يجب أن يكون RSI منخفضًا جدًا مع:
    # - زخم سلبي قريب
    # - والسعر تحت EMA21
    #

    btc_hard_rsi_bearish = (
        btc_15.rsi14 < BTC_HARD_BEAR_RSI
        and btc_15.recent_return_3 < Decimal("0")
        and btc_15.current_close < btc_15.ema21
    )

    if btc_hard_rsi_bearish:
        return (
            False,
            f"BTC ضعف هبوطي مؤكد: "
            f"RSI={btc_15.rsi14:.1f} | "
            f"3C={btc_15.recent_return_3:.2f}%",
        )

    # ---------------------------------
    # 4) Bullish regime
    # ---------------------------------

    if (
        btc_15.is_uptrend
        and btc_1h.is_uptrend
        and btc_15.current_close >= btc_15.ema21
        and btc_1h.current_close >= btc_1h.ema21
    ):
        return (
            True,
            "BTC bullish — السماح الكامل بالفحص",
        )

    # ---------------------------------
    # 5) Neutral / Mixed regime
    # ---------------------------------
    #
    # يسمح بالفحص طالما لم تظهر علامات هبوط
    # قوية في الفلاتر السابقة.
    #

    within_neutral_tolerance = (
        btc_15_ema_distance >= -BTC_15M_EMA_TOLERANCE_PCT
        and btc_1h_ema_distance >= -BTC_1H_EMA_TOLERANCE_PCT
    )

    if within_neutral_tolerance:
        if btc_15.rsi14 < BTC_CAUTION_RSI:
            return (
                True,
                f"BTC neutral/cautious — "
                f"RSI={btc_15.rsi14:.1f} — الفحص مسموح بحذر",
            )

        return (
            True,
            "BTC neutral/mixed — السماح بالفحص مع حماية",
        )

    # ---------------------------------
    # 6) Mixed but acceptable
    # ---------------------------------
    #
    # حتى لو لم يكن BTC صاعدًا بشكل واضح،
    # نسمح طالما لا توجد بنية هبوطية خطيرة.
    #

    acceptable_mixed_regime = (
        btc_15_ema_distance > BTC_MAX_15M_EMA21_DISTANCE_BEARISH_PCT
        and btc_1h_ema_distance > BTC_MAX_1H_EMA21_DISTANCE_BEARISH_PCT
        and btc_15.rsi14 >= BTC_HARD_BEAR_RSI
    )

    if acceptable_mixed_regime:
        return (
            True,
            f"BTC mixed but acceptable — "
            f"RSI={btc_15.rsi14:.1f}",
        )

    # ---------------------------------
    # 7) Final protection
    # ---------------------------------

    return (
        False,
        "BTC regime ضعيف أكثر من الحد المسموح للحماية",
    )


def btc_gate() -> tuple[bool, Optional[IndicatorResult], str]:
    """
    يقرأ BTC من Paribu فقط، ثم يقرر:
    - Bullish
    - Neutral / Cautious
    - Bearish / Block
    """

    try:
        btc_15_df = fetch_candles(
            "BTC_TL",
            "15m",
            CANDLE_LIMIT,
        )

        btc_1h_df = fetch_candles(
            "BTC_TL",
            "1h",
            CANDLE_LIMIT,
        )

        btc_15 = analyze_symbol(btc_15_df)
        btc_1h = analyze_symbol(btc_1h_df)

        if btc_15 is None or btc_1h is None:
            return (
                False,
                None,
                "BTC indicators unavailable",
            )

        # حماية إضافية: يجب أن تكون البيانات من Paribu
        source_15 = str(
            btc_15_df.attrs.get("source", "")
        ).upper()

        source_1h = str(
            btc_1h_df.attrs.get("source", "")
        ).upper()

        if source_15 != "PARIBU":
            return (
                False,
                None,
                f"BTC 15m مصدر غير موثوق: {source_15}",
            )

        if source_1h != "PARIBU":
            return (
                False,
                None,
                f"BTC 1h مصدر غير موثوق: {source_1h}",
            )

        allowed, reason = _btc_regime(
            btc_15,
            btc_1h,
        )

        return allowed, btc_15, reason

    except Exception as exc:
        return (
            False,
            None,
            f"BTC gate error: {exc}",
        )
