"""Correct audit labels for existing confirmation rules; no strategy changes.

ONDO run39605507651 sent confirmations=1 under the existing explosive rule,
but persisted confirmation_2_of_2. Quotes below are offline test fixtures,
not authentic Paribu history or evidence of another recommendation.
"""
from datetime import datetime, timezone

import pytest

import scanner
import test_post_radar_recovery as harness


@pytest.mark.parametrize('explosive', [True, False])
def test_ready_lifecycle_records_actual_confirmation_path(monkeypatch,tmp_path,explosive):
    clock, calls, scan = harness.pipeline(monkeypatch,tmp_path)
    clock['rsi']=harness.D('60')
    monkeypatch.setattr(scanner,'score_candidate',lambda *args:(89,[]))
    analyze=scanner.analyze_symbol
    def indicators(frame):
        tech=analyze(frame)
        if frame.attrs['symbol']==harness.SYMBOL and explosive:
            tech.breakout=True
            tech.volume_ratio=harness.D('6.441744194564501')
            tech.rsi14=harness.D('67.28366234795126')
            tech.recent_return_3=harness.D('0.705778561976179973533303900')
        return tech
    monkeypatch.setattr(scanner,'analyze_symbol',indicators)
    messages=[]
    monkeypatch.setattr(scanner,'TELEGRAM_READY_ALERTS',True)
    monkeypatch.setattr(scanner,'send_shadow_ready_alert',lambda opp:messages.append(opp.confirmations) or True)
    first=scan()
    if explosive:
        result=first
        assert messages==[1]
    else:
        assert not first['active_signals'] and messages==[]
        assert first['candidate_lifecycle'][harness.SYMBOL]['current_state']=='confirmation_1_of_2'
        clock['now']+=900
        result=scan()
        assert messages==[2]
    count=1 if explosive else 2
    expected='confirmation_explosive' if explosive else 'confirmation_2_of_2'
    episode=result['candidate_lifecycle'][harness.SYMBOL]
    confirmed=next(e for e in episode['history'] if e.get('reason')=='READY')
    assert confirmed['state']==expected
    assert confirmed['confirmations']==count
    assert confirmed['confirmation_target']==scanner.MIN_CONFIRMATIONS
    assert confirmed['confirmation_mode']==('explosive_breakout' if explosive else 'closed_candle_persistence')
    assert len(result['watchlist'][harness.SYMBOL]['history'])==count
    assert episode['current_state']=='shadow_entry'
    assert result['shadow_sent_signals']=={harness.SYMBOL:clock['now']}
    assert len(result['active_signals'])==1
    assert result['active_signals'][0]['status']=='PENDING_ENTRY'
    # Persist/reload must preserve the corrected audit fields. READY may not repeat.
    repeated=scan()
    assert messages==[count]
    assert len(repeated['active_signals'])==1


def test_explosive_confirmation_audit_episode_keeps_existing_expiry():
    now=harness.NOW
    then=datetime.fromtimestamp(now-scanner.WATCHLIST_TTL_SECONDS-1,timezone.utc).isoformat()
    episode={'candidate_id':'ONDO_TL:fixture','symbol':'ONDO_TL',
             'first_seen_at':then,'current_state':'confirmation_explosive','max_score':89,
             'history':[{'run_id':'fixture','ts':then,'state':'confirmation_explosive','reason':'READY',
                         'confirmations':1,'confirmation_target':2,'confirmation_mode':'explosive_breakout'}]}
    state={'candidate_lifecycle':{'ONDO_TL':episode}}
    assert scanner._expire_candidate_lifecycles(state,now)==['ONDO_TL']
    assert episode['current_state']=='expired'
    assert episode['history'][-1]['reason']=='watch_window_expired'
    assert episode['history'][0]['confirmations']==1
