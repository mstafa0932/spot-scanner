"""Offline regressions informed by the 2026-10-17 Actions observations.

Only timestamps, rejection metrics, and gap counts come from runtime evidence.
OHLCV values and HTTP responses below are deterministic test fixtures, not
recorded exchange candles or evidence that Paribu can recover those gaps.
"""

import json
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import market_data as md
import scanner
from candle_backfill import bind_history, MAX_REQUESTS
from test_candle_backfill import NOW, frame, payload, requested_span


@pytest.fixture(autouse=True)
def isolated_recovery():
    bind_history({})
    md._BACKFILL_RATE_LIMITED = False
    md._BACKFILL_RETRY_AFTER = None
    yield
    bind_history({})
    md._BACKFILL_RATE_LIMITED = False
    md._BACKFILL_RETRY_AFTER = None


def fixture_window(first, last):
    return pd.DataFrame([
        dict(timestamp=stamp, open=10, high=11, low=9, close=10, volume=5)
        for stamp in range(first, last + 900, 900)
    ])


@pytest.mark.parametrize("legacy_cache", [False, True])
def test_mina_nine_observed_gap_clusters_do_not_cache_untested_history(
    monkeypatch, legacy_cache
):
    # Run 38995993480: fifteen missing bars in nine clusters. The live code
    # queried only the oldest four clusters, then cached all fifteen bars.
    now = 1792205032
    missing = [1791935100, 1791939600, 1792018800, 1792019700,
               1792027800, 1792028700, 1792095300, 1792102500,
               1792106100, 1792107900, 1792108800, 1792109700,
               1792110600, 1792111500, 1792120500]
    full = fixture_window(1791934200, 1792203300)
    initial = full[~full.timestamp.isin(missing)]
    state = {}
    if legacy_cache:
        state["candle_gap_history"] = {"mina_tl:15m": {
            "observed_at": now - 600,
            "missing_timestamps": missing,
            "report": {"negative_cache_attempted_at": now - 600,
                       "negative_cache_expires_at": now + 300},
        }}
    bind_history(state)
    monkeypatch.setattr(md.time, "time", lambda: now)
    calls = []

    def get_json(url, params, **kwargs):
        calls.append(dict(params))
        if params["limit"] == 300:
            return payload(initial)
        # Return valid context rows, but none of the requested missing bars.
        # An error-free no-recovery result differs from a transport failure.
        start, end = requested_span(params)
        return payload(initial[initial.timestamp.between(start, end)])

    monkeypatch.setattr(md, "get_json", get_json)
    for pulse in range(2):
        with pytest.raises(md.CandleUnavailableError, match="Synthetic candle limit exceeded"):
            md.fetch_candles("MINA_TL")
        assert len(calls) == (pulse + 1) * (1 + MAX_REQUESTS)
        report = state["candle_gap_history"]["mina_tl:15m:native_v1"]["report"]
        assert report["errors"] == []
        assert report["requests"] == MAX_REQUESTS
        assert report["missing_after"] == 15
        assert report["status"] == "failed_closed"
        assert "negative_cache_expires_at" not in report
        first_recovery = calls[pulse * (1 + MAX_REQUESTS) + 1]
        assert requested_span(first_recovery) == (
            max(missing) - 900, max(missing) + 900
        )
        # The second pulse must use persisted history, not a fresh cache.
        state = json.loads(json.dumps(state, allow_nan=False))
        bind_history(state)


def test_newest_recovery_restores_authentic_indicator_suffix(monkeypatch):
    # Exercise the complete raw-response -> recovery -> validated-candles path.
    # Recovering the newest gap leaves 229 uninterrupted fixture bars. The
    # oldest-first implementation instead leaves that gap unqueried.
    full = frame()
    initial = full.drop([5, 10, 15, 20, 90])
    newest = int(full.timestamp.iloc[90])
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    calls = []

    def get_json(url, params, **kwargs):
        calls.append(dict(params))
        if len(calls) == 1:
            return payload(initial)
        start, end = requested_span(params)
        if start <= newest <= end:
            return payload(full.iloc[[90]])
        return payload(initial[initial.timestamp.between(start, end)])

    monkeypatch.setattr(md, "get_json", get_json)
    result = md.fetch_candles("MINA_TL")
    assert len(calls) == 1 + MAX_REQUESTS
    assert result.attrs["source"] == "PARIBU"
    assert result.attrs["backfill"]["status"] == "authentic_contiguous_suffix"
    assert result.attrs["backfill"]["recovered"] == 1
    assert result.attrs["candle_quality"]["synthetic_count"] == 0
    assert result.is_authentic.all()
    pd.testing.assert_frame_equal(
        result[list(full.columns)], full.iloc[21:].reset_index(drop=True),
        check_dtype=False,
    )


def test_nmr_score_80_does_not_bypass_btc_or_alert_liquidity():
    # Run 39032308887: BTC was the first logged blocker. Even a repaired BTC
    # feed would not make this observed book pass the existing 1.12x gate.
    candidate = NS(symbol="NMR_TL", score=80, book=NS(
        spread_percent=D("0.1927219136152363677587575105"),
        imbalance_ratio=D("1.036359910696463347197255140"),
    ))
    ready, reason, _, confirmations = scanner.trigger_check(
        candidate, {}, False, "BTC synthetic candles present", 1792239881
    )
    assert not ready and confirmations == 0
    assert reason == "BTC blocked: BTC synthetic candles present"
    ready, reason, _, confirmations = scanner.trigger_check(
        candidate, {}, True, "offline healthy BTC", 1792239881
    )
    assert not ready and confirmations == 0
    assert reason.startswith("buy flow not strong enough")


def test_sand_79_missing_recent_bars_remain_unavailable(monkeypatch):
    # Run 39040537629 had 113 missing bars, 79 inside the recent 205-bar
    # window. Recreate those counts; missing timestamps here are fixtures.
    now = 1792247059
    full = fixture_window(1791976500, 1792245600)
    initial = full.drop([*range(10, 44), *range(100, 179)])
    monkeypatch.setattr(md.time, "time", lambda: now)
    state = {}
    bind_history(state)
    calls = []

    def get_json(url, params, **kwargs):
        calls.append(dict(params))
        return payload(initial)

    monkeypatch.setattr(md, "get_json", get_json)
    with pytest.raises(md.CandleUnavailableError, match="Recent window incomplete: 79 missing"):
        md.fetch_candles("SAND_TL")
    assert len(calls) == 2  # One initial fetch and one bounded recent probe.
    report = state["candle_gap_history"]["sand_tl:15m:native_v1"]["report"]
    assert report["requests"] == 1
    assert report["missing_before"] == 113
    assert report["returned_window_missing"] == 79
    assert report["recovered"] == 0
    assert report["status"] == "gap_limit_exceeded"
