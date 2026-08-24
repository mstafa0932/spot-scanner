"""
Spot Scanner - المرحلة الأولى
محرك تقييم فرص Spot من 100 نقطة.

مهم:
- لا ينفذ أي شراء أو بيع.
- لا يحتاج Paribu API Key.
- لا يحتوي على أي أسرار.
- هذه المرحلة مخصصة لبناء واختبار قواعد التقييم.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketData:
    # BTC / السوق العام
    btc_4h_score: float
    btc_1h_score: float
    market_score: float
    volatility_score: float

    # اتجاه العملة
    coin_4h_score: float
    coin_1h_score: float

    # هيكل السعر
    structure_score: float
    support_score: float
    price_action_score: float

    # الحجم والسيولة
    volume_score: float
    liquidity_score: float
    volume_confirmation_score: float

    # الزخم
    rsi_score: float
    macd_score: float
    momentum_score: float
    timeframe_alignment_score: float

    # الاختراق وإعادة الاختبار
    breakout_score: float
    retest_score: float
    breakout_confirmation_score: float

    # جودة الدخول والمخاطرة
    entry_quality_score: float
    risk_reward: float
    target_quality_score: float
    stop_quality_score: float


def clamp(value: float, minimum: float, maximum: float) -> float:
    """حصر الرقم داخل نطاق محدد."""
    return max(minimum, min(value, maximum))


def calculate_score(data: MarketData) -> float:
    """
    حساب الدرجة النهائية من 100.

    الأوزان:
    BTC والسوق       20
    اتجاه العملة     15
    الهيكل والدعم     15
    الحجم والسيولة    15
    الزخم             10
    الاختراق           10
    الدخول/RR          15
    """

    btc_market = (
        data.btc_4h_score
        + data.btc_1h_score
        + data.market_score
        + data.volatility_score
    )

    coin_trend = data.coin_4h_score + data.coin_1h_score

    structure = (
        data.structure_score
        + data.support_score
        + data.price_action_score
    )

    volume_liquidity = (
        data.volume_score
        + data.liquidity_score
        + data.volume_confirmation_score
    )

    momentum = (
        data.rsi_score
        + data.macd_score
        + data.momentum_score
        + data.timeframe_alignment_score
    )

    breakout = (
        data.breakout_score
        + data.retest_score
        + data.breakout_confirmation_score
    )

    entry = (
        data.entry_quality_score
        + data.target_quality_score
        + data.stop_quality_score
    )

    # تحويل R:R إلى نقاط
    if data.risk_reward < 1.5:
        risk_reward_score = 0
    elif data.risk_reward < 2.0:
        risk_reward_score = 2
    elif data.risk_reward < 3.0:
        risk_reward_score = 4
    else:
        risk_reward_score = 5

    total = (
        btc_market
        + coin_trend
        + structure
        + volume_liquidity
        + momentum
        + breakout
        + entry
        + risk_reward_score
    )

    return round(clamp(total, 0, 100), 2)


def decision(score: float, risk_reward: float) -> str:
    """تحديد حالة الفرصة."""

    # شرط رفض مطلق
    if risk_reward < 1.5:
        return "REJECT"

    if score >= 90:
        return "EXCEPTIONAL"

    if score >= 85:
        return "STRONG_ENTRY"

    if score >= 80:
        return "WATCH"

    return "REJECT"


def analyze_coin(symbol: str, data: MarketData) -> dict:
    """تحليل عملة وإرجاع نتيجة منظمة."""

    score = calculate_score(data)
    status = decision(score, data.risk_reward)

    return {
        "symbol": symbol,
        "score": score,
        "risk_reward": data.risk_reward,
        "decision": status,
    }


if __name__ == "__main__":

    # اختبار داخلي فقط.
    # هذه ليست بيانات سوق حقيقية.
    example = MarketData(
        btc_4h_score=7,
        btc_1h_score=4,
        market_score=3,
        volatility_score=3,

        coin_4h_score=8,
        coin_1h_score=6,

        structure_score=5,
        support_score=5,
        price_action_score=4,

        volume_score=5,
        liquidity_score=5,
        volume_confirmation_score=4,

        rsi_score=3,
        macd_score=3,
        momentum_score=2,
        timeframe_alignment_score=2,

        breakout_score=4,
        retest_score=4,
        breakout_confirmation_score=2,

        entry_quality_score=5,
        risk_reward=2.7,
        target_quality_score=3,
        stop_quality_score=2,
    )

    result = analyze_coin("TEST/TRY", example)

    print("=== SPOT SCANNER TEST ===")
    print(f"Symbol: {result['symbol']}")
    print(f"Score: {result['score']}/100")
    print(f"Risk/Reward: 1:{result['risk_reward']}")
    print(f"Decision: {result['decision']}")
