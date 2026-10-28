"""Research integrity tests, not profitability or production-strategy tests."""
import unittest
from early_surge_research import Protocol, classify_path, discovery_status, features, freeze_events, number, replay


def snapshot(at, price, volume=10_000_000, btc=True):
    return {"quote_at": at, "available_at": at + 30, "state_sha": "fixture",
            "run_id": str(at), "btc_ok": btc, "markets": {"AAA_TL": [price, volume]},
            "decisions": {}, "discoveries": {}}


class ResearchIntegrity(unittest.TestCase):
    def test_future_quotes_cannot_change_features(self):
        history = [(600 + i * 600, 100 + i * .01, 10_000_000 + i * 1000) for i in range(6)]
        before = features(history, 4200, 100.1, 10_010_000)
        self.assertEqual(before, features(history + [(4800, 500, 99_999_999)], 4200, 100.1, 10_010_000))
        self.assertTrue(before["compression_heat"])

    def test_future_outcomes_cannot_change_frozen_candidates(self):
        past = [snapshot(600 + i * 600, 100 + i * .01, 10_000_000 + i * 1000) for i in range(10)]
        frozen = freeze_events(past)
        after = freeze_events(past + [snapshot(7200, 200), snapshot(7800, 1)])
        self.assertEqual(frozen, [e for e in after if e["prediction_at"] <= past[-1]["available_at"]])

    def test_liquidity_and_btc_not_bypassed(self):
        for volume, btc in [(4_999_999, True), (10_000_000, False)]:
            rows = [snapshot(600 + i * 600, 100 + i * .01, volume + (i if btc else 0), btc) for i in range(7)]
            if btc:
                for row in rows:
                    row["markets"]["AAA_TL"][1] = 4_000_000 + row["quote_at"]
            self.assertEqual([], freeze_events(rows))

    def test_missing_future_is_unknown(self):
        self.assertEqual("right_censored", classify_path([(1200, 101)], 600, 100, 6)["status"])

    def test_gaps_do_not_become_non_rallies(self):
        result = classify_path([(600, 100), (4200, 100)], 0, 100, 1)
        self.assertEqual("quote_gap", result["status"])
        self.assertNotIn("surge_5pct", result)

    def test_late_terminal_cannot_create_inside_horizon_surge(self):
        series = [(i * 600, 100) for i in range(1, 6)] + [(3650, 110)]
        result = classify_path(series, 0, 100, 1)
        self.assertEqual("observed", result["status"])
        self.assertFalse(result["surge_5pct"])
        self.assertGreater(result["last_change_pct"], 5)

    def test_adverse_path_before_surge_is_preserved(self):
        series = [(i * 600, (97 if i == 1 else 106)) for i in range(1, 7)]
        result = classify_path(series, 0, 100, 1)
        self.assertTrue(result["surge_5pct"])
        self.assertFalse(result["surge_before_adverse"])

    def test_current_open_candle_does_not_get_discovery_pass_label(self):
        row = {"discoveries": {"AAA_TL": {"authentic_only": True,
               "closed_candle_at": 3600, "reason": "passed_pending_trigger"}}}
        self.assertEqual("not_revalidated_or_missing_candle_evidence", discovery_status(row, "AAA_TL", 4200))

    def test_invalid_numbers_rejected(self):
        for value in [True, "nan", "inf", None]:
            self.assertIsNone(number(value))

    def test_long_gap_breaks_persistence(self):
        self.assertIsNone(features([(600, 100, 10_000_000)], 2400, 101, 10_010_000))

    def test_no_case_symbol_allowlist_or_success_filter(self):
        rows = [snapshot(600, 100), snapshot(1200, 100.1, 10_001_000)]
        events = freeze_events(rows)
        self.assertEqual(["AAA_TL"], [e["symbol"] for e in events])
        self.assertNotIn("outcomes", events[0])

    def test_incremental_replay_matches_one_pass_without_duplicate_candidates(self):
        rows = [snapshot(600 + i * 600, 100 + i * .02, 10_000_000 + i * 1000) for i in range(55)]
        entire = {e["id"]: e for e in replay(rows)}
        seed = {}
        first = replay(rows[:35], continuity=seed)
        second = replay(rows[33:], continuity=seed)
        combined = {e["id"]: e for e in first + second}
        self.assertEqual(entire, combined)
        self.assertEqual(len(second), len({e["id"] for e in second}))


if __name__ == "__main__":
    unittest.main()
