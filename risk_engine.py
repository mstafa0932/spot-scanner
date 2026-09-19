from __future__ import annotations

"""Deterministic execution-risk helpers for manual spot alerts.

No function in this module submits an order.  The purpose is to turn a scanner
signal into a conservative hypothetical limit-entry/risk plan that can be
paper-tracked in shadow mode.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any, Optional


D = Decimal


def _d(value: Any) -> Optional[Decimal]:
    try:
        out = D(str(value))
        return out if out.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def price_step(price: Decimal) -> Decimal:
    if price >= 100:
        return D("0.01")
    if price >= 1:
        return D("0.0001")
    if price >= D("0.01"):
        return D("0.000001")
    if price >= D("0.0001"):
        return D("0.00000001")
    return D("0.0000000001")


@dataclass(frozen=True)
class RiskPlan:
    entry: Decimal
    stop: Decimal
    tp1: Decimal
    tp2: Decimal
    risk_pct: Decimal
    reward_risk_tp1: Decimal
    target_adjusted_for_wall: bool
    sell_wall_price: Optional[Decimal] = None
    sell_wall_share: Decimal = D("0")


def limit_entry_price(book, tech, setup: str) -> Optional[Decimal]:
    """Choose a passive/reclaim limit reference instead of chasing the ask.

    Pullbacks are priced near EMA9 after a recent mean interaction.  Other
    setups use the first quartile of the spread.  The result is always bounded
    by the live bid/ask snapshot and is only a reference for manual execution.
    """
    bid = _d(getattr(book, "best_bid", None))
    ask = _d(getattr(book, "best_ask", None))
    ema9 = _d(getattr(tech, "ema9", None))
    if None in (bid, ask) or bid <= 0 or ask < bid:
        return None

    spread = ask - bid
    if setup == "PULLBACK":
        if not bool(getattr(tech, "mean_touch", False)):
            return None
        if ema9 is None or ema9 <= 0:
            return None
        reference = max(bid, min(ask, ema9))
    else:
        reference = bid + spread * D("0.25")

    step = price_step(reference)
    return (reference / step).quantize(D("1")) * step


def _sell_wall_before_target(book, entry: Decimal, target: Decimal):
    asks = tuple(getattr(book, "asks", ()) or ())
    levels = []
    for raw_price, raw_amount in asks:
        price, amount = _d(raw_price), _d(raw_amount)
        if price is None or amount is None or price <= entry or price >= target or amount <= 0:
            continue
        levels.append((price, amount, price * amount))

    if not levels:
        return None, D("0")

    total_ask = sum(
        (_d(p) * _d(q) for p, q in asks if _d(p) and _d(q) and _d(p) > 0 and _d(q) > 0),
        D("0"),
    )
    if total_ask <= 0:
        return None, D("0")

    notionals = [float(n) for _, _, n in levels]
    med = D(str(median(notionals))) if notionals else D("0")

    credible = []
    for price, _amount, notional in levels:
        share = notional / total_ask
        if share >= D("0.30") or (med > 0 and notional >= med * D("3")):
            credible.append((price, share))

    if not credible:
        return None, D("0")

    # Nearest credible obstruction matters first for a scalp target.
    return min(credible, key=lambda item: item[0])


def build_risk_plan(
    *,
    book,
    tech,
    setup: str,
    atr_multiplier: Decimal = D("1.75"),
    spread_cushion_multiplier: Decimal = D("1.50"),
    max_risk_pct: Decimal = D("4.00"),
    min_rr: Decimal = D("1.50"),
    tp1_pct: Decimal = D("1.50"),
    tp2_pct: Decimal = D("2.30"),
) -> Optional[RiskPlan]:
    entry = limit_entry_price(book, tech, setup)
    atr = _d(getattr(tech, "atr14", None))
    swing_low = _d(getattr(tech, "swing_low", None))
    spread_pct = _d(getattr(book, "spread_percent", None)) or D("0")
    if entry is None or atr is None or atr <= 0:
        return None

    spread_fraction = spread_pct / D("100")
    cushion_fraction = max(spread_fraction * spread_cushion_multiplier, D("0.0010"))

    atr_stop = entry - atr * atr_multiplier
    structural_stop = None
    if swing_low is not None and D("0") < swing_low < entry:
        structural_stop = swing_low * (D("1") - cushion_fraction)

    stop = min(atr_stop, structural_stop) if structural_stop else atr_stop
    if stop <= 0 or stop >= entry:
        return None

    risk = entry - stop
    risk_pct = risk / entry * D("100")
    if risk_pct <= 0 or risk_pct > max_risk_pct:
        return None

    raw_tp1 = entry * (D("1") + tp1_pct / D("100"))
    tp2 = entry * (D("1") + tp2_pct / D("100"))
    tp1 = raw_tp1
    adjusted = False
    wall_price, wall_share = _sell_wall_before_target(book, entry, raw_tp1)

    if wall_price is not None:
        step = price_step(wall_price)
        candidate_tp1 = wall_price - step
        if candidate_tp1 <= entry:
            return None
        candidate_rr = (candidate_tp1 - entry) / risk
        if candidate_rr < min_rr:
            return None
        tp1 = candidate_tp1
        adjusted = True

    rr = (tp1 - entry) / risk
    if rr < min_rr:
        return None
    if tp2 <= tp1:
        tp2 = tp1 + risk

    return RiskPlan(
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        risk_pct=risk_pct,
        reward_risk_tp1=rr,
        target_adjusted_for_wall=adjusted,
        sell_wall_price=wall_price,
        sell_wall_share=wall_share,
    )


def breakeven_trigger_price(entry: Decimal, tp1: Decimal) -> Decimal:
    """OR semantics: 75% of TP1 distance OR +1.0%, whichever occurs first."""
    return min(entry + (tp1 - entry) * D("0.75"), entry * D("1.01"))


def protected_breakeven_stop(
    entry: Decimal,
    *,
    spread_pct: Decimal = D("0"),
    roundtrip_fee_pct: Decimal = D("0.22"),
) -> Decimal:
    """Reference stop above entry costs; actual fills can still gap/slip."""
    buffer_pct = max(D("0"), spread_pct) + max(D("0"), roundtrip_fee_pct)
    return entry * (D("1") + buffer_pct / D("100"))
