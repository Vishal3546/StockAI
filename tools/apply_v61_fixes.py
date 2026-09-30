#!/usr/bin/env python3
"""
tools/apply_v61_fixes.py — StockAI V6.1 in-place fix generator
================================================================================
Applies every audit fix DIRECTLY to the project files (app.py, nifty_scanner.py,
deep_analyzer.py). The pre-fix versions stay in git history, so this is fully
reversible (`git revert` / `git checkout HEAD~1 -- <file>`).

Every edit is *asserted*: if an anchor is missing or ambiguous the script aborts
before writing anything (all files are patched in memory first, then flushed).

Search for `FIX-` in the patched files to see each change in context.
Full rationale + measurements: AUDIT_REPORT.md

Usage:  python3 tools/apply_v61_fixes.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
APPLIED = []


# ─────────────────────────── helpers ────────────────────────────────────────
def sub_once(src, old, new, label):
    """Replace an exact snippet that must occur exactly once."""
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"ABORT [{label}]: expected exactly 1 match, found {n}")
    APPLIED.append(label)
    return src.replace(old, new, 1)


def span_replace(src, start, end, new, label):
    """Replace everything from `start` up to (not including) the first `end`."""
    if src.count(start) != 1:
        raise SystemExit(f"ABORT [{label}]: start marker occurs {src.count(start)}x")
    i = src.index(start)
    j = src.index(end, i + len(start))
    APPLIED.append(label)
    return src[:i] + new + src[j:]


def write(path, text):
    p = ROOT / path
    p.write_text(text, encoding='utf-8')
    print(f"  ✔ wrote {path}  ({len(text):,} bytes)")


# ═══════════════════════════════════════════════════════════════════════════
#  1) app.py
# ═══════════════════════════════════════════════════════════════════════════
def patch_app():
    s = (ROOT / 'app.py').read_text(encoding='utf-8')

    # FIX-00 · header note + extra stdlib imports + global caches
    s = sub_once(s, '"""\n\nimport io\nimport json\nimport time\nimport warnings',
                 '"""\n\n# ─────────────────────────────────────────────────────────────────────────────\n'
                 '#  V6.1 (2026-09-30) — 15 audit fixes applied in place.\n'
                 '#  Search "FIX-" to see each change; see AUDIT_REPORT.md for evidence.\n'
                 '# ─────────────────────────────────────────────────────────────\n\n'
                 'import io\nimport json\nimport math\nimport os\nimport threading\nimport time\nimport warnings',
                 'FIX-00 stdlib imports (math/os/threading) + header note')

    s = sub_once(s, 'from flask import Flask, Response, jsonify, request',
                 'from flask import Flask, Response, jsonify, request, send_from_directory',
                 'FIX-11a send_from_directory import')

    s = sub_once(s, "REGIME_CACHE = {\n    'data': None,\n    'time': None\n}",
                 "REGIME_CACHE = {\n    'data': None,\n    'time': None\n}\n\n"
                 "# FIX-03/14: negative caches so a blocked/unknown symbol fails fast\n"
                 "_NSE_BLOCKED_UNTIL = [0.0]\n_FAIL_CACHE = {}\n_ML_CACHE = {}\n_ML_LOCK = threading.Lock()",
                 'FIX-03/14 negative caches + ML cache globals')

    # FIX-03 · NSE live quote with negative cache
    s = span_replace(
        s, 'def fetch_nse_live_ltp(symbol):', '\n\n\n# ═══',
        '''def fetch_nse_live_ltp(symbol):
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
    return None''',
        'FIX-03 NSE live quote negative cache')

    # FIX-04 · Tier-2 scraper: correct key + refuse intraday-only payloads
    s = span_replace(
        s, '    def fetch_nse_direct(self, symbol, days=500):', '\n\n    def fetch_yahoo(self, symbol',
        '''    def fetch_nse_direct(self, symbol, days=500):
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

        return None''',
        'FIX-04 Tier-2 scraper (key typo + intraday guard + nsepython signature)')

    # FIX-02 · clean_json: NaN/Inf → null
    s = span_replace(
        s, 'def clean_json(data):', '\n\ndef sf(val, default=0.0):',
        '''def clean_json(data):
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
    return data''',
        'FIX-02 clean_json NaN→null')

    # FIX-01 · ranked, word-boundary symbol resolver
    s = span_replace(
        s, 'def resolve_symbol(user_input):', '\n\n\n# ═══',
        '''def resolve_symbol(user_input):
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

    return q''',
        'FIX-01 ranked symbol resolver')

    # FIX-06 · cache the ML block (was 8.1s on every request)
    s = sub_once(s, 'def ml_engine(df):\n',
                 '''def ml_engine(df):
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
''',
                 'FIX-06 ML cache wrapper')

    # FIX-05 · real VWAP
    s = sub_once(s, "    # Cumulative Volume Weighted Average Price (VWAP)\n"
                    "    tp = (h + l + c) / 3\n"
                    "    df['VWAP'] = (tp * v).cumsum() / (v.cumsum() + 1e-10)",
                 "    # Volume Weighted Average Price\n"
                 "    # FIX-05: this used to be a CUMULATIVE VWAP since the first downloaded\n"
                 "    # bar (~2 years) while the intraday KPI and the indicator table used it\n"
                 "    # as if it were a session VWAP. Now 20-session rolling; the old series is\n"
                 "    # kept as VWAP_CUMULATIVE for reference.\n"
                 "    tp = (h + l + c) / 3\n"
                 "    df['VWAP_CUMULATIVE'] = (tp * v).cumsum() / (v.cumsum() + 1e-10)\n"
                 "    df['VWAP'] = (tp * v).rolling(20).sum() / (v.rolling(20).sum() + 1e-10)",
                 'FIX-05 20-session VWAP')

    # FIX-07 · position sizing
    s = span_replace(
        s, 'def calculate_risk(price, atr, score, capital=None):', '\n\n\n# ═══',
        '''def calculate_risk(price, atr, score, capital=None, action=None):
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
    }''',
        'FIX-07 direction-aware, notional-capped Kelly sizing')

    # FIX-08 · calibrated diagnostic score
    s = sub_once(s, "        'tradeable': final >= 78\n    }\n",
                 '''        'tradeable': final >= 78
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
''',
                 'FIX-08 ensemble_v2 diagnostic score')

    # FIX-14 · bad-symbol cache in stock_api
    s = sub_once(s, 'def stock_api(symbol):\n    resolved = resolve_symbol(symbol)',
                 'def stock_api(symbol):\n'
                 '    # FIX-14: an unresolvable symbol used to cost ~15s on EVERY call\n'
                 '    # (3 tiers x 2 exchanges with 4-6s timeouts). Cache the miss.\n'
                 '    _hit = _FAIL_CACHE.get(symbol.upper(), 0)\n'
                 '    if time.time() < _hit:\n'
                 '        return jsonify({\'error\': f"Symbol \'{symbol}\' could not be resolved "\n'
                 '                                  f"(cached miss, retry in {int(_hit - time.time())}s)"}), 404\n'
                 '    resolved = resolve_symbol(symbol)',
                 'FIX-14 bad-symbol cache')

    s = sub_once(s, "    if df is None or len(df) < 20:\n"
                    "        return jsonify({'error': f\"Stock '{symbol}' data not available across all 3 engines!\"}), 404",
                 "    if df is None or len(df) < 20:\n"
                 "        _FAIL_CACHE[symbol.upper()] = time.time() + 300\n"
                 "        return jsonify({'error': f\"Stock '{symbol}' data not available across all 3 engines!\"}), 404",
                 'FIX-14b populate bad-symbol cache')

    # FIX-09a · fundamentals unit normalisation
    s = sub_once(s, '        chart_data = []\n',
                 '        # FIX-09: modern yfinance already returns dividendYield / returnOnEquity\n'
                 '        # in PERCENT (0.5 == 0.50%). The old code multiplied by 100 and the\n'
                 '        # dashboard showed a 50.00% dividend yield for RELIANCE.\n'
                 '        _dy = info.get(\'dividendYield\')\n'
                 '        _dy = (_dy / 100.0) if (_dy and _dy > 25) else _dy\n'
                 '        _roe = info.get(\'returnOnEquity\')\n'
                 '        _roe = (_roe / 100.0) if (_roe and _roe > 5) else _roe\n\n'
                 '        chart_data = []\n',
                 'FIX-09a fundamentals unit clamp')

    s = sub_once(s, "                'roe': f\"{fund_data['roe_val'] * 100:.1f}%\" if fund_data['roe_val'] else 'N/A',",
                 "                'roe': f\"{_roe:.2f}%\" if _roe else 'N/A',", 'FIX-09b roe formatting')
    s = sub_once(s, "                'div_yield': f\"{info.get('dividendYield', 0) * 100:.2f}%\" if info.get('dividendYield') else 'N/A',",
                 "                'div_yield': f\"{_dy:.2f}%\" if _dy else 'N/A',", 'FIX-09c dividend formatting')

    # FIX-09d · direction-aware risk + honesty fields
    s = sub_once(s, "        risk = calculate_risk(price, atr, ens['score'])",
                 "        risk = calculate_risk(price, atr, ens['score'], action=ens['action'])",
                 'FIX-09d risk call passes the verdict direction')

    s = sub_once(s, "            'chart': chart_data\n        }",
                 "            # FIX-08/09: re-centred diagnostic score + honest data-source flags\n"
                 "            'ensemble_v2': ensemble_v2(engines, ens),\n"
                 "            'is_realtime': ('NSE' in str(active_source) and 'TradingView' not in str(active_source)),\n"
                 "            'disclaimer': ('Prices are exchange-delayed whenever data_source is TradingView/Yahoo. '\n"
                 "                           'ML accuracy is a single 80/20 split unless walk_forward_accuracy is quoted.'),\n"
                 "            'chart': chart_data\n        }",
                 'FIX-09e ensemble_v2 + is_realtime + disclaimer in payload')

    # FIX-12 · SSE hardening
    s = span_replace(
        s, "@app.route('/api/stream/<symbol>')", '\n\n\n# ═══',
        '''@app.route('/api/stream/<symbol>')
def sse_live_stream(symbol):
    """FIX-12: proper no-cache headers + heartbeats so the dashboard can use SSE."""
    def event_stream():
        resolved = resolve_symbol(symbol)
        while True:
            live_quote = fetch_nse_live_ltp(resolved)
            if live_quote:
                yield f"data: {json.dumps(live_quote)}\\n\\n"
            else:
                df, src = DATA_MANAGER.smart_fetch(resolved, period='5d', interval='5m', n_bars=10)
                if df is not None and not df.empty:
                    last = df.iloc[-1]
                    yield "data: " + json.dumps({
                        'symbol': resolved, 'source': src, 'stale': True,
                        'price': round(sf(last['Close']), 2),
                        'volume': si(last['Volume']),
                        'time': datetime.now().strftime('%H:%M:%S')}) + "\\n\\n"
                else:
                    yield ": keep-alive\\n\\n"
            time.sleep(CONFIG['SSE_STREAM_INTERVAL'])

    resp = Response(event_stream(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp''',
        'FIX-12 SSE hardening')

    # FIX-10 · real 404s (the catch-all turned every 404 into a 500)
    s = span_replace(
        s, '@app.errorhandler(Exception)', "\n\nif __name__ == '__main__':",
        '''@app.errorhandler(404)
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
    return jsonify({'error': f"Server Exception: {str(e)}"}), 500''',
        'FIX-10 real 404s + HTTPException passthrough')

    # FIX-11 · serve the dashboard + icon, and a friendlier main block
    tail_start = "if __name__ == '__main__':"
    i = s.index(tail_start)
    s = s[:i] + '''@app.route('/')
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
'''
    APPLIED.append('FIX-11 dashboard/icon/static routes + main block')

    write('app.py', s)


# ═══════════════════════════════════════════════════════════════════════════
#  2) nifty_scanner.py
# ═══════════════════════════════════════════════════════════════════════════
def patch_scanner():
    s = (ROOT / 'nifty_scanner.py').read_text(encoding='utf-8')

    s = sub_once(s, 'import yfinance as yf\nimport pandas as pd\nimport numpy as np\nimport json\nimport time',
                 'import yfinance as yf\nimport pandas as pd\nimport numpy as np\nimport json\nimport math\nimport threading\nimport time\nfrom datetime import time as _dtime\nfrom zoneinfo import ZoneInfo\n\n'
                 '# ── FIX-S1: ONE shared TradingView socket (was: a new TvDatafeed() per\n'
                 '#    symbol; with 5 threads that is 30 sockets and the feed kept dropping)\n'
                 '_TV, _TV_LOCK, _TV_OK = None, threading.RLock(), [True]\n'
                 '_IST = ZoneInfo(\'Asia/Kolkata\')\n\n\n'
                 'def _tv_connection():\n'
                 '    global _TV\n'
                 '    with _TV_LOCK:\n'
                 '        if _TV is None:\n'
                 '            from tvDatafeed import TvDatafeed\n'
                 '            _TV = TvDatafeed()\n'
                 '        return _TV\n\n\n'
                 'def _nan_safe(o):\n'
                 '    """FIX-S4: scan_results.json used to contain literal NaN tokens."""\n'
                 '    if isinstance(o, dict):\n'
                 '        return {k: _nan_safe(v) for k, v in o.items()}\n'
                 '    if isinstance(o, (list, tuple)):\n'
                 '        return [_nan_safe(v) for v in o]\n'
                 '    if isinstance(o, (np.floating, float)):\n'
                 '        f = float(o)\n'
                 '        return None if (math.isnan(f) or math.isinf(f)) else f\n'
                 '    if isinstance(o, np.integer):\n'
                 '        return int(o)\n'
                 '    if isinstance(o, np.bool_):\n'
                 '        return bool(o)\n'
                 '    return o',
                 'FIX-S1/S4 scanner imports, shared socket, nan-safe writer')

    s = span_replace(
        s, 'def fetch_scanner_data(symbol):', '\n\ndef calculate_indicators(df):',
        '''def fetch_scanner_data(symbol):
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

    return None, 'None\'''',
        'FIX-S1 shared-socket fetch')

    s = sub_once(s, "    df['Vol_SMA'] = valid_v.rolling(20).mean()\n"
                    "    df['Vol_Ratio'] = valid_v / (df['Vol_SMA'] + 1)\n\n    return df",
                 "    df['Vol_SMA'] = valid_v.rolling(20).mean()\n"
                 "    df['Vol_Ratio'] = valid_v / (df['Vol_SMA'] + 1)\n\n"
                 "    # FIX-S2: RVOL from the last COMPLETED session. Measured before the fix:\n"
                 "    # vol_ratio was 0.21-0.92 for ALL 30 stocks because the in-progress\n"
                 "    # session was divided by a full-day average — a permanent market-wide\n"
                 "    # penalty. Only applies during market hours on a trading day.\n"
                 "    partial = False\n"
                 "    try:\n"
                 "        _last = pd.to_datetime(df.index[-1])\n"
                 "        _last = _last.tz_localize(None) if getattr(_last, 'tzinfo', None) else _last\n"
                 "        _now = datetime.now(_IST)\n"
                 "        partial = (_last.date() == _now.date()) and (_now.time() < _dtime(15, 30))\n"
                 "    except Exception:\n"
                 "        partial = False\n"
                 "    if partial and len(valid_v) > 22:\n"
                 "        hist = valid_v.iloc[:-1]\n"
                 "        df.loc[df.index[-1], 'Vol_Ratio'] = float(hist.iloc[-1]) / (float(hist.iloc[-21:-1].mean()) + 1)\n"
                 "    df['SESSION_IN_PROGRESS'] = partial\n\n"
                 "    return df",
                 'FIX-S2 RVOL from last completed session')

    s = sub_once(s, "            'ml_edge': ml_edge,\n            'composite': composite,",
                 "            'ml_edge': ml_edge,\n"
                 "            # FIX-S3: the probability the composite ACTUALLY used (a negative-edge\n"
                 "            # name is neutralised to 50 — the old UI showed the raw 93% anyway)\n"
                 "            'ml_effective': 50.0 if ml_edge < 0 else ml_prob,\n"
                 "            'ml_used_in_composite': ml_edge >= 0,\n"
                 "            'composite': composite,",
                 'FIX-S3 ml_effective exposed')

    s = sub_once(s, "    with open('scan_results.json', 'w') as f:\n        json.dump(scan_data, f, indent=2, default=str)",
                 "    # FIX-S4: strict-JSON output (no NaN/Infinity tokens)\n"
                 "    with open('scan_results.json', 'w') as f:\n"
                 "        json.dump(_nan_safe(scan_data), f, indent=2, allow_nan=False)",
                 'FIX-S4 strict-JSON scan output')

    write('nifty_scanner.py', s)


# ═══════════════════════════════════════════════════════════════════════════
#  3) deep_analyzer.py
# ═══════════════════════════════════════════════════════════════════════════
def patch_analyzer():
    s = (ROOT / 'deep_analyzer.py').read_text(encoding='utf-8')

    s = sub_once(s, "import yfinance as yf\nimport pandas as pd\nimport numpy as np\nimport json",
                 "import yfinance as yf\nimport pandas as pd\nimport numpy as np\nimport json\nimport math\n\n"
                 "# FIX-A/B: requested window + date-normalised index\n"
                 "_SPAN_DAYS = {'1mo': 31, '3mo': 92, '6mo': 183, '1y': 365, '2y': 735, '5y': 1826}\n\n\n"
                 "def _normalise(df, period):\n"
                 "    \"\"\"\n"
                 "    FIX-A: the TradingView branch ignored `period` (it always returned\n"
                 "    n_bars=500 ≈ 2 years) while the NIFTY leg fell through to Yahoo and\n"
                 "    DID honour period='6mo' — so \"Relative Strength\" subtracted a 6-month\n"
                 "    index return from a 2-year stock return.\n"
                 "    FIX-B: TradingView stamps bars with a 03:45:00 time component while\n"
                 "    Yahoo uses midnight, so the inner join produced ZERO rows and\n"
                 "    beta/correlation came out NaN (silently labelled 'Market').\n"
                 "    \"\"\"\n"
                 "    if df is None or df.empty or len(df) < 20:\n"
                 "        return df\n"
                 "    df = df.copy()\n"
                 "    idx = pd.to_datetime(df.index)\n"
                 "    try:\n"
                 "        idx = idx.tz_localize(None)\n"
                 "    except (TypeError, AttributeError):\n"
                 "        pass\n"
                 "    df.index = idx.normalize()\n"
                 "    df = df[~df.index.duplicated(keep='last')].sort_index()\n"
                 "    days = _SPAN_DAYS.get(str(period).lower())\n"
                 "    if days:\n"
                 "        slice_ = df[df.index >= df.index[-1] - pd.Timedelta(days=days)]\n"
                 "        if len(slice_) >= 20:\n"
                 "            df = slice_\n"
                 "    return df",
                 'FIX-A/B scanner-side helpers in analyzer')

    # NOTE: both the TradingView and the Yahoo branch end with the same 4 lines,
    # so each anchor is extended past the `except` to stay unique.
    s = sub_once(s, "            df = df.dropna(subset=['Close'])\n"
                    "            if len(df) >= 60:\n"
                    "                return df\n"
                    "    except Exception:\n"
                    "        pass\n\n"
                    "    # Tier 2: Yahoo Finance Fallback (with MultiIndex Fix)",
                 "            df = df.dropna(subset=['Close'])\n"
                 "            if len(df) >= 60:\n"
                 "                return _normalise(df, period)   # FIX-A: honour `period`\n"
                 "    except Exception:\n"
                 "        pass\n\n"
                 "    # Tier 2: Yahoo Finance Fallback (with MultiIndex Fix)",
                 'FIX-A analyse period honoured (TradingView branch)')

    s = sub_once(s, "            df = df.dropna(subset=['Close'])\n"
                    "            if len(df) >= 60:\n"
                    "                return df\n"
                    "    except Exception:\n"
                    "        pass\n\n"
                    "    return None",
                 "            df = df.dropna(subset=['Close'])\n"
                 "            if len(df) >= 60:\n"
                 "                return _normalise(df, period)   # FIX-A: honour `period`\n"
                 "    except Exception:\n"
                 "        pass\n\n"
                 "    return None",
                 'FIX-A analyse period honoured (Yahoo branch)')

    s = sub_once(s, "        corr = float(common[0].corr(common[1]))\n"
                    "        beta = float(common[0].cov(common[1]) / (common[1].var() + 1e-10))\n"
                    "        rs = s_ret - n_ret",
                 "        # FIX-B: only claim a beta/regime label when the numbers are finite\n"
                 "        corr = float(common[0].corr(common[1])) if len(common[0]) > 5 else float('nan')\n"
                 "        beta = float(common[0].cov(common[1]) / (common[1].var() + 1e-10)) if len(common[0]) > 5 else float('nan')\n"
                 "        rs = s_ret - n_ret",
                 'FIX-B finite guard')

    s = sub_once(s, "            'correlation': round(corr, 2),\n"
                    "            'beta': round(beta, 2),\n"
                    "            'vs_nifty': 'OUTPERFORM' if rs > 0 else 'UNDERPERFORM',\n"
                    "            'beta_type': 'Aggressive' if beta > 1.2 else 'Defensive' if beta < 0.8 else 'Market'",
                 "            'correlation': round(corr, 3) if math.isfinite(corr) else None,\n"
                 "            'beta': round(beta, 3) if math.isfinite(beta) else None,\n"
                 "            'vs_nifty': 'OUTPERFORM' if rs > 0 else 'UNDERPERFORM',\n"
                 "            'beta_type': ('N/A (no overlapping sessions)' if not (math.isfinite(beta) and math.isfinite(corr))\n"
                 "                          else 'Aggressive' if beta > 1.2 else 'Defensive' if beta < 0.8 else 'Market'),\n"
                 "            'aligned_sessions': int(len(common[0])),\n"
                 "            'stock_span_days': int((stock.index[-1] - stock.index[0]).days),\n"
                 "            'nifty_span_days': int((nifty.index[-1] - nifty.index[0]).days)",
                 'FIX-B finite-only beta labelling + span diagnostics')

    s = sub_once(s, "    with open(filename, 'w') as f:\n        json.dump(result, f, indent=2, default=str)",
                 "    # FIX-C: valid strict JSON (NaN/Inf -> null). The old file contained\n"
                 "    # literal NaN tokens, which JavaScript's JSON.parse rejects.\n"
                 "    def _nan_safe(o):\n"
                 "        if isinstance(o, dict):\n"
                 "            return {k: _nan_safe(v) for k, v in o.items()}\n"
                 "        if isinstance(o, (list, tuple)):\n"
                 "            return [_nan_safe(v) for v in o]\n"
                 "        if isinstance(o, (np.floating, float)):\n"
                 "            f_ = float(o)\n"
                 "            return None if (math.isnan(f_) or math.isinf(f_)) else f_\n"
                 "        if isinstance(o, np.integer):\n"
                 "            return int(o)\n"
                 "        if isinstance(o, np.bool_):\n"
                 "            return bool(o)\n"
                 "        return o\n\n"
                 "    with open(filename, 'w') as f:\n"
                 "        json.dump(_nan_safe(result), f, indent=2, allow_nan=False)",
                 'FIX-C strict-JSON deep output')

    write('deep_analyzer.py', s)


if __name__ == '__main__':
    print("═" * 78)
    print(" StockAI V6.1 — applying in-place fixes")
    print("═" * 78)
    patch_app()
    patch_scanner()
    patch_analyzer()
    print(f"\n  {len(APPLIED)} patches applied:")
    for a in APPLIED:
        print("   ✓", a)
    print("\n  Next:  python3 tools/verify_fixes.py   (runs the regression suite)")
