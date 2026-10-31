"""Isolate actual pinned pure scanner functions; no scanner import or I/O calls.

The values are a controlled SOFTWARE fixture, not historical market evidence.
Only the source reader uses local git. No production function with I/O is run.
"""
import ast
import argparse
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest

PIN='8931c4292fb0f65fc2499935890ad315bb677aee'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('mode',nargs='?',choices=('report','before','reference'),default='report')
parser.add_argument('--repo',type=Path,required=True)
parser.add_argument('--output',type=Path)
args=parser.parse_args()
REPO=args.repo.resolve()
SOURCE=subprocess.check_output(['git','-C',str(REPO),'show',PIN+':scanner.py'],text=True)
TREE=ast.parse(SOURCE)
NAMES={'Candidate','dec','score_candidate','anti_fomo_ok','setup_type',
       '_recent_observations','_avg_decimal','trigger_check'}
CONSTANTS={'MIN_QUOTE_VOLUME_TL','MIN_WATCH_IMBALANCE','MIN_ALERT_VOLUME_RATIO',
           'MIN_WATCH_VOLUME_RATIO','MAX_SPREAD_PCT','ALERT_MIN_SCORE',
           'MIN_ALERT_IMBALANCE','ALERT_RSI_LOW','ALERT_RSI_HIGH','MAX_RETURN_3',
           'MAX_RETURN_12','MAX_RETURN_48','MIN_CONFIRMATIONS','MIN_AVG_IMBALANCE'}
nodes=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]
for node in TREE.body:
 if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in NAMES:nodes.append(node)
 elif isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id in CONSTANTS for x in node.targets):nodes.append(node)
namespace=ModuleType('_isolated_pinned_scanner_contract')
namespace.__dict__.update(Decimal=Decimal,InvalidOperation=InvalidOperation,dataclass=dataclass,
                          os=SimpleNamespace(getenv=lambda _key,default:default))
sys.modules[namespace.__name__]=namespace
exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'<pinned-pure-functions>','exec'),namespace.__dict__)
D=Decimal

def scenario():
 old=SimpleNamespace(imbalance_ratio=D('1.35'),spread_percent=D('0.10'),
                     largest_ask_wall_share=D('0.20'),largest_bid_wall_share=D('0.20'),
                     best_bid=D('100'),best_ask=D('100.10'))
 fresh=SimpleNamespace(imbalance_ratio=D('1.12'),spread_percent=D('0.39'),
                       largest_ask_wall_share=D('0.20'),largest_bid_wall_share=D('0.20'),
                       best_bid=D('100'),best_ask=D('100.39'))
 ticker=SimpleNamespace(quote_volume=D('25000000'))
 t15=SimpleNamespace(current_close=D('100'),ema9=D('99.5'),ema21=D('99'),ema50=D('98'),
                     is_uptrend=True,is_above_ema9=True,is_above_ema21=True,
                     volume_ratio=D('1.30'),macd_histogram=D('.1'),macd_line=D('1'),macd_signal=D('0'),
                     rsi14=D('57'),recent_return_3=D('.50'),recent_return_12=D('1'),recent_return_48=D('2'),
                     breakout=False,is_pullback=False,is_bullish_candle=True,mean_touch=False)
 t1=SimpleNamespace(current_close=D('100'),ema21=D('100'),is_uptrend=False,macd_histogram=D('0'))
 t4=SimpleNamespace(current_close=D('98.5'),ema50=D('100'))
 score,reasons=namespace.score_candidate(ticker,old,t15,t1,t4)
 candidate=namespace.Candidate(symbol='TEST_TL',score=score,ticker=ticker,book=old,
                              tech_15=t15,tech_1h=t1,tech_4h=t4,setup='RECOVERY',reasons=reasons,candle_at=1799100)
 now=1800000
 item={'history':[{'time':now-900,'candle_at':now-1800,'imbalance':'1.35','volume_ratio':'1.30'},
                  {'time':now,'candle_at':now-900,'imbalance':'1.35','volume_ratio':'1.30'}]}
 # Exactly the current driver's book refresh expression; score stays inherited.
 refreshed=replace(candidate,book=fresh)
 expected_score,expected_reasons=namespace.score_candidate(ticker,fresh,t15,t1,t4)
 corrected=replace(refreshed,score=expected_score,reasons=expected_reasons)
 before=namespace.trigger_check(refreshed,item,True,'fixture neutral',now)
 after=namespace.trigger_check(corrected,item,True,'fixture neutral',now)
 return {'initial_score':score,'inherited_after_book_refresh':refreshed.score,
         'score_for_actual_refreshed_book':expected_score,
         'current_trigger_accepts':before[0],'current_trigger_reason':before[1],
         'reference_recalculation_trigger_accepts':after[0],
         'reference_recalculation_trigger_reason':after[1]}, refreshed, corrected

def explosive_scenario():
 _,base,_=scenario()
 tech=SimpleNamespace(**{**vars(base.tech_15),'volume_ratio':D('1.80'),'breakout':True})
 old=SimpleNamespace(**{**vars(base.book),'imbalance_ratio':D('1.35'),'spread_percent':D('0.10')})
 fresh=SimpleNamespace(**{**vars(base.book),'imbalance_ratio':D('1.25'),'spread_percent':D('0.39')})
 old_score,_=namespace.score_candidate(base.ticker,old,tech,base.tech_1h,base.tech_4h)
 fresh_score,reasons=namespace.score_candidate(base.ticker,fresh,tech,base.tech_1h,base.tech_4h)
 stale=replace(base,score=old_score,book=fresh,tech_15=tech,setup='BREAKOUT')
 corrected=replace(stale,score=fresh_score,reasons=reasons)
 item={'history':[{'time':1800000,'candle_at':1799100,'imbalance':'1.35','volume_ratio':'1.80'}]}
 before=namespace.trigger_check(stale,item,True,'fixture neutral',1800000)
 after=namespace.trigger_check(corrected,item,True,'fixture neutral',1800000)
 return {'inherited_score':old_score,'fresh_book_score':fresh_score,
         'closed_candle_confirmations':1,'current_trigger_accepts':before[0],
         'reference_trigger_accepts':after[0],'reference_reason':after[1]}

class BeforeInvariant(unittest.TestCase):
 def test_refreshed_candidate_score_matches_refreshed_book(self):
  result,_,_=scenario()
  self.assertEqual(result['inherited_after_book_refresh'],result['score_for_actual_refreshed_book'])

class ReferenceInvariant(unittest.TestCase):
 def test_recalculation_enforces_same_score_threshold(self):
  result,_,_=scenario()
  self.assertEqual(result['initial_score'],80)
  self.assertEqual(result['score_for_actual_refreshed_book'],70)
  self.assertTrue(result['current_trigger_accepts'])
  self.assertFalse(result['reference_recalculation_trigger_accepts'])
  self.assertEqual(result['reference_recalculation_trigger_reason'],'score 70 < 80')
 def test_stale_score_does_not_activate_single_confirmation_exception(self):
  result=explosive_scenario()
  self.assertEqual((result['inherited_score'],result['fresh_book_score']),(90,84))
  self.assertTrue(result['current_trigger_accepts'])
  self.assertFalse(result['reference_trigger_accepts'])
  self.assertEqual(result['reference_reason'],'watching: confirmations 1/2')

if __name__=='__main__':
 result,_,_=scenario()
 if args.mode=='before':
  unittest.main(argv=[sys.argv[0],'BeforeInvariant'],verbosity=2)
 elif args.mode=='reference':
  unittest.main(argv=[sys.argv[0],'ReferenceInvariant'],verbosity=2)
 else:
  lines=SOURCE.splitlines()
  refresh_paths=[]
  for i,text in enumerate(lines):
   if 'candidate = replace(candidate, book=get_order_book(candidate.symbol))' not in text:continue
   refresh=i+1
   trigger=next(j+1 for j in range(refresh,min(refresh+40,len(lines))) if '= trigger_check(' in lines[j])
   calls=[j+1 for j in range(refresh,trigger-1) if 'score_candidate(' in lines[j]]
   assert not calls
   refresh_paths.append({'refresh_line':refresh,'next_trigger_line':trigger,'rescore_calls_between':calls})
  assert len(refresh_paths)==2
  packet={'classification':'confirmed_score_book_provenance_mismatch_in_controlled_fixture',
          'source_pin':PIN,'scanner_source_sha256':hashlib.sha256(SOURCE.encode()).hexdigest(),
          'affected_source_paths':refresh_paths,
          'fixture_not_historical_market_trade':True,'result':result,
          'single_confirmation_result':explosive_scenario(),
          'production_modified':False,'proposal_not_applied':'Recompute score and reasons from the refreshed book before repeating trigger gates.',
          'attribution_limit':'This proves a possible threshold inconsistency in code, not that any user recommendation or missed rally was caused by it.'}
  if args.output:
   output=args.output.resolve()
   if output==REPO or REPO in output.parents:
    raise ValueError('Output must be outside the bot checkout')
   output.write_text(json.dumps(packet,indent=2))
  print(json.dumps(packet,indent=2))
