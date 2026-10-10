"""Deterministic coverage, provenance and official-close boundary regressions."""
import os,sys,json,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import patch
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.update(STOCKAI_OFFLINE='1',STOCKAI_SCAN_AUTOREFRESH='0',STOCKAI_AUTO_OPEN='0')
import app as A
import score_calibration as C
from research.provenance import pipeline_fingerprint
from tools.reconcile_official_close import parse_official,pick_official,compare_close

class Coverage(unittest.TestCase):
 def test_requested_additions_are_explicit_not_auto_admitted(self):
  self.assertTrue({'JIOFIN','EMIL','CANBK'}<=set(C.UNIVERSE));self.assertEqual(len(C.UNIVERSE),33);self.assertNotIn('LGEINDIA',C.UNIVERSE)
 def test_both_artifacts_have_full_past_coverage(self):
  for ex in ('NSE','BSE'):
   doc=json.loads(C.artifact_path(ex).read_text());f=C.validate_artifact(doc,A.score_formula_hash());self.assertEqual(doc['exchange'],ex);self.assertEqual(f['sessions'],250)
   self.assertEqual(doc['session_policy'],C.SESSION_POLICY);self.assertIn('TradingView',doc['source'])
   for sym in ('JIOFIN','EMIL','CANBK'):
    self.assertEqual(f['symbol_sessions'][sym],250);self.assertIn(sym,f['symbols_covered'])
   self.assertLess(f['asof_session'],'2026-10-09');C.for_session(f,'2026-10-09',bars=300,current_day='2026-10-10')
 def test_scanner_keeps_separate_fitted_cohort(self):
  import nifty_scanner as NS
  self.assertEqual(tuple(NS.UNIVERSE),C.BASE_UNIVERSE);self.assertEqual(set(C.UNIVERSE)-set(NS.UNIVERSE),{'JIOFIN','EMIL','CANBK'})
 def test_manifest_edit_requires_rebuild(self):
  doc=json.loads(C.artifact_path('NSE').read_text());doc['universe']=doc['universe'][:-1]
  with self.assertRaises(C.CalibrationError):C.validate_artifact(doc,A.score_formula_hash())
 def test_manifest_duplicates_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'u.json';p.write_text(json.dumps({'schema':1,'symbols':list(C.UNIVERSE)+['TCS']}))
   with self.assertRaises(ValueError):C.load_universe(p)
 def test_verified_special_session_preserved(self):
  self.assertIn('2026-02-01',C.verified_special_sessions());self.assertIn('2025-02-01',C.verified_special_sessions())
  for ex in ('NSE','BSE'):
   doc=json.loads(C.artifact_path(ex).read_text());self.assertIn('2026-02-01',{r['session'] for r in doc['history']})
 def test_unknown_weekend_still_rejected(self):
  doc=json.loads(C.artifact_path('NSE').read_text());rows=copy.deepcopy(doc['history']);rows[-1]['session']='2026-10-11'
  with self.assertRaises(C.CalibrationError):C.fit_history(rows)
 def test_short_history_gate_not_relaxed(self):
  f=C.validate_artifact(json.loads(C.artifact_path('NSE').read_text()),A.score_formula_hash())
  with self.assertRaises(C.CalibrationError):C.for_session(f,'2026-10-09',bars=245,current_day='2026-10-10')

class Reconciliation(unittest.TestCase):
 def csv(self,ex='NSE',session='2026-10-09'):
  return f'TradDt,Src,ISIN,TckrSymb,SctySrs,FinInstrmId,ClsPric,LastPric\n{session},{ex},INE467B01029,TCS,EQ,11536,2156,2100\n'.encode()
 def test_uses_official_close_not_last_trade(self):
  df=parse_official(self.csv(),'NSE','2026-10-09');r=pick_official(df,'TCS','NSE');self.assertEqual(r['official_close'],2156)
  f=pd.DataFrame({'Close':[2156]},index=pd.to_datetime(['2026-10-09']));self.assertEqual(compare_close(r,f,'NSE')['status'],'MATCH')
  f.iloc[0,0]=2100;self.assertEqual(compare_close(r,f,'NSE')['status'],'MISMATCH')
 def test_wrong_exchange_date_html_rejected(self):
  for raw,ex,day in ((self.csv(),'BSE','2026-10-09'),(self.csv(),'NSE','2026-10-08'),(b'<html>blocked</html>','NSE','2026-10-09')):
   with self.assertRaises(ValueError):parse_official(raw,ex,day)
 def test_wrong_vendor_identity_never_matches(self):
  row=pick_official(parse_official(self.csv(),'NSE','2026-10-09'),'TCS','NSE');frame=pd.DataFrame({'Close':[2156]},index=pd.to_datetime(['2026-10-09']))
  self.assertEqual(compare_close(row,frame,'BSE')['status'],'UNVERIFIED');frame.index=pd.to_datetime(['2026-10-08']);self.assertEqual(compare_close(row,frame,'NSE')['status'],'UNVERIFIED')
 def test_recorded_official_sample_has_ten_matches(self):
  r=json.loads((ROOT/'verification_evidence/official_close_reconciliation.json').read_text());self.assertEqual(len(r['rows']),10);self.assertTrue(all(x['status']=='MATCH' for x in r['rows']))

class Research(unittest.TestCase):
 def test_fingerprint_ignores_windows_line_endings(self):
  import research.provenance as P
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);p=root/'sample.py';p.write_bytes(b'a=1\nb=2\n')
   with patch.object(P,'ROOT',root),patch.object(P,'FILES',('sample.py',)):
    a=P.pipeline_fingerprint();p.write_bytes(b'a=1\r\nb=2\r\n');self.assertEqual(a,P.pipeline_fingerprint());p.write_bytes(b'a=3\r\nb=2\r\n');self.assertNotEqual(a,P.pipeline_fingerprint())
 def test_current_exchange_specific_fingerprints(self):
  for ex in ('NSE','BSE'):
   p=A.ml_study_payload(ex);self.assertTrue(p['ready']);self.assertFalse(p['rebuild_required']);self.assertEqual(p['exchange'],ex);self.assertFalse(p['execution_validated']);self.assertIn('NOT the deployed',p['model_scope']);self.assertEqual(len(p['data_manifest']),6)
 def test_wrong_fingerprint_is_archived(self):
  d=json.loads((ROOT/'ml_edge_study.json').read_text());d['pipeline_fingerprint']='wrong'
  with patch.object(A,'load_ml_study',return_value=d):self.assertTrue(A.ml_study_payload()['rebuild_required'])
 def test_net_cost_study_ran_current_code_without_execution_claim(self):
  d=json.loads((ROOT/'reports/study_results.json').read_text());self.assertEqual(d['pipeline_fingerprint'],pipeline_fingerprint());self.assertFalse(d['execution_validated']);self.assertEqual(len(d['per_symbol']),6);self.assertEqual(d['config']['exec_lag'],1)

if __name__=='__main__':unittest.main(verbosity=2)
