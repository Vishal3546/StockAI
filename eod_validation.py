"""Per-field, same-session official EOD comparison; never certifies whole history."""
import json, math
from functools import lru_cache
from pathlib import Path
PATH=Path(__file__).resolve().parent/'.stockai'/'official_eod.json'
FIELDS=('Open','High','Low','Close','Volume')

@lru_cache(maxsize=2)
def _load(path,mtime,size):
    if size>8_000_000:raise ValueError('reference too large')
    d=json.loads(Path(path).read_text())
    if d.get('schema')!=1:raise ValueError('bad reference schema')
    return d

def compare(frame,symbol,exchange,path=None):
    base={'status':'UNVERIFIED','scope':'Latest completed daily OHLCV only; not intraday or complete historical/corporate-action verification',
          'symbol':symbol,'exchange':exchange,'refresh_command':'python tools/refresh_official_eod.py'}
    try:
        p=Path(path) if path else PATH;st=p.stat();d=_load(str(p),st.st_mtime_ns,st.st_size)
        if frame is None or frame.empty or exchange not in ('NSE','BSE'):raise ValueError('missing exchange/frame')
        session=str(frame.index[-1])[:10];base['session']=session
        if d['session']!=session:return {**base,'reason':'Official reference date differs; refresh reference before claiming a match.'}
        r=d['rows'].get(exchange+':'+symbol)
        if not r:return {**base,'reason':'No unique official cash row for this symbol/exchange.'}
        results={}
        for name in FIELDS:
            actual=float(frame.iloc[-1][name]);expected=float(r[name]);tol=0 if name=='Volume' else .005
            results[name]={'provider':actual,'official':expected,'status':'MATCH' if math.isfinite(actual) and abs(actual-expected)<=tol else 'MISMATCH'}
        return {**base,'status':'MATCH' if all(x['status']=='MATCH' for x in results.values()) else 'MISMATCH',
                'official_isin':r['isin'],'fields':results,'source':d['sources'][exchange]}
    except (OSError,ValueError,KeyError,TypeError,IndexError,AttributeError) as e:
        return {**base,'reason':'Official reference unavailable/invalid ('+type(e).__name__+'); not assumed correct.'}
