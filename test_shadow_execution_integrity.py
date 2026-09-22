"""P0 regressions: event-time expiry, provenance, and conservative OHLC fills."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

import signal_tracker as tracker


def order(opened_at=900):
    state = {}
    tracker.register_signal(
        state, symbol="TEST_TL", entry=100, stop=95, tp1=110, tp2=120,
        score=85, setup="PULLBACK", now=opened_at,
        evidence={"shadow_mode": True},
    )
    return state, state["active_signals"][0]


def bar(timestamp=1800, **overrides):
    row = dict(timestamp=timestamp, low=99, high=101, volume=10, is_authentic=True)
    row.update(overrides)
    return row


def feed(monkeypatch, rows, bid=None):
    # Explicit schemas make a valid empty response distinct from a bad response.
    frame = pd.DataFrame(rows, columns=["timestamp", "low", "high", "volume", "is_authentic"])
    monkeypatch.setattr(tracker, "fetch_candles", lambda *a: frame)
    monkeypatch.setattr(tracker, "get_order_book", lambda *a: SimpleNamespace(best_bid=bid))
    return frame


@pytest.mark.parametrize("timestamp", [6300, 7200, 9000])
def test_expiry_precedes_fill_and_reports_exact_deadline(monkeypatch, timestamp):
    state, sig = order()  # TTL deadline = 6300.
    feed(monkeypatch, [bar(timestamp, low=94, high=125)])
    events = tracker.update_active_signals(state, now=timestamp+900)
    assert sig["status"] == "ENTRY_EXPIRED"
    assert not sig["fill_confirmed"] and not sig["fill_estimated"]
    assert "filled_at" not in sig
    assert [(e["event"]["kind"], e["event"]["at"]) for e in events] == [("ENTRY_EXPIRED", 6300)]


def test_expiry_checked_even_if_post_deadline_bar_has_no_valid_prices(monkeypatch):
    state, sig = order()
    feed(monkeypatch, [bar(7200, low=None, high=None, is_authentic=False)])
    events = tracker.update_active_signals(state, now=8100)
    assert sig["status"] == "ENTRY_EXPIRED"
    assert events[0]["event"]["at"] == 6300
    assert "skipped_tracking_candles" not in sig


@pytest.mark.parametrize("now", [6300, 8100])
def test_bar_ending_at_deadline_can_be_replayed_on_delayed_scan(monkeypatch, now):
    state, sig = order()
    feed(monkeypatch, [bar(5400)])
    assert tracker.update_active_signals(state, now=now) == []
    assert sig["status"] == "OPEN" and sig["fill_estimated"]
    assert not sig["fill_confirmed"]
    assert sig["filled_at"] == 6300


def test_delayed_scan_replays_pre_expiry_fill_before_later_target(monkeypatch):
    state, sig = order()
    feed(monkeypatch, [bar(5400), bar(7200, low=101, high=121)])
    events = tracker.update_active_signals(state, now=8100)
    assert sig["status"] == "TP2"
    assert [e["event"]["kind"] for e in events] == ["TP1", "TP2"]
    assert all(e["event"]["simulation_version"] == tracker.SIMULATION_VERSION for e in events)


def test_candle_straddling_entry_ttl_cannot_estimate_fill(monkeypatch):
    state, sig = order(opened_at=1000)  # Deadline 6400 falls inside [6300, 7200).
    feed(monkeypatch, [bar(6300)])
    events = tracker.update_active_signals(state, now=7200)
    assert sig["status"] == "ENTRY_EXPIRED" and not sig["fill_estimated"]
    assert events[0]["event"]["at"] == 6400
    assert sig["tracking_data_error"] == "entry_expiry_boundary_ambiguous"


@pytest.mark.parametrize("volume", [1, 1000000])
def test_touch_does_not_fill_even_with_large_total_volume(monkeypatch, volume):
    state, sig = order()
    feed(monkeypatch, [bar(low=100, high=125, volume=volume)])
    assert tracker.update_active_signals(state, now=2700) == []
    assert sig["status"] == "PENDING_ENTRY"
    assert not sig["fill_confirmed"] and not sig["fill_estimated"]
    assert not sig["breakeven_armed"]


def test_targets_and_breakeven_not_evaluated_before_entry(monkeypatch):
    state, sig = order()
    feed(monkeypatch, [bar(low=105, high=125)], bid=125)
    assert tracker.update_active_signals(state, now=2700) == []
    assert sig["status"] == "PENDING_ENTRY" and not sig["breakeven_armed"]


def test_penetration_estimates_fill_but_never_exchange_confirmation(monkeypatch):
    state, sig = order()
    feed(monkeypatch, [bar(low=99.99, high=100)])
    assert tracker.update_active_signals(state, now=2700) == []
    assert sig["status"] == "OPEN" and sig["fill_estimated"]
    assert not sig["fill_confirmed"]
    assert sig["fill_evidence"]["candle_open"] == 1800
    assert sig["fill_evidence"]["candle_close"] == 2700
    assert sig["fill_evidence"]["quantity_basis"] == "unknown_no_queue_or_partial_fill_model"


@pytest.mark.parametrize("volume", [0, -1, None, float("nan"), float("inf")])
def test_penetration_without_valid_positive_volume_remains_unconfirmed(monkeypatch, volume):
    state, sig = order()
    feed(monkeypatch, [bar(volume=volume)])
    assert tracker.update_active_signals(state, now=2700) == []
    assert sig["status"] == "PENDING_ENTRY" and not sig["fill_estimated"]
    assert sig["tracking_incomplete"]


@pytest.mark.parametrize("authentic", [False, None, "False", "True", 1, pd.NA])
def test_missing_or_nonboolean_provenance_never_fills(monkeypatch, authentic):
    state, sig = order()
    feed(monkeypatch, [bar(is_authentic=authentic)])
    assert tracker.update_active_signals(state, now=2700) == []
    assert sig["status"] == "PENDING_ENTRY" and not sig["fill_estimated"]
    assert sig["tracking_data_error"] == "inauthentic_or_unverified_candle"
    assert "last_processed_candle" not in sig


@pytest.mark.parametrize("low,high", [(90, 101), (99, 125), (101, 105)])
@pytest.mark.parametrize("status", ["OPEN", "TP1"])
def test_synthetic_candles_cannot_stop_take_profit_or_arm_breakeven(monkeypatch, low, high, status):
    state, sig = order()
    sig.update(status=status, fill_estimated=True)
    feed(monkeypatch, [bar(low=low, high=high, is_authentic=False)])
    assert tracker.update_active_signals(state, now=2700) == []
    assert sig["status"] == status and not sig["breakeven_armed"]
    assert sig["tracking_incomplete"]


def test_skipped_synthetic_bar_can_be_recovered_as_real_on_next_scan(monkeypatch):
    state, sig = order()
    feed(monkeypatch, [bar(is_authentic=False)])
    tracker.update_active_signals(state, now=2700)
    feed(monkeypatch, [bar(is_authentic=True)])
    tracker.update_active_signals(state, now=2800)
    assert sig["status"] == "OPEN" and sig["fill_estimated"]
    assert sig["tracking_incomplete"]  # A research reviewer can see the earlier gap.


def test_synthetic_bar_is_ignored_but_later_real_bar_still_evaluated(monkeypatch):
    state, sig = order()
    feed(monkeypatch, [bar(low=90, high=125, is_authentic=False), bar(2700)])
    assert tracker.update_active_signals(state, now=3600) == []
    assert sig["status"] == "OPEN" and sig["filled_at"] == 3600


def test_fill_bar_ambiguity_counts_stop_before_targets(monkeypatch):
    state, sig = order()
    feed(monkeypatch, [bar(low=94, high=125)])
    events = tracker.update_active_signals(state, now=2700)
    assert [e["event"]["kind"] for e in events] == ["STOP"]
    assert sig["fill_estimated"] and not sig["fill_confirmed"]


def test_fill_bar_cannot_award_upside_targets(monkeypatch):
    state, sig = order()
    feed(monkeypatch, [bar(low=99, high=125)])
    assert tracker.update_active_signals(state, now=2700) == []
    assert sig["status"] == "OPEN" and not sig["tp1_notified"]


def test_empty_or_unavailable_feed_still_expires_once(monkeypatch):
    state, sig = order()
    def unavailable(*args):
        raise RuntimeError("feed unavailable")
    monkeypatch.setattr(tracker, "fetch_candles", unavailable)
    monkeypatch.setattr(tracker, "get_order_book", lambda *a: pytest.fail("pending order cannot query exit"))
    events = tracker.update_active_signals(state, now=6300)
    assert len(events) == 1 and sig["status"] == "ENTRY_EXPIRED"
    assert events[0]["event"]["at"] == 6300
    assert tracker.update_active_signals(state, now=7200) == []


def test_estimated_fill_survives_restart_without_duplicate_events(monkeypatch):
    state, sig = order()
    feed(monkeypatch, [bar()])
    tracker.update_active_signals(state, now=2700)
    state = json.loads(json.dumps(state))
    assert tracker.update_active_signals(state, now=2800) == []
    sig = state["active_signals"][0]
    assert sig["fill_estimated"] and not sig["fill_confirmed"]
    assert sig["simulation_version"] == tracker.SIMULATION_VERSION
