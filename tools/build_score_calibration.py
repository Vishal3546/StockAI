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
            score = C.stock_rank([fn(window) for fn in funcs])
            if score['complete'] and score['score'] is not None:
                daily[d.isoformat()][symbol] = score['score']
                counted += 1
        print(f'  {symbol:<12} scored {counted} as-of sessions', flush=True)
    history = [{'session': day, 'scores': daily[day]}
               for day in sorted(daily) if len(daily[day]) >= C.MIN_COVERAGE]
    return history[-max_sessions:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=C.ARTIFACT_PATH)
    args = parser.parse_args()
    print(f'FIX-33: Yahoo daily 3y → {C.WINDOW_SESSIONS} past sessions; universe {len(C.UNIVERSE)}')
    frames = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        fut = {pool.submit(download_daily, s): s for s in C.UNIVERSE}
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
    print(f'  latest Yahoo date {last_date}; skipping {exclude} newest session(s) '
          '(exclude current live reference to prevent same-day look-ahead)')
    history = backfill(frames, exclude_latest_sessions=exclude)
    artifact = C.build_artifact(history, A.score_formula_hash(),
                                source='Yahoo Finance daily adjusted OHLCV (no intraday/ML)')
    # Before touching disk, verify the entire artifact against actual history.
    fitted = C.validate_artifact(artifact, A.score_formula_hash())
    path = args.output
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
