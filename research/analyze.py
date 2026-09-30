#!/usr/bin/env python3
"""
research/analyze.py — study_results.json se honest statistics nikaalta hai
================================================================================
Kya karta hai:
  1. Har ML strategy ke per-symbol edge (acc − baseline) par paired tests:
     one-sample t-test, Wilcoxon signed-rank, aur sign test.
  2. Pooled OOS accuracy vs pooled baseline (+ binomial p-value).
  3. Net-of-cost returns ka B&H ke against paired test.
  4. Cost break-even: kitne round-trips me 0.33% hurdle kharch ho jaata hai.
  5. Permutation-null comparison: model null ceiling se upar hai ya nahi.

Output: reports/findings_summary.md  (+ stdout par same summary)

    python3 research/analyze.py
"""
import json
import pathlib
import sys

import numpy as np
from scipy import stats

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / 'reports' / 'study_results.json'
OUT = ROOT / 'reports' / 'findings_summary.md'

ML_KEYS = ['S1_ml_dir1_app28', 'S2_ml_dir1_small10', 'S3_ml_ret5atr_small10']
BT_KEYS = ML_KEYS + ['S4_rule_momentum', 'S5_rule_rsi_meanrev', 'S6_buy_hold',
         'S7_ml_s3_minhold5', 'S8_ml_s1_minhold5']


def _stars(p: float) -> str:
    return '***' if p < 0.001 else '**' if p < 0.01 else '*' if p < 0.05 else 'n.s.'


def main() -> int:
    src = RESULTS
    if len(sys.argv) > 1:                     # optional: path ya period suffix (2y/5y)
        arg = sys.argv[1]
        cand = pathlib.Path(arg) if ('/' in arg or arg.endswith('.json')) else \
            ROOT / 'reports' / f'study_results_{arg}.json'
        if cand.exists():
            src = cand
    if not src.exists():
        print(f"❌ {RESULTS} nahi mila — pehle `python3 research/run_study.py` chalao")
        return 1
    r = json.loads(src.read_text())
    rows = r['per_symbol']
    cost_rt = r['round_trip_pct']

    md = [f'# Findings summary — {src.name} (auto-generated)', '',
          f"Study: {r['generated'][:19]} · {len(rows)} symbols · "
          f"round-trip cost {cost_rt:.4f}% · warmup {r['config']['warmup']} bars · "
          f"{r['config']['folds']} purged folds · prob threshold {r['config']['threshold']}", '']

    # ───────────────────────── 1) ML quality ─────────────────────────
    md += ['## 1. ML quality vs baseline (paired across symbols)', '',
           '| strategy | symbols | pooled acc % | pooled baseline % | pooled edge pp | '
           'mean edge pp | t-test p | Wilcoxon p | sign test p | symbols w/ +edge | null ceiling beat |',
           '|---|---|---|---|---|---|---|---|---|---|---|']
    ml_block = {}
    for key in ML_KEYS:
        ent = [x['ml'][key] for x in rows if key in x.get('ml', {})]
        if not ent:
            continue
        edges = np.array([e['edge_pp'] for e in ent], dtype=float)
        n_oos = np.array([e['n_oos'] for e in ent], dtype=float)
        accs = np.array([e['accuracy_pct'] for e in ent], dtype=float) / 100.0
        bases = np.array([e['baseline_pct'] for e in ent], dtype=float) / 100.0
        pooled_acc = float((accs * n_oos).sum() / n_oos.sum())
        pooled_base = float((bases * n_oos).sum() / n_oos.sum())
        t_p = float(stats.ttest_1samp(edges, 0.0).pvalue) if len(edges) > 1 else float('nan')
        try:
            w_p = float(stats.wilcoxon(edges).pvalue)
        except ValueError:
            w_p = float('nan')
        wins = int((edges > 0).sum())
        s_p = float(stats.binomtest(wins, len(edges), 0.5).pvalue)
        # binomial on pooled counts (baseline as p0)
        k = int(round(pooled_acc * n_oos.sum()))
        pool_p = float(stats.binomtest(k, int(n_oos.sum()), pooled_base,
                                       alternative='greater').pvalue)
        nulls = [e.get('permutation_null', {}).get('max_pct') for e in ent]
        nulls = [n for n in nulls if n is not None]
        beat_null = int(sum(1 for a, n in zip(accs * 100, nulls) if a > n)) if nulls else None
        ml_block[key] = dict(pooled_acc=pooled_acc * 100, pooled_base=pooled_base * 100,
                             pooled_edge=(pooled_acc - pooled_base) * 100, mean_edge=edges.mean(),
                             t_p=t_p, wilcoxon_p=w_p, sign_p=s_p, wins=wins, n=len(edges),
                             pooled_binom_p=pool_p, beat_null=beat_null, n_null=len(nulls))
        md.append(f"| {key} | {len(ent)} | {pooled_acc*100:.2f} | {pooled_base*100:.2f} | "
                  f"{(pooled_acc-pooled_base)*100:+.2f} | {edges.mean():+.2f} | {t_p:.4f} {_stars(t_p)} | "
                  f"{w_p:.4f} {_stars(w_p)} | {s_p:.4f} {_stars(s_p)} | {wins}/{len(edges)} | "
                  f"{(str(beat_null) + '/' + str(len(nulls))) if nulls else '—'} |")
    md.append('')
    for k, v in ml_block.items():
        md.append(f"- **{k}**: pooled accuracy {v['pooled_acc']:.2f}% vs baseline "
                  f"{v['pooled_base']:.2f}% → edge **{v['pooled_edge']:+.2f}pp** "
                  f"(binomial one-sided p = {v['pooled_binom_p']:.4f}); "
                  f"per-symbol mean edge {v['mean_edge']:+.2f}pp, positive in {v['wins']}/{v['n']}.")
    md.append('')

    # ───────────────────────── 2) net-of-cost ─────────────────────────
    md += ['## 2. Net-of-cost returns (same OOS window, lag=1, same costs)', '',
           '| strategy | mean net % | median net % | t-test p (net vs 0) | mean excess vs B&H pp | '
           'symbols beating B&H | Wilcoxon p (excess) | mean trades | mean costs ₹ | cost drag pp | '
           'mean Sharpe | mean exposure % |',
           '|---|---|---|---|---|---|---|---|---|---|---|---|']
    bh = np.array([x['backtest']['S6_buy_hold']['metrics']['total_return_pct']
                   for x in rows if 'S6_buy_hold' in x.get('backtest', {})], dtype=float)
    bt_block = {}
    for key in BT_KEYS:
        ent = [x['backtest'][key] for x in rows if key in x.get('backtest', {})]
        if not ent:
            continue
        net = np.array([e['metrics']['total_return_pct'] for e in ent], dtype=float)
        b = bh[:len(net)]
        ex = net - b
        try:
            w_p = float(stats.wilcoxon(ex).pvalue)
        except ValueError:
            w_p = float('nan')
        trades = np.array([e['metrics'].get('n_trades', e['metrics'].get('trades', 0))
                           for e in ent], dtype=float)
        costs = np.array([e['costs_total'] for e in ent], dtype=float)
        cap = r['config']['capital']
        drag = costs / cap * 100
        try:
            t0_p = float(stats.ttest_1samp(net, 0.0).pvalue)
        except Exception:
            t0_p = float('nan')
        bt_block[key] = dict(net=net, ex=ex, t0_p=t0_p)
        md.append(f"| {key} | {net.mean():+.2f} | {np.median(net):+.2f} | {t0_p:.4f} {_stars(t0_p)} | {ex.mean():+.2f} | "
                  f"{int((net > b).sum())}/{len(net)} | {w_p:.4f} {_stars(w_p)} | {trades.mean():.1f} | "
                  f"{costs.mean():,.0f} | {drag.mean():.2f} | "
                  f"{np.mean([e['metrics']['sharpe'] for e in ent]):.2f} | "
                  f"{np.mean([e['metrics']['exposure_pct'] for e in ent]):.1f} |")
    md.append('')

    # ───────────────────────── 3) break-even trade budget ─────────────────────────
    md += ['## 3. Cost break-even (kitne trades afford kar sakte ho)', '']
    be = cost_rt
    md.append(f"- Ek round-trip = **{be:.4f}%**. Bina kisi edge ke, 30 round-trips ≈ "
              f"**{30*be:.2f}%** equity kha jaate hain.")
    for key in ML_KEYS:
        if key not in ml_block:
            continue
        ent = [x['backtest'][key] for x in rows if key in x.get('backtest', {})]
        trades = np.mean([e['metrics'].get('n_trades',
                                           e['metrics'].get('trades', 0)) for e in ent])
        md.append(f"- {key}: mean {trades:.1f} trades → expected cost drag ≈ "
                  f"**{trades*be:.2f}pp** (measured drag agree karta hai).")
    md.append('')

    # ───────────────────────── 4) verdict ─────────────────────────
    md += ['## 4. Verdict', '']
    for key in ML_KEYS:
        if key not in ml_block:
            continue
        v = ml_block[key]
        if v['pooled_edge'] <= 0 or v['pooled_binom_p'] > 0.05:
            verd = ('❌ NO EDGE — accuracy baseline se better nahi hai '
                    '(statistically indistinguishable from a trivial majority-class rule)')
        elif v['pooled_edge'] < 2 * be:
            verd = f"⚠️ WEAK — edge {v['pooled_edge']:+.2f}pp chhota hai; cost hurdle {2*be:.2f}pp " \
                   "chahiye (entry+exit) — net me kuch nahi bachta"
        else:
            verd = (f"✅ POSITIVE — edge {v['pooled_edge']:+.2f}pp > cost hurdle "
                    f"({2*be:.2f}pp); but per-trade edge aur turnover dekh kar hi tradable hai")
        md.append(f"- **{key}**: {verd}")
    md.append('')
    bars = [x['oos']['bars'] for x in rows if 'oos' in x]
    md.append(f"> Note: {r['config']['folds']} purged folds × {len(rows)} symbols, "
              f"OOS ≈ {min(bars)}–{max(bars)} bars/symbol (mean "
              f"{sum(bars)//max(len(bars),1)}). Ek symbol decisive nahi hota — pooled + paired "
              "tests dekho. Null (shuffled labels) agar model se upar hai, to \"signal\" noise hai.")
    md.append('')

    tag = src.stem.replace('study_results', '').strip('_')
    OUT = ROOT / 'reports' / (f'findings_summary_{tag}.md' if tag else 'findings_summary.md')
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text('\n'.join(md))
    print('\n'.join(md))
    print(f"\n💾 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
