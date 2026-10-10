"""FIX106 failure-mode tests: matched validation, full fills, evidence boundaries."""
import os,sys,json,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.update(STOCKAI_OFFLINE='1',STOCKAI_SCAN_AUTOREFRESH='0',STOCKAI_AUTO_OPEN='0')
import validation_metrics as V
import scaleout_validation as S
import eod_validation as E

def frame(n=8):
    return pd.DataFrame(dict(Open=[100.]*n,High=[101.]*n,Low=[99.]*n,Close=[100.]*n,Volume=[10000.]*n),index=pd.bdate_range('2026-09-01',periods=n))
def sig(f,i=0,direction='LONG'):
    return dict(index=i,direction=direction,atr=2.,sl_mult=2.,information_end=str(f.index[i])[:10],rule_id='fixture-causal-rule')
def run(f=None,signals=None,**kw):
    f=frame() if f is None else f
    opts=dict(fee=lambda n,side:0.,horizon=3,notional=10000.);opts.update(kw)
    return S.simulate(f,[sig(f)] if signals is None else signals,**opts)

class Metrics(unittest.TestCase):
    def test_baseline_uses_training_majority_on_same_test_rows(self):
        r=V.fold_measure([1,1,0],[0,0,1],[0,1,1],first_session='a',last_session='b');self.assertEqual(r['baseline_correct'],1);self.assertEqual(r['correct'],2)
    def test_weighted_pool_not_mean_of_unequal_folds(self):
        folds=[dict(n=2,correct=2,baseline_correct=0),dict(n=8,correct=0,baseline_correct=8)];r=V.summarize(folds);self.assertEqual(r['accuracy'],20);self.assertEqual(r['baseline'],80);self.assertEqual(r['edge'],-60);self.assertAlmostEqual(r['fold_std_pp'],70.71)
    def test_neutral_zone_metrics_use_selected_rows_and_report_coverage(self):
        r=V.summarize([dict(n=20,correct=12,baseline_correct=11,policy_n=4,policy_correct=3,policy_baseline_correct=2)])
        self.assertEqual(r['policy_coverage_pct'],20);self.assertEqual(r['policy_accuracy'],75);self.assertEqual(r['policy_baseline'],50)
    def test_uncalculated_ml_outputs_are_none_not_fake_fifty(self):
        import app as A
        r=A._ml_engine_uncached(frame(4));self.assertFalse(r['available']);self.assertIsNone(r['walk_forward_accuracy']);self.assertIsNone(r['probability'])
    def test_empty_folds_are_unknown_not_zero(self):self.assertIsNone(V.summarize([])['accuracy'])
    def test_one_fold_has_no_measured_dispersion(self):self.assertIsNone(V.summarize([dict(n=2,correct=1,baseline_correct=1)])['fold_std_pp'])
    def test_alignment_and_invalid_labels_rejected(self):
        for train,test,pred in [([],[],[]),([0],[0,1],[0]),([0],[2],[0])]:
            with self.assertRaises(ValueError):V.fold_measure(train,test,pred,first_session='a',last_session='b')
    def test_reproducibility_input_and_config_sensitive(self):
        f=frame();a=V.reproducibility(f,{'ML_X':42},'code');f.iloc[0,4]+=1;b=V.reproducibility(f,{'ML_X':43},'code');self.assertNotEqual(a['input_sha256'],b['input_sha256']);self.assertNotEqual(a['config_sha256'],b['config_sha256']);self.assertIn('scikit-learn',a['versions'])
    def test_real_ensemble_exposes_model_scope(self):
        import app as A
        config=copy.deepcopy(A.CONFIG)
        for k in ('ML_GB_PARAMS','ML_RF_PARAMS','ML_XGB_PARAMS'):config[k]['n_estimators']=2
        x=np.arange(100).reshape(25,4);y=np.arange(25)%2
        p,names=V.ensemble_predict(x[:20],y[:20],x[20:],config);self.assertEqual(len(p),5);self.assertIn('random_forest',names);self.assertIn('logistic_regression',names);self.assertTrue(((p>=0)&(p<=1)).all())

class Simulator(unittest.TestCase):
    def test_no_hit_is_included_as_time_exit(self):
        r=run();self.assertEqual(r['trade_count'],1);self.assertEqual(r['trades'][0]['fills'][0]['reason'],'time_exit');self.assertEqual(r['trades'][0]['net_pnl'],0)
    def test_next_open_not_signal_close(self):
        f=frame();f.iloc[1]=[103,104,102,103,10000];r=run(f);self.assertEqual(r['trades'][0]['entry'],103)
    def test_stop_first_when_stop_and_targets_share_bar(self):
        f=frame();f.iloc[1]=[100,110,95,100,10000];t=run(f)['trades'][0];self.assertEqual(len(t['fills']),1);self.assertEqual(t['fills'][0]['price'],96);self.assertLess(t['net_pnl'],0)
    def test_adverse_gap_stop_fills_at_open(self):
        f=frame();f.iloc[2]=[90,92,89,91,10000];t=run(f)['trades'][0];self.assertEqual(t['fills'][0]['price'],90)
    def test_two_half_exits_and_three_fee_charges(self):
        f=frame();f.iloc[2]=[103,106,102,105,10000];f.iloc[3]=[106,110,104,109,10000];calls=[]
        def fee(n,side):calls.append((n,side));return 1.
        t=run(f,fee=fee)['trades'][0];self.assertEqual([x['fraction'] for x in t['fills']],[.5,.5]);self.assertEqual([x['price'] for x in t['fills']],[105,108]);self.assertEqual(t['gross_pnl'],650);self.assertEqual(t['net_pnl'],647);self.assertEqual(len(calls),3)
    def test_entry_stop_after_t1_is_not_net_breakeven(self):
        f=frame();f.iloc[2]=[103,106,102,105,10000];f.iloc[3]=[100,101,98,100,10000]
        t=run(f,fee=lambda n,s:10.)['trades'][0];self.assertEqual(t['fills'][-1]['price'],100);self.assertEqual(t['costs'],30)
    def test_same_bar_t1_entry_stop_is_adverse(self):
        f=frame();f.iloc[1]=[100,110,99,100,10000];t=run(f)['trades'][0];self.assertEqual(t['fills'][-1]['reason'],'ambiguous_bar_entry_stop');self.assertEqual(t['gross_pnl'],250)
    def test_incomplete_horizon_not_shorter_trade(self):
        f=frame();r=run(f,[sig(f,6)]);self.assertEqual(r['trade_count'],0);self.assertEqual(r['excluded'][0]['reason'],'incomplete_future_horizon')
    def test_overlapping_positions_excluded(self):
        f=frame();r=run(f,[sig(f,0),sig(f,1),sig(f,3)]);self.assertEqual(r['trade_count'],2);self.assertEqual(r['excluded'][0]['reason'],'overlapping_position')
    def test_cash_short_requires_borrow_model(self):
        f=frame();r=run(f,[sig(f,direction='SHORT')]);self.assertEqual(r['trade_count'],0);self.assertIn('borrow',r['excluded'][0]['reason'])
    def test_short_geometry_and_borrow_cost(self):
        f=frame();f.iloc[2]=[97,98,94,95,10000];f.iloc[3]=[94,96,90,92,10000]
        t=run(f,[sig(f,direction='SHORT')],instrument='research_borrowed_cash',borrow_bps_per_day=1.)['trades'][0];self.assertEqual([x['price'] for x in t['fills']],[95,92]);self.assertEqual(t['borrow_cost'],2.5);self.assertEqual(t['net_pnl'],647.5)
    def test_borrow_charges_calendar_weekends(self):
        f=frame();f.index=pd.bdate_range('2026-09-03',periods=len(f))
        t=run(f,[sig(f,direction='SHORT')],instrument='research_borrowed_cash',borrow_bps_per_day=1.)['trades'][0];self.assertEqual(t['borrow_cost'],5.)
    def test_future_cutoff_duplicate_and_missing_rule_rejected(self):
        f=frame();s=sig(f);s['information_end']='2026-12-31'
        for signals in ([s],[sig(f),sig(f)],[{**sig(f),'rule_id':''}]):
            with self.assertRaises(ValueError):run(f,signals)
    def test_invalid_fee_and_geometry_rejected(self):
        with self.assertRaises(ValueError):run(fee=lambda n,s:-1.)
        f=frame();f.iloc[0,1]=0
        with self.assertRaises(ValueError):run(f)
    def test_no_replay_can_self_certify_execution(self):
        r=run();self.assertFalse(r['execution_validated']);self.assertFalse(r['independent_holdout'])

class Official(unittest.TestCase):
    def reference(self,f):
        return dict(schema=1,session=str(f.index[-1])[:10],sources={'NSE':{'sha256':'fixture'}},rows={'NSE:TEST':{**{k:float(f.iloc[-1][k]) for k in E.FIELDS},'isin':'INTEST'}})
    def check(self,f,doc,exchange='NSE'):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ref.json';p.write_text(json.dumps(doc));return E.compare(f,'TEST',exchange,p)
    def test_exact_fields_match(self):
        f=frame();r=self.check(f,self.reference(f));self.assertEqual(r['status'],'MATCH');self.assertEqual(len(r['fields']),5)
    def test_volume_mismatch_not_hidden_by_matching_close(self):
        f=frame();d=self.reference(f);d['rows']['NSE:TEST']['Volume']+=1;r=self.check(f,d);self.assertEqual(r['status'],'MISMATCH');self.assertEqual(r['fields']['Close']['status'],'MATCH')
    def test_other_date_or_exchange_never_matches(self):
        f=frame();d=self.reference(f);self.assertEqual(self.check(f,d,'BSE')['status'],'UNVERIFIED');d['session']='2025-01-01';self.assertEqual(self.check(f,d)['status'],'UNVERIFIED')
    def test_missing_file_fails_honestly(self):self.assertEqual(E.compare(frame(),'X','NSE',ROOT/'missing_fixture.json')['status'],'UNVERIFIED')

class Integration(unittest.TestCase):
    def test_patterns_have_dates_age_and_distinct_occurrences(self):
        import app as A
        f=frame();f['Open']=99;f['Close']=101;f['Low']=99;f['High']=101
        pats=A.detect_all_candle_patterns(f);self.assertTrue(pats);self.assertTrue(all('session' in x and 'age_bars' in x for x in pats));self.assertTrue(any(x['is_latest_bar'] for x in pats));self.assertEqual(len({(x['name'],x['session']) for x in pats}),len(pats))
    def test_own_history_message_and_closed_readiness(self):
        from tools.verify_fix103 import route
        d=route('NEWSTOCK',n=500);self.assertTrue(d['ensemble']['own_history']['ready']);self.assertIn('Own-history available',d['risk']['risk_note']);self.assertFalse(d['risk']['validation']['execution_ready']);self.assertEqual(d['risk']['qty'],0);self.assertFalse(d['risk']['cost']['applicable_to_plan']);self.assertEqual(d['risk']['cost']['targets_net_pct'],{});self.assertIn('fundamentals',d['data_validation'])
    def test_production_never_passes_barrier_rate_to_kelly(self):
        import app as A
        from tools.verify_fix103 import route
        with patch.object(A,'calculate_risk',wraps=A.calculate_risk) as calculate:
            route('TCS');self.assertIsNone(calculate.call_args.kwargs['plan_measure'])
    def test_unfinished_daily_bar_not_used_for_rank_or_ml(self):
        import app as A
        from datetime import date
        from tools.verify_fix103 import route
        with patch.object(A,'last_completed_session',return_value=date(2026,10,8)):
            d=route('NEWSTOCK',n=501);self.assertEqual(d['rank_session'],'2026-10-08');self.assertTrue(d['analysis_partial']);self.assertEqual(d['ensemble']['own_history']['reference_session'],'2026-10-08')

if __name__=='__main__':unittest.main(verbosity=2)
