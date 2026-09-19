import json
import pandas as pd
import pytest
import market_data as md
from candle_backfill import (
    repair,
    MAX_REQUESTS,
    bind_history,
    recent_authentic,
)

NOW = 1800000000


@pytest.fixture(autouse=True)
def isolated_history():
    bind_history({})


def frame():
    return pd.DataFrame([
        dict(timestamp=NOW-(250-i)*900, open=10, high=11, low=9, close=10, volume=5)
        for i in range(250)
    ])


def payload(df):
    return {"s": "ok", **{k: df[v].tolist() for k, v in
            [("t", "timestamp"), ("o", "open"), ("h", "high"),
             ("l", "low"), ("c", "close"), ("v", "volume")]}}


def test_real_backfill_preserves_prices_and_provenance(monkeypatch):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    full = frame()
    calls = []

    def get(url, params, **kwargs):
        calls.append((url, params))
        return payload(full.drop(100) if len(calls) == 1 else full)

    monkeypatch.setattr(md, "get_json", get)
    result = md.fetch_candles("BTC_TL")
    assert len(result) == 250
    assert result["is_authentic"].all()
    assert result.attrs["backfill"]["status"] == "complete_real"
    assert result.attrs["backfill"]["missing_before"] == 1
    assert result.attrs["backfill"]["missing_after_real_recovery"] == 0
    assert calls[1][1]["from"] == int(full.timestamp.iloc[100]) - 900


@pytest.mark.parametrize("network_error", [False, True])
def test_unresolved_old_gap_uses_tagged_synthetic_fallback(monkeypatch, network_error):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    calls = []

    def get(*a, **kw):
        calls.append(kw)
        if network_error and len(calls) > 1:
            raise md.ParibuHTTPError("test timeout")
        return payload(frame().drop(100))

    monkeypatch.setattr(md, "get_json", get)
    result = md.fetch_candles("BTC_TL")
    assert len(calls) == 5
    synthetic = result[~result["is_authentic"]]
    assert len(synthetic) == 1
    row = synthetic.iloc[0]
    assert row.open == row.high == row.low == row.close == 10
    assert row.volume == 0
    assert row.data_quality == "SYNTHETIC_FFILL"
    assert result.attrs["backfill"]["status"] == "synthetic_fallback"


def test_off_grid_and_conflicting_duplicates_rejected(monkeypatch):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    df = frame()
    df.timestamp += 1
    with pytest.raises(md.ParibuSchemaError, match="UTC"):
        md.validate_candles(df, "15m")
    full = frame()
    duplicate = full.tail(1).copy()
    duplicate.close = 10.5
    with pytest.raises(md.ParibuSchemaError, match="Conflicting"):
        md.validate_candles(pd.concat([full, duplicate]), "15m")


def test_identical_duplicates_are_deduplicated(monkeypatch):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    full = frame()
    assert len(md.validate_candles(pd.concat([full, full.tail(1)]), "15m")) == 250


def test_request_budget_then_bounded_synthetic_fill():
    full = frame()
    calls = []

    def request(*args):
        calls.append(args)
        return full.iloc[:0]

    result = repair(full.drop([20, 40, 60, 80, 100]), 900, NOW, request, "BTC")
    assert len(calls) == MAX_REQUESTS
    assert int((~result["is_authentic"]).sum()) == 5
    assert result.attrs["backfill"]["status"] == "synthetic_fallback"


def test_gap_limit_and_trailing_gap():
    full = frame()

    def forbidden(*a):
        pytest.fail("should not request enormous gap")

    with pytest.raises(ValueError, match="limit"):
        repair(full.drop(range(10, 150)), 900, NOW, forbidden, "BTC")

    result = repair(full.iloc[:-1], 900, NOW+61, lambda *a: full.tail(1), "BTC")
    assert result.attrs["backfill"]["recovered"] == 1
    assert result.attrs["backfill"]["status"] == "complete_real"


def test_btc_gate_blocks_recent_synthetic_candle(monkeypatch):
    import scanner
    full = frame()
    repaired = repair(full.drop(249), 900, NOW, lambda *a: full.iloc[:0], "BTC")
    repaired.attrs["source"] = "PARIBU"
    monkeypatch.setattr(scanner, "fetch_candles", lambda *a: repaired)
    ok, _, reason = scanner.btc_gate()
    assert not ok
    assert "recent 4h candle integrity failed" in reason


def test_btc_gate_allows_old_synthetic_if_recent_16_authentic(monkeypatch):
    import scanner
    full = frame()
    repaired = repair(full.drop(20), 900, NOW, lambda *a: full.iloc[:0], "BTC")
    repaired.attrs["source"] = "PARIBU"
    assert recent_authentic(repaired, 16, 900)

    class Tech:
        current_close = md.Decimal("10") if hasattr(md, "Decimal") else None

    # Avoid dependence on indicator values here; the integrity gate is the target.
    from types import SimpleNamespace as NS
    tech = NS(
        current_close=__import__("decimal").Decimal("10"),
        ema21=__import__("decimal").Decimal("10"),
        recent_return_3=__import__("decimal").Decimal("0"),
        rsi14=__import__("decimal").Decimal("50"),
        is_uptrend=True,
    )
    monkeypatch.setattr(scanner, "fetch_candles", lambda *a: repaired)
    monkeypatch.setattr(scanner, "analyze_symbol", lambda *a: tech)
    ok, _, reason = scanner.btc_gate()
    assert ok
    assert "BTC" in reason


def test_fourth_attempt_can_recover_and_logs_each_attempt(caplog):
    caplog.set_level("INFO", logger="paribu_momentum_watcher.candle_backfill")
    full = frame()
    calls = []

    def request(*a):
        calls.append(a)
        return full if len(calls) == 4 else full.iloc[:0]

    result = repair(full.drop(100), 900, NOW, request, "BTC")
    assert len(calls) == 4
    assert result.attrs["backfill"]["status"] == "complete_real"
    assert caplog.text.count("CANDLE_REPAIR_ATTEMPT") == 4


def test_persisted_history_distinguishes_continuing_new_and_mixed():
    state = {}
    bind_history(state)
    full = frame()

    def run(missing, now):
        repair(full.drop(missing), 900, now, lambda *a: full.iloc[:0], "BTC")

    run([100], NOW)
    assert state["candle_gap_history"]["BTC"]["report"]["classification"] == "first_observation"
    state = json.loads(json.dumps(state))
    bind_history(state)
    run([100], NOW)
    assert state["candle_gap_history"]["BTC"]["report"]["classification"] == "continuing"
    run([100, 110], NOW)
    report = state["candle_gap_history"]["BTC"]["report"]
    assert report["classification"] == "mixed"
    assert report["new_count"] == 1 and report["continuing_count"] == 1
    run([120], NOW)
    assert state["candle_gap_history"]["BTC"]["report"]["classification"] == "new"


def test_backfill_transport_does_not_retry_internally():
    assert md.BACKFILL_SESSION.get_adapter("https://web.paribu.com").max_retries.total == 0


def test_old_gap_is_synthetic_but_recent_16_stays_authentic():
    full = frame()
    result = repair(full.drop(20), 900, NOW, lambda *a: full.iloc[:0], "BTC")
    assert len(result) == 250
    assert int((~result["is_authentic"]).sum()) == 1
    assert recent_authentic(result, 16, 900)


def test_gap_inside_recent_16_is_detected_by_integrity_gate():
    full = frame()
    result = repair(full.drop(245), 900, NOW, lambda *a: full.iloc[:0], "BTC")
    assert not recent_authentic(result, 16, 900)


def test_consecutive_synthetic_gap_limit_fails_closed():
    full = frame()
    with pytest.raises(ValueError, match="Consecutive synthetic candle limit exceeded"):
        repair(full.drop([100, 101, 102]), 900, NOW, lambda *a: full.iloc[:0], "BTC")


def test_total_synthetic_gap_limit_fails_closed():
    full = frame()
    missing = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    with pytest.raises(ValueError, match="Synthetic candle limit exceeded"):
        repair(full.drop(missing), 900, NOW, lambda *a: full.iloc[:0], "BTC")
