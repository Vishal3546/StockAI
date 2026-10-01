#!/usr/bin/env python3
"""FIX-33 offline regression suite: 250-session fit, no leak & regime sizing.

Run: python tools/verify_score_calibration.py
Artifact from tools/build_score_calibration.py is included with the patch;
network is NOT required for this suite (app import may try master list once).
"""
import copy
import json
import math
import pathlib
import sys
import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as A  # noqa: E402
import score_calibration as C  # noqa: E402
from tools.build_score_calibration import backfill  # noqa: E402

checks = []
def check(name, ok, detail=''):
    checks.append((name, bool(ok)))
    print(('  ✅ ' if ok else '  ❌ ') + name + ((' → ' + str(detail)) if detail else ''))

def rejects(name, fn):
    try:
        fn()
    except C.CalibrationError:
        check(name, True)
    except Exception as exc:
        check(name, False, type(exc).__name__)
    else:
        check(name, False, 'unexpectedly accepted')

print('=' * 86)
print(' FIX-33: 250 historical sessions + stock rank (regime separate from 18% weight)')
print('=' * 86)

print('\n[1] real recorded artifact — validation/reproducibility')
artifact = json.loads(C.ARTIFACT_PATH.read_text(encoding='utf-8'))
fitted = C.validate_artifact(artifact, A.score_formula_hash())
t = fitted['thresholds']
check('exactly 250 distinct completed sessions', fitted['sessions'] == 250)
check('each snapshot has >=20 real universe scores',
      min(len(row['scores']) for row in artifact['history']) >= 20)
check('history as-of matches last row', artifact['history'][-1]['session'] == fitted['asof_session'])
check('pooled samples = exactly sum(per-session coverage)',
      fitted['samples'] == sum(len(row['scores']) for row in artifact['history']))
check('thresholds monotone p10 < p40 < p80 < p95',
      t['short_sell'] < t['watchlist'] < t['buy_dip'] < t['buy_breakout'], str(t))
check('not old fixed score cutoffs 65/78', t['buy_dip'] != 65 and t['buy_breakout'] != 78, str(t))
check('no synthetic missing-symbol fill',
      all(len(row['scores']) == fitted['universe_covered'] for row in artifact['history']))
check('formula hash and weights match real runtime',
      artifact['formula_hash'] == A.score_formula_hash() and
      artifact['weights'] == A.CONFIG['ENGINE_WEIGHTS'])
check('scanner universe single source of truth',
      __import__('nifty_scanner').NIFTY_STOCKS == list(C.UNIVERSE))
builder_src = (ROOT / 'tools/build_score_calibration.py').read_text(encoding='utf-8')
check('builder never uses scanner one-run composite as score history',
      "['composite']" not in builder_src and "scan_results.json', 'r'" not in builder_src
      and 'C.stock_rank([fn(window) for fn in funcs])' in builder_src)

bad = copy.deepcopy(artifact); bad['thresholds']['buy_dip'] += 1
rejects('edited threshold rejected (recomputed from raw history)',
        lambda: C.validate_artifact(bad, A.score_formula_hash()))
bad = copy.deepcopy(artifact); bad['weights']['Volume Profile'] += 0.01
rejects('old/different engine weights rejected',
        lambda: C.validate_artifact(bad, A.score_formula_hash()))
bad = copy.deepcopy(artifact); bad['model'] = 'old-regime-6'
rejects('different score-model version rejected',
        lambda: C.validate_artifact(bad, A.score_formula_hash()))
bad = copy.deepcopy(artifact); bad['history'] = bad['history'][:1]
rejects('single scanner snapshot never enough',
        lambda: C.validate_artifact(bad, A.score_formula_hash()))
bad = copy.deepcopy(artifact); bad['history'][10]['scores'][next(iter(bad['history'][10]['scores']))] = 99
rejects('out-of-scale raw score rejected',
        lambda: C.validate_artifact(bad, A.score_formula_hash()))

print('\n[2] as-of guard + rank bands')
asof = date.fromisoformat(fitted['asof_session'])
ref_day = asof + timedelta(days=1)
while ref_day.weekday() > 4:
    ref_day += timedelta(days=1)
ref = ref_day.isoformat()
check('next completed weekday bar allowed', C.for_session(fitted, ref, bars=250)['asof_session'] == fitted['asof_session'])
rejects('same-day fit forbidden (no current-bar leakage)',
        lambda: C.for_session(fitted, fitted['asof_session'], bars=250))
rejects('future fit forbidden',
        lambda: C.for_session(fitted, (asof - timedelta(days=1)).isoformat(), bars=250))
rejects('stale fit >10 calendar days forbidden',
        lambda: C.for_session(fitted, (asof + timedelta(days=11)).isoformat(), bars=250))
rejects('wall-clock stale even if source serves old reference bar',
        lambda: C.for_session(fitted, ref, bars=250,
            current_day=(asof + timedelta(days=11)).isoformat()))
rejects('too few daily bars forbidden', lambda: C.for_session(fitted, ref, bars=249))
check('p95 score labelled BUY_BREAKOUT (not magic 78)', C.label(math.ceil(t['buy_breakout']), t) == 'BUY_BREAKOUT')
check('p80 score labelled BUY_DIP (not magic 65)', C.label(math.ceil(t['buy_dip']), t) == 'BUY_DIP')
check('bottom decile SHORT_SELL', C.label(math.floor(t['short_sell']), t) == 'SHORT_SELL')
check('middle WATCHLIST', C.label(math.ceil(t['watchlist']), t) == 'WATCHLIST')
check('relative percentile descriptive only, 0..100', 0 <= C.percentile_rank(fitted['_sorted_scores'], 50) <= 100)

print('\n[3] rank excludes market-wide regime AND intraday MTF')
eng = [{'name': k, 'score': 98} for k in C.DAILY_WEIGHTS]
lo = eng + [{'name': 'Market Regime', 'score': 20},
            {'name': 'Multi-Timeframe', 'score': 5}]
hi = eng + [{'name': 'Market Regime', 'score': 85},
            {'name': 'Multi-Timeframe', 'score': 98}]
rlo = A.ensemble_score(lo, asof_session=ref, bars=250, symbol='RELIANCE', calibration=fitted)
rhi = A.ensemble_score(hi, asof_session=ref, bars=250, symbol='RELIANCE', calibration=fitted)
check('e5/e6 vary karne se score same, 0% stock-rank weight', rlo['score'] == rhi['score'])
check('e5/e6 vary karne se label same', rlo['action'] == rhi['action'] == 'BUY_BREAKOUT')
check('fit visible (sample count + thresholds)', rlo['calibration']['ready'] and
      rlo['calibration']['samples'] == fitted['samples'] and rlo['calibration']['thresholds'] == t)
check('rank alone tradeable nahi jab tak risk/plan/regime gate na ho', rlo['tradeable'] is False)
check('regime no longer in stock ENGINE_WEIGHTS', 'Market Regime' not in A.CONFIG['ENGINE_WEIGHTS'])
check('intraday MTF no longer in stock ENGINE_WEIGHTS', 'Multi-Timeframe' not in A.CONFIG['ENGINE_WEIGHTS'])
check('four daily engines renormalised', abs(sum(A.CONFIG['ENGINE_WEIGHTS'].values()) - 0.62) < 1e-9)

deg = copy.deepcopy(eng); deg[0]['degraded'] = True
rstale = A.ensemble_score(eng, asof_session=ref, bars=250, symbol='RELIANCE',
                          calibration=fitted, data_fresh=False)
check('STALE daily OHLCV → fitted labels disabled even with valid history',
      not rstale['calibration']['ready'] and rstale['action'] == 'WATCHLIST')
rdeg = A.ensemble_score(deg, asof_session=ref, bars=250, symbol='RELIANCE', calibration=fitted)
check('degraded daily engine → no BUY/SHORT despite computed partial score',
      rdeg['action'] == 'WATCHLIST' and not rdeg['calibration']['ready'] and rdeg['score'] is not None)
all_bad = [{'name': n, 'score': 50, 'degraded': True} for n in C.DAILY_WEIGHTS]
rbad = A.ensemble_score(all_bad, asof_session=ref, bars=250, symbol='RELIANCE', calibration=fitted)
check('all degraded → null score + NO DATA (fake 50 nahi)',
      rbad['score'] is None and rbad['action'] == 'DATA_UNAVAILABLE' and not rbad['tradeable'])
no_hist_symbol = next((s for s in C.UNIVERSE if s not in fitted['symbols_covered']), None)
if no_hist_symbol:
    missing = A.ensemble_score(eng, asof_session=ref, bars=250, symbol=no_hist_symbol)
    check('universe symbol with NO historical scores cannot get fitted label',
          not missing['calibration']['ready'] and missing['action'] == 'WATCHLIST')
else:
    print('  ⏭ all 30 symbols covered this run; missing-symbol check not applicable')

orig = C.ARTIFACT_PATH
try:
    with tempfile.TemporaryDirectory() as tmp:
        C.ARTIFACT_PATH = pathlib.Path(tmp) / 'missing.json'
        r = A.ensemble_score(eng, asof_session=ref, bars=250, symbol='RELIANCE')
        check('no artifact → UNFITTED, not old 65/78 fallback',
              not r['calibration']['ready'] and r['action'] == 'WATCHLIST' and not r['tradeable'])
        C.ARTIFACT_PATH.write_text('{bad json}', encoding='utf-8')
        r = A.ensemble_score(eng, asof_session=ref, bars=250, symbol='RELIANCE')
        check('corrupt artifact → same fail-closed behaviour', not r['calibration']['ready']
              and r['action'] == 'WATCHLIST')
finally:
    C.ARTIFACT_PATH = orig

print('\n[4] no-lookahead historical backfill (no network)')
# 258 business bars: last bar deliberately absurd and should NOT change history.
idx = pd.bdate_range('2024-01-01', periods=258)
c = 100 + np.arange(len(idx)) * 0.2 + np.sin(np.arange(len(idx)) / 7)
frame = pd.DataFrame({'Open': c - 0.3, 'High': c + 1, 'Low': c - 1,
                      'Close': c, 'Volume': np.full(len(idx), 1e6)}, index=idx)
old_min = C.MIN_COVERAGE
try:
    C.MIN_COVERAGE = 1  # ONLY test fixture; production still requires >=20 stocks
    h1 = backfill({'RELIANCE': frame}, max_sessions=4)
    poison = frame.copy()
    poison.loc[poison.index[-1], ['Close', 'High', 'Open']] = 1e9
    h2 = backfill({'RELIANCE': poison}, max_sessions=4)
    check('as-of never includes latest live bar', h1[-1]['session'] < idx[-1].date().isoformat())
    check('future last bar mutation cannot change prior fit', h1 == h2)
    date_last = h1[-1]['session']
    cut = frame.loc[:date_last].tail(250)
    independently = C.stock_rank([fn(cut) for fn in
                   (A.engine_volume_profile, A.engine_rvol_cvd, A.engine_vcp, A.engine_smc)])
    check('historical snapshot = direct current-formula computation on <=t bars',
          h1[-1]['scores']['RELIANCE'] == independently['score'])
finally:
    C.MIN_COVERAGE = old_min

print('\n[5] market-regime EXPOSURE cap, not stock score')
plan = {'rate': .80, 'n': 200, 'breakeven': .375, 'basis': 'test'}
r = A.calculate_risk(100, 2, 50, action='BUY_BREAKOUT', plan_measure=plan,
                     regime={'regime': 'BULL'}, require_plan=True)
check('p95 bucket geometry by ACTION, even if raw score only 50', r['kelly_rr_used'] == round(2.5 / 1.5, 2))
check('BULL LONG policy 75%: floor qty 1000 → 750',
      r['qty_pre_regime'] == 1000 and r['qty'] == 750 and r['regime_exposure_factor'] == .75,
      f"{r['qty_pre_regime']} → {r['qty']}")
check('notional & risk-at-stop computed AFTER cap',
      r['notional'] == 750 * 100 and r['risk_amount'] == round(750 * 3))
check('regime factor labelled as policy, not fitted alpha', 'NOT backtested edge' in r['regime_basis'])
r0 = A.calculate_risk(100, 2, 50, action='BUY_BREAKOUT', plan_measure=plan,
                      regime={'regime': 'UNKNOWN', 'degraded': True}, require_plan=True)
check('UNKNOWN NIFTY data → live qty 0', r0['qty'] == 0 and r0['regime_exposure_factor'] == 0)
check('unknown regime exec status no trade', 'NO TRADE (market regime exposure 0)' in r0['exec_status'])
rbear = A.calculate_risk(100, 2, 50, action='BUY_BREAKOUT', plan_measure=plan,
                         regime={'regime': 'STRONG BEAR'}, require_plan=True)
check('bear market LONG cap 0, score cannot override', rbear['qty'] == 0)
rshort = A.calculate_risk(100, 2, 50, action='SHORT_SELL',
                          plan_measure={'rate': .80, 'n': 200, 'breakeven': .5, 'basis': 'test'},
                          regime={'regime': 'STRONG BEAR'}, require_plan=True)
check('bear market SHORT cap 50%; correct mirror', rshort['regime_exposure_factor'] == .5
      and rshort['qty'] == rshort['qty_pre_regime'] // 2 and rshort['direction'] == 'SHORT')
rnone = A.calculate_risk(100, 2, 50, action='BUY_DIP', regime={'regime': 'BULL'}, require_plan=True)
check('live measured plan missing → assumed fallback BLOCKED, no Kelly',
      rnone['qty'] == 0 and rnone['kelly_pct'] == 0 and rnone['win_rate_used'] is None and
      'ASSUMED' in rnone['risk_note'] and 'NO TRADE' in rnone['exec_status'])
runknown = A.calculate_risk(100, 2, 50, action='BUY_BREAKOUT',
                            plan_measure=plan, require_plan=True)
check('live regime object missing → no quantity', runknown['qty'] == 0)
r_top_no_edge = A.calculate_risk(100, 2, 68, action='BUY_BREAKOUT',
    plan_measure={'rate': .30, 'n': 200, 'breakeven': .375, 'basis': 'test'},
    regime={'regime': 'BULL'}, require_plan=True)
check('even p95/top rank + BULL → qty0 when measured plan LCB below breakeven',
      r_top_no_edge['qty'] == 0 and not r_top_no_edge['edge_verified']
      and 'NO TRADE' in r_top_no_edge['exec_status'])

print('\n[6] dashboard disclosure + app wiring')
src = (ROOT / 'app.py').read_text(encoding='utf-8')
dash = (ROOT / 'Dashboard.html').read_text(encoding='utf-8')
check('live app uses trailing 250-bar window', 'rank_window = ranked_df.tail(SCORE_CAL.LOOKBACK_BARS)' in src)
check('live app score has explicit as-of/date, symbol and bar count',
      'asof_session=rank_session' in src and 'bars=len(ranked_df), symbol=resolved' in src)
check('market regime passed into sizing ONLY', 'regime=e5, require_plan=True' in src)
check('dashboard displays sample + p80 + p95 + relative-rank warning',
      'scoreCalNote' in dash and 'fit.samples' in dash and 'th.buy_dip' in dash
      and 'th.buy_breakout' in dash and 'NOT profit probability' in dash)
check('dashboard displays regime cap qty pre → post',
      'Market-regime exposure cap (rank me 0%)' in dash and 'rk.qty_pre_regime' in dash)

print('\n[7] real Flask route, with ALL network inputs stubbed (no upstream calls)')
from types import SimpleNamespace
from unittest.mock import patch
import yfinance as yf
idx = pd.bdate_range(end=ref, periods=300)
c = 100 + np.arange(len(idx)) * .08 + np.sin(np.arange(len(idx)) / 9)
synthetic = pd.DataFrame({'Open': c - .3, 'High': c + 1, 'Low': c - 1,
                          'Close': c, 'Volume': np.full(len(idx), 1e6)}, index=idx)
mtf = {'name': 'Multi-Timeframe', 'score': 5, 'timeframes': {}, 'signal': 'test'}
with (patch.object(A.DATA_MANAGER, 'smart_fetch', return_value=(synthetic, 'synthetic daily')),
      patch.object(A, 'ml_engine', return_value={'available': False}),
      patch.object(A, 'fetch_nse_live_ltp', return_value=None),
      patch.object(A, 'engine_multitimeframe', return_value=mtf),
      patch.object(yf, 'Ticker', return_value=SimpleNamespace(info={}))):
    with patch.object(A, 'engine_market_regime', return_value={
         'name': 'Market Regime', 'score': 20, 'regime': 'STRONG BEAR', 'signal': 'test'}):
        res1 = A.app.test_client().get('/api/stock/RELIANCE')
        data1 = res1.get_json()
    with patch.object(A, 'engine_market_regime', return_value={
         'name': 'Market Regime', 'score': 85, 'regime': 'STRONG BULL', 'signal': 'test'}):
        res2 = A.app.test_client().get('/api/stock/RELIANCE')
        data2 = res2.get_json()
    with (patch.object(A.DATA_MANAGER, 'smart_fetch', return_value=(synthetic, 'STALE synthetic daily')),
          patch.object(A, 'engine_market_regime', return_value={
              'name': 'Market Regime', 'score': 85, 'regime': 'STRONG BULL', 'signal': 'test'})):
        res3 = A.app.test_client().get('/api/stock/RELIANCE')
        data3 = res3.get_json()
check('Flask stale daily feed disables fit even if quoted price is present',
      res3.status_code == 200 and not data3['ensemble']['calibration']['ready']
      and data3['ensemble']['action'] == 'WATCHLIST' and data3['risk']['qty'] == 0)
check('Flask stock route both cases HTTP 200', res1.status_code == 200 and res2.status_code == 200,
      f'{res1.status_code}/{res2.status_code}')
if res1.status_code == 200 and res2.status_code == 200:
    en1, en2 = data1['ensemble'], data2['ensemble']
    check('Flask uses actual stored 250-session fit + as-of guard',
          en1['calibration']['ready'] and en1['calibration']['reference_session'] == ref
          and en1['calibration']['asof_session'] == fitted['asof_session'])
    check('Flask raw score unchanged when regime flips 20→85',
          en1['score'] == en2['score'] and en1['action'] == en2['action'])
    check('Flask risk policy applied after fit, no assumed Kelly on missing plan',
          data1['risk']['regime_required'] is True and data2['risk']['regime_required'] is True
          and all(r['qty'] == 0 or r['plan_hit_rate'] is not None
                  for r in (data1['risk'], data2['risk'])))
    check('Flask exposes regime separately and two-layer rank basis',
          data1['engines']['regime']['regime'] == 'STRONG BEAR' and
          data2['engines']['regime']['regime'] == 'STRONG BULL' and
          en1['rank_only'] is True)
else:
    print('  ❌ Flask payload checks skipped after HTTP failure')
    checks.extend([('Flask payload checks', False)] * 4)

passed = sum(ok for _, ok in checks)
failed = len(checks) - passed
print('\n' + '=' * 86)
print(f' RESULT: {passed} passed, {failed} failed')
print('=' * 86)
sys.exit(0 if not failed else 1)
