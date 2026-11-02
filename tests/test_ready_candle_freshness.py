"""Entry alerts must preserve each frame's closed-candle freshness contract.

All inputs and delays are software fixtures; no network, Telegram or trades.
"""
from decimal import Decimal as D

import pytest

import scanner
import test_post_radar_recovery as harness


@pytest.mark.parametrize("refresh_delay,expected_alerts", [(-1, 0), (0, 1), (960, 1), (961, 0)])
def test_ready_revalidates_candle_age_after_the_final_book(
    monkeypatch, tmp_path, refresh_delay, expected_alerts,
):
    clock, calls, scan = harness.pipeline(monkeypatch, tmp_path)
    scan()  # First genuinely distinct closed candle; RSI not yet eligible.
    clock.update(now=harness.NOW + 900, rsi=D("60"))
    messages = []
    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", True)
    monkeypatch.setattr(scanner, "send_shadow_ready_alert", lambda opp: messages.append(opp) or True)
    get_book = scanner.get_order_book

    def delayed_book(symbol, *args):
        result = get_book(symbol, *args)
        if symbol == harness.SYMBOL and calls["books"].count(symbol) == 2:
            clock["now"] += refresh_delay
        return result

    monkeypatch.setattr(scanner, "get_order_book", delayed_book)
    state = scan()
    assert len(messages) == expected_alerts
    assert calls["books"].count(harness.SYMBOL) == 2
    assert len(state["watchlist"][harness.SYMBOL]["history"]) == 2
    if not expected_alerts:
        assert state["active_signals"] == []
        assert state["shadow_sent_signals"] == {}
        assert state["scan_diagnostics"][-1]["symbols"][harness.SYMBOL]["reason"] == (
            "fresh_book_rejected: candle data not fresh"
        )
        assert state["execution_followups"][harness.SYMBOL]["last_reason"] == (
            "fresh_book_rejected: candle data not fresh"
        )


def test_a_fresh_later_scan_can_requalify_after_stale_ready_rejection(monkeypatch, tmp_path):
    clock, calls, scan = harness.pipeline(monkeypatch, tmp_path)
    scan()
    clock.update(now=harness.NOW + 900, rsi=D("60"))
    messages = []
    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", True)
    monkeypatch.setattr(scanner, "send_shadow_ready_alert", lambda opp: messages.append(opp) or True)
    get_book = scanner.get_order_book
    delay = [True]

    def delayed_once(symbol, *args):
        result = get_book(symbol, *args)
        if symbol == harness.SYMBOL and calls["books"].count(symbol) == 2 and delay[0]:
            clock["now"] += 961
            delay[0] = False
        return result

    monkeypatch.setattr(scanner, "get_order_book", delayed_once)
    stale = scan()
    assert messages == [] and stale["active_signals"] == []
    assert harness.SYMBOL in stale["execution_followups"]
    current = scan()  # Re-fetches all three frames, including a new closed 15m bar.
    assert len(messages) == 1 and len(current["active_signals"]) == 1
    assert harness.SYMBOL not in current["execution_followups"]
    assert len({o["candle_at"] for o in current["watchlist"][harness.SYMBOL]["history"]}) == 3


@pytest.mark.parametrize("timeframe,seconds", [("1h", 3600), ("4h", 14400)])
@pytest.mark.parametrize("near", [False, True], ids=["ready", "near-ready"])
def test_alert_rechecks_other_timeframes_that_age_during_refresh(monkeypatch, tmp_path, timeframe, seconds, near):
    clock, calls, scan = harness.pipeline(monkeypatch, tmp_path)
    snapshot = scanner.get_market_snapshot
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: {
        harness.SYMBOL: snapshot()[harness.SYMBOL],
    })
    if not near:
        clock["now"] = harness.NOW + seconds - 900
        scan()
    clock.update(now=harness.NOW + seconds, rsi=D("60"))
    messages = []
    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", True)
    monkeypatch.setattr(scanner, "TELEGRAM_NEAR_READY_ALERTS", near)
    monkeypatch.setattr(scanner, "send_shadow_ready_alert", lambda opp: messages.append(opp) or True)
    monkeypatch.setattr(scanner, "send_telegram", lambda msg: messages.append(msg) or True)
    fetch = scanner.fetch_candles

    def publication_delayed_frame(symbol, interval, *args):
        frame = fetch(symbol, interval, *args)
        if symbol == harness.SYMBOL and interval == timeframe:
            frame["timestamp"] -= seconds
            age = clock["now"] - (int(frame["timestamp"].iloc[-1]) + seconds)
            assert 0 <= age <= seconds + 60  # Valid at fetch, not yet stale.
        return frame

    monkeypatch.setattr(scanner, "fetch_candles", publication_delayed_frame)
    get_book = scanner.get_order_book

    def delayed_book(symbol, *args):
        result = get_book(symbol, *args)
        if symbol == harness.SYMBOL and calls["books"].count(symbol) == 2:
            clock["now"] += 61
        return result

    monkeypatch.setattr(scanner, "get_order_book", delayed_book)
    state = scan()
    assert messages == [] and state["active_signals"] == []
    if near:
        assert state["scan_diagnostics"][-1]["funnel"]["near_ready_alerts"] == 0
        assert not state["watchlist"][harness.SYMBOL].get("near_ready_attempted_at")
    else:
        assert state["scan_diagnostics"][-1]["symbols"][harness.SYMBOL]["reason"] == (
            "fresh_book_rejected: candle data not fresh"
        )
