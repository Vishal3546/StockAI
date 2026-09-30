# Study tables (auto-generated)

## ML quality

| strategy | symbols | mean acc % | baseline % | edge pp | ±95% CI pp | symbols w/ +edge | OOS preds |
|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | 20 | 50.7 | 51.44 | -0.74 | 3.38 | 8 | 17885 |
| S2_ml_dir1_small10 | 20 | 50.95 | 51.44 | -0.48 | 3.37 | 9 | 17980 |
| S3_ml_ret5atr_small10 | 20 | 54.88 | 62.32 | -7.43 | 3.36 | 0 | 17970 |

## Net-of-cost performance (same OOS window)

| strategy | symbols | mean net % | excess vs B&H pp | % beating B&H | costs ₹ | trades | Sharpe | maxDD % | exposure % |
|---|---|---|---|---|---|---|---|---|---|
| S1_ml_dir1_app28 | 20 | -25.57 | -58.04 | 5.0 | 46755.0 | 137.1 | -1.09 | -36.05 | 38.6 |
| S2_ml_dir1_small10 | 20 | -25.53 | -58.0 | 5.0 | 42202.0 | 124.8 | -1.08 | -37.52 | 42.2 |
| S3_ml_ret5atr_small10 | 20 | -9.1 | -41.57 | 20.0 | 28701.0 | 77.7 | -0.89 | -25.41 | 28.5 |
| S4_rule_momentum | 20 | 18.38 | -14.09 | 45.0 | 5898.0 | 15.2 | -0.22 | -25.98 | 62.0 |
| S5_rule_rsi_meanrev | 20 | 9.95 | -22.52 | 35.0 | 2190.0 | 5.8 | -0.43 | -14.81 | 18.1 |
| S6_buy_hold | 20 | 32.47 | 0.0 | 0.0 | 192.0 | 1.0 | -0.03 | -31.81 | 99.8 |
| S7_ml_s3_minhold5 | 20 | -2.88 | -35.35 | 20.0 | 20881.0 | 53.8 | -0.63 | -27.93 | 41.0 |
| S8_ml_s1_minhold5 | 20 | -6.88 | -39.35 | 5.0 | 31169.0 | 82.8 | -0.59 | -33.25 | 58.7 |

## Per symbol — ML (S3, proposed design)

| symbol | acc % | base % | edge pp | null ceiling % | net % | B&H % | verdict |
|---|---|---|---|---|---|---|---|
| RELIANCE | 55.08 | 61.6 | -6.52 | 57.01 | -13.06 | -0.83 | NO EDGE — accuracy 55.1% shuffled-label ceiling 57.0% se neeche/barabar hai |
| TCS | 58.29 | 67.17 | -8.88 | 62.25 | -26.38 | -35.45 | NO EDGE — accuracy 58.3% shuffled-label ceiling 62.2% se neeche/barabar hai |
| HDFCBANK | 53.8 | 64.92 | -11.12 | 56.15 | -28.34 | -12.73 | NO EDGE — accuracy 53.8% shuffled-label ceiling 56.1% se neeche/barabar hai |
| INFY | 56.9 | 61.39 | -4.49 | 58.5 | -8.95 | -33.32 | NO EDGE — accuracy 56.9% shuffled-label ceiling 58.5% se neeche/barabar hai |
| ICICIBANK | 53.48 | 64.17 | -10.7 | 59.04 | -27.23 | 46.9 | NO EDGE — accuracy 53.5% shuffled-label ceiling 59.0% se neeche/barabar hai |
| SBIN | 52.3 | 58.61 | -6.31 | 58.82 | -10.42 | 60.37 | NO EDGE — accuracy 52.3% shuffled-label ceiling 58.8% se neeche/barabar hai |
| BHARTIARTL | 50.8 | 59.57 | -8.77 | 54.55 | -8.09 | 110.09 | NO EDGE — accuracy 50.8% shuffled-label ceiling 54.5% se neeche/barabar hai |
| ITC | 51.98 | 65.13 | -13.16 | 56.15 | -20.5 | -19.19 | NO EDGE — accuracy 52.0% shuffled-label ceiling 56.1% se neeche/barabar hai |
| KOTAKBANK | 57.75 | 63.96 | -6.2 | 61.5 | -10.85 | 11.95 | NO EDGE — accuracy 57.8% shuffled-label ceiling 61.5% se neeche/barabar hai |
| LT | 54.09 | 59.14 | -5.05 | 52.69 | 10.38 | 72.44 | NO EDGE (-5.1pp vs baseline) |
| WIPRO | 59.57 | 62.46 | -2.89 | 60.32 | 1.56 | -17.76 | NO EDGE — accuracy 59.6% shuffled-label ceiling 60.3% se neeche/barabar hai |
| AXISBANK | 54.87 | 63.1 | -8.24 | 58.29 | -12.28 | 30.3 | NO EDGE — accuracy 54.9% shuffled-label ceiling 58.3% se neeche/barabar hai |
| MARUTI | 57.33 | 63.21 | -5.88 | 60.75 | -13.28 | 38.83 | NO EDGE — accuracy 57.3% shuffled-label ceiling 60.8% se neeche/barabar hai |
| TATAMOTORS | 57.62 | 71.43 | -13.81 | 70.0 | -17.21 | -27.68 | NO EDGE — accuracy 57.6% shuffled-label ceiling 70.0% se neeche/barabar hai |
| BAJFINANCE | 57.11 | 61.28 | -4.17 | 56.9 | -4.28 | 44.51 | NO EDGE (-4.2pp vs baseline) |
| SUNPHARMA | 53.05 | 60.43 | -7.38 | 54.76 | -2.53 | 85.72 | NO EDGE — accuracy 53.0% shuffled-label ceiling 54.8% se neeche/barabar hai |
| TITAN | 49.3 | 56.9 | -7.59 | 52.3 | -26.7 | 84.25 | NO EDGE — accuracy 49.3% shuffled-label ceiling 52.3% se neeche/barabar hai |
| NTPC | 53.48 | 62.14 | -8.66 | 56.68 | 11.63 | 87.5 | NO EDGE — accuracy 53.5% shuffled-label ceiling 56.7% se neeche/barabar hai |
| ONGC | 55.61 | 60.86 | -5.24 | 57.01 | 25.84 | 55.5 | NO EDGE — accuracy 55.6% shuffled-label ceiling 57.0% se neeche/barabar hai |
| TATASTEEL | 55.29 | 58.93 | -3.64 | 55.94 | -1.3 | 67.97 | NO EDGE — accuracy 55.3% shuffled-label ceiling 55.9% se neeche/barabar hai |
