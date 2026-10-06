#!/usr/bin/env python3
"""
tools/verify_timeframes.py — FIX-77 TIMEFRAMES SUITE
================================================================================
  (A) WIRING-EQUALITY — kpi_scores_for() ko STUBBED fetch ke saath chalaya
      jaata hai aur assert hota hai ki uska kpi EXACTLY wahi hai jo
      calculate_kpi_scores(calculate_all_indicators(df), fund_data) deta hai.
      Matlab naya endpoint apna alag score NAHI banata.

  (B) LIVE CROSS-PATH — asli network par /api/timeframe/<sym> ka kpi aur heavy
      /api/stock/<sym> ka kpi SAME hone chahiye. Ye is fix ka sabse zaroori
      check hai: do pages par do alag numbers na dikhein. Network na ho to
      SKIP (fail nahi) — par reason ke saath.

  (C) DEGENERATE DATA — khali/chhota frame par score ban kar nahi aana chahiye
      (FIX-28 wala _data_ok reuse hota hai).

  (D) CACHE + fund_data suffix logic (.BO for BSE, FIX-54).

  (E) HONESTY — score rule-based composite hai, prediction nahi. "basis" me
      kitne indicators measure hue wo dikhta hai.

Chalao:  python3 tools/verify_timeframes.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

results = []
skipped = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


def skip(name, reason):
    skipped.append((name, reason))
    print(f"  ⏭  {name} — SKIP: {reason}")


import app as A  # noqa: E402

# ── synthetic frame jo _data_ok pass kare (>=20 bars, Close non-zero) ──────
def synth(n=60, start=100.0):
    close = [start + i * 0.5 for i in range(n)]
    return pd.DataFrame({
        'Open': [c - 0.2 for c in close],
        'High': [c + 1.0 for c in close],
        'Low': [c - 1.0 for c in close],
        'Close': close,
        'Volume': [1_000_000 + i * 1000 for i in range(n)],
    })


class _StubFetch:
    """Sirf NETWORK stub hota hai — baaki sab asli code chalta hai."""
    def __init__(self, df, src='Yahoo Finance (NSE)'):
        self.df, self.src, self.calls = df, src, 0

    def __call__(self, symbol, period='2y', interval='1d', prefer_exch='NSE'):
        self.calls += 1
        self.last = (symbol, period, interval, prefer_exch)
        return (self.df.copy() if self.df is not None else None), self.src


print('=' * 84)
print(' (A) WIRING-EQUALITY — kpi_scores_for == calculate_kpi_scores')
print('=' * 84)

FD = {'pe_val': 21.7, 'roe_val': 0.42, 'debt_val': 46.3}
df0 = synth()
orig_fetch = A.DATA_MANAGER.smart_fetch
orig_fund = A._tf_fund_data
orig_resolve = A.resolve_symbol
try:
    stub = _StubFetch(df0)
    A.DATA_MANAGER.smart_fetch = stub
    A._tf_fund_data = lambda sym, src: dict(FD)
    A.resolve_symbol = lambda s: 'STUBSYM'

    ok, payload = A.kpi_scores_for('stubsym')
    check('kpi_scores_for ok=True (synthetic data par)', ok is True, str(payload.get('error')))
    check('payload me kpi hai', isinstance(payload.get('kpi'), dict)
          and set(payload['kpi']) == {'intraday', 'swing', 'longterm', 'master'},
          str(sorted(payload.get('kpi') or {})))
    # THE key equality: same df + same fund_data -> same kpi
    direct = A.calculate_kpi_scores(A.calculate_all_indicators(df0), FD)
    check('kpi EXACTLY wahi hai jo direct calculate_kpi_scores deta hai',
          payload['kpi'] == direct,
          f"{payload['kpi']['master']} vs {direct['master']}")
    check('har horizon me score/action/basis teeno hain',
          all(set(v) == {'score', 'action', 'basis'} for v in payload['kpi'].values()))
    check('score int 5..98 range me hai (clamp kaam karta hai)',
          all(isinstance(v['score'], int) and 5 <= v['score'] <= 98
              for v in payload['kpi'].values()),
          str({k: v['score'] for k, v in payload['kpi'].items()}))
    check('basis me "N/M indicators measured" format hai',
          all(re.match(r'^\d+/\d+ indicators measured$', v['basis'])
              for v in payload['kpi'].values()),
          str([v['basis'] for v in payload['kpi'].values()]))
    check('master = teeno ka average (int)',
          payload['kpi']['master']['score'] == int((
              payload['kpi']['intraday']['score'] + payload['kpi']['swing']['score']
              + payload['kpi']['longterm']['score']) / 3),
          str(payload['kpi']['master']['score']))
    check('fetch 2y/1d maangta hai', stub.last[1] == '2y' and stub.last[2] == '1d',
          str(stub.last))
    check('payload me price/bars/source/last_session hain',
          all(k in payload for k in ('price', 'bars', 'source', 'last_session')))
    check('bars = frame ki length', payload['bars'] == len(df0), str(payload['bars']))
    check('fund_data payload me wapas aata hai', payload['fund_data'] == FD)

    print()
    print(' (B) DEGENERATE DATA — score ban kar nahi aana chahiye')
    for label, bad, want in (('None frame', None, 'usable nahi'),
                             ('khaali frame', synth(0), 'usable nahi'),
                             ('sirf 5 bars', synth(5), 'usable nahi'),
                             ('Close sab zero', synth(30, 0.0), 'usable nahi')):
        if label == 'Close sab zero':
            z = synth(30); z['Close'] = 0.0; z['Open'] = 0.0; z['High'] = 0.0; z['Low'] = 0.0
            bad = z
        A.DATA_MANAGER.smart_fetch = _StubFetch(bad)
        o2, p2 = A.kpi_scores_for('stubsym')
        check(f'{label} -> ok False', o2 is False, str(p2.get('error'))[:60])
        check(f'{label} -> error me wajah hai', want in str(p2.get('error', '')),
              str(p2.get('error'))[:70])
        check(f'{label} -> koi kpi nahi banata', 'kpi' not in p2)

    print()
    print(' (C) fetch EXCEPTION -> clear error (traceback user ko nahi)')
    def boom(*a, **k):
        raise RuntimeError('network down')
    A.DATA_MANAGER.smart_fetch = boom
    o3, p3 = A.kpi_scores_for('stubsym')
    check('exception -> ok False', o3 is False)
    check('exception -> error message me type+reason', 'network down' in str(p3.get('error')),
          str(p3.get('error'))[:70])
finally:
    A.DATA_MANAGER.smart_fetch = orig_fetch
    A._tf_fund_data = orig_fund
    A.resolve_symbol = orig_resolve

print()
print(' (D) fund_data suffix logic (FIX-54: BSE frame -> .BO)')
src_txt = (ROOT / 'app.py').read_text(encoding='utf-8')
blk77 = src_txt[src_txt.index('# FIX-77: TIMEFRAME SCORES'):src_txt.index("@app.route('/')")]
check("BSE frame par '.BO' suffix", "'.BO' if '(BSE)' in str(daily_source)" in blk77)
check("warna '.NS'", "'.NS'" in blk77)
check('fund_data teeno wahi keys jo /api/stock/ use karta hai',
      all(k in blk77 for k in ('trailingPE', 'returnOnEquity', 'debtToEquity')))
check('_data_ok reuse hota hai (naya check nahi banaya)', '_data_ok(df, min_bars=20)' in blk77)
check('calculate_kpi_scores hi call hota hai (alag formula nahi)',
      'calculate_kpi_scores(dfi, fd)' in blk77)
check('calculate_all_indicators reuse hota hai', 'calculate_all_indicators(df)' in blk77)

print()
print(' (E) LIVE CROSS-PATH — /api/timeframe vs /api/stock')
c = A.app.test_client()
LIVE = []
for sym in ('RELIANCE', 'TCS'):
    try:
        r1 = c.get(f'/api/timeframe/{sym}')
        j1 = r1.get_json() or {}
        if not j1.get('ok'):
            raise RuntimeError(str(j1.get('error'))[:80])
        r2 = c.get(f'/api/stock/{sym}')
        j2 = r2.get_json() or {}
        k2 = j2.get('kpi')
        if not k2:
            raise RuntimeError('/api/stock ne kpi nahi diya')
        LIVE.append((sym, j1['kpi'], k2))
    except Exception as e:
        skip(f'{sym}: /api/timeframe == /api/stock kpi', f'{type(e).__name__}: {e}')

HZ = ('intraday', 'swing', 'longterm', 'master')


def _brief(k):
    return ' '.join(f'{h}={k[h]["score"]}' for h in HZ)


for sym, k1, k2 in LIVE:
    check(f'{sym}: timeframe kpi == stock kpi (charo horizon)', k1 == k2,
          f'timeframe[{_brief(k1)}] vs stock[{_brief(k2)}]')
if LIVE:
    check('dono symbols live verify hue', len(LIVE) == 2, str([s for s, _, _ in LIVE]))

print()
print(' (F) WIRING + HONESTY')
page = (ROOT / 'Timeframes.html').read_text(encoding='utf-8')
dash = (ROOT / 'Dashboard.html').read_text(encoding='utf-8')
check("route '/api/timeframe/<symbol>' hai", "@app.route('/api/timeframe/<symbol>')" in src_txt)
check("route '/timeframes' hai", "@app.route('/timeframes')" in src_txt)
check('Timeframes.html serve hoti hai', "'Timeframes.html'" in src_txt)
check('Dashboard me /timeframes link', 'href="/timeframes"' in dash)
check('page title', '<title>StockAI · Timeframes</title>' in page)
check('page /api/timeframe fetch karta hai', "'/api/timeframe/'" in page)
check('page me localhost/127.0.0.1/:5000 nahi',
      not re.search(r'localhost|127\.0\.0\.1|:5000', page))
check('page symbol ko encode karke bhejta hai', 'encodeURIComponent(sym)' in page)
check('page error par red message dikhata hai', "e.className='err'" in page)
check('page "basis" dikhata hai (kitne indicators measure hue)', 'basis' in page)
check('page "prediction ya probability nahi" kehta hai',
      'prediction ya probability nahi' in page)
check('page SEBI-registered nahi disclosure deta hai', 'SEBI-registered advisor nahi' in page)
check('page rule-based composite disclose karta hai', 'rule-based composite' in page)
check('page fund_data N/A dikhata hai (None ko 0 nahi banata)',
      "'N/A'" in page)
check('page auto-fetch NAHI karta (sirf ?sym= par)',
      'auto-fetch NAHI' in page or 'auto-fetch nahi' in page.lower())
check('page cached hone par batata hai', 'cache se' in page)

for label, s in (('app.py FIX-77 block', blk77), ('Timeframes.html', page)):
    bad = [k for k in ('78% accuracy', 'win rate', 'winrate', 'guaranteed profit',
                       'guaranteed return', 'sure shot', 'will go up',
                       'confirmed breakout', 'accuracy of') if k.lower() in s.lower()]
    check(f'{label} me fake accuracy/guarantee claim nahi', not bad, str(bad))

passed = sum(1 for _, ok, _ in results if ok)
print('=' * 84)
print(f' {passed} / {len(results)} checks passed' +
      (f'  (+{len(skipped)} skipped)' if skipped else ''))
print('=' * 84)
sys.exit(0 if passed == len(results) else 1)
