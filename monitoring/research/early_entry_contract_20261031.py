"""Offline transition contract, NOT a detector, READY signal, or alert sender.

It tests the proposed separation of preparation from unchanged entry gates.
Precursor calculation must come from separately validated native candle inputs.
An input saying PASS is evidence to audit, not proof of a future price move.
No scanner imports, network, filesystem writes, or execution capabilities.
"""
from copy import deepcopy

VERSION = 'early_entry_contract_v1_20261031'
REQUIRED_GATES = (
    'symbol_identity', 'native_candles', 'liquidity', 'spread', 'book_imbalance',
    'btc', 'atr', 'rsi', 'anti_fomo', 'setup', 'higher_timeframe', 'score',
    'confirmations', 'fresh_book', 'risk_plan', 'alert_limits',
    'budget_depth_evidence',
)
EXECUTION_GATES = {
    'liquidity', 'spread', 'book_imbalance', 'fresh_book', 'risk_plan',
    'budget_depth_evidence',
}
IDENTITY_GATES = {'symbol_identity', 'native_candles'}
BOOK_GATES = {'spread', 'book_imbalance', 'fresh_book', 'score', 'risk_plan', 'budget_depth_evidence'}
TTL_SECONDS = 8 * 3600  # Existing watch-window default; not a production change.
MAX_PRECURSOR_AGE = 1200  # Existing research closed-candle freshness bound.
MAX_ENTRY_BOOK_AGE = 30  # Proposed evidence contract, not a live setting.


def integer(value):
    return type(value) is int and value >= 0


def advance(previous, observation, now):
    """Return (research_state, audit_decision), preserving fixed preparation TTL.

    Native-candle precursor and gate evaluation are supplied, not inferred from
    photos, score, later quotes, or a 24-hour turnover change. All entry gates
    stay required. This does not implement an alternative weak-4h entry route.
    """
    state = deepcopy(previous) if isinstance(previous, dict) else {}
    verdict = {'version': VERSION, 'mode': 'offline_contract_only',
               'is_ready': False, 'is_trade': False, 'notify': False,
               'stage': 'DATA_INVALID', 'reasons': []}
    if not integer(now) or not isinstance(observation, dict):
        verdict['reasons'] = ['invalid_observation_or_clock']
        return state, verdict
    symbol = observation.get('symbol')
    if not isinstance(symbol, str) or not symbol:
        verdict['reasons'] = ['missing_symbol']
        return state, verdict
    if state and (state.get('symbol') != symbol or not integer(state.get('last_observed_at'))
                  or state['last_observed_at'] > now):
        verdict['reasons'] = ['different_symbol_or_out_of_order_observation']
        return state, verdict
    if state.get('first_prepared_at') is not None:
        first = state['first_prepared_at']
        if not integer(first) or first > now or state.get('expires_at') != first + TTL_SECONDS:
            verdict['reasons'] = ['invalid_preparation_clock']
            return state, verdict
    elif state.get('expires_at') is not None:
        verdict['reasons'] = ['expiry_without_preparation']
        return state, verdict
    if any(state.get(k) is not None and not integer(state[k]) for k in
           ('last_qualifying_candle', 'last_reviewed_candle', 'first_reviewable_at')):
        verdict['reasons'] = ['invalid_saved_candle_clock']
        return state, verdict
    state.update(symbol=symbol, last_observed_at=now)
    if state.get('expires_at') is not None and now >= state['expires_at']:
        verdict.update(stage='EXPIRED', reasons=['fixed_preparation_window_expired'])
        return state, verdict

    precursor = observation.get('precursor')
    if not isinstance(precursor, dict):
        verdict.update(stage='WAIT_DATA' if state.get('first_prepared_at') is not None else 'MONITOR',
                       reasons=['native_precursor_not_available'])
        return state, verdict
    opened, available = precursor.get('closed_candle_at'), precursor.get('available_at')
    if (precursor.get('source') != 'PARIBU' or precursor.get('authentic_only') is not True
            or type(precursor.get('candidate')) is not bool
            or not integer(opened) or opened % 900 != 0
            or not integer(available) or not opened + 900 <= available <= now
            or now - (opened + 900) > MAX_PRECURSOR_AGE
            or not isinstance(precursor.get('source_ref'), str) or not precursor['source_ref']):
        verdict['reasons'] = ['unverified_open_future_stale_or_malformed_precursor']
        return state, verdict
    if not precursor['candidate']:
        verdict.update(stage='MONITOR', reasons=['precursor_not_positive'])
        return state, verdict
    if state.get('first_prepared_at') is None:
        state.update(first_prepared_at=now, expires_at=now + TTL_SECONDS)
    last_candle = state.get('last_qualifying_candle')
    if last_candle is not None and opened < last_candle:
        verdict['reasons'] = ['older_precursor_candle']
        return state, verdict
    state['last_qualifying_candle'] = opened

    gates = observation.get('gates')
    gates = gates if isinstance(gates, dict) else {}
    statuses = {}
    for gate in REQUIRED_GATES:
        evidence = gates.get(gate)
        status = 'UNKNOWN'
        if isinstance(evidence, dict):
            at, expires = evidence.get('available_at'), evidence.get('expires_at')
            if (integer(at) and at <= now and integer(expires) and at <= now < expires
                    and isinstance(evidence.get('source_ref'), str) and evidence['source_ref']
                    and isinstance(evidence.get('status'), str)
                    and evidence['status'] in {'PASS', 'FAIL', 'UNKNOWN'}):
                status = evidence['status']
        statuses[gate] = status
    fresh = gates.get('fresh_book')
    if statuses['fresh_book'] == 'PASS' and now - fresh['available_at'] > MAX_ENTRY_BOOK_AGE:
        statuses['fresh_book'] = 'UNKNOWN'
    for gate in BOOK_GATES - {'fresh_book'}:
        if statuses[gate] == 'PASS' and (statuses['fresh_book'] != 'PASS'
                or gates[gate].get('book_ref') != fresh['source_ref']):
            statuses[gate] = 'UNKNOWN'
    failed = [g for g in REQUIRED_GATES if statuses[g] == 'FAIL']
    unknown = [g for g in REQUIRED_GATES if statuses[g] == 'UNKNOWN']
    verdict.update(gates=statuses, reasons=[g + ':FAIL' for g in failed]
                                         + [g + ':UNKNOWN' for g in unknown])
    if any(g in IDENTITY_GATES for g in failed):
        verdict['stage'] = 'WAIT_DATA'
    elif any(g in EXECUTION_GATES for g in failed):
        verdict['stage'] = 'WAIT_EXECUTION'
    elif failed:
        verdict['stage'] = 'WAIT_STRATEGY'
    elif unknown:
        verdict['stage'] = 'WAIT_DATA'
    elif state.get('last_reviewed_candle') == opened:
        verdict.update(stage='ALREADY_REVIEWED', reasons=['same_closed_candle'])
    else:
        state['last_reviewed_candle'] = opened
        state.setdefault('first_reviewable_at', now)
        verdict.update(stage='ENTRY_REVIEWABLE', hypothetical_all_gates_known_pass=True,
                       reasons=['all_supplied_entry_evidence_passed'])
    return state, verdict
