"""Refresh decisions use one book for scoring, gates, risk and notification.

All quotes and indicators are explicit offline software fixtures. These tests
exercise the scanner pipeline without HTTP, Telegram or trading requests.
"""

from copy import deepcopy
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest

import scanner
import test_post_radar_recovery as harness


def pipeline(monkeypatch, tmp_path, *, near=False, explosive=False, improved=False):
    clock, calls, scan = harness.pipeline(monkeypatch, tmp_path)
    snapshot = scanner.get_market_snapshot
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: {
        harness.SYMBOL: snapshot()[harness.SYMBOL],
    })
    ticker = scanner.get_market_snapshot()[harness.SYMBOL]
    ticker.quote_volume = D("25000000")
    tech = NS(
        current_close=D("100"), ema9=D("99.5"), ema21=D("99"),
        ema50=D("98"), atr14=D("1"), swing_low=D("98"),
        is_uptrend=True, is_above_ema9=True, is_above_ema21=True,
        volume_ratio=D("1.80" if explosive else "1.50" if improved else "1.30"),
        macd_histogram=D(".1"), macd_line=D("1"), macd_signal=D("0"),
        rsi14=D("57"), recent_return_3=D(".50"), recent_return_12=D("1"),
        recent_return_48=D("2"), breakout=explosive, is_pullback=False,
        is_bullish_candle=True, mean_touch=False,
    )
    hourly = NS(**{**vars(tech), "ema21": D("100"),
                   "is_uptrend": False, "macd_histogram": D("0")})
    four_hour = NS(**{**vars(tech), "current_close": D("98.5"), "ema50": D("100")})
    monkeypatch.setattr(scanner, "analyze_symbol", lambda frame: {
        "15m": tech, "1h": hourly, "4h": four_hour,
    }[frame.attrs["resolution"]])

    def book(imbalance, spread):
        return NS(best_bid=D("100"), best_ask=D("100") + D(spread),
                  spread_percent=D(spread), imbalance_ratio=D(imbalance),
                  largest_bid_wall_share=D(".20"), largest_ask_wall_share=D(".20"),
                  bids=(), asks=())

    initial = book("1.15", ".30") if improved else book("1.35", ".10")
    clock["fresh_book"] = initial
    original_book = scanner.get_order_book

    def get_book(symbol, *args):
        original_book(symbol, *args)  # Keep the harness's actual fetch counter.
        return clock["fresh_book"] if calls["books"].count(symbol) == 2 else initial

    monkeypatch.setattr(scanner, "get_order_book", get_book)
    monkeypatch.setattr(scanner, "TELEGRAM_NEAR_READY_ALERTS", near)
    monkeypatch.setattr(scanner, "TELEGRAM_READY_ALERTS", True)
    messages, ready, checked, risk_candidates = [], [], [], []
    monkeypatch.setattr(scanner, "send_telegram", lambda msg: messages.append(msg) or True)
    monkeypatch.setattr(scanner, "send_shadow_ready_alert", lambda opp: ready.append(opp) or True)
    trigger = scanner.trigger_check

    def check(candidate, item, *args):
        checked.append((candidate, deepcopy(item)))
        return trigger(candidate, item, *args)

    monkeypatch.setattr(scanner, "trigger_check", check)
    build = scanner.build_opportunity

    def opportunity(candidate, *args):
        risk_candidates.append(candidate)
        return build(candidate, *args)

    monkeypatch.setattr(scanner, "build_opportunity", opportunity)
    return clock, calls, scan, book, messages, ready, checked, risk_candidates


def test_ready_rejects_score_that_fell_below_threshold_and_retains_followup(monkeypatch, tmp_path):
    clock, calls, scan, book, messages, ready, checked, risk = pipeline(monkeypatch, tmp_path)
    first = scan()
    old_history = deepcopy(first["watchlist"][harness.SYMBOL]["history"])
    clock.update(now=harness.NOW + 900, fresh_book=book("1.12", ".39"))
    state = scan()

    assert state["scan_diagnostics"][-1]["symbols"][harness.SYMBOL]["reason"] == (
        "fresh_book_rejected: score 70 < 80"
    )
    assert state["scan_diagnostics"][-1]["symbols"][harness.SYMBOL]["score"] == 70
    assert checked[-2][0].score == 80
    assert checked[-1][0].score == 70
    assert "strong buy-side order book" not in checked[-1][0].reasons
    assert messages == ready == risk == []
    assert state["active_signals"] == [] and state["shadow_sent_signals"] == {}
    assert state["execution_followups"][harness.SYMBOL]["last_reason"] == (
        "fresh_book_rejected: score 70 < 80"
    )
    assert state["watchlist"][harness.SYMBOL]["history"][:1] == old_history
    assert len(state["watchlist"][harness.SYMBOL]["history"]) == 2
    assert calls["books"] == [harness.SYMBOL, harness.SYMBOL]


def test_stale_score_cannot_activate_single_candle_breakout_exception(monkeypatch, tmp_path):
    clock, calls, scan, book, messages, ready, checked, risk = pipeline(
        monkeypatch, tmp_path, explosive=True,
    )
    clock["fresh_book"] = book("1.25", ".39")
    state = scan()

    assert [c.score for c, _ in checked] == [90, 84]
    assert state["scan_diagnostics"][-1]["symbols"][harness.SYMBOL]["reason"] == (
        "fresh_book_rejected: watching: confirmations 1/2"
    )
    assert len(state["watchlist"][harness.SYMBOL]["history"]) == 1
    assert messages == ready == risk == []
    assert state["active_signals"] == [] and state["shadow_sent_signals"] == {}
    assert harness.SYMBOL in state["execution_followups"]
    assert calls["books"] == [harness.SYMBOL, harness.SYMBOL]


def test_near_ready_rejects_a_fresh_score_below_threshold(monkeypatch, tmp_path):
    clock, calls, scan, book, messages, ready, checked, risk = pipeline(
        monkeypatch, tmp_path, near=True,
    )
    clock["fresh_book"] = book("1.12", ".39")
    state = scan()

    assert [c.score for c, _ in checked] == [80, 70]
    assert messages == ready == risk == []
    assert state["scan_diagnostics"][-1]["funnel"]["near_ready_alerts"] == 0
    assert not state["watchlist"][harness.SYMBOL].get("near_ready_attempted_at")
    assert len(state["watchlist"][harness.SYMBOL]["history"]) == 1
    assert calls["books"] == [harness.SYMBOL, harness.SYMBOL]


@pytest.mark.parametrize("near", [False, True], ids=["ready", "near-ready"])
@pytest.mark.parametrize("improved", [False, True], ids=["unchanged-book", "improved-book"])
def test_qualified_refresh_uses_current_score_and_reasons_through_delivery(
    monkeypatch, tmp_path, near, improved,
):
    clock, calls, scan, book, messages, ready, checked, risk = pipeline(
        monkeypatch, tmp_path, near=near, improved=improved,
    )
    if not near:
        scan()
        clock["now"] += 900
    clock["fresh_book"] = book("1.35", ".10")
    state = scan()

    current = checked[-1][0]
    expected_score = 86 if improved else 80
    assert checked[-2][0].score == (82 if improved else 80)
    assert current.score == expected_score
    score, reasons = scanner.score_candidate(
        current.ticker, current.book, current.tech_15, current.tech_1h, current.tech_4h,
    )
    assert current.reasons == reasons + ["discovery: OK"]
    assert current.score == score
    assert risk[-1] is current
    assert len(risk) == 1
    assert calls["books"] == [harness.SYMBOL, harness.SYMBOL]
    if near:
        assert ready == [] and len(messages) == 1
        assert f"{expected_score}/100" in messages[0]
        assert state["active_signals"] == [] and state["shadow_sent_signals"] == {}
        assert len(state["watchlist"][harness.SYMBOL]["history"]) == 1
    else:
        assert messages == [] and len(ready) == 1
        assert ready[0].score == expected_score
        assert ready[0].spread_pct == current.book.spread_percent
        assert ready[0].imbalance == current.book.imbalance_ratio
        assert state["shadow_sent_signals"] == {harness.SYMBOL: clock["now"]}
        assert len(state["active_signals"]) == 1
        assert harness.SYMBOL not in state["execution_followups"]
        assert len(state["watchlist"][harness.SYMBOL]["history"]) == 2
