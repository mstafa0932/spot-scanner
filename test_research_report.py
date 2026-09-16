from research_report import build_report, net_scenario
import pytest


@pytest.mark.parametrize("fee,slip", [(None,None), (-1,0), (0, -1), (float("nan"),0), (float("inf"),0)])
def test_unknown_invalid_costs_not_assumed_zero(fee, slip):
    assert net_scenario(100, 102, fee, slip) is None


def test_both_sides_costs():
    expected = (102*.999*.998/(100*1.001*1.002)-1)*100
    assert net_scenario(100, 102, .2, .1) == pytest.approx(expected)
    assert net_scenario(100, 100, .2, .1) < 0


def state(sample=True):
    return {"scan_diagnostics": [{"finished_at": 4900, "status": "completed", "markets": 234,
                                 "symbols": {"A": {"reason": "not_evaluated_capacity"}}}],
            "accumulation_radar": {"events": [{"observed_at": 1000, "reference_ask": "100",
                                              "outcomes": {"3600": {"sampled_at": 4700, "last_price": "102"}} if sample else {}}]}}


def test_mature_missing_is_not_a_loss_or_win():
    report = build_report(state(False), now=5000)
    result = report["horizon_samples"]["3600"]
    assert result["matured_events"] == 1
    assert result["missing_or_invalid_samples"] == 1
    assert result["mean_price_change_pct"] is None
    assert report["profitability_proven"] is False


def test_gross_not_reported_as_net():
    report = build_report(state(), now=5000)
    result = report["horizon_samples"]["3600"]
    assert result["mean_price_change_pct"] == 2
    assert result["mean_cost_scenario_pct"] is None
    report = build_report(state(), now=5000, fee_pct=.2, slippage_pct=.1)
    assert report["horizon_samples"]["3600"]["mean_cost_scenario_pct"] < 2


def test_future_or_late_sample_rejected():
    s = state()
    s["accumulation_radar"]["events"][0]["outcomes"]["3600"]["sampled_at"] = 9999
    report = build_report(s, now=5000)
    assert report["horizon_samples"]["3600"]["invalid_samples"] == 1


def test_health_and_pending_flags():
    s = state()
    s["active_signals"] = [{"status": "STOP", "events": [{"delivered":False}]}]
    report = build_report(s, now=10000)
    assert report["pending_lifecycle_notifications"] == 1
    assert "universe_not_fully_evaluated" in report["warnings"]
    assert "no_recent_completed_scan" in report["warnings"]
