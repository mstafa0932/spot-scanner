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


def test_shadow_limit_waits_for_fill(monkeypatch):
    state = {"active_signals": []}
    signal_tracker.register_signal(
        state, symbol="TEST_TL", entry=Decimal("100"), stop=Decimal("98"),
        tp1=Decimal("103"), tp2=Decimal("105"), score=90, setup="BREAKOUT",
        now=1000, evidence={"shadow_mode": True, "spread_pct": "0.10"}
    )
    frame = pd.DataFrame([{"timestamp": 1900, "low": 100.5, "high": 101.5}])
    monkeypatch.setattr(signal_tracker, "fetch_candles", lambda *a, **k: frame)
    monkeypatch.setattr(signal_tracker, "get_order_book", lambda *a, **k: None)
    assert signal_tracker.update_active_signals(state, now=3000) == []
    assert state["active_signals"][0]["status"] == "PENDING_ENTRY"
    assert state["active_signals"][0]["fill_confirmed"] is False


def test_shadow_limit_fill_then_breakeven_persists(monkeypatch):
    state = {"active_signals": []}
    signal_tracker.register_signal(
        state, symbol="TEST_TL", entry=Decimal("100"), stop=Decimal("98"),
        tp1=Decimal("104"), tp2=Decimal("108"), score=90, setup="BREAKOUT",
        now=1000, evidence={"shadow_mode": True, "spread_pct": "0.10"}
    )
    first = pd.DataFrame([{"timestamp": 1900, "low": 99.9, "high": 100.5}])
    monkeypatch.setattr(signal_tracker, "fetch_candles", lambda *a, **k: first)
    monkeypatch.setattr(signal_tracker, "get_order_book", lambda *a, **k: None)
    signal_tracker.update_active_signals(state, now=3000)
    sig = state["active_signals"][0]
    assert sig["status"] == "OPEN"
    assert sig["fill_confirmed"] is True

    second = pd.DataFrame([
        {"timestamp": 1900, "low": 99.9, "high": 100.5},
        {"timestamp": 2800, "low": 100.2, "high": 101.1},
    ])
    monkeypatch.setattr(signal_tracker, "fetch_candles", lambda *a, **k: second)
    signal_tracker.update_active_signals(state, now=4000)
    sig = state["active_signals"][0]
    assert sig["breakeven_armed"] is True
    assert Decimal(sig["breakeven_stop"]) > Decimal("100")


def test_shadow_entry_expires_unfilled(monkeypatch):
    state = {"active_signals": []}
    signal_tracker.register_signal(
        state, symbol="TEST_TL", entry=Decimal("100"), stop=Decimal("98"),
        tp1=Decimal("103"), tp2=Decimal("105"), score=90, setup="BREAKOUT",
        now=1000, evidence={"shadow_mode": True}
    )
    monkeypatch.setattr(signal_tracker, "fetch_candles", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(signal_tracker, "get_order_book", lambda *a, **k: None)
    events = signal_tracker.update_active_signals(
        state, now=1000 + signal_tracker.SHADOW_ENTRY_TTL_SECONDS + 1
    )
    assert state["active_signals"][0]["status"] == "ENTRY_EXPIRED"
    assert [x["event"]["kind"] for x in events] == ["ENTRY_EXPIRED"]


def test_shadow_events_never_deliver_to_telegram():
    state = {"active_signals": []}
    signal_tracker.register_signal(
        state, symbol="TEST_TL", entry=Decimal("100"), stop=Decimal("98"),
        tp1=Decimal("103"), tp2=Decimal("105"), score=90, setup="BREAKOUT",
        now=1000, evidence={"shadow_mode": True}
    )
    signal = state["active_signals"][0]
    signal_tracker._event(signal, "STOP", Decimal("98"), 2000)
    assert signal_tracker.deliver_pending_events(
        state, lambda _msg: (_ for _ in ()).throw(AssertionError("must not send")), str
    ) == 0
