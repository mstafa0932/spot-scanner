from datetime import datetime, timezone
from decimal import Decimal as D
import json

import pandas as pd

import scanner
import check_near_misses
from check_near_misses import _measure_event, process_state
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
    assert lines[0] == "[NEAR_MISS] TEST_TL | MFE:+3.200% | MAE:-1.300% | measured"\n    assert "[INCOMPLETE_COVERAGE] incomplete_total=0 incomplete_classified=0 coverage_ratio=1.0000" in lines\n    assert "[INCOMPLETE_CODES] {}" in lines
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
    monkeypatch.setattr(
        scanner,
        "btc_gate",
        lambda: (False, None, "BTC confirmed weakness RSI=42.0 | 3C=-1.00%"),
    )
    scanner.run_scanner()
    output = capsys.readouterr().out
    lines = [
        line for line in output.splitlines()
        if line.startswith(("[INFRA_HEALTH]", "[FUNNEL_BEHAVIOR]"))
    ]
    assert len(lines) == 2
    assert lines[0].startswith("[INFRA_HEALTH] ")
    assert "btc_ok=False" in lines[0]
    assert "btc_reason=regime_bearish" in lines[0]
    assert "state_saved=OK" in lines[0]
    assert lines[1].startswith("[FUNNEL_BEHAVIOR] ")
    assert "universe=1 liq=1 spread=1 book=1 tech=1 data_valid=1 " in lines[1]
    assert "score=1 candidates=0 confirmed=0 exec=0 limit=0 " in lines[1]



def _queued_record(*, price="100", rejected_at=1001, maturity_at=16200):
    return {
        "event_id": f"TEST_TL:{rejected_at}:score_70_79",
        "rejected_at": rejected_at,
        "rejected_price": price,
        "rejected_stage": "score_70_79",
        "maturity_at": maturity_at,
        "processed": False,
        "processed_at": None,
    }


def test_incomplete_reason_codes_cover_all_measurement_paths():
    record = _queued_record()

    invalid_price = _measure_event("TEST_TL", {**record, "rejected_price": "0"}, lambda *_: None)
    assert invalid_price["incomplete_reason_code"] == "invalid_rejected_price"

    def failing_fetcher(*_args):
        raise RuntimeError("synthetic fetch failure")
    fetch_error = _measure_event("TEST_TL", record, failing_fetcher)
    assert fetch_error["incomplete_reason_code"] == "fetch_error"
    assert fetch_error["incomplete_reason_detail"] == "RuntimeError: synthetic fetch failure"

    missing_columns = _measure_event(
        "TEST_TL",
        record,
        lambda *_: pd.DataFrame({"timestamp": [1800], "high": [101.0]}),
    )
    assert missing_columns["incomplete_reason_code"] == "missing_columns"

    insufficient = _measure_event(
        "TEST_TL",
        record,
        lambda *_: pd.DataFrame({
            "timestamp": [1800 + i * 900 for i in range(15)],
            "high": [101.0] * 15,
            "low": [99.0] * 15,
        }),
    )
    assert insufficient["incomplete_reason_code"] == "insufficient_window"

    invalid_ohlc = _measure_event(
        "TEST_TL",
        record,
        lambda *_: pd.DataFrame({
            "timestamp": [1800 + i * 900 for i in range(16)],
            "high": [101.0] * 15 + [None],
            "low": [99.0] * 16,
        }),
    )
    assert invalid_ohlc["incomplete_reason_code"] == "invalid_ohlc"


def test_incomplete_reason_is_archived_and_retained_in_state(tmp_path, monkeypatch):
    maturity_at = 16200
    state = {"near_miss_queue": {"TEST_TL": _queued_record(maturity_at=maturity_at)}}
    archive = tmp_path / "near_misses_archive.jsonl"
    monkeypatch.setattr(check_near_misses.time, "sleep", lambda *_: None)

    def failing_fetcher(*_args):
        raise RuntimeError("synthetic 429")

    assert process_state(
        state,
        now=maturity_at,
        fetcher=failing_fetcher,
        archive_path=archive,
        output=lambda *_: None,
    )
    row = json.loads(archive.read_text(encoding="utf-8").strip())
    assert row["incomplete_reason_code"] == "fetch_error"
    assert row["incomplete_reason_detail"] == "RuntimeError: synthetic 429"
    queued = state["near_miss_queue"]["TEST_TL"]
    assert queued["incomplete_reason_code"] == "fetch_error"
    assert queued["incomplete_reason_detail"] == "RuntimeError: synthetic 429"


def test_process_state_paces_after_every_mature_measurement(tmp_path, monkeypatch):
    maturity_at = 16200
    state = {
        "near_miss_queue": {
            "A_TL": {
                **_queued_record(maturity_at=maturity_at),
                "event_id": "A_TL:1001:score_70_79",
            },
            "B_TL": {
                **_queued_record(rejected_at=1002, maturity_at=maturity_at),
                "event_id": "B_TL:1002:score_70_79",
            },
        }
    }
    archive = tmp_path / "near_misses_archive.jsonl"
    sleeps = []
    monkeypatch.setattr(check_near_misses.time, "sleep", lambda seconds: sleeps.append(seconds))

    timestamps = [1800 + i * 900 for i in range(16)]
    frame = pd.DataFrame({
        "timestamp": timestamps,
        "high": [101.0] * 16,
        "low": [99.0] * 16,
    })

    process_state(
        state,
        now=maturity_at,
        fetcher=lambda *_: frame,
        archive_path=archive,
        output=lambda *_: None,
    )
    assert sleeps == [0.3, 0.3]


def test_favorable_excursion_candidate_metric_is_research_only(tmp_path):
    archive = tmp_path / "near_misses_archive.jsonl"
    rows = [
        {"outcome": "measured", "mfe_pct": "1.50", "mae_pct": "-1.50"},
        {"outcome": "measured", "mfe_pct": "2.00", "mae_pct": "-1.51"},
        {"outcome": "measured", "mfe_pct": "1.49", "mae_pct": "-0.10"},
        {"outcome": "incomplete_data", "mfe_pct": None, "mae_pct": None},
    ]
    archive.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    assert check_near_misses.favorable_excursion_candidate_count(archive) == 1

# Stage 1 acceptance suite: branch CI trigger.


def test_directive_009_candidate_lifecycle_deduplicates_symbol(monkeypatch):
    import scanner
    monkeypatch.setenv("GITHUB_RUN_ID", "1001")
    state = {"candidate_lifecycle": {}}
    scanner._candidate_lifecycle_update(
        state, symbol="INJ_TL", score=91, lifecycle_state="discovery", now=100
    )
    scanner._candidate_lifecycle_update(
        state, symbol="INJ_TL", score=92, lifecycle_state="confirmation_1_of_2",
        reason="watching: confirmations 1/2", now=200
    )
    item = state["candidate_lifecycle"]["INJ_TL"]
    assert item["candidate_id"] == "INJ_TL:1001"
    assert item["first_seen_run"] == "1001"
    assert item["max_score"] == 92
    assert item["current_state"] == "confirmation_1_of_2"
    assert len(item["history"]) == 2


def test_directive_009_terminal_candidate_can_start_new_lifecycle(monkeypatch):
    import scanner
    state = {"candidate_lifecycle": {}}
    monkeypatch.setenv("GITHUB_RUN_ID", "1001")
    scanner._candidate_lifecycle_update(
        state, symbol="SENT_TL", score=89, lifecycle_state="shadow_entry", now=100
    )
    monkeypatch.setenv("GITHUB_RUN_ID", "1002")
    scanner._candidate_lifecycle_update(
        state, symbol="SENT_TL", score=87, lifecycle_state="discovery", now=200
    )
    item = state["candidate_lifecycle"]["SENT_TL"]
    assert item["candidate_id"] == "SENT_TL:1002"
    assert item["first_seen_run"] == "1002"
    assert item["current_state"] == "discovery"


def test_incomplete_coverage_below_threshold_hides_distribution_but_preserves_codes(tmp_path, monkeypatch):
    maturity_at = 16200
    state = {"near_miss_queue": {"TEST_TL": _queued_record(maturity_at=maturity_at)}}
    archive = tmp_path / "near_misses_archive.jsonl"
    # Historical unclassified row keeps coverage below 95%.
    archive.write_text(json.dumps({"outcome": "incomplete_data"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(check_near_misses.time, "sleep", lambda *_: None)
    lines = []

    def failing_fetcher(*_args):
        raise RuntimeError("synthetic 429")

    assert process_state(
        state, now=maturity_at, fetcher=failing_fetcher,
        archive_path=archive, output=lines.append,
    )
    assert any("coverage_ratio=0.5000" in line for line in lines)
    assert not any(line.startswith("[INCOMPLETE_CODES]") for line in lines)
    queued = state["near_miss_queue"]["TEST_TL"]
    assert queued["incomplete_reason_code"] == "fetch_error"
    rows = [json.loads(x) for x in archive.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["incomplete_reason_code"] == "fetch_error"
