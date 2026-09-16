from unittest.mock import Mock

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
