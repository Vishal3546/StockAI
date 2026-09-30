"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  StockAI V6.0 — Multi-Tech Parallel NIFTY Scanner V3.5                      ║
║  • 3-Tier Multi-Tech Engine (TradingView 0s -> NSE Direct -> Yahoo)          ║
║  • Negative Edge ML Penalty Filter (Eliminates Fake ML Buy Signals)          ║
║  • Off-Market Volume Ratio Fix (No more 0.0x Volume errors)                  ║
║  • 100% Crash-Proof Parallel Worker Execution                               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import math
import threading
import time
from datetime import time as _dtime
from zoneinfo import ZoneInfo

# ── FIX-S1: ONE shared TradingView socket (was: a new TvDatafeed() per
#    symbol; with 5 threads that is 30 sockets and the feed kept dropping)
_TV, _TV_LOCK, _TV_OK = None, threading.RLock(), [True]
_IST = ZoneInfo('Asia/Kolkata')


def _tv_connection():
    global _TV
    with _TV_LOCK:
        if _TV is None:
            from tvDatafeed import TvDatafeed
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
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests as http_requests
import warnings
warnings.filterwarnings('ignore')

NIFTY_STOCKS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT",
    "WIPRO", "AXISBANK", "MARUTI", "TATAMOTORS", "BAJFINANCE",
    "SUNPHARMA", "TITAN", "ADANIENT", "POWERGRID", "NTPC",
    "ONGC", "COALINDIA", "TATASTEEL", "TECHM", "ASIANPAINT",
    "ULTRACEMCO", "NESTLEIND", "BAJAJFINSV", "DRREDDY", "JSWSTEEL"
]

SECTOR_MAP = {
    "RELIANCE": "Energy", "TCS": "IT", "HDFCBANK": "Banking", "INFY": "IT",
    "ICICIBANK": "Banking", "SBIN": "PSU Bank", "BHARTIARTL": "Telecom",
    "ITC": "FMCG", "KOTAKBANK": "Banking", "LT": "Infra",
    "WIPRO": "IT", "AXISBANK": "Banking", "MARUTI": "Auto",
    "TATAMOTORS": "Auto", "BAJFINANCE": "NBFC", "SUNPHARMA": "Pharma",
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
    """FIX-S1: shared TradingView connection, then a pure Yahoo fallback."""
    clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()

    if _TV_OK[0]:
        try:
            from tvDatafeed import Interval
            with _TV_LOCK:
                df = _tv_connection().get_hist(symbol=clean_sym, exchange='NSE',
                                               interval=Interval.in_daily, n_bars=300)
            if df is not None and not df.empty:
                df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                                        'close': 'Close', 'volume': 'Volume'})
                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df.dropna(subset=['Close'])
                if len(df) >= 30:
                    return df, 'TradingView Direct'
            else:
                _TV_OK[0] = False      # feed degraded → stop retrying for this scan
        except Exception:
            _TV_OK[0] = False

    try:
        df = yf.download(f"{clean_sym}.NS", period='2y', interval='1d', progress=False, threads=False)
        if (df is None or df.empty):
            df = yf.download(f"{clean_sym}.BO", period='2y', interval='1d', progress=False, threads=False)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.dropna(subset=['Close'])
            if len(df) >= 30:
                return df, 'Yahoo Finance'
    except Exception:
        pass

    return None, 'None'

def calculate_indicators(df):
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
    df['VWAP'] = (tp * v).cumsum() / (v.cumsum() + 1e-10)
    
    # Volume SMA Fix (Takes previous valid volume if today volume is 0)
    valid_v = v.replace(0, np.nan).ffill().fillna(10000)
    df['Vol_SMA'] = valid_v.rolling(20).mean()
    df['Vol_Ratio'] = valid_v / (df['Vol_SMA'] + 1)

    # FIX-S2: RVOL from the last COMPLETED session. Measured before the fix:
    # vol_ratio was 0.21-0.92 for ALL 30 stocks because the in-progress
    # session was divided by a full-day average — a permanent market-wide
    # penalty. Only applies during market hours on a trading day.
    partial = False
    try:
        _last = pd.to_datetime(df.index[-1])
        _last = _last.tz_localize(None) if getattr(_last, 'tzinfo', None) else _last
        _now = datetime.now(_IST)
        partial = (_last.date() == _now.date()) and (_now.time() < _dtime(15, 30))
    except Exception:
        partial = False
    if partial and len(valid_v) > 22:
        hist = valid_v.iloc[:-1]
        df.loc[df.index[-1], 'Vol_Ratio'] = float(hist.iloc[-1]) / (float(hist.iloc[-21:-1].mean()) + 1)
    df['SESSION_IN_PROGRESS'] = partial

    return df


def calculate_real_ml(df):
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import accuracy_score

        d = df.copy()
        c = d['Close'].astype(float)

        d['ret_1'] = c.pct_change(1)
        d['ret_3'] = c.pct_change(3)
        d['ret_5'] = c.pct_change(5)
        d['ret_10'] = c.pct_change(10)
        d['rsi'] = d['RSI']
        d['macd_h'] = d['MACD'] - d['MACD_Sig']
        d['atr_pct'] = d['ATR'] / (c + 1e-10) * 100
        d['vol_r'] = d['Vol_Ratio']
        d['ema_cross'] = (d['EMA_9'] - d['EMA_21']) / (c + 1e-10) * 100
        d['price_50'] = (c - d['SMA_50']) / (d['SMA_50'] + 1e-10) * 100
        d['target'] = (c.shift(-1) > c).astype(int)

        feats = ['ret_1', 'ret_3', 'ret_5', 'ret_10', 'rsi', 'macd_h', 'atr_pct', 'vol_r', 'ema_cross', 'price_50']

        d[feats] = d[feats].replace([np.inf, -np.inf], np.nan).ffill().bfill()
        d_clean = d.dropna(subset=feats + ['target'])

        if len(d_clean) < 50:
            return 50.0, 0.0, 50.0, 0.0

        train_n = int(len(d_clean) * 0.8)
        X_train = np.nan_to_num(d_clean[feats].iloc[:train_n].values)
        y_train = d_clean['target'].iloc[:train_n].values
        X_test = np.nan_to_num(d_clean[feats].iloc[train_n:].values)
        y_test = d_clean['target'].iloc[train_n:].values

        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_train)
        X_te_s = scaler.transform(X_test)

        gb = GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42)
        gb.fit(X_tr_s, y_train)

        acc = round(accuracy_score(y_test, gb.predict(X_te_s)) * 100, 1)

        pos_rate = float(y_test.mean())
        baseline = round(max(pos_rate, 1 - pos_rate) * 100, 1)
        edge = round(acc - baseline, 1)

        today = scaler.transform(np.nan_to_num(d_clean[feats].iloc[-1:].values))
        prob = round(float(gb.predict_proba(today)[0][1]) * 100, 1)

        return prob, acc, baseline, edge
    except Exception:
        return 50.0, 0.0, 50.0, 0.0


def calculate_ensemble(df):
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
    try:
        df, src = fetch_scanner_data(symbol)
        if df is None:
            return None

        df = calculate_indicators(df)
        L = df.iloc[-1]
        prev = df.iloc[-2]

        ens_score, eng_scores = calculate_ensemble(df)
        ml_prob, ml_acc, ml_baseline, ml_edge = calculate_real_ml(df)

        # 🛡️ FILTER FAKE ML SIGNALS: If ML Edge is Negative, don't trust ML probability
        if ml_edge < 0:
            effective_ml_prob = 50.0 # Neutralize ML contribution if model underperforms baseline
        else:
            effective_ml_prob = ml_prob

        composite = int(ens_score * 0.55 + effective_ml_prob * 0.45)

        if composite >= 70 and effective_ml_prob >= 55:
            signal = "STRONG BUY"
        elif composite >= 60 and effective_ml_prob >= 52:
            signal = "BUY"
        elif composite >= 45:
            signal = "WATCH"
        elif composite >= 35:
            signal = "SELL"
        else:
            signal = "STRONG SELL"

        atr = float(L['ATR']) if pd.notna(L['ATR']) else float(L['Close']) * 0.02
        price = float(L['Close'])

        return {
            'symbol': symbol,
            'source': src,
            'sector': SECTOR_MAP.get(symbol, 'General'),
            'price': round(price, 2),
            'change_pct': round(float((price - float(prev['Close'])) / float(prev['Close']) * 100), 2),
            'rsi': round(float(L['RSI']), 1) if pd.notna(L['RSI']) else 50,
            'vol_ratio': round(float(L['Vol_Ratio']), 2) if pd.notna(L['Vol_Ratio']) else 1.0,
            'ensemble': ens_score,
            'ml_prob': ml_prob,
            'ml_acc': ml_acc,
            'ml_baseline': ml_baseline,
            'ml_edge': ml_edge,
            # FIX-S3: the probability the composite ACTUALLY used (a negative-edge
            # name is neutralised to 50 — the old UI showed the raw 93% anyway)
            'ml_effective': 50.0 if ml_edge < 0 else ml_prob,
            'ml_used_in_composite': ml_edge >= 0,
            'composite': composite,
            'signal': signal,
            'sl': round(price - 1.5 * atr, 2),
            't1': round(price + 2.0 * atr, 2),
            't2': round(price + 3.5 * atr, 2)
        }
    except Exception as e:
        return None


def run_full_scan():
    print("\n" + "=" * 78)
    print("  📡 NIFTY SMART SCANNER V3.5 (MULTI-TECH 3-TIER + ML FILTER)")
    print(f"  📅 {datetime.now().strftime('%d %b %Y %H:%M:%S')}")
    print(f"  🎯 Scanning {len(NIFTY_STOCKS)} stocks (5 parallel threads)")
    print("=" * 78)

    results = []
    start_time = time.time()
    completed = [0]
    total = len(NIFTY_STOCKS)

    def scan_with_log(symbol):
        r = scan_stock(symbol)
        completed[0] += 1
        if r:
            icon = "🟢" if "BUY" in r['signal'] else "🔴" if "SELL" in r['signal'] else "🟡"
            edge_flag = "⚠️ NEG-EDGE" if r['ml_edge'] < 0 else "✅ POS-EDGE"
            print(f"  [{completed[0]:02d}/{total}] {r['source'][:2]} {symbol:<11} {icon} "
                  f"Score:{r['composite']:>3}/100 ({r['signal']:<11}) "
                  f"ML:{r['ml_prob']:>5.1f}% [{edge_flag}]")
        else:
            print(f"  [{completed[0]:02d}/{total}] ⚠️ {symbol:<11} Skipped")
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
    results.sort(key=lambda x: x['composite'], reverse=True)

    scan_data = {
        'timestamp': datetime.now().isoformat(),
        'total_scanned': len(results),
        'scan_time_seconds': elapsed,
        'results': results
    }
    # FIX-S4: strict-JSON output (no NaN/Infinity tokens)
    with open('scan_results.json', 'w') as f:
        json.dump(_nan_safe(scan_data), f, indent=2, allow_nan=False)

    print(f"\n{'='*78}")
    print(f"  🏆 RANKED LEADERBOARD (⏱️ {elapsed}s)")
    print(f"{'='*78}")
    print(f"  {'#':<3} {'Stock':<12} {'Sector':<10} {'Price':>8} {'Chg%':>6} "
          f"{'RSI':>5} {'Vol':>5} {'ENS':>4} {'ML%':>6} {'Edge':>6} {'Score':>5} {'Signal':<12}")
    print("-" * 78)

    for i, r in enumerate(results):
        icon = "🟢" if "BUY" in r['signal'] else "🔴" if "SELL" in r['signal'] else "🟡"
        edge_str = f"{r['ml_edge']:+.1f}%"
        print(f"  {i+1:<3} {r['symbol']:<12} {r['sector']:<10} "
              f"₹{r['price']:>7.0f} {r['change_pct']:>+5.1f}% "
              f"{r['rsi']:>5.0f} {r['vol_ratio']:>4.1f}x "
              f"{r['ensemble']:>4} {r['ml_prob']:>5.1f}% "
              f"{edge_str:>6} {r['composite']:>5} {icon} {r['signal']}")

    buys = [r for r in results if "BUY" in r['signal']]
    sells = [r for r in results if "SELL" in r['signal']]
    watches = [r for r in results if r['signal'] == "WATCH"]

    print(f"\n  📊 Summary: {len(buys)} BUY | {len(watches)} WATCH | {len(sells)} SELL")
    if buys:
        print(f"  🏆 Top Validated Buys: {', '.join([r['symbol'] for r in buys[:5]])}")
    if sells:
        print(f"  💀 Top Sells: {', '.join([r['symbol'] for r in sells[:5]])}")
    print(f"  💾 Saved to scan_results.json")
    print(f"{'='*78}\n")

    return results


if __name__ == '__main__':
    run_full_scan()