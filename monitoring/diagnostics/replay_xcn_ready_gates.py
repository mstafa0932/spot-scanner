"""Offline forensic gate reconstruction; never invokes scanner/main or sends requests.
Run from the pinned source worktree with credentials removed and SHADOW_MODE=true.
Unknown historical L2 fields stay unknown. A replay score is not a logged decision.
"""
import ast,base64,copy,hashlib,json,subprocess,sys,zlib
from datetime import datetime,timezone
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

def deny_network(event,args):
    if event in {"socket.connect","socket.connect_ex","socket.getaddrinfo","socket.sendto","socket.sendmsg"}:
        raise RuntimeError("offline forensic replay prohibits network")
sys.addaudithook(deny_network)
sys.path.insert(0,str(Path.cwd()))
import scanner as sc
import market_data as md
from indicator_engine import analyze_symbol
from risk_engine import limit_entry_price,price_step
from market_data import Ticker

E=Path(__file__).resolve().parents[1]/"audit_evidence"
rows=json.loads((E/"xcn_decisive_20261018_state_rows.json").read_text())
old=json.loads((E/"xcn_20261018_capture_bundle.json").read_text())
new=json.loads((E/"xcn_decisive_20261018_capture_bundle.json").read_text())
def decode(bundle):
    result=[]
    for c in bundle["captures"]:
        raw=zlib.decompress(base64.b64decode(c["data"]))
        assert hashlib.sha256(raw).hexdigest()==c["sha256"]
        cap=json.loads(raw);cap["frame"]=md._parse_native_candle_payload(cap["payload"]);result.append(cap)
    return result
oldcaps,newcaps=decode(old),decode(new)
newframes={c["params"]["interval"]:c["frame"] for c in newcaps}
overlap=[]
for i,c in enumerate(oldcaps):
    if i==5:continue # Deliberately open 4h context is not an indicator source.
    interval=c["params"]["interval"];a=c["frame"].set_index("timestamp");b=newframes[interval].set_index("timestamp")
    common=sorted(set(a.index)&set(b.index));cols=["open","high","low","close","volume"]
    mismatches=int((~a.loc[common,cols].eq(b.loc[common,cols]).all(axis=1)).sum())
    overlap.append({"old_capture":i,"interval":interval,"common_rows":len(common),"ohlcv_changed_rows":mismatches})
    assert mismatches==0,("provider historical revision",i,mismatches)

def local(ts):return datetime.fromtimestamp(ts,timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Istanbul")).isoformat()
def tri_and(values):
    values=list(values)
    return False if False in values else None if None in values else True
def tri_or(values):
    values=list(values)
    return True if True in values else None if None in values else False
def asof(interval,ts):
    with patch.object(md,"time",SimpleNamespace(time=lambda:ts)):
        f=md.validate_candles(newframes[interval],interval)
    f=f.tail(250).reset_index(drop=True);f.attrs.update(source="PARIBU",provider="Paribu",resolution=interval)
    seconds=md._interval_config(interval)[1]
    assert len(f)==250 and (f.timestamp+seconds<=ts).all()
    expected=(ts//seconds-1)*seconds
    if int(f.timestamp.iloc[-1])!=expected:
        raise RuntimeError(f'Fresh original evidence required for {interval} at {ts}; capture ends before expected closed bar {expected}')
    tech=analyze_symbol(f);assert tech
    return f,tech
out=[];match_count=0
for r in rows:
    ts=int(datetime.fromisoformat(r["decision_at_utc"].replace("Z","+00:00")).timestamp()) if r["decision_at_utc"] else r["scan_start"]
    frames={};tech={}
    for interval in ("15m","1h","4h"):frames[interval],tech[interval]=asof(interval,ts)
    t,h,q=tech["15m"],tech["1h"],tech["4h"]
    logged=r["decision"];b=r["book"]
    score=logged.get("score");score_basis="logged" if score is not None else "not executed"
    score_replay=None
    if "spread_pct" in b and "imbalance" in b:
        book=SimpleNamespace(spread_percent=D(b["spread_pct"]),imbalance_ratio=D(b["imbalance"]))
        ticker=Ticker("XCN_TL",D(r["ticker"]["last"]),quote_volume=D(r["ticker"]["quote_volume"]))
        score_replay,_=sc.score_candidate(ticker,book,t,h,q)
        if score is not None:assert score_replay==score
        else:score=score_replay;score_basis="offline deterministic replay from recorded book aggregates"
    if r["technical_slot"]:
        for key,val in (("rsi",t.rsi14),("volume_ratio",t.volume_ratio),("return_3",t.recent_return_3),("atr_pct",t.atr14/t.current_close*100)):
            assert str(val)==str(logged[key]),(r["run_id"],key,str(val),logged[key])
        for interval in frames:
            assert int(frames[interval].timestamp.iloc[-1])==int(r["source"][interval]["latest_open"])
        match_count+=1
    setup_ok,setup=sc.setup_type(t);fomo,fomo_reason=sc.anti_fomo_ok(t)
    watch=r["watch_before"] or {};obs=sc._recent_observations(watch,ts)
    avg_i=sc._avg_decimal(obs,"imbalance");avg_v=sc._avg_decimal(obs,"volume_ratio")
    explosive=tri_and([score>=88 if score is not None else None,setup=="BREAKOUT",D(b["imbalance"])>=D("1.25") if "imbalance" in b else None,t.volume_ratio>=D("1.80"),t.recent_return_3<D("2.80")])
    risk_precheck=None;risk_details=None
    if b.get("best_ask") and b.get("spread_pct") and setup_ok:
        ask=D(b["best_ask"]);spread=D(b["spread_pct"]);bid=ask/(1+spread/100)
        book=SimpleNamespace(best_bid=bid,best_ask=ask,spread_percent=spread)
        entry=limit_entry_price(book,t,setup)
        if entry is not None:
            atr_stop=entry-t.atr14*D("1.75")
            cushion=max(spread/100*D("1.50"),D("0.0010"))
            structure=t.swing_low*(1-cushion) if 0<t.swing_low<entry else None
            stop=min(atr_stop,structure) if structure else atr_stop
            risk_pct=(entry-stop)/entry*100
            risk_details={"entry_reference":str(entry),"stop_reference":str(stop),"risk_pct":str(risk_pct),"wall_levels_missing":True,"method":"first risk stages only; no claim of a complete executable plan"}
            if stop<=0 or stop>=entry or not 0<risk_pct<=D("4.00"):risk_precheck=False
    source_gate=True if r["technical_slot"] else None
    gates={
        "original_closed_data_at_runtime":source_gate,
        "liquidity_24h_gte_5m":D(r["ticker"]["quote_volume"])>=sc.MIN_QUOTE_VOLUME_TL,
        "btc":r["btc_ok"],
        "score_gte_80":score>=sc.ALERT_MIN_SCORE if score is not None else None,
        "first_book_spread_lte_0_40":D(b["spread_pct"])<=sc.MAX_SPREAD_PCT if "spread_pct" in b else True if r["technical_slot"] else None,
        "first_book_imbalance_gte_1_12":D(b["imbalance"])>=sc.MIN_ALERT_IMBALANCE if "imbalance" in b else None,
        "dominant_sell_wall_absent":None,
        "ATR_discovery_0_20_to_5_00":sc.MIN_ATR_PCT<=t.atr14/t.current_close*100<=sc.MAX_ATR_PCT,
        "volume_ratio_gte_1_30":t.volume_ratio>=sc.MIN_ALERT_VOLUME_RATIO,
        "RSI_49_to_68":sc.ALERT_RSI_LOW<=t.rsi14<=sc.ALERT_RSI_HIGH,
        "pullback_extra_gates":(t.rsi14<=60 and t.mean_touch) if setup=="PULLBACK" else True,
        "anti_FOMO":fomo,
        "trigger_setup":setup_ok,
        "1h_close_gte_0_995_EMA21":h.current_close>=h.ema21*D("0.995"),
        "4h_close_gte_0_98_EMA50":q.current_close>=q.ema50*D("0.98"),
        "confirmations_2_or_explosive":tri_or([len(obs)>=sc.MIN_CONFIRMATIONS,explosive]),
        "average_imbalance_or_explosive":tri_or([avg_i>=sc.MIN_AVG_IMBALANCE,explosive]),
        "persistent_volume_or_explosive":tri_or([avg_v>=D("1.15") or t.volume_ratio>=D("1.60"),explosive]),
        "global_delivery_limit":sc._global_alert_allowed(copy.deepcopy(r["delivery_before"]),ts,True),
        "symbol_delivery_cooldown":sc._symbol_alert_allowed(r["delivery_before"],"XCN_TL",ts,True),
        "complete_risk_plan":risk_precheck,
        "fresh_final_orderbook":None,
        "shadow_mode":r["shadow"],
    }
    ready=tri_and(gates.values())
    data={interval:{"last_open":int(frames[interval].timestamp.iloc[-1]),"last_close":int(frames[interval].timestamp.iloc[-1])+md._interval_config(interval)[1],"current_close":str(tech[interval].current_close),"ema21":str(tech[interval].ema21),"ema50":str(tech[interval].ema50),"rsi":str(tech[interval].rsi14),"volume_ratio":str(tech[interval].volume_ratio),"provider_zero_volume_in_250":int(frames[interval].volume.eq(0).sum())} for interval in frames}
    out.append({"run_id":r["run_id"],"time_utc":datetime.fromtimestamp(ts,timezone.utc).isoformat(),"time_local":local(ts),"actual_decision_time_recorded":r["decision_at_utc"] is not None,"data":data,"atr_pct":str(t.atr14/t.current_close*100),"returns_3_12_48":list(map(str,(t.recent_return_3,t.recent_return_12,t.recent_return_48))),"setup":setup,"anti_fomo_reason":fomo_reason,"score":score,"score_basis":score_basis,"score_replay":score_replay,"confirmed_observations":len(obs),"avg_imbalance":str(avg_i),"avg_volume":str(avg_v),"explosive":explosive,"risk_precheck":risk_details,"gates":gates,"READY":ready,"runtime_first_failure":logged["reason"],"failed_gates":[k for k,v in gates.items() if v is False],"unknown_gates":[k for k,v in gates.items() if v is None],"replay_limit":"Historical publication instant is not independently recorded on scans that did not fetch XCN candles. Full historical L2/walls and final refreshed book are absent; null never means passed."})

functions=["trigger_check","discovery_ok","score_candidate","anti_fomo_ok","setup_type","btc_gate","_hot_radar_order","_recent_observations","_global_alert_allowed","_symbol_alert_allowed"]
by_blob={}
for row in rows:
    if row["scanner_blob"] in by_blob:continue
    source=subprocess.check_output(["git","show",row["checkout_sha"]+":scanner.py"]).decode()
    tree=ast.parse(source);nodes={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    by_blob[row["scanner_blob"]]={name:hashlib.sha256(ast.dump(nodes[name],include_attributes=False).encode()).hexdigest() for name in functions}
assert len({json.dumps(v,sort_keys=True) for v in by_blob.values()})==1
peak=newframes["15m"].loc[newframes["15m"].high.eq(0.2242),["timestamp","open","high","low","close","volume"]].to_dict("records")
metadata={"rows":len(out),"logged_technical_rows_matched_exactly":match_count,"capture_integrity_checks":len(oldcaps)+len(newcaps),"historical_overlap":overlap,"gate_function_hashes_by_scanner_blob":by_blob,"all_4h_ready_gates_false":all(r["gates"]["4h_close_gte_0_98_EMA50"] is False for r in out),"all_ready_results_false":all(r["READY"] is False for r in out),"peak_0_2242_matching_original_15m_rows":peak,"max_available_15m_close":int(newframes["15m"].timestamp.max())+900,"no_real_orders_or_messages":True,"network_disabled":True,"SHADOW_MODE":True}
(E/"xcn_decisive_20261018_gate_matrix.json").write_text(json.dumps({"metadata":metadata,"rows":out},ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"metadata":metadata,"compact":[{"run":r["run_id"],"time":r["time_local"],"rsi":r["data"]["15m"]["rsi"],"score":r["score"],"score_basis":r["score_basis"],"volume_ratio":r["data"]["15m"]["volume_ratio"],"setup":r["setup"],"failures":r["failed_gates"],"unknown":r["unknown_gates"]} for r in out]},ensure_ascii=False))
