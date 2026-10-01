# StockAI V6.0 — Deep Project Audit
**Audit date:** 2026-09-30 · **Auditor:** Arena Agent · **Method:** static review **+ live execution**
**Environment:** Python 3.13.14 · pandas 2.2.3 · numpy 1.26.4 · scikit-learn 1.6.1 · xgboost 3.4.1 · yfinance 1.7.0 · `tradingview-datafeed 2.1.1` · live NSE/TradingView/Yahoo network access

## FIX-36 addendum — 2026-10-01 (.env config)

FIX-35 ke env knobs har PowerShell session me dobara set karne padte the, isliye token
"jhanjhat" lag raha tha. Ab repo root ki `.env` automatically load hoti hai — chhota
built-in loader (`load_dotenv_file()`), koi third-party `python-dotenv` dependency nahi.

Rules: `KEY=VALUE`, `#` comments, optional `export` prefix, optional quotes; **real
environment variable jeetta hai** (`.env` sirf default deta hai); missing file silently
ignore hoti hai. `.env` `.gitignore` me hai, aur repo me sirf `.env.example` template
jaata hai jisme token **khaali** hai — koi secret ship nahi hota. `refresh_security_from_env()`
se `SECURITY` dobara env se ban jaata hai (tests/tools ke liye).

Verified: `tools/verify_security.py` **84/84** (naye 20 checks: parsing, quotes, `export`,
comment/blank/garbage lines, env-wins precedence, override, missing file, `.env` gitignore +
untracked, `.env.example` tracked with empty token).

## FIX-35 addendum — 2026-10-01 (security hardening: M-11 + M-12)

**M-11 — wildcard CORS, no auth, no rate limit.** `CORS(app)` put
`Access-Control-Allow-Origin: *` on every response; there was no authentication and
no throttling, so anyone who could reach port 5000 could drive the whole API (and
hammer NSE/TradingView through it). Now:

* CORS is an explicit allowlist (`STOCKAI_CORS_ORIGINS`, comma-separated); default is
  **same-origin only**, with `Vary: Origin`. Prefix spoofs (`https://ok.example.attacker.com`)
  do not match.
* Optional token auth: set `STOCKAI_API_TOKEN` and `/` + `/api/*` require it via
  `X-Api-Key`, `?token=…`, or the `stockai_token` cookie (HttpOnly, SameSite=Lax).
  Unauthenticated → `401` JSON. Static assets stay public. Default is still open,
  and startup prints a loud warning when no token is set.
* Per-IP sliding-window rate limit (default 240/min, `STOCKAI_RATE_LIMIT`, `0` = off)
  → `429` + `Retry-After`. `/api/stream` (SSE) is exempt because it is long-lived.
  `X-Forwarded-For` is only trusted when `STOCKAI_TRUST_PROXY=1`, so the bucket
  cannot be spoofed.
* Security headers on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a CSP with
  `frame-ancestors 'none'`, `object-src 'none'`, `connect-src 'self'` (script/style
  origins are limited to the CDNs the page already uses — no wildcard).
* Bind host is configurable (`STOCKAI_HOST`).

**M-12 — XSS sinks.** Fourteen `innerHTML` sinks took strings straight from the API,
including the search dropdown, which rendered Yahoo `symbol`/`longname` unescaped and
built `onclick="selectStock('${s.sym}')"` — a quote in a symbol could break out of the
attribute. Now a `safeHtml` tagged template escapes every interpolated value
(`raw()` marks the few nested-markup cases), and the search list is built with the DOM
API (`textContent` + `data-sym` + click delegation), so there is no HTML parsing and no
inline handler at all.

**Verified:** `tools/verify_security.py` **64/64** (policy, CORS matching, 401/cookie,
429 + Retry-After, proxy spoofing, source wiring) and `tools/verify_xss_render.js`
**14/14** — jsdom with `runScripts: 'dangerously'` fed real `<img onerror=…>` /
`<script>` payloads through `render()` and the search box: nothing executed, no element
was injected, and the payloads appear as visible text. Existing suites unchanged:
kelly 37/37, sentinels 48/48, score-calibration 64/64, risk-basis 45/45,
no-fake-numbers 23/23, live-quote 38/38, fixes 31/31, jsdom render 31/31.

**This is hygiene, not alpha** — it reduces the blast radius if the port is exposed or
an upstream feed is compromised. It says nothing about whether any signal makes money.

## FIX-34 addendum — 2026-10-01 (Windows encoding)

FIX-31/32/33 pushed fine, but every source-reading verifier crashed on Windows:
`Path.read_text()` and `tempfile.NamedTemporaryFile('w')` default to the **ANSI code page
(cp1252)**, while `app.py`, `Dashboard.html` and the tool sources are UTF-8 (Devanagari,
`→`, `═`). Result: `UnicodeDecodeError: 'charmap' codec can't decode byte 0x90`. The code
under test was fine — the *test harness* was Windows-hostile.

Fixed by passing `encoding='utf-8'` explicitly to every text read/write in
`tools/verify_*.py`, `tools/apply_v61_fixes.py`, `tools/patch_dashboard.py`, plus the JSON
writers in `deep_analyzer.py` and `nifty_scanner.py`. Verified with the default text
encoding forced to a non-UTF-8 code page (`LC_ALL=C PYTHONUTF8=0`, the Linux equivalent of
cp1252): 37/37, 48/48, 64/64, 45/45, 23/23, 38/38, 31/31.

If a future tool still crashes this way, the fix is the same: never rely on the platform's
default text encoding — always pass `encoding='utf-8'`. (Running Python with
`-X utf8` or `PYTHONUTF8=1` also works, but the code should not require it.)

## FIX-33 addendum — 2026-10-01

**This audit is an as-of-2026-09-30 record; C-3 / the 65/78 comments below describe the *old*
score.** Subsequently, FIX‑31 measured the actual T1-before-SL *plan geometry* for Kelly p,
FIX‑32 removed silent `sf`/`si` 0 fallbacks, and FIX‑33 implemented a genuinely reproducible
**distribution fit** (not a future-return backtest). For FIX‑33 the master score now uses only
**four daily stock-specific engines**, each on the same trailing 250 bars in live and historical
runs. Regime's former 18% stock-rank weight is **zero**; it instead caps directional position
exposure. Intraday MTF (old 20%) stays in its diagnostic panel: inventing 250 sessions of 5m/15m
history would break comparability and would be worse than excluding it from the fitted rank.

Builder `tools/build_score_calibration.py` fetched 3y Yahoo daily bars (2026‑10‑01 morning run),
excluded the current partial bar and the current ranked completed session, and retained **250
completed sessions from 2025‑10‑01 through 2026‑09‑29**. There are **7,250 observed scores,
29/30 NSE universe stocks** (TATAMOTORS lacked >=500 clean bars; no fabricated fill).
Type‑7 pooled percentiles on the new four-engine raw score: **p10=43, p40=50, p80=57, p95=62**.
BUY_DIP maps to p80, BUY_BREAKOUT to p95; score ties mean ≥p80 contains **22.1%** and
≥p95 **6.4%** of observed historical scores (not exactly 20%/5%). Old absolute 65/78 are
not used in live labels.
The JSON artifact includes every per-session score, formula fingerprint, universe, weights and
source. Missing/stale history, mismatched formula/weights/recorded cutoffs, same/future-day fit or
absent symbol disables directional labels (this is consistency checking, not a digital signature).
The live API also blocks an assumed Kelly p without a measured plan; missing NIFTY regime caps
quantity to zero. Standalone legacy risk helper still accepts generic `action='BUY'` score
buckets for backward-compatibility tests; **not** used by `/api/stock` labels.

**This is NOT evidence of trading profit.** Percentiles rank scores, not returns. Measured plan
setups overlap and are unconditional/in-sample, independent-sample SE is optimistic, and neither
borrow/slippage/cost-aware conditional P&L nor walk-forward rank performance was verified. The
regime exposure fractions are explicitly risk *policy*, not fitted alpha. Scanner's separate
`composite` has not been calibrated here. See README and `tools/verify_score_calibration.py`.

---

## 0. How this audit was done (so you can reproduce every number)

Nothing in this report is a "code smell" guess. Every claim was produced by running your code:

| # | Harness | What it proved |
|---|---------|----------------|
| 1 | `pip install -r requirements.txt` | install **fails** (tvdatafeed==2.1.0 not on PyPI) |
| 2 | `analysis_tmp/harness.py` | full pipeline runs: `/api/search`, `/api/quote`, `/api/stock` on live data, timed |
| 3 | `analysis_tmp/unit_tests.py` | Tier-2 key bug, dividend units, Kelly sizing, resolver, routes, Tier-1-absent fallback |
| 4 | `analysis_tmp/indicator_audit.py` | ATR/RSI vs reference math, "VWAP", Volume-Profile score quantisation |
| 5 | `analysis_tmp/supertrend_check.py` | app SuperTrend vs canonical Pine algorithm → **97.4 % agreement (correct)** |
| 6 | `analysis_tmp/ml_study.py` | **7 200 out-of-sample predictions** across 20 Nifty names |
| 7 | `analysis_tmp/ensemble_dist.py` | ensemble-score compression across 10 symbols |
| 8 | `analysis_tmp/sector_bug.py` | beta/correlation NaN + 6-month/2-year period mix-up |
| 9 | `nifty_scanner.py` (run as-is) | `scan_results.json`, 30 stocks, ML-edge distribution |
| 10 | `deep_analyzer.py RELIANCE / TCS` | `deep_*.json`, walk-forward + risk + sector blocks |
| 11 | `app_v6_1_fixed.py` + `analysis_tmp/verify_fixed.py` | every fix demonstrated working |

---

## 1. Executive summary

The engineering *shape* of this project is good: 3-tier fallback, JSON sanitiser, vectorised indicators, 18 patterns, 6 engines, a real walk-forward harness. Several things I expected to be wrong (ATR, RSI, SuperTrend, the MultiIndex fix, graceful degradation) are **actually correct** — I verified them against reference implementations and live runs.

But there are **four defects that can lose real money**, and the headline marketing claims ("0s delay", "exact Moneycontrol/NSE live", "Walk-Forward validated ML", "institutional") do not survive contact with measurement.

| Severity | Count | Headline |
|---|---|---|
| 🔴 **Critical** | 3 | Leveraged position sizing (5.4× on a ₹1 L account) · ML has **no measurable edge** (7 200 OOS predictions) · Kelly math contradicts its own R:R |
| 🟠 **High** | 12 | Tier-2 scraper fully dead (2 of 3 methods) · portfolio "beta/correlation" silently `NaN` · "Relative Strength" mixes 2-year vs 6-month returns · dividend yield 100× wrong · "VWAP" is a 2-year cumulative · resolver returns HCL-INSYS for "INFOSYS LTD" · 0 BUY signals possible by construction |
| 🟡 **Medium** | 12 | `pip install -r requirements.txt` fails · no route serves the dashboard · every 404 becomes a 500 · 8.1 s ML per request · SSE dead code · misleading "LIVE" badges |
| 🟢 **Verified good** | 8 | ATR/RSI exact · SuperTrend 97.4 % faithful · graceful 3-tier degradation · 2 565-stock search · 18 patterns · 20+ indicators · JSON sanitisation · scanner speed |

**Bottom line:** the data layer and indicator layer are ~90 % sound. The *decision* layer (scoring + ML + position sizing) is where the money is lost, and it is currently not statistically or financially valid.

---

## 2. 🔴 Critical findings

### C-1 · Position sizing creates leveraged bets, and its Kelly input contradicts itself

**Evidence (live, RELIANCE):**
```
ensemble score 32 (action = SHORT_SELL)
capital assumed ₹1,00,000 | kelly 23.0% | qty 454 shares
NOTIONAL VALUE = ₹5,39,942  →  5.40× the account (leverage!)
R:R used for Kelly = 2.5 (hardcoded)  but ACTUAL rr_ratio returned = 1.0
Kelly with true b=1.0: f* = (0.45×1.0 − 0.55)/1.0 = −0.100  → NEGATIVE = no trade
```

Three separate bugs in 40 lines of `calculate_risk()`:

1. **No notional cap.** `qty = (capital × kelly) / risk_per_share` sizes off *risk per share* but never checks `qty × price ≤ capital`. With a wide stop (2.5×ATR) and a high ATR, quantity explodes. A "23 % Kelly" instruction is silently a **5.4× margin position**.
2. **Kelly uses a fantasy R:R.** `reward_risk_ratio = 2.5` is hardcoded while the function itself computes and returns `rr_ratio`. For any score < 60, SL and T1 are *both* 2.5×ATR → the true R:R is exactly **1.0**, and at a 45 % win-rate Kelly is **negative** (i.e. "do not take this trade"). The code instead prints a 23 % allocation.
3. **Direction is ignored.** A `SHORT_SELL` verdict still emits a *long-side* plan (SL below, T1/T2 above, positive qty). There is no short-side mirror, no "no-trade" state.

**Real-world impact:** following the dashboard on one trade risks ~₹23 000 of a ₹1 L account on a broken thesis, with 5.4× notional exposure and a 1:1 payoff. This is the single most dangerous defect in the codebase.

**Fix (implemented in `app_v6_1_fixed.py` FIX-07):**
```python
qty_by_risk     = int((capital * kelly) / risk_per_share)
qty_by_notional = int(capital / price)          # 1× cap
qty  = max(0, min(qty_by_risk, qty_by_notional))
b    = abs(t1 - price) / risk_per_share         # REAL reward:risk, not 2.5
kelly = max(0.0, (win_rate*b - (1-win_rate)) / b)
if direction == 'NONE' or kelly <= 0: qty = 0   # no trade
```
Verified after fix: `direction=NONE qty=0 notional=₹0 leverage=0.0x kelly=0.0% b=1.0`.

---

### C-2 · The Machine-Learning layer has no measurable edge — and the number shown to users is noise

I rebuilt your exact 28-feature pipeline and ran an **expanding-window walk-forward** (train 120+ bars, predict the next 20, roll forward) over **20 Nifty names → 7 200 out-of-sample predictions**.

| Metric | Result |
|---|---|
| Mean OOS accuracy | **50.7 %** |
| Mean baseline (always predict majority class) | **51.9 %** |
| **Mean edge** | **−1.1 pp** (the model is *worse* than a coin flip weighted by class balance) |
| Symbols with any positive edge | 8 / 20 |
| Symbols with edge > +2 pp | **2 / 20** |
| Mean per-window σ | 9.8 pp → **±19 pp** 95 % band on a single symbol |
| Your decision rule (`prob ≥ 55 %`) | 2 682 signals, hit-rate **50.4 %**, next-day return **+0.042 pp** vs unconditional |
| Same rule after realistic costs (₹100–300 round trip on ₹1 L) | **net negative** |

The dashboard reports the *other* number: a single 80/20 split, e.g.

```
ENSEMBLE ACCURACY 53.0%   BASELINE 55.0%      ← models BELOW baseline
"Best Edge: +10.0%"                            ← best of 3 models on ONE 100-day slice
walk_forward_accuracy 49.4%                    ← this is the honest number, buried
```

Per-symbol detail (excerpt from `analysis_tmp/ml_study.json`):

| Symbol | OOS acc | Baseline | Edge | Rule hit-rate |
|---|---|---|---|---|
| RELIANCE | 53.9 % | 50.0 % | +3.9 pp | 52.1 % |
| ITC | 55.8 % | 53.6 % | +2.2 pp | 48.9 % |
| KOTAKBANK | 52.2 % | 50.6 % | +1.7 pp | 54.8 % |
| LT | 52.5 % | 50.8 % | +1.7 pp | 53.3 % |
| INFY | 52.5 % | 52.8 % | −0.3 pp | 48.1 % |
| SBIN | 50.3 % | 53.9 % | −3.6 pp | 53.2 % |
| WIPRO | 46.4 % | 50.3 % | −3.9 pp | 45.1 % |
| HDFCBANK | 50.0 % | 54.2 % | −4.2 pp | 46.7 % |
| MARUTI | 46.7 % | 50.8 % | −4.2 pp | 47.8 % |
| SUNPHARMA | 48.3 % | 53.6 % | −5.3 pp | 51.8 % |
| TCS | 49.7 % | 55.3 % | −5.6 pp | 45.1 % |
| TATASTEEL | 49.4 % | 50.3 % | −0.8 pp | 50.0 % |

Your own scanner independently confirms it — **24 of 30 stocks had negative ML edge**, mean **−5.0 pp**, worst **−21.7 pp**:

```
NESTLEIND   ML: 93.0%  [⚠️ NEG-EDGE]     ← a 93 % "UP probability" the system itself distrusts
TITAN       ML: 85.5%  [⚠️ NEG-EDGE]
ONGC        ML: 85.3%  [⚠️ NEG-EDGE]
```

Two design points worth stating plainly:
* The scanner's "negative-edge penalty" **neutralises ML for 80 % of the universe**, which means the advertised "ML-boosted composite" is mostly `0.55 × ensemble + 22.5` — a constant. Your own filter proves the ML is not usable as-is.
* `ensemble_accuracy`, `best_edge` and "Confidence: HIGH" are computed on a **single split**; with a ±19 pp noise band, "HIGH confidence" is mathematically unsupported. `confidence` is derived from how far the probability sits from 50 %, which measures *model boldness*, not *model accuracy*.

**What would actually move the needle:** label the target on a *volatility-adjusted, multi-day horizon* (e.g. 5-day forward return > 0.5×ATR), drop the 28 raw indicators down to a handful of decorrelated features, use purged/embargoed CV, report only out-of-sample results with a confidence interval, and gate live signals on a *minimum sample* (n ≥ 500 OOS predictions) before showing any accuracy at all. Until then, present ML as "research", not "validated".

---

### C-3 · The composite score is structurally bearish: 0 BUY signals are possible by construction

Measured across 10 real symbols through the full 6-engine pipeline:

```
ensemble scores: min=25  max=56  mean=42.4      'tradeable' (≥78):  0/10
engine readings ≥65 (bullish):  7/60
engine readings ≤35 (bearish): 22/60
labels reached: AVOID, SHORT_SELL, WATCHLIST   — never BUY_BREAKOUT or BUY_DIP
```

Cause: the six engines are **not on a common 0-100 scale centred on 50**.
* Volume Profile can only return `{35, 50, 70, 75}` — *measured: only `{35, 50}` appeared in 6 symbols*. Weight 12 %.
* VCP V2 starts at **30** and adds bonuses → a "no contraction, tight range" stock still scores 30-50. Weight 15 %.
* SMC/ICT starts at 50 and returns **45–50 in practice** (the +10 order-block / +10 bullish-FVG bonuses almost never fire; measured `order_blocks: []` on every symbol tested).
* Market Regime contributes **exactly 35 to every symbol** (same NIFTY read for all) → 18 % of the score is a constant offset that never discriminates between stocks.
* Multi-Timeframe divides by a hardcoded `4` even when only 2-3 TFs load.

So the weighted mean sits near **42**, while the label bands assume 50 = neutral. Result: a normal, healthy stock is displayed as **AVOID**, and the "🟢 READY TO BUY" state requires a score **78** that the pipeline essentially never produces. The scanner run agrees: **0 BUY / 18 WATCH / 12 SELL** in a normal market.

**Fix:** (a) re-centre each engine (`ensemble_v2` in the fix layer does this for VCP/MTF and is exposed as a *diagnostic*), (b) **fit the label thresholds on history** — e.g. take the score distribution of the last 250 sessions of the universe and set BUY at the 80th percentile; never hardcode 65/78 on an arbitrary scale, (c) drop the market-regime engine from the *cross-sectional* score (it belongs in position sizing / market exposure, not in a stock's rank).

---

## 3. 🟠 High-severity findings (silent wrong data)

### H-1 · Tier-2 ("NSE Official Direct") is 100 % dead — 2 of 3 methods cannot ever fire

| Method | Status | Proof |
|---|---|---|
| A — `chart-databyindex` | **dead** | code asks for `.get('gRapData')`; the live API returns **`grapthData`** (NSE's own typo). Mocked with the real payload → `fetch_nse_direct()` returned `None`. |
| B — jugaad-data | works | signature `stock_df(symbol, from_date, to_date, series='EQ')` matches your call |
| C — nsepython | **dead** | `getattr(nsep,'equity_history_volumes',None)` → **missing**; real function is `equity_history(symbol, series, start_date, end_date)` (4 positional args, you pass 3) |

Live check from this host also showed `quote-equity` → **HTTP 403** and the intraday chart endpoint returning `{"closePrice":0,"grapthData":[]}`.

### H-2 · …and even if Method A fired, it would fabricate OHLCV that poisons every indicator

```python
df['Open'] = df['Close']; df['High'] = df['Close']; df['Low'] = df['Close']
df['Volume'] = 100000.0          # a constant, for every bar, forever
```
`chart-databyindex` is a **single intraday session** (~375 minute dots). Feeding that into a daily pipeline gives: ATR ≈ 0, BB-width ≈ 0, VCP tightness ≈ 0 (→ false "VCP READY"), Williams %R garbage, zero spread for pattern detection, and a volume engine that sees a flat 100 000. The fix layer now **refuses** any response that covers one calendar day.

### H-3 · "Exact Moneycontrol / NSE Live Sync" degrades silently, while the UI claims otherwise

* NSE blocks the request from non-Indian / datacentre IPs (403 on `/` and on `/api/quote-equity`) — verified.
* On failure the code falls back to the **TradingView/Yahoo daily close** — fine — but the payload still says `data_source = 'NSE Direct Live'`, and the navbar renders **"NSE REALTIME TICK"** with a green pulse.
* TradingView's free feed is **15–20 minutes delayed**, so the "0-second delay live stream" claim in the docstring is not accurate. The audit-fixed dashboard now shows an honest badge (`DELAYED (15-20 min)` vs `NSE LIVE`) and an `is_realtime` flag.

### H-4 · Portfolio "Beta / Correlation" is `NaN` — and the label silently defaults to "Market"

```
stock index : Timestamp('2024-09-25 03:45:00')  ← TradingView (time-of-day component)
nifty index : Timestamp('2026-03-30 00:00:00')  ← Yahoo (midnight)
inner-join overlap rows = 0   →  corr = nan, beta = nan
```
`float('nan')` does not raise, so the `except` never fires; `beta_type` falls through both comparisons and reports **"Market"**. Proven on RELIANCE *and* TCS. (`json.dump(default=str)` then writes literal `NaN` into `deep_*.json` — invalid strict JSON, see H-6.)

### H-5 · "Relative Strength vs NIFTY 50" compares a 2-year stock return with a 6-month index return

`safe_download_deep(symbol, period='6mo')` — but the **TradingView path ignores `period`** (it returns `n_bars=500`):

```
RELIANCE requested period='6mo' → 500 rows spanning 735 days
^NSEI    requested period='6mo' → 126 rows spanning 184 days   (TV skipped for ^ symbols)
```
So `relative_strength = stock_2y_return − nifty_6m_return`. RELIANCE printed "−20.2 % vs NIFTY +1.7 %"; TCS "−51.5 %". Both numbers are arithmetically real but the *comparison* is meaningless.

### H-6 · `deep_*.json` is invalid strict JSON (contains `NaN`)

`deep_RELIANCE.json` contains 2 literal `NaN` tokens. Python's `json` accepts them; **JavaScript's `JSON.parse` throws** — so any web UI built on these files breaks. Your `clean_json()` (app.py) exists precisely for this but the CLI analyser doesn't use it.

### H-7 · Dividend yield displayed **100× too large**

```
yfinance raw dividendYield: 0.5   → code does value*100 → "50.00%" for RELIANCE
```
Modern yfinance already returns a percentage (0.5 = 0.50 %). The dashboard would show a 50 % dividend yield. Fixed by a plausibility clamp (`pct > 25 → /100`); post-fix the API returns `0.50%`.

### H-8 · The "VWAP" is a 2-year cumulative VWAP and it drives the *intraday* KPI

```
app 'VWAP' (cumulative since first bar) = 1363.49
true session VWAP  = 1189.27 | 1-week VWAP = 1217.42 | last close = 1189.40
price vs app-VWAP = BELOW   ← the code therefore deducts 10 points from the Intraday KPI
price vs true VWAP = ABOVE  ← …while the stock is actually above VWAP
```
Fix layer replaces `VWAP` with a **20-session rolling VWAP** (`VWAP_CUMULATIVE` kept separately for reference).

### H-9 · Symbol resolver returns the wrong company (`INFOSYS LTD` → **HCL-INSYS**)

```
'TATA'          -> TATACAP        'HDFC'  -> HDFCAMC
'BANK'          -> AUBANK         'LTD'   -> ASALCBR
'INFOSYS LTD'   -> HCL-INSYS   ← "INFOSYS" is a substring of "HCL INFOSYS+TEMS"
```
The third fallback does `cw in s['name'].upper()` — pure substring matching over 2 565 names in CSV order. Fixed with ranked, word-boundary matching (`FIX-01`); post-fix: `'INFOSYS LTD' → INFY`.

### H-10 · Scanner: the "negative-edge penalty" pulls the rug out from under its own headline

`scan_results.json` (run today, 9.1 s, 30 stocks, 26 TradingView / 4 Yahoo):

* **24 / 30 negative ML edge** (mean −5.0 pp, worst −21.7 pp)
* **0 BUY / 18 WATCH / 12 SELL**
* ML probabilities shown are **raw**, but a negative-edge name is **forced to 50** inside the composite — so the leaderboard column (`ML: 93.0%`) is a number the scoring path explicitly discarded. Users read "93 % bullish" for NESTLEIND whose composite implies 50 %.
* `vol_ratio` is **0.21 – 0.92 for every single stock**: the last bar is the *in-progress* session (a few million shares vs a 24 M average), so the RVOL/volume engine penalises nearly the whole market. The "Off-Market Volume Ratio Fix" (forward-fill of zero volume) papers over the symptom; the real fix is to **drop the unfinished session** or scale volume by elapsed session time.
* 4 symbols fell back to Yahoo (TV connection drops under 30 parallel websockets — `Connection to remote host was lost`): `fetch_scanner_data()` creates a **new `TvDatafeed()` per symbol**. Reuse one connection.

### H-11 · Engine granularity: several "scores" are near-constants

* Volume Profile: **4 possible values only** `{35, 50, 70, 75}` (observed `{35,50}`).
* Market Regime: **35 for all 10 sampled symbols** (`WEAK BEAR`).
* SMC/ICT: 45–50 on most symbols (order blocks never triggered in the sample).
* VCP: base 30 → 30-58 range mostly.

A 0-100 "score" that can only take 4 values (or is a constant across the cross-section) is not a score; it's a label wearing a number's clothes. It also makes the 6-engine weights misleading: 30 % of the composite (Volume Profile 12 % + Regime 18 %) carries almost no information.

### H-12 · `clean_json()` turns *missing* data into a *real* `0.0`

```python
SMA_200 raw: nan   →   JSON: 0.0     →   dashboard: "SMA 50/200 = 1024.5 / 0.0 → DEATH CROSS"
```
For any stock with < 200 bars (fresh listings, recent IPOs, short histories) the UI fabricates a 0 and then draws a conclusion from it. Fixed: `NaN → null`, and the front-end renders `—` / `INSUFFICIENT HISTORY`.

---

## 4. 🟡 Medium findings

| ID | Finding | Evidence / impact |
|----|---------|-------------------|
| M-1 | **`pip install -r requirements.txt` fails outright** | `ERROR: Could not find a version that satisfies the requirement tvdatafeed==2.1.0 (from versions: none)`. The package is **not on PyPI** (404). Real install: `pip install git+https://github.com/rongardF/tvdatafeed.git` or `tradingview-datafeed==2.1.1` (verified: provides the same `from tvDatafeed import TvDatafeed, Interval` API and works live). |
| M-2 | `numpy==1.26.4` has **no cp313 wheels** | Verified: 0 wheels / sdist only → pip compiles from source (~3 min, needs a C toolchain). The "Python 3.12–3.14 compatible" banner is optimistic. pandas 2.2.3, sklearn 1.5.2 and xgboost 2.1.2 *do* ship cp313 wheels. |
| M-3 | **No route serves the dashboard** | `GET /` → `500 {"error":"Server Exception: 404 Not Found…"}`. Users must open `dashboard.html` from disk, where the JS calls `http://127.0.0.1:5000` — so it breaks on any other device, tunnel, or reverse proxy. Fixed: `/` serves the dashboard; the JS now uses same-origin by default. |
| M-4 | **Every 404 becomes a 500** | `@app.errorhandler(Exception)` catches `werkzeug.exceptions.NotFound` too. Verified: `/`, `/dashboard.html`, `/api/nonexistent`, `/icon/favicon.svg` → all HTTP 500. |
| M-5 | ML cost: **8.12 s per request, uncached** | 4 model trainings + 19 walk-forward fits on every call. `/api/stock` cold 5.7–6.9 s, warm 2.5 s after the fix-layer cache. |
| M-6 | SSE endpoint was **dead code** | The dashboard polls `/api/quote` every 2.5 s instead of using `/api/stream`; the stream also lacked `Cache-Control`/heartbeat. Under `threaded=True`, every SSE client pins a worker thread forever. Now wired + hardened. |
| M-7 | `/api/quote` claims "< 100 ms" | Measured **0.56 – 0.68 s** (each call performs a fresh tiered fetch). |
| M-8 | "2 100+ stocks" | Actually **2 565** (better than claimed) — but the parse survives today only because the raw header really is `' SERIES'` (leading space). Normalise headers (`strip().upper()`) so a CSV format change can't silently empty the DB. |
| M-9 | Import-time network dependency | `load_dynamic_nse_stocks()` runs at import (2.5 s here). On a blocked network the app still starts (good) but every launch pays the timeout. Move to a cached background refresh. |
| M-10 | Unknown symbol costs **~15 s** | `ZZZNOTREAL` walks all three tiers × 2 exchanges with 4-6 s timeouts. Add a 5 s total budget + short negative cache. |
| M-11 | `CORS(app)` = `*` on everything, no auth, no rate limit | Anyone who can reach the port can hammer NSE/TradingView through your server. |
| M-12 | Search results injected via `innerHTML` | Company names come from NSE/Yahoo into the DOM unescaped — low-grade XSS surface if a name ever contains markup. |

---

## 5. 🟢 What is genuinely good (verified, not assumed)

1. **ATR(14) and RSI(14)** match independent reference implementations to the last decimal (`19.1759`, `29.3400`).
2. **SuperTrend** agrees **97.4 %** with the canonical Pine algorithm across 8 symbols (identical last-bar values and reversal counts of 15 on average). My first reference implementation was wrong; the app's is right. Only a 1-4 % edge-case divergence around band flips.
3. **Graceful degradation is real:** with `tvDatafeed` removed (the state after the requirements failure), the app silently and correctly falls through to Yahoo — 501 rows in 0.6 s, no crash.
4. **The MultiIndex fix is necessary and correct** — yfinance 1.x returns `MultiIndex` columns (verified: `Price/Close` two-level header), and without the flatten the whole pipeline would break.
5. **3-tier cascade + per-tier logging** is a genuinely good pattern, and `clean_json()` recursion covers numpy/pandas/NaN/Inf/Timestamp.
6. **18 candlestick patterns** and **20+ indicators** are all present and computed (counted, not assumed) — `detect_all_candle_patterns` returns exactly 18 distinct pattern types.
7. **The scanner is fast** (30 stocks / 9.1 s with 5 threads) and its "vol_ratio 0.0×" bug is indeed fixed — it just needs the partial-session fix.
8. **NSE master-list loader** works, loads 2 565 equities, and has a sensible curated fallback.

---

## 6. Claimed vs measured

| Claim (docstring/UI) | Measured | Verdict |
|---|---|---|
| "TradingView Direct Stream — 0 s Delay" | TV free feed ≈ 15–20 min delayed; NSE live blocked from this host; UI still shows "REALTIME TICK" | ❌ |
| "Live LTP — exact Moneycontrol / NSE India Live Sync" | 403 from non-Indian/datacentre IPs; silently falls back to delayed close *labelled* "NSE Direct Live" | ❌ |
| "NSE Direct Scraper (Native + jugaad + nsepython)" | Method A dead (key typo), Method C dead (function missing) → 1 of 3 alive | ❌ |
| "ML: 4-Model Ensemble + Walk-Forward validation" | 4 models trained ✔; mean OOS edge **−1.1 pp** over 7 200 predictions; UI shows single-split accuracy | ⚠️ implemented, not validated |
| "6 Institutional Engines + 20+ Indicators + 18 Patterns" | 6 ✔ · 20+ ✔ · 18 ✔ | ✅ |
| "Kelly Risk" | Implemented but leveraged & self-contradictory (C-1) | ❌ |
| "2 100+ Active Equities" | 2 565 | ✅ |
| "Python 3.12-3.14 compatible" | Runs on 3.13 ✔ but numpy 1.26.4 needs a source build (no cp313 wheel) | ⚠️ |
| "NumPy JSON-Safe" | `clean_json()` works ✔ — but `deep_*.json` still writes `NaN` | ⚠️ |

---

## 7. Prioritised action list

**P0 — before any real money is risked**
1. Replace position sizing with the notional-capped, real-R:R, direction-aware version (C-1 / FIX-07).
2. Stop publishing accuracy numbers that come from a single split. Show `walk_forward_accuracy`, its baseline, and the ±noise band; rename "Best Edge" to "single-split best edge (not evidence)" (C-2 / FIX-06b).
3. Treat ML probabilities as advisory only until a *rolling* OOS study (≥500 predictions, purged CV) shows edge > +5 pp with a confidence interval.

**P1 — correctness of displayed data**
4. Fix Tier-2 (correct key, refuse intraday-only payloads) — done in FIX-04.
5. Reconcile the stock/index indexes before computing beta/correlation (normalise both to `date`), and fix the `period` mismatch in `safe_download_deep` (H-4, H-5).
6. Replace cumulative VWAP with a session/rolling VWAP (H-8 / FIX-05).
7. Fix dividend units + all `if value` truthiness checks on numeric fundamentals (H-7).
8. `NaN → null` everywhere, including the CLI JSON writers (H-6, H-12).
9. Fix `resolve_symbol` with ranked word-boundary matching (H-9 / FIX-01).

**P2 — calibration and honesty**
10. Re-fit the ensemble thresholds on the score's own historical distribution; re-centre VCP and normalise MTF; move the market-regime term out of the cross-sectional score (C-3 / FIX-08 `ensemble_v2`, exposed as a diagnostic).
11. Show `direction`, `notional`, `leverage` and `risk at stop` in the UI (done in the patched dashboard).
12. Add RVOL handling for the in-progress session (drop it, or scale by elapsed time).

**P3 — engineering hygiene**
13. Ship a working `requirements.txt` (git URL for tvdatafeed) + a `constraints` note about numpy on 3.13.
14. Serve the dashboard from Flask; same-origin fetch; real 404s; `Cache-Control: no-store` — done in FIX-10/11.
15. Cache ML per symbol/day (done), add a symbol negative-cache, reuse one TvDatafeed connection in the scanner/analyser.
16. Put the dev server behind `waitress`/`gunicorn`; restrict CORS; add basic rate limiting.
17. Add a tiny test suite (the `analysis_tmp/` harnesses are a good start) so `INDICATORS`, `RISK`, and `RESOLVER` can't silently regress again.

---

## 8. Files produced by this audit

| File | Purpose |
|---|---|
| `AUDIT_REPORT.md` | this report |
| `app_v6_1_fixed.py` | drop-in fix layer: imports `app.py`, applies 11 fixes, serves dashboard + API on `:8080` |
| `dashboard_fixed.html` | `dashboard.html` + 17 anchored front-end patches (same-origin API, SSE, honest badges, notional row, walk-forward panel, null-safe rendering) |
| `patch_dashboard.py` | the reproducible patch script that generates `dashboard_fixed.html` (asserts every anchor) |
| `requirements_fixed.txt` | installable dependency set |
| `scan_results.json` | real scanner output (30 stocks, 9.1 s) |
| `deep_RELIANCE.json`, `deep_TCS.json` | real deep-analyser output (note the `NaN` bug) |
| `icon/favicon.svg` | project icon (now actually served at `/icon/favicon.svg`) |
| `analysis_tmp/` | every harness + raw results (`ml_study.json`, `stock_RELIANCE.json`, …) |

### Reproduce everything
```bash
cd stockai_v6
python3 analysis_tmp/harness.py           # full pipeline, live, timed
python3 analysis_tmp/unit_tests.py        # bug proofs (Tier-2 key, Kelly, resolver, routes)
python3 analysis_tmp/ml_study.py          # 7 200-prediction walk-forward study (~3 min)
python3 analysis_tmp/ensemble_dist.py     # score compression + beta/period bugs
python3 nifty_scanner.py                  # regenerates scan_results.json
python3 deep_analyzer.py RELIANCE         # regenerates deep_RELIANCE.json
python3 app_v6_1_fixed.py                 # fixed app + dashboard on :8080
```

---

---

## 9. Round-2 status: what is fixed and what is still open

Round 2 added three patched entry points so your **original files stay untouched**:

| New file | Patches |
|---|---|
| `deep_analyzer_v3_6.py` | FIX-A honour `period` (stock & NIFTY legs now the same window) · FIX-B date-normalised beta/correlation · FIX-C strict-JSON writer |
| `nifty_scanner_v3_6.py` | FIX-S1 one shared TV socket (RLock, degrades once to Yahoo) · FIX-S2 RVOL from the last **completed** session · FIX-S3 records `ml_effective` · FIX-S4 strict JSON |
| `app_v6_1_fixed.py` (FIX-14) | unknown-symbol negative cache (15 s → instant on retry) |

**Verified after the round-2 patches**

```
deep_analyzer_v3_6.py RELIANCE TCS
   span check : stock 182d vs nifty 182d | aligned sessions = 124
   sector     : RS -14.2% | beta 0.887 (Market) | corr 0.341      ← was NaN / "Market" by accident
   deep_TCS.json: valid strict JSON ✔                              ← was literal NaN

nifty_scanner_v3_6.py
   vol_ratio (last COMPLETED session): min 0.82  max 16.66  mean 2.39   ← was 0.21-0.92 for every stock
   signals: 1 BUY | 21 WATCH | 7 SELL   in 7.9 s   (BUY reachable again)
   ML used in composite: 13/29   | neutralised (negative edge): 16/29
```

### Status matrix

| ID | Finding | Status |
|---|---|---|
| C-1 | Leveraged / self-contradictory position sizing | ✅ **Solved** (FIX-07, verified `qty=0, leverage 0x` on non-directional) |
| C-2 | ML has no measurable edge; single-split number shown as proof | ⚠️ **Partially** — honesty fixed (walk-forward + ±noise band now in the UI, `best_edge` relabelled). **The model itself still has no edge — that is a research problem, not a bug.** |
| C-3 | Score scale uncalibrated → 0 BUYs possible | ⚠️ **Partially** — `ensemble_v2` re-centres VCP/MTF and is shown as a *diagnostic*; the scanner's BUY path works again after the RVOL fix. **Proper fix = fit thresholds on history (still open).** |
| H-1 | Tier-2 Method A key `gRapData` → `grapthData` | ✅ Solved (FIX-04) |
| H-2 | Method A fabricates OHLCV / Volume=100000 | ✅ Solved (FIX-04 refuses intraday-only payloads) |
| H-3 | "NSE LIVE" badge lies when NSE is blocked | ✅ Solved (FIX-09 + dashboard D7) — badge now shows `DELAYED (15-20 min)` |
| H-4 | Beta/Correlation `NaN`, label defaults to "Market" | ✅ Solved (`deep_analyzer_v3_6.py`) |
| H-5 | "6M RS" compared 735-day vs 184-day returns | ✅ Solved (FIX-A) — both legs 182d, 124 aligned sessions |
| H-6 | `deep_*.json` invalid strict JSON | ✅ Solved (FIX-C) |
| H-7 | Dividend yield ×100 ("50.00%") | ✅ Solved (FIX-09 plausibility clamp → `0.50%`) |
| H-8 | "VWAP" = 2-year cumulative, drives intraday KPI | ✅ Solved (FIX-05) |
| H-9 | Resolver: `INFOSYS LTD` → HCL-INSYS | ✅ Solved (FIX-01) |
| H-10 | Scanner volume/ML-threshold issues | ✅ Solved (FIX-S2, FIX-S3) |
| H-11 | Near-constant engine "scores" | ⚠️ **Partially** — measured & documented, VCP/MTF re-centred as diagnostic; Volume Profile and Regime still near-constant (weights need redesign) |
| H-12 | `NaN → 0.0` fabricates indicators | ✅ Solved (FIX-02 + selector D11) |
| M-1 | `pip install -r requirements.txt` fails | ✅ Solved — `requirements_fixed.txt` verified installable |
| M-2 | numpy 1.26.4 has no cp313 wheels | ⚠️ Documented; on 3.13 use `numpy>=2.1` (still open if you pin 1.26.4) |
| M-3 | No route serves the dashboard | ✅ Solved (FIX-11) — `/` serves `dashboard_fixed.html` |
| M-4 | Every 404 became a 500 | ✅ Solved (FIX-10) |
| M-5 | 8.1 s ML per request | ✅ Solved (FIX-06 cache + `?fast=1`) |
| M-6 | SSE unused and unhardened | ✅ Solved (FIX-12 + dashboard D9 uses it, polling fallback) |
| M-7 | `/api/quote` "under 100 ms" claim | ⚠️ Improved (negative cache) but still ~0.5 s cold |
| M-8 | NSE CSV header parsing fragility | ⚠️ Open (works today only because the header really is `' SERIES'`) |
| M-9 | Network call at import time | ⚠️ Open |
| M-10 | Unknown symbol ~15 s | ✅ Solved (FIX-14 cache) |
| M-11 | `CORS(*)`, no auth/rate limit | ✅ Solved (FIX-35) — CORS allowlist, optional token auth, per-IP rate limit, security headers + CSP |
| M-12 | Search results via `innerHTML` | ✅ Solved (FIX-35) — `safeHtml\`\`` auto-escaping + DOM-API search list; jsdom injection test 14/14 |

**Honest answer to "sab solve ho gaya?":** all the *code* defects that were fixable in a session are fixed and verified — 15 of them. Two things remain that a patch cannot fix, because they are not bugs:

1. **The ML still has no edge** (C-2). I made the UI tell the truth about it; I did not make it work. That needs a re-labelled target, purged CV, a smaller feature set and a rolling ≥500-prediction study.
2. **The score thresholds are still uncalibrated** (C-3). `ensemble_v2` is a diagnostic shim, not a calibrated model. Fit the BUY/AVOID bands on the score's own 1-year distribution.

Plus the remaining operational items (M-2, M-8, M-9) that are hygiene, not correctness. M-11 and M-12 were closed by FIX-35.

### Final word
The bones of this project are better than most "AI trading dashboard" code I get to look at — the fallback design, indicator maths and JSON handling are real engineering. The problem is **the last mile**: the scoring scale is uncalibrated, the ML is presented as validated when it isn't, and (before FIX-07) the position sizer could put a 5.4× leveraged trade on a signal it didn't even trust. Three of those are now addressed; the ML and the calibration remain research work. Until that is done, treat the "verdict", "targets" and "ML probability" as **UI decoration, not advice**.
