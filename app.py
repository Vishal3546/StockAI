"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  StockAI V6.0 — Full Institutional Multi-Tech Hybrid Mastermind Engine       ║
║                                                                              ║
║  ARCHITECTURE & DATA PIPELINE LAYERS:                                        ║
║  • Tier 1 Primary Engine: TradingView Direct Stream (tvDatafeed - 0s Delay)  ║
║  • Tier 2 Official Engine: NSE Direct Scraper (Native Session + Fallbacks)   ║
║  • Tier 3 Universal Engine: Yahoo Finance Universal (yfinance Backup)       ║
║  • Live LTP Engine: Exact Moneycontrol / NSE India Live Sync                 ║
║  • Machine Learning: 4-Model Ensemble (GB + RF + LR + XGBoost)               ║
║  • Validation System: Expanding Window Walk-Forward + Baseline Edge Analysis ║
║  • 6 Institutional Engines + 20+ Indicators + 18 Patterns + Kelly Risk       ║
║  • Production Standard: Python 3.12-3.14 Compatible & NumPy JSON-Safe        ║
║                                                                              ║
║  ZERO CONDENSATION — 100% UNABRIDGED FULL LENGTH CODEBASE                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# ─────────────────────────────────────────────────────────────────────────────
#  V6.1 (2026-09-30) — 15 audit fixes applied in place.
#  Search "FIX-" to see each change; see AUDIT_REPORT.md for evidence.
# ─────────────────────────────────────────────────────────────

import io
import json
import math
import os
import threading
import time
import warnings
from datetime import datetime, timedelta

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
import numpy as np
import pandas as pd
import requests as http_requests

# Suppress all non-critical runtime warnings
warnings.filterwarnings('ignore')

# Initialize Flask Web Application
app = Flask(__name__)
CORS(app)


# ═══════════════════════════════════════════════════════════════════════════
#  SYSTEM CONFIGURATION & HYPERPARAMETERS (100% Zero Hardcoding)
# ═══════════════════════════════════════════════════════════════════════════
CONFIG = {
    'RISK_FREE_RATE': 0.065,
    'MAX_KELLY_PCT': 0.25,
    'REGIME_CACHE_TTL': 600,
    'ML_MIN_DAYS': 50,
    'SUPERTREND_MULTIPLIER': 3.0,
    'SUPERTREND_PERIOD': 10,
    'DEFAULT_CAPITAL': 100000,
    'SEARCH_MAX_RESULTS': 15,
    'CHART_CANDLES': 150,
    'SSE_STREAM_INTERVAL': 3,
    'NSE_MASTER_URL': 'https://archives.nseindia.com/content/equities/EQUITY_L.csv',
    'YAHOO_SEARCH_URL': 'https://query1.finance.yahoo.com/v1/finance/search',
    'ENGINE_WEIGHTS': {
        'Volume Profile': 0.12,
        'RVOL + CVD + VSA': 0.20,
        'VCP V2': 0.15,
        'SMC / ICT': 0.15,
        'Market Regime': 0.18,
        'Multi-Timeframe': 0.20
    },
    'ML_WEIGHTS_3': [
        0.45,
        0.35,
        0.20
    ],
    'ML_WEIGHTS_4': [
        0.35,
        0.25,
        0.15,
        0.25
    ],
    'ML_GB_PARAMS': {
        'n_estimators': 120,
        'max_depth': 3,
        'learning_rate': 0.05,
        'subsample': 0.8,
        'random_state': 42
    },
    'ML_RF_PARAMS': {
        'n_estimators': 150,
        'max_depth': 5,
        'min_samples_leaf': 6,
        'random_state': 42
    },
    'ML_LR_PARAMS': {
        'max_iter': 1000,
        'random_state': 42
    },
    'ML_XGB_PARAMS': {
        'n_estimators': 120,
        'max_depth': 3,
        'learning_rate': 0.05,
        'random_state': 42,
        'verbosity': 0,
        'eval_metric': 'logloss'
    }
}

# Global Caching and Memory Storage
DYNAMIC_STOCK_DB = []
REGIME_CACHE = {
    'data': None,
    'time': None
}

# FIX-03/14: negative caches so a blocked/unknown symbol fails fast
_NSE_BLOCKED_UNTIL = [0.0]
_FAIL_CACHE = {}
_ML_CACHE = {}
_ML_LOCK = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════
#  DIRECT NSE LIVE QUOTE SCRAPER (Exact Moneycontrol & NSE Match)
# ═══════════════════════════════════════════════════════════════════════════
def fetch_nse_live_ltp(symbol):
    """
    Fetches real-time exact LTP, Change, and %Change from official NSE India API.

    FIX-03: NSE blocks datacentre / non-Indian IPs (403 on both the homepage and
    /api/quote-equity — measured). The old code paid a 4s + 4s handshake on EVERY
    call. We now remember the block for 5 minutes and return instantly.
    """
    clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()
    if time.time() < _NSE_BLOCKED_UNTIL[0]:
        return None

    try:
        session = http_requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.nseindia.com/'
        })
        session.get('https://www.nseindia.com', timeout=4)
        res = session.get(f"https://www.nseindia.com/api/quote-equity?symbol={clean_sym}", timeout=4)

        if res.status_code == 200:
            data = res.json()
            price_info = data.get('priceInfo', {})
            ltp = price_info.get('lastPrice')
            if ltp is not None:
                change = price_info.get('change')
                pChange = price_info.get('pChange')
                close_price = price_info.get('close') or price_info.get('previousClose')
                day_hl = price_info.get('intraDayHighLow', {}) or {}
                return {
                    'symbol': clean_sym,
                    'price': round(float(ltp), 2),
                    'change': round(float(change), 2) if change is not None else 0.0,
                    'pChange': round(float(pChange), 2) if pChange is not None else 0.0,
                    'close_price': round(float(close_price), 2) if close_price is not None else round(float(ltp), 2),
                    'dayHigh': round(float(day_hl.get('max') or ltp), 2),
                    'dayLow': round(float(day_hl.get('min') or ltp), 2),
                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                    'is_realtime': True
                }
    except Exception as e:
        print(f"⚠️ Live NSE Quote fetch error for {clean_sym}: {e}")

    # anything other than a clean 200 → assume blocked for 5 minutes
    _NSE_BLOCKED_UNTIL[0] = time.time() + 300
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  3-TIER MULTI-TECH DATA SOURCE MANAGER
# ═══════════════════════════════════════════════════════════════════════════
class MultiTechDataSourceManager:
    """
    Modular 3-Tier Multi-Engine Data Manager:
    • Tier 1 (Primary): TradingView Direct Stream (tvDatafeed - 0s Delay Live Data)
    • Tier 2 (Official Backup): NSE Official Direct (Native Session Scraper + jugaad-data / nsepython)
    • Tier 3 (Universal Backup): Yahoo Finance Universal (yfinance Global Stream)
    """
    
    def __init__(self):
        self.tv = None
        self.init_tv()

    def init_tv(self):
        """Initializes TradingView Datafeed Connection"""
        try:
            from tvDatafeed import TvDatafeed
            self.tv = TvDatafeed()
            print("🟢 [TradingView Engine Initialized] Connected to TradingView Feed.")
        except Exception as e:
            self.tv = None
            print(f"⚠️ [TradingView Notice] Could not initialize tvDatafeed: {e}")

    def fetch_tradingview(self, symbol, n_bars=500, interval_str='1d'):
        """Tier 1 Fetch: Direct TradingView Feed"""
        if self.tv is None:
            return None
            
        try:
            from tvDatafeed import Interval
            clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()
            
            tv_interval = Interval.in_daily
            if interval_str == '5m':
                tv_interval = Interval.in_5_minute
            elif interval_str == '15m':
                tv_interval = Interval.in_15_minute
            elif interval_str == '1h':
                tv_interval = Interval.in_1_hour
            elif interval_str == '1w':
                tv_interval = Interval.in_weekly

            # Primary Attempt: NSE Exchange
            df = self.tv.get_hist(
                symbol=clean_sym,
                exchange='NSE',
                interval=tv_interval,
                n_bars=n_bars
            )
            
            # Secondary Attempt: BSE Exchange Fallback
            if df is None or df.empty:
                df = self.tv.get_hist(
                    symbol=clean_sym,
                    exchange='BSE',
                    interval=tv_interval,
                    n_bars=n_bars
                )

            if df is not None and not df.empty:
                df = df.rename(columns={
                    'open': 'Open',
                    'high': 'High',
                    'low': 'Low',
                    'close': 'Close',
                    'volume': 'Volume'
                })
                
                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                        
                df = df.dropna(subset=['Close'])
                
                if len(df) >= 20:
                    return df
                    
        except Exception:
            pass
            
        return None

    def fetch_nse_direct(self, symbol, days=500):
        """
        Tier 2 Fetch: Official Direct NSE India Scrapers

        FIX-04 applied:
          • The NSE endpoint returns the key `grapthData` (NSE's own typo); the
            old code read `gRapData`, so Method A could never match.
          • Even when it fires, chart-databyindex is ONE intraday session, not
            daily history. The old code bolted Open=High=Low=Close and
            Volume=100000 (constant) onto it — which poisons ATR / BB-width /
            VCP / volume engines. Intraday-only payloads are now refused.
          • Method C called `equity_history_volumes`, which does not exist in
            nsepython; the real function is `equity_history(sym, series, from, to)`.
        """
        clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()

        # Method A: native session scraper (correct key + intraday guard)
        try:
            session = http_requests.Session()
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Referer': 'https://www.nseindia.com/'
            })
            session.get('https://www.nseindia.com', timeout=5)
            res = session.get(f"https://www.nseindia.com/api/chart-databyindex?index={clean_sym}EQN", timeout=6)
            if res.status_code == 200:
                payload = res.json()
                rows = payload.get('grapthData') or payload.get('gRapData') or []
                if rows and len(rows) > 20:
                    df = pd.DataFrame(rows, columns=['Timestamp', 'Close']).dropna()
                    df['Date'] = pd.to_datetime(df['Timestamp'], unit='ms')
                    df = df.set_index('Date').sort_index()
                    if df.index.normalize().nunique() == 1:
                        print(f"   ℹ️ NSE chart-databyindex = intraday only ({len(df)} ticks, 1 session) — refused as daily history")
                        return None
                    df['Open'], df['High'], df['Low'] = df['Close'], df['Close'], df['Close']
                    df['Volume'] = 0.0
                    return df[['Open', 'High', 'Low', 'Close', 'Volume']]
        except Exception:
            pass

        # Method B: jugaad-data (real daily OHLCV) — dynamic import
        try:
            import importlib
            jugaad = importlib.import_module('jugaad_data.nse')
            stock_df_func = getattr(jugaad, 'stock_df', None)
            if stock_df_func is not None:
                end_d = datetime.now().date()
                start_d = end_d - timedelta(days=days)
                df = stock_df_func(symbol=clean_sym, from_date=start_d, to_date=end_d, series="EQ")
                if df is not None and not df.empty:
                    df = df.rename(columns={'OPEN': 'Open', 'HIGH': 'High', 'LOW': 'Low',
                                            'CLOSE': 'Close', 'VOLUME': 'Volume', 'DATE': 'Date'})
                    df['Date'] = pd.to_datetime(df['Date'])
                    df = df.set_index('Date').sort_index()
                    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    df = df.dropna(subset=['Close'])
                    if len(df) >= 20:
                        return df
        except Exception:
            pass

        # Method C: nsepython (FIX-04: correct function name + signature)
        try:
            import importlib
            nsep = importlib.import_module('nsepython')
            eq_hist = getattr(nsep, 'equity_history', None)
            if eq_hist is not None:
                end_d = datetime.now()
                start_d = end_d - timedelta(days=days)
                df = eq_hist(clean_sym, "EQ", start_d.strftime('%d-%m-%Y'), end_d.strftime('%d-%m-%Y'))
                if df is not None and not df.empty:
                    df = df.rename(columns={'CH_OPENING_PRICE': 'Open', 'CH_TRADE_HIGH_PRICE': 'High',
                                            'CH_TRADE_LOW_PRICE': 'Low', 'CH_CLOSING_PRICE': 'Close',
                                            'CH_TOT_TRADED_QTY': 'Volume', 'CH_TIMESTAMP': 'Date'})
                    df['Date'] = pd.to_datetime(df['Date'])
                    df = df.set_index('Date').sort_index()
                    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    df = df.dropna(subset=['Close'])
                    if len(df) >= 20:
                        return df
        except Exception:
            pass

        return None

    def fetch_yahoo(self, symbol, period='2y', interval='1d'):
        """Tier 3 Fetch: Yahoo Finance Universal Backup"""
        try:
            import yfinance as yf
            target = symbol
            if not symbol.startswith('^') and not (symbol.endswith('.NS') or symbol.endswith('.BO')):
                target = f"{symbol}.NS"

            df = yf.download(target, period=period, interval=interval, progress=False, threads=False)

            if (df is None or df.empty) and not symbol.startswith('^'):
                clean = symbol.replace('.NS', '').replace('.BO', '')
                df = yf.download(f"{clean}.BO", period=period, interval=interval, progress=False, threads=False)

            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df.dropna(subset=['Close'])
                if len(df) >= 20:
                    return df
        except Exception:
            pass
            
        return None

    def smart_fetch(self, symbol, period='2y', interval='1d', n_bars=500):
        """
        Executes strict 3-tier cascade with real-time terminal logging.
        """
        clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()

        # ── TIER 1: TradingView Direct (0-Second Delay Live Stream) ──
        if not symbol.startswith('^'):
            df_tv = self.fetch_tradingview(clean_sym, n_bars=n_bars, interval_str=interval)
            if df_tv is not None:
                print(f"🔥 [TradingView Direct Engine Used] {clean_sym} ({interval}) — 0s Delay Live Stream")
                return df_tv, 'TradingView Direct'

        # ── TIER 2: NSE Official Direct Scraper ──
        if interval == '1d' and not symbol.startswith('^'):
            df_nse = self.fetch_nse_direct(clean_sym, days=500)
            if df_nse is not None:
                print(f"⚡ [NSE Official Direct Engine Used] {clean_sym} — Official Exchange Data")
                return df_nse, 'NSE Direct'

        # ── TIER 3: Yahoo Finance Universal Backup ──
        df_yf = self.fetch_yahoo(symbol, period=period, interval=interval)
        if df_yf is not None:
            print(f"🌐 [Yahoo Finance Backup Engine Used] {symbol} — Global Data Stream")
            return df_yf, 'Yahoo Finance'

        print(f"❌ [Data Stream Failed] All 3 engines failed for symbol: {symbol}")
        return None, 'None'


DATA_MANAGER = MultiTechDataSourceManager()


# ═══════════════════════════════════════════════════════════════════════════
#  JSON SANITIZER & SAFE DATA CASTING HELPERS
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/favicon.ico')
def favicon():
    return '', 204


def clean_json(data):
    """
    Recursively converts NumPy/Pandas/NaN/Inf into standard Python types.

    FIX-02: missing values now serialise as `null` instead of `0.0`. The old
    behaviour turned a NaN SMA-200 into a real-looking 0, and the dashboard then
    drew a fake "DEATH CROSS" from it.
    """
    if isinstance(data, dict):
        return {k: clean_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_json(v) for v in data]
    elif isinstance(data, (np.float32, np.float64, np.floating)):
        v = float(data)
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 6)
    elif isinstance(data, (np.int32, np.int64, np.integer)):
        return int(data)
    elif isinstance(data, (np.bool_, bool)):
        return bool(data)
    elif isinstance(data, np.ndarray):
        return clean_json(data.tolist())
    elif isinstance(data, pd.Timestamp):
        return data.isoformat()
    elif data is None:
        return None
    elif isinstance(data, float):
        return None if (math.isnan(data) or math.isinf(data)) else round(data, 6)
    try:
        if pd.isna(data):
            return None
    except Exception:
        pass
    return data

def sf(val, default=0.0):
    """Safely cast value to rounded float."""
    try:
        if val is None:
            return default
        if isinstance(val, float) and np.isnan(val):
            return default
        if pd.isna(val):
            return default
        return round(float(val), 4)
    except Exception:
        return default


def si(val, default=0):
    """Safely cast value to int."""
    try:
        if val is None:
            return default
        if pd.isna(val):
            return default
        return int(val)
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════════════════════
#  DYNAMIC NSE STOCKS DATABASE LOADER (2,100+ Active Equities)
# ═══════════════════════════════════════════════════════════════════════════
def load_dynamic_nse_stocks():
    """
    Downloads official NSE Equity Master List (EQUITY_L.csv) from archives.
    Dynamically loads 2,100+ active NSE stocks into memory.
    """
    global DYNAMIC_STOCK_DB
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        print("🌐 Downloading Official NSE Master Stock List (2,100+ Stocks)...", end=" ", flush=True)
        res = http_requests.get(CONFIG['NSE_MASTER_URL'], headers=headers, timeout=8)
        
        if res.status_code == 200:
            df_csv = pd.read_csv(io.StringIO(res.text))
            stocks = []
            
            for _, row in df_csv.iterrows():
                sym = str(row.get('SYMBOL', '')).strip()
                name = str(row.get('NAME OF COMPANY', '')).strip()
                series = str(row.get(' SERIES', '')).strip()
                
                if sym and name and series in ['EQ', 'BE', 'SM', 'ST']:
                    stocks.append({
                        'sym': sym,
                        'name': name,
                        'ex': 'NSE',
                        'sec': 'NSE Equity'
                    })
                    
            if len(stocks) > 500:
                DYNAMIC_STOCK_DB = stocks
                print(f"✅ Loaded {len(DYNAMIC_STOCK_DB)} Active NSE Stocks!")
                return
                
    except Exception as e:
        print(f"⚠️ NSE Master CSV fetch failed ({e}). Loading fallback master list.")

    # Curated High-Liquidity Fallback
    DYNAMIC_STOCK_DB = [
        {"sym": "RELIANCE", "name": "Reliance Industries Ltd", "ex": "NSE", "sec": "Energy"},
        {"sym": "TCS", "name": "Tata Consultancy Services Ltd", "ex": "NSE", "sec": "IT"},
        {"sym": "HDFCBANK", "name": "HDFC Bank Ltd", "ex": "NSE", "sec": "Banking"},
        {"sym": "INFY", "name": "Infosys Ltd", "ex": "NSE", "sec": "IT"},
        {"sym": "ICICIBANK", "name": "ICICI Bank Ltd", "ex": "NSE", "sec": "Banking"},
        {"sym": "SBIN", "name": "State Bank of India", "ex": "NSE", "sec": "PSU Bank"},
        {"sym": "BHARTIARTL", "name": "Bharti Airtel Ltd", "ex": "NSE", "sec": "Telecom"},
        {"sym": "ITC", "name": "ITC Ltd", "ex": "NSE", "sec": "FMCG"},
        {"sym": "KOTAKBANK", "name": "Kotak Mahindra Bank Ltd", "ex": "NSE", "sec": "Banking"},
        {"sym": "LT", "name": "Larsen & Toubro Ltd", "ex": "NSE", "sec": "Infra"},
        {"sym": "WIPRO", "name": "Wipro Ltd", "ex": "NSE", "sec": "IT"},
        {"sym": "AXISBANK", "name": "Axis Bank Ltd", "ex": "NSE", "sec": "Banking"},
        {"sym": "MARUTI", "name": "Maruti Suzuki India Ltd", "ex": "NSE", "sec": "Auto"},
        {"sym": "TATAMOTORS", "name": "Tata Motors Ltd", "ex": "NSE", "sec": "Auto"},
        {"sym": "BAJFINANCE", "name": "Bajaj Finance Ltd", "ex": "NSE", "sec": "NBFC"},
        {"sym": "SUNPHARMA", "name": "Sun Pharmaceutical Industries Ltd", "ex": "NSE", "sec": "Pharma"},
        {"sym": "TITAN", "name": "Titan Company Ltd", "ex": "NSE", "sec": "Consumer"},
        {"sym": "ADANIENT", "name": "Adani Enterprises Ltd", "ex": "NSE", "sec": "Conglomerate"},
        {"sym": "POWERGRID", "name": "Power Grid Corporation of India Ltd", "ex": "NSE", "sec": "Power"},
        {"sym": "NTPC", "name": "NTPC Ltd", "ex": "NSE", "sec": "Power"},
        {"sym": "ONGC", "name": "Oil & Natural Gas Corporation Ltd", "ex": "NSE", "sec": "Oil&Gas"},
        {"sym": "TATASTEEL", "name": "Tata Steel Ltd", "ex": "NSE", "sec": "Metal"},
        {"sym": "HAL", "name": "Hindustan Aeronautics Ltd", "ex": "NSE", "sec": "Defence"},
        {"sym": "BEL", "name": "Bharat Electronics Ltd", "ex": "NSE", "sec": "Defence"},
        {"sym": "ZOMATO", "name": "Zomato Ltd", "ex": "NSE", "sec": "E-Commerce"},
        {"sym": "IREDA", "name": "Indian Renewable Energy Dev Agency", "ex": "NSE", "sec": "Power Finance"},
        {"sym": "KPIGREEN", "name": "KPI Green Energy Ltd", "ex": "NSE", "sec": "Renewable Energy"},
        {"sym": "SUZLON", "name": "Suzlon Energy Ltd", "ex": "NSE", "sec": "Renewable Energy"},
        {"sym": "JIOFIN", "name": "Jio Financial Services Ltd", "ex": "NSE", "sec": "NBFC"},
        {"sym": "TATATECH", "name": "Tata Technologies Ltd", "ex": "NSE", "sec": "IT / Auto Tech"}
    ]


load_dynamic_nse_stocks()


# ═══════════════════════════════════════════════════════════════════════════
#  SEARCH API & SMART RESOLVER
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/api/search')
def dynamic_search():
    """
    Search API supporting memory search and live Yahoo Search Fallback
    """
    q = request.args.get('q', '').strip().upper()
    if len(q) < 1:
        return jsonify([])

    results = []
    
    # Layer 1: In-Memory Search
    for s in DYNAMIC_STOCK_DB:
        sym_match = q == s['sym'].upper() or s['sym'].upper().startswith(q)
        name_match = q in s['name'].upper()
        if sym_match or name_match:
            results.append(s)
        if len(results) >= 12:
            break

    # Layer 2: Yahoo Live Search Fallback
    if len(results) < 5:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            url = f"{CONFIG['YAHOO_SEARCH_URL']}?q={q}&quotesCount=8&newsCount=0"
            res = http_requests.get(url, headers=headers, timeout=3).json()
            existing = {r['sym'] for r in results}
            
            for item in res.get('quotes', []):
                sym = item.get('symbol', '')
                if sym.endswith('.NS') or sym.endswith('.BO') or item.get('exchange') in ['NSE', 'BSE']:
                    cs = sym.replace('.NS', '').replace('.BO', '')
                    if cs not in existing:
                        results.append({
                            'sym': cs,
                            'name': item.get('longname') or item.get('shortname') or cs,
                            'ex': 'NSE' if '.NS' in sym or item.get('exchange') == 'NSE' else 'BSE',
                            'sec': item.get('sector') or 'Equity'
                        })
                        existing.add(cs)
        except Exception:
            pass

    # Layer 3: Direct User Query Entry
    if not results and len(q) >= 2:
        results.append({
            'sym': q,
            'name': f"{q} (NSE/BSE)",
            'ex': 'NSE',
            'sec': 'Equity'
        })

    return jsonify(clean_json(results[:CONFIG['SEARCH_MAX_RESULTS']]))


def resolve_symbol(user_input):
    """
    Smart resolver: company name / ticker → NSE symbol.

    FIX-01: the old third pass did a raw substring test over 2,565 names and
    returned the first hit, so "INFOSYS LTD" resolved to HCL-INSYS (because
    "INFOSYS" is inside "HCL INSYSTEMS") and "TATA" → TATACAP. Matching is now
    ranked and word-boundary aware, and prefers the SHORTEST candidate name.
    """
    import re as _re
    STOP = {'LTD', 'LIMITED', 'INDIA', 'CO', 'CORP', 'CORPORATION', 'THE'}
    q = (user_input or '').upper().strip().replace('.NS', '').replace('.BO', '')
    if not q:
        return q

    for s_ in DYNAMIC_STOCK_DB:                      # 1. exact ticker
        if q == s_['sym'].upper():
            return s_['sym']

    prefix = [s_ for s_ in DYNAMIC_STOCK_DB if s_['sym'].upper().startswith(q)]
    if prefix:                                       # 2. ticker prefix, shortest wins
        return sorted(prefix, key=lambda x: len(x['sym']))[0]['sym']

    tokens = [t for t in _re.findall(r'[A-Z0-9&]+', q) if t not in STOP]
    if tokens:                                       # 3. every token a whole word in the name
        hits = []
        for s_ in DYNAMIC_STOCK_DB:
            name_tokens = set(_re.findall(r'[A-Z0-9&]+', s_['name'].upper()))
            if all(t in name_tokens for t in tokens):
                hits.append(s_)
        if hits:
            return sorted(hits, key=lambda x: len(x['name']))[0]['sym']

    return q


# ═══════════════════════════════════════════════════════════════════════════
#  REAL MACHINE LEARNING ENGINE (4-Model Ensemble + Walk-Forward)
# ═══════════════════════════════════════════════════════════════════════════
def ml_engine(df):
    """
    FIX-06: cached wrapper. The 4-model ensemble + 19 walk-forward fits cost
    ~8.1s per call; results are cached on (last bar date, bars) so repeats are
    instant. Adds the walk-forward noise band to the payload.
    """
    try:
        key = (str(df.index[-1])[:10], len(df))
    except Exception:
        key = None
    if key and key in _ML_CACHE:
        return _ML_CACHE[key]

    res = _ml_engine_uncached(df)
    if isinstance(res, dict):
        res['wf_window_sigma'] = round(math.sqrt(0.25 / 20) * 100, 1)
        wf, bl = res.get('walk_forward_accuracy'), res.get('baseline_accuracy')
        res['walk_forward_edge'] = round(wf - bl, 1) if (wf is not None and bl is not None) else None
    if key:
        with _ML_LOCK:
            _ML_CACHE[key] = res
    return res


def _ml_engine_uncached(df):
    """
    Real Machine Learning Pipeline with 28 engineered features, Expanding Window
    Walk-Forward Validation, 4-Model Ensemble, and Class Imbalance Edge Analysis.
    """
    na_response = {
        'available': False,
        'error': '',
        'prediction': 'N/A',
        'probability': 50,
        'confidence': 'LOW',
        'models': {},
        'top_features': [],
        'train_days': 0,
        'test_days': 0,
        'baseline_accuracy': 50.0,
        'pos_rate': 50.0,
        'best_edge': 0.0,
        'walk_forward_accuracy': 0
    }

    try:
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        na_response['error'] = 'scikit-learn is not installed in the environment.'
        return na_response

    try:
        d = df.copy()
        c = d['Close'].astype(float)
        h = d['High'].astype(float)
        l = d['Low'].astype(float)
        v = d['Volume'].astype(float)

        # ── 28 Feature Mathematical Engineering ──
        d['ret_1d'] = c.pct_change(1)
        d['ret_3d'] = c.pct_change(3)
        d['ret_5d'] = c.pct_change(5)
        d['ret_10d'] = c.pct_change(10)
        d['ret_20d'] = c.pct_change(20)

        delta = c.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0))
        ag = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        al = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        d['rsi'] = 100 - (100 / (1 + ag / (al + 1e-10)))

        e12 = c.ewm(span=12, adjust=False).mean()
        e26 = c.ewm(span=26, adjust=False).mean()
        d['macd'] = e12 - e26
        d['macd_sig'] = d['macd'].ewm(span=9, adjust=False).mean()
        d['macd_hist'] = d['macd'] - d['macd_sig']

        d['bb_mid'] = c.rolling(20).mean()
        bb_std = c.rolling(20).std()
        d['bb_pctb'] = (c - (d['bb_mid'] - 2*bb_std)) / (4*bb_std + 1e-10)
        d['bb_width'] = (4*bb_std) / (d['bb_mid'] + 1e-10)

        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        d['atr'] = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        d['atr_pct'] = d['atr'] / (c + 1e-10) * 100

        d['vol_ratio'] = v / (v.rolling(20).mean() + 1)
        d['vol_change'] = v.pct_change(1)
        d['obv_slope'] = (np.sign(c.diff()) * v).fillna(0).cumsum().diff(5)

        d['ema9'] = c.ewm(span=9, adjust=False).mean()
        d['ema21'] = c.ewm(span=21, adjust=False).mean()
        d['sma50'] = c.rolling(50).mean()
        d['ema_cross'] = (d['ema9'] - d['ema21']) / (c + 1e-10) * 100
        d['price_50'] = (c - d['sma50']) / (d['sma50'] + 1e-10) * 100

        pdm = h.diff().where((h.diff() > -l.diff()) & (h.diff() > 0), 0.0)
        mdm = (-l.diff()).where((-l.diff() > h.diff()) & (-l.diff() > 0), 0.0)
        d['plus_di'] = 100 * pdm.ewm(alpha=1/14, adjust=False).mean() / (d['atr'] + 1e-10)
        d['minus_di'] = 100 * mdm.ewm(alpha=1/14, adjust=False).mean() / (d['atr'] + 1e-10)
        d['adx'] = (100 * (d['plus_di'] - d['minus_di']).abs() / (d['plus_di'] + d['minus_di'] + 1e-10)).ewm(alpha=1/14, adjust=False).mean()

        rs = d['rsi']
        d['stoch_rsi'] = ((rs - rs.rolling(14).min()) / (rs.rolling(14).max() - rs.rolling(14).min() + 1e-10)).rolling(3).mean() * 100

        tp = (h + l + c) / 3
        d['cci'] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True) + 1e-10)
        d['willr'] = ((h.rolling(14).max() - c) / (h.rolling(14).max() - l.rolling(14).min() + 1e-10)) * -100

        d['vol_20'] = c.pct_change().rolling(20).std() * 100
        d['vol_5'] = c.pct_change().rolling(5).std() * 100
        d['vwap_dist'] = (c - (tp * v).cumsum() / (v.cumsum() + 1e-10)) / (c + 1e-10) * 100
        d['hl_range'] = (h - l) / (c + 1e-10) * 100
        d['close_pos'] = (c - l) / (h - l + 1e-10)

        d['target'] = (c.shift(-1) > c).astype(int)

        feats = [
            'ret_1d', 'ret_3d', 'ret_5d', 'ret_10d', 'ret_20d', 'rsi', 'macd', 'macd_sig', 'macd_hist',
            'bb_pctb', 'bb_width', 'atr_pct', 'vol_ratio', 'vol_change', 'obv_slope', 'ema_cross',
            'price_50', 'adx', 'plus_di', 'minus_di', 'stoch_rsi', 'cci', 'willr', 'vol_20', 'vol_5',
            'vwap_dist', 'hl_range', 'close_pos'
        ]

        d[feats] = d[feats].replace([np.inf, -np.inf], np.nan).ffill().bfill()
        d_clean = d.dropna(subset=['target'] + feats)

        if len(d_clean) < CONFIG['ML_MIN_DAYS']:
            na_response['error'] = f'Need {CONFIG["ML_MIN_DAYS"]}+ clean bars, found {len(d_clean)}'
            return na_response

        # ── Expanding Window Walk-Forward Validation ──
        wf_results = []
        test_window = 20
        for start in range(120, len(d_clean) - test_window, test_window):
            train_sub = d_clean.iloc[:start]
            test_sub = d_clean.iloc[start:start + test_window]
            if len(test_sub) < 10:
                continue
            X_tr_wf = np.nan_to_num(train_sub[feats].values)
            y_tr_wf = train_sub['target'].values
            X_te_wf = np.nan_to_num(test_sub[feats].values)
            y_te_wf = test_sub['target'].values

            sc_wf = StandardScaler()
            X_tr_wf_s = sc_wf.fit_transform(X_tr_wf)
            X_te_wf_s = sc_wf.transform(X_te_wf)

            gb_wf = GradientBoostingClassifier(**CONFIG['ML_GB_PARAMS'])
            gb_wf.fit(X_tr_wf_s, y_tr_wf)
            wf_results.append(accuracy_score(y_te_wf, gb_wf.predict(X_te_wf_s)))

        wf_accuracy = round(np.mean(wf_results) * 100, 1) if wf_results else 0.0

        # ── Final Train/Test Split (80/20) ──
        train_n = int(len(d_clean) * 0.8)
        X_train = np.nan_to_num(d_clean[feats].iloc[:train_n].values)
        y_train = d_clean['target'].iloc[:train_n].values
        X_test = np.nan_to_num(d_clean[feats].iloc[train_n:].values)
        y_test = d_clean['target'].iloc[train_n:].values
        X_today = np.nan_to_num(d_clean[feats].iloc[-1:].values)

        pos_rate = float(y_test.mean())
        baseline_acc = round(max(pos_rate, 1 - pos_rate) * 100, 1)

        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_train)
        X_te_s = scaler.transform(X_test)
        X_today_s = scaler.transform(X_today)

        # Model 1: Gradient Boosting
        gb = GradientBoostingClassifier(**CONFIG['ML_GB_PARAMS'])
        gb.fit(X_tr_s, y_train)
        gb_preds = gb.predict(X_te_s)
        gb_acc = round(accuracy_score(y_test, gb_preds) * 100, 1)
        gb_prob = float(gb.predict_proba(X_today_s)[0][1])

        # Model 2: Random Forest
        rf = RandomForestClassifier(**CONFIG['ML_RF_PARAMS'])
        rf.fit(X_tr_s, y_train)
        rf_preds = rf.predict(X_te_s)
        rf_acc = round(accuracy_score(y_test, rf_preds) * 100, 1)
        rf_prob = float(rf.predict_proba(X_today_s)[0][1])

        # Model 3: Logistic Regression
        lr = LogisticRegression(**CONFIG['ML_LR_PARAMS'])
        lr.fit(X_tr_s, y_train)
        lr_preds = lr.predict(X_te_s)
        lr_acc = round(accuracy_score(y_test, lr_preds) * 100, 1)
        lr_prob = float(lr.predict_proba(X_today_s)[0][1])

        models = {
            'gradient_boosting': {
                'accuracy': gb_acc,
                'edge': round(gb_acc - baseline_acc, 1),
                'prob': round(gb_prob * 100, 1)
            },
            'random_forest': {
                'accuracy': rf_acc,
                'edge': round(rf_acc - baseline_acc, 1),
                'prob': round(rf_prob * 100, 1)
            },
            'logistic_regression': {
                'accuracy': lr_acc,
                'edge': round(lr_acc - baseline_acc, 1),
                'prob': round(lr_prob * 100, 1)
            }
        }

        all_probs = [gb_prob, rf_prob, lr_prob]
        weights = CONFIG['ML_WEIGHTS_3']

        # Model 4: Optional XGBoost Classifier
        try:
            from xgboost import XGBClassifier
            xgb = XGBClassifier(**CONFIG['ML_XGB_PARAMS'])
            xgb.fit(X_tr_s, y_train)
            xgb_preds = xgb.predict(X_te_s)
            xgb_acc = round(accuracy_score(y_test, xgb_preds) * 100, 1)
            xgb_prob = float(xgb.predict_proba(X_today_s)[0][1])
            models['xgboost'] = {
                'accuracy': xgb_acc,
                'edge': round(xgb_acc - baseline_acc, 1),
                'prob': round(xgb_prob * 100, 1)
            }
            all_probs.append(xgb_prob)
            weights = CONFIG['ML_WEIGHTS_4']
        except Exception:
            pass

        weights = weights[:len(all_probs)]
        w_sum = sum(weights)
        ens_prob = round(sum(p * w / w_sum for p, w in zip(all_probs, weights)) * 100, 1)

        pred = 'UP' if ens_prob >= 55 else 'DOWN' if ens_prob <= 45 else 'NEUTRAL'
        if pred == 'UP':
            agreement = sum(1 for p in all_probs if p > 0.5)
        else:
            agreement = sum(1 for p in all_probs if p < 0.5)

        conf = 'HIGH' if agreement >= len(all_probs) - 1 else 'MEDIUM' if agreement >= 2 else 'LOW'

        imp = gb.feature_importances_
        top_f = sorted(zip(feats, imp), key=lambda x: x[1], reverse=True)[:5]
        best_edge = max(m['edge'] for m in models.values())

        return {
            'available': True,
            'prediction': pred,
            'probability': float(ens_prob),
            'confidence': conf,
            'models': models,
            'ensemble_accuracy': round(sum(m['accuracy'] for m in models.values()) / len(models), 1),
            'baseline_accuracy': baseline_acc,
            'pos_rate': round(pos_rate * 100, 1),
            'best_edge': best_edge,
            'walk_forward_accuracy': wf_accuracy,
            'top_features': [{'name': str(f[0]), 'importance': round(float(f[1]) * 100, 1)} for f in top_f],
            'train_days': int(train_n),
            'test_days': int(len(d_clean) - train_n),
            'total_features': len(feats)
        }
        
    except Exception as e:
        na_response['error'] = str(e)
        return na_response


# ═══════════════════════════════════════════════════════════════════════════
#  20+ VECTORIZED TECHNICAL INDICATORS
# ═══════════════════════════════════════════════════════════════════════════
def calculate_all_indicators(df):
    """
    Calculates 20+ Vectorized Technical Indicators using NumPy and Pandas C-accelerated math.
    """
    c = df['Close'].astype(float)
    h = df['High'].astype(float)
    l = df['Low'].astype(float)
    v = df['Volume'].astype(float)

    # Moving Averages
    df['EMA_9'] = c.ewm(span=9, adjust=False).mean()
    df['EMA_21'] = c.ewm(span=21, adjust=False).mean()
    df['EMA_50'] = c.ewm(span=50, adjust=False).mean()
    df['SMA_20'] = c.rolling(20).mean()
    df['SMA_50'] = c.rolling(50).mean()
    df['SMA_200'] = c.rolling(200).mean()

    # Relative Strength Index (RSI)
    delta = c.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    ag = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    al = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + ag / (al + 1e-10)))

    # MACD Line, Signal, and Histogram
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    df['MACD'] = e12 - e26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    # Bollinger Bands
    df['BB_Mid'] = df['SMA_20']
    bb_std = c.rolling(20).std()
    df['BB_Upper'] = df['BB_Mid'] + (bb_std * 2)
    df['BB_Lower'] = df['BB_Mid'] - (bb_std * 2)
    df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / (df['BB_Mid'] + 1e-10) * 100
    df['BB_PctB'] = (c - df['BB_Lower']) / (df['BB_Upper'] - df['BB_Lower'] + 1e-10)

    # True Range & Average True Range (ATR)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()

    # NumPy Vectorized Supertrend Calculation
    st_period = CONFIG['SUPERTREND_PERIOD']
    st_multiplier = CONFIG['SUPERTREND_MULTIPLIER']
    atr_st = tr.ewm(alpha=1/st_period, min_periods=st_period, adjust=False).mean().values
    hl2 = ((h + l) / 2).values
    c_v = c.values
    n = len(df)

    ub = hl2 + (st_multiplier * atr_st)
    lb = hl2 - (st_multiplier * atr_st)
    st = np.zeros(n)
    dr = np.ones(n, dtype=np.int8)

    for i in range(1, n):
        if c_v[i] > ub[i-1]:
            dr[i] = 1
        elif c_v[i] < lb[i-1]:
            dr[i] = -1
        else:
            dr[i] = dr[i-1]

        if dr[i] == 1:
            if dr[i-1] == 1:
                lb[i] = max(lb[i], lb[i-1])
            st[i] = lb[i]
        else:
            if dr[i-1] == -1:
                ub[i] = min(ub[i], ub[i-1])
            st[i] = ub[i]

    df['Supertrend'] = st
    df['ST_Direction'] = dr

    # ADX & Directional Movement System
    pdm = h.diff().where((h.diff() > -l.diff()) & (h.diff() > 0), 0.0)
    mdm = (-l.diff()).where((-l.diff() > h.diff()) & (-l.diff() > 0), 0.0)
    plus_di = 100 * (pdm.ewm(alpha=1/14, adjust=False).mean() / (df['ATR'] + 1e-10))
    minus_di = 100 * (mdm.ewm(alpha=1/14, adjust=False).mean() / (df['ATR'] + 1e-10))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    df['ADX'] = dx.ewm(alpha=1/14, adjust=False).mean()
    df['Plus_DI'] = plus_di
    df['Minus_DI'] = minus_di

    # Volume Weighted Average Price
    # FIX-05: this used to be a CUMULATIVE VWAP since the first downloaded
    # bar (~2 years) while the intraday KPI and the indicator table used it
    # as if it were a session VWAP. Now 20-session rolling; the old series is
    # kept as VWAP_CUMULATIVE for reference.
    tp = (h + l + c) / 3
    df['VWAP_CUMULATIVE'] = (tp * v).cumsum() / (v.cumsum() + 1e-10)
    df['VWAP'] = (tp * v).rolling(20).sum() / (v.rolling(20).sum() + 1e-10)

    # Stochastic RSI
    rsi_s = df['RSI']
    stoch_rsi = (rsi_s - rsi_s.rolling(14).min()) / (rsi_s.rolling(14).max() - rsi_s.rolling(14).min() + 1e-10)
    df['StochRSI_K'] = stoch_rsi.rolling(3).mean() * 100
    df['StochRSI_D'] = df['StochRSI_K'].rolling(3).mean()

    # On Balance Volume (OBV)
    df['OBV'] = (np.sign(c.diff()).fillna(0) * v).cumsum()
    df['OBV_EMA'] = df['OBV'].ewm(span=20, adjust=False).mean()

    # Commodity Channel Index (CCI)
    tp2 = (h + l + c) / 3
    cci_sma = tp2.rolling(20).mean()
    cci_md = tp2.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    df['CCI'] = (tp2 - cci_sma) / (0.015 * cci_md + 1e-10)

    # Williams %R
    df['WilliamsR'] = ((h.rolling(14).max() - c) / (h.rolling(14).max() - l.rolling(14).min() + 1e-10)) * -100

    # Ichimoku Kinko Hyo
    df['Ichi_Tenkan'] = (h.rolling(9).max() + l.rolling(9).min()) / 2
    df['Ichi_Kijun'] = (h.rolling(26).max() + l.rolling(26).min()) / 2
    df['Ichi_SenkouA'] = ((df['Ichi_Tenkan'] + df['Ichi_Kijun']) / 2).shift(26)
    df['Ichi_SenkouB'] = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)

    # Volume Profiling SMA
    df['Vol_SMA20'] = v.rolling(20).mean()

    return df


# ═══════════════════════════════════════════════════════════════════════════
#  18 CANDLESTICK PATTERN RECOGNITION SCANNER
# ═══════════════════════════════════════════════════════════════════════════
def detect_all_candle_patterns(df):
    """
    Detects 18 classic single, double, and triple candlestick patterns across recent price action.
    """
    patterns = []
    if len(df) < 5:
        return patterns
        
    recent = df.tail(5)
    for i in range(2, len(recent)):
        c1 = recent.iloc[i-2]
        c2 = recent.iloc[i-1]
        c3 = recent.iloc[i]

        b3 = abs(c3['Close'] - c3['Open'])
        b2 = abs(c2['Close'] - c2['Open'])
        b1 = abs(c1['Close'] - c1['Open'])
        u3 = c3['High'] - max(c3['Close'], c3['Open'])
        l3 = min(c3['Close'], c3['Open']) - c3['Low']
        t3 = c3['High'] - c3['Low']

        if t3 <= 0:
            continue

        # Single Candle Patterns
        if b3 / t3 < 0.1:
            patterns.append({'name': 'Doji', 'type': 'REVERSAL', 'direction': 'NEUTRAL', 'strength': 55, 'candles': 1})
            
        if b3 > 0 and l3 >= 2 * b3 and u3 < b3 * 0.5:
            patterns.append({'name': 'Hammer', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 72, 'candles': 1})
            
        if b3 > 0 and u3 >= 2 * b3 and l3 < b3 * 0.5 and c3['Close'] > c3['Open']:
            patterns.append({'name': 'Inverted Hammer', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 68, 'candles': 1})
            
        if b3 > 0 and u3 >= 2 * b3 and l3 < b3 * 0.5 and c3['Close'] < c3['Open']:
            patterns.append({'name': 'Shooting Star', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 72, 'candles': 1})
            
        if b3 / t3 > 0.85:
            patterns.append({'name': 'Marubozu', 'type': 'CONTINUATION', 'direction': 'BULLISH' if c3['Close'] > c3['Open'] else 'BEARISH', 'strength': 78, 'candles': 1})
            
        if b3 / t3 < 0.3 and u3 > b3 and l3 > b3:
            patterns.append({'name': 'Spinning Top', 'type': 'INDECISION', 'direction': 'NEUTRAL', 'strength': 45, 'candles': 1})

        # Double Candle Patterns
        if c3['Close'] > c3['Open'] and c2['Close'] < c2['Open'] and c3['Open'] <= c2['Close'] and c3['Close'] >= c2['Open']:
            patterns.append({'name': 'Bullish Engulfing', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 90, 'candles': 2})
            
        if c3['Close'] < c3['Open'] and c2['Close'] > c2['Open'] and c3['Open'] >= c2['Close'] and c3['Close'] <= c2['Open']:
            patterns.append({'name': 'Bearish Engulfing', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 90, 'candles': 2})
            
        if c2['Close'] < c2['Open'] and c3['Close'] > c3['Open'] and c3['Open'] > c2['Close'] and c3['Close'] < c2['Open']:
            patterns.append({'name': 'Bullish Harami', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 73, 'candles': 2})
            
        if c2['Close'] > c2['Open'] and c3['Close'] < c3['Open'] and c3['Open'] < c2['Close'] and c3['Close'] > c2['Open']:
            patterns.append({'name': 'Bearish Harami', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 73, 'candles': 2})

        mid2 = (c2['Open'] + c2['Close']) / 2
        if c2['Close'] < c2['Open'] and c3['Close'] > c3['Open'] and c3['Open'] < c2['Low'] and c3['Close'] > mid2 and c3['Close'] < c2['Open']:
            patterns.append({'name': 'Piercing Line', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 80, 'candles': 2})
            
        if c2['Close'] > c2['Open'] and c3['Close'] < c3['Open'] and c3['Open'] > c2['High'] and c3['Close'] < mid2 and c3['Close'] > c2['Open']:
            patterns.append({'name': 'Dark Cloud Cover', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 80, 'candles': 2})

        if abs(c2['Low'] - c3['Low']) / (c2['Low'] + 1e-10) < 0.002 and c2['Close'] < c2['Open'] and c3['Close'] > c3['Open']:
            patterns.append({'name': 'Tweezer Bottom', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 76, 'candles': 2})
            
        if abs(c2['High'] - c3['High']) / (c2['High'] + 1e-10) < 0.002 and c2['Close'] > c2['Open'] and c3['Close'] < c3['Open']:
            patterns.append({'name': 'Tweezer Top', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 76, 'candles': 2})

        # Triple Candle Patterns
        if c1['Close'] < c1['Open'] and b2 < b1 * 0.3 and c3['Close'] > c3['Open'] and c3['Close'] > (c1['Open'] + c1['Close']) / 2:
            patterns.append({'name': 'Morning Star', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 92, 'candles': 3})
            
        if c1['Close'] > c1['Open'] and b2 < b1 * 0.3 and c3['Close'] < c3['Open'] and c3['Close'] < (c1['Open'] + c1['Close']) / 2:
            patterns.append({'name': 'Evening Star', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 92, 'candles': 3})
            
        if c1['Close'] > c1['Open'] and c2['Close'] > c2['Open'] and c3['Close'] > c3['Open'] and c2['Close'] > c1['Close'] and c3['Close'] > c2['Close']:
            patterns.append({'name': 'Three White Soldiers', 'type': 'CONTINUATION', 'direction': 'BULLISH', 'strength': 88, 'candles': 3})
            
        if c1['Close'] < c1['Open'] and c2['Close'] < c2['Open'] and c3['Close'] < c3['Open'] and c2['Close'] < c1['Close'] and c3['Close'] < c2['Close']:
            patterns.append({'name': 'Three Black Crows', 'type': 'CONTINUATION', 'direction': 'BEARISH', 'strength': 88, 'candles': 3})

    seen = set()
    unique = []
    for p in patterns:
        if p['name'] not in seen:
            seen.add(p['name'])
            unique.append(p)
            
    return unique


# ═══════════════════════════════════════════════════════════════════════════
#  KPI SCORING & COMPOSITE MATRIX
# ═══════════════════════════════════════════════════════════════════════════
def calculate_kpi_scores(df, fund_data):
    """
    Computes multi-horizon Intraday, Swing, and Long-term KPI scores based on price technicals and fundamental metrics.
    """
    L = df.iloc[-1]
    price = sf(L.get('Close'))
    rsi = sf(L.get('RSI'), 50)

    # Intraday Horizon Score
    i_s = 50
    if price > sf(L.get('VWAP')):
        i_s += 10
    else:
        i_s -= 10

    if rsi < 30:
        i_s += 12
    elif rsi > 70:
        i_s -= 12

    if sf(L.get('EMA_9')) > sf(L.get('EMA_21')):
        i_s += 8
    else:
        i_s -= 8

    if si(L.get('ST_Direction')) == 1:
        i_s += 8
    else:
        i_s -= 8

    if sf(L.get('MACD')) > sf(L.get('MACD_Signal')):
        i_s += 6
    else:
        i_s -= 6

    srk = sf(L.get('StochRSI_K'), 50)
    if srk < 20:
        i_s += 6
    elif srk > 80:
        i_s -= 6

    if sf(L.get('Volume')) > sf(L.get('Vol_SMA20'), 1) * 1.5:
        i_s += 5
        
    i_s = int(max(5, min(98, i_s)))

    # Swing Horizon Score
    s_s = 50
    if sf(L.get('EMA_21')) > sf(L.get('SMA_50')):
        s_s += 10
    else:
        s_s -= 10

    if sf(L.get('MACD')) > sf(L.get('MACD_Signal')):
        s_s += 8
    else:
        s_s -= 8

    if 40 <= rsi <= 60:
        s_s += 6
    elif rsi < 30:
        s_s += 10
    elif rsi > 75:
        s_s -= 8

    bb_l = sf(L.get('BB_Lower'))
    bb_u = sf(L.get('BB_Upper'))
    if bb_l > 0 and price < bb_l:
        s_s += 8
    elif bb_u > 0 and price > bb_u:
        s_s -= 6

    if sf(L.get('ADX')) > 25:
        s_s += 5

    cci = sf(L.get('CCI'))
    if cci < -100:
        s_s += 6
    elif cci > 100:
        s_s -= 4
        
    s_s = int(max(5, min(98, s_s)))

    # Long-Term Horizon Score
    lt_s = 50
    sma200 = sf(L.get('SMA_200'))
    sma50 = sf(L.get('SMA_50'))
    
    if sma200 > 0 and price > sma200:
        lt_s += 15
    elif sma200 > 0:
        lt_s -= 12

    if sma50 > 0 and sma200 > 0 and sma50 > sma200:
        lt_s += 12
    elif sma50 > 0 and sma200 > 0:
        lt_s -= 8

    pe = fund_data.get('pe_val')
    if pe and isinstance(pe, (int, float)):
        if pe < 25:
            lt_s += 5
        elif pe > 50:
            lt_s -= 5

    roe = fund_data.get('roe_val')
    if roe and isinstance(roe, (int, float)) and roe > 0.15:
        lt_s += 6

    debt = fund_data.get('debt_val')
    if debt and isinstance(debt, (int, float)):
        if debt < 0:
            lt_s += 3
        elif debt < 50:
            lt_s += 4
        elif debt > 150:
            lt_s -= 4

    if sf(L.get('OBV')) > sf(L.get('OBV_EMA')):
        lt_s += 4
        
    lt_s = int(max(5, min(98, lt_s)))

    master = int((i_s + s_s + lt_s) / 3)
    
    return {
        'intraday': {
            'score': i_s,
            'action': 'BUY' if i_s >= 65 else 'SELL' if i_s <= 35 else 'HOLD'
        },
        'swing': {
            'score': s_s,
            'action': 'BUY' if s_s >= 65 else 'SELL' if s_s <= 35 else 'HOLD'
        },
        'longterm': {
            'score': lt_s,
            'action': 'INVEST' if lt_s >= 65 else 'AVOID' if lt_s <= 35 else 'WATCH'
        },
        'master': {
            'score': master,
            'action': 'STRONG BUY' if master >= 72 else 'STRONG SELL' if master <= 28 else 'NEUTRAL'
        }
    }


# ═══════════════════════════════════════════════════════════════════════════
#  6 INSTITUTIONAL TRADING ENGINES
# ═══════════════════════════════════════════════════════════════════════════

# ENGINE 1: Volume Profile (Point of Control / HVN / LVN Analysis)
def engine_volume_profile(df, bins=50):
    try:
        prices = df['Close'].values.astype(float)
        volumes = df['Volume'].values.astype(float)
        pbins = np.linspace(prices.min(), prices.max(), bins + 1)
        vp = np.zeros(bins)
        
        for i in range(bins):
            mask = (prices >= pbins[i]) & (prices < pbins[i+1])
            vp[i] = volumes[mask].sum()

        poc_i = np.argmax(vp)
        poc = (pbins[poc_i] + pbins[poc_i+1]) / 2
        si_sort = np.argsort(vp)[::-1]
        hvn = [(pbins[j] + pbins[j+1])/2 for j in si_sort[:3]]
        lvn = [(pbins[j] + pbins[j+1])/2 for j in si_sort[-3:]]

        cur = prices[-1]
        pd_d = abs(cur - poc) / (cur + 1e-10) * 100
        score = 75 if cur > poc and pd_d < 3 else 70 if cur < poc and pd_d < 2 else 35 if pd_d > 8 else 50
        
        return {
            'name': 'Volume Profile',
            'score': int(max(5, min(98, score))),
            'poc': round(poc, 2),
            'hvn': [round(h, 2) for h in hvn],
            'lvn': [round(lv, 2) for lv in lvn],
            'poc_distance': round(pd_d, 2),
            'signal': 'ABOVE POC' if cur > poc else 'BELOW POC'
        }
    except Exception:
        return {
            'name': 'Volume Profile',
            'score': 50,
            'poc': 0,
            'hvn': [],
            'lvn': [],
            'poc_distance': 0,
            'signal': 'N/A'
        }


# ENGINE 2: RVOL + Cumulative Volume Delta (CVD) + Volume Spread Analysis (VSA)
def engine_rvol_cvd(df):
    try:
        L = df.iloc[-1]
        avg20 = df['Volume'].tail(20).mean()
        rvol = float(L['Volume']) / (float(avg20) + 1)

        dfc = df.copy()
        dfc['bv'] = np.where(dfc['Close'] > dfc['Open'], dfc['Volume'], 0)
        dfc['sv'] = np.where(dfc['Close'] < dfc['Open'], dfc['Volume'], 0)
        dfc['cvd'] = (dfc['bv'] - dfc['sv']).cumsum()
        cvd_t = 'RISING' if float(dfc['cvd'].iloc[-1]) > float(dfc['cvd'].iloc[-5]) else 'FALLING'

        pu = float(L['Close']) > float(df['Close'].iloc[-5])
        div = 'BULLISH' if (not pu and cvd_t == 'RISING') else 'BEARISH' if (pu and cvd_t == 'FALLING') else 'NONE'

        li = df['Low'].tail(20).idxmin()
        ad = df.loc[li:]
        tp_ad = (ad['High'] + ad['Low'] + ad['Close']) / 3
        avwap = float((tp_ad * ad['Volume']).sum() / (ad['Volume'].sum() + 1))

        sp = float(L['High'] - L['Low'])
        asp = float((df['High'] - df['Low']).tail(20).mean())
        vsa = 'NO SUPPLY' if sp < asp*0.6 and rvol < 0.7 else 'STOPPING VOL' if sp > asp*1.5 and rvol > 2.0 and float(L['Close']) > float(L['Open']) else 'NORMAL'

        score = 50
        score += 15 if rvol >= 2.5 else 8 if rvol >= 1.5 else -10 if rvol < 0.5 else 0
        score += 8 if cvd_t == 'RISING' else -5
        score += 7 if float(L['Close']) > avwap else -7
        score += 10 if div == 'BULLISH' else -10 if div == 'BEARISH' else 0

        return {
            'name': 'RVOL + CVD + VSA',
            'score': int(max(5, min(98, score))),
            'rvol': round(rvol, 2),
            'cvd_trend': cvd_t,
            'cvd_divergence': div,
            'anchored_vwap': round(avwap, 2),
            'vsa': vsa,
            'signal': 'STRONG' if rvol >= 2.5 else 'WEAK' if rvol < 0.7 else 'NORMAL'
        }
    except Exception:
        return {
            'name': 'RVOL + CVD + VSA',
            'score': 50,
            'rvol': 1.0,
            'cvd_trend': 'N/A',
            'cvd_divergence': 'NONE',
            'anchored_vwap': 0,
            'vsa': 'N/A',
            'signal': 'N/A'
        }


# ENGINE 3: Volatility Contraction Pattern (VCP V2)
def engine_vcp(df):
    try:
        r = df.tail(60)
        h_v = r['High'].values.astype(float)
        l_v = r['Low'].values.astype(float)
        cons = []
        w = 10
        
        for i in range(w, len(h_v) - w, w // 2):
            sh = h_v[max(0, i-w):i+w].max()
            sl = l_v[max(0, i-w):i+w].min()
            if sl > 0:
                cons.append((sh - sl) / sl * 100)

        vc = sum(1 for i in range(1, len(cons)) if cons[i] < cons[i-1] * 0.75) if len(cons) >= 2 else 0

        l5r = float(r['High'].tail(5).max() - r['Low'].tail(5).min())
        l5m = float(r['Close'].tail(5).mean())
        tight = (l5r / l5m * 100) if l5m > 0 else 99

        a5 = float((r['High'].tail(5) - r['Low'].tail(5)).mean())
        a20 = float((r['High'].tail(20) - r['Low'].tail(20)).mean())
        ar = a5 / (a20 + 1e-9)

        score = 30
        score += 25 if vc >= 3 else 15 if vc >= 2 else 8 if vc >= 1 else 0
        score += 20 if tight < 3 else 12 if tight < 5 else 0
        score += 15 if ar < 0.4 else 8 if ar < 0.6 else 0

        return {
            'name': 'VCP V2',
            'score': int(max(5, min(98, score))),
            'contractions': vc,
            'tightness': round(tight, 2),
            'atr_ratio': round(ar, 2),
            'signal': 'READY' if vc >= 2 and tight < 5 else 'FORMING' if vc >= 1 else 'NONE'
        }
    except Exception:
        return {
            'name': 'VCP V2',
            'score': 30,
            'contractions': 0,
            'tightness': 0,
            'atr_ratio': 0,
            'signal': 'N/A'
        }


# ENGINE 4: Smart Money Concepts (SMC / ICT Order Blocks & FVG)
def engine_smc(df):
    try:
        L = df.iloc[-1]
        r = df.tail(20)
        rh = float(r['High'].iloc[:-3].max())
        rl = float(r['Low'].iloc[:-3].min())
        cl = float(L['Low'])
        ch = float(L['High'])
        cc = float(L['Close'])

        sweep = 'BULLISH SWEEP' if cl < rl and cc > rl else 'BEARISH SWEEP' if ch > rh and cc < rh else 'NONE'

        fvgb, fvgs, obs = [], [], []
        for i in range(2, min(10, len(df))):
            c1h = float(df.iloc[-(i+2)]['High'])
            c3l = float(df.iloc[-i]['Low'])
            c1l = float(df.iloc[-(i+2)]['Low'])
            c3h = float(df.iloc[-i]['High'])
            if c1h < c3l:
                fvgb.append({'low': round(c1h, 2), 'high': round(c3l, 2)})
            if c1l > c3h:
                fvgs.append({'low': round(c3h, 2), 'high': round(c1l, 2)})

        for i in range(3, min(15, len(df))):
            cn = df.iloc[-i]
            nx = df.iloc[-i+1]
            if float(cn['Close']) < float(cn['Open']) and float(nx['Close']) > float(nx['Open']) and (float(nx['Close']) - float(nx['Open'])) > 2 * (float(cn['Open']) - float(cn['Close'])):
                obs.append({'type': 'BULLISH', 'high': round(float(cn['Open']), 2), 'low': round(float(cn['Low']), 2)})

        score = 50
        score += 15 if sweep == 'BULLISH SWEEP' else -10 if sweep == 'BEARISH SWEEP' else 0
        score += 10 if fvgb else 0
        score -= 5 if fvgs else 0
        score += 10 if obs else 0

        return {
            'name': 'SMC / ICT',
            'score': int(max(5, min(95, score))),
            'liquidity_sweep': sweep,
            'fvg_bullish': fvgb[:2],
            'fvg_bearish': fvgs[:2],
            'order_blocks': obs[:2],
            'signal': 'BULLISH' if score >= 65 else 'BEARISH' if score <= 35 else 'NEUTRAL'
        }
    except Exception:
        return {
            'name': 'SMC / ICT',
            'score': 50,
            'liquidity_sweep': 'NONE',
            'fvg_bullish': [],
            'fvg_bearish': [],
            'order_blocks': [],
            'signal': 'N/A'
        }


# ENGINE 5: Market Macro Regime Analysis
def engine_market_regime():
    global REGIME_CACHE
    now = datetime.now()
    if REGIME_CACHE['data'] and REGIME_CACHE['time'] and (now - REGIME_CACHE['time']).seconds < CONFIG['REGIME_CACHE_TTL']:
        return REGIME_CACHE['data']

    try:
        n, _ = DATA_MANAGER.smart_fetch('^NSEI', period='6mo')
        vd, _ = DATA_MANAGER.smart_fetch('^INDIAVIX', period='1mo')

        nc = float(n['Close'].iloc[-1]) if n is not None and len(n) > 0 else 24000
        ne200 = float(n['Close'].ewm(span=200, adjust=False).mean().iloc[-1]) if n is not None and len(n) > 200 else 23000
        ne50 = float(n['Close'].ewm(span=50, adjust=False).mean().iloc[-1]) if n is not None and len(n) > 50 else 23500

        na200 = nc > ne200
        na50 = nc > ne50
        vix = float(vd['Close'].iloc[-1]) if vd is not None and len(vd) > 0 else 15.0

        if na200 and na50 and vix < 18:
            reg, rs = 'STRONG BULL', 85
        elif na200 and vix < 22:
            reg, rs = 'BULL', 70
        elif na200:
            reg, rs = 'VOLATILE BULL', 55
        elif not na200 and na50:
            reg, rs = 'RECOVERY', 50
        elif not na200 and vix < 20:
            reg, rs = 'WEAK BEAR', 35
        else:
            reg, rs = 'STRONG BEAR', 20

        res = {
            'name': 'Market Regime',
            'score': rs,
            'regime': reg,
            'nifty': round(nc, 2),
            'nifty_200ema': round(ne200, 2),
            'nifty_above_200': na200,
            'vix': round(vix, 2),
            'vix_status': 'LOW' if vix < 18 else 'NORMAL' if vix < 22 else 'HIGH' if vix < 28 else 'EXTREME',
            'signal': reg
        }
        REGIME_CACHE = {'data': res, 'time': now}
        return res
        
    except Exception:
        res = {
            'name': 'Market Regime',
            'score': 50,
            'regime': 'UNKNOWN',
            'nifty': 0,
            'nifty_200ema': 0,
            'nifty_above_200': False,
            'vix': 0,
            'vix_status': 'UNKNOWN',
            'signal': 'UNKNOWN'
        }
        REGIME_CACHE = {'data': res, 'time': now}
        return res


# ENGINE 6: Real Multi-Timeframe Confluence Engine (5m, 15m, 1h, 1d)
def engine_multitimeframe(symbol, daily_df=None):
    try:
        def _calc_tf(df_tf):
            if df_tf is None or len(df_tf) < 25:
                return None
            c = df_tf['Close'].astype(float)
            ema9 = float(c.ewm(span=9, adjust=False).mean().iloc[-1])
            ema21 = float(c.ewm(span=21, adjust=False).mean().iloc[-1])
            trend = 'BULL' if ema9 > ema21 else 'BEAR'

            delta = c.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta.where(delta < 0, 0.0))
            ag = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            al = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rsi = float((100 - (100 / (1 + ag / (al + 1e-10)))).iloc[-1])
            return {'trend': trend, 'rsi': round(max(0, min(100, rsi)), 1)}

        results = {}

        # 5m & 15m timeframes
        data5m, _ = DATA_MANAGER.smart_fetch(symbol, period='5d', interval='5m', n_bars=100)
        if data5m is not None and len(data5m) >= 25:
            results['5m'] = _calc_tf(data5m)
            try:
                data15m = data5m.resample('15min').agg({
                    'Open': 'first',
                    'High': 'max',
                    'Low': 'min',
                    'Close': 'last',
                    'Volume': 'sum'
                }).dropna()
                results['15m'] = _calc_tf(data15m)
            except Exception:
                results['15m'] = None
        else:
            results['5m'], results['15m'] = None, None

        # 1h timeframe
        data1h, _ = DATA_MANAGER.smart_fetch(symbol, period='1mo', interval='1h', n_bars=100)
        results['1h'] = _calc_tf(data1h)

        # 1d timeframe
        if daily_df is not None and len(daily_df) >= 25:
            results['1d'] = _calc_tf(daily_df)
        else:
            data1d, _ = DATA_MANAGER.smart_fetch(symbol, period='6mo', interval='1d', n_bars=100)
            results['1d'] = _calc_tf(data1d)

        valid_tfs = {k: v for k, v in results.items() if v is not None}
        total = max(len(valid_tfs), 1)
        bc = sum(1 for v in valid_tfs.values() if v['trend'] == 'BULL')

        for tf in ['5m', '15m', '1h', '1d']:
            if tf not in results or results[tf] is None:
                results[tf] = {'trend': 'N/A', 'rsi': 0}

        score = int((bc / 4) * 100) if len(valid_tfs) >= 2 else 50
        
        return {
            'name': 'Multi-Timeframe',
            'score': max(5, min(98, score)),
            'confluence': f"{bc}/{total}",
            'bullish_count': bc,
            'total': total,
            'signal': 'STRONG' if bc >= 3 else 'MEDIUM' if bc >= 2 else 'WEAK',
            'timeframes': results
        }
        
    except Exception:
        return {
            'name': 'Multi-Timeframe',
            'score': 50,
            'confluence': 'N/A',
            'bullish_count': 0,
            'total': 0,
            'signal': 'ERROR',
            'timeframes': {
                '5m': {'trend': 'N/A', 'rsi': 0},
                '15m': {'trend': 'N/A', 'rsi': 0},
                '1h': {'trend': 'N/A', 'rsi': 0},
                '1d': {'trend': 'N/A', 'rsi': 0}
            }
        }


# ═══════════════════════════════════════════════════════════════════════════
#  ENSEMBLE SCORER & INSTITUTIONAL KELLY RISK ENGINE
# ═══════════════════════════════════════════════════════════════════════════
def ensemble_score(engines):
    w = CONFIG['ENGINE_WEIGHTS']
    ws = sum(e['score'] * w.get(e['name'], 0.1) for e in engines)
    tw = sum(w.get(e['name'], 0.1) for e in engines)
    base = ws / tw if tw > 0 else 50
    be = sum(1 for e in engines if e['score'] >= 65)
    bre = sum(1 for e in engines if e['score'] <= 35)
    cb = 8 if be >= 5 else 5 if be >= 4 else 2 if be >= 3 else -8 if bre >= 4 else 0
    final = int(max(5, min(98, base + cb)))
    
    return {
        'score': final,
        'action': 'BUY_BREAKOUT' if final >= 78 else 'BUY_DIP' if final >= 65 else 'WATCHLIST' if final >= 50 else 'AVOID' if final >= 35 else 'SHORT_SELL',
        'bullish_engines': be,
        'bearish_engines': bre,
        'confluence_bonus': cb,
        'tradeable': final >= 78
    }


def ensemble_v2(engines, ens):
    """
    FIX-08: DIAGNOSTIC re-centred score.

    The six engines are not on a common 0-100 scale: VCP's additive base is 30,
    Multi-Timeframe divided by a hardcoded 4 even when only 2-3 timeframes
    loaded, Volume Profile returns only {35,50,70,75} and Market Regime returns
    the SAME value for every stock. Measured effect: the weighted mean sits at
    ~42 and the documented bands (65 / 78) are effectively unreachable — 0 BUYs
    in 10 symbols, mean 42.4, max 56.

    This re-centres VCP and normalises MTF so the number is readable. It is NOT
    a calibrated model: thresholds still have to be fitted on the score's own
    historical distribution (see AUDIT_REPORT.md §C-3).
    """
    w = CONFIG['ENGINE_WEIGHTS']
    adj, notes = {}, []
    for e in engines:
        s, n = e['score'], e['name']
        if n == 'VCP V2':
            s = max(5, min(98, 50 + (s - 30)))
            notes.append('VCP re-centred (+20)')
        if n == 'Multi-Timeframe' and e.get('total'):
            s = round(100 * e.get('bullish_count', 0) / e['total'])
            notes.append('MTF normalised by loaded TFs')
        adj[n] = s
    tw = sum(w.get(e['name'], 0.1) for e in engines) or 1
    v2 = round(sum(adj[e['name']] * w.get(e['name'], 0.1) for e in engines) / tw, 1)
    return {
        'score': int(round(v2)),
        'raw_score': ens['score'],
        'delta': round(v2 - ens['score'], 1),
        'adjustments': notes,
        'note': 'UNCALIBRATED diagnostic — re-centres VCP/MTF only; fit thresholds on history before trading',
    }


def calculate_risk(price, atr, score, capital=None, action=None):
    """
    Institutional Kelly risk plan.

    FIX-07 (three bugs fixed):
      a) Quantity had NO notional cap: on a ₹1,00,000 account the old code
         returned qty=454 for RELIANCE = ₹5,39,942 notional = 5.4x leverage.
         Quantity is now capped at 1x capital.
      b) Kelly used a hardcoded b=2.5 while the function's own rr_ratio was 1.0
         → at R:R 1.0 with a 45% win-rate the true Kelly is NEGATIVE ("no
         trade"), yet the code still emitted a 23% allocation. b is now derived
         from the actual levels, and kelly<=0 → qty=0.
      c) A SHORT_SELL verdict produced a long-side plan. Direction is now
         mirrored, and non-directional verdicts produce no trade at all.
    """
    capital = capital if capital is not None else CONFIG['DEFAULT_CAPITAL']
    if action is None:
        action = ensemble_score([{'name': 'x', 'score': score}])['action']
    direction = 'SHORT' if 'SHORT' in action else ('LONG' if action.startswith('BUY') else 'NONE')

    atr = max(float(atr or 0), price * 0.005)
    sl_mult = 1.5 if score >= 78 else 2.0 if score >= 60 else 2.5
    win_rate = 0.62 if score >= 78 else 0.55 if score >= 60 else 0.45

    if direction == 'SHORT':
        sl, t1, t2, t3 = price + atr * sl_mult, price - atr * 2.5, price - atr * 4.0, price - atr * 6.0
    else:
        sl, t1, t2, t3 = price - atr * sl_mult, price + atr * 2.5, price + atr * 4.0, price + atr * 6.0

    risk_per_share = abs(price - sl)
    b = abs(t1 - price) / risk_per_share if risk_per_share > 0 else 0.0
    kelly = ((win_rate * b - (1 - win_rate)) / b) if b > 0 else 0.0
    kelly = max(0.0, min(kelly, CONFIG['MAX_KELLY_PCT']))

    qty_by_risk = int((capital * kelly) / risk_per_share) if risk_per_share > 0 else 0
    qty_by_notional = int(capital / price) if price > 0 else 0
    qty = max(0, min(qty_by_risk, qty_by_notional))
    if direction == 'NONE' or kelly <= 0:
        qty = 0
    notional = round(qty * price, 2)

    return {
        'direction': direction,
        'sl': round(sl, 2),
        'sl_pct': round(risk_per_share / price * 100, 2),
        't1': round(t1, 2),
        't2': round(t2, 2),
        't3': round(t3, 2),
        'kelly_pct': round(kelly * 100, 1),
        'kelly_rr_used': round(b, 2),
        'qty': qty,
        'qty_uncapped': qty_by_risk,
        'capital': capital,
        'notional': notional,
        'leverage': round(notional / capital, 2) if capital else 0.0,
        'risk_amount': round(qty * risk_per_share, 0),
        'rr_ratio': round(b, 2),
        'entry_zone': f"₹{round(price - 0.3 * atr, 2)} - ₹{round(price + 0.2 * atr, 2)}",
        'trail_sl_plan': f"T1 hit hone ke baad SL ko ₹{round(price, 2)} (Cost) pe shift karein",
        'exec_status': ("🟢 READY TO BUY" if score >= 78 else
                        "🟡 WAIT FOR DIP" if score >= 65 else
                        "🔴 SHORT SETUP" if direction == 'SHORT' else
                        "⚠️ WATCHLIST / NO TRADE")
    }


# ═══════════════════════════════════════════════════════════════════════════
#  QUICK QUOTE ENDPOINT (FOR MONEYCONTROL STYLE SELECTIVE REFRESH)
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/api/quote/<symbol>')
def quick_quote_api(symbol):
    """
    Lightweight <100ms API endpoint returning real-time exact LTP tick.
    Targeted for live DOM number updates without reloading heavy ML models or charts.
    """
    resolved = resolve_symbol(symbol)
    live_quote = fetch_nse_live_ltp(resolved)
    
    if live_quote:
        return jsonify(clean_json(live_quote))
        
    df, _ = DATA_MANAGER.smart_fetch(resolved, period='5d', interval='1d')
    if df is not None and not df.empty:
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        price = sf(last['Close'])
        prev_close = sf(prev['Close'])
        change = round(price - prev_close, 2)
        pChange = round((change / prev_close) * 100, 2) if prev_close else 0.0
        
        return jsonify(clean_json({
            'symbol': resolved,
            'price': price,
            'change': change,
            'pChange': pChange,
            'timestamp': datetime.now().strftime('%H:%M:%S')
        }))
        
    return jsonify({'error': 'Live quote unavailable'}), 404


# ═══════════════════════════════════════════════════════════════════════════
#  REAL-TIME SSE (SERVER-SENT EVENTS) LIVE TICK STREAM
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/api/stream/<symbol>')
def sse_live_stream(symbol):
    """FIX-12: proper no-cache headers + heartbeats so the dashboard can use SSE."""
    def event_stream():
        resolved = resolve_symbol(symbol)
        while True:
            live_quote = fetch_nse_live_ltp(resolved)
            if live_quote:
                yield f"data: {json.dumps(live_quote)}\n\n"
            else:
                df, src = DATA_MANAGER.smart_fetch(resolved, period='5d', interval='5m', n_bars=10)
                if df is not None and not df.empty:
                    last = df.iloc[-1]
                    yield "data: " + json.dumps({
                        'symbol': resolved, 'source': src, 'stale': True,
                        'price': round(sf(last['Close']), 2),
                        'volume': si(last['Volume']),
                        'time': datetime.now().strftime('%H:%M:%S')}) + "\n\n"
                else:
                    yield ": keep-alive\n\n"
            time.sleep(CONFIG['SSE_STREAM_INTERVAL'])

    resp = Response(event_stream(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN COMPREHENSIVE STOCK ANALYSIS API ROUTE
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/api/stock/<symbol>')
def stock_api(symbol):
    # FIX-14: an unresolvable symbol used to cost ~15s on EVERY call
    # (3 tiers x 2 exchanges with 4-6s timeouts). Cache the miss.
    _hit = _FAIL_CACHE.get(symbol.upper(), 0)
    if time.time() < _hit:
        return jsonify({'error': f"Symbol '{symbol}' could not be resolved "
                                  f"(cached miss, retry in {int(_hit - time.time())}s)"}), 404
    resolved = resolve_symbol(symbol)
    df, active_source = DATA_MANAGER.smart_fetch(resolved, period='2y', interval='1d', n_bars=CONFIG['CHART_CANDLES'] * 2)

    if df is None or len(df) < 20:
        _FAIL_CACHE[symbol.upper()] = time.time() + 300
        return jsonify({'error': f"Stock '{symbol}' data not available across all 3 engines!"}), 404

    try:
        df = calculate_all_indicators(df)
        L = df.iloc[-1]
        prev = df.iloc[-2]
        
        # ── Exact Live NSE LTP Handshake Hook ──
        live_nse = fetch_nse_live_ltp(resolved)
        if live_nse:
            price = live_nse['price']
            change = live_nse['change']
            pChange = live_nse['pChange']
            active_source = 'NSE Direct Live'
        else:
            price = sf(L['Close'])
            change = round(sf(price - prev['Close']), 2)
            pChange = round(sf((price - prev['Close']) / prev['Close'] * 100), 2)

        atr = sf(L.get('ATR'), price * 0.02)
        ml_res = ml_engine(df)

        try:
            import yfinance as yf
            info = yf.Ticker(f"{resolved}.NS").info or {}
        except Exception:
            info = {}

        fund_data = {
            'pe_val': info.get('trailingPE'),
            'roe_val': info.get('returnOnEquity'),
            'debt_val': info.get('debtToEquity')
        }

        # Run 6 Institutional Engines
        e1 = engine_volume_profile(df)
        e2 = engine_rvol_cvd(df)
        e3 = engine_vcp(df)
        e4 = engine_smc(df)
        e5 = engine_market_regime()
        e6 = engine_multitimeframe(resolved, daily_df=df)
        engines = [e1, e2, e3, e4, e5, e6]

        ens = ensemble_score(engines)
        risk = calculate_risk(price, atr, ens['score'], action=ens['action'])
        kpi = calculate_kpi_scores(df, fund_data)
        patterns = detect_all_candle_patterns(df)

        # Assemble Chart Array
        # FIX-09: modern yfinance already returns dividendYield / returnOnEquity
        # in PERCENT (0.5 == 0.50%). The old code multiplied by 100 and the
        # dashboard showed a 50.00% dividend yield for RELIANCE.
        _dy = info.get('dividendYield')
        _dy = (_dy / 100.0) if (_dy and _dy > 25) else _dy
        _roe = info.get('returnOnEquity')
        _roe = (_roe / 100.0) if (_roe and _roe > 5) else _roe

        chart_data = []
        for idx, row in df.tail(CONFIG['CHART_CANDLES']).iterrows():
            t_str = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)[:10]
            chart_data.append({
                'time': t_str,
                'open': round(sf(row['Open']), 2),
                'high': round(sf(row['High']), 2),
                'low': round(sf(row['Low']), 2),
                'close': round(sf(row['Close']), 2),
                'volume': si(row['Volume'])
            })

        h52 = sf(df['High'].tail(252).max())
        l52 = sf(df['Low'].tail(252).min())
        pos52 = round((price - l52) / (h52 - l52 + 1e-10) * 100, 1)

        response_payload = {
            'symbol': resolved,
            'data_source': active_source,
            'price': price,
            'change': change,
            'pChange': pChange,
            'ml': ml_res,
            'engines': {
                'vol_profile': e1,
                'rvol_cvd': e2,
                'vcp': e3,
                'smc': e4,
                'regime': e5,
                'mtf': e6
            },
            'ensemble': ens,
            'kpi': kpi,
            'risk': risk,
            'patterns': patterns,
            'indicators': {
                'rsi': sf(L.get('RSI'), 50),
                'ema9': round(sf(L.get('EMA_9')), 2),
                'ema21': round(sf(L.get('EMA_21')), 2),
                'ema50': round(sf(L.get('EMA_50')), 2),
                'sma50': round(sf(L.get('SMA_50')), 2),
                'sma200': round(sf(L.get('SMA_200')), 2),
                'macd': round(sf(L.get('MACD')), 2),
                'macd_signal': round(sf(L.get('MACD_Signal')), 2),
                'macd_hist': round(sf(L.get('MACD_Hist')), 2),
                'bb_upper': round(sf(L.get('BB_Upper')), 2),
                'bb_lower': round(sf(L.get('BB_Lower')), 2),
                'bb_pctb': round(sf(L.get('BB_PctB')), 2),
                'bb_width': round(sf(L.get('BB_Width')), 2),
                'supertrend': round(sf(L.get('Supertrend')), 2),
                'st_direction': si(L.get('ST_Direction'), 1),
                'adx': round(sf(L.get('ADX')), 1),
                'plus_di': round(sf(L.get('Plus_DI')), 1),
                'minus_di': round(sf(L.get('Minus_DI')), 1),
                'vwap': round(sf(L.get('VWAP')), 2),
                'stochrsi_k': round(sf(L.get('StochRSI_K'), 50), 1),
                'stochrsi_d': round(sf(L.get('StochRSI_D'), 50), 1),
                'atr': round(atr, 2),
                'obv': round(sf(L.get('OBV')), 0),
                'cci': round(sf(L.get('CCI')), 1),
                'williams_r': round(sf(L.get('WilliamsR'), -50), 1),
                'ichi_tenkan': round(sf(L.get('Ichi_Tenkan')), 2),
                'ichi_kijun': round(sf(L.get('Ichi_Kijun')), 2),
                'volume': si(L.get('Volume')),
                'vol_sma20': round(sf(L.get('Vol_SMA20')), 0),
                'vol_ratio': round(sf(L.get('Volume')) / (sf(L.get('Vol_SMA20'), 1) + 1), 2)
            },
            'fundamentals': {
                'pe': f"{fund_data['pe_val']:.1f}" if fund_data['pe_val'] else 'N/A',
                'pb': f"{info.get('priceToBook', 0):.2f}" if info.get('priceToBook') else 'N/A',
                'roe': f"{_roe:.2f}%" if _roe else 'N/A',
                'debt_equity': f"{fund_data['debt_val']:.1f}" if fund_data['debt_val'] else 'N/A',
                'div_yield': f"{_dy:.2f}%" if _dy else 'N/A',
                'mcap': f"₹{info.get('marketCap', 0) / 1e7:,.0f}Cr" if info.get('marketCap') else 'N/A',
                'sector': info.get('sector', 'NSE Equity'),
                'industry': info.get('industry', 'Equities')
            },
            'week52': {
                'high': round(h52, 2),
                'low': round(l52, 2),
                'position': pos52
            },
            # FIX-08/09: re-centred diagnostic score + honest data-source flags
            'ensemble_v2': ensemble_v2(engines, ens),
            'is_realtime': ('NSE' in str(active_source) and 'TradingView' not in str(active_source)),
            'disclaimer': ('Prices are exchange-delayed whenever data_source is TradingView/Yahoo. '
                           'ML accuracy is a single 80/20 split unless walk_forward_accuracy is quoted.'),
            'chart': chart_data
        }

        return jsonify(clean_json(response_payload))

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f"Internal Server Error: {str(e)}"}), 500


@app.errorhandler(404)
def handle_not_found(e):
    # FIX-10: without this, the Exception handler below swallowed werkzeug's
    # NotFound and every 404 was reported as "500 Server Exception: 404".
    return jsonify({'error': 'Not found',
                    'hint': 'Dashboard: /  •  API: /api/stock/<SYMBOL>  •  /api/search?q=…'}), 404


@app.errorhandler(Exception)
def handle_global_exception(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({'error': e.name, 'code': e.code}), e.code
    import traceback
    traceback.print_exc()
    return jsonify({'error': f"Server Exception: {str(e)}"}), 500

@app.route('/')
@app.route('/dashboard.html')
@app.route('/Dashboard.html')
def dashboard_page():
    """FIX-11: app.py served NO html at all — `GET /` used to be a 500."""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'Dashboard.html')


@app.route('/icon/<path:fname>')
def icon_file(fname):
    return send_from_directory(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon'), fname)


@app.route('/static/<path:fname>')
def static_file(fname):
    return send_from_directory(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'), fname)


@app.after_request
def _no_store(resp):
    if resp.mimetype == 'application/json':
        resp.headers['Cache-Control'] = 'no-store'
    return resp


if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 5000))
    print("=" * 78)
    print(f"🚀 StockAI V6.1 Multi-Tech Hybrid Server → http://0.0.0.0:{PORT}")
    print("👉 Tier 1: TradingView | Tier 2: NSE Direct | Tier 3: Yahoo  (dashboard at /)")
    print("👉 15 audit fixes applied — see AUDIT_REPORT.md")
    print("=" * 78)
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
