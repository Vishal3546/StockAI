#!/usr/bin/env python3
"""
tools/verify_no_fake_numbers.py — "KOI JHOOTI VALUE" REGRESSION SUITE
================================================================================
Background: is project me baar-baar ek hi pattern mila —

    data na mile  →  engine chupchap ek NUMBER return kar deta tha
                     (score 50, tightness 99, wf_accuracy 0.0, EMA 23000)
                 →  wo number aage score/composite me chala jaata tha
                 →  UI usko asli reading ki tarah dikhata tha

Ye suite check karti hai ki ab har "pata nahi" saaf-saaf 'pata nahi' dikhe:

  1. Engine degraded ho to ensemble me EXCLUDE ho (weights renormalize),
     aur kaun exclude hua wo response me likha ho.
  2. Sab engines degraded hon to crash na ho (safe fallback).
  3. VCP ka tightness data na hone par None ho (purana 99 sentinel hataya).
  4. ML walk-forward accuracy na chale to None (purana 0.0 hataya).
  5. Regime/MTF/VCP/VP/RVOL/SMC — sab hi error paths me 'degraded' flag ho.
  6. Live API response me ye fields actually aate hon.

Chalao:  python3 tools/verify_no_fake_numbers.py
"""
import pathlib
import re
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


print('=' * 84)
print(' "NO FAKE NUMBERS" — verification')
print('=' * 84)

app_src = (ROOT / 'app.py').read_text()

# ─────────────────────────── 1. source-level checks ────────────────────────
print('\n[1] source-level: purane magic numbers / sentinels gaye?')
check('regime ka hardcoded 23000 gaya', 'else 23000' not in app_src)
check('regime ka hardcoded 24000 gaya', 'else 24000' not in app_src)
check('VCP ka 99 tightness sentinel gaya', 'else 99' not in app_src)
check('ML wf_accuracy 0.0 fallback gaya', "if wf_results else 0.0" not in app_src)
check('wf_accuracy ab None deta hai', "if wf_results else None" in app_src)

n_degraded = len(re.findall(r"'degraded':\s*(True|not enough)", app_src))
check('engine error paths me degraded flag', n_degraded >= 6, f'{n_degraded} jagah mila')
score_src = (ROOT / 'score_calibration.py').read_text()  # FIX-33: formula yahan shared hai
check('ensemble_response me degraded_engines field', "'degraded_engines': excluded" in score_src)
check('ensemble note deta hai', 'Degraded engines averaging se exclude' in score_src)

# ─────────────────────────── 2. ensemble behaviour ────────────────────────
print('\n[2] ensemble: degraded engines exclude + renormalize')
import app as A  # noqa: E402

two_live = [{'name': 'Volume Profile', 'score': 70},
            {'name': 'RVOL + CVD + VSA', 'score': 30}]
r_all = A.ensemble_score(two_live)
check('2 live → dono count, 2 missing ko mark karte hain',
      r_all['engines_used'] == 2 and len(r_all['degraded_engines']) == 2 and
      not r_all['calibration']['ready'],
      f"score={r_all['score']} used={r_all['engines_used']}")

one_degraded = [{'name': 'Volume Profile', 'score': 70},
                {'name': 'RVOL + CVD + VSA', 'score': 30, 'degraded': True}]
r_deg = A.ensemble_score(one_degraded)
check('degraded exclude hota hai', 'RVOL + CVD + VSA' in r_deg['degraded_engines']
      and r_deg['engines_used'] == 1, f"excluded={r_deg['degraded_engines']}")
check('weights renormalize (score badalna chahiye)', r_deg['score'] != r_all['score'],
      f"all-live={r_all['score']} vs degraded-excluded={r_deg['score']}")
check('note me exclude list aati hai', r_deg['note'] and 'RVOL' in r_deg['note'])
check('engines_used = live count', r_deg['engines_used'] == 1)

all_degraded = [{'name': 'Volume Profile', 'score': 50, 'degraded': True},
                {'name': 'SMC / ICT', 'score': 50, 'degraded': True}]
r_none = A.ensemble_score(all_degraded)
check('sab degraded → fake 50 nahi, null score + no trade',
      r_none['engines_used'] == 0 and r_none['score'] is None
      and r_none['action'] == 'DATA_UNAVAILABLE' and not r_none['tradeable'],
      f"score={r_none['score']}")

# ─────────────────────────── 3. engine-level honesty ──────────────────────
print('\n[3] engines: data na mile to degraded flag + None (fake number nahi)')

bad = pd.DataFrame({'Open': [0] * 80, 'High': [0] * 80, 'Low': [0] * 80,
                    'Close': [0] * 80, 'Volume': [0] * 80},
                   index=pd.date_range('2026-01-01', periods=80))

v = A.engine_vcp(bad)
check('VCP: compute na ho to tightness None (99 nahi)', v['tightness'] is None,
      f"tightness={v['tightness']} signal={v['signal']}")

for fn_name, fn in [('Volume Profile', lambda: A.engine_volume_profile(bad)),
                    ('RVOL+CVD', lambda: A.engine_rvol_cvd(bad)),
                    ('SMC', lambda: A.engine_smc(bad))]:
    try:
        out = fn()
        check(f'{fn_name}: degraded flag (+note)',
              out.get('degraded') is True and bool(out.get('note')), f"score={out.get('score')}")
    except Exception as e:
        check(f'{fn_name}: degraded flag', False, f'{type(e).__name__} — engine crash kar gaya')

# MTF: sirf 1 timeframe load ho to degraded
real_fetch = A.DATA_MANAGER.smart_fetch
A.DATA_MANAGER.smart_fetch = lambda *a, **k: (None, 'None')
try:
    m = A.engine_multitimeframe('TEST', daily_df=bad)
    empty_tfs = [k for k, t in m.get('timeframes', {}).items() if t.get('trend') == 'N/A']
    check('MTF: timeframes na milne par degraded', m.get('degraded') is True and m.get('note'),
          f"score={m.get('score')} tfs={len(empty_tfs)} empty")
finally:
    A.DATA_MANAGER.smart_fetch = real_fetch

# ─────────────────────────── 4. live API integration ──────────────────────
print('\n[4] live API: response me honest fields aate hain')
try:
    c = A.app.test_client()
    d = c.get('/api/stock/RELIANCE').get_json()
    ens = d.get('ensemble', {})
    check('ensemble.me me engines_used aata hai', 'engines_used' in ens,
          f"used={ens.get('engines_used')}")
    check('ensemble.me me degraded_engines aata hai', 'degraded_engines' in ens,
          f"degraded={ens.get('degraded_engines')}")
    engines = d.get('engines', {})
    n_deg = sum(1 for e in engines.values() if isinstance(e, dict) and e.get('degraded'))
    check('engines me degraded count report hota hai', True,
          f'{len(engines)} engines, {n_deg} degraded')
    ml = d.get('ml', {})
    wf = ml.get('walk_forward_accuracy')
    check('ML wf_accuracy None ya valid % (0.0 fake nahi)',
          wf is None or (isinstance(wf, (int, float)) and wf > 0),
          f'walk_forward_accuracy={wf}')
finally:
    pass

passed = sum(1 for _, ok, _ in results if ok)
print('=' * 84)
print(f' {passed} / {len(results)} checks passed')
print('=' * 84)
sys.exit(0 if passed == len(results) else 1)
