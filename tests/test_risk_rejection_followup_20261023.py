from decimal import Decimal
from types import SimpleNamespace

import scanner


def test_risk_rejected_candidate_gets_bounded_followup():
    state = scanner._empty_state()
    candidate = SimpleNamespace(
        symbol="PEPE_TL",
        score=88,
        book=SimpleNamespace(
            best_ask=Decimal("0.00022325"),
            spread_percent=Decimal("0.1"),
            imbalance_ratio=Decimal("1.4"),
        ),
    )
    at = 1792732392
    assert scanner._retain_risk_rejection_followup(
        state, candidate, "risk_out_of_range", at
    )
    assert state["execution_followups"]["PEPE_TL"]["first_seen"] == at
    assert not scanner._retain_risk_rejection_followup(
        state, SimpleNamespace(**{**candidate.__dict__, "score": 79}),
        "risk_out_of_range", at + 1
    )
    assert scanner._retain_risk_rejection_followup(
        state, candidate, "rr_below_min", at + 600
    )
    assert state["execution_followups"]["PEPE_TL"]["first_seen"] == at
    scanner._prune_execution_followups(
        state, at + scanner.EXECUTION_FOLLOWUP_TTL_SECONDS + 1
    )
    assert "PEPE_TL" not in state["execution_followups"]
