import json

import pandas as pd
import pytest

from candle_backfill import bind_history, repair
import market_data as md
from test_candle_backfill import NOW, frame, payload


@pytest.fixture(autouse=True)
def isolated_history():
    bind_history({})


def test_large_gap_gets_one_bounded_genuine_recovery_before_rejection():
    full = frame()
    calls = []
    def request(start, end):
        calls.append((start, end))
        return full[(full.timestamp >= start) & (full.timestamp < end)]
    result = repair(full.drop(range(10, 150)), 900, NOW, request, "SKY:15m")
    assert len(calls) == 1
    assert calls[0] == (int(full.timestamp.iloc[-205]), int(full.timestamp.iloc[-1]) + 900)
    assert len(result) == 205 and result.is_authentic.all()
    assert result.attrs["candle_quality"]["synthetic_count"] == 0
    assert result.attrs["backfill"]["requests"] == 1


@pytest.mark.parametrize("kind", ["empty", "partial", "stale", "synthetic", "bad_flag",
                                  "invalid_ohlc", "nonfinite", "off_grid", "conflict"])
def test_bad_recent_recovery_never_creates_an_authentic_window(kind):
    full = frame()
    rows = full.tail(205).astype({"open": float, "high": float, "low": float, "close": float}).copy()
    if kind == "empty": rows = rows.iloc[:0]
    elif kind == "partial": rows = rows.drop(100)
    elif kind == "stale": rows.timestamp -= 900 * 300
    elif kind == "synthetic": rows["is_authentic"] = False
    elif kind == "bad_flag": rows["is_authentic"] = "True"
    elif kind == "invalid_ohlc": rows.loc[100, "high"] = 1
    elif kind == "nonfinite": rows.loc[100, "high"] = float("inf")
    elif kind == "off_grid": rows.loc[100, "timestamp"] += 1
    elif kind == "conflict": rows.loc[200, "close"] = 10.5
    calls = []
    state = {}
    bind_history(state)
    with pytest.raises(ValueError, match="Backfill gap limit exceeded"):
        repair(full.drop(range(10, 150)), 900, NOW,
               lambda *args: calls.append(args) or rows, "SKY:15m")
    assert len(calls) == 1
    report = state["candle_gap_history"]["SKY:15m"]["report"]
    assert report["requests"] == 1
    assert report["status"] == "gap_limit_exceeded"
    assert report["returned_window_missing"] != 0
    json.dumps(state, allow_nan=False)


def test_transport_failure_is_recorded_without_retry_storm():
    calls = []
    state = {}
    bind_history(state)
    def failed(*args):
        calls.append(args)
        raise md.ParibuHTTPError("429 test response")
    with pytest.raises(ValueError, match="Backfill gap limit exceeded"):
        repair(frame().drop(range(10, 150)), 900, NOW, failed, "AXL:15m")
    assert len(calls) == 1
    assert "429" in state["candle_gap_history"]["AXL:15m"]["report"]["errors"][0]


def test_huge_old_gap_does_not_block_already_complete_recent_window():
    full = frame()
    ancient = full.head(1).copy()
    ancient.timestamp -= 900 * 10000
    rows = pd.concat([ancient, full], ignore_index=True)
    def forbidden(*args):
        pytest.fail("complete genuine recent window needs no request")
    result = repair(rows, 900, NOW, forbidden, "TEST:15m")
    assert len(result) == 205 and result.is_authentic.all()
    assert result.attrs["backfill"]["requests"] == 0
    assert result.attrs["backfill"]["returned_window_missing"] == 0


def test_market_data_integration_keeps_original_prices_and_source(monkeypatch):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    calls = []
    full = frame()
    def get(url, params, **kwargs):
        calls.append(params)
        return payload(full.drop(range(10, 150)) if len(calls) == 1 else full)
    monkeypatch.setattr(md, "get_json", get)
    result = md.fetch_candles("SKY_TL")
    assert len(calls) == 2  # Initial request plus one bounded recovery.
    assert result.attrs["source"] == "PARIBU"
    assert result.is_authentic.all()
    pd.testing.assert_frame_equal(
        result[["open", "high", "low", "close", "volume"]],
        full.tail(205).reset_index(drop=True)[["open", "high", "low", "close", "volume"]],
        check_dtype=False,
    )


def test_old_contiguous_history_does_not_pass_current_window_requirement():
    old = frame()
    old.timestamp -= 300 * 900
    calls = []
    with pytest.raises(ValueError, match="Backfill gap limit exceeded"):
        repair(old, 900, NOW + 61, lambda *a: calls.append(a) or old, "TEST:15m")
    assert len(calls) == 1


def test_unclosed_bar_does_not_replace_missing_last_closed_bar():
    full = frame()
    opened = full.tail(1).copy()
    opened.timestamp = NOW
    response = pd.concat([full.iloc[:-1], opened], ignore_index=True)
    with pytest.raises(ValueError, match="Backfill gap limit exceeded"):
        repair(full.iloc[:140], 900, NOW + 61, lambda *a: response, "TEST:15m")


def test_explicit_foreign_exchange_response_is_rejected():
    rows = frame()
    rows.attrs["source"] = "OTHER_EXCHANGE"
    with pytest.raises(ValueError, match="Non-Paribu"):
        repair(frame().drop(range(10, 150)), 900, NOW, lambda *a: rows, "TEST:15m")
