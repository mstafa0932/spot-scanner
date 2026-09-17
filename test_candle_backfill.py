import json
import pandas as pd
import pytest
import market_data as md
from candle_backfill import repair, MAX_REQUESTS
from candle_backfill import bind_history

NOW = 1800000000


@pytest.fixture(autouse=True)
def isolated_history():
    bind_history({})


def frame():
    return pd.DataFrame([dict(timestamp=NOW-(250-i)*900, open=10, high=11,
                              low=9, close=10, volume=5) for i in range(250)])


def payload(df):
    return {"s": "ok", **{k: df[v].tolist() for k, v in
            [("t", "timestamp"), ("o", "open"), ("h", "high"),
             ("l", "low"), ("c", "close"), ("v", "volume")]}}


def test_real_backfill_preserves_prices_and_utc_range(monkeypatch):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    full = frame()
    calls = []
    def get(url, params, **kwargs):
        calls.append((url, params))
        return payload(full.drop(100) if len(calls) == 1 else full)
    monkeypatch.setattr(md, "get_json", get)
    result = md.fetch_candles("BTC_TL")
    pd.testing.assert_frame_equal(result.reset_index(drop=True), full, check_dtype=False)
    report = result.attrs["backfill"]
    assert report["missing_before"] == 1 and report["missing_after"] == 0
    assert calls[1][1]["from"] == int(full.timestamp.iloc[100])-900
    assert all(url == md.PARIBU_CHART_HISTORY_URL for url, _ in calls)


@pytest.mark.parametrize("network_error", [False, True])
def test_unresolved_or_connection_failure_rejected(monkeypatch, network_error, caplog):
    monkeypatch.setattr(md.time, "time", lambda: NOW)
    calls = []
    def get(*a, **kw):
        calls.append(kw)
        if network_error and len(calls) > 1:
            raise md.ParibuHTTPError("test timeout")
        return payload(frame().drop(100))
    monkeypatch.setattr(md, "get_json", get)
    with pytest.raises(md.CandleUnavailableError, match="Unresolved"):
        md.fetch_candles("BTC_TL")
    assert len(calls) == 5
    assert '"missing_after": 1' in caplog.text
    if network_error:
        assert "test timeout" in caplog.text


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


def test_request_budget_and_no_manufactured_rows():
    full = frame()
    calls = []
    def request(*args):
        calls.append(args)
        return full.iloc[:0]
    with pytest.raises(ValueError, match="Unresolved"):
        repair(full.drop([20, 40, 60, 80, 100]), 900, NOW, request, "BTC")
    assert len(calls) == MAX_REQUESTS


def test_gap_limit_and_trailing_gap():
    full = frame()
    def forbidden(*a):
        pytest.fail("should not request enormous gap")
    with pytest.raises(ValueError, match="limit"):
        repair(full.drop(range(10, 150)), 900, NOW, forbidden, "BTC")
    result = repair(full.iloc[:-1], 900, NOW+61, lambda *a: full.tail(1), "BTC")
    assert result.attrs["backfill"]["recovered"] == 1


def test_btc_gate_blocks_unresolved_data(monkeypatch):
    import scanner
    def fail(*a):
        raise md.CandleUnavailableError("Unresolved Paribu candles")
    monkeypatch.setattr(scanner, "fetch_candles", fail)
    ok, _, reason = scanner.btc_gate()
    assert not ok and "Unresolved" in reason


def test_fourth_attempt_can_recover_and_logs_each_attempt(caplog):
    caplog.set_level("INFO", logger="paribu_momentum_watcher.candle_backfill")
    full = frame()
    calls = []
    def request(*a):
        calls.append(a)
        return full if len(calls) == 4 else full.iloc[:0]
    result = repair(full.drop(100), 900, NOW, request, "BTC")
    assert len(calls) == 4
    assert result.attrs["backfill"]["status"] == "complete"
    assert caplog.text.count("CANDLE_BACKFILL_ATTEMPT") == 4
    assert '"result": "success"' in caplog.text


def test_persisted_history_distinguishes_continuing_new_and_mixed():
    state = {}
    bind_history(state)
    full = frame()
    def run(missing, now):
        with pytest.raises(ValueError, match="Unresolved"):
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


def test_old_gap_is_recorded_but_recent_205_window_is_accepted():
    full = frame()
    calls = []
    result = repair(full.drop(20), 900, NOW,
                    lambda *a: calls.append(a) or full.iloc[:0], "BTC")
    report = result.attrs["backfill"]
    assert calls == []
    assert len(result) == 229
    assert report["status"] == "accepted_recent_window"
    assert report["missing_after"] == 1
    assert result.timestamp.diff().iloc[1:].eq(900).all()


def test_gap_inside_recent_205_window_still_blocks():
    full = frame()
    with pytest.raises(ValueError, match="Unresolved"):
        repair(full.drop(100), 900, NOW, lambda *a: full.iloc[:0], "BTC")


def test_partial_recovery_can_create_safe_recent_window():
    full = frame()
    broken = full.drop([20, 100])
    result = repair(broken, 900, NOW, lambda *a: full.iloc[[100]], "BTC")
    assert result.attrs["backfill"]["status"] == "accepted_recent_window"
    assert result.attrs["backfill"]["missing_after"] == 1
    assert len(result) == 229
