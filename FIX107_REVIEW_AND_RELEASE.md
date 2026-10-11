# FIX107 — separate manual cash planning for Angel One

**Reviewed 11 October 2026.** User-confirmed setup: FIX106 installed/tests passed; both cash intraday and delivery/swing; manual broker-app execution; Angel One, no special plan supplied.

## What this release actually delivers

New Dashboard → **Manual cash planner** (`/manual-planner`). Separate product selector, requested-timeframe descriptive context, manual entry/stop/target, user-entered cash capital and rupee risk budget. No credentials or broker API required. No order submission, position monitoring or automatic square-off.

- **Intraday:** actual 5-minute provider bars, available-session typical-price/volume VWAP proxy, timestamps, latest completed bar age, same-IST-date status, opening-bar presence and unobserved slots. Discards partial 5-minute bars and excludes bars outside the regular 09:15–15:30 window. No daily fallback. This window is not a broker square-off deadline or proof of an instrument's continuous-session/CAS schedule.
- **Delivery/swing:** completed-session daily data using the existing exchange calendar cutoff; SMA20/50/200 only with enough real bars. Long only. No overnight unborrowed cash short. Daily ML training/calibration code is unchanged.
- **Manual quantity:** independent of ML confidence, measured edge and Kelly. Integer binary search against both cash-capital and modeled stop-loss budgets. Includes adverse entry/exit slippage and fees, without leverage. A positive estimate does not authorize execution: `execution_authorized=false`, `executable_quantity=0` remain explicit.
- **NSE Angel One fee profile:** brokerage minimum/cap, transaction fee, IPFT, SEBI, product-specific STT/stamp, GST, delivery-sale DP debit, and ₹1/order fee/rounding reserve. One entry plus one complete exit; stop and target are alternative scenarios, not simultaneous charges or scale-outs.
- **BSE:** real BSE context supported with strict exchange selection. Automatic fee-aware sizing is explicitly blocked pending independently verified stock-group/sidedness rules. No hidden NSE substitution.
- **UI:** changing inputs hides old sizing; late async results cannot overwrite a newer plan/product. Errors/provider strings are inserted as text. Dashboard shortcut has the existing inline icon treatment; generic Calculators page now explicitly says it is not the Angel One tariff profile.
- **Security:** new page joins token-protected routes; both API routes inherit existing authentication, input-boundary and rate-limit handling.

## What was checked and fixed while building it

Initial application import exposed a missing module-level `sys` import; fixed and covered through real-app route tests. The first full regression exposed two existing navbar contract failures after adding a page: button count and missing inline SVG. Added the icon and updated the count assertion from 11 to 12, preserving icon checks. The final full regression passed after these fixes.

Manual sizing tests compare binary-search quantities against an independently enumerated feasible-integer set, including long/short, product fees and tiny budgets. Independent hand arithmetic for a ₹100,000 intraday buy gives brokerage ₹20, transaction ₹3.0699, IPFT ₹0.0001, SEBI ₹0.10, stamp ₹3, GST ₹4.1706, reserve ₹1: total ₹31.3406 before display rounding.

## Verification results before packaging

- **48/48 verifier scripts passed** on Linux Python 3.13, pinned project requirements and npm lockfile.
- **27 new Python tests passed**, with numerous parameterized subcases: fees, low-price brokerage caps, invalid/non-finite values, cash/risk feasibility, slippage, zero quantity, BSE refusal, overnight shorts, VWAP session reset, unfinished candles, missing timestamps, zero volume, timezone conversion, daily fallback refusal, real Flask routes, authentication and completed-session filtering.
- New jsdom tests passed: product state, BSE warning, result rendering, edit invalidation, stale async responses, and text-only errors.
- Existing Dashboard jsdom test: **48 assertions passed** after the intentional navbar update.
- **4/4 actual-provider requests returned HTTP 200**: INFY/NSE and EMIL/BSE, each with separate 5m and 1d requests.
- **4/4 frozen-input replays** exactly reproduced the input hashes and numeric context fields in the same environment. Age-of-bar is wall-clock-dependent and deliberately excluded from numeric replay equivalence.

### Actual-provider observations (latest supplied session: 9 October 2026)

| Instrument | Requested interval | Context bars | Last close | Observation |
|---|---|---:|---:|---|
| INFY / NSE | 5m | 72 latest-session bars | ₹1,025.90 | 09:15–15:10; 0 unobserved slots between endpoints; not a live quote |
| INFY / NSE | 1d | 500 daily bars | ₹1,023.40 | Completed-session context |
| EMIL / BSE | 5m | 66 latest-session bars | ₹190.40 | 09:15–15:25; 9 unobserved slots between endpoints |
| EMIL / BSE | 1d | 500 daily bars | ₹191.45 | Completed-session context |

Intraday last trade and official daily closing methodology can differ; these values are not forced to match. Unobserved slots can mean no trades or missing data. Zero missing slots between endpoints does not prove complete exchange tape, valid closing-session coverage or correct corporate-action adjustment. Available-session VWAP is explicitly a **bar-based proxy**, not official tick/trade VWAP.

## Fee scope and sources

Profile ID: `angel_one_standard_cash_nse_2026-10-11`. Review date is not a guarantee of future tariff validity.

| Component | Delivery | Intraday |
|---|---|---|
| Brokerage / executed order | Lower ₹20 or 0.1%, minimum ₹5, subject to exchange cap | Same |
| NSE transaction | 0.0030699% each side | Same |
| NSE IPFT | 0.0000001% each side | Same |
| SEBI | ₹10/crore each side | Same |
| STT | 0.1% each side | 0.025% sell only |
| Stamp | 0.015% buy only | 0.003% buy only |
| GST | 18% of brokerage, exchange/IPFT, SEBI and DP components | Same, no DP |
| DP | ₹20 + GST, one delivery-sale transaction assumed | None |

For NSE, brokerage cap is 2.5% of value, with up-to-₹0.25/share cap below ₹10. It is not safe to apply a flat ₹5 minimum unconditionally to tiny orders. Partial fills of the same order are not separately charged as orders by this model. Exact contract-note grouping/rounding is not reproduced: components are calculated unrounded, then displayed to paise; rounded displayed components can differ from rounded totals by a paise. The ₹1 reserve is an assumption, not a broker levy.

Primary sources reviewed:
- Angel One pricing: https://www.angelone.in/exchange-transaction-charges
- Angel One calculator: https://www.angelone.in/calculators/brokerage-calculator
- DP wording, per scrip per transaction: https://www.angelone.in/support/charges-and-cashbacks/dp-depository-participant-charges
- NSE maximum brokerage and low-price exception: https://nsearchives.nseindia.com/content/circulars/insp11532.htm

BSE A/B sidedness in the broker table is ambiguous relative to other rows, and other groups carry substantially different rates. This release does not guess group assignment or silently price BSE as NSE. Promotions, NRI tariffs, MTF funding, short-delivery/auction costs, account maintenance and special account plans are outside this estimate.

## How to use

1. Open Dashboard → Manual cash planner after restarting StockAI.
2. Choose product and exchange. Fetch optional symbol context; independently verify the live broker quote and current session.
3. Enter YOUR capital, rupee risk budget, entry, stop and one full-exit target. Slippage defaults to an editable 5 bps each side; this is not an empirical estimate.
4. Read quantity, estimated loss/net target, both alternative exit fee breakdowns and all assumptions. Check actual ticks, lots, broker eligibility, holdings/cash and cutoff before any separate manual action.
5. Orders, modifications, cancellations and timely exits remain entirely outside StockAI.

## Important unfinished work

This is **manual planning + descriptive timeframe separation**, not a new validated intraday or swing prediction strategy. The existing research scale-out simulator is unchanged; this planner supports only a single full exit. It does not add complete-strategy independent holdout/forward evidence, all-history/corporate-action/fundamental certification, portfolio-level limits, tick-level liquidity simulation, live margin verification or BSE automatic fee sizing. Daily ML accuracy is not an intraday win probability. Stop-loss estimates can be exceeded by gaps/circuits/illiquidity or failed orders. Native Windows application of this new release still requires the user's check.

## Apply (PowerShell, inside your StockAI repository)

Save `StockAI_fix107_manual_cash_planner.git-am.patch` in Downloads. Run each command separately and stop on any error.

```powershell
git status --short
```

Proceed only with a clean working tree and FIX106 installed.

```powershell
git am --3way "$env:USERPROFILE\Downloads\StockAI_fix107_manual_cash_planner.git-am.patch"
```

```powershell
python tools/run_verifiers.py
```

After every verifier passes:

```powershell
git push origin main
```

Then stop/restart your usual StockAI process and open the new Dashboard shortcut. Do not reapply FIX106. No new dependency or broker credential is required. If apply/test fails, stop and share the exact output rather than forcing or skipping it.
