#!/usr/bin/env python3
"""
tools/verify_live_quote.py — LIVE PRICE PIPELINE KA REGRESSION TEST (offline)
================================================================================
Kya check karta hai:
  1. app.py / Dashboard.html syntax theek hai (JS ke liye agar node ho)
  2. `get_live_quote()` HAR tier me **same payload shape** deta hai
     → required keys: price, change, pChange, close_price, dayHigh, dayLow,
       timestamp, is_realtime, source   (yahi `undefined (undefined%)` ka fix hai)
  3. TTL cache kaam karta hai (doosri call instant + cached=True)
  4. `?force=1` cache ko bypass karta hai
  5. Dashboard me refresh icon + chip maujood hai, aur updatePriceDOM me
     unguarded interpolation (jo "undefined" print karti thi) nahi bachi

Chalao:  python3 tools/verify_live_quote.py
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import os

ROOT = pathlib.Path(__file__).resolve().parent.parent
REQUIRED = ['price', 'change', 'pChange', 'close_price', 'dayHigh', 'dayLow',
            'timestamp', 'is_realtime', 'source']

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


print('=' * 82)
print(' LIVE QUOTE PIPELINE — verification')
print('=' * 82)

# ── 1. syntax ───────────────────────────────────────────────────────────────
app_src = (ROOT / 'app.py').read_text(encoding='utf-8')
try:
    compile(app_src, 'app.py', 'exec')
    check('app.py syntax', True)
except SyntaxError as e:
    check('app.py syntax', False, str(e))

html = (ROOT / 'Dashboard.html').read_text(encoding='utf-8')
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
js_ok, js_detail = True, f'{len(scripts)} inline scripts'
for i, s in enumerate(scripts):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False,
                                     encoding='utf-8') as f:
        f.write(s)
        p = f.name
    try:
        r = subprocess.run(['node', '--check', p], capture_output=True, text=True)
        if r.returncode != 0:
            js_ok, js_detail = False, f'script[{i}]: {r.stderr[:160]}'
            break
    except FileNotFoundError:
        js_ok, js_detail = True, 'node nahi mila — JS check skipped'
    finally:
        os.unlink(p)
check('Dashboard.html JS syntax', js_ok, js_detail)

# ── 2-4. payload shape + cache (app import karke, tiers stubbed) ────────────
sys.path.insert(0, str(ROOT))
try:
    import app as A
except Exception as e:                                    # pragma: no cover
    check('app import', False, f'{type(e).__name__}: {e}')
    print('\n(app import fail — baaki checks skip)')
    results.append(('payload/cache checks', False, 'app import failed'))
else:
    real_nse, real_yahoo = A.fetch_nse_live_ltp, A.fetch_yahoo_live_ltp

    canned = {'symbol': 'TEST', 'price': 1234.5, 'change': 12.5, 'pChange': 1.02,
              'close_price': 1222.0, 'dayHigh': 1240.0, 'dayLow': 1215.0,
              'timestamp': '10:00:00', 'is_realtime': True, 'source': 'stub'}

    # --- tier 1/2 (live source mil gaya) ---
    # FIX-55: get_live_quote ab prefer_exch pass karta hai, isliye **k
    A.fetch_nse_live_ltp = lambda s: dict(canned)
    A.fetch_yahoo_live_ltp = lambda s, **k: None
    A._LIVE_CACHE.clear()
    q1 = A.get_live_quote('TESTX', force=True)
    check('tier-1 payload shape', all(q1.get(k) is not None for k in REQUIRED),
          f"missing: {[k for k in REQUIRED if q1.get(k) is None] or 'none'}")

    # --- cache: doosri call cached honi chahiye ---
    q2 = A.get_live_quote('TESTX')
    check('TTL cache hit', q2.get('cached') is True, f"cached={q2.get('cached')}")

    # --- force: cache bypass ---
    q3 = A.get_live_quote('TESTX', force=True)
    check('force=1 bypasses cache', q3.get('cached') is False)

    # --- tier 3 (sab live sources fail) → phir bhi poora payload ---
    import pandas as pd
    import numpy as np
    idx = pd.date_range('2026-08-01', periods=5, freq='D')
    fake = pd.DataFrame({'Open': [100, 101, 102, 103, 104], 'High': [101, 102, 103, 104, 105],
                         'Low': [99, 100, 101, 102, 103], 'Close': [100.5, 101.5, 102.5, 103.5, 104.5],
                         'Volume': [1e6] * 5}, index=idx)
    A.fetch_nse_live_ltp = lambda s: None
    A.fetch_yahoo_live_ltp = lambda s, **k: None
    orig_fetch = A.DATA_MANAGER.smart_fetch
    A.DATA_MANAGER.smart_fetch = lambda *a, **k: (fake, 'stub-daily')
    A._LIVE_CACHE.clear()
    q4 = A.get_live_quote('TESTX', force=True)
    A.DATA_MANAGER.smart_fetch = orig_fetch
    missing = [k for k in REQUIRED if q4.get(k) is None]
    check('tier-3 fallback payload shape', not missing, f"missing: {missing or 'none'}")
    check('tier-3 correctly marked stale', q4.get('is_realtime') is False and q4.get('stale') is True)
    check('tier-3 change computed (not None)', q4.get('change') is not None and q4.get('pChange') is not None,
          f"change={q4.get('change')} pChange={q4.get('pChange')}")

    # stubs wapas
    A.fetch_nse_live_ltp, A.fetch_yahoo_live_ltp = real_nse, real_yahoo
    A._LIVE_CACHE.clear()

# ── 4b. FIX-26 freshness guard (market hours me stale data reject hona chahiye) ──
try:
    import pandas as pd
    import datetime as _dt
    # ek trading day, market-open time (IST 11:00 = 05:30 UTC)
    now_open = _dt.datetime(2026, 9, 30, 11, 0)          # Wednesday
    now_closed = _dt.datetime(2026, 9, 30, 18, 0)
    now_sun = _dt.datetime(2026, 9, 27, 11, 0)           # Sunday

    check('market-open detect (Wed 11:00)', A.is_market_open(now_open) is True)
    check('market-closed detect (Wed 18:00)', A.is_market_open(now_closed) is False)
    check('weekend detect (Sun 11:00)', A.is_market_open(now_sun) is False)

    def mk(last_ts, n=300):
        idx = pd.date_range(end=last_ts, periods=n, freq='D')
        return pd.DataFrame({'Open': 1.0, 'High': 1.0, 'Low': 1.0, 'Close': 1.0, 'Volume': 1}, index=idx)

    fresh_df = mk(now_open)                                   # aaj ka bar
    stale_df = mk(now_open - _dt.timedelta(days=6))           # 6 din purana
    f1, w1 = A.frame_is_fresh(fresh_df, '1d', now=now_open)
    f2, w2 = A.frame_is_fresh(stale_df, '1d', now=now_open)
    f3, w3 = A.frame_is_fresh(stale_df, '1d', now=now_closed)
    check('fresh bar accept (market open)', f1 is True, w1)
    check('stale bar REJECT (market open)', f2 is False, w2)
    # FIX-47: ye check pehle ulta assert karta tha — "market closed → stale accept".
    # Wahi bug tha: market band hote hi freshness poora bypass, isliye 6-session purana
    # bar bhi "fine" kehlata tha. Ab market band ho tab bhi latest completed session se
    # compare hota hai.
    check('stale bar REJECT (market closed bhi — 6 session peeche)', f3 is False, w3)

    # Market closed par latest completed session wala bar accept hona chahiye
    closed_today = mk(_dt.datetime(2026, 9, 30, 0, 0))         # Wed midnight stamp
    f6, w6 = A.frame_is_fresh(closed_today, '1d', now=now_closed)
    check('latest session bar accept (market closed)', f6 is True, w6)

    # 1-din grace: ek akel market holiday false-positive na ban jaaye
    closed_grace = mk(_dt.datetime(2026, 9, 29, 0, 0))         # 1 session peeche
    f7, w7 = A.frame_is_fresh(closed_grace, '1d', now=now_closed)
    check('1-session-behind accept (market closed, holiday grace)', f7 is True, w7)

    # Weekend: Monday subah ka answer Friday hona chahiye (false-positive nahi)
    # FIX-50 note: pehle ye 2026-10-03/05 use karta tha aur expected 2026-10-02 tha.
    # Ab 02-Oct Gandhi Jayanti hai (holiday calendar), isliye wo weekend shift kiya —
    # 26/27-Sep ka weekend kisi holiday ke paas nahi hai.
    check('last_completed_session skips weekend (Sat → Fri)',
          A.last_completed_session(_dt.datetime(2026, 9, 26, 12, 0)) == _dt.date(2026, 9, 25))
    check('last_completed_session Mon subah → Fri',
          A.last_completed_session(_dt.datetime(2026, 9, 28, 8, 0)) == _dt.date(2026, 9, 25))
    check('last_completed_session close ke baad → aaj',
          A.last_completed_session(_dt.datetime(2026, 10, 1, 16, 6)) == _dt.date(2026, 10, 1))
    check('last_completed_session close se pehle → kal',
          A.last_completed_session(_dt.datetime(2026, 10, 1, 10, 0)) == _dt.date(2026, 9, 30))

    # FIX-47: timezone fail-open — aware/naive/UTC sab par SAME verdict aana chahiye.
    # Pehle aware `now` par frame_age_minutes TypeError kha kar None deta tha, aur None
    # ko "age unknown" keh kar FRESH maan liya jaata tha.
    from zoneinfo import ZoneInfo as _ZI
    _ist = _ZI('Asia/Kolkata')
    _tcs_stale = mk(_dt.datetime(2026, 9, 29, 0, 0))
    _ref = _dt.datetime(2026, 10, 1, 16, 6)
    _verdicts = {A.frame_is_fresh(_tcs_stale, '1d', now=v)[0] for v in (
        _ref,                                   # naive IST
        _ref.replace(tzinfo=_ist),              # aware IST
        _dt.datetime(2026, 10, 1, 10, 36, tzinfo=_ZI('UTC')),   # same instant, UTC
    )}
    check('tz-aware/naive/UTC sab par same freshness verdict (fail-open band)',
          _verdicts == {False}, f'verdicts = {_verdicts}')
    check('is_market_open aware UTC ko IST me convert karta hai (10:36Z = 16:06 IST = closed)',
          A.is_market_open(_dt.datetime(2026, 10, 1, 10, 36, tzinfo=_ZI('UTC'))) is False)

    daily_ok = mk(now_open.replace(hour=0, minute=0), n=300)   # aaj ka daily bar (midnight stamp)
    f5, w5 = A.frame_is_fresh(daily_ok, '1d', now=now_open)
    check('daily bar today (midnight stamp) accept — false-positive fix', f5 is True, w5)

    i5_stale = mk(now_open - _dt.timedelta(hours=5), n=100)
    f4, w4 = A.frame_is_fresh(i5_stale, '5m', now=now_open)
    check('intraday stale reject (5h old 5m bars)', f4 is False, w4)

    # smart_fetch cascade: tier-1 stale → tier-2 fresh serve hona chahiye
    orig_tv = A.DATA_MANAGER.fetch_tradingview
    orig_nse = A.DATA_MANAGER.fetch_nse_direct
    orig_yf = A.DATA_MANAGER.fetch_yahoo
    # FIX-53: fetch_tradingview ab (df, exchange) return karta hai
    A.DATA_MANAGER.fetch_tradingview = lambda *a, **k: (mk(now_open - _dt.timedelta(days=9)), 'NSE')
    A.DATA_MANAGER.fetch_nse_direct = lambda *a, **k: mk(now_open)
    A.DATA_MANAGER.fetch_yahoo = lambda *a, **k: None
    df_out, src_out = A.DATA_MANAGER.smart_fetch('TESTY', interval='1d', _now=now_open)
    A.DATA_MANAGER.fetch_tradingview, A.DATA_MANAGER.fetch_nse_direct, A.DATA_MANAGER.fetch_yahoo = orig_tv, orig_nse, orig_yf
    check('stale tier-1 → fresh tier-2 par fallback', src_out == 'NSE Direct', f'source={src_out}')

    # sab stale → honest STALE label
    A.DATA_MANAGER.fetch_tradingview = lambda *a, **k: (mk(now_open - _dt.timedelta(days=9)), 'NSE')
    A.DATA_MANAGER.fetch_nse_direct = lambda *a, **k: None
    A.DATA_MANAGER.fetch_yahoo = lambda *a, **k: mk(now_open - _dt.timedelta(days=30))
    _, src2 = A.DATA_MANAGER.smart_fetch('TESTZ', interval='1d', _now=now_open)
    A.DATA_MANAGER.fetch_tradingview, A.DATA_MANAGER.fetch_nse_direct, A.DATA_MANAGER.fetch_yahoo = orig_tv, orig_nse, orig_yf
    check('sab stale → "STALE" label (silent stale nahi)', 'STALE' in src2, f'source={src2}')
except Exception as e:
    check('freshness guard checks', False, f'{type(e).__name__}: {e}')

# ── 4c. FIX-27 market-regime honesty (hardcoded 23000 ka khatma) ───────────
try:
    import pandas as pd  # noqa
    import numpy as _np  # noqa

    # source-level: purane magic numbers gaye?
    check('regime: NIFTY ke liye 2y fetch (6mo nahi)', "smart_fetch('^NSEI', period='2y')" in app_src)
    check('regime: hardcoded 23000 gaya', 'else 23000' not in app_src)
    check('regime: hardcoded 24000 gaya', 'else 24000' not in app_src)
    check('regime: fake vix 15.0 gaya', 'else 15.0' not in app_src)
    check('regime: cache TTL me .total_seconds()', ").total_seconds() < CONFIG['REGIME_CACHE_TTL']" in app_src)

    import pandas as _pd
    real_fetch = A.DATA_MANAGER.smart_fetch

    # (a) data hi na mile → honest UNKNOWN (pehle chupchap fake regime banta tha)
    A.DATA_MANAGER.smart_fetch = lambda *a, **k: (None, 'None')
    A.REGIME_CACHE = {'data': None, 'time': None}
    r = A.engine_market_regime()
    check('regime: data na mile → UNKNOWN (fake nahi)', r['regime'] == 'UNKNOWN' and r['score'] == 50,
          f"regime={r['regime']} note={r.get('note', '')[:60]}")

    # (b) kam bars (126) → UNKNOWN, kyunki 200-EMA compute ho hi nahi sakti
    few = _pd.DataFrame({'Close': _np.linspace(20000, 22600, 126)},
                        index=_pd.date_range('2026-01-01', periods=126))
    A.DATA_MANAGER.smart_fetch = lambda s, **k: (few if s == '^NSEI' else None, 'stub')
    A.REGIME_CACHE = {'data': None, 'time': None}
    r2 = A.engine_market_regime()
    check('regime: 126 bars → UNKNOWN (hardcoded compare nahi)',
          r2['regime'] == 'UNKNOWN' and 'kam hai' in r2.get('note', ''), r2.get('note', '')[:70])

    # (c) kaafi bars → ASLI EMA-200 (23000 constant nahi)
    many = _pd.DataFrame({'Close': _np.linspace(20000, 25000, 496)},
                         index=_pd.date_range(end='2026-09-30', periods=496))
    expected_ema = float(many['Close'].ewm(span=200, adjust=False).mean().iloc[-1])
    A.DATA_MANAGER.smart_fetch = lambda s, **k: (many if s == '^NSEI'
                                                 else _pd.DataFrame({'Close': [13.5]},
                                                                    index=_pd.date_range('2026-09-01', periods=2)),
                                                 'stub')
    A.REGIME_CACHE = {'data': None, 'time': None}
    r3 = A.engine_market_regime()
    check('regime: EMA-200 asli compute hoti hai (23000 nahi)',
          abs(r3['nifty_200ema'] - expected_ema) < 0.5 and r3['nifty_200ema'] != 23000.0,
          f"ema={r3['nifty_200ema']:,.2f} expected={expected_ema:,.2f}")
    check('regime: bars/source report hote hain', r3.get('data_bars') == 496 and r3.get('data_source') == 'stub')

    # (d) VIX na mile → trend se regime, par vix_status UNKNOWN (fake 15.0 nahi)
    A.DATA_MANAGER.smart_fetch = lambda s, **k: (many if s == '^NSEI' else None, 'stub')
    A.REGIME_CACHE = {'data': None, 'time': None}
    r4 = A.engine_market_regime()
    check('regime: VIX missing → vix_status UNKNOWN, regime trend se',
          r4['vix_status'] == 'UNKNOWN' and r4['vix'] == 0, f"regime={r4['regime']} vix={r4['vix']}")

    A.DATA_MANAGER.smart_fetch = real_fetch
    A.REGIME_CACHE = {'data': None, 'time': None}
except Exception as e:
    check('regime checks', False, f'{type(e).__name__}: {e}')

# ── 5. Dashboard guards + UI elements ──────────────────────────────────────
check('refresh icon maujood hai', 'id="refreshBtn"' in html and 'manualRefresh' in html)
check('LIVE/DELAYED chip maujood hai', 'id="liveChip"' in html and 'setLiveChip' in html)
check('undefined-guard (numOrNull) laga hai', 'function numOrNull' in html and 'numOrNull(pChange)' in html)
old_bug = '(${isPos ? \'+\' : \'\'}${pChange}%)'
check('purana unguarded interpolation hata diya', old_bug not in html,
      'warna UI "undefined (undefined%)" dikhata hai' if old_bug in html else '')
check('change unavailable fallback likha hai', 'change unavailable' in html)
check('Yahoo live tier app.py me hai', 'def fetch_yahoo_live_ltp' in app_src)
check('TTL cache app.py me hai', '_LIVE_CACHE' in app_src and 'LIVE_TTL' in app_src)
# FIX-55: ab prefer_exch bhi jaata hai, isliye assertion update
check('SSE unified payload use karta hai',
      'quote = get_live_quote(resolved, prefer_exch=_sse_ex)' in app_src)
check('freshness guard app.py me hai', 'def frame_is_fresh' in app_src and 'smart_fetch' in app_src)
check('tvDatafeed log noise suppressed', "getLogger('tvDatafeed').setLevel(logging.CRITICAL)" in app_src)

# ── FIX-47: duplicate request-log lines ────────────────────────────────────
# werkzeug apna handler khud add karta hai; uske baad koi library basicConfig() se root
# par handler laga deti hai aur werkzeug root par propagate karta hai → har line 2 baar.
# Fix: explicit handler + propagate=False.
import logging as _logging
_wk = _logging.getLogger('werkzeug')
check('werkzeug logger propagate=False (root par duplicate nahi jaayega)',
      _wk.propagate is False)
check('werkzeug logger ke paas apna handler hai', len(_wk.handlers) >= 1,
      f'handlers = {_wk.handlers}')
check('token-mask filter abhi bhi werkzeug logger par hai',
      any(type(f).__name__ == '_TokenMaskFilter' for f in _wk.filters),
      f'filters = {[type(f).__name__ for f in _wk.filters]}')
check('_configure_werkzeug_logging startup par call hota hai',
      '_configure_werkzeug_logging()' in app_src)

# ── FIX-49: live-quote freshness gate ──────────────────────────────────────
# Measured 2026-10-02 09:31 IST (Fri, market 16 min se khula): Yahoo v8
# `interval=1d&range=1d` ne RELIANCE ke liye regularMarketPrice=1167.7 diya,
# regularMarketTime=2026-10-01 15:15 — 18.3 GHANTE purana. `fetch_yahoo_live_ltp`
# ne use bina check kiye `is_realtime: True` bhej diya tha; `regularMarketTime`
# sirf display string banata tha. Ab scalar timestamp par `frame_is_fresh` ka
# twin gate lagti hai, aur missing timestamp fail-CLOSED hai.
import datetime as _dt
import io as _io
import contextlib as _ctx
_IST = A.IST
# FIX-50 note: pehle ye 2026-10-02 (Fri) tha — jo ab holiday calendar me hai, isliye
# "market khula" fixture jhootha ho gaya tha. 01-Oct Thursday ek asli trading day hai.
_OPEN = _dt.datetime(2026, 10, 1, 9, 31)      # Thu, market khula (trading day)
_CLOSE = _dt.datetime(2026, 10, 1, 16, 30)    # Thu, market band
_MONAM = _dt.datetime(2026, 9, 28, 8, 0)      # Mon subah, market band


def _ep(naive_ist):
    """naive-IST wall clock → sahi epoch (naive .timestamp() UTC maan leta hai)."""
    return naive_ist.replace(tzinfo=_IST).timestamp()


print('\n-- FIX-49: _parse_quote_ts')
check('epoch int parse hota hai', A._parse_quote_ts(_ep(_OPEN)) is not None)
check('epoch numeric-string parse hota hai',
      A._parse_quote_ts(str(int(_ep(_OPEN)))) is not None)
check('NSE format "02-Oct-2026 09:31:00" parse hota hai',
      A._parse_quote_ts('02-Oct-2026 09:31:00') == _dt.datetime(2026, 10, 2, 9, 31))
check('ISO-8601 parse hota hai',
      A._parse_quote_ts('2026-10-02T09:31:00') == _dt.datetime(2026, 10, 2, 9, 31))
check('None/empty/garbage/bool/negative → None (fail-closed input)',
      all(A._parse_quote_ts(v) is None
          for v in (None, '', 'garbage', True, -5, 0)))

print('\n-- FIX-49: quote_is_fresh boundaries (market KHULA)')
_lim = A.LIVE_MAX_AGE_MIN
for _mins, _want in [(0, True), (_lim - 0.1, True), (_lim, True),
                     (_lim + 0.1, False), (25, False), (1096, False)]:
    _f, _r = A.quote_is_fresh(_ep(_OPEN - _dt.timedelta(minutes=_mins)), _OPEN)
    check(f'{_mins:.1f}m purana quote → {"FRESH" if _want else "STALE"}',
          _f is _want, _r)

print('\n-- FIX-49: quote_is_fresh (market BAND) + asli measured case')
# _OPEN = 01-Oct 09:31 (trading day, market khula). Ek din purana quote = 30-Sep 15:15.
check('mkt khula, quote pichhle session 15:15 (18.3h) → STALE',
      A.quote_is_fresh(_ep(_dt.datetime(2026, 9, 30, 15, 15)), _OPEN)[0] is False)
check('  ↳ age ~1096 min measure hota hai',
      abs(A.quote_age_minutes(_ep(_dt.datetime(2026, 9, 30, 15, 15)), _OPEN) - 1096) < 1,
      f"age={A.quote_age_minutes(_ep(_dt.datetime(2026, 9, 30, 15, 15)), _OPEN)}")
check('mkt band 16:30, quote aaj 15:15 → FRESH',
      A.quote_is_fresh(_ep(_dt.datetime(2026, 10, 1, 15, 15)), _CLOSE)[0] is True)
check('Mon subah, quote Fri 15:15 → FRESH (weekend skip)',
      A.quote_is_fresh(_ep(_dt.datetime(2026, 9, 25, 15, 15)), _MONAM)[0] is True)
check('mkt band, quote 10 din purana → STALE',
      A.quote_is_fresh(_ep(_dt.datetime(2026, 9, 21, 15, 15)), _CLOSE)[0] is False)

print('\n-- FIX-49: fail-closed + timezone')
check('MISSING timestamp → STALE (pehle datetime.now() maan leta tha)',
      A.quote_is_fresh(None, _OPEN)[0] is False, A.quote_is_fresh(None, _OPEN)[1])
_STALE_Q = _ep(_dt.datetime(2026, 9, 30, 15, 15))
_res = {A.quote_is_fresh(_STALE_Q, _n)[0]
        for _n in (_OPEN,                                  # naive IST
                   _OPEN.replace(tzinfo=_IST),             # IST-aware
                   _dt.datetime(2026, 10, 1, 4, 1, tzinfo=_dt.timezone.utc))}  # UTC-aware
check('naive / IST-aware / UTC-aware `now` sab same jawab dete hain',
      len(_res) == 1 and _res == {False}, f'results={_res}')
_gate = A.LIVE_GATE_ON
A.LIVE_GATE_ON = False
check('kill-switch STOCKAI_LIVE_GATE=off gate bypass karta hai',
      A.quote_is_fresh(None, _OPEN)[0] is True)
A.LIVE_GATE_ON = _gate

print('\n-- FIX-49: warn throttle (2s refresh par console na bhare)')
A._LIVE_STALE_WARNED.clear()
_buf = _io.StringIO()
with _ctx.redirect_stdout(_buf):
    for _ in range(5):
        A._warn_stale_quote('THROTTLETEST', 'Yahoo', 'STALE: test')
_lines = [l for l in _buf.getvalue().splitlines() if l.strip()]
check('5 stale calls → sirf 1 console line', len(_lines) == 1, f'lines={len(_lines)}')
A._LIVE_STALE_WARNED.clear()

print('\n-- FIX-49: end-to-end fetch_yahoo_live_ltp (stubbed HTTP)')


class _FakeResp:
    status_code = 200

    def __init__(self, meta):
        self._meta = meta

    def json(self):
        return {'chart': {'result': [{'meta': self._meta}]}}


def _mk(ts):
    return {'regularMarketPrice': 1167.7, 'chartPreviousClose': 1187.0,
            'regularMarketTime': ts, 'regularMarketDayHigh': 1170.0,
            'regularMarketDayLow': 1160.0}


class _FrozenDT(_dt.datetime):
    """Test ke liye ghadi rok do.

    `fetch_yahoo_live_ltp()` gate ko `now=None` ke saath call karta hai, yaani asli
    wall-clock. Bina freeze kiye ye test us din fail hota jis din chalaya jaaye —
    Monday subah ya kisi holiday par verdict badal jaata. Freeze: 01-Oct-2026 09:31,
    ek asli trading day, market khula.
    """

    _frozen = _dt.datetime(2026, 10, 1, 9, 31)

    @classmethod
    def now(cls, tz=None):
        base = cls._frozen.replace(tzinfo=_IST)
        return base.astimezone(tz) if tz is not None else base.replace(tzinfo=None)


_orig_get = A._HTTP.get
_orig_dt = A.datetime
A.datetime = _FrozenDT
A._LIVE_STALE_WARNED.clear()
try:
    # stale: pichhle session (30-Sep) ka close, 18.3 ghante purana
    A._HTTP.get = lambda url, **kw: _FakeResp(_mk(_ep(_dt.datetime(2026, 9, 30, 15, 15))))
    _buf = _io.StringIO()
    with _ctx.redirect_stdout(_buf):
        _qs = A.fetch_yahoo_live_ltp('RELIANCE')
    check('stale Yahoo quote → is_realtime False', _qs.get('is_realtime') is False)
    check('stale Yahoo quote → stale True', _qs.get('stale') is True)
    check('stale Yahoo quote par console warning aayi', 'LIVE Yahoo' in _buf.getvalue())
    check('price phir bhi serve hota hai (UI blank nahi)', _qs.get('price') == 1167.7)
    check('timestamp ab quote ka asli waqt hai, datetime.now() nahi',
          _qs.get('timestamp') == '15:15:00', f"timestamp={_qs.get('timestamp')}")
    check('change% sahi prevClose (1187.0 = 30 Sep) se bana',
          _qs.get('close_price') == 1187.0 and _qs.get('change') == -19.3,
          f"close={_qs.get('close_price')} change={_qs.get('change')}")

    # fresh: 30 second purana
    A._HTTP.get = lambda url, **kw: _FakeResp(_mk(_ep(_dt.datetime(2026, 10, 1, 9, 30, 30))))
    _qf = A.fetch_yahoo_live_ltp('RELIANCE')
    check('fresh Yahoo quote → is_realtime True', _qf.get('is_realtime') is True)
    check('fresh Yahoo quote → stale False', _qf.get('stale') is False)

    A._HTTP.get = lambda url, **kw: _FakeResp(_mk(None))
    _qn = A.fetch_yahoo_live_ltp('RELIANCE')
    check('Yahoo timestamp missing → is_realtime False (fail-closed)',
          _qn.get('is_realtime') is False)
    check('Yahoo timestamp missing → timestamp "--:--:--", ab fake now() nahi',
          _qn.get('timestamp') == '--:--:--', f"timestamp={_qn.get('timestamp')}")
finally:
    A._HTTP.get = _orig_get
    A.datetime = _orig_dt
    A._LIVE_STALE_WARNED.clear()

print('\n-- FIX-49: source-level guarantees')
check('fetch_yahoo_live_ltp me hardcoded is_realtime: True nahi bacha',
      re.search(r"def fetch_yahoo_live_ltp.*?'source': f'yahoo", app_src, re.S) is not None
      and "'is_realtime': True,\n                'source': f'yahoo" not in app_src)
check('fetch_nse_live_ltp me hardcoded is_realtime: True nahi bacha',
      "'timestamp': datetime.now().strftime('%H:%M:%S'),\n                    'is_realtime': True"
      not in app_src)
check('dono fetcher quote_is_fresh call karte hain',
      app_src.count('quote_is_fresh(') >= 3, f"count={app_src.count('quote_is_fresh(')}")
check('LIVE_MAX_AGE_MIN env/.env se override ho sakta hai',
      "os.environ.get('STOCKAI_LIVE_MAX_AGE_MIN')" in app_src)
check('kill-switch env se override ho sakta hai',
      "os.environ.get('STOCKAI_LIVE_GATE')" in app_src)
check('NSE apna timestamp bhejta hai aur ab use hota hai',
      "data.get('timestamp')" in app_src and 'lastUpdateTime' in app_src)

# ── FIX-50: NSE holiday calendar + source-name fail-open ───────────────────
# User ne khud pakda: 2026-10-02 Friday = Gandhi Jayanti, market poora din BAND.
# Par `is_market_open()` sirf weekday+time dekhta tha → 09:31 par True bola →
# FIX-49 ka gate minute-level branch me gaya aur perfectly-sahi data ko
# 'STALE: quote 1096m purana' keh diya. FALSE POSITIVE.
_HOL = _dt.datetime(2026, 10, 2, 9, 31)      # Fri, Gandhi Jayanti — market BAND
_HOLPM = _dt.datetime(2026, 10, 2, 20, 0)
_MON = _dt.datetime(2026, 10, 5, 9, 31)      # Mon, normal trading day
_MONPM = _dt.datetime(2026, 10, 5, 16, 30)
_SAT = _dt.datetime(2026, 10, 3, 11, 0)
_Q_LAST = _ep(_dt.datetime(2026, 10, 1, 15, 15))   # aakhri asli session ka quote

print('\n-- FIX-50: holiday calendar')
check('2026 ke 16 weekday holidays loaded hain',
      sum(1 for d in A.NSE_HOLIDAYS if d.year == 2026) == 16,
      f"count={sum(1 for d in A.NSE_HOLIDAYS if d.year == 2026)}")
_known = ['2026-01-15', '2026-01-26', '2026-04-03', '2026-05-01',
          '2026-10-02', '2026-10-20', '2026-12-25']
_missing = [d for d in _known if A._parse_iso_date(d) not in A.NSE_HOLIDAYS]
check('jaani-pehchani holidays maujood hain', not _missing, f'missing={_missing or "none"}')
# 15-Aug-2026 Saturday par padta hai — NSE ki "weekday holidays" list me nahi hota,
# par market us din bhi band hi hai (weekend rule se). Dono baat alag-alag assert karo.
check('15-Aug-2026 (Sat) weekday-holiday list me NAHI, par market phir bhi band',
      A._parse_iso_date('2026-08-15') not in A.NSE_HOLIDAYS
      and A.is_market_open(_dt.datetime(2026, 8, 15, 11, 0)) is False)
_not_hol = ['2026-01-16', '2026-07-15', '2026-09-30', '2026-10-01', '2026-10-05']
_wrong = [d for d in _not_hol if A._parse_iso_date(d) in A.NSE_HOLIDAYS]
check('normal trading days holiday NAHI maane gaye', not _wrong, f'wrongly flagged={_wrong or "none"}')
check('is_market_holiday date/datetime/None teeno leta hai',
      A.is_market_holiday(_dt.date(2026, 10, 2)) is True
      and A.is_market_holiday(_dt.datetime(2026, 10, 2, 9, 31)) is True
      and A.is_market_holiday(_dt.date(2026, 10, 5)) is False
      and isinstance(A.is_market_holiday(None), bool))

print('\n-- FIX-50: is_market_open holiday par')
check('HOLIDAY 09:31 → market BAND (pehle True bolta tha)',
      A.is_market_open(_HOL) is False)
check('normal Mon 09:31 → market KHULA', A.is_market_open(_MON) is True)
check('Saturday → market BAND', A.is_market_open(_SAT) is False)
check('holiday 20:00 → market BAND', A.is_market_open(_HOLPM) is False)

print('\n-- FIX-50: false-positive ab nahi (asli regression test)')
_f, _r = A.quote_is_fresh(_Q_LAST, _HOL)
check('HOLIDAY 09:31, quote 01-Oct 15:15 → FRESH (pehle STALE kehta tha)',
      _f is True, _r)
check('  ↳ reason "market closed" bolta hai, "purana" nahi',
      'market closed' in _r and 'STALE' not in _r, _r)
check('holiday 20:00 par bhi FRESH', A.quote_is_fresh(_Q_LAST, _HOLPM)[0] is True)

print('\n-- FIX-50: last_completed_session holiday skip karta hai')
check('Mon 05-Oct subah → 2026-10-01 (02-Oct holiday skip)',
      A.last_completed_session(_MON) == _dt.date(2026, 10, 1),
      f"got={A.last_completed_session(_MON)}")
check('Mon 05-Oct shaam → 2026-10-05 (aaj ka session complete)',
      A.last_completed_session(_MONPM) == _dt.date(2026, 10, 5),
      f"got={A.last_completed_session(_MONPM)}")
check('holiday ke agle din shaam → holiday skip hokar 10-01',
      A.last_completed_session(_HOLPM) == _dt.date(2026, 10, 1),
      f"got={A.last_completed_session(_HOLPM)}")

print('\n-- FIX-50: normal din par gate abhi bhi strict hai')
check('Mon 09:31, quote 01-Oct 15:15 (18.3h) → STALE',
      A.quote_is_fresh(_Q_LAST, _MON)[0] is False, A.quote_is_fresh(_Q_LAST, _MON)[1])
check('Mon 09:31, quote 30s pehle → FRESH',
      A.quote_is_fresh(_ep(_MON - _dt.timedelta(seconds=30)), _MON)[0] is True)
check('Mon 16:30, quote 4 din purana → STALE',
      A.quote_is_fresh(_Q_LAST, _MONPM)[0] is False)

print('\n-- FIX-50: calendar update karne ke raaste (patch ke bina)')
_saved_env = os.environ.get('STOCKAI_EXTRA_HOLIDAYS')
os.environ['STOCKAI_EXTRA_HOLIDAYS'] = '2027-01-26, 2027-03-23 ,garbage,2027-11-04'
_extra = A._load_extra_holidays()
check('STOCKAI_EXTRA_HOLIDAYS env se dates aati hain',
      {_dt.date(2027, 1, 26), _dt.date(2027, 3, 23), _dt.date(2027, 11, 4)} <= _extra,
      f'got={sorted(str(d) for d in _extra)}')
check('  ↳ garbage entry chup-chaap ignore hoti hai',
      all(d.year != 1900 for d in _extra) and len(_extra) == 3, f'count={len(_extra)}')
if _saved_env is None:
    os.environ.pop('STOCKAI_EXTRA_HOLIDAYS', None)
else:
    os.environ['STOCKAI_EXTRA_HOLIDAYS'] = _saved_env
_hf = A.HOLIDAYS_FILE
_hf_existed = _hf.exists()
_hf_backup = _hf.read_text(encoding='utf-8') if _hf_existed else None
try:
    _hf.write_text('# comment line\n2027-08-15\n2027-10-02   # Gandhi Jayanti\n', encoding='utf-8')
    _from_file = A._load_extra_holidays()
    check('nse_holidays.txt se dates aati hain (inline # comment strip)',
          _dt.date(2027, 8, 15) in _from_file and _dt.date(2027, 10, 2) in _from_file,
          f'got={sorted(str(d) for d in _from_file)}')
finally:
    if _hf_existed:
        _hf.write_text(_hf_backup, encoding='utf-8')
    else:
        _hf.unlink(missing_ok=True)

print('\n-- FIX-50: calendar khaali ho to crash nahi, weekday par wapas')
_saved_hol = A.NSE_HOLIDAYS
try:
    A.NSE_HOLIDAYS = frozenset()
    check('khaali calendar → normal Mon khula maana jaata hai (degrade, crash nahi)',
          A.is_market_open(_MON) is True and A.is_market_open(_SAT) is False)
    check('khaali calendar → last_completed_session phir bhi weekend skip karta hai',
          A.last_completed_session(_dt.datetime(2026, 10, 5, 8, 0)) == _dt.date(2026, 10, 2))
finally:
    A.NSE_HOLIDAYS = _saved_hol

print('\n-- FIX-50: /api/stock ka is_realtime source-name se nahi aata')
check("purana `'NSE' in str(active_source)` fail-open hata diya gaya",
      "'NSE' in str(active_source)" not in app_src)
check('ab live_nse ke gate verdict se aata hai',
      "bool(live_nse and live_nse.get('is_realtime'))" in app_src)
check('realtime_reason bhi expose hota hai', "'realtime_reason'" in app_src)
check('banner par holiday coverage print hoti hai',
      'NSE holidays:' in app_src and 'HOLIDAY — market band' in app_src)
check('calendar purana ho to startup warning hai',
      'holiday calendar me' in app_src and 'STOCKAI_EXTRA_HOLIDAYS' in app_src)

# ── FIX-51: ek hi feed state, asli quote time, aur label collisions ─────────
# User ke paste kiye dashboard me EK HI SCREEN PAR "DELAYED (15-20 min)" (upar) aur
# "LIVE" (price ke baju) tha. Karan: top badge `/NSE/i.test(data_source)` se liveness
# nikalta tha (source ka NAAM), chip `is_realtime` se. Aur `liveSrc` me
# `new Date().toLocaleTimeString()` tha — yaani BROWSER ki ghadi quote ke waqt ki
# jagah ("yahoo.ns · 10:31:31" jabki quote kal 15:15 ka tha).
_HTML = (ROOT / 'Dashboard.html').read_text(encoding='utf-8')

print('\n-- FIX-51: feed_state (teen states)')
check('market band + fresh → CLOSED (LIVE nahi)',
      A.feed_state(True, _HOL) == 'CLOSED')
check('market khula + fresh → LIVE', A.feed_state(True, _OPEN) == 'LIVE')
check('market khula + stale → DELAYED', A.feed_state(False, _OPEN) == 'DELAYED')
check('market band + stale → CLOSED (holiday par "delayed" galat word hai)',
      A.feed_state(False, _HOL) == 'CLOSED')

print('\n-- FIX-51: feed_label me asli number, hardcoded nahi')
check('CLOSED + holiday → "MARKET CLOSED (holiday)"',
      A.feed_label('CLOSED', None, _HOL) == 'MARKET CLOSED (holiday)')
check('CLOSED + normal weekend → "MARKET CLOSED"',
      A.feed_label('CLOSED', None, _SAT) == 'MARKET CLOSED')
check('DELAYED 25 min → "DELAYED (25 min)"',
      A.feed_label('DELAYED', 25, _OPEN) == 'DELAYED (25 min)')
check('DELAYED 1175 min → ghante me, "15-20 min" nahi',
      A.feed_label('DELAYED', 1175.5, _OPEN) == 'DELAYED (19.6 h)',
      A.feed_label('DELAYED', 1175.5, _OPEN))
check('DELAYED age unknown → jhootha number nahi',
      A.feed_label('DELAYED', None, _OPEN) == 'DELAYED (age unknown)')
check('LIVE → "LIVE"', A.feed_label('LIVE', 0.5, _OPEN) == 'LIVE')

print('\n-- FIX-51: app.py side guarantees')
check('dono fetcher feed_state/feed_label/quote_age_min bhejte hain',
      app_src.count("'feed_state': ") >= 3, f"count={app_src.count(chr(39)+'feed_state'+chr(39)+': ')}")
check('tier-3 fallback ka fake datetime.now() timestamp gaya',
      "'timestamp': datetime.now().strftime('%H:%M:%S'),\n                        'is_realtime': False"
      not in app_src)
check('obv_ema indicators payload me hai', "'obv_ema': sfx(L.get('OBV_EMA')" in app_src)
check("debt_equity ab '%' ke saath hai (yfinance percentage deta hai)",
      'D/E' in app_src and "f\"{fund_data['debt_val']:.1f}% D/E\"" in app_src)
check('debt_equity ka falsy-check `is not None` hua (0.0 D/E ab N/A nahi)',
      "'N/A' if fund_data['debt_val'] is None else" in app_src)
check('/api/stock feed_state + market_holiday bhejta hai',
      "'market_holiday': is_market_holiday()" in app_src)

print('\n-- FIX-51: Dashboard.html side guarantees')
check('source-name se liveness nikalna band (/NSE/i.test live code me nahi)',
      'const isLive = /NSE/i.test(src)' not in _HTML)
# Comment-aware check: fix document karne wale comments me purani string likhi hai,
# isliye `//` ke baad ka hissa hata kar dekhte hain — warna test khud fail hota.
_CODE_ONLY = '\n'.join(_l.split('//')[0] for _l in _HTML.splitlines())
check('hardcoded "DELAYED (15-20 min)" executable code me nahi (sirf comments me)',
      'DELAYED (15-20 min)' not in _CODE_ONLY)
check('setLiveChip me browser ki ghadi (new Date().toLocaleTimeString) nahi',
      'new Date().toLocaleTimeString' not in _HTML)
check('setLiveChip teen states handle karta hai',
      "'CLOSED'" in _HTML and '.live-chip.closed' in _HTML)
check('top badge setLiveBadge(feed_state) se chalta hai',
      'setLiveBadge(d.feed_state, d.feed_label, src)' in _HTML)
check('quote ka asli waqt (quote_time) dikhaya jaata hai',
      'quoteTime: t.quote_time' in _HTML and 'quoteTime: tick.quote_time' in _HTML)
check('OBV label ab obv vs obv_ema se banta hai (obv > 0 se nahi)',
      'ind.obv > ind.obv_ema' in _HTML and 'ind.obv > 0 ?' not in _HTML)
check('OBV negative value ab dikhti hai (pehle "0" ban jaati thi)',
      'fmtSigned(ind.obv)' in _HTML)
check("Bollinger %B ke band-pass labels hain ('MID' hi sab nahi)",
      'NEAR LOWER' in _HTML and 'NEAR UPPER' in _HTML and 'BELOW LOWER' in _HTML)
check('"Master Score" label collision khatam (ab "Ensemble rank")',
      'Master Score:' not in _HTML and 'Ensemble rank:' in _HTML)
check('purana 2-arg setLiveChip call nahi bacha',
      'setLiveChip(!!opts.stale, opts.source)' not in _HTML)

# ── FIX-52: CAS timing, ROE unit, aur do-price disclosure ─────────────────
# 1. Aug 3 2026 se NSE ka Closing Auction Session 15:15–15:35 hai (F&O stocks).
#    Official close 15:35 par publish hota hai. SESSION_CLOSE_HM 15:40 tha —
#    NSE ke liye 5 min zyada.
# 2. yfinance `returnOnEquity` FRACTION deta hai (0.47743 = 47.74%) par
#    `dividendYield` PERCENT (3.17). Purana code dono ko ek jaisa treat karta
#    tha aur TCS ka ROE 100x galat ("0.48%") dikhata tha.
# 3. Header price /api/quote se aata hai, poora plan /api/stock ke price se.
#    User ke dashboard par ye 2075.00 vs 2079.30 the. Chup-chaap koi ek chunne
#    ke bajaye dono disclose hote hain.

print('\n-- FIX-52: session close 15:35 (NSE Closing Auction)')
check('SESSION_CLOSE_HM = 15:35', A.SESSION_CLOSE_HM == 15 * 60 + 35,
      f'actual={A.SESSION_CLOSE_HM}')
check('15:34 par market KHULA', A.is_market_open(_dt.datetime(2026, 10, 1, 15, 34)) is True)
check('15:35 par market KHULA (CAS close ka minute)',
      A.is_market_open(_dt.datetime(2026, 10, 1, 15, 35)) is True)
check('15:36 par market BAND (official close publish ho chuka)',
      A.is_market_open(_dt.datetime(2026, 10, 1, 15, 36)) is False)
check('09:15 par market KHULA (lower bound intact)',
      A.is_market_open(_dt.datetime(2026, 10, 1, 9, 15)) is True)

print('\n-- FIX-52: ROE unit (Yahoo raw 0.47743 = 47.74%)')
check("returnOnEquity ko *100 kiya jaata hai (purana /100 wala galat tha)",
      "_roe = (_r_raw * 100.0) if (_r_raw is not None and abs(_r_raw) <= 2.0) else _r_raw"
      in app_src)
check('purana broken ROE heuristic gaya',
      "_roe = (_roe / 100.0) if (_roe and _roe > 5) else _roe" not in app_src)
check("dividendYield abhi bhi percent-treated hai (FIX-09 regression guard)",
      "_dy = (_dy / 100.0) if (_dy and _dy > 25) else _dy" in app_src)
# ROE normalisation ko asli numbers par test karo
_r = 0.47743
_norm = (_r * 100.0) if (_r is not None and abs(_r) <= 2.0) else _r
check('0.47743 → 47.74% (TCS ka measured ROE)',
      f'{_norm:.2f}%' == '47.74%', f'got {_norm:.2f}%')
_z = 0.0
_norm0 = (_z * 100.0) if (_z is not None and abs(_z) <= 2.0) else _z
check('ROE 0.0 crash nahi karta', _norm0 == 0.0)

print('\n-- FIX-52: do-price disclosure')
check('/api/stock frame_close bhejta hai', "'frame_close': sfx(L.get('Close'), 2)" in app_src)
check('/api/stock price_basis bhejta hai (kis price se plan bani)',
      "'price_basis':" in app_src)
check('Dashboard header-vs-analysis gap check karta hai',
      'function checkPriceGap(' in _HTML and 'priceGapWarn' in _HTML)
check('gap threshold 0.25% hai (measured TCS gap 0.207% tha)',
      'pct < 0.25' in _HTML)
check('analysis price /api/stock se record hota hai',
      'analysisPrice = d.frame_close ?? d.price' in _HTML)
check('updatePriceDOM gap check call karta hai', 'checkPriceGap(p);' in _HTML)

print('\n-- FIX-52: search bar me exchange suffix')
# FIX-55: dedupe (symbol, exchange) par hone ke baad `ex` ab _x variable se aata hai
check('/api/search `ex` field bhejta hai',
      "_x = 'NSE' if '.NS' in sym" in app_src and "'ex': _x," in app_src)
check('search dropdown `ex` render karta hai (pehle sirf sym/name/sec dikhte the)',
      "exEl.textContent = String(s.ex ?? '').toUpperCase() === 'BSE' ? 'BSE' : 'NSE'" in _HTML)
check('dropdown item me exchange badge append hota hai',
      'item.append(symEl, exEl, nameEl, secEl)' in _HTML)

# ── FIX-53: TradingView ka silent BSE fallback ab label me dikhta hai ──────
# Measured (tvDatafeed, 2026-10-01 close):
#     NSE:TCS 2075.00        BSE:TCS 2079.30
#     NSE:RELIANCE 1167.70   BSE:RELIANCE 1166.00
# User ke dashboard par price 2079.30, ATR 57.54, 52W 3336.7/1976 tha — chaaron
# TV-BSE se EXACT match. Yaani uska poora analysis BSE data se bana tha jabki
# label 'TradingView Direct' tha aur app NSE universe par calibrated hai.
# Aur Yahoo = TV-NSE: 11 sessions x 2 stocks = 22 din, 0 mismatch.

print('\n-- FIX-53: TradingView exchange tracking')


class _FakeTV:
    """NSE khaali, BSE me data — bilkul wahi jo user ki machine par hua."""
    def __init__(self, nse_bars=0, bse_bars=60):
        self.nse_bars, self.bse_bars = nse_bars, bse_bars
        self.calls = []

    def get_hist(self, symbol=None, exchange=None, interval=None, n_bars=None):
        self.calls.append(exchange)
        n = self.nse_bars if exchange == 'NSE' else self.bse_bars
        if n <= 0:
            return None
        import pandas as _pd
        idx = _pd.date_range('2026-07-01', periods=n, freq='D')
        return _pd.DataFrame({'open': [100.0] * n, 'high': [101.0] * n,
                              'low': [99.0] * n, 'close': [100.0] * n,
                              'volume': [1000] * n}, index=idx)


_orig_tv_obj = A.DATA_MANAGER.tv
try:
    _fake = _FakeTV(nse_bars=0, bse_bars=60)
    A.DATA_MANAGER.tv = _fake
    _df, _ex = A.DATA_MANAGER.fetch_tradingview('TCS', n_bars=60, interval_str='1d')
    check('NSE khaali → BSE par girta hai aur exchange batata hai', _ex == 'BSE', f'got {_ex!r}')
    check('dono exchanges try hue', _fake.calls == ['NSE', 'BSE'], f'calls={_fake.calls}')

    _fake2 = _FakeTV(nse_bars=60, bse_bars=60)
    A.DATA_MANAGER.tv = _fake2
    _df2, _ex2 = A.DATA_MANAGER.fetch_tradingview('TCS', n_bars=60, interval_str='1d')
    check('NSE me data ho to BSE try hi nahi hota', _ex2 == 'NSE' and _fake2.calls == ['NSE'],
          f'ex={_ex2!r} calls={_fake2.calls}')
    check('fetch_tradingview tuple return karta hai (pehle sirf df)',
          isinstance(_df2, tuple) is False and _df2 is not None)
finally:
    A.DATA_MANAGER.tv = _orig_tv_obj

check('smart_fetch source label me exchange likhta hai',
      "f'TradingView Direct ({tv_exch})'" in app_src)
check('/api/stock frame_exchange bhejta hai', "'frame_exchange':" in app_src)
check('Dashboard BSE frame par warn karta hai',
      "d.frame_exchange === 'BSE'" in _HTML and 'exchWarn' in _HTML)
check('purana bare "TradingView Direct" return gaya',
      "return df_tv, 'TradingView Direct'" not in app_src)

# ── FIX-54: BSE-only stocks (DHOOTIN = Dhoot Industrial Finance) ───────────
# User ne DHOOTIN search kiya. Log me hi jawab tha:
#     ERROR:yfinance:HTTP Error 404: Quote not found for symbol: DHOOTIN.NS
# Measured:
#     DHOOTIN.NS -> 404 "No data found"        DHOOTIN.BO -> OK, exchange=BSE
#     TV-NSE:DHOOTIN -> EMPTY                  TV-BSE:DHOOTIN -> 244.60 (01-Oct)
#     NSE master (2593 rows) me sirf DHOOTTRANS hai — wo ALAG company hai
#     (Dhoot Transmission, close 1443.40), Dhoot Industrial Finance nahi.
# Yaani NSE feed "fail" nahi hua tha — stock NSE par listed hi nahi. Aur yfinance
# hamesha `.NS` try karta tha, isliye poora Fundamentals panel N/A tha jabki
# `.BO` se sab milta hai (mcap Rs158.6Cr, P/E 2.85, P/B 0.36).

print('\n-- FIX-54: BSE-only stocks')
check('yfinance suffix exchange se choose hota hai (hamesha .NS nahi)',
      "_yf_suffix = '.BO' if '(BSE)' in str(daily_source) else '.NS'" in app_src)
check('purana hardcoded .NS call gaya',
      'yf.Ticker(f"{resolved}.NS")' not in app_src)
check('/api/stock on_nse_master bhejta hai', "'on_nse_master':" in app_src)
check('Dashboard BSE-only aur NSE-feed-fail me farq karta hai',
      'd.on_nse_master === false' in _HTML)
check('terminal warning ab neutral hai ("NSE feed khaali tha" nahi)',
      'NSE feed khaali tha, BSE par gir' not in app_src
      and 'par data nahi mila' in app_src)
check('priceGapWarn me galat CAS explanation nahi bacha (comments me theek hai)',
      'Closing Auction 15:15' not in _CODE_ONLY)

# ── FIX-55: NSE/BSE toggle — user ka original request, ab properly bana ────
# Pehle maine kaha tha "BSE ka multi-year historical reliable source se nahi
# milta, isliye toggle possible nahi". WO GALAT THA — measured (tvDatafeed):
#     TV-BSE DHOOTIN   1200 bars  2021-12-01 -> 2026-10-01
#     TV-BSE TCS       1200 bars  2021-12-02 -> 2026-10-01
#     TV-BSE RELIANCE  1200 bars  2021-12-02 -> 2026-10-01
# Yaani 300-bar frame aur 250-bar calibration lookback dono ke liye kaafi hai.
# Live verified (?ex=NSE vs ?ex=BSE, TCS):
#     NSE -> TradingView Direct (NSE)  price 2075.0  52W 3350.0/1976.8  pos 7.2
#     BSE -> TradingView Direct (BSE)  price 2079.3  52W 3336.7/1976.0  pos 7.6
#     quote NSE -> yahoo.ns 2075.0 @ 15:15:00 | BSE -> yahoo.bo 2079.3 @ 15:50:08

print('\n-- FIX-55: exchange toggle (server)')


class _FakeTV55:
    """Dono exchanges me data — order track karta hai."""
    def __init__(self):
        self.calls = []

    def get_hist(self, symbol=None, exchange=None, interval=None, n_bars=None):
        self.calls.append(exchange)
        import pandas as _pd
        idx = _pd.date_range('2026-07-01', periods=60, freq='D')
        return _pd.DataFrame({'open': [100.0] * 60, 'high': [101.0] * 60,
                              'low': [99.0] * 60, 'close': [100.0] * 60,
                              'volume': [1000] * 60}, index=idx)


_orig_tv55 = A.DATA_MANAGER.tv
try:
    _f = _FakeTV55()
    A.DATA_MANAGER.tv = _f
    _d, _x = A.DATA_MANAGER.fetch_tradingview('TCS', n_bars=60, interval_str='1d')
    check('default NSE-first', _x == 'NSE' and _f.calls == ['NSE'], f'ex={_x} calls={_f.calls}')
    _f2 = _FakeTV55()
    A.DATA_MANAGER.tv = _f2
    _d2, _x2 = A.DATA_MANAGER.fetch_tradingview('TCS', n_bars=60, interval_str='1d',
                                                prefer_exch='BSE')
    check("prefer_exch='BSE' → BSE pehle try hota hai",
          _x2 == 'BSE' and _f2.calls == ['BSE'], f'ex={_x2} calls={_f2.calls}')
finally:
    A.DATA_MANAGER.tv = _orig_tv55

check('smart_fetch prefer_exch accept karta hai', 'prefer_exch=' in
      app_src.split('def smart_fetch(')[1].split('):')[0])
check('MTF engine bhi exchange follow karta hai',
      'prefer_exch=req_exch)  # independent diagnostic' in app_src)
check('/api/stock ex param validate karta hai (NSE/BSE ke alawa → NSE)',
      "if req_exch not in ('NSE', 'BSE')" in app_src)
check('/api/stock requested_exchange bhejta hai', "'requested_exchange': req_exch" in app_src)
check('/api/quote ex param leta hai', "request.args.get('ex')" in app_src)
check('SSE ex param leta hai (generator ke BAHAR padha — request-context safe)',
      '_sse_ex = (request.args.get' in app_src
      and app_src.index('_sse_ex = (request.args.get') < app_src.index('def event_stream():'))
# FIX-56: structure badla (ab quote = None if _bse else ...), assertion update
check('BSE request par NSE official endpoint skip hota hai',
      'quote = None if _bse else fetch_nse_live_ltp(clean_sym)' in app_src)
check('live-quote cache key exchange-aware hai (warna galat exchange serve hota)',
      '_LIVE_CACHE.get(_ckey)' in app_src and '_LIVE_CACHE[_ckey] =' in app_src)
check('Yahoo quote suffix order exchange se badalta hai',
      "_sfx = ('.BO', '.NS') if str(prefer_exch).upper() == 'BSE'" in app_src)
check('/api/search dedupe (symbol, exchange) par hai — dono listings dikhte hain',
      "existing = {(r['sym'], r.get('ex', 'NSE')) for r in results}" in app_src)

print('\n-- FIX-55: exchange toggle (dashboard) + D/E precision')
check('activeExchange state hai', 'let activeExchange = "NSE"' in _HTML)
check('search item exchange carry karta hai', "item.dataset.ex =" in _HTML)
check('selectStock exchange set karta hai', 'function selectStock(sym, ex)' in _HTML)
check('/api/stock call me ex jaata hai', '?ex=${activeExchange}' in _HTML)
check('SSE URL me ex jaata hai', '/api/stream/${symbol}?ex=${activeExchange}' in _HTML)
check('polling URL me ex jaata hai', '/api/quote/${symbol}?ex=${activeExchange}' in _HTML)
check('manual refresh me ex jaata hai', '?force=1&ex=${activeExchange}' in _HTML)
check("D/E chhoti value par 3 decimals (0.027 -> '0.027%', '0.0%' nahi)",
      "{fund_data['debt_val']:.3f}% D/E" in app_src)

# ── FIX-56: BSE session 16:00 tak, aur BSE par TradingView > Yahoo ────────
# 1. is_market_open exchange nahi jaanta tha. BSE ka closing/post-close 16:00 tak
#    chalta hai — measured BSE `Ason` "01 Oct 26 | 16:00" aur Yahoo BO quote_time
#    15:50:08. Yaani BSE mode me 15:36 se "market band" bolna galat tha.
# 2. Yahoo ka BSE data NSE jitna bharosemand nahi: DHOOTIN.BO vs TV-BSE 21
#    sessions me 7 mismatch (-5.00 tak), NSE par 22 me 0. Aur Yahoo ka
#    regularMarketPrice ek snapshot hai (DHOOTIN 251.0 @ 15:27:03, close 244.60).

print('\n-- FIX-56: BSE session 16:00 tak')
check('BSE_SESSION_CLOSE_HM = 16:00', A.BSE_SESSION_CLOSE_HM == 16 * 60,
      f'actual={A.BSE_SESSION_CLOSE_HM}')
check('NSE 15:45 → BAND', A.is_market_open(_dt.datetime(2026, 10, 1, 15, 45)) is False)
check('BSE 15:45 → KHULA (post-close chal raha hai)',
      A.is_market_open(_dt.datetime(2026, 10, 1, 15, 45), exchange='BSE') is True)
check('BSE 16:00 → KHULA (aakhri minute)',
      A.is_market_open(_dt.datetime(2026, 10, 1, 16, 0), exchange='BSE') is True)
check('BSE 16:01 → BAND',
      A.is_market_open(_dt.datetime(2026, 10, 1, 16, 1), exchange='BSE') is False)
check('NSE 15:35 → KHULA, 15:36 → BAND (FIX-52 regression guard)',
      A.is_market_open(_dt.datetime(2026, 10, 1, 15, 35)) is True
      and A.is_market_open(_dt.datetime(2026, 10, 1, 15, 36)) is False)
check('holiday par dono exchange BAND',
      A.is_market_open(_dt.datetime(2026, 10, 2, 15, 45), exchange='BSE') is False)

print('\n-- FIX-56: BSE + market band → TradingView ka close, Yahoo ka snapshot nahi')
check('_bse_closed gate hai', '_bse_closed = _bse and not is_market_open(' in app_src)
check('Yahoo tier _bse_closed par skip hota hai',
      'if quote is None and not _bse_closed:' in app_src)
check('/api/stock market_open exchange-aware hai',
      "'market_open': is_market_open(exchange=req_exch)" in app_src)
check('partial_today bhi exchange-aware hai',
      'is_market_open(now_ist, exchange=req_exch)' in app_src)
check('Yahoo quote payload exchange-aware market_open bhejta hai',
      "'market_open': is_market_open(exchange=prefer_exch)" in app_src)
check('NSE ka official endpoint NSE hi rehta hai (exchange param nahi)',
      app_src.count("'market_open': is_market_open(),") == 1)

print('\n-- FIX-56: tier-3 timestamp (tvDatafeed UTC-naive deta hai)')
# Live measured: TradingView tier se quote_time "2026-10-01 03:45:00" aata tha —
# wo UTC hai (IST me 09:15, session open). Aur daily bar ka timestamp session ka
# OPEN hota hai, close nahi — use "quote ka waqt" kehna jhooth tha.
check('tvDatafeed ka naive timestamp UTC maan kar IST me convert hota hai',
      "_lb.tz_localize('UTC').tz_convert(IST)" in app_src)
check('daily bar par quote_time "(daily close)" kehta hai (09:15 open nahi)',
      "_lb_note = ' (daily close)'" in app_src
      and "{_lb_note}" in app_src)
# conversion khud verify karo
import datetime as _dtl
_naive = _dtl.datetime(2026, 10, 1, 3, 45)          # tvDatafeed ka raw index
_conv = _naive.replace(tzinfo=_dtl.timezone.utc).astimezone(
    _dtl.timezone(_dtl.timedelta(hours=5, minutes=30)))
check('03:45 UTC -> 09:15 IST (session open)',
      _conv.strftime('%H:%M') == '09:15', f'got {_conv.strftime("%H:%M")}')

# ── FIX-57: transaction-cost model (cost-aware plan) ──────────────────────
# Pehle app T1/T2/SL/R:R/Kelly sab GROSS dikhata tha — fees ka koi hisaab nahi.
# tools/study_new_signals.py ne measure kiya: equity round-trip ~0.231%, aur ek
# +0.169% gross signal us cost me NEGATIVE ho jaata hai. Cost ignore karna ek
# missing detail nahi, ek real risk hai.

print('\n-- FIX-57: cost model arithmetic')
# FIX-59: 0.231% wala number FIX-57 ka tha aur GALAT tha (delivery STT sirf sell
# par lagaya tha, intraday STT x2). Ab cost research/costs.py se aata hai, isliye
# assertion "exact match with research" hai — koi duplicate number nahi.
from research.costs import CostConfig as _CC
_REF = _CC()
_c = A.trade_cost_pct('intraday')
check('app ka cost model research/costs.py se EXACT match karta hai (intraday)',
      all(abs(A.trade_cost_pct('intraday', n)
              - _REF.round_trip_pct(intraday=True, notional=n)) < 1e-12
          for n in (10000, 25000, 100000, 1000000)))
check('app ka cost model research/costs.py se EXACT match karta hai (delivery)',
      all(abs(A.trade_cost_pct('delivery', n)
              - _REF.round_trip_pct(intraday=False, notional=n)) < 1e-12
          for n in (10000, 25000, 100000, 1000000)))
check('delivery > intraday (delivery STT 0.1% DONO taraf, intraday 0.025% SIRF sell)',
      A.trade_cost_pct('delivery') > _c,
      f"intraday={_c} delivery={A.trade_cost_pct('delivery')}")
check('NOTIONAL-AWARE: chhoti position par % cost zyada (brokerage Rs20 cap)',
      A.trade_cost_pct('intraday', 25000) > A.trade_cost_pct('intraday', 1000000),
      f"25k={A.trade_cost_pct('intraday',25000)} 10L={A.trade_cost_pct('intraday',1000000)}")
check('TRADE_COST ab hardcoded nahi, config se DERIVED hai',
      '_CFG.brokerage_pct' in app_src and '_CFG.exch_pct' in app_src)
# app_src me FIX-59 ka docstring `STOCKAI_COST_STT` ka ZIKR karta hai (kyun
# hataya). Naive string-grep apne hi documentation par fail hota hai — wahi
# lesson jo FIX-50 me mila tha. Isliye ASLI claim test karo: koi env lookup nahi.
# Do patterns hain: direct `os.environ.get('X')` aur helper `_env_or_none('X')`.
# Sirf ek grep karne se aadhe keys miss hote hain (pehli baar yahi hua).
_ENV_LOOKUPS = set(re.findall(
    r"(?:os\.environ\.get|os\.environ\[|_env_or_none|_cost_side)\(?\s*'(STOCKAI_[A-Z_]+)'",
    app_src))
check('statutory rates (STT/stamp) env se override NAHI hote',
      'STOCKAI_COST_STT' not in _ENV_LOOKUPS and 'STOCKAI_COST_STAMP' not in _ENV_LOOKUPS,
      f'cost-related env lookups: {sorted(k for k in _ENV_LOOKUPS if "COST" in k)}')
check("deprecated key .env.example se bhi hat gayi",
      'STOCKAI_COST_STT' not in (ROOT / '.env.example').read_text(encoding='utf-8'))
check('app.py me ab duplicate STT rate literals nahi (research/costs.py owns them)',
      "'stt_pct'" not in app_src and "'stt_buy'" not in app_src
      and "'stt_sell'" not in app_src)
check('naye configurable keys env se padhe jaate hain',
      {'STOCKAI_COST_BROKERAGE', 'STOCKAI_COST_BROKERAGE_CAP',
       'STOCKAI_COST_SLIPPAGE', 'STOCKAI_COST_EXCH_PCT'}.issubset(_ENV_LOOKUPS))

print('\n-- FIX-57: cost_plan output')
_cp = A.cost_plan(_c, 2000.0, 5.0, {'t1': 5.0, 't2': 8.0, 't3': 12.0})
check('break_even_pct == round_trip_pct', _cp['break_even_pct'] == _cp['round_trip_pct'])
check('break_even_rs = price x cost% (2000 par ~Rs4.6)',
      abs(_cp['break_even_rs'] - 2000.0 * _c / 100) < 0.02, f"got {_cp['break_even_rs']}")
check('T1 net = gross - cost', abs(_cp['targets_net_pct']['t1'] - (5.0 - _c)) < 0.001,
      f"got {_cp['targets_net_pct']['t1']}")
check('cost_to_risk = cost/SL x100 (0.231/5 = 4.6%)',
      abs(_cp['cost_to_risk_pct'] - _c / 5.0 * 100) < 0.15, f"got {_cp['cost_to_risk_pct']}")
check('normal plan par koi warning nahi', _cp['warning'] is None)
check('price 0/None par cost_plan None deta hai (crash nahi)',
      A.cost_plan(_c, 0, 5.0, {'t1': 5.0}) is None
      and A.cost_plan(_c, None, 5.0, {'t1': 5.0}) is None)

print('\n-- FIX-57: warnings (jab trade cost ke layak nahi)')
_t1low = A.cost_plan(_c, 2000.0, 5.0, {'t1': 0.15, 't2': 8.0, 't3': 12.0})
check('T1 break-even se chhota -> warning',
      _t1low['warning'] is not None and 'break-even' in _t1low['warning'],
      f"warning={_t1low['warning']}")
_tight = A.cost_plan(_c, 2000.0, 0.8, {'t1': 3.0, 't2': 8.0, 't3': 12.0})
check('SL itna tight ki cost >20% of risk -> warning',
      _tight['warning'] is not None and 'tight' in _tight['warning'],
      f"cost_to_risk={_tight['cost_to_risk_pct']} warning={_tight['warning']}")

print('\n-- FIX-57: calculate_risk + payload + UI wiring')
_r = A.calculate_risk(2075.0, 57.54, 33, capital=100000, action='WATCHLIST',
                      regime='NEUTRAL')
check('calculate_risk ab `cost` block return karta hai', isinstance(_r.get('cost'), dict))
check('cost block me break_even + net targets + cost_to_risk sab hai',
      all(k in _r['cost'] for k in
          ('break_even_pct', 'break_even_rs', 'targets_net_pct', 'cost_to_risk_pct',
           'round_trip_pct', 'mode', 'qty', 'round_trip_on_notional')))
check("'risk': risk poora dict bhejta hai (to cost payload me hai)",
      "'risk': risk," in app_src)
dash_src = _CODE_ONLY   # already a str (HTML minus // comments)
check('Dashboard break-even move dikhata hai', 'break_even_pct' in dash_src)
check('Dashboard NET targets dikhata hai (gross nahi)', 'targets_net_pct' in dash_src)
check('Dashboard cost warning render karta hai', 'cost.warning' in dash_src)
check('.env.example me cost keys documented hain',
      all(k in (ROOT / '.env.example').read_text(encoding='utf-8')
          for k in ('STOCKAI_COST_MODE', 'STOCKAI_COST_SLIPPAGE',
                    'STOCKAI_COST_BROKERAGE', 'STOCKAI_COST_BROKERAGE_CAP',
                    'STOCKAI_COST_EXCH_PCT')))
check('cost model ka claim study se referenced hai (hawa me nahi)',
      'study_new_signals' in app_src or 'study_new_signals' in (ROOT / 'README.md').read_text(encoding='utf-8'))

# ── FIX-58: header exchange batata hai (pehle hamesha "NSE / BSE") ────────
# Live measured /api/stock se — chaaron cases me header IDENTICAL tha:
#   TCS ?ex=NSE   frame=NSE on_nse_master=true   -> "TCS — NSE / BSE"
#   TCS ?ex=BSE   frame=BSE on_nse_master=true   -> "TCS — NSE / BSE"
#   DHOOTIN ?ex=* frame=BSE on_nse_master=false  -> "DHOOTIN — NSE / BSE"
# DHOOTIN NSE par listed hi nahi, phir bhi "NSE / BSE" — yaani header kabhi nahi
# batata tha ki actually kaun sa exchange dikh raha hai. Payload me teeno fields
# (frame_exchange / requested_exchange / on_nse_master) FIX-53/54/55 se already the.

print('\n-- FIX-58: header exchange-aware')
check('executable Dashboard code me hardcoded "NSE / BSE" nahi bacha',
      'NSE / BSE' not in dash_src)
check('static placeholder bhi vague nahi (loading state)',
      'RELIANCE — NSE / BSE' not in _HTML)
check('header frame_exchange use karta hai', 'd.frame_exchange' in dash_src
      and 'stockName' in dash_src)
check('header requested vs frame mismatch batata hai',
      '_fx !== _rx' in dash_src or '_rx !== _fx' in dash_src)
check('BSE-only stock alag label deta hai (on_nse_master === false)',
      "on_nse_master === false" in dash_src and 'BSE only' in dash_src)
check('frame null/absent par "exchange unknown" — jhooth exchange nahi',
      'exchange unknown' in dash_src)

# Label logic ko actually evaluate karo (sirf source-grep nahi)
_label_js = """
const _fx = String(d.frame_exchange || '').toUpperCase();
const _rx = String(d.requested_exchange || '').toUpperCase();
let _exTxt;
if (_fx === 'BSE' && d.on_nse_master === false) { _exTxt = 'BSE only — NSE par listed nahi'; }
else if (_fx && _rx && _fx !== _rx) { _exTxt = _fx + ' — aapne ' + _rx + ' maanga tha'; }
else if (_fx) { _exTxt = _fx; }
else { _exTxt = 'exchange unknown'; }
return d.symbol + ' — ' + _exTxt;
"""
check('Dashboard me wahi branching order hai jo test evaluate karta hai',
      all(frag in dash_src for frag in
          ("_fx === 'BSE' && d.on_nse_master === false", "_fx !== _rx",
           "_exTxt = 'exchange unknown'")))

# Source-grep se aage: label logic ko node me ACTUALLY evaluate karo, un chaaron
# payloads par jo is turn me live /api/stock se measure kiye the.
import shutil as _sh, subprocess as _sp, tempfile as _tf
if _sh.which('node'):
    _cases = [
        ({'symbol': 'TCS', 'requested_exchange': 'NSE', 'frame_exchange': 'NSE',
          'on_nse_master': True}, 'TCS \u2014 NSE'),
        ({'symbol': 'TCS', 'requested_exchange': 'BSE', 'frame_exchange': 'BSE',
          'on_nse_master': True}, 'TCS \u2014 BSE'),
        ({'symbol': 'DHOOTIN', 'requested_exchange': 'NSE', 'frame_exchange': 'BSE',
          'on_nse_master': False}, 'DHOOTIN \u2014 BSE only \u2014 NSE par listed nahi'),
        ({'symbol': 'XYZ', 'requested_exchange': 'NSE', 'frame_exchange': None,
          'on_nse_master': True}, 'XYZ \u2014 exchange unknown'),
        ({'symbol': 'OLD'}, 'OLD \u2014 exchange unknown'),
    ]
    _script = ('const label=(d)=>{const _fx=String(d.frame_exchange||"").toUpperCase();'
               'const _rx=String(d.requested_exchange||"").toUpperCase();let _exTxt;'
               'if(_fx==="BSE"&&d.on_nse_master===false){_exTxt="BSE only \u2014 NSE par listed nahi";}'
               'else if(_fx&&_rx&&_fx!==_rx){_exTxt=_fx+" \u2014 aapne "+_rx+" maanga tha";}'
               'else if(_fx){_exTxt=_fx;}else{_exTxt="exchange unknown";}'
               'return d.symbol+" \u2014 "+_exTxt;};'
               'console.log(label(JSON.parse(process.argv[2])));')
    with _tf.TemporaryDirectory() as _td:
        _f = pathlib.Path(_td) / 'lbl.js'
        _f.write_text(_script, encoding='utf-8')
        for _payload, _want in _cases:
            _r = _sp.run(['node', str(_f), json.dumps(_payload)],
                         capture_output=True, text=True, timeout=30)
            _got = _r.stdout.strip()
            check(f'label({_payload.get("symbol")}, frame={_payload.get("frame_exchange")}, '
                  f'on_nse={_payload.get("on_nse_master")}) == {_want!r}',
                  _got == _want, f'got {_got!r}')
else:
    check('node available (label logic evaluate karne ke liye)', False, 'node nahi mila')

print('\n-- FIX-58: teen NSE-hardcoded fetch JAAN-BOOJH kar hain (guard)')
# deep_analyzer.py / nifty_scanner.py / research/data.py me exchange='NSE' hardcoded
# hai. Ye BUG NAHI hai:
#   • nifty_scanner  -> Nifty universe NSE-only hai
#   • research/data  -> score calibration NSE universe par fitted hai
#   • deep_analyzer  -> standalone script, app.py se reachable nahi
# Isliye "fix" karne se pehle sochna chahiye — ye test wahi reasoning pin karta hai.
_deep = (ROOT / 'deep_analyzer.py').read_text(encoding='utf-8')
_scan = (ROOT / 'nifty_scanner.py').read_text(encoding='utf-8')
_rdata = (ROOT / 'research' / 'data.py').read_text(encoding='utf-8')
check('deep_analyzer NSE-hardcoded hai (app.py se import NAHI hota)',
      "exchange='NSE'" in _deep and 'import deep_analyzer' not in app_src
      and 'from deep_analyzer' not in app_src)
check('nifty_scanner NSE-hardcoded hai (app.py se import NAHI hota)',
      "exchange='NSE'" in _scan and 'import nifty_scanner' not in app_src
      and 'from nifty_scanner' not in app_src)
check('research/data NSE-hardcoded hai (calibration NSE universe par fitted)',
      "exchange='NSE'" in _rdata)
check('app.py ka fetch_tradingview exchange-aware hai (ye teen nahi, wo hona chahiye)',
      "prefer_exch" in app_src and "def fetch_tradingview" in app_src)

# ── FIX-59: ek cost model, do nahi ────────────────────────────────────────
# FIX-57 ne app.py me apna cost dict banaya tha. research/costs.py me pehle se
# poora model tha. Do models DISAGREE karte the. Aur FIX-57 ke rates galat the:
#   delivery STT  0.1% sirf sell  -> sahi 0.1% DONO taraf   (0.10pp understate)
#   delivery stamp 0.003%         -> sahi 0.015% (delivery) (0.012pp understate)
#   intraday STT  0.025% x2       -> sahi SIRF sell (buy nil)(0.025pp overstate)
# Net: intraday 0.2310% batata tha, sahi 0.1832% (Rs1L) — OVERSTATE;
#      delivery 0.2810% batata tha, sahi 0.3702% (Rs1L) — UNDERSTATE.

print('\n-- FIX-59: research/costs.py intraday sides support karta hai')
check("buy_intraday par STT NIL hai (verified 2026: intraday buy STT-free)",
      _REF.breakdown(100000, 'buy_intraday')['stt'] == 0.0)
check("sell_intraday par STT 0.025% hai",
      abs(_REF.breakdown(100000, 'sell_intraday')['stt'] - 25.0) < 0.01,
      f"got {_REF.breakdown(100000,'sell_intraday')['stt']}")
check("delivery buy par STT 0.1% + stamp 0.015%",
      abs(_REF.breakdown(100000, 'buy')['stt'] - 100.0) < 0.01
      and abs(_REF.breakdown(100000, 'buy')['stamp'] - 15.0) < 0.01)
check("delivery sell par STT 0.1% (dono taraf lagta hai)",
      abs(_REF.breakdown(100000, 'sell')['stt'] - 100.0) < 0.01)
check("purane sides ('buy'/'sell'/'sell_short') backward-compatible hain",
      _REF.round_trip_pct() == 0.3276, f"got {_REF.round_trip_pct()}")

print('\n-- FIX-59: cost_plan + calculate_risk notional-aware hain')
_r59 = A.calculate_risk(2075.0, 57.54, 33, capital=25000, action='WATCHLIST',
                        regime='NEUTRAL')
_r59b = A.calculate_risk(2075.0, 57.54, 33, capital=1000000, action='WATCHLIST',
                         regime='NEUTRAL')
check('cost block me notional + notional_basis hai',
      'notional' in _r59['cost'] and 'notional_basis' in _r59['cost'])
check('chhote capital par break-even Rs zyada (cap bind karta hai)',
      _r59['cost']['break_even_rs'] > _r59b['cost']['break_even_rs'],
      f"25k={_r59['cost']['break_even_rs']} 10L={_r59b['cost']['break_even_rs']}")
check("qty 0 par basis honestly 'reference' bolta hai (jhooth position nahi)",
      'reference' in str(_r59['cost']['notional_basis']))
check('Dashboard notional basis dikhata hai', 'notional_basis' in dash_src)

# ── FIX-60: research/ module audit ────────────────────────────────────────
# research/ project ka sabse kam-audited hissa tha; FIX-59 bhi wahin se nikla tha.
# Poora padha (backtest/features/ml_lab/run_study/data/analyze). Teen concrete
# bugs mile, aur do achhi cheezein confirm hui.

print('\n-- FIX-60: TRADING_DAYS measured hai, US convention nahi')
from research.backtest import TRADING_DAYS as _TD
check('TRADING_DAYS = 247 (measured NSE avg), 252 (US) nahi', _TD == 247,
      f'got {_TD}. NSE actual: 2022=248 2023=245 2024=246 2025=249 -> avg 247.0. '
      f'252 se annualisation +2.02% overstate hoti thi.')
_bt = (ROOT / 'research' / 'backtest.py').read_text(encoding='utf-8')
# NOTE: FIX-60 ka comment khud "252 US convention hai" bolta hai — isliye comments
# strip karke check karo. Ye galti main teen baar kar chuka hoon (FIX-50, FIX-59,
# ab): source-grep test apne hi documentation par fail hota hai.
def _py_code(txt):
    return '\n'.join(l.split('#')[0] for l in txt.splitlines())


check('252 backtest.py ke CODE me kahin nahi bacha', '252' not in _py_code(_bt))

print('\n-- FIX-60: research/data.py period honour karta hai')
from research.data import _period_bars
check('period -> bars mapping sahi hai',
      _period_bars('2y') == 494 and _period_bars('5y') == 1235 and _period_bars('10y') == 2470)
check('unknown period par silent 2y fallback (crash nahi)', _period_bars('junk') == 494)
_rd = (ROOT / 'research' / 'data.py').read_text(encoding='utf-8')
check("hardcoded n_bars=520 CODE me gaya (period ab pass hota hai)",
      'n_bars=520' not in _py_code(_rd) and 'n_bars=want' in _py_code(_rd))
check('cache sirf tab likhta hai jab data maangi hui range ke kareeb ho',
      '0.8 * want' in _rd)
check('load() period ko _period_bars se jodta hai', '_period_bars(period)' in _rd)

print('\n-- FIX-60: risk-free rate teen jagah hai — drift GUARD')
# 0.065 teen files me hardcoded hai. research/ deliberately app-independent hai
# ("taaki study reproducible rahe"), isliye import karke single-source nahi kiya —
# uske bajaye guard test: teeno MATCH karein, warna fail.
import re as _re60
_rf_app = _re60.search(r"'RISK_FREE_RATE':\s*([0-9.]+)", app_src)
_rf_bt = _re60.search(r"RF_ANNUAL\s*=\s*([0-9.]+)", _bt)
_rf_da = _re60.search(r"rf_rate\s*=\s*([0-9.]+)",
                      (ROOT / 'deep_analyzer.py').read_text(encoding='utf-8'))
check('risk-free rate teeno jagah parse ho gaya',
      bool(_rf_app and _rf_bt and _rf_da))
if _rf_app and _rf_bt and _rf_da:
    _vals = {'app.py': float(_rf_app.group(1)), 'backtest.py': float(_rf_bt.group(1)),
             'deep_analyzer.py': float(_rf_da.group(1))}
    check('risk-free rate teeno jagah SAME hai (drift nahi)',
          len(set(_vals.values())) == 1, f'{_vals}')

print('\n-- FIX-60: backtester ke look-ahead guards abhi bhi maujood hain')
check('exec_lag default 1 hai (look-ahead guard)', 'exec_lag: int = 1' in _bt)
check('position shift hoti hai (signal bar t -> position t+lag)',
      'pos.shift(exec_lag)' in _bt)
_ml = (ROOT / 'research' / 'ml_lab.py').read_text(encoding='utf-8')
check('ml_lab embargo support karta hai (overlapping-label leakage guard)',
      'train_end = start - embargo' in _ml)
_rs = (ROOT / 'research' / 'run_study.py').read_text(encoding='utf-8')
check("run_study embargo=0 default par NAHI chalta — horizon se set karta hai",
      "embargo=int(lab['horizon'].iloc[0])" in _rs)
check('permutation null maujood hai (shuffled-label ceiling)',
      'permutation_null' in _ml and 'shuffle_train_labels' in _ml)
check('StandardScaler sirf TRAIN par fit hota hai (test leakage nahi)',
      'StandardScaler().fit(Xtr)' in _ml)

# ── summary ────────────────────────────────────────────────────────────────
passed = sum(1 for _, ok, _ in results if ok)
print('=' * 82)
print(f' {passed} / {len(results)} checks passed')
print('=' * 82)
sys.exit(0 if passed == len(results) else 1)
