"""Snapshot-only depth scenario. Never predicts limit fills or submits orders."""
from decimal import Decimal


def evaluate_depth(book, budget=Decimal("100000")):
    if not budget.is_finite() or budget <= 0:
        raise ValueError("Budget must be finite and positive")
    result = {"mode": "hypothetical_snapshot_only", "budget_tl": str(budget),
              "buy_fee_rate_scenario": "0.0011", "sell_fee_rate_scenario": "0.0011",
              "fees_verified_for_all_orders": False, "limit_fill_guaranteed": False,
              "status": "depth_unavailable"}
    asks = sorted(getattr(book, "asks", ()))
    bids = sorted(getattr(book, "bids", ()), reverse=True)
    if not asks or not bids:
        return result
    fee = Decimal("0.0011")
    remaining = budget / (1 + fee)
    quantity = Decimal(0)
    for price, amount in sorted(asks):
        value = min(remaining, price * amount)
        quantity += value / price
        remaining -= value
        if remaining <= 0:
            break
    if remaining > 0:
        result["status"] = "insufficient_ask_depth"
        return result
    vwap = budget / (1 + fee) / quantity
    result.update(quantity=str(quantity), entry_vwap_tl=str(vwap),
                  entry_impact_pct=str((vwap / asks[0][0] - 1) * 100))
    remaining_units = quantity
    proceeds = Decimal(0)
    for price, amount in sorted(bids, reverse=True):
        units = min(remaining_units, amount)
        proceeds += units * price
        remaining_units -= units
        if remaining_units <= 0:
            break
    if remaining_units > 0:
        result["status"] = "insufficient_bid_depth"
        return result
    result.update(status="snapshot_depth_available",
                  immediate_roundtrip_loss_tl=str(budget - proceeds * (1 - fee)))
    return result
