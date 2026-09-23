from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
import time


INTERVAL_SECONDS = 15 * 60
MATURITY_CANDLES = 16


def _price(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            return None
        return number
    except (InvalidOperation, TypeError, ValueError):
        return None


def enqueue_near_miss(
    state: dict[str, Any],
    *,
    symbol: str,
    rejected_price: Any,
    rejected_stage: str,
    now: int | None = None,
) -> bool:
    """Queue one advanced rejection per symbol without touching legacy near_misses."""
    price = _price(rejected_price)
    if price is None:
        return False

    rejected_at = int(time.time() if now is None else now)
    first_close = ((rejected_at // INTERVAL_SECONDS) + 1) * INTERVAL_SECONDS
    maturity_at = first_close + MATURITY_CANDLES * INTERVAL_SECONDS

    queue = state.setdefault("near_miss_queue", {})
    if not isinstance(queue, dict):
        raise ValueError("near_miss_queue must be an object")

    current = queue.get(symbol)
    if isinstance(current, dict) and not current.get("processed", False):
        return False

    stage = str(rejected_stage)
    queue[symbol] = {
        "event_id": f"{symbol}:{rejected_at}:{stage}",
        "rejected_at": rejected_at,
        "rejected_price": str(price),
        "rejected_stage": stage,
        "maturity_at": maturity_at,
        "processed": False,
        "processed_at": None,
    }
    return True
