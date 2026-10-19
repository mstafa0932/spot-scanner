import ast,copy,hashlib,json,subprocess,sys
from pathlib import Path
from decimal import Decimal as D
def deny(e,a):
 if e in {"socket.connect","socket.getaddrinfo","socket.sendto"}:raise RuntimeError("offline scheduling only")
sys.addaudithook(deny)
sys.path.insert(0,str(Path.cwd()))
import scanner as sc
from market_data import Ticker
from coverage_scheduler import CoverageCycle
E=Path(__file__).resolve().parents[1]/"audit_evidence"
rows=json.loads((E/"xcn_decisive_20261018_state_rows.json").read_text())
def state(sha):return json.loads(subprocess.check_output(["git","show",sha+":scanner_state.json"]))
out=[]
for r in rows:
 pre=state(r["checkout_sha"]);nxt=state(r["state_commit"]);now=r["scan_start"];actual=next(d for d in nxt["scan_diagnostics"] if str(d["run_id"])==str(r["run_id"]))
 sc._prune_watchlist(pre,now);sc._prune_early_watch_followups(pre,now);sc._prune_execution_followups(pre,now);sc._expire_candidate_lifecycles(pre,now)
 tickers=[Ticker(symbol=k,last=D(v["last"]),quote_volume=D(v["quote_volume"]) if v.get("quote_volume") else None) for k,v in nxt["ticker_radar"]["markets"].items()]
 cov=CoverageCycle(pre["coverage_scheduler"],[t.symbol for t in tickers])
 base=cov.order(tickers,"orderbooks",lambda t:t.symbol)
 def eligible(t):return t.symbol not in {"USDT_TL","USDC_TL","BTC_TL"} and t.quote_volume is not None and t.quote_volume>=sc.MIN_QUOTE_VOLUME_TL
 due=[];exe=[];latest=(now//900-1)*900
 for t in base:
  if not eligible(t):continue
  f=pre["early_watch_followups"].get(t.symbol,{})
  if 0<=now-f.get("last_seen",0)<=sc.WATCHLIST_TTL_SECONDS and 0<f.get("last_candle_at",0)<latest:due.append(t.symbol)
  g=pre["execution_followups"].get(t.symbol,{})
  start=g.get("first_seen") or g.get("last_seen",0)
  if start>0 and g.get("last_seen",0)>0 and 0<=now-start<=sc.EXECUTION_FOLLOWUP_TTL_SECONDS:exe.append(t.symbol)
 ordered,meta=sc._hot_radar_order(tickers,pre,cov,now)
 assert meta["selected_priority_symbols"]==actual["hot_radar"]["selected_priority_symbols"],r["run_id"]
 selected_order=[t.symbol for t in ordered if eligible(t)]
 rank=selected_order.index("XCN_TL")+1
 assert (rank<=sc.MAX_ORDERBOOK_MARKETS)==r["book_slot"],r["run_id"]
 f=pre["early_watch_followups"].get("XCN_TL");g=pre["execution_followups"].get("XCN_TL")
 out.append({"run_id":r["run_id"],"priority_list_exactly_reproduced":True,"book_rank":rank,"book_budget":sc.MAX_ORDERBOOK_MARKETS,"due_early_watch_rank":due.index("XCN_TL")+1 if "XCN_TL" in due else None,"due_early_watch_count":len(due),"early_priority_quota":sc.MAX_ORDERBOOK_MARKETS//8,"execution_priority_rank":exe.index("XCN_TL")+1 if "XCN_TL" in exe else None,"execution_priority_quota":sc.MAX_ORDERBOOK_MARKETS//8,"early_reserved":"XCN_TL" in meta["early_watch_symbols"],"execution_reserved":"XCN_TL" in meta["execution_followup_symbols"],"hot":"XCN_TL" in meta["hot_symbols"],"selected_priority":"XCN_TL" in meta["selected_priority_symbols"],"strategy_followup_present":f is not None,"strategy_last_seen":(f or {}).get("last_seen"),"strategy_expires_at":f["last_seen"]+sc.WATCHLIST_TTL_SECONDS if f else None,"execution_first_seen":(g or {}).get("first_seen"),"execution_expires_at":g["first_seen"]+sc.EXECUTION_FOLLOWUP_TTL_SECONDS if g else None})
(E/"xcn_decisive_20261018_scheduling.json").write_text(json.dumps(out,indent=2)+"\n")
print(json.dumps({"rows":len(out),"exact_match_count":len(out),"skipped":[x for x in out if x["book_rank"]>x["book_budget"]],"strategy_ttl_values_after_signal":sorted({x["strategy_expires_at"] for x in out if x["strategy_expires_at"]}),"execution_episodes":sorted({x["execution_first_seen"] for x in out if x["execution_first_seen"]})}))
