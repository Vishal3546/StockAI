# StockAI — Institutional Multi-Tech Hybrid Engine (V6.1)

> **Hinglish, chhota sa:** ye repo ek NSE stock-analysis engine hai (TradingView → NSE → Yahoo
> 3-tier data, ML ensemble, 6 panels, dashboard). V6.1 ke audit me 15 defects fix huye.
> **FIX‑31/32/33 (2026‑10‑01):** Kelly ka p plan-data se measure hota hai; missing values null;
> score *distribution* par 250-session percentile bands fit hote hain (pehle 65/78 hardcoded the).
> **Lekin ML aur fitted rank me proven profit edge ABHI BHI NAHI hai.** Original audit historical
> snapshot hai; [audit addendum](AUDIT_REPORT.md#fix-33-addendum--2026-10-01) dekhein.

[![verified](https://img.shields.io/badge/regression%20suite-31%20passed%20%2F%200%20failed-brightgreen)](#verification)
[![python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)]()

---

## ⚠️ Read this before you trade anything

| | |
|---|---|
| ✅ **Data layer** | 3-tier fallback (TradingView → NSE direct → Yahoo), JSON-safe, degrades gracefully |
| ✅ **Indicator layer** | ATR/RSI match reference math exactly; SuperTrend 97.4 % faithful to the canonical Pine algorithm |
| ⚠️ **ML layer** | **No measurable edge.** 7,200 out-of-sample predictions across 20 Nifty names: mean accuracy **50.7 % vs a 51.9 % baseline (edge −1.1 pp)**. Treat ML probabilities as research output, not advice. |
| ⚠️ **Scoring layer** | FIX‑33: only **4 stock-specific daily engines** enter the master score; Market Regime (former 18%) moves to *exposure*, intraday MTF remains a diagnostic. `score_calibration.json` fits **p80 / p95** on **250 completed sessions / 7,250 observed scores / 29 of 30 NSE names**, as-of 2026‑09‑29: cutoffs **57 / 62** on THIS score definition. Due to integer-score ties, ≥p80 includes **22.1%** and ≥p95 **6.4%** of that historical sample (not exact 20%/5% quotas). **Relative ranking only**, not a prediction of profitable returns. |
| ⚠️ **Execution** | Live API blocks assumed-Kelly sizing when the measured plan sample is missing, applies a separately labelled regime exposure cap, and fails closed if history/index data are missing. `qty` ≤ 1× capital, **but** overlapping in-sample plan samples + fitted percentiles are **not** an out-of-sample cost-aware strategy backtest. |

**None of this is investment advice.** Use it as a research dashboard, not an order generator.

---

## Quick start

```bash
git clone https://github.com/Vishal3546/StockAI.git && cd StockAI
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python tools/build_score_calibration.py  # update 250-day history when >10 calendar days old
python app.py            # dashboard + API  ->  http://127.0.0.1:5000/
```

Then, optionally:

```bash
python nifty_scanner.py                  # scans 30 Nifty names  -> scan_results.json
python deep_analyzer.py RELIANCE         # quant risk + ML report -> deep_RELIANCE.json
python tools/verify_fixes.py             # regression suite (31 checks) + regenerates artefacts
```

**`requirements.txt` note.** The original pinned `tvdatafeed==2.1.0`, which **does not exist on
PyPI** — `pip install -r requirements.txt` failed with
`ERROR: Could not find a version that satisfies the requirement tvdatafeed==2.1.0`. The module
`tvDatafeed` ships as `tradingview-datafeed` (used here) or from
[GitHub](https://github.com/rongardF/tvdatafeed). `numpy==1.26.4` / `xgboost==2.1.2` have no
cp313 wheels — the file documents that too.

---

## Architecture

```
┌─ Dashboard.html ─────────── glassmorphism UI, TradingView Lightweight Charts (vendored)
│                             same-origin API, SSE live ticks (polling fallback)
├─ app.py ───────────────────  Flask API
│    ├─ resolve_symbol()       ranked, word-boundary name → ticker resolver
│    ├─ MultiTechDataSourceManager
│    │      Tier 1  TradingView (tvdatafeed)      ~0.45s/symbol, one shared socket
│    │      Tier 2  NSE direct (jugaad-data / nsepython / native session)
│    │      Tier 3  Yahoo Finance (yfinance)
│    ├─ fetch_nse_live_ltp()   realtime LTP when NSE is reachable (+negative cache)
│    ├─ calculate_all_indicators()  20+ indicators (ATR/RSI/MACD/BB/ADX/VWAP/Ichimoku…)
│    ├─ ml_engine()            28 features · GB+RF+LR(+XGB) · expanding-window walk-forward
│    ├─ 6 engine panels       Volume Profile · RVOL/CVD/VSA · VCP · SMC/ICT · Regime · Multi-TF
│    ├─ ensemble_score()      4 DAILY stock engines + p80/p95 history fit (v2 diagnostic)
│    └─ calculate_risk()      measured plan → Kelly; regime exposure cap; 1× notional
├─ score_calibration.py ────── shared score formula, universe + strict history validator
├─ score_calibration.json ──── past 250 sessions; rebuild via tools/build_score_calibration.py
├─ nifty_scanner.py ───────── SEPARATE ML-composite scanner → scan_results.json
├─ deep_analyzer.py ───────── quant risk (Sharpe/Sortino/Calmar/VaR), sector RS & beta
└─ tools/ ─────────────────── reproducible fix + verification scripts
```

### API

| Endpoint | Purpose |
|---|---|
| `GET /` | dashboard (`Dashboard.html`) |
| `GET /api/stock/<SYMBOL>` | full payload: engines, KPIs, ML, risk, patterns, 150-bar chart |
| `GET /api/stock/<SYMBOL>?fast=1` | same, ML block skipped (instant render) |
| `GET /api/quote/<SYMBOL>` | lightweight LTP tick |
| `GET /api/stream/<SYMBOL>` | Server-Sent Events tick stream |
| `GET /api/search?q=…` | autocomplete over 2,565 NSE equities |

### FIX‑33 · Fitted distribution ≠ validated trading edge

`score_calibration.json` stores **each actual stock score by past session** (250 dates, >=20
observed stocks/date). Build script downloads 3y Yahoo daily OHLCV for the *same* 30-name universe,
then computes the exact live daily stock-engine formula on rolling 250-bar windows **as of each
completed date**. It excludes current analysis day from the fit, and never substitutes the
scanner's separate ML `composite` or invents historical intraday MTF bars. The four ranking
weights (0.12/0.20/0.15/0.15) are renormalised; market regime gets **0% stock-rank weight**.
MTF and regime still appear in the six UI panels. Regime now applies a clearly labelled
**directional exposure policy** *after* Kelly/notional caps: e.g. BULL → LONG ×0.75,
STRONG BEAR → LONG ×0/SHORT ×0.50; UNKNOWN → no live position. These factors are **policy,
not backtested alpha**.

`ensemble.calibration` exposes `ready`, `asof_session`, `reference_session`, `sessions`, `samples`,
`thresholds`, `relative_rank_pct` and an explicit status note. If history is invalid/stale or
stock/engine data are insufficient **or the stock daily feed is labelled STALE**, the raw score
may show but **BUY/SHORT labels and sizing are blocked**. **Out-of-universe names (2,500+ searchable stocks) and the one missing-history name
are NOT fitted**: they show the raw score only, never inherit NIFTY percentiles by accident.
Live API also blocks the fallback assumed Kelly p when the measured plan sample is
missing. `tradeable` requires p95 rank + measured plan break-even check + nonzero regime-capped qty,
**not** a proof of positive out-of-sample net returns. `nifty_scanner.py` signals use a separate
formula and keep their own bands.

**Refresh:** run `python tools/build_score_calibration.py` at least every 10 calendar days; this
artifact is as-of 2026‑09‑29 and will intentionally become stale. If market is open, the builder
skips today's partial candle *and* the current ranked completed session to avoid same-day leakage.
It writes atomically, validates formula hash/weights/universe and refuses an incomplete rebuild.
Verify offline: `python tools/verify_score_calibration.py` (64 checks) plus existing suites.

---

## What V6.1 fixed

All 15 fixes live in the code, marked `FIX-nn` (search for `FIX-`). Full evidence in
[`AUDIT_REPORT.md`](AUDIT_REPORT.md).

### 🔴 Critical
| ID | Defect | Fix |
|---|---|---|
| C-1 | `qty` had **no notional cap** — a ₹1,00,000 account was told to buy 454 RELIANCE = **₹5,39,942 (5.4× leverage)**; Kelly used a hardcoded `b=2.5` while the function's own `rr_ratio` printed `1.0` (true Kelly **negative** = no trade); a SHORT_SELL verdict still produced a **long** plan | direction-aware mirror, `qty ≤ capital/price`, `b` derived from real levels, `kelly ≤ 0 → qty = 0`, `direction=NONE → no trade` |
| C-2 | ML accuracy shown as a **single 80/20 split** ("Best Edge +10 %", "HIGH confidence") while the honest walk-forward number was buried; 8.1 s of retraining per request | ML cached per (symbol, bar) → 5.5 s → 1.5 s warm; `walk_forward_accuracy`, `walk_forward_edge` and the ±1σ window band are now in the payload **and** the UI, with a plain-language verdict (`NO EDGE (below baseline)`) |
| C-3 | Original six-engine score mean 42.4, 65/78 BUY bands seldom reachable | FIX‑33: daily four-engine *rank* fitted against 250 prior universe sessions: p80=57 / p95=62 as-of 2026‑09‑29. No forward-return proof. `ensemble_v2` remains a separate uncalibrated diagnostic. |

### 🟠 High
| ID | Defect | Fix |
|---|---|---|
| H-1/2 | Tier-2 Method A read `gRapData`; the API returns **`grapthData`** → dead code. It also bolted `Open=High=Low=Close`, `Volume=100000` onto a **single intraday session** | correct key + intraday-only payloads refused; `nsepython.equity_history` called with its real signature |
| H-3 | "NSE REALTIME TICK" badge while NSE returned 403 and the feed was 15-20 min delayed | badge now says `DELAYED (15-20 min)`; `is_realtime` flag + disclaimer in the payload |
| H-4 | beta/correlation were **NaN** (TradingView `03:45:00` vs Yahoo midnight → 0 overlapping rows) and `beta_type` silently defaulted to `"Market"` | indexes date-normalised; beta/corr finite-checked (`RELIANCE: beta 1.053, corr 0.624, 124 aligned sessions`) |
| H-5 | "6M Relative Strength" compared a **735-day** stock return with a **184-day** index return (`period` ignored on the TV path) | `_normalise()` honours `period` for both legs (`182d vs 182d`) |
| H-6 | `deep_*.json` contained literal `NaN` → breaks `JSON.parse` | strict-JSON writer (`allow_nan=False`) |
| H-7 | dividend yield ×100 → **"50.00 %"** for RELIANCE | plausibility clamp → `0.50 %` |
| H-8 | `VWAP` was a **2-year cumulative** series but drove the *intraday* KPI | 20-session rolling VWAP (`VWAP_CUMULATIVE` kept for reference) |
| H-9 | resolver returned **HCL-INSYS for "INFOSYS LTD"** ("INFOSYS" ⊂ "HCL INSYSTEMS"), `TATA → TATACAP` | ranked, word-boundary, shortest-name matching |
| H-10 | scanner RVOL was **0.21-0.92 for every stock** (in-progress session ÷ full-day average) | RVOL from the last completed session → `0.80-15.98, mean 2.30`; `ml_effective` now shows the probability the composite actually used |

### 🟡 Medium
`GET /` served nothing (500) → serves the dashboard · every 404 became a 500 → real JSON 404s ·
SSE dead code → hardened + wired into the UI · unknown symbol 15 s → cached (0.002 s) ·
`requirements.txt` uninstallable → fixed · favicon unwired → served · chart lib CDN-only → vendored
· NaN sanitised to `0.0` → `null` (no more fake "DEATH CROSS" from a missing SMA-200).

---

## Verification

```bash
$ python tools/verify_fixes.py
 RESULT: 31 passed, 0 failed
```

The suite exercises routes, resolver, live payload, sizing invariants, ML caching, artefact
regeneration and strict-JSON output. Two generator scripts keep the fixes auditable:

```bash
python tools/apply_v61_fixes.py     # in-place patches, each anchor asserted
python tools/patch_dashboard.py     # front-end patches, each anchor asserted
```

Both abort on a changed anchor instead of producing a half-applied patch.

---

## Research: is the ML edge real? → [`RESEARCH_REPORT.md`](RESEARCH_REPORT.md)

That question is now **answered with evidence**, not opinion: a cost-aware study in `research/`
(20 NSE symbols, purged walk-forward, execution lag = 1 bar, real delivery costs of **0.3276%**
round trip, shuffled-label permutation null) ran **17,885 out-of-sample predictions** over 5 years.

**Result: the ML has no measurable edge** — 50.79% accuracy vs 51.26% majority-class baseline
(±0.73 pp), i.e. a coin flip; and it pays ₹46,755 in costs on ₹1 L of capital (137 round-trips,
≈12 pp/year) versus +32.5% for buy & hold. The one structural fix that helps is a **turnover cap**
(minimum-hold 5 bars): +18.7 pp, but still no profit. Reproduce with:

```bash
python3 research/run_study.py --period 5y && python3 research/analyze.py 5y
```

---

## Still open (honest list)

1. **ML edge.** Measured, and it isn't there (see [`RESEARCH_REPORT.md`](RESEARCH_REPORT.md)).
   The proposed redesign (volatility-adjusted multi-day label + decorrelated features + purged CV)
   was *also* tested and **also failed** (−7.2 pp vs baseline, 0/20 symbols positive). Until a
   design shows a positive edge with CI, ML stays advisory-only — no accuracy number in the UI
   without a ≥500-prediction rolling sample behind it.
2. **Score *performance* validation (still OPEN).** FIX‑33 fits score DISTRIBUTION, not expected
   future return. At 2026‑09‑29: p80/p95 = 57/62 on 7,250 observed daily-engine scores. Changes to
   formula/weights/universe, <250 completed sessions, missing stock data, same/future-date fit or
   >10-day-old fit disable directional labels. Rebuild with
   `python tools/build_score_calibration.py`; script uses Yahoo daily OHLCV and excludes the live
   bar (and yesterday if today's bar is in progress). **Scanner `composite` is a different model**;
   never fit these cutoffs on `scan_results.json`. Remaining research: cost-aware OOS returns, CI,
   conditional plan hit-rate and market-regime exposure policy validation. Neither percentile rank
   nor a 1-sigma bound on overlapping, unconditional setups proves profitable trading.
3. **Ops.** `CORS(*)` with no auth/rate-limit (do not expose the port), NSE master-list parse
   survives only because the CSV header really is `' SERIES'`, network call at import time,
   `innerHTML` search rendering.
4. **Backtesting.** ✅ A slippage/cost-aware backtester now exists (`research/backtest.py`,
   5/5 invariants passing: zero-cost B&H ≡ price return, no look-ahead, costs bite). It is a
   *research* tool — it is not yet wired into the app's scoring path, and no score in the UI
   has been validated with it.

---

## License / disclaimer

Provided as-is for research and education. Market data belongs to its respective sources
(NSE, TradingView, Yahoo Finance). **Not investment advice.**
