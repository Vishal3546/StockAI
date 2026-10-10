#!/usr/bin/env python3
"""
C-2 · ML edge study → recorded artifact (`ml_edge_study.json`)
==============================================================
`research/` me purged walk-forward + permutation null pehle se tha, par uska
natija sirf reports/*.md me tha — live app apna *internal* ML number dikhata raha
(single 80/20 split wala), jo OOS-validated nahi hai. Ye tool wahi honest study
ek machine-readable artifact me record karta hai taaki app/dashboard usi ko quote
karein.

Kya measure hota hai (per strategy, pooled across symbols):
  • purged + embargoed walk-forward accuracy (train kabhi test ke aage nahi;
    embargo = label horizon, warna overlapping labels leak karte hain)
  • baseline = usi OOS window ka majority class (50% coin-flip se tougher)
  • edge_pp + binomial 95% CI
  • shuffled-label permutation null ceiling (S3 proposed design par)
  • verdict (research.ml_lab.verdict wala hi logic)

Ye profitability ka proof NAHI hai — sirf "is design me OOS predictive edge hai
ya nahi" ka measured jawaab. Net-of-cost numbers `research/run_study.py` me hain.

Run:  python tools/build_ml_edge_study.py [--period 5y] [--perms 3] [--quick]
"""
import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from research import ml_lab  # noqa: E402
from research.data import load  # noqa: E402
from research.features import APP_28, SMALL_10, build_features, build_labels  # noqa: E402

SCHEMA = 1
MODEL = 'ml-edge-purged-wf-v1'
ARTIFACT_PATH = ROOT / 'ml_edge_study.json'

# Wahi 20 large-caps jo recorded study (RESEARCH_REPORT.md) me use hue.
DEFAULT_SYMBOLS = (
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'SBIN', 'BHARTIARTL',
    'ITC', 'KOTAKBANK', 'LT', 'WIPRO', 'AXISBANK', 'MARUTI', 'TMPV',
    'BAJFINANCE', 'SUNPHARMA', 'TITAN', 'HCLTECH', 'POWERGRID', 'NTPC',
)

STRATEGIES = (
    ('S1_ml_dir1_app28', APP_28, 'dir1', "app.py ka current design (28 features, next-day direction)"),
    ('S2_ml_dir1_small10', SMALL_10, 'dir1', "wahi label, chhota feature set"),
    ('S3_ml_ret5atr_small10', SMALL_10, 'ret5_atr', "proposed redesign (volatility-adjusted 5-day label)"),
)


def _pooled(results):
    """Per-symbol WFResult ko ek pooled number set me badlo (koi averaging trick nahi —
    saare OOS predictions ek saath count hote hain)."""
    p = np.concatenate([r.oos_prob for r in results])
    y = np.concatenate([r.oos_y for r in results])
    acc = float(((p >= 0.5).astype(int) == y).mean())
    base = float(sum(r.baseline * r.n_oos for r in results) / len(y))
    se = float(np.sqrt(max(acc * (1 - acc), 1e-9) / len(y)))
    fold_edges = np.array([f['edge_pp'] for r in results for f in r.folds])
    return {
        'accuracy_pct': round(acc * 100, 2),
        'baseline_pct': round(base * 100, 2),
        'edge_pp': round((acc - base) * 100, 2),
        'ci95_pp': round(se * 1.96 * 100, 2),
        'fold_sigma_pp': round(float(fold_edges.std()), 2) if len(fold_edges) else 0.0,
        'n_oos': int(len(y)),
        'symbols_with_positive_edge': int(sum(1 for r in results if r.edge_pp > 0)),
    }


def _lib_versions():
    """Study kis environment me bani — reproducibility ke liye artifact me record."""
    import importlib.metadata as md
    out = {'python': sys.version.split()[0]}
    for pkg in ('numpy', 'pandas', 'scikit-learn', 'xgboost', 'yfinance'):
        try:
            out[pkg] = md.version(pkg)
        except Exception:
            out[pkg] = None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS))
    ap.add_argument('--period', default='5y')
    ap.add_argument('--folds', type=int, default=5)
    ap.add_argument('--perms', type=int, default=3)
    ap.add_argument('--quick', action='store_true', help='permutation null skip (fast)')
    ap.add_argument('--output', type=pathlib.Path, default=ARTIFACT_PATH)
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    print(f"C-2 · ML edge study — {len(symbols)} symbols, {args.period} window, "
          f"{args.folds} purged folds" + (" (quick: no permutation null)" if args.quick else ""))

    frames = {}
    for sym in symbols:
        df = load(sym, period=args.period)
        if df is None or len(df) < 400:
            print(f"  SKIP {sym:<12} insufficient daily history")
            continue
        frames[sym] = df
        print(f"  loaded {sym:<12} {len(df)} bars, last {df.index[-1].date()}")
    if len(frames) < 5:
        raise SystemExit(f'only {len(frames)} usable symbols (need >=5); no artifact written')

    out = {}
    null_block = None
    for name, feats, label_kind, note in STRATEGIES:
        t0 = time.time()
        results, skipped = [], []
        for sym, df in frames.items():
            try:
                lab = build_labels(build_features(df), kind=label_kind)
                horizon = int(lab['horizon'].iloc[0])
                X = lab[feats]
                y = lab['label']
                keep = y.notna() & X.notna().all(axis=1)
                X, y = X[keep], y[keep].astype(float)
                if len(X) < 400:
                    skipped.append(sym)
                    continue
                results.append(ml_lab.purged_walk_forward(
                    X, y, n_folds=args.folds, warmup=252, embargo=horizon))
            except Exception as exc:
                skipped.append(f'{sym}({type(exc).__name__})')
        if not results:
            print(f"  {name:<24} no usable symbols — skipped")
            continue
        pooled = _pooled(results)
        pooled['note'] = note
        pooled['features'] = len(feats)
        pooled['label'] = label_kind
        pooled['symbols'] = len(results)
        pooled['skipped'] = skipped
        out[name] = pooled
        print(f"  {name:<24} acc {pooled['accuracy_pct']:.2f}% vs baseline "
              f"{pooled['baseline_pct']:.2f}% → edge {pooled['edge_pp']:+.2f}pp "
              f"(±{pooled['ci95_pp']:.2f}) · n={pooled['n_oos']} · {time.time()-t0:.0f}s")

        if name == 'S3_ml_ret5atr_small10' and not args.quick:
            nulls = []
            for sym, df in frames.items():
                try:
                    lab = build_labels(build_features(df), kind=label_kind)
                    horizon = int(lab['horizon'].iloc[0])
                    X = lab[feats]
                    y = lab['label']
                    keep = y.notna() & X.notna().all(axis=1)
                    X, y = X[keep], y[keep].astype(float)
                    if len(X) < 400:
                        continue
                    per_sym = []
                    for seed in range(args.perms):
                        r = ml_lab.purged_walk_forward(
                            X, y, n_folds=args.folds, warmup=252, embargo=horizon,
                            shuffle_train_labels=True, seed=seed)
                        per_sym.append(r)
                    if per_sym:
                        nulls.append(_pooled(per_sym)['accuracy_pct'])
                except Exception:
                    continue
            if nulls:
                null_block = {
                    'n_symbols': len(nulls), 'perms_per_symbol': args.perms,
                    'mean_pct': round(float(np.mean(nulls)), 2),
                    'max_pct': round(float(np.max(nulls)), 2),
                    'min_pct': round(float(np.min(nulls)), 2),
                }
                print(f"  {'permutation null':<24} shuffled-label ceiling "
                      f"{null_block['mean_pct']:.2f}% (max {null_block['max_pct']:.2f}%)")

    if not out:
        raise SystemExit('no strategy produced OOS predictions; no artifact written')

    # Verdict: pooled numbers par wahi logic jo research.ml_lab.verdict use karta hai.
    proposed = out.get('S3_ml_ret5atr_small10') or next(iter(out.values()))
    floor = (null_block or {}).get('max_pct')
    lo = proposed['accuracy_pct'] - proposed['ci95_pp']
    if floor is not None and proposed['accuracy_pct'] <= floor:
        verdict = (f"NO EDGE — accuracy {proposed['accuracy_pct']:.1f}% shuffled-label "
                   f"ceiling {floor:.1f}% se neeche/barabar hai")
        edge_found = False
    elif proposed['edge_pp'] > 5 and lo > proposed['baseline_pct']:
        verdict = (f"POSSIBLE EDGE ({proposed['edge_pp']:+.1f}pp) — trust karne se pehle "
                   f"aur data par verify karein")
        edge_found = True
    elif proposed['edge_pp'] > 0:
        verdict = (f"MARGINAL ({proposed['edge_pp']:+.1f}pp) — noise band "
                   f"(±{proposed['ci95_pp']:.1f}pp) ke andar")
        edge_found = False
    else:
        verdict = f"NO EDGE ({proposed['edge_pp']:+.1f}pp vs baseline)"
        edge_found = False

    artifact = {
        'schema': SCHEMA, 'model': MODEL, 'pipeline_version': 'fix100',
        'baseline_method': 'train-only majority per fold; OOS-count-weighted pooling',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        # Reproducibility: same numbers sirf inhi library versions par expect karein.
        'libs': _lib_versions(),
        'method': ('purged + embargoed expanding walk-forward '
                   f'({args.folds} folds, embargo = label horizon), '
                   'baseline = same-window majority class, binomial 95% CI'
                   + ('' if args.quick else ', shuffled-label permutation null')),
        'period': args.period, 'folds': args.folds,
        'symbols_requested': symbols, 'symbols_scored': sorted(frames),
        'strategies': out,
        'permutation_null': null_block,
        'verdict': verdict,
        'edge_found': bool(edge_found),
        'disclosure': ('Out-of-sample predictive-edge test only. Ye profitability ka proof '
                       'nahi hai; costs/slippage ke baad ke numbers research/run_study.py '
                       'aur RESEARCH_REPORT.md me hain.'),
    }

    path = args.output
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(artifact, indent=1, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)
    total_oos = sum(s['n_oos'] for s in out.values())
    print(f"\n✅ {path}: {len(out)} strategies, {total_oos:,} pooled OOS predictions")
    print(f"   VERDICT: {verdict}")
    print("   REMINDER: ye OOS predictive-edge test hai, profitable strategy ka proof nahi.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
