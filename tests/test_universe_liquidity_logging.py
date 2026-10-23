"""Offline regression tests for actionable universe-stage liquidity audit logs.

No Paribu requests, Telegram sends, strategy thresholds, or live scanner runs.
"""

from decimal import Decimal

import scanner
from test_scanner_research import prepare


def test_low_liquidity_rejection_logs_exact_quote_turnover(monkeypatch, tmp_path, caplog):
    ticker, _, _ = prepare(monkeypatch, tmp_path)
    ticker.quote_volume = Decimal("4085651.91")

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("Excluded low-volume symbol must not request orderbook or candles")

    monkeypatch.setattr(scanner, "get_order_book", unexpected_request)
    monkeypatch.setattr(scanner, "fetch_candles", unexpected_request)

    with caplog.at_level("INFO", logger=scanner.LOGGER.name):
        scanner.run_scanner()

    decision = scanner.load_state()["scan_diagnostics"][-1]["symbols"]["SYN_TL"]
    assert decision["stage"] == "universe"
    assert decision["reason"] == "quote_volume_below_minimum"
    assert decision["quote_volume"] == "4085651.91"

    lines = [record.getMessage() for record in caplog.records]
    matching = [line for line in lines if "Scan decision | SYN_TL | universe |" in line]
    assert matching == [
        "Scan decision | SYN_TL | universe | quote_volume_below_minimum | quote_volume=4085651.91"
    ]
    # Keep the existing log-line budget: no second metrics line is emitted.
    assert not any("Scan metrics | SYN_TL | universe" in line for line in lines)
    assert not scanner.load_state().get("sent_signals")


def test_base_asset_exclusion_keeps_existing_log_format(monkeypatch, tmp_path, caplog):
    ticker, _, _ = prepare(monkeypatch, tmp_path)
    ticker.symbol = "BTC_TL"

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("Excluded base asset must not request orderbook or candles")

    monkeypatch.setattr(scanner, "get_order_book", unexpected_request)
    monkeypatch.setattr(scanner, "fetch_candles", unexpected_request)

    with caplog.at_level("INFO", logger=scanner.LOGGER.name):
        scanner.run_scanner()

    decision = scanner.load_state()["scan_diagnostics"][-1]["symbols"]["BTC_TL"]
    assert decision["stage"] == "universe"
    assert decision["reason"] == "excluded_base_asset"
    lines = [record.getMessage() for record in caplog.records]
    assert [line for line in lines if "Scan decision | BTC_TL | universe |" in line] == [
        "Scan decision | BTC_TL | universe | excluded_base_asset"
    ]
