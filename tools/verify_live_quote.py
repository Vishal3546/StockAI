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
app_src = (ROOT / 'app.py').read_text()
try:
    compile(app_src, 'app.py', 'exec')
    check('app.py syntax', True)
except SyntaxError as e:
    check('app.py syntax', False, str(e))

html = (ROOT / 'Dashboard.html').read_text()
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
js_ok, js_detail = True, f'{len(scripts)} inline scripts'
for i, s in enumerate(scripts):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
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
    A.fetch_nse_live_ltp = lambda s: dict(canned)
    A.fetch_yahoo_live_ltp = lambda s: None
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
    A.fetch_yahoo_live_ltp = lambda s: None
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
check('SSE unified payload use karta hai', 'quote = get_live_quote(resolved)' in app_src)

# ── summary ────────────────────────────────────────────────────────────────
passed = sum(1 for _, ok, _ in results if ok)
print('=' * 82)
print(f' {passed} / {len(results)} checks passed')
print('=' * 82)
sys.exit(0 if passed == len(results) else 1)
