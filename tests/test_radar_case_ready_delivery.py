"""Full follow-up and notification path with explicit offline fixtures.

These are behavior tests, not a claim that the photographed markets passed
READY in production. No market data, order, or Telegram request is sent.
"""

import pytest

import scanner
import test_post_radar_recovery as harness


@pytest.mark.parametrize("symbol", ["NMR_TL", "RAD_TL", "ALT_TL", "SAND_TL"])
@pytest.mark.parametrize("blocker", ["btc", "synthetic"])
def test_followed_case_is_rechecked_and_delivered_after_full_requalification(
    monkeypatch, tmp_path, symbol, blocker
):
    monkeypatch.setattr(harness, "SYMBOL", symbol)
    clock, calls, scan = harness.pipeline(monkeypatch, tmp_path, contended=True)
    delivered = []
    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", True)
    monkeypatch.setattr(
        scanner, "send_shadow_ready_alert",
        lambda opportunity: delivered.append(opportunity.symbol) or True,
    )

    # The high score first becomes a follow-up, without overriding its RSI.
    first = scan()
    assert first["watchlist"][symbol]["last_score"] == 83
    assert symbol in first["early_watch_followups"]
    assert not delivered and not first["active_signals"]

    clock.update(now=harness.NOW + 900, rsi=harness.D("60"))
    clock[blocker] = blocker == "synthetic"
    rejected = scan()  # Reload the persisted state, then recheck real gates.
    decision = rejected["scan_diagnostics"][-1]["symbols"][symbol]
    if blocker == "btc":
        assert decision["reason"].startswith("BTC blocked:")
    else:
        assert decision["reason"] == "synthetic_candles_present"
    assert symbol in calls["books"]
    assert not delivered and not rejected["active_signals"]
    assert rejected["shadow_sent_signals"] == {}

    # Genuine fixture bars and healthy BTC return on a distinct closed bar.
    # Discovery, trigger, risk, fresh book, and delivery all run unchanged.
    clock.update(now=harness.NOW + 1800, synthetic=False, btc=True)
    recovered = scan()
    assert calls["books"].count(symbol) == 2  # Includes the final refreshed book.
    assert (symbol, "15m") in calls["candles"]
    assert len(set(calls["books"])) == 80
    assert delivered == [symbol]
    assert recovered["scan_diagnostics"][-1]["alert_sent"] is True
    assert recovered["shadow_sent_signals"] == {symbol: harness.NOW + 1800}
    assert len(recovered["active_signals"]) == 1
    assert recovered["active_signals"][0]["symbol"] == symbol
    assert symbol not in recovered["execution_followups"]
    assert symbol not in recovered["early_watch_followups"]
    history = recovered["watchlist"][symbol]["history"]
    assert len({event["candle_at"] for event in history}) >= 2
