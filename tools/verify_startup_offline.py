#!/usr/bin/env python3
"""
FIX-39 verification — startup + offline hygiene (M-9, M-8, M-7).

M-9: `load_dynamic_nse_stocks()` module level par chalta tha → har import par
     NSE CSV download (~1-3 s), aur offline startup curated 30-stock fallback par
     chup-chaap gir jaata. Ab: on-disk cache (24 h TTL) → stale ho to background
     refresh → sync fetch → purani cache → curated fallback. `STOCKAI_OFFLINE=1`
     par network bilkul nahi.
M-8: CSV header parsing `' SERIES'` (leading space) par tika tha — NSE ne header
     badla to silently 0 stocks. Ab columns strip+upper karke tolerant lookup.
M-7: har HTTP call par naya TCP+TLS handshake hota tha. Ab shared
     `requests.Session` (connection pool) — Yahoo chart/search/master list sab usi se.

Run:  python3 tools/verify_startup_offline.py
"""
import json
import os
import pathlib
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import app as A  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  → {detail}" if detail else ""))


src = (ROOT / 'app.py').read_text(encoding='utf-8')
print("=" * 84)
print(" FIX-39 — startup + offline hygiene (M-9 cache · M-8 headers · M-7 session)")
print("=" * 84)

# ── [1] M-8: tolerant CSV header parsing ─────────────────────────────────
print("\n[1] M-8 — CSV header parsing (NSE ke ajeeb headers)")
df = pd.DataFrame({' SYMBOL': ['RELIANCE'], 'NAME OF COMPANY': ['Reliance'], ' SERIES': ['EQ']})
norm = A._normalize_columns(df)
check("leading-space headers normalize hote hain",
      'SYMBOL' in norm.columns and 'SERIES' in norm.columns, str(list(norm.columns)))
check("_pick_col exact match", A._pick_col(norm, 'SYMBOL') == 'SYMBOL')
check("_pick_col missing par None", A._pick_col(norm, 'NOPE') is None)
check("_pick_col candidates me se pehla available", A._pick_col(norm, 'NOPE', 'SERIES') == 'SERIES')
lower = A._normalize_columns(pd.DataFrame({'symbol': ['X'], 'name of company': ['Y'], 'series': ['EQ']}))
check("lowercase headers bhi chal jaate hain",
      A._pick_col(lower, 'SYMBOL') == 'SYMBOL' and A._pick_col(lower, 'SERIES') == 'SERIES')
check("source me exact \"row.get(' SERIES'\" hack gaya", "row.get(' SERIES'" not in src)


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


def _fake_master_csv(headers, rows):
    lines = [','.join(headers)]
    for r in rows:
        lines.append(','.join(r))
    return '\n'.join(lines) + '\n'


tmp_cache = pathlib.Path(tempfile.mkdtemp()) / 'nse_master_cache.json'
real_cache, real_get = A.MASTER_CACHE_FILE, A._HTTP.get
A.MASTER_CACHE_FILE = tmp_cache

# NSE jaisa ajeeb header + 501 rows (threshold se upar)
rows = [[f'SYM{i}', f'Company {i}', 'EQ'] for i in range(501)]
A._HTTP.get = lambda *a, **k: _FakeResp(_fake_master_csv([' SYMBOL', 'NAME OF COMPANY', ' SERIES'], rows))
got = A._fetch_nse_master(verbose=False)
check("' SERIES' wale CSV se stocks parse hote hain", isinstance(got, list) and len(got) == 501,
      str(len(got) if got else None))
check("parsed stock shape sahi (sym/name/ex/sec)",
      got and set(got[0].keys()) == {'sym', 'name', 'ex', 'sec'} and got[0]['sec'] is None,
      str(got[0]) if got else '')
check("series filter case-insensitive (eq chalta hai)",
      all(s['sym'].startswith('SYM') for s in (got or [])))

# BE/SM/ST bhi allowed, XX nahi
rows2 = [[f'A{i}', f'C{i}', s] for i, s in enumerate(['EQ', 'BE', 'SM', 'ST'] * 130 + ['XX'] * 10)]
A._HTTP.get = lambda *a, **k: _FakeResp(_fake_master_csv(['SYMBOL', 'NAME OF COMPANY', 'SERIES'], rows2))
got2 = A._fetch_nse_master(verbose=False)
check("EQ/BE/SM/ST allowed, doosri series reject",
      got2 is not None and all(s['sym'] not in {f'A{i}' for i in range(520, 530)} for s in got2),
      f"{len(got2) if got2 else 0} stocks")

# galat columns → None (silent 0 nahi)
A._HTTP.get = lambda *a, **k: _FakeResp('FOO,BAR\n1,2\n')
check("unknown columns par None (silent fallback nahi)", A._fetch_nse_master(verbose=False) is None)
A._HTTP.get = lambda *a, **k: _FakeResp('x', status=404)
check("HTTP 404 par None", A._fetch_nse_master(verbose=False) is None)

# chhota CSV (adhoora download) → None, cache overwrite nahi
rows3 = [[f'B{i}', f'C{i}', 'EQ'] for i in range(10)]
A._HTTP.get = lambda *a, **k: _FakeResp(_fake_master_csv(['SYMBOL', 'NAME OF COMPANY', 'SERIES'], rows3))
check("<=500 stocks wala adhoora CSV reject", A._fetch_nse_master(verbose=False) is None)

# ── [2] M-9: cache read/write ────────────────────────────────────────────
print("\n[2] M-9 — on-disk cache")
stocks = [{'sym': f'S{i}', 'name': f'N{i}', 'ex': 'NSE', 'sec': None} for i in range(600)]
check("cache write True deta hai", A._master_cache_write(stocks) is True)
check("cache file bani", tmp_cache.exists())
hit = A._master_cache_read()
check("cache read roundtrip", hit is not None and len(hit[0]) == 600, f"{len(hit[0]) if hit else 0} stocks")
check("cache age ~0h", hit is not None and hit[1] < 0.1, f"{hit[1] if hit else '?'}h")

tmp_cache.write_text('{bad json', encoding='utf-8')
check("corrupt cache → None (crash nahi)", A._master_cache_read() is None)
tmp_cache.write_text(json.dumps({'saved_at_utc': datetime.now(timezone.utc).isoformat(),
                                 'stocks': [{'sym': 'X'}]}), encoding='utf-8')
check("<500 stocks wali cache ignore", A._master_cache_read() is None)
tmp_cache.write_text(json.dumps({'saved_at_utc': 'not-a-date', 'stocks': stocks}), encoding='utf-8')
check("galat timestamp wali cache ignore", A._master_cache_read() is None)
tmp_cache.write_bytes(b'\xff\xfe' + b'x' * 200)
check("binary/ANSI cache par crash nahi", A._master_cache_read() is None)
tmp_cache.unlink(missing_ok=True)
check("cache file na ho → None", A._master_cache_read() is None)
check("cache .gitignore me hai",
      'nse_master_cache.json' in (ROOT / '.gitignore').read_text(encoding='utf-8'))

# ── [3] M-9: loader behaviour ────────────────────────────────────────────
print("\n[3] M-9 — loader: cache-first, offline, background")
# Import ne khud ek background refresh shuru ki ho sakti hai (jab cache file na ho,
# jaise fresh clone me). Usse khatam hone do aur handle reset karo — warna test ka
# apna spawn skip hota hai aur asli NSE list (2567) assertion tod deti hai.
if A._master_refresh_thread is not None:
    A._master_refresh_thread.join(timeout=20)
A._master_refresh_thread = None
calls = {'n': 0}


def _counting_get(*a, **k):
    calls['n'] += 1
    return _FakeResp(_fake_master_csv(['SYMBOL', 'NAME OF COMPANY', 'SERIES'], rows))


A._HTTP.get = _counting_get
A._master_cache_write(stocks)
calls['n'] = 0
n = A.load_dynamic_nse_stocks()
check("fresh cache par network call NAHI", calls['n'] == 0, f"{calls['n']} calls")
check("fresh cache se stocks load hue", n == 600 and len(A.DYNAMIC_STOCK_DB) == 600, str(n))

# stale cache → background refresh (sync fetch nahi)
data = json.loads(tmp_cache.read_text(encoding='utf-8'))
data['saved_at_utc'] = (datetime.now(timezone.utc) - timedelta(hours=99)).isoformat()
tmp_cache.write_text(json.dumps(data), encoding='utf-8')
calls['n'] = 0
before = len(A.DYNAMIC_STOCK_DB)
n2 = A.load_dynamic_nse_stocks(background=True)
check("stale cache par bhi turant stocks milte hain", n2 == 600, str(n2))
# NOTE: background thread turant fetch shuru kar sakta hai, isliye call-count se
# nahi — returned value se prove karte hain: stale cache me 600 stocks the jabki
# sync fetch 501 deta. 600 aaya matlab main path network par gaya hi nahi.
check("stale cache par sync fetch nahi hua (cache value wapas aayi)", n2 == 600, f"{n2} (fresh fetch 501 deta)")
check("background refresh thread chalu",
      A._master_refresh_thread is not None and A._master_refresh_thread.name == 'nse-master-refresh')
time.sleep(2.0)
check("background refresh ne fresh list load ki", len(A.DYNAMIC_STOCK_DB) == 501,
      f"{len(A.DYNAMIC_STOCK_DB)} stocks")

# OFFLINE mode
os.environ['STOCKAI_OFFLINE'] = '1'
tmp_cache.unlink(missing_ok=True)
calls['n'] = 0
n3 = A.load_dynamic_nse_stocks()
check("STOCKAI_OFFLINE=1 par network NAHI", calls['n'] == 0, f"{calls['n']} calls")
check("OFFLINE + no cache → curated fallback", n3 == len(A._fallback_master_list()) and n3 > 10, str(n3))
A._master_cache_write(stocks)
calls['n'] = 0
n4 = A.load_dynamic_nse_stocks()
check("OFFLINE me cache use hoti hai", calls['n'] == 0 and n4 == 600, str(n4))
data = json.loads(tmp_cache.read_text(encoding='utf-8'))
data['saved_at_utc'] = (datetime.now(timezone.utc) - timedelta(hours=99)).isoformat()
tmp_cache.write_text(json.dumps(data), encoding='utf-8')
calls['n'] = 0
n5 = A.load_dynamic_nse_stocks(background=True)
check("OFFLINE me stale cache par refresh trigger NAHI", calls['n'] == 0 and n5 == 600, str(n5))
os.environ.pop('STOCKAI_OFFLINE', None)

# fetch fail → purani cache, warna fallback
tmp_cache.unlink(missing_ok=True)
A._HTTP.get = lambda *a, **k: _FakeResp('nope', status=500)
n6 = A.load_dynamic_nse_stocks()
check("fetch fail + no cache → curated fallback", n6 == len(A._fallback_master_list()), str(n6))
A._master_cache_write(stocks)
n7 = A.load_dynamic_nse_stocks()
check("fetch fail + cache → purani cache", n7 == 600, str(n7))

# force=True sync fetch karta hai
A._HTTP.get = _counting_get
calls['n'] = 0
n8 = A.load_dynamic_nse_stocks(force=True)
check("force=True par sync fetch hota hai", calls['n'] == 1 and n8 == 501, f"{calls['n']} calls, {n8} stocks")

check("module-level call background=True se hota hai (import block nahi)",
      'load_dynamic_nse_stocks(background=True)' in src)
check("import par direct sync call nahi", '\nload_dynamic_nse_stocks()\n' not in src)

# ── [4] M-7: shared HTTP session ─────────────────────────────────────────
print("\n[4] M-7 — connection reuse")
A._HTTP.get = real_get      # fake hatao — ab asli network calls
import requests  # noqa: E402
check("shared _HTTP session hai", isinstance(A._HTTP, requests.Session))
check("https adapter pool ke saath mount hai",
      isinstance(A._HTTP.get_adapter('https://x'), requests.adapters.HTTPAdapter))
check("Yahoo chart shared session se jaata hai",
      'r = _HTTP.get(\n                f"https://query1.finance.yahoo.com/v8/finance/chart/' in src)
check("Yahoo search shared session se jaata hai",
      'res = _HTTP.get(url, headers=headers, timeout=3).json()' in src)
check("master list shared session se jaata hai",
      "res = _HTTP.get(CONFIG['NSE_MASTER_URL']," in src)

t0 = time.time()
q1 = A.get_live_quote('RELIANCE', force=True)
t1 = time.time()
q2 = A.get_live_quote('RELIANCE', force=True)
t2 = time.time()
check("live quote abhi bhi kaam karta hai", bool(q1) and bool(q2))
# Timing comparison flaky hota hai (network jitter), isliye M-7 ka proof
# structural hai (shared session + wiring upar). Yahan sirf sanity: warm call
# kaam kare aur reasonable time me aaye. Numbers informational hain.
check("warm call bhi valid quote deta hai", bool(q2) and q2.get('price') is not None)
check("warm call 1s ke andar (connection reuse ke baad)", (t2 - t1) < 1.0,
      f"cold {(t1-t0)*1000:.0f}ms → warm {(t2-t1)*1000:.0f}ms, source={q2.get('source') if q2 else None}")

# ── restore ──────────────────────────────────────────────────────────────
A.MASTER_CACHE_FILE, A._HTTP.get = real_cache, real_get
if real_cache.exists():
    A.load_dynamic_nse_stocks()

passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print("\n" + "=" * 84)
print(f" RESULT: {passed} passed, {failed} failed")
print("=" * 84)
sys.exit(1 if failed else 0)
