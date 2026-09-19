from copy import deepcopy
from decimal import Decimal as D
from types import SimpleNamespace as NS
import json

import pytest
import scanner
import accumulation_radar
from test_accumulation_radar import fixture, NOW


def prepare(monkeypatch, tmp_path):
    monkeypatch.setattr(scanner, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(scanner.time, "time", lambda: NOW)
    monkeypatch.setattr(scanner, "update_active_signals", lambda *a: [])
    monkeypatch.setattr(scanner, "update_near_miss_outcomes", lambda *a: dict(checked=0, updated=0, errors=0))
    monkeypatch.setattr(scanner, "btc_gate", lambda: (True, None, "BTC acceptable"))
    ticker = NS(symbol="SYN_TL", last=D("100.5"), quote_volume=D("10000000"))
    frame, book = fixture()
    book.spread_percent = D("0.1")
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: {ticker.symbol: ticker})
    monkeypatch.setattr(scanner, "get_order_book", lambda *a: book)
    monkeypatch.setattr(scanner, "fetch_candles", lambda *a: frame)
    monkeypatch.setattr(scanner, "recent_authentic", lambda *a, **k: True)
    tech = NS(rsi14=D("60"), recent_return_3=D("1"), volume_ratio=D("2"))
    monkeypatch.setattr(scanner, "analyze_symbol", lambda *a: tech)
    monkeypatch.setattr(scanner, "score_candidate", lambda *a: (85, []))
    monkeypatch.setattr(scanner, "send_telegram", lambda *a: pytest.fail("unexpected Telegram"))
    return ticker, book, tech


def test_rejection_recorded_and_state_roundtrip(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (False, "anti-FOMO: 3C already +4%"))
    scanner.run_scanner()
    state = scanner.load_state()
    assert state["scan_diagnostics"][-1]["symbols"]["SYN_TL"]["reason"].startswith("anti-FOMO")
    assert state["accumulation_radar"]["watches"]["SYN_TL"]["confirmations"] == 1
    scanner.run_scanner()
    assert len(scanner.load_state()["scan_diagnostics"]) == 2


@pytest.mark.parametrize("disabled,broken", [(True, False), (False, True), (False, False)])
def test_entry_path_survives_research_modes(monkeypatch, tmp_path, disabled, broken):
    prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("SHADOW_RADAR_ENABLED", "false" if disabled else "true")
    if broken:
        def fail(*a): raise ValueError("simulated research failure")
        monkeypatch.setattr(accumulation_radar, "advance", fail)
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (True, "OK"))
    monkeypatch.setattr(scanner, "setup_type", lambda *a: (True, "BREAKOUT"))
    monkeypatch.setattr(scanner, "_update_watchlist", lambda state, c, now: state["watchlist"].update({c.symbol: {}}))
    monkeypatch.setattr(scanner, "trigger_check", lambda *a: (True, "OK", D("2"), 2))
    monkeypatch.setattr(scanner, "_global_alert_allowed", lambda *a: True)
    monkeypatch.setattr(scanner, "_symbol_alert_allowed", lambda *a: True)
    opportunity = NS(symbol="SYN_TL", entry=D("100"), stop=D("98.5"),
                     tp1=D("101.5"), tp2=D("102.3"), score=85, setup="BREAKOUT",
                     spread_pct=D("0.1"), imbalance=D("2"), volume_ratio=D("2"),
                     quote_volume=D("10000000"), bid_wall_share=D("0.2"), ask_wall_share=D("0.2"))
    monkeypatch.setattr(scanner, "build_opportunity", lambda *a: opportunity)
    monkeypatch.setattr(scanner, "format_opportunity", lambda *a: "original-entry")
    messages = []
    monkeypatch.setattr(scanner, "send_telegram", lambda msg: messages.append(msg) or True)
    scanner.run_scanner()
    state = scanner.load_state()
    assert messages == ["original-entry"]
    assert state["sent_signals"] == {"SYN_TL": NOW}
    assert state["active_signals"][0]["entry"] == "100"
    assert state["scan_diagnostics"][-1]["alert_sent"] is True
    expected = "disabled" if disabled else "error:ValueError" if broken else "shadow_only"
    assert state["scan_diagnostics"][-1]["radar_status"] == expected


def test_data_error_and_snapshot_error_visible(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    def bad_book(*a): raise ValueError("no data")
    monkeypatch.setattr(scanner, "get_order_book", bad_book)
    scanner.run_scanner()
    assert scanner.load_state()["scan_diagnostics"][-1]["symbols"]["SYN_TL"]["reason"] == "orderbook_error:ValueError"
    def bad_snapshot(): raise scanner.ParibuDataError("no data")
    monkeypatch.setattr(scanner, "get_market_snapshot", bad_snapshot)
    with pytest.raises(RuntimeError, match="scan incomplete"):
        scanner.run_scanner()
    assert scanner.load_state()["scan_diagnostics"][-1]["status"] == "snapshot_failed"


def test_capacity_visible_and_history_bounded(monkeypatch, tmp_path):
    ticker, book, tech = prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 0)
    for _ in range(15): scanner.run_scanner()
    state = scanner.load_state()
    assert len(state["scan_diagnostics"]) == 12
    assert state["scan_diagnostics"][-1]["symbols"]["SYN_TL"]["reason"] == "not_evaluated_capacity"


@pytest.mark.parametrize("unavailable", [False, True])
def test_pre_alert_refresh_blocks_deteriorated_or_missing_book(monkeypatch, tmp_path, unavailable):
    _, book, _ = prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (True, "OK"))
    monkeypatch.setattr(scanner, "setup_type", lambda *a: (True, "BREAKOUT"))
    monkeypatch.setattr(scanner, "_update_watchlist", lambda state, c, now: state["watchlist"].update({c.symbol: {}}))
    monkeypatch.setattr(scanner, "_global_alert_allowed", lambda *a: True)
    monkeypatch.setattr(scanner, "_symbol_alert_allowed", lambda *a: True)
    answers = iter([(True, "OK", D(2), 2), (False, "spread widened", D(2), 2)])
    monkeypatch.setattr(scanner, "trigger_check", lambda *a: next(answers))
    calls = []
    def refresh(*a):
        calls.append(a)
        if unavailable and len(calls) == 2:
            raise scanner.ParibuDataError("unavailable")
        return book
    monkeypatch.setattr(scanner, "get_order_book", refresh)
    scanner.run_scanner()
    assert len(calls) == 2
    state = scanner.load_state()
    assert not state["scan_diagnostics"][-1]["alert_sent"]
    reason = state["scan_diagnostics"][-1]["symbols"]["SYN_TL"]["reason"]
    assert reason.startswith("fresh_book_unavailable" if unavailable else "fresh_book_rejected")
