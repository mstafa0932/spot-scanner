"""Recovery must resume after cooldown or a transient failure."""
import json

import pandas as pd
import pytest

import market_data as md
from candle_backfill import bind_history, repair
from test_candle_backfill import NOW, frame, authentic_rows


@pytest.fixture(autouse=True)
def isolated_history():
    bind_history({})


def growing_frame(now):
    full = frame()
    extra = []
    for timestamp in range(NOW, now // 900 * 900, 900):
        row = full.iloc[-1].to_dict()
        row["timestamp"] = timestamp
        extra.append(row)
    return pd.concat([full, pd.DataFrame(extra)], ignore_index=True) if extra else full


@pytest.mark.parametrize("large", [False, True])
def test_negative_cache_expires_without_sliding_on_a_cache_hit(large):
    state = {}
    calls = []
    missing = list(range(100, 230)) if large else list(range(100, 118, 2))
    for now, expected_calls in ((NOW, 1 if large else 4),
                                (NOW + 600, 1 if large else 4)):
        bind_history(state)
        full = growing_frame(now)
        with pytest.raises(ValueError):
            repair(full.drop(missing), 900, now,
                   lambda *a: calls.append(a) or full.iloc[:0], "CACHE")
        assert len(calls) == expected_calls
        state = json.loads(json.dumps(state, allow_nan=False))
    full = growing_frame(NOW + 1200)
    bind_history(state)
    before = len(calls)
    result = repair(full.drop(missing), 900, NOW + 1200,
                    lambda *a: calls.append(a) or authentic_rows(full), "CACHE")
    assert len(calls) == before + 1
    assert result.is_authentic.all()
    assert result.attrs["backfill"]["requests"] == 1


@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "rate_limit", "provenance"])
def test_transient_or_invalid_response_is_not_negative_data_evidence(large, failure):
    full = frame()
    missing = list(range(100, 230)) if large else [100]
    calls = []
    def unavailable(*args):
        calls.append(args)
        if failure == "provenance":
            return full  # Intentionally missing the recovery provenance contract.
        raise md.ParibuHTTPError("temporary source error", status_code=429 if failure == "rate_limit" else None)
    try:
        repair(full.drop(missing), 900, NOW, unavailable, "TRANSIENT")
    except ValueError:
        pass
    before = len(calls)
    result = repair(full.drop(missing), 900, NOW + 600,
                    lambda *a: calls.append(a) or authentic_rows(full), "TRANSIENT")
    assert len(calls) == before + 1
    assert result.is_authentic.all()


def test_legacy_cache_without_expiry_is_revalidated():
    full = frame()
    missing = [int(full.timestamp.iloc[100])]
    bind_history({"candle_gap_history": {"LEGACY": {
        "observed_at": NOW, "missing_timestamps": missing,
        "report": {"requests": 4, "recovered": 0},
    }}})
    calls = []
    result = repair(full.drop(100), 900, NOW + 600,
                    lambda *a: calls.append(a) or authentic_rows(full), "LEGACY")
    assert len(calls) == 1
    assert result.is_authentic.all()


@pytest.mark.parametrize("fault", ["synthetic", "foreign", "nonfinite"])
def test_initial_rows_cannot_bypass_recovery_provenance(fault):
    rows = frame().astype({"high": float})
    if fault == "synthetic": rows["is_authentic"] = False
    elif fault == "foreign": rows.attrs["source"] = "OTHER_EXCHANGE"
    else: rows.loc[100, "high"] = float("inf")
    with pytest.raises(ValueError):
        repair(rows, 900, NOW, lambda *a: rows.iloc[:0], "INPUT")


def test_conflicting_recovery_duplicates_are_not_silently_deduplicated():
    full = frame()
    rows = full.iloc[[100]].astype({"close": float})
    conflict = rows.copy()
    conflict["close"] = 10.5
    rows = authentic_rows(pd.concat([rows, conflict], ignore_index=True))
    with pytest.raises(ValueError, match="Conflicting"):
        repair(full.drop(100), 900, NOW, lambda *a: rows, "CONFLICT")
