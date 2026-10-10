"""Adversarial checks against a68cbd1; findings deliberately assert safe behavior.
Regressions assert the corrected FIX-100 contracts (including required Open fills). All writes use temp files.
Run: python tools/verify_fix100.py.
"""
import pathlib,os,sys,tempfile,unittest,math,json,time
from unittest.mock import patch,Mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.environ.update(STOCKAI_OFFLINE='1',STOCKAI_SCAN_AUTOREFRESH='0')
import app as A
import pandas as pd
import numpy as np
from research.backtest import run_backtest
from research.costs import CostConfig
from option_analytics import strategy_stats, straddle_price, oi_walls
A.configure_security(token='',rate_limit_per_min=0)
def df():
 c=100*np.exp(np.cumsum(np.random.default_rng(37).normal(0,.02,140)))
 return pd.DataFrame({'Open':c,'High':c+3,'Low':c-3,'Close':c,'Volume':10000},index=pd.bdate_range('2025-01-01',periods=len(c)))
class Audit(unittest.TestCase):
 def setUp(self):
  A._ML_CACHE.clear();A._FAIL_CACHE.clear();A._PLAN_MEASURE_CACHE.clear();A._LIVE_CACHE.clear()
 def test_bse_analysis_never_fetches_nse_price(self):
  with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(df(),'TradingView Direct (BSE)')),patch.object(A,'resolve_symbol',return_value='TCS'),patch.object(A,'fetch_nse_live_ltp',return_value=None) as nse,patch.object(A,'fetch_yahoo_live_ltp',return_value=None),patch.object(A,'ml_engine',side_effect=RuntimeError('audit intentional stop after price selection')):
   A.app.test_client().get('/api/stock/TCS?ex=BSE');self.assertEqual(nse.call_count,0)
 def test_negative_cache_exchange_isolation(self):
  with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(None,None)) as f,patch.object(A.DATA_MANAGER,'exch_fallback',None):
   c=A.app.test_client();c.get('/api/stock/TCS?ex=BSE');c.get('/api/stock/TCS?ex=NSE');self.assertEqual(f.call_count,2)
 def test_yahoo_quote_cannot_cross_exchange(self):
  fail=Mock(status_code=404);success=Mock(status_code=200)
  success.json.return_value={'chart':{'result':[{'meta':{'regularMarketPrice':100,'previousClose':99,'regularMarketTime':int(time.time())}}]}}
  with patch.object(A._HTTP,'get',side_effect=[fail,success]):
   q=A.fetch_yahoo_live_ltp('TCS',prefer_exch='BSE');self.assertTrue(q is None or q['source']=='yahoo.bo',q)
 def test_quote_daily_fallback_is_strict(self):
  with patch.object(A,'is_market_open',return_value=True),patch.object(A,'fetch_yahoo_live_ltp',return_value=None),patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(None,None)) as f:
   A.get_live_quote('TCS',force=True,prefer_exch='BSE');self.assertTrue(f.call_args.kwargs.get('strict_exch'))
 def test_mtf_requests_are_strict(self):
  with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(df(),'TradingView Direct (BSE)')) as f:
   A.engine_multitimeframe('TCS',daily_df=df(),prefer_exch='BSE')
   self.assertTrue(all(c.kwargs.get('strict_exch') for c in f.call_args_list))
 def test_plan_cache_all_parameters(self):
  d=df();A.measure_plan_hit_rate(d,'LONG',1,min_n=1,symbol='TCS',horizon=10,t1_mult=1)
  r=A.measure_plan_hit_rate(d,'LONG',1,min_n=1,symbol='TCS',horizon=2,t1_mult=3)
  self.assertEqual((r['horizon'],r['t1_mult']),(2,3))
 def test_frame_missing_completed_session_not_fresh(self):
  d=df().iloc[:3].copy();d.index=pd.to_datetime(['2026-10-02','2026-10-05','2026-10-06'])
  self.assertFalse(A.frame_is_fresh(d,'1d',now=A.datetime(2026,10,8,14,0))[0])
 def test_stale_quote_does_not_fire_live_alert(self):
  with tempfile.TemporaryDirectory() as td,patch.object(A,'ALERTS_FILE',td+'/a.json'):
   json.dump([{'id':1,'symbol':'TCS','exchange':'BSE','condition':'above','level':100,'fired':False}],open(A.ALERTS_FILE,'w'))
   with patch.object(A,'get_live_quote',return_value={'price':150,'is_realtime':False,'stale':True,'source':'yahoo.ns'}):
    r=A.app.test_client().post('/api/alerts/check').get_json();self.assertFalse(r['alerts'][0]['fired'])
 def test_alert_ids_unique_with_same_millisecond(self):
  with tempfile.TemporaryDirectory() as td,patch.object(A,'ALERTS_FILE',td+'/a.json'),patch.object(A.time,'time',return_value=1234567):
   c=A.app.test_client()
   for s in ['TCS','RELIANCE']:c.post('/api/alerts',json={'symbol':s,'condition':'above','level':100})
   ids=[r['id'] for r in c.get('/api/alerts').get_json()['alerts']];self.assertEqual(len(ids),len(set(ids)))
 def test_nonfinite_alert_rejected_before_persist(self):
  with tempfile.TemporaryDirectory() as td,patch.object(A,'ALERTS_FILE',td+'/a.json'):
   self.assertEqual(A.app.test_client().post('/api/alerts',json={'symbol':'TCS','condition':'above','level':'NaN'}).status_code,400)
 def test_bounded_cache_size(self):
  c={}
  for i in range(100):A._cache_put(c,i,i,max_entries=10)
  self.assertEqual(len(c),10)
 def test_documented_last_write_eviction(self):
  c={};A._cache_put(c,'a',1,2);A._cache_put(c,'b',2,2);c.get('a');A._cache_put(c,'c',3,2)
  self.assertNotIn('a',c);self.assertEqual(set(c),{'b','c'})
 def test_json_response_nonfinite_becomes_null(self):
  self.assertEqual(json.loads(A.app.json.dumps({'x':float('nan')})),{'x':None})
 def test_backtest_ledger_reconciles_zero_cost_gain(self):
  ix=pd.date_range('2026-01-01',periods=4);d=pd.DataFrame({'Open':[100,100,110,110],'Close':[100,110,110,110]},index=ix)
  free=CostConfig(brokerage_pct=0,brokerage_cap=0,stt_buy=0,stt_sell=0,stt_intraday_sell=0,exch_pct=0,sebi_pct=0,stamp_buy=0,gst_rate=0,slippage_bps=0)
  r=run_backtest(d,pd.Series([1,0,0,0],index=ix),cost=free)
  self.assertAlmostEqual(r.equity.iloc[-1]-100000,sum(t['pnl'] for t in r.trades),places=2)
 def test_terminal_liquidation_has_exit_cost(self):
  ix=pd.date_range('2026-01-01',periods=4);d=pd.DataFrame({'Open':[100]*4,'Close':[100]*4},index=ix)
  cost=CostConfig(brokerage_pct=.001,brokerage_cap=100000,stt_buy=0,stt_sell=0,exch_pct=0,sebi_pct=0,stamp_buy=0,gst_rate=0,slippage_bps=0)
  r=run_backtest(d,pd.Series([1]*4,index=ix),cost=cost);self.assertGreater(r.costs_total,100)
 def test_naked_call_loss_not_finite_sample_bound(self):
  r=strategy_stats([{'opt':'CE','side':'sell','strike':100,'premium':5}],[80,100,120]);self.assertTrue(r['max_loss'] is None or math.isinf(r['max_loss']))
 def test_straddle_missing_leg_unavailable(self):
  self.assertIsNone(straddle_price([{'strike':100,'ce_ltp':5,'pe_ltp':None}],100))
 def test_zero_oi_no_wall(self):
  self.assertEqual(oi_walls([{'strike':100,'ce_oi':0,'pe_oi':0}]),{'call_wall':None,'put_wall':None})
class JournalAndMath(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=A.JOURNAL_FILE;A.JOURNAL_FILE=self.tmp.name+'/journal.json';self.c=A.app.test_client()
  self.base={'symbol':'TCS','exchange':'BSE','side':'LONG','entry':100,'stop':90,'qty':10,'note':'keep me'}
 def tearDown(self):
  A.JOURNAL_FILE=self.old;self.tmp.cleanup()
 def add(self):
  r=self.c.post('/api/journal',json=self.base);self.assertEqual(r.status_code,200);return r.get_json()['item']
 def test_atomic_close_invalid_input_preserves_record(self):
  row=self.add();before=pathlib.Path(A.JOURNAL_FILE).read_bytes()
  r=self.c.patch('/api/journal/'+row['id']+'/close',json={'exit':'abc'})
  self.assertIn(r.status_code,(400,422));self.assertEqual(before,pathlib.Path(A.JOURNAL_FILE).read_bytes())
 def test_close_idempotent_preserves_identity_note_exchange(self):
  row=self.add();url='/api/journal/'+row['id']+'/close'
  self.assertEqual(self.c.patch(url,json={'exit':120}).status_code,200)
  self.assertEqual(self.c.patch(url,json={'exit':120}).status_code,200)
  rows=self.c.get('/api/journal').get_json()['items'];self.assertEqual(len(rows),1)
  for key in ('id','exchange','note','logged_at'):self.assertEqual(rows[0][key],row[key])
  self.assertEqual(self.c.patch(url,json={'exit':130}).status_code,409)
  self.assertLess(rows[0]['r_net'],rows[0]['r'])
 def test_journal_rejects_nonfinite_fractional_and_nonobject(self):
  for body in ([1],{**self.base,'entry':'NaN'},{**self.base,'qty':1.9},{**self.base,'exit':'Infinity'},{**self.base,'exchange':'INVALID'},{**self.base,'qty':True}):
   with self.subTest(body=body):self.assertIn(self.c.post('/api/journal',json=body).status_code,(400,422))
  self.assertEqual(self.c.get('/api/journal').get_json()['items'],[])
 def test_full_stock_route_provenance_and_execution_gate(self):
  d=df();d.index=pd.bdate_range(end='2026-10-09',periods=len(d));A._FAIL_CACHE.clear()
  with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(d,'yahoo.bo')),patch.object(A,'fetch_yahoo_live_ltp',return_value=None),patch.object(A,'fetch_nse_live_ltp',return_value=None) as nse,patch.object(A,'ml_engine',return_value={'score':50,'probability':50}),patch.object(A,'engine_market_regime',return_value={'score':50,'regime':'NEUTRAL'}),patch.object(A,'engine_multitimeframe',return_value={'score':50}),patch('yfinance.Ticker',return_value=Mock(info={})) as ticker:
   response=self.c.get('/api/stock/TCS?ex=BSE');self.assertEqual(response.status_code,200,response.get_json())
   payload=response.get_json();self.assertFalse(payload['ensemble']['tradeable']);self.assertEqual(payload['risk']['qty'],0)
   self.assertFalse(payload['risk']['edge_verified']);self.assertEqual(payload['data_provenance']['exchange'],'BSE');nse.assert_not_called();ticker.assert_called_once_with('TCS.BO')
 def test_gap_uses_open_not_close(self):
  d=df();d['Close']=100.;d['Open']=100.;d['High']=111.;d['Low']=99.;d['ATR']=2.;d['BB_Width']=.1;d['BB_PctB']=.5
  d.iloc[-1,d.columns.get_loc('Open')]=110
  r=A.calculate_trade_plan(d,index_frame=d);self.assertEqual(r['prevday']['gap_pct'],10);self.assertTrue(r['filters']['gap_skip'])
 def test_cache_content_invalidation(self):
  A._ML_CACHE.clear();d=df()
  with patch.object(A,'_ml_engine_uncached',side_effect=lambda x:{'close':float(x.Close.iloc[-1])}) as f:
   a=A.ml_engine(d);rev=d.copy();rev['Close']+=10;b=A.ml_engine(rev)
   self.assertNotEqual(a['close'],b['close']);self.assertEqual(f.call_count,2)
 def test_no_target_backfill_or_unknown_label_zero(self):
  for name in ('app.py','deep_analyzer.py','nifty_scanner.py'):
   src=(ROOT/name).read_text();self.assertNotIn('.bfill()',src);self.assertIn('.where(c.shift(-1).notna())',src)
 def test_next_open_does_not_capture_unowned_overnight_gap(self):
  ix=pd.date_range('2026-01-01',periods=4)
  d=pd.DataFrame({'Open':[100,120,120,120],'Close':[100,120,120,120]},index=ix)
  free=CostConfig(brokerage_pct=0,brokerage_cap=0,stt_buy=0,stt_sell=0,stt_intraday_sell=0,exch_pct=0,sebi_pct=0,stamp_buy=0,gst_rate=0,slippage_bps=0)
  r=run_backtest(d,pd.Series([1,0,0,0],index=ix),cost=free)
  self.assertAlmostEqual(r.equity.iloc[-1],100000,places=6)
  self.assertAlmostEqual(r.metrics['ledger_reconciliation_error'],0,places=6)
 def test_no_same_open_fills_for_close_signal(self):
  with self.assertRaises(ValueError):run_backtest(df(),pd.Series(1.,index=df().index),exec_lag=0)
 def test_ledger_reversal_and_partial_allocation_reconcile(self):
  d=df();p=pd.Series(([0,1,.5,-.5,-1,0]*24)[:len(d)],index=d.index)
  r=run_backtest(d,p,allow_short=True)
  self.assertAlmostEqual(r.equity.iloc[-1]-100000,sum(t['pnl'] for t in r.trades),places=6)
  self.assertAlmostEqual(r.costs_total,sum(t['costs'] for t in r.trades),places=6)
 def test_options_unlimited_and_spread_bounds(self):
  legs=[{'opt':'CE','side':'buy','strike':100,'premium':5}]
  r=strategy_stats(legs,[99,100,101]);self.assertTrue(r['profit_unlimited']);self.assertEqual(r['max_loss'],-5);self.assertEqual(r['breakevens'],[105])
  legs.append({'opt':'CE','side':'sell','strike':110,'premium':2})
  r=strategy_stats(legs,[100]);self.assertFalse(r['profit_unlimited']);self.assertEqual(r['max_profit'],7);self.assertEqual(r['max_loss'],-3)
 def test_cash_capacity_and_finite_calculators(self):
  from calculators import position_size,_num,trade_costs
  r=position_size(100000,1,100,99.9);self.assertLessEqual(r['position_value'],100000)
  self.assertIsNone(_num('Infinity'));self.assertIsNone(trade_costs(1000,slippage_bps='NaN'));self.assertIsNone(trade_costs(1000,brokerage_pct=-1))
 def test_process_lock_reentrant(self):
  from store_lock import ProcessRLock
  lock=ProcessRLock(self.tmp.name+'/test.lock')
  with lock:
   with lock:pass
 def test_repeated_alert_check_does_not_renotify(self):
  old=A.ALERTS_FILE;A.ALERTS_FILE=self.tmp.name+'/alerts.json'
  try:
   self.c.post('/api/alerts',json={'symbol':'TCS','condition':'above','level':100})
   with patch.object(A,'is_market_open',return_value=True),patch.object(A,'get_live_quote',return_value={'price':150.,'is_realtime':True,'stale':False,'source':'yahoo.ns'}):
    self.assertEqual(len(self.c.post('/api/alerts/check').get_json()['fired_now']),1)
    self.assertEqual(self.c.post('/api/alerts/check').get_json()['fired_now'],[])
  finally:A.ALERTS_FILE=old
 def test_malformed_json_rejected(self):
  self.assertEqual(self.c.post('/api/alerts/check',data='null',content_type='application/json').status_code,400)
  self.assertEqual(self.c.post('/api/alerts/check',data='{',content_type='application/json').status_code,400)
 def test_corrupt_store_not_overwritten(self):
  old=A.WATCHLIST_FILE;A.WATCHLIST_FILE=self.tmp.name+'/watchlist.json'
  try:
   p=pathlib.Path(A.WATCHLIST_FILE);p.write_text('{broken')
   self.assertEqual(self.c.post('/api/watchlist',json={'symbol':'TCS'}).status_code,409)
   self.assertEqual(p.read_text(),'{broken')
  finally:A.WATCHLIST_FILE=old
 def test_train_only_majority_baseline_and_remainder(self):
  from research.ml_lab import purged_walk_forward
  ix=pd.date_range('2025-01-01',periods=277)
  X=pd.DataFrame({'x':np.arange(277)},index=ix);y=pd.Series([0]*250+[1]*27,index=ix)
  r=purged_walk_forward(X,y,warmup=250,n_folds=2)
  self.assertEqual(r.n_oos,27);self.assertEqual(r.baseline,0)
  with self.assertRaises(ValueError):purged_walk_forward(X,y.iloc[::-1])
 def test_process_store_mutex_prevents_lost_updates(self):
  import subprocess
  counter=pathlib.Path(self.tmp.name)/'count';counter.write_text('0')
  code="from store_lock import ProcessRLock; from pathlib import Path; import sys; p=Path(sys.argv[1]); lock=ProcessRLock(str(p)+'.lock')\nfor i in range(30):\n with lock: p.write_text(str(int(p.read_text())+1))"
  children=[subprocess.Popen([sys.executable,'-c',code,str(counter)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(3)]
  try:
   for child in children:
    out,err=child.communicate(timeout=30);self.assertEqual(child.returncode,0,err.decode())
   self.assertEqual(counter.read_text(),'90')
  finally:
   for child in children:
    if child.poll() is None:child.kill();child.wait()
 def test_utf8_and_frontend_atomic_close_wiring(self):
  src=(ROOT/'app.py').read_text();page=(ROOT/'Timeframes.html').read_text()
  self.assertIn("'PYTHONIOENCODING': 'utf-8'",src)
  self.assertIn("journalSend('PATCH'",page)
  self.assertNotIn("journalSend('DELETE', '/api/journal?id=' + encodeURIComponent(cid))",page)
  self.assertIn("exchange: document.getElementById('exch').value",page)

if __name__=='__main__':unittest.main(verbosity=2)
