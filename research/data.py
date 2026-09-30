"""
research/data.py — standalone OHLCV loader for the research module
================================================================================
App se independent rakha gaya hai taaki study reproducible rahe:
    Tier 1  yfinance (2y daily)
    Tier 2  tvDatafeed (agar yfinance fail ho)
Disk cache: `reports/.cache/<SYMBOL>.csv` (dobara download nahi hota).
"""
import pathlib
import time

import pandas as pd

CACHE = pathlib.Path(__file__).resolve().parent.parent / 'reports' / '.cache'


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.loc[:, [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]]
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['Close'])
    idx = pd.to_datetime(df.index)
    try:
        idx = idx.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    df.index = idx.normalize()
    return df[~df.index.duplicated(keep='last')].sort_index()


def load(symbol: str, period: str = '2y', use_cache: bool = True) -> pd.DataFrame | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f'{symbol.replace("^", "_")}_{period}.csv'
    if use_cache and f.exists() and (time.time() - f.stat().st_mtime) < 12 * 3600:
        try:
            return _norm(pd.read_csv(f, index_col=0, parse_dates=True))
        except Exception:
            pass

    # Tier 1: yfinance
    try:
        import yfinance as yf
        df = yf.download(f'{symbol}.NS', period=period, interval='1d',
                         progress=False, threads=False, auto_adjust=False)
        if df is None or df.empty:
            df = yf.download(f'{symbol}.BO', period=period, interval='1d',
                             progress=False, threads=False, auto_adjust=False)
        if df is not None and not df.empty:
            df = _norm(df)
            if len(df) >= 250:
                df.to_csv(f)
                return df
    except Exception:
        pass

    # Tier 2: tvDatafeed
    try:
        from tvDatafeed import TvDatafeed, Interval
        df = TvDatafeed().get_hist(symbol=symbol, exchange='NSE',
                                   interval=Interval.in_daily, n_bars=520)
        if df is not None and not df.empty:
            df = _norm(df.rename(columns=str.title))
            if len(df) >= 250:
                df.to_csv(f)
                return df
    except Exception:
        pass
    return None
