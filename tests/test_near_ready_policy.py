"""Notification policy through the real scanner with explicit offline fixtures.

These generated quotes/indicators are behavior tests, not historical prices.
Every Telegram/READY call is captured locally; no message or order is sent.
"""
import pytest

import scanner
import test_post_radar_recovery as harness


def setup(monkeypatch, tmp_path, near_success=True):
    clock, calls, scan = harness.pipeline(monkeypatch, tmp_path)
    clock["rsi"] = harness.D("60")
    messages, ready = [], []
    monkeypatch.setattr(scanner, "TELEGRAM_EARLY_WATCH_ALERTS", True)
    monkeypatch.setattr(scanner, "TELEGRAM_NEAR_READY_ALERTS", True, raising=False)
    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", True)
    monkeypatch.setattr(scanner, "send_telegram", lambda msg: messages.append(msg) or near_success)
    monkeypatch.setattr(scanner, "send_shadow_ready_alert",
                        lambda opp: ready.append(opp.symbol) or True)
    return clock, calls, scan, messages, ready


def test_general_early_watch_is_internal_even_with_legacy_alert_flag(monkeypatch, tmp_path):
    _, _, scan, messages, ready = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(scanner, "discovery_ok", lambda *args: (False, "4h clearly weak"))
    state = scan()
    assert harness.SYMBOL in state["early_watch_followups"]
    assert state["scan_diagnostics"][-1]["funnel"]["early_watch"] == 1
    assert messages == ready == []


def test_near_ready_is_once_per_watch_and_does_not_consume_ready_cooldowns(monkeypatch, tmp_path):
    clock, _, scan, messages, ready = setup(monkeypatch, tmp_path)
    first = scan()
    assert len(messages) == 1 and "NEAR READY" in messages[0]
    assert first["scan_diagnostics"][-1]["funnel"]["near_ready_alerts"] == 1
    assert first["shadow_sent_signals"] == {} and first["shadow_daily_alerts"] == []
    assert first["shadow_last_alert_at"] == 0 and first["active_signals"] == []
    clock["now"] += 600  # The same CLOSED candle cannot add a confirmation.
    repeated = scan()  # Reloads the actual saved state, including near dedup.
    assert len(messages) == 1 and ready == []
    assert len(repeated["watchlist"][harness.SYMBOL]["history"]) == 1
    clock["now"] = harness.NOW + 900
    qualified = scan()
    assert ready == [harness.SYMBOL]
    assert qualified["shadow_sent_signals"] == {harness.SYMBOL: harness.NOW + 900}
    scan()
    assert len(messages) == 1 and ready == [harness.SYMBOL]


def test_failed_near_delivery_cannot_block_later_ready(monkeypatch, tmp_path):
    clock, _, scan, messages, ready = setup(monkeypatch, tmp_path, near_success=False)
    first = scan()
    assert len(messages) == 1 and ready == []
    assert not first["watchlist"][harness.SYMBOL].get("near_ready_notified_at")
    assert first["shadow_last_alert_at"] == 0
    clock["now"] += 900
    second = scan()
    assert ready == [harness.SYMBOL]
    assert second["scan_diagnostics"][-1]["alert_sent"] is True


@pytest.mark.parametrize("blocker", ["btc", "synthetic", "rsi", "risk", "wide", "unavailable", "stale"])
def test_near_ready_requires_market_data_risk_and_a_fresh_acceptable_book(monkeypatch, tmp_path, blocker):
    clock, calls, scan, messages, ready = setup(monkeypatch, tmp_path)
    if blocker == "btc":
        clock["btc"] = False
    elif blocker == "synthetic":
        clock["synthetic"] = True
    elif blocker == "rsi":
        clock["rsi"] = harness.D("69.3")
    elif blocker == "risk":
        monkeypatch.setattr(scanner, "build_opportunity", lambda *args: None)
    elif blocker == "stale":
        get_book = scanner.get_order_book

        def delayed_book(symbol, *args):
            result = get_book(symbol, *args)
            if symbol == harness.SYMBOL and calls["books"].count(symbol) == 2:
                clock["now"] = harness.NOW + 961
            return result

        monkeypatch.setattr(scanner, "get_order_book", delayed_book)
    else:
        clock["refresh"] = blocker
    state = scan()
    assert messages == ready == []
    assert state["shadow_sent_signals"] == {} and state["active_signals"] == []


def test_different_markets_cannot_burst_near_ready_messages_in_one_candle(monkeypatch, tmp_path):
    clock, _, scan, messages, ready = setup(monkeypatch, tmp_path)
    analyze = scanner.analyze_symbol

    def another_eligible_market(frame):
        result = analyze(frame)
        if frame.attrs["symbol"] == "M0_TL":
            result.rsi14 = harness.D("60")
        return result

    monkeypatch.setattr(scanner, "analyze_symbol", another_eligible_market)
    first = scan()
    assert len(first["watchlist"]) == 2 and len(messages) == 1
    snapshot = scanner.get_market_snapshot
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: {
        key: value for key, value in snapshot().items()
        if key in {harness.SYMBOL, "M0_TL"}
    })
    clock["now"] += 600
    second = scan()
    assert second["scan_diagnostics"][-1]["funnel"]["discovery_passed"] == 2
    assert len(messages) == 1 and ready == []


def test_uncertain_near_send_survives_process_stop_without_repeating_or_blocking_ready(monkeypatch, tmp_path):
    clock, _, scan, messages, ready = setup(monkeypatch, tmp_path)
    snapshot = scanner.get_market_snapshot
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: {
        harness.SYMBOL: snapshot()[harness.SYMBOL]
    })

    def uncertain_send(message):
        messages.append(message)
        raise RuntimeError("process stopped after Telegram could accept")

    monkeypatch.setattr(scanner, "send_telegram", uncertain_send)
    with pytest.raises(RuntimeError, match="process stopped"):
        scan()
    monkeypatch.setattr(scanner, "send_telegram", lambda msg: messages.append(msg) or True)
    clock["now"] += 600
    scan()
    assert len(messages) == 1
    clock["now"] = harness.NOW + 900
    state = scan()
    assert ready == [harness.SYMBOL]
    assert state["shadow_sent_signals"] == {harness.SYMBOL: harness.NOW + 900}
