"""FIX-102 identity, deterministic stale selection and timezone regressions."""
import os, sys, pathlib, json, time, unittest
from datetime import datetime
from unittest.mock import patch, Mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.environ.update(STOCKAI_OFFLINE='1',STOCKAI_SCAN_AUTOREFRESH='0')
import pandas as pd
import app as A
from tv_history import HistoryDatafeed
from safety import checked_symbol
A.configure_security(token='',rate_limit_per_min=0)

def frame(end):
 return pd.DataFrame({'Open':100.,'High':102.,'Low':99.,'Close':101.,'Volume':1000.},index=pd.bdate_range(end=end,periods=30))
def packet(method,params):
 body=json.dumps({'m':method,'p':params});return f'~m~{len(body)}~m~'+body

def wire(complete=True,volume=100):
 epoch=pd.Timestamp('2026-10-09 15:00',tz='Asia/Kolkata').timestamp()
 raw=packet('timescale_update',['cs_test',{'s1':{'s':[{'i':0,'v':[epoch,100,102,99,101,volume]}]}}])
 return raw+(packet('series_completed',['cs_test','s1']) if complete else '')

class Isolation(unittest.TestCase):
 def test_stale_requested_history_independent_of_other_exchange(self):
  for other in (None,frame('2026-10-09'),frame('2026-10-08')):
   with self.subTest(other=other is not None),patch.object(A.DATA_MANAGER,'fetch_tradingview',return_value=(other,'NSE')),patch.object(A.DATA_MANAGER,'fetch_yahoo',return_value=(frame('2026-10-08'),'BSE')),patch.object(A.DATA_MANAGER,'fetch_nse_direct') as nse:
    d,src=A.DATA_MANAGER.smart_fetch('TCS',prefer_exch='BSE',strict_exch=True,_now=datetime(2026,10,10,13,23))
    self.assertIsNotNone(d);self.assertEqual(d.attrs['exchange'],'BSE');self.assertIn('STALE',src);nse.assert_not_called()
 def test_missing_requested_never_returns_other_fresh_or_stale(self):
  for end in ('2026-10-09','2026-10-08'):
   with patch.object(A.DATA_MANAGER,'fetch_tradingview',return_value=(frame(end),'NSE')),patch.object(A.DATA_MANAGER,'fetch_yahoo',return_value=(None,None)):
    d,_=A.DATA_MANAGER.smart_fetch('TCS',prefer_exch='BSE',strict_exch=True,_now=datetime(2026,10,10,13,23));self.assertIsNone(d)
 def test_requested_fresh_wins(self):
  with patch.object(A.DATA_MANAGER,'fetch_tradingview',return_value=(frame('2026-10-09'),'NSE')),patch.object(A.DATA_MANAGER,'fetch_yahoo',return_value=(frame('2026-10-09'),'BSE')):
   d,src=A.DATA_MANAGER.smart_fetch('TCS',prefer_exch='BSE',strict_exch=True,_now=datetime(2026,10,10,13,23));self.assertEqual(d.attrs['exchange'],'BSE');self.assertNotIn('STALE',src)
 def test_tv_adapter_does_not_probe_other_exchange(self):
  fake=Mock();fake.get_hist.return_value=None
  with patch.object(A.DATA_MANAGER,'tv',fake):
   self.assertEqual(A.DATA_MANAGER.fetch_tradingview('TCS',prefer_exch='BSE'),(None,None))
   self.assertEqual(fake.get_hist.call_count,1);self.assertEqual(fake.get_hist.call_args.kwargs['exchange'],'BSE')
 def test_yahoo_adapter_does_not_probe_other_exchange(self):
  with patch('yfinance.download',return_value=pd.DataFrame()) as f:
   self.assertEqual(A.DATA_MANAGER.fetch_yahoo('TCS',prefer_exch='BSE'),(None,None))
   self.assertEqual(f.call_count,1);self.assertEqual(f.call_args.args[0],'TCS.BO')
 def test_qualified_symbol_conflicts_rejected(self):
  for symbol,ex in (('NSE:TCS','BSE'),('TCS.NS','BSE'),('TCS.BO','NSE'),('BSE:TCS','NSE')):
   with self.assertRaises(ValueError):checked_symbol(symbol,ex)
  self.assertEqual(checked_symbol('BSE:TCS','BSE'),'TCS')
 def test_api_rejects_conflicting_identities_and_exchange_typo(self):
  c=A.app.test_client()
  for url in ('/api/stock/TCS.NS?ex=BSE','/api/quote/NSE:TCS?ex=BSE','/api/stream/TCS.BO?ex=NSE','/api/stock/TCS?ex=BSSE'):
   self.assertEqual(c.get(url).status_code,400,url)
 def test_quote_identity_explicit(self):
  with patch.object(A,'is_market_open',return_value=True),patch.object(A,'fetch_yahoo_live_ltp',return_value={'symbol':'TCS','price':100.,'source':'yahoo.bo','is_realtime':True}):
   q=A.get_live_quote('TCS',force=True,prefer_exch='BSE');self.assertEqual(q['symbol'],'TCS');self.assertEqual(q['exchange'],'BSE')
 def test_wrong_exchange_quote_refused(self):
  with patch.object(A,'is_market_open',return_value=True),patch.object(A,'fetch_yahoo_live_ltp',return_value={'symbol':'TCS','price':100.,'source':'yahoo.ns'}):
   self.assertIsNone(A.get_live_quote('TCS',force=True,prefer_exch='BSE'))
 def test_stale_stock_payload_is_historical_only(self):
  A._FAIL_CACHE.clear();d=frame('2026-10-08')
  with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(d,'Yahoo Finance (BSE) (STALE)')),patch.object(A,'fetch_yahoo_live_ltp',return_value=None),patch.object(A,'ml_engine',return_value={'score':50}),patch.object(A,'engine_market_regime',return_value={'score':50,'regime':'NEUTRAL'}),patch.object(A,'engine_multitimeframe',return_value={'score':50}),patch('yfinance.Ticker',return_value=Mock(info={})):
   r=A.app.test_client().get('/api/stock/TCS?ex=BSE');self.assertEqual(r.status_code,200,r.get_json());v=r.get_json();self.assertTrue(v['historical_only']);self.assertEqual(v['history_status'],'STALE');self.assertFalse(v['ensemble']['calibration']['ready']);self.assertEqual(v['risk']['qty'],0)

 def test_analysis_boundaries_reject_wrong_or_unknown_history_identity(self):
  for src in ('Yahoo Finance (NSE)', 'unknown'):
   A._FAIL_CACHE.clear()
   with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(frame('2026-10-09'),src)):
    r=A.app.test_client().get('/api/stock/TCS?ex=BSE');self.assertEqual(r.status_code,503)
    ok,p=A.kpi_scores_for('TCS',prefer_exch='BSE');self.assertFalse(ok);self.assertNotIn('kpi',p)
 def test_timeframes_stale_history_disables_plan(self):
  with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(frame('2026-10-08'),'Yahoo Finance (BSE) (STALE)')),patch.object(A,'_tf_fund_data',return_value={}):
   ok,p=A.kpi_scores_for('TCS',prefer_exch='BSE');self.assertTrue(ok,p);self.assertTrue(p['historical_only']);self.assertIsNone(p['plan'])

class Clock(unittest.TestCase):
 def parse(self,raw):return HistoryDatafeed._TvDatafeed__create_df(raw,'BSE:TCS')
 def test_epoch_to_aware_ist(self):
  d=self.parse(wire());self.assertEqual(str(d.index[-1]),'2026-10-09 15:00:00+05:30');self.assertEqual(str(d.index.tz),'Asia/Kolkata')
 def test_age_is_five_minutes(self):
  self.assertEqual(A.frame_age_minutes(self.parse(wire()),datetime(2026,10,9,15,5)),5)
 def test_process_timezone_independence(self):
  if not hasattr(time,'tzset'):self.skipTest('tzset unavailable; UTC epoch parser still tested')
  old=os.environ.get('TZ');values=[]
  try:
   for zone in ('UTC','Asia/Kolkata','America/New_York'):
    os.environ['TZ']=zone;time.tzset();values.append(str(self.parse(wire()).index[-1]))
  finally:
   if old is None:os.environ.pop('TZ',None)
   else:os.environ['TZ']=old
   time.tzset()
  self.assertEqual(len(set(values)),1)
 def test_incomplete_series_not_accepted(self):self.assertIsNone(self.parse(wire(False)))
 def test_chart_error_not_accepted(self):self.assertIsNone(self.parse(wire()+packet('series_error',['cs','s1','denied'])))
 def test_missing_volume_not_fabricated(self):self.assertIsNone(self.parse(wire(volume=None)))
 def test_fallback_quote_no_extra_timezone_shift(self):
  d=self.parse(wire()).rename(columns=str.title)
  with patch.object(A,'is_market_open',return_value=False),patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(d,'TradingView Direct (BSE)')):
   q=A.get_live_quote('TCS',force=True,prefer_exch='BSE');self.assertEqual(q['timestamp'],'15:00:00');self.assertEqual(q['exchange'],'BSE')

if __name__=='__main__':unittest.main(verbosity=2)
