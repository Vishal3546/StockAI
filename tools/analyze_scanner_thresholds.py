#!/usr/bin/env python3
"""
C-3 · Scanner threshold measurement (no code changed by this tool)
==================================================================
`nifty_scanner.py` ke signal bands hardcoded hain:

    composite = int(ens * 0.55 + effective_ml_prob * 0.45)
    STRONG BUY : composite >= 70 AND effective_ml_prob >= 55
    BUY        : composite >= 60 AND effective_ml_prob >= 52
    WATCH      : composite >= 45
    SELL       : composite >= 35
    STRONG SELL: else

Aur `ml_edge < 0` par `effective_ml_prob` ko 50.0 par neutralise kar diya jaata hai.
Ye tool **scanner ke asli functions** (`calculate_indicators`, `calculate_ensemble`)
se 250 sessions × universe ka distribution nikalta hai aur batata hai:

  1. ens aur composite ka actual distribution (min/median/mean/max, percentiles)
  2. current bands kitne STRONG BUY / BUY / WATCH / SELL / STRONG SELL dete hain
  3. ML-neutralised case me top bands **reachable hain ya nahi** (algebra + data)
  4. agar bands composite ke measured percentiles par fit kiye jaayein to kya bane

Kuch change nahi karta — sirf measure karta hai.

Run:  python tools/analyze_scanner_thresholds.py [--sessions 250] [--period 5y]
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

import nifty_scanner as S  # noqa: E402  (asli functions, re-implementation nahi)
from research.data import load  # noqa: E402

# scanner ke current hardcoded bands
BANDS = (
    ('STRONG BUY', 70, 55),
    ('BUY', 60, 52),
    ('WATCH', 45, None),
    ('SELL', 35, None),
    ('STRONG SELL', None, None),
)
ML_NEUTRAL = 50.0  # scanner: ml_edge < 0 → effective_ml_prob = 50.0


def signal(composite, eff):
    for name, cthr, mthr in BANDS:
        if cthr is None:
            return name
        if composite >= cthr and (mthr is None or eff >= mthr):
            return name
    return 'STRONG SELL'


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float('nan')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sessions', type=int, default=250)
    ap.add_argument('--period', default='5y')
    args = ap.parse_args()

    syms = list(S.NIFTY_STOCKS)
    print(f"C-3 measurement — {len(syms)} symbols × {args.sessions} sessions "
          f"(scanner ke asli calculate_indicators/calculate_ensemble)\n")

    ens_rows, comp_neutral, symbols_used, skipped = [], [], [], []
    for sym in syms:
        df = load(sym, period=args.period)
        if df is None or len(df) < args.sessions + 210:
            skipped.append(f'{sym}(bars={0 if df is None else len(df)})')
            continue
        df = S.calculate_indicators(df)
        n = len(df)
        start = max(210, n - args.sessions)  # SMA_200 warmup ke baad hi
        for i in range(start, n):
            ens, _ = S.calculate_ensemble(df.iloc[:i + 1])
            comp = int(ens * 0.55 + ML_NEUTRAL * 0.45)
            ens_rows.append(ens)
            comp_neutral.append(comp)
        symbols_used.append(sym)

    if not ens_rows:
        raise SystemExit(f'koi usable symbol nahi mila (skipped: {skipped})')

    ens = np.array(ens_rows)
    comp = np.array(comp_neutral)
    print(f"[data] {len(symbols_used)} symbols scored, {len(ens):,} stock-sessions "
          f"(skipped: {len(skipped)} {skipped[:4]})\n")

    print('[1] distribution (ML-neutralised, yaani jab ml_edge < 0 — jo default case hai)')
    for name, a in (('ensemble', ens), ('composite', comp)):
        print(f'  {name:<10} min {a.min():>3}  p10 {pct(a,10):5.1f}  p40 {pct(a,40):5.1f}  '
              f'median {np.median(a):5.1f}  mean {a.mean():5.1f}  p80 {pct(a,80):5.1f}  '
              f'p95 {pct(a,95):5.1f}  max {a.max():>3}')

    print('\n[2] current hardcoded bands ka output (ML-neutralised case)')
    counts = {name: 0 for name, _, _ in BANDS}
    for c in comp:
        counts[signal(int(c), ML_NEUTRAL)] += 1
    for name, _, _ in BANDS:
        share = 100.0 * counts[name] / len(comp)
        print(f'  {name:<12} {counts[name]:>7,}  ({share:5.1f}%)')

    print('\n[3] reachability — ML neutralised (effective_ml_prob = 50.0)')
    print(f'  BUY gate        : eff >= 52 → 50.0 fails → '
          f'{"UNREACHABLE" if ML_NEUTRAL < 52 else "reachable"}')
    print(f'  STRONG BUY gate : eff >= 55 → 50.0 fails → '
          f'{"UNREACHABLE" if ML_NEUTRAL < 55 else "reachable"}')
    need60 = (60 - ML_NEUTRAL * 0.45) / 0.55
    need70 = (70 - ML_NEUTRAL * 0.45) / 0.55
    print(f'  composite>=60 ke liye ens >= {need60:.1f} chahiye '
          f'(max possible ens = 95; observed max = {ens.max()})')
    print(f'  composite>=70 ke liye ens >= {need70:.1f} chahiye '
          f'(observed ens >= {need70:.0f}: {int((ens >= need70).sum()):,} / {len(ens):,} sessions)')
    print('  → ML-neutralised stock ke liye best possible signal = WATCH, '
          'chahe technicals kitne bhi strong ho.')

    print('\n[4] agar bands composite ke measured percentiles par fit ho')
    proposal = (
        ('STRONG BUY', pct(comp, 95)),
        ('BUY', pct(comp, 80)),
        ('WATCH', pct(comp, 40)),
        ('SELL', pct(comp, 10)),
    )
    print('  measured-percentile bands (ML gate hata kar, kyunki FIX-41 ne dikhaya '
          'ML me edge nahi):')
    for name, thr in proposal:
        print(f'    {name:<12} composite >= {thr:5.1f}')
    print(f'  → distribution by construction: top 5% STRONG BUY, next 15% BUY, '
          f'middle 40% WATCH, next 30% SELL, bottom 10% STRONG SELL')

    print('\nNOTE: ye sirf measurement hai. Percentile bands ka matlab ye NAHI ki '
          'wo signals profitable hain —\n      score ki relative ranking hai, '
          'validated edge nahi (RESEARCH_REPORT.md: net-of-cost ML −25.6% vs B&H +32.5%).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
