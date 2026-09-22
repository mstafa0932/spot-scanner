from types import SimpleNamespace as NS
from decimal import Decimal as D
import json

import pandas as pd
import pytest

import near_miss
import scanner
from candle_backfill import recent_authentic
from research_report import build_report
from test_scanner_research import prepare


def rejection(monkeypatch, at=1000):
    monkeypatch.setattr(near_miss.time, "time", lambda: at)
    state = {}
    near_miss.record_near_miss(state, symbol="TEST_TL", gate="TRIGGER",
                              reason="not ready", reference_price=100)
    return state


def test_checkpoint_cannot_be_recorded_before_its_horizon(monkeypatch):
    state = rejection(monkeypatch)
    monkeypatch.setattr(near_miss.time, "time", lambda: 1400)
    monkeypatch.setattr(near_miss, "get_order_book", lambda *a: NS(best_bid=D(99), best_ask=D(101)))
    near_miss.update_near_miss_outcomes(state)
    assert state["near_misses"][0]["outcomes"] == {}


def test_new_checkpoint_uses_bid_and_never_fills_two_future_horizons(monkeypatch):
    state = rejection(monkeypatch)
    monkeypatch.setattr(near_miss.time, "time", lambda: 1900)
    monkeypatch.setattr(near_miss, "get_order_book", lambda *a: NS(best_bid=D(99), best_ask=D(101)))
    near_miss.update_near_miss_outcomes(state)
    outcomes = state["near_misses"][0]["outcomes"]
    assert list(outcomes) == ["15m"]
    assert outcomes["15m"]["price"] == "99"
    assert D(outcomes["15m"]["change_pct"]) == -1
    assert outcomes["15m"]["actual_age_seconds"] == 900
    assert outcomes["15m"]["price_basis"] == "sampled_bid_not_fill"


def test_changed_rejection_reason_is_not_lost_in_cooldown(monkeypatch):
    state = rejection(monkeypatch)
    assert near_miss.record_near_miss(state, symbol="TEST_TL", gate="TRIGGER",
                                      reason="different reason", reference_price=100)
    assert not near_miss.record_near_miss(state, symbol="TEST_TL", gate="TRIGGER",
                                          reason="different reason", reference_price=100)


@pytest.mark.parametrize("authentic", ["False", "True", 1, None, pd.NA])
def test_data_gate_requires_boolean_provenance(authentic):
    frame = pd.DataFrame({"timestamp": [900], "is_authentic": [authentic]})
    assert recent_authentic(frame, 1, 900) is False


def test_shadow_events_are_not_failed_telegram_deliveries():
    state = {"active_signals": [
        {"status": "TP2", "tracking_mode": "shadow_limit_simulation",
         "events": [{"delivered": False}], "simulation_version": "ohlc_penetration_v2"},
        {"status": "STOP", "events": [{"delivered": False}]},
    ]}
    report = build_report(state, now=1000)
    assert report["pending_lifecycle_notifications"] == 1
    assert report["shadow_events_retained"] == 1


def test_current_execution_sample_excludes_legacy_and_incomplete():
    cohort = {"id": "new", "started_at": 1000}
    state = {"research_cohort": cohort, "active_signals": [
        {"status": "TP2", "fill_confirmed": True},
        {"status": "STOP", "research_cohort": "new", "simulation_version": "ohlc_penetration_v2",
         "tracking_mode": "shadow_limit_simulation", "fill_estimated": True},
        {"status": "TP2", "research_cohort": "new", "simulation_version": "ohlc_penetration_v2",
         "tracking_mode": "shadow_limit_simulation", "fill_estimated": True, "tracking_incomplete": True},
    ]}
    report = build_report(state, now=2000)
    assert report["execution_sample"]["estimated_fill_status_counts"] == {"STOP": 1}
    assert report["execution_sample"]["incomplete_records"] == 1
    assert report["execution_sample"]["excluded_legacy_records"] == 1
    assert not report["profitability_proven"]


def test_cohort_and_rejection_timestamps_survive_restart(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(scanner, "SHADOW_MODE", True)
    monkeypatch.setenv("GITHUB_SHA", "tested-sha")
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (False, "rejected"))
    scanner.run_scanner()
    first = scanner.load_state()
    scanner.run_scanner()
    second = scanner.load_state()
    assert first["research_cohort"] == second["research_cohort"]
    diag = second["scan_diagnostics"][-1]
    assert diag["research_cohort"] == second["research_cohort"]["id"]
    assert diag["code_sha"] == "tested-sha"
    assert diag["symbols"]["SYN_TL"]["observed_at"] == diag["started_at"]
    assert diag["funnel"]["data_valid"] == 1
    assert diag["funnel"]["discovery_passed"] == 0


def test_entrypoint_refuses_non_shadow_before_any_work(monkeypatch):
    import main
    monkeypatch.setenv("SHADOW_MODE", "false")
    monkeypatch.setattr(main, "run_scanner", lambda: pytest.fail("must stop before scanning"))
    with pytest.raises(RuntimeError, match="SHADOW_MODE"):
        main.main()


def test_radar_rejects_synthetic_input():
    import accumulation_radar
    from test_accumulation_radar import fixture, NOW
    frame, book = fixture()
    frame["is_authentic"] = True
    frame.loc[frame.index[-10], "is_authentic"] = False
    assert accumulation_radar.evaluate(frame, book, NOW) == (None, "inauthentic_candles")


@pytest.mark.parametrize("flag,env", [(False, "true"), (True, "false"), (True, "invalid")])
def test_direct_scanner_call_cannot_bypass_shadow_guard(monkeypatch, flag, env):
    monkeypatch.setattr(scanner, "SHADOW_MODE", flag)
    monkeypatch.setenv("SHADOW_MODE", env)
    monkeypatch.setattr(scanner, "load_state", lambda: pytest.fail("no work before mode validation"))
    with pytest.raises(RuntimeError, match="SHADOW_MODE"):
        scanner.run_scanner()


def test_shadow_tracking_mode_blocks_delivery_even_without_evidence():
    import signal_tracker
    state = {"active_signals": [{"tracking_mode": "shadow_limit_simulation",
                                "events": [{"kind": "STOP", "delivered": False}]}]}
    def unexpected(*args):
        pytest.fail("Shadow event must not reach Telegram")
    assert signal_tracker.deliver_pending_events(state, unexpected, unexpected) == 0


def test_failed_near_miss_request_does_not_starve_next_symbol(monkeypatch):
    state = rejection(monkeypatch)
    monkeypatch.setattr(near_miss.time, "time", lambda: 1100)
    near_miss.record_near_miss(state, symbol="NEXT_TL", gate="TRIGGER",
                              reason="not ready", reference_price=100)
    monkeypatch.setattr(near_miss, "MAX_PRICE_CHECKS_PER_RUN", 1)
    calls = []
    def book(symbol, *args):
        calls.append(symbol)
        if symbol == "TEST_TL":
            raise RuntimeError("temporarily unavailable")
        return NS(best_bid=D(101), best_ask=D(102))
    monkeypatch.setattr(near_miss, "get_order_book", book)
    monkeypatch.setattr(near_miss.time, "time", lambda: 1900)
    assert near_miss.update_near_miss_outcomes(state)["errors"] == 1
    state = json.loads(json.dumps(state))
    monkeypatch.setattr(near_miss.time, "time", lambda: 2000)
    result = near_miss.update_near_miss_outcomes(state)
    assert calls == ["TEST_TL", "NEXT_TL"]
    assert result == {"checked": 1, "updated": 1, "errors": 0}
