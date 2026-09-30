# RESEARCH_REPORT.md — "Kya StockAI ka ML (ya koi bhi simple rule) NSE costs ko beat karta hai?"

**Study window:** 20 NSE large-caps · daily bars · 2-year aur 5-year dono windows
**Method:** purged walk-forward (5 expanding folds) · execution lag = 1 bar · real NSE delivery costs · shuffled-label permutation null
**Generated:** 2026-09-30 · **Scripts:** `research/` (sab kuch reproducible, neeche commands diye hain)

---

## 0 · TL;DR (2 minute me pura sach)

| # | Sawaal | Jawaab (evidence) |
|---|---|---|
| 1 | App ka ML (28 features + `dir1` label) market ko beat karta hai? | ❌ **Nahi.** 5-year test me **50.79%** accuracy vs **51.26%** majority-class baseline over **17,885 OOS predictions** → edge **−0.47 pp** (95% CI ±0.73 pp). Literally coin-flip. |
| 2 | Proposed redesign (10 features + volatility-adjusted 5-day label) better hai? | ❌ **Nahi.** Accuracy 54.77% *dikhti* hai, par baseline usi window me 61.95% hai → edge **−7.18 pp**. **0/20 symbols** positive. Shuffled-label null (57.98% ceiling) model se **upar** hai — matlab jo "signal" hai wo noise hai. |
| 3 | Toh paisa kahan gaya? | **Costs me.** App-config ML ne 5 saal me **137 round-trips** kiye, **₹46,755 costs** diye (₹1 L capital ka **46.8%**, ≈ **12.2 pp/saal**). Net **−25.6%** jabki usi window me buy&hold **+32.5%**. |
| 4 | Koi bhi strategy buy&hold ko beat kar paayi? | ❌ **Nahi.** 5-year window me: B&H +32.5% > momentum +18.4% > RSI +10.0% > ML variants (sab negative). 2-year falling market me RSI (+1.1%) aur cash (0%) ne ML ko beat kiya. |
| 5 | Kuch kaam ka nikla? | ✅ **Ek structural fix:** minimum-holding-period (5 bars). Isse trades 137 → 54, cost drag 46.8 pp → 20.9 pp, aur net −25.6% → −2.9%. Par ye *nuksaan kam* karta hai, *profit* nahi banata — kyunki underlying signal me edge hi nahi hai. |

**Ek line me:** *ML ka problem sirf cost nahi hai — signal hi nahi hai. Cost fix (min-hold) 22 pp bacha leta hai, lekin 0 edge ko 0 se upar nahi kar sakta. Isliye app me ML ko "validated edge" ki tarah dikhana ab evidence ke against hai.*

---

## 1 · Sawaal kya tha (aur kyun)

`AUDIT_REPORT.md` (§C-2) me maine likha tha: *"ML has no measurable edge"* — 7,200 OOS predictions par mean edge −1.1 pp. Us waqt ye ek **diagnosis** thi, treatment nahi. Audit ne likha tha:

> "What would actually move the needle: *volatility-adjusted multi-day label, decorrelated features, purged/embargoed CV, out-of-sample-only reporting with CI, minimum sample gate.*"

Ye study exactly wahi test karti hai — plus ek sawaal jo audit me nahi tha: **agar signal theoretically hai bhi, to NSE ke ~0.33% round-trip cost ke baad bachta kya hai?**

---

## 2 · Method (kya banaya, kaise, kyun)

| Design decision | Kyun (aur kya galat hota agar na karte) |
|---|---|
| **Purged + embargoed walk-forward**, 5 expanding folds | Standard `train_test_split` future leak karta hai. `horizon`=5 wale label me last 5 train bars ka label test window ke returns se overlap karta hai — isliye `train_end = fold_start − horizon`. |
| **Execution lag = 1 bar** | Signal `close(t)` par banta hai → position `t+1` par. Lag=0 par backtest "aaj ke close par aaj hi trade" maan leta hai, jo practically impossible hai. |
| **Cost har position-change par lagti hai** | Flat position par kuch nahi, sirf 0→1 aur 1→0 par. Ye realistic hai (delivery, no intraday square-off). |
| **Baseline = usi fold ke test window ka majority class** | Ye "50% coin flip" se zyada tough baseline hai. Trending window me majority class 60-70% hit kar jaati hai — model ko usse beat karna padta hai, warna "accuracy" jhooth hai. |
| **Permutation null (shuffled train labels, 3 permutations)** | Agar shuffled labels se train kiya model bhi utna hi score kare, to "edge" pure overfitting/noise hai. |
| **Sab strategies same OOS window par** | ML aur rules ko alag windows par compare karna cheating hoti hai. OOS start = sab ML folds ka max start. |
| **Paired tests across 20 symbols** | 20 symbols = 20 samples. t-test, Wilcoxon, sign test — teeno. Pooled binomial alag. Ek symbol ka result conclusive nahi hota. |
| **₹1,00,000 notional, 1 symbol at a time** | Position sizing/compositing ko bahar rakha, taaki comparison *signal quality* ka ho, *bet-sizing* ka nahi. |

**Strategies tested**

| ID | Kya hai | Notes |
|---|---|---|
| S1 | ML `dir1` (kal up?) + app ke 28 features | **App ka exact current design** |
| S2 | ML `dir1` + 10 decorrelated features | Feature-reduction ka test |
| S3 | ML `ret5_atr` (5-day fwd return > 0.5×ATR) + 10 features | Audit ki recommendation; permutation null bhi |
| S4 | Rule: Close > SMA200 | Classic momentum |
| S5 | Rule: RSI < 30 entry, RSI > 55 exit | Mean-reversion |
| S6 | **Buy & Hold** | The bar every strategy must cross |
| S7/S8 | S3/S1 + **min-hold 5 bars** | Turnover/cost structural fix |

---

## 3 · Cost model (asli dushman)

`research/costs.py` — ₹1,00,000 notional, NSE delivery, self-tested:

| Leg | Brokerage | STT | Exchange | SEBI | Stamp | GST | Slippage (5 bps) | **Total** |
|---|---|---|---|---|---|---|---|---|
| BUY | ₹20.00 | ₹100.00 | ₹3.25 | ₹0.10 | ₹15.00 | ₹4.20 | ₹50.00 | **₹192.55 (0.1926%)** |
| SELL | ₹20.00 | ₹100.00 | ₹3.25 | ₹0.10 | — | ₹4.20 | ₹50.00 | **₹177.55 (0.1776%)** |
| | | | | | | | **ROUND TRIP** | **₹327.60 = 0.3276%** |

**Break-even math (yaad rakhne layak):**

- Har round-trip ko **+0.33%** gross chahiye bas costs nikaalne ke liye.
- 30 round-trips = **9.8%** equity, bina kisi galat call ke.
- App-config ML: **137 trades × 0.3276% ≈ 44.9 pp** expected drag — measured **46.8 pp**. Theory aur practice match karte hain (achhi baat: cost model sahi hai).

---

## 4 · Primary results — 5-year window (Dec 2022 → Sep 2026, OOS ≈ 940 bars/symbol)

> Ye window dono regime cover karta hai: 2023-24 ka strong bull aur 2025-26 ki correction. Isliye ye primary evidence hai.

### 4.1 ML quality (honest numbers, same folds, same baseline)

| Strategy | OOS preds | Pooled acc | Pooled baseline | **Edge** | t-test p | Wilcoxon p | Sign p | +edge symbols | Null ceiling beat |
|---|---|---|---|---|---|---|---|---|---|
| **S1** app-config (`dir1`, 28 feat) | 17,885 | 50.79% | 51.26% | **−0.47 pp** | 0.17 n.s. | 0.25 n.s. | 0.50 n.s. | 8/20 | — |
| S2 (`dir1`, 10 feat) | 17,980 | 50.74% | 51.26% | **−0.52 pp** | 0.29 n.s. | 0.49 n.s. | 0.82 n.s. | 9/20 | — |
| S3 (`ret5_atr`, 10 feat) | 17,970 | 54.77% | 61.95% | **−7.18 pp** | <0.001 | <0.001 | <0.001 | **0/20** | 2/20 |
| S3 shuffled-label null | — | mean 56.27% | — | — | — | — | — | — | ceiling **57.98%** |

> **Pooled vs mean:** "Pooled" = saare OOS predictions ko mila kar (samples-weighted). Unweighted per-symbol mean bhi same kahani kehta hai: S1 mean edge **−0.74 pp**, S2 **−0.48 pp**, S3 **−7.44 pp** (5y). Dono figure scripts se cross-check kiye gaye hain.

**Padhne ka tarika:** S3 ki 54.8% accuracy aankh ko acchi lagti hai, par usi window me *sirf majority class bolne wala* predictor 62.0% score kar raha tha. Aur shuffled-label model 56.3%/57.98% — **asli model se zyada**. Matlab model me jo "structure" hai, wo data me nahi hai; wo noise se fit hui hai.

S1 (app ka design) ka 50.79% ± 0.73 pp — 50% se statistically alag nahi. **17,885 predictions ke baad bhi.** Ye single largest OOS sample jo ab tak is project me measure hua hai (audit: 7,200; ab: 17,885).

### 4.2 Net-of-cost performance (same OOS window, lag=1, real costs)

| Strategy | Mean net % | Median net % | t-test (net vs 0) | vs B&H pp | Symbols beating B&H | Mean trades | Costs ₹ (per ₹1L) | **Drag pp (pp/yr)** | Sharpe | Exposure % |
|---|---|---|---|---|---|---|---|---|---|---|
| S1 app-config ML | **−25.57** | −25.86 | <0.001 | −58.04 | 1/20 | 137.1 | 46,755 | 46.8 (12.2) | −1.09 | 38.6 |
| S2 ML small-10 | −25.53 | −28.76 | <0.001 | −58.00 | 1/20 | 124.8 | 42,202 | 42.2 (11.0) | −1.08 | 42.2 |
| S3 ML `ret5_atr` | −9.10 | −10.63 | 0.010 | −41.57 | 4/20 | 77.7 | 28,701 | 28.7 (7.5) | −0.89 | 28.5 |
| S4 momentum (SMA200) | +18.38 | +10.40 | 0.046 | −14.09 | 9/20 | 15.2 | 5,898 | 5.9 (1.5) | −0.22 | 62.0 |
| S5 RSI mean-reversion | +9.95 | +10.34 | 0.032 | −22.52 | 7/20 | 5.8 | 2,190 | 2.2 (0.6) | −0.43 | 18.1 |
| **S6 Buy & Hold** | **+32.47** | +41.67 | 0.005 | 0.00 | — | 1.0 | 192 | 0.2 (0.1) | −0.03 | 99.8 |
| **S7** S3 + min-hold | **−2.88** | −7.64 | 0.58 n.s. | −35.35 | 4/20 | 53.8 | 20,881 | 20.9 (5.4) | −0.63 | 41.0 |
| **S8** S1 + min-hold | −6.88 | −11.16 | 0.36 n.s. | −39.35 | 1/20 | 82.8 | 31,169 | 31.2 (8.1) | −0.59 | 58.7 |

**Observations:**

1. **Har ML variant paisa khoti hai, aur mostly costs ki wajah se.** S7 (−2.9%) aur S8 (−6.9%) me costs ke *baad* ka nuksaan bahut chhota hai — par phir bhi 0 se neeche, kyunki gross signal ~flat hai.
2. **B&H jeeta**, kyunki 5 saal me NSE large-caps upar gaye (mean +32.5%, median +41.7%, range −35.5% se +110.1%). Ye baat important hai: *falling-market test (2y) me B&H hi −12.5% tha — tab bhi ML ne nahi jeeta (section 5).*
3. **Momentum aur RSI** (bina ML) net-positive hain, par B&H se 14–23 pp peeche — inka edge *timing* nahi, sirf *lower exposure* tha.
4. **Exposure dekho:** ML 28-39% exposed rehte hue bhi B&H se 35-58 pp peeche. Matlab ye sirf "kam invest kiya" wala farq nahi — **selection galat thi**.

---

## 5 · Cross-check — 2-year window (Dec 2024 → Sep 2026, falling market, OOS ≈ 200 bars/symbol)

| Strategy | Pooled acc | Baseline | Edge | Mean net % | vs B&H pp | Trades | Drag pp |
|---|---|---|---|---|---|---|---|
| S1 app-config | 52.08% | 53.65% | −1.57 pp | −12.32 | +0.21 | 28.6 | 9.99 |
| S2 small-10 | 50.23% | 53.78% | −3.55 pp | −14.48 | −1.94 | 27.6 | 9.53 |
| S3 `ret5_atr` | 56.11% | 68.52% | −12.41 pp | −10.11 | +2.43 | 16.6 | 5.81 |
| S4 momentum | — | — | — | −7.06 | +5.48 | 3.9 | 1.32 |
| **S5 RSI mean-rev** | — | — | — | **+1.13** | **+13.67** (17/20, p=0.004) | 2.1 | 0.70 |
| S6 Buy & Hold | — | — | — | −12.54 | 0.00 | 1.0 | 0.19 |
| S7 S3+minhold | — | — | — | −10.22 | +2.31 | 11.8 | 4.10 |
| S8 S1+minhold | — | — | — | −9.88 | +2.66 | 17.4 | 6.12 |

**Do window ka compare karke jo samajh aata hai:**

- **Costs ki bimari har regime me consistent hai:** app-config ML ka drag 2y me 10.0 pp, 5y me 46.8 pp. Ye compounding churn hai.
- **"B&H se better" ≠ profitable:** 2y window me S5 ne B&H ko 17/20 symbols me beat kiya (p=0.004), par absolute return sirf **+1.13%** (t-test vs zero: p=0.69). Falling market me *kam* khona hi "alpha" nahi hota.
- **Overfitting ka signature:** S3 ki accuracy 2y me 56.1% → 5y me 54.8%, par baseline 68.5% → 62.0%. Loss ratio jaisa hi rehta hai. Model ke numbers window ke saath *shift* hote hain, *improve* nahi hote — classic no-edge behaviour.

---

## 6 · Jo actually kaam aaya — turnover fix (min-hold)

| Config | Trades | Costs ₹ | Drag pp | Net % | **Delta** |
|---|---|---|---|---|---|
| S1 (app-config, no hold) | 137.1 | 46,755 | 46.8 | −25.57 | — |
| **S8** (S1 + min-hold 5) | **82.8** | **31,169** | **31.2** | **−6.88** | **+18.7 pp** |
| S3 (ret5_atr, no hold) | 77.7 | 28,701 | 28.7 | −9.10 | — |
| **S7** (S3 + min-hold 5) | **53.8** | **20,881** | **20.9** | **−2.88** | **+6.2 pp** |

**Ye study ka sabse practical natija hai:** signal ko 5 bars tak pakad ke rakhna (chhote flip-flop pe rok) **trades ~40% kam** karta hai aur **18.7 pp** bacha leta hai. Par ye nuksaan ko zero par laata hai, profit nahi deta — kyunki gross edge ~0 hai.

👉 **Recommendation:** agar app me ML signal live rahega, to minimum-hold (ya equivalent turnover cap) **mandatory** hai. Backtest me bhi yahi rule hona chahiye jo live me chalega — warna backtest ka number live se 20 pp tak jhooth hoga.

---

## 7 · Audit ke numbers se cross-check (reproducibility)

| Source | Sample | Result | Match? |
|---|---|---|---|
| Audit `AUDIT_REPORT.md` §C-2 | 7,200 OOS preds | mean edge **−1.1 pp** | ✅ |
| Ye study, 2y window | 3,825 pooled preds (S1) | edge **−1.57 pp** | ✅ same direction/magnitude |
| Ye study, 5y window | **17,885** pooled preds (S1) | edge **−0.47 pp**, CI ±0.73 | ✅ statistically zero |
| Audit, scanner as-is | 30 stocks | 24/30 negative edge, mean −5.0 pp | ✅ |
| Ye study, S3 (audit ki recommendation) | 17,970 preds | edge **−7.18 pp**, 0/20 positive | ❌ recommendation kaam nahi kari |

**Note:** S3 (jo audit me "what would move the needle" tha) **kaam nahi kari** — aur ye ek honest finding hai. Volatility-adjusted 5-day label ne class imbalance badha diya (label 62-68% ek hi class me), jisse majority-class baseline itna strong ho gaya ki model usse door bhi na aa paaya. Label engineering se edge nahi banti; edge data se aani chahiye.

---

## 8 · Study ke dauraan mile apne bugs (transparency)

Research code bhi bug-free nahi tha; teen mile aur fix kiye — isliye ye numbers pe bharosa kiya ja sakta hai:

1. **`inf` → NaN bug (critical):** Volume=0 wale bars par `pct_change` infinite deta hai. `np.nan_to_num` inf ko `1.8e308` bana deta hai → `StandardScaler` NaN produce karta hai → pehla study crash. Fix: `posinf/neginf=0.0` + clip. (Regression: study ab deterministic — same seed, same numbers.)
2. **Momentum rule ka warmup bug:** `SMA200` ko OOS slice par compute kiya ja raha tha (200 NaN bars) → strategy kabhi entry hi nahi leti thi (0 trades, 0% exposure). Fix: indicator poori history par, phir slice.
3. **Aggregation key collision:** `S3_permutation_null` (jo ek dict hai, per-symbol result nahi) ML rows me mix ho gaya tha → report rendering crash. Fix: filter `_ml_` se.

Har fix ke baad **full study dobara chalaya** — numbers upar wahi final hain.

---

## 9 · App ke liye kya matlab (actionable)

1. **Claim sudharo (highest priority).** "ML: 4-Model Ensemble + Walk-Forward validation" ab evidence ke against hai. Sahi wording: *"ML = experimental research signal. 17,885 out-of-sample predictions par accuracy baseline se better nahi mili (±0.73 pp)."*
2. **Accuracy/probability ko confidence ki tarah kabhi na dikhao.** `confidence` model ki *boldness* measure karta hai, *correctness* nahi — 51.4% baseline wali duniya me 0.55 probability ka matlab zero edge hai.
3. **Turnover cap lagao** (min-hold ya 5-day label + hold). 18.7 pp ka farq hai — ye purely cost ka farq hai, koi magic nahi.
4. **Live signal ko sample gate ke peeche rakho:** jab tak rolling OOS ≥ 500 predictions par edge > +2 pp aur CI ka lower bound > 0 na ho, ML ko advisory-only rakho (na sizing, na BUY/SELL tag).
5. **Agar user ko genuinely edge chahiye**, to ye study kehti hai ki daily OHLCV + indicators se NSE delivery costs ke baad edge nahi mil rahi. Agla serious attempt in directions me ho: (a) cross-sectional/relative signals (kis stock me baaki se better risk-reward hai), (b) event/earnings/fundamental data, (c) longer horizons (20-60 din) jahan per-trade cost ka share kam ho, (d) portfolio-level construction (position sizing, stops) — ek ML classifier ko tradable banane ke bajaye.
6. **Costs ko UI me dikhao.** Jab app ₹1L ka position suggest kare, saath likho: *"round-trip cost ≈ ₹328 (0.33%)"*. Ye ek line hi 90% unrealistic expectations khatam kar deti hai.

---

## 10 · Limitations (ye study kya prove nahi karti)

- **Universe:** 20 NSE large-caps (liquid, low impact cost). Small-caps/midcaps me costs *zyada* honge — result unke liye aur kharab hoga, behtar nahi.
- **Data:** daily OHLCV only (yfinance / tvDatafeed fallback). Intraday, order-book, fundamentals, news — kuch nahi. Corporate actions ka manual audit nahi kiya gaya.
- **TATAMOTORS** ka 5y history available nahi tha (ticker source 404) — us symbol ka window chhota (520 bars) hai, baaki 19 symbols 1,241 bars.
- **Costs:** ₹1 L notional, 5 bps slippage assumption. Bade size ya illiquid names par slippage zyada hoga; capital-gains tax model me nahi hai.
- **No shorting, no leverage, no stops, no pyramiding.** Long/flat only.
- **Permutation null:** 3 permutations per symbol (speed ke liye) — coarse, par conclusion (null > model) itna clear hai ki permutations badhane se badlega nahi.
- **Ek market, ek asset class, ek century ka ek chhota hissa.** Ye "ML kabhi kaam nahi karta" ka proof nahi — ye *is design* ka proof hai, in features aur is horizon par.
- **2y window ka B&H −12.5%** tha; 5y ka +32.5%. Regime-dependent results ko single number me wrap karna galat hoga — isliye dono diye hain.

---

## 11 · Reproduce (copy-paste)

```bash
cd StockAI_repo

python3 research/costs.py                     # cost model self-test  → 0.3276% round trip
python3 -m research.backtest                  # backtest invariants   → 5/5 PASS
python3 research/run_study.py --period 5y     # ~250s · reports/study_results_5y.json
python3 research/run_study.py --period 2y     # ~160s · reports/study_results_2y.json
python3 research/analyze.py 5y                # significance tests    → reports/findings_summary_5y.md
python3 research/analyze.py 2y
```

**Files:**

| File | Kya hai |
|---|---|
| `research/costs.py` | NSE delivery cost model (self-test ke saath) |
| `research/backtest.py` | Vectorised long/flat engine, lag + costs + trade bookkeeping (+ 5 invariants) |
| `research/features.py` | Feature sets (`APP_28`, `SMALL_10`), labels (`dir1`, `ret5_atr`), decorrelation |
| `research/ml_lab.py` | Purged walk-forward, permutation null, verdict logic |
| `research/data.py` | yfinance → tvDatafeed loader + disk cache |
| `research/run_study.py` | S1–S8 study runner |
| `research/analyze.py` | Paired tests, pooled stats, cost break-even |
| `reports/study_results_5y.json` | Raw 5y numbers (per-symbol + aggregates) |
| `reports/study_results_2y.json` | Raw 2y numbers |
| `reports/findings_summary_5y.md` | Auto-generated stats tables (5y) |
| `reports/findings_summary_2y.md` | Auto-generated stats tables (2y) |
| `reports/study_table_5y.md` | Per-symbol ML breakdown (5y) |

**Determinism:** seeds fixed (`default_rng(seed)`); do baar chalane par identical numbers aaye — verify kiya gaya.

---

## 12 · Bottom line (seedha jawaab)

1. **App ka ML out-of-sample edge hold nahi karta.** 5 saal, 20 stocks, 17,885 predictions: 50.79% vs 51.26% baseline. Coin flip se better nahi.
2. **Costs ne construction ko maar diya, par construction pehle se hi kamzor thi.** Zero-cost par bhi signal ~coin flip hai; costs ne uske ₹46,755 waapas le liye.
3. **Turnover cap sabse bada single improvement hai** (+18.7 pp) — sasta, bina model change, bina badle bhi implementable.
4. **Buy&hold ko is window me kuch bhi nahi hara paya.** Ye baat report me likhne layak hai: 5 years me sabse profitable "strategy" wahi thi jo kuch nahi karti.
5. **Agla step (agar user chahe):** cross-sectional/relative-value ML, longer horizons (20-60 din), ya event-driven features — kyunki daily OHLCV classifier + 0.33% cost = structurally losing game.
