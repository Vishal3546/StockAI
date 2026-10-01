#!/usr/bin/env python3
"""
C-3 · Scanner signal bands → fitted artifact (`scanner_bands.json`)
==================================================================
`nifty_scanner.py` ke signal bands hardcoded the (70/60/45/35) aur BUY/STRONG BUY
ek ML gate (`effective_ml_prob >= 52/55`) ke peeche the. Measurement
(`tools/analyze_scanner_thresholds.py`, 29 symbols × 250 sessions = 7,250
stock-sessions, scanner ke asli `calculate_indicators`/`calculate_ensemble`) ne
dikhaya:

  • ML-neutralised case (ml_edge < 0 → effective = 50.0, jo 22/29 stocks par
    lagta hai) me BUY gate 52 aur STRONG BUY gate 55 **kabhi pass nahi hote**
  • composite >= 60 ke liye ens >= 68.2 chahiye; observed max ens = 66
  • composite >= 70 ke liye ens >= 86.4 chahiye; 0 / 7,250 sessions
  • natija: 5 me se 3 signals (STRONG BUY, BUY, STRONG SELL) structurally dead;
    96.4% WATCH, 3.6% SELL

Ye tool ensemble score ki measured distribution se percentile bands fit karke
ek committed artifact banata hai. **Ye relative ranking hai, validated profit
nahi** — FIX-41 ne 53,295 OOS predictions par dikhaya ki ML me edge nahi, aur
score calibration (FIX-33) bhi yahi kehta hai.

Run:  python tools/build_scanner_bands.py [--sessions 250] [--period 5y]
"""
import argparse
import json
import pathlib
import sys
from collections import Counter
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

import nifty_scanner as S  # noqa: E402  (asli functions — re-implementation nahi)
from research.data import load  # noqa: E402

SCHEMA = 1
MODEL = 'scanner-bands-ens-percentile-v1'
ARTIFACT_PATH = ROOT / 'scanner_bands.json'
ML_NEUTRAL = 50.0

# scanner ke purane hardcoded bands (composite, ml gate)
OLD_BANDS = (('STRONG BUY', 70, 55), ('BUY', 60, 52), ('WATCH', 45, None),
             ('SELL', 35, None), ('STRONG SELL', None, None))

# fitted percentile targets
TARGETS = (('STRONG BUY', 95), ('BUY', 80), ('WATCH', 40), ('SELL', 10))


def old_signal(composite, eff):
    for name, cthr, mthr in OLD_BANDS:
        if cthr is None:
            return name
        if composite >= cthr and (mthr is None or eff >= mthr):
            return name
    return 'STRONG SELL'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sessions', type=int, default=250)
    ap.add_argument('--period', default='5y')
    ap.add_argument('--output', type=pathlib.Path, default=ARTIFACT_PATH)
    args = ap.parse_args()

    syms = list(S.NIFTY_STOCKS)
    print(f"C-3 · scanner band fitting — {len(syms)} symbols × {args.sessions} sessions "
          f"({args.period})")

    ens_rows, comp_rows, used, skipped = [], [], [], []
    for sym in syms:
        df = load(sym, period=args.period)
        if df is None or len(df) < args.sessions + 210:
            skipped.append(f'{sym}(bars={0 if df is None else len(df)})')
            print(f"  SKIP {sym:<12} insufficient history")
            continue
        df = S.calculate_indicators(df)
        n = len(df)
        for i in range(max(210, n - args.sessions), n):
            ens, _ = S.calculate_ensemble(df.iloc[:i + 1])
            ens_rows.append(int(ens))
            comp_rows.append(int(ens * 0.55 + ML_NEUTRAL * 0.45))
        used.append(sym)
        print(f"  ok   {sym:<12} {len(df)} bars")

    if len(used) < 10 or len(ens_rows) < 2000:
        raise SystemExit(f'insufficient data ({len(used)} symbols, {len(ens_rows)} sessions) '
                         '— no artifact written')

    ens = np.array(ens_rows)
    hist = {str(k): int(v) for k, v in sorted(Counter(ens_rows).items())}
    total = int(ens.size)

    bands = {}
    for name, q in TARGETS:
        bands[name] = float(np.percentile(ens, q))

    # fitted bands ka apni hi distribution par asar
    fitted_counts = Counter()
    for v in ens_rows:
        if v >= bands['STRONG BUY']:
            fitted_counts['STRONG BUY'] += 1
        elif v >= bands['BUY']:
            fitted_counts['BUY'] += 1
        elif v >= bands['WATCH']:
            fitted_counts['WATCH'] += 1
        elif v >= bands['SELL']:
            fitted_counts['SELL'] += 1
        else:
            fitted_counts['STRONG SELL'] += 1

    # purane bands ka measured natija (ML-neutralised case)
    old_counts = Counter(old_signal(c, ML_NEUTRAL) for c in comp_rows)
    need60 = (60 - ML_NEUTRAL * 0.45) / 0.55
    need70 = (70 - ML_NEUTRAL * 0.45) / 0.55

    artifact = {
        'schema': SCHEMA, 'model': MODEL,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'period': args.period, 'sessions_per_symbol': args.sessions,
        'symbols_scored': used, 'symbols_skipped': skipped,
        'n_stock_sessions': total,
        'signal_score': ('ensemble score (nifty_scanner.calculate_ensemble) — '
                         'ML isko gate ya weight nahi karta; ML sirf diagnostic hai '
                         '(FIX-41: 53,295 OOS predictions par ML edge +1.14pp ±0.74 = noise)'),
        'distribution': {
            'min': int(ens.min()), 'p10': float(np.percentile(ens, 10)),
            'p40': float(np.percentile(ens, 40)), 'median': float(np.median(ens)),
            'mean': round(float(ens.mean()), 2), 'p80': float(np.percentile(ens, 80)),
            'p95': float(np.percentile(ens, 95)), 'max': int(ens.max()),
        },
        'histogram': hist,
        'bands': bands,
        'band_order': [n for n, _ in TARGETS] + ['STRONG SELL'],
        'fitted_signal_counts': {k: int(fitted_counts.get(k, 0))
                                 for k in [n for n, _ in TARGETS] + ['STRONG SELL']},
        'old_bands': {
            'thresholds': {'STRONG BUY': [70, 55], 'BUY': [60, 52],
                           'WATCH': [45, None], 'SELL': [35, None]},
            'measured_counts_ml_neutralised': {k: int(old_counts.get(k, 0))
                                               for k in ('STRONG BUY', 'BUY', 'WATCH',
                                                         'SELL', 'STRONG SELL')},
            'buy_gate_unreachable_when_neutralised': ML_NEUTRAL < 52,
            'strong_buy_gate_unreachable_when_neutralised': ML_NEUTRAL < 55,
            'ens_needed_for_composite_60': round(need60, 2),
            'ens_needed_for_composite_70': round(need70, 2),
            'observed_max_ens': int(ens.max()),
            'sessions_reaching_composite_70': int(sum(1 for c in comp_rows if c >= 70)),
        },
        'disclosure': ('Fitted percentile bands = relative ranking within the measured '
                       'window, NOT a probability and NOT validated profit. '
                       'RESEARCH_REPORT.md: net-of-cost ML -25.6% vs buy&hold +32.5%.'),
    }

    tmp = args.output.with_name(args.output.name + '.tmp')
    tmp.write_text(json.dumps(artifact, indent=1, ensure_ascii=False), encoding='utf-8')
    tmp.replace(args.output)

    print(f"\n✅ {args.output} — {total:,} stock-sessions, {len(used)} symbols")
    print(f"   ens distribution: min {ens.min()} p10 {np.percentile(ens,10):.0f} "
          f"median {np.median(ens):.0f} p80 {np.percentile(ens,80):.0f} "
          f"p95 {np.percentile(ens,95):.0f} max {ens.max()}")
    print("   fitted bands:", {k: v for k, v in bands.items()})
    print("   fitted counts:", dict(artifact['fitted_signal_counts']))
    print("   OLD bands (ML-neutralised):",
          dict(artifact['old_bands']['measured_counts_ml_neutralised']))
    print("   REMINDER: relative ranking hai, profitable signal ka proof nahi.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
