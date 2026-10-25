"""Offline integration: risk-rejected followup persists only for its fixed TTL.

No Paribu or Telegram requests; original READY gates remain fail-closed.
"""
from decimal import Decimal as D
from types import SimpleNamespace as NS

import scanner
from test_scanner_research import prepare


def test_risk_episode_overheat_recheck_then_fixed_expiry(monkeypatch, tmp_path):
    ticker, book, tech = prepare(monkeypatch, tmp_path)
    tech.atr14 = D("0.8")
    tech.swing_low = D("98")
    tech.rsi14 = D("60")
    clock = {"now": 1792732392}
    monkeypatch.setattr(scanner.time, "time", lambda: clock["now"])
    monkeypatch.setattr(scanner, "SHADOW_MODE", True)
    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", True)
    monkeypatch.setattr(scanner, "TELEGRAM_NEAR_READY_ALERTS", False)
    monkeypatch.setattr(scanner, "score_candidate", lambda *a: (88, []))
    monkeypatch.setattr(
        scanner, "discovery_ok",
        lambda *a: (False, "RSI outside watch range")
        if tech.rsi14 > scanner.WATCH_RSI_HIGH else (True, "OK"),
    )
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
            plan=None, rejection_reason="risk_out_of_range",
            evidence={"risk_pct": "5", "max_risk_pct": "4"},
        ),
    )

    # A genuine eligible technical candidate is rejected by risk planning.
    scanner.run_scanner()
    first = scanner.load_state()
    followup = first["execution_followups"][ticker.symbol]
    assert followup["first_seen"] == clock["now"]
    assert followup["last_reason"] == "risk:risk_out_of_range"
    assert first["shadow_sent_signals"] == {}
    assert first["active_signals"] == []

    # The next scan's order book becomes unusable. This must record the
    # current rejection without erasing the earlier risk-followup provenance.
    clock["now"] += 300
    book.spread_percent = scanner.MAX_SPREAD_PCT + D("0.01")
    scanner.run_scanner()
    wide = scanner.load_state()
    assert wide["scan_diagnostics"][-1]["symbols"][ticker.symbol]["reason"] == (
        "spread_too_high"
    )
    assert wide["execution_followups"][ticker.symbol]["last_reason"] == "spread_too_high"
    assert wide["execution_followups"][ticker.symbol]["risk_rejection_origin"] is True
    assert wide["execution_followups"][ticker.symbol]["first_seen"] == followup["first_seen"]
    assert wide["shadow_sent_signals"] == {}
    assert wide["active_signals"] == []

    # The book recovers but RSI overheats: still no READY, yet bounded
    # priority persists until its original expiry (not a renewed TTL).
    clock["now"] += 300
    book.spread_percent = D("0.1")
    tech.rsi14 = scanner.WATCH_RSI_HIGH + D("0.01")
    scanner.run_scanner()
    second = scanner.load_state()
    assert second["scan_diagnostics"][-1]["symbols"][ticker.symbol]["reason"] == (
        "RSI outside watch range"
    )
    preserved = second["execution_followups"][ticker.symbol]
    assert preserved["first_seen"] == followup["first_seen"]
    # last_seen accurately advances to the most recent rejected book scan,
    # but it must NOT move the original fixed TTL anchor (first_seen).
    assert preserved["last_seen"] == wide["execution_followups"][ticker.symbol]["last_seen"]
    assert preserved["last_seen"] == followup["first_seen"] + 300
    assert not second["active_signals"]
    assert not second["shadow_sent_signals"]

    # The original fixed TTL expires despite the intervening RSI scan.
    clock["now"] = followup["first_seen"] + scanner.EXECUTION_FOLLOWUP_TTL_SECONDS + 1
    scanner.run_scanner()
    expired = scanner.load_state()
    assert ticker.symbol not in expired["execution_followups"]
    assert not expired["active_signals"]
    assert not expired["shadow_sent_signals"]
    assert expired["scan_diagnostics"][-1]["alert_sent"] is False
