#!/usr/bin/env python3
"""
tools/verify_store_safety.py — FIX-98 SUITE
================================================================================
Teen cheezein jo deep-scan me measure karke mili thi, ab regress nahi honi
chahiye:

  (A) CONCURRENCY — per-user JSON stores (journal/watchlist/alerts) par
      read-modify-write pehle lock ke BAHAR tha. Measure kiya tha: 12 parallel
      POST /api/journal → file me sirf 4 records (8 lost writes).
  (B) NaN / Infinity — Flask ka default encoder `NaN` literal emit karta hai jo
      JSON spec me nahi hai; browser ka JSON.parse throw karta hai. Ab app-level
      provider non-finite float ko None banata hai.
  (C) INPUT — 200k char ka symbol accept ho raha tha; ab cap par reject.
      Aur hand-edited file ke non-dict rows se 500 nahi aana chahiye.

Chalao:  python3 tools/verify_store_safety.py
"""
import json
import os
import pathlib
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as A  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


TMP = tempfile.mkdtemp(prefix='store_verify')
A.JOURNAL_FILE = os.path.join(TMP, 'trade_journal.json')
A.WATCHLIST_FILE = os.path.join(TMP, 'watchlist.json')
A.ALERTS_FILE = os.path.join(TMP, 'alerts.json')
C = A.app.test_client()


# Probe route pehle request se PEHLE register karna zaroori hai — Flask uske baad
# setup allow nahi karta ("has already handled its first request").
@A.app.route('/__nan_probe')
def _nan_probe():
    from flask import jsonify
    return jsonify({'ok': True, 'v': float('nan'), 'nested': {'w': float('inf')}})

print("\n── A. Concurrency — lost writes ──────────────────────────────────")
N = 12
errs = []


def _jpost(i):
    try:
        r = C.post('/api/journal', json={'symbol': 'SYM%d' % i, 'side': 'LONG',
                                         'entry': 100, 'stop': 90, 'qty': 1})
        if r.status_code != 200:
            errs.append(('journal', i, r.status_code))
    except Exception as e:                                       # noqa: BLE001
        errs.append(('journal', i, type(e).__name__))


ts = [threading.Thread(target=_jpost, args=(i,)) for i in range(N)]
[t.start() for t in ts]
[t.join() for t in ts]
saved = json.load(open(A.JOURNAL_FILE, encoding='utf-8'))
check(f'{N} parallel journal POST -> {N} records (koi write lost nahi)',
      len(saved) == N and not errs, f'saved={len(saved)} errs={errs}')
check('file valid JSON list hai (torn write nahi)', isinstance(saved, list))
check('sab records ke unique ids hain',
      len({s.get('id') for s in saved}) == len(saved))

if os.path.exists(A.WATCHLIST_FILE):
    os.remove(A.WATCHLIST_FILE)
werr = []


def _wpost(i):
    try:
        r = C.post('/api/watchlist', json={'symbol': 'W%d' % i, 'exchange': 'NSE'})
        if r.status_code != 200:
            werr.append((i, r.status_code))
    except Exception as e:                                       # noqa: BLE001
        werr.append((i, type(e).__name__))


ts = [threading.Thread(target=_wpost, args=(i,)) for i in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
wsaved = json.load(open(A.WATCHLIST_FILE, encoding='utf-8'))
check('8 parallel watchlist POST -> 8 items', len(wsaved) == 8 and not werr,
      f'saved={len(wsaved)} errs={werr}')

# mixed POST + DELETE parallel: file corrupt na ho
ids = [s['id'] for s in json.load(open(A.JOURNAL_FILE, encoding='utf-8'))]


def _mix(k):
    try:
        if k % 2:
            C.delete('/api/journal?id=%s' % ids[k // 2])
        else:
            C.post('/api/journal', json={'symbol': 'M%d' % k, 'side': 'SHORT',
                                         'entry': 100, 'stop': 110, 'qty': 1})
    except Exception as e:                                       # noqa: BLE001
        errs.append(('mix', k, type(e).__name__))


ts = [threading.Thread(target=_mix, args=(k,)) for k in range(min(8, len(ids) * 2))]
[t.start() for t in ts]
[t.join() for t in ts]
try:
    mixed = json.load(open(A.JOURNAL_FILE, encoding='utf-8'))
    ok_mix = isinstance(mixed, list) and all(isinstance(x, dict) for x in mixed)
except Exception as e:                                           # noqa: BLE001
    mixed, ok_mix = None, False
    errs.append(('mixed-read', type(e).__name__))
check('parallel POST+DELETE ke baad bhi file valid (corrupt nahi)', ok_mix,
      f'rows={len(mixed) if mixed is not None else "n/a"}')

print("\n── B. NaN / Infinity JSON me leak nahi hona chahiye ──────────────")
s = A.app.json.dumps({'x': float('nan'), 'y': float('inf'), 'z': float('-inf')})
check('nan/inf -> null (NaN literal nahi)', 'NaN' not in s and 'Infinity' not in s, s)
check('output valid JSON hai (json.loads chalta hai)',
      json.loads(s) == {'x': None, 'y': None, 'z': None}, s)
_fp = A.app.json.dumps({'a': 1.5, 'b': -2.25})
check('normal floats untouched (fast path)', json.loads(_fp) == {'a': 1.5, 'b': -2.25}
      and 'null' not in _fp, _fp)
deep = {'a': [1.0, {'b': float('nan')}], 'c': (2.0, float('inf'))}
check('nested dict/list/tuple me bhi strip hota hai',
      json.loads(A.app.json.dumps(deep)) == {'a': [1.0, {'b': None}], 'c': [2.0, None]},
      A.app.json.dumps(deep))
try:
    import numpy as _np
    _nn = A.app.json.dumps({'n': float(_np.float64('nan'))})
    check('numpy float64 NaN bhi strip hota hai', json.loads(_nn) == {'n': None}, _nn)
except ImportError:
    print('  ⏭  numpy nahi hai — skip')


_r = C.get('/__nan_probe')
check('HTTP route par bhi NaN null banta hai (asli Flask path)', _r.status_code == 200
      and 'NaN' not in _r.get_data(as_text=True), _r.get_data(as_text=True)[:80])
try:
    _parsed = json.loads(_r.get_data(as_text=True))
    check('browser jaisa strict JSON.parse chalega', _parsed['v'] is None
          and _parsed['nested']['w'] is None)
except Exception as e:                                           # noqa: BLE001
    check('browser jaisa strict JSON.parse chalega', False, type(e).__name__)

print("\n── C. Input caps + non-dict rows ───────────────────────────────")
_long = 'Z' * 200000
check('journal: 200k-char symbol REJECT (422)',
      C.post('/api/journal', json={'symbol': _long, 'side': 'LONG', 'entry': 100,
                                   'stop': 90, 'qty': 1}).status_code == 422)
check('watchlist: 200k-char symbol REJECT (400)',
      C.post('/api/watchlist', json={'symbol': _long, 'exchange': 'NSE'}).status_code == 400)
check('alerts: 200k-char symbol REJECT (400)',
      C.post('/api/alerts', json={'symbol': _long, 'exchange': 'NSE',
                                  'condition': 'above', 'level': 10}).status_code == 400)
check(f'journal: {A.SYMBOL_MAX_LEN}-char symbol accept',
      C.post('/api/journal', json={'symbol': 'Q' * A.SYMBOL_MAX_LEN, 'side': 'LONG',
                                   'entry': 100, 'stop': 90, 'qty': 1}).status_code == 200)
check('journal: cap+1 reject',
      C.post('/api/journal', json={'symbol': 'Q' * (A.SYMBOL_MAX_LEN + 1), 'side': 'LONG',
                                   'entry': 100, 'stop': 90, 'qty': 1}).status_code == 422)

with open(A.WATCHLIST_FILE, 'w', encoding='utf-8') as f:
    json.dump([{'symbol': 'OK', 'exchange': 'NSE'}, 'junk', 7], f)
check('watchlist: junk rows ke saath POST 500 NAHI',
      C.post('/api/watchlist', json={'symbol': 'NEW', 'exchange': 'NSE'}).status_code == 200)
_w = json.load(open(A.WATCHLIST_FILE, encoding='utf-8'))
check('watchlist: rewrite ke baad sirf dicts', all(isinstance(x, dict) for x in _w),
      str([type(x).__name__ for x in _w]))
check('watchlist: junk rows ke saath DELETE 500 NAHI',
      C.delete('/api/watchlist?symbol=NEW&exchange=NSE').status_code == 200)

with open(A.ALERTS_FILE, 'w', encoding='utf-8') as f:
    json.dump([{'id': 5, 'symbol': 'OK', 'exchange': 'NSE', 'condition': 'above',
                'level': 10.0, 'fired': False}, 'junk'], f)
check('alerts: junk rows ke saath POST 500 NAHI',
      C.post('/api/alerts', json={'symbol': 'A2', 'exchange': 'BSE',
                                  'condition': 'below', 'level': 5}).status_code == 200)
check('alerts: junk rows ke saath DELETE 500 NAHI',
      C.delete('/api/alerts?id=5').status_code == 200)

print("\n── D. alerts/check — network lock ke bahar, write andar ────────")
with open(A.ALERTS_FILE, 'w', encoding='utf-8') as f:
    json.dump([{'id': 11, 'symbol': 'REL', 'exchange': 'NSE', 'condition': 'above',
                'level': 100.0, 'fired': False},
               {'id': 12, 'symbol': 'TCS', 'exchange': 'NSE', 'condition': 'below',
                'level': 100.0, 'fired': False}], f)
_orig_quote = A.get_live_quote
A.get_live_quote = lambda sym, prefer_exch='NSE', **k: {'price': 150.0}
try:
    _rc = C.post('/api/alerts/check')
    _jc = _rc.get_json()
    _byid = {a['id']: a for a in _jc.get('alerts', [])}
    check('alerts/check 200', _rc.status_code == 200, str(_rc.status_code))
    check("'above' alert fire hua (150 >= 100)", _byid.get(11, {}).get('fired') is True)
    check("'below' alert fire NAHI hua (150 > 100)", _byid.get(12, {}).get('fired') is False)
    check('checked=2', _jc.get('checked') == 2, str(_jc.get('checked')))
    # check ke dauraan add hua alert kho na jaaye (pehle snapshot write hota tha)
    check('check ke baad bhi dono alerts file me hain',
          len(json.load(open(A.ALERTS_FILE, encoding='utf-8'))) == 2)
finally:
    A.get_live_quote = _orig_quote

n_pass = sum(results)
n_fail = len(results) - n_pass
print('\n' + '=' * 82)
print(f" {n_pass} / {len(results)} checks passed" + (f"  ({n_fail} FAILED)" if n_fail else ''))
print('=' * 82)
sys.exit(1 if n_fail else 0)
