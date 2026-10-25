"""Offline regression: rejected setups retain exact predicate evidence only.

No orders, no Telegram requests, no extra Paribu requests. The existing
setup_type and trigger_check predicates remain the sole eligibility authority.
"""
from decimal import Decimal as D
from types import SimpleNamespace as NS

import scanner
from test_scanner_research import prepare


def test_veto_evidence_reports_nonconstructive_recovery_without_requalifying():
    # SAHARA-like strong score/volume, but no bullish/setup candle.
    tech = NS(
        breakout=False, is_pullback=False, mean_touch=False,
        is_bullish_candle=False, is_above_ema9=True, is_above_ema21=True,
        ema21=D("100"), ema50=D("99"), macd_histogram=D("0.1"),
        macd_line=D("1.2"), macd_signal=D("1.1"), volume_ratio=D("2.35"),
        rsi14=D("60.27"), recent_return_3=D("1.8"),
    )
    assert scanner.setup_type(tech) == (False, "NO_TRIGGER_SETUP")
    evidence = scanner._setup_veto_diagnostics(tech)
    assert evidence["breakout"] is False
    assert evidence["is_pullback"] is False
    assert evidence["bullish_candle"] is False
    assert evidence["macd_hist_positive"] is True
    assert evidence["volume_ratio_gte_1_20"] is True
    assert evidence["pullback_rsi_lte_60"] is False


def test_veto_diagnostics_do_not_invent_missing_indicator_values():
    tech = NS(breakout=False, volume_ratio=D("1.3"))
    evidence = scanner._setup_veto_diagnostics(tech)
    assert evidence["breakout"] is False
    assert evidence["is_pullback"] is None
    assert evidence["ema21_gte_ema50"] is None
    assert evidence["macd_hist_positive"] is None
    assert evidence["volume_ratio_gte_1_20"] is True


def test_full_scanner_records_setup_veto_but_sends_no_alert(monkeypatch, tmp_path):
    ticker, book, tech = prepare(monkeypatch, tmp_path)
    tech.breakout = False
    tech.is_pullback = False
    tech.is_bullish_candle = False
    tech.is_above_ema9 = True
    tech.is_above_ema21 = True
    tech.ema21, tech.ema50 = D("100"), D("99")
    tech.macd_histogram = D("0.1")
    tech.macd_line, tech.macd_signal = D("1.2"), D("1.1")
    tech.mean_touch = False
    tech.recent_return_3 = D("1")
    tech.recent_return_12 = D("2")
    tech.recent_return_48 = D("4")
    tech.rsi14 = D("60")
    book.imbalance_ratio = D("1.4")
    book.largest_bid_wall_share = D("0.20")
    book.largest_ask_wall_share = D("0.10")
    monkeypatch.setattr(scanner, "score_candidate", lambda *a: (89, []))
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (True, "OK"))
    # Exercise the real setup_type() and trigger_check() decisions using
    # offline book/candle fixtures. No actual Telegram or Paribu requests.
    scanner.run_scanner()
    state = scanner.load_state()
    row = state["scan_diagnostics"][-1]["symbols"][ticker.symbol]
    assert row["stage"] == "trigger"
    assert row["reason"] == "setup not ready"
    assert row["score"] == 89
    assert row["setup_veto_diagnostics"]["bullish_candle"] is False
    assert row["setup_veto_diagnostics"]["macd_hist_positive"] is True
    assert row["setup_veto_diagnostics"]["volume_ratio_gte_1_20"] is True
    assert state["scan_diagnostics"][-1]["alert_sent"] is False
    assert state["active_signals"] == []
    assert state["shadow_sent_signals"] == {}
    assert state["sent_signals"] == {}
