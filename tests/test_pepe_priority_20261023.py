from decimal import Decimal
from types import SimpleNamespace

import scanner
from coverage_scheduler import CoverageCycle
from market_data import Ticker


def test_risk_blocked_pepe_is_selected_for_fresh_scan():
    now = 1792732392
    state = scanner._empty_state()
    candidate = SimpleNamespace(
        symbol="PEPE_TL", score=88,
        book=SimpleNamespace(
            best_ask=Decimal("0.00022325"),
            spread_percent=Decimal("0.1"),
            imbalance_ratio=Decimal("1.4"),
        ),
    )
    assert scanner._retain_risk_rejection_followup(
        state, candidate, "risk_out_of_range", now
    )
    ticker = Ticker(
        symbol="PEPE_TL", last=Decimal("0.00022325"),
        quote_volume=Decimal("25000000"),
    )
    coverage = CoverageCycle(state["coverage_scheduler"], ["PEPE_TL"])
    _, metrics = scanner._hot_radar_order(
        [ticker], state, coverage, now + 600
    )
    assert "PEPE_TL" in metrics["execution_followup_symbols"]
    assert "PEPE_TL" in metrics["selected_priority_symbols"]
    assert state["shadow_sent_signals"] == {}
    assert state["shadow_paper_signals"] == {}
