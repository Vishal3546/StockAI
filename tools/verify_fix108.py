import os,sys,pathlib,json,tempfile,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.update(STOCKAI_OFFLINE='1',STOCKAI_SCAN_AUTOREFRESH='0',STOCKAI_AUTO_OPEN='0')
import numpy as np,pandas as pd
from unittest.mock import patch,Mock
import nifty_scanner as S,deep_analyzer as D,daily_context as C

def frame(n=250):
 c=100+np.arange(n)*.1+np.sin(np.arange(n))
 return pd.DataFrame(dict(Open=c,High=c+2,Low=c-2,Close=c,Volume=np.full(n,100.)),index=pd.bdate_range(end='2026-10-09',periods=n))
class Tests(unittest.TestCase):
 def test_zero_volume_not_invented(self):
  f=frame();f.iloc[-1,f.columns.get_loc('Volume')]=0;r=S.calculate_indicators(f)
  self.assertEqual(r.Vol_Ratio.iloc[-1],0);self.assertEqual(r.Volume.iloc[-1],0)
 def test_prior_volume_window(self):
  f=frame();f.iloc[-1,f.columns.get_loc('Volume')]=1000
  self.assertEqual(S.calculate_indicators(f).Vol_Ratio.iloc[-1],10)
 def test_missing_volume_baseline(self):
  f=frame();f.Volume=0;r=S.calculate_indicators(f)
  self.assertTrue(pd.isna(r.Vol_Ratio.iloc[-1]));self.assertTrue(pd.isna(r.VWAP.iloc[-1]))
 def test_vwap_not_history_anchored(self):
  f=frame();a=S.calculate_indicators(f).VWAP.iloc[-1];f.iloc[:100,f.columns.get_loc('Volume')]=1e8
  self.assertEqual(S.calculate_indicators(f).VWAP.iloc[-1],a)
 def test_no_missing_sma200_bear(self):
  with self.assertRaises(ValueError):S.calculate_ensemble(S.calculate_indicators(frame(100)))
 def test_completed_daily_cutoff(self):
  f=frame();h=Mock();h.last_completed_session.return_value=pd.Timestamp('2026-10-08').date()
  with patch.object(C,'host',return_value=h):r=C.completed(f)
  self.assertEqual(r.index[-1].date(),pd.Timestamp('2026-10-08').date())
 def test_geometry_rejected(self):
  f=frame();f.iloc[-1,f.columns.get_loc('High')]=1
  with self.assertRaises(ValueError):C.completed(f)
 def test_duplicates_rejected(self):
  f=frame();f.index=[f.index[0]]*len(f)
  with self.assertRaises(ValueError):C.completed(f)
 def test_timezone_ist_before_normalize(self):
  f=frame(20);f.index=pd.date_range('2026-09-01 20:00',periods=20,tz='UTC')
  self.assertEqual(D._normalise(f,'1mo').index[0],pd.Timestamp('2026-09-02'))
 def test_strict_exchange(self):
  h=Mock();h.checked_symbol.return_value='EMIL';h.resolve_symbol.return_value='EMIL';h.DATA_MANAGER.smart_fetch.return_value=(frame(),'fixture');h.last_completed_session.return_value=pd.Timestamp('2026-10-09').date()
  with patch.object(C,'host',return_value=h):r=C.fetch('EMIL','2y','BSE')
  self.assertTrue(h.DATA_MANAGER.smart_fetch.call_args.kwargs['strict_exch']);self.assertEqual(r.attrs['exchange'],'BSE')
 def test_intraday_refused(self):
  with self.assertRaises(ValueError):D.safe_download_deep('INFY',interval='5m')
 def test_flat_risk_no_infinity(self):
  f=frame();f[['Open','Close']]=100;f.High=102;f.Low=98
  r=D.advanced_risk_metrics(f);self.assertIsNone(r['sharpe_ratio']);self.assertIsNone(r['calmar_ratio']);self.assertIsNone(r['profit_factor']);self.assertEqual(r['win_rate'],0)
 def test_sortino_semideviation(self):
  f=frame();r=D.advanced_risk_metrics(f);ret=f.Close.pct_change().dropna();daily=1.065**(1/247)-1
  expected=(ret.mean()*247-.065)/(np.sqrt(np.minimum(ret-daily,0).pow(2).mean())*np.sqrt(247))
  self.assertAlmostEqual(r['sortino_ratio'],expected,places=3)
 def test_var_sign(self):self.assertGreaterEqual(D.advanced_risk_metrics(frame())['var_95'],0)
 def test_missing_benchmark_null(self):
  with patch.object(D,'safe_download_deep',return_value=None):r=D.sector_strength('INFY')
  self.assertIsNone(r['beta']);self.assertIsNone(r['relative_strength'])
 def test_benchmark_same_endpoints(self):
  f=frame();n=frame().iloc[40:-10]
  with patch.object(D,'safe_download_deep',return_value=n):r=D.sector_strength('INFY',stock=f)
  self.assertEqual(r['relative_strength'],0);self.assertEqual(r['beta'],1)
 def test_ml_shared_not_second_model(self):
  h=Mock();h.ml_engine.return_value={'available':True,'probability':55,'wf_folds':[{}]}
  with patch.object(C,'host',return_value=h):r=D.deep_ml_analysis(frame(),'INFY')
  h.ml_engine.assert_called_once();self.assertEqual(r['ensemble_prob'],55)
 def test_scanner_no_ml_gate_or_fake_plan(self):
  f=frame();f.attrs.update(historical_only=False,as_of='2026-10-09')
  with patch.object(S,'fetch_scanner_data',return_value=(f,'fixture')),patch.object(S,'calculate_real_ml',side_effect=AssertionError('must not train batch ML')):r=S.scan_stock('INFY')
  self.assertIsNone(r['ml_prob']);self.assertIsNone(r['sl']);self.assertFalse(r['execution_authorized'])
 def test_stale_unrated(self):
  f=frame();f.attrs['historical_only']=True
  with patch.object(S,'fetch_scanner_data',return_value=(f,'fixture')):self.assertEqual(S.scan_stock('INFY')['signal'],'UNRATED')
 def test_band_invalid_and_future(self):
  doc=json.loads(pathlib.Path(S.BANDS_PATH).read_text())
  with tempfile.TemporaryDirectory() as tmp:
   p=pathlib.Path(tmp)/'b.json'
   for change in [{'generated_at_utc':'2099-01-01T00:00:00+00:00'},{'bands':{'STRONG BUY':float('inf'),'BUY':60,'WATCH':50,'SELL':40}},{'schema':1}]:
    p.write_text(json.dumps({**doc,**change}))
    with patch.object(S,'BANDS_PATH',p):self.assertIsNone(S.load_scanner_bands()[0])
 def test_atomic_json_strict(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=pathlib.Path(tmp)/'x.json';C.atomic_json(p,{'x':float('nan')});self.assertEqual(json.loads(p.read_text()),{'x':None})
 def test_routes_auth_and_bounded_concurrency(self):
  import app as A,deep_web
  c=A.app.test_client()
  with patch.dict(A.SECURITY,{'TOKEN':'test108','RATE_LIMIT_PER_MIN':100000}):
   self.assertEqual(c.get('/api/deep-analysis/INFY').status_code,401);self.assertEqual(c.get('/screener').status_code,401)
  with patch.dict(A.SECURITY,{'TOKEN':'','RATE_LIMIT_PER_MIN':100000}),patch.object(D,'run_deep_analysis',return_value={'available':True,'symbol':'INFY'}) as run:
   self.assertEqual(c.get('/api/deep-analysis/INFY?exchange=NSE').status_code,200);self.assertEqual(run.call_args.kwargs['save'],False)
   deep_web._GATE.acquire()
   try:self.assertEqual(c.get('/api/deep-analysis/INFY').status_code,429)
   finally:deep_web._GATE.release()
 def test_metadata_survives_screener_whitelist(self):
  import screener
  row=screener.normalize_rows([{'symbol':'INFY','exchange':'NSE','data_as_of':'2026-10-09','input_hash':'abc','historical_only':False}])[0]
  self.assertEqual(row['input_hash'],'abc');self.assertFalse(row['historical_only'])
 def test_production_refit_separate_from_holdout(self):
  import app as A
  config={k:dict(A.CONFIG[k]) for k in ('ML_GB_PARAMS','ML_RF_PARAMS','ML_XGB_PARAMS')}
  for v in config.values(): v['n_estimators']=3
  with patch.dict(A.CONFIG,config):r=A._ml_engine_uncached(frame())
  self.assertTrue(r['available'],r.get('error'));self.assertGreater(r['live_refit_train_days'],r['train_days']+r['test_days'])
  self.assertLess(r['last_known_label_feature_session'],r['prediction_as_of'])
  self.assertIn('full known-label refit',r['metric_scope'])
  self.assertTrue(0<=r['ensemble_accuracy']<=100)
 def test_fundamental_zero_and_official_scope(self):
  f=frame();f.attrs.update(source='fixture',as_of='2026-10-09')
  ticker=Mock();ticker.info={'trailingPE':0,'returnOnEquity':0,'marketCap':0}
  with patch.object(D,'safe_download_deep',return_value=f),patch.object(D,'deep_ml_analysis',return_value={'available':False}),patch.object(D,'sector_strength',return_value={'available':False}),patch.object(D.yf,'Ticker',return_value=ticker):
   r=D.run_deep_analysis('INFY',save=False)
  self.assertEqual(r['fundamentals']['pe'],0);self.assertEqual(r['fundamentals']['roe'],0)
  self.assertIn('Latest completed daily OHLCV only',r['official_eod']['scope'])
if __name__=='__main__':unittest.main(verbosity=2)
