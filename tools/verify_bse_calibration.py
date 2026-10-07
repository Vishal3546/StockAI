#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FIX-84 (Phase 2) verification — per-exchange score calibration.

Pehle app ka score calibration SIRF NSE universe par fitted tha (30 naam, 250
sessions, Yahoo .NS) aur BSE frame par wahi NSE distribution se compare hota tha
— app.py FIX-53 comment me khud disclose tha. BSE aur NSE ke closing prices alag
hote hain (TradingView: RELIANCE 1207.70 NSE vs 1206.65 BSE), isliye score
distribution bhi alag hoti hai.

Yahoo ke paas BSE historicals hote hi nahi (RELIANCE.BO par `max` range me bhi
1 row), isliye BSE artifact TradingView se banta hai: 1400 bars, 2021-02-12 se.

Run:  python tools/verify_bse_calibration.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402

import app as A  # noqa: E402
import score_calibration as C  # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(f"  {'✅' if ok else '❌'} {name}" + (f'  → {detail}' if detail else ''))


def skip(name, why):
    RESULTS.append((name, True))
    print(f"  ⏭️  {name} — skip ({why})")


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (A) artifact path resolution')
print('=' * 84)

check('A1 NSE -> score_calibration.json (purana naam, backward compat)',
      C.artifact_path('NSE').name == 'score_calibration.json', C.artifact_path('NSE').name)
check('A2 BSE -> score_calibration_bse.json',
      C.artifact_path('BSE').name == 'score_calibration_bse.json', C.artifact_path('BSE').name)
check('A3 lowercase bse normalize hota hai',
      C.artifact_path('bse') == C.artifact_path('BSE'))
check('A4 unknown exchange -> NSE (fail-safe, crash nahi)',
      C.artifact_path('garbage') == C.ARTIFACT_PATH, C.artifact_path('garbage').name)
check('A5 None/empty -> NSE', C.artifact_path(None) == C.ARTIFACT_PATH
      and C.artifact_path('') == C.ARTIFACT_PATH)
check('A6 ARTIFACT_PATHS me dono exchange hain',
      set(C.ARTIFACT_PATHS) == {'NSE', 'BSE'}, str(sorted(C.ARTIFACT_PATHS)))


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (B) builder — download_daily_tv')
print('=' * 84)

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import build_score_calibration as B  # noqa: E402


def _mkdf(dates, base=100.0):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
    cl = base + np.arange(len(idx)) * 0.5
    return pd.DataFrame({'Open': cl, 'High': cl + 1, 'Low': cl - 1,
                         'Close': cl, 'Volume': np.full(len(idx), 1e6)}, index=idx)


# 500+ bars chahiye; usme jaan-boojh kar weekend dates daalo
_weekdays = [d for d in pd.bdate_range('2021-01-01', periods=520)]
_weekend = [pd.Timestamp('2023-11-12'), pd.Timestamp('2026-02-01')]   # Sun, Sun
_dates = sorted(set(_weekdays) | set(_weekend))

orig_fetch = A.DATA_MANAGER.fetch_tradingview
try:
    def _fake_ok(symbol, n_bars=500, interval_str='1d', prefer_exch='NSE'):
        return _mkdf(_dates), str(prefer_exch).upper()

    def _fake_wrong_exch(symbol, n_bars=500, interval_str='1d', prefer_exch='NSE'):
        # BSE maanga, NSE mila — builder ko reject karna chahiye
        return _mkdf(_dates), 'NSE'

    A.DATA_MANAGER.fetch_tradingview = _fake_ok
    df = B.download_daily_tv('RELIANCE', 'BSE')
    _nwe = 0 if df is None else int((df.index.dayofweek >= 5).sum())
    check('B1 weekend dates filter ho gayin', df is not None and _nwe == 0,
          f'weekend={_nwe}')
    check('B1 required columns hain',
          df is not None and all(k in df.columns for k in
                                 ('Open', 'High', 'Low', 'Close', 'Volume')),
          str(list(df.columns) if df is not None else []))
    check('B1 index normalized (time component nahi)',
          df is not None and all(t == t.normalize() for t in df.index[:5]))
    check('B1 sorted + duplicate nahi',
          df is not None and df.index.is_monotonic_increasing
          and not df.index.duplicated().any())

    # B2: exchange mismatch guard
    A.DATA_MANAGER.fetch_tradingview = _fake_wrong_exch
    try:
        B.download_daily_tv('RELIANCE', 'BSE')
        check('B2 BSE maanga/NSE mila -> CalibrationError', False, 'exception nahi aaya')
    except C.CalibrationError as e:
        check('B2 BSE maanga/NSE mila -> CalibrationError', 'NSE' in str(e), str(e)[:60])

    # B3: kam bars -> None (fake history se fit nahi banana chahiye)
    def _fake_short(symbol, n_bars=500, interval_str='1d', prefer_exch='NSE'):
        return _mkdf(list(pd.bdate_range('2026-01-01', periods=100))), 'BSE'
    A.DATA_MANAGER.fetch_tradingview = _fake_short
    check('B3 <500 clean bars -> None (artifact nahi banega)',
          B.download_daily_tv('RELIANCE', 'BSE') is None)

    # B4: TradingView down -> None, crash nahi
    def _fake_none(symbol, n_bars=500, interval_str='1d', prefer_exch='NSE'):
        return None, None
    A.DATA_MANAGER.fetch_tradingview = _fake_none
    check('B4 TradingView down -> None (exception nahi)',
          B.download_daily_tv('RELIANCE', 'BSE') is None)
finally:
    A.DATA_MANAGER.fetch_tradingview = orig_fetch


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (C) app — per-exchange calibration load')
print('=' * 84)

orig_cache = dict(A._SCORE_CAL_CACHE)
bse_path = C.ARTIFACT_PATH_BSE
nse_path = C.ARTIFACT_PATH
# validate_artifact() ka rule: artifact ka asof_session requested asof_session se
# PEHLE ka hona chahiye (same-day/future history current label me leak na ho),
# aur requested session IST clock se aage nahi. Isliye hardcoded date use karne
# ke bajaye IST aaj ki date lete hain — wahi live flow bhi karta hai.
from datetime import datetime as _dt
_ASOF = _dt.now(A.IST).date().isoformat()


def _art_asof(path):
    try:
        with open(path, encoding='utf8') as f:
            return json.load(f).get('asof_session')
    except Exception:
        return None


try:
    if not bse_path.exists():
        skip('C1 BSE artifact load', f'{bse_path.name} absent — '
             'python tools/build_score_calibration.py --exchange BSE')
    elif (_art_asof(bse_path) or '') >= _ASOF:
        skip('C1-C3 BSE fit', f'BSE artifact asof {_art_asof(bse_path)} >= aaj {_ASOF} '
             '— rebuild karein: python tools/build_score_calibration.py --exchange BSE')
    else:
        A._SCORE_CAL_CACHE.clear()
        f_bse, err_bse = A._score_history_for(_ASOF, 300, 'RELIANCE', 'BSE')
        check('C1 BSE artifact load hota hai', f_bse is not None, str(err_bse))
        if f_bse:
            check('C1 BSE fit ke apne samples hain', f_bse.get('samples', 0) > 0,
                  str(f_bse.get('samples')))
        f_nse, err_nse = A._score_history_for(_ASOF, 300, 'RELIANCE', 'NSE')
        check('C2 NSE artifact alag load hota hai', f_nse is not None, str(err_nse))
        if f_bse and f_nse:
            check('C2 dono fit genuinely alag hain (ek doosre ko overwrite nahi karte)',
                  (f_bse.get('samples') != f_nse.get('samples')
                   or f_bse.get('asof_session') != f_nse.get('asof_session')),
                  f"bse={f_bse.get('samples')}/{f_bse.get('asof_session')} "
                  f"nse={f_nse.get('samples')}/{f_nse.get('asof_session')}")
        check('C2 cache me dono exchange ki alag entry hai',
              set(A._SCORE_CAL_CACHE) >= {'NSE', 'BSE'}, str(sorted(A._SCORE_CAL_CACHE)))

        # C3: BSE ke baad NSE maango to BSE ka fit na mile (purana single-entry bug)
        f_nse2, _ = A._score_history_for(_ASOF, 300, 'RELIANCE', 'NSE')
        check('C3 BSE load ke baad NSE request par NSE fit hi milta hai',
              f_nse2 is not None and (f_bse is None or
                                      f_nse2.get('samples') == f_nse.get('samples')),
              str(f_nse2.get('samples') if f_nse2 else None))

    # C4: missing artifact -> actionable message with the right command
    A._SCORE_CAL_CACHE.clear()
    _missing = C.ARTIFACT_PATHS['BSE']
    _had = _missing.exists()
    try:
        if _had:
            os.replace(_missing, str(_missing) + '.bak')
        _, err = A._score_history_for(_ASOF, 300, 'RELIANCE', 'BSE')
        check('C4 BSE artifact missing -> message me --exchange BSE command hai',
              err is not None and '--exchange BSE' in err, str(err)[:80])
    finally:
        if _had and os.path.exists(str(_missing) + '.bak'):
            os.replace(str(_missing) + '.bak', _missing)
        A._SCORE_CAL_CACHE.clear()

    # C5: universe ke bahar ka symbol -> same honest message
    _, err5 = A._score_history_for(_ASOF, 300, 'ZZZNOTINUNIVERSE', 'BSE')
    check('C5 universe ke bahar -> honest message', err5 is not None and 'universe' in err5,
          str(err5)[:60])
finally:
    A._SCORE_CAL_CACHE.clear(); A._SCORE_CAL_CACHE.update(orig_cache)


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (D) cross-wired artifact guard')
print('=' * 84)

import tempfile  # noqa: E402
tmpdir = tempfile.mkdtemp()
try:
    # BSE path par ek artifact jisme exchange='NSE' likha ho
    fake = {'exchange': 'NSE', 'model': C.MODEL, 'formula_hash': 'x'}
    fp = os.path.join(tmpdir, 'score_calibration_bse.json')
    with open(fp, 'w', encoding='utf8') as f:
        json.dump(fake, f)

    import pathlib  # noqa: E402
    # artifact_path() module global padhta hai (dict se nahi) — isliye
    # ARTIFACT_PATH_BSE ko hi patch karo, warna change dikhega hi nahi.
    orig_bse = C.ARTIFACT_PATH_BSE
    C.ARTIFACT_PATH_BSE = pathlib.Path(fp)
    A._SCORE_CAL_CACHE.clear()
    try:
        fitted, err = A._score_history_for(_ASOF, 300, 'RELIANCE', 'BSE')
        check('D1 BSE path par NSE artifact -> reject hota hai', fitted is None,
              'fit mil gaya!' if fitted else '')
        check('D1 error me dono exchange ka naam hai',
              err is not None and 'BSE' in err and 'NSE' in err, str(err)[:80])
    finally:
        C.ARTIFACT_PATH_BSE = orig_bse
        A._SCORE_CAL_CACHE.clear()

    # D2: monkeypatch contract abhi bhi kaam karta hai (regression guard) —
    # tools/verify_score_calibration.py ke fail-closed tests isi par depend karte hain.
    orig_nse = C.ARTIFACT_PATH
    try:
        C.ARTIFACT_PATH = pathlib.Path(tmpdir) / 'does_not_exist.json'
        check('D2 C.ARTIFACT_PATH monkeypatch NSE lookup par lagta hai',
              C.artifact_path('NSE') == C.ARTIFACT_PATH, str(C.artifact_path('NSE').name))
    finally:
        C.ARTIFACT_PATH = orig_nse
finally:
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
print('=' * 84)
print(' (E) ensemble meta + page disclosure')
print('=' * 84)

dash = open(os.path.join(ROOT, 'Dashboard.html'), encoding='utf-8').read()
check('E1 Dashboard calibration.exchange padhta hai',
      "calibration?.exchange" in dash)
check('E1 Dashboard ab "NSE universe" hardcode nahi karta',
      dash.count('percentile NSE universe par fitted hai') == 0,
      str(dash.count('percentile NSE universe par fitted hai')))
check('E1 frame==calibration match par "alag calibration" bolta hai',
      'alag' in dash and 'calibration' in dash)

import inspect  # noqa: E402
src = inspect.getsource(A.ensemble_score)
check('E2 ensemble_score exchange param leta hai', 'exchange=' in src.split('"""')[0])
check('E2 meta me exchange jata hai', "'exchange':" in src)
check('E2 basis string me exchange dynamic hai', '-universe sessions' in src
      and 'f\'250 completed' in src or "f'250 completed" in src)

sig = inspect.signature(A._score_history_for)
check('E3 _score_history_for exchange param leta hai', 'exchange' in sig.parameters)
check('E3 default NSE hai (purane callers na tootein)',
      sig.parameters['exchange'].default == 'NSE',
      str(sig.parameters['exchange'].default))

# E4: app.py ka FIX-53 comment ab jhooth nahi bolta
appsrc = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
check('E4 FIX-53 comment FIX-84 se update hua hai',
      'FIX-84 (Phase 2) UPDATE' in appsrc)

print('=' * 84)
p = sum(1 for _, ok in RESULTS if ok)
print(f' {p} / {len(RESULTS)} checks passed')
print('=' * 84)
sys.exit(0 if p == len(RESULTS) else 1)
