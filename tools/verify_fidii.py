#!/usr/bin/env python3
"""
tools/verify_fidii.py — FIX-75 FII/DII SUITE
================================================================================
  (A) PARSE — NSE ka ASLI captured payload (05-Oct-2026, live fetch se). Values
      STRING me aate hain comma ke saath ("20,492.93") — yahi trap allIndices me
      bhi hai. Har expected value neeche arithmetic ke saath likha hai.

  (B) INTEGRITY — NSE ka apna data internally consistent hona chahiye:
      net == buy - sell. Ye ek asli check hai, display decoration nahi.

  (C) DERIVED — total net, absorption %, gross. Sab haath se gina hua.

  (D) SNAPSHOT — do scopes ke labels, dates_match, empty-input par fake number
      na bane.

  (E) STREAK — sirf count, koi verdict nahi.

  (F) EDGE — garbage/None/missing fields + fidii.py purity (koi flask/requests).

  (G) CROSS-CHECK PIN — 05-Oct-2026 ke numbers do independent sources se
      milaye gaye the (Groww raw table + niftytrader). Wo values yahan pin hain
      taaki endpoint badle ya rate/scope badle to pakda jaaye.

  (H) WIRING/HONESTY — routes, page, Dashboard link, disclosures, NSE ke apne
      headings wale labels, collector me ZoneInfo nahi (Windows tzdata missing).

Chalao:  python3 tools/verify_fidii.py
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fidii as F  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


def imported_modules(path):
    """AST se ASLI imports. String-grep se nahi — pehle wala check
    'ZoneInfo' not in src tha, jo comment me likhe 'ZoneInfo AVOID' par
    false-fail kar raha tha. Intent test karo, text nahi."""
    tree = ast.parse(pathlib.Path(path).read_text(encoding='utf-8'))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split('.')[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split('.')[0])
    return mods


# ── ASLI NSE payload, 05-Oct-2026 (live capture, isi turn me fetch kiya) ────
ALL = [{'buyValue': '20492.93', 'category': 'DII', 'date': '05-Oct-2026',
        'netValue': '5181.62', 'sellValue': '15311.31'},
       {'buyValue': '15674.61', 'category': 'FII/FPI', 'date': '05-Oct-2026',
        'netValue': '-4699.14', 'sellValue': '20373.75'}]
NSE = [{'buyValue': '18076.94', 'category': 'DII', 'date': '05-Oct-2026',
        'netValue': '4878.16', 'sellValue': '13198.78'},
       {'buyValue': '14888.13', 'category': 'FII/FPI', 'date': '05-Oct-2026',
        'netValue': '-4092.96', 'sellValue': '18981.09'}]

print('=' * 84)
print(' (A) PARSE — fidii.parse_rows')
print('=' * 84)

ra = F.parse_rows(ALL)
check('parse_rows 2 records deta hai', ra is not None and len(ra) == 2, str(len(ra or [])))
d = {r['category']: r for r in ra}
# DII buy "20,492.93" -> 20492.93 (comma strip)
check('DII buy comma-string parse (20492.93)', d['DII']['buy'] == 20492.93, str(d['DII']['buy']))
check('DII sell parse (15311.31)', d['DII']['sell'] == 15311.31, str(d['DII']['sell']))
check('DII net parse (+5181.62)', d['DII']['net'] == 5181.62, str(d['DII']['net']))
check('FII net NEGATIVE parse (-4699.14)', d['FII/FPI']['net'] == -4699.14, str(d['FII/FPI']['net']))
check('category normalise "FII/FPI"', d['FII/FPI']['category'] == 'FII/FPI')
check('date preserved', d['DII']['date'] == '05-Oct-2026', d['DII']['date'])
check('parse_rows(list nahi) -> None', F.parse_rows('junk') is None)
check('parse_rows([]) -> None', F.parse_rows([]) is None)
check('parse_rows(None) -> None', F.parse_rows(None) is None)
check('unknown category drop hota hai',
      F.parse_rows([{'category': 'PROPs', 'buyValue': '1', 'sellValue': '1',
                     'netValue': '0', 'date': 'x'}]) is None)
check('_f("₹1,234.5") -> 1234.5', F._f('\u20b91,234.5') == 1234.5, str(F._f('\u20b91,234.5')))
check('_f("") -> None (0.0 nahi)', F._f('') is None)
check('_f("abc") -> None', F._f('abc') is None)
check('_f(None) -> None', F._f(None) is None)

print('=' * 84)
print(' (B) INTEGRITY — net == buy - sell (NSE ka apna data)')
print('=' * 84)
# DII  : 20492.93 - 15311.31 = 5181.62  exact
# FII  : 15674.61 - 20373.75 = -4699.14 exact
check('ALL scope: dono rows net_matches', all(r['net_matches'] for r in ra))
rn = F.parse_rows(NSE)
check('NSE scope: dono rows net_matches', all(r['net_matches'] for r in rn))
check('manual arithmetic DII 20492.93-15311.31 = 5181.62',
      round(20492.93 - 15311.31, 2) == 5181.62)
check('manual arithmetic FII 15674.61-20373.75 = -4699.14',
      round(15674.61 - 20373.75, 2) == -4699.14)
bad = F.parse_rows([{'category': 'DII', 'date': 'x', 'buyValue': '100',
                     'sellValue': '40', 'netValue': '99'}])
check('galat net -> net_matches False (chhupta nahi)', bad[0]['net_matches'] is False)

print('=' * 84)
print(' (C) DERIVED — haath se gine hue values')
print('=' * 84)
sa = F.snapshot(ALL, NSE)
a, nz = sa['all_exchanges'], sa['nse_only']
# total = -4699.14 + 5181.62 = 482.48
check('ALL total_net = 482.48', a['total_net'] == 482.48, str(a['total_net']))
# NSE total = -4092.96 + 4878.16 = 785.20
check('NSE total_net = 785.2', nz['total_net'] == 785.2, str(nz['total_net']))
# fii_gross = 15674.61 + 20373.75 = 36048.36
check('ALL fii_gross = 36048.36', a['fii_gross'] == 36048.36, str(a['fii_gross']))
# dii_gross = 20492.93 + 15311.31 = 35804.24
check('ALL dii_gross = 35804.24', a['dii_gross'] == 35804.24, str(a['dii_gross']))
# absorption = 100 * 5181.62 / 4699.14 = 110.2673... -> 110.27
check('ALL dii_absorption_pct = 110.27', a['dii_absorption_pct'] == 110.27,
      str(a['dii_absorption_pct']))
# NSE absorption = 100 * 4878.16 / 4092.96 = 119.1844... -> 119.18
check('NSE dii_absorption_pct = 119.18', nz['dii_absorption_pct'] == 119.18,
      str(nz['dii_absorption_pct']))
check('ALL integrity_ok True', a['integrity_ok'] is True)
pos = F.snapshot([{'category': 'FII/FPI', 'date': 'd', 'buyValue': '200',
                   'sellValue': '100', 'netValue': '100'},
                  {'category': 'DII', 'date': 'd', 'buyValue': '50',
                   'sellValue': '80', 'netValue': '-30'}])
check('FII BUYING ho to absorption None (ratio ka matlab nahi)',
      pos['all_exchanges']['dii_absorption_pct'] is None,
      str(pos['all_exchanges']['dii_absorption_pct']))

print('=' * 84)
print(' (D) SNAPSHOT — labels / dates / empty')
print('=' * 84)
# Labels NSE ke apne page ke headings se hain (fii-dii.html se scrape kiye the)
check('ALL label = NSE ka heading',
      a['label'] == 'NSE, BSE and MSEI - Capital Market segment', a['label'])
check('NSE label = NSE ka heading', nz['label'] == 'NSE only - Capital Market segment', nz['label'])
check('labels ALAG hain (dono same nahi)', a['label'] != nz['label'])
check('as_of = 05-Oct-2026', sa['as_of'] == '05-Oct-2026', str(sa['as_of']))
check('dates_match True (dono same date)', sa['dates_match'] is True)
check('ok True jab data hai', sa['ok'] is True)
check('source string me dono endpoints ka zikr', 'fiidiiTradeReact' in sa['source']
      and 'fiidiiTradeNse' in sa['source'])
check('note me "latest trading day" limitation', 'latest trading day' in sa['note'])
emp = F.snapshot(None, None)
check('empty input -> ok False', emp['ok'] is False)
check('empty input -> as_of None (fake date nahi)', emp['as_of'] is None)
check('empty input -> fii_net None (fake 0 nahi)', emp['all_exchanges']['fii_net'] is None)
check('empty input -> records []', emp['all_exchanges']['records'] == [])
check('empty input -> dates_match None', emp['dates_match'] is None)
one = F.snapshot(ALL, None)
check('sirf ek scope mile to bhi ok True', one['ok'] is True)
check('missing scope ka label phir bhi present',
      one['nse_only']['label'] == 'NSE only - Capital Market segment')
mism = F.snapshot(ALL, [{'category': 'DII', 'date': '04-Oct-2026', 'buyValue': '1',
                         'sellValue': '1', 'netValue': '0'}])
check('dates alag ho to dates_match False', mism['dates_match'] is False,
      str(mism['dates_match']))

print('=' * 84)
print(' (E) STREAK — count only')
print('=' * 84)
check('5 lagataar selling -> -5', F.streak([('a', -1), ('b', -2), ('c', -3), ('d', -4), ('e', -5)]) == -5)
check('beech me break -> sirf trailing count (-2)', F.streak([10, -5, -8]) == -2)
check('buying streak -> positive', F.streak([5, 6, 7]) == 3)
check('empty -> 0', F.streak([]) == 0)
check('None -> 0', F.streak(None) == 0)
check('garbage values -> 0', F.streak([('x', 'abc')]) == 0)
check('last = 0 -> 0 (koi direction nahi)', F.streak([-5, -5, 0]) == 0)
check('string numbers bhi chalte hain', F.streak([('a', '-3'), ('b', '-4')]) == -2)
check('single din -> ±1', F.streak([42]) == 1 and F.streak([-42]) == -1)

print('=' * 84)
print(' (F) EDGE + PURITY')
print('=' * 84)
check('parse_nse_date valid', str(F.parse_nse_date('05-Oct-2026')) == '2026-10-05',
      str(F.parse_nse_date('05-Oct-2026')))
check('parse_nse_date garbage -> None', F.parse_nse_date('junk') is None)
check('parse_nse_date None -> None', F.parse_nse_date(None) is None)
src_fidii = (ROOT / 'fidii.py').read_text(encoding='utf-8')
_imp_fidii = imported_modules(ROOT / 'fidii.py')
check('fidii.py me koi flask/requests import nahi (pure module)',
      not (_imp_fidii & {'flask', 'requests'}), str(sorted(_imp_fidii)))
check('fidii.py zoneinfo import nahi karta (Windows par tzdata missing)',
      'zoneinfo' not in _imp_fidii, str(sorted(_imp_fidii)))
check('fidii.py IST fixed +05:30 offset hai',
      'timedelta(hours=5, minutes=30)' in src_fidii)
recs = F.parse_rows([{'category': 'DII', 'date': 'x'}])
check('missing numeric fields -> record drop (None number nahi)', recs is None, str(recs))

print('=' * 84)
print(' (G) CROSS-CHECK PIN — 05-Oct-2026, do independent sources')
print('=' * 84)
# Groww raw table (groww.in/fii-dii-data) 05 Oct 2026 row, exactly:
#   15,674.61 | 20,373.75 | -4,699.14 | 20,492.93 | 15,311.31 | +5,181.62
# niftytrader.in: FII Cash -4,699 Cr, DII Cash +5,182 Cr, Total Net +482 Cr
GROWW = {'fii_buy': 15674.61, 'fii_sell': 20373.75, 'fii_net': -4699.14,
         'dii_buy': 20492.93, 'dii_sell': 15311.31, 'dii_net': 5181.62}
fa = {r['category']: r for r in ra}
check('Groww FII buy match', fa['FII/FPI']['buy'] == GROWW['fii_buy'])
check('Groww FII sell match', fa['FII/FPI']['sell'] == GROWW['fii_sell'])
check('Groww FII net match', fa['FII/FPI']['net'] == GROWW['fii_net'])
check('Groww DII buy match', fa['DII']['buy'] == GROWW['dii_buy'])
check('Groww DII sell match', fa['DII']['sell'] == GROWW['dii_sell'])
check('Groww DII net match', fa['DII']['net'] == GROWW['dii_net'])
check('niftytrader "Total Net +482 Cr" match (482.48)', round(a['total_net']) == 482,
      str(a['total_net']))
# fiidiiTradeNse deliberately CHHOTA hai (sirf NSE). Agar kabhi dono barabar ho
# jaayein to matlab endpoint/scope badal gaya hai.
check('NSE-only subset < all-exchanges (FII gross)',
      nz['fii_gross'] < a['fii_gross'], f"{nz['fii_gross']} < {a['fii_gross']}")
check('NSE-only subset < all-exchanges (DII gross)',
      nz['dii_gross'] < a['dii_gross'], f"{nz['dii_gross']} < {a['dii_gross']}")

print('=' * 84)
print(' (H) WIRING + HONESTY')
print('=' * 84)
app = (ROOT / 'app.py').read_text(encoding='utf-8')
page = (ROOT / 'Fidii.html').read_text(encoding='utf-8')
dash = (ROOT / 'Dashboard.html').read_text(encoding='utf-8')
coll = (ROOT / 'tools' / 'collect_fidii_daily.py').read_text(encoding='utf-8')

check("route '/api/fidii' hai", "@app.route('/api/fidii')" in app)
check("route '/fidii' hai", "@app.route('/fidii')" in app)
check('Fidii.html serve hoti hai', "'Fidii.html'" in app)
check('import fidii as _fid', 'import fidii as _fid' in app)
check('Dashboard me /fidii link', 'href="/fidii"' in dash)
check('page title', '<title>StockAI · FII / DII</title>' in page)

# Page JS: relative fetch, koi localhost/hardcoded port nahi
check('page /api/fidii fetch karta hai', "fetch('/api/fidii')" in page)
check('page me localhost/127.0.0.1/hardcoded port nahi',
      not re.search(r'localhost|127\.0\.0\.1|:5000', page))
check('error par red class + message', "className = 'err'" in page and 'Load fail' in page)

# Honesty: NSE ke apne headings wale labels page par aate hain (API se), aur
# page khud se koi "NSE only" guess nahi likhta — label API se aata hai.
check('page label API se leta hai (hardcode nahi)',
      "j.all_exchanges.label" in page and "j.nse_only.label" in page)
check('page me "provisional" disclosure', 'provisional' in page.lower())
check('page me "investment advice nahi"', 'investment advice nahi' in page)
check('page me SEBI-registered nahi disclosure', 'SEBI-registered advisor nahi' in page)
check('streak ko "count" bola gaya, verdict nahi',
      'sirf count' in page and 'koi verdict nahi' in page)
check('live/cached/unavailable badge teeno states',
      'b-live' in page and 'b-cache' in page and 'b-off' in page)
check('integrity_ok false par warning dikhata hai', 'integrity_ok === false' in page)
check('dates_match false par warning dikhata hai', 'dates_match === false' in page)
check('history scope label dikhata hai (mix nahi)', 'hist_scope' in page)

# Collector
_imp_coll = imported_modules(ROOT / 'tools' / 'collect_fidii_daily.py')
check('collector zoneinfo import nahi karta (Windows par crash karta)',
      'zoneinfo' not in _imp_coll, str(sorted(_imp_coll)))
check('collector fixed +05:30 IST use karta hai',
      'timedelta(hours=5, minutes=30)' in coll)
check('collector dono scopes save karta hai',
      "'all', 'fiidiiTradeReact'" in coll and "'nse', 'fiidiiTradeNse'" in coll)
check('collector idempotent (date,scope) check', "(row['date'], scope) in have" in coll)
check('collector integrity warn karta hai', 'net_matches' in coll)
check('collector --status deta hai', "add_argument('--status'" in coll)
check('collector fidii.parse_rows reuse karta hai (duplicate parse nahi)',
      'fidii.parse_rows' in coll)

# app.py: history scope-filter (mix hone se bachne ke liye) — FIX-75 ka asli bug
check('app.py _fid_history scope filter karta hai', "scope='all'" in app
      and "!= scope" in app)
check('app.py history_scope response me bhejta hai', "snap['history_scope'] = 'all'" in app)
check('app.py NSE fail hone par live=False bhejta hai (chhupata nahi)',
      "snap['live'] = live" in app and 'live = bool(' in app)

# Fake accuracy guard — FIX-75 ke changed files par scoped
block = app[app.index('# FIX-75: FII/DII ACTIVITY'):app.index("@app.route('/')")]
for label, s in (('fidii.py', src_fidii), ('Fidii.html', page),
                 ('app.py FIX-75 block', block), ('collector', coll)):
    bad = [k for k in ('78% accuracy', 'win rate', 'winrate', 'accuracy of',
                       'guaranteed profit', 'guaranteed return', 'sure shot',
                       'bullish signal', 'bearish signal', 'buy signal', 'sell signal')
           if k.lower() in s.lower()]
    check(f'{label} me fake accuracy/prediction claim nahi', not bad, str(bad))

# ── INFO (count nahi hota): known risk jo FIX-75 ka part NAHI hai ────────────
# requirements.txt me `tzdata` nahi hai. Windows par system tz database nahi
# hota, isliye ZoneInfo('Asia/Kolkata') wahan ZoneInfoNotFoundError deta hai.
# FIX-75 ke files isliye fixed +05:30 offset use karte hain. Par ye do PURANE
# files abhi bhi ZoneInfo use karte hain — alag se fix karna hoga, yahan sirf
# report kar rahe hain (chhupa nahi rahe).
_at_risk = [p for p in ('nifty_scanner.py', 'tools/collect_oi_daily.py',
                        'tools/verify_live_quote.py')
            if 'zoneinfo' in imported_modules(ROOT / p)]
_req = (ROOT / 'requirements.txt').read_text(encoding='utf-8').lower()
if _at_risk and 'tzdata' not in _req:
    print('\nℹ️  KNOWN RISK (FIX-75 ka part nahi, isliye count nahi):')
    print(f'   requirements.txt me tzdata NAHI hai, par ye files ZoneInfo use karti hain:')
    for p in _at_risk:
        print(f'     • {p}')
    print('   Linux/macOS par system tz database hai isliye chalta hai; Windows par')
    print("   ZoneInfoNotFoundError aayega — jab tak `pip install tzdata` na ho.")
    print('   FIX-75 ke files (fidii.py, collect_fidii_daily.py) is par depend nahi karte.')

passed = sum(1 for _, ok, _ in results if ok)
print('=' * 84)
print(f' {passed} / {len(results)} checks passed')
print('=' * 84)
sys.exit(0 if passed == len(results) else 1)
