from collections import Counter
from decimal import Decimal as D
from types import SimpleNamespace as NS
import json

import pytest

from coverage_scheduler import CoverageCycle
import scanner


def tickers(n=8, hot=6, pulse=0):
    return [NS(symbol=f"M{i}_TL", last=D(100 + pulse if i < hot else 100),
               quote_volume=D(10000000 + pulse * 100000 if i < hot else 10000000))
            for i in range(n)]


def test_continuing_hot_markets_cannot_starve_cold_markets(monkeypatch):
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 2)
    root = {"coverage_scheduler": {}}
    seen = Counter()
    for pulse in range(10):
        items = tickers(pulse=pulse)
        cycle = CoverageCycle(root["coverage_scheduler"], [t.symbol for t in items])
        ordered, _ = scanner._hot_radar_order(items, root, cycle, 1000 + pulse * 600)
        for item in ordered[:2]:
            cycle.attempted("orderbooks", item.symbol)
            seen[item.symbol] += 1
    assert set(seen) == {f"M{i}_TL" for i in range(8)}


@pytest.mark.parametrize("prior_at", [None, 100, 3000, 2200])
def test_stale_future_or_equal_time_snapshots_do_not_boost(monkeypatch, prior_at):
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 2)
    root = {"coverage_scheduler": {"generation": 2}, "ticker_radar": {
        "observed_at": prior_at,
        "markets": {"M4_TL": {"last": "90", "quote_volume": "9000000"}},
    }}
    items = tickers(hot=0)
    cycle = CoverageCycle(root["coverage_scheduler"], [t.symbol for t in items])
    ordered, meta = scanner._hot_radar_order(items, root, cycle, now=2200)
    assert meta["eligible_count"] == 0
    assert [t.symbol for t in ordered[:2]] == ["M0_TL", "M1_TL"]


def test_pending_confirmation_rechecked_when_new_candle_can_exist(monkeypatch):
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 4)
    items = tickers(hot=0)
    root = {"coverage_scheduler": {"generation": 2, "orderbooks": {"M7_TL": 2}},
            "candidate_lifecycle": {"M7_TL": {"current_state": "confirmation_1_of_2"}},
            "watchlist": {"M7_TL": {"history": [{"time": 3610, "candle_at": 2700}]}}}
    cycle = CoverageCycle(root["coverage_scheduler"], [t.symbol for t in items])
    ordered, meta = scanner._hot_radar_order(items, root, cycle, now=4510)
    assert ordered[0].symbol == "M7_TL"
    assert meta["confirmation_symbols"] == ["M7_TL"]
    assert len(ordered) == len(items)
    assert len({t.symbol for t in ordered}) == len(items)


@pytest.mark.parametrize("now,lifecycle", [(4200, "confirmation_1_of_2"),
                                         (12000, "confirmation_1_of_2"),
                                         (4510, "rejected")])
def test_pending_priority_needs_fresh_watch_and_new_candle(monkeypatch, now, lifecycle):
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 4)
    items = tickers(hot=0)
    root = {"coverage_scheduler": {"generation": 2, "orderbooks": {"M7_TL": 2}},
            "candidate_lifecycle": {"M7_TL": {"current_state": lifecycle}},
            "watchlist": {"M7_TL": {"history": [{"time": 3610, "candle_at": 2700}]}}}
    cycle = CoverageCycle(root["coverage_scheduler"], [t.symbol for t in items])
    ordered, meta = scanner._hot_radar_order(items, root, cycle, now=now)
    assert "M7_TL" not in meta.get("confirmation_symbols", [])
    assert ordered[0].symbol == "M0_TL"


def test_full_universe_is_covered_despite_persistent_heat_and_restarts(monkeypatch):
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 80)
    root = {"coverage_scheduler": {}}
    seen = set()
    for pulse in range(7):
        items = tickers(n=234, hot=160, pulse=pulse)
        cycle = CoverageCycle(root["coverage_scheduler"], [t.symbol for t in items])
        ordered, meta = scanner._hot_radar_order(items, root, cycle, 1000 + pulse * 600)
        assert len(meta["selected_priority_symbols"]) <= 40
        assert len(ordered[:80]) == 80
        for item in ordered[:80]:
            cycle.attempted("orderbooks", item.symbol)
            seen.add(item.symbol)
        root = json.loads(json.dumps(root))
    assert len(seen) == 234


def test_excluded_market_cannot_consume_priority_slot(monkeypatch):
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 2)
    root = {"coverage_scheduler": {}, "ticker_radar": {"observed_at": 1000,
        "markets": {"LOW_TL": {"last": "1", "quote_volume": "1"},
                    "M7_TL": {"last": "99", "quote_volume": "9000000"}}}}
    items = [NS(symbol="LOW_TL", last=D(100), quote_volume=D(100))] + tickers(hot=0)
    cycle = CoverageCycle(root["coverage_scheduler"], [t.symbol for t in items])
    ordered, meta = scanner._hot_radar_order(items, root, cycle, now=1600)
    assert ordered[0].symbol == "M7_TL"
    assert "LOW_TL" not in meta["hot_symbols"]
    assert ordered[-1].symbol == "LOW_TL"


def test_priority_survives_technical_budget_without_bypassing_rejection(monkeypatch, tmp_path):
    from test_scanner_research import prepare
    from test_accumulation_radar import NOW, fixture
    _, book, _ = prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 4)
    monkeypatch.setattr(scanner, "MAX_TECHNICAL_MARKETS", 2)
    monkeypatch.setenv("SHADOW_RADAR_ENABLED", "false")
    items = tickers(hot=0)
    root = scanner._empty_state()
    root["ticker_radar"] = {"observed_at": NOW - 600,
        "markets": {"M7_TL": {"last": "99", "quote_volume": "9000000"}}}
    root["coverage_scheduler"] = {"generation": 2, "technicals": {"M7_TL": 2}}
    scanner.save_state(root)
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: {t.symbol: t for t in items})
    books, candles = [], []
    monkeypatch.setattr(scanner, "get_order_book", lambda symbol, *a: books.append(symbol) or book)
    frame, _ = fixture()
    def fetch(symbol, interval, *args):
        if interval == "15m":
            candles.append(symbol)
        return frame
    monkeypatch.setattr(scanner, "fetch_candles", fetch)
    monkeypatch.setattr(scanner, "discovery_ok", lambda *a: (False, "unchanged gate"))
    scanner.run_scanner()
    assert len(books) == 4 and len(candles) == 2
    assert books[0] == candles[0] == "M7_TL"
    after = scanner.load_state()
    assert after["active_signals"] == []
    assert after["scan_diagnostics"][-1]["symbols"]["M7_TL"]["reason"] == "unchanged gate"
    assert after["scan_diagnostics"][-1]["hot_radar"]["technical_priority_symbols"] == ["M7_TL"]


def test_many_pending_confirmations_leave_hot_and_fair_slots(monkeypatch):
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 80)
    items = tickers(n=234, hot=0)
    root = {"coverage_scheduler": {}, "candidate_lifecycle": {}, "watchlist": {},
            "ticker_radar": {"observed_at": 3910, "markets": {}}}
    for i in range(80):
        name = f"M{i}_TL"
        root["candidate_lifecycle"][name] = {"current_state": "confirmation_1_of_2"}
        root["watchlist"][name] = {"history": [{"time": 3610, "candle_at": 2700}]}
    for i in range(100, 160):
        root["ticker_radar"]["markets"][f"M{i}_TL"] = {"last": "99", "quote_volume": "9000000"}
    cycle = CoverageCycle(root["coverage_scheduler"], [t.symbol for t in items])
    ordered, meta = scanner._hot_radar_order(items, root, cycle, now=4510)
    assert len(meta["confirmation_symbols"]) == 20
    assert len(meta["selected_priority_symbols"]) == 40
    assert [t.symbol for t in ordered[:80]][40:] == [f"M{i}_TL" for i in range(20, 60)]



def test_early_watch_followup_gets_priority_without_ready_confirmation(monkeypatch):
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 4)
    items = tickers(hot=0)
    root = {
        "coverage_scheduler": {"generation": 2, "orderbooks": {"M7_TL": 2}},
        "early_watch_followups": {
            "M7_TL": {
                "symbol": "M7_TL",
                "first_seen": 3000,
                "last_seen": 3610,
                "last_candle_at": 2700,
                "last_score": 82,
                "max_score": 82,
                "last_reason": "4h clearly weak",
            }
        },
        "watchlist": {},
        "candidate_lifecycle": {},
    }
    cycle = CoverageCycle(root["coverage_scheduler"], [t.symbol for t in items])
    ordered, meta = scanner._hot_radar_order(items, root, cycle, now=4510)
    assert ordered[0].symbol == "M7_TL"
    assert meta["early_watch_symbols"] == ["M7_TL"]
    assert meta["confirmation_symbols"] == []
    assert root["watchlist"] == {}


def test_early_watch_followup_waits_for_new_closed_candle(monkeypatch):
    monkeypatch.setattr(scanner, "MAX_ORDERBOOK_MARKETS", 4)
    items = tickers(hot=0)
    root = {
        "coverage_scheduler": {"generation": 2},
        "early_watch_followups": {
            "M7_TL": {
                "last_seen": 3000,
                "last_candle_at": 2700,
            }
        },
    }
    cycle = CoverageCycle(root["coverage_scheduler"], [t.symbol for t in items])
    ordered, meta = scanner._hot_radar_order(items, root, cycle, now=3200)
    assert "M7_TL" not in meta["early_watch_symbols"]
    assert ordered[0].symbol == "M0_TL"


def test_recorded_early_watch_is_separate_from_ready_confirmation_history():
    root = scanner._empty_state()
    scanner._record_early_watch_followup(
        root,
        symbol="SYN_TL",
        now=1000,
        candle_at=900,
        score=82,
        reason="4h clearly weak",
    )
    assert root["early_watch_followups"]["SYN_TL"]["last_score"] == 82
    assert "SYN_TL" not in root["watchlist"]
