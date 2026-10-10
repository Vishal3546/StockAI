"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  StockAI V6.0 — Full Institutional Multi-Tech Hybrid Mastermind Engine       ║
║                                                                              ║
║  ARCHITECTURE & DATA PIPELINE LAYERS:                                        ║
║  • Tier 1 Primary Engine: TradingView Direct Stream (tvDatafeed - latency unverified)  ║
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
import uuid
from safety import frame_digest, validate_body, checked_symbol
from store_lock import ProcessRLock
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
import own_history as OWN_HISTORY
import validation_metrics as VALIDATION
import eod_validation as EOD_VALIDATION
import html as html_entities

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory
from flask.json.provider import DefaultJSONProvider
import numpy as np
import pandas as pd
import requests as http_requests

# Suppress all non-critical runtime warnings
warnings.filterwarnings('ignore')

# Initialize Flask Web Application
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 256 * 1024

@app.before_request
def _validate_write_body():
    g.request_started = time.perf_counter()
    if request.path.startswith('/api/'):
        ex = str(request.args.get('ex') or request.args.get('exch') or 'NSE').strip().upper()
        if ex not in ('NSE', 'BSE'):
            return jsonify({'error': 'exchange must be NSE or BSE'}), 400
        try:
            if (request.view_args or {}).get('symbol'):
                checked_symbol(request.view_args['symbol'], ex)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
    if request.path.startswith('/api/') and request.method in ('POST', 'PUT', 'PATCH'):
        body = request.get_json(silent=True)
        error = ('JSON object required; malformed/null JSON rejected' if request.content_length and body is None else validate_body(body))
        if error:
            return jsonify({'ok': False, 'error': error}), 400



# ── FIX-98: NaN / Infinity JSON me leak na ho ──────────────────────────────
# Flask ka default encoder `NaN` / `Infinity` LITERAL emit karta hai — measure
# kiya: app.json.dumps({'x': float('nan')}) → '{"x": NaN}'. Wo JSON spec me hai
# hi nahi, isliye browser ka JSON.parse throw karta hai aur poora panel chup-chaap
# khaali reh jaata hai. Koi bhi 0-division / 0-variance path ye kar sakta hai,
# isliye guard app-level hai:
#   • fast path: allow_nan=False — koi NaN nahi to output bilkul pehle jaisa
#   • NaN/Inf mile to wo None ban jaata hai (jhootha 0.0 nahi — "measure nahi hua")
def _strip_nonfinite(obj):
    if isinstance(obj, dict):
        return {k: _strip_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_strip_nonfinite(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


class _SafeJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        kwargs['allow_nan'] = False
        try:
            return super().dumps(obj, **kwargs)
        except ValueError:
            return super().dumps(_strip_nonfinite(obj), **kwargs)


app.json = _SafeJSONProvider(app)

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
    'SEARCH_MAX_RESULTS': 20,   # FIX-85: dual-listing mirror ke baad 15 chhota tha
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
# FIX-88: SSE har 3s poll karta hai (SSE_STREAM_INTERVAL) par TTL 2s tha — yaani
# cache KABHI hit nahi hota tha. Market band ho to har 3 second me poora
# 3-tier × 2-exchange smart_fetch chalta tha (user ke log me wahi line 20-30 baar).
# Market band ho to daily close badal nahi sakta, isliye lambi TTL.
LIVE_TTL_CLOSED = 900.0             # 15 min — market band, close immutable
_LIVE_CACHE = {}                    # symbol:ex -> (ts, payload, ttl)

# ── FIX-99: bounded caches ─────────────────────────────────────────────────
# Ye sab caches symbol/exchange se key hote hain aur inme se KISI me bhi eviction
# nahi tha (grep: koi .pop/.clear/len-check nahi). TTL sirf stale entry ko
# *skip* karta hai, hatata nahi — matlab LAN par jitne alag stocks khule, utne
# entries hamesha ke liye memory me. Ek ek entry me poora KPI payload + plan
# hota hai, isliye server hafton chalne par ye dheere-dheere badhta rehta.
CACHE_MAX_ENTRIES = 512             # per-cache cap; oldest write is evicted


_CACHE_WRITE_LOCK = threading.RLock()

def _cache_put(cache, key, value, max_entries=CACHE_MAX_ENTRIES):
    """Bounded last-write-order put (not read-recency LRU). Callers synchronize writes."""
    if max_entries < 1:
        raise ValueError('positive cache bound required')
    with _CACHE_WRITE_LOCK:
        cache.pop(key, None)
        cache[key] = value
        while len(cache) > max_entries:
            cache.pop(next(iter(cache)), None)
    return value

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


# ══════════════════════════════════════════════════════════════════════════
# FIX-62: source string se exchange nikalna — har provider ka format alag hai
#
# BUG (user ne LGEINDIA par dekha): frame_exchange sirf TradingView ke format
# '(NSE)' / '(BSE)' dekhta tha. Par Yahoo path (L640) source
# `f'yahoo{suffix.lower()}'` banata hai -> 'yahoo.ns' / 'yahoo.bo' — parentheses
# NAHI. Natija: har us stock par "exchange unknown" jismein analysis frame
# Yahoo se aaya tha. LGEINDIA NSE master me maujood hai ("LG Electronics India
# Limited") — fir bhi unknown dikh raha tha.
#
# Har format ko ek jagah handle karo, aur jo pehchana na ja sake uske liye
# JHOOTH exchange mat banao — None do (UI "exchange unknown" dikhayega, jo
# imaandaar hai).
# ══════════════════════════════════════════════════════════════════════════
_SOURCE_EXCHANGE_PATTERNS = (
    ('(BSE)', 'BSE'), ('(NSE)', 'NSE'),      # TradingView display label
    ('BSE:', 'BSE'), ('NSE:', 'NSE'),        # TradingView symbol prefix
    ('.BO', 'BSE'), ('.NS', 'NSE'),          # yfinance suffix (yahoo.bo / yahoo.ns)
    ('-BO', 'BSE'), ('-NS', 'NSE'),          # kuch feeds dash use karti hain
)

# FIX-62b: upar wale patterns 'NSE Direct' / 'BSE Direct' jaise labels par FAIL
# hote hain — usme na parentheses hain, na ':', na '.', na '-'. Aur wahi user ke
# screenshot wala case tha: source 'NSE Direct', par "exchange unknown".
# Isliye aakhir me word-boundary token match — 'NSE'/'BSE' ek ALAG word ke roop
# me aaye tabhi. Substring match jaan-boojh kar nahi kiya: 'consuNSEr' jaisi
# cheez par galat exchange ban jaata.
_BARE_EXCHANGE_RE = __import__('re').compile(r'\b(NSE|BSE)\b')


def exchange_from_source(src) -> str | None:
    """Source/label string se exchange nikalo. Na pehchan sake to None.

    >>> exchange_from_source('yahoo.ns')
    'NSE'
    >>> exchange_from_source('yahoo.bo')
    'BSE'
    >>> exchange_from_source('TCS (NSE)')
    'NSE'
    >>> exchange_from_source('NSE:TCS')
    'NSE'
    >>> exchange_from_source('TCS.NS')
    'NSE'
    >>> exchange_from_source('NSE Direct')    # FIX-62b — user ka actual case
    'NSE'
    >>> exchange_from_source('kuch aur')      # jhooth nahi banayenge
    """
    u = str(src or '').upper()
    if not u:
        return None
    for needle, exch in _SOURCE_EXCHANGE_PATTERNS:
        if needle in u:
            return exch
    m = _BARE_EXCHANGE_RE.search(u)
    return m.group(1) if m else None


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
    _sfx = ('.BO',) if str(prefer_exch).upper() == 'BSE' else ('.NS',)
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
        # FIX-88: TTL cache me store hoti hai (market open/band ke hisaab se).
        # Purani 2-tuple entries bhi tolerate karo — warna upgrade par crash.
        if hit:
            _ttl = hit[2] if len(hit) > 2 else LIVE_TTL
            if (now - hit[0]) < _ttl:
                payload = dict(hit[1])
                if (payload.get('symbol') == clean_sym
                        and exchange_from_source(payload.get('source')) == str(prefer_exch).upper()):
                    payload['exchange'] = str(prefer_exch).upper()
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
            df, src = DATA_MANAGER.smart_fetch(clean_sym, period='1mo', interval='1d',
                                               prefer_exch=prefer_exch, strict_exch=True)
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
    actual_ex = exchange_from_source(quote.get('source'))
    if actual_ex != str(prefer_exch).upper():
        return None
    if quote.get('symbol') and str(quote['symbol']).upper() != clean_sym:
        return None
    quote['symbol'] = clean_sym
    quote['exchange'] = actual_ex
    quote.setdefault('change', None)
    quote.setdefault('pChange', None)
    quote.setdefault('close_price', None)
    quote.setdefault('dayHigh', None)
    quote.setdefault('dayLow', None)
    quote.setdefault('is_realtime', False)
    quote.setdefault('source', 'unknown')

    # FIX-88: market band ho to close immutable hai — lambi TTL. Warna 2s.
    # Isse SSE ka 3s poll cache hit karta hai aur upstream par load nahi padta.
    try:
        _ttl = LIVE_TTL if is_market_open(exchange=('BSE' if _bse else 'NSE')) else LIVE_TTL_CLOSED
    except Exception:                                        # noqa: BLE001
        _ttl = LIVE_TTL
    with _LIVE_LOCK:
        _cache_put(_LIVE_CACHE, _ckey, (now, dict(quote), _ttl))   # FIX-99
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


def last_completed_session(now=None, exchange='NSE'):
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
    if not (today_traded and hm >= (BSE_SESSION_CLOSE_HM if exchange == 'BSE' else SESSION_CLOSE_HM)):
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


def frame_is_fresh(df, interval='1d', now=None, exchange='NSE'):
    """Session completeness is distinct from minute-level quote freshness.

    Conservative cash-session envelope; unknown/future timestamps fail closed.
    Calendar is maintained in NSE_HOLIDAYS plus configured extra holidays.
    """
    ref = _naive_ist(now)
    bar = frame_last_date(df)
    age = frame_age_minutes(df, now)
    if bar is None or age is None or bar > ref.date():
        return False, 'STALE: timestamp missing/invalid/future'
    expected = last_completed_session(now, exchange=exchange)
    if ref.year != 2026:
        return False, 'STALE: trading calendar must be reviewed for this year'
    if interval == '1w':
        fresh = bar >= expected - timedelta(days=7)
    else:
        fresh = bar >= expected
    if not fresh:
        return False, f'STALE: last session {bar}; latest completed {expected}'
    if interval in ('1d', '1w'):
        return True, f'session {bar}; latest completed {expected}'
    if not is_market_open(now, exchange=exchange):
        return True, f'market closed; session {bar}'
    limit = MAX_AGE_MIN.get(interval, 150)
    return age <= limit, f'{"fresh" if age <= limit else "STALE"}: last bar {age:.1f}m; limit {limit}m'


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
    • Tier 1 (Primary): TradingView Direct Stream (tvDatafeed - latency unverified Live Data)
    • Tier 2 (Official Backup): NSE Official Direct (Native Session Scraper + jugaad-data / nsepython)
    • Tier 3 (Universal Backup): Yahoo Finance Universal (yfinance Global Stream)
    """
    
    @property
    def exch_fallback(self):
        return getattr(self._fetch_state, 'fallback', None)

    @exch_fallback.setter
    def exch_fallback(self, value):
        self._fetch_state.fallback = value

    @exch_fallback.deleter
    def exch_fallback(self):
        self._fetch_state.fallback = None

    def __init__(self):
        self._fetch_state = threading.local()
        self._tv_lock = threading.RLock()
        self.tv = None
        self.exch_fallback = None   # FIX-83: (src, exch) — strict mode me caller ke liye
        self.init_tv()

    def init_tv(self):
        """Initializes TradingView Datafeed Connection"""
        try:
            from tv_history import HistoryDatafeed as TvDatafeed
            self.tv = TvDatafeed()
            print("🟢 [TradingView FIX-102 chart-only client initialized; timestamps normalized to IST] Provider availability is checked on fetch; live quote access is not verified.")
        except Exception as e:
            self.tv = None
            print(f"⚠️ [TradingView Notice] Could not initialize tvDatafeed: {e}")

    def fetch_tradingview(self, symbol, n_bars=500, interval_str='1d', prefer_exch='NSE'):
        """Tier 1 Fetch: Direct TradingView Feed"""
        if self.tv is None:
            return None, None

        try:
            from tvDatafeed import Interval
            clean_sym = checked_symbol(symbol, prefer_exch)
            
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
            # FIX-102: query only the explicitly requested exchange.
            _order = (str(prefer_exch).strip().upper(),)
            for exch in _order:
                with self._tv_lock:
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

    def fetch_yahoo(self, symbol, period='2y', interval='1d', prefer_exch='NSE'):
        """Tier 3 Fetch: Yahoo Finance Universal Backup.

        FIX-102: returns (frame, actual exchange), querying only the requested
        suffix. No opposite-exchange network probe or substitution.
        """
        try:
            import yfinance as yf
            clean = checked_symbol(symbol, prefer_exch)
            if symbol.startswith('^'):
                actual = {'^NSEI':'NSE','^NSEBANK':'NSE','^INDIAVIX':'NSE','^BSESN':'BSE'}.get(symbol)
                if actual != str(prefer_exch).upper():
                    return None, None
                order = [(symbol, actual)]
            else:
                first = '.BO' if str(prefer_exch).upper() == 'BSE' else '.NS'
                order = [(f"{clean}{first}", 'BSE' if first == '.BO' else 'NSE')]

            for target, exch in order:
                df = yf.download(target, period=period, interval=interval,
                                 progress=False, threads=False, auto_adjust=False)
                if df is None or df.empty:
                    continue
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df.dropna(subset=['Close'])
                if len(df) >= 20:
                    return df, exch
        except Exception:
            pass

        return None, None

    def smart_fetch(self, symbol, period='2y', interval='1d', n_bars=500, _now=None,
                    prefer_exch='NSE', strict_exch=False):
        """
        Executes strict 3-tier cascade with real-time terminal logging.

        FIX-26: har tier ka data FRESHNESS-check hota hai (market khula hone par).
        Pehle tvDatafeed ka socket girne ke baad bhi "0s Delay Live Stream" print
        ho jaata tha; ab wahi tier reject hota hai aur agla try hota hai. Sab
        stale ho to sabse fresh ko clearly "STALE" label ke saath return karte hain.
        """
        clean_sym = checked_symbol(symbol, prefer_exch)
        prefer = str(prefer_exch).upper()
        # FIX-83: HAR call ke shuru me reset — warna pichhli call ka fallback
        # chipak rehta aur /api/stock galat "available_exchange" bata deta.
        # (Pehle ye fresh_fallback block me tha, par TradingView tier early-return
        #  kar deta hai to wo line chalti hi nahi thi — test A4 ne pakda.)
        self.exch_fallback = None
        stale_candidates = []          # (age_min, df, source)
        fresh_fallback = None          # (df, src) — fresh, par requested exchange nahi

        # FIX-68: requested exchange ko tier-order me prefer karo. Doosre exchange ka
        # fresh frame turant return NAHI hota — pehle requested exchange ke baaki
        # sources (NSE Direct / Yahoo .NS) try hote hain; sirf agar koi preferred
        # fresh na mile tabhi doosre-exchange fresh frame use hota hai (disclosed).
        def _consider(df, src, exch):
            nonlocal fresh_fallback
            from safety import valid_ohlcv
            if not valid_ohlcv(df):
                return 'reject', 'invalid OHLCV geometry/schema'
            df.attrs.update(source=src, exchange=exch)
            df.attrs.setdefault('adjustment', 'provider convention; corporate actions not independently reconciled')
            fresh, why = frame_is_fresh(df, interval, now=_now, exchange=exch)
            if fresh:
                if exch == prefer:
                    return 'return', why
                if fresh_fallback is None:
                    fresh_fallback = (df, src)
                print(f"⚠️  [{src}] fresh hai par {exch} se — aapne {prefer} maanga; "
                      f"pehle {prefer} ke baaki sources try honge")
                return 'hold', why
            if strict_exch and exch != prefer:
                return 'hold', 'wrong exchange rejected (including stale fallback)'
            a = frame_age_minutes(df, _now)
            stale_candidates.append((a if a is not None else 1e9, df, src))
            print(f"⚠️  [{src} REJECTED] {clean_sym} ({interval}) — {why}, agla tier try kar rahe hain")
            return 'stale', why

        # ── TIER 1: TradingView Direct (0-Second Delay Live Stream) ──
        if not symbol.startswith('^'):
            df_tv, tv_exch = self.fetch_tradingview(clean_sym, n_bars=n_bars,
                                                     interval_str=interval,
                                                     prefer_exch=prefer_exch)
            if df_tv is not None:
                # FIX-53: exchange label me — 'TradingView Direct' akela ye nahi
                # batata tha ki data NSE se aaya ya BSE fallback se.
                _tv_src = f'TradingView Direct ({tv_exch})' if tv_exch else 'TradingView Direct'
                act, why = _consider(df_tv, _tv_src, tv_exch or prefer)
                if act == 'return':
                    print(f"🔥 [{_tv_src}] {clean_sym} ({interval}) · {len(df_tv)} bars · {why}")
                    return df_tv, _tv_src

        # ── TIER 2: NSE Official Direct Scraper ──
        if interval == '1d' and not symbol.startswith('^') and prefer == 'NSE':
            df_nse = self.fetch_nse_direct(clean_sym, days=500)
            if df_nse is not None:
                act, why = _consider(df_nse, 'NSE Direct', 'NSE')
                if act == 'return':
                    print(f"⚡ [NSE Official Direct] {clean_sym} — Official Exchange Data · {len(df_nse)} bars · {why}")
                    return df_nse, 'NSE Direct'

        # ── TIER 3: Yahoo Finance Universal Backup ──
        df_yf, yf_exch = self.fetch_yahoo(symbol, period=period, interval=interval,
                                          prefer_exch=prefer_exch)
        if df_yf is not None:
            _yf_src = f'Yahoo Finance ({yf_exch})' if yf_exch else 'Yahoo Finance'
            act, why = _consider(df_yf, _yf_src, yf_exch or prefer)
            if act == 'return':
                print(f"🌐 [Yahoo] {symbol} ({interval}) · {len(df_yf)} bars · {why}")
                return df_yf, _yf_src

        # ── Last resort: sabse fresh stale frame (honest label ke saath) ──
        # FIX-102: available same-exchange stale data is consistently historical-only.
        # Vendor agreement cannot establish a holiday; retain the calendar expectation.
        if stale_candidates:
            stale_candidates.sort(key=lambda t: t[0])
            age, df, src = stale_candidates[0]
            bar_date = frame_last_date(df)
            expected = last_completed_session(_now, exchange=prefer)
            print(f"🟡 [HISTORICAL ONLY] {symbol} {interval} [{prefer}] — {src}; "
                  f"last session {bar_date}, expected {expected}. "
                  "Same-exchange historical display only; no missing candle invented.")
            return df, src + ' (STALE)'

        # FIX-68: requested exchange ka koi fresh source nahi mila — doosre exchange
        # ka held fresh frame use karo (frame_exchange se disclosed rahega).
        # FIX-83: strict_exch=True ho to cross-exchange fallback RETURN NAHI hota.
        # User ne BSE maanga aur sirf NSE mila — to NSE ka data BSE bana kar dikhana
        # galat hai (dono ke closing prices alag hote hain). Caller ko batao ki
        # requested exchange ka data nahi hai, par doosra available hai.
        if fresh_fallback is not None:
            df, src = fresh_fallback
            _fb_exch = exchange_from_source(src) or ('BSE' if prefer == 'NSE' else 'NSE')
            self.exch_fallback = (src, _fb_exch)
            if strict_exch:
                print(f"⛔ [{prefer}] {clean_sym} — koi source nahi mila. "
                      f"{_fb_exch} ka data available hai par strict mode me "
                      f"cross-exchange data nahi dete.")
                return None, None
            print(f"ℹ️  {prefer} ka koi fresh source nahi mila — fallback [{src}] use ho raha hai")
            return df, src

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
    # DYNAMIC_STOCK_DB is global list me zaroori NAHI tha — outer scope me wo
    # assign nahi hota, sirf inner _run() me hota hai (pyflakes ne pakda).
    global _master_refresh_thread
    if _master_refresh_thread is not None and _master_refresh_thread.is_alive():
        return

    def _run():
        # FIX-86: global assignment ab _set_master_db() karta hai (NSE + BSE merge)
        fresh = _fetch_nse_master(verbose=False)
        if fresh:
            n = _set_master_db(fresh)
            print(f"✅ Master list background refresh — {n} stocks (NSE + BSE)")

    _master_refresh_thread = threading.Thread(target=_run, name='nse-master-refresh', daemon=True)
    _master_refresh_thread.start()


def load_dynamic_nse_stocks(force=False, background=False):
    """FIX-39 (M-9): import par network call band.

    Order: fresh cache → (stale cache + background refresh) → sync fetch
    → purani cache → curated fallback. STOCKAI_OFFLINE=1 par network bilkul nahi.
    FIX-86: global assignment ab _set_master_db() karta hai — NSE list set hote hi
    bse_master.json ke BSE symbols merge ho jaate hain, isliye koi bhi code path
    (fresh fetch / stale cache / offline / fallback) BSE ko chhod nahi sakta.
    """
    ttl = float(CONFIG['NSE_MASTER_CACHE_HOURS'])
    offline = (os.environ.get('STOCKAI_OFFLINE') or '').strip().lower() in ('1', 'true', 'yes')

    if not force:
        hit = _master_cache_read()
        if hit:
            stocks, age_h = hit
            # FIX-86: return value merged count hona chahiye, warna caller ko
            # 600 dikhta hai jabki index me 600 NSE + BSE rows hain.
            n = _set_master_db(stocks)
            if age_h <= ttl:
                print(f"💾 NSE master list cache se — {len(stocks)} NSE + "
                      f"{n - len(stocks)} BSE = {n} stocks "
                      f"({age_h}h purani, TTL {ttl:g}h) — koi network call nahi")
                return n
            print(f"♻️  NSE master cache {age_h}h purani (TTL {ttl:g}h)"
                  + (" — STOCKAI_OFFLINE, refresh skip" if offline else " — background me refresh"))
            if not offline:
                _spawn_master_refresh()
            return n
        if offline:
            n = _set_master_db(_fallback_master_list())
            print(f"⚠️ STOCKAI_OFFLINE=1 aur cache nahi — curated fallback "
                  f"({n} stocks)")
            return n
        if background:
            n = _set_master_db(_fallback_master_list())
            print("🌐 NSE master list background me download ho rahi hai "
                  f"(filhaal curated fallback — {n} stocks)")
            _spawn_master_refresh()
            return n

    fresh = None if offline else _fetch_nse_master()
    if fresh:
        return _set_master_db(fresh)
    hit = _master_cache_read()
    if hit:
        n = _set_master_db(hit[0])
        print(f"⚠️ fetch fail — purani cache use ho rahi hai ({hit[1]}h, {n} stocks incl. BSE)")
    else:
        n = _set_master_db(_fallback_master_list())
        print(f"⚠️ fetch fail, cache nahi — curated fallback ({n} stocks)")
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
        # FIX-87: 'TATAMOTORS' -> 'TMPV'. Tata Motors 1 Oct 2025 ko demerge hua aur
        # NSE ticker TATAMOTORS ab exist nahi karta (Yahoo .NS/.BO dono 404). Ye
        # mapping score_calibration.py aur nifty_scanner.py me FIX-45 se thi, par
        # curated fallback list me stale entry reh gayi thi — matlab NSE fetch fail
        # hone par search ek dead ticker dikhata. TMPV = Tata Motors Passenger
        # Vehicles Ltd (24 Oct 2025 se listed, Nifty 50 me); TMCV = CV arm.
        {"sym": "TMPV", "name": "Tata Motors Passenger Vehicles Ltd", "ex": "NSE", "sec": "Auto"},
        {"sym": "TMCV", "name": "Tata Motors Ltd (Commercial Vehicles)", "ex": "NSE", "sec": "Auto"},
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


# ────────────────────────────────────────────────────────────────────────────
#  FIX-86: BSE master list — TradingView public scanner se bani hui artifact
# ────────────────────────────────────────────────────────────────────────────
# NSE ka EQUITY_L.csv sirf NSE symbols deta hai (2570, 100 % NSE), isliye BSE-only
# listings (TAPARIA, DHOOTIN, …) search index me the hi nahi. BSE ki apni files
# datacenter IP se nahi milti (07-Oct-2026 ko dobara measure kiya):
#   api.bseindia.com/Msource/*  -> HTTP 403
#   EQ_ISINCODE_*.CSV           -> HTTP 200 par 0 bytes
#   List_Scrips.html            -> HTTP 200, sirf Angular shell
# TradingView ka scanner API chalta hai aur BSE par 4770 stocks deta hai.
# Artifact banane ka tarika:  python tools/build_bse_master.py
BSE_MASTER_FILE = pathlib.Path(__file__).resolve().parent / 'bse_master.json'
_BSE_MASTER_ROWS = None
_MASTER_IDX_KEYS = None
_MASTER_IDX_SIG = None


def _load_bse_master(verbose=True):
    """bse_master.json -> list[dict] in DYNAMIC_STOCK_DB shape. Ek hi baar padhta hai."""
    global _BSE_MASTER_ROWS
    if _BSE_MASTER_ROWS is not None:
        return _BSE_MASTER_ROWS
    rows = []
    generated = '?'
    try:
        art = json.loads(BSE_MASTER_FILE.read_text(encoding='utf-8'))
        generated = art.get('generated_at') or '?'
        for r in (art.get('stocks') or []):
            sym = str(r.get('symbol') or '').strip().upper()
            if not sym:
                continue
            rows.append({'sym': sym,
                         'name': str(r.get('name') or sym).strip() or sym,
                         'ex': 'BSE',
                         'sec': None})
        if verbose:
            print(f"🏛  BSE master list — {len(rows)} stocks (generated {generated})"
                  f" · source: TradingView scanner · rebuild: python tools/build_bse_master.py")
    except FileNotFoundError:
        if verbose:
            print("⚠️  bse_master.json nahi mila — search me sirf NSE rahega. "
                  "Banane ke liye: python tools/build_bse_master.py")
    except Exception as e:                                       # noqa: BLE001
        if verbose:
            print(f"⚠️  bse_master.json padh nahi paya ({type(e).__name__}) — sirf NSE rahega")
    _BSE_MASTER_ROWS = rows
    return rows


def _master_index_keys():
    """(SYM, EX) ka set — mirror ko verify karne ke liye. DYNAMIC_STOCK_DB badle to rebuild."""
    global _MASTER_IDX_KEYS, _MASTER_IDX_SIG
    sig = len(DYNAMIC_STOCK_DB)
    if _MASTER_IDX_KEYS is not None and _MASTER_IDX_SIG == sig:
        return _MASTER_IDX_KEYS
    _MASTER_IDX_KEYS = {(str(x.get('sym') or '').strip().upper(),
                         (x.get('ex') or 'NSE').strip().upper())
                        for x in DYNAMIC_STOCK_DB}
    _MASTER_IDX_SIG = sig
    return _MASTER_IDX_KEYS


def _set_master_db(stocks):
    """FIX-86: NSE list set karo + BSE master merge karo. Har assignment isi se hota hai.

    Dedupe key (sym, ex) hai — isliye dual-listed stock dono exchange par aata hai,
    aur BSE-only stock bhi index me aa jata hai. Koi bhi code path BSE ko chhod
    nahi sakta, kyunki global par direct assignment ab kahin nahi hai.
    """
    global DYNAMIC_STOCK_DB
    merged = list(stocks or [])
    seen = set()
    for s_ in merged:
        seen.add((str(s_.get('sym') or '').strip().upper(),
                  (s_.get('ex') or 'NSE').strip().upper()))
    for b in _load_bse_master(verbose=False):
        key = (b['sym'], 'BSE')
        if key not in seen:
            merged.append(b)
            seen.add(key)
    DYNAMIC_STOCK_DB = merged
    global _MASTER_IDX_KEYS
    _MASTER_IDX_KEYS = None          # index badla — mirror ka cache reset
    return len(merged)


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
    # FIX-85: pehle ye 12 results bhar kar `break` kar deta tha, aur Layer 2 sirf
    # `len(results) < 5` par chalta tha. Matlab jis query par 5+ NSE naam mil jaate
    # (TATA/BANK/STEEL — measured: 12 results, 100% NSE), wahan Yahoo search chalta
    # HI NAHI tha aur BSE ke stocks kabhi nahi dikhte the. DYNAMIC_STOCK_DB me
    # 2570 entries hain aur exchange breakdown {'NSE': 2570} — ek bhi BSE nahi,
    # kyunki source NSE ka EQUITY_L.csv hai. Isliye BSE coverage Layer 2 se hi
    # aa sakti hai; usse conditional mat karo.
    _L1_CAP = 8
    for s in DYNAMIC_STOCK_DB:
        sym_match = q == s['sym'].upper() or s['sym'].upper().startswith(q)
        name_match = q in s['name'].upper()
        if sym_match or name_match:
            results.append(s)
        if len(results) >= _L1_CAP:
            break

    # Layer 2: Yahoo Live Search — AB HAMESHA chalta hai (BSE coverage ke liye)
    if True:
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
    # FIX-85: pehle ex='NSE' hardcode tha jabki name me "(NSE/BSE)" likha tha —
    # yaani dropdown jhooth bolta tha. Aur Dashboard search result ke `ex` se
    # activeExchange set kar deta hai (line ~988), to BSE-only stock par click
    # karne se zabardasti NSE compute hota tha.
    if not results and len(q) >= 2:
        results.append({
            'sym': q,
            'name': f"{q} — exchange confirm nahi hua",
            'ex': None,          # None = unknown; Dashboard apna chuna hua use karega
            'sec': 'Equity'
        })

    # ── FIX-86: dual-listing mirror, ab INDEX-VERIFIED ──────────────────────
    # FIX-85 ka mirror blind tha: har result ka sibling exchange bina check kiye
    # add kar deta tha. Jab tak index me sirf NSE tha ye zaroori tha (BSE coverage
    # ka koi source nahi tha). Ab bse_master.json se 4770 BSE symbols index me hain,
    # to blind mirror JHOOTH bolne laga:
    #     TAPARIA -> [TAPARIA ex=NSE, TAPARIA ex=BSE]
    # jabki TAPARIA sirf BSE par listed hai (NSE master me hai hi nahi).
    # Ab sibling sirf tab add hota hai jab index me (sym, sibling) actually ho.
    _idx = _master_index_keys()
    _by_key = {(r['sym'].upper(), (r.get('ex') or '').upper()) for r in results}
    _merged = []
    for r in results:
        _merged.append(r)
        _cur = (r.get('ex') or '').upper()
        _sib = 'BSE' if _cur == 'NSE' else ('NSE' if _cur == 'BSE' else None)
        # sirf verified listing mirror karo; warna user ko aisa exchange dikhta
        # hai jahan stock listed hi nahi — aur click par 409/404 milta hai.
        if _sib and (r['sym'].upper(), _sib) not in _by_key \
                and (r['sym'].upper(), _sib) in _idx:
            _merged.append({'sym': r['sym'], 'name': r.get('name'),
                            'ex': _sib, 'sec': r.get('sec', 'Equity')})
            _by_key.add((r['sym'].upper(), _sib))

    # FIX-85b: same symbol ke NSE aur BSE entries ADJACENT karo.
    # Bug tha: Yahoo ne 'RELIANCE.BO' Layer 2 me diya tha, to (RELIANCE,'BSE')
    # pehle se _by_key me tha aur mirror ne duplicate add nahi kiya — natija
    # RELIANCE NSE position 5 par, RELIANCE BSE position 12 (sabse aakhir) par.
    # Stable sort se first-appearance order rehta hai, sirf siblings saath aate hain.
    _first = {}
    for _i, r in enumerate(results):
        _first.setdefault(r['sym'].upper(), _i)
    # NOTE: sort _MERGED par karna hai, `results` par nahi — warna mirror ki
    # banayi hui entries chupchap drop ho jaati hain (ye bug khud kar chuka hoon:
    # `results = _merged` assignment edit me ud gayi thi aur TATA par 20 -> 11
    # results, saari BSE entries gayab).
    results = sorted(_merged, key=lambda r: (_first[r['sym'].upper()],
                                             0 if (r.get('ex') or '').upper() == 'NSE' else 1))

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
def ml_engine(df, symbol=None, exchange=None):
    """
    FIX-06: cached wrapper. The 4-model ensemble + 19 walk-forward fits cost
    ~8.1s per call; results are cached on (last bar date, bars) so repeats are
    instant. Adds the walk-forward noise band to the payload.
    """
    try:
        key = (symbol, exchange, frame_digest(df), 'ml-fix100', json.dumps(CONFIG, sort_keys=True))
    except Exception:
        key = None
    hit = _ML_CACHE.get(key) if key else None
    if hit is not None:
        return hit

    res = _ml_engine_uncached(df)
    if isinstance(res, dict):
        res['wf_window_sigma'] = res.get('wf_fold_std_pp')
        wf, bl = res.get('walk_forward_accuracy'), res.get('walk_forward_baseline')
        # Edge was pooled from the SAME unrounded fold observations.
        from research.provenance import pipeline_fingerprint
        if df is not None and len(df):
            res['reproducibility'] = VALIDATION.reproducibility(df, CONFIG, pipeline_fingerprint())
    if key:
        with _ML_LOCK:
            _cache_put(_ML_CACHE, key, res)                        # FIX-99
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
        'probability': None,
        'confidence': 'N/A',
        'models': {},
        'top_features': [],
        'train_days': 0,
        'test_days': 0,
        'baseline_accuracy': None,
        'pos_rate': None,
        'best_edge': None,
        'walk_forward_accuracy': None
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

        d['target'] = (c.shift(-1) > c).astype(float).where(c.shift(-1).notna())

        feats = [
            'ret_1d', 'ret_3d', 'ret_5d', 'ret_10d', 'ret_20d', 'rsi', 'macd', 'macd_sig', 'macd_hist',
            'bb_pctb', 'bb_width', 'atr_pct', 'vol_ratio', 'vol_change', 'obv_slope', 'ema_cross',
            'price_50', 'adx', 'plus_di', 'minus_di', 'stoch_rsi', 'cci', 'willr', 'vol_20', 'vol_5',
            'vwap_dist', 'hl_range', 'close_pos'
        ]

        d[feats] = d[feats].replace([np.inf, -np.inf], np.nan).ffill()
        d_clean = d.dropna(subset=['target'] + feats)

        if len(d_clean) < CONFIG['ML_MIN_DAYS']:
            na_response['error'] = f'Need {CONFIG["ML_MIN_DAYS"]}+ clean bars, found {len(d_clean)}'
            return na_response

        # ── Expanding Window Walk-Forward Validation ──
        wf_results = []
        wf_folds = []
        wf_models = []
        test_window = 20
        for start in range(120, len(d_clean) - test_window + 1, test_window):
            train_sub = d_clean.iloc[:max(0, start - 1)]
            test_sub = d_clean.iloc[start:start + test_window]
            if len(test_sub) < 10:
                continue
            X_tr_wf = np.nan_to_num(train_sub[feats].values)
            y_tr_wf = train_sub['target'].values
            X_te_wf = np.nan_to_num(test_sub[feats].values)
            y_te_wf = test_sub['target'].values

            probs, wf_models = VALIDATION.ensemble_predict(X_tr_wf, y_tr_wf, X_te_wf, CONFIG)
            predicted = (probs >= 0.5).astype(int)
            wf_results.append(accuracy_score(y_te_wf, predicted))
            fold = VALIDATION.fold_measure(y_tr_wf, y_te_wf, predicted,
                first_session=test_sub.index[0], last_session=test_sub.index[-1])
            scores = np.round(probs * 100, 1)
            selected = (scores >= 55) | (scores <= 45)
            fold.update(policy_n=int(selected.sum()),
                        policy_correct=int(((scores[selected] >= 55).astype(int) == y_te_wf[selected]).sum()),
                        policy_baseline_correct=int((y_te_wf[selected] == fold['training_majority']).sum()))
            wf_folds.append(fold)

        # FIX-28: pehle '0.0' tha — UI par ye 'model 0% accurate' jaisa padha
        # jaata tha, jabki sach ye hai ki walk-forward chali hi nahi. Ab None
        # (dashboard ise 'UNKNOWN' dikhata hai).
        wf_summary = VALIDATION.summarize(wf_folds)
        wf_accuracy = wf_summary['accuracy']

        # ── Final Train/Test Split (80/20) ──
        train_n = int(len(d_clean) * 0.8)
        X_train = np.nan_to_num(d_clean[feats].iloc[:max(0, train_n - 1)].values)
        y_train = d_clean['target'].iloc[:max(0, train_n - 1)].values
        X_test = np.nan_to_num(d_clean[feats].iloc[train_n:].values)
        y_test = d_clean['target'].iloc[train_n:].values
        X_today = np.nan_to_num(d[feats].iloc[-1:].values)

        pos_rate = float(y_test.mean())
        baseline_acc = round(float((y_test == int(y_train.mean() >= 0.5)).mean()) * 100, 1)

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

        if wf_models and sorted(models) != sorted(wf_models):
            raise ValueError('walk-forward and final prediction model availability disagree')
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
            'walk_forward_baseline': wf_summary['baseline'],
            'walk_forward_edge': wf_summary['edge'],
            'wf_fold_std_pp': wf_summary['fold_std_pp'],
            'wf_n_oos': wf_summary['n_oos'],
            'wf_neutral_policy': {k: wf_summary.get(k) for k in ('policy_n','policy_coverage_pct','policy_accuracy','policy_baseline')},
            'wf_folds': wf_summary['folds'],
            'wf_models': wf_models,
            'wf_model_scope': 'Weighted ensemble, same configured models/weights; threshold 0.5',
            'wf_uncertainty_note': 'Observed sample SD across fold accuracies, NOT a confidence interval or independence-adjusted significance test.',
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
        occurrence = {'session': str(recent.index[i])[:10], 'age_bars': len(recent)-1-i,
                      'is_latest_bar': i == len(recent)-1, 'trend_context_confirmed': False}
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
            patterns.append({**occurrence, 'name': 'Doji', 'type': 'REVERSAL', 'direction': 'NEUTRAL', 'strength': 55, 'candles': 1})
            
        if b3 > 0 and l3 >= 2 * b3 and u3 < b3 * 0.5:
            patterns.append({**occurrence, 'name': 'Hammer', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 72, 'candles': 1})
            
        if b3 > 0 and u3 >= 2 * b3 and l3 < b3 * 0.5 and c3['Close'] > c3['Open']:
            patterns.append({**occurrence, 'name': 'Inverted Hammer', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 68, 'candles': 1})
            
        if b3 > 0 and u3 >= 2 * b3 and l3 < b3 * 0.5 and c3['Close'] < c3['Open']:
            patterns.append({**occurrence, 'name': 'Shooting Star', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 72, 'candles': 1})
            
        if b3 / t3 > 0.85:
            patterns.append({**occurrence, 'name': 'Marubozu', 'type': 'CONTINUATION', 'direction': 'BULLISH' if c3['Close'] > c3['Open'] else 'BEARISH', 'strength': 78, 'candles': 1})
            
        if b3 / t3 < 0.3 and u3 > b3 and l3 > b3:
            patterns.append({**occurrence, 'name': 'Spinning Top', 'type': 'INDECISION', 'direction': 'NEUTRAL', 'strength': 45, 'candles': 1})

        # Double Candle Patterns
        if c3['Close'] > c3['Open'] and c2['Close'] < c2['Open'] and c3['Open'] <= c2['Close'] and c3['Close'] >= c2['Open']:
            patterns.append({**occurrence, 'name': 'Bullish Engulfing', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 90, 'candles': 2})
            
        if c3['Close'] < c3['Open'] and c2['Close'] > c2['Open'] and c3['Open'] >= c2['Close'] and c3['Close'] <= c2['Open']:
            patterns.append({**occurrence, 'name': 'Bearish Engulfing', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 90, 'candles': 2})
            
        if c2['Close'] < c2['Open'] and c3['Close'] > c3['Open'] and c3['Open'] > c2['Close'] and c3['Close'] < c2['Open']:
            patterns.append({**occurrence, 'name': 'Bullish Harami', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 73, 'candles': 2})
            
        if c2['Close'] > c2['Open'] and c3['Close'] < c3['Open'] and c3['Open'] < c2['Close'] and c3['Close'] > c2['Open']:
            patterns.append({**occurrence, 'name': 'Bearish Harami', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 73, 'candles': 2})

        mid2 = (c2['Open'] + c2['Close']) / 2
        if c2['Close'] < c2['Open'] and c3['Close'] > c3['Open'] and c3['Open'] < c2['Low'] and c3['Close'] > mid2 and c3['Close'] < c2['Open']:
            patterns.append({**occurrence, 'name': 'Piercing Line', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 80, 'candles': 2})
            
        if c2['Close'] > c2['Open'] and c3['Close'] < c3['Open'] and c3['Open'] > c2['High'] and c3['Close'] < mid2 and c3['Close'] > c2['Open']:
            patterns.append({**occurrence, 'name': 'Dark Cloud Cover', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 80, 'candles': 2})

        if abs(c2['Low'] - c3['Low']) / (c2['Low'] + 1e-10) < 0.002 and c2['Close'] < c2['Open'] and c3['Close'] > c3['Open']:
            patterns.append({**occurrence, 'name': 'Tweezer Bottom', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 76, 'candles': 2})
            
        if abs(c2['High'] - c3['High']) / (c2['High'] + 1e-10) < 0.002 and c2['Close'] > c2['Open'] and c3['Close'] < c3['Open']:
            patterns.append({**occurrence, 'name': 'Tweezer Top', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 76, 'candles': 2})

        # Triple Candle Patterns
        if c1['Close'] < c1['Open'] and b2 < b1 * 0.3 and c3['Close'] > c3['Open'] and c3['Close'] > (c1['Open'] + c1['Close']) / 2:
            patterns.append({**occurrence, 'name': 'Morning Star', 'type': 'REVERSAL', 'direction': 'BULLISH', 'strength': 92, 'candles': 3})
            
        if c1['Close'] > c1['Open'] and b2 < b1 * 0.3 and c3['Close'] < c3['Open'] and c3['Close'] < (c1['Open'] + c1['Close']) / 2:
            patterns.append({**occurrence, 'name': 'Evening Star', 'type': 'REVERSAL', 'direction': 'BEARISH', 'strength': 92, 'candles': 3})
            
        if c1['Close'] > c1['Open'] and c2['Close'] > c2['Open'] and c3['Close'] > c3['Open'] and c2['Close'] > c1['Close'] and c3['Close'] > c2['Close']:
            patterns.append({**occurrence, 'name': 'Three White Soldiers', 'type': 'REVERSAL CANDIDATE', 'direction': 'BULLISH', 'strength': 88, 'candles': 3})
            
        if c1['Close'] < c1['Open'] and c2['Close'] < c2['Open'] and c3['Close'] < c3['Open'] and c2['Close'] < c1['Close'] and c3['Close'] < c2['Close']:
            patterns.append({**occurrence, 'name': 'Three Black Crows', 'type': 'REVERSAL CANDIDATE', 'direction': 'BEARISH', 'strength': 88, 'candles': 3})

    seen = set()
    unique = []
    for p in reversed(patterns):
        if (p['name'], p['session']) not in seen:
            seen.add((p['name'], p['session']))
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
            'action': 'BULLISH' if i_s >= 65 else 'BEARISH' if i_s <= 35 else 'MIXED',
            'interpretation': 'Uncalibrated heuristic from daily inputs; not an entry signal or fitted ensemble rank',
            'tradeable': False,
            'basis': f'{i_used}/{i_total} indicators measured'
        },
        'swing': {
            'score': s_s,
            'action': 'BULLISH' if s_s >= 65 else 'BEARISH' if s_s <= 35 else 'MIXED',
            'interpretation': 'Uncalibrated heuristic from daily inputs; not an entry signal or fitted ensemble rank',
            'tradeable': False,
            'basis': f'{s_used}/{s_total} indicators measured'
        },
        'longterm': {
            'score': lt_s,
            'action': 'BULLISH' if lt_s >= 65 else 'BEARISH' if lt_s <= 35 else 'MIXED',
            'interpretation': 'Uncalibrated heuristic from daily inputs; not an entry signal or fitted ensemble rank',
            'tradeable': False,
            'basis': f'{lt_used}/{lt_total} indicators measured'
        },
        'master': {
            'score': master,
            'action': 'STRONG BULLISH' if master >= 72 else 'STRONG BEARISH' if master <= 28 else 'NEUTRAL',
            'interpretation': 'Uncalibrated heuristic from daily inputs; not an entry signal or fitted ensemble rank',
            'tradeable': False,
            'basis': f'{i_used + s_used + lt_used}/{i_total + s_total + lt_total} indicators measured'
        }
    }


# ═══════════════════════════════════════════════════════════════════════════
#  FIX-93: TRADE PLAN — 2026 execution layer (stop / target / RR / MTF / squeeze)
# ═══════════════════════════════════════════════════════════════════════════
#
# calculate_kpi_scores() DIRECTION batata hai. Ye function batata hai agar trade
# lena hi ho to stop kahan, target kahan, aur trade layak hai ya nahi. Dono
# jaan-bujh kar alag rakhe gaye hain — 2026 practice bhi "signal" aur "trade
# management" ko alag treat karti hai, aur alag rakhne se purana score (jis par
# screener + FIX-89a backtest bane hain) chhedna nahi pada.
#
# Thresholds — Oct-2026 Indian sources (stoxra Mar-26, univest Apr-26,
# icfmindia Apr-26, onetradejournal Jul-26, gettogetherfinance Sep-26):
#   • ATR(14) se stop sizing            — 1.5 × ATR
#   • risk:reward >= 1:2                — stated core rule
#   • weekly chart MTF confirmation     — daily + weekly dono
#   • Bollinger squeeze                 — BB width apni history ke tightest 20% me
#   • ADV > 5 lakh shares/day           — liquidity screen
#   • 52W high se 15-40% neeche         — swing screening band
#   • 2-4 indicators, 10+ nahi          — depth over breadth
#
# HONESTY (ye zaroori hai): ye conventions LOSS CAP karte hain, win-rate NAHI
# badhate. FIX-89a ne 289 sessions / 8667 samples par measure kiya tha ki score
# ka 5-day predictive edge detectable hi nahi hai. Isliye yahan koi accuracy ya
# probability claim nahi — sirf position sizing aur exit discipline.
TP_ATR_STOP = 1.5
TP_ATR_T1, TP_ATR_T2, TP_ATR_T3 = 1.0, 2.0, 3.0
TP_RR_MIN = 2.0            # 2026: risk:reward >= 1:2
TP_SQUEEZE_PCT = 20.0      # BB width, tightest 20% of its own history
TP_ADV_MIN = 500000        # 5 lakh shares/day
TP_HI52_LOW, TP_HI52_HIGH = -40.0, -15.0    # 15-40% below 52W high
TP_GAP_FLAT_PCT = 0.2      # |gap| < 0.2% -> FLAT (rounding-level gap nahi ginete)
# FIX-97: 2026 stock-SELECTION filters (intraday checklists se — sources commit msg me)
TP_ATR_PCT_MIN = 1.5       # ATR(14) >= 1.5% of close, warna costs ke baad move bachta nahi
TP_GAP_SKIP_PCT = 1.5      # morning routine: 1.5% se bada gap = candidate skip
TP_BETA_MIN_OK = 0.8       # isse kam beta = market ke saath move hi nahi karta
TP_BETA_IDEAL = (1.2, 1.8)  # ideal intraday band
TP_BETA_MAX_OK = 2.0       # isse upar erratic moves, risk manage karna mushkil
TP_BETA_BARS = 60          # ~3 mahine ke daily returns se beta
TP_BETA_MIN = 30           # isse kam overlapping returns -> beta None (guess nahi)
TP_INDEX_TTL = 3600        # NIFTY frame cache, 1 ghanta
TP_HI52_BARS = 252         # ~52 trading weeks
TP_CONF_CHECKS = 8         # confluence kitne indicators se ginta hai
TP_MIN_WEEKLY = 50         # MTF ke liye kam se kam weekly bars
TP_MTF_FLAT_TOL = 0.001    # EMA20/EMA50 isse kam alag hon to FLAT, warna tie ko
                           # galti se DOWN keh dete (0.1% se kam gap = flat)
TP_MIN_BARS = 20           # isse kam par plan banega hi nahi


def _rnd(v, nd=2):
    """round() jo None passthrough kare aur junk par exception na fekna."""
    try:
        return None if v is None else round(float(v), nd)
    except Exception:
        return None


_INDEX_CACHE = {'time': 0.0, 'df': None, 'err': None, 'symbol': None}


_INDEX_LOCK = threading.RLock()

def _index_frame(symbol='NIFTY', ttl=TP_INDEX_TTL):
    with _INDEX_LOCK:
        return _index_frame_locked(symbol, ttl)

def _index_frame_locked(symbol='NIFTY', ttl=TP_INDEX_TTL):
    """(df, err) — benchmark ka daily frame, cached. Fail-soft: kabhi raise nahi.

    Beta ke liye chahiye. Har plan par network hit na ho isliye TTL cache; fail
    hone par bhi TTL tak wahi reason dobara dete hain (baar-baar retry nahi).
    """
    ttl = min(ttl, 15) if _INDEX_CACHE.get('err') else ttl
    if _INDEX_CACHE.get('symbol') == symbol and (time.time() - _INDEX_CACHE['time']) < ttl:
        if _INDEX_CACHE['df'] is not None:
            return _INDEX_CACHE['df'], None
        if _INDEX_CACHE['err']:
            return None, _INDEX_CACHE['err']
    _INDEX_CACHE['symbol'] = symbol
    try:
        df, _src = DATA_MANAGER.smart_fetch(symbol, period='1y', interval='1d',
                                            prefer_exch='NSE')
    except Exception as e:                                           # noqa: BLE001
        _INDEX_CACHE.update(time=time.time(), df=None,
                            err=f'index fetch fail: {type(e).__name__}')
        return None, _INDEX_CACHE['err']
    if df is None or getattr(df, 'empty', True) or 'Close' not in df.columns:
        _INDEX_CACHE.update(time=time.time(), df=None,
                            err='index frame khali hai ya Close column nahi')
        return None, _INDEX_CACHE['err']
    _INDEX_CACHE.update(time=time.time(), df=df, err=None)
    return df, None


def calculate_beta(stock_close, index_close, bars=TP_BETA_BARS, min_bars=TP_BETA_MIN):
    """(beta, corr, n) — daily returns par cov/var. Kam data -> (None, None, n).

    Do input shapes accept karta hai: DatetimeIndex wali pandas Series (asli
    frames — session date par align hote hain) ya equal-length sequences (tests).
    Beta 1.0 ka matlab "market jitna hi move", <0.8 sluggish, >2.0 erratic.
    """
    try:
        if isinstance(stock_close, pd.Series) and isinstance(index_close, pd.Series):
            a = stock_close.copy()
            b = index_close.copy()
            a.index = pd.to_datetime([str(x.date()) for x in pd.to_datetime(a.index)])
            b.index = pd.to_datetime([str(x.date()) for x in pd.to_datetime(b.index)])
            j = pd.concat([a.rename('s'), b.rename('i')], axis=1, join='inner').dropna()
            sp = pd.to_numeric(j['s'], errors='coerce')
            ip = pd.to_numeric(j['i'], errors='coerce')
        else:
            s_list = list(stock_close)
            i_list = list(index_close)
            n = min(len(s_list), len(i_list))
            j = pd.DataFrame({'s': s_list[-n:], 'i': i_list[-n:]})
            sp = pd.to_numeric(j['s'], errors='coerce')
            ip = pd.to_numeric(j['i'], errors='coerce')
        both = pd.concat([sp, ip], axis=1, keys=('s', 'i')).dropna()
        rets = pd.concat([both['s'].pct_change(), both['i'].pct_change()],
                         axis=1, keys=('s', 'i')).dropna()
        if len(rets) > bars:
            rets = rets.iloc[-bars:]
        n = len(rets)
        if n < min_bars:
            return None, None, n
        var = float(rets['i'].var(ddof=1))
        if not var or var <= 0:
            return None, None, n
        cov = float(rets['s'].cov(rets['i'], ddof=1))
        sd_s = float(rets['s'].std(ddof=1))
        sd_i = float(rets['i'].std(ddof=1))
        corr = (cov / (sd_s * sd_i)) if (sd_s > 0 and sd_i > 0) else None
        return round(cov / var, 2), (round(corr, 2) if corr is not None else None), n
    except Exception:                                                # noqa: BLE001
        return None, None, 0


def calculate_trade_plan(dfi, index_frame=None):
    """(dict) Execution layer. Missing input → key absent + note, kabhi guess nahi.

    Har number ke saath uska RULE bhi jaata hai, taaki user dekh sake ki ye
    kahan se aaya — koi magic number nahi.
    """
    out = {'ok': False, 'notes': [],
           'disclosure': ('Ye levels ATR se bane RISK-MANAGEMENT conventions '
                          'hain — loss cap karte hain, jeetne ki sambhavna '
                          'NAHI badhate. Koi accuracy ya probability claim '
                          'nahi. Score ek relative rank hai (FIX-89a me '
                          'measure hua: 5-day par predictive edge detectable '
                          'nahi tha).')}
    if dfi is None or len(dfi) < TP_MIN_BARS:
        out['notes'].append(f'{TP_MIN_BARS} se kam bars — plan banane layak '
                            f'data nahi ({0 if dfi is None else len(dfi)} hain)')
        return out

    L = dfi.iloc[-1]
    px = sfx(L.get('Close'))
    atr = sfx(L.get('ATR'))
    bbw = sfx(L.get('BB_Width'))
    pctb = sfx(L.get('BB_PctB'))
    if px is None:
        out['notes'].append('Close missing — plan skip')
        return out
    out['price'] = _rnd(px)

    # ── 1. ATR(14) se stop + targets + risk:reward ──────────────────────────
    if atr is not None and atr > 0:
        risk = atr * TP_ATR_STOP
        out['atr'] = _rnd(atr)
        out['atr_pct'] = _rnd(atr / px * 100.0)
        out['stop_rule'] = f'{TP_ATR_STOP} × ATR(14)'
        out['risk_per_share'] = _rnd(risk)
        out['rr_min'] = TP_RR_MIN
        out['long'] = {'stop': _rnd(px - risk),
                       't1': _rnd(px + atr * TP_ATR_T1),
                       't2': _rnd(px + atr * TP_ATR_T2),
                       't3': _rnd(px + atr * TP_ATR_T3),
                       'rr_t1': _rnd(TP_ATR_T1 / TP_ATR_STOP),
                       'rr_t2': _rnd(TP_ATR_T2 / TP_ATR_STOP),
                       'rr_t3': _rnd(TP_ATR_T3 / TP_ATR_STOP),
                       'meets_1_2': bool((TP_ATR_T3 / TP_ATR_STOP) >= TP_RR_MIN)}
        out['short'] = {'stop': _rnd(px + risk),
                        't1': _rnd(px - atr * TP_ATR_T1),
                        't2': _rnd(px - atr * TP_ATR_T2),
                        't3': _rnd(px - atr * TP_ATR_T3)}
        out['rr_note'] = (f'1:{TP_RR_MIN:.0f} tabhi milta hai jab T3 '
                          f'({TP_ATR_T3}×ATR) tak hold kiya jaye — T1 par RR '
                          f'sirf 1:{_rnd(TP_ATR_T1 / TP_ATR_STOP)} hai.')
    else:
        out['notes'].append('ATR(14) missing ya 0 — stop/target nahi banaye')

    # ── 2. Bollinger squeeze ────────────────────────────────────────────────
    if bbw is not None and 'BB_Width' in dfi:
        hist = dfi['BB_Width'].dropna().tail(TP_HI52_BARS)
        if len(hist) >= 60:
            rank = float((hist < bbw).sum()) / len(hist) * 100.0
            out['bb'] = {'width': _rnd(bbw), 'width_rank_pct': _rnd(rank, 1),
                         'pctb': _rnd(pctb), 'n_bars': int(len(hist)),
                         'squeeze': bool(rank <= TP_SQUEEZE_PCT),
                         'rule': (f'width apni {len(hist)}-bar history ke '
                                  f'tightest {TP_SQUEEZE_PCT:.0f}% me')}
            if rank <= TP_SQUEEZE_PCT:
                out['notes'].append('Bollinger SQUEEZE — breakout ka setup, par '
                                    'direction squeeze se pata nahi chalta')
        else:
            out['notes'].append(f'BB history sirf {len(hist)} bars — squeeze '
                                'percentile reliable nahi (60+ chahiye)')
    else:
        out['notes'].append('BB_Width missing — squeeze check skip')

    # ── 3. 52-week high se doori (swing screening band) ─────────────────────
    if 'High' in dfi:
        hi = sfx(dfi['High'].tail(TP_HI52_BARS).max())
        if hi and hi > 0:
            d = (px / hi - 1.0) * 100.0
            out['hi52'] = {'value': _rnd(hi), 'dist_pct': _rnd(d),
                           'in_swing_band': bool(TP_HI52_LOW <= d <= TP_HI52_HIGH),
                           'rule': (f'{abs(TP_HI52_HIGH):.0f}-'
                                    f'{abs(TP_HI52_LOW):.0f}% below 52W high')}
        else:
            out['notes'].append('52W high compute nahi hua')
    else:
        out['notes'].append('High column missing — 52W high skip')

    # ── 3b. Prev session H/L/C + gap (intraday ke key S/R — 2026 checklist #2/3)
    # 2026 sources (isfm Sep-26, reentrynow Jul-26, scribd institutional checklist)
    # sab "previous day high/low mark karo" aur "gap up/down note karo" kehte hain.
    # Ye pure daily-frame data hai — koi external key nahi, isliye guess-free.
    if len(dfi) >= 2:
        P = dfi.iloc[-2]
        pc, ph, pl = sfx(P.get('Close')), sfx(P.get('High')), sfx(P.get('Low'))
        if pc is not None and pc > 0:
            op = sfx(L.get('Open'))
            gap = (op / pc - 1.0) * 100.0 if op is not None and op > 0 else None
            out['prevday'] = {'close': _rnd(pc), 'high': _rnd(ph), 'low': _rnd(pl),
                              'gap_pct': _rnd(gap),
                              'gap': (None if gap is None else 'UP' if gap > TP_GAP_FLAT_PCT else
                                      'DOWN' if gap < -TP_GAP_FLAT_PCT else 'FLAT'),
                              'session': str(dfi.index[-1])[:10],
                              'previous_session': str(dfi.index[-2])[:10],
                              'basis': 'Open / previous Close - 1',
                              'above_prev_high': bool(ph is not None and px > ph),
                              'below_prev_low': bool(pl is not None and px < pl),
                              'rule': 'prev session H/L/C — intraday key S/R'}
        else:
            out['notes'].append('prev close missing — gap nahi nikala')
    else:
        out['notes'].append('sirf 1 bar — prev session levels skip')

    # ── 4. Weekly MTF confirmation ──────────────────────────────────────────
    try:
        w = dfi.resample('W-FRI').agg({'Close': 'last'}).dropna()
        if len(w) >= TP_MIN_WEEKLY:
            wc = w['Close']
            e20 = float(wc.ewm(span=20, adjust=False).mean().iloc[-1])
            e50 = float(wc.ewm(span=50, adjust=False).mean().iloc[-1])
            # Tie ko DOWN na kahein — flat market ko bearish dikhana jhooth hai.
            if e50 and abs(e20 - e50) <= abs(e50) * TP_MTF_FLAT_TOL:
                trend = 'FLAT'
            else:
                trend = 'UP' if e20 > e50 else 'DOWN'
            out['mtf'] = {'weekly_ema20': _rnd(e20), 'weekly_ema50': _rnd(e50),
                          'weekly_trend': trend,
                          'weekly_bars': int(len(w)),
                          'flat_tol_pct': _rnd(TP_MTF_FLAT_TOL * 100.0, 2),
                          'rule': (f'weekly EMA20 vs EMA50 '
                                   f'(±{TP_MTF_FLAT_TOL * 100:.1f}% = FLAT)')}
        else:
            out['notes'].append(f'weekly bars sirf {len(w)} — MTF ke liye kam '
                                f'se kam {TP_MIN_WEEKLY} chahiye')
    except Exception as e:
        out['notes'].append(f'weekly resample fail: {type(e).__name__}')

    # ── 5. Confluence count ─────────────────────────────────────────────────
    votes = []
    vwap = sfx(L.get('VWAP'))
    e9, e21 = sfx(L.get('EMA_9')), sfx(L.get('EMA_21'))
    sma50, sma200 = sfx(L.get('SMA_50')), sfx(L.get('SMA_200'))
    macd, msig = sfx(L.get('MACD')), sfx(L.get('MACD_Signal'))
    rsi = sfx(L.get('RSI'))
    std = six(L.get('ST_Direction'))
    obv, oe = sfx(L.get('OBV')), sfx(L.get('OBV_EMA'))
    if vwap is not None:
        votes.append(px > vwap)
    if e9 is not None and e21 is not None:
        votes.append(e9 > e21)
    if e21 is not None and sma50 is not None:
        votes.append(e21 > sma50)
    if sma200 is not None:
        votes.append(px > sma200)
    if macd is not None and msig is not None:
        votes.append(macd > msig)
    if rsi is not None:
        votes.append(rsi > 50)
    if std is not None:
        votes.append(std == 1)
    if obv is not None and oe is not None:
        votes.append(obv > oe)
    if votes:
        bull = sum(1 for b in votes if b)
        bear = len(votes) - bull
        top = max(bull, bear)
        pct = top / len(votes) * 100.0
        out['confluence'] = {'agree': int(top), 'measured': int(len(votes)),
                             'pct': _rnd(pct, 1), 'bull': int(bull),
                             'bear': int(bear),
                             'side': 'BULLISH' if bull >= bear else 'BEARISH',
                             'label': ('STRONG' if pct >= 75.0 else
                                       'MODERATE' if pct >= 62.5 else 'WEAK')}
    else:
        out['notes'].append('confluence ke liye koi indicator available nahi')

    # ── 6. Liquidity (ADV > 5 lakh) ─────────────────────────────────────────
    if 'Volume' in dfi:
        adv = sfx(dfi['Volume'].tail(20).mean())
        if adv is not None:
            out['adv'] = {'value': _rnd(adv, 0), 'lakh': _rnd(adv / 100000.0, 2),
                          'meets_5lakh': bool(adv >= TP_ADV_MIN),
                          'rule': f'20-day ADV vs {TP_ADV_MIN // 100000} lakh'}
        else:
            out['notes'].append('ADV compute nahi hua')
    else:
        out['notes'].append('Volume missing — ADV skip')

    # ── FIX-97: 2026 stock-SELECTION filters (beta / ATR% / gap) ───────────
    # Plan batata tha stop/target kahan hai; ye batata hai ki stock intraday ke
    # LIYE layak hai ya nahi. Sab kuch existing data se — jo measure na ho paye
    # wo None + reason ke saath jaata hai (0 ya 1.0 guess nahi karte).
    flt = {'rules': {'beta_min': TP_BETA_MIN_OK, 'beta_ideal': list(TP_BETA_IDEAL),
                     'beta_max': TP_BETA_MAX_OK, 'atr_pct_min': TP_ATR_PCT_MIN,
                     'gap_skip_pct': TP_GAP_SKIP_PCT, 'beta_bars': TP_BETA_BARS,
                     'beta_min_bars': TP_BETA_MIN}}
    _ierr = None
    if index_frame is None:
        index_frame, _ierr = _index_frame()
    beta = corr = None
    beta_n = 0
    if index_frame is not None and 'Close' in getattr(index_frame, 'columns', []):
        beta, corr, beta_n = calculate_beta(dfi['Close'], index_frame['Close'])
    flt['beta'] = beta
    flt['beta_corr'] = corr
    flt['beta_n'] = beta_n
    if beta is None:
        flt['beta_ok'] = None
        flt['beta_note'] = (_ierr or (f'beta ke liye {TP_BETA_MIN} overlapping daily '
                                      f'returns chahiye — mile {beta_n}'))
    else:
        flt['beta_ok'] = bool(TP_BETA_MIN_OK <= beta <= TP_BETA_MAX_OK)
        flt['beta_ideal_ok'] = bool(TP_BETA_IDEAL[0] <= beta <= TP_BETA_IDEAL[1])
        flt['beta_note'] = (f'beta ≥ {TP_BETA_MIN_OK} — market ke saath move karta hai'
                            if flt['beta_ok'] else
                            f'beta < {TP_BETA_MIN_OK} — market ke saath move nahi karta, '
                            'intraday ke liye sluggish')
        if beta > TP_BETA_MAX_OK:
            flt['beta_note'] += (f' | > {TP_BETA_MAX_OK} = erratic moves, '
                                 'risk manage karna mushkil')
        if flt['beta_ideal_ok']:
            flt['beta_note'] += f' | ideal band {TP_BETA_IDEAL[0]}–{TP_BETA_IDEAL[1]} ke andar'
    _ap = out.get('atr_pct')
    flt['atr_pct'] = _ap
    flt['atr_pct_ok'] = None if _ap is None else bool(_ap >= TP_ATR_PCT_MIN)
    _gp = (out.get('prevday') or {}).get('gap_pct')
    flt['gap_pct'] = _gp
    flt['gap_skip'] = None if _gp is None else bool(abs(_gp) > TP_GAP_SKIP_PCT)
    out['filters'] = flt

    out['ok'] = True
    return out


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
        if not np.isfinite(c.values).all() or (c <= 0).any():
            return False, 'nonfinite/nonpositive Close'
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
            mask = (prices >= pbins[i]) & ((prices <= pbins[i+1]) if i == bins-1 else (prices < pbins[i+1]))
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
        # FIX-64: pehle period='1mo' tha — Yahoo ^INDIAVIX par ~19 daily bars deta
        # hai, aur fetch_yahoo ka `len(df) >= 20` minimum use None bana deta tha
        # ("All 3 engines failed for ^INDIAVIX" → VIX: 0 UNKNOWN). VIX ko sirf last
        # value chahiye, isliye lamba window harmless hai aur threshold cross karta hai.
        vd, _ = DATA_MANAGER.smart_fetch('^INDIAVIX', period='6mo')

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
                                              prefer_exch=prefer_exch, strict_exch=True)
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
                                              prefer_exch=prefer_exch, strict_exch=True)
        results['1h'] = _calc_tf(data1h)

        # 1d timeframe
        if daily_df is not None and len(daily_df) >= 25:
            results['1d'] = _calc_tf(daily_df)
        else:
            data1d, _ = DATA_MANAGER.smart_fetch(symbol, period='6mo', interval='1d', n_bars=100,
                                                  prefer_exch=prefer_exch, strict_exch=True)
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
ML_STUDY_PATH_BSE = pathlib.Path('ml_edge_study_bse.json')
ML_STUDY_SCHEMA = 1
ML_STUDY_MODEL = 'ml-edge-purged-wf-v1'
ML_STUDY_MAX_AGE_DAYS = 365
_ml_study_cache = {'mtime': None, 'size': None, 'data': None}
_ml_study_lock = threading.Lock()


def load_ml_study(exchange='NSE'):
    """`ml_edge_study.json` padho (mtime/size badle to re-read). Missing/invalid → None.

    Fail-closed in the *honest* direction: artifact na ho to UI "OOS study absent"
    dikhata hai — iska matlab ye NAHI ki edge hai.
    """
    path = ML_STUDY_PATH_BSE if exchange == 'BSE' else ML_STUDY_PATH
    try:
        st = path.stat()
    except OSError:
        return None
    with _ml_study_lock:
        if (_ml_study_cache.get('path') == str(path)
                and _ml_study_cache['mtime'] == st.st_mtime
                and _ml_study_cache['size'] == st.st_size
                and _ml_study_cache['data'] is not None):
            return _ml_study_cache['data']
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
            if doc.get('schema') != ML_STUDY_SCHEMA or doc.get('model') != ML_STUDY_MODEL:
                raise ValueError('schema/model mismatch')
            strategies = doc.get('strategies')
            if not isinstance(strategies, dict) or not strategies:
                raise ValueError('no strategies')
            for name, s in strategies.items():
                if not isinstance(s, dict) or 'accuracy_pct' not in s:
                    raise ValueError(f'strategy {name} incomplete')
            _ml_study_cache.update(path=str(path), mtime=st.st_mtime, size=st.st_size, data=doc)
            return doc
        except Exception:
            _ml_study_cache.update(mtime=st.st_mtime, size=st.st_size, data=None)
            return None


def ml_study_payload(exchange='NSE', symbol=None):
    """UI/API ke liye compact study block (+ staleness note)."""
    doc = load_ml_study(exchange)
    if not doc:
        return {'ready': False,
                'error': ('OOS ML study absent — python tools/build_ml_edge_study.py '
                          'run karein (internal ML number unvalidated hai)'),
                'edge_found': None}
    from research.provenance import pipeline_fingerprint
    compatible = (doc.get('pipeline_fingerprint') == pipeline_fingerprint() and doc.get('exchange') == exchange)
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
        'selected_symbol': symbol,
        'selected_symbol_in_study': (symbol in (doc.get('symbols_scored') or []) if symbol else None),
        'selected_symbol_note': ('Selected stock not included in this study; cohort verdict is not a stock-specific test.'
                                 if symbol and symbol not in (doc.get('symbols_scored') or []) else
                                 'Selected stock included in the research cohort; pooled results are not a stock-specific or deployed-ensemble certification.'),
        'strategies': strategies,
        'permutation_null': doc.get('permutation_null'),
        'verdict': doc.get('verdict'),
        'edge_found': bool(doc.get('edge_found')),
        'stale': (days is not None and days > ML_STUDY_MAX_AGE_DAYS),
        'age_days': days,
        'pipeline_version': doc.get('pipeline_version'),
        'exchange': doc.get('exchange'), 'model_scope': doc.get('model_scope'),
        'data_manifest': doc.get('data_manifest'), 'execution_validated': False,
        'rebuild_required': not compatible,
        'disclosure': (('ARCHIVED or different-exchange study: content fingerprint/scope mismatch; rebuild with the current research code. ' if not compatible else '') + (doc.get('disclosure') or '')),
    }


_SCORE_FORMULA_HASH = None
# FIX-84 (Phase 2): exchange-wise cache. Pehle ek hi entry thi, isliye BSE ka
# artifact load karne par NSE ka fit overwrite ho jaata (aur ulta).
_SCORE_CAL_CACHE = {}      # exchange -> {'key', 'fitted', 'error'}
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


def _score_history_for(asof_session, bars, symbol, exchange='NSE'):
    """Read validated JSON once per file change; fail CLOSED if missing/stale.

    FIX-84 (Phase 2): exchange ke hisaab se alag artifact load hota hai. BSE frame
    par ab BSE ka apna fit lagta hai, NSE distribution se compare nahi hota.

    A single scanner run has 29 `composite` scores, NOT comparable to this
    pipeline. Only 250 historical snapshots of THIS exact stock-score formula
    can supply percentile bands.
    """
    exch = str(exchange or 'NSE').strip().upper()
    if symbol not in SCORE_CAL.UNIVERSE:
        return None, (f'{symbol} is outside the supported {len(SCORE_CAL.UNIVERSE)}-symbol {exch} calibration universe; '
                      'rebuilding the current universe does not add this symbol')
    if exch not in ('NSE', 'BSE'):
        exch = 'NSE'
    path = SCORE_CAL.artifact_path(exch)
    try:
        st = path.stat()
        if st.st_size > 5_000_000:
            raise SCORE_CAL.CalibrationError('artifact unusually large')
        key = (str(path), st.st_mtime_ns, st.st_size, score_formula_hash())
    except FileNotFoundError:
        return None, (f'{exch} score history missing — python '
                      f'tools/build_score_calibration.py'
                      + (' --exchange BSE' if exch == 'BSE' else '') + ' run karein')
    except (OSError, SCORE_CAL.CalibrationError) as exc:
        return None, f'history unreadable: {exc}'

    with _SCORE_CAL_LOCK:
        ent = _SCORE_CAL_CACHE.get(exch)
        if ent is None or ent['key'] != key:
            try:
                with path.open(encoding='utf8') as f:
                    data = json.load(f)
                # FIX-84: artifact khud batata hai kis exchange ka hai — cross-wired
                # file (BSE frame par NSE fit) yahan pakdi jaati hai.
                a_exch = str(data.get('exchange') or exch).upper()
                if a_exch != exch:
                    raise SCORE_CAL.CalibrationError(
                        f'artifact {exch} ka hona chahiye tha par {a_exch} ka hai')
                _SCORE_CAL_CACHE[exch] = {
                    'key': key,
                    'fitted': SCORE_CAL.validate_artifact(data, key[-1]),
                    'error': None}
            except (OSError, ValueError, SCORE_CAL.CalibrationError) as exc:
                _SCORE_CAL_CACHE[exch] = {'key': key, 'fitted': None,
                                          'error': f'invalid score history: {exc}'}
            ent = _SCORE_CAL_CACHE[exch]
        fitted, error = ent['fitted'], ent['error']
    if not fitted:
        return None, error
    if symbol not in fitted['symbols_covered']:
        return None, f'{symbol} historical snapshots me absent — no fitted rank'
    if fitted.get('symbol_sessions', {}).get(symbol, 0) < SCORE_CAL.WINDOW_SESSIONS:
        return None, f'{symbol}: fewer than {SCORE_CAL.WINDOW_SESSIONS} scored historical sessions; rebuild/expand real history'
    try:
        SCORE_CAL.for_session(fitted, asof_session, bars=bars,
                              current_day=datetime.now(IST).date().isoformat())
        return fitted, None
    except SCORE_CAL.CalibrationError as exc:
        return None, str(exc)


def ensemble_score(engines, *, asof_session=None, bars=None, symbol=None,
                   calibration=None, data_fresh=True, exchange='NSE'):
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
        fitted, reason = _score_history_for(asof_session, bars, symbol, exchange)

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
        'symbol_supported': symbol in SCORE_CAL.UNIVERSE,
        'bars_available': bars,
        'bars_required': SCORE_CAL.LOOKBACK_BARS,
        'rebuild_command': (f'python tools/build_score_calibration.py --exchange {str(exchange).upper()}'
                            if symbol in SCORE_CAL.UNIVERSE and not ready and not fitted
                            and str(exchange).upper() in ('NSE','BSE')
                            and bars is not None and bars >= SCORE_CAL.LOOKBACK_BARS and data_fresh else None),
        'remediation': ('Coverage expansion and validated history are required; a routine rebuild will not add this symbol.'
                        if symbol not in SCORE_CAL.UNIVERSE else
                        'Insufficient completed history; do not pad or synthesize bars.'
                        if bars is not None and bars < SCORE_CAL.LOOKBACK_BARS else
                        'Refresh the requested-exchange history before fitting.' if not data_fresh else
                        'A rebuild only refreshes the existing universe; it does not establish a profitable edge.'),
        # FIX-84: kaun sa exchange fit use hua — UI ko sach bolne ke liye zaroori.
        # Pehle 'basis' me hamesha "NSE-universe" hardcoded tha, chahe BSE fit laga ho.
        'exchange': (str(exchange or 'NSE').strip().upper()
                     if str(exchange or 'NSE').strip().upper() in ('NSE', 'BSE') else 'NSE'),
        'basis': (f'250 completed {str(exchange or "NSE").strip().upper()}-universe sessions; '
                  '4 daily OHLCV engines; trailing 250 bars'),
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
                          min_n=None, symbol=None, exchange='NSE'):
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
            key = (str(symbol), exchange, direction, float(sl_mult), float(t1_mult),
                   int(horizon), int(min_n), frame_digest(df[['High', 'Low', 'Close']]))
        except Exception:
            key = None
        hit = _PLAN_MEASURE_CACHE.get(key) if key else None
        if hit is not None:
            return hit
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
            _cache_put(_PLAN_MEASURE_CACHE, key, out)              # FIX-99
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


# ── FIX-57/59: transaction-cost model ─────────────────────────────────────
# FIX-57 ne yahan ek ALAG cost model banaya tha (flat % dict). FIX-59 me pata
# chala ki `research/costs.py` me pehle se poora model maujood tha — notional-
# aware, brokerage-cap ke saath, aur sahi STT/stamp rules ke saath. Do models
# the jo aapas me DISAGREE karte the. Ab ek hi source of truth.
#
# ⚠️ FIX-57 ke do errors (measured, 2026 rates se verify karke):
#   1. Delivery STT: maine 0.1% SIRF SELL par lagaya. Sahi: delivery par 0.1%
#      DONO taraf (buy + sell). -> 0.10pp understate
#   2. Delivery stamp: maine 0.003% lagaya, jo INTRADAY rate hai. Sahi:
#      delivery buy par 0.015%. -> 0.012pp understate
#   3. Intraday STT: maine 0.025% x2 lagaya. Sahi: SIRF sell par (buy leg nil).
#      -> 0.025pp overstate
#   Net effect: intraday 0.2310% batata tha, sahi 0.1406% (Rs1L par) — OVERSTATE;
#               delivery 0.2810% batata tha, sahi 0.3276% (Rs1L par) — UNDERSTATE.
#   Yaani mode ke hisaab se dono directions me galat tha.
#
# Aur brokerage ₹20 par CAPPED hai — isliye flat % chhote positions par galat
# hota hai. research/costs.py ye pehle se sahi karta tha.
def _cost_side(default, env_key, lo=0.0, hi=50.0):
    """`.env` override, sane range me clamp. Galat value chup-chaap 0 na ho."""
    raw = (os.environ.get(env_key) or '').strip()
    if not raw:
        return float(default)
    try:
        v = float(raw)
    except ValueError:
        return float(default)
    return float(min(max(v, lo), hi))


def _env_or_none(env_key):
    """Set ho to float, warna None — "default use karo" ka signal."""
    raw = (os.environ.get(env_key) or '').strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _cost_config():
    """`research/costs.CostConfig` — DEFAULTS WAHI REHTE HAIN jo research me hain.

    FIX-59: pehle maine yahan apne defaults daale the (exch_pct 0.00297) jabki
    research/costs.py me 0.00325 tha — yaani do jagah do default, 0.0008pp ka
    drift. Ab `.env` me set ho TABHI override hota hai, warna research ka default.
    Ek source of truth.

    Statutory rates (STT/stamp) override karne ka raasta jaan-boojh kar nahi diya
    — FIX-57 ka `STOCKAI_COST_STT` isi wajah se bug bana tha (ek number se
    mode-dependent tax override karna).
    """
    from research.costs import CostConfig
    kw = {}
    # STOCKAI_COST_BROKERAGE percent-per-side me hai (0.03 = 0.03%)
    _b = _env_or_none('STOCKAI_COST_BROKERAGE')
    if _b is not None:
        kw['brokerage_pct'] = min(max(_b, 0.0), 5.0) / 100.0
    _bc = _env_or_none('STOCKAI_COST_BROKERAGE_CAP')
    if _bc is not None:
        kw['brokerage_cap'] = min(max(_bc, 0.0), 5000.0)
    # STOCKAI_COST_SLIPPAGE percent-per-side me hai (0.05 = 5 bps)
    _sl = _env_or_none('STOCKAI_COST_SLIPPAGE')
    if _sl is not None:
        kw['slippage_bps'] = min(max(_sl, 0.0), 5.0) * 100.0
    # NSE equity transaction charge — sources me 0.00297 / 0.00307 / 0.00325
    # teeno milte hain, isliye configurable. Default research/costs.py ka.
    _ex = _env_or_none('STOCKAI_COST_EXCH_PCT')
    if _ex is not None:
        kw['exch_pct'] = min(max(_ex, 0.0), 1.0) / 100.0
    return CostConfig(**kw)


def trade_cost_pct(mode='intraday', notional=100000.0):
    """Round-trip cost, % of notional. Ab research/costs.py se — notional-aware.

    `notional` matter karta hai kyunki brokerage ₹20 par capped hai:
    ₹1L position par 0.02%/side, ₹25k par 0.03%/side.
    """
    return float(_cost_config().round_trip_pct(
        intraday=(str(mode).lower() != 'delivery'), notional=float(notional or 0) or 100000.0))


def cost_breakdown(mode='intraday', notional=100000.0):
    """Component-wise breakdown (₹ + %) — display/debug ke liye."""
    cfg = _cost_config()
    n = float(notional or 0) or 100000.0
    intraday = (str(mode).lower() != 'delivery')
    b = cfg.breakdown(n, 'buy_intraday' if intraday else 'buy')
    s = cfg.breakdown(n, 'sell_intraday' if intraday else 'sell')
    return {'notional': n, 'mode': 'intraday' if intraday else 'delivery',
            'buy': b, 'sell': s, 'round_trip_pct': round(b['total_pct'] + s['total_pct'], 4)}


# Display ke liye derived view — ab ye hardcoded NAHI hai, config se banta hai,
# isliye drift nahi kar sakta. (FIX-57 me ye hardcoded dict tha.)
_CFG = _cost_config()
TRADE_COST = {
    'brokerage_pct':    round(_CFG.brokerage_pct * 200, 5),      # x2 sides, % me
    'brokerage_cap_rs': _CFG.brokerage_cap,
    'exchange_txn_pct': round(_CFG.exch_pct * 200, 6),
    'slippage_pct':     round(_CFG.slippage_bps / 100.0 * 2, 5),
    'stt_stamp_gst':    'statutory — research/costs.py me mode ke hisaab se lagta hai',
}


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
    Legacy research geometry helper, not institutional/live sizing validation.
    The production route supplies plan_measure=None and keeps execution blocked.

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

    # FIX-57/59: cost-aware plan. Ye naya prediction NAHI hai — sirf wo arithmetic
    # jo pehle missing thi. Targets/SL gross the; ab net (cost ke baad) bhi.
    # FIX-59: cost ab NOTIONAL-AWARE hai, kyunki brokerage Rs20 par capped hai —
    # Rs25k position par 0.03%/side, Rs1L par 0.02%/side. Position hai to uska
    # notional; warna capital ko reference maante hain aur BATATE hain ki reference hai.
    _sl_pct = round(risk_per_share / price * 100, 2) if price > 0 else 0.0
    _cost_notional = notional if notional and notional > 0 else float(capital or 0)
    _cost_basis = 'position' if (notional and notional > 0) else 'capital (reference — qty 0 hai)'
    _cost = cost_plan(trade_cost_pct(_cost_mode, _cost_notional), price, _sl_pct, {
        't1': (abs(t1 - price) / price * 100 if price > 0 else None),
        't2': (abs(t2 - price) / price * 100 if price > 0 else None),
        't3': (abs(t3 - price) / price * 100 if price > 0 else None),
    })
    if _cost:
        _cost['mode'] = _cost_mode
        _cost['qty'] = qty
        _cost['notional'] = round(_cost_notional, 2)
        _cost['notional_basis'] = _cost_basis
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
    quote = get_live_quote(resolve_symbol(checked_symbol(symbol, _ex)), force=force,
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
        resolved = resolve_symbol(checked_symbol(symbol, _sse_ex))
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
    _miss_ex = (request.args.get('ex') or 'NSE').strip().upper()
    _miss_key = (symbol.upper(), _miss_ex)
    _hit = _FAIL_CACHE.get(_miss_key, 0)
    if time.time() < _hit:
        return jsonify({'error': f"Symbol '{symbol}' could not be resolved "
                                  f"(cached miss, retry in {int(_hit - time.time())}s)"}), 404
    resolved = resolve_symbol(checked_symbol(symbol, _miss_ex))
    # FIX-55: user jo exchange chune wahi data aaye. Pehle pipeline hamesha
    # NSE-first tha — BSE chunne ka koi rasta hi nahi tha.
    req_exch = (request.args.get('ex') or 'NSE').strip().upper()
    if req_exch not in ('NSE', 'BSE'):
        req_exch = 'NSE'
    # FIX-83: strict — user ne jo exchange chuna, USI ka data aayega. Pehle BSE
    # maangne par chupchap NSE ka frame mil jaata tha (Yahoo ke paas BSE
    # historicals nahi hote), aur dashboard NSE ke numbers BSE ki tarah dikha
    # deta tha. Ab cross-exchange data nahi milta — saaf error milta hai.
    # Outside the pooled cohort, request enough REAL history for an automatic
    # own-history diagnostic (500 completed inputs; cushion for partial bars).
    own_scope = resolved not in SCORE_CAL.UNIVERSE
    df, active_source = DATA_MANAGER.smart_fetch(resolved, period='3y' if own_scope else '2y', interval='1d',
                                                 n_bars=max(550, CONFIG['CHART_CANDLES'] * 2) if own_scope else CONFIG['CHART_CANDLES'] * 2,
                                                 prefer_exch=req_exch, strict_exch=True)
    if df is not None and exchange_from_source(active_source) != req_exch:
        return jsonify({'error': 'Historical provider exchange unverified or mismatched; response refused', 'requested_exchange': req_exch}), 503
    daily_source = active_source  # price path NSE live source se baad me replace ho sakta hai

    if df is None or len(df) < 20:
        _fb = getattr(DATA_MANAGER, 'exch_fallback', None)
        if _fb:
            # Requested exchange ka data nahi, par doosre ka hai — ye "stock nahi
            # mila" nahi hai, isliye _FAIL_CACHE me mat daalo (NSE kaam kar sakta hai).
            _other = _fb[1]
            return jsonify({
                'error': f"{req_exch} par '{symbol}' ka data abhi kisi source se nahi mil raha.",
                'requested_exchange': req_exch,
                'available_exchange': _other,
                'available_source': _fb[0],
                'hint': f"{_other} chunein to data mil jayega. Dono exchange ke "
                        f"closing prices alag hote hain, isliye {_other} ka data "
                        f"{req_exch} ki jagah nahi dikha rahe.",
            }), 409
        _cache_put(_FAIL_CACHE, _miss_key, time.time() + 300)  # FIX-99
        return jsonify({'error': f"No usable {req_exch} history for '{symbol}'.",
                        'error_code': 'SYMBOL_OR_HISTORY_UNAVAILABLE',
                        'hint': 'Check the ticker and exchange using stock search. A provider miss does not prove that a company is unlisted; no similar ticker was substituted.'}), 404

    try:
        df = calculate_all_indicators(df)
        L = df.iloc[-1]
        prev = df.iloc[-2]
        if (sfx(L.get('Close')) is None or sfx(L.get('Close')) <= 0):
            return jsonify({'error': 'Last daily Close unavailable — fake ₹0 price nahi dikhate'}), 503
        
        # ── Exact Live NSE LTP Handshake Hook ──
        live_nse = fetch_nse_live_ltp(resolved) if req_exch == 'NSE' else None
        if not live_nse:
            live_nse = fetch_yahoo_live_ltp(resolved, prefer_exch=req_exch)
        if live_nse and (exchange_from_source(live_nse.get('source')) != req_exch
                         or (live_nse.get('symbol') and live_nse['symbol'] != resolved)
                         or not sfx(live_nse.get('price')) or sfx(live_nse.get('price')) <= 0):
            live_nse = None
        if live_nse:
            price = live_nse['price']
            change = live_nse.get('change')
            pChange = live_nse.get('pChange')
            active_source = live_nse.get('source') or 'NSE Direct Live'
        else:
            price = sf(L['Close'])
            _prev_close = sfx(prev.get('Close'))
            change = round(price - _prev_close, 2) if _prev_close is not None else None
            pChange = (round(change / _prev_close * 100, 2)
                       if change is not None and _prev_close and _prev_close > 0 else None)

        # FIX-64: top feed-badge sirf `live_nse` (NSE Direct) se chalta tha. Jab NSE
        # Direct block/fail ho (403) par Yahoo live FRESH ho, badge "DELAYED (age
        # unknown)" dikhaega jabki price-chip "LIVE" — bilkul wahi do-badge conflict
        # jo FIX-51 ne theek kiya tha, ab `live_nse` variable se laut aaya tha.
        # Feed-state ke liye NSE live use karo; na ho to Yahoo live (jo header chip
        # ko chalata hai) — taaki dono badges ek hi source se agree karein.
        _feed_live = live_nse  # analysis badge describes the price actually used

        # FIX-32: ATR missing ho to 2% of price fallback — par ab ye DISCLOSE hota hai
        # (pehle chup-chaap hota tha aur risk plan 'ATR-based' lagta tha)
        _atr_raw = sfx(L.get('ATR'))
        atr_basis = 'ATR(14)' if _atr_raw else 'assumed 2% of price (ATR missing)'
        atr = _atr_raw if _atr_raw else price * 0.02

        try:
            import yfinance as yf
            # FIX-54: pehle hamesha `.NS` lagta tha. BSE-only stocks (jaise
            # DHOOTIN = Dhoot Industrial Finance) par Yahoo 404 deta tha aur
            # poora Fundamentals panel N/A dikh jaata tha — jabki `.BO` se sab
            # milta hai (measured: mcap Rs158.6Cr, P/E 2.85, P/B 0.36).
            # Frame kis exchange se aaya wo FIX-53 se pata hai.
            _yf_suffix = '.BO' if req_exch == 'BSE' else '.NS'
            info = yf.Ticker(f"{resolved}{_yf_suffix}").info or {}
        except Exception:
            info = {}

        for numeric_key in ('trailingPE','returnOnEquity','debtToEquity','priceToBook','dividendYield','marketCap'):
            info[numeric_key] = sfx(info.get(numeric_key))
        if info.get('marketCap') is not None and info['marketCap'] < 0:
            info['marketCap'] = None
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
        completed_session = last_completed_session(now_ist, exchange=req_exch)
        ranked_df = df[[pd.Timestamp(x).date() <= completed_session for x in df.index]]
        partial_today = len(ranked_df) != len(df)
        if ranked_df.empty:
            return jsonify({'error': 'No completed daily sessions for ranking'}), 503
        ml_res = ml_engine(ranked_df, symbol=resolved, exchange=req_exch)
        _atr_raw = sfx(ranked_df.iloc[-1].get('ATR'))
        atr_basis = 'ATR(14), completed daily session' if _atr_raw else 'assumed 2% of price (ATR missing)'
        atr = _atr_raw if _atr_raw else price * 0.02
        rank_window = ranked_df.tail(SCORE_CAL.LOOKBACK_BARS)
        rank_session = pd.Timestamp(ranked_df.index[-1]).strftime('%Y-%m-%d')
        e1 = engine_volume_profile(rank_window)
        e2 = engine_rvol_cvd(rank_window)
        e3 = engine_vcp(rank_window)
        e4 = engine_smc(rank_window)
        e5 = engine_market_regime()                 # market-wide exposure ONLY
        e1['method_note'] = 'Candle-binned volume proxy, not trade-level volume at price'
        e2['method_note'] = 'Candle-direction volume proxy, not true aggressor-classified CVD'
        e6 = engine_multitimeframe(resolved, daily_df=df,
                                    prefer_exch=req_exch)  # independent diagnostic
        engines = [e1, e2, e3, e4, e5, e6]

        # FIX-84 (Phase 2): exchange ke hisaab se calibration — BSE frame par ab
        # BSE ka apna fitted rank lagta hai, NSE distribution se compare nahi hota.
        ens = ensemble_score(engines, asof_session=rank_session,
                             bars=len(ranked_df), symbol=resolved,
                             data_fresh='STALE' not in str(daily_source).upper(),
                             exchange=req_exch)
        ens['calibration']['reference_session'] = rank_session
        if own_scope:
            own = OWN_HISTORY.describe(
                ranked_df, symbol=resolved, exchange=req_exch, source=daily_source,
                formula_hash=score_formula_hash(),
                engines=(engine_volume_profile, engine_rvol_cvd, engine_vcp, engine_smc),
                current_day=now_ist.date().isoformat(),
                fresh='STALE' not in str(daily_source).upper())
            if own.get('ready') and own.get('score') != ens.get('score'):
                own.update(ready=False, status='DEFINITION_MISMATCH', relative_rank_pct=None,
                           reason='Live and historical score definitions disagree; rank refused.')
            # Descriptive only: NEVER replace pooled calibration or risk gates.
            ens['own_history'] = own
            ens['calibration']['coverage_mode'] = 'own_history_only'
            ens['calibration']['remediation'] = ('Own-history rank is built automatically when 500 real completed bars are available; pooled cross-stock action bands are not applied.')
        # FIX-30: ML ka measured accuracy bhi bhejo — risk plan ab apna win-rate
        # assumption disclose karta hai (pehle 0.62/0.55/0.45 chup-chaap use hote the)
        _ml_acc = ml_res.get('ensemble_accuracy') if isinstance(ml_res, dict) else None
        _ml_wf = ml_res.get('walk_forward_accuracy') if isinstance(ml_res, dict) else None
        _ml_base = ml_res.get('walk_forward_baseline') if isinstance(ml_res, dict) else None
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
            # Legacy unconditional barrier rates are not the signal-conditioned
            # partial-exit strategy. They must not enter live Kelly sizing.
            risk = calculate_risk(price, atr, ens['score'], action=ens['action'],
                                  measured_accuracy=_ml_acc,
                                  measured_wf_accuracy=_ml_wf,
                                  measured_baseline=_ml_base,
                                  plan_measure=None, atr_basis=atr_basis,
                                  regime=e5, require_plan=True)
            if not ens['calibration']['ready']:
                risk['risk_note'] += ' | ' + ('Own-history available; pooled action calibration unavailable.' if ens.get('own_history', {}).get('ready') else ens['calibration']['note'])
        ens['tradeable'] = False
        ens['tradeable_basis'] = 'Research only: out-of-sample net execution edge is not validated'
        risk['illustrative_qty'] = 0
        risk['kelly_pct'] = risk['kelly_pct_after_regime'] = 0.0
        risk['qty'] = 0
        risk['notional'] = 0.0
        risk['risk_amount'] = 0.0
        risk['leverage'] = 0.0
        risk['qty_pre_regime'] = 0
        if risk.get('cost'):
            risk['cost'].update(qty=0, round_trip_on_notional=0.0, notional_basis='illustrative reference, no execution')
        risk['edge_verified'] = False
        risk['exec_status'] = 'RESEARCH ONLY / NO EXECUTABLE ORDER'
        risk['risk_note'] = risk.get('risk_note', '') + ' | Illustrative sizing only; executable quantity is zero.'
        # FIX-103: NONE must not silently receive LONG-shaped execution levels.
        risk['plan_available'] = bool(ens['calibration']['ready'] and risk.get('direction') in ('LONG','SHORT'))
        risk['reference_price'] = price
        risk['reference_source'] = active_source
        if not risk['plan_available']:
            for key in ('sl','sl_pct','t1','t2','t3','entry_zone','trail_sl_plan','rr_ratio','kelly_rr_used'):
                risk[key] = None
            if risk.get('cost'):
                risk['cost']['targets_net_pct'] = {}
                risk['cost']['cost_to_risk_pct'] = None
        if risk.get('cost'):
            risk['cost']['fee_scope'] = ('NSE-default estimate; BSE and broker-specific fees are not verified' if req_exch == 'BSE' else 'Estimated NSE costs; verify broker, slippage and applicable charges')
        risk['validation'] = {
            'status': 'BLOCKED', 'execution_ready': False,
            'scenario': 'Daily ATR geometry only; 50% T1 / 50% T2 with gross entry-stop after T1',
            'holding_horizon_bars': CONFIG['PLAN_MEASURE_HORIZON'],
            'reasons': ['No independent out-of-sample validation of the full signal-conditioned scale-out strategy',
                        'Broker/product-specific per-fill fees, slippage and execution permissions not verified',
                        'Instrument tick size, lot size, price bands and borrowing permissions not verified',
                        'Corporate-action-adjusted history and fundamentals not independently reconciled'],
            'short_policy': 'Overnight cash short requires a verified borrowing/product model; intraday fees cannot validate daily holding',
            'simulator': 'scaleout_validation.py: next-Open, sequential positions, partial exits, adverse ambiguity, time exits, per-fill costs',
            'simulator_tested_is_not_strategy_validated': True}
        # Do not present an intraday round-trip fee as the net cost of a daily scale-out plan.
        if risk.get('cost'):
            risk['cost']['applicable_to_plan'] = False
            risk['cost']['targets_net_pct'] = {}
            risk['cost']['cost_to_risk_pct'] = None
            risk['cost']['warning'] = 'Separate one-entry/one-exit fee illustration only; NOT costs of the daily scale-out plan. No net target return is established.'
        risk['risk_note'] = ('No validated strategy win-rate for Kelly: allocation and executable quantity remain zero. '
                             'Historical unconditional barrier-touch rates are NOT the full scale-out strategy. '
                             + ('Own-history available; pooled action calibration unavailable.' if ens.get('own_history', {}).get('ready') else ''))
        if risk.get('plan_available'):
            risk['trail_sl_plan'] = 'Research scenario: after T1 move remaining stop to reference entry; this is gross entry-stop, NOT fee-adjusted break-even. Actual fills change risk/reward.'
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
        _dy = sfx(_dy)  # yfinance dividendYield is percent; no magnitude guessing
        _r_raw = info.get('returnOnEquity')
        # |x| <= 2.0 → fraction maano (200% tak ka ROE cover hota hai); usse
        # bada → pehle se percent. yfinance abhi hamesha fraction deta hai, ye
        # guard sirf future-proofing hai.
        _roe = sfx(_r_raw) * 100.0 if sfx(_r_raw) is not None else None

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

        year_df = df.loc[df.index >= df.index[-1] - pd.Timedelta(days=365)]
        h52 = sfx(year_df['High'].max(), 2)      # FIX-32: missing → None (0 nahi)
        l52 = sfx(year_df['Low'].min(), 2)
        pos52 = (round((price - l52) / (h52 - l52 + 1e-10) * 100, 1)
                 if (h52 is not None and l52 is not None) else None)

        response_payload = {
            'symbol': resolved,
            'data_source': active_source,  # legacy alias: headline price provider, not history
            'analysis_source': daily_source,
            'quote_source': active_source,
            'quote_time': (live_nse.get('quote_time') if live_nse else
                           f"{str(df.index[-1])[:10]} (daily bar close; not a live tick)"),
            'price': price,
            'change': change,
            'pChange': pChange,
            'ml': ml_res,
            # FIX-41 (C-2): recorded OOS study — internal `ml` number sirf
            # diagnostic hai; UI/API ka "edge hai ya nahi" jawaab yahaan se aata hai.
            'ml_study': ml_study_payload(req_exch, resolved),
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
                'pe': f"{fund_data['pe_val']:.1f}" if fund_data['pe_val'] is not None else 'N/A',
                'pb': f"{info.get('priceToBook', 0):.2f}" if info.get('priceToBook') is not None else 'N/A',
                'roe': f"{_roe:.2f}%" if _roe is not None else 'N/A',
                # FIX-51: yfinance ka `debtToEquity` PERCENTAGE hota hai (36.7 = 36.7%),
                # ratio nahi. Bina '%' ke 36.7x lagta tha. Aur `if debt_val` falsy-check
                # tha, isliye asli 0.0 D/E (zero-debt company) bhi 'N/A' ban jaata tha.
                # FIX-55: :.1f se DHOOTIN ka 0.027 -> "0.0%" ban jaata tha (precision
                # lost, value fake nahi). 1 se chhoti value par 2 extra decimals.
                'debt_equity': ('N/A' if fund_data['debt_val'] is None else
                                (f"{fund_data['debt_val']:.3f}% D/E"
                                 if abs(fund_data['debt_val']) < 1
                                 else f"{fund_data['debt_val']:.1f}% D/E")),
                'div_yield': f"{_dy:.2f}%" if _dy is not None else 'N/A',
                'mcap': f"₹{info.get('marketCap', 0) / 1e7:,.0f}Cr" if info.get('marketCap') else 'N/A',
                'sector': html_entities.unescape(str(info['sector'])) if info.get('sector') else None,        # FIX-32: 'NSE Equity' invented nahi
                'industry': html_entities.unescape(str(info['industry'])) if info.get('industry') else None
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
            'realtime_reason': ((live_nse.get('stale_reason') or 'recent same-exchange provider quote; latency unverified')
                                if live_nse else 'koi live quote nahi — price daily close se'),
            # FIX-51: top badge pehle `/NSE/i.test(data_source)` se liveness nikalta tha
            # — yaani source ke NAAM se, bilkul wahi bug jo FIX-50 ne backend me theek
            # kiya tha. Ab server state bhejta hai; UI guess nahi karta.
            # FIX-64: `_feed_live` = NSE live ya (fallback) Yahoo live — taaki top
            # badge aur price-chip dono ek hi freshness se agree karein.
            'feed_state': feed_state(bool(_feed_live and _feed_live.get('is_realtime'))),
            'feed_label': feed_label(feed_state(bool(_feed_live and _feed_live.get('is_realtime'))),
                                     (_feed_live or {}).get('quote_age_min')),
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
            'analysis_session': str(df.index[-1])[:10],
            'analysis_partial': bool(partial_today),
            'rank_session': rank_session,
            'price_basis': ((str(active_source) + ' provider quote') if live_nse
                            else 'daily frame latest Close (may be partial; see analysis_session)'),
            # FIX-53: frame kis exchange se aaya.
            # FIX-84 (Phase 2) UPDATE: pehle yahan likha tha ki "app ka score
            # calibration NSE universe par fitted hai isliye BSE frame par percentile
            # ranks technically NSE distribution se compare ho rahe hote hain".
            # Ab BSE ka APNA artifact hai (score_calibration_bse.json, TradingView
            # se 250 sessions). BSE fit missing ho to hi NSE-style gap rehta hai —
            # aur wo case calibration meta me saaf dikhta hai. Chhupana nahi, batana.
            'requested_exchange': req_exch,
            # FIX-62: pehle sirf '(NSE)'/'(BSE)' (TradingView) match hota tha —
            # Yahoo 'yahoo.ns'/'yahoo.bo' deta hai, isliye LGEINDIA jaise NSE
            # stocks par bhi "exchange unknown" dikh raha tha.
            'frame_exchange': exchange_from_source(daily_source),
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

        response_payload['historical_only'] = 'STALE' in str(daily_source).upper()
        response_payload['history_status'] = 'STALE' if response_payload['historical_only'] else 'CURRENT_SESSION'
        response_payload['expected_session'] = str(last_completed_session(exchange=req_exch))
        response_payload['data_provenance'] = {
            'analysis_source': daily_source, 'quote_source': active_source, 'exchange': req_exch,
            'adjustment_policy': df.attrs.get('adjustment', 'provider-dependent; not reconciled'),
            'licensed_realtime_entitlement': False,
            'latency_verified': False,
            'note': 'LIVE means recent provider timestamp, not certified zero-delay exchange feed. Corporate actions may distort raw lookbacks.'}
        response_payload['data_validation'] = {
            'official_eod': EOD_VALIDATION.compare(ranked_df, resolved, req_exch),
            'fundamentals': {'status': 'PROVIDER_REPORTED_NOT_INDEPENDENTLY_VERIFIED',
                             'provider_symbol': resolved + ('.BO' if req_exch == 'BSE' else '.NS'),
                             'financial_statement_asof': info.get('mostRecentQuarter'),
                             'note': 'Provider ratios may use different reporting dates; no audited-statement reconciliation claimed.'},
            'history': {'status': 'PROVIDER_OHLCV_NOT_FULLY_RECONCILED',
                        'corporate_actions_verified': False, 'all_sessions_verified': False}}
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

# ═══════════════════════════════════════════════════════════════════════════
# FIX-69: OPTIONS ANALYTICS — /options page + /api/option_chain
#   Live NSE option-chain (option-chain-v3, FIX-61 verified recipe) se chain
#   lekar option_analytics ke pure functions se PCR / OI-walls / max-pain /
#   straddle / ATM-IV nikalte hain. Sab metrics DESCRIPTIVE hain (support/
#   resistance/sentiment) — predictive edge ka claim NAHI (standing NO EDGE).
#   Sandbox se NSE blocked hai; ye aapke residential machine par chalta hai.
# ═══════════════════════════════════════════════════════════════════════════
import option_analytics as _opta

_OPT_SESS = None


# FIX-83: _opt_get pehle `except Exception: pass` karta tha aur non-200 par chupchap
# None return karta tha. Matlab jab NSE 403 "Access Denied" deta tha (Akamai IP
# block), reason KAHIN nahi dikhta tha — user ko sirf generic "market data
# unavailable" milta tha, aur mujhe 3 alag commands chala kar pata karna pada ki
# asal me handshake hi 403 de raha hai aur 0 cookies milte hain.
# Ab har call ka asli outcome record hota hai aur API response me jaata hai.
_OPT_LAST = {'url': None, 'status': None, 'error': None, 'handshake': None,
             'cookies': None, 'at': None}


def _opt_session():
    import requests as _rq
    s = _rq.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                    'Chrome/131.0.0.0 Safari/537.36'})
    try:
        r = s.get('https://www.nseindia.com/option-chain', timeout=20)   # cookie handshake
        # FIX-83: handshake ka status bhi record karo — 403 + 0 cookies ka matlab
        # hai poori NSE site is IP ke liye blocked hai (sirf API nahi).
        _OPT_LAST['handshake'] = r.status_code
        _OPT_LAST['cookies'] = len(s.cookies)
        if r.status_code in (401, 403):
            _OPT_LAST['error'] = (f'NSE cookie handshake HTTP {r.status_code} '
                                  f'({len(s.cookies)} cookies) — is IP ko NSE ne block kiya hua hai')
            print(f"⚠️  [NSE] {_OPT_LAST['error']}")
    except Exception as e:
        _OPT_LAST['handshake'] = None
        _OPT_LAST['cookies'] = 0
        _OPT_LAST['error'] = f'NSE handshake fail: {type(e).__name__}: {e}'[:160]
    return s


def _opt_get(url):
    global _OPT_SESS
    if _OPT_SESS is None:
        _OPT_SESS = _opt_session()
    try:
        r = _OPT_SESS.get(url, timeout=25, headers={
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': 'https://www.nseindia.com/option-chain',
            'Accept-Encoding': 'identity'})
        if r.status_code == 200 and r.text and r.text[:1] in '{[':
            _OPT_LAST.update(url=url, status=200, error=None, at=time.time())
            return r.json()
        # FIX-83: non-200 ya non-JSON — reason record karo, chhupao nahi.
        if r.status_code in (401, 403):
            why = (f'HTTP {r.status_code} Access Denied — NSE ne is IP ko block kiya '
                   f'hua hai (Akamai). Residential IP / market hours me try karo.')
        elif r.status_code == 200:
            why = f'HTTP 200 par response JSON nahi hai (pehla char {r.text[:1]!r})'
        else:
            why = f'HTTP {r.status_code}'
        _OPT_LAST.update(url=url, status=r.status_code, error=why, at=time.time())
        print(f"⚠️  [NSE] {url.split('/api/')[-1][:40]} → {why}")
    except Exception as e:
        _OPT_LAST.update(url=url, status=None,
                         error=f'{type(e).__name__}: {e}'[:160], at=time.time())
        print(f"⚠️  [NSE] {url.split('/api/')[-1][:40]} → {type(e).__name__}: {e}")
    return None


def _opt_last_error():
    """FIX-83: last NSE call ka asli failure reason — API response me bhejne ke liye."""
    d = dict(_OPT_LAST)
    if d.get('at'):
        d['age_seconds'] = round(time.time() - d['at'], 1)
    return d


def _fetch_option_chain(symbol, expiry=None):
    if expiry is None:
        ci = _opt_get(f'https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}')
        if not ci or not ci.get('expiryDates'):
            return None
        expiry = ci['expiryDates'][0]
    j = _opt_get(f'https://www.nseindia.com/api/option-chain-v3?symbol={symbol}&expiry={expiry}')
    if not j:
        return None
    rec = j.get('records') or {}
    rows = rec.get('data') or []
    if not rows:
        return None
    norm = []
    for r in rows:
        ce = r.get('CE') or {}
        pe = r.get('PE') or {}
        norm.append({
            'strike': float(r.get('strikePrice') or 0),
            'ce_oi': float(ce.get('openInterest') or 0), 'ce_ltp': float(ce.get('lastPrice') or 0),
            'ce_iv': float(ce.get('impliedVolatility') or 0),
            'pe_oi': float(pe.get('openInterest') or 0), 'pe_ltp': float(pe.get('lastPrice') or 0),
            'pe_iv': float(pe.get('impliedVolatility') or 0)})
    return {'symbol': symbol, 'expiry': expiry, 'spot': rec.get('underlyingValue'), 'rows': norm}


@app.route('/api/option_chain/<symbol>')
def api_option_chain(symbol):
    data = _fetch_option_chain(symbol.upper().replace('.NS', ''))
    if not data:
        return jsonify({'error': 'option chain unavailable (NSE block / off-market) — '
                                 'residential IP + market hours par try karo'}), 503
    rows = data['rows']
    spot = data['spot']
    return jsonify({
        'symbol': data['symbol'], 'expiry': data['expiry'], 'spot': spot,
        'totals': _opta.chain_totals(rows),
        'walls': _opta.oi_walls(rows),
        'max_pain': _opta.max_pain(rows),
        'straddle': _opta.straddle_price(rows, spot),
        'atm_iv': _opta.atm_iv(rows, spot),
        'rows': sorted(rows, key=lambda r: r['strike'])})


@app.route('/options')
def options_page():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'Options.html')


# ═══════════════════════════════════════════════════════════════════════════
# FIX-70: MARKET COCKPIT — /cockpit page + /api/market_cockpit
#   NSE /api/allIndices (cookie-free endpoint) se saare indices + whole-market
#   advance/decline + sectoral heatmap. FIX-69 ka _opt_get session hi reuse
#   hota hai (same cookie handshake / headers).
#   Sab kuch DESCRIPTIVE market-state hai — koi predictive claim nahi.
# ═══════════════════════════════════════════════════════════════════════════
import market_cockpit as _mkt

_COCKPIT_HEADLINE = ['NIFTY 50', 'NIFTY BANK', 'NIFTY NEXT 50', 'NIFTY MIDCAP 50',
                     'NIFTY FINANCIAL SERVICES 25/50']


def _fetch_all_indices():
    return _opt_get('https://www.nseindia.com/api/allIndices')


@app.route('/api/market_cockpit')
def api_market_cockpit():
    payload = _fetch_all_indices()
    if not payload or not payload.get('data'):
        # FIX-83: pehle generic message tha — asli reason (403 block? handshake
        # fail? timeout?) kahin nahi dikhta tha. Ab server jo jaanta hai wo bhejta hai.
        why = _opt_last_error()
        return jsonify({
            'error': 'market data unavailable (NSE block / off-market) '
                     '— residential IP + market hours par try karo',
            'reason': why.get('error'),
            'nse_status': why.get('status'),
            'handshake_status': why.get('handshake'),
            'cookies': why.get('cookies'),
            'endpoint': (why.get('url') or '').split('/api/')[-1][:60],
        }), 503
    sectors = _mkt.sector_heatmap(payload)
    return jsonify({
        'source': 'NSE India (allIndices)',
        'timestamp': payload.get('timestamp'),
        'age_minutes': _mkt.data_age_minutes(payload.get('timestamp')),
        'breadth': _mkt.market_breadth(payload),
        'indices': _mkt.index_cards(payload, _COCKPIT_HEADLINE),
        'vix': _mkt.find_vix(payload),
        'sectors': sectors,
        'movers': _mkt.extremes(sectors, 3)})


@app.route('/cockpit')
def cockpit_page():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'Cockpit.html')


# ═══════════════════════════════════════════════════════════════════════════
# FIX-74: CALCULATORS HUB — /calculators page + /api/calc/*
#   Saara arithmetic calculators.py (pure Python) me hota hai, page sirf
#   dikhata hai — taaki numbers TESTED code se aayein, untested JS se nahi.
#   Rates research/costs.py se (FIX-59 model + FIX-73 NSE 0.00307%).
# ═══════════════════════════════════════════════════════════════════════════
import calculators as _calc


def _q(name, default=None):
    v = request.args.get(name)
    return default if v in (None, '') else v


@app.route('/api/calc/trade_costs')
def api_calc_trade_costs():
    r = _calc.trade_costs(_q('notional', 100000), _q('mode', 'delivery'),
                          _q('brokerage_per_order', 0), _q('brokerage_pct', 0),
                          _q('slippage_bps', 0))
    if r is None:
        return jsonify({'error': 'notional 0 se bada number hona chahiye'}), 400
    return jsonify(r)


@app.route('/api/calc/position_size')
def api_calc_position_size():
    r = _calc.position_size(_q('capital'), _q('risk_pct'), _q('entry'),
                            _q('stop'), _q('side', 'long'))
    if r is None:
        return jsonify({'error': 'inputs invalid — capital>0, 0<risk%<=100, '
                                 'entry>0, stop number'}), 400
    if 'error' in r:
        return jsonify(r), 400
    return jsonify(r)


@app.route('/api/calc/sip')
def api_calc_sip():
    r = _calc.sip(_q('monthly'), _q('years'), _q('annual_rate_pct'))
    if r is None:
        return jsonify({'error': 'inputs invalid — monthly>0, years>0, rate>=0'}), 400
    return jsonify(r)


@app.route('/api/calc/rates')
def api_calc_rates():
    """Kaun se rates, kis date ke, kis source se — chhupaya hua kuch nahi."""
    return jsonify({'as_of': _calc.RATES_AS_OF, 'source': _calc.RATES_SOURCE,
                    'note': 'Brokerage broker-specific hai, isliye wo input hai.'})


@app.route('/calculators')
def calculators_page():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)),
                               'Calculators.html')


# FIX-75: FII/DII ACTIVITY — /fidii page + /api/fidii
# Do alag NSE endpoints hain (NSE-only vs NSE+BSE+MSEI) — fidii.py ke docstring
# me measured numbers + authoritative labels hain. Dono dikhate hain, label ke saath.
import fidii as _fid

_FID_CACHE = {'ts': None, 'data': None}
_FID_TTL = 900          # 15 min. Ye data din me EK baar publish hota hai (close ke baad).
_FID_HIST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'reports', 'fidii_history.csv')


def _fid_get(path):
    """NSE FII/DII endpoint. _opt_session() ka cookie jar reuse karta hai, par
    Referer fii-dii page ka bhejta hai — _opt_get() option-chain ka referer
    hardcode karta hai, isliye wo yahan use nahi kiya."""
    global _OPT_SESS
    if _OPT_SESS is None:
        _OPT_SESS = _opt_session()
    try:
        r = _OPT_SESS.get('https://www.nseindia.com/api/' + path, timeout=25, headers={
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': 'https://www.nseindia.com/reports/fii-dii',
            'Accept-Encoding': 'identity'})
        if r.status_code == 200 and r.text and r.text[:1] in '{[':
            return r.json()
    except Exception:
        pass
    return None


def _fid_history(limit=20, scope='all'):
    """Locally accumulated history (tools/collect_fidii_daily.py). Nahi hai to [].

    CSV me DONO scopes hote hain ('all' = NSE+BSE+MSEI, 'nse' = NSE only) aur unke
    numbers alag hote hain. Bina filter ke dono rows mix ho jaate — bilkul wahi
    confusion jo is fix ka reason hai. Isliye default sirf 'all' deta hai (jo
    third-party sites publish karte hain) aur caller ko scope batata hai.
    """
    try:
        if not os.path.exists(_FID_HIST):
            return []
        rows = []
        with open(_FID_HIST, encoding='utf-8') as fh:
            hdr = fh.readline().strip().split(',')
            if 'fii_net' not in hdr:
                return []
            for ln in fh:
                p = ln.strip().split(',')
                if len(p) != len(hdr):
                    continue
                r = dict(zip(hdr, p))
                if scope and r.get('scope', 'all') != scope:
                    continue
                rows.append(r)
        return rows[-limit:][::-1]
    except Exception:
        return []


@app.route('/api/fidii')
def api_fidii():
    all_rows = _fid_get('fiidiiTradeReact')
    nse_rows = _fid_get('fiidiiTradeNse')
    snap = _fid.snapshot(all_rows, nse_rows)
    live = bool(snap['ok'])
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    if live:
        _FID_CACHE['ts'] = now
        _FID_CACHE['data'] = snap
    else:
        # NSE block/403 ho sakta hai (intermittent). Purana cache fake number se
        # behtar hai — par `live: False` ke saath, chhupa kar nahi.
        snap = _FID_CACHE['data'] or snap
    snap['live'] = live
    snap['fetched_at'] = _FID_CACHE['ts']
    snap['history'] = _fid_history()
    snap['history_scope'] = 'all'
    snap['streak_fii'] = _fid.streak(
        [(h.get('date'), h.get('fii_net')) for h in reversed(snap['history'])])
    return jsonify(snap)


@app.route('/fidii')
def fidii_page():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'Fidii.html')


# FIX-76: AI SCREENER — /screener page + /api/screener
# scan_results.json ka pehla app-level consumer. Filtering/sorting SERVER par
# hota hai (screener.py, tested) — page sirf dikhata hai.
import screener as _scr

_SCAN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scan_results.json')
_SCR_CACHE = {'mtime': None, 'data': None}


def _scr_load():
    """File ko har request par dobara parse na karna pade — mtime badle tabhi."""
    try:
        mt = os.path.getmtime(_SCAN_FILE)
    except OSError:
        _SCR_CACHE['mtime'] = None
        _SCR_CACHE['data'] = None
        return _scr.load_scan(_SCAN_FILE)          # error message wahi dega
    if _SCR_CACHE['mtime'] != mt:
        _SCR_CACHE['data'] = _scr.load_scan(_SCAN_FILE)
        _SCR_CACHE['mtime'] = mt
    return _SCR_CACHE['data']


def _fnum(name):
    """Query param -> float, ya (None, error). Galat input par 400 milta hai,
    chup-chaap ignore nahi hota (warna user ko lagega filter laga hi nahi)."""
    v = _q(name)
    if v is None:
        return None, None
    try:
        return float(v), None
    except (TypeError, ValueError):
        return None, f'{name} ek number hona chahiye (mila: {v!r})'


@app.route('/api/screener')
def api_screener():
    scan = _scr_load()
    if scan.get('error'):
        return jsonify({'ok': False, 'error': scan['error'], 'rows': [],
                        'total': 0, 'matched': 0, 'facets': {'sectors': {}, 'signals': {}},
                        'refresh': _scan_status()}), 200

    sector = _q('sector')
    signal = _q('signal')
    q = _q('q')
    sort = _q('sort', 'signal_score')
    order = _q('order', 'desc')
    min_score, err = _fnum('min_score')
    if err:
        return jsonify({'ok': False, 'error': err}), 400
    min_vol, err = _fnum('min_vol')
    if err:
        return jsonify({'ok': False, 'error': err}), 400
    mu = _q('ml_used')
    ml_used = None if mu is None else str(mu).lower() in ('1', 'true', 'yes')

    rows = scan['rows']
    matched = _scr.sort_rows(
        _scr.filter_rows(rows, sector=sector, signal=signal, min_score=min_score,
                         min_vol=min_vol, q=q, ml_used=ml_used),
        key=sort, order=order)

    st = _scr.staleness(scan.get('timestamp'))
    return jsonify({
        'ok': True,
        'as_of': scan.get('timestamp'),
        'age_label': st['age_label'],
        'age_verdict': st['verdict'],
        'total': len(rows),
        'matched': len(matched),
        'rows': matched,
        # Facets POORE dataset se — warna filter lagane par options gayab ho jaate
        'facets': _scr.facets(rows),
        'filters': {'sector': sector, 'signal': signal, 'min_score': min_score,
                    'min_vol': min_vol, 'q': q, 'ml_used': ml_used},
        'sort': {'key': sort if sort in _scr.SORT_KEYS else 'signal_score',
                 'order': order, 'valid_keys': list(_scr.SORT_KEYS)},
        'ml_note_used': _scr.ml_note({'ml_used_in_composite': True}),
        'ml_note_unused': _scr.ml_note({'ml_used_in_composite': False}),
        'refresh': _scan_status(),
        'note': ('signal_score / composite / ensemble RELATIVE RANK hain — '
                 'probability ya accuracy nahi. Data kitna purana hai upar '
                 'dikha hai; Refresh button se naya scan chala sakte ho.'),
    })


@app.route('/screener')
def screener_page():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'Screener.html')


# FIX-77: TIMEFRAME SCORES — /timeframes page + /api/timeframe/<symbol>
# calculate_kpi_scores() pehle se maujood tha par SIRF heavy /api/stock/<symbol>
# ke andar call hota tha. Measured (06-Oct-2026, RELIANCE/TCS):
#     /api/stock/RELIANCE  13.83s   (pehli call — regime ke liye ^NSEI + ^INDIAVIX
#                                    + 5m/1h frames bhi fetch karta hai)
#     lightweight path      0.47s   (2y daily + indicators + kpi)
# aur DONO ke scores EXACTLY same aaye (RELIANCE 23/43/39/35, TCS 30/54/45/43).
# Isliye ye alag lightweight endpoint hai — naya score formula NAHI.
#
# ZAROORI: fund_data wahi pass hota hai jo /api/stock/ pass karta hai
# (yf.Ticker(...).info ke trailingPE / returnOnEquity / debtToEquity). Bina
# iske longterm score 30 aata tha jabki /api/stock/ 39 dikhata — do pages par
# do alag numbers, bilkul wahi problem jo bar-bar pakdi gayi hai.
import time as _t77

_TF_CACHE = {}
_TF_TTL = 120          # 2 min. Yahoo ko bar-bar hit na karna pade.


def _tf_fund_data(resolved, daily_source):
    """/api/stock/ jaisa hi fund_data. FIX-54: BSE frame par .BO suffix."""
    try:
        import yfinance as _yf
        suffix = '.BO' if exchange_from_source(daily_source) == 'BSE' else '.NS'
        info = _yf.Ticker(f'{resolved}{suffix}').info or {}
    except Exception:
        info = {}
    return {'pe_val': info.get('trailingPE'),
            'roe_val': info.get('returnOnEquity'),
            'debt_val': info.get('debtToEquity')}


def kpi_scores_for(symbol, prefer_exch='NSE'):
    """(ok, payload). Heavy /api/stock/ ke bina wahi kpi scores."""
    resolved = resolve_symbol(checked_symbol(symbol, prefer_exch))
    if not resolved:
        return False, {'error': f'symbol "{symbol}" resolve nahi hua'}
    ok, reason = None, None
    try:
        # FIX-83: strict — requested exchange ka hi data, cross-exchange fallback nahi.
        df, src = DATA_MANAGER.smart_fetch(resolved, period='2y', interval='1d',
                                          prefer_exch=prefer_exch, strict_exch=True)
    except Exception as e:
        return False, {'error': f'data fetch fail: {type(e).__name__}: {e}'}
    # FIX-28 wala degenerate-data check reuse — khali frame par score bana kar
    # dena wahi purana bug hai.
    ok, reason = _data_ok(df, min_bars=20)
    if not ok:
        _fb = getattr(DATA_MANAGER, 'exch_fallback', None)
        _req = str(prefer_exch or 'NSE').strip().upper()
        if _fb and df is None:
            return False, {'error': f"{_req} par '{resolved}' ka data abhi nahi mil raha.",
                           'requested_exchange': _req,
                           'available_exchange': _fb[1],
                           'available_source': _fb[0],
                           'exchange_mismatch': False,
                           'hint': f"{_fb[1]} chunein to data mil jayega — par dono "
                                   f"exchange ke prices alag hote hain, isliye "
                                   f"{_fb[1]} ka data {_req} bana kar nahi dikha rahe."}
        return False, {'error': f'{resolved} ka data usable nahi: {reason}',
                       'source': src}
    if exchange_from_source(src) != str(prefer_exch).upper():
        return False, {'error': 'Historical provider exchange unverified or mismatched; response refused'}
    dfi = calculate_all_indicators(df)
    fd = _tf_fund_data(resolved, src)
    kpi = calculate_kpi_scores(dfi, fd)
    last = dfi.iloc[-1]
    # FIX-80: aapne kaunsa exchange maanga aur data kis exchange se aaya — ye
    # pehle SIRF server console me warn hota tha ("BSE ka koi fresh source nahi
    # mila — fallback use ho raha hai"). API response me koi field hi nahi tha,
    # isliye page par sirf source string '(NSE)' dikhti thi. Chhupa hua fallback
    # nahi hona chahiye — wahi problem jo bar-bar pakdi gayi hai.
    req = str(prefer_exch or '').strip().upper() or None
    act = exchange_from_source(src)
    mismatch = bool(req and act and req != act)
    return True, {
        'symbol': resolved,
        'source': src,
        'historical_only': 'STALE' in str(src).upper(),
        'expected_session': str(last_completed_session(exchange=req)),
        'exchange_requested': req,
        'exchange_actual': act,
        'exchange_mismatch': mismatch,
        'exchange_note': (
            f'Aapne {req} maanga tha, par {req} ka koi fresh source nahi mila — '
            f'ye data {act} se aaya hai. Dono exchange par price alag ho sakta '
            f'hai, isliye numbers {act} ke hain, {req} ke nahi.'
            if mismatch else None),
        'bars': int(len(dfi)),
        'last_session': str(frame_last_date(dfi) or ''),
        'price': sfx(last.get('Close')),
        'kpi': kpi,
        # FIX-93: execution layer — score (direction) ke saath stop/target/RR/
        # squeeze/MTF/confluence. Alag field isliye ki purana score chheda na
        # jaye (screener + FIX-89a backtest usi par bane hain).
        'plan': calculate_trade_plan(dfi) if 'STALE' not in str(src).upper() else None,
        'fund_data': fd,
        'fund_note': ('PE / ROE / D/E Yahoo .info se. Koi value None ho to us '
                      'indicator ka vote SKIP hota hai (FIX-32) — isi liye '
                      '"N/6 measured" dikhta hai.'),
    }


@app.route('/api/timeframe/<symbol>')
def api_timeframe(symbol):
    exch = _q('exch', 'NSE')
    key = (str(symbol).strip().upper(), str(exch).strip().upper())
    hit = _TF_CACHE.get(key)
    if hit and (_t77.time() - hit[0]) < _TF_TTL:
        payload = dict(hit[1])
        payload['cached'] = True
        return jsonify({'ok': bool(not payload.get('error')), **payload})
    ok, payload = kpi_scores_for(symbol, prefer_exch=key[1])
    _cache_put(_TF_CACHE, key, (_t77.time(), payload))             # FIX-99
    if not ok:
        return jsonify({'ok': False, **payload}), 200
    return jsonify({'ok': True, 'cached': False,
                    'note': ('Scores rule-based composite hain (kitne indicators '
                             'measure hue wo "basis" me hai) — prediction ya '
                             'probability nahi.'),
                    **payload})


@app.route('/timeframes')
def timeframes_page():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)),
                              'Timeframes.html')


# FIX-79: SCREENER AUTO-REFRESH
# Ab tak scan_results.json sirf tab banta tha jab user manually
# `python nifty_scanner.py` chalata. Measured: TradingView up ho to ~7s,
# TradingView down (Yahoo fallback) to ~39s.
#
# Do raste diye hain:
#   • MANUAL  — Screener page par "Refresh" button -> POST /api/screener/refresh
#   • AUTO    — server start par, agar file missing ya STOCKAI_SCAN_MAX_AGE_HOURS
#               se purani ho, to background me scan (page block nahi hota)
#
# Subprocess kyun (in-process import nahi): scanner apne threads chalata hai aur
# ~40 lines print karta hai. Crash hone par server nahi marna chahiye — isolate
# rakho. Yahi verify_fixes.py:100 bhi karta hai.
import subprocess as _sp79
import sys as _sys79   # app.py top par sys import nahi karta — probe me
                       # NameError mila, isliye yahan local alias

_SCAN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'nifty_scanner.py')
_SCAN_TIMEOUT = 900          # 15 min. 39s wale scan ke liye bahut zyada slack.
_scan_thread = None
_SCAN_STATE = {'running': False, 'last_ok': None, 'last_at': None,
               'last_seconds': None, 'last_error': None, 'last_reason': None,
               'runs': 0}
_scan_lock = threading.Lock()


def _scan_autorefresh_on():
    """Default ON. .env me STOCKAI_SCAN_AUTOREFRESH=0 se band."""
    return str(os.environ.get('STOCKAI_SCAN_AUTOREFRESH', '1')).strip().lower() \
        not in ('0', 'false', 'no', 'off')


def _scan_max_age_hours():
    """Kitni purani hone par auto-refresh. Default 12 ghante."""
    try:
        v = float(os.environ.get('STOCKAI_SCAN_MAX_AGE_HOURS', '12'))
        return v if v > 0 else 12.0
    except (TypeError, ValueError):
        return 12.0


def _scan_age_hours():
    """File kitni purani hai (ghante me). Missing ho to None."""
    try:
        return (_t77.time() - os.path.getmtime(_SCAN_FILE)) / 3600.0
    except OSError:
        return None


def _run_scan_subprocess(reason):
    """Asli scan. Blocking hai — hamesha background thread se call karo."""
    # NOTE: `global _SCAN_STATE` yahan zaroori NAHI tha (pyflakes ne pakda) —
    # hum dict ko rebind nahi karte, sirf .update() se mutate karte hain.
    t0 = _t77.time()
    ok, err = False, None
    try:
        p = _sp79.run([_sys79.executable, _SCAN_SCRIPT],
                      cwd=os.path.dirname(_SCAN_SCRIPT),
                      capture_output=True, text=True, encoding='utf-8', errors='replace',
                      env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'},
                      timeout=_SCAN_TIMEOUT)
        ok = (p.returncode == 0)
        if not ok:
            tail = ((p.stderr or '') + (p.stdout or '')).strip()
            err = (tail[-500:] or f'exit code {p.returncode}')
    except _sp79.TimeoutExpired:
        err = f'timeout ({_SCAN_TIMEOUT}s)'
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
    secs = round(_t77.time() - t0, 1)
    with _scan_lock:
        _SCAN_STATE.update({'running': False, 'last_ok': ok, 'last_at': _t77.time(),
                            'last_seconds': secs, 'last_error': err,
                            'last_reason': reason,
                            'runs': _SCAN_STATE['runs'] + 1})
    _SCR_CACHE['mtime'] = None          # FIX-76 cache invalidate
    print(f"{'✅' if ok else '❌'} Screener scan ({reason}) — {secs}s"
          + ('' if ok else f" — {str(err)[:160]}"))
    return ok


def _spawn_scan(reason):
    """Background scan shuru karo. Already chal raha ho to False."""
    global _scan_thread
    with _scan_lock:
        if _SCAN_STATE['running']:
            return False
        _SCAN_STATE['running'] = True
        _SCAN_STATE['last_reason'] = reason
    _scan_thread = threading.Thread(target=_run_scan_subprocess, args=(reason,),
                                    name='screener-scan', daemon=True)
    _scan_thread.start()
    return True


def _scan_status():
    with _scan_lock:
        st = dict(_SCAN_STATE)
    st['age_hours'] = (round(a, 2) if (a := _scan_age_hours()) is not None else None)
    st['autorefresh'] = _scan_autorefresh_on()
    st['max_age_hours'] = _scan_max_age_hours()
    st['exists'] = _scan_age_hours() is not None
    return st


def maybe_autorefresh_scan():
    """Server start par call hota hai. Missing/purani file -> background scan."""
    if not _scan_autorefresh_on():
        print("👉 Screener auto-refresh OFF (STOCKAI_SCAN_AUTOREFRESH=0)")
        return False
    age = _scan_age_hours()
    mx = _scan_max_age_hours()
    if age is None:
        why = 'scan_results.json nahi mila'
    elif age > mx:
        why = f'scan {age:.1f}h purana hai (limit {mx:g}h)'
    else:
        print(f"👉 Screener data theek hai ({age:.1f}h purana, limit {mx:g}h) — scan skip")
        return False
    print(f"👉 Screener auto-refresh: {why} — background me scan shuru")
    return _spawn_scan('startup-auto')


@app.route('/api/screener/refresh', methods=['POST'])
def api_screener_refresh():
    """POST-only — link prefetch/crawler se accidental scan na ho."""
    if _spawn_scan('manual'):
        return jsonify({'ok': True, 'started': True, 'status': _scan_status()})
    return jsonify({'ok': False, 'started': False, 'already_running': True,
                    'error': 'Scan pehle se chal raha hai — thoda ruk kar dobara',
                    'status': _scan_status()}), 409


@app.route('/api/screener/status')
def api_screener_status():
    """Page isko poll karta hai jab scan chal raha ho."""
    return jsonify({'ok': True, 'status': _scan_status()})


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
    if hasattr(g, 'request_started'):
        resp.headers['Server-Timing'] = f'total;dur={(time.perf_counter()-g.request_started)*1000:.1f}'
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



# ═══════════════════════════════════════════════════════════════════════════
#  FIX-89b/90/91/92 — BACKTEST · WATCHLIST · HEATMAP · ALERTS
# ═══════════════════════════════════════════════════════════════════════════
import statistics as _st89
import math as _m89
import threading as _th89

_ROOT89 = os.path.dirname(os.path.abspath(__file__))
_BACKTEST_FILES = {'NSE': os.path.join(_ROOT89, 'backtest_history_nse.json'),
                   'BSE': os.path.join(_ROOT89, 'backtest_history_bse.json')}
_BACKTEST_CACHE = {}
_STORE_LOCK = ProcessRLock(os.path.join(_ROOT89, '.stockai-store.lock'))
# FIX-98: identifier ki length cap — 200k char ka "symbol" accept ho raha tha
# (measure kiya) aur per-user JSON file ko bloat kar sakta tha. Truncate karke
# galat symbol banana usse bura hai, isliye reject.
SYMBOL_MAX_LEN = 24
WATCHLIST_FILE = os.path.join(_ROOT89, 'watchlist.json')
ALERTS_FILE = os.path.join(_ROOT89, 'alerts.json')
_MIN_BAND_N = 30          # isse kam samples par "insufficient" — statistically jhooth nahi bolenge
_T_SIG = 2.0              # |t| >= 2 ~ 95% (rough, normal approx)


def _bt_load(exchange):
    """Backtest artifact padho (memoised). Missing ho to None — 500 nahi."""
    ex = str(exchange or 'NSE').strip().upper()
    if ex not in _BACKTEST_FILES:
        return None
    try:
        stat = os.stat(_BACKTEST_FILES[ex])
        key = (ex, stat.st_mtime_ns, stat.st_size)
        hit = _BACKTEST_CACHE.get(key)
        if hit is not None:
            return hit
        with open(_BACKTEST_FILES[ex], encoding='utf-8') as stream:
            art = json.load(stream)
        if not isinstance(art, dict):
            return None
    except FileNotFoundError:
        return None
    except Exception:                                            # noqa: BLE001
        return None
    _cache_put(_BACKTEST_CACHE, key, art)                           # FIX-99
    return art


def _bt_bands(art):
    """10-point bands -> n, mean/SE/t per horizon, %positive. Honest stats."""
    horizons = [str(h) for h in (art.get('horizons') or [1, 3, 5, 10, 20])]
    groups = {}
    for r in (art.get('rows') or []):
        sc = r.get('score')
        if not isinstance(sc, (int, float)):
            continue
        b = '%d-%d' % ((int(max(0, min(100, sc))) // 10) * 10,
                       (int(max(0, min(100, sc))) // 10) * 10 + 10)
        groups.setdefault(b, []).append(r)
    out = []
    for b in sorted(groups, key=lambda x: int(x.split('-')[0])):
        rows = groups[b]
        rec = {'band': b, 'n': len(rows), 'thin': len(rows) < _MIN_BAND_N, 'h': {}}
        for h in horizons:
            v = [r['fwd'][h] for r in rows
                 if isinstance(r.get('fwd'), dict) and isinstance(r['fwd'].get(h), (int, float))]
            if len(v) < 2:
                rec['h'][h] = {'n': len(v), 'mean': None, 'se': None, 't': None,
                               'median': None, 'pos_pct': None}
                continue
            mean = _st89.mean(v)
            sd = _st89.stdev(v)
            se = sd / _m89.sqrt(len(v)) if len(v) > 1 else None
            rec['h'][h] = {
                'n': len(v),
                'mean': round(mean, 3),
                'se': round(se, 3) if se else None,
                't': round(mean / se, 2) if se else None,
                'median': round(_st89.median(v), 3),
                'pos_pct': round(100.0 * sum(1 for x in v if x > 0) / len(v), 1),
            }
        out.append(rec)
    return out


@app.route('/api/backtest')
def api_backtest():
    """Score-band forward returns — real historical distribution, prediction nahi."""
    ex = (request.args.get('ex') or 'NSE').strip().upper()
    if ex not in ('NSE', 'BSE'):
        ex = 'NSE'
    art = _bt_load(ex)
    if not art or art.get('formula_hash') != score_formula_hash():
        return jsonify({'ok': False,
                        'error': 'Missing or incompatible score history: backtest_history_%s.json nahi mila.' % ex.lower(),
                        'hint': 'Banane ke liye: python tools/build_backtest_history.py --exchange %s' % ex}), 200
    return jsonify({'ok': True, 'exchange': ex, 'bands': _bt_bands(art),
                    'meta': {k: art.get(k) for k in
                             ('generated_at', 'source', 'model', 'sessions', 'samples',
                              'first_session', 'last_session', 'lookback_bars')},
                    'universe': art.get('universe'),
                    'horizons': art.get('horizons'),
                    'min_band_n': _MIN_BAND_N, 't_sig': _T_SIG,
                    # Ye disclosure UI me hamesha dikhegi — score predictive nahi hai.
                    'overlapping_observations': True,
                    'inference_validated': False,
                    'verdict': ('Score relative/confluence RANK hai, predictor nahi. '
                                'Gross overlapping forward returns descriptive hain. '
                                'Naive SE/t aur |t| >= 2 ko significance ya executable edge na samjhein; '
                                'dependence, costs and multiple testing are not controlled. '
                                'Thin bands have fewer than %d observations.' % _MIN_BAND_N)}), 200


# ── shared tiny JSON store (watchlist + alerts) ─────────────────────────────
def _store_read(path, default):
    try:
        with _STORE_LOCK:
            with open(path, encoding='utf-8') as handle:
                data = json.load(handle)
                if not isinstance(data, type(default)):
                    raise ValueError('unexpected store shape')
                return data
    except FileNotFoundError:
        return default
    except Exception as exc:
        from werkzeug.exceptions import Conflict
        raise Conflict('Local JSON store unreadable; restore a backup before writing') from exc


def _store_write(path, data):
    tmp = path + '.tmp'
    with _STORE_LOCK:
        f = open(tmp, 'w', encoding='utf-8')
        try:
            f.write(json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False))
            f.flush()
            os.fsync(f.fileno())
        finally:
            f.close()
        os.replace(tmp, path)


# ── WATCHLIST ───────────────────────────────────────────────────────────────
@app.route('/api/watchlist', methods=['GET'])
def api_watchlist_get():
    items = _store_read(WATCHLIST_FILE, [])
    return jsonify({'ok': True, 'items': items if isinstance(items, list) else []}), 200


@app.route('/api/watchlist', methods=['POST'])
def api_watchlist_post():
    body = request.get_json(silent=True) or {}
    sym = str(body.get('symbol') or '').strip().upper()
    ex = str(body.get('exchange') or 'NSE').strip().upper()
    if not sym:
        return jsonify({'ok': False, 'error': 'symbol chahiye'}), 400
    if ex not in ('NSE', 'BSE'):
        return jsonify({'ok': False, 'error': "exchange 'NSE' ya 'BSE' hona chahiye"}), 400
    if len(sym) > SYMBOL_MAX_LEN:                              # FIX-98
        return jsonify({'ok': False,
                        'error': f'symbol {SYMBOL_MAX_LEN} char se lamba nahi ho sakta'}), 400
    with _STORE_LOCK:                                          # FIX-98: RMW atomic
        # non-dict rows filter — warna hand-edited file par i.get() se 500 aata tha
        items = [i for i in _store_read(WATCHLIST_FILE, []) if isinstance(i, dict)]
        if any(i.get('symbol') == sym and i.get('exchange') == ex for i in items):
            return jsonify({'ok': False,
                            'error': '%s (%s) pehle se watchlist me hai' % (sym, ex)}), 409
        items.append({'symbol': sym, 'exchange': ex, 'note': str(body.get('note') or '')[:120],
                      'added_at': datetime.now(IST).strftime('%Y-%m-%d %H:%M')})
        _store_write(WATCHLIST_FILE, items)
    return jsonify({'ok': True, 'items': items}), 200


@app.route('/api/watchlist', methods=['DELETE'])
def api_watchlist_delete():
    sym = (request.args.get('symbol') or '').strip().upper()
    ex = (request.args.get('exchange') or '').strip().upper()
    with _STORE_LOCK:                                          # FIX-98: RMW atomic
        items = [i for i in _store_read(WATCHLIST_FILE, []) if isinstance(i, dict)]
        keep = [i for i in items
                if not (i.get('symbol') == sym and (i.get('exchange') or 'NSE') == ex)]
        if len(keep) == len(items):
            return jsonify({'ok': False, 'error': 'mila nahi'}), 404
        _store_write(WATCHLIST_FILE, keep)
    return jsonify({'ok': True, 'items': keep}), 200


# ── SECTOR HEATMAP ──────────────────────────────────────────────────────────
@app.route('/api/heatmap')
def api_heatmap():
    """Sector-wise performance scan_results.json se. Kuch bhi invent nahi hota —
    jis row me sector nahi hai wo 'Unknown' me jaata hai, chhupaya nahi jaata."""
    path = os.path.join(_ROOT89, 'scan_results.json')
    if not os.path.exists(path):
        return jsonify({'ok': False, 'error': 'scan_results.json nahi mila',
                        'hint': 'python nifty_scanner.py chalao, ya Screener par Refresh dabao'}), 200
    try:
        raw = json.loads(open(path, encoding='utf-8').read())
    except Exception as e:                                       # noqa: BLE001
        return jsonify({'ok': False, 'error': 'scan_results.json parse fail: %s' % e}), 200
    rows = raw if isinstance(raw, list) else (raw.get('results') or raw.get('rows') or [])
    sectors = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        sec = (r.get('sector') or 'Unknown') or 'Unknown'
        ch = r.get('change_pct')
        if not isinstance(ch, (int, float)):
            continue
        sectors.setdefault(sec, []).append({'symbol': r.get('symbol'), 'change_pct': ch,
                                            'price': r.get('price'),
                                            'ensemble': r.get('ensemble'),
                                            'rsi': r.get('rsi')})
    out = []
    for sec, rs in sectors.items():
        chs = [x['change_pct'] for x in rs]
        out.append({'sector': sec, 'n': len(rs),
                    'avg_change': round(_st89.mean(chs), 2),
                    'median_change': round(_st89.median(chs), 2),
                    'adv': sum(1 for c in chs if c > 0),
                    'dec': sum(1 for c in chs if c < 0),
                    'unch': sum(1 for c in chs if c == 0),
                    'best': max(rs, key=lambda x: x['change_pct']),
                    'worst': min(rs, key=lambda x: x['change_pct']),
                    'stocks': sorted(rs, key=lambda x: -x['change_pct'])})
    out.sort(key=lambda x: -x['avg_change'])
    return jsonify({'ok': True, 'sectors': out, 'total_stocks': len(rows),
                    'generated_at': (raw.get('generated_at') if isinstance(raw, dict) else None),
                    'note': ('Sector average sirf scanned stocks ka hai — poore sector ka '
                             'index nahi. Chhote n wale sectors par average noise hota hai.')}), 200


# ── PRICE ALERTS ────────────────────────────────────────────────────────────
def _alerts_list():
    a = _store_read(ALERTS_FILE, [])
    return a if isinstance(a, list) else []


@app.route('/api/alerts', methods=['GET'])
def api_alerts_get():
    return jsonify({'ok': True, 'alerts': _alerts_list()}), 200


@app.route('/api/alerts', methods=['POST'])
def api_alerts_post():
    body = request.get_json(silent=True) or {}
    sym = str(body.get('symbol') or '').strip().upper()
    ex = str(body.get('exchange') or 'NSE').strip().upper()
    cond = str(body.get('condition') or '').strip().lower()
    try:
        level = float(body.get('level'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'level number hona chahiye'}), 400
    if not sym:
        return jsonify({'ok': False, 'error': 'symbol chahiye'}), 400
    if ex not in ('NSE', 'BSE'):
        return jsonify({'ok': False, 'error': "exchange 'NSE' ya 'BSE' hona chahiye"}), 400
    if cond not in ('above', 'below'):
        return jsonify({'ok': False, 'error': "condition 'above' ya 'below' hona chahiye"}), 400
    if level <= 0:
        return jsonify({'ok': False, 'error': 'level 0 se bada hona chahiye'}), 400
    if len(sym) > SYMBOL_MAX_LEN:                              # FIX-98
        return jsonify({'ok': False,
                        'error': f'symbol {SYMBOL_MAX_LEN} char se lamba nahi ho sakta'}), 400
    with _STORE_LOCK:                                          # FIX-98: RMW atomic
        items = [a for a in _alerts_list() if isinstance(a, dict)]
        items.append({'id': uuid.uuid4().hex, 'symbol': sym, 'exchange': ex,
                      'condition': cond, 'level': level, 'fired': False,
                      'fired_at': None, 'created_at': datetime.now(IST).strftime('%Y-%m-%d %H:%M')})
        _store_write(ALERTS_FILE, items)
    return jsonify({'ok': True, 'alerts': items}), 200


@app.route('/api/alerts', methods=['DELETE'])
def api_alerts_delete():
    aid = str(request.args.get('id') or '').strip()
    if not re.fullmatch(r'(?:[0-9]{1,40}|[0-9a-f]{32})', aid):
        return jsonify({'ok': False, 'error': 'id required'}), 400
    with _STORE_LOCK:                                          # FIX-98: RMW atomic
        items = [a for a in _alerts_list() if isinstance(a, dict)]
        keep = [a for a in items if str(a.get('id')) != aid]
        if len(keep) == len(items):
            return jsonify({'ok': False, 'error': 'alert nahi mila'}), 404
        _store_write(ALERTS_FILE, keep)
    return jsonify({'ok': True, 'alerts': keep}), 200


@app.route('/api/alerts/check', methods=['POST'])
def api_alerts_check():
    """Pending alerts ko current quote se check karo. Frontend poll karta hai —
    server khud background loop nahi chalata (resource leak se bachne ke liye)."""
    snapshot = [a for a in _alerts_list() if isinstance(a, dict)]
    pending = [a for a in snapshot if not a.get('fired')]
    checked, errors = 0, []
    now = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
    seen = {}          # id -> update; network calls lock ke BAHAR hote hain
    for a in pending:
        sym, ex = a.get('symbol'), (a.get('exchange') or 'NSE').upper()
        try:
            q = get_live_quote(sym, prefer_exch=ex)
        except Exception as e:                                   # noqa: BLE001
            errors.append('%s: %s' % (sym, type(e).__name__))
            q = None
        if not q or not isinstance(q.get('price'), (int, float)):
            errors.append('%s: quote nahi mila' % sym)
            continue
        if (not q.get('is_realtime') or q.get('stale')
                or exchange_from_source(q.get('source')) != ex
                or not math.isfinite(float(q['price'])) or q['price'] <= 0
                or not is_market_open(exchange=ex)):
            errors.append(f'{sym}: live same-exchange quote unavailable; alert not evaluated')
            continue
        checked += 1
        price = float(q['price'])
        upd = {'last_price': price, 'checked_at': now}
        hit = (price >= a['level']) if a.get('condition') == 'above' else (price <= a['level'])
        if hit:
            upd.update({'fired': True, 'fired_at': now, 'fired_price': price})
        seen[a.get('id')] = upd
    # FIX-98: write lock ke andar aur FRESH list par. Pehle network calls ke
    # dauraan wala snapshot hi write hota tha — beech me add/delete hua alert
    # kho jaata, aur do parallel /check ek doosre ka update mita dete.
    fired_now = []
    with _STORE_LOCK:
        items = [a for a in _alerts_list() if isinstance(a, dict)]
        for a in items:
            upd = seen.get(a.get('id'))
            if upd and not a.get('fired'):
                a.update(upd)
                if upd.get('fired'):
                    fired_now.append(a)
        _store_write(ALERTS_FILE, items)
    return jsonify({'ok': True, 'alerts': items, 'checked': checked,
                    'fired_now': fired_now,
                    'errors': errors[:8]}), 200


# ── FIX-96: TRADE JOURNAL ───────────────────────────────────────────────────
# 2026 ke pre-trade checklist ka aakhri item "trade logged?" hota hai, aur
# journal hi akela aisa record hai jo aapki ASLI execution measure karta hai —
# backtest edge ka estimate deta hai, journal actual (execution + psychology).
# Design rules (2026 journal guides: quantum-algo / bouncetrade / journalplus /
# tradetally — sab isi par agree karte hain):
#   • primary metric = R-multiple (position-size normalised), win rate akela
#     misleading hai (40% win rate + 2.5R winners = profitable)
#   • expectancy = sum(R)/n — edge hai ya nahi, yahi batata hai
#   • -1R se bura result = stop miss/gap/widened SL → alag count hota hai
#   • n chhota ho to stats ko ANECDOTE kaha jaata hai — chhupaya nahi jaata
# Aur sabse zaroori: yahan koi predicted/assumed number nahi hai. Sirf wo trades
# count hote hain jo user ne khud log kiye. Model/score ki "accuracy" nahi.
JOURNAL_FILE = os.path.join(_ROOT89, 'trade_journal.json')

JN_ANECDOTE = 20      # <20 closed trades = anecdote (variance dominate karta hai)
JN_ROUGH = 30         # 30 = expectancy ka rough minimum
JN_RELIABLE = 100     # 100+ = reliable sample
JN_STOP_R = -1.0      # exact stop-out = -1R


def _journal_read():
    """(items, corrupt, dropped).

    Corrupt file par chup-chaap khali list NAHI dikhate — warna user ko lagega
    journal khaali hai aur wo dobara save karke purana data overwrite kar dega.
    FIX-97a: valid JSON list ho par usme non-dict rows hon (hand-edited file) to
    wo rows DROP hote hain aur count `dropped` me jaata hai. Pehle ye rows aage
    jaakar `t.get('id')` par AttributeError dete the → DELETE 500. Chup-chaap
    drop nahi karte: count API response tak jaata hai.
    """
    try:
        with open(JOURNAL_FILE, encoding='utf-8') as f:
            raw = f.read()
    except FileNotFoundError:
        return [], False, 0
    except Exception:                                            # noqa: BLE001
        return [], True, 0
    try:
        data = json.loads(raw)
    except Exception:                                            # noqa: BLE001
        return [], True, 0
    if not isinstance(data, list):
        return [], True, 0
    rows = [t for t in data if isinstance(t, dict)]
    return rows, False, (len(data) - len(rows))


def journal_cost(entry, exit_price, qty, mode, side):
    cfg = _cost_config()
    buy, sell = ('buy_intraday', 'sell_intraday') if mode == 'intraday' else ('buy', 'sell')
    first, second = (buy, sell) if side == 'LONG' else (sell, buy)
    return round(cfg.cost(entry*qty, first) + cfg.cost(exit_price*qty, second), 2)


def journal_r_multiple(entry, stop, exit_price, side):
    """R = outcome / initial risk. Full stop-out = exactly -1R.

    Invalid record (stop entry ke galat taraf, ya koi number nahi) par None —
    jhootha 0.0 nahi.
    """
    try:
        entry = float(entry)
        stop = float(stop)
        exit_price = float(exit_price)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) and x > 0 for x in (entry, stop, exit_price)) or str(side).upper() not in ('LONG', 'SHORT'):
        return None
    short = str(side).upper() == 'SHORT'
    risk = (stop - entry) if short else (entry - stop)
    if risk <= 0:
        return None
    gain = (entry - exit_price) if short else (exit_price - entry)
    return round(gain / risk, 3)


def _journal_confidence(n):
    if n < JN_ANECDOTE:
        return 'anecdote'
    if n < JN_ROUGH:
        return 'thin'
    if n < JN_RELIABLE:
        return 'rough'
    return 'larger_sample'


_JN_NOTE = {
    'anecdote': ('%d closed trades — 20 se kam, isliye ye ANECDOTE hai: ek-do '
                 'trade poora average hila dete hain. Pattern mat nikalo.'),
    'thin':     ('%d closed trades — direction dikhta hai, par numbers abhi '
                 'noise hain (20–29).'),
    'rough':    ('%d closed trades — expectancy ka rough minimum (30). Reliable '
                 'kehne ke liye 100+ chahiye.'),
    'larger_sample': ('%d closed trades — larger sample; independence, regime coverage and net edge NOT established.'),
}


def journal_stats(items):
    """Sirf CLOSED trades se stats. Open trades count me dikhte hain, mix nahi hote."""
    rows = [t for t in items if isinstance(t, dict)]
    closed = [t for t in rows if isinstance(t.get('r'), (int, float))]
    rs = [float(t['r']) for t in closed]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    conf = _journal_confidence(n)
    net_rs = [float(t['r_net']) for t in closed if isinstance(t.get('r_net'), (int, float)) and math.isfinite(t['r_net'])]
    return {
        'expectancy_net_r': round(sum(net_rs)/len(net_rs), 4) if net_rs else None,
        'net_r_samples': len(net_rs),
        'open': len(rows) - n,
        'closed': n,
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(len(wins) / n * 100, 1) if n else None,
        'avg_win_r': round(gross_win / len(wins), 3) if wins else None,
        'avg_loss_r': round(-gross_loss / len(losses), 3) if losses else None,
        'expectancy_r': round(sum(rs) / n, 3) if n else None,
        'profit_factor': round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        'worst_r': min(rs) if rs else None,
        'stop_breaches': sum(1 for r in rs if r < JN_STOP_R - 1e-9),
        'net_pnl': round(sum(float(t.get('pnl_net') or 0.0) for t in closed), 2),
        'confidence': conf,
        'thresholds': {'anecdote_below': JN_ANECDOTE, 'rough_from': JN_ROUGH,
                       'larger_sample_from': JN_RELIABLE},
        'disclosure': (_JN_NOTE[conf] % n) + (' Ye aapke khud log kiye trades ka '
                       'record hai — kisi model/score ki accuracy nahi, aur na '
                       'hi koi prediction.'),
    }


@app.route('/api/journal', methods=['GET'])
def api_journal_get():
    items, corrupt, dropped = _journal_read()
    return jsonify({'ok': True, 'items': items, 'stats': journal_stats(items),
                    'corrupt': corrupt, 'dropped_rows': dropped,
                    'cost_note': ('net P&L round-trip cost ke baad hai '
                                  '(research/costs.py, notional-aware)')}), 200


@app.route('/api/journal', methods=['POST'])
def api_journal_post():
    body = request.get_json(silent=True) or {}
    sym = str(body.get('symbol') or '').strip().upper()
    side = str(body.get('side') or '').strip().upper()
    if not sym:
        return jsonify({'ok': False, 'error': 'symbol zaroori hai'}), 422
    if len(sym) > SYMBOL_MAX_LEN:
        return jsonify({'ok': False,
                        'error': f'symbol {SYMBOL_MAX_LEN} char se lamba nahi ho sakta'}), 422
    if side not in ('LONG', 'SHORT'):
        return jsonify({'ok': False, 'error': 'side LONG ya SHORT hona chahiye'}), 422
    try:
        entry = float(body.get('entry'))
        stop = float(body.get('stop'))
        qty = int(body.get('qty'))
    except (TypeError, ValueError):
        return jsonify({'ok': False,
                        'error': 'entry/stop number aur qty integer hona chahiye'}), 422
    if entry <= 0 or stop <= 0 or qty <= 0:
        return jsonify({'ok': False,
                        'error': 'entry/stop/qty teeno positive hone chahiye'}), 422
    # "Stop before entry" — 2026 checklists ka non-negotiable. Stop galat taraf
    # ho to risk negative banta hai aur R-multiple ka sign ulta ho jaata hai,
    # isliye yahan hi reject.
    if (stop >= entry) if side == 'LONG' else (stop <= entry):
        return jsonify({'ok': False, 'error': (
            'LONG me stop entry se NEECHE hona chahiye'
            if side == 'LONG' else 'SHORT me stop entry se UPAR hona chahiye')}), 422
    exit_price = body.get('exit')
    if exit_price in (None, ''):
        exit_price = None
    else:
        try:
            exit_price = float(exit_price)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'exit number hona chahiye'}), 422
        if exit_price <= 0:
            return jsonify({'ok': False, 'error': 'exit positive hona chahiye'}), 422

    mode = str(body.get('mode') or 'intraday').strip().lower()
    if mode not in ('intraday', 'delivery'):
        return jsonify({'ok': False, 'error': 'mode intraday ya delivery hona chahiye'}), 422
    notional = entry * qty
    cost = journal_cost(entry, exit_price if exit_price is not None else entry, qty, mode, side)
    r = journal_r_multiple(entry, stop, exit_price, side) if exit_price else None
    pnl_gross = pnl_net = None
    if exit_price:
        pnl_gross = round(((exit_price - entry) if side == 'LONG'
                           else (entry - exit_price)) * qty, 2)
        pnl_net = round(pnl_gross - cost, 2)
    rec = {
        'id': uuid.uuid4().hex,
        'symbol': sym,
        'exchange': str(body.get('exchange') or 'NSE').strip().upper(),
        'side': side,
        'entry': entry, 'stop': stop, 'qty': qty,
        'exit': exit_price,
        'risk_per_share': round(abs(entry - stop), 4),
        'risk_rupees': round(abs(entry - stop) * qty, 2),
        'r': r,
        'r_net': round(pnl_net / (abs(entry-stop)*qty), 6) if pnl_net is not None else None,
        'cost_basis': 'NSE cash estimate; BSE tariff/DP/borrow/actual broker fees not verified',
        'cost_status': 'both recorded notionals' if exit_price is not None else 'reference round-trip at entry; not realized cost',
        'pnl_gross': pnl_gross,
        'pnl_net': pnl_net,
        'cost': cost,
        'mode': mode,
        'setup': str(body.get('setup') or '')[:60],
        'note': str(body.get('note') or '')[:240],
        'logged_at': _naive_ist(None).strftime('%Y-%m-%d %H:%M'),
    }
    # FIX-98: poora read-modify-write lock ke ANDAR. Pehle sirf _store_write
    # locked tha, isliye do parallel POST ek doosre ka record mita dete the —
    # measure kiya: 12 parallel POST → file me sirf 4 records, 8 lost writes.
    with _STORE_LOCK:
        items, corrupt, dropped = _journal_read()
        if corrupt:
            # Fail-closed: corrupt file ke upar likhna = purana journal gayab.
            return jsonify({'ok': False, 'error': (
                'trade_journal.json corrupt hai — us file ko rename/delete karein, '
                'phir dobara save karein (purana data overwrite na ho isliye write '
                'block kiya)')}), 409
        if len(items) >= 10000:
            return jsonify({'ok': False, 'error': 'journal limit reached; archive records first'}), 413
        items.append(rec)
        _store_write(JOURNAL_FILE, items)
    return jsonify({'ok': True, 'item': rec, 'items': items,
                    'stats': journal_stats(items), 'corrupt': False,
                    'dropped_rows': dropped}), 200


@app.route('/api/journal/<tid>/close', methods=['PATCH'])
def api_journal_close(tid):
    body = request.get_json()
    exit_price = sfx(body.get('exit'))
    if exit_price is None or exit_price <= 0:
        return jsonify({'ok': False, 'error': 'finite positive exit required'}), 422
    with _STORE_LOCK:
        items, corrupt, dropped = _journal_read()
        if corrupt:
            return jsonify({'ok': False, 'error': 'journal corrupt; write blocked'}), 409
        row = next((t for t in items if str(t.get('id')) == tid), None)
        if row is None:
            return jsonify({'ok': False, 'error': 'trade not found'}), 404
        if row.get('exit') is not None:
            if row['exit'] != exit_price:
                return jsonify({'ok': False, 'error': 'already closed at a different price'}), 409
        else:
            r = journal_r_multiple(row.get('entry'), row.get('stop'), exit_price, row.get('side'))
            if r is None:
                return jsonify({'ok': False, 'error': 'invalid stored risk fields; repair required'}), 409
            qty = row.get('qty')
            if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
                return jsonify({'ok': False, 'error': 'invalid stored quantity'}), 409
            cost = journal_cost(row['entry'], exit_price, qty, row.get('mode', 'intraday'), row['side'])
            pnl = (exit_price-row['entry']) * qty * (1 if row['side']=='LONG' else -1)
            risk = abs(row['entry']-row['stop'])*qty
            row.update(exit=exit_price, r=r, pnl_gross=round(pnl,2), pnl_net=round(pnl-cost,2),
                       cost=cost, cost_status='both recorded notionals', r_net=round((pnl-cost)/risk,6),
                       closed_at=_naive_ist().isoformat(timespec='seconds'))
            _store_write(JOURNAL_FILE, items)
    return jsonify({'ok': True, 'items': items, 'stats': journal_stats(items), 'dropped_rows': dropped})


@app.route('/api/journal', methods=['DELETE'])
def api_journal_delete():
    tid = str(request.args.get('id') or '').strip()
    with _STORE_LOCK:                                          # FIX-98: RMW atomic
        items, corrupt, dropped = _journal_read()
        if corrupt:
            return jsonify({'ok': False, 'error': 'trade_journal.json corrupt hai'}), 409
        # items ab sirf dicts hain (FIX-97a) — pehle yahan non-dict row par
        # AttributeError se 500 aata tha.
        keep = [t for t in items if str(t.get('id')) != tid]
        if len(keep) == len(items):
            return jsonify({'ok': False, 'error': 'id nahi mili'}), 404
        _store_write(JOURNAL_FILE, keep)
    return jsonify({'ok': True, 'items': keep, 'stats': journal_stats(keep),
                    'dropped_rows': dropped}), 200


# ── page routes ─────────────────────────────────────────────────────────────
for _pg89 in ('Backtest', 'Watchlist', 'Heatmap', 'Alerts'):
    def _mk89(name):
        def _view():
            return send_from_directory(_ROOT89, name + '.html')
        _view.__name__ = 'page_' + name.lower()
        return _view
    app.add_url_rule('/' + _pg89.lower(), 'page_' + _pg89.lower(), _mk89(_pg89))


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
              f"(aaj {'HOLIDAY — market band' if is_market_holiday() or _naive_ist().weekday() > 4 else 'scheduled trading day'})")
    maybe_autorefresh_scan()
    if SECURITY['TOKEN']:
        print("🔒 Token auth ON — token hidden; use configured token to sign in")
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
        print(f"   {'🖥 ' if i == 0 else '📱'} {re.sub(r'([?&]token=)[^&]+', r'\1<configured-token>', u)}")
    if SECURITY['TOKEN']:
        print("   ℹ️  Cookie set hone ke baad ye saaf link chalegi (24 ghante):")
        for u in plain:
            print(f"      {u.split('?')[0]}")
        print("   🔒 Console log me token mask hota hai (token=***)")
    print("=" * 78)
    if urls and auto_open_enabled():
        try:
            webbrowser.open(urls[0])   # STOCKAI_AUTO_OPEN=0 se band hota hai
        except Exception:
            pass
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
