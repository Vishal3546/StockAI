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

import hashlib
import inspect
import io
import json
import logging
import math
import os
import pathlib
import re
import socket
from urllib.parse import urlencode
import threading
import time
import warnings
import webbrowser
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import score_calibration as SCORE_CAL

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory
import numpy as np
import pandas as pd
import requests as http_requests

# Suppress all non-critical runtime warnings
warnings.filterwarnings('ignore')

# Initialize Flask Web Application
app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  FIX-36: .env support — har baar $env:… set karne ki zaroorat nahi
#  Chhota built-in loader (python-dotenv jaisi dependency nahi chahiye).
#  Rules:
#    • repo root ki `.env` padhi jaati hai (KEY=VALUE, `#` comment, quotes ok)
#    • REAL environment variable jeetta hai — .env sirf default deta hai
#    • `.env` git me NAHI jaati (.gitignore me hai); secret commit mat karna
# ═══════════════════════════════════════════════════════════════════════════
ENV_FILE = pathlib.Path(__file__).resolve().parent / '.env'


def load_dotenv_file(path=None, override=False):
    """`.env` ko os.environ me load karo. Return: {key: value} jo file me the."""
    target = pathlib.Path(path) if path is not None else ENV_FILE
    loaded = {}
    try:
        # utf-8-sig: PowerShell 5.1 'Set-Content -Encoding UTF8' BOM likhta hai —
        # warna pehla key '\ufeffKEY' ban kar match hi nahi karta.
        text = target.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeDecodeError):
        # Windows par Notepad/PowerShell ne ANSI/cp1252 me save kar diya ho to
        # app crash na ho — .env ignore karke defaults par chalao.
        return loaded
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, _, val = line.partition('=')
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]          # quotes hatado, andar ka # comment nahi todta
        if not key:
            continue
        loaded[key] = val
        if override or key not in os.environ:
            os.environ[key] = val
    return loaded


# FIX-48: `.env` load hone se PEHLE ka snapshot. Iske bina ye batana namumkin hai ki
# koi value `.env` se aayi ya process/Windows environment se — aur wahi ambiguity ne
# real confusion banaya: user ka token Windows User env var me tha, `.env` me nahi,
# par banner sirf "Token auth ON" kehta tha. `load_dotenv_file()` ka default
# `override=False` hai, matlab pehle se set key par `.env` ka value LAGTA HI NAHI.
PRE_DOTENV_KEYS = frozenset(k for k in os.environ)

DOTENV_KEYS = load_dotenv_file()


def config_source(key):
    """Koi config key effective kahan se hui — user ko debug karne ke liye.

    Return: '.env' | 'environment variable' | 'environment variable (.env ko override)'
            | 'default (kahin set nahi)'
    """
    in_env_now = key in os.environ
    in_dotenv = key in DOTENV_KEYS
    was_set_before = key in PRE_DOTENV_KEYS
    if not in_env_now and not in_dotenv:
        return 'default (kahin set nahi)'
    if was_set_before and in_dotenv:
        # .env me likha tha, par pehle se set tha — override=False ki wajah se ignore hua
        return 'environment variable (.env ko override kar raha hai)'
    if was_set_before:
        return 'environment variable'
    if in_dotenv:
        return '.env'
    return 'environment variable'

# ═══════════════════════════════════════════════════════════════════════════
#  FIX-35 (M-11): CORS allowlist + optional token auth + per-IP rate limit
#  Pehle `CORS(app)` tha → har response par `Access-Control-Allow-Origin: *`,
#  koi auth nahi, koi rate limit nahi. Port internet par gaya to poora API khula.
#  Ab:
#    • CORS sirf explicit allowlist (STOCKAI_CORS_ORIGINS); default = same-origin
#    • STOCKAI_API_TOKEN set ho to `/` aur `/api/*` par token zaroori (warna 401)
#    • per-IP sliding-window rate limit (default 240/min → 429 + Retry-After)
#    • security headers: nosniff, frame-deny, referrer, CSP
#  Ye hygiene hai — isse strategy edge nahi badhta.
# ═══════════════════════════════════════════════════════════════════════════
SECURITY = {
    'TOKEN': (os.environ.get('STOCKAI_API_TOKEN') or '').strip(),
    'CORS_ORIGINS': [o.strip().rstrip('/') for o in
                     (os.environ.get('STOCKAI_CORS_ORIGINS') or '').split(',') if o.strip()],
    'RATE_LIMIT_PER_MIN': max(0, int(os.environ.get('STOCKAI_RATE_LIMIT') or 240)),
    'TRUST_PROXY': (os.environ.get('STOCKAI_TRUST_PROXY') or '').strip().lower() in ('1', 'true', 'yes'),
    'COOKIE': 'stockai_token',
    'WINDOW_SECONDS': 60,
    'CSP': ("default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"),
}
_rate_hits: dict = {}
_rate_lock = threading.Lock()


def configure_security(token=None, cors_origins=None, rate_limit_per_min=None, trust_proxy=None):
    """Runtime par security policy badlo (tests/tools ke liye). None = unchanged."""
    if token is not None:
        SECURITY['TOKEN'] = str(token).strip()
    if cors_origins is not None:
        SECURITY['CORS_ORIGINS'] = [str(o).strip().rstrip('/') for o in cors_origins if str(o).strip()]
    if rate_limit_per_min is not None:
        SECURITY['RATE_LIMIT_PER_MIN'] = max(0, int(rate_limit_per_min))
    if trust_proxy is not None:
        SECURITY['TRUST_PROXY'] = bool(trust_proxy)
    reset_rate_limiter()


def refresh_security_from_env():
    """os.environ (ya dobara load ki gayi .env) se SECURITY ko refresh karo."""
    SECURITY['TOKEN'] = (os.environ.get('STOCKAI_API_TOKEN') or '').strip()
    SECURITY['CORS_ORIGINS'] = [o.strip().rstrip('/') for o in
                                (os.environ.get('STOCKAI_CORS_ORIGINS') or '').split(',') if o.strip()]
    SECURITY['RATE_LIMIT_PER_MIN'] = max(0, int(os.environ.get('STOCKAI_RATE_LIMIT') or 240))
    SECURITY['TRUST_PROXY'] = (os.environ.get('STOCKAI_TRUST_PROXY') or '').strip().lower() in ('1', 'true', 'yes')
    reset_rate_limiter()
    return dict(SECURITY)


def reset_rate_limiter():
    with _rate_lock:
        _rate_hits.clear()


def _client_ip():
    # X-Forwarded-For spoof ho sakta hai — sirf explicit TRUST_PROXY par use karo.
    if SECURITY['TRUST_PROXY']:
        fwd = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
        if fwd:
            return fwd
    return request.remote_addr or 'unknown'


def _protected_path(path):
    p = (path or '/').lower()
    return p == '/' or p in ('/dashboard.html',) or p.startswith('/api/')


def _rate_limited(ip):
    limit = SECURITY['RATE_LIMIT_PER_MIN']
    if limit <= 0:
        return False, 0
    now = time.time()
    cutoff = now - SECURITY['WINDOW_SECONDS']
    with _rate_lock:
        hits = [t for t in _rate_hits.get(ip, ()) if t > cutoff]
        if len(hits) >= limit:
            _rate_hits[ip] = hits
            return True, int(max(1, SECURITY['WINDOW_SECONDS'] - (now - hits[0])))
        hits.append(now)
        _rate_hits[ip] = hits
        return False, 0


class _TokenMaskFilter(logging.Filter):
    """FIX-38: server console log me `?token=…` chhupao.

    Werkzeug har request line log karta hai — query string ke saath. Token URL me
    tha to log file/history me bhi chala jaata. Ab record message me
    `token=<anything>` → `token=***` ho jaata hai.
    """

    _PATTERN = re.compile(r'([?&]token=)[^&\s"\']+')

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if 'token=' in msg:
            record.msg = self._PATTERN.sub(r'\1***', msg)
            record.args = ()
        return True


logging.getLogger('werkzeug').addFilter(_TokenMaskFilter())


def _configure_werkzeug_logging():
    """Werkzeug logger ko ek explicit handler do aur root par propagate band karo.

    FIX-47 (duplicate log lines): werkzeug ka `_log()` pehli baar log hone par khud ek
    handler add kar leta hai agar us waqt koi level-handler na mile. Uske BAAD koi
    library (yahan `tvDatafeed` suspect hai) `logging.basicConfig()` call kar deti hai,
    jo root par ek StreamHandler laga deti hai — aur werkzeug root par propagate karta
    hai, isliye tab se har request line DO baar chhapti thi:

        127.0.0.1 - - "GET /api/stock/RELIANCE HTTP/1.1" 200 -
        INFO:werkzeug:127.0.0.1 - - "GET /api/stock/RELIANCE HTTP/1.1" 200 -

    Apna handler + `propagate = False` se ek hi raasta bachta hai, chahe baad me koi
    kitni bhi library root par handler laga de.

    Note: handler JAAN-BOOJH kar khud add karte hain (werkzeug par chhodne ke bajaye) —
    agar `basicConfig()` pehle hi chal chuka ho to werkzeug `_has_level_handler()` True
    pa kar apna handler add nahi karta, aur sirf `propagate=False` karne par output
    poora gayab ho jaata.
    """
    lg = logging.getLogger('werkzeug')
    try:  # werkzeug ka colored handler (private API — fallback zaroori hai)
        from werkzeug._internal import _ColorStreamHandler as _HandlerCls
    except Exception:
        _HandlerCls = logging.StreamHandler
    lg.handlers.clear()
    handler = _HandlerCls()
    handler.setFormatter(logging.Formatter('%(message)s'))
    lg.addHandler(handler)
    lg.setLevel(logging.INFO)
    lg.propagate = False


_configure_werkzeug_logging()


def local_ip_addresses():
    """Is machine ke LAN IP addresses — startup par clickable link dikhane ke liye."""
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith('127.') and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    if not ips:  # fallback: default-route wala interface (koi packet bheje bina)
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(('10.255.255.255', 1))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith('127.'):
                ips.append(ip)
        except OSError:
            pass
        finally:
            if sock is not None:
                sock.close()
    return ips


def auto_open_enabled():
    """STOCKAI_AUTO_OPEN=0 se browser auto-open band (default ON)."""
    val = (os.environ.get('STOCKAI_AUTO_OPEN') or '1').strip().lower()
    return val not in ('0', 'false', 'no', 'off')


def startup_urls(host=None, port=None):
    """Dashboard ke ready-to-click URLs. Token set ho to `?token=…` jud jaata hai,
    taaki aapko link khud na jodni pade. Pehla URL hamesha 127.0.0.1 (same PC)."""
    try:
        port = int(port or os.environ.get('PORT') or 5000)
    except (TypeError, ValueError):
        port = 5000
    bind = (host or os.environ.get('STOCKAI_HOST') or '0.0.0.0').strip()
    if bind in ('0.0.0.0', ''):
        # sab interfaces par bind → localhost + LAN IPs dono reachable hain
        hosts = ['127.0.0.1'] + [ip for ip in local_ip_addresses()]
    else:
        # ek specific interface par bind → sirf wahi address reachable hai
        hosts = [bind]
    token = SECURITY['TOKEN']
    urls = []
    for h in hosts:
        base = f'http://{h}:{port}/'
        urls.append(base + (f'?token={token}' if token else ''))
    return urls


@app.before_request
def _security_gate():
    """Token auth (agar set ho) + per-IP rate limit. Static assets public rehte hain."""
    path = request.path or '/'
    if request.method == 'OPTIONS':
        return None  # CORS preflight — headers after_request me
    token = SECURITY['TOKEN']
    if token and _protected_path(path):
        supplied = (request.headers.get('X-Api-Key') or request.args.get('token')
                    or request.cookies.get(SECURITY['COOKIE']) or '')
        if supplied != token:
            return jsonify({'error': 'unauthorized',
                            'detail': 'STOCKAI_API_TOKEN set hai — X-Api-Key header ya ?token=… bhejein.'}), 401
        if request.cookies.get(SECURITY['COOKIE']) != token:
            g.set_token_cookie = True  # browser ko cookie do, phir dashboard ke fetch khud chalenge
            # FIX-38: token URL me khula dikhta hai (history/log). Cookie set karke
            # HTML pages ko clean URL par bhej do. /api/* par redirect NAHI —
            # programmatic clients (curl/scripts) break ho jaate.
            if request.args.get('token') and not path.startswith('/api/'):
                args = {k: v for k, v in request.args.items(multi=True) if k != 'token'}
                clean = request.path + (('?' + urlencode(args, doseq=True)) if args else '')
                return redirect(clean, code=302)
    if path.startswith('/api/') and not path.startswith('/api/stream'):
        limited, retry = _rate_limited(_client_ip())
        if limited:
            resp = jsonify({'error': 'rate_limited',
                            'detail': f"limit {SECURITY['RATE_LIMIT_PER_MIN']} requests/min per IP"})
            resp.status_code = 429
            resp.headers['Retry-After'] = str(retry)
            return resp
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  SYSTEM CONFIGURATION & HYPERPARAMETERS (100% Zero Hardcoding)
# ═══════════════════════════════════════════════════════════════════════════
CONFIG = {
    'RISK_FREE_RATE': 0.065,
    'MAX_KELLY_PCT': 0.25,
    # FIX-31: plan hit-rate measurement (Kelly ka 'p') ke tunables
    'PLAN_T1_MULT': 2.5,          # target = 2.5x ATR (calculate_risk ke targets ke saath match)
    'PLAN_MEASURE_HORIZON': 20,   # max bars — itne me na SL na T1 → 'unresolved'
    'PLAN_MEASURE_MIN_N': 30,     # itne se kam setups par measurement 'insufficient'
    'PLAN_LCB_Z': 1.0,            # conservative lower-bound (1 sd ≈ 84% one-sided)
    'REGIME_CACHE_TTL': 600,
    # FIX-39 (M-9): NSE master list on-disk cache — import par network call nahi
    'NSE_MASTER_CACHE_HOURS': 24,
    'ML_MIN_DAYS': 50,
    'SUPERTREND_MULTIPLIER': 3.0,
    'SUPERTREND_PERIOD': 10,
    'DEFAULT_CAPITAL': 100000,
    'SEARCH_MAX_RESULTS': 15,
    'CHART_CANDLES': 150,
    'SSE_STREAM_INTERVAL': 3,
    'NSE_MASTER_URL': 'https://archives.nseindia.com/content/equities/EQUITY_L.csv',
    'YAHOO_SEARCH_URL': 'https://query1.finance.yahoo.com/v1/finance/search',
    # FIX-33: sirf daily stock-specific engines ko cross-sectional rank me weight.
    # Market Regime sab shares par same hota hai → sector/stock ranking me 18%
    # constant bias; MTF 5m/15m ka comparable 250-session history available nahi,
    # isliye woh standalone diagnostic hai (daily-only as-of backfill apples-to-apples).
    'ENGINE_WEIGHTS': dict(SCORE_CAL.DAILY_WEIGHTS),
    # FIX-33: regime ka use sirf DIRECTIONAL exposure gate me. Ye risk POLICY hai,
    # model-fit alpha nahi. UNKNOWN/missing index => new positions 0.
    'REGIME_EXPOSURE': {
        'STRONG BULL': {'LONG': 1.00, 'SHORT': 0.00},
        'BULL': {'LONG': 0.75, 'SHORT': 0.25},
        'VOLATILE BULL': {'LONG': 0.50, 'SHORT': 0.25},
        'RECOVERY': {'LONG': 0.50, 'SHORT': 0.50},
        'WEAK BEAR': {'LONG': 0.25, 'SHORT': 0.50},
        'BEAR': {'LONG': 0.00, 'SHORT': 0.50},
        'STRONG BEAR': {'LONG': 0.00, 'SHORT': 0.50},
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

# FIX-46: CONFIG heterogeneous hai (floats, ints, strings, lists, nested dicts),
# isliye type checker `CONFIG['ML_GB_PARAMS']` ka type pura value-union maan leta
# hai aur `**` unpack par "Expected argument after ** to be a mapping" bolta hai.
# Runtime par ye chaaron sach me dict hain (verified), par checker prove nahi kar
# sakta. Sirf `cast` lagaana lint chup karana hota; ye helper usse behtar hai —
# checker ko mapping return karta hai AUR galti se non-dict value aane par loud
# TypeError deta hai, sklearn ke confusing error ke bajaye.
_ML_PARAM_KEYS = ('ML_GB_PARAMS', 'ML_RF_PARAMS', 'ML_LR_PARAMS', 'ML_XGB_PARAMS')


def ml_params(key: str) -> Mapping[str, Any]:
    """`CONFIG` se ML hyperparameter mapping laao, validated.

    `dict[str, Any]` return type ki wajah se `GradientBoostingClassifier(**ml_params(...))`
    type-check clean hota hai. Guard runtime par bhi kaam karta hai.
    """
    if key not in _ML_PARAM_KEYS:
        raise KeyError(f'unknown ML param key {key!r}; expected one of {_ML_PARAM_KEYS}')
    params = CONFIG[key]
    if not isinstance(params, dict):
        raise TypeError(
            f'CONFIG[{key!r}] must be a mapping of hyperparameters, '
            f'got {type(params).__name__}')
    return params

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

# FIX-22: LIVE QUOTE CACHE (TTL + single-flight)
#   Client 2.5s par poll karta hai; 10 tabs = 10x upstream load. Isliye ek hi
#   TTL-cached upstream call sab clients ko serve karti hai (Yahoo/NSE ko safe
#   rakhta hai, rate-limit se bachata hai).
IST = timezone(timedelta(hours=5, minutes=30))   # NSE clock (FIX-23)

# FIX-26: tvDatafeed apne socket errors ko ERROR level par spam karta hai
# ("Connection to remote host was lost" / "no data for symbol") — chahe hum
# usko retry me handle kar rahe hon. Apni honest summary log karte hain,
# isliye library ka noise band kar rahe hain (warna terminal bhara rehta hai).
logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)
logging.getLogger('tvDatafeed.main').setLevel(logging.CRITICAL)
BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
LIVE_TTL = 2.0                      # seconds — se chhota mat karo (upstream load)
_LIVE_CACHE = {}                    # symbol -> (ts, payload)

# FIX-39 (M-7): har call par naya TCP+TLS handshake hota tha (~0.2-0.4 s).
# Shared session connections warm rakhta hai — cold quote measurably faster.
_HTTP = http_requests.Session()
_HTTP.mount('https://', http_requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=32))
_HTTP.mount('http://', http_requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=32))
_LIVE_LOCK = threading.Lock()


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
                # FIX-49: NSE khud timestamp bhejta hai — pehle use ignore karke
                # `datetime.now()` likha jaata tha, matlab quote kitna bhi purana
                # ho "LIVE" hi kehlata. Ab wahi gate jo Yahoo par lagti hai.
                nse_ts = (data.get('timestamp')
                          or price_info.get('lastUpdateTime')
                          or (data.get('info') or {}).get('lastUpdateTime'))
                q_dt = _parse_quote_ts(nse_ts)
                fresh, reason = quote_is_fresh(nse_ts)
                if not fresh:
                    _warn_stale_quote(clean_sym, 'NSE', reason)
                _age = quote_age_minutes(nse_ts)
                _state = feed_state(fresh)
                return {
                    'symbol': clean_sym,
                    'price': round(float(ltp), 2),
                    'change': round(float(change), 2) if change is not None else 0.0,
                    'pChange': round(float(pChange), 2) if pChange is not None else 0.0,
                    'close_price': round(float(close_price), 2) if close_price is not None else round(float(ltp), 2),
                    'dayHigh': round(float(day_hl.get('max') or ltp), 2),
                    'dayLow': round(float(day_hl.get('min') or ltp), 2),
                    'timestamp': q_dt.strftime('%H:%M:%S') if q_dt else '--:--:--',
                    'is_realtime': fresh,
                    'stale': not fresh,
                    'stale_reason': reason,
                    'quote_time': q_dt.strftime('%Y-%m-%d %H:%M:%S') if q_dt else None,
                    # FIX-51: same fields as Yahoo tier — UI ek hi shape padhta hai
                    'feed_state': _state,
                    'feed_label': feed_label(_state, _age),
                    'market_open': is_market_open(),
                    'quote_age_min': round(_age, 1) if _age is not None else None,
                    'source': 'nse'
                }
    except Exception as e:
        print(f"⚠️ Live NSE Quote fetch error for {clean_sym}: {e}")

    # anything other than a clean 200 → assume blocked for 5 minutes
    _NSE_BLOCKED_UNTIL[0] = time.time() + 300
    return None


def fetch_yahoo_live_ltp(symbol, prefer_exch='NSE'):
    """
    FIX-23: Yahoo Finance v8 chart se LIVE LTP (measured 2026-09-30, market hours).

    `interval=1d&range=1d` ka **meta block** hi live price deta hai:
        • 1,297 bytes (1m candle wale call ka 22 KB nahi — 17x sasta)
        • regularMarketPrice = live LTP, ~2s fresh, 66ms latency (measured)
        • chartPreviousClose / regularMarketDayHigh / regularMarketDayLow bhi isi me

    Gotchas (measured, isliye code me handle kar rahe hain):
        • Bina browser User-Agent → **HTTP 429**. UA dena mandatory hai.
        • v7 `/finance/quote` → 401 (crumb chahiye) — use mat karo.
        • Endpoint unofficial hai → TTL cache + fallback chain zaroori.
    """
    clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()
    # FIX-55: user jo exchange chune wo pehle try ho (pehle hamesha .NS-first)
    _sfx = ('.BO', '.NS') if str(prefer_exch).upper() == 'BSE' else ('.NS', '.BO')
    for suffix in _sfx:
        try:
            r = _HTTP.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_sym}{suffix}",
                params={'interval': '1d', 'range': '1d'},
                headers={'User-Agent': BROWSER_UA, 'Accept': 'application/json'},
                timeout=5)
            if r.status_code != 200:
                continue
            meta = (r.json().get('chart', {}).get('result') or [{}])[0].get('meta', {})
            ltp = meta.get('regularMarketPrice')
            if ltp is None:
                continue
            prev = meta.get('chartPreviousClose') or meta.get('previousClose') or ltp
            change = float(ltp) - float(prev)
            pChange = (change / float(prev) * 100.0) if float(prev) else 0.0
            # FIX-49: `regularMarketTime` ab sirf display ke liye nahi — freshness
            # gate bhi isi se chalti hai. Pehle ye hardcoded `is_realtime: True`
            # tha aur missing timestamp par `datetime.now()` quote ka waqt ban
            # jaata tha (fail-open). Ab: stale → is_realtime False + DELAYED chip.
            mkt_time = meta.get('regularMarketTime')
            q_dt = _parse_quote_ts(mkt_time)
            fresh, reason = quote_is_fresh(mkt_time)
            if not fresh:
                _warn_stale_quote(clean_sym, 'Yahoo', reason)
            _age = quote_age_minutes(mkt_time)
            _state = feed_state(fresh)
            return {
                'symbol': clean_sym,
                'price': round(float(ltp), 2),
                'change': round(change, 2),
                'pChange': round(pChange, 2),
                'close_price': round(float(prev), 2),
                'dayHigh': round(float(meta.get('regularMarketDayHigh') or ltp), 2),
                'dayLow': round(float(meta.get('regularMarketDayLow') or ltp), 2),
                'timestamp': q_dt.strftime('%H:%M:%S') if q_dt else '--:--:--',
                'is_realtime': fresh,
                'stale': not fresh,
                'stale_reason': reason,
                'quote_time': q_dt.strftime('%Y-%m-%d %H:%M:%S') if q_dt else None,
                # FIX-51: UI ko guess karne na do — state + asli age server se
                'feed_state': _state,
                'feed_label': feed_label(_state, _age),
                'market_open': is_market_open(exchange=prefer_exch),
                'quote_age_min': round(_age, 1) if _age is not None else None,
                'source': f'yahoo{suffix.lower()}',
            }
        except Exception as e:
            print(f"⚠️ Live Yahoo Quote fetch error for {clean_sym}{suffix}: {e}")
    return None


def get_live_quote(symbol, force=False, prefer_exch='NSE'):
    """
    FIX-24: ONE function, ONE payload shape — chahe data kisi bhi tier se aaye.

    Order:  TTL cache → NSE official (agar block na ho) → Yahoo v8 chart → daily close (stale)

    Har payload me ye keys GUARANTEED hain (UI kabhi `undefined` nahi dikhayega):
        price · change · pChange · close_price · dayHigh · dayLow · timestamp · is_realtime · source
    """
    clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()
    now = time.time()

    # FIX-55: BSE chuna ho to NSE ka official endpoint skip — wo NSE ka LTP dega,
    # BSE ka nahi. Dono exchanges ke close alag hote hain (TCS 2075.00 vs 2079.30).
    _bse = str(prefer_exch).upper() == 'BSE'
    # Cache key me exchange zaroori hai — warna NSE ka quote BSE request par serve
    # ho jaata (TTL 2s chhota hai, par galat exchange ka number galat hi hai).
    _ckey = f"{clean_sym}:{'BSE' if _bse else 'NSE'}"

    if not force:
        with _LIVE_LOCK:
            hit = _LIVE_CACHE.get(_ckey)
        if hit and (now - hit[0]) < LIVE_TTL:
            payload = dict(hit[1])
            payload['cached'] = True
            return payload

    # FIX-56: BSE par Yahoo ka data noticeably kam reliable hai — measured
    # DHOOTIN.BO vs TV-BSE, 21 sessions me 7 mismatch (-5.00 tak), jabki NSE par
    # 22 sessions me 0. Aur Yahoo ka `regularMarketPrice` ek point-in-time
    # snapshot hai (DHOOTIN par 251.0 @ 15:27:03 jabki session close 244.60 tha).
    # Isliye: market BAND ho to Yahoo ka snapshot skip karke TradingView ke daily
    # close (tier 3) par jao — wo session ka asli close hai. Market khula ho to
    # Yahoo hi near-real-time deta hai, isliye tab wahi.
    _bse_closed = _bse and not is_market_open(exchange='BSE')
    quote = None if _bse else fetch_nse_live_ltp(clean_sym)
    if quote is None and not _bse_closed:
        quote = fetch_yahoo_live_ltp(clean_sym, prefer_exch=prefer_exch)

    if quote is None:
        # Tier 3 — last daily close (STALE). Change phir bhi compute hota hai,
        # warna UI me "undefined" aa jaata hai (jaisa pehle SSE path me hota tha).
        try:
            df, src = DATA_MANAGER.smart_fetch(clean_sym, period='5d', interval='1d',
                                               prefer_exch=prefer_exch)
            if df is not None and not df.empty:
                price = sf(df['Close'].iloc[-1])
                prev = sf(df['Close'].iloc[-2]) if len(df) > 1 else None
                if price is not None and price > 0:
                    change = round(price - prev, 2) if prev is not None and prev > 0 else None
                    _lb = None
                    _lb_note = ''
                    try:
                        _lb = pd.Timestamp(df.index[-1])
                        if _lb.tzinfo is not None:
                            _lb = _lb.tz_convert(IST).tz_localize(None)
                        elif 'TradingView' in str(src):
                            # FIX-56: tvDatafeed NAIVE-UTC index deta hai — measured
                            # 2026-10-01 03:45:00, jo IST me 09:15 hai (session open).
                            # Purana code sirf tz-aware convert karta tha, isliye
                            # 03:45:00 seedha UI me chala jaata tha.
                            _lb = _lb.tz_localize('UTC').tz_convert(IST).tz_localize(None)
                        if True:      # tier-3 hamesha interval='1d' fetch karta hai
                            # Daily bar ka timestamp session ka OPEN hota hai (09:15),
                            # close nahi. Use "quote ka waqt" bolna jhooth hoga — price
                            # session ka CLOSE hai. Isliye label karte hain.
                            _lb_note = ' (daily close)'
                    except Exception:
                        _lb = None            # parse na ho to '--:--:--', fake now() nahi
                    quote = {
                        'symbol': clean_sym, 'price': price,
                        'change': change,
                        'pChange': round((change / prev) * 100, 2) if change is not None else None,
                        'close_price': prev,
                        'dayHigh': sfx(df['High'].iloc[-1], 2),
                        'dayLow': sfx(df['Low'].iloc[-1], 2),
                        # FIX-51: pehle yahan bhi `datetime.now()` tha — yaani daily
                        # close ko "abhi ka waqt" bata kar. Ab frame ke last bar ka
                        # asli timestamp, aur feed_state CLOSED/DELAYED server se.
                        'timestamp': (_lb.strftime('%H:%M:%S') if _lb else '--:--:--'),
                        'quote_time': ((f"{_lb.strftime('%Y-%m-%d')}{_lb_note}")
                                       if _lb else None),
                        'is_realtime': False, 'stale': True,
                        'stale_reason': 'daily-close fallback — koi live feed nahi',
                        'feed_state': feed_state(False),
                        'feed_label': feed_label(feed_state(False)),
                        'market_open': is_market_open(exchange=prefer_exch),
                        'quote_age_min': None,
                        'source': src or 'daily-close',
                    }
        except Exception as e:
            print(f"⚠️ Live quote tier-3 error for {clean_sym}: {e}")

    if quote is None:
        return None

    # FIX-32: missing fields ki keys zaroor hon, lekin current price ko
    # fake previous close/day high/day low ya 0% change bana kar na bhejein.
    quote.setdefault('change', None)
    quote.setdefault('pChange', None)
    quote.setdefault('close_price', None)
    quote.setdefault('dayHigh', None)
    quote.setdefault('dayLow', None)
    quote.setdefault('is_realtime', False)
    quote.setdefault('source', 'unknown')

    with _LIVE_LOCK:
        _LIVE_CACHE[_ckey] = (now, dict(quote))
        if len(_LIVE_CACHE) > 256:           # memory bound
            for k in sorted(_LIVE_CACHE, key=lambda s: _LIVE_CACHE[s][0])[:64]:
                _LIVE_CACHE.pop(k, None)
    quote['cached'] = False
    return quote


# ═══════════════════════════════════════════════════════════════════════════
#  FIX-26 — DATA FRESHNESS GUARD ("0s Delay" claim ab verify hoti hai)
# ═══════════════════════════════════════════════════════════════════════════
#  Problem (user ke terminal log me dikha): tvDatafeed ka websocket gir jaata
#  hai, app phir bhi "🔥 0s Delay Live Stream" print kar deta hai — bina ye
#  check kiye ki bars asli me fresh hain. Stale chart + jhootha log.
#
#  Ab: market khula ho aur last bar purana ho → us tier ko REJECT karo aur
#  agle tier par jao; agar sab purane hain to sabse fresh ko "STALE" label ke
#  saath serve karo (silent stale se better hai honest stale).
#  NOTE (real test me pakda gaya): daily bars ki timestamp midnight hoti hai
#  (Yahoo/NSE dono), isliye 1d ko minute-scale se naapna false-positive deta hai
#  — "aaj ka session" 890m 'purana' dikhta hai. Isliye:
#      • 1d  → 4 calendar din (weekend + holiday cover) — date-level freshness
#      • intraday → minute-level (yahan staleness asli matter karti hai)
MAX_AGE_MIN = {'1d': 4 * 24 * 60, '5m': 45, '15m': 60, '1h': 150, '1w': 15 * 24 * 60}


# ═══════════════════════════════════════════════════════════════════════════
#  FIX-50 — NSE/BSE HOLIDAY CALENDAR
# ═══════════════════════════════════════════════════════════════════════════
#  Problem (measured, user ne khud pakda): 2026-10-02 Friday = Mahatma Gandhi
#  Jayanti, NSE/BSE poora din BAND. Par `is_market_open()` sirf weekday + time
#  dekhta tha, isliye 09:31 par `True` bola — aur FIX-49 ka gate minute-level
#  branch me chala gaya:
#      is_market_open()  = True                          ← galat
#      quote_is_fresh()  = 'STALE: quote 1096m purana'   ← FALSE POSITIVE
#  Jabki data bilkul sahi tha — aaj trading hai hi nahi, last quote 01-Oct
#  15:15 hona chahiye aur wahi tha.
#
#  Ye sirf FIX-49 ko nahi, FIX-47 ko bhi chhuta tha: `last_completed_session()`
#  holiday ko normal weekday maan kar "expected session" galat batata tha
#  (2026-10-05 Mon ko expected 2026-10-02 batata, jabki wo holiday tha).
#  `CLOSED_GRACE_DAYS = 1` ne verdict bacha liya tha, par reason string jhoothi thi.
#
#  NSE equity + equity-derivatives ke 2026 ke 16 weekday trading holidays.
#  NOTE: 15-Jan original calendar me nahi tha — NSE ne 12-Jan-2026 ke circular se
#  add kiya (Maharashtra municipal elections). Isliye late circular ke liye
#  `STOCKAI_EXTRA_HOLIDAYS` env / `nse_holidays.txt` file ka raasta bhi hai —
#  patch ke bina update ho jaata hai.
NSE_HOLIDAYS_RAW = {
    '2026-01-15',   # Municipal Corporation Election in Maharashtra (circular se add)
    '2026-01-26',   # Republic Day
    '2026-03-03',   # Holi
    '2026-03-26',   # Shri Ram Navami
    '2026-03-31',   # Shri Mahavir Jayanti
    '2026-04-03',   # Good Friday
    '2026-04-14',   # Dr. Baba Saheb Ambedkar Jayanti
    '2026-05-01',   # Maharashtra Day
    '2026-05-28',   # Bakri Id / Eid ul-Adha
    '2026-06-26',   # Muharram
    '2026-09-14',   # Ganesh Chaturthi
    '2026-10-02',   # Mahatma Gandhi Jayanti
    '2026-10-20',   # Dussehra
    '2026-11-10',   # Diwali-Balipratipada
    '2026-11-24',   # Prakash Gurpurb Sri Guru Nanak Dev
    '2026-12-25',   # Christmas
}
HOLIDAYS_FILE = pathlib.Path(__file__).resolve().parent / 'nse_holidays.txt'


def _parse_iso_date(s):
    try:
        return datetime.strptime(str(s).strip()[:10], '%Y-%m-%d').date()
    except Exception:
        return None


def _load_extra_holidays():
    """`STOCKAI_EXTRA_HOLIDAYS` env + optional `nse_holidays.txt` — dono ISO dates.

    Late NSE circular (jaise 15-Jan-2026) ke liye: patch ki zaroorat nahi.
    Galat format chup-chaap ignore hota hai, crash nahi.
    """
    out = set()
    raw = (os.environ.get('STOCKAI_EXTRA_HOLIDAYS') or '')
    for chunk in raw.replace(';', ',').replace(' ', ',').split(','):
        d = _parse_iso_date(chunk)
        if d:
            out.add(d)
    try:
        for line in HOLIDAYS_FILE.read_text(encoding='utf-8-sig').splitlines():
            line = line.split('#', 1)[0].strip()
            if line:
                d = _parse_iso_date(line)
                if d:
                    out.add(d)
    except (OSError, UnicodeDecodeError):
        pass                     # file optional hai
    return out


NSE_HOLIDAYS = frozenset(
    d for d in (_parse_iso_date(s) for s in NSE_HOLIDAYS_RAW) if d
) | _load_extra_holidays()


def is_market_holiday(d=None):
    """Ye IST date NSE equity ke liye trading holiday hai?"""
    if d is None:
        d = _naive_ist(None).date()
    if isinstance(d, datetime):
        d = d.date()
    return d in NSE_HOLIDAYS


def is_market_open(now=None, exchange='NSE'):
    """Cash session: Mon-Fri 09:15–15:35 (NSE) / 09:15–16:00 (BSE) IST, holiday na ho.

    FIX-47: `now` pehle IST me normalize hota hai. Pehle ye caller ke `.hour`/`.minute`
    ko as-is padhta tha — ek aware UTC datetime (10:36Z = 16:06 IST, market BAND) ko
    10:36 IST maan kar market KHULA bata deta. Production me `_now=None` hota hai isliye
    ye trigger nahi hua, par wahi fail-open class hai jo `frame_age_minutes` me thi.

    FIX-50: holiday check add hua. Pehle Gandhi Jayanti (Fri 02-Oct-2026) ko 09:31 par
    market khula batata tha, jisse FIX-49 ka gate perfectly-sahi data ko STALE keh deta.
    """
    now = _naive_ist(now)
    if now.weekday() > 4:            # Sat/Sun
        return False
    if now.date() in NSE_HOLIDAYS:   # FIX-50
        return False
    hm = now.hour * 60 + now.minute
    # FIX-52: pehle yahan literal `15 * 60 + 40` tha — `SESSION_CLOSE_HM` se
    # alag. Matlab SESSION_CLOSE_HM badalne par is_market_open change hi nahi
    # hota tha; dono constants chup-chaap drift kar gaye the. Ab ek hi source.
    _close = BSE_SESSION_CLOSE_HM if str(exchange).upper() == 'BSE' else SESSION_CLOSE_HM
    return SESSION_OPEN_HM <= hm <= _close


def _naive_ist(now=None):
    """`now` ko naive-IST datetime banao.

    FIX-47: `frame_age_minutes()` me aware−naive subtraction TypeError deta tha, jo
    `except Exception` me chhup kar None ban jaata tha — aur None ko `frame_is_fresh`
    "age unknown" keh kar FRESH maan leta tha (fail-open). Ab aware `now` pehle
    normalize hota hai, isliye wo raasta hi nahi banta.
    """
    now = now or datetime.now(IST)
    if getattr(now, 'tzinfo', None) is not None:
        now = now.astimezone(IST).replace(tzinfo=None)
    return now


def frame_age_minutes(df, now=None):
    """Last bar kitna purana hai (minutes). Index parse na ho to None."""
    try:
        if df is None or len(df) == 0:
            return None
        ts = df.index[-1]
        ts = pd.Timestamp(ts)
        if ts.tzinfo is not None:
            ts = ts.tz_convert(IST).tz_localize(None)
        now = _naive_ist(now)
        return max(0.0, (pd.Timestamp(now) - ts).total_seconds() / 60.0)
    except Exception:
        return None


# FIX-47: market BAND ho tab bhi ek "latest completed session" hota hai — aur usse
# purana bar sach me stale hota hai. Purana code market band hote hi freshness check
# poora bypass kar deta tha (`return True, 'market closed ... (fine)'`), isliye ek
# 10-din purana bar bhi "fine" kehlata tha. Real case jo pakda gaya: NSE-direct tier ne
# TCS ka bar 2 session purana diya (45.6h) jabki Yahoo ke paas aaj ka session tha
# (16.1h) — aur cascade ne pehle tier ko "fine" maan kar Yahoo try hi nahi kiya.
#
# Grace 1 din isliye: ek akel market holiday (bar pichhle weekday ka) false-positive na
# ban jaaye. Weekend ka gap `last_completed_session()` khud skip kar deta hai.
# FIX-52 — 15:40 → 15:35. Aug 3, 2026 se NSE ne F&O wale stocks (TCS, RELIANCE,
# aur universe ke zyada-tar naam) ke liye Closing Auction Session lagaya:
#   continuous trading  09:15 – 15:15
#   Closing Auction     15:15 – 15:35  ← official closing price yahin banta hai
#   post-close          15:50 – 16:00
# Non-CAS securities 15:30 par band, aur BSE apna closing/post-close 15:30–16:00
# chalata hai. 15:35 teeno cover karta hai; 15:40 NSE ke liye 5 min zyada tha
# (us waqt tak official close already publish ho chuka hota hai).
SESSION_OPEN_HM = 9 * 60 + 15          # regular cash session ka pehla minute
SESSION_CLOSE_HM = 15 * 60 + 35        # NSE official CAS close
# FIX-56: BSE ka closing/post-close 16:00 tak chalta hai — measured BSE ka `Ason`
# "01 Oct 26 | 16:00" tha aur Yahoo BO quote_time 15:50:08. Pehle is_market_open
# exchange nahi jaanta tha, isliye BSE mode me 15:36 se "band" bol deta jabki
# BSE par price abhi bhi move kar raha tha.
BSE_SESSION_CLOSE_HM = 16 * 60
CLOSED_GRACE_DAYS = 1


def last_completed_session(now=None):
    """Sabse recent weekday (IST date) jiska cash session complete ho chuka hai.

    Weekend skip hota hai, isliye Mon subah ka answer Friday hota hai — false-positive
    nahi. Holiday calendar nahi hai, isliye ek holiday `CLOSED_GRACE_DAYS` se absorb hota
    hai (aur sab tiers reject hone par honest "no newer session" message milta hai).
    """
    now = _naive_ist(now)
    d = now.date()
    hm = now.hour * 60 + now.minute
    # FIX-50: holiday par aaj ka session kabhi complete hi nahi hota
    today_traded = (d.weekday() <= 4 and d not in NSE_HOLIDAYS)
    if not (today_traded and hm >= SESSION_CLOSE_HM):
        d = d - timedelta(days=1)       # aaj ka session abhi complete nahi hua
    for _ in range(30):                 # Sat/Sun + holidays skip (bound: calendar
        if d.weekday() <= 4 and d not in NSE_HOLIDAYS:   # galat ho to infinite na ho)
            break
        d -= timedelta(days=1)
    return d


def frame_last_date(df):
    """Frame ke last bar ki IST date (None agar parse na ho)."""
    try:
        if df is None or len(df) == 0:
            return None
        ts = pd.Timestamp(df.index[-1])
        if ts.tzinfo is not None:
            ts = ts.tz_convert(IST).tz_localize(None)
        return ts.date()
    except Exception:
        return None


def session_gap_days(df, now=None):
    """Last bar latest completed session se kitne din peeche hai (None = unknown)."""
    bar_date = frame_last_date(df)
    if bar_date is None:
        return None
    return (last_completed_session(now) - bar_date).days


def frame_is_fresh(df, interval='1d', now=None):
    """(fresh?, reason).

    Market KHULA → minute-level limit (intraday staleness yahan asli matter karti hai).
    Market BAND  → session-level check: bar latest completed session se zyada peeche
                   nahi hona chahiye. Minute-limit yahan lagate to har roz close ke baad
                   sab kuch stale dikhta, isliye date-level compare karte hain.
    """
    open_now = is_market_open(now)
    age = frame_age_minutes(df, now)
    if age is None:
        return True, ('market open' if open_now else 'market closed') + ', age unknown'
    limit = MAX_AGE_MIN.get(interval, 150)
    human = (f'{age/60:.1f}h' if age < 48 * 60 else f'{age/1440:.1f}d')
    lim_h = (f'{limit/1440:.0f}d' if limit >= 24 * 60 else f'{limit}m')

    if not open_now:
        gap = session_gap_days(df, now)
        expected = last_completed_session(now)
        if gap is None:
            return True, f'market closed, last bar {human} old (session unknown)'
        if gap <= CLOSED_GRACE_DAYS:
            return True, (f'market closed, last session {frame_last_date(df)} '
                          f'(latest {expected}) — fine')
        return False, (f'STALE: last session {frame_last_date(df)}, latest completed '
                       f'session {expected} ({gap}d behind)')

    if age <= limit:
        return True, f'last bar {human} old (fresh, limit {lim_h})'
    return False, f'STALE: last bar {human} old > {lim_h} limit'


# ═══════════════════════════════════════════════════════════════════════════
#  FIX-49 — LIVE QUOTE FRESHNESS GATE
# ═══════════════════════════════════════════════════════════════════════════
#  Problem (measured 2026-10-02 09:31 IST, Fri, market 16 minute se khula):
#    Yahoo v8 `interval=1d&range=1d` ne RELIANCE ke liye diya
#        regularMarketPrice = 1167.7
#        regularMarketTime  = 2026-10-01 15:15   ← 18.3 GHANTE purana
#    aur `fetch_yahoo_live_ltp()` ne use bina check kiye `is_realtime: True`
#    keh kar bhej diya. `regularMarketTime` sirf ek display string banane ke
#    liye padha jaata tha — freshness kahin verify nahi hoti thi (poore file
#    me uska ek hi reference tha). Usi waqt 5m/1h OHLC frames sahi-sahi STALE
#    reject ho rahe the, isliye UI me chart "DELAYED" bolta tha aur header wala
#    price "LIVE" — dono ek saath, ek hi screen par.
#
#  Ye wahi fail-open class hai jo FIX-47 ne OHLC frames ke liye band ki thi;
#  live-quote path usme cover nahi hua tha. Ab scalar timestamp ke liye
#  `frame_is_fresh()` ka twin hai: `quote_is_fresh()`.
#
#  NOTE: gate sirf LABEL badalta hai (is_realtime / stale) — price phir bhi
#  serve hota hai, UI kabhi blank nahi hoga. Silent stale se honest stale
#  behtar hai. Kill-switch: STOCKAI_LIVE_GATE=off (`.env` me bhi chal jaata hai).
LIVE_MAX_AGE_MIN = max(1, int(os.environ.get('STOCKAI_LIVE_MAX_AGE_MIN') or 10))
LIVE_GATE_ON = (os.environ.get('STOCKAI_LIVE_GATE') or 'on').strip().lower() \
    not in ('0', 'off', 'false', 'no')

_QUOTE_TS_FORMATS = ('%d-%b-%Y %H:%M:%S', '%d-%b-%Y %H:%M',
                     '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d')
_LIVE_STALE_WARNED = {}


def _parse_quote_ts(v):
    """Live-quote ka timestamp → naive-IST datetime (None agar parse na ho).

    Yahoo epoch seconds deta hai; NSE `'02-Oct-2026 09:31:00'` jaisa IST string.
    Dono handle hote hain. Kuch parse na ho to None — caller fail-CLOSED karta hai.
    """
    if v is None or v == '':
        return None
    try:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return (datetime.fromtimestamp(float(v), tz=IST).replace(tzinfo=None)
                    if float(v) > 0 else None)
        s = str(v).strip()
        if s.replace('.', '', 1).isdigit():          # numeric string = epoch
            f = float(s)
            return datetime.fromtimestamp(f, tz=IST).replace(tzinfo=None) if f > 0 else None
        for fmt in _QUOTE_TS_FORMATS:
            try:
                return datetime.strptime(s, fmt)     # NSE IST me hi bhejta hai
            except ValueError:
                continue
        dt = pd.Timestamp(s)                         # ISO-8601 fallback
        if getattr(dt, 'tzinfo', None) is not None:
            dt = dt.tz_convert(IST).tz_localize(None)
        return dt.to_pydatetime()
    except Exception:
        return None


def quote_age_minutes(mkt_time, now=None):
    """Quote kitna purana hai (minutes). Parse na ho to None."""
    dt = mkt_time if isinstance(mkt_time, datetime) else _parse_quote_ts(mkt_time)
    if dt is None:
        return None
    try:
        if getattr(dt, 'tzinfo', None) is not None:
            dt = dt.astimezone(IST).replace(tzinfo=None)
        return max(0.0, (_naive_ist(now) - dt).total_seconds() / 60.0)
    except Exception:
        return None


def quote_is_fresh(mkt_time, now=None):
    """(fresh?, reason) — `frame_is_fresh()` ka scalar twin.

    Market KHULA → quote `LIVE_MAX_AGE_MIN` se purana nahi hona chahiye.
    Market BAND  → quote ka date latest completed session se zyada peeche nahi
                   (`CLOSED_GRACE_DAYS` grace — ek akel holiday absorb karne ko).
    Timestamp missing/unparseable → STALE (fail-CLOSED). Pehle code missing
    timestamp par `datetime.now()` ko quote ka waqt maan leta tha, jo actively
    misleading tha — UI "LIVE" dikhata tha jabki data ka waqt pata hi nahi tha.
    """
    if not LIVE_GATE_ON:
        return True, 'gate off (STOCKAI_LIVE_GATE=off)'
    age = quote_age_minutes(mkt_time, now)
    if age is None:
        return False, 'STALE: quote ka timestamp mila hi nahi (fail-closed)'
    if is_market_open(now):
        if age <= LIVE_MAX_AGE_MIN:
            return True, f'quote {age:.1f}m old (fresh, limit {LIVE_MAX_AGE_MIN}m)'
        return False, f'STALE: quote {age:.0f}m purana > {LIVE_MAX_AGE_MIN}m limit'
    dt = mkt_time if isinstance(mkt_time, datetime) else _parse_quote_ts(mkt_time)
    q_date = dt.date() if dt is not None else None
    if q_date is None:
        return False, 'STALE: quote ka date parse nahi hua (fail-closed)'
    expected = last_completed_session(now)
    gap = (expected - q_date).days
    if gap <= CLOSED_GRACE_DAYS:
        return True, f'market closed, quote {q_date} (latest {expected}) — fine'
    return False, (f'STALE: quote {q_date}, latest completed session '
                   f'{expected} ({gap}d behind)')


def _warn_stale_quote(symbol, source, reason):
    """Stale live-quote par ek line warn karo — par spam nahi.

    UI har ~2s refresh karta hai aur `LIVE_TTL = 2.0` hai, isliye bina throttle
    ke console har 2 second me bhar jaata. Ek symbol ke liye 5 min me ek line.
    """
    now = time.time()
    if now - _LIVE_STALE_WARNED.get(symbol, 0.0) < 300:
        return
    if len(_LIVE_STALE_WARNED) > 512:
        _LIVE_STALE_WARNED.clear()
    _LIVE_STALE_WARNED[symbol] = now
    print(f"⚠️  [LIVE {source}] {symbol} — {reason} → is_realtime=False")


def feed_state(is_realtime, now=None):
    """UI ke liye EK, unambiguous state: 'LIVE' | 'DELAYED' | 'CLOSED'.

    FIX-51: pehle Dashboard do alag badges dikhata tha jo aapas me ladte the —
    upar wala `data_source` ke NAAM se (`/NSE/i.test(src)`) aur price ke baju wala
    `is_realtime` se. Nateeja: ek hi screen par "DELAYED (15-20 min)" aur "LIVE"
    saath me. Aur "(15-20 min)" hardcoded tha jabki asli staleness 19 ghante thi
    (ya market hi band tha). Ab state server se aati hai, UI guess nahi karta.

    CLOSED alag state isliye zaroori hai kyunki FIX-50 ke baad holiday par quote
    "fresh" kehlata hai (last completed session se match karta hai) — jo data ke
    liye sahi hai, par "LIVE" jhooth hoga kyunki trade ho hi nahi raha.
    """
    if not is_market_open(now):
        return 'CLOSED'
    return 'LIVE' if is_realtime else 'DELAYED'


def feed_label(state, age_min=None, now=None):
    """State → insaani label. Asli number, koi hardcoded "15-20 min" nahi."""
    if state == 'CLOSED':
        return 'MARKET CLOSED (holiday)' if is_market_holiday(_naive_ist(now).date()) \
            else 'MARKET CLOSED'
    if state == 'LIVE':
        return 'LIVE'
    if age_min is None:
        return 'DELAYED (age unknown)'
    if age_min < 60:
        return f'DELAYED ({age_min:.0f} min)'
    if age_min < 24 * 60:
        return f'DELAYED ({age_min / 60:.1f} h)'
    return f'DELAYED ({age_min / 1440:.1f} d)'


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

    def fetch_tradingview(self, symbol, n_bars=500, interval_str='1d', prefer_exch='NSE'):
        """Tier 1 Fetch: Direct TradingView Feed"""
        if self.tv is None:
            return None, None

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

            # FIX-53: pehle ye silently NSE se BSE par gir jaata tha aur caller
            # dono ko 'TradingView Direct' keh deta tha. Measured (tvDatafeed,
            # 2026-10-01) — dono exchanges ke close ALAG hain:
            #     NSE:TCS 2075.00   BSE:TCS 2079.30
            #     NSE:RELIANCE 1167.70   BSE:RELIANCE 1166.00
            # User ke dashboard par price 2079.30, ATR 57.54, 52W 3336.7/1976
            # tha — chaaron TV-BSE se EXACT match. Yaani uska poora analysis
            # (indicators, SL, targets, percentile rank) BSE data se bana tha
            # jabki app NSE universe par calibrated hai, aur label 'TradingView
            # Direct' se pata hi nahi chalta tha. Ab exchange track hota hai.
            df = None
            used_exch = None
            # FIX-55: user jo exchange chune wahi pehle try ho. Pehle hamesha
            # NSE-first tha, isliye BSE chunne ka koi rasta hi nahi tha.
            _order = (('BSE', 'NSE') if str(prefer_exch).upper() == 'BSE'
                      else ('NSE', 'BSE'))
            for exch in _order:
                _d = self.tv.get_hist(
                    symbol=clean_sym,
                    exchange=exch,
                    interval=tv_interval,
                    n_bars=n_bars
                )
                if _d is not None and not _d.empty:
                    df, used_exch = _d, exch
                    if exch != _order[0]:
                        # FIX-54: "NSE feed khaali tha" misleading tha — BSE-only
                        # stocks (DHOOTIN = Dhoot Industrial Finance) NSE par hote
                        # hi nahi. Neutral wording.
                        print(f"ℹ️  [TradingView] {clean_sym}: {_order[0]} par data nahi mila, "
                              f"{exch} se le rahe hain (stock us exchange par listed nahi ho "
                              f"sakta). Label me dikhega.")
                    break

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
                    return df, used_exch

        except Exception:
            pass

        return None, None

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

    def smart_fetch(self, symbol, period='2y', interval='1d', n_bars=500, _now=None,
                    prefer_exch='NSE'):
        """
        Executes strict 3-tier cascade with real-time terminal logging.

        FIX-26: har tier ka data FRESHNESS-check hota hai (market khula hone par).
        Pehle tvDatafeed ka socket girne ke baad bhi "0s Delay Live Stream" print
        ho jaata tha; ab wahi tier reject hota hai aur agla try hota hai. Sab
        stale ho to sabse fresh ko clearly "STALE" label ke saath return karte hain.
        """
        clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()
        stale_candidates = []          # (age_min, df, source)

        # ── TIER 1: TradingView Direct (0-Second Delay Live Stream) ──
        if not symbol.startswith('^'):
            df_tv, tv_exch = self.fetch_tradingview(clean_sym, n_bars=n_bars,
                                                     interval_str=interval,
                                                     prefer_exch=prefer_exch)
            if df_tv is not None:
                # FIX-53: exchange label me — 'TradingView Direct' akela ye nahi
                # batata tha ki data NSE se aaya ya BSE fallback se.
                _tv_src = f'TradingView Direct ({tv_exch})' if tv_exch else 'TradingView Direct'
                fresh, why = frame_is_fresh(df_tv, interval, now=_now)
                if fresh:
                    print(f"🔥 [{_tv_src}] {clean_sym} ({interval}) · {len(df_tv)} bars · {why}")
                    return df_tv, _tv_src
                a = frame_age_minutes(df_tv, _now)
                stale_candidates.append((a if a is not None else 1e9, df_tv, _tv_src))
                print(f"⚠️  [TradingView REJECTED] {clean_sym} ({interval}) — {why}, agla tier try kar rahe hain")

        # ── TIER 2: NSE Official Direct Scraper ──
        if interval == '1d' and not symbol.startswith('^'):
            df_nse = self.fetch_nse_direct(clean_sym, days=500)
            if df_nse is not None:
                fresh, why = frame_is_fresh(df_nse, interval, now=_now)
                if fresh:
                    print(f"⚡ [NSE Official Direct] {clean_sym} — Official Exchange Data · {len(df_nse)} bars · {why}")
                    return df_nse, 'NSE Direct'
                a = frame_age_minutes(df_nse, _now)
                stale_candidates.append((a if a is not None else 1e9, df_nse, 'NSE Direct'))
                print(f"⚠️  [NSE Direct REJECTED] {clean_sym} — {why}")

        # ── TIER 3: Yahoo Finance Universal Backup ──
        df_yf = self.fetch_yahoo(symbol, period=period, interval=interval)
        if df_yf is not None:
            fresh, why = frame_is_fresh(df_yf, interval, now=_now)
            if fresh:
                print(f"🌐 [Yahoo] {symbol} ({interval}) · {len(df_yf)} bars · {why}")
                return df_yf, 'Yahoo Finance'
            a = frame_age_minutes(df_yf, _now)
            stale_candidates.append((a if a is not None else 1e9, df_yf, 'Yahoo Finance'))
            print(f"⚠️  [Yahoo REJECTED] {symbol} — {why}")

        # ── Last resort: sabse fresh stale frame (honest label ke saath) ──
        # FIX-47: message ab session-date batata hai, sirf minutes nahi. Aur agar sab
        # tiers EK HI session par agree karte hain to ye "stale feed" nahi, "us din
        # session tha hi nahi" (holiday) ho sakta hai — dono me farq karna zaroori hai,
        # warna har holiday par jhootha STALE alarm bajta.
        if stale_candidates:
            stale_candidates.sort(key=lambda t: t[0])
            age, df, src = stale_candidates[0]
            bar_date = frame_last_date(df)
            expected = last_completed_session(_now)
            gap = session_gap_days(df, _now)
            dates = {frame_last_date(d) for _, d, _ in stale_candidates}
            if len(dates) == 1 and gap is not None and gap > CLOSED_GRACE_DAYS:
                # Sabhi source same purane session par — feed stale nahi, session missing.
                print(f"🟡 [NO NEWER SESSION] {symbol} {interval} — teeno tiers ke paas last "
                      f"session {bar_date} hai (expected {expected}). Agar {expected} ko market "
                      f"holiday tha to ye normal hai; warna data genuinely purana hai. "
                      f"UI ko is_realtime=False milega.")
            else:
                print(f"🟡 [STALE DATA] {symbol} {interval} — sab tiers purane; '{src}' "
                      f"use kar rahe hain (last session {bar_date}, expected {expected}, "
                      f"{gap}d behind). UI ko is_realtime=False milega.")
            return df, src + ' (STALE)'

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

def sf(val, default=None):
    """Safely cast to 4 decimals; missing is None, never an invented zero."""
    return sfx(val, 4, default)


def sfx(val, nd=None, default=None):
    """FIX-32: response/display ke liye safe float — missing par 0 NAHI, None.

    Pehle sf()/si() ka default 0 tha, isliye JSON me missing value '0' bankar
    jaati thi aur dashboard use ASLI reading ki tarah dikhata tha
    (jaise RSI 0, StochRSI 50, volume-ratio 0x). Ab null jaata hai →
    dashboard '—' dikhata hai (FIX-29 wale helpers)."""
    try:
        if val is None:
            return default
        if isinstance(val, float) and np.isnan(val):
            return default
        if pd.isna(val):
            return default
        out = float(val)
        if not math.isfinite(out):
            return default
        return round(out, nd) if nd is not None else out
    except Exception:
        return default


def six(val, default=None):
    """FIX-32: si() ka honest version — missing par None (0 nahi)."""
    v = sfx(val)
    return default if v is None else int(v)


def si(val, default=None):
    """Safely cast to int; missing is None, never an invented zero."""
    return six(val, default)


# ═══════════════════════════════════════════════════════════════════════════
#  DYNAMIC NSE STOCKS DATABASE LOADER (2,100+ Active Equities)
# ═══════════════════════════════════════════════════════════════════════════
MASTER_CACHE_FILE = pathlib.Path(__file__).resolve().parent / 'nse_master_cache.json'
_master_refresh_thread = None


def _normalize_columns(df):
    """FIX-39 (M-8): NSE CSV ke headers me leading spaces hote hain (' SERIES').

    Pehle code sirf us exact string par chalta tha — NSE ne header badla to
    silently 0 stocks load hote aur app curated fallback par chup-chaap gir jaata.
    Ab columns strip + uppercase karke tolerant lookup hota hai.
    """
    return df.rename(columns={c: str(c).strip().upper() for c in df.columns})


def _pick_col(df, *candidates):
    for cand in candidates:
        if cand in df.columns:
            return cand
    return None


def _master_cache_read():
    """(stocks, age_hours) ya None — corrupt/chhoti cache ignore."""
    try:
        data = json.loads(MASTER_CACHE_FILE.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    stocks, ts = data.get('stocks'), data.get('saved_at_utc')
    if not isinstance(stocks, list) or len(stocks) < 500 or not ts:
        return None
    try:
        saved = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if saved.tzinfo is None:
        saved = saved.replace(tzinfo=timezone.utc)
    return stocks, round((datetime.now(timezone.utc) - saved).total_seconds() / 3600.0, 2)


def _master_cache_write(stocks):
    tmp = MASTER_CACHE_FILE.with_name(MASTER_CACHE_FILE.name + '.tmp')
    try:
        tmp.write_text(json.dumps({'saved_at_utc': datetime.now(timezone.utc).isoformat(),
                                   'count': len(stocks), 'stocks': stocks},
                                  ensure_ascii=False), encoding='utf-8')
        tmp.replace(MASTER_CACHE_FILE)
        return True
    except OSError:
        return False


def _fetch_nse_master(verbose=True):
    """NSE EQUITY_L.csv → list[dict] ya None. Kamyaab ho to cache me likhta hai."""
    try:
        if verbose:
            print("🌐 Downloading Official NSE Master Stock List (2,100+ Stocks)...", end=" ", flush=True)
        res = _HTTP.get(CONFIG['NSE_MASTER_URL'],
                        headers={'User-Agent': BROWSER_UA}, timeout=8)
        if res.status_code != 200:
            if verbose:
                print(f"❌ HTTP {res.status_code}")
            return None
        df_csv = _normalize_columns(pd.read_csv(io.StringIO(res.text)))
        c_sym = _pick_col(df_csv, 'SYMBOL')
        c_name = _pick_col(df_csv, 'NAME OF COMPANY', 'NAMEOFCOMPANY', 'NAME')
        c_ser = _pick_col(df_csv, 'SERIES')
        if not (c_sym and c_name and c_ser):
            if verbose:
                print(f"❌ CSV columns nahi mile: {list(df_csv.columns)[:6]}")
            return None
        stocks = []
        for _, row in df_csv.iterrows():
            sym = str(row.get(c_sym, '')).strip()
            name = str(row.get(c_name, '')).strip()
            series = str(row.get(c_ser, '')).strip().upper()
            if sym and name and series in ('EQ', 'BE', 'SM', 'ST'):
                stocks.append({
                    'sym': sym,
                    'name': name,
                    'ex': 'NSE',
                    # FIX-32: NSE master list me sector nahi hota — pehle har stock
                    # par 'NSE Equity' likha jaata tha (placeholder jo asli sector
                    # jaisa lagta hai). Ab None; dashboard blank dikhata hai.
                    'sec': None,
                })
        if len(stocks) <= 500:
            if verbose:
                print(f"⚠️ sirf {len(stocks)} stocks — CSV adhoora lagta hai")
            return None
        _master_cache_write(stocks)
        if verbose:
            print(f"✅ Loaded {len(stocks)} Active NSE Stocks!")
        return stocks
    except Exception as e:
        if verbose:
            print(f"⚠️ NSE Master CSV fetch failed ({e})")
        return None


def _spawn_master_refresh():
    """Background me fresh list lao — import/search block na ho."""
    global _master_refresh_thread, DYNAMIC_STOCK_DB
    if _master_refresh_thread is not None and _master_refresh_thread.is_alive():
        return

    def _run():
        global DYNAMIC_STOCK_DB
        fresh = _fetch_nse_master(verbose=False)
        if fresh:
            DYNAMIC_STOCK_DB = fresh
            print(f"✅ NSE master list background refresh — {fresh and len(fresh)} stocks")

    _master_refresh_thread = threading.Thread(target=_run, name='nse-master-refresh', daemon=True)
    _master_refresh_thread.start()


def load_dynamic_nse_stocks(force=False, background=False):
    """FIX-39 (M-9): import par network call band.

    Order: fresh cache → (stale cache + background refresh) → sync fetch
    → purani cache → curated fallback. STOCKAI_OFFLINE=1 par network bilkul nahi.
    """
    global DYNAMIC_STOCK_DB
    ttl = float(CONFIG['NSE_MASTER_CACHE_HOURS'])
    offline = (os.environ.get('STOCKAI_OFFLINE') or '').strip().lower() in ('1', 'true', 'yes')

    if not force:
        hit = _master_cache_read()
        if hit:
            stocks, age_h = hit
            DYNAMIC_STOCK_DB = stocks
            if age_h <= ttl:
                print(f"💾 NSE master list cache se — {len(stocks)} stocks "
                      f"({age_h}h purani, TTL {ttl:g}h) — koi network call nahi")
                return len(stocks)
            print(f"♻️  NSE master cache {age_h}h purani (TTL {ttl:g}h)"
                  + (" — STOCKAI_OFFLINE, refresh skip" if offline else " — background me refresh"))
            if not offline:
                _spawn_master_refresh()
            return len(stocks)
        if offline:
            DYNAMIC_STOCK_DB = _fallback_master_list()
            print(f"⚠️ STOCKAI_OFFLINE=1 aur cache nahi — curated fallback "
                  f"({len(DYNAMIC_STOCK_DB)} stocks)")
            return len(DYNAMIC_STOCK_DB)
        if background:
            DYNAMIC_STOCK_DB = _fallback_master_list()
            print("🌐 NSE master list background me download ho rahi hai "
                  f"(filhaal curated fallback — {len(DYNAMIC_STOCK_DB)} stocks)")
            _spawn_master_refresh()
            return len(DYNAMIC_STOCK_DB)

    fresh = None if offline else _fetch_nse_master()
    if fresh:
        DYNAMIC_STOCK_DB = fresh
        return len(fresh)
    hit = _master_cache_read()
    if hit:
        DYNAMIC_STOCK_DB = hit[0]
        print(f"⚠️ fetch fail — purani cache use ho rahi hai ({hit[1]}h, {len(hit[0])} stocks)")
    else:
        DYNAMIC_STOCK_DB = _fallback_master_list()
        print(f"⚠️ fetch fail, cache nahi — curated fallback ({len(DYNAMIC_STOCK_DB)} stocks)")
    return len(DYNAMIC_STOCK_DB)


def _fallback_master_list():
    """Curated high-liquidity list — network aur cache dono fail hon to yahi."""
    return [
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


# FIX-39 (M-9): import par sirf cache padha jaata hai; network background me.
load_dynamic_nse_stocks(background=True)


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
            res = _HTTP.get(url, headers=headers, timeout=3).json()
            # FIX-55: dedupe (symbol, exchange) par — pehle sirf symbol par tha,
            # isliye TCS.NSE milne ke baad TCS.BSE drop ho jaata tha. Dono
            # exchanges alag instruments hain (close bhi alag), dono dikhne chahiye.
            existing = {(r['sym'], r.get('ex', 'NSE')) for r in results}

            for item in res.get('quotes', []):
                sym = item.get('symbol', '')
                if sym.endswith('.NS') or sym.endswith('.BO') or item.get('exchange') in ['NSE', 'BSE']:
                    cs = sym.replace('.NS', '').replace('.BO', '')
                    _x = 'NSE' if '.NS' in sym or item.get('exchange') == 'NSE' else 'BSE'
                    if (cs, _x) not in existing:
                        results.append({
                            'sym': cs,
                            'name': item.get('longname') or item.get('shortname') or cs,
                            'ex': _x,
                            'sec': item.get('sector') or 'Equity'
                        })
                        existing.add((cs, _x))
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

            gb_wf = GradientBoostingClassifier(**ml_params('ML_GB_PARAMS'))
            gb_wf.fit(X_tr_wf_s, y_tr_wf)
            wf_results.append(accuracy_score(y_te_wf, gb_wf.predict(X_te_wf_s)))

        # FIX-28: pehle '0.0' tha — UI par ye 'model 0% accurate' jaisa padha
        # jaata tha, jabki sach ye hai ki walk-forward chali hi nahi. Ab None
        # (dashboard ise 'UNKNOWN' dikhata hai).
        wf_accuracy = round(np.mean(wf_results) * 100, 1) if wf_results else None

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
        gb = GradientBoostingClassifier(**ml_params('ML_GB_PARAMS'))
        gb.fit(X_tr_s, y_train)
        gb_preds = gb.predict(X_te_s)
        gb_acc = round(accuracy_score(y_test, gb_preds) * 100, 1)
        gb_prob = float(gb.predict_proba(X_today_s)[0][1])

        # Model 2: Random Forest
        rf = RandomForestClassifier(**ml_params('ML_RF_PARAMS'))
        rf.fit(X_tr_s, y_train)
        rf_preds = rf.predict(X_te_s)
        rf_acc = round(accuracy_score(y_test, rf_preds) * 100, 1)
        rf_prob = float(rf.predict_proba(X_today_s)[0][1])

        # Model 3: Logistic Regression
        lr = LogisticRegression(**ml_params('ML_LR_PARAMS'))
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
            xgb = XGBClassifier(**ml_params('ML_XGB_PARAMS'))
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
    Multi-horizon Intraday / Swing / Long-term KPI scores.

    FIX-32: pehle har indicator sf()/si() se aata tha jinka default 0/50/1 tha —
    matlab missing value par bhi vote lag jaata tha, aur wo bhi arbitrary
    direction me:
        VWAP missing     → 0 → "price > 0" hamesha true → +10 (fake bullish)
        EMA_9/21 missing → dono 0 → -8 (fake bearish)
        CCI missing      → 0 → koi effect nahi (theek)
    Ab jo indicator maujood nahi hai uska vote SKIP hota hai, aur har horizon ke
    saath `basis` aata hai — kitne indicators se score bana.
    """
    L = df.iloc[-1]
    price = sfx(L.get('Close'))
    rsi = sfx(L.get('RSI'))
    vwap = sfx(L.get('VWAP'))
    e9, e21 = sfx(L.get('EMA_9')), sfx(L.get('EMA_21'))
    sma50, sma200 = sfx(L.get('SMA_50')), sfx(L.get('SMA_200'))
    macd, macd_sig = sfx(L.get('MACD')), sfx(L.get('MACD_Signal'))
    std = six(L.get('ST_Direction'))
    srk = sfx(L.get('StochRSI_K'))
    vol, vol_sma = sfx(L.get('Volume')), sfx(L.get('Vol_SMA20'))
    bb_l, bb_u = sfx(L.get('BB_Lower')), sfx(L.get('BB_Upper'))
    adx = sfx(L.get('ADX'))
    cci = sfx(L.get('CCI'))
    obv, obv_ema = sfx(L.get('OBV')), sfx(L.get('OBV_EMA'))

    # ── Intraday Horizon Score ──
    i_s, i_used, i_total = 50, 0, 7  # VWAP, RSI, EMA, ST, MACD, StochRSI, Volume
    if price is not None and vwap is not None:
        i_used += 1
        i_s += 10 if price > vwap else -10
    if rsi is not None:
        i_used += 1
        if rsi < 30:
            i_s += 12
        elif rsi > 70:
            i_s -= 12
    if e9 is not None and e21 is not None:
        i_used += 1
        i_s += 8 if e9 > e21 else -8
    if std is not None:
        i_used += 1
        i_s += 8 if std == 1 else -8
    if macd is not None and macd_sig is not None:
        i_used += 1
        i_s += 6 if macd > macd_sig else -6
    if srk is not None:
        i_used += 1
        if srk < 20:
            i_s += 6
        elif srk > 80:
            i_s -= 6
    if vol is not None and vol_sma is not None and vol_sma > 0:
        i_used += 1
        if vol > vol_sma * 1.5:
            i_s += 5
    i_s = int(max(5, min(98, i_s)))

    # ── Swing Horizon Score ──
    s_s, s_used, s_total = 50, 0, 6
    if e21 is not None and sma50 is not None:
        s_used += 1
        s_s += 10 if e21 > sma50 else -10
    if macd is not None and macd_sig is not None:
        s_used += 1
        s_s += 8 if macd > macd_sig else -8
    if rsi is not None:
        s_used += 1
        if 40 <= rsi <= 60:
            s_s += 6
        elif rsi < 30:
            s_s += 10
        elif rsi > 75:
            s_s -= 8
    if price is not None and bb_l is not None and bb_u is not None:
        s_used += 1
        if price < bb_l:
            s_s += 8
        elif price > bb_u:
            s_s -= 6
    if adx is not None:
        s_used += 1
        if adx > 25:
            s_s += 5
    if cci is not None:
        s_used += 1
        if cci < -100:
            s_s += 6
        elif cci > 100:
            s_s -= 4
    s_s = int(max(5, min(98, s_s)))

    # ── Long-Term Horizon Score ──
    lt_s, lt_used, lt_total = 50, 0, 6
    if price is not None and sma200 is not None and sma200 > 0:
        lt_used += 1
        lt_s += 15 if price > sma200 else -12
    if sma50 is not None and sma200 is not None and sma200 > 0:
        lt_used += 1
        lt_s += 12 if sma50 > sma200 else -8
    pe = fund_data.get('pe_val')
    if pe and isinstance(pe, (int, float)):
        lt_used += 1
        if pe < 25:
            lt_s += 5
        elif pe > 50:
            lt_s -= 5
    roe = fund_data.get('roe_val')
    if roe and isinstance(roe, (int, float)):
        lt_used += 1
        if roe > 0.15:
            lt_s += 6
    debt = fund_data.get('debt_val')
    if debt and isinstance(debt, (int, float)):
        lt_used += 1
        if debt < 0:
            lt_s += 3
        elif debt < 50:
            lt_s += 4
        elif debt > 150:
            lt_s -= 4
    if obv is not None and obv_ema is not None:
        lt_used += 1
        if obv > obv_ema:
            lt_s += 4
    lt_s = int(max(5, min(98, lt_s)))

    master = int((i_s + s_s + lt_s) / 3)

    return {
        'intraday': {
            'score': i_s,
            'action': 'BUY' if i_s >= 65 else 'SELL' if i_s <= 35 else 'HOLD',
            'basis': f'{i_used}/{i_total} indicators measured'
        },
        'swing': {
            'score': s_s,
            'action': 'BUY' if s_s >= 65 else 'SELL' if s_s <= 35 else 'HOLD',
            'basis': f'{s_used}/{s_total} indicators measured'
        },
        'longterm': {
            'score': lt_s,
            'action': 'INVEST' if lt_s >= 65 else 'AVOID' if lt_s <= 35 else 'WATCH',
            'basis': f'{lt_used}/{lt_total} indicators measured'
        },
        'master': {
            'score': master,
            'action': 'STRONG BUY' if master >= 72 else 'STRONG SELL' if master <= 28 else 'NEUTRAL',
            'basis': f'{i_used + s_used + lt_used}/{i_total + s_total + lt_total} indicators measured'
        }
    }

# ═══════════════════════════════════════════════════════════════════════════
#  6 INSTITUTIONAL TRADING ENGINES
# ═══════════════════════════════════════════════════════════════════════════

def _data_ok(df, min_bars=20):
    """
    FIX-28: degenerate data pakdo — pehle engines khali/all-zero frame par bhi
    chupchap 'compute' kar dete the aur garbage score (50/28 aadi) de dete the,
    jo ensemble me asli reading ki tarah chala jaata tha.
    Returns (ok, reason).
    """
    try:
        if df is None or len(df) == 0:
            return False, 'data frame khali hai'
        if len(df) < min_bars:
            return False, f'bahut kam bars ({len(df)} < {min_bars})'
        if 'Close' not in df.columns:
            return False, 'Close column hi nahi hai'
        c = pd.to_numeric(df['Close'], errors='coerce')
        if c.notna().sum() < min_bars:
            return False, f'Close me usable values kam ({int(c.notna().sum())})'
        if float(c.abs().fillna(0).sum()) <= 0:
            return False, 'Close sab 0/NaN hai'
        if float(c.notna().iloc[-1]) == 0:
            return False, 'last Close invalid (0/NaN)'
        return True, ''
    except Exception as e:
        return False, f'data check error: {type(e).__name__}'


# ENGINE 1: Volume Profile (Point of Control / HVN / LVN Analysis)
def engine_volume_profile(df, bins=50):
    try:
        _ok, _why = _data_ok(df)
        if not _ok:
            raise ValueError(_why)
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
    except Exception as _e:
        _why = str(_e)[:80] or type(_e).__name__
        return {
            'name': 'Volume Profile',
            'score': 50,
            'poc': 0,
            'hvn': [],
            'lvn': [],
            'poc_distance': 0,
            'signal': 'N/A',
            'degraded': True,          # FIX-28: fake 50 ensemble me nahi jayega
            'note': f'Volume Profile available nahi: {_why} — averaging se exclude'
        }


# ENGINE 2: RVOL + Cumulative Volume Delta (CVD) + Volume Spread Analysis (VSA)
def engine_rvol_cvd(df):
    try:
        _ok, _why = _data_ok(df)
        if not _ok:
            raise ValueError(_why)
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
    except Exception as _e:
        _why = str(_e)[:80] or type(_e).__name__
        return {
            'name': 'RVOL + CVD + VSA',
            'score': 50,
            'rvol': 1.0,
            'cvd_trend': 'N/A',
            'cvd_divergence': 'NONE',
            'anchored_vwap': 0,
            'vsa': 'N/A',
            'signal': 'N/A',
            'degraded': True,          # FIX-28
            'note': f'RVOL/CVD available nahi: {_why} — averaging se exclude'
        }


# ENGINE 3: Volatility Contraction Pattern (VCP V2)
def engine_vcp(df):
    try:
        _ok, _why = _data_ok(df)
        if not _ok:
            raise ValueError(_why)
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
        tight = (l5r / l5m * 100) if l5m > 0 else None   # FIX-28: 99 sentinel hataya

        a5 = float((r['High'].tail(5) - r['Low'].tail(5)).mean())
        a20 = float((r['High'].tail(20) - r['Low'].tail(20)).mean())
        ar = a5 / (a20 + 1e-9)

        score = 30
        score += 25 if vc >= 3 else 15 if vc >= 2 else 8 if vc >= 1 else 0
        score += 20 if (tight is not None and tight < 3) else 12 if (tight is not None and tight < 5) else 0
        score += 15 if ar < 0.4 else 8 if ar < 0.6 else 0

        return {
            'name': 'VCP V2',
            'score': int(max(5, min(98, score))),
            'contractions': vc,
            'tightness': round(tight, 2) if tight is not None else None,
            'atr_ratio': round(ar, 2),
            'signal': 'READY' if (vc >= 2 and tight is not None and tight < 5) else 'FORMING' if vc >= 1 else 'NONE'
        }
    except Exception as _e:
        _why = str(_e)[:80] or type(_e).__name__
        return {
            'name': 'VCP V2',
            'score': 30,
            'contractions': 0,
            'tightness': None,         # FIX-28: fake 0 ki jagah None (UI '—' dikhata hai)
            'atr_ratio': 0,
            'signal': 'N/A',
            'degraded': True,
            'note': f'VCP available nahi: {_why} — averaging se exclude'
        }


# ENGINE 4: Smart Money Concepts (SMC / ICT Order Blocks & FVG)
def engine_smc(df):
    try:
        _ok, _why = _data_ok(df)
        if not _ok:
            raise ValueError(_why)
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
    except Exception as _e:
        _why = str(_e)[:80] or type(_e).__name__
        return {
            'name': 'SMC / ICT',
            'score': 50,
            'liquidity_sweep': 'NONE',
            'fvg_bullish': [],
            'fvg_bearish': [],
            'order_blocks': [],
            'signal': 'N/A',
            'degraded': True,          # FIX-28
            'note': f'SMC available nahi: {_why} — averaging se exclude'
        }


# ENGINE 5: Market Macro Regime Analysis
def engine_market_regime():
    """
    FIX-27 — Market Regime ke TEEN bug fix (user ke live log se pakde gaye):

    1. `period='6mo'` → sirf ~126 bars aate the, aur EMA-200 ke liye `len(n) > 200`
       check fail hota tha → `ne200` **hardcoded 23000** ho jaata tha. Matlab
       "NIFTY above 200-EMA" asal me "NIFTY above 23000" tha — ek magic number.
       (Test: aaj NIFTY 22,620.45 par hai, asli EMA-200 24,193.22 hai. Dono se
       neeche hone ki wajah se aaj ka natija ittefaqan sahi tha — par agar NIFTY
       23,000-24,193 ke beech hota to app "above 200-EMA" jhoot bolta.)
       → Ab 2y data (~496 bars) fetch hoti hai aur EMA asli compute hoti hai.
    2. `nc = 24000` / `vix = 15.0` jaise silent fallbacks: data na milne par app
       fiction par regime bana deta tha. Ab honest 'UNKNOWN' (score 50 + note)
       return hota hai — jhooti BULL/BEAR se better.
    3. `(now - t).seconds` → timedelta ke `.seconds` me poore din chale jaate hain
       (24h+ purani cache "fresh" dikh sakti thi). Sahi `.total_seconds()`.
    """
    global REGIME_CACHE
    now = datetime.now()
    if (REGIME_CACHE['data'] and REGIME_CACHE['time']
            and (now - REGIME_CACHE['time']).total_seconds() < CONFIG['REGIME_CACHE_TTL']):
        return REGIME_CACHE['data']

    def _unknown(reason):
        return {'name': 'Market Regime', 'score': 50, 'regime': 'UNKNOWN',
                'nifty': 0, 'nifty_200ema': 0, 'nifty_above_200': False,
                'vix': 0, 'vix_status': 'UNKNOWN', 'signal': 'UNKNOWN',
                'degraded': True, 'note': reason}

    try:
        n, nsrc = DATA_MANAGER.smart_fetch('^NSEI', period='2y')     # EMA-200 ke liye 2y chahiye
        vd, _ = DATA_MANAGER.smart_fetch('^INDIAVIX', period='1mo')

        have = 0 if n is None else len(n)
        if have < 200:
            res = _unknown(f'NIFTY history kam hai ({have} bars < 200) — 200-EMA compute nahi ho sakti')
            REGIME_CACHE = {'data': res, 'time': now}
            return res

        c = n['Close']
        nc = float(c.iloc[-1])
        ne200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1])
        ne50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
        na200 = nc > ne200
        na50 = nc > ne50

        vix = float(vd['Close'].iloc[-1]) if vd is not None and len(vd) > 0 else None

        if vix is None:
            # VIX nahi mila → regime sirf trend se, aur vix_status UNKNOWN (fake 15.0 nahi)
            reg, rs = ('BULL', 70) if na200 else ('BEAR', 30)
            vix_status = 'UNKNOWN'
        else:
            vix_status = 'LOW' if vix < 18 else 'NORMAL' if vix < 22 else 'HIGH' if vix < 28 else 'EXTREME'
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
            'nifty_200ema': round(ne200, 2),      # ab ASLI EMA-200 (pehle constant 23000)
            'nifty_50ema': round(ne50, 2),
            'nifty_above_200': na200,
            'vix': round(vix, 2) if vix is not None else 0,
            'vix_status': vix_status,
            'data_bars': have,
            'data_source': nsrc,
            'signal': reg
        }
        REGIME_CACHE = {'data': res, 'time': now}
        return res

    except Exception as e:
        res = _unknown(f'regime fetch error: {type(e).__name__}')
        REGIME_CACHE = {'data': res, 'time': now}
        return res



# ENGINE 6: Real Multi-Timeframe Confluence Engine (5m, 15m, 1h, 1d)
def engine_multitimeframe(symbol, daily_df=None, prefer_exch='NSE'):
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
        data5m, _ = DATA_MANAGER.smart_fetch(symbol, period='5d', interval='5m', n_bars=100,
                                              prefer_exch=prefer_exch)
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
        data1h, _ = DATA_MANAGER.smart_fetch(symbol, period='1mo', interval='1h', n_bars=100,
                                              prefer_exch=prefer_exch)
        results['1h'] = _calc_tf(data1h)

        # 1d timeframe
        if daily_df is not None and len(daily_df) >= 25:
            results['1d'] = _calc_tf(daily_df)
        else:
            data1d, _ = DATA_MANAGER.smart_fetch(symbol, period='6mo', interval='1d', n_bars=100,
                                                  prefer_exch=prefer_exch)
            results['1d'] = _calc_tf(data1d)

        valid_tfs = {k: v for k, v in results.items() if v is not None}
        total = max(len(valid_tfs), 1)
        bc = sum(1 for v in valid_tfs.values() if v['trend'] == 'BULL')

        for tf in ['5m', '15m', '1h', '1d']:
            if tf not in results or results[tf] is None:
                results[tf] = {'trend': 'N/A', 'rsi': 0}

        enough = len(valid_tfs) >= 2          # FIX-28
        score = int((bc / 4) * 100) if enough else 50
        
        return {
            'name': 'Multi-Timeframe',
            'score': max(5, min(98, score)),
            'confluence': f"{bc}/{total}",
            'bullish_count': bc,
            'total': total,
            'signal': 'STRONG' if bc >= 3 else 'MEDIUM' if bc >= 2 else 'WEAK',
            'timeframes': results,
            'degraded': not enough,
            'note': None if enough else f'sirf {len(valid_tfs)} timeframe load hua (<2) — 50 placeholder, averaging se exclude'
        }
        
    except Exception:
        return {
            'name': 'Multi-Timeframe',
            'score': 50,
            'confluence': 'N/A',
            'bullish_count': 0,
            'total': 0,
            'signal': 'ERROR',
            'degraded': True,          # FIX-28
            'note': 'Multi-Timeframe compute nahi hua (error) — averaging se exclude',
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
# ── FIX-33: cross-sectional calibration (the scanner's "composite" is unrelated) ──
# ── FIX-41: recorded ML edge study (C-2) ──
# App ka internal ML number in-sample hai. Purged+embargoed walk-forward +
# permutation null ka OOS verdict `ml_edge_study.json` me recorded hai aur UI
# usi ko quote karta hai — internal number diagnostic label ke saath dikhta hai.
ML_STUDY_PATH = pathlib.Path('ml_edge_study.json')
ML_STUDY_SCHEMA = 1
ML_STUDY_MODEL = 'ml-edge-purged-wf-v1'
ML_STUDY_MAX_AGE_DAYS = 365
_ml_study_cache = {'mtime': None, 'size': None, 'data': None}
_ml_study_lock = threading.Lock()


def load_ml_study():
    """`ml_edge_study.json` padho (mtime/size badle to re-read). Missing/invalid → None.

    Fail-closed in the *honest* direction: artifact na ho to UI "OOS study absent"
    dikhata hai — iska matlab ye NAHI ki edge hai.
    """
    try:
        st = ML_STUDY_PATH.stat()
    except OSError:
        return None
    with _ml_study_lock:
        if (_ml_study_cache['mtime'] == st.st_mtime
                and _ml_study_cache['size'] == st.st_size
                and _ml_study_cache['data'] is not None):
            return _ml_study_cache['data']
        try:
            doc = json.loads(ML_STUDY_PATH.read_text(encoding='utf-8'))
            if doc.get('schema') != ML_STUDY_SCHEMA or doc.get('model') != ML_STUDY_MODEL:
                raise ValueError('schema/model mismatch')
            strategies = doc.get('strategies')
            if not isinstance(strategies, dict) or not strategies:
                raise ValueError('no strategies')
            for name, s in strategies.items():
                if not isinstance(s, dict) or 'accuracy_pct' not in s:
                    raise ValueError(f'strategy {name} incomplete')
            _ml_study_cache.update(mtime=st.st_mtime, size=st.st_size, data=doc)
            return doc
        except Exception:
            _ml_study_cache.update(mtime=st.st_mtime, size=st.st_size, data=None)
            return None


def ml_study_payload():
    """UI/API ke liye compact study block (+ staleness note)."""
    doc = load_ml_study()
    if not doc:
        return {'ready': False,
                'error': ('OOS ML study absent — python tools/build_ml_edge_study.py '
                          'run karein (internal ML number unvalidated hai)'),
                'edge_found': None}
    days = None
    try:
        ts = datetime.fromisoformat(doc['generated_at_utc'])
        days = (datetime.now(timezone.utc) - ts).days
    except Exception:
        pass
    strategies = {}
    for name, s in doc.get('strategies', {}).items():
        strategies[name] = {
            'label': s.get('label'), 'features': s.get('features'),
            'accuracy_pct': s.get('accuracy_pct'),
            'baseline_pct': s.get('baseline_pct'),
            'edge_pp': s.get('edge_pp'),
            'ci95_pp': s.get('ci95_pp'),
            'n_oos': s.get('n_oos'),
            'symbols_with_positive_edge': s.get('symbols_with_positive_edge'),
            'note': s.get('note'),
        }
    return {
        'ready': True,
        'model': doc.get('model'),
        'method': doc.get('method'),
        'period': doc.get('period'),
        'as_of': (doc.get('generated_at_utc') or '')[:10],
        'symbols': doc.get('symbols_scored') or [],
        'strategies': strategies,
        'permutation_null': doc.get('permutation_null'),
        'verdict': doc.get('verdict'),
        'edge_found': bool(doc.get('edge_found')),
        'stale': (days is not None and days > ML_STUDY_MAX_AGE_DAYS),
        'age_days': days,
        'disclosure': doc.get('disclosure'),
    }


_SCORE_FORMULA_HASH = None
_SCORE_CAL_CACHE = {'key': None, 'fitted': None, 'error': None}
_SCORE_CAL_LOCK = threading.Lock()


def score_formula_hash():
    """Invalidate a historical fit if any daily engine/score formula changes."""
    global _SCORE_FORMULA_HASH
    if _SCORE_FORMULA_HASH is None:
        funcs = (_data_ok, engine_volume_profile, engine_rvol_cvd,
                 engine_vcp, engine_smc, SCORE_CAL.stock_rank)
        source = '\n'.join(inspect.getsource(fn) for fn in funcs)
        source += json.dumps({'weights': CONFIG['ENGINE_WEIGHTS'],
                              'percentiles': SCORE_CAL.PERCENTILES,
                              'window': SCORE_CAL.LOOKBACK_BARS,
                              'model': SCORE_CAL.MODEL}, sort_keys=True)
        _SCORE_FORMULA_HASH = hashlib.sha256(source.encode('utf8')).hexdigest()
    return _SCORE_FORMULA_HASH


def _score_history_for(asof_session, bars, symbol):
    """Read validated JSON once per file change; fail CLOSED if missing/stale.

    A single scanner run has 29 `composite` scores, NOT comparable to this
    pipeline. Only 250 historical snapshots of THIS exact stock-score formula
    can supply percentile bands.
    """
    if symbol not in SCORE_CAL.UNIVERSE:
        return None, 'symbol calibration universe me nahi (30 NSE names)'
    path = SCORE_CAL.ARTIFACT_PATH
    try:
        st = path.stat()
        if st.st_size > 5_000_000:
            raise SCORE_CAL.CalibrationError('artifact unusually large')
        key = (str(path), st.st_mtime_ns, st.st_size, score_formula_hash())
    except FileNotFoundError:
        return None, 'score history missing — python tools/build_score_calibration.py run karein'
    except (OSError, SCORE_CAL.CalibrationError) as exc:
        return None, f'history unreadable: {exc}'

    with _SCORE_CAL_LOCK:
        if _SCORE_CAL_CACHE['key'] != key:
            try:
                with path.open(encoding='utf8') as f:
                    data = json.load(f)
                _SCORE_CAL_CACHE['fitted'] = SCORE_CAL.validate_artifact(data, key[-1])
                _SCORE_CAL_CACHE['error'] = None
            except (OSError, ValueError, SCORE_CAL.CalibrationError) as exc:
                _SCORE_CAL_CACHE['fitted'] = None
                _SCORE_CAL_CACHE['error'] = f'invalid score history: {exc}'
            _SCORE_CAL_CACHE['key'] = key
        fitted, error = _SCORE_CAL_CACHE['fitted'], _SCORE_CAL_CACHE['error']
    if not fitted:
        return None, error
    if symbol not in fitted['symbols_covered']:
        return None, f'{symbol} historical snapshots me absent — no fitted rank'
    try:
        SCORE_CAL.for_session(fitted, asof_session, bars=bars,
                              current_day=datetime.now(IST).date().isoformat())
        return fitted, None
    except SCORE_CAL.CalibrationError as exc:
        return None, str(exc)


def ensemble_score(engines, *, asof_session=None, bars=None, symbol=None,
                   calibration=None, data_fresh=True):
    """Four daily stock engines → stock rank; p80/p95 labels only with a valid fit.

    Regime (market-wide) has ZERO stock-score weight and never contributes to
    confluence. Minute-level MTF is diagnostic because historical minute data
    cannot be reconstructed to fit its bands without look-ahead / fake input.
    Missing/partial history, degraded engine, stale fit, or unlisted symbol:
    show raw *partial* score but NEVER emit a BUY/SHORT direction or tradeable.
    """
    result = SCORE_CAL.stock_rank(engines)
    fitted, reason = None, None
    if not result['complete']:
        reason = '4/4 daily stock engines required for comparable calibration'
    elif not data_fresh:
        reason = 'stock daily source STALE — past score ko current label nahi maan sakte'
    elif not asof_session:
        reason = 'bar date unavailable — history fit disabled'
    elif calibration is not None:
        try:
            if symbol is not None and symbol not in calibration['symbols_covered']:
                raise SCORE_CAL.CalibrationError(f'{symbol} historical snapshots me absent')
            fitted = SCORE_CAL.for_session(calibration, asof_session, bars=bars)
        except (KeyError, SCORE_CAL.CalibrationError) as exc:
            reason = str(exc)
    else:
        fitted, reason = _score_history_for(asof_session, bars, symbol)

    ready = bool(fitted) and result['score'] is not None
    threshold = fitted['thresholds'] if ready else None
    action = (SCORE_CAL.label(result['score'], threshold) if ready else
              'DATA_UNAVAILABLE' if result['score'] is None else 'WATCHLIST')
    meta = {
        'ready': ready, 'model': SCORE_CAL.MODEL,
        'asof_session': fitted['asof_session'] if ready else None,
        'sessions': fitted['sessions'] if ready else 0,
        'samples': fitted['samples'] if ready else 0,
        'thresholds': threshold,
        'percentiles': dict(SCORE_CAL.PERCENTILES),
        'relative_rank_pct': (SCORE_CAL.percentile_rank(fitted['_sorted_scores'], result['score'])
                              if ready else None),
        'universe_size': len(SCORE_CAL.UNIVERSE),
        'basis': '250 completed NSE-universe sessions; 4 daily OHLCV engines; trailing 250 bars',
        'note': ('Relative ranking ONLY — calibrated labels do not imply future return / edge'
                 if ready else f'UNFITTED — {reason or "history not ready"}; no directional action'),
    }
    result.update({'action': action, 'tradeable': False, 'rank_only': True,
                   'score_model': SCORE_CAL.MODEL, 'calibration': meta,
                   'note': ' | '.join(x for x in [result.get('note'), meta['note']] if x)})
    return result


def ensemble_v2(engines, ens):
    """Legacy VCP re-centering DIAGNOSTIC; NOT the fitted score scale.

    Uses the same four stock-specific daily engines; Regime and intraday MTF
    remain separate UI panels. No v2 cutoff is used for actions or Kelly.
    """
    w = CONFIG['ENGINE_WEIGHTS']
    live = [e for e in engines if e.get('name') in w and not e.get('degraded')]
    if not live:
        return {'score': None, 'raw_score': ens['score'], 'delta': None,
                'adjustments': [], 'note': 'DIAGNOSTIC unavailable — no daily engines'}
    notes = []
    weighted = 0.0
    for e in live:
        val = e['score']
        if e['name'] == 'VCP V2':
            val = max(5, min(98, 50 + (val - 30)))
            notes.append('VCP re-centred (+20)')
        weighted += val * w[e['name']]
    v2 = round(weighted / sum(w[e['name']] for e in live), 1)
    return {'score': int(round(v2)), 'raw_score': ens['score'],
            'delta': round(v2 - ens['score'], 1) if ens['score'] is not None else None,
            'adjustments': notes,
            'note': ('UNCALIBRATED diagnostic — daily VCP re-centred; percentile labels '
                     'only apply to the raw 4-engine stock rank'),
    }


# ── FIX-31: plan geometry + ASLI measured hit-rate ────────────────────────
MIN_PLAN_SAMPLE = 30          # default; asli value CONFIG['PLAN_MEASURE_MIN_N'] se aati hai


def plan_geometry(score, action=None):
    """Plan geometry follows calibrated RANK band, not arbitrary score cutoffs.

    For historical standalone `calculate_risk(..., action='BUY')` callers only,
    retain the old numeric bucket mapping (deprecated compatibility). The live
    API always supplies BUY_BREAKOUT/BUY_DIP/SHORT_SELL etc from a fitted score.
    Assumed win rates are an explicitly labelled fallback, NOT a model edge.
    """
    if action == 'BUY_BREAKOUT':
        return 1.5, 0.62
    if action == 'BUY_DIP':
        return 2.0, 0.55
    if action in ('SHORT_SELL', 'WATCHLIST', 'AVOID', 'DATA_UNAVAILABLE'):
        return 2.5, 0.45
    # FIX-33: legacy direct unit-call compatibility; NEVER used by live labels.
    if score >= 78:
        return 1.5, 0.62
    if score >= 60:
        return 2.0, 0.55
    return 2.5, 0.45


_PLAN_MEASURE_CACHE = {}


def measure_plan_hit_rate(df, direction, sl_mult, t1_mult=None, horizon=None,
                          min_n=None, symbol=None):
    """
    FIX-31 — is plan ka ASLI win-rate, symbol ke apne data se measure karo.

    Kelly ko 'p' chahiye = P(win). Pehle wo 0.62/0.55/0.45 ASSUMED tha (kisi
    verification se nahi aaya, par usi par qty aur risk_amount bante the).

    Yahan wahi geometry — jo app aaj propose kar raha hai (SL = sl_mult x ATR,
    T1 = t1_mult x ATR) — 2 saal ke daily bars par chalayi jaati hai:

        entry = close[i],  j = i+1 … i+horizon
        LONG : low[j]  <= SL → loss  |  high[j] >= T1 → win
        SHORT: high[j] >= SL → loss  |  low[j]  <= T1 → win

    Ek hi bar me SL aur T1 dono touch → loss (conservative).
    horizon me na SL na T1 → 'unresolved' (rate me count nahi hota).

    Return: dict(rate, wins, losses, unresolved, n, breakeven, ...) ya None
    (data/sample kaafi nahi). Ye ek *unconditional* measurement hai — score
    par conditioned nahi (score-conditional history reconstruct nahi hoti).
    """
    if direction not in ('LONG', 'SHORT'):
        return None
    t1_mult = CONFIG['PLAN_T1_MULT'] if t1_mult is None else t1_mult
    horizon = CONFIG['PLAN_MEASURE_HORIZON'] if horizon is None else horizon
    min_n = CONFIG['PLAN_MEASURE_MIN_N'] if min_n is None else min_n
    key = None
    if symbol:
        try:
            key = (str(symbol), direction, round(float(sl_mult), 3),
                   str(df.index[-1])[:10], len(df))
        except Exception:
            key = None
        if key and key in _PLAN_MEASURE_CACHE:
            return _PLAN_MEASURE_CACHE[key]
    try:
        if df is None or len(df) < max(min_n, horizon) + 20:
            return None
        h = df['High'].astype(float).values
        l = df['Low'].astype(float).values
        c = df['Close'].astype(float).values
        prev_c = np.roll(c, 1)
        prev_c[0] = c[0]
        tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
        atr = pd.Series(tr).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean().values

        wins = losses = unresolved = 0
        for i in range(14, len(c) - 1):
            a = atr[i]
            if not np.isfinite(a) or a <= 0:
                continue
            entry = c[i]
            if direction == 'LONG':
                sl, t1 = entry - sl_mult * a, entry + t1_mult * a
            else:
                sl, t1 = entry + sl_mult * a, entry - t1_mult * a
            end = min(i + horizon, len(c) - 1)
            outcome = None
            for j in range(i + 1, end + 1):
                if direction == 'LONG':
                    if l[j] <= sl:
                        outcome = 'loss'; break
                    if h[j] >= t1:
                        outcome = 'win'; break
                else:
                    if h[j] >= sl:
                        outcome = 'loss'; break
                    if l[j] <= t1:
                        outcome = 'win'; break
            if outcome == 'win':
                wins += 1
            elif outcome == 'loss':
                losses += 1
            else:
                unresolved += 1

        n_eff = wins + losses
        if n_eff < min_n:
            return None
        b = t1_mult / sl_mult
        out = {
            'rate': round(wins / n_eff, 4),
            'wins': wins, 'losses': losses, 'unresolved': unresolved,
            'n': n_eff,
            'breakeven': round(1.0 / (1.0 + b), 4),
            'sl_mult': sl_mult, 't1_mult': t1_mult, 'horizon': horizon,
            'direction': direction,
            'basis': (f"T1-before-SL backtest ({direction}, SL {sl_mult}x ATR / "
                      f"T1 {t1_mult}x ATR, {horizon}-bar horizon, is symbol ka daily data)"),
            'scope': ('unconditional, overlapping in-sample setups; score par conditioned '
                      'nahi, costs/slippage included nahi. Current signal ka profit '
                      'ya statistical edge prove NAHI hota'),
        }
        if key:
            if len(_PLAN_MEASURE_CACHE) > 64:
                _PLAN_MEASURE_CACHE.clear()
            _PLAN_MEASURE_CACHE[key] = out
        return out
    except Exception:
        return None


def regime_exposure(regime, direction, *, required=False):
    """FIX-33: market-wide regime affects DIRECTIONAL exposure, never stock rank.

    The factors are explicitly POLICY, not fitted to P&L. UNKNOWN data =>
    fail closed for live risk; direct standalone calculate_risk() calls without
    a regime keep old unit-test maths but carry an explicit basis label.
    """
    if direction == 'NONE':
        return 0.0, 'no directional trade'
    if regime is None:
        return ((0.0, 'market regime missing — live sizing blocked') if required
                else (1.0, 'standalone calculation: no regime supplied; policy NOT applied'))
    if not isinstance(regime, dict) or regime.get('degraded'):
        return 0.0, 'market regime unavailable/degraded — sizing blocked'
    name = str(regime.get('regime') or 'UNKNOWN').upper()
    policy = CONFIG['REGIME_EXPOSURE'].get(name)
    if policy is None:
        return 0.0, f'market regime {name} unknown — sizing blocked'
    return policy[direction], f'{name}: {direction} exposure cap {policy[direction]:.0%} (risk policy, NOT backtested edge)'


# ── FIX-57: transaction-cost model ────────────────────────────────────────
# Pehle app T1/T2/SL/R-multiple/Kelly sab calculate karta tha bina ye jaane ki
# trade kitne ki padti hai. `tools/study_new_signals.py` ne measure kiya ki
# +0.169% gross signal 0.230% round-trip cost me NEGATIVE ho jaata hai — matlab
# cost ignore karke plan banana ek real risk hai, sirf ek missing detail nahi.
# Rates NSE ke published charges se (equity, intraday, per side unless noted):
#   brokerage 0.03%  ·  STT 0.025%  ·  exchange txn 0.00297%  ·  SEBI 0.0001%
#   stamp duty 0.003% (BUY side only)  ·  GST 18% brokerage+txn par
#   slippage 0.05% (1-2 ticks) — env se override, kyunki ye stock par depend karta hai
def _cost_side(pct, env_key, lo=0.0, hi=5.0):
    """`.env` override, sane range me clamp. Galat value chup-chaap 0 na ho."""
    raw = (os.environ.get(env_key) or '').strip()
    if not raw:
        return float(pct)
    try:
        v = float(raw)
    except ValueError:
        return float(pct)
    return float(min(max(v, lo), hi))


TRADE_COST = {
    'brokerage_pct':    _cost_side(0.03 * 2,   'STOCKAI_COST_BROKERAGE'),
    'stt_pct':          _cost_side(0.025 * 2,  'STOCKAI_COST_STT'),
    'exchange_txn_pct': 0.00297 * 2,
    'sebi_pct':         0.0001 * 2,
    'stamp_pct':        0.003,                  # buy side only
    'gst_pct':          0.18 * (0.03 + 0.00297) * 2,
    'slippage_pct':     _cost_side(0.05 * 2,   'STOCKAI_COST_SLIPPAGE'),
}


def trade_cost_pct(mode='intraday'):
    """Round-trip cost, % of notional. 'delivery' par STT 0.1% (sell only)."""
    base = sum(TRADE_COST.values())
    if str(mode).lower() == 'delivery':
        # intraday STT 0.025%x2 hatao, delivery STT 0.1% sell-only lagao
        base = base - TRADE_COST['stt_pct'] + 0.1
    return round(base, 4)


def cost_plan(cost_pct, price, sl_pct, targets):
    """Break-even move + har target ka NET (cost ke baad), aur honest warnings.

    Do cheezein matter karti hain jo pehle dikhti hi nahi thi:
      1. Break-even move — sirf fees cover karne ke liye price kitna move kare.
      2. Cost-to-risk — cost aapke SL budget ka kitna % hai. SL tight ho to cost
         risk ka bada hissa kha jaata hai, chahe R:R accha dikhe.
    """
    if not price or price <= 0:
        return None
    be = float(cost_pct)
    out = {
        'mode': 'intraday',
        'round_trip_pct': round(be, 3),
        'break_even_pct': round(be, 3),
        'break_even_rs': round(price * be / 100.0, 2),
        'cost_per_share': round(price * be / 100.0, 2),
        'targets_net_pct': {},
        'cost_to_risk_pct': None,
        'warning': None,
    }
    for name, gross in (targets or {}).items():
        if gross is None:
            out['targets_net_pct'][name] = None
        else:
            out['targets_net_pct'][name] = round(float(gross) - be, 3)
    if sl_pct and sl_pct > 0:
        out['cost_to_risk_pct'] = round(be / float(sl_pct) * 100.0, 1)

    warns = []
    t1 = out['targets_net_pct'].get('t1')
    t1g = (targets or {}).get('t1')
    if t1g is not None and t1g <= be:
        warns.append(f'T1 (+{t1g:.2f}%) break-even (+{be:.2f}%) se chhota hai — '
                     f'ye trade fees bhi cover nahi karti.')
    elif t1 is not None and t1 <= 0:
        warns.append(f'T1 cost ke baad negative (+{t1:.2f}%) — target fees se chhota hai.')
    if out['cost_to_risk_pct'] is not None and out['cost_to_risk_pct'] > 20:
        warns.append(f'Cost aapke SL ka {out["cost_to_risk_pct"]:.0f}% hai — SL itna tight '
                     f'hai ki fees risk budget ka bada hissa kha jaati hain.')
    out['warning'] = ' '.join(warns) if warns else None
    return out


_cost_mode = ((os.environ.get('STOCKAI_COST_MODE') or 'intraday').strip().lower()
              or 'intraday')
if _cost_mode not in ('intraday', 'delivery'):
    _cost_mode = 'intraday'


def calculate_risk(price, atr, score, capital=None, action=None,
                   measured_accuracy=None, measured_wf_accuracy=None, measured_baseline=None,
                   plan_measure=None, atr_basis=None, regime=None, require_plan=False):
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
        # Live flow ALWAYS passes an explicitly calibrated action; without one,
        # abstain rather than fabricating a BUY from an arbitrary raw score.
        action = 'WATCHLIST'
    direction = 'SHORT' if 'SHORT' in action else ('LONG' if action.startswith('BUY') else 'NONE')
    _no_trade = (direction == 'NONE')

    atr = max(float(atr or 0), price * 0.005)
    sl_mult, win_rate_assumed = plan_geometry(score, action)

    # FIX-31: ab 'p' MEASURED hota hai — is plan ka asli T1-before-SL hit-rate
    _plan = plan_measure if isinstance(plan_measure, dict) else None
    _measured_ok = bool(_plan and _plan.get('rate') is not None
                        and (_plan.get('n') or 0) >= CONFIG['PLAN_MEASURE_MIN_N'])
    # FIX-33: LIVE API me measurement unavailable hone par assumed bucket se
    # Kelly compute karna bhi band. Standalone helper old calculation disclose
    # karta hai for regression/research; live = require_plan=True fail closed.
    _blocked_no_plan = require_plan and direction != 'NONE' and not _measured_ok
    # FIX-31: point estimate par seedha size dena over-confident hai — RELIANCE par
    # 50.93% mila jabki breakeven 50% aur n=214 (se ≈ 3.4pp) — ye sampling error ke
    # andar hai. Isliye sizing conservative LOWER CONFIDENCE BOUND (1 sd) se hoti hai.
    _p_hat = _se = _p_lcb = None
    if _measured_ok:
        _p_hat = float(_plan['rate'])
        _n_plan = max(int(_plan.get('n') or 0), 1)
        _se = math.sqrt(max(_p_hat * (1.0 - _p_hat), 1e-9) / _n_plan)
        _p_lcb = max(0.0, _p_hat - CONFIG['PLAN_LCB_Z'] * _se)
        win_rate = round(_p_lcb, 4)
    else:
        win_rate = win_rate_assumed
    _plan_breakeven = (float(_plan['breakeven']) if _measured_ok
                       else round(1.0 / (1.0 + 2.5 / sl_mult), 4))
    win_rate_basis_txt = ('measured (T1-before-SL backtest, is symbol ka data)'
                          if _measured_ok else 'assumed (score-bucket heuristic)')

    # FIX-30: ye win_rate ASSUMED hai (score-bucket heuristic) — measured nahi.
    # Ye seedha Kelly sizing me jaata hai (win_rate -> kelly -> qty -> risk_amount),
    # to pehle UI par "Position Size (Kelly)" ek verified number jaisa lagta tha.
    # Ab basis + measured comparison response me jaate hain aur dashboard label karta hai.
    _wr_measured = None
    if measured_accuracy is not None:
        try:
            _m = float(measured_accuracy)
            _wr_measured = round(_m / 100.0, 4) if _m > 1 else round(_m, 4)
        except (TypeError, ValueError):
            _wr_measured = None
    try:
        _wf = float(measured_wf_accuracy) if measured_wf_accuracy is not None else None
    except (TypeError, ValueError):
        _wf = None
    try:
        _base = float(measured_baseline) if measured_baseline is not None else None
    except (TypeError, ValueError):
        _base = None

    # edge_verified = conservative: sirf tab True jab measured accuracy > 52% AUR
    # walk-forward accuracy bhi baseline se kam na ho. Ek hi achha number kaafi nahi
    # (ensemble accuracy in-sample ho sakti hai — RELIANCE par 53.9% ensemble vs
    #  49.4% walk-forward dekha gaya tha).
    _checks = []
    if _wr_measured is not None:
        _checks.append(_wr_measured > 0.52)
    if _wf is not None:
        _checks.append((_wf >= _base) if _base is not None else (_wf > 51.0))
    if _measured_ok:
        # FIX-31: primary basis — measured plan hit-rate breakeven se upar hai?
        _edge_verified = bool(win_rate > _plan_breakeven)
    elif _blocked_no_plan:
        _edge_verified = False  # model accuracy plan win-rate ki jagah nahi le sakti
    else:
        _edge_verified = bool(_checks) and all(_checks)

    if atr_basis and 'assumed' in str(atr_basis):
        # FIX-32: SL/targets ATR par bane hain — ATR assumed ho to ye zaroori disclosure hai
        _note_atr = " ⚠️ ATR missing tha, isliye SL/targets 2% of price par bane hain."
    else:
        _note_atr = ""
    if direction == 'NONE':
        _risk_note = ("Koi directional trade nahi (verdict neutral) — position size "
                      "apply nahi hota." + _note_atr)
    elif _blocked_no_plan:
        _risk_note = ("Plan ka measured sample nahi mila. Score-bucket win-rate "
                      "ASSUMED hai, live Kelly me USE NAHI hua — qty 0. "
                      "Relative percentile profitable signal ka proof nahi.")
    elif _measured_ok:
        _risk_note = (f"Plan ka ASLI hit-rate measured: {_p_hat:.1%} (n={_plan.get('n')} setups, "
                      f"breakeven {_plan_breakeven:.1%}). Sizing conservative lower-bound "
                      f"{_p_lcb:.1%} (±{_se:.1%} sampling error) se — "
                      f"{'geometric breakeven check pass; profitable strategy ka proof NAHI.' if _edge_verified else 'measured edge NAHI (sampling error ke andar) — qty 0 rakha gaya.'}")
        if _wf is not None:
            _risk_note += (f" (ML walk-forward {_wf:.1f}%"
                           + (f" vs baseline {_base:.1f}%" if _base is not None else '') + ")")
    elif _wr_measured is None:
        _risk_note = (f"Position size {win_rate:.0%} ASSUMED win-rate par based hai "
                      f"(verified nahi) — measured plan sample nahi mila.")
    else:
        _gap = (win_rate - _wr_measured) * 100
        _wf_txt = ""
        if _wf is not None:
            _wf_txt = f", walk-forward {_wf:.1f}%" + (f" vs baseline {_base:.1f}%" if _base is not None else "")
        _risk_note = (f"Position size {win_rate:.0%} ASSUMED win-rate par based hai (verified nahi). "
                      f"Model ki measured accuracy {_wr_measured:.1%}{_wf_txt} ({_gap:+.1f}pp gap) — "
                      f"{'verified edge mila hai' if _edge_verified else 'verified edge NAHI mila'}; "
                      f"Kelly allocation ko definite edge ki tarah na maanein.")
    # FIX-32: assumption note teenon risk-note paths par dikhni chahiye, sirf NONE par nahi.
    if direction != 'NONE':
        _risk_note += _note_atr

    if direction == 'SHORT':
        sl, t1, t2, t3 = price + atr * sl_mult, price - atr * 2.5, price - atr * 4.0, price - atr * 6.0
    else:
        sl, t1, t2, t3 = price - atr * sl_mult, price + atr * 2.5, price + atr * 4.0, price + atr * 6.0

    risk_per_share = abs(price - sl)
    b = abs(t1 - price) / risk_per_share if risk_per_share > 0 else 0.0
    kelly = ((win_rate * b - (1 - win_rate)) / b) if b > 0 else 0.0
    kelly = max(0.0, min(kelly, CONFIG['MAX_KELLY_PCT']))
    if _blocked_no_plan:
        kelly = 0.0  # production me unmeasured p se ek bhi share size nahi hoga

    qty_by_risk = int((capital * kelly) / risk_per_share) if risk_per_share > 0 else 0
    qty_by_notional = int(capital / price) if price > 0 else 0
    qty_pre_regime = max(0, min(qty_by_risk, qty_by_notional))
    if direction == 'NONE' or kelly <= 0:
        qty_pre_regime = 0
    exposure, regime_basis = regime_exposure(regime, direction, required=require_plan)
    # ALWAYS floor: exposure cannot accidentally exceed the stated policy cap.
    qty = int(math.floor(qty_pre_regime * exposure))
    notional = round(qty * price, 2)

    if direction == 'NONE':
        status = '⚠️ WATCHLIST / NO TRADE'
    elif _blocked_no_plan:
        status = '⚠️ NO TRADE (measured plan unavailable)'
    elif _measured_ok and kelly <= 0:
        status = '⚠️ NO TRADE (measured edge nahi)'
    elif exposure <= 0:
        status = '⚠️ NO TRADE (market regime exposure 0)'
    elif qty <= 0:
        status = '⚠️ NO TRADE (size cap/rounding)'
    elif direction == 'SHORT':
        status = '🧪 SHORT rank — research only, strategy unverified'
    else:
        status = '🧪 BUY rank — research only, strategy unverified'
    if direction != 'NONE':
        _risk_note += f' | Market regime: {regime_basis}.'

    # FIX-57: cost-aware plan. Ye naya prediction NAHI hai — sirf wo arithmetic
    # jo pehle missing thi. Targets/SL gross the; ab net (cost ke baad) bhi.
    _sl_pct = round(risk_per_share / price * 100, 2) if price > 0 else 0.0
    _cost = cost_plan(trade_cost_pct(_cost_mode), price, _sl_pct, {
        't1': (abs(t1 - price) / price * 100 if price > 0 else None),
        't2': (abs(t2 - price) / price * 100 if price > 0 else None),
        't3': (abs(t3 - price) / price * 100 if price > 0 else None),
    })
    if _cost:
        _cost['mode'] = _cost_mode
        _cost['qty'] = qty
        _cost['round_trip_on_notional'] = (round(notional * _cost['round_trip_pct'] / 100.0, 2)
                                          if notional else 0.0)
        if _cost['warning']:
            _risk_note += f" | 💸 {_cost['warning']}"

    return {
        'direction': direction,
        'cost': _cost,
        'sl': round(sl, 2),
        'sl_pct': _sl_pct,
        't1': round(t1, 2),
        't2': round(t2, 2),
        't3': round(t3, 2),
        'kelly_pct': round(kelly * 100, 1),
        'kelly_pct_after_regime': round(kelly * exposure * 100, 1),
        'kelly_rr_used': round(b, 2),
        # FIX-33: live sample missing ho to koi assumed p actually USE nahi hota.
        'win_rate_used': None if _blocked_no_plan else win_rate,
        'win_rate_basis': ('n/a (no directional trade)' if _no_trade else
                           'unavailable (assumed fallback BLOCKED — measured plan missing)'
                           if _blocked_no_plan else win_rate_basis_txt),
        'win_rate_assumed': win_rate_assumed,
        'plan_hit_rate': round(_p_hat, 4) if _measured_ok else None,
        'plan_hit_rate_lcb': round(_p_lcb, 4) if _measured_ok else None,
        'plan_std_err': round(_se, 4) if _measured_ok else None,
        'plan_sample_size': int(_plan['n']) if _measured_ok else None,
        'plan_breakeven': round(_plan_breakeven, 4) if _measured_ok else None,
        'plan_basis': (_plan.get('basis') if _measured_ok else None),
        'atr_basis': atr_basis,
        'plan_scope': (_plan.get('scope') if _measured_ok else None),
        'win_rate_measured': _wr_measured,
        'accuracy_walk_forward': _wf,
        'accuracy_baseline': _base,
        'edge_verified': _edge_verified,
        'risk_note': _risk_note,
        'qty': qty,
        'qty_pre_regime': qty_pre_regime,
        'regime_exposure_factor': exposure,
        'regime_basis': regime_basis,
        'regime_required': bool(require_plan),
        'qty_uncapped': qty_by_risk,
        'capital': capital,
        'notional': notional,
        'leverage': round(notional / capital, 2) if capital else 0.0,
        'risk_amount': round(qty * risk_per_share, 0),
        'rr_ratio': round(b, 2),
        'entry_zone': f"₹{round(price - 0.3 * atr, 2)} - ₹{round(price + 0.2 * atr, 2)}",
        'trail_sl_plan': f"T1 hit hone ke baad SL ko ₹{round(price, 2)} (Cost) pe shift karein",
        'exec_status': status
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
    # FIX-24: ek hi source-of-truth. `?force=1` manual refresh (refresh icon) ke liye
    force = request.args.get('force', '0') in ('1', 'true', 'yes')
    # FIX-55: ?ex=NSE|BSE
    _ex = (request.args.get('ex') or 'NSE').strip().upper()
    quote = get_live_quote(resolve_symbol(symbol), force=force,
                           prefer_exch=(_ex if _ex in ('NSE', 'BSE') else 'NSE'))
    if quote:
        return jsonify(clean_json(quote))
    return jsonify({'error': 'Live quote unavailable'}), 404


# ═══════════════════════════════════════════════════════════════════════════
#  REAL-TIME SSE (SERVER-SENT EVENTS) LIVE TICK STREAM
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/api/stream/<symbol>')
def sse_live_stream(symbol):
    """FIX-12: proper no-cache headers + heartbeats so the dashboard can use SSE."""
    # FIX-55: SSE bhi wahi exchange use kare jo /api/stock use kar raha hai.
    # `request.args` ko generator ke BAHAR padhna zaroori hai — streaming response
    # me generator request-context ke bahar iterate hota hai.
    _sse_ex = (request.args.get('ex') or 'NSE').strip().upper()
    if _sse_ex not in ('NSE', 'BSE'):
        _sse_ex = 'NSE'

    def event_stream():
        resolved = resolve_symbol(symbol)
        while True:
            # FIX-24: wahi unified payload jo /api/quote deta hai — isliye
            # change/pChange hamesha present rehte hain (pehle SSE fallback me
            # ye keys gayab thi → UI "undefined (undefined%)" dikhata tha).
            quote = get_live_quote(resolved, prefer_exch=_sse_ex)
            if quote:
                yield f"data: {json.dumps(clean_json(quote))}\n\n"
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
    # FIX-55: user jo exchange chune wahi data aaye. Pehle pipeline hamesha
    # NSE-first tha — BSE chunne ka koi rasta hi nahi tha.
    req_exch = (request.args.get('ex') or 'NSE').strip().upper()
    if req_exch not in ('NSE', 'BSE'):
        req_exch = 'NSE'
    df, active_source = DATA_MANAGER.smart_fetch(resolved, period='2y', interval='1d',
                                                 n_bars=CONFIG['CHART_CANDLES'] * 2,
                                                 prefer_exch=req_exch)
    daily_source = active_source  # price path NSE live source se baad me replace ho sakta hai

    if df is None or len(df) < 20:
        _FAIL_CACHE[symbol.upper()] = time.time() + 300
        return jsonify({'error': f"Stock '{symbol}' data not available across all 3 engines!"}), 404

    try:
        df = calculate_all_indicators(df)
        L = df.iloc[-1]
        prev = df.iloc[-2]
        if (sfx(L.get('Close')) is None or sfx(L.get('Close')) <= 0):
            return jsonify({'error': 'Last daily Close unavailable — fake ₹0 price nahi dikhate'}), 503
        
        # ── Exact Live NSE LTP Handshake Hook ──
        live_nse = fetch_nse_live_ltp(resolved)
        if live_nse:
            price = live_nse['price']
            change = live_nse['change']
            pChange = live_nse['pChange']
            active_source = 'NSE Direct Live'
        else:
            price = sf(L['Close'])
            _prev_close = sfx(prev.get('Close'))
            change = round(price - _prev_close, 2) if _prev_close is not None else None
            pChange = (round(change / _prev_close * 100, 2)
                       if change is not None and _prev_close and _prev_close > 0 else None)

        # FIX-32: ATR missing ho to 2% of price fallback — par ab ye DISCLOSE hota hai
        # (pehle chup-chaap hota tha aur risk plan 'ATR-based' lagta tha)
        _atr_raw = sfx(L.get('ATR'))
        atr_basis = 'ATR(14)' if _atr_raw else 'assumed 2% of price (ATR missing)'
        atr = _atr_raw if _atr_raw else price * 0.02
        ml_res = ml_engine(df)

        try:
            import yfinance as yf
            # FIX-54: pehle hamesha `.NS` lagta tha. BSE-only stocks (jaise
            # DHOOTIN = Dhoot Industrial Finance) par Yahoo 404 deta tha aur
            # poora Fundamentals panel N/A dikh jaata tha — jabki `.BO` se sab
            # milta hai (measured: mcap Rs158.6Cr, P/E 2.85, P/B 0.36).
            # Frame kis exchange se aaya wo FIX-53 se pata hai.
            _yf_suffix = '.BO' if '(BSE)' in str(daily_source) else '.NS'
            info = yf.Ticker(f"{resolved}{_yf_suffix}").info or {}
        except Exception:
            info = {}

        fund_data = {
            'pe_val': info.get('trailingPE'),
            'roe_val': info.get('returnOnEquity'),
            'debt_val': info.get('debtToEquity')
        }

        # FIX-33: completed 250-bar DAILY window for both historical and live
        # stock ranking. In-market today's volume/candle is incomplete → last
        # completed bar wins; live LTP still comes from the dedicated quote path.
        now_ist = datetime.now(IST)
        last_daily = pd.Timestamp(df.index[-1]).date()
        partial_today = last_daily == now_ist.date() and is_market_open(now_ist, exchange=req_exch)
        ranked_df = df.iloc[:-1] if partial_today else df
        rank_window = ranked_df.tail(SCORE_CAL.LOOKBACK_BARS)
        rank_session = pd.Timestamp(ranked_df.index[-1]).strftime('%Y-%m-%d')
        e1 = engine_volume_profile(rank_window)
        e2 = engine_rvol_cvd(rank_window)
        e3 = engine_vcp(rank_window)
        e4 = engine_smc(rank_window)
        e5 = engine_market_regime()                 # market-wide exposure ONLY
        e6 = engine_multitimeframe(resolved, daily_df=df,
                                    prefer_exch=req_exch)  # independent diagnostic
        engines = [e1, e2, e3, e4, e5, e6]

        ens = ensemble_score(engines, asof_session=rank_session,
                             bars=len(ranked_df), symbol=resolved,
                             data_fresh='STALE' not in str(daily_source).upper())
        ens['calibration']['reference_session'] = rank_session
        # FIX-30: ML ka measured accuracy bhi bhejo — risk plan ab apna win-rate
        # assumption disclose karta hai (pehle 0.62/0.55/0.45 chup-chaap use hote the)
        _ml_acc = ml_res.get('ensemble_accuracy') if isinstance(ml_res, dict) else None
        _ml_wf = ml_res.get('walk_forward_accuracy') if isinstance(ml_res, dict) else None
        _ml_base = ml_res.get('baseline_accuracy') if isinstance(ml_res, dict) else None
        # FIX-31/33: measured plan geometry calibrated rank band se aati hai.
        # Unfitted/invalid history → no directional label, qty 0. Regime/ML
        # stock rank ko change nahi karte; regime sirf exposure factor hai.
        _dir = ('SHORT' if 'SHORT' in str(ens['action'])
                else 'LONG' if str(ens['action']).startswith('BUY') else 'NONE')
        if ens['score'] is None:
            risk = {'direction': 'NONE', 'qty': 0, 'kelly_pct': 0.0,
                    'win_rate_used': None, 'edge_verified': False,
                    'risk_note': 'Stock-specific daily engines unavailable — no plan.',
                    'exec_status': '⚠️ NO DATA / NO TRADE', 'regime_exposure_factor': 0.0,
                    'regime_basis': 'no stock score', 'qty_pre_regime': 0}
        else:
            _sl_mult, _ = plan_geometry(ens['score'], ens['action'])
            _plan = (measure_plan_hit_rate(ranked_df, _dir, _sl_mult, symbol=resolved)
                     if _dir != 'NONE' else None)
            risk = calculate_risk(price, atr, ens['score'], action=ens['action'],
                                  measured_accuracy=_ml_acc,
                                  measured_wf_accuracy=_ml_wf,
                                  measured_baseline=_ml_base,
                                  plan_measure=_plan, atr_basis=atr_basis,
                                  regime=e5, require_plan=True)
            if not ens['calibration']['ready']:
                risk['risk_note'] += ' | ' + ens['calibration']['note']
        ens['tradeable'] = bool(ens['calibration']['ready'] and
                                ens['action'] == 'BUY_BREAKOUT' and risk.get('qty', 0) > 0 and
                                risk.get('plan_hit_rate') is not None and
                                risk.get('edge_verified') and not e5.get('degraded'))
        ens['tradeable_basis'] = ('Rules passed (p95 rank + in-sample plan check + regime sizing); '
                                  'out-of-sample profit NOT verified' if ens['tradeable'] else
                                  'No executable BUY_BREAKOUT: calibration/plan/regime/size gate')
        kpi = calculate_kpi_scores(df, fund_data)
        patterns = detect_all_candle_patterns(df)

        # Assemble Chart Array
        # FIX-09: modern yfinance `dividendYield` ko PERCENT me deta hai
        # (3.17 == 3.17%). Purana code *100 karta tha aur RELIANCE ke liye
        # 50.00% dividend yield dikhata tha.
        #
        # FIX-52: par `returnOnEquity` ke liye ye assumption GALAT hai — wo
        # FRACTION me aata hai. Measured (Yahoo quoteSummary, TCS.NS):
        #     financialData.returnOnEquity raw = 0.47743, fmt = "47.74%"
        #     financialData.debtToEquity   raw = 10.211,  fmt = "10.21%"
        # Yaani do fields ke OPPOSITE units hain, aur purana code dono ko ek
        # jaisa treat karta tha:
        #     _roe = (_roe/100.0) if (_roe and _roe > 5) else _roe
        #     f"{_roe:.2f}%"
        # → 0.47743 aaye to "0.48%", aur 47.743 aaye to /100 karke phir "0.48%".
        #   DONO branches galat. TCS ka ROE 47.74% hai, dashboard "0.48%" dikha
        #   raha tha — 100x off.
        _dy = info.get('dividendYield')
        _dy = (_dy / 100.0) if (_dy and _dy > 25) else _dy
        _r_raw = info.get('returnOnEquity')
        # |x| <= 2.0 → fraction maano (200% tak ka ROE cover hota hai); usse
        # bada → pehle se percent. yfinance abhi hamesha fraction deta hai, ye
        # guard sirf future-proofing hai.
        _roe = (_r_raw * 100.0) if (_r_raw is not None and abs(_r_raw) <= 2.0) else _r_raw

        chart_data = []
        for idx, row in df.tail(CONFIG['CHART_CANDLES']).iterrows():
            # Missing OHLC par fake ₹0 candle chart ko distort karta tha.
            o, h, low, c = (sfx(row.get(k), 2) for k in ('Open', 'High', 'Low', 'Close'))
            if (any(v is None or v <= 0 for v in (o, h, low, c)) or h < low):
                continue
            t_str = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)[:10]
            chart_data.append({
                'time': t_str, 'open': o, 'high': h, 'low': low, 'close': c,
                'volume': six(row.get('Volume'))  # missing volume → null; histogram skip karta hai
            })

        h52 = sfx(df['High'].tail(252).max(), 2)      # FIX-32: missing → None (0 nahi)
        l52 = sfx(df['Low'].tail(252).min(), 2)
        pos52 = (round((price - l52) / (h52 - l52 + 1e-10) * 100, 1)
                 if (h52 is not None and l52 is not None) else None)

        response_payload = {
            'symbol': resolved,
            'data_source': active_source,
            'price': price,
            'change': change,
            'pChange': pChange,
            'ml': ml_res,
            # FIX-41 (C-2): recorded OOS study — internal `ml` number sirf
            # diagnostic hai; UI/API ka "edge hai ya nahi" jawaab yahaan se aata hai.
            'ml_study': ml_study_payload(),
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
            # FIX-32: missing indicator ab None (null) — pehle 0/50/-50/1 defaults
            # asli reading jaise chhapte the
            'indicators': {
                'rsi': sfx(L.get('RSI'), 1),
                'ema9': sfx(L.get('EMA_9'), 2),
                'ema21': sfx(L.get('EMA_21'), 2),
                'ema50': sfx(L.get('EMA_50'), 2),
                'sma50': sfx(L.get('SMA_50'), 2),
                'sma200': sfx(L.get('SMA_200'), 2),
                'macd': sfx(L.get('MACD'), 2),
                'macd_signal': sfx(L.get('MACD_Signal'), 2),
                'macd_hist': sfx(L.get('MACD_Hist'), 2),
                'bb_upper': sfx(L.get('BB_Upper'), 2),
                'bb_lower': sfx(L.get('BB_Lower'), 2),
                'bb_pctb': sfx(L.get('BB_PctB'), 2),
                'bb_width': sfx(L.get('BB_Width'), 2),
                'supertrend': sfx(L.get('Supertrend'), 2),
                'st_direction': six(L.get('ST_Direction')),
                'adx': sfx(L.get('ADX'), 1),
                'plus_di': sfx(L.get('Plus_DI'), 1),
                'minus_di': sfx(L.get('Minus_DI'), 1),
                'vwap': sfx(L.get('VWAP'), 2),
                'stochrsi_k': sfx(L.get('StochRSI_K'), 1),
                'stochrsi_d': sfx(L.get('StochRSI_D'), 1),
                'atr': round(atr, 2),
                'atr_basis': atr_basis,
                'obv': sfx(L.get('OBV'), 0),
                # FIX-51: UI ka ACCUMULATE/DISTRIBUTE label pehle `obv > 0` se banta tha,
                # jabki backend scoring `obv > obv_ema` use karta hai (calculate_ensemble).
                # Dono same comparison par lao — isliye obv_ema bhi bhejo.
                'obv_ema': sfx(L.get('OBV_EMA'), 0),
                'cci': sfx(L.get('CCI'), 1),
                'williams_r': sfx(L.get('WilliamsR'), 1),
                'ichi_tenkan': sfx(L.get('Ichi_Tenkan'), 2),
                'ichi_kijun': sfx(L.get('Ichi_Kijun'), 2),
                'volume': six(L.get('Volume')),
                'vol_sma20': sfx(L.get('Vol_SMA20'), 0),
                'vol_ratio': (round(float(L.get('Volume')) / float(L.get('Vol_SMA20')), 2)
                              if (sfx(L.get('Volume')) is not None
                                  and (sfx(L.get('Vol_SMA20')) or 0) > 0) else None)
            },
            'fundamentals': {
                'pe': f"{fund_data['pe_val']:.1f}" if fund_data['pe_val'] else 'N/A',
                'pb': f"{info.get('priceToBook', 0):.2f}" if info.get('priceToBook') else 'N/A',
                'roe': f"{_roe:.2f}%" if _roe else 'N/A',
                # FIX-51: yfinance ka `debtToEquity` PERCENTAGE hota hai (36.7 = 36.7%),
                # ratio nahi. Bina '%' ke 36.7x lagta tha. Aur `if debt_val` falsy-check
                # tha, isliye asli 0.0 D/E (zero-debt company) bhi 'N/A' ban jaata tha.
                # FIX-55: :.1f se DHOOTIN ka 0.027 -> "0.0%" ban jaata tha (precision
                # lost, value fake nahi). 1 se chhoti value par 2 extra decimals.
                'debt_equity': ('N/A' if fund_data['debt_val'] is None else
                                (f"{fund_data['debt_val']:.3f}% D/E"
                                 if abs(fund_data['debt_val']) < 1
                                 else f"{fund_data['debt_val']:.1f}% D/E")),
                'div_yield': f"{_dy:.2f}%" if _dy else 'N/A',
                'mcap': f"₹{info.get('marketCap', 0) / 1e7:,.0f}Cr" if info.get('marketCap') else 'N/A',
                'sector': info.get('sector') or None,        # FIX-32: 'NSE Equity' invented nahi
                'industry': info.get('industry') or None
            },
            'week52': {
                'high': h52,
                'low': l52,
                'position': pos52
            },
            # FIX-08/09: re-centred diagnostic score + honest data-source flags
            'ensemble_v2': ensemble_v2(engines, ens),
            # FIX-50: pehle ye SOURCE KE NAAM se decide karta tha — `'NSE' in
            # str(active_source)`. Do raaste jahan ye jhooth bolta tha:
            #   • smart_fetch stale frame par `src + ' (STALE)'` return karta hai,
            #     to 'NSE Official Direct (STALE)' me bhi 'NSE' match ho jaata tha
            #   • `active_source = 'NSE Direct Live'` tab set hota hai jab NSE quote
            #     mile — stale ho ya fresh, farq nahi padta tha
            # Ab single source of truth: FIX-49 ka gate. Realtime = live quote mila
            # AUR usne gate pass kiya. Warna price daily close se aaya hai → False.
            'is_realtime': bool(live_nse and live_nse.get('is_realtime')),
            'realtime_reason': ((live_nse.get('stale_reason') or 'live NSE quote, gate passed')
                                if live_nse else 'koi live quote nahi — price daily close se'),
            # FIX-51: top badge pehle `/NSE/i.test(data_source)` se liveness nikalta tha
            # — yaani source ke NAAM se, bilkul wahi bug jo FIX-50 ne backend me theek
            # kiya tha. Ab server state bhejta hai; UI guess nahi karta.
            'feed_state': feed_state(bool(live_nse and live_nse.get('is_realtime'))),
            'feed_label': feed_label(feed_state(bool(live_nse and live_nse.get('is_realtime'))),
                                     (live_nse or {}).get('quote_age_min')),
            'market_open': is_market_open(exchange=req_exch),
            'market_holiday': is_market_holiday(),
            # FIX-52: analysis kis price se bani aur frame ka close kya tha — ye
            # dono disclose karte hain. Karan: header ka bada number /api/quote se
            # aata hai (Yahoo ka `regularMarketPrice`) jabki poora plan yahan wale
            # `price` se banta hai. 2026-10-02 (holiday) par ye do alag the:
            #     header  ₹2,075.00  (Yahoo, regularMarketTime 15:15:00)
            #     plan    ₹2,079.30  (frame close = BSE ka official CAS close)
            # Aug-2026 se NSE ka Closing Auction 15:15–15:35 chalta hai, aur
            # Yahoo ka timestamp 15:15 tha — yaani continuous session ka last
            # trade, official close nahi. Dashboard ab dono compare karke warn
            # karta hai bajaye chup-chaap koi ek chunne ke.
            'frame_close': sfx(L.get('Close'), 2),
            'price_basis': ('live NSE LTP' if live_nse
                            else 'daily frame close (koi live NSE quote nahi)'),
            # FIX-53: frame kis exchange se aaya. App ka score calibration NSE
            # universe par fitted hai (30 NSE naam, 250 NSE sessions), isliye BSE
            # frame par percentile ranks technically NSE distribution se compare
            # ho rahe hote hain. Chhupana nahi, batana.
            'requested_exchange': req_exch,
            'frame_exchange': ('BSE' if '(BSE)' in str(daily_source)
                               else 'NSE' if '(NSE)' in str(daily_source) else None),
            # FIX-54: 'NSE feed khaali tha' aur 'ye stock NSE par listed hi nahi'
            # do alag baatein hain. DHOOTIN (Dhoot Industrial Finance) NSE master
            # me hai hi nahi — uska NSE symbol DHOOTTRANS nahi, wo ALAG company
            # hai (Dhoot Transmission). Bina is field ke dashboard hamesha "feed
            # fail hua" bolta, jo galat tha.
            'on_nse_master': any(s.get('sym', '').upper() == resolved.upper()
                                 for s in DYNAMIC_STOCK_DB),
            'disclaimer': ('Prices are exchange-delayed whenever data_source is TradingView/Yahoo. '
                           'ml.* accuracy is in-sample/diagnostic; the OOS verdict comes from '
                           'ml_study (tools/build_ml_edge_study.py) — see ml_study.verdict.'),
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
def _harden_response(resp):
    # FIX-35: CORS sirf allowlisted origin ke liye (wildcard nahi)
    origin = (request.headers.get('Origin') or '').rstrip('/')
    if origin and origin in SECURITY['CORS_ORIGINS']:
        resp.headers['Access-Control-Allow-Origin'] = origin
        resp.headers['Vary'] = 'Origin'
        resp.headers['Access-Control-Allow-Headers'] = 'X-Api-Key, Content-Type'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'no-referrer')
    resp.headers.setdefault('Content-Security-Policy', SECURITY['CSP'])
    if getattr(g, 'set_token_cookie', False) and SECURITY['TOKEN']:
        resp.set_cookie(SECURITY['COOKIE'], SECURITY['TOKEN'],
                        httponly=True, samesite='Lax', max_age=86400)
    if resp.mimetype == 'application/json':
        resp.headers['Cache-Control'] = 'no-store'
    return resp


if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 5000))
    HOST = os.environ.get('STOCKAI_HOST', '0.0.0.0')
    print("=" * 78)
    print(f"🚀 StockAI V6.1 Multi-Tech Hybrid Server → http://{HOST}:{PORT}")
    print("👉 Tier 1: TradingView | Tier 2: NSE Direct | Tier 3: Yahoo  (dashboard at /)")
    print(f"👉 Config: {('.env loaded — ' + str(len(DOTENV_KEYS)) + ' keys') if DOTENV_KEYS else '.env nahi mila (env vars/defaults)'}")
    print(f"👉 CORS allowlist: {SECURITY['CORS_ORIGINS'] or 'same-origin only'} | "
          f"rate limit: {SECURITY['RATE_LIMIT_PER_MIN']}/min/IP")
    # FIX-50: holiday calendar ki coverage. Saal badalne par calendar purana ho
    # jaata hai aur har holiday par jhootha STALE warning aane lagega — chup-chaap
    # nahi, startup par batao.
    _hol_years = sorted({d.year for d in NSE_HOLIDAYS})
    _this_year = _naive_ist(None).year
    if _this_year not in _hol_years:
        print(f"⚠️  NSE holiday calendar me {_this_year} NAHI hai (sirf {_hol_years}) — "
              f"har holiday par jhootha STALE warning aayega.")
        print(f"   Fix: nse_holidays.txt me {_this_year}-MM-DD lines daalo, ya "
              f".env me STOCKAI_EXTRA_HOLIDAYS=...")
    else:
        _n = sum(1 for d in NSE_HOLIDAYS if d.year == _this_year)
        print(f"👉 NSE holidays: {_n} dates loaded for {_this_year} "
              f"(aaj {'HOLIDAY — market band' if is_market_holiday() else 'trading day'})")
    if SECURITY['TOKEN']:
        print("🔒 Token auth ON — neeche wali link me token pehle se juda hua hai")
        # FIX-48: token KAHAN se aaya — .env se ya Windows/process env se. Pehle ye
        # nahi dikhta tha, isliye user apne .env me token dhoondhta reh gaya jabki wo
        # Windows User env var me tha (aur .env ka value override=False ki wajah se
        # ignore ho raha tha).
        _src = config_source('STOCKAI_API_TOKEN')
        print(f"   ↳ source: {_src}")
        if 'override' in _src:
            print("   ⚠️  .env me bhi STOCKAI_API_TOKEN likha hai par WO IGNORE ho raha hai.")
            print("      Env var hatane ke liye: [Environment]::SetEnvironmentVariable("
                  "'STOCKAI_API_TOKEN',$null,'User')  — phir naya terminal kholo.")
    else:
        print("⚠️  STOCKAI_API_TOKEN set NAHI hai → LAN ka koi bhi device ye API use kar sakta hai.")
        print("    Token chahiye to .env me STOCKAI_API_TOKEN bhar dein (ya $env: set karein).")
    # FIX-37: link khud jodni nahi padegi — server ready-to-click URLs print karta hai
    urls = startup_urls(HOST, PORT)
    plain = startup_urls(HOST, PORT)  # same hosts, token ke bina
    print("👉 Dashboard kholein (Ctrl+click / copy-paste):")
    for i, u in enumerate(urls):
        print(f"   {'🖥 ' if i == 0 else '📱'} {u}")
    if SECURITY['TOKEN']:
        print("   ℹ️  Cookie set hone ke baad ye saaf link chalegi (24 ghante):")
        for u in plain:
            print(f"      {u.replace('?token=' + SECURITY['TOKEN'], '')}")
        print("   🔒 Console log me token mask hota hai (token=***)")
    print("=" * 78)
    if urls and auto_open_enabled():
        try:
            webbrowser.open(urls[0])   # STOCKAI_AUTO_OPEN=0 se band hota hai
        except Exception:
            pass
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
