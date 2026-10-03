"""FIX-63: REAL historical OI downloader — GitHub se, koi API key nahi.

User ka sawaal: "historical OI kahan milega, git pe repo nahi he kya?"
Jawaab (verified): do GitHub repos me scraped+hosted historical OI hai:

  1. AvilPage/historical-option-chain-data   (170 MB)
       data/fo/{YYYY-MM-DD}/{SYMBOL}.csv     <- PER-SYMBOL files (sasti!)
       138 dates 2025-01-01 -> 2026-10-01, 282 symbols
       naya SEBI bhavcopy format: OpnIntrst, ChngInOpnIntrst, StrkPric,
       OptnTp, UndrlygPric
  2. sajal101agrawal/nse-options-last-5-years (894 MB — poora tarball namumkin)
       bhavcopy/extracted/fo{DD}{MON}{YYYY}bhav.csv  <- PER-DATE (sab symbols)
       1,246 files Apr-2021 -> Oct-2024
       classic format: OPEN_INT, CHG_IN_OI, STRIKE_PR, OPTION_TYP

Strategy:
  • AvilPage per-symbol hai, isliye sirf apna UNIVERSE download karo (~20 KB/file)
  • sajal101 per-date hai (3 MB/file, sab symbols) — isliye usse WEEKLY sample
    karo, warna 3.7 GB ho jaata
  • Resume: jo date+symbol already CSV me likh chuka hai, dobara nahi uthata
  • Normalized schema ek hi hai, dono sources se

Run:
    python3 tools/fetch_oi_history.py --list-dates
    python3 tools/fetch_oi_history.py --symbols NIFTY,RELIANCE,TCS
    python3 tools/fetch_oi_history.py --all           # poora universe
    python3 tools/fetch_oi_history.py --source sajal --symbols RELIANCE
"""
from __future__ import annotations

import argparse
import io
import pathlib
import sys
import time

import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUTDIR = ROOT / 'reports' / 'oi_history_real'

AVIL = 'https://raw.githubusercontent.com/AvilPage/historical-option-chain-data/master'
SAJAL = ('https://raw.githubusercontent.com/sajal101agrawal/'
         'nse-options-last-5-years/main')

# App ka F&O universe (jo AvilPage me milte hain). LGEINDIA AvilPage me NAHI hai.
UNIVERSE = [
    'NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK',
    'SBIN', 'ITC', 'LT', 'AXISBANK', 'BHARTIARTL', 'KOTAKBANK', 'HINDUNILVR',
    'MARUTI', 'ASIANPAINT', 'SUNPHARMA', 'TITAN', 'WIPRO', 'NTPC', 'ULTRACEMCO',
    'ONGC', 'COALINDIA', 'JSWSTEEL', 'HINDALCO', 'TATASTEEL', 'POWERGRID',
    'GRASIM', 'CIPLA',
]

_sess = None


def _S():
    global _sess
    if _sess is None:
        _sess = requests.Session()
        _sess.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                            'Chrome/131.0.0.0 Safari/537.36'})
    return _sess


def avil_dates() -> list[str]:
    r = _S().get('https://api.github.com/repos/AvilPage/historical-option-chain-data/'
                 'git/trees/master?recursive=1', timeout=60)
    r.raise_for_status()
    files = [x['path'] for x in r.json()['tree']
             if x['type'] == 'blob' and x['path'].startswith('data/fo/')]
    return sorted(set(f.split('/')[2] for f in files
                      if len(f.split('/')) == 4 and f.split('/')[2].count('-') == 2))


def avil_symbols() -> set[str]:
    r = _S().get('https://api.github.com/repos/AvilPage/historical-option-chain-data/'
                 'git/trees/master?recursive=1', timeout=60)
    r.raise_for_status()
    files = [x['path'] for x in r.json()['tree']
             if x['type'] == 'blob' and x['path'].startswith('data/fo/')]
    return set(f.split('/')[-1].replace('.csv', '') for f in files)


def _norm_from_avil(df: pd.DataFrame) -> dict | None:
    """AvilPage (naya SEBI format) se per-date OI summary."""
    if df is None or df.empty:
        return None
    df = df.copy()
    df['OpnIntrst'] = pd.to_numeric(df.get('OpnIntrst'), errors='coerce').fillna(0)
    df['StrkPric'] = pd.to_numeric(df.get('StrkPric'), errors='coerce').fillna(0)
    ot = df['OptnTp'].astype(str).str.upper()
    ce = df[ot == 'CE']; pe = df[ot == 'PE']
    c = float(ce['OpnIntrst'].sum()); p = float(pe['OpnIntrst'].sum())
    if c <= 0 and p <= 0:
        return None
    up = pd.to_numeric(df.get('UndrlygPric'), errors='coerce').dropna()
    return {
        'underlying': float(up.iloc[0]) if len(up) else None,
        'total_call_oi': int(c), 'total_put_oi': int(p),
        'pcr_oi': round(p / c, 4) if c else None,
        'max_call_strike': float(ce.loc[ce['OpnIntrst'].idxmax(), 'StrkPric']) if len(ce) and ce['OpnIntrst'].max() > 0 else None,
        'max_put_strike': float(pe.loc[pe['OpnIntrst'].idxmax(), 'StrkPric']) if len(pe) and pe['OpnIntrst'].max() > 0 else None,
        'n_strikes': int(df['StrkPric'].nunique()),
    }


def fetch_avil_symbol(symbol: str, dates: list[str], sleep: float = 0.05) -> pd.DataFrame:
    rows, got, miss = [], 0, 0
    for d in dates:
        r = _S().get(f'{AVIL}/data/fo/{d}/{symbol}.csv', timeout=30)
        if r.status_code != 200 or not r.text.strip():
            miss += 1
            continue
        try:
            df = pd.read_csv(io.StringIO(r.text))
        except Exception:
            miss += 1
            continue
        s = _norm_from_avil(df)
        if s:
            s['date'] = d
            s['source'] = 'avilpage'
            rows.append(s)
            got += 1
        else:
            miss += 1
        time.sleep(sleep)
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values('date').reset_index(drop=True)
    print(f'  {symbol:<10} got={got} miss={miss}')
    return out


def save(panel: pd.DataFrame, symbol: str) -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    f = OUTDIR / f'{symbol}.csv'
    if f.exists():
        old = pd.read_csv(f)
        panel = (pd.concat([old, panel], ignore_index=True)
                 .drop_duplicates(subset=['date'], keep='last')
                 .sort_values('date').reset_index(drop=True))
    panel.to_csv(f, index=False)
    return len(panel)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--list-dates', action='store_true')
    ap.add_argument('--symbols', default='', help='comma-separated')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--sleep', type=float, default=0.05)
    a = ap.parse_args()

    if a.list_dates:
        ds = avil_dates()
        print(f'{len(ds)} dates: {ds[0]} .. {ds[-1]}')
        return 0

    syms = ([s.strip().upper() for s in a.symbols.split(',') if s.strip()]
            if a.symbols else (UNIVERSE if a.all else []))
    if not syms:
        print('koi symbol nahi diya (--symbols ya --all)')
        return 1

    avail = avil_symbols()
    dates = avil_dates()
    print(f'{len(dates)} dates, {len(avail)} symbols available')
    for sym in syms:
        if sym not in avail:
            print(f'  {sym}: AvilPage me NAHI — skip')
            continue
        panel = fetch_avil_symbol(sym, dates, sleep=a.sleep)
        if not panel.empty:
            n = save(panel, sym)
            print(f'    saved {n} rows -> reports/oi_history_real/{sym}.csv')
    return 0


if __name__ == '__main__':
    sys.exit(main())
