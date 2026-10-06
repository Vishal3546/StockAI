#!/usr/bin/env python3
"""
tools/verify_screener.py — FIX-76 AI SCREENER SUITE
================================================================================
  (A) LOAD/NORMALIZE — asli scan_results.json (30 rows, tracked in git) +
      synthetic. Field whitelist, number typing, symbol normalisation.

  (B) STALENESS — haath se gina hua. Market-open/closed ka claim NAHI hona
      chahiye (scanner manually chalta hai; 02-Oct-2026 Gandhi Jayanti thi —
      weekday tha, market band tha).

  (C) FILTER — har filter ka count MEASURED distribution se pin kiya gaya hai
      (30 rows: Banking 4, SELL 14, STRONG SELL 9, WATCH 6, BUY 1, score>=55 -> 1,
       vol_ratio>=2 -> 4, ml_used -> 8). Threshold guess nahi kiye.

  (D) SORT — desc/asc, None hamesha neeche (asc AUR desc dono me), aur sort key
      WHITELIST (user-supplied key seedha use nahi hota).

  (E) FACETS — poore dataset se counts (filtered se nahi).

  (F) HONESTY — ML fields ka matlab. ml_acc ek hi holdout split ka hai;
      ml_used_in_composite False matlab ML use hi nahi hua. Koi "accuracy"
      claim nahi hona chahiye.

  (G) PURITY + WIRING — screener.py pure module, routes, page, disclosures.

Chalao:  python3 tools/verify_screener.py
"""
import ast
import pathlib
import re
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import screener as S  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


def imported_modules(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding='utf-8'))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split('.')[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split('.')[0])
    return mods


SCAN = ROOT / 'scan_results.json'
print('=' * 84)
print(' (A) LOAD / NORMALIZE')
print('=' * 84)

if SCAN.exists():
    d = S.load_scan(str(SCAN))
    check('asli scan_results.json load hua (koi error nahi)', 'error' not in d,
          str(d.get('error')))
    check('30 rows', len(d.get('rows', [])) == 30, str(len(d.get('rows', []))))
    check('timestamp mila', bool(d.get('timestamp')), str(d.get('timestamp')))
    r0 = d['rows'][0]
    check('har row me symbol hai', all(r.get('symbol') for r in d['rows']))
    check('symbol UPPERCASE normalise', all(r['symbol'] == r['symbol'].upper() for r in d['rows']))
    check('numeric fields float me hain',
          all(isinstance(r0[k], float) for k in ('price', 'signal_score', 'rsi')
              if r0.get(k) is not None))
    check('ml_used_in_composite bool hai', isinstance(r0['ml_used_in_composite'], bool))
    # whitelist: internal/future fields leak na hon
    allowed = set(S.DISPLAY_FIELDS) | {'symbol'}
    leaked = {k for r in d['rows'] for k in r if k not in allowed}
    check('sirf whitelisted fields (koi leak nahi)', not leaked, str(leaked))
    check('total_scanned parse hua', d.get('total_scanned') == 30.0, str(d.get('total_scanned')))
else:
    check('asli scan_results.json load hua', False, 'file nahi mila — scanner chalao')
    d = {'rows': []}

check('missing file -> error message me command hai',
      'nifty_scanner.py' in S.load_scan(str(ROOT / 'nope_xyz.json'))['error'])
# empty / malformed file — real temp files par
(ROOT / '_t_empty.json').write_text('', encoding='utf-8')
(ROOT / '_t_bad.json').write_text('{not json', encoding='utf-8')
(ROOT / '_t_shape.json').write_text('{"results": "not a list"}', encoding='utf-8')
check('khaali file -> error', 'error' in S.load_scan(str(ROOT / '_t_empty.json')),
      S.load_scan(str(ROOT / '_t_empty.json'))['error'][:50])
check('invalid JSON -> error', 'error' in S.load_scan(str(ROOT / '_t_bad.json')))
check('galat shape -> error', 'error' in S.load_scan(str(ROOT / '_t_shape.json')))
for f in ('_t_empty.json', '_t_bad.json', '_t_shape.json'):
    (ROOT / f).unlink(missing_ok=True)

syn = S.normalize_rows([
    {'symbol': ' infy ', 'signal_score': '60', 'price': 1500, 'sector': 'IT',
     'signal': 'BUY', 'ml_used_in_composite': 1, 'rsi': None},
    {'symbol': '', 'signal_score': 5},                      # symbol khaali -> drop
    'not a dict',                                          # garbage -> drop
    {'symbol': 'X', 'signal_score': 'abc', 'price': '₹1,200.50'},
])
# NOTE: pehle yahan "3 valid rows" expect kiya tha — GALAT. Input me 4 entries
# hain: INFY (valid), khaali symbol (drop), 'not a dict' (drop), X (valid) = 2.
# Code sahi tha, expectation galat. (Yahi galti FIX-74 me 26.98 ke saath hui thi.)
check('normalize: 2 valid rows (khaali symbol + non-dict drop)', len(syn) == 2,
      str([r['symbol'] for r in syn]))
check('normalize: sirf INFY aur X bache', [r['symbol'] for r in syn] == ['INFY', 'X'],
      str([r['symbol'] for r in syn]))
check('normalize: symbol strip+upper', syn[0]['symbol'] == 'INFY', syn[0]['symbol'])
check('normalize: string number -> float', syn[0]['signal_score'] == 60.0,
      str(syn[0]['signal_score']))
check('normalize: rsi None reh jaata hai (0 nahi ban jaata)', syn[0]['rsi'] is None)
check('normalize: ml_used truthy -> True', syn[0]['ml_used_in_composite'] is True)
# screener._num currency symbol strip NAHI karta (fidii._f karta hai, kyunki
# wahan defensive chahiye tha). Yahan deliberate hai: scanner plain numbers
# likhta hai, aur agar kabhi garbage aaya to GALAT number dikhane se None
# behtar hai. Isliye expected None hai, 1200.5 nahi.
check('normalize: currency-wala string -> None (galat number nahi banata)',
      syn[1]['price'] is None, str(syn[1]['price']))
check('normalize: plain comma string parse hota hai',
      S.normalize_rows([{'symbol': 'A', 'price': '1,200.50'}])[0]['price'] == 1200.5)
check('normalize: "abc" -> None (fake 0 nahi)', syn[1]['signal_score'] is None)
check('normalize(non-list) -> []', S.normalize_rows('junk') == [])

print('=' * 84)
print(' (B) STALENESS — haath se gina hua')
print('=' * 84)
TS = '2026-10-03T12:27:39.808244'
base = S.parse_scan_ts(TS)
check('parse_scan_ts valid', base is not None and base.year == 2026 and base.month == 10
      and base.day == 3 and base.hour == 12, str(base))
check('parse_scan_ts tz-aware (naive nahi)', base.tzinfo is not None)
check('parse_scan_ts garbage -> None', S.parse_scan_ts('junk') is None)
check('parse_scan_ts empty -> None', S.parse_scan_ts('') is None)

# exactly 3 din baad = 4320 min -> "3.0 days", verdict old
n3 = datetime(2026, 10, 6, 12, 27, 39, tzinfo=S.IST)
st3 = S.staleness(TS, now=n3)
check('exactly 3 din -> 4320 min', st3['age_minutes'] == 4320.0, str(st3['age_minutes']))
check('exactly 3 din -> label "3.0 days"', st3['age_label'] == '3.0 days', st3['age_label'])
check('exactly 3 din -> verdict old', st3['verdict'] == 'old', st3['verdict'])
# 30 min baad -> fresh
# 12:57:39.000000 - 12:27:39.808244 = 29m 59.19s. int() truncate karta hai
# isliye label "29 min" hai, "30 min" nahi (pehle maine 30 expect kiya tha —
# timestamp ke .808244 microseconds bhool gaya). round() wala age_minutes 30.0
# aata hai kyunki wo round karta hai — dono sahi, alag rounding.
st_f = S.staleness(TS, now=datetime(2026, 10, 3, 12, 57, 39, tzinfo=S.IST))
check('~30 min -> verdict fresh', st_f['verdict'] == 'fresh', st_f['verdict'])
check('~30 min -> label "29 min" (int truncate, microseconds ki wajah se)',
      st_f['age_label'] == '29 min', st_f['age_label'])
check('~30 min -> age_minutes round hota hai (30.0)', st_f['age_minutes'] == 30.0,
      str(st_f['age_minutes']))
# 5 ghante baad -> today
st_t = S.staleness(TS, now=datetime(2026, 10, 3, 17, 27, 39, tzinfo=S.IST))
check('5 ghante -> verdict today', st_t['verdict'] == 'today' and st_t['age_label'] == '5.0 hours',
      f"{st_t['age_label']}/{st_t['verdict']}")
# future timestamp -> clock_skew (chhupana nahi)
st_c = S.staleness(TS, now=datetime(2026, 10, 3, 11, 27, 39, tzinfo=S.IST))
check('future timestamp -> clock_skew (negative age chhupta nahi)',
      st_c['verdict'] == 'clock_skew' and st_c['age_minutes'] < 0,
      f"{st_c['age_minutes']}/{st_c['verdict']}")
st_u = S.staleness('junk')
check('bad timestamp -> verdict unknown, age None',
      st_u['verdict'] == 'unknown' and st_u['age_minutes'] is None)
check('staleness market-open/closed claim NAHI karta',
      not any(k in str(st3).lower() for k in ('market open', 'market closed', 'trading day')))

print('=' * 84)
print(' (C) FILTER — measured counts (30-row scan se)')
print('=' * 84)
rows = d.get('rows', [])
if rows:
    check('koi filter nahi -> 30', len(S.filter_rows(rows)) == 30)
    check('sector=Banking -> 4', len(S.filter_rows(rows, sector='Banking')) == 4,
          str(len(S.filter_rows(rows, sector='Banking'))))
    check('sector case-sensitive exact match (banking -> 0)',
          len(S.filter_rows(rows, sector='banking')) == 0)
    check("sector='all' -> sab (filter apply nahi)",
          len(S.filter_rows(rows, sector='all')) == 30)
    check('signal=SELL -> 14', len(S.filter_rows(rows, signal='SELL')) == 14)
    check('signal=STRONG SELL -> 9', len(S.filter_rows(rows, signal='STRONG SELL')) == 9)
    check('signal=WATCH -> 6', len(S.filter_rows(rows, signal='WATCH')) == 6)
    check('signal=BUY -> 1', len(S.filter_rows(rows, signal='BUY')) == 1)
    check('signal lowercase bhi chalta hai -> 14',
          len(S.filter_rows(rows, signal='sell')) == 14)
    check('min_score=55 -> 1 (sirf KOTAKBANK)',
          len(S.filter_rows(rows, min_score=55)) == 1
          and S.filter_rows(rows, min_score=55)[0]['symbol'] == 'KOTAKBANK')
    check('min_score=40 -> 30 (sabse chhota score 40 hai)',
          len(S.filter_rows(rows, min_score=40)) == 30)
    check('min_score=61 -> 0 (max 60 hai)', len(S.filter_rows(rows, min_score=61)) == 0)
    check('min_vol=2 -> 4', len(S.filter_rows(rows, min_vol=2)) == 4)
    check('min_vol=99 -> 0', len(S.filter_rows(rows, min_vol=99)) == 0)
    check("q='infy' -> 1", len(S.filter_rows(rows, q='infy')) == 1)
    check('q case-insensitive', len(S.filter_rows(rows, q='INFY')) == 1)
    check('q substring (BANK -> 2+)', len(S.filter_rows(rows, q='BANK')) >= 2,
          str([r['symbol'] for r in S.filter_rows(rows, q='BANK')]))
    check('ml_used=True -> 8', len(S.filter_rows(rows, ml_used=True)) == 8)
    check('ml_used=False -> 22', len(S.filter_rows(rows, ml_used=False)) == 22)
    check('ml_used True+False = total 30',
          len(S.filter_rows(rows, ml_used=True)) + len(S.filter_rows(rows, ml_used=False)) == 30)
    combo = S.filter_rows(rows, sector='Banking', signal='SELL')
    check('combined filters AND hote hain (Banking+SELL <= 4)', len(combo) <= 4, str(len(combo)))
    check('koi match nahi -> []', S.filter_rows(rows, sector='NoSuchSector') == [])
    check('filters original list mutate nahi karte', len(rows) == 30)

print('=' * 84)
print(' (D) SORT')
print('=' * 84)
if rows:
    desc = S.sort_rows(rows, 'signal_score', 'desc')
    asc = S.sort_rows(rows, 'signal_score', 'asc')
    check('desc: pehla = KOTAKBANK (score 60)',
          desc[0]['symbol'] == 'KOTAKBANK' and desc[0]['signal_score'] == 60.0,
          f"{desc[0]['symbol']}/{desc[0]['signal_score']}")
    check('asc: pehla ka score 40 (sabse kam)', asc[0]['signal_score'] == 40.0,
          str(asc[0]['signal_score']))
    check('desc monotonically non-increasing',
          all(desc[i]['signal_score'] >= desc[i + 1]['signal_score'] for i in range(len(desc) - 1)))
    check('asc monotonically non-decreasing',
          all(asc[i]['signal_score'] <= asc[i + 1]['signal_score'] for i in range(len(asc) - 1)))
    check('sort row count preserve karta hai', len(desc) == 30 and len(asc) == 30)
    check('sort default = signal_score desc',
          S.sort_rows(rows)[0]['symbol'] == desc[0]['symbol'])
check('galat sort key -> whitelist fallback (crash nahi)',
      len(S.sort_rows([{'symbol': 'A', 'signal_score': 1}], '__import__', 'desc')) == 1)
check('galat order -> desc treat hota hai',
      S.sort_rows([{'symbol': 'A', 'rsi': 1}, {'symbol': 'B', 'rsi': 9}], 'rsi', 'sideways')[0]['symbol'] == 'B')
# None hamesha NEECHE — asc aur desc dono me. Ye bug easy hai: reverse=True ke
# saath sentinel-tuple ulta ho jaata hai.
mix = [{'symbol': 'A', 'rsi': None}, {'symbol': 'B', 'rsi': 50},
       {'symbol': 'C', 'rsi': None}, {'symbol': 'D', 'rsi': 10}]
check('desc me None neeche', [r['symbol'] for r in S.sort_rows(mix, 'rsi', 'desc')] == ['B', 'D', 'A', 'C'],
      str([r['symbol'] for r in S.sort_rows(mix, 'rsi', 'desc')]))
check('asc me None neeche', [r['symbol'] for r in S.sort_rows(mix, 'rsi', 'asc')] == ['D', 'B', 'A', 'C'],
      str([r['symbol'] for r in S.sort_rows(mix, 'rsi', 'asc')]))
check('string key (symbol) sort hota hai',
      [r['symbol'] for r in S.sort_rows(mix, 'symbol', 'asc')] == ['A', 'B', 'C', 'D'])
check('sab None ho to bhi crash nahi', len(S.sort_rows([{'symbol': 'A', 'rsi': None}], 'rsi')) == 1)

print('=' * 84)
print(' (E) FACETS')
print('=' * 84)
if rows:
    f = S.facets(rows)
    check('facets signals ka total = 30', sum(f['signals'].values()) == 30, str(sum(f['signals'].values())))
    check('facets sectors ka total = 30', sum(f['sectors'].values()) == 30)
    check('facets me 18 sectors hain (measured)', len(f['sectors']) == 18, str(len(f['sectors'])))
    check('facets signals counts exact', f['signals'] == {'BUY': 1, 'SELL': 14,
          'STRONG SELL': 9, 'WATCH': 6}, str(f['signals']))
    check('signals count-descending order me hain (UI ke liye)',
          list(f['signals'].values()) == sorted(f['signals'].values(), reverse=True))
    check('facets([]) -> khaali dicts', S.facets([]) == {'sectors': {}, 'signals': {}})
    check('missing sector -> "Unknown"', S.facets([{'symbol': 'A'}])['sectors'] == {'Unknown': 1})

print('=' * 84)
print(' (F) HONESTY — ML fields')
print('=' * 84)
nu = S.ml_note({'ml_used_in_composite': True})
nn = S.ml_note({'ml_used_in_composite': False})
check('used note me "walk-forward validated nahi" hai', 'walk-forward' in nu.lower()
      or 'walk-forward' in nu, nu[:70])
check('used note diagnostic bolta hai', 'diagnostic' in nu.lower())
check('not-used note clearly bolta hai ML use NAHI hua', 'use NAHI hua' in nn, nn[:70])
check('not-used note edge<0 ki wajah batata hai', 'edge < 0' in nn)
check('dono notes ALAG hain', nu != nn)
for label, s in (('ml_note used', nu), ('ml_note unused', nn)):
    bad = [k for k in ('accuracy of', 'guaranteed', 'sure shot', 'win rate',
                       'will go up', 'price target guaranteed') if k in s.lower()]
    check(f'{label} me koi prediction/guarantee claim nahi', not bad, str(bad))

print('=' * 84)
print(' (G) PURITY + WIRING + HONESTY')
print('=' * 84)
src = (ROOT / 'screener.py').read_text(encoding='utf-8')
page = (ROOT / 'Screener.html').read_text(encoding='utf-8')
dash = (ROOT / 'Dashboard.html').read_text(encoding='utf-8')
appsrc = (ROOT / 'app.py').read_text(encoding='utf-8')
coll = (ROOT / 'tools' / 'collect_fidii_daily.py').read_text(encoding='utf-8')

imp = imported_modules(ROOT / 'screener.py')
check('screener.py me flask/requests import nahi (pure module)',
      not (imp & {'flask', 'requests'}), str(sorted(imp)))
check('screener.py zoneinfo import nahi karta (fixed +05:30 offset)',
      'zoneinfo' not in imp, str(sorted(imp)))
check('screener.py IST fixed offset hai', 'timedelta(hours=5, minutes=30)' in src)

check("route '/api/screener' hai", "@app.route('/api/screener')" in appsrc)
check("route '/screener' hai", "@app.route('/screener')" in appsrc)
check('Screener.html serve hoti hai', "'Screener.html'" in appsrc)
check('import screener as _scr', 'import screener as _scr' in appsrc)
check('Dashboard me /screener link', 'href="/screener"' in dash)
check('page title', '<title>StockAI · Screener</title>' in page)
check('page /api/screener fetch karta hai', "'/api/screener'" in page)
check('page me localhost/127.0.0.1/:5000 nahi',
      not re.search(r'localhost|127\.0\.0\.1|:5000', page))
check('page error ko red class me dikhata hai', "className = 'err'" in page or "class=\"err\"" in page)

# disclosures
check('page "relative rank" disclosure deta hai', 'relative rank' in page.lower())
check('page "investment advice nahi" kehta hai', 'investment advice nahi' in page)
check('page SEBI-registered nahi disclosure deta hai', 'SEBI-registered advisor nahi' in page)
check('page batata hai scanner manually chalta hai', 'manually' in page)
check('page staleness banner dikhata hai', 'purana hai' in page and 'age_label' in page)
check('page ML+/ML- distinction dikhata hai', 'ML+' in page and 'ML−' in page)
check('page ml_used_in_composite se label decide karta hai', 'ml_used_in_composite' in page)
check('page walk-forward caveat dikhata hai', 'walk-forward' in page)

# sort whitelist server par enforce hota hai
check('app.py sort key whitelist use karta hai', '_scr.SORT_KEYS' in appsrc)
check('app.py galat number par 400 deta hai (chup-chaap ignore nahi)',
      '400' in appsrc[appsrc.index('# FIX-76'):appsrc.index("@app.route('/')")])
check('app.py facets POORE dataset se banata hai',
      "_scr.facets(rows)" in appsrc)
check('app.py scan error par bhi 200 + ok:false deta hai (page crash na ho)',
      "'ok': False" in appsrc)

# fake accuracy guard — FIX-76 ke changed files par scoped
blk = appsrc[appsrc.index('# FIX-76: AI SCREENER'):appsrc.index("@app.route('/')")]
for label, s in (('screener.py', src), ('Screener.html', page),
                 ('app.py FIX-76 block', blk)):
    bad = [k for k in ('78% accuracy', 'win rate', 'winrate', 'guaranteed profit',
                       'guaranteed return', 'sure shot', 'buy signal guaranteed',
                       'will go up', 'confirmed breakout') if k.lower() in s.lower()]
    check(f'{label} me fake accuracy/guarantee claim nahi', not bad, str(bad))

# FIX-75 ki tarah yahan bhi wahi galti dobara na ho: collector untouched
check('collect_fidii_daily.py abhi bhi zoneinfo import nahi karta',
      'zoneinfo' not in imported_modules(ROOT / 'tools' / 'collect_fidii_daily.py'))

passed = sum(1 for _, ok, _ in results if ok)
print('=' * 84)
print(f' {passed} / {len(results)} checks passed')
print('=' * 84)
sys.exit(0 if passed == len(results) else 1)
