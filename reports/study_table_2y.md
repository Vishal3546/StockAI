# Study tables (auto-generated)

## ML quality

| strategy | symbols | mean acc % | baseline % | edge pp | ±95% CI pp | symbols w/ +edge | OOS preds |
|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | 20 | 52.1 | 53.63 | -1.53 | 7.05 | 8 | 3825 |
| S2_ml_dir1_small10 | 20 | 50.2 | 53.77 | -3.57 | 6.99 | 5 | 3920 |
| S3_ml_ret5atr_small10 | 20 | 56.1 | 68.5 | -12.4 | 6.9 | 1 | 3910 |

## Net-of-cost performance (same OOS window)

| strategy | symbols | mean net % | excess vs B&H pp | % beating B&H | costs ₹ | trades | Sharpe | maxDD % | exposure % |
|---|---|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | 20 | -12.32 | 0.21 | 55.0 | 9990.0 | 28.6 | -1.3 | -18.7 | 43.6 |
| S2_ml_dir1_small10 | 20 | -14.48 | -1.94 | 40.0 | 9532.0 | 27.6 | -1.53 | -20.4 | 42.5 |
| S3_ml_ret5atr_small10 | 20 | -10.11 | 2.43 | 60.0 | 5812.0 | 16.6 | -1.55 | -16.39 | 27.0 |
| S4_rule_momentum | 20 | -7.06 | 5.48 | 50.0 | 1317.0 | 3.9 | -1.3 | -14.93 | 41.4 |
| S5_rule_rsi_meanrev | 20 | 1.13 | 13.67 | 85.0 | 704.0 | 2.1 | -0.3 | -11.51 | 29.7 |
| S6_buy_hold | 20 | -12.54 | 0.0 | 0.0 | 193.0 | 1.0 | -0.83 | -25.41 | 99.0 |
| S7_ml_s3_minhold5 | 20 | -10.22 | 2.31 | 50.0 | 4098.0 | 11.8 | -1.3 | -17.73 | 39.8 |
| S8_ml_s1_minhold5 | 20 | -9.88 | 2.66 | 60.0 | 6116.0 | 17.4 | -0.92 | -20.28 | 62.1 |

## Per symbol — ML (S3, proposed design)

| symbol | acc % | base % | edge pp | null ceiling % | net % | B&H % | verdict |
|---|---|---|---|---|---|---|---|
| RELIANCE | 58.97 | 72.31 | -13.33 | 69.23 | -1.66 | -22.97 | NO EDGE — accuracy 59.0% shuffled-label ceiling 69.2% se neeche/barabar hai |
| TCS | 64.62 | 77.44 | -12.82 | 73.33 | -12.74 | -36.97 | NO EDGE — accuracy 64.6% shuffled-label ceiling 73.3% se neeche/barabar hai |
| HDFCBANK | 46.67 | 74.87 | -28.21 | 72.31 | -20.91 | -26.72 | NO EDGE — accuracy 46.7% shuffled-label ceiling 72.3% se neeche/barabar hai |
| INFY | 67.69 | 72.82 | -5.13 | 63.59 | -6.51 | -38.21 | NO EDGE (-5.1pp vs baseline) |
| ICICIBANK | 57.44 | 67.18 | -9.74 | 61.54 | -5.41 | -2.02 | NO EDGE — accuracy 57.4% shuffled-label ceiling 61.5% se neeche/barabar hai |
| SBIN | 66.15 | 59.49 | 6.67 | 53.85 | 6.84 | -0.8 | POSSIBLE EDGE (+6.7pp) — verify on more data before trusting |
| BHARTIARTL | 55.38 | 73.33 | -17.95 | 63.59 | -12.84 | -15.71 | NO EDGE — accuracy 55.4% shuffled-label ceiling 63.6% se neeche/barabar hai |
| ITC | 57.44 | 70.77 | -13.33 | 63.08 | -18.15 | -33.67 | NO EDGE — accuracy 57.4% shuffled-label ceiling 63.1% se neeche/barabar hai |
| KOTAKBANK | 50.26 | 68.21 | -17.95 | 56.41 | -23.06 | -3.97 | NO EDGE — accuracy 50.3% shuffled-label ceiling 56.4% se neeche/barabar hai |
| LT | 53.68 | 66.32 | -12.63 | 66.32 | -2.9 | -6.5 | NO EDGE — accuracy 53.7% shuffled-label ceiling 66.3% se neeche/barabar hai |
| WIPRO | 60.51 | 74.36 | -13.85 | 68.21 | -24.28 | -39.39 | NO EDGE — accuracy 60.5% shuffled-label ceiling 68.2% se neeche/barabar hai |
| AXISBANK | 58.46 | 64.62 | -6.15 | 56.41 | -12.49 | 0.05 | NO EDGE (-6.2pp vs baseline) |
| MARUTI | 53.85 | 72.82 | -18.97 | 68.72 | -19.28 | -26.9 | NO EDGE — accuracy 53.8% shuffled-label ceiling 68.7% se neeche/barabar hai |
| TATAMOTORS | 57.62 | 71.43 | -13.81 | 70.0 | -17.4 | -27.85 | NO EDGE — accuracy 57.6% shuffled-label ceiling 70.0% se neeche/barabar hai |
| BAJFINANCE | 53.85 | 68.21 | -14.36 | 64.62 | 21.94 | -3.65 | NO EDGE — accuracy 53.8% shuffled-label ceiling 64.6% se neeche/barabar hai |
| SUNPHARMA | 49.74 | 63.59 | -13.85 | 58.97 | -15.82 | 4.9 | NO EDGE — accuracy 49.7% shuffled-label ceiling 59.0% se neeche/barabar hai |
| TITAN | 41.54 | 57.95 | -16.41 | 53.85 | -19.57 | 18.68 | NO EDGE — accuracy 41.5% shuffled-label ceiling 53.9% se neeche/barabar hai |
| NTPC | 54.36 | 66.15 | -11.79 | 64.1 | -12.23 | 1.09 | NO EDGE — accuracy 54.4% shuffled-label ceiling 64.1% se neeche/barabar hai |
| ONGC | 51.28 | 64.1 | -12.82 | 61.03 | -11.99 | -2.03 | NO EDGE — accuracy 51.3% shuffled-label ceiling 61.0% se neeche/barabar hai |
| TATASTEEL | 62.56 | 64.1 | -1.54 | 62.56 | 6.35 | 11.88 | NO EDGE (-1.5pp vs baseline) |
