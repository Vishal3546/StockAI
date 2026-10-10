# FIX106 — matched ensemble validation, scale-out research engine and data verification

**Date:** 10 October 2026, IST. **Base:** public main `09b099bfb2ddec211df70570b9772c52491c7c24` (user-applied FIX105).

## Executive decision

Fix the demonstrated defects rather than hide warnings or enable orders without evidence. This release corrects the ML comparison, evaluates the configured ensemble, separates a barrier-touch diagnostic from strategy sizing, implements a complete signal-ledger scale-out simulator, adds per-field official EOD comparison, and corrects misleading display semantics.

**It is not a certificate that the strategy is profitable, every fundamental/history field is verified, or live trading is approved.** Executable quantity remains zero. The app now lists concrete missing requirements. A tested simulator is not an independently validated trading strategy.

## 1. ML: same observations, same ensemble

Previously the UI subtracted final 80/20-split baseline accuracy from a different GB-only walk-forward sample. That comparison is removed.

- Each fold chooses the majority class from that fold's training labels only, then evaluates it on exactly the model's test observations.
- Aggregate accuracy/baseline use summed correct counts divided by the same pooled test count, not unweighted means of unequal samples.
- Walk-forward now fits the configured GradientBoosting, RandomForest, LogisticRegression and optional XGBoost ensemble with the same parameter dictionaries and weights used by the final prediction.
- XGBoost absent means explicit three-model scope. A fit failure is not silently dropped as a difficult fold. Model availability disagreement between walk-forward and final prediction refuses the result.
- StandardScaler is fitted on each training window only. Existing conservative one-row label gap is preserved. A complete final 20-row fold is no longer accidentally excluded.
- Main accuracy is binary classification at 0.5. The app separately reports the actual rounded 45/55 neutral-zone policy's selection count, coverage, accuracy and baseline on those SAME selected observations. Neither metric simulates financial returns.
- The fixed `sqrt(.25/20)` value is gone. Dispersion is the observed sample SD of fold accuracies; it is explicitly **not a confidence interval** or dependence-adjusted significance test.
- Missing computations return null, not invented 50% probabilities or 0% accuracy.
- Per-fold dates, training-majority class, sample counts and correct/baseline counts are exposed for audit. Edge uses unrounded counts before rounding, so it can differ by 0.1pp from subtracting two displayed rounded percentages.

### Current-code actual-provider diagnostics

Five stock API requests, reference session 9 October 2026, passed assertions. All four models were present in this environment.

| Stock | Ensemble WF accuracy | Same-fold baseline | Gap | Observed fold SD | OOS observations |
|---|---:|---:|---:|---:|---:|
| INFY NSE | 49.2% | 60.0% | -10.8pp | 14.63pp | 120 |
| TORNTPHARM NSE | 52.4% | 48.9% | +3.4pp | 12.29pp | 380 |
| EMIL BSE | 50.0% | 49.2% | +0.8pp | 12.25pp | 120 |
| PRAKASH BSE | 50.5% | 55.8% | -5.3pp | 11.41pp | 380 |
| LGEINDIA BSE | 55.0% | 43.3% | +11.7pp | 8.66pp | 60 |

**Do not treat a positive gap as a verified edge.** LGEINDIA's sample has only 60 OOS observations and its own-history rank remains insufficient at 245 input bars. The three/five-stock selection itself is retrospective, not an untouched external holdout.

45/55 policy diagnostics:

| Stock | Selected OOS n | Coverage | Selected-direction accuracy | Matched selected baseline |
|---|---:|---:|---:|---:|
| INFY NSE | 104 | 86.7% | 47.1% | 57.7% |
| TORNTPHARM NSE | 297 | 78.2% | 52.9% | 48.1% |
| EMIL BSE | 99 | 82.5% | 49.5% | 52.5% |
| PRAKASH BSE | 276 | 72.6% | 51.1% | 54.3% |
| LGEINDIA BSE | 49 | 81.7% | 53.1% | 51.0% |

These are classification diagnostics, not a net strategy backtest, calibrated probability, or authorization to place trades.

## 2. Reproducibility

Every ML result now carries:
- Exact consumed OHLCV CSV SHA256, bar count and first/last timestamps.
- Model parameters, configuration SHA256 and research-pipeline content fingerprint.
- Python/platform and NumPy, pandas, scikit-learn, XGBoost versions.

Two independent offline reload/recomputations (INFY and TORNTPHARM, `float_precision='round_trip'`) reproduced the recorded input hashes, current probabilities, model dictionaries, fold counts/metrics and neutral-policy results exactly in this environment. This does not promise bit-identical output on a different Windows/library/BLAS setup. Compare those fields to diagnose discrepancies instead of blaming the feed without evidence.

The original FIX104 six-stock predictive/net-cost artifacts remain unchanged and **ARCHIVED**. They were not rerun or re-stamped as current validation. Current per-stock diagnostics above are a different, explicitly scoped computation.

## 3. Trading plan: remove the false sizing connection

The production stock route no longer calls/passes `measure_plan_hit_rate()` into Kelly. That legacy helper measures unconditional, overlapping T1-before-SL barriers, excludes unresolved setups and does not simulate the displayed 50/50 exits. It is retained only for old research/helper compatibility, not used for live sizing.

- Public API Kelly allocations, illustrative allocation and executable quantity remain zero without a validated strategy probability.
- No current strategy win-rate or lower confidence bound is invented from the old barrier rate.
- ATR entry/stop/targets remain explicitly illustrative geometry; R:R is labelled **reference, gross T1**. Actual entry fills change the ratio.
- Moving a stop to entry is labelled **gross entry-stop**, not fee-adjusted break-even.
- The intraday one-entry/one-exit fee illustration is explicitly inapplicable to the daily partial-exit plan. Incorrect net-target percentages are withheld.
- Daily cash SHORT scenarios do not imply legal/operational overnight borrowing availability. Broker/product, tick size, lot size, price bands, costs and permissions are unverified blockers.

### Full scale-out simulator implemented

`scaleout_validation.py`, with CLI `tools/validate_scaleout.py`, models:
1. A supplied dated signal/rule ledger; no today's-thresholds-applied-backward shortcut.
2. Signal at session close, entry at **next Open**, ATR frozen from the signal.
3. One sequential position at a time; overlapping entry candidates excluded and counted.
4. Initial ATR stop; **50% at 2.5 ATR, 50% at 4 ATR**, remaining stop moved to entry after T1.
5. Adverse stop-first ordering when daily bars cannot establish intrabar sequence; adverse stop gaps fill at Open, not a fictitious stop price.
6. Full-horizon candidates only. Incomplete future horizons are reported as excluded; no shortened late sample silently counted.
7. All fully observed timeouts exit at horizon Close and enter net outcomes, rather than disappear from the denominator.
8. Fees/slippage through an explicit callback on entry AND each partial exit.
9. Ordinary overnight cash shorts rejected. An explicitly named research borrowed-cash scenario requires a borrowing assumption; borrow accrues on outstanding fractions and calendar days, including weekends.
10. Net P&L, fills, exclusions and assumptions are auditable. Fractional quantities and fixed notional are research conventions, not exchange-ready order lots or compounded portfolio returns.

**What is still missing:** an independently sourced, genuinely past-only signal ledger for the deployed calibrated strategy, an untouched holdout, verified broker/product per-fill costs, and forward/paper execution evidence. The simulator cannot prove that a caller-supplied ledger was not overfit. Its output deliberately never self-certifies execution. No actual full-strategy profitability result is claimed in this release.

The CLI estimates NSE delivery costs. It refuses to label those as a BSE-specific or derivatives cost model. BSE/product-specific replay requires an explicit appropriate fee callback through the Python API.

Example interface (filenames are user-supplied inputs, not bundled fabricated trading evidence):

```text
python tools/validate_scaleout.py --csv actual_history.csv --signals recorded_signals.json --exchange NSE --instrument cash_delivery --output scaleout_results.json
```

## 4. Data: primary-source EOD checks, not blanket certification

`tools/refresh_official_eod.py` downloads final NSE/BSE UDiFF files, validates exchange/date/schema, excludes ambiguous ticker rows, and atomically refreshes a **local** reference. Failure does not overwrite the previous valid reference. `.stockai/` is gitignored; the bulk daily reference is not shipped to the public source repository.

A fresh network download succeeded for 9 October 2026 and exactly matched the earlier primary-source hashes. The local date-specific reference contains **7,767 rows**, not 7,767 calibrated/trade-approved securities.

`eod_validation.py` compares the requested exchange, symbol and actual completed session, with per-field results for **Open, High, Low, Close and Volume**. Wrong/missing reference dates, missing symbols, malformed files or download failures do not get a fabricated MATCH.

| Stock | Official close | Latest OHLCV result |
|---|---:|---|
| INFY NSE | ₹1,023.40 | 5/5 fields MATCH |
| TORNTPHARM NSE | ₹4,656.90 | 5/5 fields MATCH |
| EMIL BSE | ₹191.45 | 5/5 fields MATCH |
| PRAKASH BSE | ₹119.20 | 5/5 fields MATCH |
| LGEINDIA BSE | ₹1,755.90 | 5/5 fields MATCH |

**25/25 fields matched.** Full historical corporate-action adjustment, all sessions, all fundamentals and licensed real-time latency are not thereby verified. Fundamentals now carry provider identity/reporting-period metadata and an explicit provider-reported/unreconciled status. Nonfinite financial values become missing; genuine zero ROE/dividend values are not disguised as N/A.

Sources:
- https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261009_F_0000.csv.zip
- https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_20261009_F_0000.CSV

Refresh after each completed session when final exchange files are available. A reference-date mismatch stays UNVERIFIED, not silently 'current'. Reconciliation does not replace provider history with invented candles or cross-exchange prices.

## 5. Patterns, indicators and data cutoffs

- Each pattern occurrence has ending date, age in observed bars and latest/historical status. Different dated occurrences no longer collapse to an undated first occurrence. Shape-only/trend-context-unconfirmed status is explicit.
- Three Black Crows / Three White Soldiers are labelled reversal candidates rather than unconditional continuation.
- VWAP is explicitly **20-session daily VWAP proxy**, not intraday session VWAP.
- MACD label identifies comparison with the signal line.
- Ichimoku Tenkan-versus-Kijun now actually compares Tenkan to Kijun, not price to Kijun.
- EMA/SMA states are called alignments/above-below, not falsely labelled new crossover events.
- Industry HTML entities are decoded once at the backend, then safely escaped on render.
- Own-history-ready stocks receive a clear no-pooled-action explanation rather than the old repeated contradictory warning in the risk note/plan panel.
- Ranking and ML use completed sessions, including the pre-open/future-bar boundary—not merely an `is_market_open` check. Existing heuristic/current-frame indicators remain separately identified by the analysis session/partial flag.

## 6. Verification and performance

- New FIX106 deterministic suite: **32 tests passed**.
- Actual Dashboard jsdom suite: **19 cases passed**.
- Full in-place regression: **46/46 scripts passed** before the final browser-wait wording update; final clean-patch results are in the separately delivered apply-verification JSON.
- Actual provider stock API requests: **5/5 passed**.
- Latest-session official OHLCV comparisons: **25/25 MATCH**.
- Frozen-input offline ML replay: **2/2 exact matches in the tested environment**.
- Fresh official reference network download: succeeded; matched recorded source hashes.

New tests exercise same-fold baselines, pooled weighting, actual dispersion, missing observations, ensemble scope, neutral-zone coverage, input/config identity, next-Open fills, same-bar adversity, gaps, two partial exits/three fee charges, gross versus net entry-stop, full-horizon censoring, sequential positions, short eligibility/borrow weekends, malformed signals/fees, per-field mismatch, stale references, dated patterns, completed-session cutoffs, and the production Kelly disconnect.

Initial regressions exposed source-text assertions tied to the old GB-only call count, old scalar `None` expression, old `is_market_open` cutoff and old plan-measure wiring. They were replaced with checks for the new guarded ensemble helper, actual empty-summary behavior, completed-session cutoff and explicit barrier-rate exclusion—not simply deleted. Browser-timeout tests likewise now require truthful full-ensemble wait semantics.

Full-ensemble validation costs more than the old GB-only diagnostic. Final recorded whole-request times were approximately 11.72s INFY, 28.84s TORNTPHARM, 6.20s EMIL, 27.23s PRAKASH and 4.32s LGEINDIA on this Linux environment. Slower Windows CPUs may take minutes. Identical-history ML outputs remain cached. Browser wait budget is now five minutes; timeout text correctly says work may still be running server-side and warns against repeated retries. This is not a server cancellation mechanism or latency guarantee.

Windows/Linux CI includes FIX106. **Native Windows FIX106 execution and hosted CI results are not yet confirmed.** No model/dependency upgrade was used to manufacture a better result.

## 7. October 2026 execution context

The current NSE official algo page identifies April 30, 2026 circular NSE/INVG/73992 for Client Direct API/member retail-algo applications. A functioning local dashboard is not equivalent to broker/API approval or execution entitlement. [1](https://www.nseindia.com/static/trade/platform-services-non-neat-decision-support-tools-algorithm-trading)

Also reviewed in the preceding audit: NSE licensed real-time/EOD products and data usage policy. Market Lens is a useful official beta screener, not proof that an undocumented free API can be embedded or that BSE data can be replaced with NSE data.

Execution prerequisites are operational as well as statistical: selected broker and instrument/product, actual fee/slippage schedule, tick/lot/price-band rules, borrowing eligibility, authenticated authorized execution, position/order reconciliation, kill switch/daily loss controls, and forward paper evidence. This release does not connect a broker or submit any order.

## 8. Install — one command at a time

Download `StockAI_fix106_validation_integrity.git-am.patch` into Downloads. Stop the existing app with Ctrl+C. Do not reapply FIX105. Review Git status first; do not delete the user's two untracked diagnostic JSON files. Stop on tracked-file changes/conflicts or any command error.

```powershell
git status --short
```

```powershell
git am --3way "$env:USERPROFILE\Downloads\StockAI_fix106_validation_integrity.git-am.patch"
```

```powershell
python tools/verify_fix106.py
```

```powershell
python tools/verify_fix105.py
```

```powershell
python tools/verify_fix104.py
```

```powershell
node tools/verify_fix103_render.js
```

Download the latest completed session's official reference (needed for local MATCH status; this does not enable trading):

```powershell
python tools/refresh_official_eod.py
```

If exchange downloads fail, stop and inspect the error; do not fake the reference or hide UNVERIFIED. The tool also supports `--date`, `--nse-file` and `--bse-file` for explicit primary-file replay.

After successful checks:

```powershell
git push origin main
```

```powershell
python app.py
```

Hard-refresh with Ctrl+F5. Expect matched ensemble diagnostics, dated patterns, the daily VWAP label, primary daily verification status, and explicit BLOCKED execution reasons—not a newly profitable/approved trading system.

## Scope statement

Reviewed and changed the affected ML, historical rank cutoff, stock-risk, pattern, financial display, reference-data and Dashboard paths; ran the wider regression suite. This is not a claim of external certification or that no undiscovered defect exists anywhere in the project. Code correctness, data completeness and net trading edge are separate acceptance gates.
