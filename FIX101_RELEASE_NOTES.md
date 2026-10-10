# FIX-101 — targeted TradingView history protocol hotfix

Date: 10 October 2026 (IST)  
Base: `2f153d95282799be3dfebed0e01f871ee903eeb7` (public FIX-100 main)

## Evidence and purpose

The user's Windows A/B run completed **2/8** original history requests versus **8/8** chart-only requests. All original requests recorded quote-channel protocol errors; none of the chart-only requests did. Failed requests included `unknown_session_id` for `quote_fast_symbols` and a disconnect. A sandbox comparison independently reproduced that failure: original **7/8**, chart-only **8/8**.

This supports isolating historical chart retrieval from unnecessary quote subscriptions. It does not establish the provider's exact server-side reason for rejecting a quote session, guaranteed future uptime, or any real-time data entitlement.

## Changes

- New `tv_history.HistoryDatafeed` is an instance-local subclass of the pinned `tradingview-datafeed==2.1.1` client.
- It omits **only `quote_*` messages** from the history client. Authentication, chart-session creation, symbol resolution, requested exchange, interval and bar count still go through the upstream implementation.
- The subclass preserves optional constructor credentials; it does not bypass authentication or permissions. No credentials are needed or included in this release.
- History calls on one client are serialized. The socket is closed in `finally` on success and failure.
- Empty/failed history requests produce a concise adapter warning instead of implying the symbol is unlisted. Raw exception text, tokens and headers are not logged by the adapter.
- App initialization now says the chart-only client was initialized, not that a provider connection or live-quote subscription was verified.
- The application, deep analyzer, scanner, research loader and the two direct TradingView study scripts use the adapter. Historical patch-generator scripts are not runtime entry points and were not rewritten.
- No `site-packages` modifications, global library monkey patches, dependency upgrades, rate changes or recalibration are needed.

## Scope — intentionally not an all-bugs fix

This patch **does not fix** the following separately identified problems:

1. Stale requested-exchange history can be selected inconsistently depending on other-exchange availability.
2. An old browser price stream can still update after a failed symbol/exchange switch.
3. The upstream parser's timezone-naive timestamps depend on the host timezone; adapter-boundary normalization is still required.
4. Yahoo BSE's missing 9 October Close is an upstream-data issue; this patch does not synthesize or fill that candle.

The existing strict return gates and research-only executable sizing remain unchanged. Do not infer that this targeted protocol hotfix certifies complete NSE/BSE UI isolation. Those high-priority follow-ups still require separate implementation and regressions.

The subclass overrides a private upstream message hook. That is deliberate and narrowly tested against the pinned version; retest before changing the dependency or substituting a fork. There are no automatic repeated retries and no promise of always-available provider data.

## Verification

**39/39 verifier scripts passed**, including **13 new adapter tests**. Patched-app live checks passed for **8/8** requested symbol/exchange/interval history combinations, and both TCS/JIOFIN BSE stock routes returned 200 with BSE frame identity and zero executable quantity. See `FIX101_VERIFICATION.json` for results. The new deterministic tests cover quote-message suppression, preserved auth/chart commands, credentials forwarding, exact argument forwarding, unchanged upstream class, socket cleanup, error behavior, concurrency serialization and runtime wiring.

The user's Windows A/B result validates the chart-only approach there; it is **not** a native Windows run of every production hotfix entry point. Release tests run in the sandbox. Some inherited verifier scripts use the network.

## Apply

Back up user data, stop the running application with Ctrl+C, and use a clean local `main` containing the stated FIX-100 base. Do not reapply the old FIX-100 patch.

```powershell
git am --3way "$env:USERPROFILE\Downloads\StockAI_fix101_tradingview.git-am.patch"
git push origin main
```

Run the push only if application succeeds. Then restart `python app.py`; an already-running Python process will not pick up the new adapter. No authenticated remote push is performed by the assistant.

On restart, the new initialization message mentions **chart-only client initialized**. A successful request should then report the actual provider/exchange/session. If a request still fails, retain the new adapter warning; do not relax freshness checks, paste secrets, or treat a failure as proof of an unlisted stock.
