import json
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pandas as pd
import pytest
import scanner
import signal_tracker as tracker


def signal():
    state = {}
    tracker.register_signal(state, symbol="TEST_TL", entry=100, stop=98,
                            tp1=101.5, tp2=103, score=90, setup="TEST", now=900)
    return state


def bars(monkeypatch, rows, bid=None):
    monkeypatch.setattr(tracker, "fetch_candles", lambda *a: pd.DataFrame(rows, columns=["timestamp", "low", "high"]))
    monkeypatch.setattr(tracker, "get_order_book", lambda *a: NS(best_bid=bid))


def test_targets_after_expiry_not_counted(monkeypatch):
    state = signal()
    bars(monkeypatch, [(23400, 99, 110)], bid=110)
    events = tracker.update_active_signals(state, now=25000)
    assert [p["event"]["kind"] for p in events] == ["EXPIRED"]
    assert events[0]["event"]["at"] == 22500


def test_pre_expiry_target_found_on_late_scan(monkeypatch):
    state = signal()
    bars(monkeypatch, [(21600, 99, 104)], bid=95)
    events = tracker.update_active_signals(state, now=25000)
    assert [p["event"]["kind"] for p in events] == ["TP1", "TP2"]
    assert events[-1]["event"]["at"] == 22500


@pytest.mark.parametrize("timestamp", [0, 1800])
def test_pre_entry_and_open_candles_ignored(monkeypatch, timestamp):
    state = signal()
    bars(monkeypatch, [(timestamp, 90, 110)])
    assert tracker.update_active_signals(state, now=2000) == []


def test_boundary_candle_not_assigned_to_pre_expiry(monkeypatch):
    state = signal()
    state["active_signals"][0]["opened_at"] = 1000
    bars(monkeypatch, [(22500, 99, 110)])
    events = tracker.update_active_signals(state, now=24000)
    assert [p["event"]["kind"] for p in events] == ["EXPIRED"]


def test_pending_retries_even_after_terminal_status(monkeypatch):
    state = signal()
    bars(monkeypatch, [(1800, 99, 104)])
    tracker.update_active_signals(state, now=3000)
    calls = []
    def fail(msg): calls.append(msg); return False
    fmt = lambda p: p["event"]["kind"]
    assert tracker.deliver_pending_events(state, fail, fmt) == 0
    assert calls == ["TP1"]
    state = json.loads(json.dumps(state))
    assert tracker.update_active_signals(state, now=4000) == []
    assert tracker.deliver_pending_events(state, lambda msg: calls.append(msg) or True, fmt) == 2
    assert calls == ["TP1", "TP1", "TP2"]
    assert tracker.deliver_pending_events(state, fail, fmt) == 0


def test_legacy_events_not_resent():
    state = signal()
    state["active_signals"][0]["events"] = [{"kind": "TP1", "at": 1000, "price": "101.5"}]
    assert tracker.deliver_pending_events(state, lambda msg: pytest.fail("duplicate"), str) == 0


def test_cursor_and_conservative_stop(monkeypatch):
    state = signal()
    bars(monkeypatch, [(1800, 99, 102)])
    tracker.update_active_signals(state, now=3000)
    assert tracker.update_active_signals(state, now=3100) == []
    bars(monkeypatch, [(1800, 99, 102), (2700, 97, 105)])
    events = tracker.update_active_signals(state, now=4000)
    assert [e["event"]["kind"] for e in events] == ["STOP"]


@pytest.mark.parametrize("entry,stop,tp1,tp2", [(0, -1, 1, 2), (100, 110, 105, 106), (100, 98, 102, 101)])
def test_invalid_price_plans(monkeypatch, entry, stop, tp1, tp2):
    state = signal()
    state["active_signals"][0].update(entry=entry, stop=stop, tp1=tp1, tp2=tp2)
    bars(monkeypatch, [], bid=110)
    assert tracker.update_active_signals(state, now=3000) == []
    assert state["active_signals"][0]["status"] == "INVALID"


@pytest.mark.parametrize("raw", ["{broken", "[]", '{"active_signals":{}}', '{"last_alert_at":"bad"}'])
def test_corrupt_state_preserved(tmp_path, monkeypatch, raw):
    path = tmp_path / "state.json"
    path.write_text(raw)
    monkeypatch.setattr(scanner, "STATE_FILE", path)
    with pytest.raises(RuntimeError, match="preserved"):
        scanner.load_state()
    assert path.read_text() == raw


def test_save_failure_not_reported_as_success(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "STATE_FILE", tmp_path / "missing" / "state.json")
    with pytest.raises(RuntimeError, match="persisted"):
        scanner.save_state({})


def test_pending_events_survive_state_roundtrip(tmp_path, monkeypatch):
    state = signal()
    tracker._event(state["active_signals"][0], "STOP", D("98"), 3000)
    monkeypatch.setattr(scanner, "STATE_FILE", tmp_path / "state.json")
    scanner.save_state(state)
    loaded = scanner.load_state()
    assert loaded["active_signals"][0]["events"][0]["delivered"] is False


def test_indicator_prefix_does_not_use_future_candles():
    import math
    from indicator_engine import calculate_indicators
    frame = pd.DataFrame([{"timestamp": i*900+900, "open":100+math.sin(i),
                           "close":100+math.sin(i+.5), "low":98, "high":102,
                           "volume":100+i%7} for i in range(300)])
    full = calculate_indicators(frame)
    for length in (205, 240, 280):
        prefix = calculate_indicators(frame.iloc[:length])
        pd.testing.assert_frame_equal(prefix, full.iloc[:length])
