"""Expired observations must not masquerade as a current strategy watch."""

from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

import scanner
import test_post_radar_recovery as harness


def test_recorded_rad_episode_expires_without_erasing_new_book_followup(
    monkeypatch, tmp_path
):
    clock, calls, scan = harness.pipeline(monkeypatch, tmp_path)
    clock["now"] = 1792249326  # 2026-10-17 15:02:06 UTC diagnostic capture.
    state = scanner.load_state()
    state["candidate_lifecycle"]["RAD_TL"] = {
        "candidate_id": "RAD_TL:38763797295", "symbol": "RAD_TL",
        "first_seen_run": "38763797295",
        "first_seen_at": "2026-10-15T15:50:47+00:00", "max_score": 73,
        "current_state": "early_watch", "history": [{
            "run_id": "38776454352", "ts": "2026-10-15T17:20:49+00:00",
            "score": 73, "state": "early_watch", "reason": "4h clearly weak",
        }],
    }
    scanner._record_execution_followup(
        state, symbol="RAD_TL", now=clock["now"]-600, reason="spread_too_high"
    )
    scanner.save_state(state)
    original_snapshot = scanner.get_market_snapshot
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: {
        **original_snapshot(),
        "RAD_TL": NS(symbol="RAD_TL", last=harness.D("14.8"),
                     quote_volume=harness.D("25000000")),
    })
    original_candles = scanner.fetch_candles

    def inauthentic(symbol, *args):
        frame = original_candles(symbol, *args)
        if symbol == "RAD_TL":
            frame.loc[frame.index[-1], "is_authentic"] = False
        return frame

    monkeypatch.setattr(scanner, "fetch_candles", inauthentic)
    result = scan()
    episode = result["candidate_lifecycle"]["RAD_TL"]
    assert episode["current_state"] == "expired"
    assert episode["candidate_id"] == "RAD_TL:38763797295"
    assert episode["max_score"] == 73
    assert episode["history"][-1]["reason"] == "watch_window_expired"
    assert "RAD_TL" in result["scan_diagnostics"][-1]["expired_candidates"]
    # A separate fresh execution follow-up must keep its actual scheduling slot.
    assert "RAD_TL" in result["execution_followups"]
    assert "RAD_TL" in calls["books"]
    assert result["scan_diagnostics"][-1]["symbols"]["RAD_TL"]["reason"] == "synthetic_candles_present"
    assert not result["active_signals"] and not result["shadow_sent_signals"]


def stale_episode(now, state="early_watch"):
    timestamp = datetime.fromtimestamp(now-scanner.WATCHLIST_TTL_SECONDS-1, timezone.utc).isoformat()
    return {"candidate_id": "RAD_TL:old", "symbol": "RAD_TL",
            "first_seen_run": "old", "first_seen_at": timestamp,
            "max_score": 73, "current_state": state,
            "history": [{"run_id": "old", "ts": timestamp,
                         "score": 73, "state": state}]}


@pytest.mark.parametrize("field", ["watchlist", "early_watch_followups"])
@pytest.mark.parametrize("offset", [-600, 600])
def test_old_episode_with_live_or_future_strategy_anchor_is_preserved(field, offset):
    now = harness.NOW
    episode = stale_episode(now)
    state = {"candidate_lifecycle": {"RAD_TL": episode},
             field: {"RAD_TL": {"last_seen": now+offset}}}
    assert scanner._expire_candidate_lifecycles(state, now) == []
    assert episode["current_state"] == "early_watch"


def test_recent_unknown_future_and_terminal_records_are_not_expired():
    now = harness.NOW
    episodes = {name: stale_episode(now, name) for name in ("rejected", "expired", "shadow_entry")}
    for name, timestamp in (
        ("recent", datetime.fromtimestamp(now-600, timezone.utc).isoformat()),
        ("future", datetime.fromtimestamp(now+600, timezone.utc).isoformat()),
        ("unknown", "unparseable"),
    ):
        episode = stale_episode(now)
        episode.update(first_seen_at=timestamp, history=[])
        episodes[name] = episode
    state = {"candidate_lifecycle": episodes}
    assert scanner._expire_candidate_lifecycles(state, now) == []
    assert all(episodes[name]["current_state"] == "early_watch" for name in ("recent", "future", "unknown"))


def test_expiry_persists_once_and_new_discovery_starts_new_episode(monkeypatch, tmp_path):
    now = harness.NOW
    monkeypatch.setattr(scanner, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setenv("GITHUB_RUN_ID", "expiry-run")
    state = scanner._empty_state()
    state["candidate_lifecycle"]["RAD_TL"] = stale_episode(now)
    assert scanner._expire_candidate_lifecycles(state, now) == ["RAD_TL"]
    scanner.save_state(state)
    restored = scanner.load_state()
    assert scanner._expire_candidate_lifecycles(restored, now+600) == []
    assert len(restored["candidate_lifecycle"]["RAD_TL"]["history"]) == 2
    monkeypatch.setenv("GITHUB_RUN_ID", "new-observation")
    fresh = scanner._candidate_lifecycle_update(
        restored, symbol="RAD_TL", score=67, lifecycle_state="discovery", now=now+900
    )
    assert fresh["candidate_id"] == "RAD_TL:new-observation"
    assert fresh["max_score"] == 67
    assert len(fresh["history"]) == 1
    assert fresh["first_seen_run"] == "new-observation"
