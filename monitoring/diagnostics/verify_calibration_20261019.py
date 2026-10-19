"""Offline evidence consistency checks. No network, scanner, orders or messages.

This verifies retained facts/arithmetic; missing ALT raw candles/L2 remain unknown.
Run from any checkout of the monitoring branch with Python's standard library.
"""
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime
import argparse
import json
import re


def verify(root: Path, prior: Path):
    def read(name):
        return json.loads((root/'audit_evidence'/name).read_text())
    alt=read('alt_20261019_calibration.json')
    late=read('xcn_20261018_window_completion.json')
    logs=read('calibration_20261019_runtime_extracts.json')
    health=read('calibration_20261019_latest_health.json')
    matrix=json.loads((prior/'xcn_decisive_20261018_gate_matrix.json').read_text())
    checks=[]
    def check(name, value):
        if not value:
            raise AssertionError(name)
        checks.append({'check':name,'passed':True})

    issue=alt['issue']; signal=alt['signal_at_issue']; obs=alt['persisted_watch_observations']
    now=signal['opened_at']; m=alt['ready_assessment']['recorded_values']; risk=alt['risk_consistency']
    check('ALT source SHA is explicitly pinned', issue['source_sha']=='3a8a66216c038bd0ca052dfb55dea7e925046c0b')
    check('Exactly two distinct recorded candle identities, not two same-candle scans',len(obs)==2 and len({o['candle_at'] for o in obs})==2)
    check('Both observations refer to already closed15m bars within120min',all(o['candle_at']+900<=o['time']<=now and now-o['time']<=7200 for o in obs))
    check('Latest bars were already closed at issuance, not the live00:45 bar',all(c['last_close']<=now and c['count']==250 and c['local_synthetic']==0 for c in alt['closed_candles_used']))
    check('Final metrics meet unchanged score/RSI/ATR/volume gates',m['score']>=80 and D('49')<=D(m['RSI_15m'])<=D('68') and D('.20')<=D(m['ATR_pct'])<=D('5') and D(m['volume_ratio'])>=D('1.30'))
    check('Final metrics meet unchanged liquidity/book/BTC gates',D(m['quote_volume_tl'])>=5000000 and D(m['final_spread_pct'])<=D('.40') and D(m['final_imbalance'])>=D('1.12') and m['btc_ok'] is True)
    check('Recorded ask wall is below extreme-wall threshold',D(m['final_ask_wall_share'])<D('.45'))
    check('Two-observation persistence arithmetic meets existing gates',D(alt['confirmations']['avg_imbalance'])>=D('1.06') and D(alt['confirmations']['avg_volume_ratio'])>=D('1.15'))
    e,s,t1,t2=(D(risk[k]) for k in ('entry','stop','tp1','tp2'));r=e-s
    check('Risk/level ordering is valid under4percent maximum',0<s<e<t1<t2 and 0<r/e*100<=4)
    check('TP1 exactly matches existing1.5R floor formula',t1==max(e*D('1.015'),e+r*D('1.5')))
    check('TP2 exactly matches existing2R floor formula',t2==max(e*D('1.023'),e+r*2))
    check('Derived quarter-spread entry rounds to published entry',D(risk['derived_quartile_entry']).quantize(D('.000001'))==e)
    check('Missing raw ALT inputs are explicitly NOT certified as replayed',alt['ready_assessment']['independent_full_numeric_replay'] is False)
    rows=alt['observed_cycles']; by_id={int(x['run_id']):x for x in rows}
    prior_obs=by_id[39169224660]['watch']['history']
    check('00:46 and00:56 same-candle refresh did not add a third confirmation',{o['candle_at'] for o in prior_obs}=={o['candle_at'] for o in obs} and len(prior_obs)==2)
    check('Earlier score72 observation is disclosed as WATCHING, not READY',obs[0]['score']==72 and obs[0]['setup']=='WATCHING')
    emitted=[r['signal'] for r in rows if r['signal']]
    check('No retained SHADOW fill or profit event was invented',all(s['fill_confirmed'] is False and s['fill_estimated'] is False and not any(e['kind'] in {'TP1','TP2','STOP'} for e in s.get('events',[])) for s in emitted))
    ex=alt['execution']; end=ex['entry_deadline']
    check('Entry deadline uses existing90min TTL',end==now+5400 and ex['status']=='ENTRY_EXPIRED')
    boundary={str((end//900)*900):'entry_expiry_boundary_ambiguous'}
    check('Expiry boundary remains marked ambiguous',ex['skipped_tracking_candles']==boundary)
    first=by_id[int(ex['first_expired_state_run'])]
    check('Expiry recognition is a later scan, not an exact02:26 execution claim',first['snapshot_at']>=end and first['snapshot_at']-end<1200)
    sample1=ex['first_retained_sample_at_or_above_tp1'];sample2=ex['first_retained_sample_at_or_above_tp2']
    check('Later price samples exceed targets after the entry window',sample1['snapshot_at']>end and sample2['snapshot_at']>sample1['snapshot_at'] and D(sample1['ticker']['last'])>=t1 and D(sample2['ticker']['last'])>=t2)
    check('Sparse samples do not certify TP-before-STOP trade ordering',ex['tp1_then_tp2_before_stop_proven'] is False)
    check('All19 previously replayed XCN cycles retain failed READY result',len(matrix['rows'])==19 and all(r['READY'] is False for r in matrix['rows']))
    check('Only four saved XCN completion cycles were added',len(late['rows'])==4)
    runtime={int(x['run_id']):x for x in logs['runs']}
    for rid in (39161682650,39162441412):
        row=next(r for r in late['rows'] if int(r['run_id'])==rid)
        dec=row['decisions'][-1]
        original='\n'.join(runtime[rid]['lines'])
        check(f'XCN{rid} loaded NEW native4h19:00–23:00 closed candle',bool(re.search(r'PARIBU_CANDLE_SOURCE market=xcn_tl interval=4h .*closed=250 .*local_synthetic=0 latest_open=1792339200',original)) and 1792339200+14400<=dec['observed_at'])
        check(f'XCN{rid} still failed mandatory BTC/score/volume',row['btc_ok'] is False and dec['score']<80 and D(dec['volume_ratio'])<D('1.30'))
    tail=late['ttl_end']['lifecycle']['history'][-1]
    check('XCN episode ended durably by intended8h TTL, not lost state',late['ttl_end']['early_followup'] is None and tail['reason']=='watch_window_expired' and tail['ts'].startswith('2026-10-19T01:16:01'))
    infra=health['infra'];last=health['run']
    check('Pinned latest production cycle has valid native data and state',last['shadow_mode'] is True and last['status']=='completed' and infra['candles_error']==0 and infra['candles_synthetic']==0 and health['persist_state_step']=='success')
    check('Pinned production executable/settings unchanged since reviewedPR48',set(health['source_diff_names_since_approved'])<= {'scanner_state.json','near_misses_archive.jsonl'})
    check('Generic EARLY WATCH delivery remains zero',infra['funnel']['early_watch_alerts']==0)
    check('Archived run steps include successful scanner and persistence',all(all(next(s for s in run['steps'] if s['name']==name)['conclusion']=='success' for name in ('Run scanner','Persist scanner state')) for run in logs['runs']))
    return {'status':'passed','checks_count':len(checks),'checks':checks,'scope':'Offline evidence consistency only; not production unit tests, full independent ALT numeric replay, a trading backtest or a profitability finding.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--prior-evidence',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=verify(args.root,args.prior_evidence or args.root/'audit_evidence')
    if args.output:
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'checks_count':result['checks_count']}))
