"""FIX-63 #2: REAL OI signal study — GitHub bhavcopy se, koi key nahi.

Ye FIX-63 ke downloader (fetch_oi_history.py) ke REAL OI data par wahi honest
battery chalata hai jo proxy (study_oi_signal.py) par chali thi:

  1. tercile spread (signal high vs low) — pooled cross-sectional
  2. char controls: momentum / OOS split / long-only / per-symbol
  3. PURGED WALK-FORWARD (repo ka tested research/ml_lab)

⚠️ DATA LIMITATION (imaandari se): real OI history sirf ~9 mahine hai
   (AvilPage 138 dates, 2025-01 -> 2026-10). Proxy ke paas 5 saal the.
   Isliye walk-forward me warmup=252 NAHI fit hota — warmup=60 use hota hai.
   Ye ek KAMZOR test hai; verdict indicative hai, definitive nahi. Agar verdict
   "edge" kahe to bhi use sirf hypothesis maanna chahiye, kyunki sample chhota
   hai aur multiple-testing (features x horizons) upward-biased hai.

Features (sab PAST-only, date t par):
  pcr_oi        put_OI / call_OI
  pcr_z         (pcr - 20d mean) / 20d std
  d_pcr         pcr - pcr.shift(1)
  oi_chg_pct    total OI ka 1-din % change
  call_wall_d   (max_call_strike - underlying) / underlying
  put_wall_d    (underlying - max_put_strike) / underlying

Outcome: underlying ka forward h-din return, shift(-h) — no look-ahead.

Run:
    python3 tools/study_real_oi.py                 # sab kuch
    python3 tools/study_real_oi.py --json tools/real_oi_study.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
OI_DIR = ROOT / 'reports' / 'oi_history_real'

# Futures round-trip cost % (research/costs.py se consistent)
FUT_COST = 0.146


def load_panel() -> pd.DataFrame:
    frames = []
    for f in sorted(OI_DIR.glob('*.csv')):
        df = pd.read_csv(f)
        df['symbol'] = f.stem
        df['date'] = pd.to_datetime(df['date'])
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    p = pd.concat(frames, ignore_index=True)

    # ── features (past-only) ──
    p = p.sort_values(['symbol', 'date']).reset_index(drop=True)
    g = p.groupby('symbol')
    p['pcr_z'] = g['pcr_oi'].transform(lambda s: (s - s.rolling(20).mean())
                                      / s.rolling(20).std())
    p['d_pcr'] = g['pcr_oi'].transform(lambda s: s - s.shift(1))
    tot = p['total_call_oi'] + p['total_put_oi']
    p['oi_chg_pct'] = (tot.pct_change() * 100)
    p['call_wall_d'] = (p['max_call_strike'] - p['underlying']) / p['underlying'] * 100
    p['put_wall_d'] = (p['underlying'] - p['max_put_strike']) / p['underlying'] * 100
    # ── outcomes (future) ──
    for h in (1, 2, 3, 5):
        p[f'r{h}'] = g['underlying'].transform(
            lambda s: s.pct_change(h).shift(-h) * 100)
    return p.dropna(subset=['pcr_z'])


FEATURES = ['pcr_oi', 'pcr_z', 'd_pcr', 'oi_chg_pct', 'call_wall_d', 'put_wall_d']


def tercile(panel, signal, outcome):
    w = panel.dropna(subset=[signal, outcome]).copy()
    if len(w) < 60:
        return {}
    w['_t'] = pd.qcut(w[signal], 3, labels=['l', 'm', 'h'], duplicates='drop')
    if w['_t'].nunique() < 3:
        return {}
    per = w.groupby(['symbol', '_t'], observed=True)[outcome].mean().unstack()
    if not {'l', 'h'}.issubset(per.columns):
        return {}
    d = (per['h'] - per['l']).dropna()
    sd = float(d.std(ddof=1))
    t = float(d.mean() / (sd / np.sqrt(len(d)))) if sd > 0 else 0.0
    gross = float(w.groupby('_t', observed=True)[outcome].mean()['h']
                  - w.groupby('_t', observed=True)[outcome].mean()['l'])
    return {'gross_pct': round(gross, 4), 'net_pct': round(gross - FUT_COST, 4),
            't': round(t, 2), 'n_symbols': int(len(d))}


def controls(panel, signal='pcr_z', horizon=5):
    w = panel.dropna(subset=[signal, 'underlying', f'r{horizon}']).copy()
    if len(w) < 100:
        return {'error': 'not enough data'}
    w['_t'] = pd.qcut(w[signal], 3, labels=['l', 'm', 'h'], duplicates='drop')
    # momentum (backward only)
    w['past5'] = w.groupby('symbol')['underlying'].transform(
        lambda s: s.pct_change(5) * 100)
    m = w.dropna(subset=['past5'])
    import numpy.linalg as la
    y = m[f'r{horizon}'].values
    X2 = np.column_stack([np.ones(len(m)), m['past5'].values])
    X3 = np.column_stack([np.ones(len(m)), m['past5'].values, m[signal].values])
    b2, *_ = la.lstsq(X2, y, rcond=None)
    b3, *_ = la.lstsq(X3, y, rcond=None)
    r2m = float(1 - np.var(y - X2 @ b2) / np.var(y))
    r2f = float(1 - np.var(y - X3 @ b3) / np.var(y))
    # OOS split
    half = len(w) // 2
    oos = {}
    for lab, sub in (('first_half', w.iloc[:half]), ('second_half', w.iloc[half:])):
        if sub['_t'].nunique() < 3:
            continue
        per = sub.groupby(['symbol', '_t'], observed=True)[f'r{horizon}'].mean().unstack()
        if {'l', 'h'}.issubset(per.columns):
            oos[lab] = round(float((per['h'] - per['l']).mean()), 4)
    # long-only
    uni = float(w[f'r{horizon}'].mean())
    hi = w[w['_t'] == 'h'][f'r{horizon}']
    lo_ex = float(hi.mean() - uni)
    # per-symbol
    per = w.groupby(['symbol', '_t'], observed=True)[f'r{horizon}'].mean().unstack()
    d = (per['h'] - per['l']).dropna()
    return {'momentum': {'r2_mom': round(r2m, 6), 'r2_full': round(r2f, 6),
                         'delta': round(r2f - r2m, 6), 'coeff': round(float(b3[2]), 6)},
            'oos': oos,
            'long_only': {'universe': round(uni, 4), 'high': round(float(hi.mean()), 4),
                          'excess': round(lo_ex, 4), 'net': round(lo_ex - FUT_COST, 4)},
            'per_symbol': {'pos': int((d > 0).sum()), 'tot': int(len(d)),
                           'median': round(float(d.median()), 4)}}


def walk_forward(panel, horizon=5, train_min=120, n_folds=4, n_perm=3, embargo_days=7):
    """POOLED cross-sectional purged walk-forward (real OI ke liye).

    Repo ka ml_lab.purged_walk_forward 150 train-rows ka hard minimum rakhta hai
    (wo 1200-bar single-series ML ke liye bana tha) — real OI me sirf ~114
    rows/symbol hain, isliye wo fit nahi hota. Yahan date-ordered, sab-symbols-
    pooled folds hain: train = sab rows jinki date < (fold_start - embargo),
    test = fold ki dates. Purge/embargo + permutation null wahi rehte hain.
    """
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler

    df = panel.dropna(subset=FEATURES + [f'r{horizon}']).copy()
    df = df.sort_values('date').reset_index(drop=True)
    dates = sorted(df['date'].unique())
    usable = dates[int(len(dates) * 0.3):]          # pehla 30% pure warmup
    if len(usable) < n_folds * 8:
        return {'error': 'dates kam hain'}
    block = len(usable) // n_folds
    oos_y, oos_p, folds = [], [], []
    rng = np.random.default_rng(0)

    def _run(shuffle):
        oy, op = [], []
        for k in range(n_folds):
            test_dates = set(usable[k * block:(k + 1) * block])
            te = df[df['date'].isin(test_dates)]
            cutoff = min(test_dates) - pd.Timedelta(days=embargo_days)
            tr = df[df['date'] < cutoff]
            if len(tr) < train_min or len(te) < 10:
                continue
            Xtr, ytr = tr[FEATURES].values, (tr[f'r{horizon}'] > 0).astype(int).values
            Xte, yte = te[FEATURES].values, (te[f'r{horizon}'] > 0).astype(int).values
            if shuffle:
                ytr = rng.permutation(ytr)
            sc = StandardScaler().fit(Xtr)
            mdl = GradientBoostingClassifier(n_estimators=100, max_depth=2,
                                             learning_rate=0.05, random_state=42)
            mdl.fit(sc.transform(Xtr), ytr)
            pr = mdl.predict_proba(sc.transform(Xte))[:, 1]
            oy.append(yte); op.append(pr)
        if not oy:
            return None
        return np.concatenate(oy), np.concatenate(op)

    real = _run(False)
    if real is None:
        return {'error': 'koi valid fold nahi'}
    oy, op = real
    acc = float(((op >= 0.5).astype(int) == oy).mean()) * 100
    base = float(max(oy.mean(), 1 - oy.mean())) * 100
    nulls = []
    for _ in range(n_perm):
        r = _run(True)
        if r:
            ny, np_ = r
            nulls.append(float(((np_ >= 0.5).astype(int) == ny).mean()) * 100)
    return {'n_oos': int(len(oy)), 'accuracy_pct': round(acc, 2),
            'baseline_pct': round(base, 2), 'edge_pp': round(acc - base, 2),
            'shuffled_ceiling_pct': round(max(nulls), 2) if nulls else None,
            'beats_shuffled': bool(nulls) and acc > max(nulls)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default='tools/real_oi_study.json')
    a = ap.parse_args()

    panel = load_panel()
    if panel.empty:
        print('koi OI data nahi — pehle fetch_oi_history.py chalao')
        return 1
    print(f'panel: {len(panel)} rows, {panel.symbol.nunique()} symbols, '
          f'{panel.date.min():%Y-%m-%d}..{panel.date.max():%Y-%m-%d}')

    res = {'asof': str(pd.Timestamp.now().date()),
           'rows': int(len(panel)), 'symbols': int(panel.symbol.nunique()),
           'window': f'{panel.date.min():%Y-%m-%d} .. {panel.date.max():%Y-%m-%d}',
           'note': 'REAL OI (AvilPage GitHub). Data CLUSTERED hai: 2025-01 ke '
                   '8 din, phir 356-din ka hole (poora 2025 missing), phir '
                   '2026-01 -> 2026-10 daily. Matlab real daily OI sirf ~9 '
                   'mahine (2026). Walk-forward warmup=30 — INDICATIVE verdict, '
                   'definitive nahi.',
           'tercile': {}, 'controls': {}, 'features': FEATURES}
    print('\n=== tercile spread (pcr_z, har horizon) ===')
    for h in (1, 2, 3, 5):
        r = tercile(panel, 'pcr_z', f'r{h}')
        if r:
            res['tercile'][f'{h}d'] = r
            print(f"  {h}d gross {r['gross_pct']:+.3f}  net {r['net_pct']:+.3f}  "
                  f"t {r['t']:+.2f}  n={r['n_symbols']}")

    print('\n=== controls (pcr_z, 5d) ===')
    ctl = controls(panel, 'pcr_z', 5)
    res['controls'] = ctl
    if 'error' not in ctl:
        mo = ctl['momentum']
        print(f"  momentum: R2 {mo['r2_mom']:.5f} -> {mo['r2_full']:.5f} (D{mo['delta']:+.6f})")
        print(f"  OOS: {ctl['oos']}")
        lo = ctl['long_only']
        print(f"  long-only: high {lo['high']:+.3f} vs uni {lo['universe']:+.3f} "
              f"-> excess {lo['excess']:+.3f} -> net {lo['net']:+.3f}")
        ps = ctl['per_symbol']
        print(f"  per-symbol: {ps['pos']}/{ps['tot']} positive, median {ps['median']:+.3f}")

    print('\n=== purged walk-forward (warmup=60) ===')
    wf = walk_forward(panel)
    res['walk_forward'] = wf
    if 'error' not in wf:
        ok = (wf['accuracy_pct'] > wf['baseline_pct']
              and wf.get('beats_shuffled', False))
        res['verdict'] = ('POSSIBLE EDGE (indicative — chhota sample)' if ok
                          else 'NO TRADEABLE EDGE')
        print(f"\n  acc {wf['accuracy_pct']} vs base {wf['baseline_pct']} vs "
              f"shuffled {wf['shuffled_ceiling_pct']}  n_oos={wf['n_oos']}  "
              f"beats={wf['beats_shuffled']}")
    else:
        res['verdict'] = 'INSUFFICIENT DATA'
    print(f'\n=== VERDICT: {res["verdict"]}')

    pathlib.Path(a.json).write_text(json.dumps(res, indent=2, ensure_ascii=False),
                                    encoding='utf-8')
    print(f'written: {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
