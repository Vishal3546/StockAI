# Study tables (auto-generated)

## ML quality

| strategy | symbols | mean acc % | baseline % | edge pp | ±95% CI pp | symbols w/ +edge | OOS preds |
|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | 6 | 49.96 | 51.97 | -2.01 | 3.52 | 1 | 4877 |
| S2_ml_dir1_small10 | 6 | 49.69 | 51.99 | -2.3 | 3.52 | 1 | 4891 |
| S3_ml_ret5atr_small10 | 6 | 55.65 | 65.12 | -9.47 | 3.47 | 0 | 4867 |

## Net-of-cost performance (same OOS window)

| strategy | symbols | mean net % | excess vs B&H pp | % beating B&H | costs ₹ | trades | Sharpe | maxDD % | exposure % |
|---|---|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | 6 | -21.5 | -21.75 | 33.3 | 38490.0 | 113.8 | -0.75 | -34.96 | 38.2 |
| S2_ml_dir1_small10 | 6 | -26.61 | -26.85 | 16.7 | 34612.0 | 106.5 | -0.91 | -41.72 | 38.9 |
| S3_ml_ret5atr_small10 | 6 | -17.56 | -17.81 | 16.7 | 22678.0 | 67.7 | -0.8 | -35.48 | 26.9 |
| S4_rule_momentum | 6 | 6.25 | 6.01 | 83.3 | 4741.0 | 13.2 | -0.45 | -27.47 | 52.1 |
| S5_rule_rsi_meanrev | 6 | 0.39 | 0.15 | 66.7 | 2345.0 | 6.2 | -0.37 | -25.0 | 25.2 |
| S6_buy_hold | 6 | 0.24 | 0.0 | 0.0 | 370.0 | 1.0 | -0.25 | -43.25 | 99.9 |
| S7_ml_s3_minhold5 | 6 | -24.16 | -24.4 | 33.3 | 15369.0 | 46.8 | -0.77 | -41.61 | 39.1 |
| S8_ml_s1_minhold5 | 6 | -19.25 | -19.5 | 16.7 | 25002.0 | 69.8 | -0.6 | -40.44 | 56.8 |

## Per symbol — ML (S3, proposed design)

| symbol | acc % | base % | edge pp | null ceiling % | net % | B&H % | verdict |
|---|---|---|---|---|---|---|---|
| RELIANCE | 56.56 | 61.4 | -4.84 | 59.03 | -3.65 | -0.45 | NO EDGE — accuracy 56.6% shuffled-label ceiling 59.0% se neeche/barabar hai |
| TCS | 56.45 | 66.77 | -10.32 | 62.58 | -38.99 | -33.91 | NO EDGE — accuracy 56.5% shuffled-label ceiling 62.6% se neeche/barabar hai |
| HDFCBANK | 51.51 | 65.05 | -13.55 | 60.97 | -26.25 | -13.18 | NO EDGE — accuracy 51.5% shuffled-label ceiling 61.0% se neeche/barabar hai |
| JIOFIN | 65.25 | 67.59 | -2.35 | 63.11 | -2.36 | -31.03 | NO EDGE (-2.3pp vs baseline) |
| EMIL | 56.19 | 70.35 | -14.16 | 62.83 | -32.02 | -13.23 | NO EDGE — accuracy 56.2% shuffled-label ceiling 62.8% se neeche/barabar hai |
| CANBK | 47.96 | 59.57 | -11.61 | 53.98 | -2.1 | 93.26 | NO EDGE — accuracy 48.0% shuffled-label ceiling 54.0% se neeche/barabar hai |
