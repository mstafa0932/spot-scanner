from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional
import os
import time

from market_data import get_order_book


CHECKPOINTS = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "60m": 60 * 60,
}

CHECKPOINT_TOLERANCE_SECONDS = 10 * 60

MAX_RECORDS = max(
    50,
    int(os.getenv("NEAR_MISS_MAX_RECORDS", "300")),
)

RETENTION_SECONDS = max(
    3600,
    int(os.getenv("NEAR_MISS_RETENTION_SECONDS", str(3 * 24 * 60 * 60))),
)

RECORD_COOLDOWN_SECONDS = max(
    900,
    int(os.getenv("NEAR_MISS_RECORD_COOLDOWN_SECONDS", str(2 * 60 * 60))),
)

MAX_PRICE_CHECKS_PER_RUN = max(
    1,
    int(os.getenv("NEAR_MISS_MAX_PRICE_CHECKS", "10")),
)


def _dec(value: Any) -> Optional[Decimal]:
    if value is None:
        return None

    try:
        result = Decimal(str(value))
        if not result.is_finite():
            return None
        return result
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value: Any) -> Optional[str]:
    value = _dec(value)
    if value is None:
        return None
    return str(value)


def _records(state: dict[str, Any]) -> list[dict[str, Any]]:
    records = state.get("near_misses")

    if not isinstance(records, list):
        records = []
        state["near_misses"] = records

    return records


def prune_near_misses(state: dict[str, Any]) -> None:
    records = _records(state)
    now = int(time.time())

    fresh: list[dict[str, Any]] = []

    for record in records:
        try:
            observed_at = int(record.get("observed_at", 0))
        except (TypeError, ValueError):
            continue

        if now - observed_at <= RETENTION_SECONDS:
            fresh.append(record)

    fresh.sort(
        key=lambda item: int(item.get("observed_at", 0)),
        reverse=True,
    )

    state["near_misses"] = fresh[:MAX_RECORDS]


def record_near_miss(
    state: dict[str, Any],
    *,
    symbol: str,
    gate: str,
    reason: str,
    reference_price: Any,
    score: Optional[int] = None,
    spread_pct: Any = None,
    imbalance: Any = None,
    rsi_15m: Any = None,
    volume_ratio_15m: Any = None,
) -> bool:

    price = _dec(reference_price)

    if price is None or price <= 0:
        return False

    now = int(time.time())
    records = _records(state)

    # لا نسجل نفس العملة ونفس سبب الرفض عشرات المرات.
    for old in reversed(records):
        if old.get("symbol") != symbol:
            continue

        if old.get("gate") != gate:
            continue

        try:
            age = now - int(old.get("observed_at", 0))
        except (TypeError, ValueError):
            continue

        if age < RECORD_COOLDOWN_SECONDS:
            return False

    record = {
        "id": f"{symbol}:{gate}:{now}",
        "symbol": symbol,
        "gate": gate,
        "reason": reason,
        "observed_at": now,
        "reference_price": str(price),
        "score": score,
        "spread_pct": _text(spread_pct),
        "imbalance": _text(imbalance),
        "rsi_15m": _text(rsi_15m),
        "volume_ratio_15m": _text(volume_ratio_15m),

        "last_price": str(price),
        "last_checked_at": now,

        "max_gain_pct": "0",
        "max_drawdown_pct": "0",

        "outcomes": {},
    }

    records.append(record)
    prune_near_misses(state)

    return True


def update_near_miss_outcomes(
    state: dict[str, Any],
) -> dict[str, int]:

    records = _records(state)
    now = int(time.time())

    checked_symbols: set[str] = set()

    result = {
        "checked": 0,
        "updated": 0,
        "errors": 0,
    }

    # الأحدث أولاً.
    ordered = sorted(
        records,
        key=lambda item: int(item.get("observed_at", 0)),
        reverse=True,
    )

    for record in ordered:

        symbol = str(record.get("symbol", ""))

        if not symbol:
            continue

        try:
            observed_at = int(record.get("observed_at", 0))
        except (TypeError, ValueError):
            continue

        age = now - observed_at

        # بعد انتهاء نافذة الساعة لا نحتاج استدعاءات سعر إضافية.
        if age < 0:
            continue

        if age > 60 * 60 + CHECKPOINT_TOLERANCE_SECONDS:
            continue

        if symbol in checked_symbols:
            continue

        if len(checked_symbols) >= MAX_PRICE_CHECKS_PER_RUN:
            break

        reference_price = _dec(record.get("reference_price"))

        if reference_price is None or reference_price <= 0:
            continue

        try:
            book = get_order_book(symbol, 5)
            current_price = _dec(book.best_ask)

            if current_price is None or current_price <= 0:
                result["errors"] += 1
                continue

        except Exception:
            result["errors"] += 1
            continue

        checked_symbols.add(symbol)
        result["checked"] += 1

        change_pct = (
            (current_price / reference_price) - Decimal("1")
        ) * Decimal("100")

        record["last_price"] = str(current_price)
        record["last_checked_at"] = now

        previous_gain = _dec(record.get("max_gain_pct")) or Decimal("0")
        previous_drawdown = _dec(record.get("max_drawdown_pct")) or Decimal("0")

        record["max_gain_pct"] = str(max(previous_gain, change_pct))
        record["max_drawdown_pct"] = str(min(previous_drawdown, change_pct))

        outcomes = record.setdefault("outcomes", {})

        for label, target_age in CHECKPOINTS.items():

            if label in outcomes:
                continue

            distance = abs(age - target_age)

            if distance > CHECKPOINT_TOLERANCE_SECONDS:
                continue

            outcomes[label] = {
                "checked_at": now,
                "price": str(current_price),
                "change_pct": str(change_pct),
            }

            result["updated"] += 1

    prune_near_misses(state)

    return result
