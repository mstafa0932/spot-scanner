"""Replays original public Paribu replies, without network, messages, or orders.

Zero-volume intervals below are records published by Paribu's native chart
service, not locally filled candles and not evidence of executed trades.
"""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

import market_data as md
from candle_backfill import bind_history
from indicator_engine import analyze_symbol


EVIDENCE = Path(__file__).parent / "audit_evidence" / "native_ohlc_20261017"


def capture(symbol, interval="15m", window="recorded_300"):
    return json.loads((EVIDENCE / f"{symbol}_{interval}_{window}.json").read_text())["request"]


@pytest.fixture(autouse=True)
def isolated_recovery():
    bind_history({})
    md._BACKFILL_RATE_LIMITED = False
    md._BACKFILL_RETRY_AFTER = None
    yield
    bind_history({})
    md._BACKFILL_RATE_LIMITED = False
    md._BACKFILL_RETRY_AFTER = None


@pytest.mark.parametrize("symbol", ["nmr_tl", "mina_tl", "sand_tl", "btc_tl", "rad_tl", "alt_tl"])
def test_original_native_reply_supplies_closed_continuous_history(monkeypatch, symbol):
    recorded = capture(symbol)
    now = (recorded["params"]["to"] + 1) // 1000
    monkeypatch.setattr(md.time, "time", lambda: now)
    calls = []

    def read(url, params, **kwargs):
        calls.append((url, params))
        assert url == "https://web.paribu.com/v1/chart/ohlc"
        assert params["marketType"] == "crypto"
        assert params["symbol"] == symbol
        assert params["interval"] == "15m"
        assert params["limit"] == 300
        # Native chart timestamps and cursor are milliseconds; to is exclusive
        # in the UI and the HTTP request subtracts one millisecond.
        assert params["to"] == (now // 900) * 900_000 - 1
        return copy.deepcopy(recorded["payload"])

    monkeypatch.setattr(md, "get_json", read)
    result = md.fetch_candles(symbol.upper())
    assert len(calls) == 1
    assert len(result) == 250 >= md.MIN_VALID_CANDLES
    assert result.timestamp.diff().iloc[1:].eq(900).all()
    assert (result.timestamp + 900 <= now).all()
    assert result.is_authentic.all()
    assert result.attrs["candle_quality"]["synthetic_count"] == 0
    assert result.attrs["candle_endpoint"] == "https://web.paribu.com/v1/chart/ohlc"

    native = pd.DataFrame(recorded["payload"]["payload"]["candles"])
    native["timestamp"] = native.date.astype("int64") // 1000
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    expected = native[native.timestamp + 900 <= now].tail(250)[columns]
    for col in columns:
        expected[col] = pd.to_numeric(expected[col])
    pd.testing.assert_frame_equal(result[columns], expected.reset_index(drop=True), check_dtype=False)
    # Retain explicit provenance of exchange-published empty intervals. Do not
    # claim a trade took place or silently turn locally synthesized rows real.
    assert result.exchange_zero_volume.eq(result.volume.eq(0)).all()
    assert result.attrs["published_zero_volume_count"] == int(result.volume.eq(0).sum())
    assert result.attrs["backfill"]["requests"] == 0
    assert analyze_symbol(result) is not None


@pytest.mark.parametrize("symbol", ["btc_tl", "nmr_tl", "mina_tl", "sand_tl"])
@pytest.mark.parametrize("interval,seconds", [("15m", 900), ("1h", 3600), ("4h", 14400)])
def test_all_scanner_timeframes_replay_original_native_data(monkeypatch, symbol, interval, seconds):
    recorded = capture(symbol, interval, "current_300")
    now = (recorded["params"]["to"] + 1) // 1000
    monkeypatch.setattr(md.time, "time", lambda: now)
    monkeypatch.setattr(md, "get_json", lambda *args, **kwargs: copy.deepcopy(recorded["payload"]))
    result = md.fetch_candles(symbol, interval)
    assert len(result) == 250
    assert result.timestamp.diff().iloc[1:].eq(seconds).all()
    assert result.is_authentic.all()
    assert result.attrs["candle_quality"]["synthetic_count"] == 0
    assert analyze_symbol(result) is not None


@pytest.mark.parametrize("defect", ["missing_volume", "nonfinite", "bad_ms", "bad_ohlc",
                                    "negative_volume", "synthetic", "non_paribu", "bad_status"])
def test_invalid_native_reply_fails_closed(monkeypatch, defect):
    recorded = capture("btc_tl")
    data = recorded["payload"]
    row = data["payload"]["candles"][100]
    if defect == "missing_volume":
        del row["volume"]
    elif defect == "nonfinite":
        row["close"] = "NaN"
    elif defect == "bad_ms":
        row["date"] += 1
    elif defect == "bad_ohlc":
        row["high"] = "1"
    elif defect == "negative_volume":
        row["volume"] = "-1"
    elif defect == "synthetic":
        row["is_authentic"] = False
        row["data_quality"] = "SYNTHETIC_FFILL"
    elif defect == "non_paribu":
        row["source"] = "OTHER_EXCHANGE"
    elif defect == "bad_status":
        data["message"]["severity"] = "error"
    monkeypatch.setattr(md.time, "time", lambda: (recorded["params"]["to"] + 1) // 1000)
    calls = []
    monkeypatch.setattr(md, "get_json", lambda *args, **kwargs: calls.append(args) or data)
    with pytest.raises(md.ParibuDataError):
        md.fetch_candles("BTC_TL")
    assert len(calls) == 1


def test_native_rate_limit_is_not_retried_through_legacy_endpoint(monkeypatch):
    calls = []

    def rate_limited(url, params, **kwargs):
        calls.append(url)
        raise md.ParibuHTTPError("HTTP 429", status_code=429, retry_after="60")

    monkeypatch.setattr(md, "get_json", rate_limited)
    with pytest.raises(md.CandleUnavailableError, match="429"):
        md.fetch_candles("BTC_TL")
    with pytest.raises(md.CandleUnavailableError, match="429"):
        md.fetch_candles("MINA_TL")
    assert calls == ["https://web.paribu.com/v1/chart/ohlc"]


@pytest.mark.parametrize("symbol", ["btc_tl", "nmr_tl", "sand_tl", "rad_tl", "alt_tl"])
def test_traded_closed_bars_match_legacy_and_extra_records_are_explicitly_empty(symbol):
    recorded = capture(symbol)
    now = (recorded["params"]["to"] + 1) // 1000
    native = recorded["payload"]["payload"]["candles"]
    legacy = json.loads((EVIDENCE / "legacy_15m_comparison.json").read_text())["replies"][f"candles:{symbol}:current"]
    old = {stamp: index for index, stamp in enumerate(legacy["t"])}
    matched = extra = 0
    for row in native:
        stamp = row["date"] // 1000
        if stamp + 900 > now:
            continue
        if stamp in old:
            index = old[stamp]
            for field, array in (("open", "o"), ("high", "h"), ("low", "l"),
                                 ("close", "c"), ("volume", "v")):
                assert md.to_decimal(row[field]) == md.to_decimal(legacy[array][index])
            matched += 1
        else:
            assert row["volume"] == "0"
            assert row["open"] == row["high"] == row["low"] == row["close"]
            extra += 1
    assert matched >= 180
    if symbol == "nmr_tl":
        assert extra == 0
    else:
        assert extra > 0


def test_source_change_does_not_reuse_legacy_negative_cache(monkeypatch):
    # An unavailable period in legacy advanced history is not a negative
    # observation about the native source. Its old cache must not stop a read.
    from test_candle_backfill import NOW, frame, payload
    full = frame()
    missing = int(full.timestamp.iloc[100])
    state = {"candle_gap_history": {"btc_tl:15m": {
        "missing_timestamps": [missing],
        "report": {"negative_cache_attempted_at": NOW - 60,
                   "negative_cache_expires_at": NOW + 840},
    }}}
    bind_history(state)
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    calls = []

    def read(url, params, **kwargs):
        calls.append(params)
        return payload(full.drop(100) if len(calls) == 1 else full)

    monkeypatch.setattr(md, "get_json", read)
    result = md.fetch_candles("BTC_TL")
    assert len(calls) == 2
    assert result.is_authentic.all()
    assert result.attrs["backfill"]["recovered"] == 1
    assert result.attrs["candle_quality"]["synthetic_count"] == 0
    assert state["candle_gap_history"]["btc_tl:15m:native_v1"]["report"]["requests"] == 1


@pytest.mark.parametrize("location", ["root", "payload", "message", "display", "meta"])
@pytest.mark.parametrize("field,value", [
    ("source", "OTHER_EXCHANGE"),
    ("is_authentic", False),
    ("data_quality", "SYNTHETIC_FFILL"),
])
def test_native_provenance_metadata_cannot_be_discarded(monkeypatch, location, field, value):
    """A source/synthetic declaration outside a row must block promotion too."""
    recorded = capture("btc_tl")
    data = copy.deepcopy(recorded["payload"])
    if location == "root":
        target = data
    elif location == "display":
        target = data["message"]["display"]
    elif location == "meta":
        data["meta"] = {}
        target = data["meta"]
    else:
        target = data[location]
    target[field] = value
    monkeypatch.setattr(md.time, "time", lambda: (recorded["params"]["to"] + 1) // 1000)
    calls = []
    monkeypatch.setattr(md, "get_json", lambda *args, **kwargs: calls.append(args) or data)
    with pytest.raises(md.ParibuSchemaError):
        md.fetch_candles("BTC_TL")
    assert len(calls) == 1


def test_positive_native_volume_cannot_underflow_into_a_no_trade_record(monkeypatch):
    recorded = capture("btc_tl")
    data = copy.deepcopy(recorded["payload"])
    data["payload"]["candles"][100]["volume"] = "1e-400"
    monkeypatch.setattr(md.time, "time", lambda: (recorded["params"]["to"] + 1) // 1000)
    monkeypatch.setattr(md, "get_json", lambda *args, **kwargs: data)
    with pytest.raises(md.ParibuSchemaError, match="underflow"):
        md.fetch_candles("BTC_TL")
