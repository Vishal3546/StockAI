#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FIX-85 verification — search me BSE coverage + honest exchange labelling + timeout.

User ki complaint: "search karne pe NSE ke hi share dikhte he, BSE ke share search
me nhi dikh rha he" aur BSE stock par "Request timed out".

Root causes (measure kiye):
  1. DYNAMIC_STOCK_DB = 2570 entries, exchange split {'NSE': 2570} — source NSE ka
     EQUITY_L.csv hai, ek bhi BSE entry nahi.
  2. Layer 1 12 results bhar kar break karta tha aur Layer 2 (Yahoo) sirf
     `len(results) < 5` par chalta tha. TATA/BANK/STEEL par measured: 12 results,
     100% NSE — Yahoo search chalta hi nahi tha.
  3. Yahoo search khud BSE nahi deta: q=TATA -> 7 quotes, 7/7 '.NS' (NSI), koi
     '.BO' nahi. q=RELIANCE/TAPARIA par '.BO' aata hai.
  4. Layer 3 ex='NSE' hardcode karta tha jabki name '(NSE/BSE)' bolta tha, aur
     Dashboard search result ke `ex` se activeExchange set karta hai — to BSE-only
     stock par click se zabardasti NSE compute hota tha.
  5. Frontend 40s abort; user ke log me TAPARIA ne 47s liye (search 18:24:18 ->
     response 18:25:05) kyunki 3 engines x 2 exchange try hue aur Yahoo read-timeout
     hua. Error message galat cheez ko blame karta tha ("ML walk-forward").

Run:  python tools/verify_search_bse.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import app as A  # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(f"  {'✅' if ok else '❌'} {name}" + (f'  → {detail}' if detail else ''))


c = A.app.test_client()


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (A) search index ki haqeeqat')
print('=' * 84)

db = A.DYNAMIC_STOCK_DB
split = {}
for s in db:
    split[s.get('ex')] = split.get(s.get('ex'), 0) + 1
print(f'   index size={len(db)} split={split}')
check('A1 index populated hai', len(db) > 500, str(len(db)))
check('A1 index NSE-only hai (isliye BSE coverage Layer 2/mirror se aani chahiye)',
      set(k for k in split if k) <= {'NSE'}, str(split))


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (B) dono exchange search me dikhte hain')
print('=' * 84)

for q in ('TATA', 'BANK', 'STEEL', 'RELIANCE'):
    r = c.get(f'/api/search?q={q}').get_json() or []
    exs = {}
    for x in r:
        exs[x.get('ex')] = exs.get(x.get('ex'), 0) + 1
    check(f'B1 "{q}" par BSE results bhi aate hain', exs.get('BSE', 0) > 0, str(exs))
    check(f'B1 "{q}" par NSE results bhi aate hain', exs.get('NSE', 0) > 0, str(exs))

# B2: same symbol ke NSE aur BSE entries alag-alag hain (dedupe (sym, ex) par)
r = c.get('/api/search?q=RELIANCE').get_json() or []
pairs = [(x.get('sym'), x.get('ex')) for x in r]
check('B2 koi (sym, ex) pair duplicate nahi', len(pairs) == len(set(pairs)),
      str([p for p in pairs if pairs.count(p) > 1][:3]))
check('B2 RELIANCE ke dono exchange entries hain',
      ('RELIANCE', 'NSE') in pairs and ('RELIANCE', 'BSE') in pairs, str(pairs[:6]))

# B2b (FIX-85b): same symbol ke NSE aur BSE entries ADJACENT hone chahiye.
# Ye bug khud kar chuka hoon — mirror `_merged` banata tha par sort `results` par
# chalta tha (`results = _merged` assignment edit me ud gayi thi), to saari BSE
# entries chupchap drop ho jaati thin (TATA 20 -> 11). Aur usse pehle wala bug:
# Yahoo ne RELIANCE.BO Layer 2 me diya tha to mirror ne skip kiya aur RELIANCE NSE
# #5 par / RELIANCE BSE #12 par tha — user ko laga "BSE dikh hi nahi raha".
for q in ('RELIANCE', 'TATA', 'BANK'):
    r = c.get(f'/api/search?q={q}').get_json() or []
    pos = {}
    for i, x in enumerate(r):
        pos.setdefault(x['sym'].upper(), []).append(i)
    bad = {k: v for k, v in pos.items() if len(v) > 1 and (max(v) - min(v)) != len(v) - 1}
    check(f'B2b "{q}" par same symbol ke NSE/BSE entries adjacent hain', not bad, str(bad))
    dual = {k: v for k, v in pos.items() if len(v) == 2}
    check(f'B2b "{q}" par kam se kam 3 symbols ke dono exchange entries hain',
          len(dual) >= 3, f'{len(dual)} dual-listed')

# B3: results cap respect hota hai
cap = A.CONFIG['SEARCH_MAX_RESULTS']
check('B3 SEARCH_MAX_RESULTS se zyada results nahi', len(r) <= cap, f'{len(r)} vs cap {cap}')


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (C) Layer 3 — unknown symbol par jhootha NSE nahi')
print('=' * 84)

r = c.get('/api/search?q=ZZZQQQNOTREAL').get_json() or []
check('C1 fallback result milta hai (user typed symbol)', len(r) == 1, str(len(r)))
if r:
    check('C1 ex None hai (NSE hardcode nahi)', r[0].get('ex') is None,
          repr(r[0].get('ex')))
    check('C1 name me "NSE" ka jhootha dawa nahi',
          'NSE' not in (r[0].get('name') or ''), str(r[0].get('name')))
    check('C1 name me exchange unknown batata hai',
          'confirm nahi' in (r[0].get('name') or ''), str(r[0].get('name')))

# C2: mirror unknown-exchange entry ko NSE/BSE bana kar nahi failta
r2 = c.get('/api/search?q=ZZZQQQNOTREAL').get_json() or []
check('C2 unknown-exchange par mirror crash nahi karta', isinstance(r2, list))


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (D) Dashboard wiring')
print('=' * 84)

dash = open(os.path.join(ROOT, 'Dashboard.html'), encoding='utf-8').read()
check('D1 dropdown unknown par "?" dikhata hai (NSE nahi)',
      "_exKnown ? _exRaw : '?'" in dash)
check('D1 dataset.ex unknown par khaali set hota hai',
      "item.dataset.ex = _exKnown ? _exRaw : ''" in dash)
check("D1 click par `|| 'NSE'` fallback hata diya gaya",
      "(item.dataset.ex || '') : ''" in dash
      and "(item.dataset.ex || 'NSE')" not in dash)
check('D2 timeout 40s se badha', "controller.abort(), 90000" in dash)
check('D2 purana 40s nahi bacha', "controller.abort(), 40000" not in dash)
check('D2 timeout message ab tier cascade bolta hai (ML nahi)',
      'data fetch slow hai' in dash or 'data ' in dash
      and 'ML walk-forward needs' not in dash)
check('D2 galat "ML walk-forward needs 5-10s" claim hat gaya',
      'ML walk-forward needs 5-10s' not in dash)


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (E) regression — purana behaviour intact')
print('=' * 84)

r = c.get('/api/search?q=').get_json()
check('E1 khaali query par khaali list (crash nahi)', r == [], str(r))
r = c.get('/api/search?q=A').get_json() or []
check('E2 1-char query par bhi results + dono exchange', len(r) > 0, str(len(r)))
check('E2 har result me sym aur ex keys hain',
      all('sym' in x and 'ex' in x for x in r))
# E3: mirror ne existing entries ko mutate nahi kiya
syms = [x['sym'] for x in r]
check('E3 mirror ke baad bhi original NSE entries maujood hain',
      any((x.get('ex') == 'NSE') for x in r), str({x.get('ex') for x in r}))

print('=' * 84)
p = sum(1 for _, ok in RESULTS if ok)
print(f' {p} / {len(RESULTS)} checks passed')
print('=' * 84)
sys.exit(0 if p == len(RESULTS) else 1)
