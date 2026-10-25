"""An intervening book rejection must not erase the bounded risk-followup origin.

This is scheduling only: no rejected market becomes eligible for READY.
"""
from decimal import Decimal as D
from types import SimpleNamespace as NS

import scanner


def test_intervening_spread_rejection_does_not_erase_risk_followup_origin():
    state = scanner._empty_state()
    candidate = NS(
        symbol="PEPE_TL",
        score=88,
        book=NS(
            best_ask=D("0.00022325"),
            spread_percent=D("0.1"),
            imbalance_ratio=D("1.4"),
        ),
    )
    at = 1792732392
    assert scanner._retain_risk_rejection_followup(
        state, candidate, "risk_out_of_range", at
    )
    item = state["execution_followups"]["PEPE_TL"]
    assert item["first_seen"] == at

    # The following recheck encounters a book that is too wide to use.
    scanner._record_execution_followup(
        state,
        symbol="PEPE_TL",
        now=at + 600,
        reason="spread_too_high",
        spread_pct=D("0.6"),
    )
    assert item["last_reason"] == "spread_too_high"
    assert item["first_seen"] == at

    # RSI then overheats; no READY is allowed, but bounded priority survives.
    assert scanner._preserve_risk_followup_during_rsi_overheat(
        state, "PEPE_TL", scanner.WATCH_RSI_HIGH + D("0.01"),
        "RSI outside watch range", at + 1200
    )
    assert state["execution_followups"]["PEPE_TL"]["first_seen"] == at
    assert state["shadow_sent_signals"] == {}
    assert state["active_signals"] == []

    # The original fixed lifetime is not extended by book or RSI rejection.
    scanner._prune_execution_followups(
        state, at + scanner.EXECUTION_FOLLOWUP_TTL_SECONDS + 1
    )
    assert "PEPE_TL" not in state["execution_followups"]


def test_book_only_followup_cannot_claim_prior_risk_origin():
    state = scanner._empty_state()
    scanner._record_execution_followup(
        state, symbol="BOOKONLY_TL", now=1792732392,
        reason="spread_too_high", spread_pct=D("0.6"),
    )
    assert not scanner._preserve_risk_followup_during_rsi_overheat(
        state, "BOOKONLY_TL", scanner.WATCH_RSI_HIGH + D("0.01"),
        "RSI outside watch range", 1792732992,
    )
    assert "risk_rejection_origin" not in state["execution_followups"]["BOOKONLY_TL"]
    assert state["shadow_sent_signals"] == {}
    assert state["active_signals"] == []
