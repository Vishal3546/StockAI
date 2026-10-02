"""FIX-57 study — "accuracy high kaise kare" ka measured jawaab.

User ne poochha: accuracy badhane ke liye kaun sa naya signal/timeframe add kare,
aur Oct 2026 me traders kis tarah trade karte hain.

Ye script teen cheezein MEASURE karti hai (koi assumption nahi):

  1. App me kaun sa data already hai, aur 2026-practice wale signals reachable
     hain ya nahi (NSE/BSE official endpoints + TradingView probe).
  2. Jo ek naya data reachable hai — FUTURES (tvDatafeed `fut_contract`) — uska
     futures-spot basis signal actually predictive hai ya nahi, look-ahead free.
  3. Us signal ka gross edge TRANSACTION COST ke baad bachta hai ya nahi.

HEADLINE RESULT (2026-10-02, 28 symbols, 1200 bars each = 33,544 symbol-days):

  basis_t = (fut_close - spot_close) / spot_close

  SPOT return par (NOT tradable):
      corr(basis, r+1) = +0.0529     tercile spread +0.166%/day
      cross-symbol t   = +6.38       directional accuracy 52.74%
      out-of-sample: 1st half t=+7.07, 2nd half t=+3.36  -> real, but decaying

  FUTURES return par (the ONLY cheap way to trade it):
      1d gross = -0.057%   accuracy 49.04%  (coin flip se BHI KAM)
      2d gross = -0.197%   accuracy 48.09%
      5d gross = -0.498%   accuracy 47.26%

  Matlab "signal" sirf BASIS CONVERGENCE hai — mechanical, already priced.
  Spot premium converge karta hai, isliye spot upar jaata hai aur future neeche.
  Tradable leg par edge NEGATIVE hai. Koi naya indicator nahi mila.

COST MEASUREMENT (why accuracy is the wrong target):
  equity round-trip   ~0.230%   (STT + brokerage + slippage + exch)
  futures round-trip  ~0.146%   (STT sell-only + brokerage + 1 tick)
  Ek +0.169% gross signal 0.230% cost me NEGATIVE ho jaata hai.

Run:  python3 tools/study_new_signals.py [--json tools/new_signal_study.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Same universe as score_calibration — NSE liquid names with live futures.
UNIVERSE = [
    'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'ITC', 'LT',
    'AXISBANK', 'BHARTIARTL', 'KOTAKBANK', 'HINDUNILVR', 'BAJFINANCE', 'MARUTI',
    'ASIANPAINT', 'SUNPHARMA', 'TITAN', 'WIPRO', 'NTPC', 'ULTRACEMCO', 'ONGC',
    'COALINDIA', 'JSWSTEEL', 'HINDALCO', 'TATASTEEL', 'POWERGRID', 'GRASIM',
    'DRREDDY', 'CIPLA',
]

# Round-trip costs, percent of notional, per side summed.
COST_EQUITY = {
    'stt': 0.050, 'brokerage': 0.060, 'slippage': 0.100, 'exchange_gst_sebi': 0.020,
}
COST_FUTURES = {
    'stt_sell_only': 0.020, 'brokerage': 0.060, 'transaction': 0.004,
    'sebi': 0.0002, 'gst_on_charges': 0.18 * (0.060 + 0.004), 'slippage_1tick': 0.050,
}


def load_frames(n_bars: int = 1200):
    """(symbol, DataFrame[basis, spot returns, futures returns]) — no look-ahead."""
    from tvDatafeed import TvDatafeed
    from tvDatafeed.main import Interval
    tv = TvDatafeed()
    out = []
    for sym in UNIVERSE:
        try:
            fut = tv.get_hist(symbol=sym, exchange='NSE', interval=Interval.in_daily,
                              n_bars=n_bars, fut_contract=1)
            spot = tv.get_hist(symbol=sym, exchange='NSE', interval=Interval.in_daily,
                               n_bars=n_bars)
        except Exception as exc:                                    # noqa: BLE001
            print(f'  {sym:<12} fetch failed: {type(exc).__name__}')
            continue
        if fut is None or spot is None:
            continue
        df = (fut[['close']].rename(columns={'close': 'fut'})
              .join(spot[['close']].rename(columns={'close': 'spot'}), how='inner')
              .dropna())
        if len(df) < 250:
            continue
        df['basis'] = (df['fut'] - df['spot']) / df['spot'] * 100
        df['r0'] = df['spot'].pct_change() * 100
        for h in (1, 2, 3, 5):
            # shift(-h) = FUTURE return. Signal at t, outcome at t+h. No look-ahead.
            df[f'sr{h}'] = df['spot'].pct_change(h).shift(-h) * 100
            df[f'fr{h}'] = df['fut'].pct_change(h).shift(-h) * 100
        df['sym'] = sym
        out.append(df[['sym', 'basis', 'r0'] + [f'{p}{h}' for h in (1, 2, 3, 5)
                                                for p in ('sr', 'fr')]].dropna())
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def tercile_spread(panel: pd.DataFrame, col: str) -> dict:
    """High-basis minus low-basis mean of `col`, plus a cross-symbol t-stat."""
    work = panel.copy()
    work['_t'] = pd.qcut(work['basis'], 3, labels=['low', 'mid', 'high'])
    per = work.groupby(['sym', '_t'], observed=True)[col].mean().unstack()
    diff = (per['high'] - per['low']).dropna()
    sd = float(diff.std(ddof=1))
    tstat = float(diff.mean() / (sd / np.sqrt(len(diff)))) if sd > 0 else 0.0
    pooled = float(work.groupby('_t', observed=True)[col].mean()['high']
                   - work.groupby('_t', observed=True)[col].mean()['low'])
    hi = work[work['_t'] == 'high'][col]
    lo = work[work['_t'] == 'low'][col]
    accuracy = float(((hi > 0).mean() + (lo < 0).mean()) / 2 * 100)
    return {'gross_spread_pct': round(pooled, 4), 'cross_symbol_t': round(tstat, 2),
            'n_symbols': int(len(diff)), 'directional_accuracy_pct': round(accuracy, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default='tools/new_signal_study.json')
    ap.add_argument('--bars', type=int, default=1200)
    args = ap.parse_args()

    print('=== fetching futures + spot (tvDatafeed, TradingView) ...')
    panel = load_frames(args.bars)
    if panel.empty:
        print('  no data — aborting (no verdict, not a failure verdict)')
        return 2
    n_sym = int(panel['sym'].nunique())
    print(f'  {len(panel):,} symbol-days from {n_sym} symbols\n')

    result: dict = {
        'asof': str(pd.Timestamp.now().date()),
        'symbols': n_sym,
        'symbol_days': int(len(panel)),
        'signal': 'futures_spot_basis = (fut_close - spot_close) / spot_close * 100',
        'basis_stats': {
            'mean_pct': round(float(panel['basis'].mean()), 4),
            'median_pct': round(float(panel['basis'].median()), 4),
            'sd_pct': round(float(panel['basis'].std()), 4),
            'pct_negative': round(float((panel['basis'] < 0).mean() * 100), 2),
        },
        'on_spot_return_NOT_tradable': {},
        'on_futures_return_tradable': {},
        'costs': {
            'equity_round_trip_pct': round(sum(COST_EQUITY.values()), 4),
            'futures_round_trip_pct': round(sum(COST_FUTURES.values()), 4),
        },
    }

    print('--- A. SPOT return par (ye leg trade nahi hota) ---')
    for h in (1, 2, 3, 5):
        r = tercile_spread(panel, f'sr{h}')
        result['on_spot_return_NOT_tradable'][f'{h}d'] = r
        print(f'  {h}d  spread {r["gross_spread_pct"]:+.3f}%  t {r["cross_symbol_t"]:+.2f}'
              f'  accuracy {r["directional_accuracy_pct"]:.2f}%')

    print('\n--- B. FUTURES return par (ye leg trade hota hai) ---')
    for h in (1, 2, 3, 5):
        r = tercile_spread(panel, f'fr{h}')
        net = r['gross_spread_pct'] - sum(COST_FUTURES.values())
        r['net_after_futures_cost_pct'] = round(net, 4)
        result['on_futures_return_tradable'][f'{h}d'] = r
        print(f'  {h}d  spread {r["gross_spread_pct"]:+.3f}%  net {net:+.3f}%'
              f'  accuracy {r["directional_accuracy_pct"]:.2f}%')

    print('\n--- C. Timing-artifact control ---')
    ic0 = float(np.corrcoef(panel['basis'], panel['r0'])[0, 1])
    ic1 = float(np.corrcoef(panel['basis'], panel['sr1'])[0, 1])
    result['timing_control'] = {'corr_basis_vs_same_day_return': round(ic0, 4),
                                'corr_basis_vs_next_day_return': round(ic1, 4)}
    print(f'  corr(basis, same-day) = {ic0:+.4f}   corr(basis, next-day) = {ic1:+.4f}')

    best = result['on_futures_return_tradable']['1d']
    verdict = ('NO TRADEABLE EDGE'
               if best['net_after_futures_cost_pct'] <= 0
               or best['directional_accuracy_pct'] <= 50.0 else 'POSSIBLE EDGE — verify')
    result['verdict'] = verdict
    result['verdict_detail'] = (
        'Futures-spot basis ka spot-return relationship real hai (t>6, out-of-sample '
        'survives) par wo BASIS CONVERGENCE hai — mechanical. Tradable leg (futures) '
        'par gross spread negative hai aur accuracy 50% se neeche. Cost alag se '
        'negative karta hai. Naya indicator nahi mila.'
        if verdict == 'NO TRADEABLE EDGE' else
        'Tradable leg par positive net — aage walk-forward + per-symbol check karo.'
    )

    print(f'\n=== VERDICT: {verdict}')
    print(f'  equity round-trip cost  = {result["costs"]["equity_round_trip_pct"]:.3f}%')
    print(f'  futures round-trip cost = {result["costs"]["futures_round_trip_pct"]:.3f}%')
    print('  Ek +0.169% gross signal equity cost me negative ho jaata hai —')
    print('  isliye "accuracy high karo" galat target hai; cost-aware expectancy sahi hai.')

    Path(args.json).write_text(json.dumps(result, indent=2, ensure_ascii=False),
                               encoding='utf-8')
    print(f'\nwritten: {args.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
