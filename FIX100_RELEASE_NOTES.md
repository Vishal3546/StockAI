# FIX-100 — safety, data boundaries, research accounting and pages

Release date: **10 October 2026 (IST)**  
Patch base: **a68cbd136d080b6cd64151b235bfccd4d149f307**  
Public `main` was rechecked against that hash during release preparation. This is a local git-am release; no authenticated remote push was performed.

## Important behavior changes

- **Research only:** `/api/stock` never converts an unverified historical hit rate into executable size. `ensemble.tradeable=false`, executable quantity/notional/risk are zero, and a separately named `illustrative_qty` may retain the paper calculation. This is deliberate, not a broken sizing panel.
- **Atomic journal close:** `PATCH /api/journal/<id>/close` replaces the old browser DELETE-then-POST sequence. Identity, exchange, notes and original logging time survive. Repeating the same exit is idempotent; conflicting exits return 409. Invalid requests do not destroy the original record.
- Gross R remains available for compatibility. **Net R/net expectancy are separate**, based on estimated costs on the actual recorded entry and exit notionals. Open positions do not acquire realized P&L. An open trade's legacy `cost` field is explicitly a reference round-trip estimate.
- **Next-open research fills:** a close-derived signal executes no earlier than the next bar's real Open. Fixed units, cash, entry/exit fees, reversals, allocation changes and terminal liquidation reconcile to the trade ledger. Close-only input and same-open execution are rejected. Historical performance reports are marked archived: regenerate rather than comparing old and new engine numbers as if equivalent.
- Research fills remain fractional and assume available liquidity. They do **not** simulate borrow availability, margin liquidation, circuit limits, lot constraints, stop execution or a real broker. Overnight short costs use a delivery-cost proxy, not permission to short cash equity overnight.

## Audit coverage

| Finding | Change / status |
|---|---|
| A01 journal close data loss | Atomic update with input, conflict and retry regressions |
| A02 ML history cache collisions | Content/index digest plus symbol, exchange, model/config identity |
| A03 incomplete exchange isolation | Requested Yahoo suffix only; strict historical/MTF paths; BSE analysis does not call NSE live quote or NSE fundamentals |
| A04 freshness/session errors | Latest-completed-session checks, future/unknown/invalid-frame rejection, explicit partial/rank dates, calendar coverage guard |
| A05 backtest ledger | Replaced shifted-return approximation with next-open cash/holdings ledger; terminal fees and reconciliation tests |
| A06 unknown ML target/backfill | Unknown final target stays missing, no feature backfill, newest feature row used for inference, training purged before evaluation |
| A07 plan-cache parameters | Cache includes content, exchange, stop/target multipliers, horizon and sample threshold |
| A08 opening gap / beta | Open versus previous Close, beta bounds and session alignment; benchmark cache synchronized and failure TTL shortened |
| A09 invalid input | Object-only writes, malformed/null JSON rejection, finite positive numeric limits, integer quantities, enum checks, bounded calculator inputs and 256 KiB request bodies |
| A10 misleading net outcome | Actual-notional two-leg estimates, separate gross/net R and net sample count |
| A11 alerts / IDs | Fresh same-exchange positive quotes during session only; UUID IDs; no duplicate same-second `fired_now` notification on retries; pending updates merged under lock |
| A12 options bounds | Analytic strike-knot/tail extrema over underlying price >= 0, explicit unlimited tails, missing-leg rejection and zero-OI handling; Python and actual browser-function tests |
| O01 calibration expiry | Genuine NSE/BSE rebuilds; existing expiry, integrity and as-of gates retained |
| O02 rate mismatch | Shared NSE cash transaction + IPFT rate corrected to 0.00307% per side |
| O03 cache policy | Explicit bounded **last-write-order**, not falsely advertised as read-recency LRU; synchronized writes |
| O04 Windows encoding | Explicit UTF-8 subprocess parent/child configuration; Windows runtime still needs native validation |
| O05 multi-worker stores | Reentrant local file/process lock, atomic replacement and fsync; corrupt JSON stores refuse overwrite. Linux multi-process counter regression passed |
| O06 vendor close disagreement | **Not adjudicated.** No claim that disputed DHOOTIN BSE/vendor closes are now exchange-correct |
| O07 proxy/window/unit issues | ROE unit handling, no dividend magnitude guessing, calendar-year range, volume-profile endpoint bin, proxy disclosures and clearer ML/heuristic labels. Not a conversion into real order-flow data |
| O08 journal UI | Requested exchange saved; gross/net/sample labels clarified; 100 observations no longer called statistically reliable |
| O09 slow sequential pipeline | Bounded caches, TradingView serialization, request cancellation/sequence protection and timer cleanup mitigate repeated work/stale rendering. **Not a latency/load certification** |
| O10 research adjustment/provenance | Requested-exchange, raw-tagged research caches; Yahoo builders use the same unadjusted convention; source/adjustment/latency disclosures. **Corporate actions and provider adjustments are not independently reconciled** |

Additional checks corrected the train-only majority ML baseline (including pooled studies), the final walk-forward remainder, malformed shared-store overwrite, special-character token disclosure in startup links, Saturday startup labeling, and calculator optional-fee NaN handling.

## Rebuilt artifacts — not synthetic replacements

The historical builders downloaded provider OHLCV and recomputed scores using the changed scoring function. Thresholds/hashes were not merely relabeled. The as-of and ten-calendar-day expiry guards were not weakened.

| Artifact | Coverage / observations | Last scored session |
|---|---|---|
| `score_calibration.json` | 250 sessions; 30 covered symbols; 7,500 scores | 2026-10-08 |
| `score_calibration_bse.json` | 250 sessions; 28 covered symbols; 7,000 scores | 2026-10-08 |
| `backtest_history_nse.json` | 289 sessions; 30 symbols; 8,670 observations | 2026-10-08 |
| `backtest_history_bse.json` | 289 sessions; 27 symbols; 7,803 observations | 2026-10-08 |

The BSE builders had different available coverage on their separate fetches; it is not claimed to be 30/30. Calibration is **relative ranking**, not a strategy-profitability validation. Score-band history contains descriptive gross forward returns with overlapping observations, not independent net-execution trials. Its naive standard errors/t-statistics are not trading significance evidence. Hash-incompatible history is refused, and artifact cache keys include file revision.

`ml_edge_study.json` and older net-performance reports are retained as historical evidence, with an explicit prior-pipeline/rebuild notice. A complete new cost-aware OOS study has **not** been supplied by this patch. It cannot enable executable sizing.

## Verification and reproducibility

Test environment: Linux, Python 3.13, Node 20.20.2, jsdom 29.1.1. Browser tests use jsdom, not a native visual/end-to-end browser certification. Some inherited scripts fetch provider data despite offline startup; the full suite is not hermetic.

- New `tools/verify_fix100.py`: **37 semantic/adversarial regressions** (including a successful full BSE stock response, no-execution gate and cross-process lock test).
- New `tools/verify_fix100_render.js`: **4 payoff cases using the actual Options page functions**, including tails beyond the displayed chart range.
- Python syntax/import and all **12 inline JavaScript scripts** checked; **14/14 running-server HTTP smoke routes** returned 200.
- **38/38 verifier scripts passed** on the final source. Results are recorded in `FIX100_VERIFICATION.json`; clean git-am apply is separately verified before delivery.
- New portable `tools/run_verifiers.py` preserves exit codes/logs in ignored `verification_results/`; development-only jsdom is pinned through `package.json` and `package-lock.json`.

To reproduce after installing `requirements.txt`:

```text
npm ci
python tools/verify_fix100.py
node tools/verify_fix100_render.js
python tools/run_verifiers.py
```

Existing tests were updated where the contract legitimately changed: actual exit-notional costs, boundary-validation 400 responses, strict freshness/same-exchange quote fixtures, request-scoped suffixes, safer sample labels, and next-open execution. The independent original audit had 16 failing adverse checks out of 18. Its Close-only fixtures were changed to explicit Open fills; the former LRU expectation now tests the documented write-order policy. Safety gates were not removed to obtain passes.

Calibration is time-sensitive: later runs may correctly fail because an artifact expired. Rebuild it from real data rather than adjusting timestamps or bypassing expiry:

```text
python tools/build_score_calibration.py --exchange NSE
python tools/build_score_calibration.py --exchange BSE
python tools/build_backtest_history.py --exchange NSE
python tools/build_backtest_history.py --exchange BSE
```

## Deployment cautions and remaining acceptance gates

1. Back up the user's untracked `trade_journal.json`, `alerts.json`, `watchlist.json` and `.env` before updating. No user portfolio or credentials are included in this patch.
2. Apply on a clean local `main` based on the stated commit. An advanced/divergent branch can still produce a three-way conflict. A verified clean apply is not a guarantee against every future branch state.
3. Use a supported production server/authenticated private deployment for real records; do not expose the Flask development server with an empty token. File locks are for **one machine/local filesystem**, not multiple hosts or arbitrary network shares.
4. Native Windows process-lock/subprocess smoke tests, browser visual tests, multi-worker soak tests, security dependency/CVE review and all-symbol/tick correctness are still acceptance work.
5. Obtain official bhavcopy adjudication for disputed prices and an appropriate licensed feed if certified real-time latency is required. A recent provider timestamp does not prove exchange entitlement or zero delay.
6. Reconcile corporate actions, broker contract-note costs (especially BSE/DP/borrow charges), liquidity/slippage and paper executions. Do not promote this release to autonomous trading on test-suite success alone.

## Primary tariff reference

NSE/FA/73061, 27 February 2026, effective 1 March 2026, was read during this release: cash transaction charge Rs 306.99 plus IPFT Rs 0.01 per crore **per side** = Rs 307/crore = 0.00307%. This does not establish BSE tariffs or a universal broker cost schedule.

https://nsearchives.nseindia.com/content/circulars/FA73061.pdf

Exchange data-product distinction (licensed real-time versus delayed/snapshot products):

https://www.nseindia.com/static/market-data/real-time-data-subscription
