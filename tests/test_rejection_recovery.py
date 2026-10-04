from scanner import (
    _early_watch_followups,
    _record_early_watch_followup,
)


def test_score_78_recovery_watch_is_persistent_but_not_ready_confirmation():
    state = {"watchlist": {}, "early_watch_followups": {}}
    item = _record_early_watch_followup(
        state,
        symbol="AKT_TL",
        now=1000,
        candle_at=900,
        score=78,
        reason="score 78 < 80",
    )
    assert item["last_score"] == 78
    assert item["last_reason"] == "score 78 < 80"
    assert "AKT_TL" in _early_watch_followups(state)
    assert state["watchlist"] == {}


def test_score_recovery_watch_tracks_improvement_without_faking_confirmation():
    state = {"watchlist": {}, "early_watch_followups": {}}
    _record_early_watch_followup(
        state, symbol="AKT_TL", now=1000, candle_at=900,
        score=74, reason="score 74 < 80",
    )
    item = _record_early_watch_followup(
        state, symbol="AKT_TL", now=1900, candle_at=1800,
        score=79, reason="score 79 < 80",
    )
    assert item["max_score"] == 79
    assert item["last_candle_at"] == 1800
    assert state["watchlist"] == {}
