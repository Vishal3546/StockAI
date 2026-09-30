# Findings summary — study_results_2y.json (auto-generated)

Study: 2026-09-30T07:14:09 · 20 symbols · round-trip cost 0.3276% · warmup 252 bars · 5 purged folds · prob threshold 0.55

## 1. ML quality vs baseline (paired across symbols)

| strategy | symbols | pooled acc % | pooled baseline % | pooled edge pp | mean edge pp | t-test p | Wilcoxon p | sign test p | symbols w/ +edge | null ceiling beat |
|---|---|---|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | 20 | 52.08 | 53.65 | -1.57 | -1.53 | 0.3020 n.s. | 0.3224 n.s. | 0.5034 n.s. | 8/20 | — |
| S2_ml_dir1_small10 | 20 | 50.23 | 53.78 | -3.55 | -3.57 | 0.0004 *** | 0.0017 ** | 0.0414 * | 5/20 | — |
| S3_ml_ret5atr_small10 | 20 | 56.11 | 68.52 | -12.41 | -12.40 | 0.0000 *** | 0.0002 *** | 0.0000 *** | 1/20 | 3/20 |

- **S1_ml_dir1_app28**: pooled accuracy 52.08% vs baseline 53.65% → edge **-1.57pp** (binomial one-sided p = 0.9750); per-symbol mean edge -1.53pp, positive in 8/20.
- **S2_ml_dir1_small10**: pooled accuracy 50.23% vs baseline 53.78% → edge **-3.55pp** (binomial one-sided p = 1.0000); per-symbol mean edge -3.57pp, positive in 5/20.
- **S3_ml_ret5atr_small10**: pooled accuracy 56.11% vs baseline 68.52% → edge **-12.41pp** (binomial one-sided p = 1.0000); per-symbol mean edge -12.40pp, positive in 1/20.

## 2. Net-of-cost returns (same OOS window, lag=1, same costs)

| strategy | mean net % | median net % | t-test p (net vs 0) | mean excess vs B&H pp | symbols beating B&H | Wilcoxon p (excess) | mean trades | mean costs ₹ | cost drag pp | mean Sharpe | mean exposure % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | -12.32 | -13.64 | 0.0001 *** | +0.21 | 11/20 | 0.8408 n.s. | 28.6 | 9,990 | 9.99 | -1.30 | 43.6 |
| S2_ml_dir1_small10 | -14.48 | -14.34 | 0.0000 *** | -1.94 | 8/20 | 0.5958 n.s. | 27.6 | 9,532 | 9.53 | -1.53 | 42.5 |
| S3_ml_ret5atr_small10 | -10.11 | -12.62 | 0.0010 ** | +2.43 | 12/20 | 0.4524 n.s. | 16.6 | 5,812 | 5.81 | -1.55 | 27.0 |
| S4_rule_momentum | -7.06 | -6.48 | 0.0029 ** | +5.48 | 10/20 | 0.4524 n.s. | 3.9 | 1,317 | 1.32 | -1.30 | 41.4 |
| S5_rule_rsi_meanrev | +1.13 | +0.60 | 0.6936 n.s. | +13.67 | 17/20 | 0.0042 ** | 2.1 | 704 | 0.70 | -0.30 | 29.7 |
| S6_buy_hold | -12.54 | -5.24 | 0.0051 ** | +0.00 | 0/20 | nan n.s. | 1.0 | 193 | 0.19 | -0.83 | 99.0 |
| S7_ml_s3_minhold5 | -10.22 | -10.52 | 0.0007 *** | +2.31 | 10/20 | 0.5217 n.s. | 11.8 | 4,098 | 4.10 | -1.30 | 39.8 |
| S8_ml_s1_minhold5 | -9.88 | -10.71 | 0.0033 ** | +2.66 | 12/20 | 0.2611 n.s. | 17.4 | 6,116 | 6.12 | -0.92 | 62.1 |

## 3. Cost break-even (kitne trades afford kar sakte ho)

- Ek round-trip = **0.3276%**. Bina kisi edge ke, 30 round-trips ≈ **9.83%** equity kha jaate hain.
- S1_ml_dir1_app28: mean 28.6 trades → expected cost drag ≈ **9.37pp** (measured drag agree karta hai).
- S2_ml_dir1_small10: mean 27.6 trades → expected cost drag ≈ **9.03pp** (measured drag agree karta hai).
- S3_ml_ret5atr_small10: mean 16.6 trades → expected cost drag ≈ **5.45pp** (measured drag agree karta hai).

## 4. Verdict

- **S1_ml_dir1_app28**: ❌ NO EDGE — accuracy baseline se better nahi hai (statistically indistinguishable from a trivial majority-class rule)
- **S2_ml_dir1_small10**: ❌ NO EDGE — accuracy baseline se better nahi hai (statistically indistinguishable from a trivial majority-class rule)
- **S3_ml_ret5atr_small10**: ❌ NO EDGE — accuracy baseline se better nahi hai (statistically indistinguishable from a trivial majority-class rule)

> Note: 5 purged folds × 20 symbols, OOS ≈ 198–219 bars/symbol (mean 200). Ek symbol decisive nahi hota — pooled + paired tests dekho. Null (shuffled labels) agar model se upar hai, to "signal" noise hai.
