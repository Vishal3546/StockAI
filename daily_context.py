"""FIX108 shared completed-daily boundary for CLI scanner and deep analysis.
No live-price, intraday, execution or corporate-action certification implied.
"""
from pathlib import Path
import os, json, tempfile
import pandas as pd
from safety import valid_ohlcv, frame_digest
_HOST = None
ROOT = Path(__file__).resolve().parent

def bind(host):
    global _HOST
    _HOST = host

def host():
    if _HOST is not None: return _HOST
    import app
    return app

def completed(df, exchange='NSE', now=None):
    if df is None or df.empty: raise ValueError('No daily OHLCV')
    out=df.copy()
    idx=pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is not None: idx=idx.tz_convert('Asia/Kolkata').tz_localize(None)
    out.index=idx.normalize()
    if out.index.has_duplicates or not out.index.is_monotonic_increasing:
        raise ValueError('Duplicate or unordered daily sessions')
    if not valid_ohlcv(out): raise ValueError('Invalid OHLCV geometry/schema')
    last=host().last_completed_session(now, exchange=exchange)
    out=out[out.index.date<=last].copy()
    if out.empty: raise ValueError('No completed daily sessions')
    out.attrs.update(df.attrs)
    out.attrs.update(exchange=exchange,interval='1d',as_of=str(out.index[-1].date()),
        expected_session=str(last),historical_only=out.index[-1].date()<last,input_hash=frame_digest(out))
    return out

def fetch(symbol, period='2y', exchange='NSE', now=None):
    if period not in ('1mo','3mo','6mo','1y','2y','5y'): raise ValueError('Unsupported daily period')
    if exchange not in ('NSE','BSE'): raise ValueError('Invalid exchange')
    a=host();sym=a.resolve_symbol(a.checked_symbol(symbol,exchange))
    n={'1mo':60,'3mo':100,'6mo':180,'1y':300,'2y':500,'5y':1300}[period]
    df,source=a.DATA_MANAGER.smart_fetch(sym,period=period,interval='1d',n_bars=n,
        prefer_exch=exchange,strict_exch=True,_now=now)
    out=completed(df,exchange,now)
    days={'1mo':31,'3mo':92,'6mo':183,'1y':365,'2y':735,'5y':1826}[period]
    out=out[out.index>=out.index[-1]-pd.Timedelta(days=days)].copy()
    out.attrs.update(source=source,input_hash=frame_digest(out),requested_period=period,
        adjustment=out.attrs.get('adjustment','Provider convention; not independently reconciled'))
    return out

def atomic_json(path, value):
    from nifty_scanner import _nan_safe
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(_nan_safe(value),f,indent=2,allow_nan=False)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
