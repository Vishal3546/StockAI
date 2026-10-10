"""FIX103: separate provenance, honest calibration eligibility and no-plan API."""
import os, sys, unittest
from pathlib import Path
from unittest.mock import patch, Mock
os.environ.update(STOCKAI_OFFLINE='1', STOCKAI_SCAN_AUTOREFRESH='0', STOCKAI_AUTO_OPEN='0')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import numpy as np
import app as A
A.configure_security(token='',rate_limit_per_min=0)

def frame(n=300):
 c=100+np.arange(n)*.1+np.sin(np.arange(n)/4)
 return pd.DataFrame({'Open':c-.2,'High':c+1,'Low':c-1,'Close':c,'Volume':np.full(n,100000)},index=pd.bdate_range(end='2026-10-09',periods=n))

def route(symbol='ZZZUNSUPPORTED',exchange='BSE',n=300,quote=True):
 q={'symbol':symbol,'source':'yahoo.bo' if exchange=='BSE' else 'yahoo.ns','price':200.,'change':2.,'pChange':1.,'quote_time':'2026-10-09 15:15:00','is_realtime':False} if quote else None
 A._FAIL_CACHE.clear()
 with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(frame(n),f'TradingView Direct ({exchange})')),patch.object(A,'fetch_nse_live_ltp',return_value=None),patch.object(A,'fetch_yahoo_live_ltp',return_value=q),patch.object(A,'ml_engine',return_value={'available':False}),patch.object(A,'engine_market_regime',return_value={'score':50,'regime':'NEUTRAL'}),patch.object(A,'engine_multitimeframe',return_value={'score':50}),patch.object(A,'measure_plan_hit_rate',return_value=None),patch('yfinance.Ticker',return_value=Mock(info={})):
  r=A.app.test_client().get(f'/api/stock/{symbol}?ex={exchange}')
  assert r.status_code==200,r.get_json()
  return r.get_json()

class Disclosure(unittest.TestCase):
 def test_provenance_separates_same_exchange_providers(self):
  d=route();self.assertEqual(d['analysis_source'],'TradingView Direct (BSE)');self.assertEqual(d['quote_source'],'yahoo.bo');self.assertEqual(d['data_source'],d['quote_source']);self.assertEqual(d['price'],200);self.assertNotEqual(d['price'],d['frame_close']);self.assertEqual(d['risk']['reference_price'],200);self.assertEqual(d['quote_time'],'2026-10-09 15:15:00')
 def test_unknown_outside_universe_not_fixable_by_rebuild(self):
  c=route()['ensemble']['calibration'];self.assertFalse(c['ready']);self.assertFalse(c['symbol_supported']);self.assertIsNone(c['rebuild_command']);self.assertIn('BSE',c['note']);self.assertNotIn('NSE names',c['note']);self.assertIn('does not add',c['note'])
 def test_short_lgeindia_history_explicit(self):
  c=route('LGEINDIA',n=245)['ensemble']['calibration'];self.assertEqual(c['bars_available'],245);self.assertEqual(c['bars_required'],250);self.assertFalse(c['ready']);self.assertIsNone(c['rebuild_command'])
 def test_unknown_has_no_long_shaped_plan(self):
  d=route();r=d['risk'];self.assertFalse(r['plan_available']);self.assertEqual(r['direction'],'NONE');self.assertEqual(r['qty'],0)
  for k in ('sl','sl_pct','t1','t2','t3','entry_zone','trail_sl_plan','rr_ratio'):self.assertIsNone(r[k],k)
  self.assertEqual(r['cost']['targets_net_pct'],{});self.assertIsNone(r['cost']['cost_to_risk_pct']);self.assertIn('BSE',r['cost']['fee_scope'])
 def test_fallback_price_is_disclosed_as_history_not_tick(self):
  d=route(quote=False);self.assertAlmostEqual(d['price'],d['frame_close'],places=2);self.assertEqual(d['quote_source'],d['analysis_source']);self.assertIn('not a live tick',d['quote_time'])
 def test_kpi_labels_not_trade_instructions(self):
  for k in route()['kpi'].values():
   self.assertFalse(k['tradeable']);self.assertIn('Uncalibrated heuristic',k['interpretation']);self.assertNotIn(k['action'],('BUY','SELL','INVEST','AVOID','STRONG BUY','STRONG SELL'))
 def test_supported_symbol_rebuild_is_exchange_specific(self):
  engines=[{'name':name,'score':50} for name in A.SCORE_CAL.DAILY_WEIGHTS]
  with patch.object(A,'_score_history_for',return_value=(None,'artifact missing')):
   for exchange in ('NSE','BSE'):
    c=A.ensemble_score(engines,asof_session='2026-10-09',bars=250,symbol='TCS',exchange=exchange)['calibration'];self.assertEqual(c['rebuild_command'],f'python tools/build_score_calibration.py --exchange {exchange}')
 def test_calibration_formula_not_changed(self):
  import json
  # Presentation/KPI labels do not invalidate or silently refit the four-engine rank.
  for ex in ('NSE','BSE'):
   p=A.SCORE_CAL.artifact_path(ex)
   data=json.loads(p.read_text());A.SCORE_CAL.validate_artifact(data,A.score_formula_hash())

if __name__=='__main__':unittest.main(verbosity=2)
