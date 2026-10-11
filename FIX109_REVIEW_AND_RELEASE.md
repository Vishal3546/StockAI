# FIX109 — indexed NSE/BSE scanner and UI corrections

11 October 2026. Base: user-applied FIX108, commit 2fef5f7.

## Scope and audit corrections

- Existing Screener defaults to all indexed NSE/BSE listings. NSE, BSE and the original 30-stock research cohort remain selectable. Fifty rows per page is pagination, not a stock cap.
- Existing master index supplies company/ticker suggestions and exact exchange selection; selected-stock deep analysis remains the FIX108 pipeline on the same page. No new broker execution or unrelated product.
- Facets/counts rebuild after every response; valid selected filters survive. Invalid selections reset and reload.
- Generated timestamp and completed candle dates are separate. Browser Refresh is the normal workflow. Existing cohort auto-refresh is a startup age check, NOT a repeating timer/live feed.
- Response sequence/scope guards prevent stale search, data, status and cohort-refresh responses overwriting another scope. Mobile selections scroll to deep analysis.

## Full-market processing contract

SQLite WAL storage in `.stockai/market_scanner.sqlite`, with a local-host OS file lease shared with the legacy cohort subprocess. Inventory browsing does not fetch thousands of historical series. One serial background worker fetches completed-daily data through the existing strict-exchange pipeline; bulk ML training is not performed.

Start freezes the indexed queue and completed-session targets. Each listing/result/checkpoint commits atomically. Default pacing is 0.5 seconds between requests; a 30-minute budget is checked between requests. Pause waits for the current provider call. Progress includes processed, remaining and failures. Resume skips completed entries, including ERROR entries; start a new scan to retry errors. Ten consecutive failures pause processing. An abandoned worker becomes PAUSED; explicit resume is required. Interrupted workers must not falsely report completion. Session rollover requires a new scan. A changed master is disclosed, not silently appended to an existing queue.

Pending/error values are unavailable, never copied from master snapshot prices. Under 200 completed bars yields INSUFFICIENT_HISTORY. Zero latest-session volume withholds ranking (NO_RECENT_VOLUME). Historical results withhold rank; previously fresh saved results are aged on reads when a newer completed session exists. Wrong-exchange and future-session results are rejected. A fallback-only NSE master blocks ALL/NSE scan starts.

Outside the fitted NSE research cohort, technical scores are exploratory heuristics and signals remain UNRATED. Existing 30-stock percentile bands are not transferred to other NSE stocks or BSE. A partial scan is not a complete market ranking. No accuracy/profitability uplift is established by this release.

## Verification evidence

- Full regression before the final scope-race guard: 52/52 verifier scripts passed. The guard passed targeted DOM regressions; release also requires a clean-applied full regression (see external release verification JSON).
- FIX109 Python: 29 isolated tests cover >30 listings, pagination/clamping, company search, literal wildcards, dual exchanges, pending prices, queue completion, pause/resume, budgets, failure circuit breaker, session changes, stale rank suppression on read, wrong exchange, leases/crash recovery, master revisions/removals/fallback, API validation/auth/start/pause/busy, interrupts and zero volume.
- DOM tests cover current facets, selections, dates, wording, 7,344-entry pagination, escaped suggestions, exact exchange, stale lookup responses and late cohort responses after scope changes.
- Live inventory audit: 2,574 NSE + 4,770 BSE = 7,344 indexed listings; 74 API pages visited; every exchange/symbol key unique. These are listings, not unique companies. BSE master snapshot: 7 October 2026, 19:29:22 IST. Coverage is source-dependent, not proof of every currently active/tradable equity.
- Actual provider analysis sampled only three listings: INFY/NSE ₹1,023.40 (SELL), TORNTPHARM/NSE ₹4,656.90 (UNRATED), EMIL/BSE ₹191.45 (UNRATED). These are historical completed-session observations, not live quotes or trade recommendations. A 7,344-stock provider scan was NOT completed.
- Existing FIX108 out-of-sample methodology/negative costed diagnostic is unchanged; no new predictive-performance study was performed. Engineering regressions are not an OOS trading result.

## Usage after applying

Restart the existing application and open Dashboard → Screener. Browse/search all indexed listings immediately. Refresh starts a new scan for the selected scope; Resume continues an eligible paused queue. Use Pause to stop after the current request. Click a listing/suggestion for existing on-demand deep analysis. Retain the 30-stock cohort when its research-specific view is desired.

No new package dependencies. SQLite and locking require writable local storage. The lease is local-host coordination, not a distributed scheduler. Use the existing single application-server deployment; independent servers with different in-memory masters are not a validated shared-database deployment. Native Windows locking has not been runtime-tested in this Linux sandbox. Provider availability, throttling, licensing and instrument history can limit results. Restart/new scan after a software update is recommended; cross-version resume is not certified.
