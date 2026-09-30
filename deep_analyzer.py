"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  🔬 DEEP ANALYZER V3.5 — Walk-Forward ML + Quant Risk + Sector Matrix        ║
║  • 3-Tier Multi-Tech Fetcher (TradingView Direct + Yahoo Fallback)           ║
║  • MultiIndex Column Crash-Proof Handler                                     ║
║  • Expanding Window Walk-Forward ML Validation                               ║
║  • Sharpe, Sortino, Calmar, VaR (95%), Win Rate, Profit Factor               ║
║  • Relative Strength vs NIFTY 50 + Beta Classification                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import math

# FIX-A/B: requested window + date-normalised index
_SPAN_DAYS = {'1mo': 31, '3mo': 92, '6mo': 183, '1y': 365, '2y': 735, '5y': 1826}


def _normalise(df, period):
    """
    FIX-A: the TradingView branch ignored `period` (it always returned
    n_bars=500 ≈ 2 years) while the NIFTY leg fell through to Yahoo and
    DID honour period='6mo' — so "Relative Strength" subtracted a 6-month
    index return from a 2-year stock return.
    FIX-B: TradingView stamps bars with a 03:45:00 time component while
    Yahoo uses midnight, so the inner join produced ZERO rows and
    beta/correlation came out NaN (silently labelled 'Market').
    """
    if df is None or df.empty or len(df) < 20:
        return df
    df = df.copy()
    idx = pd.to_datetime(df.index)
    try:
        idx = idx.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep='last')].sort_index()
    days = _SPAN_DAYS.get(str(period).lower())
    if days:
        slice_ = df[df.index >= df.index[-1] - pd.Timedelta(days=days)]
        if len(slice_) >= 20:
            df = slice_
    return df
import requests as http_requests
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
#  CRASH-PROOF DATA FETCHER
# ═══════════════════════════════════════════════════════════
def safe_download_deep(symbol, period='2y', interval='1d'):
    clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()
    
    # Tier 1: TradingView Direct
    try:
        from tvDatafeed import TvDatafeed, Interval
        tv = TvDatafeed()
        df = tv.get_hist(symbol=clean_sym, exchange='NSE', interval=Interval.in_daily, n_bars=500)
        if df is not None and not df.empty:
            df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.dropna(subset=['Close'])
            if len(df) >= 60:
                return _normalise(df, period)   # FIX-A: honour `period`
    except Exception:
        pass

    # Tier 2: Yahoo Finance Fallback (with MultiIndex Fix)
    try:
        target = f"{clean_sym}.NS" if not symbol.startswith('^') else symbol
        df = yf.download(target, period=period, interval=interval, progress=False, threads=False)
        
        if (df is None or df.empty) and not symbol.startswith('^'):
            df = yf.download(f"{clean_sym}.BO", period=period, interval=interval, progress=False, threads=False)

        if df is not None and not df.empty:
            # 🟢 MULTIINDEX FIX: Prevents pandas crashes in newer yfinance versions
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            df = df.dropna(subset=['Close'])
            if len(df) >= 60:
                return _normalise(df, period)   # FIX-A: honour `period`
    except Exception:
        pass

    return None


# ═══════════════════════════════════════════════════════════
#  DEEP ML ANALYSIS (Walk-Forward + Baseline Edge)
# ═══════════════════════════════════════════════════════════
def deep_ml_analysis(df, symbol):
    try:
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
    except ImportError:
        return {'error': 'scikit-learn is not installed. Run: pip install scikit-learn'}

    d = df.copy()
    c = d['Close'].astype(float)
    h = d['High'].astype(float)
    l = d['Low'].astype(float)
    v = d['Volume'].astype(float)

    # ── 28 Features ──
    d['ret_1'] = c.pct_change(1)
    d['ret_3'] = c.pct_change(3)
    d['ret_5'] = c.pct_change(5)
    d['ret_10'] = c.pct_change(10)
    d['ret_20'] = c.pct_change(20)

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
    d['sma200'] = c.rolling(200).mean()
    d['ema_cross'] = (d['ema9'] - d['ema21']) / (c + 1e-10) * 100
    d['price_200'] = (c - d['sma200']) / (d['sma200'] + 1e-10) * 100

    plus_dm_raw = h.diff()
    minus_dm_raw = -l.diff()
    plus_dm = plus_dm_raw.where((plus_dm_raw > minus_dm_raw) & (plus_dm_raw > 0), 0.0)
    minus_dm = minus_dm_raw.where((minus_dm_raw > plus_dm_raw) & (minus_dm_raw > 0), 0.0)
    d['plus_di'] = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / (d['atr'] + 1e-10)
    d['minus_di'] = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / (d['atr'] + 1e-10)
    d['adx'] = (100 * (d['plus_di'] - d['minus_di']).abs() / (d['plus_di'] + d['minus_di'] + 1e-10)).ewm(alpha=1/14, adjust=False).mean()

    rsi_s = d['rsi']
    d['stoch_rsi'] = ((rsi_s - rsi_s.rolling(14).min()) / (rsi_s.rolling(14).max() - rsi_s.rolling(14).min() + 1e-10)).rolling(3).mean() * 100

    tp = (h + l + c) / 3
    d['cci'] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True) + 1e-10)
    d['willr'] = ((h.rolling(14).max() - c) / (h.rolling(14).max() - l.rolling(14).min() + 1e-10)) * -100

    d['vol_20'] = c.pct_change().rolling(20).std() * 100
    d['vol_5'] = c.pct_change().rolling(5).std() * 100
    d['vwap_dist'] = (c - (tp * v).cumsum() / (v.cumsum() + 1e-10)) / (c + 1e-10) * 100
    d['hl_range'] = (h - l) / (c + 1e-10) * 100
    d['close_pos'] = (c - l) / (h - l + 1e-10)

    d['target'] = (c.shift(-1) > c).astype(int)

    feats = ['ret_1','ret_3','ret_5','ret_10','ret_20','rsi','macd','macd_sig','macd_hist',
             'bb_pctb','bb_width','atr_pct','vol_ratio','vol_change','obv_slope',
             'ema_cross','price_200','adx','plus_di','minus_di','stoch_rsi','cci',
             'willr','vol_20','vol_5','vwap_dist','hl_range','close_pos']

    d[feats] = d[feats].replace([np.inf, -np.inf], np.nan).ffill().bfill()
    d_clean = d.dropna(subset=feats + ['target'])

    if len(d_clean) < 120:
        return {'error': f'Need 120+ clean days, got {len(d_clean)}'}

    # ── Walk-Forward Validation (Expanding Window) ──
    wf_results = []
    step = 20
    test_window = 20

    for start in range(120, len(d_clean) - test_window, step):
        train = d_clean.iloc[:start]
        test = d_clean.iloc[start:start + test_window]

        if len(test) < 10:
            continue

        X_tr = np.nan_to_num(train[feats].values)
        y_tr = train['target'].values
        X_te = np.nan_to_num(test[feats].values)
        y_te = test['target'].values

        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr)
        X_te_s = scaler.transform(X_te)

        gb = GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=0.05, random_state=42)
        gb.fit(X_tr_s, y_tr)
        preds = gb.predict(X_te_s)
        wf_results.append(accuracy_score(y_te, preds))

    wf_accuracy = round(np.mean(wf_results) * 100, 1) if wf_results else 0.0
    wf_stability = round(np.std(wf_results) * 100, 1) if wf_results else 99.0

    # ── Final Train / Test Split (80/20) ──
    train_n = int(len(d_clean) * 0.8)
    X_train = np.nan_to_num(d_clean[feats].iloc[:train_n].values)
    y_train = d_clean['target'].iloc[:train_n].values
    X_test = np.nan_to_num(d_clean[feats].iloc[train_n:].values)
    y_test = d_clean['target'].iloc[train_n:].values

    pos_rate = float(y_test.mean())
    baseline_acc = round(max(pos_rate, 1 - pos_rate) * 100, 1)

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_train)
    X_te_s = scaler.transform(X_test)
    X_today = scaler.transform(np.nan_to_num(d_clean[feats].iloc[-1:].values))

    models = {}

    # GB
    gb = GradientBoostingClassifier(n_estimators=150, max_depth=4, learning_rate=0.05, subsample=0.8, random_state=42)
    gb.fit(X_tr_s, y_train)
    gb_preds = gb.predict(X_te_s)
    gb_acc = round(accuracy_score(y_test, gb_preds) * 100, 1)
    models['GradientBoosting'] = {
        'accuracy': gb_acc, 'edge': round(gb_acc - baseline_acc, 1),
        'precision': round(precision_score(y_test, gb_preds, zero_division=0) * 100, 1),
        'recall': round(recall_score(y_test, gb_preds, zero_division=0) * 100, 1),
        'f1': round(f1_score(y_test, gb_preds, zero_division=0) * 100, 1),
        'prob_up': round(float(gb.predict_proba(X_today)[0][1]) * 100, 1)
    }

    # RF
    rf = RandomForestClassifier(n_estimators=200, max_depth=6, min_samples_leaf=8, random_state=42)
    rf.fit(X_tr_s, y_train)
    rf_preds = rf.predict(X_te_s)
    rf_acc = round(accuracy_score(y_test, rf_preds) * 100, 1)
    models['RandomForest'] = {
        'accuracy': rf_acc, 'edge': round(rf_acc - baseline_acc, 1),
        'precision': round(precision_score(y_test, rf_preds, zero_division=0) * 100, 1),
        'recall': round(recall_score(y_test, rf_preds, zero_division=0) * 100, 1),
        'f1': round(f1_score(y_test, rf_preds, zero_division=0) * 100, 1),
        'prob_up': round(float(rf.predict_proba(X_today)[0][1]) * 100, 1)
    }

    # LR
    lr = LogisticRegression(max_iter=1000, random_state=42)
    lr.fit(X_tr_s, y_train)
    lr_preds = lr.predict(X_te_s)
    lr_acc = round(accuracy_score(y_test, lr_preds) * 100, 1)
    models['LogisticRegression'] = {
        'accuracy': lr_acc, 'edge': round(lr_acc - baseline_acc, 1),
        'precision': round(precision_score(y_test, lr_preds, zero_division=0) * 100, 1),
        'recall': round(recall_score(y_test, lr_preds, zero_division=0) * 100, 1),
        'f1': round(f1_score(y_test, lr_preds, zero_division=0) * 100, 1),
        'prob_up': round(float(lr.predict_proba(X_today)[0][1]) * 100, 1)
    }

    # XGBoost
    try:
        from xgboost import XGBClassifier
        xgb = XGBClassifier(n_estimators=150, max_depth=4, learning_rate=0.05, random_state=42, verbosity=0,
                            use_label_encoder=False, eval_metric='logloss')
        xgb.fit(X_tr_s, y_train)
        xgb_preds = xgb.predict(X_te_s)
        xgb_acc = round(accuracy_score(y_test, xgb_preds) * 100, 1)
        models['XGBoost'] = {
            'accuracy': xgb_acc, 'edge': round(xgb_acc - baseline_acc, 1),
            'precision': round(precision_score(y_test, xgb_preds, zero_division=0) * 100, 1),
            'recall': round(recall_score(y_test, xgb_preds, zero_division=0) * 100, 1),
            'f1': round(f1_score(y_test, xgb_preds, zero_division=0) * 100, 1),
            'prob_up': round(float(xgb.predict_proba(X_today)[0][1]) * 100, 1)
        }
    except Exception:
        pass

    probs = [m['prob_up'] for m in models.values()]
    weights = [0.35, 0.25, 0.15] + ([0.25] if 'XGBoost' in models else [])
    weights = weights[:len(probs)]
    w_sum = sum(weights)
    ensemble_prob = round(sum(p * w / w_sum for p, w in zip(probs, weights)), 1)

    imp = gb.feature_importances_
    top_feats = sorted(zip(feats, imp), key=lambda x: x[1], reverse=True)[:8]

    return {
        'walk_forward_accuracy': wf_accuracy,
        'walk_forward_stability': wf_stability,
        'walk_forward_windows': len(wf_results),
        'baseline_accuracy': baseline_acc,
        'pos_rate': round(pos_rate * 100, 1),
        'models': models,
        'ensemble_prob': ensemble_prob,
        'prediction': 'UP' if ensemble_prob >= 55 else 'DOWN' if ensemble_prob <= 45 else 'NEUTRAL',
        'confidence': 'HIGH' if abs(ensemble_prob - 50) > 12 else 'MEDIUM' if abs(ensemble_prob - 50) > 6 else 'LOW',
        'top_features': [{'name': f[0], 'importance': round(f[1]*100, 1)} for f in top_feats],
        'train_days': train_n,
        'test_days': len(d_clean) - train_n,
        'total_features': len(feats)
    }


# ═══════════════════════════════════════════════════════════
#  QUANT RISK METRICS (Sharpe, Sortino, VaR)
# ═══════════════════════════════════════════════════════════
def advanced_risk_metrics(df):
    c = df['Close'].astype(float)
    returns = c.pct_change().dropna()

    ann_ret = float(returns.mean() * 252)
    ann_vol = float(returns.std() * np.sqrt(252))

    rf_rate = 0.065 # India 10Y Yield
    sharpe = (ann_ret - rf_rate) / (ann_vol + 1e-10)

    downside = float(returns[returns < 0].std() * np.sqrt(252))
    sortino = (ann_ret - rf_rate) / (downside + 1e-10)

    cummax = c.cummax()
    drawdown = (c - cummax) / (cummax + 1e-10)
    max_dd = float(drawdown.min() * 100)

    calmar = ann_ret / (abs(max_dd / 100) + 1e-10)

    var_95 = float(np.percentile(returns, 5) * 100)
    win_rate = float((returns > 0).sum() / len(returns) * 100)

    gross_profit = float(returns[returns > 0].sum())
    gross_loss = float(abs(returns[returns < 0].sum()))
    profit_factor = gross_profit / (gross_loss + 1e-10)

    return {
        'annual_return': round(ann_ret * 100, 1),
        'annual_volatility': round(ann_vol * 100, 1),
        'sharpe_ratio': round(sharpe, 2),
        'sortino_ratio': round(sortino, 2),
        'max_drawdown': round(max_dd, 1),
        'calmar_ratio': round(calmar, 2),
        'var_95': round(var_95, 2),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
        'rating': 'EXCELLENT' if sharpe > 1.5 else 'GOOD' if sharpe > 1.0 else 'AVERAGE' if sharpe > 0.5 else 'POOR'
    }


# ═══════════════════════════════════════════════════════════
#  SECTOR & BETA MATRIX vs NIFTY 50
# ═══════════════════════════════════════════════════════════
def sector_strength(symbol):
    try:
        stock = safe_download_deep(symbol, period='6mo')
        nifty = safe_download_deep("^NSEI", period='6mo')

        if stock is None or nifty is None or nifty.empty:
            return {'stock_return_6m': 0, 'nifty_return_6m': 0, 'relative_strength': 0,
                    'correlation': 0, 'beta': 1, 'vs_nifty': 'N/A', 'beta_type': 'N/A'}

        s_ret = float((stock['Close'].iloc[-1] / stock['Close'].iloc[0] - 1) * 100)
        n_ret = float((nifty['Close'].iloc[-1] / nifty['Close'].iloc[0] - 1) * 100)

        s_returns = stock['Close'].pct_change().dropna()
        n_returns = nifty['Close'].pct_change().dropna()
        common = s_returns.align(n_returns, join='inner')
        
        # FIX-B: only claim a beta/regime label when the numbers are finite
        corr = float(common[0].corr(common[1])) if len(common[0]) > 5 else float('nan')
        beta = float(common[0].cov(common[1]) / (common[1].var() + 1e-10)) if len(common[0]) > 5 else float('nan')
        rs = s_ret - n_ret

        return {
            'stock_return_6m': round(s_ret, 1),
            'nifty_return_6m': round(n_ret, 1),
            'relative_strength': round(rs, 1),
            'correlation': round(corr, 3) if math.isfinite(corr) else None,
            'beta': round(beta, 3) if math.isfinite(beta) else None,
            'vs_nifty': 'OUTPERFORM' if rs > 0 else 'UNDERPERFORM',
            'beta_type': ('N/A (no overlapping sessions)' if not (math.isfinite(beta) and math.isfinite(corr))
                          else 'Aggressive' if beta > 1.2 else 'Defensive' if beta < 0.8 else 'Market'),
            'aligned_sessions': int(len(common[0])),
            'stock_span_days': int((stock.index[-1] - stock.index[0]).days),
            'nifty_span_days': int((nifty.index[-1] - nifty.index[0]).days)
        }
    except Exception:
        return {'stock_return_6m': 0, 'nifty_return_6m': 0, 'relative_strength': 0,
                'correlation': 0, 'beta': 1, 'vs_nifty': 'N/A', 'beta_type': 'N/A'}


# ═══════════════════════════════════════════════════════════
#  CLI EXECUTION
# ═══════════════════════════════════════════════════════════
def run_deep_analysis(symbol):
    print(f"\n{'='*65}")
    print(f"  🔬 DEEP ANALYSIS V3.5: {symbol}")
    print(f"  📅 {datetime.now().strftime('%d %b %Y %H:%M:%S')}")
    print(f"{'='*65}")

    print(f"\n  📥 Downloading 2-year data...")
    df = safe_download_deep(symbol)
    if df is None:
        print("  ❌ Stock not found or data unavailable!")
        return None

    price = float(df['Close'].iloc[-1])

    print(f"  🧠 Training ML models (Walk-Forward, Expanding Window)...")
    ml = deep_ml_analysis(df, symbol)

    print(f"  📊 Calculating risk metrics...")
    risk = advanced_risk_metrics(df)

    print(f"  🏢 Analyzing sector strength...")
    sector = sector_strength(symbol)

    print(f"  💰 Fetching fundamentals...")
    try:
        info = yf.Ticker(f"{symbol}.NS").info or {}
    except Exception:
        info = {}

    # Print Report
    print(f"\n{'─'*65}")
    print(f"  💰 Price: ₹{price:.2f}")
    print(f"{'─'*65}")

    if 'error' not in ml:
        print(f"\n  🧠 ML ANALYSIS:")
        print(f"     Walk-Forward Accuracy: {ml['walk_forward_accuracy']}% (±{ml['walk_forward_stability']}%)")
        print(f"     Baseline (Random): {ml['baseline_accuracy']}% | UP Days: {ml['pos_rate']}%")
        print(f"     Ensemble Prediction: {ml['prediction']} ({ml['ensemble_prob']}%) [{ml['confidence']}]")
        print(f"     Models:")
        for name, m in ml['models'].items():
            edge_str = f"+{m['edge']}%" if m['edge'] > 0 else f"{m['edge']}%"
            print(f"       {name:<22} Acc:{m['accuracy']}% (Edge:{edge_str}) | P:{m['precision']}% | R:{m['recall']}% | F1:{m['f1']}% | P(UP):{m['prob_up']}%")
        print(f"     Top Features: {', '.join([f['name'] for f in ml['top_features'][:5]])}")
    else:
        print(f"\n  ❌ ML Error: {ml['error']}")

    print(f"\n  📊 RISK METRICS:")
    print(f"     Sharpe: {risk['sharpe_ratio']} ({risk['rating']})")
    print(f"     Sortino: {risk['sortino_ratio']}")
    print(f"     Max Drawdown: {risk['max_drawdown']}%")
    print(f"     Calmar: {risk['calmar_ratio']}")
    print(f"     VaR (95%): {risk['var_95']}%")
    print(f"     Win Rate: {risk['win_rate']}%")
    print(f"     Profit Factor: {risk['profit_factor']}")

    print(f"\n  🏢 SECTOR:")
    print(f"     Stock 6M: {sector['stock_return_6m']}%")
    print(f"     NIFTY 6M: {sector['nifty_return_6m']}%")
    print(f"     RS: {sector['relative_strength']}% ({sector['vs_nifty']})")
    print(f"     Beta: {sector['beta']} ({sector['beta_type']})")
    print(f"     Correlation: {sector['correlation']}")

    print(f"\n  💰 FUNDAMENTALS:")
    pe = info.get('trailingPE')
    roe = info.get('returnOnEquity')
    mcap = info.get('marketCap')
    print(f"     P/E: {f'{pe:.1f}' if pe else 'N/A'}")
    print(f"     ROE: {f'{roe*100:.1f}%' if roe else 'N/A'}")
    print(f"     MCap: {'₹{:,.0f} Cr'.format(mcap/1e7) if mcap else 'N/A'}")

    result = {
        'symbol': symbol,
        'price': price,
        'timestamp': datetime.now().isoformat(),
        'ml': ml,
        'risk': risk,
        'sector': sector,
        'fundamentals': {
            'pe': pe, 'roe': roe, 'mcap': mcap,
            'debt_equity': info.get('debtToEquity'),
            'div_yield': info.get('dividendYield')
        }
    }

    filename = f'deep_{symbol}.json'
    # FIX-C: valid strict JSON (NaN/Inf -> null). The old file contained
    # literal NaN tokens, which JavaScript's JSON.parse rejects.
    def _nan_safe(o):
        if isinstance(o, dict):
            return {k: _nan_safe(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_nan_safe(v) for v in o]
        if isinstance(o, (np.floating, float)):
            f_ = float(o)
            return None if (math.isnan(f_) or math.isinf(f_)) else f_
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.bool_):
            return bool(o)
        return o

    with open(filename, 'w') as f:
        json.dump(_nan_safe(result), f, indent=2, allow_nan=False)

    print(f"\n  💾 Saved to {filename}")
    print(f"{'='*65}\n")

    return result


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else input("Enter NSE Symbol (e.g. RELIANCE): ").strip().upper()
    run_deep_analysis(sym)