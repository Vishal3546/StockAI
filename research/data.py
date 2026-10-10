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


# FIX-60: period -> bars. 247 NSE trading days/saal (measured, backtest.py me bhi).
_PERIOD_BARS = {'6mo': 124, '1y': 247, '2y': 494, '3y': 741, '5y': 1235, '10y': 2470}


def _period_bars(period: str) -> int:
    """`'5y'` -> 1235. Unknown/blank par 2y (purana default behaviour)."""
    try:
        return int(_PERIOD_BARS.get(str(period).strip().lower(), _PERIOD_BARS['2y']))
    except Exception:
        return _PERIOD_BARS['2y']


def load(symbol: str, period: str = '2y', use_cache: bool = True, exchange: str = 'NSE') -> pd.DataFrame | None:
    exchange = str(exchange).upper()
    if exchange not in ('NSE', 'BSE'): raise ValueError('invalid exchange')
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f'{symbol.replace("^", "_")}_{exchange}_{period}_raw.csv'
    if use_cache and f.exists() and (time.time() - f.stat().st_mtime) < 12 * 3600:
        try:
            return _norm(pd.read_csv(f, index_col=0, parse_dates=True))
        except Exception:
            pass

    # Tier 1: yfinance
    try:
        import yfinance as yf
        df = yf.download(f'{symbol}{".BO" if exchange == "BSE" else ".NS"}', period=period, interval='1d',
                         progress=False, threads=False, auto_adjust=False)
        if df is not None and not df.empty:
            df = _norm(df)
            if len(df) >= 250:
                df.to_csv(f)
                return df
    except Exception:
        pass

    # Tier 2: tvDatafeed
    # FIX-60: pehle yahan `n_bars=520` HARDCODED tha — `period` pass hi nahi hota
    # tha. Matlab `--period 5y`/`10y` maangne par bhi ~2 saal milta tha. Aur cache
    # file `{symbol}_{period}.csv` naam se banti thi, isliye 520 bars
    # `RELIANCE_5y.csv` me 12 ghante tak "5 saal ka data" bankar serve hote the —
    # measured: period='5y' -> 520 bars (2024-08-29..2026-10-01), wahi '10y' par bhi.
    # Ab period honour hota hai, aur cache tabhi likhte hain jab data maangi hui
    # range ke kareeb ho (warna file apne contents ke baare me jhooth bolegi).
    want = _period_bars(period)
    try:
        from tvDatafeed import TvDatafeed, Interval
        df = TvDatafeed().get_hist(symbol=symbol, exchange=exchange,
                                   interval=Interval.in_daily, n_bars=want)
        if df is not None and not df.empty:
            df = _norm(df.rename(columns=str.title))
            if len(df) >= 250:
                # Kam se kam 80% maangi hui depth mile tabhi is naam se cache karo.
                if len(df) >= 0.8 * want:
                    df.to_csv(f)
                return df
    except Exception:
        pass
    return None
