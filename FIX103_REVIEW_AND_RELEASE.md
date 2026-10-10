# FIX-103 — price provenance, calibration eligibility and no-plan consistency

10 October 2026. Base: public FIX102 commit `c625ca362a3ebb61841d1e6e94b2e7ea6227c455`.

## 1. Findings from the supplied Windows logs

FIX102 applied and pushed successfully. The Windows run discovered 19 tests: **18 passed and one `tzset` host-timezone simulation was skipped**. The portable epoch and age tests did pass. Messages such as `series_error`, strict-exchange rejection and `Data Stream Failed` occurred inside deliberately simulated failure tests ending in `ok`; those are not evidence that the real TCS feed failed.

The subsequent application logs show requested NSE/BSE TradingView histories and HTTP 200 analysis/stream responses. That establishes successful retrieval for those samples, not certified official prices, licensed zero-delay ticks or proof of every browser race path.

### Why terminal TradingView and dashboard Yahoo can both be correct

`stock_api()` obtains daily candles through `smart_fetch()`, commonly TradingView. It separately asks NSE/Yahoo for the headline price. It used to overwrite `active_source` with that quote provider, then expose it as `data_source`. Thus `data_source=yahoo.bo` did not mean the BSE historical indicators had switched to Yahoo.

There was also a real presentation bug: `render()` set the top source badge once, whereas SSE/poll/manual-refresh updated only the price-side chip. The lower chip could say TradingView daily close while the top continued to say Yahoo.

**FIX103:** separate API fields `analysis_source`, `quote_source`, `quote_time`; persistent history/indicator provenance with frame session and close; both quote labels synchronize through the same update function. Initial rendering fills the price source/time before the first stream event. Source disclosure uses text-safe DOM insertion. Existing identity/generation guards remain. We did not force all providers to say TradingView or remove truthful fallback behavior.

## 2. Calibration findings

The fixed `score_calibration.UNIVERSE` contains 30 symbols, including TCS, but not JIOFIN or LGEINDIA. NSE and BSE have separate artifacts for that same supported membership.

- **TCS NSE:** the submitted FIT result is consistent with supported membership and the existing artifact.
- **JIOFIN BSE:** outside the supported universe. A generic rebuild will not add it, regardless of available candles.
- **LGEINDIA BSE:** outside the universe and the observed 245 completed bars are below the 250-bar live rank requirement. The builder needs approximately 500 input bars to produce a 250-session distribution after warm-up; padding five bars would not establish a historical fit or an edge.
- A fit as of 8 October for a 9 October reference bar is intentional past-only calibration, not automatically a stale-data defect.

Two messages were wrong: the BSE banner checked only the exchange field, not `calibration.ready`, and the exclusion error hardcoded “30 NSE names” even for BSE. The UI also duplicated “UNFITTED” and universally prescribed an NSE-default build command.

**FIX103:** readiness and matching exchange are both required to claim a fit. Unsupported symbols get explicit coverage/remediation information and no blind rebuild instruction. Available/required bars are disclosed. Rebuild hints for supported eligible symbols are exchange-specific. No universe membership, thresholds, formula hash, artifact, minimum history, fitted status or execution gate has been relaxed.

**JIOFIN/LGEINDIA remain UNFITTED after this patch. That is intentional and honest.** Supporting a broader universe requires deliberate historical collection, coverage/session/corporate-action checks, a reproducible refit and independent validation—not hiding the warning.

## 3. Conflicting scores and trade instructions

The four top KPI cards and the four-engine ensemble rank are different formulas. Master KPI 80 and ensemble rank 58 are not directly comparable scales. The KPI names included BUY/SELL/INVEST despite being uncalibrated heuristic votes from daily inputs. An “Intraday” card was not an independently calibrated intraday strategy.

**FIX103:** numeric KPI scores/formulas are unchanged; labels become BULLISH/BEARISH/MIXED (and strong variants), with explicit heuristic/no-order metadata and Dashboard/Timeframes disclosure. The fitted ensemble remains separate. Consumers that parse the old KPI `action` strings must accommodate these new non-instruction labels.

A more concrete defect existed in the risk output: `calculate_risk()` used its non-SHORT arithmetic branch for `direction=NONE`, so neutral/unfitted analyses could still receive long-shaped stops, targets and a trailing instruction. Zero executable quantity prevented an order, but the displayed instructions were contradictory.

**FIX103:** stock API exposes `plan_available`; without a fitted directional rank it clears entry/stop/target/trailing/R:R fields and net-target estimates. Dashboard independently checks readiness, explicit direction and availability before displaying any illustrative plan. NONE shows **NO DIRECTIONAL PLAN** instead. Genuine fitted directional scenarios remain research-only with executable quantity zero. Reference price/source are explicit; price-gap copy no longer claims quote prices drive historical indicators.

## 4. ML and costs

HIGH model agreement is consensus, not demonstrated predictive accuracy. The supplied TCS/JIOFIN results underperform their baselines; LGEINDIA's small positive diagnostic edge is not sufficient evidence given the reported variability. The October 1 OOS study is already flagged as prior-pipeline and cannot validate FIX100/102/103 behavior.

FIX103 makes archived status prominent at the study header and removes text presenting that archived study as the current authority. It does not retrain models, regenerate OOS evidence, turn off safeguards, or claim a profitable strategy.

The quoted 0.183% is a configured estimate, not a guaranteed broker/exchange bill. FIX103 labels BSE figures as an NSE-default estimate with unverified BSE/broker charges, changes “Capital used” to a budget label, and removes the “net = actual profit” tooltip. No fee tariff or slippage guarantee is introduced.

## 5. Verification and live samples

New suites: **8 deterministic Python cases** and **10 actual Dashboard jsdom cases**, including initial provenance, tick-source synchronization, exchange switching, false-fit prevention, no-plan hiding, archive disclosure and markup injection defense. The full runner contains **43 scripts**; final clean-apply results and hashes are recorded in the accompanying verification JSON.

Fresh sandbox live observations from this work:

| Route | History | Initial quote | Calibration | Completed bars | Plan |
|---|---|---|---|---:|---|
| TCS NSE | TradingView NSE | yahoo.ns | ready, as-of Oct 8 | 300 | none for this rank |
| TCS BSE | TradingView BSE | yahoo.bo | ready, as-of Oct 8 | 300 | none for this rank |
| JIOFIN BSE | TradingView BSE | yahoo.bo | unsupported membership | 300 | none |
| LGEINDIA BSE | TradingView BSE | yahoo.bo | unsupported membership | 245 | none |

All four returned HTTP 200 and zero executable quantity. Separate TCS/JIOFIN × NSE/BSE × daily/5-minute retrieval checks returned 100 bars in all eight cases. These verify sample retrieval/identity—not independent exchange price certification. Five-minute samples ended at Oct 9 15:10 IST, not a verified complete end-of-session tape.

Existing verifier expectations were updated for additive heuristic metadata, explicit plan availability, no R:R without a plan, reference-price semantics and rendered disclosure text. Numerical KPI formulas and calibration artifacts are unchanged. The XSS plan fixture now explicitly marks an available research plan so it continues to test that rendering path, rather than passing only because the plan is hidden.

## 6. Boundaries / remaining work

This is a targeted review and repair of the submitted Dashboard paths, not a claim that every repository line is now certified bug-free. In particular:

- Broader calibration coverage is not implemented; no false fitted ranks are supplied for unsupported stocks.
- Official prices, provider corporate-action conventions and full intraday completeness are not independently reconciled.
- Current-pipeline predictive and net execution edge remain unvalidated. Recorded archived research is not current proof.
- A separate calendar audit is warranted: the calibration builder currently filters weekend bars wholesale; special exchange sessions must be handled with an authoritative session calendar rather than assuming all weekend observations are invalid. This patch does not rewrite calendars or refit the artifacts.
- No blind dependency upgrade, current-exchange-cost certification or licensed realtime claim.
- FIX103 has not been run natively on Windows here. The supplied native Windows evidence is for FIX102.
- No remote push was performed by the assistant.

## 7. Windows apply (same Downloads workflow)

Stop the running app with Ctrl+C. Download `StockAI_fix103_provenance.git-am.patch` to Downloads. From the repository, back up/commit local edits and verify a clean working tree. Expected base is FIX102 `c625ca3`; do not reapply FIX102.

Run each command separately; stop on any failure:

```powershell
git status --short
git am --3way "$env:USERPROFILE\Downloads\StockAI_fix103_provenance.git-am.patch"
python tools\verify_fix103.py
python tools\verify_fix102.py
```

Optional browser-function regressions (Node required):

```powershell
npm ci
node tools\verify_fix103_render.js
node tools\verify_fix102_render.js
```

After successful verification:

```powershell
git push origin main
python app.py
```

Hard-refresh the browser with Ctrl+F5. If `git am` conflicts, stop and inspect; `git am --abort` returns to the pre-application state. Do not force-overwrite local changes.

Expected UI: `Quote: ...` and `History / indicators: ...` as separate sources; no false BSE fitted claim; one clear unsupported-coverage warning for JIOFIN/LGEINDIA; no entry/targets for direction NONE. A quote provider can legitimately change during fallback, but both quote labels must change together while historical provenance remains fixed until re-analysis.
