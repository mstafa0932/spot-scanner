from scanner import (
    _execution_followups,
    _record_execution_followup,
    _prune_execution_followups,
    WATCHLIST_TTL_SECONDS,
)


def test_execution_followup_persists_transient_book_rejection():
    state = {}
    _record_execution_followup(
        state, symbol="SYN_TL", now=1000, reason="imbalance_too_low"
    )
    item = _execution_followups(state)["SYN_TL"]
    assert item["last_reason"] == "imbalance_too_low"
    assert item["checks"] == 1
    _record_execution_followup(
        state, symbol="SYN_TL", now=1100, reason="spread_too_high"
    )
    item = _execution_followups(state)["SYN_TL"]
    assert item["checks"] == 2
    assert item["last_reason"] == "spread_too_high"


def test_execution_followup_expires_at_existing_watch_ttl():
    state = {}
    _record_execution_followup(
        state, symbol="SUPER_TL", now=1000, reason="spread_too_high"
    )
    _prune_execution_followups(state, 1000 + WATCHLIST_TTL_SECONDS + 1)
    assert "SUPER_TL" not in _execution_followups(state)


def test_execution_followup_state_is_isolated_from_ready_watchlist():
    state = {"watchlist": {"ALT_TL": {"confirmations": 1}}}
    _record_execution_followup(
        state, symbol="ALT_TL", now=1000, reason="imbalance_too_low"
    )
    assert state["watchlist"]["ALT_TL"]["confirmations"] == 1
    assert _execution_followups(state)["ALT_TL"]["checks"] == 1
