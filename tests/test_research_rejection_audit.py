"""Offline regressions for the non-trading rejection evidence ledger."""
import json

import pytest

from research_rejection_audit import analyze_snapshots, main


def _run(run_id, started_at, symbols, *, shadow=True):
    return {
        "run_id": str(run_id), "started_at": started_at,
        "status": "completed", "shadow_mode": shadow, "symbols": symbols,
    }


def _state(tmp_path, filename, rows):
    path = tmp_path / filename
    path.write_text(json.dumps({"scan_diagnostics": rows}), encoding="utf-8")
    return path


def test_overlapping_snapshots_are_deduplicated_and_early_to_late_is_research_only(tmp_path):
    before = _run("101", 10000, {
        "ATH_TL": {"stage": "trigger", "reason": "score 77 < 80", "score": 77},
        "DOT_TL": {"stage": "coverage", "reason": "not_evaluated_capacity"},
        "ATOM_TL": {"stage": "universe", "reason": "quote_volume_below_minimum", "quote_volume": "2278915.03"},
    })
    after = _run("102", 10600, {
        "ATH_TL": {"stage": "discovery", "reason": "RSI outside watch range", "score": 84, "rsi": "79.64"},
        "DOT_TL": {"stage": "book", "reason": "imbalance_too_low", "imbalance": "0.72"},
        "ATOM_TL": {"stage": "universe", "reason": "quote_volume_below_minimum"},
    })
    later = _run("103", 11200, {
        "ATH_TL": {"stage": "discovery", "reason": "RSI outside watch range", "score": 86, "rsi": "82.6"},
        "DOT_TL": {"stage": "coverage", "reason": "not_evaluated_capacity"},
    })
    paths = [
        _state(tmp_path, "older.json", [before, after]),
        _state(tmp_path, "newer.json", [after, later]),
    ]
    report = analyze_snapshots(paths, ["ATH_TL", "DOT_TL", "ATOM_TL"])
    assert report["input_snapshots"] == 2
    assert report["distinct_completed_runs"] == 3
    ath = report["symbols"]["ATH_TL"]
    assert ath["observed_decisions"] == 3
    assert ath["reason_counts"]["RSI outside watch range"] == 2
    assert ath["max_recorded_score"] == 86
    assert ath["early_70_79_to_later_rsi_rejection"] == [{
        "early_run_id": "101", "early_score": 77, "later_run_id": "102",
        "later_score": 84, "later_rsi": "79.64",
    }]
    assert report["symbols"]["DOT_TL"]["capacity_skips_evaluated_next"] == 1
    assert report["symbols"]["DOT_TL"]["capacity_skips_repeated_next"] == 0
    assert report["symbols"]["ATOM_TL"]["stage_counts"] == {"universe": 2}
    assert "offline_shadow" in report["mode"]


def test_consecutive_capacity_skips_are_detected_not_counted_as_evaluations(tmp_path):
    runs = [
        _run("1", 1000, {"DASH_TL": {"stage": "coverage", "reason": "not_evaluated_capacity"}}),
        _run("2", 1600, {"DASH_TL": {"stage": "coverage", "reason": "not_evaluated_capacity"}}),
        _run("3", 2200, {"DASH_TL": {"stage": "universe", "reason": "quote_volume_below_minimum"}}),
    ]
    p = _state(tmp_path, "state.json", runs)
    result = analyze_snapshots([p], ["DASH_TL"])["symbols"]["DASH_TL"]
    assert result["capacity_skips_repeated_next"] == 1
    assert result["capacity_skips_universe_excluded_next"] == 1
    assert result["capacity_skips_evaluated_next"] == 0


def test_conflicting_duplicate_diagnostics_fail_closed(tmp_path):
    a = _run("a", 2000, {"ATH_TL": {"stage": "trigger", "reason": "score 77 < 80"}})
    b = _run("a", 2000, {"ATH_TL": {"stage": "trigger", "reason": "score 88 < 80"}})
    paths = [_state(tmp_path, "one.json", [a]), _state(tmp_path, "two.json", [b])]
    with pytest.raises(ValueError, match="Conflicting duplicate scan"):
        analyze_snapshots(paths)


def test_non_shadow_data_rejected_even_when_it_looks_profitable(tmp_path):
    p = _state(tmp_path, "live.json", [
        _run("1", 1000, {"ATH_TL": {"stage": "trigger", "score": 99}}, shadow=False)
    ])
    with pytest.raises(ValueError, match="Non-shadow scan"):
        analyze_snapshots([p])


def test_only_real_integer_range_scores_enter_research_transition(tmp_path):
    runs = [
        _run("1", 1000, {"ATH_TL": {"stage": "trigger", "reason": "score 75 < 80", "score": "75"}}),
        _run("2", 1600, {"ATH_TL": {"stage": "discovery", "reason": "RSI outside watch range", "score": 90}}),
    ]
    p = _state(tmp_path, "typed.json", runs)
    ath = analyze_snapshots([p], ["ATH_TL"])["symbols"]["ATH_TL"]
    assert ath["early_70_79_to_later_rsi_rejection"] == []


def test_cli_emits_read_only_json_report(tmp_path, monkeypatch, capsys):
    p = _state(tmp_path, "single.json", [_run("1", 1000, {
        "ATH_TL": {"stage": "trigger", "reason": "score 72 < 80", "score": 72}
    })])
    monkeypatch.setattr("sys.argv", ["audit", str(p), "--symbols", "ATH_TL"])
    main()
    data = json.loads(capsys.readouterr().out)
    assert data["distinct_completed_runs"] == 1
    assert list(data["symbols"]) == ["ATH_TL"]
    assert data["symbols"]["ATH_TL"]["max_recorded_score"] == 72
