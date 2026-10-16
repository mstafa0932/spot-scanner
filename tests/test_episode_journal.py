from episode_journal import record_episode_event


def test_episode_journal_keeps_execution_failure_as_research_wait():
    state = {}
    record_episode_event(
        state, symbol="SYN_TL", stage="radar", reason="hot_radar_detected",
        observed_at=100, run_id="1", metrics={"heat": "2.0"},
    )
    record_episode_event(
        state, symbol="SYN_TL", stage="book", reason="imbalance_too_low",
        observed_at=200, run_id="2", metrics={"imbalance": "0.43"},
    )
    item = state["candidate_episodes"]["SYN_TL"]
    assert item["status"] == "WAITING_EXECUTION_RESEARCH"
    assert [e["stage"] for e in item["events"]] == ["radar", "book"]


def test_episode_journal_is_idempotent_within_run_stage_reason():
    state = {}
    kwargs = dict(
        symbol="SUPER_TL", stage="book", reason="spread_too_high",
        observed_at=100, run_id="7", metrics={"spread_pct": "0.54"},
    )
    record_episode_event(state, **kwargs)
    record_episode_event(state, **kwargs)
    assert len(state["candidate_episodes"]["SUPER_TL"]["events"]) == 1


def test_episode_journal_does_not_change_trading_state():
    state = {"watchlist": {"ALT_TL": {"confirmations": 1}}}
    record_episode_event(
        state, symbol="ALT_TL", stage="execution", reason="imbalance_too_low",
        observed_at=100, run_id="9", metrics={},
    )
    assert state["watchlist"]["ALT_TL"]["confirmations"] == 1
