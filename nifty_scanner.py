"""StockAI FIX108 — completed-daily curated scanner. Relative rank only.
Shared exchange-specific fetch; no invented volume; selected-stock ML on demand.
Not official NIFTY membership, a live feed or a trade approval.
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import math
import threading
import time
from datetime import time as _dtime
from collections import Counter
from zoneinfo import ZoneInfo

# ── FIX-S1: ONE shared TradingView socket (was: a new TvDatafeed() per
#    symbol; with 5 threads that is 30 sockets and the feed kept dropping)
_TV, _TV_LOCK, _TV_OK = None, threading.RLock(), [True]
_IST = ZoneInfo('Asia/Kolkata')


# ── FIX-43 (C-3): fitted signal bands ──
# Signal bands ab measured distribution se aate hain (tools/build_scanner_bands.py
# → scanner_bands.json), hardcoded 70/60/45/35 se nahi. Missing/stale/invalid
# artifact → signal 'UNRATED' (fail CLOSED): purane bands ML-neutralised case me
# 96.4% WATCH dete the aur BUY/STRONG BUY kabhi nahi de sakte the.
from pathlib import Path
BANDS_PATH = Path(__file__).resolve().with_name('scanner_bands.json')
BANDS_SCHEMA = 2
BANDS_MODEL = 'scanner-completed-daily-rvol20-vwap20-v2'
BANDS_MAX_AGE_DAYS = 60
_bands_cache = {'mtime': None, 'size': None, 'data': None}
_bands_lock = threading.Lock()


def load_scanner_bands():
    # Recheck age on EVERY call, including unchanged files. Reject future dates,
    # booleans/non-finite thresholds and the old cumulative-VWAP calibration.
    from datetime import datetime, timezone
    try:
        doc=json.loads(Path(BANDS_PATH).read_text(encoding='utf-8'))
        if doc.get('schema')!=BANDS_SCHEMA or doc.get('model')!=BANDS_MODEL:
            raise ValueError('signal bands schema/model mismatch; rebuild corrected scanner bands')
        b=doc.get('bands',{})
        values=[b[k] for k in ('STRONG BUY','BUY','WATCH','SELL')]
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not 0<=v<=100 for v in values):
            raise ValueError('invalid/non-finite band thresholds')
        if not all(a>b for a,b in zip(values,values[1:])): raise ValueError('bands not strictly decreasing')
        ts=datetime.fromisoformat(doc['generated_at_utc'])
        age=(datetime.now(timezone.utc)-ts).total_seconds()/86400
        if not 0<=age<=BANDS_MAX_AGE_DAYS: raise ValueError('future or expired band timestamp')
        if not isinstance(doc.get('n_stock_sessions'),int) or doc['n_stock_sessions']<2000: raise ValueError('insufficient band fit sample')
        doc['_age_days']=int(age)
        return doc,None
    except Exception as exc:
        return None,f'Signal bands unavailable: {exc}; python tools/build_scanner_bands.py'


def signal_from_bands(score, bands):
    """Fitted ens-percentile bands. Relative ranking hai — profit ka proof nahi."""
    if score >= bands['STRONG BUY']:
        return 'STRONG BUY'
    if score >= bands['BUY']:
        return 'BUY'
    if score >= bands['WATCH']:
        return 'WATCH'
    if score >= bands['SELL']:
        return 'SELL'
    return 'STRONG SELL'


def signal_icon(signal):
    return "🟢" if "BUY" in signal else "🔴" if "SELL" in signal else "🟡"


def format_progress_row(r, idx, total):
    return f"  [{idx:02d}/{total}] {r['symbol']:<11} Score:{r['signal_score']:>3}/100 ({r['signal']}) · ML: select for deep analysis"


def format_leaderboard_row(r, rank):
    vol='N/A' if r.get('vol_ratio') is None else f"{r['vol_ratio']:.2f}x"
    return f"  {rank:<3} {r['symbol']:<12} {r['sector']:<10} ₹{r['price']:.2f} RVOL:{vol} Score:{r['signal_score']} {r['signal']}"


def format_summary_line(results: list) -> str:
    """`📊 Summary:` line — FIX-45.

    Pehle ye inline tha aur `"BUY" in r['signal']` / `"SELL" in r['signal']` se count karta
    tha. Substring match hone ki wajah se `23 SELL` me 17 SELL + 6 STRONG SELL chhupe rehte
    the, aur STRONG BUY kabhi apna alag band nahi dikhata tha (wo "BUY" me gin liya jaata
    tha). Ab exact per-band counts, fixed order me, sirf non-zero bands.

    Function isliye hai (print inline nahi) taaki verifier isse synthetic rows par chala kar
    assert kar sake — FIX-44 ka lesson: inline print verify nahi ho sakta.
    """
    counts = Counter(r['signal'] for r in results)
    order = ['STRONG BUY', 'BUY', 'WATCH', 'SELL', 'STRONG SELL', 'UNRATED']
    parts = [f"{counts.get(b, 0)} {b}" for b in order if counts.get(b, 0)]
    return "📊 Summary: " + (" | ".join(parts) if parts else "(no rows)")


def _tv_connection():
    global _TV
    with _TV_LOCK:
        if _TV is None:
            from tv_history import HistoryDatafeed as TvDatafeed
            _TV = TvDatafeed()
        return _TV


def _nan_safe(o):
    """FIX-S4: scan_results.json used to contain literal NaN tokens."""
    if isinstance(o, dict):
        return {k: _nan_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_nan_safe(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o
from datetime import datetime   # timedelta unused tha (pyflakes) — hataya
from concurrent.futures import ThreadPoolExecutor, as_completed


# FIX-33: scanner ka ML composite calibration ke liye KABHI use nahi hota.
# FIX104: scanner bands retain their separately fitted 30-symbol cohort.
# Dashboard's expanded historical-rank cohort must not silently change scanner bands.
from score_calibration import BASE_UNIVERSE as UNIVERSE
NIFTY_STOCKS = list(UNIVERSE)

SECTOR_MAP = {
    "RELIANCE": "Energy", "TCS": "IT", "HDFCBANK": "Banking", "INFY": "IT",
    "ICICIBANK": "Banking", "SBIN": "PSU Bank", "BHARTIARTL": "Telecom",
    "ITC": "FMCG", "KOTAKBANK": "Banking", "LT": "Infra",
    "WIPRO": "IT", "AXISBANK": "Banking", "MARUTI": "Auto",
    # FIX-45: TATAMOTORS → TMPV (demerger; NSE ticker TATAMOTORS ab exist nahi karta)
    "TMPV": "Auto", "BAJFINANCE": "NBFC", "SUNPHARMA": "Pharma",
    "TITAN": "Consumer", "ADANIENT": "Conglomerate", "POWERGRID": "Power",
    "NTPC": "Power", "ONGC": "Oil&Gas", "COALINDIA": "Mining",
    "TATASTEEL": "Metal", "TECHM": "IT", "ASIANPAINT": "Paint",
    "ULTRACEMCO": "Cement", "NESTLEIND": "FMCG", "BAJAJFINSV": "NBFC",
    "DRREDDY": "Pharma", "JSWSTEEL": "Metal"
}

# ═══════════════════════════════════════════════════════════
#  3-TIER SMART DATA FETCH FOR SCANNER
# ═══════════════════════════════════════════════════════════
def fetch_scanner_data(symbol):
    from daily_context import fetch
    try:
        df=fetch(symbol,'2y','NSE')
        return df,df.attrs.get('source','Unavailable')
    except Exception as exc:
        return None,f'{type(exc).__name__}: {exc}'

def calculate_indicators(df):
    df = df.copy()
    c = df['Close'].astype(float)
    h = df['High'].astype(float)
    l = df['Low'].astype(float)
    v = df['Volume'].astype(float)

    df['EMA_9'] = c.ewm(span=9, adjust=False).mean()
    df['EMA_21'] = c.ewm(span=21, adjust=False).mean()
    df['SMA_50'] = c.rolling(50).mean()
    df['SMA_200'] = c.rolling(200).mean()

    delta = c.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    ag = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    al = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + ag / (al + 1e-10)))

    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    df['MACD'] = e12 - e26
    df['MACD_Sig'] = df['MACD'].ewm(span=9, adjust=False).mean()

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()

    tp = (h + l + c) / 3
    df['VWAP'] = (tp*v).rolling(20).sum()/v.rolling(20).sum().replace(0,np.nan)
    df['Vol_SMA'] = v.shift(1).rolling(20).mean()
    df['Vol_Ratio'] = v/df['Vol_SMA'].replace(0,np.nan)
    df['SESSION_IN_PROGRESS'] = False  # fetch boundary removed partial daily bars

    return df


def calculate_real_ml(df):
    """Compatibility diagnostic; no second independently configured classifier."""
    from daily_context import host
    r=host().ml_engine(df)
    return (r.get('probability'),r.get('walk_forward_accuracy'),r.get('walk_forward_baseline'),r.get('walk_forward_edge'))


def calculate_ensemble(df):
    if len(df)<200: raise ValueError('200 completed bars required; no fake bearish SMA200')
    L = df.iloc[-1]
    scores = []

    # Trend
    s = 50
    sma200 = L.get('SMA_200')
    if pd.notna(sma200) and float(L['Close']) > float(sma200): s += 20
    else: s -= 15
    scores.append(max(10, min(95, s)))

    # Momentum
    s = 50
    rsi = float(L.get('RSI', 50)) if pd.notna(L.get('RSI')) else 50
    if rsi < 30: s += 15
    elif rsi > 70: s -= 15
    macd = float(L.get('MACD', 0)) if pd.notna(L.get('MACD')) else 0
    msig = float(L.get('MACD_Sig', 0)) if pd.notna(L.get('MACD_Sig')) else 0
    if macd > msig: s += 10
    else: s -= 10
    scores.append(max(10, min(95, s)))

    # Volume
    s = 50
    vr = float(L.get('Vol_Ratio', 1)) if pd.notna(L.get('Vol_Ratio')) else 1
    if vr > 1.5: s += 15
    elif vr < 0.5: s -= 10
    vwap = float(L.get('VWAP', 0)) if pd.notna(L.get('VWAP')) else 0
    if pd.notna(L.get('VWAP')):
        if float(L['Close']) > vwap: s += 10
        else: s -= 10
    scores.append(max(10, min(95, s)))

    # EMA Cross
    s = 50
    ema9 = float(L.get('EMA_9', 0)) if pd.notna(L.get('EMA_9')) else 0
    ema21 = float(L.get('EMA_21', 0)) if pd.notna(L.get('EMA_21')) else 0
    if ema9 > ema21: s += 15
    else: s -= 15
    scores.append(max(10, min(95, s)))

    # Volatility
    s = 50
    atr_v = float(L.get('ATR', 0)) if pd.notna(L.get('ATR')) else 0
    atr_pct = atr_v / (float(L['Close']) + 1e-10) * 100
    if atr_pct < 2.0: s += 10
    elif atr_pct > 4.5: s -= 10
    scores.append(max(10, min(95, s)))

    return int(np.mean(scores)), scores


def scan_stock(symbol):
    df,src=fetch_scanner_data(symbol)
    if df is None or len(df)<200: return None
    return analyze_frame(df,symbol,'NSE',src)


def analyze_frame(df,symbol,exchange='NSE',src=None):
    if len(df)<200: raise ValueError('200 completed bars required')
    df=calculate_indicators(df);L=df.iloc[-1];price=float(L.Close)
    score,_=calculate_ensemble(df)
    bands,err=load_scanner_bands()
    stale=bool(df.attrs.get('historical_only',True))
    eligible=exchange=='NSE' and symbol in NIFTY_STOCKS and bands is not None and symbol in bands.get('symbols_scored',[])
    signal='UNRATED' if bands is None or stale or not eligible else signal_from_bands(score,bands['bands'])
    basis=('Historical-only data; rank withheld' if stale else err if bands is None else
        f"Corrected daily relative percentile bands; {bands['n_stock_sessions']} fitted sessions; NOT expected profit")
    if not eligible and not stale: basis='Outside fitted NSE cohort; raw technical score only, no fitted BUY/SELL band'
    return _nan_safe(dict(symbol=symbol,exchange=exchange,source=src,sector=SECTOR_MAP.get(symbol,'General'),
        price=round(price,2),change_pct=round((price/float(df.Close.iloc[-2])-1)*100,2),
        rsi=round(float(L.RSI),1),vol_ratio=round(float(L.Vol_Ratio),2),ensemble=score,
        signal_score=score,signal=signal,signal_basis=basis,composite=None,
        ml_prob=None,ml_acc=None,ml_baseline=None,ml_edge=None,ml_effective=None,ml_used_in_composite=False,
        ml_status='NOT_RUN: select stock for shared full-ensemble walk-forward diagnostics',
        sl=None,t1=None,t2=None,plan_note='No executable plan inferred from relative ranking',
        data_as_of=df.attrs.get('as_of'),historical_only=stale,input_hash=df.attrs.get('input_hash'),
        vwap_label='20 completed daily bars typical-price volume proxy — NOT session VWAP',execution_authorized=False))


def run_full_scan():
    print("\n" + "=" * 78)
    print("  📡 NIFTY SMART SCANNER V3.5 (MULTI-TECH DAILY RANK; DEEP ML ON SELECTION)")
    print(f"  📅 {datetime.now().strftime('%d %b %Y %H:%M:%S')}")
    print(f"  🎯 Scanning {len(NIFTY_STOCKS)} stocks (5 parallel threads)")
    print('  Curated research cohort; NOT current official NIFTY 50 membership. DAILY data only.')
    print("=" * 78)

    results = []
    failures = []
    progress_lock = threading.Lock()
    start_time = time.time()
    completed = [0]
    total = len(NIFTY_STOCKS)

    def scan_with_log(symbol):
        try:
            r = scan_stock(symbol)
        except Exception as exc:
            with progress_lock: failures.append({'symbol':symbol,'reason':str(exc)})
            r = None
        with progress_lock:
            completed[0] += 1
            progress_index = completed[0]
            if r is None and not any(x['symbol']==symbol for x in failures):
                failures.append({'symbol':symbol,'reason':'No valid 200-bar completed NSE history'})
        if r:
            print(format_progress_row(r, progress_index, total))
        else:
            print(f"  [{progress_index:02d}/{total}] ⚠️ {symbol:<11} Skipped")
        return r

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(scan_with_log, sym): sym for sym in NIFTY_STOCKS}
        for future in as_completed(futures):
            try:
                r = future.result()
                if r:
                    results.append(r)
            except Exception as e:
                print(f"  ❌ {futures[future]}: {e}")

    elapsed = round(time.time() - start_time, 1)
    # FIX-44: leaderboard usi score par sort hota hai jo signal deta hai. Pehle
    # composite par sort hota tha → #2 par STRONG SELL aur #13 par BUY aa jaata tha.
    results.sort(key=lambda x: (-x['signal_score'], x['symbol']))

    scan_data = {
        # FIX-79: pehle `datetime.now().isoformat()` tha = NAIVE local time.
        # screener.parse_scan_ts use IST maanta hai, isliye jo machine IST par
        # nahi hai wahan age galat nikalta tha (measured: UTC sandbox par exactly
        # 5.5h ka farq). Ab offset ke saath likhte hain — file self-describing
        # hai, machine ke timezone par depend nahi karti. _IST line 25 par pehle
        # se defined tha.
        'timestamp': datetime.now(_IST).isoformat(),
        'total_scanned': len(results),
        'attempted': total, 'failed': failures, 'schema': 2,
        'universe_note': 'Curated 30-stock research cohort; NOT current official NIFTY 50 membership',
        'scan_time_seconds': elapsed,
        'results': results
    }
    # FIX-S4: strict-JSON output (no NaN/Infinity tokens)
    from daily_context import atomic_json, ROOT
    if not results: raise RuntimeError('All scanner symbols failed; previous scan retained')
    atomic_json(ROOT/'scan_results.json',scan_data)

    print(f"\n{'='*78}")
    print(f"  🏆 RANKED LEADERBOARD (⏱️ {elapsed}s)")
    print(f"{'='*78}")
    print('  #   Stock        Sector      Completed close · prior-20 RVOL · relative score · band')
    print("-" * 78)

    for i, r in enumerate(results):
        print(format_leaderboard_row(r, i + 1))

    # FIX-45: exact per-band counts (pehle substring-match se SELL/BUY mix ho jaate the)
    print("\n  " + format_summary_line(results))
    # FIX-43 (C-3): signal ka basis chhupa nahi — fitted bands ya honest absent-state
    if results:
        print(f"  📐 Signal basis: {results[0].get('signal_basis', 'n/a')}")
        print("  ⚠️ Ye relative ranking hai (fitted percentiles), validated profit nahi.")
    # FIX-44: "Top Validated Buys" overclaim tha — kuch validate hua hi nahi
    # (FIX-41: ML me edge nahi). Ye sirf fitted band me aane wale naam hain.
    top_buys = [r for r in results if r['signal'] in ('STRONG BUY', 'BUY')]
    top_sells = [r for r in results if r['signal'] in ('SELL', 'STRONG SELL')]
    if top_buys:
        print(f"  🏆 Top in BUY band: {', '.join([r['symbol'] for r in top_buys[:5]])}")
    if top_sells:
        print(f"  💀 Top in SELL band: {', '.join([r['symbol'] for r in top_sells[:5]])}")
    print("  💾 Saved to scan_results.json")   # f prefix bekaar tha (pyflakes)
    print(f"{'='*78}\n")

    return results


if __name__ == '__main__':
    run_full_scan()