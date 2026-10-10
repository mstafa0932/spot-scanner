"""Offline scanner regressions for a strong watch across real pipeline gates.

Quotes and indicator results are deterministic fixtures, never production feeds.
The discovery, trigger, risk, scheduler, and state persistence code are real.
"""

from decimal import Decimal as D
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import scanner


NOW = 1_800_000_000
SYMBOL = "RLC_TL"


def pipeline(monkeypatch, tmp_path, contended=False):
    clock = {"now": NOW, "rsi": D("69.3"), "refresh": None,
             "btc": True, "synthetic": False}
    calls = {"books": [], "candles": []}
    # The watch has lower turnover than every competing market. Normal least-
    # recently-attempted polling cannot give it the next slot under congestion.
    tickers = [NS(symbol=f"M{i}_TL", last=D("100"), quote_volume=D("20000000"))
               for i in range(233)]
    tickers.append(NS(symbol=SYMBOL, last=D("100"), quote_volume=D("10000000")))
    book = NS(best_bid=D("100"), best_ask=D("100.25"),
              spread_percent=D("0.25"), imbalance_ratio=D("2"),
              largest_bid_wall_share=D("0.20"), largest_ask_wall_share=D("0.20"),
              bids=(), asks=())

    def get_book(symbol, *args):
        calls["books"].append(symbol)
        if symbol == SYMBOL and calls["books"].count(symbol) == 2:
            if clock["refresh"] == "unavailable":
                raise scanner.ParibuDataError("offline refresh unavailable")
            if clock["refresh"] == "wide":
                return NS(**{**vars(book), "spread_percent": D("0.8"),
                             "best_ask": D("100.8")})
        return book

    def candles(symbol, interval, *args):
        calls["candles"].append((symbol, interval))
        seconds = {"15m": 900, "1h": 3600, "4h": 14400}[interval]
        last = (clock["now"] // seconds - 1) * seconds
        frame = pd.DataFrame([
            {"timestamp": last - (79-i)*seconds, "open": 100, "high": 100.5,
             "low": 99.5, "close": 100.2, "volume": 100, "is_authentic": True}
            for i in range(80)
        ])
        frame.attrs.update(source="PARIBU", resolution=interval, symbol=symbol)
        if symbol == SYMBOL and clock["synthetic"]:
            frame.loc[frame.index[-1], "is_authentic"] = False
        return frame

    def indicators(frame):
        rsi = clock["rsi"] if frame.attrs["symbol"] == SYMBOL else D("30")
        return NS(current_close=D("100.2"), ema9=D("100.1"), ema21=D("100"),
                  ema50=D("99.8"), atr14=D("1"), swing_low=D("99"),
                  rsi14=rsi, volume_ratio=D("1.5"), macd_histogram=D("0.1"),
                  macd_line=D("0.2"), macd_signal=D("0.1"),
                  recent_return_3=D("1"), recent_return_12=D("1.5"),
                  recent_return_48=D("2"), is_above_ema9=True,
                  is_above_ema21=True, is_uptrend=True, is_bullish_candle=True,
                  is_pullback=False, breakout=False, mean_touch=False)

    monkeypatch.setattr(scanner, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(scanner.time, "time", lambda: clock["now"])
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 80)
    monkeypatch.setattr(scanner, "MAX_TECHNICAL_MARKETS", 24)
    monkeypatch.setattr(scanner, "SHADOW_MODE", True)
    monkeypatch.setenv("SHADOW_MODE", "true")
    monkeypatch.setenv("SHADOW_RADAR_ENABLED", "false")
    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", False)
    monkeypatch.setattr(scanner, "send_telegram", lambda *a: pytest.fail("offline test sent Telegram"))
    monkeypatch.setattr(scanner, "update_active_signals", lambda *a: [])
    monkeypatch.setattr(scanner, "btc_gate", lambda: (clock["btc"], None, "offline BTC gate"))
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: {t.symbol: t for t in tickers})
    monkeypatch.setattr(scanner, "get_order_book", get_book)
    monkeypatch.setattr(scanner, "fetch_candles", candles)
    monkeypatch.setattr(scanner, "analyze_symbol", indicators)

    root = scanner._empty_state()
    # A prior hot-radar rejection is already being followed by existing #31.
    scanner._record_execution_followup(root, symbol=SYMBOL, now=NOW-600,
                                       reason="imbalance_too_low")
    if contended:
        previous_candle = (NOW // 900 - 2) * 900
        for i in range(4):
            name = f"M{i}_TL"
            root["watchlist"][name] = {"last_seen": NOW-900, "history": [
                {"time": NOW-900, "candle_at": previous_candle}]}
            root["candidate_lifecycle"][name] = {"current_state": "confirmation_1_of_2"}
        for i in range(4, 8):
            scanner._record_early_watch_followup(
                root, symbol=f"M{i}_TL", now=NOW-900, candle_at=previous_candle,
                score=83, reason="offline competing watch")
        for i in range(8, 11):
            scanner._record_execution_followup(
                root, symbol=f"M{i}_TL", now=NOW-900, reason="imbalance_too_low")
    scanner.save_state(root)

    def scan():
        calls["books"].clear()
        calls["candles"].clear()
        scanner.run_scanner()
        return scanner.load_state()

    return clock, calls, scan


@pytest.mark.parametrize("refresh", ["wide", "unavailable"])
def test_strong_followup_survives_final_book_rejection_then_gets_actual_slot(
    monkeypatch, tmp_path, refresh
):
    clock, calls, scan = pipeline(monkeypatch, tmp_path)
    first = scan()
    assert calls["books"][0] == SYMBOL
    assert first["watchlist"][SYMBOL]["last_score"] == 83
    assert "RSI 69.3 outside alert range" in first["early_watch_followups"][SYMBOL]["last_reason"]
    assert not first["active_signals"]

    clock.update(now=NOW+900, rsi=D("60"), refresh=refresh)
    second = scan()  # loads the state saved by the preceding invocation
    assert calls["books"].count(SYMBOL) == 2
    assert second["scan_diagnostics"][-1]["symbols"][SYMBOL]["reason"].startswith("fresh_book_")
    assert len(second["watchlist"][SYMBOL]["history"]) == 2
    assert not second["active_signals"]
    # A temporary rejection on the last quote must not erase the existing
    # watch's bounded scheduling priority before another eligible candle.
    clock.update(now=NOW+1800, refresh=None)
    third = scan()
    assert calls["books"][0] == SYMBOL  # actual slot, not mere JSON membership
    assert (SYMBOL, "15m") in calls["candles"]
    assert SYMBOL in third["scan_diagnostics"][-1]["hot_radar"]["technical_priority_symbols"]
    assert SYMBOL in second["execution_followups"], "final book rejection lost the strong follow-up"
    assert SYMBOL not in third["execution_followups"]
    assert len(third["active_signals"]) == 1
    assert third["active_signals"][0]["symbol"] == SYMBOL
    assert third["shadow_sent_signals"] == {SYMBOL: NOW+1800}
    assert len({o["candle_at"] for o in third["watchlist"][SYMBOL]["history"]}) == 3


def test_final_book_followup_gets_slot_among_other_priority_groups(monkeypatch, tmp_path):
    clock, calls, scan = pipeline(monkeypatch, tmp_path, contended=True)
    first = scan()
    priority = first["scan_diagnostics"][-1]["hot_radar"]
    assert len(priority["technical_priority_symbols"]) == 12
    assert priority["technical_priority_symbols"][-1] == SYMBOL
    assert len(first["watchlist"][SYMBOL]["history"]) == 1

    clock.update(now=NOW+900, rsi=D("60"), refresh="wide")
    rejected = scan()
    assert calls["books"].count(SYMBOL) == 2
    assert not rejected["active_signals"]

    clock.update(now=NOW+1800, refresh=None)
    recovered = scan()
    assert SYMBOL in calls["books"]
    assert (SYMBOL, "15m") in calls["candles"]
    assert SYMBOL in recovered["scan_diagnostics"][-1]["hot_radar"]["technical_priority_symbols"]
    assert len(recovered["active_signals"]) == 1
    assert recovered["active_signals"][0]["symbol"] == SYMBOL
    assert len(set(calls["books"])) == 80


def test_repeated_final_book_rejections_keep_fixed_ttl_and_release_priority(monkeypatch, tmp_path):
    clock, calls, scan = pipeline(monkeypatch, tmp_path)
    scan()
    clock.update(now=NOW+900, rsi=D("60"), refresh="wide")
    first_rejection = scan()
    started = first_rejection["execution_followups"][SYMBOL]["first_seen"]

    clock["now"] += 900
    repeated = scan()
    assert calls["books"][0] == SYMBOL
    episode = repeated["execution_followups"][SYMBOL]
    assert episode["first_seen"] == started
    assert episode["checks"] == 2
    assert [event["at"] for event in episode["history"]] == [NOW+900, NOW+1800]
    assert not repeated["active_signals"]
    assert len(set(calls["books"])) == 80  # the original fair budget remains available

    clock["now"] = started + scanner.EXECUTION_FOLLOWUP_TTL_SECONDS + 1
    expired = scan()
    assert SYMBOL not in expired["execution_followups"]
    assert SYMBOL not in calls["books"]
    assert SYMBOL not in expired["scan_diagnostics"][-1]["hot_radar"]["selected_priority_symbols"]
    assert not expired["active_signals"]


@pytest.mark.parametrize("blocking_gate", ["synthetic", "btc"])
def test_final_book_followup_rechecks_safety_gates_before_recovery(
    monkeypatch, tmp_path, blocking_gate
):
    clock, calls, scan = pipeline(monkeypatch, tmp_path)
    scan()
    clock.update(now=NOW+900, rsi=D("60"), refresh="unavailable")
    rejected = scan()
    assert len(rejected["watchlist"][SYMBOL]["history"]) == 2

    clock.update(now=NOW+1800, refresh=None)
    clock[blocking_gate] = blocking_gate == "synthetic"
    blocked = scan()
    assert calls["books"][0] == SYMBOL
    reason = blocked["scan_diagnostics"][-1]["symbols"][SYMBOL]["reason"]
    if blocking_gate == "synthetic":
        assert reason == "synthetic_candles_present"
        assert len(blocked["watchlist"][SYMBOL]["history"]) == 2
        assert SYMBOL in blocked["execution_followups"]
    else:
        assert reason.startswith("BTC blocked:")
        assert SYMBOL in blocked["early_watch_followups"]
    assert not blocked["active_signals"]
    assert not blocked["shadow_sent_signals"]

    clock.update(now=NOW+2700, synthetic=False, btc=True)
    recovered = scan()
    assert calls["books"][0] == SYMBOL
    assert len(recovered["active_signals"]) == 1
    assert recovered["shadow_sent_signals"] == {SYMBOL: NOW+2700}


def test_existing_rlc_fix_keeps_real_trigger_gate_and_distinct_candles(monkeypatch, tmp_path):
    clock, calls, scan = pipeline(monkeypatch, tmp_path)
    first = scan()
    assert first["watchlist"][SYMBOL]["last_score"] == 83
    assert SYMBOL in first["early_watch_followups"]
    assert not first["active_signals"]

    clock.update(now=NOW+600, rsi=D("60"))  # still the same closed candle
    second = scan()
    assert len(second["watchlist"][SYMBOL]["history"]) == 1
    assert not second["active_signals"]

    clock.update(now=NOW+900, btc=False)
    third = scan()
    assert calls["books"][0] == SYMBOL
    assert third["scan_diagnostics"][-1]["symbols"][SYMBOL]["reason"].startswith("BTC blocked")
    assert not third["active_signals"]

    clock.update(now=NOW+1800, btc=True)
    fourth = scan()
    assert calls["books"][0] == SYMBOL
    assert len(fourth["active_signals"]) == 1


def test_early_watch_followup_survives_low_score_trigger_rejection(monkeypatch, tmp_path):
    """A discovery pass must not silently erase a previously scheduled watch."""
    clock, calls, scan = pipeline(monkeypatch, tmp_path)
    initial = scan()
    original = initial["early_watch_followups"][SYMBOL]
    original_seen = original["last_seen"]
    # A low-score trigger rejection should retain the original episode without
    # renewing its TTL. Use a deterministic trigger gate in this focused test.
    monkeypatch.setattr(scanner, "NEAR_MISS_MIN_SCORE", 100)
    # Keep RSI outside the READY trigger range so the second scan is rejected.
    # RSI=60 would legitimately pass READY and clear the follow-up.
    clock.update(now=NOW + 900, rsi=D("69.3"))
    rejected = scan()
    assert SYMBOL in rejected["early_watch_followups"]
    assert rejected["early_watch_followups"][SYMBOL]["last_seen"] == original_seen
    assert not rejected["active_signals"]
