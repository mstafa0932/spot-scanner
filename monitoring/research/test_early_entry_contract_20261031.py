"""Software contract tests with explicit fixtures; no market-performance claim."""
from copy import deepcopy
import ast
from pathlib import Path
import unittest

from early_entry_contract_20261031 import advance, REQUIRED_GATES, TTL_SECONDS

NOW = 1800900  # Arbitrary test-fixture clock, unrelated to market evidence.


def fixture():
    return {'symbol': 'TEST_TL',
            'precursor': {'candidate': True, 'source': 'PARIBU', 'authentic_only': True,
                          'closed_candle_at': NOW - 900, 'available_at': NOW,
                          'source_ref': 'unit-test-fixture-not-market-data'},
            'gates': {g: {'status': 'PASS', 'available_at': NOW, 'expires_at': NOW + 60,
                          'source_ref': 'unit-test-fixture-not-market-data',
                          'book_ref': 'unit-test-fixture-not-market-data'} for g in REQUIRED_GATES}}


class ContractTests(unittest.TestCase):
    def test_all_gate_success_remains_non_live(self):
        state, decision = advance(None, fixture(), NOW)
        self.assertEqual(decision['stage'], 'ENTRY_REVIEWABLE')
        for key in ('notify', 'is_ready', 'is_trade'):
            self.assertIs(decision[key], False)
        self.assertEqual(state['expires_at'], NOW + TTL_SECONDS)

    def test_every_missing_gate_blocks(self):
        for g in REQUIRED_GATES:
            with self.subTest(gate=g):
                obs = fixture(); del obs['gates'][g]
                _, decision = advance(None, obs, NOW)
                self.assertEqual(decision['stage'], 'WAIT_DATA')
                self.assertIn(g + ':UNKNOWN', decision['reasons'])

    def test_every_failed_gate_blocks(self):
        for g in REQUIRED_GATES:
            with self.subTest(gate=g):
                obs = fixture(); obs['gates'][g]['status'] = 'FAIL'
                _, decision = advance(None, obs, NOW)
                self.assertNotEqual(decision['stage'], 'ENTRY_REVIEWABLE')
                self.assertIn(g + ':FAIL', decision['reasons'])

    def test_four_hour_failure_never_becomes_implicit_exception(self):
        obs = fixture(); obs['gates']['higher_timeframe']['status'] = 'FAIL'
        _, decision = advance(None, obs, NOW)
        self.assertEqual(decision['stage'], 'WAIT_STRATEGY')

    def test_high_score_cannot_override_recorded_risk_failure(self):
        obs = fixture(); obs['score'] = 100; obs['gates']['risk_plan']['status'] = 'FAIL'
        _, decision = advance(None, obs, NOW)
        self.assertEqual(decision['stage'], 'WAIT_EXECUTION')

    def test_capacity_skip_keeps_first_preparation_and_deadline(self):
        state, _ = advance(None, fixture(), NOW)
        state2, decision = advance(state, {'symbol': 'TEST_TL'}, NOW + 600)
        self.assertEqual(decision['stage'], 'WAIT_DATA')
        self.assertEqual(state2['first_prepared_at'], NOW)
        self.assertEqual(state2['expires_at'], NOW + TTL_SECONDS)

    def test_repeated_positive_observation_does_not_slide_deadline(self):
        obs = fixture(); state, _ = advance(None, obs, NOW)
        obs['precursor']['available_at'] = NOW + 300
        for e in obs['gates'].values():
            e.update(available_at=NOW + 300, expires_at=NOW + 360)
        state2, _ = advance(state, obs, NOW + 300)
        self.assertEqual(state2['expires_at'], state['expires_at'])

    def test_fixed_expiry_is_not_reset_by_new_candle(self):
        state, _ = advance(None, fixture(), NOW)
        now2 = NOW + TTL_SECONDS
        obs = fixture(); obs['precursor'].update(closed_candle_at=now2 - 900, available_at=now2)
        state2, decision = advance(state, obs, now2)
        self.assertEqual(decision['stage'], 'EXPIRED')
        self.assertEqual(state2['first_prepared_at'], NOW)

    def test_same_closed_candle_does_not_duplicate_review(self):
        state, _ = advance(None, fixture(), NOW)
        _, decision = advance(state, fixture(), NOW + 1)
        self.assertEqual(decision['stage'], 'ALREADY_REVIEWED')

    def test_later_actual_availability_cannot_enter_earlier_decision(self):
        obs = fixture(); obs['precursor']['available_at'] = NOW + 1
        state, decision = advance(None, obs, NOW)
        self.assertEqual(decision['stage'], 'DATA_INVALID')
        self.assertNotIn('first_prepared_at', state)

    def test_open_candle_and_alignment_rejected(self):
        for opened in (NOW, NOW - 899):
            obs = fixture(); obs['precursor']['closed_candle_at'] = opened
            _, decision = advance(None, obs, NOW)
            self.assertEqual(decision['stage'], 'DATA_INVALID')

    def test_truthy_authenticity_and_numeric_boolean_rejected(self):
        for value in (1, 'true', False, None):
            obs = fixture(); obs['precursor']['authentic_only'] = value
            _, decision = advance(None, obs, NOW)
            self.assertEqual(decision['stage'], 'DATA_INVALID')
        obs = fixture(); obs['precursor']['closed_candle_at'] = True
        _, decision = advance(None, obs, NOW)
        self.assertEqual(decision['stage'], 'DATA_INVALID')

    def test_expired_future_and_unreferenced_gate_evidence_are_unknown(self):
        variants = ({'expires_at': NOW}, {'available_at': NOW + 1}, {'source_ref': ''},
                    {'status': True}, {'status': []}, {'status': {}})
        for patch in variants:
            obs = fixture(); obs['gates']['fresh_book'].update(patch)
            _, decision = advance(None, obs, NOW)
            self.assertEqual(decision['stage'], 'WAIT_DATA')
            self.assertEqual(decision['gates']['fresh_book'], 'UNKNOWN')

    def test_future_results_cannot_change_decision(self):
        obs = fixture(); state1, decision1 = advance(None, obs, NOW)
        obs['future_prices'] = [{'at': NOW + 1, 'last': 1000000}]
        state2, decision2 = advance(None, obs, NOW)
        self.assertEqual((state1, decision1), (state2, decision2))

    def test_mixed_book_references_cannot_form_entry(self):
        obs = fixture(); obs['gates']['risk_plan']['book_ref'] = 'different-book'
        _, decision = advance(None, obs, NOW)
        self.assertEqual(decision['stage'], 'WAIT_DATA')
        self.assertIn('risk_plan:UNKNOWN', decision['reasons'])

    def test_score_from_old_book_cannot_qualify_refreshed_entry(self):
        obs = fixture(); obs['gates']['score']['book_ref'] = 'old-book'
        _, decision = advance(None, obs, NOW)
        self.assertEqual(decision['stage'], 'WAIT_DATA')
        self.assertIn('score:UNKNOWN', decision['reasons'])

    def test_entry_book_must_be_recent_despite_longer_claimed_expiry(self):
        _, decision = advance(None, fixture(), NOW + 31)
        self.assertEqual(decision['stage'], 'WAIT_DATA')
        self.assertEqual(decision['gates']['fresh_book'], 'UNKNOWN')

    def test_malformed_saved_clocks_fail_without_exception(self):
        for patch in ({'expires_at': 'bad'}, {'last_qualifying_candle': []},
                      {'first_prepared_at': True, 'expires_at': 1}):
            prior={'symbol':'TEST_TL','last_observed_at':NOW,**patch}
            _, decision = advance(prior, fixture(), NOW)
            self.assertEqual(decision['stage'], 'DATA_INVALID')

    def test_out_of_order_symbol_and_clock_invalid(self):
        state, _ = advance(None, fixture(), NOW)
        for patch, clock in [({'symbol': 'OTHER_TL'}, NOW), ({}, NOW - 1), ({}, True), ({}, float('nan'))]:
            obs = fixture(); obs.update(patch)
            _, decision = advance(state, obs, clock)
            self.assertEqual(decision['stage'], 'DATA_INVALID')

    def test_inputs_are_not_mutated(self):
        obs = fixture(); original = deepcopy(obs)
        state, _ = advance(None, obs, NOW)
        state_before = deepcopy(state)
        advance(state, obs, NOW + 1)
        self.assertEqual(obs, original)
        self.assertEqual(state, state_before)

    def test_missing_precursor_and_negative_detection_do_not_enter(self):
        _, decision = advance(None, {'symbol': 'TEST_TL'}, NOW)
        self.assertEqual(decision['stage'], 'MONITOR')
        obs = fixture(); obs['precursor']['candidate'] = False
        _, decision = advance(None, obs, NOW)
        self.assertEqual(decision['stage'], 'MONITOR')

    def test_contract_has_no_bot_network_or_write_imports(self):
        tree = ast.parse(Path(__file__).with_name('early_entry_contract_20261031.py').read_text())
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(imports, {'copy'})
        self.assertFalse(any(isinstance(n, ast.Import) for n in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
