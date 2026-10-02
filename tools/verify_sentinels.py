#!/usr/bin/env python3
"""
FIX-32 verification — sf()/si() ke 0 fallback hataye (response + KPI).

Problem: sf(val) ka default 0.0 aur si(val) ka 0 tha. Response build karte waqt
missing indicator '0' bankar JSON me jaata tha aur dashboard use ASLI reading
ki tarah dikhata tha. KPI scoring me isse FAKE VOTES bante the:
    VWAP missing     → 0 → "price > 0" hamesha true → +10 (fake bullish)
    EMA_9/21 missing → dono 0 → -8 (fake bearish)
    StochRSI missing → 50 (fake neutral)

Ab: response me missing → None (dashboard '—'), KPI me missing indicator ka
vote SKIP hota hai + 'basis' (kitne indicators se score bana) aa jaata hai.
Real 0 ab real maana jaata hai (0 ≠ missing).

Run:  python3 tools/verify_sentinels.py
"""
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as A  # noqa: E402

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  → {detail}" if detail else ""))


print("=" * 84)
print(" FIX-32 — sentinel fallbacks (sf/si 0) verification")
print("=" * 84)

print("\n[1] sfx()/six() behaviour")
check("None → None", A.sfx(None) is None)
check("NaN → None", A.sfx(float('nan')) is None)
check("pd.NA → None", A.sfx(pd.NA) is None)
check("real 0 → 0 (missing nahi!)", A.sfx(0) == 0.0 and A.sfx(0) is not None)
check("value rounded", A.sfx(31.27965, 2) == 31.28)
check("garbage string → None", A.sfx('abc') is None)
check("six(1234.6) → 1234", A.six(1234.6) == 1234)
check("six(None) → None", A.six(None) is None)
check("six(0) → 0 (real zero)", A.six(0) == 0)
check("sf(None) → None — original helper bhi ab honest", A.sf(None) is None)
check("si(None) → None — original helper bhi ab honest", A.si(None) is None)
check("sf/si real zero preserve", A.sf(0) == 0.0 and A.si(0) == 0)
check("infinite values missing", A.sfx(float('inf')) is None and A.sf(float('-inf')) is None and A.si(float('inf')) is None)

print("\n[2] KPI: missing indicator par FAKE vote nahi")
n = 60
c = np.linspace(100, 110, n)
bare = pd.DataFrame({'Open': c, 'High': c + 1, 'Low': c - 1, 'Close': c,
                     'Volume': np.full(n, 1e6)},
                    index=pd.date_range('2024-01-01', periods=n, freq='D'))
k = A.calculate_kpi_scores(bare, {})
check("sab indicators missing → intraday neutral 50", k['intraday']['score'] == 50, str(k['intraday']['score']))
check("sab missing → swing neutral 50", k['swing']['score'] == 50)
check("sab missing → longterm neutral 50 (fund bhi nahi)", k['longterm']['score'] == 50)
check("basis '0/7' dikhata hai", k['intraday']['basis'] == '0/7 indicators measured', k['intraday']['basis'])
check("master neutral", k['master']['score'] == 50)
check("master denominator 19 (7+6+6)", k['master']['basis'] == '0/19 indicators measured')

# VWAP missing par bhi baaki indicators vote karte hain (aur koi fake +10 nahi)
rich = bare.copy()
rich['EMA_9'] = c * 1.01; rich['EMA_21'] = c
rich['RSI'] = 45.0
k2 = A.calculate_kpi_scores(rich, {})
check("VWAP missing → vote skip (0/7 nahi, 2/7)", k2['intraday']['basis'].startswith('2/7'), k2['intraday']['basis'])
# purana behaviour would have been: +10 (VWAP=0) +8 (EMA9>EMA21) + 0 = 68
check("purana fake +10 nahi hua (score < 68)", k2['intraday']['score'] < 68, str(k2['intraday']['score']))
check("EMA9>EMA21 ka asli +8 laga", k2['intraday']['score'] == 58, str(k2['intraday']['score']))

print("\n[3] response: /api/stock payload me missing → null (0 nahi)")
app_client = A.app.test_client()
d = app_client.get('/api/stock/RELIANCE').get_json()
ind = d.get('indicators', {})
check("payload me indicators hai", bool(ind))
real_rsi = ind.get('rsi')
check("real RSI numeric hai (0 nahi)", isinstance(real_rsi, (int, float)) and real_rsi != 0, str(real_rsi))
check("atr_basis field aata hai", 'atr_basis' in ind, str(ind.get('atr_basis')))
check("vol_ratio honest formula (÷ vol_sma20)", ind.get('vol_ratio') is None or ind.get('vol_ratio') > 0,
      str(ind.get('vol_ratio')))
check("ST_Direction asli value (-1/0/1) ya None — fake default 1 nahi",
      ind.get('st_direction') is None or ind.get('st_direction') in (-1, 0, 1), str(ind.get('st_direction')))
w52 = d.get('week52', {})
check("week52 high/low numeric ya None", all(k in w52 for k in ('high', 'low', 'position')))
check("risk.atr_basis aata hai", 'atr_basis' in d.get('risk', {}), str(d['risk'].get('atr_basis')))
check("7/7 se upar KPI basis nahi (denominator accurate)",
      int(d.get('kpi', {}).get('intraday', {}).get('basis', '0/7').split('/')[0]) <= 7,
      d.get('kpi', {}).get('intraday', {}).get('basis', ''))
check("master KPI denominator 19", d.get('kpi', {}).get('master', {}).get('basis', '').split(' ')[0].endswith('/19'))

# short-history frame → sma200 missing → sfx None (pehle 0 ban jaata tha)
c80 = np.linspace(100, 110, 80)
short = pd.DataFrame({'Open': c80, 'High': c80 + 1, 'Low': c80 - 1,
                      'Close': c80, 'Volume': np.full(80, 1e6)},
                     index=pd.date_range('2024-01-01', periods=80, freq='D'))
k_short = A.calculate_kpi_scores(short, {})
check("short history: SMA_200 vote skip (longterm basis me dikhta hai)",
      '/6' in k_short['longterm']['basis'] and not k_short['longterm']['basis'].startswith('6/'),
      k_short['longterm']['basis'])

# Close hi missing ho → VWAP/BB/SMA compare nahi, crash nahi.
no_price = rich.copy()
no_price.loc[no_price.index[-1], 'Close'] = np.nan
no_price['VWAP'] = 105.0
k_nan = A.calculate_kpi_scores(no_price, {})
check("missing Close + VWAP → no fake vote / no crash", k_nan['intraday']['basis'].startswith('2/7'),
      str(k_nan['intraday']))

r_atr = A.calculate_risk(100, 2, 80, action='BUY', atr_basis='assumed 2% of price (ATR missing)')
check("directional risk-note par ATR assumption bhi dikhti hai", 'ATR missing tha' in r_atr['risk_note'])
r_atr_measured = A.calculate_risk(100, 2, 80, action='BUY',
                                  plan_measure={'rate': .6, 'n': 200, 'breakeven': .375, 'basis': 'test'},
                                  atr_basis='assumed 2% of price (ATR missing)', measured_wf_accuracy=52.0)
check("measured risk-note par ATR assumption + WF sirf ek baar", 'ATR missing tha' in r_atr_measured['risk_note']
      and r_atr_measured['risk_note'].count('ML walk-forward') == 1)

# Quote ka last Close NaN ho to fake ₹0 nahi aana chahiye; prev missing ho to
# missing change None ho (0% invented nahi), par valid price phir bhi aana chahiye.
fetch, nse, yahoo = A.DATA_MANAGER.smart_fetch, A.fetch_nse_live_ltp, A.fetch_yahoo_live_ltp
try:
    # FIX-55: get_live_quote ab prefer_exch bhejta hai, isliye **k
    A.fetch_nse_live_ltp = lambda _s: None
    A.fetch_yahoo_live_ltp = lambda _s, **_k: None
    q_nan = bare.tail(5).copy()
    q_nan.loc[q_nan.index[-1], 'Close'] = np.nan
    A.DATA_MANAGER.smart_fetch = lambda *a, **kw: (q_nan, 'test')
    A._LIVE_CACHE.clear()
    check("tier-3 quote missing last Close → no quote, not fake ₹0",
          A.get_live_quote('TESTNAN', force=True) is None)
    q_prev = bare.tail(5).copy()
    q_prev.loc[q_prev.index[-2], 'Close'] = np.nan
    A.DATA_MANAGER.smart_fetch = lambda *a, **kw: (q_prev, 'test')
    A._LIVE_CACHE.clear()
    q = A.get_live_quote('TESTPREV', force=True)
    check("tier-3 quote missing prev Close → change + pChange None",
          q and q['price'] > 0 and q['change'] is None and q['pChange'] is None)
finally:
    A.DATA_MANAGER.smart_fetch, A.fetch_nse_live_ltp, A.fetch_yahoo_live_ltp = fetch, nse, yahoo
    A._LIVE_CACHE.clear()

print("\n[4] source-level")
src = (ROOT / "app.py").read_text(encoding='utf-8')
check("sfx/six helpers maujood", "def sfx(" in src and "def six(" in src)
check("indicators dict sfx use karta hai", "'rsi': sfx(L.get('RSI'), 1)" in src)
check("purana fake RSI default gaya", "sf(L.get('RSI'), 50)" not in src.split("def calculate_kpi_scores")[1])
check("purana ST_Direction fake 1 gaya", "si(L.get('ST_Direction'), 1)" not in src)
# sirf CODE check karo — comment/docstring me zikr hona theek hai
_code_txt = "\n".join(l.split('#')[0] for l in src.splitlines())
check("'NSE Equity' invented string CODE me nahi", "'NSE Equity'" not in _code_txt)
check("master list 'sec' ab None", "'sec': None" in src)
check("KPI basis field", "'basis': f'{i_used}/{i_total} indicators measured'" in src)
check("ATR assumed disclosure", "assumed 2% of price (ATR missing)" in src)
dash = (ROOT / "Dashboard.html").read_text(encoding='utf-8')
check("dashboard KPI basis tooltip", "v.basis" in dash)
check("dashboard ATR badge", 'ASSUMED (2% price)' in dash)
check("chart null-volume bars skip karta hai", 'filter(d => Number.isFinite(d.volume))' in dash)

passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print("\n" + "=" * 84)
print(f" RESULT: {passed} passed, {failed} failed")
print("=" * 84)
sys.exit(0 if failed == 0 else 1)
