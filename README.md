# StockAI — Institutional Multi-Tech Hybrid Engine (V6.1)

> **Hinglish, chhota sa:** ye repo ek NSE stock-analysis engine hai (TradingView → NSE → Yahoo
> 3-tier data, ML ensemble, 6 scoring engines, dashboard). **V6.1 me ek deep audit ke 15 defects
> fix kiye gaye hain** — sab ek regression suite se verified. Do cheezein **jaan-boojh kar open**
> chhodi gayi hain kyunki wo bug nahi, research problems hain: **ML ka measurable edge nahi hai**
> aur **score thresholds calibrated nahi hain**. Details: [`AUDIT_REPORT.md`](AUDIT_REPORT.md).

[![verified](https://img.shields.io/badge/regression%20suite-31%20passed%20%2F%200%20failed-brightgreen)](#verification)
[![python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)]()

---

## ⚠️ Read this before you trade anything

| | |
|---|---|
| ✅ **Data layer** | 3-tier fallback (TradingView → NSE direct → Yahoo), JSON-safe, degrades gracefully |
| ✅ **Indicator layer** | ATR/RSI match reference math exactly; SuperTrend 97.4 % faithful to the canonical Pine algorithm |
| ⚠️ **ML layer** | **No measurable edge.** 7,200 out-of-sample predictions across 20 Nifty names: mean accuracy **50.7 % vs a 51.9 % baseline (edge −1.1 pp)**. Treat ML probabilities as research output, not advice. |
| ⚠️ **Scoring layer** | The six engines are **not on a common 0-100 scale**, so the master score sits near 42 and the label bands (65/78) are effectively unreachable. `ensemble_v2` is a diagnostic shim, **not** a calibrated model. |
| ⚠️ **Execution** | `qty`/`notional` are now capped at 1× capital, but this is **not** a backtested system. No slippage, no costs, no walk-forward P&L. |

**None of this is investment advice.** Use it as a research dashboard, not an order generator.

---

## Quick start

```bash
git clone https://github.com/Vishal3546/StockAI.git && cd StockAI
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

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
│    ├─ 6 engines             Volume Profile · RVOL/CVD/VSA · VCP · SMC/ICT · Regime · Multi-TF
│    ├─ ensemble_score()      weighted blend  (+ ensemble_v2 diagnostic)
│    └─ calculate_risk()      direction-aware Kelly sizing with a 1× notional cap
├─ nifty_scanner.py ───────── threaded scanner → scan_results.json
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

---

## What V6.1 fixed

All 15 fixes live in the code, marked `FIX-nn` (search for `FIX-`). Full evidence in
[`AUDIT_REPORT.md`](AUDIT_REPORT.md).

### 🔴 Critical
| ID | Defect | Fix |
|---|---|---|
| C-1 | `qty` had **no notional cap** — a ₹1,00,000 account was told to buy 454 RELIANCE = **₹5,39,942 (5.4× leverage)**; Kelly used a hardcoded `b=2.5` while the function's own `rr_ratio` printed `1.0` (true Kelly **negative** = no trade); a SHORT_SELL verdict still produced a **long** plan | direction-aware mirror, `qty ≤ capital/price`, `b` derived from real levels, `kelly ≤ 0 → qty = 0`, `direction=NONE → no trade` |
| C-2 | ML accuracy shown as a **single 80/20 split** ("Best Edge +10 %", "HIGH confidence") while the honest walk-forward number was buried; 8.1 s of retraining per request | ML cached per (symbol, bar) → 5.5 s → 1.5 s warm; `walk_forward_accuracy`, `walk_forward_edge` and the ±1σ window band are now in the payload **and** the UI, with a plain-language verdict (`NO EDGE (below baseline)`) |
| C-3 | 0 BUYs possible by construction (engine scales incompatible; measured mean 42.4, max 56 over 10 symbols) | `ensemble_v2` diagnostic re-centres VCP (+20) and normalises MTF by loaded timeframes — explicitly labelled *uncalibrated* |

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

## Still open (honest list)

1. **ML edge.** The target is 1-day direction with raw indicator features. Realistic next step:
   volatility-adjusted multi-day label, purged/embargoed CV, a handful of decorrelated features,
   and a rolling ≥500-prediction study before any accuracy number is shown.
2. **Score calibration.** Thresholds (65/78) are hardcoded on an arbitrary scale. Fit them on the
   score's own 1-year distribution (e.g. BUY = 80th percentile). Volume Profile returns only
   `{35,50,70,75}` and Market Regime is constant across stocks — both need redesign, and Market
   Regime belongs in exposure sizing, not in a stock's rank.
3. **Ops.** `CORS(*)` with no auth/rate-limit (do not expose the port), NSE master-list parse
   survives only because the CSV header really is `' SERIES'`, network call at import time,
   `innerHTML` search rendering.
4. **Backtesting.** There is no slippage/cost-aware backtest. Without it, none of the scores can
   claim predictive value.

---

## License / disclaimer

Provided as-is for research and education. Market data belongs to its respective sources
(NSE, TradingView, Yahoo Finance). **Not investment advice.**
