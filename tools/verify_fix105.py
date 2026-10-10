"""FIX105: descriptive coverage without unvalidated pooled/trade admission."""
import os, sys, unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.update(STOCKAI_OFFLINE='1', STOCKAI_SCAN_AUTOREFRESH='0', STOCKAI_AUTO_OPEN='0')
import own_history as H
import score_calibration as C

def frame(n=500):
    c=100+np.arange(n)*.1
    return pd.DataFrame(dict(Open=c,High=c+2,Low=c-2,Close=c,Volume=np.full(n,100000)),index=pd.bdate_range(end='2026-10-09',periods=n))

def funcs():
    return [lambda d,name=name:dict(name=name,score=50) for name in C.DAILY_WEIGHTS]

def describe(df=None,**kw):
    opts=dict(symbol='NEWSTOCK',exchange='NSE',source='TradingView Direct (NSE)',formula_hash='test-formula',engines=funcs(),current_day='2026-10-10')
    opts.update(kw)
    return H.describe(frame() if df is None else df,**opts)

class OwnHistory(unittest.TestCase):
    def setUp(self):
        H._CACHE.clear()
    def test_exact_500_yields_250_prior_scores(self):
        r=describe();self.assertTrue(r['ready']);self.assertEqual(r['sessions'],250);self.assertEqual(r['asof_session'],'2026-10-08');self.assertEqual(r['reference_session'],'2026-10-09');self.assertEqual(r['relative_rank_pct'],100);self.assertFalse(r['directional_signal']);self.assertFalse(r['execution_validated']);self.assertNotIn('thresholds',r)
    def test_499_and_300_and_245_are_not_padded(self):
        for n in (499,300,245):
            r=describe(frame(n));self.assertFalse(r['ready']);self.assertEqual(r['status'],'INSUFFICIENT_HISTORY');self.assertEqual(r['bars_available'],n)
    def test_historical_windows_never_see_reference_bar(self):
        seen=[]
        fs=funcs();old=fs[0]
        def spy(d):
            seen.append((len(d),d.index[-1]));return old(d)
        fs[0]=spy;r=describe(engines=fs)
        self.assertTrue(r['ready']);self.assertEqual(len(seen),251);self.assertTrue(all(n==250 for n,_ in seen));self.assertTrue(all(d<pd.Timestamp('2026-10-09') for _,d in seen[:-1]));self.assertEqual(seen[-1][1],pd.Timestamp('2026-10-09'))
    def test_reference_price_change_does_not_change_training(self):
        a=describe();f=frame();f.iloc[-1,:4]*=1.5;b=describe(f)
        self.assertEqual(a['history'],b['history']);self.assertNotEqual(a['input_sha256'],b['input_sha256'])
    def test_degraded_engine_fails_closed(self):
        fs=funcs();fs[0]=lambda d:dict(name='Volume Profile',score=50,degraded=True)
        self.assertEqual(describe(engines=fs)['status'],'INCOMPLETE_ENGINES');self.assertFalse(H._CACHE)
    def test_engine_exception_releases_build_slot(self):
        def bad(d):raise RuntimeError('fixture')
        self.assertEqual(describe(engines=[bad])['status'],'BUILD_FAILED');self.assertTrue(describe()['ready'])
    def test_stale_future_and_invalid_clock_refused(self):
        for kw in ({'fresh':False},{'current_day':'2026-10-08'},{'current_day':'2026-12-01'},{'current_day':'bad'}):self.assertFalse(describe(**kw)['ready'])
    def test_invalid_ohlcv_refused(self):
        for column,value in [('Close',float('nan')),('Volume',-1),('High',0)]:
            f=frame();f.loc[f.index[-1],column]=value;self.assertEqual(describe(f)['status'],'INVALID_HISTORY')
    def test_duplicate_and_reverse_sessions_refused(self):
        f=frame();self.assertFalse(describe(f.iloc[::-1])['ready']);f.index=list(f.index[:-1])+[f.index[-2]];self.assertFalse(describe(f)['ready'])
    def test_unverified_weekend_refused(self):
        f=frame();f.index=list(f.index[:-1])+[pd.Timestamp('2026-10-10')];self.assertEqual(describe(f)['status'],'INVALID_HISTORY')
    def test_primary_verified_special_session_allowed(self):
        f=frame(499);extra=f.iloc[[0]].copy();extra.index=pd.to_datetime(['2026-02-01']);f=pd.concat([f,extra]).sort_index();self.assertTrue(describe(f)['ready'])
    def test_frame_exchange_conflict_refused(self):
        f=frame();f.attrs['exchange']='BSE';self.assertEqual(describe(f)['status'],'INVALID_INPUT')
    def test_cache_isolated_by_symbol_exchange_source_formula_and_prices(self):
        self.assertFalse(describe()['cache_hit']);self.assertTrue(describe()['cache_hit'])
        for kw in ({'symbol':'OTHER'},{'exchange':'BSE','source':'TradingView Direct (BSE)'},{'source':'yahoo.ns'},{'formula_hash':'changed'}):self.assertFalse(describe(**kw)['cache_hit'])
        f=frame();f.iloc[0,4]+=1;self.assertFalse(describe(f)['cache_hit'])
    def test_cached_result_not_mutable_by_caller_and_stale_checked_before_cache(self):
        r=describe();r['history'][0]['score']=1;self.assertEqual(describe()['history'][0]['score'],50);self.assertFalse(describe(current_day='2027-01-01')['ready'])
    def test_bounded_lru(self):
        with patch.object(H,'MAX_CACHE',2):
            for symbol in ('A','B','C'):describe(symbol=symbol)
            self.assertEqual(len(H._CACHE),2);self.assertFalse(describe(symbol='A')['cache_hit'])
    def test_busy_is_explicit_not_unbounded_queue(self):
        H._BUILD.acquire()
        try:self.assertEqual(describe()['status'],'BUSY')
        finally:H._BUILD.release()

class Integration(unittest.TestCase):
    def test_arbitrary_stock_automatic_rank_preserves_zero_order_and_no_plan(self):
        from tools.verify_fix103 import route
        for ex in ('NSE','BSE'):
            d=route('NEWSTOCK',exchange=ex,n=500);o=d['ensemble']['own_history'];self.assertTrue(o['ready']);self.assertEqual(o['exchange'],ex);self.assertEqual(o['score'],d['ensemble']['score']);self.assertFalse(d['ensemble']['calibration']['ready']);self.assertFalse(d['risk']['plan_available']);self.assertEqual(d['risk']['qty'],0);self.assertEqual(d['risk']['direction'],'NONE');self.assertFalse(d['ml_study']['selected_symbol_in_study']);self.assertIn('not included',d['ml_study']['selected_symbol_note'])
    def test_short_history_has_explicit_500_bar_requirement(self):
        from tools.verify_fix103 import route
        d=route('LGEINDIA',n=245);self.assertEqual(d['ensemble']['own_history']['status'],'INSUFFICIENT_HISTORY');self.assertEqual(d['ensemble']['own_history']['bars_required'],500)
    def test_existing_pooled_rank_is_preserved(self):
        from tools.verify_fix103 import route
        d=route('EMIL',exchange='NSE',n=300);self.assertNotIn('own_history',d['ensemble']);self.assertTrue(d['ensemble']['calibration']['ready'])
    def test_typo_or_provider_miss_is_exchange_scoped_without_substitution(self):
        import app as A
        A.configure_security(token='',rate_limit_per_min=0);A._FAIL_CACHE.clear()
        with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(None,None)) as fetch,patch.object(A.DATA_MANAGER,'exch_fallback',None):
            r=A.app.test_client().get('/api/stock/EMAIL?ex=BSE')
            self.assertEqual(r.status_code,404);self.assertEqual(r.get_json()['error_code'],'SYMBOL_OR_HISTORY_UNAVAILABLE');self.assertIn('does not prove',r.get_json()['hint']);self.assertEqual(fetch.call_args.args[0],'EMAIL');self.assertEqual(fetch.call_args.kwargs['prefer_exch'],'BSE');self.assertTrue(fetch.call_args.kwargs['strict_exch'])
    def test_outside_cohort_requests_real_history_depth_automatically(self):
        import app as A
        A.configure_security(token='',rate_limit_per_min=0);A._FAIL_CACHE.clear()
        with patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(None,None)) as fetch,patch.object(A.DATA_MANAGER,'exch_fallback',None):
            A.app.test_client().get('/api/stock/ANOTHERNEWSTOCK?ex=NSE')
            self.assertEqual(fetch.call_args.kwargs['period'],'3y');self.assertGreaterEqual(fetch.call_args.kwargs['n_bars'],501)
    def test_study_coverage_and_archival_are_separate(self):
        import app as A
        p=A.ml_study_payload('NSE','TORNTPHARM');self.assertFalse(p['selected_symbol_in_study']);self.assertTrue(p['rebuild_required']);self.assertFalse(p['execution_validated']);self.assertTrue(A.ml_study_payload('NSE','TCS')['selected_symbol_in_study'])

if __name__=='__main__':unittest.main(verbosity=2)
