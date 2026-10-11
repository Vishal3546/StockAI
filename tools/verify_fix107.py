"""No network: fee arithmetic, monotone integer sizing, context and authenticated routes."""
import os,sys,pathlib,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.update(STOCKAI_OFFLINE='1',STOCKAI_SCAN_AUTOREFRESH='0',STOCKAI_AUTO_OPEN='0')
from decimal import Decimal as D
import pandas as pd
from unittest.mock import patch
from manual_planner import plan,fees,context_from_frame

def base(**kw):
    return dict(mode='intraday',direction='long',exchange='NSE',capital=100000,risk_rupees=1000,entry=100,stop=98,target=105,slippage_bps=5,**kw)
def bars(index):
    return pd.DataFrame({'Open':100.,'High':102.,'Low':98.,'Close':101.,'Volume':10.},index=index)
class Test107(unittest.TestCase):
    def test_brokerage_min(self):self.assertEqual(fees(D(1000),1,'buy','intraday')['brokerage'],D(5))
    def test_brokerage_max(self):self.assertEqual(fees(D(1000),100,'buy','intraday')['brokerage'],D(20))
    def test_regulatory_cap(self):self.assertEqual(fees(D(100),1,'buy','intraday')['brokerage'],D('2.5'))
    def test_penny_cap(self):self.assertEqual(fees(D(1),1,'buy','intraday')['brokerage'],D('.25'))
    def test_fee_hand_calculation(self):
        f=fees(D(1000),100,'buy','intraday')
        self.assertEqual(f['transaction'],D('3.0699'));self.assertEqual(f['ipft'],D('.0001'))
        self.assertEqual(f['sebi'],D('.1'));self.assertEqual(f['stamp'],D(3));self.assertEqual(f['stt'],0)
        self.assertEqual(f['gst'],D('4.1706'));self.assertEqual(f['total'],D('31.3406'))
    def test_sell_stt(self):self.assertEqual(fees(D(1000),100,'sell','intraday')['stt'],D(25))
    def test_delivery(self):
        b=fees(D(1000),100,'buy','delivery');s=fees(D(1000),100,'sell','delivery')
        self.assertEqual(b['stt'],100);self.assertEqual(s['stt'],100);self.assertEqual(b['stamp'],15)
        self.assertEqual(s['dp'],20);self.assertEqual(b['dp'],0);self.assertEqual(s['gst']-b['gst'],D('3.60'))
    def test_zero_qty_no_fees(self):self.assertTrue(all(v==0 for v in fees(D(100),0,'sell','delivery').values()))
    def test_positive_manual_without_edge(self):
        p=plan(base());self.assertGreater(p['quantity_estimate'],0);self.assertEqual(p['executable_quantity'],0);self.assertFalse(p['execution_authorized'])
    def test_budget_constraints_and_largest_integer(self):
        # Compare binary search to independently enumerated direct arithmetic calls.
        for mode in ('delivery','intraday'):
            for direction in ('long','short'):
                if mode=='delivery' and direction=='short':continue
                for capital,risk in [(1000,25),(10000,50),(10000,1000),(.01,.01)]:
                    x=base();x.update(mode=mode,direction=direction,capital=capital,risk_rupees=risk)
                    if direction=='short':x.update(stop=102,target=95)
                    p=plan(x);long=direction=='long';e=D(100)*(D('1.0005') if long else D('.9995'));s=D(str(x['stop']))*(D('.9995') if long else D('1.0005'))
                    feasible=[]
                    for q in range(int(D(str(capital))/e)+1):
                        fi=fees(e,q,'buy' if long else 'sell',mode)['total'];fo=fees(s,q,'sell' if long else 'buy',mode)['total']
                        loss=(e-s)*q*(1 if long else -1)+fi+fo
                        if loss<=D(str(risk)) and e*q+fi<=D(str(capital)):feasible.append(q)
                    self.assertEqual(p['quantity_estimate'],max(feasible))
    def test_more_slippage_not_more_qty(self):
        x=base();a=plan(x);x['slippage_bps']=100;self.assertLessEqual(plan(x)['quantity_estimate'],a['quantity_estimate'])
    def test_no_share_fits(self):
        x=base();x.update(capital=1,risk_rupees=.01);p=plan(x);self.assertEqual(p['quantity_estimate'],0);self.assertIsNone(p['net_reward_risk'])
    def test_invalid_numbers(self):
        for key in ('capital','risk_rupees','entry','stop','target','slippage_bps'):
            for value in (True,None,'NaN','Infinity','-Infinity',{},'bad',-1,1e100):
                x=base();x[key]=value
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):plan(x)
    def test_invalid_body(self):
        for value in (None,[],1,'abc'):
            with self.assertRaises(ValueError):plan(value)
    def test_bse_not_nse(self):
        x=base();x['exchange']='BSE'
        with self.assertRaisesRegex(ValueError,'NSE rates will not'):plan(x)
    def test_overnight_short_blocked(self):
        x=base();x.update(mode='delivery',direction='short')
        with self.assertRaisesRegex(ValueError,'overnight'):plan(x)
    def test_wrong_stop_target(self):
        for key,value in [('stop',100),('stop',105),('target',99)]:
            x=base();x[key]=value
            with self.assertRaises(ValueError):plan(x)
    def test_risk_exceeds_capital(self):
        x=base();x['risk_rupees']=100001
        with self.assertRaises(ValueError):plan(x)
    def test_intraday_session_reset_and_partial(self):
        df=bars(pd.to_datetime(['2026-10-08 15:20','2026-10-08 15:25','2026-10-09 09:15','2026-10-09 09:20','2026-10-09 09:25']))
        df.iloc[:2,4]=999999
        c=context_from_frame(df,'intraday','2026-10-09 09:27')
        self.assertEqual(c['bars'],2);self.assertTrue(c['starts_at_0915']);self.assertAlmostEqual(c['vwap_proxy'],301/3)
        self.assertEqual(c['minutes_since_bar_end'],2)
    def test_zero_volume(self):
        df=bars(pd.date_range('2026-10-09 09:15',periods=3,freq='5min'));df.Volume=0
        self.assertIsNone(context_from_frame(df,'intraday','2026-10-09 10:00')['vwap_proxy'])
    def test_missing_slots(self):
        df=bars(pd.to_datetime(['2026-10-09 09:20','2026-10-09 09:25','2026-10-09 09:35']))
        c=context_from_frame(df,'intraday','2026-10-09 10:00');self.assertEqual(c['missing_slots_between_endpoints'],1);self.assertFalse(c['starts_at_0915'])
    def test_no_daily_intraday_relabel(self):
        with self.assertRaisesRegex(ValueError,'genuine 5-minute'):context_from_frame(bars(pd.date_range('2026-10-01',periods=5)),'intraday','2026-10-11')
    def test_utc_converted(self):
        df=bars(pd.date_range('2026-10-09 03:45',periods=2,freq='5min',tz='UTC'))
        self.assertTrue(context_from_frame(df,'intraday','2026-10-09 10:00')['starts_at_0915'])
    def test_duplicates(self):
        df=bars(pd.to_datetime(['2026-10-09 09:15']*2))
        with self.assertRaises(ValueError):context_from_frame(df,'intraday','2026-10-09 10:00')
    def test_invalid_ohlcv(self):
        df=bars(pd.date_range('2026-10-09 09:15',periods=3,freq='5min'));df.iloc[0,1]=1
        with self.assertRaises(ValueError):context_from_frame(df,'intraday','2026-10-09 10:00')
    def test_daily_no_fake_sma(self):
        c=context_from_frame(bars(pd.date_range('2026-10-01',periods=5)),'delivery','2026-10-11')
        self.assertIsNone(c['sma20']);self.assertNotIn('vwap_proxy',c)
    def test_real_app_routes_and_auth(self):
        import app as A
        client=A.app.test_client()
        with patch.dict(A.SECURITY,{'TOKEN':'test107','RATE_LIMIT_PER_MIN':100000}):
            self.assertEqual(client.post('/api/manual-plan',json=base()).status_code,401)
            self.assertEqual(client.get('/manual-planner').status_code,401)
            self.assertEqual(client.get('/manual-planner',headers={'X-Api-Key':'test107'}).status_code,200)
            r=client.post('/api/manual-plan',json=base(),headers={'X-Api-Key':'test107'});self.assertEqual(r.status_code,200,r.json)
            self.assertEqual(r.json['status'],'HYPOTHETICAL_ONLY')
        with patch.dict(A.SECURITY,{'TOKEN':'','RATE_LIMIT_PER_MIN':100000}),patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(bars(pd.date_range('2026-10-09 09:15',periods=3,freq='5min')),'fixture NSE')) as fetch:
            r=client.get('/api/manual-context/INFY?mode=intraday&exchange=NSE');self.assertEqual(r.status_code,200,r.json)
            self.assertEqual(fetch.call_args.kwargs['interval'],'5m');self.assertTrue(fetch.call_args.kwargs['strict_exch'])
        with patch.dict(A.SECURITY,{'TOKEN':'','RATE_LIMIT_PER_MIN':100000}),patch.object(A.DATA_MANAGER,'smart_fetch',return_value=(bars(pd.date_range('2026-10-08',periods=5)),'fixture NSE')),patch.object(A,'last_completed_session',return_value=pd.Timestamp('2026-10-09').date()):
            r=client.get('/api/manual-context/INFY?mode=delivery&exchange=NSE');self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['bars'],2)

if __name__=='__main__':unittest.main(verbosity=2)
