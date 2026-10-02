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
| ⚠️ **Scoring layer** | FIX‑33: only **4 stock-specific daily engines** enter the master score; Market Regime (former 18%) moves to *exposure*, intraday MTF remains a diagnostic. `score_calibration.json` fits **p80 / p95** on **250 completed sessions / 7,500 observed scores / 30 of 30 NSE names**, as-of 2026‑09‑29: cutoffs **57 / 62** on THIS score definition. Due to integer-score ties, ≥p80 includes **22.1%** and ≥p95 **6.4%** of that historical sample (not exact 20%/5% quotas). **Relative ranking only**, not a prediction of profitable returns. |
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
python tools/verify_fixes.py             # regression suite (53 checks) + regenerates artefacts
```

**`requirements.txt` note (FIX‑42).** Every pin now has to ship a **binary wheel for cp312,
cp313 *and* cp314** — otherwise `pip` silently compiles C from source (slow, needs a
toolchain). That evidence is committed in `requirements.lock.json` and checked by
`tools/verify_dependency_pins.py`. Three old pins were wrong, all measured on 2026‑10‑01:

| old pin | measured problem | now |
|---|---|---|
| `numpy==1.26.4` | cp312 wheels only → 3.13/3.14 compile from source | `numpy==2.3.5` |
| `pandas==2.2.3` | no cp314 wheel | `pandas==2.3.3` |
| `scikit-learn==1.5.2` | no cp314 wheel | `scikit-learn==1.7.2` |
| `yfinance==0.2.44` | installs, but **dead against Yahoo**: `yf.download("RELIANCE.NS", period="6mo")` → 0 rows, `JSONDecodeError` → `/api/stock` answered *"All 3 engines failed"* | `yfinance==1.7.0` |

`xgboost` needed no change of kind: its wheels are `py3-none-win_amd64` / `py3-none-manylinux*`,
i.e. binary but with no CPython‑ABI constraint, so the old "no cp313 wheels" worry was a
false alarm (2.1.4 → fit+predict verified). And `tvdatafeed` is still **not on PyPI** — the
module ships as `tradingview-datafeed`. Re-check the wheel evidence any time with:

```bash
python tools/build_requirements_lock.py
```

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

**Engine weight audit (FIX-40):** artifact ab har session/symbol ke per-engine scores
bhi rakhta hai, isliye composite offline recomputable hai (`verify_engine_history()` = 0
mismatch). Weight/redesign ka koi bhi faisla pehle measure karein:

```powershell
python tools\analyze_engine_dispersion.py
```

Ye cross-sectional spread, time-series spread, composite se Spearman rho aur drop-one
simulation (band change %) print karta hai — bina network ke. Current evidence par
weights unchanged hain: koi engine cross-sectionally flat nahi hai.

**Security (FIX-35) — server chalane se pehle:**

| Env var | Default | Kaam |
|---|---|---|
| `STOCKAI_API_TOKEN` | *(khaali = open)* | Set karo to `/` aur `/api/*` par token zaroori (`X-Api-Key` header, `?token=…`, ya cookie). Warna 401. |
| `STOCKAI_CORS_ORIGINS` | *(khaali = same-origin only)* | Comma-separated allowlist, jaise `https://mystore.example`. Wildcard support nahi. |
| `STOCKAI_RATE_LIMIT` | `240` | Per-IP requests/minute on `/api/*`; `0` = off. 429 + `Retry-After`. SSE exempt. |
| `STOCKAI_HOST` | `0.0.0.0` | Bind address. Sirf apne machine par chalana ho to `127.0.0.1`. |
| `STOCKAI_TRUST_PROXY` | `0` | `1` sirf tab jab sach me reverse proxy ho; warna `X-Forwarded-For` spoof ho sakta hai. |

**Sabse aasaan tarika — `.env` file (FIX-36):** har baar `$env:…` set karne ki zaroorat nahi.

```powershell
Copy-Item .env.example .env
notepad .env
python app.py
```

`.env` repo root me rahti hai, **git me nahi jaati** (`.gitignore` me hai), aur **real
environment variable usse jeetta hai** — yani temporary override ke liye `$env:…` phir bhi
chalega. Koi nayi dependency nahi: loader `app.py` me hi hai (`load_dotenv_file()`).
Values server restart par lagti hain.

PowerShell me token ke saath:

```powershell
$env:STOCKAI_API_TOKEN='koi-lamba-random-string'
python app.py
```

Pehli baar dashboard `http://<host>:5000/?token=<wahi-string>` se kholein — cookie set ho
jayegi, phir normal URL chalega. Internet par port forward karne se pehle token zaroori hai.
Verify: `python tools\verify_security.py` (126 checks) aur `node tools/verify_xss_render.js`
(14 checks, jsdom me asli injection attempt).

**Startup + offline (FIX-39):** NSE master list on-disk cache (`nse_master_cache.json`,
24 h TTL) se aati hai — import par network call nahi. Cache stale ho to purani list
turant use hoti hai aur refresh background me chalta hai. `.env` me:

```
NSE_MASTER_CACHE_HOURS=24     # (optional) CONFIG me bhi set kar sakte ho
STOCKAI_OFFLINE=1             # network bilkul band — cache/fallback se chalao
```

Pehli baar (cache na ho) curated 30-stock list se shuru hota hai aur poori list
background me aa jaati hai; uske baad har start fast aur offline-safe.

**Windows note (FIX-34):** all verifiers now read source files with explicit
`encoding='utf-8'`, so they run on a cp1252/ANSI default code page. If any tool still
raises `UnicodeDecodeError: 'charmap' codec…`, it is a missing `encoding='utf-8'` on a
`read_text()`/`open()` call — fix that line (or run `python -X utf8 …` as a stopgap).

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
 RESULT: 53 passed, 0 failed
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

### FIX‑41 · The verdict is now recorded, and the dashboard quotes it

The study above lived only in a markdown report while the live dashboard kept showing its own
**in-sample** accuracy. That is fixed: `tools/build_ml_edge_study.py` records the same
purged + embargoed walk-forward (plus the shuffled-label null) into **`ml_edge_study.json`**
at the repo root, `/api/stock` ships it as `ml_study`, and the dashboard prints the recorded
verdict under the ML panel — with the in-app accuracy explicitly labelled a **diagnostic**.

Measured on 20 large-caps, 5y, 5 purged folds, **56,100 pooled out-of-sample predictions**:

| strategy | accuracy | baseline | edge | ±95% CI |
|---|---|---|---|---|
| S1 `ml_dir1_app28` (the app's current design) | 51.03% | 50.07% | **+0.96pp** | ±0.72 |
| S2 `ml_dir1_small10` | 50.58% | 50.07% | +0.51pp | ±0.72 |
| S3 `ml_ret5atr_small10` (proposed redesign) | 55.03% | 62.02% | **−6.99pp** | ±0.71 |
| shuffled-label null ceiling | mean 56.24%, max **59.32%** | — | — | — |

**Verdict: NO EDGE.** The proposed design sits *below* the accuracy you get from shuffled
labels, and the current design's +0.96pp is inside its own ±0.72pp noise band. Rebuild
(~3 min, needs network for daily bars):

```bash
python tools/build_ml_edge_study.py
```

If the artifact is missing the dashboard says *"OOS ML study absent"* — which means
**unverified**, never *"edge found"*. Verify the wiring with
`python tools/verify_ml_edge_study.py` (47 checks).

### FIX‑43 · Scanner signals now come from fitted bands, not hardcoded ones

The scanner's BUY/SELL bands were hardcoded (`70/60/45/35`) and BUY/STRONG BUY sat behind
an ML gate. Measured on **7,500 stock-sessions** (30 symbols × 250 sessions, using the
scanner's own `calculate_ensemble`) the old bands produced:

| signal | old bands | share |
|---|---|---|
| STRONG BUY | **0** | 0.0% |
| BUY | **0** | 0.0% |
| WATCH | 7,162 | **95.5%** |
| SELL | 338 | 4.5% |
| STRONG SELL | **0** | 0.0% |

They were not just uncalibrated — they were **unreachable**. BUY required
`effective_ml_prob >= 52`, but a negative-edge model is neutralised to `50.0`, so the gate
could never pass; and `composite >= 70` needs `ens >= 86.4` while the observed maximum
ensemble score in 7,500 sessions is **66**.

Bands are now fitted percentiles committed in `scanner_bands.json`
(p95/p80/p40/p10 = **63/60/50/44**), ML is a **diagnostic** and no longer gates the signal,
and a missing/stale (>60 days)/tampered artifact makes the signal `UNRATED` rather than a
guess. Rebuild (~10 s, needs network for daily bars) and verify with:

```bash
python tools/build_scanner_bands.py
python tools/verify_scanner_bands.py     # 67 checks
```

**FIX‑44 · the `Score` column now shows the number that actually decides the signal.**
FIX‑43 moved the signal onto `ens` but left the *display* and the *sort* on the legacy
`composite`, so every row contradicted itself (`NTPC Score: 62 → STRONG SELL`,
`KOTAKBANK Score: 47 → BUY`) and the leaderboard ranked high‑ML names on top while their
signal was a SELL band. All three sites now print/sort `signal_score`; the redundant `ENS`
column is gone (it *is* the Score now) and `ML%`/`Edge` stay as diagnostics.
`🏆 Top Validated Buys` became `🏆 Top in BUY band`, because nothing is validated.
Section **[6] display consistency** of the verifier executes the formatting functions on a
synthetic row so this cannot silently come back.

**Fitted percentiles are a relative ranking, not a probability and not profit.** The
live scan on 2026‑10‑01 read 1 BUY | 5 WATCH | 23 SELL (only KOTAKBANK above p80 = 60) —
a legitimate weak-market reading, not a broken band. Net-of-cost performance is in
[`RESEARCH_REPORT.md`](RESEARCH_REPORT.md).

**FIX‑45 · `TATAMOTORS` → `TMPV`, and the summary line is now exact.**
Every scan printed `⚠️ TATAMOTORS Skipped`. The cause was not a Yahoo outage: the ticker
stopped existing. Tata Motors demerged effective 1 Oct 2025 and the NSE ticker became
**`TMPV`** (Tata Motors Passenger Vehicles Ltd, from 24 Oct 2025). All four probes
(`TATAMOTORS.NS`, `TATAMOTORS.BO`, `TATAMOTOR.NS`, `TATAMTRDVR.NS`) return **HTTP 404**.
`TMPV` is the right successor on two counts: under NSE demerger rules the **demerged company
stays in the Nifty 50** (the CV arm `TMCV` is carried at constant price for a few sessions
and then excluded), and it carries the full pre-demerger history — **1,241 bars from
2021‑10‑01**, where `TMCV` has only 225 bars from its 2025‑11‑12 listing. Calibration needs
the long history, so `TMPV` is the drop-in replacement.

The summary line was also silently lossy: it counted with `"BUY" in signal` /
`"SELL" in signal`, so `23 SELL` was really **17 SELL + 6 STRONG SELL** and STRONG BUY never
appeared as its own band. It now prints exact per-band counts
(`STRONG BUY | BUY | WATCH | SELL | STRONG SELL | UNRATED`, non-zero bands only).

Changing the universe invalidates the committed artifacts (the loader fails closed on a
universe mismatch), so all three were rebuilt — calibration **29 → 30 symbols / 7,500
scores**, scanner bands **7,500 stock-sessions / 30 symbols / 0 skipped**, ML study
**20 symbols / 56,100 OOS predictions**. Calibration cutoffs are unchanged (43/50/57/62);
the only scanner band that moved is WATCH 51 → **50**; and the ML verdict is still
**NO EDGE** (+0.96 pp vs a 59.32 % shuffled-label ceiling).

---

### FIX-46 · Pyrefly `bad-unpacking` × 4 in `app.py` (type-check, not a runtime bug)

`app.py` had four `GradientBoostingClassifier(**CONFIG['ML_GB_PARAMS'])`-style calls that
Pyrefly flagged at lines 1443/1469/1476/1483:

> Expected argument after ** to be a mapping, got: `dict[str, float | int] | … | float | int | list[float] | str`

`CONFIG` is a heterogeneous dict, so the checker infers the *whole value union* for any key
and cannot prove the result is a mapping. At runtime all four values really are `dict`s —
the classifiers built fine and the hyperparameters landed unchanged
(`n_estimators=120`, `max_depth=5`, `max_iter=1000`). **Nothing was broken at runtime.**

Reproducing it mattered: sandbox scikit-learn 1.7.2 ships **no `py.typed` marker**, so in
default single-file mode Pyrefly treats sklearn as untyped, never sees `__init__`, and
reports **0 errors**. A `pyrefly.toml` (project mode, so site-packages resolve) plus
`preset = "strict"` was needed — and then the pre-fix code produced **exactly those four
errors on exactly those lines**.

Fix: a validated helper instead of a bare `cast`, so the checker gets a mapping *and* a
mistaken non-dict fails loudly rather than surfacing as a confusing sklearn error:

```python
def ml_params(key: str) -> Mapping[str, Any]:
    if not isinstance(params, dict):
        raise TypeError(f'CONFIG[{key!r}] must be a mapping of hyperparameters, …')
    return params
```

The return type was **measured, not guessed** — under `preset = "all"`, `dict[str, object]`
and `dict[str, int | float | str]` both blow `bad-argument-type` from 5 to **78**, because
`**`-unpacking makes the checker match every value against every constructor parameter.
`Mapping[str, Any]` avoids that.

**Measured, `preset = "strict"`** (the preset that reproduces the reported diagnostics):

| | before | after |
|---|---|---|
| total errors in `app.py` | 262 | **258** |
| `bad-unpacking` | **4** | **0** |
| new error kinds introduced | — | **none** |

`verify_fixes.py` grew 31 → **53 checks**; block `[6]` asserts the `**CONFIG[` pattern does
not come back, that `ml_params` returns the live `CONFIG` dict (not a copy, so mutation
semantics are unchanged), that both guards fire, and that the three sklearn classifiers
build with parameters identical to the old call.

---

### FIX-47 · freshness guard no longer switches itself off when the market closes

A real server log showed the bug:

```
⚡ [NSE Official Direct] TCS · 345 bars · market closed, last bar 45.6h old (fine)
🌐 [Yahoo] ^NSEI (1d) · 496 bars · market closed, last bar 16.1h old (fine)
```

Yahoo actually had TCS's bar for **2026-10-01** (measured, 16.2h old) — so the current
session *was* available, but the NSE-direct tier was serving a bar **29.5h older** (at least
one full session behind), and `frame_is_fresh()` waved it through. Its first branch was:

```python
if not open_now:
    return True, f'market closed, last bar {human} old (fine)'
```

An extreme test showed how deep that went: a frame **10 days old** was also reported
`fresh=True → "fine"`. The comment claimed *"market closed, so nothing new exists"* — that
reasoning was wrong. When the market is closed there is still a **latest completed session**,
and a bar older than it really is stale.

**Fix:** when the market is closed, compare at *session* level instead of applying the
minute-level limit (which would flag everything stale every evening after close).
`last_completed_session()` finds the most recent weekday whose cash session has completed and
skips weekends itself; `CLOSED_GRACE_DAYS = 1` absorbs a single market holiday so it does not
raise a false alarm.

Measured at `2026-10-01 16:06` (Thu, closed, latest session 2026-10-01):

| frame | result |
|---|---|
| 2026-10-01 (today's session) | `fresh=True` — fine |
| 2026-09-30 (one behind, grace) | `fresh=True` — fine |
| **2026-09-29 (the TCS case)** | **`fresh=False` — "STALE … (2d behind)"** |
| 2026-09-21 (10 days) | `fresh=False` — STALE |

End-to-end cascade (fetchers stubbed):

| scenario | outcome |
|---|---|
| NSE-direct 2 sessions stale, Yahoo fresh | NSE **REJECTED** → **Yahoo Finance** served ✓ |
| holiday — all three tiers on one session | `🟡 [NO NEWER SESSION]` honest message ✓ |
| all fresh | first tier wins, no extra fetches ✓ |

There is no holiday calendar, so when all tiers are rejected but **agree on the same
session**, the message is `NO NEWER SESSION` rather than `STALE DATA` — that is "there was no
session that day", not "the feed is broken".

**Two fail-open paths my own test then caught.** Both were the same *swallowed exception*
class:

- `frame_age_minutes()` raised `TypeError` on aware−naive subtraction; `except Exception`
  turned it into `None`, and `frame_is_fresh` read `None` as *"age unknown"* → **FRESH**.
- `is_market_open()` read the caller's `.hour`/`.minute` verbatim, so an aware UTC datetime
  (`10:36Z` = `16:06 IST`, market **closed**) was read as `10:36 IST` → market **open**.

Neither fired in production (`_now=None` everywhere), but both are the same bug shape, so
both now go through `_naive_ist()`. All three spellings of one instant now agree.

### FIX-47 · duplicate request-log lines

Every request printed twice:

```
127.0.0.1 - - "GET /api/stock/RELIANCE HTTP/1.1" 200 -
INFO:werkzeug:127.0.0.1 - - "GET /api/stock/RELIANCE HTTP/1.1" 200 -
```

Reproduced, not guessed: werkzeug's `_log()` adds its own handler the first time it logs *if
no level-handler exists yet*. Afterwards a library (`tvDatafeed` is the suspect) calls
`logging.basicConfig()`, which puts a handler on the **root** logger — and werkzeug propagates
to root. `_configure_werkzeug_logging()` now installs an explicit handler and sets
`propagate = False`. The handler is added deliberately rather than left to werkzeug: if
`basicConfig()` had already run, werkzeug sees a level-handler, adds nothing, and
`propagate=False` alone would silence logging entirely.

Verified: 2 requests → **2 lines**, and token masking still holds (real token printed **0**
times, `token=***` **2** times).

`verify_live_quote.py` grew 38 → **50 checks**. One old check was asserting the *buggy*
behaviour (`'stale bar accept (market closed…)'`), so the verifier had locked the bug in
place; it now asserts the corrected behaviour, plus grace/weekend/timezone cases and the
duplicate-logging guards.

---

### FIX-52 · the price did not match the exchange

The user asked the right question: *"Moneycontrol, NSE, BSE — do these match 100%?"* I had
earlier written off two things as "couldn't verify". Both verified, and the answer was **no**.

Two things unblocked: a **Yahoo crumb** (`fc.yahoo.com` cookie → `/v1/test/getcrumb`) opened
`quoteSummary`, and **BSE's `getScripHeaderData` worked from this sandbox** (NSE returns 403).

**The price was wrong.**

| | app header | Yahoo | **BSE official** |
|---|---|---|---|
| TCS close 01-Oct | ₹2,075.00 | 2075.00 | **₹2,079.30** (−4.30) |
| TCS change | +24.40 (+1.19%) | same | **+29.30 (+1.43%)** |
| RELIANCE close | ₹1,167.70 | 1167.70 | **₹1,166.00** (+1.70) |

**Cause: NSE's Closing Auction Session.** From Aug 3 2026, F&O stocks stop continuous trading
at 15:15 and the CAS runs 15:15–15:35 to set the official close. Yahoo's `regularMarketTime`
was **exactly 15:15:00 on both stocks** — the last continuous trade, not the official close.
*(Inference, not measured — I didn't inspect Yahoo's upstream. But both stocks landing on
15:15 exactly points there.)*

**ROE was off by 100×.** Yahoo's `returnOnEquity` raw is `0.47743`, `fmt` "47.74%". The
dashboard showed **"0.48%"**. FIX-09's comment assumed `dividendYield` and `returnOnEquity`
both arrive as percentages — true for the first (3.17), false for the second. The heuristic
was wrong in **both** branches. Everything else matched Yahoo exactly: P/E, P/B, D/E, market
cap, and market cap ÷ price = `sharesOutstanding` (TCS 3,618,087,518 · RELIANCE 13,532,472,634).

**A duplicated constant.** Changing `SESSION_CLOSE_HM` did nothing, because `is_market_open`
had its own literal `15 * 60 + 40` and never read it. The comment even claimed they matched.
A new test caught it (15:36 expected closed, got open). One source now.

**Fixed:** close 15:40 → 15:35 (CAS) with `is_market_open` reading the constant · ROE ×100
(TCS now **47.74%**, live-verified) · `/api/stock` exposes `frame_close` + `price_basis` ·
`checkPriceGap()` warns when the header price and the analysis price differ by >0.25%, showing
both instead of silently picking one (it will fire **+₹4.30** on the user's dashboard) · search
bar renders the `(NSE)`/`(BSE)` suffix that `/api/search` was already sending.

**⚠️ A correction to something I claimed earlier.** I wrote that the analysis block used
₹2,079.30 "which matches BSE exactly — so TradingView matches BSE and Yahoo doesn't". That was
an inference from a single data point and it was **wrong**. A live run in this sandbox, with
`tvDatafeed` installed and 300 bars, returned close **2075.00**, 52W **3350.0/1976.8**, ATR
**58.79** under the same `'TradingView Direct'` label — identical to Yahoo, not to BSE. The
user's machine produced 2079.30 under that same label. **The source label is not a reliable
indicator of where data came from** — the same class of problem FIX-50/51 fixed elsewhere.

**Why BSE was not made a live tier.** `getScripHeaderData` worked, then returned **403 "Access
Denied"** from Akamai after ~40 requests. The dashboard polls every 2 s, so BSE would block
within minutes. The scrip master (`getScripList`, `scripmasterdata`), the search API, and the
bhavcopy URLs are all blocked or return an SPA shell, so symbol → scripcode can't be resolved
dynamically either. BSE data is genuinely valuable — it's what revealed Yahoo is 4.30 off —
but as an on-demand check, not a polling tier.

`verify_live_quote.py` 141 → **160 checks**; regression **801 passed, 0 failed**.

---

### FIX-51 · one feed state instead of two arguing badges

The user pasted their whole dashboard — 02-Oct-2026 10:31, a market holiday — and it showed
**two contradictory badges on one screen**:

```
DELAYED (15-20 min)                          ← top badge
₹1,167.70   LIVE   yahoo.ns · 10:31:31       ← next to the price
```

**FIX-50 was incomplete.** I had fixed the source-name bug at `app.py` L3585 and called it
done, but `Dashboard.html` L1111 had its own copy that I never looked at:

```js
const isLive = /NSE/i.test(src) && !/TradingView/i.test(src);   // src = 'Yahoo Finance'
liveBadgeText.textContent = isLive ? 'NSE LIVE' : 'DELAYED (15-20 min)';
```

Three separate problems:

1. Liveness came from the source **name**, so `'Yahoo Finance'` always meant DELAYED
2. `"DELAYED (15-20 min)"` was a **hardcoded lie** — the real staleness was 1175 min (19.6 h),
   and the market was closed anyway
3. `setLiveChip` did `const t = new Date().toLocaleTimeString(...)` — the **browser's clock**
   standing in for the quote's time. `10:31:31` was when the JS ran; the quote was from
   `2026-10-01 15:15`. Same `datetime.now()` lie FIX-49 removed server-side, still alive
   client-side.

**The server now sends one state.** `feed_state` ∈ `{LIVE, DELAYED, CLOSED}`, plus
`feed_label`, `market_open`, `quote_age_min` and `quote_time`. The UI no longer guesses.
`CLOSED` had to be a third state because after FIX-50 a holiday quote counts as *fresh* (it
matches the last completed session) — correct for the data, but "LIVE" would be a lie when
nothing is trading. The label carries the **real** number; no hardcoded band.

Measured, real `/api/quote/RELIANCE` on the holiday:

```
feed_state    = 'CLOSED'
feed_label    = 'MARKET CLOSED (holiday)'
quote_age_min = 1175.5
quote_time    = '2026-10-01 15:15:00'
```

**Four label bugs found on the same pass.** The user asked whether the numbers were right, so
every figure was recomputed — the **arithmetic was all correct** (change%, 52-week position,
SL = 2.5×ATR, T1 = exactly 1R, breakeven, win-rate lower bound, Kelly clamp, master average,
model edges, OOS deltas). The labels were not:

- **"Master" meant two different things.** The card's `kpi.master.score` was 42 (the average of
  intraday/swing/long-term); the verdict's "Master Score: 37/100" was `ensemble.score`
  (confirmed 37 from the API). Now the verdict says "Ensemble rank".
- **OBV's "0" was never zero.** `(ind.obv > 0 ? … : '0')` turned *any negative value* into "0"
  — measured `obv = -381723268`. The ACCUMULATE/DISTRIBUTE label also used `obv > 0` while the
  backend scores `obv > obv_ema` (`-343805114`). UI and scoring disagreed. Both now use
  `obv_ema`, and negatives display properly.
- **Bollinger %B = 0.04 said "MID"** — only `< 0` and `> 1` were flagged, so a price sitting on
  the lower band read as middle while RSI, CCI, Williams %R and StochRSI all said OVERSOLD.
  Now: BELOW LOWER / NEAR LOWER / MID / NEAR UPPER / ABOVE UPPER.
- **Debt/Equity 36.7** is yfinance's `debtToEquity`, which is a **percentage**. Shown without
  `%` it reads as 36.7×. Now `36.7% D/E`. The falsy check also meant a genuine `0.0` D/E
  displayed as `N/A`; it's now `is not None`.

The tier-3 daily-close fallback's `datetime.now()` timestamp went too — it now reports the
frame's actual last bar.

**Calendar and timings, both verified correct** (the user asked): 16 holidays, all weekdays,
matching NSE's 2026 list exactly; `09:14 → False`, `09:15 → True`, `15:40 → True`,
`15:41 → False`. NSE's regular session ends 15:30 and the code runs to 15:40 **deliberately**,
to cover the closing auction — conservative in the right direction.

`verify_live_quote.py` grew 114 → **141 checks**; `verify_dashboard_display` still 28/28.

---

### FIX-50 · the app now knows what a market holiday is

**The user caught this one, not me.** Shipping FIX-49, I wrote "market open for 16 minutes"
about 02-Oct-2026 09:31 IST. Their reply: *"today market me holiday he."*

They were right. 2 October 2026, Friday, is **Mahatma Gandhi Jayanti** — NSE and BSE closed all
day. So my premise was wrong, and that also meant the "stale" reading I had measured was
**correct data**: there is no trading today, the last quote *should* be 01-Oct 15:15, and it
was. Measured before the fix:

```
2026-10-02 09:31 Fri (HOLIDAY)
   is_market_open()  = True                            ← wrong
   quote_is_fresh()  = 'STALE: quote 1096m purana'     ← FALSE POSITIVE
```

The root cause: `is_market_open()` only checked `weekday <= 4` and `09:15–15:40`. **There was
no concept of a holiday at all.** That hit FIX-49 and FIX-47 both — `last_completed_session()`
treated a holiday as an ordinary weekday, so it reported the wrong "expected session" (Monday
05-Oct expected `2026-10-02`, which was a holiday). `CLOSED_GRACE_DAYS = 1` rescued the
verdict, but the reason string was a lie.

**Fix A — a holiday calendar.** `NSE_HOLIDAYS` holds NSE equity + equity-derivatives' **16
weekday trading holidays for 2026**. Two ways to extend it *without a patch*:

- `STOCKAI_EXTRA_HOLIDAYS=2027-01-26,2027-03-23` (works from `.env`)
- an optional `nse_holidays.txt` in the repo root, one ISO date per line, `#` comments allowed

That escape hatch matters because **15-Jan-2026 was never in the original calendar** — NSE
added it by circular on 12-Jan-2026 for the Maharashtra municipal elections. A calendar can
always be out of date. If it is, the app does not crash: it degrades to the weekday rule (today's
behaviour) and prints a startup warning.

```
👉 NSE holidays: 16 dates loaded for 2026 (aaj HOLIDAY — market band)
```

Measured after the fix:

| case | `is_market_open` | `last_completed_session` | `quote_is_fresh` |
|---|---|---|---|
| HOLIDAY 02-Oct 09:31 | **False** | 2026-10-01 | **FRESH** — `market closed, quote 2026-10-01 (latest 2026-10-01) — fine` |
| normal Mon 05-Oct 09:31 | True | **2026-10-01** (holiday skipped) | STALE for an 18.3h-old quote |
| Mon 09:31, quote 30s old | True | — | FRESH |

The holiday false positive is gone **and the gate is exactly as strict on a normal day**.

One subtlety worth recording: `2026-08-15` (Independence Day) falls on a **Saturday**, so it is
not in the 16-date weekday list — but the market is still closed that day, via the weekend rule.
Both facts are asserted separately.

**Fix B — `/api/stock` derived `is_realtime` from a source *name*.** This is the leftover I
flagged when shipping FIX-49:

```python
'is_realtime': ('NSE' in str(active_source) and 'TradingView' not in str(active_source)),
```

Nothing to do with freshness — a substring match. Two verified paths where it lied:
`smart_fetch` returns `src + ' (STALE)'` for a stale frame, and `'NSE Official Direct (STALE)'`
still matches `'NSE'`; and `active_source = 'NSE Direct Live'` is set whenever an NSE quote
arrives, stale or not. Now it reuses the FIX-49 gate as the single source of truth, plus a
`realtime_reason` field. Measured on the holiday, real `/api/stock/RELIANCE`:

```
is_realtime     = False
realtime_reason = 'koi live quote nahi — price daily close se'
data_source     = 'Yahoo Finance'
```

**What I got wrong, and what it caught.** My FIX-49 tests used `2026-10-02` as the
"market open" fixture — a day that is not a trading day at all. Adding the calendar broke **11
of my own tests**, which surfaced two design faults: that fixture, and an end-to-end stub test
that **depended on the wall clock** (its verdict would change on a Monday morning or a holiday).
`now` is now frozen (`_FrozenDT` = 01-Oct-2026 09:31, a real trading day), so the test gives
the same answer whenever it runs. Two older FIX-47 tests needed the same treatment — they
treated `2026-10-02` as "the Friday"; the weekend fixture moved to 26/27-Sep, which is not
adjacent to any holiday.

`verify_live_quote.py` grew 86 → **114 checks**.

---

### FIX-49 · a stale price no longer gets a "LIVE" badge

> ⚠️ **Correction, added by FIX-50.** This section says "market open for 8/16 minutes" on
> 02-Oct-2026. That was wrong — it was Gandhi Jayanti, market closed all day. The two bugs
> described here are real (a hardcoded `is_realtime: True`, and `datetime.now()` standing in
> for a missing quote timestamp — on a holiday nothing is live either), but the
> `1126m purana` STALE verdict was a **false positive** that FIX-50's holiday calendar fixed.

Found while reading a pasted server log from **02-Oct-2026 09:23 IST — a Friday, market open
for 8 minutes**. Every 5m/1h frame reported `18.2h old`. Before blaming the app, I asked the
upstream directly:

```
sandbox now (IST): 2026-10-02 09:31  Fri
RELIANCE.NS  5m  range=1d   bars=  0
RELIANCE.NS  5m  range=5d   bars=298  last_bar=10-01 15:15  age=18.3h
RELIANCE.NS  1d  range=1d   price=1167.7  regularMarketTime=10-01 15:15
RELIANCE.NS  daily closes: 09-30=1187.0 · 10-01=1167.7 · 10-02=None
```

Two conclusions. The app's STALE cascade was **correct** — upstream genuinely had no bar for
today's session, so rejecting it and sending `is_realtime=False` was right. But the same
response hid the real bug: `regularMarketPrice=1167.7` paired with
`regularMarketTime=2026-10-01 15:15`, i.e. **18.3 hours old while the market was open**.

`fetch_yahoo_live_ltp()` read that timestamp **only to build a display string** — it was the
sole reference to `regularMarketTime` in the whole file — and hardcoded `is_realtime: True`.
Worse, a missing timestamp fell back to `datetime.now()`, presenting "right now" as the
quote's time. Since `Dashboard.html` derives its chip from `stale: t.stale || !t.is_realtime`,
the result was **the chart saying DELAYED and the header price saying LIVE, on one screen,
from one payload**. `fetch_nse_live_ltp()` had the same shape: NSE sends its own `timestamp`,
and the code wrote `datetime.now()` instead.

This is the same fail-open class FIX-47 closed for OHLC frames; the live-quote path was never
covered.

**The fix** adds `quote_is_fresh()` — a scalar twin of `frame_is_fresh()` — plus
`_parse_quote_ts()`, which handles both Yahoo epoch seconds and NSE's
`'02-Oct-2026 09:31:00'`:

- market **open** → the quote must not be older than `LIVE_MAX_AGE_MIN` (default 10)
- market **closed** → the quote's date must not lag `last_completed_session()` by more than
  `CLOSED_GRACE_DAYS` — the same session logic FIX-47 introduced, weekends skipped
- timestamp **missing or unparseable → STALE (fail-closed)**
- `timestamp` is now the quote's real time; missing gives `--:--:--`, never a fake `now()`
- **the price is still served** — the gate only changes the label, the UI never blanks out
- `STOCKAI_LIVE_MAX_AGE_MIN` tunes the limit, `STOCKAI_LIVE_GATE=off` is a kill-switch
  (both work from `.env`)
- the console warning is throttled to one line per symbol per 5 minutes, otherwise a 2-second
  refresh would flood it

Measured against real Yahoo, live, market open — `GET /api/quote/RELIANCE`:

```jsonc
// before
{ "price": 1167.7, "timestamp": "09:31:04", "is_realtime": true }          // ← a lie

// after
{ "price": 1167.7, "timestamp": "15:15:00", "is_realtime": false,
  "stale": true, "quote_time": "2026-10-01 15:15:00",
  "stale_reason": "STALE: quote 1126m purana > 10m limit",
  "source": "yahoo.ns" }
```

Boundaries measured: 0 / 9.9 / 10.0 min → FRESH; 10.1 / 25 / 1096 min → STALE. Naive,
IST-aware and UTC-aware `now` all return the same verdict, so FIX-47's timezone fail-open
does not repeat here.

Side fix: NSE quotes used to report `source: 'unknown'` (they never set the key, so
`get_live_quote`'s `setdefault` won). They now report `'nse'`.

**One thing deliberately left alone:** the `change%` denominator was already correct.
`range=1d` yields `chartPreviousClose = 1187.0`, which is the 30-Sep close ✅. `range=5d`
would have given `1197.6` (28-Sep) — wrong. The old note that `range=5d` shifts `prevClose`
is confirmed, and the app was already on the right side of it. No change made.

`verify_live_quote.py` grew 50 → **86 checks**.

---

### FIX-48 · the startup banner now says *where* your token came from

This one started with **my own wrong advice**. Seeing `🔒 Token auth ON` in a pasted log, I
twice told the user to "change `STOCKAI_API_TOKEN` in `.env`". They replied that no such token
was in their `.env` — and they were right. Verified: the token was nowhere in the working tree,
`git log --all -S "…"` was empty across all history, and `.env.example` ships it blank. The
only source in code is `os.environ.get('STOCKAI_API_TOKEN')`.

It was a **Windows User-scope environment variable**:

```
[Environment]::GetEnvironmentVariable('STOCKAI_API_TOKEN','User')     → b5232050…
[Environment]::GetEnvironmentVariable('STOCKAI_API_TOKEN','Machine')  → (empty)
```

Two design gaps made that confusing rather than obvious:

**1. `load_dotenv_file()` defaults to `override=False`** — a key already present in
`os.environ` is *never* overwritten by `.env`. Measured:

```
.env file me likha tha : FROM_DOTENV_FILE
os.environ me pehle tha: FROM_WINDOWS_ENV
-> app jo use karega   : FROM_WINDOWS_ENV
```

So editing `.env` to change the token would have silently done nothing. That is standard
dotenv behaviour, but it is confusing when nothing tells you.

**2. The banner never said where the value came from** — only `Token auth ON`. So the user
reasonably went looking in `.env`.

**Fix:** a `config_source(key)` helper, backed by a `PRE_DOTENV_KEYS` snapshot taken *before*
`.env` is loaded (without it, provenance is unknowable):

| state | reported source |
|---|---|
| only in `.env` | `.env` |
| only in env var | `environment variable` |
| **in both** | `environment variable (.env ko override kar raha hai)` + the exact removal command |
| nowhere | `default (kahin set nahi)` |

Real `app.py` startup output, all three cases measured:

```
🔒 Token auth ON — neeche wali link me token pehle se juda hua hai
   ↳ source: .env
```

```
🔒 Token auth ON — neeche wali link me token pehle se juda hua hai
   ↳ source: environment variable (.env ko override kar raha hai)
   ⚠️  .env me bhi STOCKAI_API_TOKEN likha hai par WO IGNORE ho raha hai.
      Env var hatane ke liye: [Environment]::SetEnvironmentVariable('STOCKAI_API_TOKEN',$null,'User')  — phir naya terminal kholo.
```

Worth knowing: removing a Windows env var needs a **new terminal** (the old one caches
`$env:`), and once removed, `.env` genuinely takes effect — verified.

`verify_security.py` grew 118 → **126 checks** (all four provenance cases, banner wiring, and
an assert that the snapshot is taken before `.env` loads).

---

## Still open (honest list)

1. **ML edge.** Measured, and it isn't there (see [`RESEARCH_REPORT.md`](RESEARCH_REPORT.md)).
   The proposed redesign (volatility-adjusted multi-day label + decorrelated features + purged CV)
   was *also* tested and **also failed** (−7.2 pp vs baseline, 0/20 symbols positive). Until a
   design shows a positive edge with CI, ML stays advisory-only — no accuracy number in the UI
   without a ≥500-prediction rolling sample behind it.
2. **Score *performance* validation (still OPEN).** FIX‑33 fits score DISTRIBUTION, not expected
   future return. At 2026‑09‑29: p80/p95 = 57/62 on 7,500 observed daily-engine scores. Changes to
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
