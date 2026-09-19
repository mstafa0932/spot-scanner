from decimal import Decimal as D
from types import SimpleNamespace as NS

from risk_engine import (
    build_risk_plan,
    breakeven_trigger_price,
    limit_entry_price,
    protected_breakeven_stop,
)


def tech(**overrides):
    base = dict(
        ema9=D("100"),
        atr14=D("1"),
        swing_low=D("98.5"),
        mean_touch=True,
    )
    base.update(overrides)
    return NS(**base)


def book(**overrides):
    base = dict(
        best_bid=D("99.9"),
        best_ask=D("100.1"),
        spread_percent=D("0.2"),
        bids=((D("99.9"), D("100")), (D("99.8"), D("80"))),
        asks=((D("100.1"), D("20")), (D("101"), D("20")), (D("102"), D("20"))),
    )
    base.update(overrides)
    return NS(**base)


def test_pullback_limit_requires_mean_touch():
    assert limit_entry_price(book(), tech(mean_touch=False), "PULLBACK") is None
    price = limit_entry_price(book(), tech(mean_touch=True), "PULLBACK")
    assert book().best_bid <= price <= book().best_ask


def test_dynamic_stop_uses_atr_or_structure_and_preserves_rr():
    plan = build_risk_plan(
        book=book(), tech=tech(atr14=D("1.2"), swing_low=D("98.2")),
        setup="BREAKOUT", atr_multiplier=D("1.75"), max_risk_pct=D("4")
    )
    assert plan is not None
    assert plan.risk_pct > D("1")
    assert plan.reward_risk_tp1 >= D("1.5")


def test_sell_wall_front_run_or_reject_on_bad_rr():
    wall_book = book(asks=(
        (D("100.1"), D("5")),
        (D("101.0"), D("10")),
        (D("103.0"), D("200")),
    ))
    plan = build_risk_plan(
        book=wall_book, tech=tech(atr14=D("0.4"), swing_low=D("99.2")),
        setup="BREAKOUT", atr_multiplier=D("1.5"), max_risk_pct=D("4")
    )
    assert plan is not None
    assert plan.target_adjusted_for_wall
    assert plan.sell_wall_price == D("103.0")
    assert plan.tp1 < D("103.0")
    assert plan.reward_risk_tp1 >= D("1.5")

    close_wall = book(asks=(
        (D("100.1"), D("5")),
        (D("100.5"), D("200")),
        (D("103.0"), D("5")),
    ))
    rejected = build_risk_plan(
        book=close_wall, tech=tech(atr14=D("1"), swing_low=D("98.5")),
        setup="BREAKOUT", atr_multiplier=D("1.75"), max_risk_pct=D("4")
    )
    assert rejected is None


def test_breakeven_or_semantics_and_cost_buffer():
    assert breakeven_trigger_price(D("100"), D("104")) == D("101")
    assert breakeven_trigger_price(D("100"), D("101")) == D("100.75")
    stop = protected_breakeven_stop(
        D("100"), spread_pct=D("0.10"), roundtrip_fee_pct=D("0.20")
    )
    assert stop == D("100.30")
