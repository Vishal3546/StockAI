# FIX-102 — exchange isolation, deterministic stale history, IST timestamps

Date: 10 October 2026. Applies directly to public FIX101 commit `ab170d761d7347e57c0f0d8faae77c9a237422df`. Do not reapply FIX100 or FIX101.

## Implemented

- TradingView and Yahoo historical adapters query only the requested exchange. Conflicting qualified symbols (`NSE:TCS` with BSE, or `.NS` with BSE), and invalid API exchange values, are rejected rather than silently rewritten.
- Quote/cache identities and historical source identities are checked. Stock and Timeframes analysis refuse wrong or unidentified historical exchanges before computing/displaying results.
- Available same-exchange stale history is consistently returned as **HISTORICAL ONLY**, with last/expected sessions disclosed. It no longer changes from HTTP 200 to 409 just because another exchange happens to have fresh data. Missing requested history is not replaced with another exchange. No candles are invented and vendor agreement is not treated as proof of a holiday.
- Dashboard closes the previous stream at the start of a new analysis and hides the previous analysis on loading/failure. SSE, poll, manual-refresh, analysis and error callbacks are guarded by captured symbol/exchange/generation. Old callbacks cannot mutate the current selection or close its stream.
- Timeframes similarly guards async responses, clears the previous plan and hides old cards on exchange changes/failure. Missing/mismatched response identity is rejected. Historical-only responses cannot expose a current trade plan.
- Chart epoch seconds are parsed as UTC and converted to timezone-aware Asia/Kolkata, independent of host timezone. Removed a second downstream timezone conversion. Incomplete/error chart responses and missing/nonfinite OHLCV values are rejected.
- Existing FIX100 research/execution restrictions remain: executable quantity zero and no verified trading edge. Stale history also disables score calibration/current plan.

## Verification

- Full regression runner: **41/41 verifier scripts passed** before final packaging; clean-apply results are recorded in the accompanying apply-verification JSON.
- New deterministic Python suite: **19 tests** (includes the original stale-history counterexample, requested-only adapters, route boundaries and UTC/IST/New York host-timezone simulation).
- New actual JavaScript function VM tests: **14 cases**, covering Dashboard and Timeframes race/identity/stale-plan paths. Existing jsdom render suites also run in the full suite. These are not a native Windows browser end-to-end test.
- Fresh live TradingView matrix: **8/8** requests returned 100 bars with requested exchange identity (TCS/JIOFIN × NSE/BSE × daily/5-minute).
- Fresh full stock routes: **3/3 HTTP 200** with expected frame exchange and executable quantity zero: TCS NSE, TCS BSE, JIOFIN BSE. This exercises timezone-aware frames through the analysis engines, not just the parser.
- Old verifier fixtures were updated to declare real exchange identity, use deterministic session dates, reject invalid exchanges, and expect fail-closed UI behavior. Tests that previously expected cross-exchange/missing-identity cards to remain visible now assert that those cards are hidden.

## What the live sample does and does not establish

The daily samples end on **9 October 2026, 09:15 IST** (daily bar opening timestamp). On Saturday 10 October, Friday 9 October is the latest completed regular cash-market session. This is not a Saturday live tick.

The sampled 5-minute histories end at **15:10 IST on 9 October**. The matrix verifies retrieval, identity, row count and timestamp normalization—not completeness through the last exchange candle. Do not interpret these results as zero-delay or a complete official end-of-session tape.

The stock route's `data_source` can identify its headline quote provider, while `frame_exchange` identifies the analysis history exchange. Same-exchange providers may still disagree on price or adjustment convention. This patch does not certify official exchange price accuracy, corporate-action reconciliation, uptime, licensed real-time access, profitability, or every other project behavior.

Native Windows FIX102 execution remains untested. The user's Windows evidence established FIX101 chart retrieval; timezone simulation here is not a substitute for running FIX102 on that machine. No remote push was performed. Dependencies were not blindly upgraded.

## Apply on Windows (PowerShell)

Stop the running app. Place `StockAI_fix102_isolation.git-am.patch` in the repository directory, then:

```powershell
git status --short
git rev-parse HEAD
# Expected current HEAD: ab170d761d7347e57c0f0d8faae77c9a237422df
# Back up/commit local edits first; apply on a clean working tree.
git am .\StockAI_fix102_isolation.git-am.patch
python tools\verify_fix102.py
# JS tests require Node and npm dependencies:
npm ci
node tools\verify_fix102_render.js
python app.py
```

If patch application conflicts, stop and use `git am --abort`; do not force-overwrite local edits. Restart the server and hard-refresh the browser (Ctrl+F5) so the new JavaScript is loaded. Startup should mention `TradingView FIX-102 ... timestamps normalized to IST`.

Full suite, optional: `python tools\run_verifiers.py`. Some legacy verifiers make network calls despite offline master-list mode, so future provider outages can affect results.

## Manual acceptance checks

1. TCS NSE → BSE → NSE: each successful frame/quote identifies the selected exchange.
2. Throttle requests, switch exchange while analysis is pending, and force the new request to fail: the old analysis must remain hidden; old ticks must not update it.
3. Change Timeframes exchange during a pending request: the old result/plan must not reappear.
4. Historical-only data must visibly disclose its actual session; it must not create a current trade plan or executable size.
5. If requested-exchange history is unavailable, show an error rather than relabeling the other exchange's data.
