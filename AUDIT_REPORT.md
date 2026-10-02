# StockAI V6.0 — Deep Project Audit
**Audit date:** 2026-09-30 · **Auditor:** Arena Agent · **Method:** static review **+ live execution**
**Environment:** Python 3.13.14 · pandas 2.2.3 · numpy 1.26.4 · scikit-learn 1.6.1 · xgboost 3.4.1 · yfinance 1.7.0 · `tradingview-datafeed 2.1.1` · live NSE/TradingView/Yahoo network access

## FIX-53 addendum — 2026-10-02 (asli wajah: TradingView ka silent BSE fallback)

### ⚠️ Pehle: FIX-52 me maine teen galat cheezein likhi thi

User ne teeno par sawaal kiya ("ye q verify nhi kiya he"). Verify kiya — **teeno galat nikli.**

**Galat 1: "Yahoo ka price exchange se match nahi karta, official close 2079.30 hai."**

Galat. `2079.30` **BSE** ka close hai, NSE ka nahi. `tvDatafeed` se directly measure kiya:

```
NSE:TCS        close = 2075.00      BSE:TCS        close = 2079.30
NSE:RELIANCE   close = 1167.70      BSE:RELIANCE   close = 1166.00
```

Yahoo (2075.00 / 1167.70) **NSE se match karta hai**. Do alag exchanges, do alag closing
auctions, do alag closes — ye legitimate difference hai, koi data error nahi.

**Galat 2: "Yahoo ka timestamp 15:15 hai kyunki wo CAS close miss karta hai."**

Galat inference. 11 sessions × 2 stocks compare kiye (Yahoo vs TradingView-NSE closes):

```
TCS       09-17..10-01  11 din   0 mismatch
RELIANCE  09-17..10-01  11 din   0 mismatch
```

**22 din, 0 mismatch.** Yahoo ke daily bars NSE ke bars hain aur sahi hain. 15:15 timestamp
ka close se koi lena-dena nahi tha.

**Galat 3: "ML study par asar hai, har close me CAS move missing ho sakta hai."**

Nahi hai. Yahoo = NSE, to study ka data theek hai. Ye concern poora khatam.

### Asli wajah — `fetch_tradingview` silently BSE par gir jaata tha

```python
df = self.tv.get_hist(symbol=clean_sym, exchange='NSE', ...)     # Primary
if df is None or df.empty:
    df = self.tv.get_hist(symbol=clean_sym, exchange='BSE', ...) # Silent fallback
```

Aur caller dono ko `'TradingView Direct'` keh deta tha.

**Proof — user ke dashboard ke chaaron numbers TV-BSE se exact match hain:**

| | price | ATR(14) | 52W high | 52W low |
|---|---|---|---|---|
| User ka dashboard | 2079.30 | 57.54 | 3336.7 | 1976 |
| **TV-BSE (300 bars)** | **2079.30** | **57.54** | **3336.7** | **1976.0** |
| TV-NSE (300 bars) | 2075.00 | 58.79 | 3350.0 | 1976.8 |

Yaani user ki machine par TradingView ka NSE fetch khaali aaya, BSE fallback chala, aur
**poora analysis — indicators, SL, targets, percentile rank — BSE data se bana**, jabki app ka
score calibration NSE universe (30 naam, 250 sessions) par fitted hai. Label se pata hi nahi
chalta tha.

Aur wahi `priceGapWarn` (FIX-52) jo maine banaya tha — wo actually sahi kaam kar raha tha:
header `/api/quote` se NSE price (2075.00) laata tha, analysis BSE frame (2079.30) se bani thi.
Maine us gap ki wajah galat samjhi thi.

### Fix

`fetch_tradingview` ab `(df, exchange)` return karta hai, aur source label exchange carry
karta hai: `'TradingView Direct (NSE)'` / `'TradingView Direct (BSE)'`. BSE par girne par
terminal par warning bhi aati hai.

`/api/stock` ab `frame_exchange` bhejta hai, aur Dashboard orange warning dikhata hai jab
frame BSE se aaya ho — kyuki percentile ranks NSE distribution se compare ho rahe hote hain.

Live verified:
```
data_source    = 'TradingView Direct (NSE)'
frame_exchange = 'NSE'
frame_close    = 2075.0
week52         = {high: 3350.0, low: 1976.8}
```

### Jo ab confirm ho gaya

- **NSE close (01-Oct)**: TCS **2075.00**, RELIANCE **1167.70** — Yahoo + TradingView-NSE, do
  independent sources
- **BSE close (01-Oct)**: TCS **2079.30**, RELIANCE **1166.00** — BSE ka apna API + TradingView-BSE
- NSE ka bhavcopy CSV maine nahi khol paya (`archives.nseindia.com/content/historical/...`
  404 deta hai; `EQUITY_L.csv` master 200 deta hai) — par do independent sources agree kar
  rahe hain, isliye attribution solid hai

`verify_live_quote.py` 160 → **168 checks** (naya `_FakeTV` behavioral test: NSE khaali →
BSE, aur NSE me data ho to BSE try hi na ho) · regression **809 passed, 0 failed**

---

## FIX-52 addendum — 2026-10-02 (external cross-check: price exchange se match nahi karta tha)

> ⚠️ **Is section ka central claim — "Yahoo ka price exchange se match nahi karta" — GALAT
> tha.** Neeche FIX-53 me correction hai. Jo theek hai: ROE 100x bug, `is_market_open` ka
> duplicate constant, `SESSION_CLOSE_HM` 15:35, search bar ka exchange suffix, aur market
> cap/fundamentals ka Yahoo se exact match.

### User ne poocha: "Moneycontrol/NSE/BSE se compare karo — 100% match ho raha hai ya nahi"

Pehle maine do cheezein "verify nahi kar paya" bol kar chhod di thi (NSE timestamp field,
market cap). User ne sahi pakda — dono verify ho gayi, aur jawab **na** hai.

**Yahoo crumb mil gaya** (`fc.yahoo.com` cookie → `/v1/test/getcrumb`), isliye `quoteSummary`
khul gaya. **BSE ka `getScripHeaderData` bhi is sandbox se chal gaya** (NSE 403 deta hai).

### Finding 1 — price exchange se match nahi karta

| | App header | Yahoo | **BSE official** |
|---|---|---|---|
| TCS close 01-Oct | ₹2,075.00 | 2075.00 | **₹2,079.30** (−4.30) |
| TCS prevClose | 2050.60 | 2050.6 | **2050.00** |
| TCS change | +24.40 (+1.19%) | same | **+29.30 (+1.43%)** |
| RELIANCE close | ₹1,167.70 | 1167.70 | **₹1,166.00** (+1.70) |
| RELIANCE prevClose | 1187.00 | 1187.0 | **1187.50** |

BSE ka `Ason` = `01 Oct 26 | 16:00` — official close.

**Karan: NSE ka Closing Auction Session.** Aug 3, 2026 se F&O wale stocks ke liye
continuous trading 15:15 par rukta hai aur CAS 15:15–15:35 official close banata hai.
Yahoo ka `regularMarketTime` **dono stocks par exactly 15:15:00** tha (measured) — yaani
continuous session ka last trade, official close nahi.

*(Ye inference hai — Yahoo ka upstream source maine directly measure nahi kiya. Par timestamp
dono stocks par exactly 15:15 aana isi taraf point karta hai.)*

Iska ML study par bhi asar hai: saari daily bars Yahoo se aati hain, to har close me CAS ka
move missing ho sakta hai. Magnitude TCS par 0.207%, jabki ATR 2.83% — yaani **ek ATR ka ~7%**.
Chhota hai, par zero nahi.

### ⚠️ Meri ek galti — correction

Maine pehle likha tha: *"app ke analysis block ne 2079.30 use kiya jo BSE se exact match
hai — yaani TradingView BSE se match karta hai, Yahoo nahi."* **Ye galat tha.** Ek hi data
point (user ka dashboard) se pattern nikal liya tha.

Is sandbox me live run kiya: `data_source = 'TradingView Direct'`, `tvDatafeed` installed,
300 bars — aur TCS ka close **2075.00** aaya, 52W **3350.0 / 1976.8**, ATR **58.79**. Ye sab
Yahoo ke values se **exact match** hain, BSE se nahi.

Yaani: **user ki machine par 'TradingView Direct' label ke neeche 2079.30 aaya, is sandbox me
usi label ke neeche 2075.00.** Source label data ki provenance ka bharosemand indicator nahi
hai — wahi class ka problem jo FIX-50/51 me backend/frontend me thi.

Jo solid hai (measured):
- BSE official: TCS 2079.30, RELIANCE 1166.00
- Yahoo: TCS 2075.00, RELIANCE 1167.70
- Sandbox TradingView: TCS 2075.00, RELIANCE 1167.70 (Yahoo jaisa)
- User ki machine, 'TradingView Direct' label: TCS 2079.30, ATR 57.54, 52W 3336.7/1976

### Finding 2 — ROE 100x galat tha

Yahoo `financialData.returnOnEquity` raw = **0.47743**, `fmt` = **"47.74%"**. Dashboard
dikha raha tha **"0.48%"**.

```python
_roe = info.get('returnOnEquity')                        # 0.47743 (FRACTION)
_roe = (_roe / 100.0) if (_roe and _roe > 5) else _roe   # 0.47743 (5 se chhota)
'roe': f"{_roe:.2f}%"                                    # "0.48%"  ← 100x off
```

FIX-09 ka comment kehta tha "yfinance `dividendYield` / `returnOnEquity` dono PERCENT me
deta hai". **`dividendYield` ke liye sahi (3.17), `returnOnEquity` ke liye galat (0.47743).**
Do fields ke opposite units hain. Aur heuristic **dono branches me galat** tha — 47.743 aata
to `/100` kar ke phir "0.48%" banta.

**Fundamentals ka poora cross-check (Yahoo raw se):**

| | Dashboard | Yahoo raw | |
|---|---|---|---|
| TCS P/E | 14.9 | 14.903398 | ✓ |
| TCS P/B | 6.85 | 6.8478684 | ✓ |
| TCS D/E | 10.2% D/E | 10.211 | ✓ |
| TCS ROE | **0.48%** | **0.47743 = 47.74%** | ❌ |
| TCS mcap | ₹750,753Cr | 7,507,531,530,240 | ✓ |
| RELIANCE P/E | 21.5 | 21.504604 | ✓ |
| RELIANCE P/B | 1.75 | 1.7479361 | ✓ |
| RELIANCE D/E | 36.7% D/E | 36.653 | ✓ |
| RELIANCE ROE | N/A | None | ✓ |
| RELIANCE mcap | ₹1,580,187Cr | 15,801,867,304,960 | ✓ |

`sharesOutstanding`: TCS 3,618,087,518 · RELIANCE 13,532,472,634 — market cap ÷ price se
dono exact match. (Pehle maine 13.53bn ka andaza lagaya tha; exact nikla.)

### Finding 3 — `is_market_open` me duplicate constant

`SESSION_CLOSE_HM` badalne par **kuch nahi badla**, kyunki `is_market_open` ke andar apna
literal tha:

```python
return (9 * 60 + 15) <= hm <= (15 * 60 + 40)   # SESSION_CLOSE_HM use hi nahi karta tha
```

Comment likhta tha "is_market_open ke upper bound ke saath match" — par dono alag literals
the, isliye chup-chaap drift ho gaye. **Test ne pakda** (15:36 False expected, True mila).
Ab `SESSION_OPEN_HM` / `SESSION_CLOSE_HM` ek hi source hain.

### Fix

1. `SESSION_CLOSE_HM` 15:40 → **15:35** (CAS close), aur `is_market_open` ab constant padhta hai
2. ROE × 100 with `|x| <= 2.0` guard → TCS **47.74%** (live verified)
3. `/api/stock` ab `frame_close` + `price_basis` bhejta hai
4. Dashboard `checkPriceGap()` — header price aur analysis price me **0.25% se zyada** gap ho
   to dono numbers + reason dikhata hai, chup-chaap koi ek nahi chunta. User ke dashboard par
   ye **+₹4.30 (0.21%)** fire karega.
5. Search bar me **(NSE)/(BSE)** suffix — `/api/search` pehle se `ex` bhejta tha, UI render hi
   nahi karta tha

### Jo drop kiya, aur kyun

**BSE ko live quote tier nahi banaya.** `getScripHeaderData` pehle kaam karta tha, phir ~40
requests ke baad **Akamai ne 403 "Access Denied"** de diya. Dashboard 2s par poll karta hai —
BSE minutes me block ho jaata. Scrip master (`getScripList`, `scripmasterdata`) aur search API
(`Msource/90D/getQuoteSearch.aspx`) bhi 403 dete hain, aur bhavcopy URLs SPA shell return karte
hain. Isliye symbol → scripcode dynamically resolve karna bhi possible nahi.

BSE ka data valuable hai (wahi batata hai ki Yahoo 4.30 off hai) par **on-demand check ke liye,
2s polling ke liye nahi**.

`verify_live_quote.py` 141 → **160 checks** · full regression **801 passed, 0 failed**
(`verify_dependency_pins` 45→47, environment-dependent)

---

## FIX-51 addendum — 2026-10-02 (ek hi feed state, asli quote time, label collisions)

### Shuruaat user ke pasted dashboard se

User ne poora dashboard paste kiya — 02-Oct-2026 10:31, **holiday**. Usme ek hi screen par do
contradictory badges the:

```
DELAYED (15-20 min)          ← upar, top badge
...
₹1,167.70  LIVE  yahoo.ns · 10:31:31     ← price ke baju
```

Aur sawaal: *"DELAYED (15-20 min) q dikha rha he"*.

### Karan — FIX-50 adhoora tha

`Dashboard.html` L1111:

```js
const isLive = /NSE/i.test(src) && !/TradingView/i.test(src);   // src = 'Yahoo Finance'
document.getElementById('liveBadgeText').textContent = isLive ? 'NSE LIVE' : 'DELAYED (15-20 min)';
```

**Ye bilkul wahi source-name bug tha jo maine FIX-50 me `app.py` L3585 me theek kiya —
frontend me uska apna copy tha jo maine dekha hi nahi.** Maine backend theek kar ke keh diya
tha ki bug gaya; wo adhoora tha.

Teen alag problems:

1. Top badge source ke **naam** se liveness nikalta tha → `'Yahoo Finance'` par hamesha DELAYED
2. `"DELAYED (15-20 min)"` **hardcoded jhooth** tha — asli staleness 1175 min (19.6h) thi, aur
   aaj to market hi band tha
3. `setLiveChip` me `const t = new Date().toLocaleTimeString(...)` — yaani **browser ki ghadi**
   quote ke waqt ki jagah. `10:31:31` wo waqt tha jab JS chali, quote ka waqt nahi. Quote
   `2026-10-01 15:15` ka tha. Ye wahi `datetime.now()` wala jhooth tha jo FIX-49 ne server par
   theek kiya — client par zinda tha.

### Aur jo mila (user ne "sab data sahi aata hai na" poocha tha)

**Arithmetic ✅ sahi nikla.** Har number dobara compute kiya:

| check | dashboard | dobara calc |
|---|---|---|
| change% | −19.30 (−1.63%) | −19.30/1187.00 = −1.626% ✓ |
| 52W Position | 1.5% | (1167.70−1160.8)/(1611.8−1160.8) = 1.53% ✓ |
| Stop Loss | ₹1219.38 (4.43%) | 2.5 × ATR 20.67 = 51.68 ✓ |
| Target 1 | ₹1116.02 | 1167.70 − 51.68 = exactly 1R ✓ |
| Breakeven | 50.0% | 1/(1+2.5/2.5) ✓ |
| Win-rate LCB | 48.2% | 0.516 − √(0.516·0.484/217) = 0.482 ✓ |
| Kelly / qty | 0% / 0 | p 0.482 < breakeven 0.50 → negative → clamp ✓ |
| Master card | 42 | int((36+53+39)/3) = int(42.67) ✓ |
| Model edges | −1.6/0/+6.7/+5.0 | 51.7−53.3, 53.3−53.3, 60−53.3, 58.3−53.3 ✓ |
| OOS | +0.96pp / −6.99pp | 51.03−50.07, 55.03−62.02 ✓ |

**Par labels me chaar bug mile:**

**1. "Master" ka matlab do jagah do cheez.** Card wala `kpi.master.score` = 42
(intraday/swing/longterm ka average). Verdict wala `"Master Score: 37/100"` = `ensemble.score`
(API se confirm: `ensemble.score = 37`). Do alag quantities, ek hi naam.

**2. OBV "0" asli zero nahi tha.** `(ind.obv > 0 ? (obv/1e6).toFixed(1)+'M' : '0')` — matlab
**koi bhi negative value "0" dikhti thi**. Measure kiya: `obv = -381723268.0`. Aur label
`obv > 0` se banta tha jabki backend scoring `obv > obv_ema` use karta hai — **UI aur scoring
alag comparison kar rahe the** (`obv_ema = -343805114.0`).

**3. Bollinger %B = 0.04 par "MID".** Thresholds sirf `< 0` aur `> 1` flag karte the. %B 0.04
matlab price lower band ke bilkul paas — jabki RSI 28.2, CCI −160, Williams %R −93.5,
StochRSI 5.3 sab OVERSOLD bol rahe the.

**4. Debt/Equity 36.7** — yfinance ka `debtToEquity` **percentage** hota hai (36.7 = 36.7%),
ratio nahi. Bina `%` ke 36.7× lagta tha. Aur `if fund_data['debt_val']` falsy-check tha, isliye
asli `0.0` D/E (zero-debt company) bhi `'N/A'` ban jaata tha.

### Fix

**Server ab ek hi state bhejta hai** — `feed_state` ∈ `{LIVE, DELAYED, CLOSED}` + `feed_label`
+ `market_open` + `quote_age_min` + `quote_time`. UI guess nahi karta.

`CLOSED` teesra state isliye zaroori tha kyunki FIX-50 ke baad holiday par quote "fresh"
kehlata hai (last completed session se match karta hai) — data ke liye sahi, par "LIVE" jhooth
hoga kyunki trade ho hi nahi raha.

Label me **asli number** jaata hai, koi hardcoded band nahi.

Measured, real `/api/quote/RELIANCE` (02-Oct, holiday):

```
feed_state    = 'CLOSED'
feed_label    = 'MARKET CLOSED (holiday)'
market_open   = False
quote_age_min = 1175.5
quote_time    = '2026-10-01 15:15:00'
timestamp     = '15:15:00'
```

`/api/stock/RELIANCE`:
```
feed_state = 'CLOSED'   market_holiday = True
obv = -381723268.0      obv_ema = -343805114.0
debt_equity = '36.7% D/E'
```

Tier-3 daily-close fallback ka `datetime.now()` timestamp bhi gaya — ab frame ke last bar ka
asli timestamp.

### Calendar aur timing (user ne poocha tha)

Dono verify kiye, **dono sahi**:

- 16 holidays, sab weekday, NSE 2026 list se exact match
- `09:14 → False`, `09:15 → True`, `15:40 → True`, `15:41 → False`
- NSE regular session 15:30 par band hota hai; code 15:40 tak jaata hai — **jaan-boojh kar**
  (closing auction cover karne ke liye), aur direction conservative hai

`verify_live_quote.py` 114 → **141 checks** · full regression **780 passed, 0 failed**
(`verify_dashboard_display` 28/28 — Dashboard changes ne use nahi toda).

---

## FIX-50 addendum — 2026-10-02 (NSE holiday calendar + ek aur source-name fail-open)

### Ye user ne pakda, maine nahi

FIX-49 deliver karte waqt maine likha tha: *"market 16 minute se khula"* — 02-Oct-2026 09:31
IST par. User ne jawab diya: **"today market me holiday he."**

Wo sahi tha. 2 October 2026, Friday = **Mahatma Gandhi Jayanti** — NSE/BSE poora din band.
Mera premise galat tha, aur uska matlab ye bhi hua ki **FIX-49 ka jo "false STALE" maine
measure kiya tha, wo actually sahi data tha** — aaj trading hai hi nahi, last quote 01-Oct
15:15 hona chahiye aur wahi tha.

Measure kiya, fix se pehle:

```
2026-10-02 09:31 Fri (HOLIDAY)
   is_market_open()  = True                              ← galat
   quote_is_fresh()  = 'STALE: quote 1096m purana'       ← FALSE POSITIVE
```

Jad: `is_market_open()` sirf `weekday <= 4` aur `09:15–15:40` dekhta tha. **Holiday ka koi
concept hi nahi tha.** Ye FIX-49 ko bhi chhuta tha aur FIX-47 ko bhi — `last_completed_session()`
holiday ko normal weekday maan kar "expected session" galat batata tha (Mon 05-Oct ko expected
`2026-10-02` batata, jabki wo holiday tha). `CLOSED_GRACE_DAYS = 1` ne verdict bacha liya tha,
par reason string jhoothi thi.

### Fix A — holiday calendar

NSE equity + equity-derivatives ke **2026 ke 16 weekday trading holidays** `NSE_HOLIDAYS` me.
Do update-raaste bhi, taaki late NSE circular ke liye patch na maangna pade:

- `STOCKAI_EXTRA_HOLIDAYS=2027-01-26,2027-03-23` (`.env` me bhi)
- repo root me optional `nse_holidays.txt` (ek date per line, `#` comments ok)

Ye raasta isliye zaroori hai kyunki **15-Jan-2026 original calendar me tha hi nahi** — NSE ne
use 12-Jan-2026 ke circular se add kiya (Maharashtra municipal elections). Calendar hamesha
adhoora reh sakta hai.

Safety: calendar khaali/purana ho to **crash nahi** — behaviour weekday-rule par wapas chala
jaata hai (jo aaj ka behaviour hai), aur agar current year calendar me nahi hai to startup par
warning aati hai:

```
👉 NSE holidays: 16 dates loaded for 2026 (aaj HOLIDAY — market band)
```
```
⚠️  NSE holiday calendar me 2027 NAHI hai (sirf [2026]) — har holiday par jhootha STALE warning aayega.
   Fix: nse_holidays.txt me 2027-MM-DD lines daalo, ya .env me STOCKAI_EXTRA_HOLIDAYS=...
```

Measured, fix ke baad:

| case | `is_market_open` | `last_completed_session` | `quote_is_fresh` |
|---|---|---|---|
| HOLIDAY 02-Oct 09:31 | **False** | 2026-10-01 | **FRESH** — `market closed, quote 2026-10-01 (latest 2026-10-01) — fine` |
| HOLIDAY 02-Oct 20:00 | False | 2026-10-01 | FRESH |
| normal Mon 05-Oct 09:31 | True | **2026-10-01** (holiday skip) | STALE (18.3h purana quote) |
| normal Mon 05-Oct 16:30 | False | 2026-10-05 | STALE (4d behind) |
| Mon 09:31, quote 30s pehle | True | — | FRESH |

Yaani holiday par false-positive gaya, **aur normal din par gate utna hi strict hai**.

Ek subtlety: `2026-08-15` (Independence Day) **Saturday** par padta hai, isliye wo 16-date
weekday list me nahi hai — par market us din bhi band hai, weekend rule se. Dono alag assert kiye.

### Fix B — `/api/stock` ka `is_realtime` source-name se aata tha

Ye wahi cheez thi jo maine FIX-49 deliver karte waqt "bacha hua" batayi thi:

```python
'is_realtime': ('NSE' in str(active_source) and 'TradingView' not in str(active_source)),
```

Freshness se koi lena-dena nahi — **sirf source ke naam ka substring match**. Do concrete
raaste jahan ye jhooth bolta tha, dono verify kiye:

1. `smart_fetch` stale frame par `src + ' (STALE)'` return karta hai (app.py L1231) —
   `'NSE Official Direct (STALE)'` me bhi `'NSE'` match ho jaata tha → `is_realtime: True`
2. `active_source = 'NSE Direct Live'` tab set hota hai jab NSE quote mile — **stale ho ya
   fresh, farq nahi padta tha** → `is_realtime: True`

Ab single source of truth FIX-49 ka gate hai:

```python
'is_realtime': bool(live_nse and live_nse.get('is_realtime')),
'realtime_reason': (... gate ka reason, ya 'koi live quote nahi — price daily close se'),
```

Measured, real `/api/stock/RELIANCE` (02-Oct, holiday):

```
is_realtime     = False
realtime_reason = 'koi live quote nahi — price daily close se'
data_source     = 'Yahoo Finance'
```

### Ek cheez jo maine galat ki, aur uska hisaab

FIX-49 ke tests me maine `2026-10-02` ko "market khula" fixture banaya tha — jo ab holiday
hai. Calendar lagte hi **mere apne 11 tests toote**. Ye accha hua: isse do design galtiyan
pakdi gayi — (a) fixture ek aise din par bana tha jo trading day hi nahi tha, aur (b) end-to-end
stub test **wall-clock par depend** karta tha (Monday subah ya holiday par verdict badal jaata).
Ab `now` freeze hota hai (`_FrozenDT` = 01-Oct-2026 09:31, ek asli trading day), to test jis
din chale wahi jawab dega.

Do purane FIX-47 tests bhi update karne pade — wo `2026-10-02` ko "Friday" maan kar expected
session batate the. Weekend fixture 26/27-Sep par shift kiya, jo kisi holiday ke paas nahi hai.

`verify_live_quote.py` 86 → **114 checks** · full regression **753 passed, 0 failed**.

---

## FIX-49 addendum — 2026-10-02 (live-quote freshness gate: stale price "LIVE" kehlata tha)

### Kaise pakda gaya

FIX-48 ke baad user ne apna server log paste kiya — 02-Oct-2026 09:23 IST, **Friday,
market 8 minute se khula**. Har 5m/1h frame `18.2h old` dikha raha tha. Pehla sawaal ye
tha ki ye app ka bug hai ya data ka. App ko blame karne se pehle upstream se poocha:

```
sandbox abhi (IST): 2026-10-02 09:31  Fri
RELIANCE.NS  5m  range=1d   bars=  0
RELIANCE.NS  1m  range=1d   bars=  0
RELIANCE.NS  5m  range=5d   bars=298  last_bar=10-01 15:15  age=18.3h
RELIANCE.NS  1h  range=5d   bars= 28  last_bar=10-01 15:15  age=18.3h
RELIANCE.NS  1d  range=1d   price=1167.7  mktTime=10-01 15:15
RELIANCE.NS  daily closes: 09-30=1187.0 · 10-01=1167.7 · 10-02=None
```

Do nateeje:

1. **App ka STALE cascade sahi tha.** Aaj ki session ka ek bhi bar upstream par maujood
   nahi tha — reject karna aur `is_realtime=False` bhejna bilkul correct behaviour tha.
2. **Par usi response me asli bug chhupa tha** — `regularMarketPrice=1167.7` ke saath
   `regularMarketTime=2026-10-01 15:15`, yaani **18.3 ghante purana**, jab market khula tha.

### Bug

`fetch_yahoo_live_ltp()` (app.py L557) us `regularMarketTime` ko **sirf ek display string
banane ke liye** padhta tha:

```python
mkt_time = meta.get('regularMarketTime')
ts = (datetime.fromtimestamp(mkt_time, tz=IST).strftime('%H:%M:%S')
      if mkt_time else datetime.now().strftime('%H:%M:%S'))   # ← missing = abhi ka waqt!
return { ..., 'timestamp': ts, 'is_realtime': True, ... }    # ← hardcoded
```

Poore file me `regularMarketTime` ka **ek hi reference** tha. Freshness kahin verify nahi
hoti thi, aur timestamp missing ho to `datetime.now()` quote ka waqt maan liya jaata tha —
actively misleading.

Nateeja user ki screen par: **chart "DELAYED" bolta tha aur header wala price "LIVE"** —
dono ek saath, ek hi screen par, ek hi data se. `Dashboard.html` me chip
`stale: t.stale || !t.is_realtime` se banta hai, isliye `is_realtime: True` ne chip ko
jhootha "LIVE" bana diya tha.

`fetch_nse_live_ltp()` me bhi wahi pattern tha — NSE khud `timestamp` bhejta hai par code
`datetime.now()` likh deta tha.

Ye wahi **fail-open class** hai jo FIX-47 ne OHLC frames ke liye band ki thi; live-quote
path usme cover nahi hua tha.

### Fix

`frame_is_fresh()` ka **scalar twin** — `quote_is_fresh(mkt_time, now)` — plus
`_parse_quote_ts()` (Yahoo epoch seconds aur NSE ke `'02-Oct-2026 09:31:00'` dono).

- Market **khula** → quote `LIVE_MAX_AGE_MIN` (default 10) se purana nahi hona chahiye
- Market **band** → quote ka date `last_completed_session()` se zyada peeche nahi
  (`CLOSED_GRACE_DAYS = 1` grace, weekend skip) — FIX-47 wali hi logic
- Timestamp **missing/unparseable → STALE (fail-CLOSED)**
- `timestamp` ab quote ka **asli** waqt hai; missing ho to `--:--:--`, fake `now()` nahi
- Price **phir bhi serve hota hai** — gate sirf label badalta hai, UI kabhi blank nahi
- `STOCKAI_LIVE_MAX_AGE_MIN` se limit aur `STOCKAI_LIVE_GATE=off` se kill-switch (`.env` me bhi)
- Console warning **5 min me ek baar per symbol** — warna 2 s refresh par console bhar jaata

### Measured (real Yahoo, live, market khula)

`GET /api/quote/RELIANCE` — pehle vs ab:

```jsonc
// pehle
{ "price": 1167.7, "timestamp": "09:31:04", "is_realtime": true }          // ← jhooth

// ab
{ "price": 1167.7, "timestamp": "15:15:00", "is_realtime": false,
  "stale": true, "quote_time": "2026-10-01 15:15:00",
  "stale_reason": "STALE: quote 1126m purana > 10m limit",
  "source": "yahoo.ns" }
```

Console par ek line (throttled):

```
⚠️  [LIVE Yahoo] RELIANCE — STALE: quote 1126m purana > 10m limit → is_realtime=False
```

Boundaries measured: 0 / 9.9 / 10.0 min → FRESH · 10.1 / 25 / 1096 min → STALE.
Timezone: naive, IST-aware aur UTC-aware `now` teeno **same** jawab dete hain — FIX-47 wali
tz fail-open class yahan repeat nahi hui.

### Side-fix

NSE quote pehle `source: 'unknown'` report karta tha (`get_live_quote` ka
`setdefault('source', 'unknown')` lag jaata tha). Ab `'nse'` jaata hai.

### Ek cheez jo **nahi** badli

`change%` ka `prevClose` **pehle se sahi tha** — verify kiya. `range=1d` se
`chartPreviousClose = 1187.0` = 30-Sep ka close ✅. (`range=5d` `1197.6` = 28-Sep deta, jo
galat hota.) Purani note "`range=5d` prevClose badal deta hai" confirm hui, aur app pehle
se sahi side thi. Koi change nahi kiya.

`verify_live_quote.py` 50 → **86 checks** · full regression **725 passed, 0 failed**.

---

## FIX-48 addendum — 2026-10-01 (config provenance: token kahan se aaya)

### Shuruaat ek galat salah se hui

User ne apna server log paste kiya. Maine dekha `🔒 Token auth ON` aur **do baar kaha
"`.env` me `STOCKAI_API_TOKEN` badal do"** — kyunki token us chat me paste hua tha aur maine
maan liya ki wo `.env` se aa raha hai.

User ne kaha: *".env me b5232050 jaisa koi token nahi hai."* Wo sahi tha. Verify kiya:

- wo token working tree me **kahin nahi**
- `git log --all -S "b5232050"` → **khaali** (poori history me bhi nahi)
- `.env.example` me `STOCKAI_API_TOKEN=` khaali
- code me token ka **ek hi source**: `os.environ.get('STOCKAI_API_TOKEN')`

To token environment se aa raha tha, `.env` se nahi. User ne check kiya:

```
[Environment]::GetEnvironmentVariable('STOCKAI_API_TOKEN','User')     → b5232050…
[Environment]::GetEnvironmentVariable('STOCKAI_API_TOKEN','Machine')  → (khaali)
```

**Windows User-scope env var.** Delete kar diya gaya.

### Asli design gap

Sirf "galat jagah dhoondh liya" nahi tha — app me do cheezein mili jo is confusion ko
*banati* hain:

**1. `load_dotenv_file()` ka default `override=False` hai.** Matlab jo key pehle se
`os.environ` me ho, us par `.env` ka value **lagta hi nahi**. Measure kiya:

```
.env file me likha tha : FROM_DOTENV_FILE
os.environ me pehle tha: FROM_WINDOWS_ENV
-> app jo use karega   : FROM_WINDOWS_ENV
```

Yaani user `.env` me token daal kar usse change karne ki koshish karta, aur kuch nahi hota —
chup-chaap. Ye standard dotenv behaviour hai, par bina bataye confusing hai.

**2. Banner source nahi batata tha.** Wo sirf `🔒 Token auth ON` kehta tha — ye nahi ki token
`.env` se aaya ya Windows env se. Isliye user (sahi tarah se) `.env` me dhoondhta reh gaya.

### Fix

`config_source(key)` helper, jo `.env` load hone se **pehle** ka environment snapshot
(`PRE_DOTENV_KEYS`) leta hai — uske bina provenance batana namumkin hai:

| state | source |
|---|---|
| sirf `.env` me | `.env` |
| sirf env var me | `environment variable` |
| **dono me** | `environment variable (.env ko override kar raha hai)` + actionable fix |
| kahin nahi | `default (kahin set nahi)` |

Banner ab (real `app.py` startup se, teeno cases measured):

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

```
⚠️  STOCKAI_API_TOKEN set NAHI hai → LAN ka koi bhi device ye API use kar sakta hai.
```

`verify_security.py` 118 → **126 checks** (chaaron provenance cases + banner wiring +
snapshot-order assert). Full regression: **689 checks, 0 failed.**

### Jo user ko batana zaroori hai

- Wo token **jal chuka hai** (chat me kai baar aaya). Dobara token chahiye to **naya**
  generate karna, wo string reuse nahi.
- Token auth ab OFF hai — LAN ka koi bhi device API use kar sakta hai. Ye user ka apna
  chuna hua trade-off hai ("token ka jhanjhat nahi chahiye").
- Env var hatne ke baad `.env` ab actually kaam karta hai (verify kiya).
- Windows me env var hatane ke baad **naya terminal** zaroori hai — purana `$env:` cache
  rakhta hai.

---

## FIX-47 addendum — 2026-10-01 (freshness guard market band hone par bypass + duplicate log lines)

User ne apna server log paste kiya aur poocha "sahi aa rha he na". Log ka core healthy tha,
par do cheezein pakdi gayi — ek data-correctness, ek cosmetic.

### 1. Freshness guard market band hote hi poora bypass ho jaata tha

Log me:
```
⚡ [NSE Official Direct] TCS · 345 bars · market closed, last bar 45.6h old (fine)
🌐 [Yahoo] ^NSEI (1d) · 496 bars · market closed, last bar 16.1h old (fine)
```

Measure kiya: Yahoo par TCS ka daily bar `2026-10-01` tha (16.2h), matlab **aaj ka session
available tha** — par NSE-direct tier ~45.6h purana bar de raha tha (**29.5h ka gap, kam se
kam ek poora session peeche**). Aur `frame_is_fresh()` ne use pass kar diya, kyunki uska
pehla branch tha:

```python
if not open_now:
    return True, f'market closed, last bar {human} old (fine)'
```

Extreme test se confirm hua ki ye kitna gehra tha:

| frame | pehle |
|---|---|
| 16.1h purana | `fresh=True` → "fine" |
| 45.6h purana | `fresh=True` → "fine" |
| **10 DIN purana** | `fresh=True` → "fine" |

Code ka comment kehta tha *"market band ho to freshness enforce nahi karte (kuch naya hai
hi nahi)"* — **wo reasoning galat thi**. Market band ho tab bhi ek *latest completed
session* hota hai, aur usse purana bar sach me stale hota hai. Nuksaan: user ko TCS ka
chart aaj ka session missing dikha, aur log ne "fine" kaha.

**Fix:** market band ho tab minute-level limit ki jagah **session-level** compare:

```python
def last_completed_session(now=None):
    """Sabse recent weekday (IST) jiska cash session complete ho chuka hai."""
```

`CLOSED_GRACE_DAYS = 1` rakha hai taaki ek akel market holiday false-positive na ban jaaye;
weekend ka gap `last_completed_session()` khud skip karta hai. Minute-limit market band
hone par lagate to har roz close ke baad sab kuch stale dikhta — isliye date-level compare.

Measured, `now = 2026-10-01 16:06` (Thu, market closed, latest session 2026-10-01):

| frame | ab |
|---|---|
| 2026-10-01 (aaj ka session) | `fresh=True` → "last session 2026-10-01 (latest 2026-10-01) — fine" |
| 2026-09-30 (1 peeche, grace) | `fresh=True` → fine |
| **2026-09-29 (TCS wala)** | **`fresh=False` → "STALE: … (2d behind)"** |
| 2026-09-21 (10 din) | `fresh=False` → STALE |

Cascade end-to-end test (teeno scenario, fetchers stub karke):

| scenario | natija |
|---|---|
| NSE-direct 2 session purana, Yahoo fresh | NSE **REJECTED** → **Yahoo Finance** serve hua ✓ |
| holiday — teeno tiers ek hi session par | `🟡 [NO NEWER SESSION]` honest message ✓ |
| sab fresh | pehla tier hi jeeta, extra fetch nahi ✓ |

**Holiday false-alarm se bachna:** sab tiers reject hone par agar teeno **ek hi** session
par agree karte hain to message `STALE DATA` ki jagah `NO NEWER SESSION` hota hai — kyunki
wo "feed stale" nahi, "us din session tha hi nahi" ho sakta hai. Holiday calendar nahi hai,
isliye ye farq karna zaroori tha.

### 2. Do fail-open paths jo test ne pakde (mere apne test ne)

Fix likhne ke baad jab maine aware `now` ke saath test kiya, tab **do aur bug** mile — dono
usi "exception chhup jaana" class ke:

**(a) `frame_age_minutes()`** aware−naive subtraction par `TypeError` deta tha, jo
`except Exception` me chhup kar `None` ban jaata tha — aur `None` ko `frame_is_fresh`
*"age unknown"* keh kar **FRESH** maan leta tha. Ab `_naive_ist()` helper pehle normalize
karta hai.

**(b) `is_market_open()`** caller ke `.hour`/`.minute` ko as-is padhta tha — ek aware UTC
datetime (`10:36Z` = `16:06 IST`, market **band**) ko `10:36 IST` maan kar market **KHULA**
bata deta. Production me `_now=None` hota hai isliye trigger nahi hua, par wahi class thi.

Ab teeno representation ek hi instant par same verdict dete hain:

| `now` | `is_market_open` | TCS-stale frame |
|---|---|---|
| naive IST `16:06` | False | `fresh=False` |
| aware IST `16:06+05:30` | False | `fresh=False` |
| aware UTC `10:36Z` | False | `fresh=False` |

### 3. Duplicate request-log lines

User ke log me har line do baar aa rahi thi:
```
127.0.0.1 - - "GET /api/stock/RELIANCE HTTP/1.1" 200 -
INFO:werkzeug:127.0.0.1 - - "GET /api/stock/RELIANCE HTTP/1.1" 200 -
```

Mechanism reproduce karke confirm kiya (guess nahi): werkzeug ka `_log()` pehli baar log
hone par khud ek handler add kar leta hai **agar us waqt koi level-handler na mile**. Uske
*baad* koi library (`tvDatafeed` suspect hai) `logging.basicConfig()` call kar deti hai, jo
root par StreamHandler laga deti hai — aur werkzeug root par propagate karta hai:

```
>>> werkzeug.handlers ab = [<_ColorStreamHandler <stderr> (NOTSET)>]
>>> [B] ab koi library basicConfig() karti hai
127.0.0.1 - - "GET /api/search?q=b HTTP/1.1" 200 -
INFO:werkzeug:127.0.0.1 - - "GET /api/search?q=b HTTP/1.1" 200 -
```

**Fix:** `_configure_werkzeug_logging()` — explicit handler + `propagate = False`. Handler
jaan-boojh kar khud add karte hain: agar `basicConfig()` pehle hi chal chuka ho to werkzeug
`_has_level_handler()` True pa kar apna handler add nahi karta, aur sirf `propagate=False`
karne par output **poora gayab** ho jaata.

Verified: 2 requests → **2 lines** (pehle doosri request 2 lines deti thi). Aur token
masking intact — asli token **0** baar print hua, `token=***` **2** baar.

### Verifier

`verify_live_quote.py` 38 → **50 checks**. Ek purana check **ulta assert kar raha tha** —
`'stale bar accept (market closed — naya kuch hai hi nahi)'` — yaani verifier ne bug ko
lock kar rakha tha. Use naye sahi behaviour par flip kiya, plus grace/weekend/tz cases aur
duplicate-logging guards add kiye.

Full regression: **681 checks, 0 failed.**

---

## FIX-46 addendum — 2026-10-01 (Pyrefly `bad-unpacking` × 4 in `app.py`)

User ne VS Code (Pyrefly) ke 4 diagnostics paste kiye — `app.py` lines **1443, 1469, 1476,
1483**:

> Expected argument after ** to be a mapping, got: `dict[str, float | int] | ... | float |
> int | list[float] | str` in function `GradientBoostingClassifier.__init__`

**Ye runtime bug nahi tha.** `CONFIG` ek heterogeneous dict hai (floats, ints, strings,
lists, nested dicts), isliye checker `CONFIG['ML_GB_PARAMS']` ka type *poore value-union*
ke roop me infer karta hai aur `**` unpack par complain karta hai. Runtime par wo chaaron
values sach me `dict` hain — verified: classifiers bante hain aur hyperparameters exactly
wahi land karte hain (`n_estimators=120`, `max_depth=5`, `max_iter=1000`).

**Reproduce karna zaroori tha, warna "fix" ka koi meaning nahi.** Sandbox ke
scikit-learn 1.7.2 me `py.typed` marker nahi hai, isliye default single-file mode me
Pyrefly sklearn ko untyped maan kar `__init__` signature dekhta hi nahi — aur **0 errors**
deta hai. Do cheezein chahiye thi: ek `pyrefly.toml` (project mode, site-packages resolve)
aur `preset = "strict"`. Tab pre-fix code par **exactly wahi 4 errors, exactly unhi lines
par** mile: `[1443, 1469, 1476, 1483]`.

**Fix:** bare `cast` se lint chup karane ke bajaye ek validated helper:

```python
_ML_PARAM_KEYS = ('ML_GB_PARAMS', 'ML_RF_PARAMS', 'ML_LR_PARAMS', 'ML_XGB_PARAMS')

def ml_params(key: str) -> Mapping[str, Any]:
    ...
    if not isinstance(params, dict):
        raise TypeError(...)
    return params
```

Ye `cast` se behtar hai kyunki checker ko mapping prove karta hai **aur** galti se non-dict
value aane par loud `TypeError` deta hai (sklearn ke confusing error ke bajaye). Paanchon
unpack sites (`ML_GB_PARAMS` ×2 walk-forward + final, `ML_RF_PARAMS`, `ML_LR_PARAMS`,
`ML_XGB_PARAMS`) ab isi se jaate hain.

**Return type chunna measured tha, guess nahi.** `preset = "all"` par chaar variants test kiye:

| annotation | total errors | bad-unpacking | explicit-any | bad-argument-type |
|---|---|---|---|---|
| bare `dict` | 1068 | 0 | 0 | 5 |
| `dict[str, Any]` | 1067 | 0 | **1** | 5 |
| `dict[str, int \| float \| str]` | 1140 | 0 | 0 | **78** |
| `dict[str, object]` | 1140 | 0 | 0 | **78** |
| **`Mapping[str, Any]`** | **1067** | **0** | **1** | **5** |

Concrete value types (`object` / `int|float|str`) `bad-argument-type` ko 5 → **78** kar
dete hain, kyunki `**` unpack par checker har param ko us type se match karne ki koshish
karta hai. Isliye `Mapping[str, Any]` chuna.

**Ek correction:** maine pehle kaha tha "`Mapping[str, Any]` ne `explicit-any` = 0 diya".
Wo **galat tha** — us test loop me `Mapping` import hi nahi tha, isliye annotation
*unresolved* thi aur Pyrefly kuch flag hi nahi kar sakta tha. Properly import karne par
`explicit-any` = 1 aata hai — lekin **sirf `preset = "all"` par**, jo poore file me
**1,067** errors deta hai. User ko sirf 4 dikhe the, isliye unka config `all` nahi hai.

**Final measured result, `preset = "strict"`** (jo user ke 4 diagnostics reproduce karta hai):

| | PRE (HEAD) | POST (fix) |
|---|---|---|
| total errors | 262 | **258** |
| `bad-unpacking` | **4** (lines 1443/1469/1476/1483) | **0** |
| new error kinds introduced | — | **none** |

`preset = "basic"` (default) par dono taraf 0 — wo preset sklearn resolve nahi karta.

**Verifier:** `verify_fixes.py` 31 → **53 checks**. Naya block `[6]` assert karta hai ki
`**CONFIG[` pattern wapas na aaye, helper live `CONFIG` dict hi return karta hai (copy
nahi — mutation semantics unchanged), guard dono failure modes par fire karta hai, aur
teeno sklearn classifier purane call ke byte-identical params ke saath bante hain.

Full regression: **669 checks, 0 failed.**

---

## FIX-45 addendum — 2026-10-01 (summary label exact + TATAMOTORS → TMPV; M-8/M-9 correction)

Teen cheezein, teeno measured.

**1. M-8 / M-9 pehle se band the — mera claim galat tha.** Maine kai baar kaha "M-8/M-9
bache hain". Wo galat tha. Dono **FIX-39** me close ho chuke the: code me
`_normalize_columns()` + `_pick_col()` (M-8) aur `nse_master_cache.json` (24 h TTL) +
background refresh + `STOCKAI_OFFLINE` (M-9) maujood hain, aur
`tools/verify_startup_offline.py` ke checks unhe cover karte hain. Is file ki table me
dono rows pehle se ✅ thi — sirf neeche wala prose paragraph stale reh gaya tha, aur main
usi ko padh kar bolta raha. Ab wo line correct kar di gayi hai. **Audit me ab koi item
open nahi hai.**

**2. `TATAMOTORS` → `TMPV` (demerger, delisting nahi).** Scanner har run par
`⚠️ TATAMOTORS Skipped` dikhata tha. Maine pehle ye maan liya tha ki Yahoo par data nahi
hai; measurement ne asli wajah batayi:

| probe | result |
|---|---|
| `TATAMOTORS.NS` / `.BO` / `TATAMOTOR.NS` / `TATAMTRDVR.NS` | sab **HTTP 404** |
| Yahoo search "Tata Motors" | `TMPV.NS` = Tata Motors Passenger Vehicles Ltd, `TMCV.NS` = Tata Motors Ltd |
| `TMPV.NS` | **1,241 bars**, 2021-10-01 se (poori history) |
| `TMCV.NS` | 225 bars, 2025-11-12 se (listing ke baad se) |

Tata Motors ka demerger 1 Oct 2025 se effective hua; NSE ticker `TATAMOTORS` → **`TMPV`**
(24 Oct 2025). NSE ke demerger rules ke mutabik **demerged company (TMPV) Nifty 50 me
rehti hai**, aur CV arm (`TMCV`, listed 12 Nov 2025) constant price par kuch sessions ke
baad indices se exclude hota hai. Isliye `TMPV` hi sahi successor hai — aur uske paas
calibration ke liye poori history bhi hai (`TMCV` ke 225 bars naakaafi hote).

Badla: `score_calibration.UNIVERSE`, `nifty_scanner.SECTOR_MAP`, aur
`tools/build_ml_edge_study.py` ke `DEFAULT_SYMBOLS`. Universe size 30 hi raha.

**3. Summary label ab exact hai.** Pehle `"BUY" in signal` / `"SELL" in signal` se count
hota tha, isliye `23 SELL` me 17 SELL + 6 STRONG SELL chhupe rehte the aur STRONG BUY kabhi
alag dikhta hi nahi tha. Ab `Counter` se per-band exact count chhapta hai
(`STRONG BUY | BUY | WATCH | SELL | STRONG SELL | UNRATED`, sirf non-zero bands).

**Universe change ka consequence — teeno artifacts rebuild kiye** (`validate_artifact`
universe match maangta hai, warna fail-closed):

| artifact | pehle | ab |
|---|---|---|
| `score_calibration.json` | 250 sessions, 7,250 scores, **29** symbols | 250 sessions, **7,500** scores, **30** symbols |
| `scanner_bands.json` | 7,250 stock-sessions, 29 symbols, 1 skipped | **7,500** stock-sessions, **30** symbols, **0 skipped** |
| `ml_edge_study.json` | 19 symbols, 53,295 OOS predictions | **20** symbols, **56,100** OOS predictions |

Calibration rank bands **same rahe** (p10 43 / p40 50 / p80 57 / p95 62). Scanner bands me
sirf WATCH edge 51 → **50** hua (p40 badla); STRONG BUY 63 / BUY 60 / SELL 44 same.
ML study ke numbers thode hile (S1 +1.14pp → **+0.96pp ±0.72**, S3 −7.15pp → **−6.99pp**,
null max 59.32% same) — **verdict NO EDGE wahi raha**. Purane hardcoded bands ka measured
natija ab bhi wahi hai: 0 STRONG BUY, 0 BUY, 0 STRONG SELL, 7,162 WATCH (95.5%).

**Verifier note:** `verify_score_calibration` ab **74** checks deta hai (pehle 75). Koi
check gayab nahi hua — wo ek *conditional* check tha ("universe symbol with NO historical
scores cannot get fitted label") jo sirf tab chalta tha jab koi universe symbol bina
history ke ho. TATAMOTORS ke paas history thi hi nahi, isliye wo check chalta tha; TMPV ke
paas poori history hai, isliye ab `⏭ all 30 symbols covered this run` print hota hai.
Ye coverage improve hona hai, regression nahi.

---

## FIX-44 addendum — 2026-10-01 (FIX-43 ka display bug: Score column jhooth bol raha tha)

FIX-43 ne signal ko `ens` par shift kiya, par **display aur sort abhi bhi legacy
`composite` use kar rahe the**. Natija: scanner ki har row self-contradictory thi.
Ye bug user ke live run (2026-10-01 14:26 IST) se pakda gaya — mere sandbox run me
maine sirf summary line dekhi thi, per-row consistency check nahi kiya tha.

**Kya galat dikhta tha (user ka actual output):**

| row | dikhta tha | hona chahiye tha |
|---|---|---|
| NTPC | `Score: 62 → STRONG SELL` | score 42 → STRONG SELL |
| KOTAKBANK | `Score: 47 → BUY` | score 60 → BUY |
| TITAN | `Score: 70 → WATCH` | score 57 → WATCH |
| leaderboard order | `#2 NTPC = STRONG SELL`, `#13 KOTAKBANK = BUY` | BUY sabse upar |

Teen jagah legacy `composite` chhupa hua tha: progress line (`Score:{composite}`),
leaderboard column, aur `results.sort(key=...['composite'])`. Isliye high-ML stocks
(TITAN 87.2%, NTPC 87.9%) top par dikhte the jabki unka signal SELL-band ka tha —
yaani ranking usi noise number se drive ho rahi thi jise FIX-41 ne reject kiya.

**Fix:**
- Display formatting ko functions me nikala: `format_progress_row()`,
  `format_leaderboard_row()`, `signal_icon()` — taaki verifier inhe **actually chala
  sake**, sirf source-grep na kare.
- Teeno jagah ab `signal_score` (ens) chhapta/sort hota hai. Leaderboard se redundant
  `ENS` column hata diya (ab `Score` hi ens hai); `ML%` aur `Edge` diagnostic columns
  ke roop me rahte hain.
- `🏆 Top Validated Buys` → `🏆 Top in BUY band` (kuch validate hua hi nahi).
- `tools/verify_scanner_bands.py` me naya **[6] display consistency** block: 12 checks
  jo synthetic row par wahi functions chalate hain — printed score == `signal_score`,
  composite display me kahin nahi (`r['composite']` refs = 0), ML 88% + edge −12% hone
  par bhi signal score se hi banta hai, aur sort key `signal_score` hai. FIX-45 ne isme exact summary line + valid universe ke checks add kiye — **67 checks total.**

**Live re-run (2026-10-01) fix ke baad:** `1 BUY | 5 WATCH | 23 SELL`, leaderboard
`#1 KOTAKBANK score 60 → BUY`, `#2 TITAN score 57 → WATCH` — ab har row ka Score aur
Signal ek doosre se match karte hain, aur 29/29 rows descending sorted hain (programmatically
verify kiya: 0 contradictions in leaderboard rows, 0 in progress lines, 0 in
`scan_results.json`).

**Sabak jo maine note kiya:** summary line dekh kar "ho gaya" kehna kaafi nahi —
per-row invariant check karna padta hai. Isliye ab verifier display path ko execute karta hai.

---

## FIX-43 addendum — 2026-10-01 (C-3 scanner thresholds: measured, then refitted)

C-3 audit me likha tha *"score scale uncalibrated → 0 BUYs possible"*. Maine pehle
**measure** kiya (aapki approval ke mutabik), aur natija audit ke claim se bhi zyada
kharab nikla: bands sirf uncalibrated nahi the, **structurally unreachable** the.

**Measurement — 29 symbols × 250 sessions = 7,250 stock-sessions**, scanner ke asli
`calculate_indicators`/`calculate_ensemble` se (`tools/analyze_scanner_thresholds.py`):

| quantity | min | p10 | p40 | median | mean | p80 | p95 | max |
|---|---|---|---|---|---|---|---|---|
| ensemble score | 38 | 44 | 51 | 53 | 53.0 | 60 | 63 | 66 |
| composite (ML-neutralised) | 43 | 46 | 50 | 51 | 51.1 | 55 | 57 | 58 |

Purane hardcoded bands ka isi distribution par output:

| signal | count | share |
|---|---|---|
| STRONG BUY | **0** | 0.0% |
| BUY | **0** | 0.0% |
| WATCH | 6,986 | **96.4%** |
| SELL | 264 | 3.6% |
| STRONG SELL | **0** | 0.0% |

**Wajah (algebra + data, dono se confirm):**
1. `BUY` gate `effective_ml_prob >= 52` maangta hai, par `ml_edge < 0` par code usi ko
   **50.0** par neutralise kar deta hai → gate kabhi pass nahi hota. Real scan
   (2026-09-30) me 22/29 stocks neutralised the.
2. `composite >= 70` ke liye `ens >= 86.4` chahiye; observed max ens **66** hai
   (0 / 7,250 sessions). `composite >= 60` ke liye `ens >= 68.2` — wo bhi observed
   range se bahar.
3. Yaani ML-neutralised stock ke liye best possible signal **WATCH** tha, chahe
   technicals kitne bhi strong ho. Aur jo ek BUY nikalta tha (TITAN) wo isi ML number
   se aata tha jise FIX-41 ne noise saabit kiya (+1.14pp ±0.74).

**Fix (measured support ke saath):**
- `tools/build_scanner_bands.py` → **`scanner_bands.json`** (committed): ensemble score
  ke fitted percentiles p95/p80/p40/p10 = **63 / 60 / 51 / 44**, poora histogram,
  distribution stats, purane bands ka measured natija, aur disclosure.
- `nifty_scanner.py`: signal ab `signal_from_bands(ens_score, bands)` se aata hai.
  ML **diagnostic** hai, gate nahi. Payload me `signal_score` + `signal_basis` aate hain.
  Artifact missing / stale (>60d) / tampered / non-monotonic → signal **`UNRATED`**
  (fail CLOSED, guess nahi).
- Fitted bands ka apni hi distribution par asar: STRONG BUY 785, BUY 752, WATCH 2,866,
  SELL 2,260, STRONG SELL 587 — paanchon signals ab non-empty hain.
- `tools/verify_scanner_bands.py`: **41 checks** — histogram se percentiles dobara
  derive karna, purane bands ka dead hona, fail-closed paths, scanner wiring,
  no-overclaim.

**Live scan (2026-10-01) naye bands ke saath:** 0 BUY | 6 WATCH | 23 SELL, signal_score
range 40–57. Ye **legit reading** hai (aaj market weak hai, koi stock p80=60 ke upar
nahi), broken band nahi. Note karein: TITAN ka legacy `composite` 70 tha (ML 76.7% se
inflate) par fitted signal WATCH hai, kyunki ens 57 hai — yahi farq hai jo FIX-41 ke
baad honest hai.

**Scope discipline:** fitted percentiles **relative ranking** hain, profitable signal ka
proof nahi. `composite` field continuity ke liye payload me hai, par signal drive nahi
karta. Net-of-cost sach `RESEARCH_REPORT.md` me hai (ML −25.6% vs B&H +32.5%).

---

## FIX-42 addendum — 2026-10-01 (M-2 dependency pins: measured against PyPI, one dead pin found)

M-2 audit me sirf *"numpy 1.26.4 ke cp313 wheels nahi hain"* likha tha. Poora
`requirements.txt` PyPI ke against dobara measure kiya — aur do nayi baatein nikli,
ek purani galat.

**Naya rule:** har pin ka **cp312 + cp313 + cp314** wheel hona chahiye (ya pure-python
`py3-none-any`), warna `pip` chup-chaap C compile karta hai. Evidence
`tools/build_requirements_lock.py` → **`requirements.lock.json`** me committed hai aur
`tools/verify_dependency_pins.py` (44 checks) offline assert karta hai.

| pin | purana | measured problem | naya |
|---|---|---|---|
| numpy | 1.26.4 | sirf cp312 wheels (35 wheels, sdist fallback) | **2.3.5** (73 wheels, cp312/13/14) |
| pandas | 2.2.3 | cp312/cp313 only — cp314 wheel nahi | **2.3.3** (54 wheels, cp312/13/14) |
| scikit-learn | 1.5.2 | cp312/cp313 only — cp314 wheel nahi | **1.7.2** (30 wheels, cp312/13/14) |
| yfinance | 0.2.44 | install hota hai par **Yahoo ke against dead**: `yf.download("RELIANCE.NS", period="6mo")` → **0 rows**, `JSONDecodeError: Expecting value: line 1 column 1` → `/api/stock` jawab deta tha *"All 3 engines failed"* | **1.7.0** (130 rows) |
| xgboost | 2.1.2 | audit ka "no cp313 wheels" **galat framing** tha — wheels `py3-none-win_amd64` / `py3-none-manylinux*` hote hain, yaani binary par CPython-ABI constraint ke bina; compile kuch nahi hota | **2.1.4** (fit+predict verify kiya) |

`requires_python` bhi check hua: numpy 2.3.5 `>=3.11`, pandas 2.3.3 `>=3.9`, sklearn 1.7.2
`>=3.10`, xgboost 2.1.4 `>=3.8` — teeno supported Python (3.12/3.13/3.14) allow karte hain.
Windows ke liye specifically `numpy-2.3.5-cp314-cp314-win_amd64.whl`,
`pandas-2.3.3-cp314-cp314-win_amd64.whl`, `scikit_learn-1.7.2-cp314-cp314-win_amd64.whl`
aur `xgboost-2.1.4-py3-none-win_amd64.whl` exist karte hain (PyPI JSON se confirm).

**Ek galat shak jo maine measure karke drop ki:** `deep_analyzer.py:280` par
`use_label_encoder=False` xgboost 2.0+ me removed hone ki wajah se TypeError de sakta tha —
test kiya, xgboost 2.1.4 aur 3.4.1 dono par constructor+fit chal gaya. **Koi bug nahi tha.**

**Compatibility proof (sirf pin badalna kaafi nahi):** pinned set install karke poora ML
study dobara chalaya — numbers **byte-identical** rahe (S1 51.16/50.02 = +1.14pp,
S3 54.84 vs 61.99 = −7.15pp, null max 59.32%), aur `ml_edge_study.json` ab apne
`libs` block me python/numpy/pandas/sklearn/xgboost/yfinance versions record karta hai.
Repo me numpy-2.0 ke removed APIs ka scan bhi clean hai (verifier check [7]).

**Note:** sandbox Python 3.13.14 hai, isliye cp314 wheels PyPI metadata se verify hue hain,
3.14 interpreter par install karke nahi. Baaki sab (wheel names, requires_python,
yfinance behaviour, suites) is turn me chalakar dekha gaya.

---

## FIX-41 addendum — 2026-10-01 (C-2 "ML ka asli edge": measured, recorded, published)

C-2 ab tak sirf UI honesty tak simta tha: in-app accuracy ke saath walk-forward band
dikhta tha, par *"edge hai ya nahi"* ka koi recorded, reproducible jawaab nahi tha.
`research/` me purged walk-forward + permutation null pehle se tha — lekin uska natija
sirf `RESEARCH_REPORT.md` me pada tha, aur live app apna **in-sample** number dikhata
raha. Ab wahi study ek artifact me record hoti hai aur UI usi ko quote karti hai.

**Naya tool:** `tools/build_ml_edge_study.py` → `ml_edge_study.json` (repo root, committed).

**Measured result — 19 large-caps, 5y window, 5 purged folds, embargo = label horizon,
53,295 pooled out-of-sample predictions:**

| strategy | accuracy | baseline (majority class) | edge | ±95% CI | verdict |
|---|---|---|---|---|---|
| S1 `ml_dir1_app28` (app ka current design) | 51.16% | 50.02% | **+1.14pp** | ±0.74 | noise band ke andar |
| S2 `ml_dir1_small10` | 50.61% | 50.02% | +0.59pp | ±0.74 | noise band ke andar |
| S3 `ml_ret5atr_small10` (proposed redesign) | 54.84% | 61.99% | **−7.15pp** | ±0.73 | baseline se neeche |
| shuffled-label permutation null (S3 design) | mean 56.36%, max **59.32%** | — | — | — | model is ceiling se upar nahi gaya |

**Verdict: NO EDGE.** Proposed design ka 54.84% shuffled-label ceiling (59.32%) se
neeche hai — matlab wo accuracy random labels par bhi mil jaati. App ka current design
+1.14pp ±0.74 hai, yaani coin-flip se statistically alag nahi. Costs ke baad ka hissa
`RESEARCH_REPORT.md` me hai: 5y net **−25.6%** vs buy-and-hold **+32.5%**, costs
₹46,755 / ₹1 L (≈12.2pp/yr).

**Kya badla code me (koi model/weight change nahi):**
- `app.py`: `load_ml_study()` (mtime/size cache, schema+model check, fail-closed) +
  `ml_study_payload()` → `/api/stock` payload me `ml_study` block; purana
  "single 80/20 split" disclaimer hata.
- `Dashboard.html`: ML panel me recorded OOS study block (verdict + per-strategy table +
  null ceiling + as-of), aur in-app accuracy ko explicitly **diagnostic** bola.
  Artifact missing ho to "OOS ML study absent" — iska matlab *edge hai* nahi hota.
- `tools/verify_ml_edge_study.py`: **46 checks** — artifact integrity, internal
  consistency, verdict ko numbers se dobara derive karna, app wiring (missing/tampered →
  rejected), Dashboard labels, no-overclaim.

**Scope discipline:** ye profitability ka proof nahi hai, sirf predictive-edge test.
Aur "no edge" ko chhupaya nahi gaya — wahi verdict UI me dikhta hai.

---

## FIX-40 addendum — 2026-10-01 (H-11 engine weights: measured, then left alone)

Audit ka claim tha: *"Volume Profile aur Regime near-constant hain — weights redesign
chahiye."* Regime ka hissa FIX-33 me nipat gaya tha (stock-rank weight 0). Volume
Profile ka hissa maine **measure** kiya — aur evidence ne claim ko ulta kar diya,
isliye weights **badle nahi gaye**.

**Naya data (250 completed sessions × 29 stocks = 7,250 observations).** Builder ab har
session/symbol ke chaaron daily engine scores bhi store karta hai (short keys), isliye
composite ko engine scores se dobara banaya ja sakta hai — `verify_engine_history()`
**0 mismatch** deta hai. Artifact me `engine_dispersion` block bhi hai.

Cross-sectional spread (ek din, alag stocks — yahi ranking information hai):

| engine | mean session SD | min | max | flat (<2) sessions | unique values | range | weight |
|---|---|---|---|---|---|---|---|
| Volume Profile | **14.63** | 7.63 | 18.39 | 0.0% | 4 | 35–75 | 0.12 |
| RVOL + CVD + VSA | 13.01 | 6.71 | 17.64 | 0.0% | 28 | 18–90 | 0.20 |
| VCP V2 | 8.59 | 3.95 | 11.84 | 0.0% | 14 | 30–75 | 0.15 |
| SMC / ICT | 7.97 | 5.02 | 12.28 | 0.0% | 11 | 35–85 | 0.15 |

Koi engine cross-sectionally flat nahi hai. Volume Profile sabse *zyada* spread karta
hai (par sirf 4 distinct values par — coarse bucket jaisa). Spearman rho vs composite:
RVOL +0.717, Volume Profile +0.461, SMC +0.397, VCP +0.334. Drop-one simulation (engine
hatakar weights renormalize): band change **33.6% / 34.9% / 32.3% / 25.0%** — yaani
charon engine ranking ko materially badalte hain.

**Faisla:** is evidence par kisi engine ko drop karna ya weights badalna justified nahi.
Badla hota to poori calibration dobara fit karni padti — bina measured reason ke wo sirf
churn hota. Purana "near-constant" observation galat nahi tha, wo *time-series* (ek stock,
alag din) dekh raha tha; ranking ke liye jo matter karta hai wo cross-section hai.

**Naya tool:** `python tools/analyze_engine_dispersion.py` — offline (koi network nahi),
stored per-engine history se cross-sectional + time-series spread, Spearman rho aur
drop-one simulation print karta hai. Weight ka faisla future me isi se ho.

Verified: `tools/verify_score_calibration.py` **75/75** (naye 11 checks — per-engine
history present, key mapping, 0-mismatch integrity, `stock_rank_from_map` round-trip,
dispersion block complete + internally consistent + recomputation se match, koi flat
engine nahi, tampered engine history reject). Regression: startup 46/46, security
118/118, kelly 37/37, sentinels 48/48, risk-basis 45/45, no-fake 23/23, live-quote 38/38.

**Ye diagnostics hain, alpha nahi** — dispersion ye nahi batati ki koi weighting
profitable hai.

## FIX-39 addendum — 2026-10-01 (startup + offline hygiene: M-9, M-8, M-7)

**M-9 — import par network call.** `load_dynamic_nse_stocks()` module level par
NSE ka `EQUITY_L.csv` download karta tha: har import (server *aur* har verifier)
1–3 s network par rukta, aur offline startup chup-chaap 30-stock curated fallback
par gir jaata. Ab: **on-disk cache** `nse_master_cache.json` (24 h TTL,
`NSE_MASTER_CACHE_HOURS`) → cache stale ho to turant purani list + **background
thread** refresh → cache na ho aur `background=True` ho to curated fallback ke
saath background fetch → `STOCKAI_OFFLINE=1` par network bilkul nahi. Fetch fail
ho to purani cache, warna fallback — har step console par likha jaata hai.
Cache corrupt/chhoti/ANSI ho to ignore (crash nahi). Measured: cache se import
**0.59 s**, 2,567 stocks, zero network.

**M-8 — CSV header fragility.** Parsing `row.get(' SERIES')` (leading space) par
tika tha; NSE ne header badla to silently 0 stocks aur app fallback par gir jaata.
Ab `_normalize_columns()` sab headers strip+upper karta hai aur `_pick_col()`
candidates me se match dhundta hai (`SYMBOL`, `NAME OF COMPANY`/`NAMEOFCOMPANY`/
`NAME`, `SERIES`). Unknown columns par **loud None** (silent 0 nahi), aur ≤500
stocks wala adhoora CSV reject hota hai taaki adhoora download cache ko overwrite
na kare.

**M-7 — connection reuse.** Har HTTP call par naya TCP+TLS handshake hota tha.
Ab module-level shared `requests.Session` (`pool_connections=8, pool_maxsize=32`)
Yahoo chart, Yahoo search aur master list — sab use karte hain. Measured:
cold **342 ms → warm 14 ms**. (Cold call abhi bhi network-bound hai — purana
"under 100 ms" claim abhi bhi galat hai, isliye M-7 poori tarah close nahi.)

Verified: `tools/verify_startup_offline.py` **45/45** (header normalization,
_pick_col, 501-row fake CSV end-to-end, series filter, unknown columns/404/adhoora
CSV reject, cache roundtrip + corrupt/binary/chhoti cache, fresh cache par zero
network, stale par background refresh, OFFLINE mode ke 4 cases, fetch-fail
fallbacks, force=True, module-level `background=True`, shared session + wiring).
Regression: security 118/118, kelly 37/37, sentinels 48/48, score-calibration
64/64, risk-basis 45/45, no-fake 23/23, live-quote 38/38.

## FIX-37 / FIX-38 addendum — 2026-10-01 (.env robustness + token URL hygiene)

**FIX-37** — do Windows-specific footguns: (1) Notepad / PowerShell 5.1
`Set-Content` default ANSI (cp1252) me likhta hai aur `.env.example` pure ASCII
nahi hai, isliye UTF-8 padhna `UnicodeDecodeError` deta tha — loader sirf
`OSError` catch karta tha, to app **import par crash** ho jaati. Ab dono catch
hote hain: aisi `.env` silently ignore, app defaults par chalti hai. (2)
`Set-Content -Encoding UTF8` (PS 5.1) BOM likhta hai, jisse pehla key
`\ufeffSTOCKAI_…` ban kar match hi nahi karta tha — ab loader `utf-8-sig` se
padhta hai. Saath me startup par ready-to-click links (`startup_urls()`) aur
browser auto-open (`STOCKAI_AUTO_OPEN=0` se band).

**FIX-38** — token pehli baar `?token=…` se jaata hai, isliye browser history aur
Werkzeug console log me dikh jaata tha:

* Cookie set hote hi HTML pages (`/`, `/dashboard.html`) **302 se clean URL** par;
  baaki query args preserve. `/api/*` par redirect **nahi** (curl/scripts safe).
* Werkzeug logger par `_TokenMaskFilter`: `token=<anything>` → `token=***`.
* Startup par token wali link ke saath **plain link bhi** print hoti hai.

Verified: `tools/verify_security.py` **118/118** (naye 34 checks in dono fixes me —
cp1252 ignore, BOM, `utf-8-sig`, `startup_urls` token/port/bind behaviour,
auto-open flag, 302 + clean Location, cookie on 302, args preserved, `/api/*` par
redirect nahi, LogRecord masking, filter mounted). Exposure ghatata hai, khatam
nahi karta — pehli request ka URL history me ek baar reh jaata hai; public hosting
ke liye HTTPS + reverse proxy zaroori hai.

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
| C-2 | ML has no measurable edge; single-split number shown as proof | ✅ **Answered (FIX-41)** — purged + embargoed walk-forward + shuffled-label null on **56,100 pooled OOS predictions** across 20 large-caps, recorded in `ml_edge_study.json` and shown in the UI. Measured verdict: **NO EDGE** (best design 55.03% vs 62.02% majority baseline = **−6.99pp**; app's own 28-feature design 51.03% vs 50.07% = **+0.96pp ±0.72** = inside the noise band; shuffled-label ceiling 59.3%). The honest finding is published, not hidden — and the in-app accuracy is now labelled a diagnostic. |
| C-3 | Score scale uncalibrated → 0 BUYs possible | ✅ **Answered (FIX-43)** — measured on **7,500 stock-sessions** (30 symbols × 250 sessions, scanner ke asli `calculate_ensemble`): the old hardcoded bands gave **0 STRONG BUY, 0 BUY, 0 STRONG SELL — 95.5% WATCH**. Reason was structural: BUY needed `effective_ml_prob >= 52` but a negative-edge model is neutralised to 50.0, so the gate could never pass; and `composite >= 70` needs `ens >= 86.4` while observed max ens is 66. Bands are now **fitted percentiles** (p95/p80/p40/p10 = 63/60/50/44) stored in `scanner_bands.json`, ML is a diagnostic and no longer gates, and a missing/stale artifact yields `UNRATED` instead of a guess. |
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
| H-11 | Near-constant engine "scores" | ✅ **Measured & closed (FIX-40)** — 250-session cross-sectional dispersion ne "Volume Profile near-constant" claim ko **refute** kiya: meanSD 14.63 (sabse zyada), flat sessions 0%. Charon engine rank me yogdaan dete hain (drop-one se 25–35% band change). Weights **unchanged** — change ka koi measured basis nahi tha |
| H-12 | `NaN → 0.0` fabricates indicators | ✅ Solved (FIX-02 + selector D11) |
| M-1 | `pip install -r requirements.txt` fails | ✅ Solved — `requirements_fixed.txt` verified installable |
| M-2 | numpy 1.26.4 has no cp313 wheels | ✅ **Solved (FIX-42)** — every pin re-chosen for cp312/cp313/cp314 wheel coverage (numpy 2.3.5, pandas 2.3.3, sklearn 1.7.2), evidence committed in `requirements.lock.json`, asserted by `tools/verify_dependency_pins.py` (44 checks). Two audit details corrected by measurement: xgboost's wheels are `py3-none-*` (no ABI constraint — nothing to compile), and `yfinance==0.2.44` was **functionally dead**, not just old. |
| M-3 | No route serves the dashboard | ✅ Solved (FIX-11) — `/` serves `dashboard_fixed.html` |
| M-4 | Every 404 became a 500 | ✅ Solved (FIX-10) |
| M-5 | 8.1 s ML per request | ✅ Solved (FIX-06 cache + `?fast=1`) |
| M-6 | SSE unused and unhardened | ✅ Solved (FIX-12 + dashboard D9 uses it, polling fallback) |
| M-7 | `/api/quote` "under 100 ms" claim | ⚠️ Better (FIX-39 shared session: measured cold 342 ms → warm 14 ms) — cold call abhi bhi network-bound hai, "under 100 ms" claim sahi nahi |
| M-8 | NSE CSV header parsing fragility | ✅ Solved (FIX-39) — columns strip+upper, tolerant `_pick_col`; unknown columns → loud None, silent 0 nahi |
| M-9 | Network call at import time | ✅ Solved (FIX-39) — on-disk cache (24 h TTL), stale par background refresh, `STOCKAI_OFFLINE=1` |
| M-10 | Unknown symbol ~15 s | ✅ Solved (FIX-14 cache) |
| M-11 | `CORS(*)`, no auth/rate limit | ✅ Solved (FIX-35) — CORS allowlist, optional token auth, per-IP rate limit, security headers + CSP |
| M-12 | Search results via `innerHTML` | ✅ Solved (FIX-35) — `safeHtml\`\`` auto-escaping + DOM-API search list; jsdom injection test 14/14 |
| — | TradingView ka fetch silently NSE se BSE par gir jaata tha, aur label batata hi nahi tha | ✅ **Solved (FIX-53)** — user ne FIX-52 ke teen "verify nahi kiya" items par sawaal kiya; verify karne par **teeno meri galtiyan nikli**. `tvDatafeed` se direct measure: `NSE:TCS 2075.00` vs `BSE:TCS 2079.30`, `NSE:RELIANCE 1167.70` vs `BSE:RELIANCE 1166.00`. Yaani **"Yahoo 4.30 off tha" galat tha** — 2079.30 BSE ka close hai, NSE ka nahi. **"Yahoo CAS close miss karta hai" bhi galat** — 11 sessions × 2 stocks = **22 din, 0 mismatch** (Yahoo = TradingView-NSE). **"ML study par asar" bhi nahi.** Asli wajah: `fetch_tradingview` me silent `exchange='BSE'` fallback tha aur caller dono ko `'TradingView Direct'` kehta tha. **Proof:** user ke dashboard ke chaaron numbers (2079.30 / ATR 57.54 / 52W 3336.7 / 1976) TV-BSE se **exact match** — poora analysis BSE data se bana tha jabki calibration NSE universe par fitted hai. Ab `fetch_tradingview` `(df, exchange)` return karta hai, label `'TradingView Direct (NSE|BSE)'`, `/api/stock` me `frame_exchange`, aur Dashboard BSE frame par warn karta hai. `verify_live_quote.py` 160 → **168 checks**, regression **809 passed, 0 failed**. |
| — | Price exchange se match nahi karta tha — aur `is_market_open` me duplicate constant | ✅ **Solved (FIX-52)** — user ne "Moneycontrol/NSE/BSE se compare karo, 100% match ho raha hai" poocha. Yahoo crumb + BSE API se cross-check kiya: **BSE official TCS ₹2079.30 vs app ₹2075.00 (−4.30)**, RELIANCE ₹1166.00 vs ₹1167.70. Karan: Aug-2026 se NSE ka Closing Auction 15:15–15:35 chalta hai aur Yahoo ka `regularMarketTime` dono stocks par exactly **15:15:00** tha — continuous session ka last trade, official close nahi. Saath me **ROE 100x galat** tha (TCS "0.48%" jabki Yahoo raw 0.47743 = **47.74%**) — FIX-09 ka comment maanta tha `dividendYield` aur `returnOnEquity` dono percent me aate hain, par ROE fraction me aata hai, aur heuristic dono branches me galat tha. Aur `is_market_open` me apna literal `15*60+40` tha jo `SESSION_CLOSE_HM` padhta hi nahi tha — naya test pakda. Baaki fundamentals (P/E, P/B, D/E, market cap ÷ shares) Yahoo se **exact match** nikle. **Correction:** maine pehle kaha tha "TradingView BSE se match karta hai" — ek hi data point se nikala tha, aur sandbox me live run ne ulta dikhaya (usi label ke neeche 2075.00). **BSE ko live tier nahi banaya** — ~40 requests ke baad Akamai 403, aur 2s polling me minutes me block ho jaata. `verify_live_quote.py` 141 → **160 checks**, regression **801 passed, 0 failed**. |
| — | Do badges ek hi screen par contradict karte the + browser ki ghadi quote ke waqt ki jagah | ✅ **Solved (FIX-51)** — user ke pasted dashboard me ek hi screen par `DELAYED (15-20 min)` (upar) aur `LIVE` (price ke baju) tha. Karan: **FIX-50 adhoora tha** — maine `app.py` L3585 ka source-name bug theek kiya, par `Dashboard.html` L1111 me uska apna copy (`/NSE/i.test(data_source)`) dekha hi nahi. Saath me `"DELAYED (15-20 min)"` hardcoded jhooth tha (asli staleness 1175 min / 19.6h, aur market hi band tha), aur `setLiveChip` me `new Date().toLocaleTimeString()` tha — yaani **browser ki ghadi** quote ke waqt ki jagah (`yahoo.ns · 10:31:31` jabki quote `2026-10-01 15:15` ka tha). Ab server ek hi `feed_state` ∈ {LIVE, DELAYED, **CLOSED**} + `feed_label` + `quote_age_min` + `quote_time` bhejta hai; UI guess nahi karta. `CLOSED` teesra state isliye zaroori tha kyunki FIX-50 ke baad holiday par quote "fresh" kehlata hai — data ke liye sahi, par "LIVE" jhooth. Saath me 4 label bug: `kpi.master.score` (42) aur `ensemble.score` (37) dono "Master Score" kehlate the; OBV ki koi bhi negative value "0" dikhti thi (measured `obv = -381723268`) aur UI label `obv > 0` se banta tha jabki scoring `obv > obv_ema` use karta hai; Bollinger %B 0.04 par "MID" kehlata tha; aur `debtToEquity` (percentage) bina `%` ke dikhaya jaata tha + falsy-check se asli 0.0 bhi 'N/A' ban jaata tha. **Arithmetic verify kiya — sab sahi nikla** (change%, 52W position, SL=2.5×ATR, T1=1R, breakeven, win-rate LCB, Kelly, master average, model edges, OOS). `verify_live_quote.py` 114 → **141 checks**. |
| — | Holiday ka koi concept hi nahi tha — market band hone par bhi app "market khula" maanti thi | ✅ **Solved (FIX-50)** — **user ne pakda**, maine nahi. Maine FIX-49 me likha tha "market 16 min se khula" (02-Oct-2026 09:31); wo **Mahatma Gandhi Jayanti** tha, NSE/BSE poora din band. `is_market_open()` sirf weekday+time dekhta tha, isliye `True` bola aur FIX-49 ka gate perfectly-sahi data ko `STALE: quote 1096m purana` keh gaya — **false positive**. Wahi bug FIX-47 ko bhi chhuta tha: `last_completed_session()` holiday ko normal weekday maan kar expected session galat batata tha. Ab NSE equity ke **2026 ke 16 weekday holidays** `NSE_HOLIDAYS` me, plus do patch-free update raaste (`STOCKAI_EXTRA_HOLIDAYS` env, optional `nse_holidays.txt`) — zaroori isliye kyunki 15-Jan-2026 original calendar me tha hi nahi, NSE ne circular se baad me add kiya. Calendar purana/khaali ho to crash nahi, weekday-rule par degrade + startup warning. Saath me `/api/stock` ka `is_realtime` bhi theek hua — pehle `'NSE' in str(active_source)` substring match se aata tha, jisme `'NSE Official Direct (STALE)'` bhi True ban jaata tha; ab FIX-49 ke gate verdict se + `realtime_reason`. Measured: holiday 09:31 → `is_market_open=False`, quote **FRESH** (`market closed, quote 2026-10-01 (latest 2026-10-01) — fine`); normal Mon 09:31 → gate utna hi strict. `verify_live_quote.py` 86 → **114 checks**. |
| — | Live-quote freshness verify hi nahi hoti thi — 18 ghante purana price "LIVE" label ke saath jaata tha | ✅ **Solved (FIX-49)** — `fetch_yahoo_live_ltp()` Yahoo ke `regularMarketTime` ko **sirf display string** banane ke liye padhta tha (poore file me ek hi reference), aur `is_realtime: True` **hardcoded** tha; timestamp missing ho to `datetime.now()` quote ka waqt maan leta tha. Measured 2026-10-02 09:31 IST (market 16 min se khula): `regularMarketPrice=1167.7` ke saath `regularMarketTime=2026-10-01 15:15` — **18.3h purana**. Nateeja: chart "DELAYED" aur header price "LIVE", ek hi screen par. `fetch_nse_live_ltp()` me bhi wahi pattern (NSE ka apna `timestamp` ignore, `datetime.now()` likha). Ab `frame_is_fresh()` ka scalar twin `quote_is_fresh()` + `_parse_quote_ts()` (Yahoo epoch + NSE `'02-Oct-2026 09:31:00'`), missing timestamp **fail-CLOSED**, `STOCKAI_LIVE_MAX_AGE_MIN` limit + `STOCKAI_LIVE_GATE=off` kill-switch, aur console warning 5 min/symbol throttle. Price phir bhi serve hota hai — gate sirf label badalta hai. Side-fix: NSE ab `source: 'nse'` bhejta hai, `'unknown'` nahi. `change%` ka prevClose **pehle se sahi tha** (verify kiya) — chheda nahi. `verify_live_quote.py` 50 → **86 checks**. ⚠️ **Correction (FIX-50 me):** is fix me jo "measured 09:31, market 16 min se khula" likha hai wo **galat premise** tha — 02-Oct-2026 Gandhi Jayanti tha, market poora din band. Hardcoded `is_realtime: True` aur fake `datetime.now()` timestamp asli bugs the (holiday par bhi kuch "live" nahi hota), par wo `1126m purana` STALE verdict **false positive** tha. FIX-50 ne holiday calendar add karke use theek kiya. |
| — | Config provenance chhupi thi — token `.env` se aaya ya Windows env se, pata nahi chalta tha | ✅ **Solved (FIX-48)** — shuruaat **meri galat salah** se hui: maine do baar kaha "`.env` me token badal do", jabki wo token **Windows User-scope env var** me tha (verify: working tree + poori git history dono me absent). Do design gap the: `load_dotenv_file()` ka `override=False` default matlab pehle se set key par `.env` ka value **chup-chaap ignore** hota tha, aur banner sirf `Token auth ON` kehta tha — source nahi. Ab `config_source()` (`.env` load se **pehle** ka `PRE_DOTENV_KEYS` snapshot) banner par `↳ source: …` dikhata hai, aur override case me exact removal command bhi. `verify_security.py` 118 → **126 checks**. |
| — | Freshness guard market band hone par poora bypass + duplicate log lines | ✅ **Solved (FIX-47)** — `frame_is_fresh()` market band hote hi `return True, 'market closed … (fine)'` kar deta tha, isliye **10-din purana bar bhi "fine"** kehlata tha. Real case: NSE-direct ne TCS ka bar 2 session purana diya (45.6h) jabki Yahoo ke paas aaj ka session tha (16.2h) — aur cascade ne pehle tier ko "fine" maan kar Yahoo try hi nahi kiya. Ab market band ho tab **session-level** compare hota hai (`last_completed_session()`, `CLOSED_GRACE_DAYS = 1` holiday ke liye). Saath me do **fail-open** paths theek kiye jo mere apne test ne pakde: `frame_age_minutes()` aware−naive `TypeError` ko `except` me chhupa kar `None` deta tha (→ "age unknown" → FRESH), aur `is_market_open()` aware UTC ko IST maan leta tha. Duplicate log lines: werkzeug apna handler khud add karta tha, phir koi library `basicConfig()` se root par handler laga deti → har line 2 baar; ab explicit handler + `propagate = False`. `verify_live_quote.py` 38 → **50 checks** (ek purana check *bug ko hi assert* kar raha tha). |
| — | Pyrefly `bad-unpacking` × 4 in `app.py` (ML param unpacking) | ✅ **Solved (FIX-46)** — type-check issue tha, runtime bug nahi (`CONFIG` heterogeneous hai isliye checker mapping prove nahi kar sakta; runtime par chaaron values sach me `dict` hain, hyperparams unchanged). Reproduce karne ke liye `pyrefly.toml` + `preset = "strict"` chahiye tha — sandbox sklearn 1.7.2 me `py.typed` nahi hai, isliye default mode 0 errors deta hai. `ml_params()` helper se paanchon sites route kiye. Measured, `preset = "strict"`: total 262 → **258**, `bad-unpacking` 4 → **0**, **koi naya error kind nahi**. `verify_fixes.py` 31 → **53 checks**. |
| — | Scanner universe me dead ticker + summary line substring-count | ✅ **Solved (FIX-45)** — `TATAMOTORS` → `TMPV` (demerger, 1 Oct 2025; NSE ticker ab exist nahi karta, chaaron probe HTTP 404). `TMPV` Nifty 50 successor hai **aur** uske paas poori history hai (1,241 bars, 2021‑10‑01 se; `TMCV` ke sirf 225). Summary line ab exact per-band counts deta hai (pehle `"SELL" in signal` se 17 SELL + 6 STRONG SELL ek hi `23 SELL` me chhupe the). Teeno artifacts rebuild: calibration 30 symbols/7,500 scores, bands 7,500 sessions/0 skipped, ML study 20 symbols/56,100 preds. `verify_scanner_bands.py` 53 → **67 checks**. **Audit me ab koi item open nahi.** |

**Honest answer to "sab solve ho gaya?":** all the *code* defects that were fixable in a session are fixed and verified — 15 of them. Two things remain that a patch cannot fix, because they are not bugs:

1. **The ML still has no edge** (C-2) — and FIX-41 measured it properly instead of guessing: 53,295 pooled out-of-sample predictions, purged + embargoed walk-forward, shuffled-label null. Best design is **7.15pp below** its own majority-class baseline and below the noise ceiling; the app's current 28-feature design is +1.14pp ±0.74, i.e. indistinguishable from a coin flip. The verdict is now recorded in `ml_edge_study.json` and printed in the UI. **That is the answer, not a to-do.**
2. **The score thresholds were uncalibrated** (C-3) — and FIX-43 measured and fixed the scanner's half of it: the old bands were not merely uncalibrated, they were *unreachable* (0/7,500 sessions could produce a BUY). Bands are now fitted percentiles committed in `scanner_bands.json`. `ensemble_v2` in the dashboard remains a diagnostic shim. **Fitted percentiles are a relative ranking, not profit** — see `RESEARCH_REPORT.md` for net-of-cost results.

**Correction (FIX-45):** this line previously said M-8 and M-9 were still open. That was
wrong — both were closed by **FIX-39** (see the M-8/M-9 rows in the table above and the
FIX-39 addendum). The stale sentence survived because the table was updated but this
prose paragraph was not. There are now **no remaining audit items**. M-11/M-12 were closed
by FIX-35, M-2 by FIX-42, C-2 by FIX-41, C-3 by FIX-43/44.

### Final word
The bones of this project are better than most "AI trading dashboard" code I get to look at — the fallback design, indicator maths and JSON handling are real engineering. The problem is **the last mile**: the scoring scale is uncalibrated, the ML is presented as validated when it isn't, and (before FIX-07) the position sizer could put a 5.4× leveraged trade on a signal it didn't even trust. Three of those are now addressed; the ML and the calibration remain research work. Until that is done, treat the "verdict", "targets" and "ML probability" as **UI decoration, not advice**.
