from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
import json
import os
import time

from market_data import fetch_candles


STATE_FILE = Path(os.getenv("SCANNER_STATE_FILE", "scanner_state.json"))
ARCHIVE_FILE = Path(os.getenv("NEAR_MISS_ARCHIVE_FILE", "near_misses_archive.jsonl"))
INTERVAL_SECONDS = 15 * 60
MATURITY_CANDLES = 16
PRUNE_AFTER_SECONDS = 172800


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def load_state(path: Path = STATE_FILE) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("scanner state must be an object")
    queue = raw.get("near_miss_queue", {})
    if not isinstance(queue, dict):
        raise ValueError("near_miss_queue must be an object")
    raw["near_miss_queue"] = queue
    return raw


def save_state(state: dict[str, Any], path: Path = STATE_FILE) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def archive_event_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    event_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_id = row.get("event_id") if isinstance(row, dict) else None
        if isinstance(event_id, str) and event_id:
            event_ids.add(event_id)
    return event_ids


def append_archive(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _event_id(symbol: str, record: dict[str, Any]) -> str:
    existing = record.get("event_id")
    if isinstance(existing, str) and existing:
        return existing
    rejected_at = int(record.get("rejected_at", 0) or 0)
    stage = str(record.get("rejected_stage", "unknown"))
    return f"{symbol}:{rejected_at}:{stage}"


def _measure_event(symbol: str, record: dict[str, Any], fetcher: Callable[..., Any]) -> dict[str, Any]:
    rejected_at = int(record.get("rejected_at", 0) or 0)
    first_close = ((rejected_at // INTERVAL_SECONDS) + 1) * INTERVAL_SECONDS
    maturity_at = int(record.get("maturity_at", 0) or 0)
    rejected_price = _decimal(record.get("rejected_price"))
    base = {
        "event_id": _event_id(symbol, record),
        "symbol": symbol,
        "rejected_at": rejected_at,
        "rejected_price": str(record.get("rejected_price")),
        "rejected_stage": str(record.get("rejected_stage", "")),
        "first_close": first_close,
        "maturity_at": maturity_at,
    }
    if rejected_price is None or rejected_price <= 0:
        return {**base, "candles": 0, "mfe_pct": None, "mae_pct": None, "outcome": "incomplete_data"}

    try:
        candles = fetcher(symbol, "15m", 250)
    except Exception:
        return {**base, "candles": 0, "mfe_pct": None, "mae_pct": None, "outcome": "incomplete_data"}

    required = {"timestamp", "high", "low"}
    if not required.issubset(set(getattr(candles, "columns", []))):
        return {**base, "candles": 0, "mfe_pct": None, "mae_pct": None, "outcome": "incomplete_data"}

    window = candles[(candles["timestamp"] >= first_close) & (candles["timestamp"] < maturity_at)].copy()
    window = window.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    expected = [first_close + i * INTERVAL_SECONDS for i in range(MATURITY_CANDLES)]
    actual = [int(x) for x in window["timestamp"].tolist()]
    if len(window) < MATURITY_CANDLES or actual != expected:
        return {**base, "candles": len(window), "mfe_pct": None, "mae_pct": None, "outcome": "incomplete_data"}

    highs = [_decimal(x) for x in window["high"].tolist()]
    lows = [_decimal(x) for x in window["low"].tolist()]
    if any(x is None for x in highs + lows):
        return {**base, "candles": len(window), "mfe_pct": None, "mae_pct": None, "outcome": "incomplete_data"}

    raw_mfe = (max(highs) / rejected_price - Decimal("1")) * Decimal("100")
    raw_mae = (min(lows) / rejected_price - Decimal("1")) * Decimal("100")
    mfe = max(Decimal("0"), raw_mfe)
    mae = min(Decimal("0"), raw_mae)
    return {
        **base,
        "candles": len(window),
        "mfe_pct": str(mfe),
        "mae_pct": str(mae),
        "outcome": "measured",
    }


def process_state(
    state: dict[str, Any],
    *,
    now: int,
    fetcher: Callable[..., Any] = fetch_candles,
    archive_path: Path = ARCHIVE_FILE,
    output: Callable[[str], Any] = print,
) -> bool:
    queue = state.setdefault("near_miss_queue", {})
    if not isinstance(queue, dict):
        raise ValueError("near_miss_queue must be an object")

    archived = archive_event_ids(archive_path)
    changed = False

    for symbol, record in list(queue.items()):
        if not isinstance(record, dict) or record.get("processed") is True:
            continue
        try:
            maturity_at = int(record.get("maturity_at", 0) or 0)
        except (TypeError, ValueError):
            maturity_at = 0
        if maturity_at <= 0 or maturity_at > now:
            continue

        event_id = _event_id(symbol, record)
        if event_id in archived:
            record["processed"] = True
            record["processed_at"] = now
            changed = True
            continue

        row = _measure_event(symbol, record, fetcher)
        row["processed_at"] = now
        append_archive(archive_path, row)
        archived.add(event_id)

        if row["outcome"] == "measured":
            mfe = Decimal(row["mfe_pct"])
            mae = Decimal(row["mae_pct"])
            output(f"[NEAR_MISS] {symbol} | MFE:{mfe:+.3f}% | MAE:{mae:+.3f}% | measured")
        else:
            output(f"[NEAR_MISS] {symbol} | MFE:n/a | MAE:n/a | incomplete_data")

        record["processed"] = True
        record["processed_at"] = now
        changed = True

    cutoff = now - PRUNE_AFTER_SECONDS
    for symbol, record in list(queue.items()):
        if not isinstance(record, dict) or record.get("processed") is not True:
            continue
        try:
            processed_at = int(record.get("processed_at", 0) or 0)
        except (TypeError, ValueError):
            processed_at = 0
        if processed_at and processed_at < cutoff and _event_id(symbol, record) in archived:
            del queue[symbol]
            changed = True

    if datetime.fromtimestamp(now, tz=timezone.utc).hour == 23:
        total = len(queue)
        processed = sum(isinstance(r, dict) and r.get("processed") is True for r in queue.values())
        pending = total - processed
        output(
            "[WEEK1_METRICS] "
            f"queue={total} pending={pending} processed={processed} "
            f"archive_events={len(archived)}"
        )

    return changed


def run(
    now: int | None = None,
    *,
    state_path: Path = STATE_FILE,
    archive_path: Path = ARCHIVE_FILE,
    fetcher: Callable[..., Any] = fetch_candles,
    output: Callable[[str], Any] = print,
) -> bool:
    current = int(time.time() if now is None else now)
    state = load_state(state_path)
    changed = process_state(state, now=current, fetcher=fetcher, archive_path=archive_path, output=output)
    if changed:
        save_state(state, state_path)
    return changed


if __name__ == "__main__":
    run()
