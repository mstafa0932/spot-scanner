from decimal import Decimal as D
from types import SimpleNamespace as NS
from copy import deepcopy

import pandas as pd
import pytest
import accumulation_radar as radar

NOW = 1800000000


def fixture(now=NOW):
    rows = [{"timestamp": now - (49-i)*900, "open": 100,
             "high": 101, "low": 99, "close": 100.5,
             "volume": 200 if i >= 46 else 100} for i in range(49)]
    frame = pd.DataFrame(rows)
    frame.attrs.update(source="PARIBU", resolution="15m")
    book = NS(best_bid=D("100.5"), best_ask=D("100.6"),
              bid_notional=D("200000"), ask_notional=D("100000"), imbalance_ratio=D("2"))
    return frame, book


def test_hypothesis_not_signal():
    frame, book = fixture()
    result, reason = radar.evaluate(frame, book, NOW)
    assert reason == "qualifies"
    assert "not_an_entry_signal" in result["limitations"]
    assert result["volume_ratio"] == 2


@pytest.mark.parametrize("kind,reason", [
    ("stale", "stale_candles"), ("open", "open_candle"),
    ("gap", "candle_gap"), ("duplicate", "candle_gap"),
    ("nan", "invalid_candles"), ("bad_ohlc", "invalid_ohlc"),
    ("source", "source_or_timeframe"), ("zero_volume", "no_volume_expansion"),
    ("one_spike", "isolated_volume_spike"), ("pump", "extended_or_falling"),
    ("spread", "wide_spread"), ("thin", "weak_book"),
    ("book_nan", "invalid_book"), ("short", "insufficient_history"),
    ("live_jump", "live_price_outside_setup"),
])
def test_rejections(kind, reason):
    frame, book = fixture()
    now = NOW
    if kind == "stale": now += 1300
    if kind == "open": now -= 1
    if kind == "gap": frame.loc[20, "timestamp"] -= 60
    if kind == "duplicate": frame.loc[20, "timestamp"] = frame.loc[19, "timestamp"]
    if kind == "nan": frame.loc[10, "volume"] = float("nan")
    if kind == "bad_ohlc": frame.loc[10, "low"] = 102
    if kind == "source": frame.attrs["source"] = "OTHER"
    if kind == "zero_volume": frame["volume"] = 0
    if kind == "one_spike": frame.loc[46:48, "volume"] = [50, 50, 1000]
    if kind == "pump": frame.loc[48, ["close", "high"]] = [110, 111]
    if kind == "spread": book.best_ask = D("101")
    if kind == "thin": book.bid_notional = D("1000")
    if kind == "book_nan": book.imbalance_ratio = D("NaN")
    if kind == "short": frame = frame.tail(20)
    if kind == "live_jump": book.best_bid, book.best_ask = D("103"), D("103.1")
    assert radar.evaluate(frame, book, now)[1] == reason


def confirmed():
    frame, book = fixture()
    state = radar.advance(None, [("SYN_TL", frame, book)], {}, NOW, True, "ok")
    frame, book = fixture(NOW+900)
    return radar.advance(state, [("SYN_TL", frame, book)], {}, NOW+900, False, "blocked")


def test_distinct_bars_and_duplicate_suppression():
    frame, book = fixture()
    first = radar.advance(None, [("SYN_TL", frame, book)], {}, NOW, True, "ok")
    repeat = radar.advance(first, [("SYN_TL", frame, book)], {}, NOW+600, True, "ok")
    assert repeat["events"] == []
    assert repeat["watches"]["SYN_TL"]["confirmations"] == 1
    state = confirmed()
    assert len(state["events"]) == 1
    assert state["events"][0]["btc_ok"] is False
    next_frame, book = fixture(NOW+1800)
    again = radar.advance(state, [("SYN_TL", next_frame, book)], {}, NOW+1800, True, "ok")
    assert len(again["events"]) == 1


def test_missing_scan_resets_streak():
    frame, book = fixture()
    state = radar.advance(None, [("SYN_TL", frame, book)], {}, NOW, True, "ok")
    state = radar.advance(state, [], {}, NOW+900, True, "ok")
    frame, book = fixture(NOW+1800)
    state = radar.advance(state, [("SYN_TL", frame, book)], {}, NOW+1800, True, "ok")
    assert state["events"] == []


def test_long_gap_resets_streak():
    frame, book = fixture()
    state = radar.advance(None, [("SYN_TL", frame, book)], {}, NOW, True, "ok")
    frame, book = fixture(NOW+3600)
    state = radar.advance(state, [("SYN_TL", frame, book)], {}, NOW+3600, True, "ok")
    assert state["events"] == []


def test_outcomes_are_sampled_and_do_not_backfill():
    state = confirmed()
    original = deepcopy(state)
    at = NOW+900+4000
    state = radar.advance(state, [], {"SYN_TL": NS(last=D("98"))}, at, True, "ok")
    event = state["events"][0]
    assert event["invalidated_at"] == at
    assert event["outcomes"]["3600"]["actual_age_seconds"] == 4000
    assert original["events"][0]["outcomes"] == {}
    late = radar.advance(original, [], {"SYN_TL": NS(last=D("110"))}, NOW+900+7000, True, "ok")
    assert "3600" not in late["events"][0]["outcomes"]
    final = radar.advance(original, [], {"SYN_TL": NS(last=D("110"))}, NOW+900+87000, True, "ok")
    assert "86400" in final["events"][0]["outcomes"]


def test_event_history_bounded():
    state = confirmed()
    state["events"] *= 300
    result = radar.advance(state, [], {}, NOW+1800, True, "ok")
    assert len(result["events"]) == 200
