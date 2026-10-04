from decimal import Decimal

from scanner import _execution_followups, _record_execution_followup


def test_execution_followup_keeps_bounded_forensic_history():
    state = {"execution_followups": {}}
    for n in range(15):
        reason = "spread_too_high" if n % 2 == 0 else "imbalance_too_low"
        _record_execution_followup(
            state,
            symbol="OXT_TL",
            now=1000 + n,
            reason=reason,
            spread_pct=Decimal("0.31") + Decimal(n) / Decimal("100"),
            imbalance=Decimal("0.80"),
            best_ask=Decimal("0.497"),
        )

    item = _execution_followups(state)["OXT_TL"]
    assert item["checks"] == 15
    assert item["reason_counts"]["spread_too_high"] == 8
    assert item["reason_counts"]["imbalance_too_low"] == 7
    assert len(item["history"]) == 12
    assert item["history"][-1]["best_ask"] == "0.497"


def test_execution_journal_does_not_create_ready_confirmation():
    state = {"execution_followups": {}, "watchlist": {}}
    _record_execution_followup(
        state,
        symbol="ORCA_TL",
        now=1000,
        reason="spread_too_high",
        spread_pct=Decimal("0.60"),
        imbalance=Decimal("1.40"),
        best_ask=Decimal("99.933"),
    )
    assert state["watchlist"] == {}
    assert _execution_followups(state)["ORCA_TL"]["last_reason"] == "spread_too_high"
