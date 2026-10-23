from decimal import Decimal as D
from types import SimpleNamespace

from risk_engine import build_risk_plan_result


def _book(asks=()):
    return SimpleNamespace(
        best_bid=D("0.00022000"),
        best_ask=D("0.00022020"),
        spread_percent=D("0.10"),
        asks=asks,
    )


def _tech(swing_low):
    return SimpleNamespace(
        ema9=D("0.00022000"),
        atr14=D("0.00000030"),
        swing_low=D(swing_low),
    )


def test_risk_rejection_has_exact_inputs_without_loosening_cap():
    result = build_risk_plan_result(
        book=_book(), tech=_tech("0.00020900"), setup="RECOVERY"
    )
    assert result.plan is None
    assert result.rejection_reason == "risk_out_of_range"
    assert D(result.evidence["risk_pct"]) > D(result.evidence["max_risk_pct"])
    assert D(result.evidence["stop"]) < D(result.evidence["entry"])


def test_sell_wall_rejection_explains_insufficient_reward_risk():
    asks = (
        (D("0.00022020"), D("100000000")),
        (D("0.00022025"), D("1000000")),
        (D("0.00022030"), D("1000000")),
    )
    result = build_risk_plan_result(
        book=_book(asks), tech=_tech("0.00021990"), setup="RECOVERY"
    )
    assert result.plan is None
    assert result.rejection_reason == "rr_below_min"
    assert D(result.evidence["actual_rr"]) < D(result.evidence["min_rr"])
    assert result.evidence["sell_wall_price"] == "0.00022020"


def test_valid_risk_plan_is_unchanged():
    result = build_risk_plan_result(
        book=_book(), tech=_tech("0.00021990"), setup="RECOVERY"
    )
    assert result.rejection_reason is None
    assert result.plan is not None
    assert result.evidence is None
    assert result.plan.reward_risk_tp1 >= D("1.50")
