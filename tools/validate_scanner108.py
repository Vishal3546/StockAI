"""Chronological diagnostic evaluation AFTER frozen rank-band fitting.
Not an untouched independent holdout or production strategy backtest.
Cost scenario is separate: next open -> same-session close, intraday long only,
fixed 100,000 rupee notional, 5bp adverse slippage each side, no stops or portfolio.
"""
import sys,pathlib,json,hashlib
import pandas as pd
from decimal import Decimal as D
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import nifty_scanner as S
from research.data import load
from daily_context import completed,atomic_json
from manual_planner import fees
from safety import frame_digest
ROOT=pathlib.Path(__file__).resolve().parents[1]

def evaluate():
 doc,error=S.load_scanner_bands()
 if doc is None: raise ValueError(error)
 cutoff=pd.Timestamp(doc['fit_end']);rows=[];inputs=[];skipped=[]
 for sym in S.NIFTY_STOCKS:
  raw=load(sym,'5y')
  if raw is None: skipped.append(sym);continue
  raw=completed(raw,'NSE');df=S.calculate_indicators(raw)
  inputs.append(dict(symbol=sym,input_hash=frame_digest(raw),first=str(raw.index[0]),last=str(raw.index[-1]),bars=len(raw)))
  for i in range(200,len(df)-1):
   if df.index[i]<=cutoff:continue
   score,_=S.calculate_ensemble(df.iloc[:i+1]);signal=S.signal_from_bands(score,doc['bands'])
   selected=signal in ('BUY','STRONG BUY','SELL','STRONG SELL')
   predicted=int(signal in ('BUY','STRONG BUY'));truth=int(df.Close.iloc[i+1]>df.Close.iloc[i])
   # All baseline labels were knowable by the signal close, no future majority.
   majority=int((df.Close.iloc[1:i+1].to_numpy()>df.Close.iloc[:i].to_numpy()).mean()>=.5)
   opening=D(str(df.Open.iloc[i+1]));closing=D(str(df.Close.iloc[i+1]));entry=opening*D('1.0005');exit=closing*D('.9995')
   qty=int(D(100000)/entry);buy=fees(entry,qty,'buy','intraday')['total'];sell=fees(exit,qty,'sell','intraday')['total']
   net=(exit-entry)*qty-buy-sell
   rows.append(dict(symbol=sym,signal_date=str(df.index[i].date()),outcome_date=str(df.index[i+1].date()),score=score,signal=signal,selected=selected,
       predicted=predicted,truth=truth,majority=majority,correct=(predicted==truth),baseline_correct=(majority==truth),
       long_selected=signal in ('BUY','STRONG BUY'),scenario_net_rupees=float(net),scenario_notional=100000,fees=float(buy+sell)))
 ledger=pd.DataFrame(rows);sel=ledger[ledger.selected];long=ledger[ledger.long_selected]
 result=dict(scope='Exploratory chronological diagnostic; not independently untouched validation or portfolio returns',
   calibration_end=str(cutoff.date()),first_evaluation_signal=ledger.signal_date.min(),last_outcome=ledger.outcome_date.max(),
   band_sha256=hashlib.sha256((ROOT/'scanner_bands.json').read_bytes()).hexdigest(),n=len(ledger),selected_n=len(sel),coverage_pct=100*len(sel)/len(ledger),
   directional_accuracy_pct=100*sel.correct.mean(),matched_selected_baseline_pct=100*sel.baseline_correct.mean(),
   long_scenario_n=len(long),long_scenario_mean_net_rupees=long.scenario_net_rupees.mean(),all_sessions_scenario_mean_net_rupees=ledger.scenario_net_rupees.mean(),
   long_scenario_positive_pct=100*(long.scenario_net_rupees>0).mean(),
   scenario='Independent fixed-notional round trips, next open to same-session close, NSE intraday, 5bp each side, estimated Angel One fees; no stops or scale-outs; not portfolio total return',
   limitations=['Survivorship: current curated cohort, not historical index membership','Provider bars/corporate actions not independently reconciled','No liquidity/spread/circuit model','Historical data already used in prior project research; not untouched holdout'],inputs=inputs,skipped=skipped)
 folder=ROOT/'verification_evidence';folder.mkdir(exist_ok=True);ledger.to_csv(folder/'scanner108_oos_ledger.csv',index=False);atomic_json(folder/'scanner108_oos_summary.json',result)
 print(json.dumps({k:v for k,v in result.items() if k!='inputs'},indent=2))
if __name__=='__main__':evaluate()
