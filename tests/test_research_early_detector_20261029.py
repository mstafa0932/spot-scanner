"""Deterministic offline tests; no exchange access, scanner mutation or trades."""
from copy import deepcopy

from research_early_detector import evaluate, mark_followups, replay

LAST_OPEN = 1793270700
NOW = LAST_OPEN + 900 + 60


def candles():
    rows = []
    for i in range(23):
        close = 100 + i * 0.004
        rows.append({
            "timestamp": LAST_OPEN - (22 - i) * 900,
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": 100 if i < 20 else 200,
            "authentic": True,
        })
    return rows


def test_early_volume_watch_not_ready_or_a_trade():
    result = evaluate("TEST_TL", candles(), NOW)
    assert result["candidate"] is True
    assert result["decision"] == "early_volume_watch"
    assert result["volume_ratio"] == 2
    assert result["is_ready"] is False
    assert result["is_trade"] is False


def test_closed_bars_only():
    assert evaluate("TEST_TL", candles(), LAST_OPEN + 899)["decision"] == "unclosed_candle"
    assert evaluate("TEST_TL", candles(), NOW + 1300)["decision"] == "stale_candle"


def test_reject_inauthentic_and_discontinuous():
    fake = candles()
    fake[-2]["authentic"] = False
    assert evaluate("TEST_TL", fake, NOW)["decision"] == "unauthenticated_candle"
    gap = candles()
    gap[-2]["timestamp"] += 60
    assert evaluate("TEST_TL", gap, NOW)["decision"] == "noncontiguous_candles"


def test_reject_zero_and_isolated_volume_spikes():
    quiet = candles()
    for b in quiet[-3:]:
        b["volume"] = 0
    assert evaluate("TEST_TL", quiet, NOW)["decision"] == "volume_not_sustained"
    one_spike = candles()
    one_spike[-3]["volume"] = 100
    one_spike[-2]["volume"] = 100
    one_spike[-1]["volume"] = 800
    assert evaluate("TEST_TL", one_spike, NOW)["decision"] == "volume_not_sustained"


def test_prevent_fomo_after_price_jump():
    jump = candles()
    jump[-1]["close"] = jump[-1]["open"] + 4
    jump[-1]["high"] = jump[-1]["close"] + 0.1
    assert evaluate("TEST_TL", jump, NOW)["decision"] == "extended_or_falling"


def test_marks_never_become_executions_or_pnl():
    rec = evaluate("TEST_TL", candles(), NOW)
    later = [
        {"timestamp": NOW + 3600 - 1, "last": 200},
        {"timestamp": NOW + 3600 + 60, "last": 103},
        {"timestamp": NOW + 14400 + 60, "last": 97},
    ]
    result = mark_followups(rec, later)
    assert result["marks_not_fills"]["3600"]["last"] == 103
    assert result["marks_not_fills"]["14400"]["last"] == 97
    assert result["candidate"] and not result["is_trade"]
    assert result["marks_not_fills"]["3600"]["return_pct_before_costs"] > 0
    assert result["marks_not_fills"]["14400"]["return_pct_before_costs"] < 0


def test_replay_keeps_controls_and_deduplicates_same_closed_bar():
    positive = {"symbol": "W_TL", "observed_at": NOW, "candles": candles()}
    negative = {"symbol": "L_TL", "observed_at": NOW, "candles": candles()}
    for b in negative["candles"][-3:]:
        b["volume"] = 0
    report = replay([positive, deepcopy(positive), negative])
    assert report["records"] == 2
    assert report["early_watches"] == 1
    assert report["controls"] == 1
    assert report["no_trade_execution"] is True


def test_independent_of_future_prices_and_book_data():
    base = {"symbol": "TEST_TL", "observed_at": NOW, "candles": candles()}
    with_future = deepcopy(base)
    with_future["future_prices"] = [{"timestamp": NOW + 3600, "last": 999999}]
    with_future["book_spread_pct"] = 50
    with_future["rsi"] = 99
    before, after = replay([base])["decisions"][0], replay([with_future])["decisions"][0]
    assert before["candidate"] == after["candidate"]
    assert before["decision"] == after["decision"]
    assert before["reference_close"] == after["reference_close"]
