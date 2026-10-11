"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  🔬 DEEP ANALYZER V3.5 — Walk-Forward ML + Quant Risk + Sector Matrix        ║
║  • Shared requested-exchange completed-daily fetcher           ║
║  • Validated OHLCV boundary; explicit unavailable states                                     ║
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

# FIX-65: measured NSE trading days/year = 247 (FIX-60 ke measurement se:
# 2022=248, 2023=245, 2024=246, 2025=249 -> avg 247.0). Purana 252 US-style
# constant annualised return/vol ko ~+2% overstate karta tha — research/backtest.py
# me ye FIX-60 me theek hua tha, deep_analyzer me abhi 252 bacha tha.
TRADING_DAYS = 247


def _normalise(df, period):
    if df is None or df.empty: return df
    df=df.copy();idx=pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is not None: idx=idx.tz_convert('Asia/Kolkata').tz_localize(None)
    df.index=idx.normalize()
    if df.index.has_duplicates or not df.index.is_monotonic_increasing:
        raise ValueError('Duplicate/unordered dates')
    days=_SPAN_DAYS.get(str(period).lower())
    return df[df.index>=df.index[-1]-pd.Timedelta(days=days)] if days else df
from datetime import datetime


# ═══════════════════════════════════════════════════════════
#  CRASH-PROOF DATA FETCHER
# ═══════════════════════════════════════════════════════════
def safe_download_deep(symbol, period='2y', interval='1d', exchange='NSE'):
    if interval != '1d': raise ValueError('Deep analyzer is daily only, not intraday')
    from daily_context import fetch
    try: return fetch(symbol,period,exchange)
    except (ValueError, RuntimeError): return None


# ═══════════════════════════════════════════════════════════
#  DEEP ML ANALYSIS (Walk-Forward + Baseline Edge)
# ═══════════════════════════════════════════════════════════
def deep_ml_analysis(df, symbol, exchange='NSE'):
    # Single production pipeline: same features, models, weights and matched-fold
    # baselines as Dashboard. No separate GB-only walk-forward for a four-model UI.
    from daily_context import host
    result=dict(host().ml_engine(df,symbol,exchange))
    if not result.get('available'):
        return {'available':False,'error':result.get('error','ML unavailable')}
    result.update(ensemble_prob=result.get('probability'),
        walk_forward_stability=result.get('wf_fold_std_pp'),
        walk_forward_windows=len(result.get('wf_folds',[])),
        probability_note='Uncalibrated model score, not trade win probability',
        confidence_note='Model agreement only, not statistical confidence',
        prediction_training_note='Live score refitted on all known labels; holdout and walk-forward metrics remain out of sample',
        available=True)
    return result


# ═══════════════════════════════════════════════════════════
#  QUANT RISK METRICS (Sharpe, Sortino, VaR)
# ═══════════════════════════════════════════════════════════
def advanced_risk_metrics(df, rf_rate=0.065):
    """Historical asset close-return statistics, NOT strategy returns/win rate.
    6.5% default is an explicit scenario assumption, not today's India 10Y quote.
    """
    from safety import valid_ohlcv
    if not valid_ohlcv(df) or len(df)<3: return {'available':False,'error':'Need valid OHLCV and 3+ sessions'}
    if not math.isfinite(rf_rate) or not 0<=rf_rate<1: raise ValueError('Invalid assumed risk-free rate')
    c=df.Close.astype(float);returns=c.pct_change(fill_method=None).dropna()
    ann_ret=float(returns.mean() * TRADING_DAYS)
    ann_vol=float(returns.std(ddof=1)*np.sqrt(TRADING_DAYS))
    rf_daily=(1+rf_rate)**(1/TRADING_DAYS)-1
    downside=float(np.sqrt(np.mean(np.minimum(returns-rf_daily,0)**2))*np.sqrt(TRADING_DAYS))
    elapsed=(pd.Timestamp(df.index[-1])-pd.Timestamp(df.index[0])).total_seconds()/86400
    cagr=float((c.iloc[-1]/c.iloc[0])**(365.25/elapsed)-1) if elapsed>0 else None
    max_dd=float((c/c.cummax()-1).min())
    def ratio(a,b): return round(float(a/b),3) if b>1e-12 and a is not None else None
    gross_loss=float(-returns[returns<0].sum())
    q=float(np.percentile(returns,5))
    return dict(available=True,scope='Historical asset price returns; NOT a traded-strategy backtest',
        annual_return=round(ann_ret*100,2),annual_return_basis='Arithmetic mean × 247 assumed sessions/year',
        cagr=round(cagr*100,2) if cagr is not None and math.isfinite(cagr) else None,
        annual_volatility=round(ann_vol*100,2),sharpe_ratio=ratio(ann_ret-rf_rate,ann_vol),
        sortino_ratio=ratio(ann_ret-rf_rate,downside),max_drawdown=round(max_dd*100,2),
        calmar_ratio=ratio(cagr,abs(max_dd)),var_95=round(max(0,-q)*100,3),
        return_percentile_5=round(q*100,3),win_rate=round(float((returns>0).mean())*100,2),
        win_rate_label='Positive close-return intervals (%) — NOT trade win rate',
        profit_factor=ratio(float(returns[returns>0].sum()),gross_loss),
        profit_factor_label='Sum positive / absolute sum negative asset returns, not trade P&L',
        risk_free_assumption=rf_rate,trading_days_assumption=TRADING_DAYS,
        adjustment_note='Provider-adjusted price series; not independently verified total returns',rating='DESCRIPTIVE ONLY')


# ═══════════════════════════════════════════════════════════
#  SECTOR & BETA MATRIX vs NIFTY 50
# ═══════════════════════════════════════════════════════════
def sector_strength(symbol, exchange='NSE', stock=None):
    """Stock vs NIFTY benchmark, not a sector index. Identical endpoints/intervals."""
    missing=dict(available=False,stock_return_6m=None,nifty_return_6m=None,relative_strength=None,
        correlation=None,beta=None,vs_nifty='N/A',beta_type='N/A',aligned_sessions=0)
    try:
        stock=safe_download_deep(symbol,'6mo',exchange=exchange) if stock is None else _normalise(stock,'6mo')
        nifty=safe_download_deep('^NSEI','6mo')
        if stock is None or nifty is None: return missing
        prices=pd.concat([stock.Close.rename('stock'),nifty.Close.rename('benchmark')],axis=1,join='inner').dropna()
        if len(prices)<20: return {**missing,'reason':'Fewer than 20 shared sessions'}
        returns=prices.pct_change(fill_method=None).dropna()
        s_ret=float((prices.stock.iloc[-1]/prices.stock.iloc[0]-1)*100)
        n_ret=float((prices.benchmark.iloc[-1]/prices.benchmark.iloc[0]-1)*100)
        variance=float(returns.benchmark.var())
        beta=float(returns.stock.cov(returns.benchmark)/variance) if variance>1e-16 else None
        corr=float(returns.stock.corr(returns.benchmark)) if returns.stock.std()>1e-12 and variance>1e-16 else None
        return dict(available=True,stock_return_6m=round(s_ret,2),nifty_return_6m=round(n_ret,2),
            relative_strength=round(s_ret-n_ret,2),correlation=round(corr,3) if corr is not None else None,
            beta=round(beta,3) if beta is not None else None,vs_nifty='OUTPERFORM' if s_ret>n_ret else 'UNDERPERFORM' if s_ret<n_ret else 'MATCH',
            beta_type='N/A' if beta is None else 'Aggressive' if beta>1.2 else 'Defensive' if beta<.8 else 'Market',
            aligned_sessions=len(prices),aligned_return_intervals=len(returns),first_session=str(prices.index[0].date()),
            last_session=str(prices.index[-1].date()),stock_span_days=(prices.index[-1]-prices.index[0]).days,
            nifty_span_days=(prices.index[-1]-prices.index[0]).days,
            note='Up to 6 months on shared endpoints; beta uses aligned intervals, potentially multi-session gaps. NIFTY benchmark, not a sector index.')
    except Exception as exc: return {**missing,'reason':type(exc).__name__}


# ═══════════════════════════════════════════════════════════
#  CLI EXECUTION
# ═══════════════════════════════════════════════════════════
def run_deep_analysis(symbol, exchange='NSE', *, save=True):
    from daily_context import host, atomic_json, ROOT
    import re
    symbol=host().checked_symbol(symbol,exchange)
    if not re.fullmatch(r'[A-Z0-9&_.-]{1,40}',symbol): raise ValueError('Invalid stock symbol')
    df=safe_download_deep(symbol,exchange=exchange)
    if df is None: return {'available':False,'error':'Requested-exchange completed daily data unavailable'}
    from eod_validation import compare
    official=compare(df,symbol,exchange)
    ml=deep_ml_analysis(df,symbol,exchange)
    risk=advanced_risk_metrics(df)
    sector=sector_strength(symbol,exchange,stock=df)
    # Explicit exchange-specific fundamentals; absent values remain null, zeros survive.
    try: info=yf.Ticker(symbol+('.NS' if exchange=='NSE' else '.BO')).info or {}
    except Exception: info={}
    result=dict(available=True,symbol=symbol,exchange=exchange,interval='1d',price=float(df.Close.iloc[-1]),
        timestamp=datetime.now().astimezone().isoformat(),data=dict(df.attrs),official_eod=official,ml=ml,risk=risk,sector=sector,
        fundamentals={'pe':info.get('trailingPE'),'roe':info.get('returnOnEquity'),'mcap':info.get('marketCap'),
            'debt_equity':info.get('debtToEquity'),'div_yield':info.get('dividendYield'),
            'verification':'Provider reported; historical as-of and fundamentals NOT independently verified'},
        execution_authorized=False,note='Completed daily descriptive analysis; not intraday or a trade approval')
    from nifty_scanner import _nan_safe
    result=_nan_safe(result)
    if save: atomic_json(ROOT/f'deep_{symbol}.json',result)
    return result


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Completed daily deep analysis (no execution approval)')
    parser.add_argument('symbol');parser.add_argument('--exchange',choices=['NSE','BSE'],default='NSE')
    args=parser.parse_args();result=run_deep_analysis(args.symbol,args.exchange)
    print(json.dumps(result,indent=2,allow_nan=False))
    if not result.get('available'): raise SystemExit(1)
