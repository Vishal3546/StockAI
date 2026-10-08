#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FIX-89: Backtest history builder — score ke BAAD actually kya hua.

Kyun alag artifact (score_calibration*.json ko extend kyun nahi kiya)
--------------------------------------------------------------------
`score_calibration.py:validate_artifact()` schema==1 par strict hai aur
`fit_history(history)` se thresholds/samples/sessions dobara reproduce karke
match karta hai. Wahan extra keys daalna runtime calibration ko todne ka risk
tha. Isliye backtest ka apna artifact hai: `backtest_history_{nse,bse}.json`.

Kya record hota hai
-------------------
Har (session, symbol) par:
    score  — wahi composite jo live dashboard dikhata hai (same engine funcs,
             same weights, past-only window — koi lookahead nahi)
    close  — us session ka close
    fwd    — aage 1/3/5/10/20 sessions ka actual % return (jo available nahi
             wo null, guess nahi)

Ye "accuracy" nahi hai. Ye real historical distribution hai: score-band X wale
samples ne aage N din me average kya kiya, kitne positive rahe, median kya tha.
UI wahi dikhata hai — koi "78% accuracy" jaisa number nahi.

Usage:
    python tools/build_backtest_history.py --exchange NSE
    python tools/build_backtest_history.py --exchange BSE
    python tools/build_backtest_history.py --check      # validate only, no network
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'tools'))

import app as A                                    # noqa: E402
import score_calibration as C                      # noqa: E402
import build_score_calibration as B                # noqa: E402

HORIZONS = (1, 3, 5, 10, 20)
MIN_COVERAGE = C.MIN_COVERAGE


def _out_path(exchange):
    return os.path.join(HERE, 'backtest_history_%s.json' % exchange.lower())


def _calibration_formula_hash(exchange):
    """Live calibration artifact ka formula_hash — traceability ke liye."""
    name = ('score_calibration_bse.json' if str(exchange).upper() == 'BSE'
            else 'score_calibration.json')
    try:
        return json.load(open(os.path.join(HERE, name), encoding='utf-8')).get('formula_hash')
    except Exception:                                            # noqa: BLE001
        return None


def build(exchange='NSE', max_sessions=C.WINDOW_SESSIONS, verbose=True):
    """Score + close + forward returns. Past-only scoring, lookahead sirf fwd me."""
    exch = str(exchange).strip().upper()
    if exch not in ('NSE', 'BSE'):
        raise C.CalibrationError('exchange NSE ya BSE hona chahiye, mila: %r' % exchange)

    frames = {}
    for sym in C.UNIVERSE:
        try:
            if exch == 'BSE':
                df = B.download_daily_tv(sym, exchange='BSE', n_bars=1400)
            else:
                df = B.download_daily(sym, period='3y')
        except Exception as e:                                   # noqa: BLE001
            if verbose:
                print('  ⚠️  %s download fail: %s' % (sym, type(e).__name__))
            df = None
        if df is not None and len(df) >= C.LOOKBACK_BARS:
            frames[sym] = df
    if verbose:
        print('  %s: %d/%d symbols ke paas >= %d bars'
              % (exch, len(frames), len(C.UNIVERSE), C.LOOKBACK_BARS))
    if len(frames) < 10:
        raise C.CalibrationError('sirf %d symbols ka data mila — backtest meaningless'
                                 % len(frames))

    # BSE ke TradingView frames me weekend dates aati hain (pehle measure kiya)
    if exch == 'BSE':
        for sym, df in list(frames.items()):
            clean = df[df.index.dayofweek < 5]
            if len(clean) < C.LOOKBACK_BARS:
                frames.pop(sym)
            else:
                frames[sym] = clean

    all_dates = sorted({t.date() for df in frames.values() for t in df.index})
    if len(all_dates) <= 1:
        raise C.CalibrationError('not enough distinct sessions')
    latest = all_dates[-1]
    min_day = all_dates[max(0, len(all_dates) - max_sessions - 40)]

    funcs = (A.engine_volume_profile, A.engine_rvol_cvd, A.engine_vcp, A.engine_smc)
    rows = []
    for sym in sorted(frames):
        df = frames[sym]
        idx = [t.date() for t in df.index]
        closes = [float(df['Close'].iloc[i]) for i in range(len(df))]
        scored = 0
        for i in range(C.LOOKBACK_BARS - 1, len(df)):
            d = idx[i]
            if d < min_day or d >= latest:
                continue
            window = df.iloc[i + 1 - C.LOOKBACK_BARS:i + 1]
            engines = [fn(window) for fn in funcs]
            sc = C.stock_rank(engines)
            if not (sc['complete'] and sc['score'] is not None):
                continue
            c0 = closes[i]
            if not c0 or c0 <= 0:
                continue
            fwd = {}
            for h in HORIZONS:
                j = i + h
                # forward return sirf tab jab aage ka bar ACTUALLY maujood ho
                fwd[str(h)] = (round((closes[j] / c0 - 1.0) * 100.0, 3)
                               if j < len(closes) and closes[j] and closes[j] > 0
                               else None)
            rows.append({'s': d.isoformat(), 'sym': sym,
                         'score': int(round(sc['score'])),
                         'close': round(c0, 2), 'fwd': fwd})
            scored += 1
        if verbose:
            print('  %-12s %d scored sessions' % (sym, scored), flush=True)

    if not rows:
        raise C.CalibrationError('koi scored session nahi bana')

    sessions = sorted({r['s'] for r in rows})
    ist = timezone(timedelta(hours=5, minutes=30))
    return {
        'schema': 1,
        'generated_at': datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S IST'),
        'exchange': exch,
        'model': C.MODEL,
        # Traceability: live dashboard wala hi score formula. Hash score_calibration
        # artifact se padhte hain (score_calibration module me function nahi hai).
        'formula_hash': _calibration_formula_hash(exch),
        'source': ('TradingView daily OHLCV (BSE)' if exch == 'BSE'
                   else 'Yahoo Finance daily adjusted OHLCV (NSE)'),
        'universe': sorted(frames),
        'horizons': list(HORIZONS),
        'lookback_bars': C.LOOKBACK_BARS,
        'first_session': sessions[0],
        'last_session': sessions[-1],
        'sessions': len(sessions),
        'samples': len(rows),
        'rows': rows,
    }


def band_of(score):
    """10-point bands. App ke apne band edges alag hain — UI dono dikhata hai."""
    s = max(0, min(100, int(score)))
    return '%d-%d' % ((s // 10) * 10, (s // 10) * 10 + 10)


def validate(path, min_samples=500):
    problems = []
    if not os.path.exists(path):
        return ['%s missing — run: python tools/build_backtest_history.py --exchange %s'
                % (os.path.basename(path), os.path.basename(path).split('_')[-1].split('.')[0].upper())]
    try:
        art = json.load(open(path, encoding='utf-8'))
    except Exception as e:                                       # noqa: BLE001
        return ['parse fail: %s' % e]
    rows = art.get('rows') or []
    if len(rows) < min_samples:
        problems.append('only %d rows (expected >= %d)' % (len(rows), min_samples))
    if art.get('schema') != 1:
        problems.append('schema != 1')
    if art.get('exchange') not in ('NSE', 'BSE'):
        problems.append('bad exchange: %r' % art.get('exchange'))
    if not art.get('generated_at'):
        problems.append('generated_at missing')
    need = {'s', 'sym', 'score', 'close', 'fwd'}
    bad = [r for r in rows[:200] if not need.issubset(r)]
    if bad:
        problems.append('%d/%d sampled rows missing keys' % (len(bad), min(200, len(rows))))
    # score range sane ho
    sc = [r['score'] for r in rows if isinstance(r.get('score'), (int, float))]
    if sc and (min(sc) < 0 or max(sc) > 100):
        problems.append('score out of 0-100: %s..%s' % (min(sc), max(sc)))
    # lookahead guard: last session par koi bhi horizon null hona chahiye
    if rows:
        last = art.get('last_session')
        tail = [r for r in rows if r['s'] == last]
        if tail and any(r['fwd'].get('20') is not None for r in tail):
            problems.append('last session par 20d forward return hai — lookahead leak')
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--exchange', default='NSE', choices=['NSE', 'BSE', 'both'])
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--min-samples', type=int, default=500)
    a = ap.parse_args(argv)

    targets = ['NSE', 'BSE'] if a.exchange == 'both' else [a.exchange]

    if a.check:
        bad = 0
        for ex in targets:
            p = _out_path(ex)
            probs = validate(p, a.min_samples)
            if probs:
                bad += 1
                for q in probs:
                    print('  ❌ %s: %s' % (ex, q))
            else:
                art = json.load(open(p, encoding='utf-8'))
                print('  ✅ %s: %d samples, %d sessions (%s → %s), generated %s'
                      % (ex, art['samples'], art['sessions'],
                         art['first_session'], art['last_session'], art['generated_at']))
        return 1 if bad else 0

    for ex in targets:
        print('[backtest] %s universe score + forward returns bana rahe hain …' % ex)
        t0 = time.time()
        art = build(ex)
        p = _out_path(ex)
        f = open(p, 'w', encoding='utf-8')
        json.dump(art, f, ensure_ascii=False)
        f.close()
        print('  ✅ %s — %d samples, %d sessions, %.1fs (%.0f KB)'
              % (os.path.basename(p), art['samples'], art['sessions'],
                 time.time() - t0, os.path.getsize(p) / 1024))
        probs = validate(p, a.min_samples)
        for q in probs:
            print('  ❌ %s' % q)
        if probs:
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
