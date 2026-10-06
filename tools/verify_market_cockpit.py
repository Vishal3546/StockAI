#!/usr/bin/env python3
"""
tools/verify_market_cockpit.py — FIX-70 MARKET COCKPIT SUITE
================================================================================
  (A) UNIT — market_cockpit ke pure functions ek synthetic `/api/allIndices`
      payload par. Payload NSE ke ASLI shape me hai (per-index pe/pb/dy/advances
      STRING, top-level breadth INT) — warna string-coercion ka bug pakda hi
      nahi jaata. Values haath se gini hui hain.

  (B) WIRING/HONESTY — routes hain, page par disclosure hai, koi fake
      accuracy/edge claim nahi ghusa.

Chalao:  python3 tools/verify_market_cockpit.py
"""
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import market_cockpit as mc  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


PAYLOAD = {
    'timestamp': '06-Oct-2026 15:30:00',
    'advances': 884, 'declines': 1909, 'unchanged': 25,       # INT (whole market)
    'data': [
        {'key': 'BROAD MARKET INDICES', 'index': 'NIFTY 50', 'indexSymbol': 'NIFTY 50',
         'last': 22421.95, 'variation': -78.55, 'percentChange': -0.35,
         'open': 22500.5, 'high': 22550.0, 'low': 22380.0, 'previousClose': 22500.5,
         'yearHigh': 26277.35, 'yearLow': 21281.45,
         'pe': '26.45', 'pb': '4.01', 'dy': '1.2',             # STRING
         'advances': '19', 'declines': '31', 'unchanged': '0'},  # STRING
        {'key': 'BROAD MARKET INDICES', 'index': 'INDIA VIX', 'last': 13.85,
         'variation': 0.56, 'percentChange': 4.22},
        {'key': 'BROAD MARKET INDICES', 'index': 'NIFTY NEXT 50', 'last': 60000.0,
         'percentChange': 0.10},
        {'key': 'SECTORAL INDICES', 'index': 'NIFTY IT', 'last': 35000.0, 'percentChange': 1.25},
        {'key': 'SECTORAL INDICES', 'index': 'NIFTY AUTO', 'last': 21000.0, 'percentChange': 0.4},
        {'key': 'SECTORAL INDICES', 'index': 'NIFTY BANK', 'last': 52000.0, 'percentChange': -0.8},
        {'key': 'SECTORAL INDICES', 'index': 'NIFTY METAL', 'last': 9000.0,
         'percentChange': '-'},                                # NSE kabhi '-' deta hai
    ],
}

print('=' * 84)
print(' (A) UNIT — market_cockpit pure functions')
print('=' * 84)

b = mc.market_breadth(PAYLOAD)
check('breadth advances 884', b['advances'] == 884, str(b['advances']))
check('breadth declines 1909', b['declines'] == 1909, str(b['declines']))
check('breadth total = 884+1909+25 = 2818', b['total'] == 2818, str(b['total']))
check('A/D ratio = 884/1909 = 0.4631', abs(b['ad_ratio'] - 0.4631) < 1e-9, str(b['ad_ratio']))
check('% up = 884/2818 = 31.37', abs(b['pct_up'] - 31.37) < 1e-9, str(b['pct_up']))

cards = mc.index_cards(PAYLOAD, ['NIFTY 50', 'NIFTY BANK', 'INDIA VIX'])
check('index_cards maange gaye order me, teeno mile',
      [c['name'] for c in cards] == ['NIFTY 50', 'NIFTY BANK', 'INDIA VIX'],
      str([c['name'] for c in cards]))
n50 = cards[0]
check('NIFTY 50 last 22421.95', n50['last'] == 22421.95, str(n50['last']))
check('NIFTY 50 pct -0.35', n50['pct'] == -0.35, str(n50['pct']))
check("string 'pe' → float 26.45", n50['pe'] == 26.45 and isinstance(n50['pe'], float),
      repr(n50['pe']))
check("string 'advances' → float 19.0", n50['advances'] == 19.0, repr(n50['advances']))
check('jo index nahi hai wo skip (fake row nahi)',
      mc.index_cards(PAYLOAD, ['SENSEX', 'NIFTY 50'])[0]['name'] == 'NIFTY 50'
      and len(mc.index_cards(PAYLOAD, ['SENSEX', 'NIFTY 50'])) == 1)

vix = mc.find_vix(PAYLOAD)
check('VIX naam se mila (key group par depend nahi)',
      vix and vix['name'] == 'INDIA VIX' and vix['last'] == 13.85, str(vix))

sec = mc.sector_heatmap(PAYLOAD)
check('heatmap sirf SECTORAL INDICES (4)', len(sec) == 4, str([s['name'] for s in sec]))
check('heatmap best→worst sorted, None sabse aakhir me',
      [s['name'] for s in sec] == ['NIFTY IT', 'NIFTY AUTO', 'NIFTY BANK', 'NIFTY METAL'],
      str([s['name'] for s in sec]))
check("'-' percentChange → None (fake 0 nahi)", sec[-1]['pct'] is None, repr(sec[-1]['pct']))
check('NIFTY 50 heatmap me nahi (broad-market hai)',
      'NIFTY 50' not in [s['name'] for s in sec])

ex = mc.extremes(sec, 2)
check('top 2 = NIFTY IT, NIFTY AUTO', [s['name'] for s in ex['top']] == ['NIFTY IT', 'NIFTY AUTO'],
      str([s['name'] for s in ex['top']]))
check('bottom 2 = NIFTY BANK (worst pehle), phir AUTO',
      [s['name'] for s in ex['bottom']] == ['NIFTY BANK', 'NIFTY AUTO'],
      str([s['name'] for s in ex['bottom']]))

check('data age 15:30 → 15:42 = 12 min',
      mc.data_age_minutes('06-Oct-2026 15:30:00', now=datetime(2026, 10, 6, 15, 42)) == 12,
      str(mc.data_age_minutes('06-Oct-2026 15:30:00', now=datetime(2026, 10, 6, 15, 42))))
check('galat timestamp format → None (crash nahi)', mc.data_age_minutes('not-a-date') is None)
check('khaali timestamp → None', mc.data_age_minutes('') is None)
check('clock NSE se aage ho to age NEGATIVE aata hai (jhootha 0 nahi)',
      mc.data_age_minutes('06-Oct-2026 15:30:00', now=datetime(2026, 10, 6, 15, 20)) == -10,
      str(mc.data_age_minutes('06-Oct-2026 15:30:00', now=datetime(2026, 10, 6, 15, 20))))
# ── LIVE-FOUND (06-Oct-2026) regression guards ─────────────────────────────
check('LIVE BUG: NSE seconds-less timestamp "10:24" bhi parse hota hai',
      mc.data_age_minutes('06-Oct-2026 10:24', now=datetime(2026, 10, 6, 10, 36)) == 12,
      str(mc.data_age_minutes('06-Oct-2026 10:24', now=datetime(2026, 10, 6, 10, 36))))
# 10:24 IST = 04:54 UTC; machine UTC 04:57 par ho to age 3 min aana chahiye (-328 nahi)
check('LIVE BUG: machine ka timezone UTC ho to bhi age IST-correct aata hai',
      mc.data_age_minutes('06-Oct-2026 10:24',
                          now=datetime(2026, 10, 6, 4, 57, tzinfo=timezone.utc)) == 3,
      str(mc.data_age_minutes('06-Oct-2026 10:24',
                              now=datetime(2026, 10, 6, 4, 57, tzinfo=timezone.utc))))
check('naive now ko IST maana jaata hai (tests deterministic rehte hain)',
      mc.data_age_minutes('06-Oct-2026 10:24', now=datetime(2026, 10, 6, 10, 24)) == 0)
_z = mc.index_cards({'data': [{'index': 'NIFTY FINANCIAL SERVICES 25/50', 'last': 26726.15,
                               'yearHigh': 0.0, 'yearLow': 0.0}]},
                    ['NIFTY FINANCIAL SERVICES 25/50'])[0]
check('LIVE BUG: NSE naye index par yearHigh/low 0 bhejta hai → None (fake 0 nahi)',
      _z['year_high'] is None and _z['year_low'] is None,
      f"year_high={_z['year_high']} year_low={_z['year_low']}")
check('LIVE BUG: asli yearHigh 0 nahi hai to preserve hota hai',
      mc.index_cards({'data': [{'index': 'X', 'yearHigh': 26373.2}]}, ['X'])[0]['year_high']
      == 26373.2)

# edge cases — khaali/adhoora data par crash ya fake value nahi
check('khaali payload → breadth None', mc.market_breadth({}) is None)
check('sab breadth fields None → None',
      mc.market_breadth({'advances': None, 'declines': None, 'unchanged': None}) is None)
_p = mc.market_breadth({'advances': 100})
check('sirf advances → total 100, ad_ratio None (0 se divide nahi)',
      _p['total'] == 100 and _p['ad_ratio'] is None and _p['pct_up'] == 100.0, str(_p))
_z = mc.market_breadth({'advances': 100, 'declines': 0})
check('declines 0 → ad_ratio None (ZeroDivisionError nahi)', _z['ad_ratio'] is None, str(_z))
check('khaali payload → heatmap []', mc.sector_heatmap({}) == [])
check('payload None → cards []', mc.index_cards(None, ['NIFTY 50']) == [])
check('khaali sectors → extremes khaali', mc.extremes([], 3) == {'top': [], 'bottom': []})
check("_f('-') → None", mc._f('-') is None, repr(mc._f('-')))
check("_f('1,234.5') → 1234.5", mc._f('1,234.5') == 1234.5, repr(mc._f('1,234.5')))
check('_f(None) → None', mc._f(None) is None)

print('=' * 84)
print(' (B) WIRING + HONESTY')
print('=' * 84)

app_src = (ROOT / 'app.py').read_text(encoding='utf-8', errors='ignore')
page = (ROOT / 'Cockpit.html').read_text(encoding='utf-8', errors='ignore')
dash = (ROOT / 'Dashboard.html').read_text(encoding='utf-8', errors='ignore')
mod = (ROOT / 'market_cockpit.py').read_text(encoding='utf-8', errors='ignore')

check('app.py market_cockpit import karta hai', 'import market_cockpit' in app_src)
check('app.py /api/market_cockpit route hai', "@app.route('/api/market_cockpit')" in app_src)
check('app.py /cockpit page route hai', "@app.route('/cockpit')" in app_src)
check('app.py NSE allIndices endpoint use karta hai', 'api/allIndices' in app_src)
check('app.py FIX-69 ka _opt_get session reuse karta hai (naya session nahi)',
      '_opt_get(' in app_src.split('FIX-70')[1][:1200])
check('Dashboard se Cockpit page ka link hai', 'href="/cockpit"' in dash)
check('LIVE BUG: headline me NSE ka asli index naam hai (galat naam card drop karta tha)',
      'NIFTY FINANCIAL SERVICES 25/50' in app_src and "'NIFTY FIN SERVICE'" not in app_src)

check('market_cockpit pure hai (no flask/requests import)',
      'import flask' not in mod and 'import requests' not in mod)
check('Cockpit.html SEBI disclosure rakhta hai', 'not SEBI registered' in page)
check('Cockpit.html "no validated edge" disclaimer rakhta hai', 'validated edge' in page.lower())
check('Cockpit.html source NSE disclose karta hai (SENSEX claim nahi)',
      'NSE India' in page and 'SENSEX/BSE indices is page par nahi' in page)
check('Cockpit.html stale-data warning rakhta hai', 'stale' in page and 'age_minutes' in page)
check('Cockpit.html negative age par clock-skew batata hai (jhooth nahi)', 'clock skew' in page)
check('Cockpit.html relative /api/ fetch karta hai (localhost hardcode nahi)',
      "fetch('/api/market_cockpit')" in page and 'localhost' not in page)

_i = app_src.find('FIX-70: MARKET COCKPIT')
_j = app_src.find("@app.route('/')", _i) if _i >= 0 else -1
fix70 = app_src[_i:_j] if _i >= 0 and _j > _i else ''
check('app.py me FIX-70 block mila', bool(fix70), f'{len(fix70)} chars')

for label, src in (('market_cockpit', mod), ('Cockpit.html', page),
                   ('app.py FIX-70 block', fix70)):
    bad = [k for k in ('78% accuracy', 'win rate', 'winrate', 'accuracy of',
                       'guaranteed profit', 'guaranteed return')
           if k.lower() in src.lower()]
    check(f'{label} me fake accuracy/edge claim nahi', not bad, str(bad))

passed = sum(1 for _, ok, _ in results if ok)
print('=' * 84)
print(f' {passed} / {len(results)} checks passed')
print('=' * 84)
sys.exit(0 if passed == len(results) else 1)
