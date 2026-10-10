"""Replay a supplied causal signal ledger; never synthesizes strategy evidence.
CSV: Date,Open,High,Low,Close,Volume. JSON: a list of signals with index,
direction, atr, sl_mult, information_end (YYYY-MM-DD), rule_id.
NSE delivery fee model is an estimate, not broker verification. For BSE/product-
specific fees use the simulator API with an explicit verified per-fill callback.
"""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pandas as pd
from research.costs import CostConfig
from research.provenance import pipeline_fingerprint
from scaleout_validation import simulate

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv',type=Path,required=True);p.add_argument('--signals',type=Path,required=True)
    p.add_argument('--horizon',type=int,default=20);p.add_argument('--notional',type=float,default=100000)
    p.add_argument('--exchange',choices=['NSE'],required=True)
    p.add_argument('--instrument',choices=['cash_delivery','research_borrowed_cash'],default='cash_delivery')
    p.add_argument('--borrow-bps-per-day',type=float);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();frame=pd.read_csv(a.csv,index_col=0,parse_dates=True,float_precision='round_trip')
    signals=json.loads(a.signals.read_text());cost=CostConfig()
    result=simulate(frame,signals,fee=cost.cost,horizon=a.horizon,notional=a.notional,
                    instrument=a.instrument,borrow_bps_per_day=a.borrow_bps_per_day)
    result.update(input_sha256=hashlib.sha256(a.csv.read_bytes()).hexdigest(),signals_sha256=hashlib.sha256(a.signals.read_bytes()).hexdigest(),pipeline_fingerprint=pipeline_fingerprint(),exchange=a.exchange,fee_parameters=cost.as_dict(),fee_scope='NSE estimated delivery per-fill fees and slippage; not a verified broker bill')
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2,allow_nan=False));print(json.dumps({k:v for k,v in result.items() if k not in ('trades','excluded')},indent=2))
if __name__=='__main__':main()
