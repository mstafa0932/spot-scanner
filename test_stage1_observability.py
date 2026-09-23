from datetime import datetime, timezone
from decimal import Decimal as D
import json

import pandas as pd

import scanner
from check_near_misses import process_state
from near_miss_queue import enqueue_near_miss
from risk_engine import build_risk_plan_result
from test_risk_engine import book, tech
from test_scanner_research import prepare


def test_near_miss_queue_is_separate_and_first_write_wins():
    legacy = [{"symbol": "LEGACY_TL"}]
    state = {"near_misses": legacy.copy()}
    assert enqueue_near_miss(state, symbol="TEST_TL", rejected_price=D("100"),
                             rejected_stage="score_70_79", now=1001)
    assert state["near_misses"] == legacy
    queued = state["near_miss_queue"]["TEST_TL"]
    assert queued["event_id"] == "TEST_TL:1001:score_70_79"
    assert queued["maturity_at"] == 16200
    assert not enqueue_near_miss(state, symbol="TEST_TL", rejected_price=D("101"),
                                 rejected_stage="book:spread_too_high", now=2000)


def test_risk_plan_result_exposes_rr_rejection_without_changing_wrapper_contract():
    close_wall = book(asks=(
        (D("100.1"), D("5")),
        (D("100.5"), D("200")),
        (D("103.0"), D("5")),
    ))
    result = build_risk_plan_result(
        book=close_wall, tech=tech(atr14=D("1"), swing_low=D("98.5")),
        setup="BREAKOUT", atr_multiplier=D("1.75"), max_risk_pct=D("4")
    )
    assert result.plan is None
    assert result.rejection_reason == "rr_below_min"


def test_synthetic_near_miss_mfe_mae_and_archive_idempotency(tmp_path):
    rejected_at = 1001
    first_close = 1800
    maturity_at = 16200
    event_id = "TEST_TL:1001:score_70_79"
    state = {"near_miss_queue": {"TEST_TL": {
        "event_id": event_id, "rejected_at": rejected_at, "rejected_price": "100",
        "rejected_stage": "score_70_79", "maturity_at": maturity_at,
        "processed": False, "processed_at": None,
    }}}
    timestamps = [first_close + i * 900 for i in range(16)]
    highs = [101.0] * 16
    lows = [99.0] * 16
    highs[7] = 103.20
    lows[9] = 98.70
    frame = pd.DataFrame({"timestamp": timestamps, "high": highs, "low": lows})
    archive = tmp_path / "near_misses_archive.jsonl"
    lines = []

    def fetcher(*_args):
        return frame

    assert process_state(state, now=maturity_at, fetcher=fetcher,
                         archive_path=archive, output=lines.append)
    assert lines == ["[NEAR_MISS] TEST_TL | MFE:+3.200% | MAE:-1.300% | measured"]
    rows = [json.loads(x) for x in archive.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["event_id"] == event_id
    assert rows[0]["mfe_pct"] == "3.200"
    assert rows[0]["mae_pct"] == "-1.300"

    state["near_miss_queue"]["TEST_TL"]["processed"] = False
    state["near_miss_queue"]["TEST_TL"]["processed_at"] = None
    assert process_state(state, now=maturity_at + 1, fetcher=fetcher,
                         archive_path=archive, output=lines.append)
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 1


def test_week1_metrics_emits_only_at_23_utc(tmp_path):
    state = {"near_miss_queue": {}}
    archive = tmp_path / "near_misses_archive.jsonl"
    archive.write_text("", encoding="utf-8")
    lines = []
    hour_22 = int(datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc).timestamp())
    hour_23 = int(datetime(2026, 9, 23, 23, 0, tzinfo=timezone.utc).timestamp())
    process_state(state, now=hour_22, archive_path=archive, output=lines.append)
    assert not any(line.startswith("[WEEK1_METRICS]") for line in lines)
    process_state(state, now=hour_23, archive_path=archive, output=lines.append)
    assert sum(line.startswith("[WEEK1_METRICS]") for line in lines) == 1


def test_funnel_line_is_emitted_once_for_synthetic_scan(monkeypatch, tmp_path, capsys):
    prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (False, "synthetic rejection"))
    scanner.run_scanner()
    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line.startswith("[FUNNEL]")]
    assert lines == [
        "[FUNNEL] universe=1 liq=1 spread=1 book=1 tech=1 "
        "score=1 exec=0 candidates=0 selected=0"
    ]

# Stage 1 acceptance suite: branch CI trigger.
