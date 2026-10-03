from __future__ import annotations

"""Research-only episode journal for radar/candidate decisions.

This module deliberately does not decide whether a market is tradable.  It
stores chronological evidence so transient execution failures (spread/book)
can be distinguished from setup invalidation without weakening live gates.
"""

from typing import Any

MAX_EPISODES = 240
MAX_EVENTS_PER_EPISODE = 96
TERMINAL_STATES = {"READY", "INVALIDATED", "EXPIRED"}


def _store(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("candidate_episodes")
    if not isinstance(value, dict):
        value = {}
        state["candidate_episodes"] = value
    return value


def record_episode_event(
    state: dict[str, Any],
    *,
    symbol: str,
    stage: str,
    reason: str,
    observed_at: int,
    run_id: str,
    metrics: dict[str, Any] | None = None,
) -> None:
    """Append an idempotent chronological event; never changes scanner gates."""
    episodes = _store(state)
    item = episodes.get(symbol)
    if not isinstance(item, dict) or item.get("status") in TERMINAL_STATES:
        item = {
            "episode_id": f"{symbol}:{run_id}",
            "symbol": symbol,
            "opened_at": int(observed_at),
            "status": "OBSERVED",
            "events": [],
        }
        episodes[symbol] = item

    event = {
        "run_id": str(run_id),
        "observed_at": int(observed_at),
        "stage": str(stage),
        "reason": str(reason),
        "metrics": dict(metrics or {}),
    }
    events = item.setdefault("events", [])
    if not isinstance(events, list):
        events = []
        item["events"] = events
    key = (event["run_id"], event["stage"], event["reason"])
    if not events or (events[-1].get("run_id"), events[-1].get("stage"),
                      events[-1].get("reason")) != key:
        events.append(event)
    item["events"] = events[-MAX_EVENTS_PER_EPISODE:]
    item["last_seen_at"] = int(observed_at)
    item["last_stage"] = str(stage)
    item["last_reason"] = str(reason)

    # Descriptive research state only.  WAITING_EXECUTION must never be
    # interpreted by scanner.py as permission to bypass a live execution gate.
    if stage == "radar":
        item["status"] = "RADAR"
    elif stage == "early_watch":
        item["status"] = "EARLY_WATCH"
    elif stage in {"book", "execution"} and reason in {
        "spread_too_high", "imbalance_too_low"
    }:
        item["status"] = "WAITING_EXECUTION_RESEARCH"
    elif stage == "trigger" and reason.startswith("watching: confirmations"):
        item["status"] = "CONFIRMING"
    elif stage in {"shadow", "notification"}:
        item["status"] = "READY"

    if len(episodes) > MAX_EPISODES:
        ordered = sorted(
            episodes.items(),
            key=lambda pair: int(pair[1].get("last_seen_at", 0) or 0)
            if isinstance(pair[1], dict) else 0,
        )
        for old_symbol, _ in ordered[: len(episodes) - MAX_EPISODES]:
            episodes.pop(old_symbol, None)
