from collections import Counter
from decimal import Decimal as D
import json
from types import SimpleNamespace as NS

import pytest

from coverage_scheduler import CoverageCycle
from research_report import build_report
import scanner
from test_scanner_research import prepare


def test_rotating_selection_survives_restart_under_unchanged_budget():
    state = {}
    # Input order represents a stable descending volume rank.
    symbols = [f"M{i}_TL" for i in range(234)]
    seen = Counter()
    for _ in range(3):
        cycle = CoverageCycle(state, symbols)
        selected = cycle.order(symbols, "orderbooks", lambda symbol: symbol)[:80]
        assert len(selected) == 80
        for symbol in selected:
            cycle.attempted("orderbooks", symbol)
            seen[symbol] += 1
        state = json.loads(json.dumps(state))
    assert len(seen) == 234


def test_two_budgets_rotate_independently():
    state = {}
    symbols = list("ABCD")
    seen = set()
    for _ in range(4):
        cycle = CoverageCycle(state, symbols)
        books = cycle.order(symbols, "orderbooks", lambda symbol: symbol)[:2]
        for symbol in books:
            cycle.attempted("orderbooks", symbol)
        selected = cycle.order(books, "technicals", lambda symbol: symbol)[:1]
        for symbol in selected:
            cycle.attempted("technicals", symbol)
            seen.add(symbol)
    assert seen == set(symbols)


@pytest.mark.parametrize("state", [
    {"generation": -1}, {"generation": "1"}, {"generation": True},
    {"orderbooks": []}, {"generation": 1, "technicals": {"A": 2}},
])
def test_corrupt_coverage_state_is_not_silently_reset(state):
    with pytest.raises(ValueError, match="Invalid coverage"):
        CoverageCycle(state, ["A"])


def test_removed_symbols_pruned_without_changing_volume_tie_order():
    state = {"generation": 2, "orderbooks": {"OLD": 1, "B": 2}}
    cycle = CoverageCycle(state, ["A", "B", "C"])
    assert "OLD" not in state["orderbooks"]
    assert cycle.order(["B", "C", "A"], "orderbooks", lambda item: item) == ["C", "A", "B"]


def test_scanner_rotates_both_budgets_without_changing_gates(monkeypatch, tmp_path):
    _, book, tech = prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(scanner, "SHADOW_MODE", True)
    monkeypatch.setenv("SHADOW_RADAR_ENABLED", "false")
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 2)
    monkeypatch.setattr(scanner, "MAX_TECHNICAL_MARKETS", 1)
    snapshot = {f"M{i}_TL": NS(symbol=f"M{i}_TL", last=D("100"),
                              quote_volume=D("10000000")-i) for i in range(4)}
    # This below-minimum market must be classified even after the book cap.
    snapshot["LOW_TL"] = NS(symbol="LOW_TL", last=D("100"), quote_volume=D("1"))
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: snapshot)
    book_calls, technical_calls = [], []
    monkeypatch.setattr(scanner, "get_order_book", lambda symbol, *a: book_calls.append(symbol) or book)
    from test_accumulation_radar import fixture
    frame, _ = fixture()
    def candles(symbol, interval, *args):
        if interval == "15m":
            technical_calls.append(symbol)
        return frame
    monkeypatch.setattr(scanner, "fetch_candles", candles)
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (False, "unchanged rejection"))
    for _ in range(4):
        before_books, before_technicals = len(book_calls), len(technical_calls)
        scanner.run_scanner()
        assert len(book_calls) - before_books == 2
        assert len(technical_calls) - before_technicals == 1
        state = scanner.load_state()
        decisions = state["scan_diagnostics"][-1]["symbols"]
        assert decisions["LOW_TL"]["reason"] == "quote_volume_below_minimum"
        assert not state["active_signals"]
        assert any(d["reason"] == "unchanged rejection" for d in decisions.values())
    assert set(book_calls) == set(technical_calls) == set(snapshot) - {"LOW_TL"}
    assert scanner.load_state()["coverage_scheduler"]["generation"] == 4


def test_report_distinguishes_book_and_technical_capacity():
    report = build_report({"scan_diagnostics": [{"status": "completed", "finished_at": 100,
        "symbols": {"A": {"reason": "not_evaluated_capacity"},
                    "B": {"reason": "not_evaluated_technical_capacity"}}}]}, now=100)
    assert report["coverage"]["unexamined_capacity"] == 2
    assert report["coverage"]["unexamined_orderbook_capacity"] == 1
    assert report["coverage"]["unexamined_technical_capacity"] == 1
    assert "universe_not_fully_evaluated" in report["warnings"]
