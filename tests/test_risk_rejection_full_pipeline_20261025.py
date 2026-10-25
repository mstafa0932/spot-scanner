"""End-to-end risk rejection regression (offline; zero HTTP and orders)."""

from decimal import Decimal as D
from types import SimpleNamespace as NS

import scanner
from test_scanner_research import prepare


def test_risk_blocked_full_scan_preserves_priority_without_ready_or_alert(
    monkeypatch, tmp_path,
):
    ticker, _book, tech = prepare(monkeypatch, tmp_path)
    tech.atr14 = D("0.8")
    tech.swing_low = D("98")
    monkeypatch.setattr(scanner, "SHADOW_MODE", True)
    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", True)
    monkeypatch.setattr(scanner, "TELEGRAM_NEAR_READY_ALERTS", False)
    monkeypatch.setattr(scanner, "score_candidate", lambda *a: (88, []))
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (True, "OK"))
    monkeypatch.setattr(scanner, "setup_type", lambda *a: (True, "RECOVERY"))
    monkeypatch.setattr(
        scanner, "_update_watchlist",
        lambda state, candidate, now: state["watchlist"].update(
            {candidate.symbol: {}}
        ),
    )
    monkeypatch.setattr(scanner, "trigger_check", lambda *a: (True, "OK", D("2"), 2))
    monkeypatch.setattr(scanner, "_global_alert_allowed", lambda *a: True)
    monkeypatch.setattr(scanner, "_symbol_alert_allowed", lambda *a: True)
    monkeypatch.setattr(scanner, "build_opportunity", lambda *a: None)
    monkeypatch.setattr(
        scanner, "build_risk_plan_result",
        lambda **kw: NS(
            plan=None,
            rejection_reason="risk_out_of_range",
            evidence={"risk_pct": "6.5", "max_risk_pct": "4.00"},
        ),
    )
    # prepare() fails the test if any Telegram message is actually attempted.
    scanner.run_scanner()
    state = scanner.load_state()
    decision = state["scan_diagnostics"][-1]["symbols"][ticker.symbol]
    assert (decision["stage"], decision["reason"]) == ("risk", "risk_out_of_range")
    assert decision["risk_audit"] == {"risk_pct": "6.5", "max_risk_pct": "4.00"}
    assert state["execution_followups"][ticker.symbol]["last_reason"] == "risk:risk_out_of_range"
    assert state["active_signals"] == []
    assert state["shadow_sent_signals"] == {}
    assert state["shadow_paper_signals"] == {}
    assert state["scan_diagnostics"][-1]["alert_sent"] is False
    assert state["scan_diagnostics"][-1]["signal_recorded"] is False
