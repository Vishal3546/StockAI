# FIX105 — automatic descriptive coverage, without fabricated trading confidence

**Review date:** 10 October 2026 (IST). **Patch base:** public main `07f6c4c`.

## Decision

Keep the validated 33-security pooled NSE/BSE calibration unchanged. Add an automatically computed **own-history percentile** for requested stocks outside that cohort. Do not equate the two distributions, apply the pooled p80/p95 action bands to a different population, or grant execution permission.

This is a general code path, not a TORNTPHARM/PRAKASH allowlist. Any successfully resolved requested-exchange security can enter this descriptive calculation if its actual input history passes the same checks. It does NOT certify all searchable listings or all provider histories.

## What changed

- Outside-cohort stock requests now ask for 550 TradingView daily bars / a three-year fallback period, rather than the former 300-bar analysis request. A provider may return fewer bars; the app never invents the difference.
- The new `own_history.py` uses the last 500 real completed daily bars: 250 historical scores, each from its own trailing 250-bar OHLCV window, plus the reference score. All historical scoring windows end strictly before the reference session.
- A minimum of 500 completed inputs is required. 300 current bars cannot generate 250 previous 250-bar score windows. A partial current bar does not count toward completed rank history.
- All four existing daily engines must be complete. Their weights, pooled formula and existing artifacts are unchanged. The new reference score must also agree with the current ensemble score, otherwise the descriptive rank is refused.
- Percentile uses the existing upper empirical CDF: percentage of prior scores **less than or equal to** the current score. Ties count at their upper rank. This is a descriptive placement of a discrete heuristic score, NOT calibrated odds of a price rise or profit.
- No cross-exchange substitution. Input OHLCV geometry, unique ordered daily dates, freshness and verified special-session policy are checked. No arbitrary weekend bars or fabricated pre-listing history.
- A maximum 64-entry, process-local LRU cache is keyed by symbol, exchange, source, formula, session policy and the exact consumed input hash. Data corrections invalidate it. Returned values cannot mutate the stored cache. Restarting the app clears the cache.
- At most one first-time history calculation runs per process. Concurrent uncached work returns `BUSY` with a refresh instruction; there is no unbounded queue or hidden background completion promise. A first build is synchronous, so the initial request is slower than a cached request.
- UI distinguishes `OWN-HISTORY percentile`, insufficient history, stale/invalid history, incomplete engines, busy and failed builds. Pooled fit and risk gates remain separate.
- The OOS study payload and card explicitly disclose whether the selected stock was in the research cohort. Inclusion still does not turn a pooled result into stock-specific validation.
- An unsuccessful stock/history lookup now gives an exchange-scoped message and search hint, instead of claiming all three engines failed. No typo is silently replaced with a different security.
- The Windows/Linux CI configuration now includes `verify_fix105.py`. This configuration is not a claim that hosted CI or Windows has already run FIX105.

## Live API checks using actual provider history

Latest completed reference session: **2026-10-09**. Historical sample for the two new descriptive ranks: **2025-10-06 through 2026-10-08**, 250 scores each.

| Stock | Actual daily bars | Result | Own-history percentile | Daily close |
|---|---:|---|---:|---:|
| TORNTPHARM NSE | 550 | Own-history READY; pooled fit not granted | 84.8% | ₹4,656.90 |
| PRAKASH BSE | 550 | Own-history READY; pooled fit not granted | 24.8% | ₹119.20 |
| LGEINDIA BSE | 245 | INSUFFICIENT_HISTORY; no invented bars | — | ₹1,755.90 |
| EMIL NSE | 300 | Existing pooled fit preserved | not applicable | ₹191.17 |
| TORNTPHARM NSE, repeat | 550 | Own-history cache hit | 84.8% | ₹4,656.90 |

All five API requests passed their assertions. Executable quantity stayed **0** and no directional plan was granted to the own-history-only stocks. TORNTPHARM's 84.8% is **not a BUY recommendation**.

In the final recorded run, complete request times were approximately 16.08s / 14.88s / 3.62s / 4.53s / 1.93s. These include provider, ML, indicator and other request work—not just percentile computation. A separate existing TCS snapshot computation took approximately 2.60s for the new 250-window calculation. Timing depends on machine/load/network and is not a service guarantee.

Files: `verification_evidence/live_routes_fix105.json` and `verification_evidence/official_close_fix105.json`.

## Price reconciliation

All four distinct daily closes above matched the saved official **9 October 2026** NSE/BSE UDiFF bhavcopies. Instrument ISIN, requested exchange and session were recorded. Compared the **ClsPric** field, not last-traded price.

Sources:
- NSE: https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261009_F_0000.csv.zip
- BSE: https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_20261009_F_0000.CSV

The evidence records primary-file hashes. The separately delivered evidence bundle includes the two actual 500-bar consumed input snapshots; their byte hashes matched the live own-history input hashes. These are user-requested audit inputs, not a market-data redistribution service.

**Scope limit:** four closing-price matches do not certify every OHLCV field, all historical corporate actions, every listing, intraday quotes or licensed real-time latency.

## Verification

- `python tools/verify_fix105.py`: **22 tests passed** in the final targeted run.
- Dashboard jsdom verifier: **14 cases passed**, including own-history/pooled separation, insufficient-history wording, no-plan safety, study exclusion and markup escaping.
- Actual provider API checks: **5/5**.
- Official daily close sample: **4/4 MATCH**.
- The initial full-suite run passed 44/45 scripts. The only failure was an old source-text assertion expecting `ml_study_payload(req_exch)` instead of the new symbol-aware call. It was updated to require `ml_study_payload(req_exch, resolved)`; that verifier then passed 47/47 assertions. Final clean-patch suite results are delivered in `StockAI_fix105_apply_verification.json`; do not confuse the initial run with final evidence.
- FIX104 research tests now explicitly require honest archival of the old artifacts after the source change, not false current-code claims. Numeric research artifacts themselves were not re-stamped or tuned.

Tests cover exact 500-bar boundary, 499/300/245 rejection, past-only windows, current-bar isolation, degraded engines, exception recovery, invalid geometry/NaNs, duplicate/reversed dates, weekend rules, verified special sessions, stale/future dates, cache invalidation and isolation, defensive copying, bounded cache/build capacity, arbitrary symbols on both exchanges, original pooled fits, typo handling and automatic depth requests.

## Research status: intentionally honest

This patch changes source fingerprints. The FIX104 selected-cohort predictive and net-cost studies were **not rerun** and are therefore archived, not presented as current-code validation. Their negative findings remain available as historical evidence. `RESEARCH_REPORT.md` and the Dashboard make this explicit. The new descriptive percentile has no predictive-edge or profitability validation.

The scanner still uses its separately fitted 30-stock universe. The dashboard pooled cohort remains 33 stocks. Neither was silently expanded using unchanged historical bands. Existing pooled calibration remains valid because its daily engine formula did not change.

## Current official-source review and Market Lens

Inspected on 10 October 2026:

1. **NSE Market Lens:** https://marketlens.nseindia.com/ — homepage identifies Beta mode, 2,000+ equities and customizable screening powered by NSE data. Useful for human stock discovery/official-reference comparisons; homepage index values alone were not treated as verified live quotes.
2. **NSE paid real-time data:** https://www.nseindia.com/static/market-data/real-time-data-subscription — distinguishes L1/L2/L3 and tick-by-tick products, leased-line feed or authorized vendors. The current page links September 2026 CM/CD TBT v6.6 and August 2026 CM L1/L2/L3 v1.33 and Index v1.30 specifications. No feed entitlement or zero-delay certification was inferred from a provider timestamp.
3. **NSE data policy:** https://www.nseindia.com/static/market-data/nse-data-policy — subscribers' relevant agreements govern intended use/handling/dissemination; redistribution is restricted to what is agreed. A publicly viewable screener is not itself proof of reusable API or redistribution permission.
4. **NSE historical products:** https://www.nseindia.com/static/market-data/eod-historical-data-subscription — EOD and historical order/trade data are explicit subscription products, not interchangeable with a free browser screener.

No undocumented Market Lens endpoints were reverse-engineered or integrated. Public API availability and suitable licence for this app were not established. NSE content is not a substitute for BSE-specific history.

## Senior-trader interpretation

The operational order is: establish security/exchange/session and data quality; describe trend/score placement; independently validate predictive and net-cost edge; only then consider execution eligibility. A percentile or uncalibrated ML confidence does not skip those stages. This is a correctness and coverage improvement—not a newly profitable strategy, complete project certification or production trading approval.

## Apply on Windows

Download `StockAI_fix105_automatic_history_rank.git-am.patch` into Downloads. In the existing StockAI PowerShell terminal, stop the app with Ctrl+C. Run one command at a time and stop on any error. Do not reapply FIX104.

```powershell
git status --short
```

Proceed only with a clean working tree and FIX104 already applied (base `07f6c4c` or compatible descendant).

```powershell
git am --3way "$env:USERPROFILE\Downloads\StockAI_fix105_automatic_history_rank.git-am.patch"
```

```powershell
python tools/verify_fix105.py
```

```powershell
python tools/verify_fix104.py
```

```powershell
python tools/verify_fix103.py
```

```powershell
node tools/verify_fix103_render.js
```

After all pass:

```powershell
git push origin main
```

```powershell
python app.py
```

Hard-refresh the browser, then check TORNTPHARM NSE and PRAKASH BSE. Expect own-history percentile, not pooled FIT or automatically enabled BUY/SELL orders. Short-history IPOs must still show an explicit insufficiency state.
