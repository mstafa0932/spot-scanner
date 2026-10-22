"""Research-only regression tests for bounded near-miss measurement retries."""
import json

import pandas as pd

import check_near_misses as research


NOW = 1_800_000_000


def event(rejected_at=NOW - 5 * 3600):
    first_close = (rejected_at // 900 + 1) * 900
    return {
        "event_id": f"MASK_TL:{rejected_at}:book:spread_too_high",
        "rejected_at": rejected_at,
        "rejected_price": "22.98",
        "rejected_stage": "book:spread_too_high",
        "maturity_at": first_close + 16 * 900,
        "processed": False,
    }


def candles_for(record):
    start = (record["rejected_at"] // 900 + 1) * 900
    return pd.DataFrame({
        "timestamp": [start + 900 * k for k in range(16)],
        "high": [23.10] * 16,
        "low": [22.20] * 16,
    })


def test_rate_limit_defers_without_archiving_then_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(research.time, "sleep", lambda _: None)
    record = event()
    state = {"near_miss_queue": {"MASK_TL": record}}
    archive = tmp_path / "research.jsonl"
    observed = []

    def limited(*args):
        observed.append("429")
        raise RuntimeError("HTTP 429 rate limited")

    t0 = record["maturity_at"] + 60
    assert research.process_state(state, now=t0, fetcher=limited,
                                  archive_path=archive, output=lambda _: None)
    assert observed == ["429"]
    assert record["processed"] is False
    assert record["measurement_attempts"] == 1
    assert record["next_measurement_attempt_at"] == t0 + research.TRANSIENT_RETRY_DELAY_SECONDS
    assert not archive.exists()

    # Repeated scans inside the waiting window must not call the exchange.
    assert not research.process_state(state, now=t0 + 3600, fetcher=limited,
                                      archive_path=archive, output=lambda _: None)
    assert observed == ["429"]

    def recovered(symbol, interval, limit):
        assert (symbol, interval, limit) == ("MASK_TL", "15m", 250)
        return candles_for(record)

    assert research.process_state(state, now=t0 + research.TRANSIENT_RETRY_DELAY_SECONDS,
                                  fetcher=recovered, archive_path=archive, output=lambda _: None)
    assert record["processed"] is True
    rows = [json.loads(line) for line in archive.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "measured"
    assert rows[0]["candles"] == 16
    assert "next_measurement_attempt_at" not in record
    assert "measurement_attempts" not in record


def test_rate_limit_stops_rest_of_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(research.time, "sleep", lambda _: None)
    first = event()
    second = event(NOW - 6 * 3600)
    second["event_id"] = second["event_id"].replace("MASK_TL", "LIT_TL")
    state = {"near_miss_queue": {"MASK_TL": first, "LIT_TL": second}}
    requests = []

    def limited(symbol, *_):
        requests.append(symbol)
        raise RuntimeError("429")

    assert research.process_state(state, now=NOW, fetcher=limited,
                                  archive_path=tmp_path / "archive.jsonl", output=lambda _: None)
    assert requests == ["MASK_TL"]
    assert first["processed"] is False
    assert second["processed"] is False


def test_permanent_gap_is_archived_once(tmp_path, monkeypatch):
    monkeypatch.setattr(research.time, "sleep", lambda _: None)
    record = event()
    state = {"near_miss_queue": {"MASK_TL": record}}
    archive = tmp_path / "archive.jsonl"
    calls = []

    def synthetic_error(*_):
        calls.append(True)
        raise RuntimeError("Synthetic candle limit exceeded")

    assert research.process_state(state, now=NOW, fetcher=synthetic_error,
                                  archive_path=archive, output=lambda _: None)
    assert record["processed"] is True
    assert json.loads(archive.read_text().strip())["incomplete_reason_code"] == "synthetic_candle_limit_exceeded"
    assert not research.process_state(state, now=NOW + 6 * 3600, fetcher=synthetic_error,
                                      archive_path=archive, output=lambda _: None)
    assert len(calls) == 1


def test_transient_retry_budget_exhaustion_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(research.time, "sleep", lambda _: None)
    record = event()
    record["measurement_attempts"] = research.TRANSIENT_RETRY_MAX_ATTEMPTS - 1
    state = {"near_miss_queue": {"MASK_TL": record}}
    archive = tmp_path / "archive.jsonl"

    def limited(*_):
        raise RuntimeError("429")

    assert research.process_state(state, now=NOW, fetcher=limited,
                                  archive_path=archive, output=lambda _: None)
    assert record["processed"] is True
    saved = json.loads(archive.read_text().strip())
    assert saved["outcome"] == "incomplete_data"
    assert saved["incomplete_reason_code"] == "http_429"


def test_retry_window_expiry_archives_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(research.time, "sleep", lambda _: None)
    record = event(NOW - 3 * 86400)
    state = {"near_miss_queue": {"MASK_TL": record}}
    archive = tmp_path / "archive.jsonl"

    def limited(*_):
        raise RuntimeError("timeout")

    assert research.process_state(state, now=NOW, fetcher=limited,
                                  archive_path=archive, output=lambda _: None)
    assert record["processed"] is True
    assert json.loads(archive.read_text().strip())["outcome"] == "incomplete_data"


def test_durable_archive_reconciles_before_retry_backoff(tmp_path):
    # Simulate an archive fsync followed by a crash before state persistence.
    record = event()
    record["measurement_attempts"] = 1
    record["next_measurement_attempt_at"] = NOW + 3600
    state = {"near_miss_queue": {"MASK_TL": record}}
    archive = tmp_path / "archive.jsonl"
    archive.write_text(json.dumps({
        "event_id": record["event_id"],
        "outcome": "incomplete_data",
        "incomplete_reason_code": "http_429",
    }) + "\n", encoding="utf-8")
    requested = []

    def unexpected_fetch(*args):
        requested.append(args)
        raise AssertionError("Archived event must not be fetched again")

    assert research.process_state(
        state, now=NOW, fetcher=unexpected_fetch,
        archive_path=archive, output=lambda _: None,
    )
    assert record["processed"] is True
    assert record["processed_at"] == NOW
    assert "measurement_attempts" not in record
    assert "next_measurement_attempt_at" not in record
    assert requested == []
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 1


def test_near_deadline_failure_is_archived_without_late_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(research.time, "sleep", lambda _: None)
    record = event()
    now = record["maturity_at"] + 35 * 3600
    state = {"near_miss_queue": {"MASK_TL": record}}
    archive = tmp_path / "archive.jsonl"
    calls = []

    def limited(*_):
        calls.append(True)
        raise RuntimeError("HTTP 429 rate limited")

    assert research.process_state(
        state, now=now, fetcher=limited, archive_path=archive,
        output=lambda _: None,
    )
    assert calls == [True]
    assert record["processed"] is True
    assert "next_measurement_attempt_at" not in record
    assert json.loads(archive.read_text().strip())["incomplete_reason_code"] == "http_429"


def test_late_persisted_retry_expires_without_network_call(tmp_path):
    # Reproduce a workflow delayed past the 36h retry deadline, with an
    # already queued attempt that would otherwise fetch at hour 41.
    record = event()
    maturity = record["maturity_at"]
    record["measurement_attempts"] = 1
    record["next_measurement_attempt_at"] = maturity + 41 * 3600
    record["incomplete_reason_code"] = "http_429"
    record["incomplete_reason_detail"] = "RuntimeError: HTTP 429"
    state = {"near_miss_queue": {"MASK_TL": record}}
    archive = tmp_path / "archive.jsonl"
    requests = []
    logs = []

    def unexpected_fetch(*args):
        requests.append(args)
        raise AssertionError("No fetch allowed after the retry deadline")

    assert research.process_state(
        state, now=maturity + 37 * 3600, fetcher=unexpected_fetch,
        archive_path=archive, output=logs.append,
    )
    row = json.loads(archive.read_text().strip())
    assert row["outcome"] == "incomplete_data"
    assert row["incomplete_reason_code"] == "retry_window_expired"
    assert "http_429" in row["incomplete_reason_detail"]
    assert record["processed"] is True
    assert requests == []
    assert not any("[NEAR_MISS_RATE_LIMIT]" in x for x in logs)
    assert not research.process_state(
        state, now=maturity + 42 * 3600, fetcher=unexpected_fetch,
        archive_path=archive, output=lambda _: None,
    )
    assert len(archive.read_text().splitlines()) == 1
