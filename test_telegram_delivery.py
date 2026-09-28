from unittest.mock import Mock
from decimal import Decimal

import pytest
import requests
import scanner


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv(scanner.TELEGRAM_TOKEN_ENV, "private-test-token")
    monkeypatch.setenv(scanner.TELEGRAM_CHAT_ENV, "private-chat")


@pytest.mark.parametrize("payload,expected", [
    ({"ok": True}, True), ({"ok": False}, False),
    ({}, False), ([], False), ({"ok": "true"}, False),
])
def test_requires_explicit_confirmation(monkeypatch, payload, expected):
    response = Mock(status_code=200)
    response.json.return_value = payload
    monkeypatch.setattr(scanner.requests, "post", Mock(return_value=response))
    assert scanner.send_telegram("test") is expected


def test_invalid_json(monkeypatch):
    response = Mock(status_code=200)
    response.json.side_effect = ValueError("bad response")
    monkeypatch.setattr(scanner.requests, "post", Mock(return_value=response))
    assert scanner.send_telegram("test") is False


def test_http_error_body_is_not_logged(monkeypatch, caplog):
    response = Mock(status_code=429, text="private-test-token private-chat")
    monkeypatch.setattr(scanner.requests, "post", Mock(return_value=response))
    assert scanner.send_telegram("test") is False
    assert "private-test-token" not in caplog.text
    assert "private-chat" not in caplog.text


def test_request_exception_url_is_not_logged(monkeypatch, caplog):
    monkeypatch.setattr(scanner.requests, "post", Mock(side_effect=
        requests.ConnectionError("https://api.telegram.org/botprivate-test-token/sendMessage")))
    assert scanner.send_telegram("test") is False
    assert "private-test-token" not in caplog.text


def test_missing_credentials_never_sends(monkeypatch):
    monkeypatch.delenv(scanner.TELEGRAM_TOKEN_ENV)
    post = Mock()
    monkeypatch.setattr(scanner.requests, "post", post)
    assert scanner.send_telegram("test") is False
    post.assert_not_called()

def test_shadow_ready_alert_requires_explicit_opt_in(monkeypatch):
    opportunity = Mock()
    sender = Mock(return_value=True)
    formatter = Mock(return_value="ready-message")
    monkeypatch.setattr(scanner, "send_telegram", sender)
    monkeypatch.setattr(scanner, "format_opportunity", formatter)

    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", False)
    assert scanner.send_shadow_ready_alert(opportunity) is False
    sender.assert_not_called()

    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", True)
    assert scanner.send_shadow_ready_alert(opportunity) is True
    formatter.assert_called_once_with(opportunity)
    sender.assert_called_once_with("ready-message")

def _watch_book():
    book = Mock()
    book.spread_percent = Decimal("0.20")
    book.imbalance_ratio = Decimal("1.00")
    return book


def _watch_tech():
    tech = Mock()
    tech.rsi14 = Decimal("60")
    tech.atr14 = Decimal("1")
    tech.current_close = Decimal("100")
    tech.recent_return_3 = Decimal("1.00")
    tech.recent_return_12 = Decimal("2.00")
    tech.recent_return_48 = Decimal("4.00")
    tech.is_above_ema21 = True
    tech.macd_histogram = Decimal("0.10")
    tech.volume_ratio = Decimal("1.20")
    return tech


def test_early_watch_only_bypasses_higher_timeframe_veto():
    book, tech = _watch_book(), _watch_tech()
    assert scanner._early_watch_mtf_ok(book, tech, scanner.DISCOVERY_MIN_SCORE, True, "1h clearly weak")
    assert scanner._early_watch_mtf_ok(book, tech, scanner.DISCOVERY_MIN_SCORE, True, "4h clearly weak")
    assert not scanner._early_watch_mtf_ok(book, tech, scanner.DISCOVERY_MIN_SCORE, True, "15m momentum not constructive")
    assert not scanner._early_watch_mtf_ok(book, tech, scanner.DISCOVERY_MIN_SCORE, False, "1h clearly weak")


def test_early_watch_keeps_existing_short_term_guards():
    book, tech = _watch_book(), _watch_tech()
    tech.volume_ratio = scanner.MIN_WATCH_VOLUME_RATIO - Decimal("0.01")
    assert not scanner._early_watch_mtf_ok(book, tech, scanner.DISCOVERY_MIN_SCORE, True, "1h clearly weak")
    tech.volume_ratio = Decimal("1.20")
    tech.macd_histogram = Decimal("-0.01")
    assert not scanner._early_watch_mtf_ok(book, tech, scanner.DISCOVERY_MIN_SCORE, True, "1h clearly weak")


def test_early_watch_alert_requires_explicit_opt_in(monkeypatch):
    book, tech = _watch_book(), _watch_tech()
    sender = Mock(return_value=True)
    monkeypatch.setattr(scanner, "send_telegram", sender)

    monkeypatch.setattr(scanner, "TELEGRAM_EARLY_WATCH_ALERTS", False)
    assert scanner.send_early_watch_alert(
        symbol="TEST_TL", score=70, reason="1h clearly weak",
        book=book, tech_15=tech, btc_reason="BTC neutral/acceptable"
    ) is False
    sender.assert_not_called()

    monkeypatch.setattr(scanner, "TELEGRAM_EARLY_WATCH_ALERTS", True)
    assert scanner.send_early_watch_alert(
        symbol="TEST_TL", score=70, reason="1h clearly weak",
        book=book, tech_15=tech, btc_reason="BTC neutral/acceptable"
    ) is True
    message = sender.call_args.args[0]
    assert "EARLY WATCH" in message
    assert "ليست توصية دخول" in message
    assert "TEST_TL" in message

