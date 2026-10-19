from pathlib import Path
import json,re,subprocess,hashlib,ast
E=Path(__file__).resolve().parents[1]/"audit_evidence"
runs=json.loads((E/"xcn_decisive_20261018_runs.json").read_text())
cache_path=E/"xcn_decisive_20261018_state_rows.json"
out=json.loads(cache_path.read_text()) if cache_path.exists() else []
known={x["run_id"] for x in out}
def show(sha,path):
 return subprocess.check_output(["git","show",sha+":"+path])
def rev(x):
 return subprocess.check_output(["git","rev-parse",x]).decode().strip()
for item in runs:
 rid=item["run"]["id"];lines=item["evidence_lines"]
 if rid in known:continue
 # Match the persisted commit marker; literal brackets.
 short=next(l.split("[main ",1)[1].split("]",1)[0] for l in lines if "[main " in l)
 sha=rev(short);state_bytes=show(sha,"scanner_state.json");s=json.loads(state_bytes);pre=rev(sha+"^");before=json.loads(show(pre,"scanner_state.json"))
 d=next(d for d in s["scan_diagnostics"] if str(d.get("run_id"))==str(rid));case=d["symbols"].get("XCN_TL")
 f=s["early_watch_followups"].get("XCN_TL");ef=s["execution_followups"].get("XCN_TL");entry=next((z for z in reversed((ef or {}).get("history",[])) if z.get("at")==d["started_at"]),None)
 nm=next((z for z in s.get("near_misses",[]) if z.get("symbol")=="XCN_TL" and d["started_at"]<=z.get("observed_at",0)<=d["finished_at"]),None)
 book={}
 if entry:book={k:entry[k] for k in ("spread_pct","imbalance","best_ask") if k in entry};book["basis"]="current execution-followup record"
 elif nm:book={k:nm[k] for k in ("spread_pct","imbalance","reference_price") if k in nm};book["best_ask"]=book.pop("reference_price",None);book["basis"]="current near-miss record"
 if case:
  for k in ("spread_pct","imbalance"):
   if k in case:book[k]=case[k]
 observed=[l for l in lines if "Scan decision | XCN_TL" in l]
 started=d["started_at"];decision_iso=observed[-1].split("Z ",1)[0]+"Z" if observed else None
 source={}
 for l in lines:
  # Use direct token parsing for source diagnostics.
  if "PARIBU_CANDLE_SOURCE market=xcn_tl " in l:
   tokens=dict(t.split("=",1) for t in l.split() if "=" in t)
   source[tokens["interval"]]={k:tokens.get(k) for k in ("endpoint","closed","published_zero_volume","local_synthetic","latest_open")}
 radar=d.get("hot_radar",{})
 life=s["candidate_lifecycle"].get("XCN_TL")
 itemout={"run_id":rid,"run_url":item["run"].get("html_url"),"job_id":item["job"]["id"],"recorded_sha":d.get("code_sha"),"checkout_sha":pre,"metadata_sha":item["run"]["head_sha"],"metadata_matches_checkout":pre==item["run"]["head_sha"],"scanner_blob":rev(pre+":scanner.py"),"state_commit":sha,"state_json_bytes":len(state_bytes),"scan_start":started,"scan_finish":d["finished_at"],"decision_at_utc":decision_iso,"decision":case,"ticker":s["ticker_radar"]["markets"].get("XCN_TL"),"book":book,"source":source,"btc_ok":d["btc_ok"],"btc_reason":d["btc_reason"],"shadow":d["shadow_mode"],"status":d["status"],"alert_sent":d.get("alert_sent"),"early_followup_before":before["early_watch_followups"].get("XCN_TL"),"early_followup_after":f,"execution_before":before["execution_followups"].get("XCN_TL"),"execution_after":ef,"watch_before":before["watchlist"].get("XCN_TL"),"watch_after":s["watchlist"].get("XCN_TL"),"lifecycle":life,"hot":"XCN_TL" in radar.get("hot_symbols",[]),"priority_selected":"XCN_TL" in radar.get("selected_priority_symbols",[]),"technical_priority":"XCN_TL" in radar.get("technical_priority_symbols",[]),"book_slot":s["coverage_scheduler"]["orderbooks"].get("XCN_TL")==s["coverage_scheduler"]["generation"],"technical_slot":s["coverage_scheduler"]["technicals"].get("XCN_TL")==s["coverage_scheduler"]["generation"],"coverage_generation":s["coverage_scheduler"]["generation"],"delivery_before":{k:before.get(k) for k in ("shadow_last_alert_at","shadow_daily_alerts","shadow_sent_signals","shadow_paper_signals")},"steps":item["job"]["steps"]}
 out.append(itemout)
dest=E/"xcn_decisive_20261018_state_rows.json"
dest.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"rows":len(out),"path":str(dest),"mismatches":[x["run_id"] for x in out if not x["metadata_matches_checkout"]],"values":[{"run":x["run_id"],"decision":x["decision_at_utc"],"ticker":x["ticker"],"book":x["book"],"btc":x["btc_ok"],"followup":bool(x["early_followup_after"]),"book_slot":x["book_slot"],"tech_slot":x["technical_slot"],"reason":x["decision"]["reason"]} for x in out]},ensure_ascii=False))
