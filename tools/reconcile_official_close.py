"""Compare raw TradingView daily Close with primary UDiFF ClsPric, never LastPric.
Run: python tools/reconcile_official_close.py --date 2026-10-09
This verifies specified end-of-day observations only, not live quotes/adjusted history.
"""
import argparse, hashlib, io, json, math, os, sys, zipfile
from datetime import date
from pathlib import Path
import pandas as pd
import requests
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def official_url(exchange,session):
 d=date.fromisoformat(session).strftime('%Y%m%d')
 if exchange=='NSE':return f'https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip'
 if exchange=='BSE':return f'https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d}_F_0000.CSV'
 raise ValueError('invalid exchange')

def parse_official(raw,exchange,session):
 if raw[:2]==b'PK':
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   entries=[i for i in z.infolist() if i.filename.lower().endswith('.csv')]
   if len(entries)!=1 or entries[0].file_size>30_000_000:raise ValueError('unexpected archive')
   raw=z.read(entries[0])
 df=pd.read_csv(io.BytesIO(raw),dtype={'ISIN':str,'TckrSymb':str,'FinInstrmId':str})
 required={'TradDt','Src','ISIN','TckrSymb','SctySrs','FinInstrmId','ClsPric'}
 if not required <= set(df.columns):raise ValueError('UDiFF schema absent (possibly HTML error)')
 if set(df.TradDt.astype(str))!={session}:raise ValueError('wrong official session')
 if set(df.Src.astype(str).str.upper())!={exchange}:raise ValueError('wrong official exchange')
 return df

def pick_official(df,symbol,exchange):
 rows=df[df.TckrSymb.eq(symbol)]
 if exchange=='NSE':rows=rows[rows.SctySrs.eq('EQ')]
 if len(rows)!=1:raise ValueError(f'{symbol}: ambiguous or missing official cash row')
 r=rows.iloc[0];v=float(r.ClsPric)
 if not math.isfinite(v) or v<=0 or not isinstance(r.ISIN,str) or not r.ISIN.startswith('IN'):raise ValueError('invalid price/ISIN')
 return {'symbol':symbol,'exchange':exchange,'session':str(r.TradDt),'isin':r.ISIN,'instrument_id':str(r.FinInstrmId),'official_close':v,'official_field':'ClsPric'}

def compare_close(row,frame,actual_exchange):
 result=dict(row)
 if frame is None or actual_exchange!=row['exchange']:
  return {**result,'status':'UNVERIFIED','reason':'missing requested-exchange history'}
 matching=frame[[str(t)[:10]==row['session'] for t in frame.index]]
 if len(matching)!=1:return {**result,'status':'UNVERIFIED','reason':'requested session missing/ambiguous'}
 v=float(matching.iloc[0]['Close'])
 if not math.isfinite(v) or v<=0:return {**result,'status':'UNVERIFIED','reason':'invalid vendor close'}
 delta=v-row['official_close']
 return {**result,'vendor':'TradingView','vendor_close':v,'difference':round(delta,8),'status':'MATCH' if abs(delta)<=.005 else 'MISMATCH'}

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--date',required=True);ap.add_argument('--symbols',default='TCS,JIOFIN,EMIL,CANBK,LGEINDIA');ap.add_argument('--output',type=Path,default=ROOT/'verification_evidence/official_close_reconciliation.json');args=ap.parse_args()
 os.environ.setdefault('STOCKAI_OFFLINE','1');os.environ.setdefault('STOCKAI_SCAN_AUTOREFRESH','0')
 import app as A
 sources=[];rows=[]
 for ex in ('NSE','BSE'):
  url=official_url(ex,args.date)
  try:
   response=requests.get(url,headers={'User-Agent':'Mozilla/5.0'},timeout=20);response.raise_for_status()
   if len(response.content)>30_000_000:raise ValueError('response too large')
   df=parse_official(response.content,ex,args.date)
   sources.append({'exchange':ex,'url':url,'sha256':hashlib.sha256(response.content).hexdigest(),'http_status':response.status_code})
   for sym in args.symbols.upper().split(','):
    sym=sym.strip()
    try:
     row=pick_official(df,sym,ex);frame,actual=A.DATA_MANAGER.fetch_tradingview(sym,n_bars=100,interval_str='1d',prefer_exch=ex)
     rows.append(compare_close(row,frame,actual))
    except Exception as e:rows.append({'exchange':ex,'symbol':sym,'status':'UNVERIFIED','reason':str(e)})
  except Exception as e:sources.append({'exchange':ex,'url':url,'error':str(e)})
 for symbol in {r.get('symbol') for r in rows}:
  group=[r for r in rows if r.get('symbol')==symbol and r.get('isin')]
  if len({r['isin'] for r in group})>1:
   for r in group:r.update(status='UNVERIFIED',reason='cross-exchange ISIN disagreement')
 report={'session':args.date,'scope':'Specified raw daily closes only; not certified live ticks or adjusted history','sources':sources,'rows':rows}
 args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2))
 print(json.dumps(report,indent=2))
 return 0 if len(rows)==2*len(args.symbols.split(',')) and all(r['status']=='MATCH' for r in rows) else 1
if __name__=='__main__':sys.exit(main())
