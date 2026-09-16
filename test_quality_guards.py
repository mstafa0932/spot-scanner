from decimal import Decimal as D
from types import SimpleNamespace as NS
import pandas as pd
import pytest
import market_data as md
import scanner
from execution_research import evaluate_depth

NOW = 1800000000


def candles():
    return pd.DataFrame([dict(timestamp=NOW - (250-i)*900,
                              open=10, high=11, low=9, close=10, volume=1)
                         for i in range(250)])


def test_fresh_contiguous_pass(monkeypatch):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    assert len(md.validate_candles(candles(), "15m")) == 250


@pytest.mark.parametrize("fault", ["stale", "gap", "infinity"])
def test_bad_candles_rejected(monkeypatch, fault):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    df = candles()
    if fault == "stale":
        df.timestamp -= 3600
    elif fault == "gap":
        df = df.drop(100)
    else:
        df["volume"] = float("inf")
    with pytest.raises(md.ParibuDataError):
        md.validate_candles(df, "15m")


def test_all_unfinished_candles_excluded(monkeypatch):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    df = candles()
    extra = df.tail(2).copy()
    extra.timestamp = [NOW, NOW+900]
    assert len(md.validate_candles(pd.concat([df, extra]), "15m")) == 250


def test_repeat_candle_is_not_confirmation():
    state = {}
    candidate = NS(symbol="SYN_TL", candle_at=NOW-900, score=80, reasons=[], setup="WATCHING",
                   book=NS(best_ask=D(10), imbalance_ratio=D(2), spread_percent=D(".1"),
                           largest_bid_wall_share=D(".1"), largest_ask_wall_share=D(".1")),
                   tech_15=NS(volume_ratio=D(2), rsi14=D(50), recent_return_3=D(1)))
    item = scanner._update_watchlist(state, candidate, NOW)
    scanner._update_watchlist(state, candidate, NOW+60)
    assert len(scanner._recent_observations(item, NOW+60)) == 1
    candidate.candle_at += 900
    scanner._update_watchlist(state, candidate, NOW+900)
    assert len(scanner._recent_observations(item, NOW+900)) == 2


def test_legacy_and_duplicate_observations_not_counted():
    item = {"history": [{"time": NOW}, {"time": NOW, "candle_at": NOW-900},
                        {"time": NOW, "candle_at": NOW-900}]}
    assert len(scanner._recent_observations(item, NOW)) == 1


def test_depth_cost_and_insufficient_depth():
    book = NS(asks=((D(10), D(20000)),), bids=((D("9.9"), D(20000)),))
    result = evaluate_depth(book)
    assert result["status"] == "snapshot_depth_available"
    assert D(result["immediate_roundtrip_loss_tl"]) > 1000
    assert not result["limit_fill_guaranteed"]
    book.asks = ((D(10), D(1)),)
    assert evaluate_depth(book)["status"] == "insufficient_ask_depth"
    book.asks = ((D(10), D(20000)),)
    book.bids = ((D("9.9"), D(1)),)
    assert evaluate_depth(book)["status"] == "insufficient_bid_depth"
