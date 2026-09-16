from decimal import Decimal
import pandas as pd
import signal_tracker


def _state(low, high):
    state = {"active_signals": []}
    signal_tracker.register_signal(state, symbol="TEST_TL", entry=Decimal("100"),
        stop=Decimal("98"), tp1=Decimal("101.5"), tp2=Decimal("102.3"),
        score=90, setup="BREAKOUT", now=1000)
    return state, pd.DataFrame([{"timestamp": 1900, "low": low, "high": high}])


def test_ambiguous_candle_is_stop_first(monkeypatch):
    state, frame = _state(97, 103)
    monkeypatch.setattr(signal_tracker, "fetch_candles", lambda *a, **k: frame)
    monkeypatch.setattr(signal_tracker, "get_order_book", lambda *a, **k: None)
    events = signal_tracker.update_active_signals(state, now=3000)
    assert [x["event"]["kind"] for x in events] == ["STOP"]


def test_tp1_then_tp2_are_recorded(monkeypatch):
    state, frame = _state(99, 103)
    monkeypatch.setattr(signal_tracker, "fetch_candles", lambda *a, **k: frame)
    monkeypatch.setattr(signal_tracker, "get_order_book", lambda *a, **k: None)
    events = signal_tracker.update_active_signals(state, now=3000)
    assert [x["event"]["kind"] for x in events] == ["TP1", "TP2"]
