from data_health_audit import summarize_run, summarize_history


def test_data_health_uses_only_markets_that_reached_candle_evaluation():
    run = {
        "run_id": "x",
        "symbols": {
            "A_TL": {"stage": "data", "reason": "candle_error:timeout"},
            "B_TL": {"stage": "book", "reason": "spread_too_high"},
            "C_TL": {"stage": "coverage", "reason": "not_evaluated_capacity"},
        },
        "funnel": {"universe": 234, "book": 30, "data_valid": 7},
    }
    row = summarize_run(run)
    assert row["universe"] == 234
    assert row["data_attempted"] == 8
    assert row["data_valid"] == 7
    assert row["data_failed"] == 1
    assert row["data_valid_rate"] == 7 / 8


def test_data_health_counts_fail_closed_quality_rejections():
    run = {
        "symbols": {
            "A": {"stage": "data", "reason": "synthetic_candles_present"},
            "B": {"stage": "data", "reason": "recent_15m_integrity_failed"},
            "C": {"stage": "data", "reason": "non_paribu_candles"},
        },
        "funnel": {"data_valid": 2},
    }
    row = summarize_run(run)
    assert row["data_attempted"] == 5
    assert row["data_failed"] == 3


def test_history_aggregates_without_universe_denominator():
    state = {"scan_diagnostics": [
        {"symbols": {"A": {"stage": "data", "reason": "candle_error:timeout"}},
         "funnel": {"universe": 234, "data_valid": 3}},
        {"symbols": {}, "funnel": {"universe": 234, "data_valid": 4}},
    ]}
    totals = summarize_history(state)["totals"]
    assert totals["data_attempted"] == 8
    assert totals["data_valid"] == 7
    assert totals["data_failed"] == 1
    assert totals["data_valid_rate"] == 7 / 8
