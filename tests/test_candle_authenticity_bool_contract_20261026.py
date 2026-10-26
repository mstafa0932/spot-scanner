"""Provenance flags must be actual booleans, never coerced from strings.

Offline-only; no Paribu, Telegram, or trading requests.
"""
import numpy as np
import pandas as pd
import pytest

import scanner


@pytest.mark.parametrize("bad_flag", [
    "False", "True", "0", "1", "yes", 1, 0, None, float("nan"),
])
def test_malformed_authenticity_flag_fails_closed_and_is_counted(bad_flag):
    frame = pd.DataFrame({"is_authentic": pd.Series([bad_flag], dtype=object)})
    frame.attrs["source"] = "PARIBU"
    assert scanner._all_candles_authentic(frame) is False
    assert scanner._candle_synthetic_rows(frame) == 1


def test_real_bool_rows_are_accepted_and_false_rows_rejected():
    good = pd.DataFrame({"is_authentic": pd.Series([True, np.bool_(True)], dtype=object)})
    bad = pd.DataFrame({"is_authentic": pd.Series([True, np.bool_(False)], dtype=object)})
    assert scanner._all_candles_authentic(good) is True
    assert scanner._candle_synthetic_rows(good) == 0
    assert scanner._all_candles_authentic(bad) is False
    assert scanner._candle_synthetic_rows(bad) == 1


def test_missing_and_empty_authenticity_metadata_fails_closed():
    assert scanner._all_candles_authentic(pd.DataFrame({"close": [1]})) is False
    assert scanner._all_candles_authentic(pd.DataFrame({"is_authentic": []})) is False
