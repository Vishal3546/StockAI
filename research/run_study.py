#!/usr/bin/env python3
"""
research/run_study.py — "does ANY of this actually beat the costs?" study
================================================================================
Har strategy ko **same OOS window**, **same costs** aur **same execution lag**
par chalata hai, phir buy&hold se compare karta hai.

Strategies:
  S1  ML(dir1, app_28)      — jo abhi app.py karta hai (comparison baseline)
  S2  ML(dir1, small_10)    — same target, chhota de-correlated feature set
  S3  ML(ret5_atr, small_10) — proposed: volatility-adjusted 5-day label
                                 (+ PERMUTATION NULL, shuffled-label ceiling)
  S4  Momentum rule         — Close > SMA200
  S5  RSI mean-reversion    — RSI<30 entry, RSI>55 exit
  S6  Buy & Hold            — reference
  S7  ML S3 + min-hold 5    — turnover/cost fix ka test
  S8  ML S1 + min-hold 5    — same fix, app ke current config par

Output:
  reports/study_results.json   (raw numbers)
  reports/study_table.md       (markdown tables)
  RESEARCH_REPORT.md           (findings + verdict, root par)

    python3 research/run_study.py [--symbols RELIANCE,TCS] [--quick]
"""
import argparse
import json
import pathlib
import sys
import time
import warnings

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.backtest import buy_and_hold, run_backtest          # noqa: E402
from research.costs import CostConfig, DEFAULT as DEFAULT_COSTS    # noqa: E402
from research.data import load                                    # noqa: E402
from research.features import (APP_28, SMALL_10, build_features,   # noqa: E402
                               build_labels, decorrelate)
from research import ml_lab                                        # noqa: E402

UNIVERSE = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'SBIN', 'BHARTIARTL', 'ITC',
            # FIX-87: 'TATAMOTORS' -> 'TMPV' (demerger, 1 Oct 2025). Ticker ab exist nahi karta.
            'KOTAKBANK', 'LT', 'WIPRO', 'AXISBANK', 'MARUTI', 'TMPV', 'BAJFINANCE',
            'SUNPHARMA', 'TITAN', 'NTPC', 'ONGC', 'TATASTEEL']

WARMUP = 252          # 1 saal training se pehle
N_FOLDS = 5
PROB_THRESHOLD = 0.55  # app ka threshold
CAPITAL = 100_000.0


# ─────────────────────────── simple rule strategies ─────────────────────────
def rule_momentum(df: pd.DataFrame) -> pd.Series:
    sma = df['Close'].rolling(200, min_periods=200).mean()
    return (df['Close'] > sma).astype(float).fillna(0.0)


def rule_rsi_meanrev(df: pd.DataFrame, entry: float = 30, exit_: float = 55) -> pd.Series:
    r = df['rsi'].values
    pos, state = np.zeros(len(r)), 0
    for i, v in enumerate(r):
        if not np.isnan(v):
            if state == 0 and v < entry:
                state = 1
            elif state == 1 and v > exit_:
                state = 0
        pos[i] = state
    return pd.Series(pos, index=df.index)


def apply_min_hold(pos: pd.Series, min_bars: int = 5) -> pd.Series:
    """
    Signal ko minimum `min_bars` bars tak hold karo — chhote flip-flop (jo sirf
    brokerage/STT generate karte hain) kill ho jaate hain. Costs ke against ye
    sabse sasta structural fix hai.
    """
    vals = pos.to_numpy(dtype=float)
    out = np.zeros_like(vals)
    state, held = 0.0, 0
    for i, v in enumerate(vals):
        if state == 0.0:
            if v > 0.5:
                state, held = 1.0, 1
        else:
            held += 1
            if v < 0.5 and held > min_bars:
                state = 0.0
        out[i] = state
    return pd.Series(out, index=pos.index)


def ml_positions(res: 'ml_lab.WFResult', index: pd.DatetimeIndex) -> pd.Series:
    """OOS probabilities → long/flat positions (aage ka data fill karne ke saath)."""
    s = pd.Series((res.oos_prob >= PROB_THRESHOLD).astype(float), index=res.oos_index)
    s = s[~s.index.duplicated(keep='last')]
    return s.reindex(index).ffill().fillna(0.0)


# ─────────────────────────── main study ─────────────────────────────────────
def study_symbol(sym: str, cfg: CostConfig, quick: bool = False,
                 period: str = '2y') -> dict | None:
    df = load(sym, period=period)
    if df is None or len(df) < WARMUP + 60:
        print(f"   ⚠️  {sym}: data nahi mila / bahut kam ({0 if df is None else len(df)} bars)")
        return None

    feats = build_features(df)
    out = {'symbol': sym, 'bars': len(df), 'start': str(df.index[0])[:10], 'end': str(df.index[-1])[:10]}

    # ── feature-set diagnostics ──
    kept28, dropped28 = decorrelate(feats, APP_28, threshold=0.85)
    out['feature_diag'] = {'app_28_kept': len(kept28), 'app_28_dropped': dropped28,
                           'small_10': len(SMALL_10)}

    strategies, ml_results = {}, {}

    # ── ML strategies ──
    specs = [('S1_ml_dir1_app28', 'dir1', APP_28, False),
             ('S2_ml_dir1_small10', 'dir1', SMALL_10, False),
             ('S3_ml_ret5atr_small10', 'ret5_atr', SMALL_10, not quick)]
    for name, label_kind, feat_list, do_null in specs:
        t0 = time.time()
        lab = build_labels(feats, label_kind)
        # inf → NaN (warna StandardScaler NaN de deta hai), phir dropna
        lab[feat_list] = lab[feat_list].replace([np.inf, -np.inf], np.nan)
        data = lab.dropna(subset=feat_list + ['label'])
        if len(data) < WARMUP + 80:
            continue
        X, y = data[feat_list], data['label']
        try:
            res = ml_lab.purged_walk_forward(X, y, n_folds=N_FOLDS, warmup=WARMUP,
                                             embargo=int(lab['horizon'].iloc[0]))
        except Exception as e:
            print(f"   ⚠️  {sym} {name}: {e}")
            continue
        entry = {'label': label_kind, 'features': len(feat_list), **res.to_dict(),
                 'verdict': ml_lab.verdict(res), 'seconds': round(time.time() - t0, 1)}
        if do_null:
            null = ml_lab.permutation_null(X, y, n_perm=3, n_folds=N_FOLDS, warmup=WARMUP,
                                           embargo=int(lab['horizon'].iloc[0]))
            entry['permutation_null'] = null
            entry['verdict'] = ml_lab.verdict(res, null)
        ml_results[name] = res
        strategies[name] = entry
    out['ml'] = strategies

    if not ml_results:
        return out

    # ── common OOS window (sab strategies isi par compare honge) ──
    oos_start = max(r.oos_index.min() for r in ml_results.values())
    oos_df = df.loc[df.index >= oos_start].copy()
    out['oos'] = {'start': str(oos_df.index[0])[:10], 'end': str(oos_df.index[-1])[:10],
                  'bars': len(oos_df)}

    # ── backtests (same costs, same lag, same window) ──
    bt = {}
    for name, res in ml_results.items():
        bt[name] = run_backtest(oos_df, ml_positions(res, oos_df.index), capital=CAPITAL, cost=cfg)
    # NOTE: rule indicators poori history par compute hote hain, phir OOS window
    # par slice — warna SMA200 sirf OOS slice par NaN hota (200-bar warmup gayab)
    # aur rule kabhi entry hi na leta. (Study ke doosre run me pakda gaya bug.)
    mom_full = rule_momentum(feats)
    rsi_full = rule_rsi_meanrev(feats)
    bt['S4_rule_momentum'] = run_backtest(oos_df, mom_full.reindex(oos_df.index),  capital=CAPITAL, cost=cfg)
    bt['S5_rule_rsi_meanrev'] = run_backtest(oos_df, rsi_full.reindex(oos_df.index), capital=CAPITAL, cost=cfg)

    # ── S7/S8: ML signal + minimum-hold (turnover fix) ──
    for src, dst in (('S3_ml_ret5atr_small10', 'S7_ml_s3_minhold5'),
                     ('S1_ml_dir1_app28', 'S8_ml_s1_minhold5')):
        if src in ml_results:
            base_pos = ml_positions(ml_results[src], oos_df.index)
            bt[dst] = run_backtest(oos_df, apply_min_hold(base_pos, min_bars=5),
                                   capital=CAPITAL, cost=cfg)
    bt['S6_buy_hold'] = buy_and_hold(oos_df, capital=CAPITAL, cost=cfg)

    out['backtest'] = {k: {'metrics': v.metrics, 'costs_total': round(v.costs_total, 2),
                           'final_equity': round(float(v.equity.iloc[-1]), 2)}
                       for k, v in bt.items()}
    out['signals_long_pct'] = {k: round(float(v.positions.mean() * 100), 1) for k, v in bt.items()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='')
    ap.add_argument('--quick', action='store_true', help='skip permutation null (fast)')
    ap.add_argument('--period', default='2y', help="data window: 2y / 5y / 10y (default 2y)")
    args = ap.parse_args()
    universe = [s.strip().upper() for s in args.symbols.split(',')] if args.symbols else UNIVERSE

    cfg = CostConfig()
    print('=' * 88)
    print(f" STOCKAI RESEARCH STUDY — {len(universe)} symbols · purged walk-forward · "
          f"costs {cfg.round_trip_pct():.2f}% round trip")
    print('=' * 88)
    print(f" round-trip hurdle : {cfg.round_trip_pct():.2f}%  (delivery STT + brokerage + stamp + GST"
          f" + {cfg.slippage_bps}bps slippage)")
    print(f" execution         : signal at close t → position at t+1 (lag=1)")
    print(f" window            : {WARMUP}-bar warmup · {N_FOLDS} expanding folds · "
          f"prob threshold {PROB_THRESHOLD}\n")

    t0 = time.time()
    rows = []
    for i, sym in enumerate(universe, 1):
        r = study_symbol(sym, cfg, quick=args.quick, period=args.period)
        if r:
            rows.append(r)
            m = r.get('ml', {}).get('S3_ml_ret5atr_small10', {})
            b = r.get('backtest', {})
            print(f" [{i:02d}/{len(universe)}] {sym:11s} bars={r['bars']:3d} "
                  f"| S3 acc {m.get('accuracy_pct', '—')}% edge {m.get('edge_pp', '—')}pp "
                  f"null {m.get('permutation_null', {}).get('max_pct', '—')}% "
                  f"| net: S3 {b.get('S3_ml_ret5atr_small10', {}).get('metrics', {}).get('total_return_pct', '—')}% "
                  f"mom {b.get('S4_rule_momentum', {}).get('metrics', {}).get('total_return_pct', '—')}% "
                  f"B&H {b.get('S6_buy_hold', {}).get('metrics', {}).get('total_return_pct', '—')}%",
                  flush=True)
    print(f"\n study time: {time.time() - t0:.0f}s · symbols used: {len(rows)} · period {args.period}")

    if not rows:
        print(" ❌ koi symbol study nahi hua"); return

    # ── aggregate ──
    strat_names = sorted({k for r in rows for k in r.get('backtest', {})})
    agg = {}
    for s in strat_names:
        vals = [r['backtest'][s]['metrics'] for r in rows if s in r.get('backtest', {})]
        if not vals:
            continue
        net = np.array([v['total_return_pct'] for v in vals], dtype=float)
        bh = np.array([r['backtest']['S6_buy_hold']['metrics']['total_return_pct']
                       for r in rows if 'S6_buy_hold' in r.get('backtest', {})], dtype=float)
        agg[s] = {
            'n': len(vals),
            'mean_net_return_pct': round(float(net.mean()), 2),
            'median_net_return_pct': round(float(np.median(net)), 2),
            'mean_excess_vs_bh_pp': round(float(np.mean(net - bh[:len(net)])), 2),
            'pct_beating_bh': round(float((net > bh[:len(net)]).mean() * 100), 1),
            'mean_costs_rs': round(float(np.mean([v['costs_paid'] for v in vals])), 0),
            'mean_trades': round(float(np.mean([v['trades'] for v in vals])), 1),
            'mean_sharpe': round(float(np.mean([v['sharpe'] for v in vals])), 2),
            'mean_maxdd_pct': round(float(np.mean([v['max_drawdown_pct'] for v in vals])), 2),
            'mean_exposure_pct': round(float(np.mean([v['exposure_pct'] for v in vals])), 1),
        }

    ml_agg = {}
    for key in ('S1_ml_dir1_app28', 'S2_ml_dir1_small10', 'S3_ml_ret5atr_small10'):
        vals = [r['ml'][key] for r in rows if key in r.get('ml', {})]
        if not vals:
            continue
        ml_agg[key] = {
            'n': len(vals),
            'mean_accuracy_pct': round(float(np.mean([v['accuracy_pct'] for v in vals])), 2),
            'mean_baseline_pct': round(float(np.mean([v['baseline_pct'] for v in vals])), 2),
            'mean_edge_pp': round(float(np.mean([v['edge_pp'] for v in vals])), 2),
            'symbols_positive_edge': int(sum(1 for v in vals if v['edge_pp'] > 0)),
            'symbols_edge_gt_2pp': int(sum(1 for v in vals if v['edge_pp'] > 2)),
            'mean_ci95_pp': round(float(np.mean([v['ci95_pp'] for v in vals])), 2),
            'mean_fold_sigma_pp': round(float(np.mean([v['fold_sigma_pp'] for v in vals])), 2),
            'total_oos_predictions': int(sum(v['n_oos'] for v in vals)),
        }
    nulls = [r['ml']['S3_ml_ret5atr_small10'].get('permutation_null', {})
             for r in rows if 'S3_ml_ret5atr_small10' in r.get('ml', {})]
    nulls = [n for n in nulls if n.get('n')]
    if nulls:
        ml_agg['S3_permutation_null'] = {
            'symbols': len(nulls),
            'mean_ceiling_pct': round(float(np.mean([n['max_pct'] for n in nulls])), 2),
            'mean_null_pct': round(float(np.mean([n['mean_pct'] for n in nulls])), 2),
        }

    results = {'generated': pd.Timestamp.now().isoformat(), 'costs': cfg.as_dict(),
               'round_trip_pct': cfg.round_trip_pct(), 'config': {
                   'warmup': WARMUP, 'folds': N_FOLDS, 'threshold': PROB_THRESHOLD,
                   'capital': CAPITAL, 'exec_lag': 1, 'period': args.period},
               'universe': [r['symbol'] for r in rows],
               'ml_aggregate': ml_agg, 'strategy_aggregate': agg, 'per_symbol': rows}

    rd = ROOT / 'reports'
    rd.mkdir(exist_ok=True)
    blob = json.dumps(results, indent=1, default=str)
    (rd / f'study_results_{args.period}.json').write_text(blob)
    (rd / 'study_results.json').write_text(blob)      # alias: sabse recent run

    # ── markdown tables ──
    def md_table(d, cols, headers):
        hdr = '| ' + ' | '.join(headers) + ' |\n|' + '|'.join(['---'] * len(headers)) + '|\n'
        return hdr + ''.join('| ' + ' | '.join(str(row[c]) for c in cols) + ' |\n' for row in d)

    ml_rows = [{'strategy': k, **v} for k, v in ml_agg.items() if '_ml_' in k]
    bt_rows = [{'strategy': k, **v} for k, v in agg.items()]
    md = '# Study tables (auto-generated)\n\n## ML quality\n\n'
    md += md_table(ml_rows, ['strategy', 'n', 'mean_accuracy_pct', 'mean_baseline_pct',
                             'mean_edge_pp', 'mean_ci95_pp', 'symbols_positive_edge',
                             'total_oos_predictions'],
                   ['strategy', 'symbols', 'mean acc %', 'baseline %', 'edge pp', '±95% CI pp',
                    'symbols w/ +edge', 'OOS preds'])
    md += '\n## Net-of-cost performance (same OOS window)\n\n'
    md += md_table(bt_rows, ['strategy', 'n', 'mean_net_return_pct', 'mean_excess_vs_bh_pp',
                             'pct_beating_bh', 'mean_costs_rs', 'mean_trades', 'mean_sharpe',
                             'mean_maxdd_pct', 'mean_exposure_pct'],
                   ['strategy', 'symbols', 'mean net %', 'excess vs B&H pp', '% beating B&H',
                    'costs ₹', 'trades', 'Sharpe', 'maxDD %', 'exposure %'])
    md += '\n## Per symbol — ML (S3, proposed design)\n\n'
    ps = [{'symbol': r['symbol'],
           'acc': r.get('ml', {}).get('S3_ml_ret5atr_small10', {}).get('accuracy_pct'),
           'base': r.get('ml', {}).get('S3_ml_ret5atr_small10', {}).get('baseline_pct'),
           'edge': r.get('ml', {}).get('S3_ml_ret5atr_small10', {}).get('edge_pp'),
           'null_max': r.get('ml', {}).get('S3_ml_ret5atr_small10', {}).get('permutation_null', {}).get('max_pct'),
           'net_pct': r.get('backtest', {}).get('S3_ml_ret5atr_small10', {}).get('metrics', {}).get('total_return_pct'),
           'bh_pct': r.get('backtest', {}).get('S6_buy_hold', {}).get('metrics', {}).get('total_return_pct'),
           'verdict': r.get('ml', {}).get('S3_ml_ret5atr_small10', {}).get('verdict', '')} for r in rows]
    md += md_table(ps, ['symbol', 'acc', 'base', 'edge', 'null_max', 'net_pct', 'bh_pct', 'verdict'],
                   ['symbol', 'acc %', 'base %', 'edge pp', 'null ceiling %', 'net %', 'B&H %', 'verdict'])
    (rd / f'study_table_{args.period}.md').write_text(md)
    (rd / 'study_table.md').write_text(md)

    print('\n' + '=' * 88)
    print(' ML QUALITY (mean over symbols)')
    print('=' * 88)
    for k, v in ml_agg.items():
        if '_ml_' in k:
            print(f"  {k:24s} acc {v['mean_accuracy_pct']:5.2f}%  base {v['mean_baseline_pct']:5.2f}%  "
                  f"edge {v['mean_edge_pp']:+5.2f}pp  ±{v['mean_ci95_pp']:.2f}  "
                  f"|+edge {v['symbols_positive_edge']}/{v['n']}  OOS {v['total_oos_predictions']}")
    if 'S3_permutation_null' in ml_agg:
        n = ml_agg['S3_permutation_null']
        print(f"  {'S3 shuffled-label null':24s} mean {n['mean_null_pct']}%  ceiling {n['mean_ceiling_pct']}%  "
              f"({n['symbols']} symbols)")

    print('\n' + '=' * 88)
    print(' NET-OF-COST (same OOS window, all strategies)')
    print('=' * 88)
    print(f"  {'strategy':24s} {'mean net%':>10} {'vs B&H pp':>10} {'beat B&H%':>10} "
          f"{'costs ₹':>9} {'trades':>7} {'Sharpe':>7} {'exp%':>6}")
    for k, v in agg.items():
        print(f"  {k:24s} {v['mean_net_return_pct']:>10.2f} {v['mean_excess_vs_bh_pp']:>+10.2f} "
              f"{v['pct_beating_bh']:>10.1f} {v['mean_costs_rs']:>9,.0f} {v['mean_trades']:>7.1f} "
              f"{v['mean_sharpe']:>7.2f} {v['mean_exposure_pct']:>6.1f}")
    print(f"\n  💾 reports/study_results.json · reports/study_table.md")


if __name__ == '__main__':
    main()
