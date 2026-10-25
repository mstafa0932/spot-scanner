"""Regression: Telegram policy must remain silent for broad EARLY WATCH.

This checks source and workflow configuration without making any network calls.
"""
from pathlib import Path
from types import SimpleNamespace

import scanner


def test_shadow_workflow_only_opts_in_ready_and_near_ready():
    workflow = (Path(__file__).resolve().parents[1] / ".github"
                / "workflows" / "scanner.yml").read_text(encoding="utf-8")
    # Restrict assertions to the live scanner step, not other workflow jobs.
    scan_step = workflow.split("      - name: Run scanner\n", 1)[1]
    scan_step = scan_step.split("\n      - name: ", 1)[0]
    expected = {
        "SHADOW_MODE": "true",
        "TELEGRAM_READY_ALERTS": "true",
        "TELEGRAM_NEAR_READY_ALERTS": "true",
        "TELEGRAM_EARLY_WATCH_ALERTS": "false",
    }
    for key, value in expected.items():
        assert scan_step.count(f"{key}: '{value}'") == 1
        assert scan_step.count(f"{key}:") == 1


def test_early_watch_api_never_sends_telegram(monkeypatch):
    def unsafe_send(*args, **kwargs):
        raise AssertionError("EARLY WATCH must never send to Telegram")

    monkeypatch.setattr(scanner, "send_telegram", unsafe_send)
    dummy = SimpleNamespace()
    assert scanner.send_early_watch_alert(
        symbol="PEPE_TL",
        score=90,
        reason="setup not ready",
        book=dummy,
        tech_15=dummy,
        btc_reason="BTC acceptable",
    ) is False
