# FIX104 — expanded calibration, official closes and fresh measured research

Date: 10 October 2026. Base: public FIX103 `57c2b892eddc78d3ba98e4c52c3570517f1c5551`.

This release performs the work that was still pending after FIX103. It does not merely change warning wording or declare an untested model profitable.

## 1. Calibration coverage: implemented and rebuilt

The Dashboard's historical-rank universe is now an explicit, validated `calibration_universe.json` manifest rather than an immutable 30-name tuple. The delivered cohort contains the existing 30 names **plus JIOFIN, EMIL and CANBK**. Invalid/duplicate names are rejected. Changing membership invalidates the old artifact and requires rebuilding, rather than silently applying old percentiles to a new cohort.

Both exchange artifacts were rebuilt from requested-exchange **TradingView daily history**, matching the normal runtime historical provider convention:

| Artifact | Symbols actually covered | Scored sessions | Observations | As of | p10 / p40 / p80 / p95 |
|---|---:|---:|---:|---|---|
| NSE | 33 | 250 | 8,250 | 2026-10-08 | 43 / 50 / 57 / 62 |
| BSE | 33 | 250 | 8,250 | 2026-10-08 | 42 / 49 / 57 / 62 |

Every newly covered symbol has 250 real historical score observations on each exchange. Production additionally checks per-symbol historical coverage, not just whether a symbol appeared once in the pooled history. The 9 October reference bar is excluded from fitting. The 250-bar current-input requirement, freshness checks and execution restrictions remain intact.

### Special-session defect fixed

The builder previously discarded all weekend observations. This was incorrect for actual special trading sessions. Official NSE/BSE UDiFF bhavcopies were downloaded and parsed for **1 February 2025 and 1 February 2026**; their URLs, SHA256 values and row counts are recorded in `calibration_special_sessions.json`.

Those verified sessions are retained in historical input windows, and 1 February 2026 is included in the new 250-session artifacts. Unverified weekend dates remain excluded/rejected. Artifact validation now checks the session-policy version so a prior weekday-only artifact is not silently accepted as the new policy.

This is the verified historical-session set relevant to these refits, not a complete future market-phase/holiday calendar. It does not authorize trading during all weekends or redefine CAS/post-close sessions.

### Scanner is deliberately separate

The scanner has a different score formula and its own existing 30-symbol bands artifact. It remains on `BASE_UNIVERSE`; expanding the Dashboard cohort must not silently invalidate or mislabel scanner bands. This separation is tested. FIX104 does not claim all 7,344 searchable securities are calibrated.

LGEINDIA still has only 245 observed daily bars and is not in this delivered cohort. No missing pre-listing history, candles or fitted rank is fabricated.

## 2. Official daily-price reconciliation: actually performed

Primary downloads for **9 October 2026**, not a secondary quote website:

- NSE: https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261009_F_0000.csv.zip
- BSE: https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_20261009_F_0000.CSV

The parser checks the UDiFF schema, exchange and session; selects the cash-security row; records instrument ID and ISIN; and compares the TradingView daily Close with **`ClsPric`**, not `LastPric`. Ambiguous rows, wrong identities, missing sessions and nonfinite values cannot receive MATCH. Cross-exchange ISIN disagreement also prevents MATCH.

| Symbol | NSE official close / TradingView close | BSE official close / TradingView close |
|---|---:|---:|
| TCS | 2156.00 / 2156.00 | 2163.00 / 2163.00 |
| JIOFIN | 214.62 / 214.62 | 214.80 / 214.80 |
| EMIL | 191.17 / 191.17 | 191.45 / 191.45 |
| CANBK | 119.59 / 119.59 | 119.50 / 119.50 |
| LGEINDIA | 1757.00 / 1757.00 | 1755.90 / 1755.90 |

**Result: 10/10 sampled closes matched.** EMIL is a useful example of why Close and Last must not be confused: its NSE `ClsPric` was 191.17 while `LastPric` was 190.50.

Machine-readable results and download hashes: `verification_evidence/official_close_reconciliation.json` and `official_downloads.json`. Raw downloads are included in the separate audit bundle rather than the source patch.

A reusable command is delivered:

```powershell
python tools\reconcile_official_close.py --date 2026-10-09
```

This verifies that date's specified raw daily closing observations. It does **not** certify every historical OHLCV field, corporate-action adjustment, intraday candle, quote timestamp or licensed real-time feed. The tool does not overwrite a discrepant vendor price to hide a mismatch.

### Current 2026 exchange context

The NSE's March SOP distinguishes CAS closing-price determination from LTP/reference-price rules: https://nsearchives.nseindia.com/content/circulars/CMTR73362.pdf . The July circular confirms the live effective date of 3 August 2026: https://nsearchives.nseindia.com/content/circulars/CMTR75479.pdf . Its mock-session schedule must not be mistaken for a universal live schedule. These are reasons to reconcile the actual official Close rather than infer it from the final five-minute bar or assume one session schedule for every security.

## 3. Fresh predictive studies: completed for NSE and BSE

Executed from the current research code, separately per exchange, with the selected cohort **RELIANCE, TCS, HDFCBANK, JIOFIN, EMIL, CANBK**. These are six selected case-study securities, not the entire calibration cohort or an unbiased historical market universe.

Method: five expanding purged/embargoed folds, train-only majority baseline evaluated on OOS observations, and three shuffled-training-label repetitions for the S3 diagnostic. Approximate binomial intervals are not dependence-adjusted significance proofs. The requested period is five years, but IPO history is shorter: for example NSE JIOFIN begins 2023-08-21 and EMIL begins 2022-10-17. Per-symbol real start/end dates, bar counts, data hashes and library versions are recorded.

| Exchange | Candidate | OOS accuracy | Baseline | Difference |
|---|---|---:|---:|---:|
| NSE | S1: 28-feature next-day direction | 50.30% | 51.75% | -1.45 pp |
| NSE | S2: 10-feature next-day direction | 49.79% | 51.75% | -1.96 pp |
| NSE | S3: five-day ATR-scaled label | 54.72% | 64.62% | -9.90 pp |
| BSE | S1 | 49.47% | 51.37% | -1.91 pp |
| BSE | S2 | 49.20% | 51.37% | -2.17 pp |
| BSE | S3 | 55.17% | 64.50% | -9.33 pp |

NSE recorded 14,649 OOS predictions across the three candidates; BSE recorded 14,610. These counts are not independent observations across candidates. S3's shuffled-label ceilings were approximately 61.6% and 64.9%, respectively. Both study verdicts are **NO EDGE**.

Artifacts: `ml_edge_study.json` and `ml_edge_study_bse.json`. The UI now selects the requested exchange's study and discloses cohort/model scope. A content fingerprint replaces trusting the release string alone; changed research code or wrong-exchange artifacts are marked archived. Fingerprints normalize CRLF/LF and have a regression test so Windows checkout line endings do not falsely invalidate them.

**Important distinction:** this is a fresh run of the GradientBoosting research candidates, not a new independent validation of the Dashboard's deployed four-model ensemble, not a fully point-in-time/survivorship-free study, and not an executable strategy certification. The live ensemble's diagnostic accuracy is still displayed as diagnostic. We did not relabel the old October 1 artifact as newly tested.

## 4. Net-of-cost study: completed, not deferred

Ran the current `research/run_study.py` on the same six selected NSE securities. It uses next-Open execution after the signal, fixed units, terminal close liquidation, configured costs/slippage, and a common OOS window for strategy comparisons within each symbol.

| Strategy | Mean cumulative net return | Mean excess vs buy-and-hold |
|---|---:|---:|
| S1 next-day ML candidate | -21.50% | -21.75 pp |
| S2 small-feature ML | -26.61% | -26.85 pp |
| S3 five-day-label ML | -17.56% | -17.81 pp |
| S4 SMA200 momentum research rule | +6.25% | +6.01 pp |
| S5 RSI mean-reversion rule | +0.39% | +0.15 pp |
| S6 buy-and-hold reference | +0.24% | 0 |
| S7 S3 with minimum hold | -24.16% | -24.40 pp |
| S8 S1 with minimum hold | -19.25% | -19.50 pp |

These are arithmetic means of cumulative per-security study returns, **not annualized portfolio returns or a forecast**. They span differing listing/OOS ranges. The momentum rule's positive sample mean is not sufficient evidence to deploy it: its mean Sharpe was negative, the cohort is selected, multiple candidates were compared, provider history is not a point-in-time corporate-action database, and there is no untouched external/live execution holdout.

Full current results: `reports/study_results.json`, `reports/study_results_5y.json`, `reports/study_table.md`. The top-level `RESEARCH_REPORT.md` now points to this fresh work rather than leaving the old report as the current authority.

The measured ML results do not support enabling executable orders. **A negative result is verification, not a missing verification step.** Changing thresholds until this sample looks profitable would be overfitting, not a correctness fix.

The cost study is an NSE-default model, not a verified broker bill or a BSE cost study. Exact user costs still require broker/plan/instrument inputs. We do not turn an estimated fee into an asserted official charge.

## 5. Is NO DIRECTIONAL PLAN correct?

Yes for a fitted non-directional rank band, and yes when calibration/history is unavailable. A percentile fit supplies relative-rank bands; it does not guarantee a LONG/SHORT setup.

Live acceptance on this release exercised TCS BSE; JIOFIN, EMIL and CANBK on both exchanges; and LGEINDIA BSE. The newly added symbols were fitted. Observed cases included:

- TCS BSE: FIT, AVOID, direction NONE, no plan.
- EMIL NSE/BSE: FIT, AVOID, direction NONE, no plan.
- JIOFIN NSE: FIT, BUY_DIP, an **illustrative LONG plan**, executable quantity zero.
- JIOFIN BSE: FIT, WATCHLIST, no directional plan.
- CANBK NSE/BSE: FIT, non-directional bands, no plan.
- LGEINDIA BSE: still unfitted/245 bars, no plan.

This proves plans are not simply disabled for every symbol. They are conditional and exchange-specific. The no-plan message now names the fitted rank band instead of incorrectly suggesting that every no-plan state means a missing fit or a neutral technical reading. These observations are test results, not buy/sell recommendations.

## 6. Verification and platform status

New deterministic suite: **16 tests** for membership/refit boundaries, full per-symbol coverage, special-session handling, retained short-history safeguards, separate scanner scope, official Close vs Last parsing, wrong-session/exchange rejection, current research fingerprints, Windows line-ending portability and cost-study provenance.

The full runner contains **44 verifier scripts**. Final clean-apply results, code-tree equality, SHA256 and live-route outcomes are supplied in `StockAI_fix104_apply_verification.json` with the release bundle. Existing tests were corrected where they wrongly required both exchange fits to have different sample counts, rejected every weekend, assumed an OOS train-majority baseline cannot fall below 50%, or still treated JIOFIN as unsupported.

Your supplied logs now establish native Windows FIX103 verification: 8 passed, plus FIX102 18 passed/one Unix-only timezone-simulation skip. FIX104 includes a Windows/Linux Python 3.14 workflow and the new portable hash test. The workflow is configured to run after your push; its native Windows result is **not yet available from this sandbox**. No remote push or successful CI run is claimed.

## 7. Apply on Windows

The patch contains the rebuilt artifacts; you do not need to run the long builders just to install it. Stop the server with Ctrl+C, save local changes, and put `StockAI_fix104_coverage_validation.git-am.patch` in Downloads. Expected base is your applied FIX103 `57c2b89`. Run commands individually; stop on errors:

```powershell
git status --short
git am --3way "$env:USERPROFILE\Downloads\StockAI_fix104_coverage_validation.git-am.patch"
python tools\verify_fix104.py
python tools\verify_fix103.py
python tools\verify_fix102.py
```

Then, after successful tests:

```powershell
git push origin main
python app.py
```

Hard-refresh with Ctrl+F5. The CI workflow is included for repeatable native platform checks. Do not reapply FIX102 or FIX103. On an application conflict, stop; `git am --abort` cancels the patch attempt without force-overwriting local edits.

### Subsequent maintenance, not necessary for installation

Calibration artifacts retain their freshness gate; they are not valid forever. To refresh the current explicit cohort:

```powershell
python tools\build_score_calibration.py --exchange NSE
python tools\build_score_calibration.py --exchange BSE
```

To add further symbols, deliberately edit `calibration_universe.json` and rebuild both exchanges with adequate real history. A search result is not proof of calibration eligibility. Do not add unavailable/short-history symbols merely to suppress warnings.

Re-running predictive research is separate from recalibrating score percentiles. Neither action establishes profit by itself.
