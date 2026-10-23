"""Momentum discovery follow-up tests: scheduling only, never an alert bypass."""
from decimal import Decimal as D

import pytest

import scanner
from test_scanner_research import prepare
from test_accumulation_radar import NOW


@pytest.mark.parametrize(
    "reason,score,volume,btc_ok,expected",
    [
        ("15m momentum not constructive", 87, "2.333", True, True),
        ("15m momentum not constructive", 80, "1.05", True, True),
        ("15m momentum not constructive", 79, "2.333", True, False),
        ("15m momentum not constructive", 87, "1.04", True, False),
        ("15m momentum not constructive", 87, "2.333", False, False),
        ("RSI outside watch range", 87, "2.333", True, False),
        ("4h clearly weak", 87, "2.333", True, False),
    ],
)
def test_followup_eligibility_does_not_change_discovery_or_ready(
    reason, score, volume, btc_ok, expected
):
    assert scanner._discovery_momentum_followup_ok(
        reason=reason, score=score, volume_ratio=D(volume), btc_ok=btc_ok
    ) is expected


def test_high_score_rejection_preserved_and_prioritized_only_after_new_closed_bar(
    monkeypatch, tmp_path
):
    ticker, _book, tech = prepare(monkeypatch, tmp_path)
    tech.volume_ratio = D("2.333")
    monkeypatch.setattr(scanner, "score_candidate", lambda *a: (87, []))
    monkeypatch.setattr(
        scanner, "discovery_ok",
        lambda *a: (False, "15m momentum not constructive")
    )
    scanner.run_scanner()
    state = scanner.load_state()
    decision = state["scan_diagnostics"][-1]["symbols"][ticker.symbol]
    assert (decision["stage"], decision["reason"], decision["score"]) == (
        "discovery", "15m momentum not constructive", 87
    )
    assert decision["above_ema21"] is None  # Stub never invents a missing flag.
    assert state["scan_diagnostics"][-1]["signal_recorded"] is False
    assert state["scan_diagnostics"][-1]["alert_sent"] is False
    assert state["active_signals"] == []
    followup = state["early_watch_followups"][ticker.symbol]
    assert followup["last_reason"] == "15m momentum not constructive"
    assert followup["last_score"] == 87

    def priority(at):
        coverage = scanner.CoverageCycle(
            state.setdefault("coverage_scheduler", {}),
            {ticker.symbol: ticker},
        )
        return scanner._hot_radar_order([ticker], state, coverage, at)[1]

    # An identical still-forming bar is not a new READY confirmation.
    current = priority(NOW)
    assert ticker.symbol not in current["early_watch_symbols"]
    # Once a new authentic 15m candle *could* have closed, a scheduled
    # revisit can occur; eligibility will still be independently checked.
    later = priority(NOW + 900)
    assert ticker.symbol in later["early_watch_symbols"]
    assert ticker.symbol in later["priority_symbols"]


@pytest.mark.parametrize(
    "score,volume,btc_ok,reason",
    [
        (79, "2.333", True, "15m momentum not constructive"),
        (87, "1.04", True, "15m momentum not constructive"),
        (87, "2.333", False, "15m momentum not constructive"),
        (87, "2.333", True, "RSI outside watch range"),
    ],
)
def test_no_followup_for_ineligible_rejects(
    monkeypatch, tmp_path, score, volume, btc_ok, reason
):
    ticker, _book, tech = prepare(monkeypatch, tmp_path)
    tech.volume_ratio = D(volume)
    monkeypatch.setattr(scanner, "btc_gate", lambda: (btc_ok, None, "fixture"))
    monkeypatch.setattr(scanner, "score_candidate", lambda *a: (score, []))
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (False, reason))
    scanner.run_scanner()
    state = scanner.load_state()
    assert ticker.symbol not in state["early_watch_followups"]
    assert state["scan_diagnostics"][-1]["symbols"][ticker.symbol]["reason"] == reason
    assert not state["scan_diagnostics"][-1]["signal_recorded"]
    assert not state["scan_diagnostics"][-1]["alert_sent"]
