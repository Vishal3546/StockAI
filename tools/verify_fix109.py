"""Isolated synthetic universe/job correctness, not stock-profitability evidence."""
import os,sys,pathlib,tempfile,unittest,threading,json,subprocess
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.update(STOCKAI_OFFLINE='1',STOCKAI_SCAN_AUTOREFRESH='0',STOCKAI_AUTO_OPEN='0')
from types import SimpleNamespace
from datetime import date
from unittest.mock import patch
from market_scanner import MarketScanner,Lease,Busy

def fake(item):
 return dict(symbol=item['symbol'],exchange=item['exchange'],price=100,signal='UNRATED',signal_score=50,
   analysis_status='OK',data_as_of='2026-10-09',historical_only=False,execution_authorized=False)

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.host=SimpleNamespace(DYNAMIC_STOCK_DB=[dict(sym=f'S{i:04}',name=f'Company {i}',ex='NSE',sec='Test') for i in range(121)]+[dict(sym='S0000',name='Company 0',ex='BSE',sec='Test')],_scan_lock=threading.Lock(),_SCAN_STATE={'running':False},last_completed_session=lambda **kw:date(2026,10,9))
  self.service=MarketScanner(self.host,pathlib.Path(self.tmp.name)/'jobs.sqlite',fake,delay=0,budget=100)
 def test_all_listing_pagination_not_30_cap(self):
  pages=[self.service.page({'limit':50,'offset':i}) for i in (0,50,100)]
  self.assertEqual(pages[0]['total'],122);self.assertEqual(sum(len(p['rows']) for p in pages),122)
  self.assertTrue(all(r['price'] is None for p in pages for r in p['rows']))
 def test_same_symbol_two_exchanges(self):
  p=self.service.page({'q':'S0000'});self.assertEqual({r['exchange'] for r in p['rows']},{'NSE','BSE'})
 def test_company_search(self):self.assertEqual(self.service.page({'q':'Company 120'})['matched'],1)
 def test_unknown_not_invented(self):self.assertEqual(self.service.page({'q':'NOTREAL'})['matched'],0)
 def test_wildcards_literal(self):self.assertEqual(self.service.page({'q':'%'})['matched'],0)
 def test_no_master_prices_used(self):
  self.host.DYNAMIC_STOCK_DB[0]['price']=999;self.assertIsNone(self.service.page({'q':'S0000'})['rows'][0]['price'])
 def test_full_queue_completes_more_than_30(self):
  self.service.start(background=False);s=self.service.status();self.assertEqual(s['processed'],122);self.assertEqual(s['state'],'COMPLETED');self.assertEqual(s['remaining'],0)
 def test_exchange_scope(self):
  self.service.start('BSE',background=False);self.assertEqual(self.service.status()['processed'],1)
  self.assertEqual(self.service.page({'scope':'NSE'})['states'],{'PENDING':121})
 def test_pause_resume_checkpoint(self):
  def f(item):
   if item['symbol']=='S0002':self.service.pause()
   return fake(item)
  self.service.analyze=f;self.service.start(background=False);s=self.service.status();self.assertEqual(s['state'],'PAUSED');self.assertLess(s['processed'],122)
  self.service.analyze=fake;self.service.start(resume=True,background=False);self.assertEqual(self.service.status()['processed'],122)
 def test_time_budget_not_universe_cap(self):
  self.service.budget=0;self.service.start(background=False);self.assertEqual(self.service.status()['state'],'PAUSED');self.assertEqual(self.service.status()['remaining'],122)
 def test_repeated_failure_circuit_breaker(self):
  def fail(item):raise RuntimeError('fixture failure')
  self.service.analyze=fail;self.service.start(background=False);s=self.service.status();self.assertEqual(s['errors'],10);self.assertEqual(s['state'],'PAUSED')
 def test_session_rollover_requires_restart(self):
  self.service.budget=0;self.service.start(background=False);self.host.last_completed_session=lambda **kw:date(2026,10,12)
  with self.assertRaisesRegex(ValueError,'session changed'):self.service.start(resume=True,background=False)
 def test_new_session_result_not_saved(self):
  self.service.analyze=lambda item:{**fake(item),'data_as_of':'2026-10-12'};self.service.start(background=False)
  self.assertEqual(self.service.status()['state'],'EXPIRED');self.assertEqual(self.service.status()['processed'],0)
 def test_wrong_exchange_rejected(self):
  self.service.analyze=lambda item:{**fake(item),'exchange':'OTHER'};self.service.start(background=False);self.assertEqual(self.service.status()['errors'],10)
 def test_stale_rank_withheld(self):
  self.service.analyze=lambda item:{**fake(item),'analysis_status':'HISTORICAL_ONLY','historical_only':True};self.service.start('BSE',background=False)
  self.assertIsNone(self.service.page({'scope':'BSE'})['rows'][0]['signal_score'])
 def test_worker_process_lease(self):
  with Lease(self.service.lease_path):
   with self.assertRaises(Busy):self.service.start()
   code="from market_scanner import Lease,Busy\nimport sys\ntry: Lease(sys.argv[1])\nexcept Busy: sys.exit(7)"
   child=subprocess.run([sys.executable,'-c',code,str(self.service.lease_path)],capture_output=True)
   self.assertEqual(child.returncode,7,child.stderr)
 def test_interrupted_job_recovers_to_paused(self):
  self.service.budget=0;self.service.start(background=False)
  with self.service.db() as c:c.execute("UPDATE job SET state='RUNNING'")
  self.assertEqual(self.service.status()['state'],'PAUSED')
 def test_same_length_master_revision(self):
  self.service.sync();self.host.DYNAMIC_STOCK_DB[0]['name']='RENAMED'
  self.assertEqual(self.service.page({'q':'RENAMED'})['matched'],1)
 def test_removed_master_not_current_listing(self):
  self.service.sync();self.host.DYNAMIC_STOCK_DB.pop();self.assertEqual(self.service.page({})['total'],121)
 def test_invalid_filters(self):
  for args in ({'limit':1000},{'offset':-1},{'scope':'NYSE'},{'min_score':'NaN'},{'limit':'abc'}):
   with self.subTest(args=args),self.assertRaises(ValueError):self.service.page(args)
 def test_outside_cohort_no_bands(self):
  import nifty_scanner as S
  from tools.verify_fix108 import frame
  f=frame();f.attrs.update(historical_only=False,as_of='2026-10-09')
  self.assertEqual(S.analyze_frame(f,'TORNTPHARM','NSE')['signal'],'UNRATED')
  self.assertEqual(S.analyze_frame(f,'INFY','BSE')['signal'],'UNRATED')
 def test_api_auth_and_validation(self):
  import app as A
  c=A.app.test_client()
  with patch.dict(A.SECURITY,{'TOKEN':'fix109','RATE_LIMIT_PER_MIN':100000}):self.assertEqual(c.get('/api/screener/universe').status_code,401)
  with patch.dict(A.SECURITY,{'TOKEN':'','RATE_LIMIT_PER_MIN':100000}):
   self.assertEqual(c.get('/api/screener/universe?limit=101').status_code,400)
   self.assertEqual(c.post('/api/screener/universe/scan',json={'action':'invalid'}).status_code,400)
 def test_master_fallback_blocks_nse_full_scan(self):
  self.host._fallback_master_list=lambda:list(self.host.DYNAMIC_STOCK_DB)
  with self.assertRaisesRegex(ValueError,'master not loaded'):self.service.start('NSE',background=False)
 def test_master_change_disclosed(self):
  self.service.budget=0;self.service.start(background=False);self.host.DYNAMIC_STOCK_DB.append(dict(sym='NEW',name='New',ex='NSE'))
  self.assertTrue(self.service.status()['master_changed'])
 def test_pagination_clamps_after_removal(self):
  r=self.service.page({'offset':500,'limit':50});self.assertEqual(r['offset'],100);self.assertEqual(len(r['rows']),22)
 def test_interrupt_not_false_completion(self):
  def interrupt(item):raise KeyboardInterrupt()
  self.service.analyze=interrupt
  with self.assertRaises(KeyboardInterrupt):self.service.start(background=False)
  self.assertEqual(self.service.status()['state'],'ERROR');self.assertEqual(self.service.status()['remaining'],122)
 def test_zero_volume_no_rank(self):
  from market_scanner import listing_analysis
  from tools.verify_fix108 import frame
  import daily_context
  f=frame();f.iloc[-1,f.columns.get_loc('Volume')]=0;f.attrs.update(historical_only=False,as_of='2026-10-09')
  with patch.object(daily_context,'fetch',return_value=f):r=listing_analysis(dict(symbol='INFY',exchange='NSE',sector='IT'))
  self.assertEqual(r['analysis_status'],'NO_RECENT_VOLUME');self.assertIsNone(r['signal_score'])
 def test_completed_results_age_on_read(self):
  self.service.start(background=False);self.host.last_completed_session=lambda **kw:date(2026,10,12)
  r=self.service.page({});self.assertEqual(r['states'],{'HISTORICAL_ONLY':122})
  self.assertTrue(all(x['signal_score'] is None and x['historical_only'] for x in r['rows']))
  self.assertTrue(self.service.status()['session_changed'])
 def test_api_start_pause_busy(self):
  import app as A
  c=A.app.test_client()
  with patch.dict(A.SECURITY,{'TOKEN':'','RATE_LIMIT_PER_MIN':100000}),patch.object(A.MARKET_SCANNER,'start') as start:
   self.assertEqual(c.post('/api/screener/universe/scan',json={'action':'start','scope':'BSE'}).status_code,202)
   start.assert_called_once_with('BSE',resume=False)
   start.side_effect=Busy('already running')
   self.assertEqual(c.post('/api/screener/universe/scan',json={'action':'start','scope':'BSE'}).status_code,409)
  with patch.dict(A.SECURITY,{'TOKEN':'','RATE_LIMIT_PER_MIN':100000}),patch.object(A.MARKET_SCANNER,'pause') as pause:
   self.assertEqual(c.post('/api/screener/universe/scan',json={'action':'pause'}).status_code,200);pause.assert_called_once()
if __name__=='__main__':unittest.main(verbosity=2)
