# FIX108 — audit, corrections and one-page scanner/deep integration

**11 October 2026 · based on the user's applied FIX107.** No new trading product, broker automation or unrelated feature expansion. Existing Screener upgraded; existing two scripts repaired and integrated.

## What to open

**Dashboard → Screener** (`/screener`). The title is now **Scanner & Deep Analysis**.

- Left: existing filters and corrected completed-daily scanner, with per-row data dates.
- Click a symbol: right-side deep analysis; below the scanner on smaller screens.
- Deep model work runs only for the selected stock, not for all 30 scanner rows.
- The new panel shows matched ensemble walk-forward diagnostics, neutral-policy coverage, asset risk statistics, aligned NIFTY comparison, provider fundamentals and audit fields.
- New rows hide the obsolete empty composite/ML/automatic target columns. Old scan files remain readable, explicitly as legacy diagnostics. Refresh once to replace old rows.
- This page is **daily research/context**. It is not the separate manual intraday planner and must not be read as a real-time intraday signal.

## Audit findings and implemented corrections

| Finding in the submitted scripts | Correction |
|---|---|
| Scanner claimed a 3-tier zero-delay feed but had its own inconsistent fetch path | Shared app DataManager and explicit completed-daily/source metadata; removed zero-delay/crash-proof claims |
| NSE request could silently fall back to `.BO` | Strict requested-exchange selection; unavailable rather than wrong-exchange data |
| Partial latest daily candle could affect every indicator while only RVOL was patched | Completed-session cutoff applied before scanner/deep calculations |
| OHLCV validation mostly dropped only missing Close | Shared finite-price/volume, geometry, ordering and duplicate-session boundary |
| Zero volume was replaced by previous volume, with fallback `10000` | No volume invention; RVOL uses actual volume divided by the prior 20 sessions; undefined denominator stays missing |
| Daily VWAP was anchored to arbitrary history start | Scanner now uses a labeled 20-completed-daily-bar typical-price/volume proxy, not session VWAP |
| Missing SMA200 automatically produced bearish scoring | Scanner requires 200 actual completed bars; no invented bearish result |
| Cached bands could remain accepted after expiry; NaN/future timestamp validation weak | Age checked every load, finite/ranged/ordered thresholds, future timestamps rejected |
| Old percentile artifact no longer matched corrected indicators | Schema/model bumped; artifact refitted on 7,500 observations from 30 symbols, calibration ending 10 July 2026 |
| Signal labels are relative percentile ranks, but long stops/targets were attached even to SELL rows | No automatic executable plan from rank; legacy target fields explicitly null |
| Scanner ML failure became fake `50/0/50/0` | Batch scanner no longer trains a separate classifier; ML fields remain null until selected-stock analysis |
| Deep walk-forward used a small GB while displayed prediction used a different four-model ensemble | Reuses the same configured Dashboard ensemble and FIX106 matched-fold validation |
| Walk-forward accuracy was presented beside an unrelated final-split baseline | Panel pairs WF accuracy with the baseline from the very same WF observations; final-split diagnostics remain separately scoped |
| Latest model score was computed by models trained on only the first 80% | Shared production engine now retains holdout metrics, then refits on **all known labels** for the current score; latest unknown label is never trained |
| “Ensemble accuracy” averaged individual model accuracies | Now measures the actual weighted ensemble's held-out predictions |
| Train-day count included the purged row | Reports actual holdout training rows and separate full-label live-refit row count |
| Deep feature/model configuration duplicated and drifted from the website | Single shared ML implementation; standalone script and web endpoint both use it |
| Sortino used standard deviation of only negative returns | Uses downside semideviation over all observations relative to the declared daily minimum return |
| Epsilon division produced huge meaningless risk ratios | Undefined zero-denominator ratios return null |
| Arithmetic annual return was used as CAGR/Calmar without distinction | Arithmetic annualization and calendar-time CAGR are separate; Calmar uses CAGR |
| “Win rate” and “profit factor” described underlying price returns as though they were trade results | Explicit asset-return labels; no strategy-performance or EXCELLENT investment rating |
| 6.5% described as current India 10Y yield | Explicit configurable **assumption**, not a live yield quote; 247 annual sessions also disclosed as an assumption |
| Benchmark legs could have different endpoints/return intervals | Join prices first, then compute both interval returns on shared endpoints; disclose gaps and shared sample |
| Missing benchmark invented zero returns and beta=1 | Missing values stay null, without a fake market regime |
| “Sector strength” was actually stock vs NIFTY | Correctly labeled benchmark comparison, not a sector index |
| Zero-valued fundamentals displayed as missing | Preserved zero; provider fundamentals remain independently unverified |
| Script paths depended on working directory; direct JSON writes could expose partial files | Repository-relative paths and atomic strict-JSON replacement |
| Parallel results could have nondeterministic tie ordering and ambiguous progress | Symbol tie-breaker, locked progress index, attempted/failed counts; all-failed scan retains prior file and exits with error |
| Web integration could show stale selected-stock results or unsafe strings | Response-generation guards, timeout wording, text-safe rendering, requested-exchange validation, token protection and one active deep-analysis request per process |

### Distinctions deliberately retained

Scanner score is a **five-component technical relative rank**, not the ML probability or Dashboard's broader ensemble rank. They must not be blended or described as the same measure. Shared indicator formulas where applicable and shared data boundaries do not make different models interchangeable.

The deep page typically uses 500 daily bars. Dashboard analysis can use another window. Identical shared code does not imply identical predictions for different input histories. The panel displays input-window details and fingerprints; same-input parity was tested separately.

Per-model holdout metrics are preserved **before** refitting. Current probabilities and full-fit feature importances come from the later refit. Refitting improves training recency; it is not evidence that forecasting accuracy increased. Model probabilities remain uncalibrated; “confidence” in the audit payload describes model agreement, not statistical confidence or trade win probability.

## Chronological evaluation — actual results, including the negative result

Corrected percentile bands were fitted using data **through 10 July 2026**. Evaluation signals begin **13 July 2026**; outcomes end **9 October 2026**. No later evaluation observations were included in that band fit. Thresholds were not tuned to make the reported evaluation favorable.

| Measurement | Result |
|---|---:|
| Fitting observations / symbols | 7,500 / 30 |
| Later evaluated stock-sessions | 1,860 |
| Selected BUY/SELL-band observations | 1,203 |
| Selected coverage | 64.68% |
| Selected next-close direction accuracy | **53.12%** |
| Training-majority baseline on exactly those selected observations | **48.21%** |
| Descriptive difference | **+4.90 percentage points** |

These are **scanner-band directional diagnostics**, not the ML ensemble accuracy and not trading returns. Same-date stocks and adjacent sessions are dependent. No IID significance claim or confidence interval is inferred from the pooled count.

### Separate costed long-only scenario

Signal formed at the previous completed close; hypothetical entry at next Open, exit at that session's Close. Fixed ₹100,000 notional per independent round trip, integer shares, 5 bps adverse slippage on each side, NSE Angel One standard intraday fees plus modeled reserves. The current fee profile is applied throughout this hypothetical sample; historical account-specific tariffs are not reconciled. This is not the deployed full holding/scale-out/stop strategy or a funded portfolio simulation.

| Measurement | Result |
|---|---:|
| Long-band round trips | 186 |
| Average net P&L per selected hypothetical round trip | **−₹210.56** |
| Positive net round trips | **40.86%** |
| Same scenario on every evaluated stock-session, average net | −₹288.82 |

**Conclusion: the measured directional gap did NOT establish profitability.** This release does not certify the scanner as a profitable trading strategy, increase execution-authorized quantity, or turn relative BUY/SELL ranks into broker orders.

Important limitations: current curated cohort rather than historical index constituents (survivorship); provider corporate actions/history not fully reconciled; no bid/ask/depth/circuit/eligibility model; historical data already used in earlier project research. Therefore this is **exploratory chronological out-of-sample evaluation**, not a demonstrably untouched independent holdout or forward-paper result. There is no honest basis for claiming “all accuracy is now high.”

Reproduction command, after reading those limits:

```powershell
python tools/validate_scanner108.py
```

Full ledger and input hashes are included in the evidence package. Re-fetches can differ because providers revise historical data. The fitter supports `--fit-end`; by default it reserves the latest 92 calendar days rather than permanently hardcoding a 2026 cutoff. This release's artifact was generated with explicit `--fit-end 2026-07-10`.

## Actual-provider and primary-reference checks

Both new deep API routes were exercised with real provider data, not only mocks:

| Instrument | HTTP | Daily bars | WF accuracy | Matched WF baseline | Gap | WF observations | Live refit labels |
|---|---:|---:|---:|---:|---:|---:|---:|
| INFY / NSE | 200 | 500 | 52.8% | 54.7% | −1.9 pp | 320 | 450 |
| EMIL / BSE | 200 | 500 | 54.4% | 54.4% | 0.0 pp | 320 | 450 |

Both exactly matched the shared Dashboard core when given the **same input frame**. Two frozen CSV replays independently refitted the models in this environment and exactly reproduced model probabilities, per-model diagnostics, fold results, live training counts and asset risk metrics; both input hashes also matched. Complete 20-observation WF folds are reported; this count does not claim every trailing labeled observation was evaluated.

Latest session **9 October 2026:** all five OHLCV fields for both instruments matched the previously downloaded official NSE/BSE bhavcopies (**10/10 fields**). The official reference was rebuilt from saved primary files in this turn, not described as a fresh network download. Source paths/hashes remain in the reference/evidence.

This checks only the latest completed daily OHLCV. It does not verify every historical bar, corporate action, fundamental, or intraday quote. The web panel shows MATCH / MISMATCH / UNVERIFIED rather than assuming a missing reference is correct.

## Regression and verification scope

- Development regression: **50/50 verifier scripts passed** after the main integration and ML refit changes.
- Final targeted Python suite: **25/25 tests passed**, including real app routes, auth, busy handling, zero volume, prior-window RVOL, undefined ratios, semideviation arithmetic, benchmark endpoints, null states, metadata propagation, shared model routing, refit/evaluation separation and zero fundamentals.
- New jsdom suite: same-page selection, text-safe rendering, superseded response rejection, null benchmark, zero fundamentals, error states.
- Existing Screener jsdom suite: **45/45 checks passed**.
- Band verifier: **67/67 checks passed** after updating artifact-version expectations.
- Some old verifiers asserted removed duplicate source code, old title text or exact old thresholds. They were updated for the new shared path/schema and retained the substantive checks. Initial failures and their causes were investigated, not omitted from the final suite.
- Late primary-reference display, CLI wording and UI window/legacy-column polish were targeted-tested; the exported patch's final full clean-check results are appended to the separately delivered release report after packaging verification.

Python/package environment is pinned project requirements on Linux Python 3.13. Native Windows execution of FIX108 remains for the user's check. Passing verifiers is not a guarantee of no undiscovered bugs or profitable trading.

## Apply on FIX107 — PowerShell

Download `StockAI_fix108_scanner_deep_integration.git-am.patch` into Downloads. Inside your existing StockAI repository, run **one command at a time; stop on error**.

```powershell
git status --short
```

Proceed only if the working tree is clean.

```powershell
git am --3way "$env:USERPROFILE\Downloads\StockAI_fix108_scanner_deep_integration.git-am.patch"
```

```powershell
python tools/run_verifiers.py
```

For a current local primary daily reference (network availability permitting):

```powershell
python tools/refresh_official_eod.py
```

After successful tests:

```powershell
git push origin main
```

Restart the normal StockAI process. Open **Dashboard → Screener**, press **Refresh**, wait for completion and click a symbol. A selected-stock analysis can take tens of seconds; changing selection does not cancel server computation. If busy, the page asks you to wait and retry rather than showing another stock's result.

The corrected band artifact is included; there is no need to refit it just to apply this release. Rebuild only deliberately, with an explicit evaluation separation. No new dependencies or broker credentials are required. FIX107 manual planning remains separate and unchanged.
