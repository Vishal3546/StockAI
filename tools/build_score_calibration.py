#!/usr/bin/env python3
"""FIX-33 · Rebuild comparable 250-session NIFTY-universe score history.

Run from anywhere: python tools/build_score_calibration.py

Requires Yahoo daily OHLCV and the *same* four daily stock engines as app.py.
Use 3y data: 250 trailing bars of warm-up + 250 past scored sessions. The most
recent daily bar is deliberately EXCLUDED; it is the live bar to be classified.
No scanner `composite`/ML, regime, partial candles or made-up minute history.
The JSON contains all 250 per-session symbol scores for audit/reproducibility.
Re-run regularly (<=10 calendar days); otherwise app disables fitted labels.
"""
import argparse
import json
import os
import pathlib
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as A   # noqa: E402 — import after ROOT is in sys.path
import score_calibration as C  # noqa: E402


def download_daily(symbol, period='3y'):
    """Honest Yahoo OHLCV; no fabricated fills for missing prices/volume."""
    try:
        df = yf.download(f'{symbol}.NS', period=period, interval='1d',
                         auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        needed = ['Open', 'High', 'Low', 'Close', 'Volume']
        if not all(k in df for k in needed):
            return None
        df = df[needed].apply(pd.to_numeric, errors='coerce').dropna()
        df = df[(df[['Open', 'High', 'Low', 'Close']] > 0).all(axis=1) & (df['Volume'] >= 0)]
        idx = pd.DatetimeIndex(df.index)
        df.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
        df = df[~df.index.duplicated(keep='last')].sort_index()
        # 250 bars of warm-up plus 250 scored sessions (missing days okay per
        # symbol, but global daily snapshot must cover >=20 actual symbols).
        return df if len(df) >= C.LOOKBACK_BARS + C.WINDOW_SESSIONS else None
    except Exception as exc:
        print(f'    {symbol} download unavailable: {type(exc).__name__}: {exc}')
        return None


def backfill(frames, *, max_sessions=C.WINDOW_SESSIONS, exclude_latest_sessions=1):
    """Past-only scores: historical day t sees each stock's bars <= t, never t+1.

    `frames` accepts preloaded DataFrames so regression tests can run offline.
    If today's NSE session is ongoing and Yahoo has a partial daily bar, skip
    today AND yesterday (yesterday is the live rank's reference bar).
    """
    if not frames:
        raise C.CalibrationError('no daily histories')
    all_dates = sorted({t.date() for df in frames.values() for t in df.index})
    if len(all_dates) <= exclude_latest_sessions:
        raise C.CalibrationError('not enough distinct dates for calibration')
    latest = all_dates[-exclude_latest_sessions]
    candidate_days = [d for d in all_dates if d < latest]
    if not candidate_days:
        raise C.CalibrationError('no completed prior sessions')
    # Keep a small cushion for holidays/coverage gaps before final 250.
    min_day = candidate_days[max(0, len(candidate_days) - max_sessions - 40)]
    daily = defaultdict(dict)
    engines_daily = defaultdict(dict)   # H-11: per-engine scores bhi record karo
    funcs = (A.engine_volume_profile, A.engine_rvol_cvd, A.engine_vcp, A.engine_smc)
    for symbol in C.UNIVERSE:
        df = frames.get(symbol)
        if df is None or len(df) < C.LOOKBACK_BARS:
            continue
        counted = 0
        for i in range(C.LOOKBACK_BARS - 1, len(df)):
            d = df.index[i].date()
            if d < min_day or d >= latest:
                continue
            window = df.iloc[i + 1 - C.LOOKBACK_BARS:i + 1]
            engines = [fn(window) for fn in funcs]
            score = C.stock_rank(engines)
            if score['complete'] and score['score'] is not None:
                daily[d.isoformat()][symbol] = score['score']
                # H-11: raw engine scores (chhote keys) — inhi se composite dobara
                # banaya ja sakta hai, isliye weight redesign offline analyse hota hai.
                engines_daily[d.isoformat()][symbol] = {
                    C.ENGINE_KEYS_REV[e['name']]: int(round(e['score']))
                    for e in engines if e.get('name') in C.ENGINE_KEYS_REV
                }
                counted += 1
        print(f'  {symbol:<12} scored {counted} as-of sessions', flush=True)
    history = [{'session': day, 'scores': daily[day],
                'engines': engines_daily[day]}
               for day in sorted(daily) if len(daily[day]) >= C.MIN_COVERAGE]
    return history[-max_sessions:]


def download_daily_tv(symbol, exchange='BSE', n_bars=1400):
    """FIX-84 (Phase 2): BSE ke liye TradingView se daily OHLCV.

    Yahoo ke paas BSE historicals hote hi nahi — `.BO` par RELIANCE ke liye
    `max` range par bhi sirf 1 row aata tha (measure kiya). TradingView ke paas
    poori history hai: 1400 bars, 2021-02-12 se. NSE/BSE ke prices genuinely
    alag hote hain (RELIANCE 1207.70 vs 1206.65), isliye BSE ka apna fit chahiye.

    Output shape download_daily() jaisa hi rakha hai taaki backfill()/build_artifact()
    bina badlav ke dono source par chalein.
    """
    df, exch = A.DATA_MANAGER.fetch_tradingview(symbol, n_bars=n_bars,
                                                interval_str='1d',
                                                prefer_exch=exchange)
    if df is None or len(df) == 0:
        return None
    if str(exch or '').upper() != str(exchange).upper():
        # Strict: BSE maanga aur NSE mila to is artifact ko BSE kehna jhooth hoga.
        raise C.CalibrationError(
            f'{symbol}: {exchange} maanga tha par source ne {exch} diya')
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    rename = {c.lower(): c.capitalize() for c in df.columns}
    df = df.rename(columns=rename)
    needed = ['Open', 'High', 'Low', 'Close', 'Volume']
    if not all(k in df for k in needed):
        return None
    df = df[needed].apply(pd.to_numeric, errors='coerce').dropna()
    df = df[(df[['Open', 'High', 'Low', 'Close']] > 0).all(axis=1) & (df['Volume'] >= 0)]
    idx = pd.DatetimeIndex(df.index)
    df.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
    # FIX-84: TradingView ke daily feed me kabhi-kabhi weekend dates aa jaati hain
    # (RELIANCE par 6 mili: 2023-11-12 Sun, 2024-01-20 Sat, 2024-03-02 Sat,
    #  2024-05-18 Sat, 2025-02-01 Sat, 2026-02-01 Sun). NSE/BSE cash session
    # weekend par hota hi nahi, aur score_calibration.validate_artifact() inhe
    # reject karta hai ("NSE cash session cannot be a weekend") — sahi karta hai.
    # Yahoo me ye dates aati nahi, isliye ye sirf TradingView-source ka filter hai.
    _we = df.index.dayofweek >= 5
    if _we.any():
        df = df[~_we]
    df = df[~df.index.duplicated(keep='last')].sort_index()
    return df if len(df) >= C.LOOKBACK_BARS + C.WINDOW_SESSIONS else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=None,
                        help='default: exchange ke hisaab se score_calibration[_bse].json')
    parser.add_argument('--exchange', choices=('NSE', 'BSE'), default='NSE',
                        help='NSE = Yahoo .NS (purana rasta); BSE = TradingView (FIX-84)')
    args = parser.parse_args()
    exch = args.exchange
    # FIX-84: universe wahi 30 naam, par BSE par bhi wahi tickers chalte hain
    # (TradingView symbol exchange-param se resolve karta hai).
    if exch == 'BSE':
        fetch = lambda s: download_daily_tv(s, 'BSE')
        src_label = 'TradingView daily OHLCV (BSE)'
        print(f'FIX-84: TradingView daily → {C.WINDOW_SESSIONS} past sessions; '
              f'universe {len(C.UNIVERSE)} (BSE)')
    else:
        fetch = lambda s: download_daily(s)
        src_label = 'Yahoo Finance daily adjusted OHLCV (no intraday/ML)'
        print(f'FIX-33: Yahoo daily 3y → {C.WINDOW_SESSIONS} past sessions; universe {len(C.UNIVERSE)}')
    frames = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        fut = {pool.submit(fetch, s): s for s in C.UNIVERSE}
        for job in as_completed(fut):
            symbol = fut[job]
            try:
                df = job.result()
                if df is not None:
                    frames[symbol] = df
                    print(f'  fetched {symbol:<12} {len(df)} bars, last {df.index[-1].date()}')
                else:
                    print(f'  SKIP {symbol:<12} <500 clean daily bars / unavailable')
            except Exception as exc:
                print(f'  SKIP {symbol}: {exc}')
    if len(frames) < C.MIN_COVERAGE:
        raise C.CalibrationError(f'only {len(frames)} usable symbols (need {C.MIN_COVERAGE}); no artifact written')
    from datetime import datetime
    now_ist = datetime.now(A.IST)
    last_date = max(df.index[-1].date() for df in frames.values())
    exclude = 2 if A.is_market_open(now_ist) and last_date == now_ist.date() else 1
    print(f'  latest {exch} date {last_date}; skipping {exclude} newest session(s) '
          '(exclude current live reference to prevent same-day look-ahead)')
    history = backfill(frames, exclude_latest_sessions=exclude)
    artifact = C.build_artifact(history, A.score_formula_hash(), source=src_label)
    # FIX-84: artifact me exchange bhi record karo — taaki app verify kar sake ki
    # BSE frame par BSE ka fit lag raha hai, NSE ka nahi.
    artifact['exchange'] = exch
    # Before touching disk, verify the entire artifact against actual history.
    fitted = C.validate_artifact(artifact, A.score_formula_hash())
    path = args.output or C.artifact_path(exch)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    try:
        with temp.open('w', encoding='utf8') as f:
            # Human-auditable snapshots (avoid a 100kB single line in git-am patches).
            json.dump(artifact, f, allow_nan=False, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    th = fitted['thresholds']
    print(f'\n✅ {path}: {fitted["sessions"]} sessions, {fitted["samples"]} scores, '
          f'{fitted["universe_covered"]} symbols; asof {fitted["asof_session"]}')
    print('   rank bands: bottom≤p10={short_sell:g}, watch≥p40={watchlist:g}, '
          'dip≥p80={buy_dip:g}, breakout≥p95={buy_breakout:g}'.format(**th))
    print('   REMINDER: percentile = relative rank, NOT a profitable/backtested signal.')


if __name__ == '__main__':
    try:
        main()
    except C.CalibrationError as exc:
        print(f'❌ Calibration NOT written: {exc}', file=sys.stderr)
        sys.exit(1)
