"""On-demand descriptive rank, deliberately NOT a pooled fit or trading signal.

250 trailing-window scores strictly BEFORE the reference bar. The current
score uses the same raw 250-bar window. No learned returns, probability,
p80/p95 action thresholds, or execution permissions are inferred here.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import date
import hashlib
import threading
import pandas as pd
import score_calibration as C
from safety import valid_ohlcv

MODEL = 'own-history-daily4-v1'
MIN_BARS = C.LOOKBACK_BARS + C.WINDOW_SESSIONS
_CACHE = OrderedDict()
_LOCK = threading.Lock()
_BUILD = threading.BoundedSemaphore(1)
MAX_CACHE = 64


def describe(frame, *, symbol, exchange, source, formula_hash, engines,
             current_day, fresh=True):
    """Bounded synchronous first build; later identical inputs use an LRU cache.

    Call ONLY with requested-exchange, completed daily bars. The provider is
    responsible for holiday completeness; this function never fills a gap.
    Busy responses are retryable on dashboard refresh, not background promises.
    """
    base = dict(ready=False, model=MODEL, scope='own_history', symbol=symbol,
                exchange=exchange, source=source, bars_required=MIN_BARS,
                bars_available=len(frame) if frame is not None else 0,
                sessions=0, samples=0, relative_rank_pct=None,
                execution_validated=False, directional_signal=False,
                note='Own-history percentile is not cross-stock rank, win probability or evidence of an edge. Overlapping windows are not independent trials. Provider corporate-action conventions are not independently reconciled.')
    def stop(status, reason):
        return {**base, 'status': status, 'reason': reason}
    if exchange not in ('NSE', 'BSE') or not source or not formula_hash:
        return stop('INVALID_INPUT', 'Exchange, source and formula provenance required.')
    if not fresh:
        return stop('STALE', 'Refresh requested-exchange history; stale data cannot obtain a current rank.')
    if not valid_ohlcv(frame):
        return stop('INVALID_HISTORY', 'OHLCV must be finite, ordered, unique and geometrically valid.')
    if frame.attrs.get('exchange') not in (None, exchange):
        return stop('INVALID_INPUT', 'Frame exchange conflicts with requested exchange.')
    df = frame[['Open', 'High', 'Low', 'Close', 'Volume']].tail(MIN_BARS).copy()
    try:
        dates = [pd.Timestamp(x).date() for x in df.index]
        today = date.fromisoformat(current_day)
        specials = C.verified_special_sessions()
        if len(set(dates)) != len(dates):
            return stop('INVALID_HISTORY', 'Daily session dates must be unique.')
        if any(d.weekday() >= 5 and d.isoformat() not in specials for d in dates):
            return stop('INVALID_HISTORY', 'Unverified weekend session; no synthetic backfill permitted.')
        if dates[-1] > today or (today - dates[-1]).days > C.MAX_AGE_CALENDAR_DAYS:
            return stop('STALE', 'Reference session is future-dated or stale.')
    except (TypeError, ValueError, OverflowError):
        return stop('INVALID_HISTORY', 'Invalid daily session dates.')
    base['reference_session'] = dates[-1].isoformat()
    if len(df) < MIN_BARS:
        return stop('INSUFFICIENT_HISTORY', f'{len(frame)}/{MIN_BARS} completed daily bars; do not pad history. 300 bars do not supply 250 prior 250-bar score windows.')
    # Exactly 500 inputs, 250 past windows and one current window; no future bar.
    df = df.tail(MIN_BARS)
    if (dates[-1] - dates[-2]).days > C.MAX_AGE_CALENDAR_DAYS:
        return stop('STALE', 'Historical score sample ends too far before reference.')
    content_hash = hashlib.sha256(df.to_csv(lineterminator='\n').encode()).hexdigest()
    key = (MODEL, symbol, exchange, source, formula_hash, C.SESSION_POLICY, content_hash)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return {**deepcopy(cached), 'bars_available':len(frame), 'cache_hit':True}
    if not _BUILD.acquire(blocking=False):
        return stop('BUSY', 'Another own-history build is running; refresh to retry. No unbounded job queue.')
    try:
        history = []
        for end in range(C.LOOKBACK_BARS, len(df)):
            window = df.iloc[end-C.LOOKBACK_BARS:end]
            result = C.stock_rank([fn(window.copy()) for fn in engines])
            if not result['complete']:
                return stop('INCOMPLETE_ENGINES', 'A historical daily engine is degraded; no partial fit accepted.')
            history.append({'session':pd.Timestamp(window.index[-1]).date().isoformat(), 'score':result['score']})
        current = C.stock_rank([fn(df.tail(C.LOOKBACK_BARS).copy()) for fn in engines])
        if not current['complete']:
            return stop('INCOMPLETE_ENGINES', 'Reference engines are incomplete.')
        values = sorted(r['score'] for r in history)
        result = {**base, 'ready':True, 'status':'READY', 'sessions':len(history),
                  'samples':len(values), 'score':current['score'],
                  'relative_rank_pct':C.percentile_rank(values,current['score']),
                  'asof_session':history[-1]['session'], 'first_session':history[0]['session'],
                  'history':history, 'input_sha256':content_hash,
                  'formula_hash':formula_hash, 'cache_hit':False,
                  'reason':'Descriptive own-history rank only; pooled action bands remain unavailable.'}
        with _LOCK:
            _CACHE[key] = deepcopy(result)
            _CACHE.move_to_end(key)
            while len(_CACHE) > MAX_CACHE:
                _CACHE.popitem(last=False)
        return result
    except Exception as exc:
        return stop('BUILD_FAILED', f'Own-history build failed ({type(exc).__name__}); no rank granted.')
    finally:
        _BUILD.release()
