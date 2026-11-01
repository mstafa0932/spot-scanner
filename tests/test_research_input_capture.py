"""Offline archive regressions; fixtures never represent market performance."""
from datetime import datetime, timezone
from decimal import Decimal as D
import gzip
import hashlib
import json
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import research_capture as capture
import scanner
import test_post_radar_recovery as harness


NOW = int(datetime(2026, 11, 1, 12, tzinfo=timezone.utc).timestamp())


def enable(monkeypatch, directory):
    """Opt in to a short, deterministic shadow-only capture window."""
    monkeypatch.setenv("SHADOW_MODE", "true")
    monkeypatch.setenv("RESEARCH_CAPTURE_DIR", str(directory))
    monkeypatch.setenv("RESEARCH_CAPTURE_UNTIL", "2026-11-03T12:00:00Z")
    monkeypatch.setenv("GITHUB_RUN_ID", "offline-archive-fixture")
    monkeypatch.setenv("GITHUB_SHA", "fixture-code-identity")
    monkeypatch.setenv("RESEARCH_CAPTURE_CODE_SHA", "fixture-checked-out-identity")
    monkeypatch.setattr(capture.time, "time", lambda: NOW)
    return capture.ResearchCapture.from_env(NOW)


def book():
    return NS(symbol="TEST_TL", bids=((D("100.123456789"), D("2.3456789")),),
              asks=((D("100.2"), D("3")),), best_bid=D("100.123456789"),
              best_ask=D("100.2"), spread_percent=D(".076"), imbalance_ratio=D(".78"),
              bid_notional=D("234.85746"), ask_notional=D("300.6"), timestamp="exchange-ts")


def frame():
    end = NOW // 900 * 900 - 900
    f = pd.DataFrame([{"timestamp": end - i * 900, "open": 100., "high": 101.,
                       "low": 99., "close": 100.5, "volume": 0., "is_authentic": True}
                      for i in range(4, -1, -1)])
    f.attrs.update(source="PARIBU", resolution="15m", symbol="TEST_TL",
                   candle_endpoint="https://web.paribu.com/v1/chart/ohlc")
    return f


def records(directory):
    return [json.loads(line) for line in gzip.decompress((directory / "inputs.jsonl.gz").read_bytes()).splitlines()]


@pytest.mark.parametrize("setting,value", [
    ("RESEARCH_CAPTURE_UNTIL", ""), ("RESEARCH_CAPTURE_UNTIL", "2026-10-31T12:00:00Z"),
    ("RESEARCH_CAPTURE_UNTIL", "2026-12-01T12:00:00Z"),
    ("RESEARCH_CAPTURE_UNTIL", "2026-11-03T12:00:00"), ("SHADOW_MODE", "false"),
])
def test_disabled_or_expired_capture_does_not_create_files(monkeypatch, tmp_path, setting, value):
    enable(monkeypatch, tmp_path / "archive")
    monkeypatch.setenv(setting, value)
    recorder = capture.ResearchCapture.from_env(NOW)
    recorder.book(book(), "initial")
    assert recorder.finish() == {"enabled": False}
    assert not (tmp_path / "archive").exists()


def test_archive_preserves_public_inputs_precision_and_integrity_without_secrets(monkeypatch, tmp_path):
    recorder = enable(monkeypatch, tmp_path)
    secret = "TEST-ONLY-SECRET-MUST-NOT-BE-ARCHIVED"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    f, b = frame(), book()
    f.attrs["private_extra"] = secret
    before = f.copy(deep=True)
    recorder.tickers({"TEST_TL": NS(last=D("100.2"), quote_volume=D("6000000"), private_extra=secret)})
    recorder.book(b, "initial")
    recorder.candles("TEST_TL", "15m", f)
    summary = recorder.finish()
    raw = (tmp_path / "inputs.jsonl.gz").read_bytes()
    assert summary["status"] == "captured"
    assert summary["data_sha256"] == hashlib.sha256(raw).hexdigest()
    decoded = gzip.decompress(raw)
    assert secret.encode() not in decoded
    rows = records(tmp_path)
    for item in rows:
        serialized = json.dumps(item["record"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert item["sha256"] == hashlib.sha256(serialized).hexdigest()
        assert item["record"]["available_at"] == NOW
    archived_book = next(r["record"]["payload"] for r in rows if r["record"]["kind"] == "book")
    assert archived_book["bids"] == [["100.123456789", "2.3456789"]]
    assert archived_book["timestamp"] == "exchange-ts"
    candles = next(r["record"]["payload"] for r in rows if r["record"]["kind"] == "candles")
    assert len(candles["rows"]) == len(f)
    assert all(r["volume"] == "0.0" and r["is_authentic"] is True for r in candles["rows"])
    assert f.equals(before) and f.attrs == before.attrs


@pytest.mark.parametrize("invalid", ["open", "synthetic", "wrong_source"])
def test_invalid_candle_inputs_are_not_relabelled_as_original(monkeypatch, tmp_path, invalid):
    recorder = enable(monkeypatch, tmp_path)
    f = frame()
    if invalid == "open":
        f.loc[f.index[-1], "timestamp"] = NOW
    elif invalid == "synthetic":
        f.loc[f.index[-1], "is_authentic"] = False
    else:
        f.attrs["source"] = "OTHER"
    recorder.candles("TEST_TL", "15m", f)
    summary = recorder.finish()
    assert summary["status"] == "partial"
    assert summary["counts"] == {"omitted_integrity:candles": 1}
    assert records(tmp_path) == []


def test_candle_budget_cannot_consume_the_reserved_book_budget(monkeypatch, tmp_path):
    recorder = enable(monkeypatch, tmp_path)
    monkeypatch.setattr(capture, "CANDLE_BUDGET", 1)
    recorder.candles("TEST_TL", "15m", frame())
    recorder.book(book(), "ready_refresh")
    summary = recorder.finish()
    assert summary["counts"]["omitted_budget:candles"] == 1
    assert summary["counts"]["captured:book"] == 1
    assert summary["compressed_bytes"] <= summary["maximum_compressed_bytes"]
    assert [r["record"]["kind"] for r in records(tmp_path)] == ["book"]


def test_archive_io_failure_is_observational_only(monkeypatch, tmp_path):
    path = tmp_path / "not-a-directory"
    path.write_text("existing file")
    recorder = enable(monkeypatch, path)
    recorder.book(book(), "initial")
    assert recorder.finish()["status"].startswith("error:")
    assert path.read_text() == "existing file"


def test_checked_out_code_and_trigger_are_distinct_identities(monkeypatch, tmp_path):
    """A refreshed checkout must not be attributed to its workflow trigger."""
    recorder = enable(monkeypatch, tmp_path)
    recorder.book(book(), "initial")
    manifest = recorder.finish()
    assert manifest["code_sha"] == "fixture-checked-out-identity"
    assert manifest["trigger_sha"] == "fixture-code-identity"
    assert records(tmp_path)[0]["record"]["code_sha"] == manifest["code_sha"]


def test_missing_checkout_provenance_is_not_filled_with_the_trigger(monkeypatch, tmp_path):
    enable(monkeypatch, tmp_path)
    monkeypatch.delenv("RESEARCH_CAPTURE_CODE_SHA")
    recorder = capture.ResearchCapture.from_env(NOW)
    recorder.book(book(), "initial")
    summary = recorder.finish()
    assert summary["code_sha"] is None
    assert summary["trigger_sha"] == "fixture-code-identity"
    assert summary["status"] == "partial"


def test_snapshot_failure_does_not_report_successful_capture(monkeypatch, tmp_path):
    """The scanner's real failure path marks the empty artifact incomplete."""
    harness.pipeline(monkeypatch, tmp_path)
    enable(monkeypatch, tmp_path / "archive")

    def unavailable():
        raise scanner.ParibuDataError("offline snapshot failure")

    monkeypatch.setattr(scanner, "get_market_snapshot", unavailable)
    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        scanner.run_scanner()
    summary = json.loads((tmp_path / "archive" / "manifest.json").read_text())
    assert summary["scan_status"] == "snapshot_failed"
    assert summary["status"] == "incomplete:snapshot_failed"
    assert records(tmp_path / "archive") == []


@pytest.mark.parametrize("near", [False, True])
def test_full_scanner_archives_rejected_books_and_native_frames_without_extra_fetches(monkeypatch, tmp_path, near):
    """A book veto and subsequent real READY run through unchanged gates."""
    monkeypatch.setattr(harness, "NOW", NOW)
    clock, calls, scan = harness.pipeline(monkeypatch, tmp_path)
    clock["rsi"] = D("60")
    monkeypatch.setattr(scanner, "TELEGRAM_NEAR_READY_ALERTS", near)
    messages = []
    monkeypatch.setattr(scanner, "send_telegram", lambda msg: messages.append(msg) or True)
    enable(monkeypatch, tmp_path / "archive")
    # Keep the harness's moving clock after the standalone helper setup.
    monkeypatch.setattr(scanner.time, "time", lambda: clock["now"])
    original = scanner.get_order_book
    clock["book_veto"] = True

    def get_book(symbol, *args):
        result = original(symbol, *args)
        fields = {**vars(result), "symbol": symbol}
        if symbol == harness.SYMBOL and clock["book_veto"]:
            fields["imbalance_ratio"] = D(".4")
        return NS(**fields)

    monkeypatch.setattr(scanner, "get_order_book", get_book)
    first = scan()
    assert first["scan_diagnostics"][-1]["symbols"][harness.SYMBOL]["reason"] == "imbalance_too_low"
    assert calls["books"].count(harness.SYMBOL) == 1
    assert (harness.SYMBOL, "15m") not in calls["candles"]
    archived = [r["record"] for r in records(tmp_path / "archive")]
    assert any(r["kind"] == "book" and r["payload"]["symbol"] == harness.SYMBOL
               and r["payload"]["imbalance_ratio"] == "0.4" for r in archived)
    clock.update(now=NOW + 900, book_veto=False)
    scan()
    if near:
        assert len(messages) == 1 and "NEAR READY" in messages[0]
        assert any(r["record"]["kind"] == "book" and r["record"]["payload"]["stage"] == "near_ready_refresh"
                   for r in records(tmp_path / "archive"))
    clock["now"] += 900
    ready = scan()
    assert calls["books"].count(harness.SYMBOL) == 2
    assert len(set(calls["books"])) == 80
    assert len(ready["active_signals"]) == 1
    assert ready["shadow_sent_signals"] == {harness.SYMBOL: clock["now"]}
    archived = [r["record"] for r in records(tmp_path / "archive")]
    assert any(r["kind"] == "book" and r["payload"]["stage"] == "ready_refresh" for r in archived)
    actual_candles = [r for r in archived if r["kind"] == "candles"]
    assert len(actual_candles) == len(calls["candles"])
    assert {r["payload"]["resolution"] for r in actual_candles} == {"15m", "1h", "4h"}
