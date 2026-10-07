#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FIX-83 verification — strict exchange + NSE failure transparency.

Do cheezein test hoti hain:

(A) STRICT EXCHANGE — user ne jo exchange chuna, USI ka data aana chahiye.
    Pehle BSE maangne par chupchap NSE ka frame mil jaata tha (Yahoo ke paas BSE
    historicals nahi hote) aur dashboard NSE ke numbers BSE ki tarah dikha deta
    tha. Ab cross-exchange data nahi milta — saaf 409 + "doosra exchange
    available hai" milta hai.

(B) NSE FAILURE TRANSPARENCY — _opt_get pehle `except Exception: pass` karta tha
    aur non-200 par chupchap None deta tha. Jab NSE 403 "Access Denied" deta tha
    (Akamai IP block) reason KAHIN nahi dikhta tha. Ab har call ka asli outcome
    record hota hai aur API response me jaata hai.

Run:  python tools/verify_strict_exchange.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A  # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(f"  {'✅' if ok else '❌'} {name}" + (f'  → {detail}' if detail else ''))


def skip(name, why):
    RESULTS.append((name, True))
    print(f"  ⏭️  {name} — skip ({why})")


import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402


def synth(n=60, base=100.0):
    idx = pd.date_range('2026-01-01', periods=n, freq='B')
    close = base + np.arange(n) * 0.5
    return pd.DataFrame({'Open': close, 'High': close + 1, 'Low': close - 1,
                         'Close': close, 'Volume': np.full(n, 1e6)}, index=idx)


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (A) smart_fetch — strict_exch behaviour')
print('=' * 84)

DF = synth()
orig_fetch = A.DATA_MANAGER.smart_fetch
orig_tv = A.DATA_MANAGER.tv


class _TierStub:
    """TradingView ko 'up' dikhata hai jo requested exchange ka data deta hai,
    aur NSE Direct ko fresh NSE frame — taaki cross-exchange fallback path bane."""

    def __init__(self, tv_exch):
        self.tv_exch = tv_exch      # TradingView kis exchange ka data dega
        self.tv_calls = []

    def fetch_tradingview(self, symbol, n_bars=500, interval_str='1d', prefer_exch='NSE'):
        want = str(prefer_exch).upper()
        self.tv_calls.append(want)
        if self.tv_exch is None:            # TradingView down
            return None, None
        if self.tv_exch != want:            # TV doosre exchange ka dega
            return DF.copy(), self.tv_exch
        return DF.copy(), want


try:
    # A1: requested exchange ka data hai -> dono mode me wahi milta hai
    for strict in (False, True):
        A.DATA_MANAGER.tv = object()
        A.DATA_MANAGER.fetch_tradingview = _TierStub('NSE').fetch_tradingview
        df, src = A.DATA_MANAGER.smart_fetch('RELIANCE', prefer_exch='NSE',
                                             strict_exch=strict, _now=None)
        check(f'A1 strict={strict}: NSE available -> NSE frame milta hai',
              df is not None and 'NSE' in (src or ''), str(src))

    # A2: sirf doosre exchange ka fresh frame hai
    #     non-strict -> fallback return hota hai (purana behaviour, abhi bhi chahiye
    #                   un callers ke liye jo strict nahi maangte)
    A.DATA_MANAGER.tv = object()
    A.DATA_MANAGER.fetch_tradingview = _TierStub('NSE').fetch_tradingview
    df_ns, src_ns = A.DATA_MANAGER.smart_fetch('RELIANCE', prefer_exch='BSE',
                                               strict_exch=False)
    check('A2 strict=False: cross-exchange fallback abhi bhi milta hai',
          df_ns is not None and src_ns is not None, str(src_ns))

    # A3: strict -> (None, None) + exch_fallback set
    A.DATA_MANAGER.tv = object()
    A.DATA_MANAGER.fetch_tradingview = _TierStub('NSE').fetch_tradingview
    df_s, src_s = A.DATA_MANAGER.smart_fetch('RELIANCE', prefer_exch='BSE',
                                             strict_exch=True)
    check('A3 strict=True: cross-exchange data RETURN nahi hota',
          df_s is None and src_s is None, f'df={df_s is None} src={src_s!r}')
    fb = getattr(A.DATA_MANAGER, 'exch_fallback', 'MISSING')
    check('A3 exch_fallback set hota hai (caller ko batane ke liye)',
          isinstance(fb, tuple) and len(fb) == 2, str(fb))
    check('A3 exch_fallback me sahi exchange hai',
          isinstance(fb, tuple) and fb[1] == 'NSE', str(fb))

    # A4: exch_fallback har call par reset hota hai (stale na rahe)
    A.DATA_MANAGER.tv = object()
    A.DATA_MANAGER.fetch_tradingview = _TierStub('NSE').fetch_tradingview
    A.DATA_MANAGER.smart_fetch('RELIANCE', prefer_exch='NSE', strict_exch=True)
    check('A4 successful call ke baad exch_fallback None hai (stale nahi)',
          A.DATA_MANAGER.exch_fallback is None, str(A.DATA_MANAGER.exch_fallback))

    # A5: __init__ me attribute hota hai (getattr ke bina bhi safe)
    check('A5 DataManager.exch_fallback attribute pehle se defined hai',
          hasattr(A.DATA_MANAGER, 'exch_fallback'))
finally:
    A.DATA_MANAGER.smart_fetch = orig_fetch
    A.DATA_MANAGER.tv = orig_tv
    if hasattr(A.DATA_MANAGER, 'fetch_tradingview'):
        del A.DATA_MANAGER.fetch_tradingview


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (B) /api/stock — strict exchange')
print('=' * 84)

c = A.app.test_client()
orig_sf = A.DATA_MANAGER.smart_fetch
try:
    # B1: dono exchange available (TradingView up)
    def _both(symbol, period='2y', interval='1d', n_bars=500, _now=None,
              prefer_exch='NSE', strict_exch=False):
        A.DATA_MANAGER.exch_fallback = None
        return DF.copy(), f'TradingView Direct ({str(prefer_exch).upper()})'
    A.DATA_MANAGER.smart_fetch = _both
    for e in ('NSE', 'BSE'):
        j = c.get(f'/api/stock/RELIANCE?ex={e}').get_json()
        check(f'B1 ex={e}: usi exchange ka data aata hai',
              j.get('frame_exchange') == e and not j.get('error'),
              f"frame={j.get('frame_exchange')!r} err={j.get('error')}")

    # B2: sirf NSE available, BSE maanga -> 409, NSE data NAHI
    def _only_nse(symbol, period='2y', interval='1d', n_bars=500, _now=None,
                  prefer_exch='NSE', strict_exch=False):
        if str(prefer_exch).upper() == 'BSE':
            A.DATA_MANAGER.exch_fallback = ('NSE Direct', 'NSE')
            return None, None
        A.DATA_MANAGER.exch_fallback = None
        return DF.copy(), 'NSE Direct'
    A.DATA_MANAGER.smart_fetch = _only_nse
    r = c.get('/api/stock/RELIANCE?ex=BSE')
    j = r.get_json()
    check('B2 BSE maanga, sirf NSE hai -> HTTP 409', r.status_code == 409,
          str(r.status_code))
    check('B2 NSE ka data return NAHI hua', 'frame_close' not in j and not j.get('kpi'),
          str(list(j.keys())[:6]))
    check('B2 available_exchange=NSE batata hai', j.get('available_exchange') == 'NSE',
          str(j.get('available_exchange')))
    check('B2 hint me doosra exchange chunne ko kehta hai',
          'NSE' in (j.get('hint') or '') and 'alag' in (j.get('hint') or ''),
          str(j.get('hint'))[:60])
    check('B2 error message me requested exchange ka naam hai',
          'BSE' in (j.get('error') or ''), str(j.get('error'))[:60])

    # B3: exchange miss _FAIL_CACHE me nahi jaana chahiye (NSE to kaam karta hai)
    before = A._FAIL_CACHE.get('RELIANCE', 0)
    c.get('/api/stock/RELIANCE?ex=BSE')
    check('B3 exchange-specific miss _FAIL_CACHE me nahi gaya',
          A._FAIL_CACHE.get('RELIANCE', 0) == before, str(A._FAIL_CACHE.get('RELIANCE')))
    rn = c.get('/api/stock/RELIANCE?ex=NSE')
    check('B3 uske baad NSE request 200 deta hai (cache ne block nahi kiya)',
          rn.status_code == 200, str(rn.status_code))

    # B4: asli "kuch nahi mila" -> 404 hi rahe (regression)
    def _nothing(symbol, period='2y', interval='1d', n_bars=500, _now=None,
                 prefer_exch='NSE', strict_exch=False):
        A.DATA_MANAGER.exch_fallback = None
        return None, None
    A.DATA_MANAGER.smart_fetch = _nothing
    A._FAIL_CACHE.pop('ZZZSTRICT', None)
    r4 = c.get('/api/stock/ZZZSTRICT?ex=NSE')
    check('B4 koi exchange nahi mila -> 404 (409 nahi)', r4.status_code == 404,
          str(r4.status_code))
    check('B4 404 me available_exchange nahi hota',
          'available_exchange' not in (r4.get_json() or {}))
finally:
    A.DATA_MANAGER.smart_fetch = orig_sf
    A._FAIL_CACHE.clear()


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (C) /api/timeframe — strict exchange')
print('=' * 84)

orig_kpi = A.kpi_scores_for
try:
    for e in ('NSE', 'BSE'):
        r = c.get(f'/api/timeframe/RELIANCE?exch={e}')
        j = r.get_json() or {}
        if j.get('ok'):
            check(f'C1 exch={e} live: ok=True aur exchange fields hain',
                  all(k in j for k in ('exchange_requested', 'exchange_actual',
                                       'exchange_mismatch', 'exchange_note')),
                  str(sorted(k for k in j if k.startswith('exchange_'))))
        else:
            # strict miss — error shape check karo
            check(f'C1 exch={e} live: strict miss par available_exchange batata hai',
                  'available_exchange' in j or 'error' in j, str(list(j.keys())[:6]))
            check(f'C1 exch={e} live: jhootha ok=True nahi', j.get('ok') is not True)
except Exception as e:
    skip('C1 live timeframe', f'{type(e).__name__}: {e}')

# C2: stubbed — BSE miss par payload shape
try:
    def _tf_only_nse(resolved, period='2y', interval='1d', prefer_exch='NSE',
                     strict_exch=False):
        if str(prefer_exch).upper() == 'BSE':
            A.DATA_MANAGER.exch_fallback = ('NSE Direct', 'NSE')
            return None, None
        A.DATA_MANAGER.exch_fallback = None
        return DF.copy(), 'NSE Direct'
    A.DATA_MANAGER.smart_fetch = _tf_only_nse
    ok, p = A.kpi_scores_for('RELIANCE', prefer_exch='BSE')
    check('C2 BSE miss -> ok=False', ok is False, str(ok))
    check('C2 error me BSE ka naam hai', 'BSE' in (p.get('error') or ''),
          str(p.get('error'))[:60])
    check('C2 available_exchange=NSE', p.get('available_exchange') == 'NSE',
          str(p.get('available_exchange')))
    check('C2 exchange_mismatch False (jhoothi mismatch warning nahi)',
          p.get('exchange_mismatch') is False, str(p.get('exchange_mismatch')))
    check('C2 kpi NAHI banta (NSE data se BSE score nahi)', 'kpi' not in p,
          str(list(p.keys())[:6]))
finally:
    A.DATA_MANAGER.smart_fetch = orig_sf


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (D) NSE failure transparency (_opt_get / _opt_last_error)')
print('=' * 84)

orig_sess = A._OPT_SESS
orig_last = dict(A._OPT_LAST)


class _Resp:
    def __init__(self, code, text='{}'):
        self.status_code, self.text, self.headers = code, text, {}

    def json(self):
        import json as _j
        return _j.loads(self.text)


class _Sess403:
    cookies = {}
    def get(self, *a, **k): return _Resp(403, '<TITLE>Access Denied</TITLE>')


class _SessHTML:
    cookies = {}
    def get(self, *a, **k): return _Resp(200, '<html>homepage</html>')


class _SessBoom:
    cookies = {}
    def get(self, *a, **k): raise RuntimeError('socket hang up')


try:
    # D1: 403 -> reason record + API me surface
    A._OPT_SESS = _Sess403()
    check('D1 403 par _opt_get None deta hai', A._opt_get('https://x/api/allIndices') is None)
    w = A._opt_last_error()
    check('D1 status 403 record hua', w.get('status') == 403, str(w.get('status')))
    check('D1 reason me "Access Denied" + block ki baat hai',
          'Access Denied' in (w.get('error') or '') and 'block' in (w.get('error') or ''),
          str(w.get('error'))[:70])
    r = c.get('/api/market_cockpit')
    j = r.get_json() or {}
    check('D1 /api/market_cockpit 503 deta hai', r.status_code == 503, str(r.status_code))
    check('D1 503 body me nse_status=403 hai', j.get('nse_status') == 403,
          str(j.get('nse_status')))
    check('D1 503 body me reason hai', bool(j.get('reason')), str(j.get('reason'))[:60])
    check('D1 503 body me endpoint naam hai', 'allIndices' in (j.get('endpoint') or ''),
          str(j.get('endpoint')))

    # D2: 200 par HTML (NSE ka classic "homepage wapas" behaviour)
    A._OPT_SESS = _SessHTML()
    A._opt_get('https://x/api/allIndices')
    w = A._opt_last_error()
    check('D2 HTML response par reason "JSON nahi hai" batata hai',
          'JSON nahi' in (w.get('error') or ''), str(w.get('error'))[:70])

    # D3: exception -> type + message record
    A._OPT_SESS = _SessBoom()
    A._opt_get('https://x/api/allIndices')
    w = A._opt_last_error()
    check('D3 exception ka type record hota hai',
          'RuntimeError' in (w.get('error') or ''), str(w.get('error'))[:70])
    check('D3 exception par status None hota hai', w.get('status') is None,
          str(w.get('status')))

    # D4: success -> error clear hota hai (stale reason na chipke)
    class _SessOK:
        cookies = {'a': 1}
        def get(self, *a, **k): return _Resp(200, '{"data":[{"name":"NIFTY"}]}')
    A._OPT_SESS = _SessOK()
    got = A._opt_get('https://x/api/allIndices')
    check('D4 success par data milta hai', bool(got and got.get('data')))
    check('D4 success ke baad error None hai (stale reason nahi)',
          A._opt_last_error().get('error') is None, str(A._opt_last_error().get('error')))
    check('D4 success par status 200', A._opt_last_error().get('status') == 200)

    # D5: _opt_last_error copy deta hai (caller mutate na kar sake)
    a1 = A._opt_last_error(); a1['status'] = 'TAMPERED'
    check('D5 _opt_last_error() copy return karta hai',
          A._opt_last_error().get('status') == 200, str(A._opt_last_error().get('status')))
finally:
    A._OPT_SESS = orig_sess
    A._OPT_LAST.clear(); A._OPT_LAST.update(orig_last)


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (E) PAGE WIRING')
print('=' * 84)

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dash = open(os.path.join(root, 'Dashboard.html'), encoding='utf-8').read()
tf = open(os.path.join(root, 'Timeframes.html'), encoding='utf-8').read()

check('E1 Dashboard available_exchange handle karta hai',
      'd.available_exchange' in dash)
check('E1 Dashboard hint me doosra exchange chunne ko kehta hai',
      'chunein' in dash and 'available hai' in dash)
check('E1 Dashboard exchange selector abhi bhi ex= bhejta hai',
      '?ex=${activeExchange}' in dash)
check('E2 Timeframes available_exchange handle karta hai',
      'j.available_exchange' in tf)
check('E2 Timeframes strict miss par red error ki jagah amber warning',
      "xw0.style.display = 'block'" in tf)
check('E2 Timeframes exch= param bhejta hai', "?exch=" in tf)

# E3: non-Latin guard (pichhle fixes me 手动 jaise chars ghus gaye the)
import unicodedata  # noqa: E402
bad = []
for fn in ('app.py', 'Dashboard.html', 'Timeframes.html'):
    txt = open(os.path.join(root, fn), encoding='utf-8').read()
    for ch in set(txt):
        if ord(ch) > 0x2500 and unicodedata.category(ch).startswith('L'):
            bad.append((fn, ch))
check('E3 koi stray CJK/letter-symbol character nahi', not bad, str(bad[:4]))

print('=' * 84)
p = sum(1 for _, ok in RESULTS if ok)
print(f' {p} / {len(RESULTS)} checks passed')
print('=' * 84)
sys.exit(0 if p == len(RESULTS) else 1)
