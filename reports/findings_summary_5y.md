# Findings summary — study_results_5y.json (auto-generated)

Study: 2026-09-30T07:18:56 · 20 symbols · round-trip cost 0.3276% · warmup 252 bars · 5 purged folds · prob threshold 0.55

## 1. ML quality vs baseline (paired across symbols)

| strategy | symbols | pooled acc % | pooled baseline % | pooled edge pp | mean edge pp | t-test p | Wilcoxon p | sign test p | symbols w/ +edge | null ceiling beat |
|---|---|---|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | 20 | 50.79 | 51.26 | -0.47 | -0.74 | 0.1727 n.s. | 0.2469 n.s. | 0.5034 n.s. | 8/20 | — |
| S2_ml_dir1_small10 | 20 | 50.74 | 51.26 | -0.52 | -0.48 | 0.2923 n.s. | 0.4897 n.s. | 0.8238 n.s. | 9/20 | — |
| S3_ml_ret5atr_small10 | 20 | 54.77 | 61.95 | -7.18 | -7.43 | 0.0000 *** | 0.0000 *** | 0.0000 *** | 0/20 | 2/20 |

- **S1_ml_dir1_app28**: pooled accuracy 50.79% vs baseline 51.26% → edge **-0.47pp** (binomial one-sided p = 0.8978); per-symbol mean edge -0.74pp, positive in 8/20.
- **S2_ml_dir1_small10**: pooled accuracy 50.74% vs baseline 51.26% → edge **-0.52pp** (binomial one-sided p = 0.9207); per-symbol mean edge -0.48pp, positive in 9/20.
- **S3_ml_ret5atr_small10**: pooled accuracy 54.77% vs baseline 61.95% → edge **-7.18pp** (binomial one-sided p = 1.0000); per-symbol mean edge -7.43pp, positive in 0/20.

## 2. Net-of-cost returns (same OOS window, lag=1, same costs)

| strategy | mean net % | median net % | t-test p (net vs 0) | mean excess vs B&H pp | symbols beating B&H | Wilcoxon p (excess) | mean trades | mean costs ₹ | cost drag pp | mean Sharpe | mean exposure % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | -25.57 | -25.86 | 0.0000 *** | -58.04 | 1/20 | 0.0000 *** | 137.1 | 46,755 | 46.75 | -1.09 | 38.6 |
| S2_ml_dir1_small10 | -25.53 | -28.76 | 0.0001 *** | -58.00 | 1/20 | 0.0000 *** | 124.8 | 42,202 | 42.20 | -1.08 | 42.2 |
| S3_ml_ret5atr_small10 | -9.10 | -10.63 | 0.0098 ** | -41.57 | 4/20 | 0.0006 *** | 77.7 | 28,701 | 28.70 | -0.89 | 28.5 |
| S4_rule_momentum | +18.38 | +10.40 | 0.0464 * | -14.09 | 9/20 | 0.1054 n.s. | 15.2 | 5,898 | 5.90 | -0.22 | 62.0 |
| S5_rule_rsi_meanrev | +9.95 | +10.34 | 0.0317 * | -22.52 | 7/20 | 0.0545 n.s. | 5.8 | 2,190 | 2.19 | -0.43 | 18.1 |
| S6_buy_hold | +32.47 | +41.67 | 0.0053 ** | +0.00 | 0/20 | nan n.s. | 1.0 | 192 | 0.19 | -0.03 | 99.8 |
| S7_ml_s3_minhold5 | -2.88 | -7.64 | 0.5796 n.s. | -35.35 | 4/20 | 0.0010 ** | 53.8 | 20,881 | 20.88 | -0.63 | 41.0 |
| S8_ml_s1_minhold5 | -6.88 | -11.16 | 0.3566 n.s. | -39.35 | 1/20 | 0.0000 *** | 82.8 | 31,169 | 31.17 | -0.59 | 58.7 |

## 3. Cost break-even (kitne trades afford kar sakte ho)

- Ek round-trip = **0.3276%**. Bina kisi edge ke, 30 round-trips ≈ **9.83%** equity kha jaate hain.
- S1_ml_dir1_app28: mean 137.1 trades → expected cost drag ≈ **44.91pp** (measured drag agree karta hai).
- S2_ml_dir1_small10: mean 124.8 trades → expected cost drag ≈ **40.90pp** (measured drag agree karta hai).
- S3_ml_ret5atr_small10: mean 77.7 trades → expected cost drag ≈ **25.44pp** (measured drag agree karta hai).

## 4. Verdict

- **S1_ml_dir1_app28**: ❌ NO EDGE — accuracy baseline se better nahi hai (statistically indistinguishable from a trivial majority-class rule)
- **S2_ml_dir1_small10**: ❌ NO EDGE — accuracy baseline se better nahi hai (statistically indistinguishable from a trivial majority-class rule)
- **S3_ml_ret5atr_small10**: ❌ NO EDGE — accuracy baseline se better nahi hai (statistically indistinguishable from a trivial majority-class rule)

> Note: 5 purged folds × 20 symbols, OOS ≈ 219–940 bars/symbol (mean 903). Ek symbol decisive nahi hota — pooled + paired tests dekho. Null (shuffled labels) agar model se upar hai, to "signal" noise hai.
