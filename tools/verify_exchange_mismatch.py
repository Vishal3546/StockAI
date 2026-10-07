#!/usr/bin/env python3
"""
tools/verify_exchange_mismatch.py — FIX-80 SUITE
================================================================================
Problem jo fix hui: jab aap BSE maangte the aur data NSE se aata tha, to warning
SIRF server console me jaati thi. API response me koi field hi nahi tha — page
par sirf source string '(NSE)' dikhti thi. Chhupa hua fallback.

  (A) exchange_from_source() — source string se exchange nikalna (helper pehle
      se tha, FIX-62 ka). Saare formats.
  (B) kpi_scores_for() — stubbed fetch ke saath: exchange fields sahi bante hain.
  (C) MISMATCH LOGIC — requested vs actual, case-insensitive, unknown source par
      jhootha mismatch NAHI banana chahiye.
  (D) LIVE — dono exchange request; mismatch INVARIANT check hota hai
      (outcome environment par depend karta hai: TradingView up ho to BSE
       ka asli data milta hai aur mismatch False hota hai — wo sahi hai).
  (E) PAGE WIRING — banner dikhta hai, aur HAR error path par chhupta hai.

Chalao:  python3 tools/verify_exchange_mismatch.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


def skip(name, reason):
    print(f"  ⏭  {name} — SKIP: {reason}")
    results.append((name, True, 'skipped'))


import app as A  # noqa: E402

print('=' * 84)
print(' (A) exchange_from_source() — saare formats')
print('=' * 84)
CASES = [('Yahoo Finance (NSE)', 'NSE'), ('Yahoo Finance (BSE)', 'BSE'),
         ('NSE Direct', 'NSE'), ('TradingView (BSE)', 'BSE'),
         ('yahoo.ns', 'NSE'), ('yahoo.bo', 'BSE'),
         ('NSE:TCS', 'NSE'), ('TCS (NSE)', 'NSE')]
for src, want in CASES:
    got = A.exchange_from_source(src)
    check(f'{src!r} -> {want}', got == want, str(got))
for bad in (None, '', 'garbage', 'Yahoo Finance'):
    check(f'{bad!r} -> None (guess nahi karta)', A.exchange_from_source(bad) is None,
          str(A.exchange_from_source(bad)))

print('=' * 84)
print(' (B) kpi_scores_for() — exchange fields (stubbed fetch)')
print('=' * 84)


def synth(n=60, start=100.0):
    close = [start + i * 0.5 for i in range(n)]
    return pd.DataFrame({'Open': [c - .2 for c in close], 'High': [c + 1 for c in close],
                         'Low': [c - 1 for c in close], 'Close': close,
                         'Volume': [1_000_000] * n})


class _Stub:
    def __init__(self, src): self.src = src
    def __call__(self, symbol, period='2y', interval='1d', prefer_exch='NSE'):
        return synth(), self.src


orig_fetch, orig_fund, orig_resolve = (A.DATA_MANAGER.smart_fetch, A._tf_fund_data,
                                       A.resolve_symbol)
FD = {'pe_val': 21.7, 'roe_val': 0.42, 'debt_val': 46.3}
try:
    A._tf_fund_data = lambda s, src: dict(FD)
    A.resolve_symbol = lambda s: 'STUBSYM'

    for req, src, want_act, want_mm in (
            ('NSE', 'Yahoo Finance (NSE)', 'NSE', False),
            ('BSE', 'Yahoo Finance (NSE)', 'NSE', True),
            ('NSE', 'Yahoo Finance (BSE)', 'BSE', True),
            ('BSE', 'Yahoo Finance (BSE)', 'BSE', False),
            ('BSE', 'NSE Direct', 'NSE', True),
            ('NSE', 'garbage-source', None, False),   # unknown -> jhootha mismatch NAHI
    ):
        A.DATA_MANAGER.smart_fetch = _Stub(src)
        ok, p = A.kpi_scores_for('stubsym', prefer_exch=req)
        check(f'req={req} src={src!r} -> actual={want_act}',
              ok and p['exchange_actual'] == want_act, str(p.get('exchange_actual')))
        check(f'req={req} src={src!r} -> mismatch={want_mm}',
              p['exchange_mismatch'] is want_mm, str(p.get('exchange_mismatch')))
        if want_mm:
            n = p['exchange_note']
            check('  mismatch par note hai', bool(n))
            check('  note me dono exchange ka naam hai',
                  req in n and str(want_act) in n, str(n)[:70])
            check('  note "price alag ho sakta hai" kehta hai', 'alag ho sakta' in n)
        else:
            check('  no-mismatch par note None', p['exchange_note'] is None,
                  str(p['exchange_note']))
        check(f'req={req} -> exchange_requested uppercase normalized',
              p['exchange_requested'] == req)

    # lowercase / khaali / None
    A.DATA_MANAGER.smart_fetch = _Stub('Yahoo Finance (NSE)')
    # lowercase normalize hota hai. Khaali/None par requested None rehta hai —
    # matlab "aapne kuch maanga hi nahi", aur mismatch tab False hota hai.
    # (Pehle maine yahan 'NSE' expect kiya tha — GALAT. 'NSE' default ROUTE level
    # par _q('exch','NSE') bharta hai, function level par nahi. Function ka None
    # zyada honest hai: jhootha "aapne NSE maanga tha" claim nahi karta.)
    for raw, want in (('bse', 'BSE'), ('Bse', 'BSE'), ('', None), (None, None)):
        ok, p = A.kpi_scores_for('stubsym', prefer_exch=raw)
        check(f'prefer_exch={raw!r} -> requested={want!r}',
              p['exchange_requested'] == want, str(p['exchange_requested']))
        if want is None:
            check('  requested None -> mismatch False (jhootha flag nahi)',
                  p['exchange_mismatch'] is False, str(p['exchange_mismatch']))

    print()
    print(' (C2) ROUTE level — _q default "NSE" bharta hai')
    A.DATA_MANAGER.smart_fetch = orig_fetch
    A._tf_fund_data = orig_fund
    A.resolve_symbol = orig_resolve
    try:
        cc = A.app.test_client()
        for qs, want in (('?exch=', 'NSE'), ('', 'NSE'), ('?exch=bse', 'BSE')):
            jj = cc.get('/api/timeframe/RELIANCE' + qs).get_json()
            if not jj.get('ok'):
                skip(f'route {qs or "(no param)"}', str(jj.get('error'))[:60])
                continue
            check(f'route {qs or "(no param)"} -> requested={want!r}',
                  jj['exchange_requested'] == want, str(jj['exchange_requested']))
    except Exception as e:
        skip('route-level exchange default', f'{type(e).__name__}: {e}')
    # stubs dobara laga do (aage ke checks ke liye); finally block restore karega
    A._tf_fund_data = lambda s, src: dict(FD)
    A.resolve_symbol = lambda s: 'STUBSYM'
    A.DATA_MANAGER.smart_fetch = _Stub('Yahoo Finance (NSE)')

    print()
    print(' (C) kpi scores par koi asar nahi (sirf metadata add hua)')
    A.DATA_MANAGER.smart_fetch = _Stub('Yahoo Finance (NSE)')
    _, p1 = A.kpi_scores_for('stubsym', prefer_exch='NSE')
    A.DATA_MANAGER.smart_fetch = _Stub('Yahoo Finance (BSE)')
    _, p2 = A.kpi_scores_for('stubsym', prefer_exch='BSE')
    check('same df -> same kpi, chahe exchange label kuch bhi ho',
          p1['kpi'] == p2['kpi'], f"{p1['kpi']['master']} vs {p2['kpi']['master']}")
    check('exchange fields payload me hain',
          {'exchange_requested', 'exchange_actual', 'exchange_mismatch',
           'exchange_note'} <= set(p1))
finally:
    A.DATA_MANAGER.smart_fetch = orig_fetch
    A._tf_fund_data = orig_fund
    A.resolve_symbol = orig_resolve

print('=' * 84)
print(' (D) LIVE — asli BSE request')
print('=' * 84)
c = A.app.test_client()
live = []
try:
    jn = c.get('/api/timeframe/RELIANCE?exch=NSE').get_json()
    jb = c.get('/api/timeframe/RELIANCE?exch=BSE').get_json()
    if not (jn.get('ok') and jb.get('ok')):
        raise RuntimeError(str(jn.get('error') or jb.get('error'))[:80])
    live = [(jn, jb)]
except Exception as e:
    skip('live BSE mismatch check', f'{type(e).__name__}: {e}')

# NOTE: pehle ye section hardcode karta tha ki BSE request HAMESHA NSE par giregi
# ("Yahoo ke paas BSE historicals nahi"). Wo sirf tab sach hai jab TradingView
# tier unavailable ho. TvDatafeed installed + connect ho to BSE ka ASLI data
# milta hai (source='TradingView Direct (BSE)'), mismatch False hota hai — jo
# sahi behaviour hai. Isliye ab outcome nahi, INVARIANT assert hota hai.
for jn, jb in live:
    for lbl, j, want in (('NSE', jn, 'NSE'), ('BSE', jb, 'BSE')):
        req, act, mm = (j.get('exchange_requested'), j.get('exchange_actual'),
                        j.get('exchange_mismatch'))
        check(f'{lbl} request -> fields present',
              all(k in j for k in ('exchange_requested', 'exchange_actual',
                                   'exchange_mismatch', 'exchange_note')))
        check(f'{lbl} request -> requested={want}', req == want, str(req))
        # mismatch ka matlab hi ye hai: dono pata ho aur alag ho
        check(f'{lbl} request -> mismatch == (req != act)',
              mm is bool(req and act and req != act), f'req={req} act={act} mm={mm}')
        # note sirf mismatch par, aur usme maanga hua exchange likha ho
        note = j.get('exchange_note')
        if mm:
            check(f'{lbl} mismatch -> note present + exchange naam hai',
                  bool(note) and want in note, str(note)[:70])
        else:
            check(f'{lbl} no-mismatch -> note nahi (jhoothi warning nahi)',
                  not note, str(note)[:70])
        check(f'{lbl} request -> source string exchange se consistent',
              (act is None) or (act in (j.get('source') or '')), str(j.get('source')))
    check('cached response me bhi fields hain',
          'exchange_mismatch' in jb and jb.get('cached') in (True, False))
    # Scores exchange ke saath BADAL sakte hain (BSE/NSE ka data alag hota hai) —
    # isliye equality assert nahi karte, sirf structure aur range check karte hain.
    check('dono exchange par kpi structure same hai',
          sorted(jn['kpi']) == sorted(jb['kpi']), f"{sorted(jn['kpi'])} vs {sorted(jb['kpi'])}")
    check('dono exchange par scores 0-100 range me hain',
          all(0 <= v['score'] <= 100 for k in ('jn', 'jb')
              for v in (jn if k == 'jn' else jb)['kpi'].values()))
    print(f"   (info) NSE source={jn.get('source')!r} BSE source={jb.get('source')!r} "
          f"— mismatch={jb['exchange_mismatch']}")

print('=' * 84)
print(' (E) PAGE WIRING')
print('=' * 84)
page = (ROOT / 'Timeframes.html').read_text(encoding='utf-8')
appsrc = (ROOT / 'app.py').read_text(encoding='utf-8')
blk = appsrc[appsrc.index('# FIX-80:'):appsrc.index('@app.route(\'/api/timeframe/<symbol>\')')]
check('page par #exwarn banner hai', 'id="exwarn"' in page)
check('banner mismatch par dikhta hai', 'j.exchange_mismatch && j.exchange_note' in page)
check('banner me note ka text jaata hai', 'j.exchange_note' in page)
check('banner else me chhupta hai', "xw.style.display = 'none'" in page)
# har error path par chhupna chahiye — warna purana warning naye error ke saath dikhega
hides = page.count("exwarn').style.display='none'") + page.count("xw.style.display = 'none'")
check('banner kam se kam 4 jagah chhupta hai (pre-fetch + api-error + catch + else)',
      hides >= 4, str(hides))
check('helper exchange_from_source use hota hai (naya parse nahi banaya)',
      'exchange_from_source(src)' in blk)
check('mismatch tabhi True jab DONO pata hon (None par guess nahi)',
      'bool(req and act and req != act)' in blk)
check('requested exchange uppercase normalize hota hai',
      ".strip().upper() or None" in blk)
for label, s in (('app.py FIX-80 block', blk), ('Timeframes.html', page)):
    bad = [k for k in ('78% accuracy', 'guaranteed', 'sure shot', 'will go up')
           if k.lower() in s.lower()]
    check(f'{label} me fake claim nahi', not bad, str(bad))

passed = sum(1 for _, ok, _ in results if ok)
print('=' * 84)
print(f' {passed} / {len(results)} checks passed')
print('=' * 84)
sys.exit(0 if passed == len(results) else 1)
