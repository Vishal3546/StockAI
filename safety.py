"""FIX-100 boundary validation and cache identity; no network or Flask dependency."""
import hashlib
import math
import pandas as pd

def frame_digest(df):
    cols = [c for c in ('Open','High','Low','Close','Volume') if c in df.columns]
    data = df[cols]
    h = hashlib.sha256('|'.join(cols).encode())
    h.update(pd.util.hash_pandas_object(data, index=True).values.tobytes())
    return h.hexdigest()

def validate_body(body):
    if body is None:
        return None  # endpoints without body (refresh/check) are valid
    if not isinstance(body, dict):
        return 'JSON object required'
    for key in ('entry','stop','exit','level','qty'):
        raw = body.get(key)
        if raw in (None, ''):
            continue
        if isinstance(raw, bool):
            return key + ' must be a number, not boolean'
        try:
            v = float(raw)
        except (ValueError, TypeError, OverflowError):
            return key + ' must be numeric'
        if not math.isfinite(v) or not 0 < v <= 1e9:
            return key + ' must be finite, positive and <= 1e9'
        if key == 'qty' and not v.is_integer():
            return 'qty must be a whole number'
    if 'exchange' in body and str(body['exchange']).upper() not in ('NSE','BSE'):
        return 'exchange must be NSE or BSE'
    if 'mode' in body and body['mode'] not in ('intraday','delivery'):
        return 'mode must be intraday or delivery'
    return None

def valid_ohlcv(df):
    try:
        import numpy as np
        if df is None or df.empty or not df.index.is_unique or not df.index.is_monotonic_increasing:
            return False
        cols=['Open','High','Low','Close','Volume']
        a=df[cols].astype(float)
        return bool(np.isfinite(a.values).all() and (a[['Open','High','Low','Close']]>0).all().all()
                    and (a.Volume>=0).all() and (a.High>=a[['Open','Close','Low']].max(axis=1)).all()
                    and (a.Low<=a[['Open','Close','High']].min(axis=1)).all())
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def checked_symbol(symbol, exchange):
    """Reject qualified identities that contradict the requested exchange."""
    ex = str(exchange).strip().upper()
    sym = str(symbol).strip().upper()
    if ':' in sym:
        prefix, sym = sym.split(':', 1)
        if prefix != ex or ':' in sym:
            raise ValueError('symbol prefix conflicts with requested exchange')
    for suffix, actual in (('.NS','NSE'),('.BO','BSE')):
        if sym.endswith(suffix):
            if actual != ex:
                raise ValueError('symbol suffix conflicts with requested exchange')
            sym = sym[:-len(suffix)]
    if not sym:
        raise ValueError('symbol required')
    return sym
