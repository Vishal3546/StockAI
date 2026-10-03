"""FIX-61 study — OI/PCR/IV: kya add karna chahiye? (measured jawaab)

User ne poochha: "OI / PCR / IV / delivery nahi hai to add karna padega kya"

Pehle REACHABILITY measure ki (koi guess nahi):

  ✅ LIVE option chain  — GET /api/option-chain-v3?symbol=X&expiry=DD-MMM-YYYY
     248,963 B JSON, 116 strikes, fields: openInterest, changeinOpenInterest,
     impliedVolatility, lastPrice, totalTradedVolume. NIFTY par compute kiya:
       PCR(OI) = 2,054,451 / 2,998,319 = 0.6852
       max call OI strike = 23,000 (resistance)
       max put  OI strike = 22,000 (support)
     Stock-level bhi chalta hai (RELIANCE → 6,378 B).
     ⚠️ Teen cheezein zaroori hain warna fail hota hai:
        1. `expiry` param REQUIRED — bina uske `{}` (2 B) milta hai
        2. cookie handshake (Akamai: AKA_A2 / _abck / bm_sz)
        3. `Accept-Encoding: identity` — warna body decode nahi hoti
     NOTE: purana path `option-chain-indices` ab 404 hai — path MOVE ho chuka hai.

  ❌ HISTORICAL OI time series — kahin nahi mila:
       archives fo{DD}{MON}{YYYY}bhav.csv.zip        404 (dono hosts, 3 dates)
       BhavCopy_NSE_FO_0_0_0_{DDMMYYYY}_F_0000.csv   404 (naya naming bhi)
       fo_participant_{DDMMYYYY}.csv                 404 (FII/DII OI)
       /api/historical-oi, /api/oi-history, /api/fo-quote-history ... sab 404

  ⚠️ Matlab: **bina OI history ke OI signal backtest NAHI ho sakta.** Isliye ye
     script do kaam karta hai:

     MODE 1 (--proxy, default) — jo AAJ measure ho sakta hai:
       Futures VOLUME ek reachable proxy hai derivatives participation ka, aur
       uski 1200 bars ki history tvDatafeed se milti hai. Signal:
         fv_ratio  = futures_volume / spot_volume        (F&O intensity)
         fv_rvol   = futures_volume / 20d avg            (aaj unusual?)
       Look-ahead free: signal bar t, outcome bar t+h.

     MODE 2 (--collect SYMBOL) — real OI history banana shuru karo:
       Live option chain ka snapshot `reports/oi_history/<SYM>.csv` me append.
       Roz chalaao; 3-6 mahine me asli OI study possible hogi.

Run:
    python3 tools/study_oi_signal.py                  # proxy study (abhi)
    python3 tools/study_oi_signal.py --collect NIFTY  # OI snapshot save
    python3 tools/study_oi_signal.py --json tools/oi_signal_study.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Same universe as the other studies — NSE liquid names with live futures.
UNIVERSE = [
    'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'ITC', 'LT',
    'AXISBANK', 'BHARTIARTL', 'KOTAKBANK', 'HINDUNILVR', 'BAJFINANCE', 'MARUTI',
    'ASIANPAINT', 'SUNPHARMA', 'TITAN', 'WIPRO', 'NTPC', 'ULTRACEMCO', 'ONGC',
    'COALINDIA', 'JSWSTEEL', 'HINDALCO', 'TATASTEEL', 'POWERGRID', 'GRASIM',
    'CIPLA',
]

# Futures round-trip cost, % of notional (research/costs.py se consistent).
FUTURES_ROUND_TRIP_PCT = 0.146


# ══════════════════════════════════════════════════════════════════════════
#  NSE option chain — verified working recipe
# ══════════════════════════════════════════════════════════════════════════
def _session():
    import requests
    s = requests.Session()
    s.headers.update({
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'),
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    # Cookie handshake — Akamai iske bina API se khaali response deta hai.
    try:
        s.get('https://www.nseindia.com/option-chain', timeout=20)
    except Exception:
        pass
    return s


def fetch_option_chain(symbol: str, expiry: str | None = None, timeout: int = 25) -> dict | None:
    """Live option chain. `expiry` REQUIRED — bina uske NSE `{}` deta hai.

    Returns dict(pcr_oi, total_call_oi, total_put_oi, max_call_strike,
                 max_put_strike, spot, n_strikes, expiry) ya None.
    """
    s = _session()
    ajax = {'Accept': 'application/json, text/javascript, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': 'https://www.nseindia.com/option-chain',
            # identity zaroori — compressed body yahan decode nahi hoti
            'Accept-Encoding': 'identity'}

    def _get(url: str):
        try:
            r = s.get(url, headers=ajax, timeout=timeout)
            if r.status_code != 200 or not r.text or r.text[:1] not in '{[':
                return None
            return r.json()
        except Exception:
            return None

    if expiry is None:
        ci = _get(f'https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}')
        if not ci or not ci.get('expiryDates'):
            return None
        expiry = ci['expiryDates'][0]

    j = _get(f'https://www.nseindia.com/api/option-chain-v3?symbol={symbol}&expiry={expiry}')
    if not j:
        return None
    rec = j.get('records') or {}
    rows = rec.get('data') or []
    if not rows:
        return None

    tot_c = sum((r.get('CE') or {}).get('openInterest', 0) or 0 for r in rows)
    tot_p = sum((r.get('PE') or {}).get('openInterest', 0) or 0 for r in rows)
    mc = max(rows, key=lambda r: (r.get('CE') or {}).get('openInterest', 0) or 0)
    mp = max(rows, key=lambda r: (r.get('PE') or {}).get('openInterest', 0) or 0)
    ivs = [c.get('impliedVolatility') or 0 for r in rows
           for c in ((r.get('CE'), r.get('PE'))) if c and (c.get('impliedVolatility') or 0) > 0]
    return {
        'asof': str(pd.Timestamp.now())[:19],
        'symbol': symbol, 'expiry': expiry,
        'spot': rec.get('underlyingValue'),
        'n_strikes': len(rows),
        'total_call_oi': int(tot_c), 'total_put_oi': int(tot_p),
        'pcr_oi': round(tot_p / tot_c, 4) if tot_c else None,
        'max_call_strike': mc.get('strikePrice'),
        'max_put_strike': mp.get('strikePrice'),
        'atm_iv': round(float(np.median(ivs)), 2) if ivs else None,
    }


def collect(symbol: str, outdir: pathlib.Path | None = None) -> int:
    """Ek snapshot append karo. Roz chalaao — history banegi."""
    d = outdir or (ROOT / 'reports' / 'oi_history')
    d.mkdir(parents=True, exist_ok=True)
    snap = fetch_option_chain(symbol)
    if not snap:
        print(f'  ⚠️  {symbol}: option chain nahi mila')
        return 1
    f = d / f'{symbol.replace("^", "_")}.csv'
    df = pd.DataFrame([snap])
    new = not f.exists()
    df.to_csv(f, mode='a', header=new, index=False)
    print(f'  ✅ {symbol}: PCR(OI)={snap["pcr_oi"]}  spot={snap["spot"]}  '
          f'call-wall={snap["max_call_strike"]} put-wall={snap["max_put_strike"]}  '
          f'IV={snap["atm_iv"]}  -> {f.relative_to(ROOT)}')
    return 0


# ══════════════════════════════════════════════════════════════════════════
#  MODE 1: futures-volume proxy (jis par AAJ study ho sakti hai)
# ══════════════════════════════════════════════════════════════════════════
def load_proxy(n_bars: int = 1200) -> pd.DataFrame:
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
        except Exception:
            continue
        if fut is None or spot is None:
            continue
        df = (fut[['close', 'volume']].rename(columns={'close': 'f_close', 'volume': 'f_vol'})
              .join(spot[['close', 'volume']].rename(columns={'close': 's_close',
                                                             'volume': 's_vol'}),
                    how='inner').dropna())
        if len(df) < 250:
            continue
        # ── signals (sab PAST data se) ──
        df['fv_ratio'] = df['f_vol'] / (df['s_vol'] + 1)
        df['fv_rvol'] = df['f_vol'] / (df['f_vol'].rolling(20).mean() + 1)
        # ── PAST returns (momentum control ke liye — sirf backward) ──
        for h in (5, 20):
            df[f'past{h}'] = df['s_close'].pct_change(h) * 100
        # ── outcomes (FUTURE, shift(-h)) — no look-ahead ──
        for h in (1, 2, 3, 5):
            df[f'r{h}'] = df['s_close'].pct_change(h).shift(-h) * 100
        df['sym'] = sym
        out.append(df[['sym', 'fv_ratio', 'fv_rvol', 'past5', 'past20']
                      + [f'r{h}' for h in (1, 2, 3, 5)]].dropna())
        print(f'  {sym:<12} bars={len(df):>5}  fv_ratio mean={df["fv_ratio"].mean():.2f}')
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def tercile(panel: pd.DataFrame, signal: str, outcome: str) -> dict:
    w = panel.dropna(subset=[signal, outcome]).copy()
    if len(w) < 100:
        return {}
    w['_t'] = pd.qcut(w[signal], 3, labels=['low', 'mid', 'high'], duplicates='drop')
    if w['_t'].nunique() < 3:
        return {}
    per = w.groupby(['sym', '_t'], observed=True)[outcome].mean().unstack()
    if not {'low', 'high'}.issubset(per.columns):
        return {}
    diff = (per['high'] - per['low']).dropna()
    sd = float(diff.std(ddof=1))
    t = float(diff.mean() / (sd / np.sqrt(len(diff)))) if sd > 0 else 0.0
    hi, lo = w[w['_t'] == 'high'][outcome], w[w['_t'] == 'low'][outcome]
    gross = float(w.groupby('_t', observed=True)[outcome].mean()['high']
                  - w.groupby('_t', observed=True)[outcome].mean()['low'])
    return {
        'gross_spread_pct': round(gross, 4),
        'net_after_cost_pct': round(gross - FUTURES_ROUND_TRIP_PCT, 4),
        'cross_symbol_t': round(t, 2),
        'n_symbols': int(len(diff)),
        'directional_accuracy_pct': round(float(((hi > 0).mean() + (lo < 0).mean()) / 2 * 100), 2),
    }


def controls(panel: pd.DataFrame, signal: str = 'fv_ratio', horizon: int = 5) -> dict:
    """Char controls — FIX-57 ka lesson: headline number par rukna nahi.

    FIX-57 me futures-basis ka t=+6.21 / 52.78% accuracy "strong" dikha tha aur wo
    BASIS CONVERGENCE nikla — tradable leg par negative. Isliye ye checks har
    naye signal par mandatory hain.
    """
    w = panel.dropna(subset=[signal, f'r{horizon}']).copy()
    if len(w) < 200:
        return {'error': 'not enough data'}
    w['_t'] = pd.qcut(w[signal], 3, labels=['l', 'm', 'h'], duplicates='drop')

    # 1) MOMENTUM — kya ye sirf past return ka doosra naam hai?
    # ⚠️ Pehle maine yahan `r5.shift(1).rolling(5).sum()` use kiya tha — usme
    # LOOK-AHEAD tha, kyunki r5 khud forward return hai. Control hi leaky ho to
    # wo control nahi. Ab `past5` / `past20` = pure backward-looking returns.
    m = w.dropna(subset=['past5', 'past20'])
    import numpy.linalg as la
    y = m[f'r{horizon}'].values
    X2 = np.column_stack([np.ones(len(m)), m['past5'].values, m['past20'].values])
    X3 = np.column_stack([np.ones(len(m)), m['past5'].values, m['past20'].values,
                          m[signal].values])
    b2, *_ = la.lstsq(X2, y, rcond=None)
    b3, *_ = la.lstsq(X3, y, rcond=None)
    r2_mom = float(1 - np.var(y - X2 @ b2) / np.var(y))
    r2_full = float(1 - np.var(y - X3 @ b3) / np.var(y))
    _sig_coef = float(b3[3])

    # 2) OUT-OF-SAMPLE split — decay hota hai ya nahi?
    half = len(w) // 2
    oos = {}
    for lab, sub in (('first_half', w.iloc[:half]), ('second_half', w.iloc[half:])):
        if sub['_t'].nunique() < 3:
            continue
        per = sub.groupby(['sym', '_t'], observed=True)[f'r{horizon}'].mean().unstack()
        if {'l', 'h'}.issubset(per.columns):
            d = (per['h'] - per['l']).dropna()
            oos[lab] = {'gross_pct': round(float(d.mean()), 4), 'n_symbols': int(len(d))}

    # 3) LONG-ONLY framing — retail easily short nahi kar sakta, isliye spread
    #    nahi, high-tercile minus UNIVERSE hi asli tradeable edge hai.
    uni = float(w[f'r{horizon}'].mean())
    hi = w[w['_t'] == 'h']
    lo = w[w['_t'] == 'l']
    long_only_excess = float(hi[f'r{horizon}'].mean() - uni)

    # 4) PER-SYMBOL consistency — ek outlier to nahi chala raha?
    per = w.groupby(['sym', '_t'], observed=True)[f'r{horizon}'].mean().unstack()
    d = (per['h'] - per['l']).dropna()

    return {
        'momentum': {'controls_used': 'past5 + past20 (pure backward-looking)',
                     'r2_momentum_only': round(r2_mom, 6), 'r2_with_signal': round(r2_full, 6),
                     'r2_delta': round(r2_full - r2_mom, 6),
                     'signal_coeff_after_momentum': round(_sig_coef, 6)},
        'out_of_sample': oos,
        'long_only': {'universe_mean_pct': round(uni, 4),
                      'high_tercile_mean_pct': round(float(hi[f'r{horizon}'].mean()), 4),
                      'low_tercile_mean_pct': round(float(lo[f'r{horizon}'].mean()), 4),
                      'excess_vs_universe_pct': round(long_only_excess, 4),
                      'net_after_cost_pct': round(long_only_excess - FUTURES_ROUND_TRIP_PCT, 4)},
        'per_symbol': {'positive': int((d > 0).sum()), 'total': int(len(d)),
                       'median_pct': round(float(d.median()), 4),
                       'worst_pct': round(float(d.min()), 4),
                       'best_pct': round(float(d.max()), 4)},
    }


def run_proxy(n_bars: int, panel: pd.DataFrame | None = None,
              walk_forward: dict | None = None) -> dict:
    if panel is None:
        print('=== fetching futures + spot (tvDatafeed) ...')
        panel = load_proxy(n_bars)
    if panel is None or panel.empty:
        return {'verdict': 'NO DATA', 'detail': 'tvDatafeed se data nahi mila — koi verdict nahi'}
    res = {
        'asof': str(pd.Timestamp.now().date()),
        'symbols': int(panel['sym'].nunique()),
        'symbol_days': int(len(panel)),
        'proxy_signals': ['fv_ratio = futures_vol / spot_vol', 'fv_rvol = fut_vol / 20d_avg'],
        'futures_round_trip_cost_pct': FUTURES_ROUND_TRIP_PCT,
        'results': {},
    }
    for sig in ('fv_ratio', 'fv_rvol'):
        res['results'][sig] = {}
        print(f'\n--- signal: {sig} ---')
        for h in (1, 2, 3, 5):
            r = tercile(panel, sig, f'r{h}')
            if not r:
                continue
            res['results'][sig][f'{h}d'] = r
            print(f'  {h}d  gross {r["gross_spread_pct"]:+.3f}%  net {r["net_after_cost_pct"]:+.3f}%'
                  f'  t {r["cross_symbol_t"]:+.2f}  accuracy {r["directional_accuracy_pct"]:.2f}%')

    print('\n--- CONTROLS (fv_ratio, 5d) — headline par rukna nahi ---')
    ctl = controls(panel, 'fv_ratio', 5)
    res['controls_fv_ratio_5d'] = ctl
    if 'error' not in ctl:
        mo = ctl['momentum']
        print(f"  momentum: R2 {mo['r2_momentum_only']:.5f} -> {mo['r2_with_signal']:.5f} "
              f"(delta {mo['r2_delta']:+.6f}), coeff {mo['signal_coeff_after_momentum']:+.5f}")
        for k, v in ctl['out_of_sample'].items():
            print(f"  OOS {k:<12} gross {v['gross_pct']:+.3f}%  (n={v['n_symbols']})")
        lo_ = ctl['long_only']
        print(f"  long-only: high {lo_['high_tercile_mean_pct']:+.3f}% vs universe "
              f"{lo_['universe_mean_pct']:+.3f}% -> excess {lo_['excess_vs_universe_pct']:+.3f}% "
              f"-> net {lo_['net_after_cost_pct']:+.3f}%")
        ps = ctl['per_symbol']
        print(f"  per-symbol: {ps['positive']}/{ps['total']} positive, median {ps['median_pct']:+.3f}%")

    best_net = max((v['net_after_cost_pct']
                    for s in res['results'].values() for v in s.values()), default=-9.9)
    best_acc = max((v['directional_accuracy_pct']
                    for s in res['results'].values() for v in s.values()), default=0.0)
    _lo = ctl.get('long_only', {}) if 'error' not in ctl else {}
    _oos = ctl.get('out_of_sample', {})
    _oos_ok = (len(_oos) == 2
               and all(v['gross_pct'] > 0 for v in _oos.values()))
    _lo_ok = bool(_lo) and _lo.get('net_after_cost_pct', -9) > 0
    _ps = ctl.get('per_symbol', {})
    _ps_ok = bool(_ps) and _ps.get('total', 0) > 0 and _ps['positive'] / _ps['total'] >= 0.7
    res['in_sample_checks'] = {'out_of_sample_both_positive': _oos_ok,
                               'long_only_net_positive': _lo_ok,
                               'per_symbol_70pct_positive': _ps_ok}

    # ── VERDICT: purged walk-forward DECISIVE hai, in-sample controls nahi ──
    # Ye line FIX-62 me add hui aur isne mera apna "POSSIBLE EDGE" verdict
    # REFUTE kiya. Pehle verdict sirf char in-sample/split-half controls se
    # banta tha — aur wo sab pass ho gaye the. Par jab repo ka tested
    # `ml_lab.purged_walk_forward` chalaya to:
    #     mean accuracy        50.97%
    #     mean BASELINE        53.62%   <- accuracy MAJORITY CLASS se bhi NEECHE
    #     shuffled ceiling     52.18%
    #     symbols beating null 5/27
    # Matlab: +0.107% long-only "edge" purged walk-forward par SURVIVE NAHI
    # karta. Wajah: split-half me boundary par overlapping 5-day labels leak
    # karte hain, aur pooled cross-sectional t-stat autocorrelated observations
    # se inflate hota hai.
    if walk_forward:
        res['walk_forward'] = walk_forward
    _wf = res.get('walk_forward') or {}
    if _wf and 'error' not in _wf:
        _acc, _base = _wf['mean_accuracy_pct'], _wf['mean_baseline_pct']
        _nb, _nt = (int(x) for x in str(_wf['symbols_beating_shuffled']).split('/'))
        _wf_ok = _acc > _base and _acc > _wf['mean_shuffled_ceiling_pct'] and _nb >= _nt / 2
        res['walk_forward_verdict'] = ('SURVIVES' if _wf_ok else
                                       'FAILS — accuracy baseline se neeche')
        res['verdict'] = ('POSSIBLE EDGE — real OI collector se verify karo'
                          if _wf_ok else 'NO TRADEABLE EDGE')
    else:
        # Walk-forward nahi chala to honest raho — "possible" bolne ka haq nahi.
        res['walk_forward_verdict'] = 'NOT RUN — isliye edge CLAIM nahi ho sakta'
        res['verdict'] = ('NO TRADEABLE EDGE (walk-forward chalao: --walk-forward)'
                          if not (_oos_ok and _lo_ok and _ps_ok)
                          else 'UNCONFIRMED — in-sample pass, purged walk-forward REQUIRED')
    res['verdict_detail'] = (
        f'best net-after-cost = {best_net:+.3f}%/trade, best accuracy = {best_acc:.2f}% '
        f'(coin flip 50%). '
        + ('Cost ke baad positive hai — par ye sirf proxy hai, asli OI nahi.'
           if best_net > 0 else
           'Cost ke baad negative ya accuracy 50% se neeche. Futures-volume proxy me '
           'tradeable edge nahi — iska matlab ye NAHI ki asli OI me bhi nahi hoga '
           '(OI genuinely independent data hai). Asli jawaab ke liye --collect se '
           'history banao.'))
    return res


# ══════════════════════════════════════════════════════════════════════════
#  PURGED WALK-FORWARD — split-half se aage ka honest test
#
# Split-half sirf ek cut hai. Ye expanding-window purged walk-forward hai, aur
# naya code NAHI likha — repo ka apna TESTED `research/ml_lab.purged_walk_forward`
# use kiya hai (wahi jo ml_edge_study chalata hai):
#   • train kabhi test ke aage nahi jaata
#   • train/test ke beech `embargo = horizon` bars ka gap (overlapping labels
#     leakage banate hain — r5 5 din overlap karta hai)
#   • PERMUTATION NULL: train labels shuffle karke wahi pipeline — jo accuracy
#     shuffle ke baad bhi mile wahi "no-skill" floor hai
# ══════════════════════════════════════════════════════════════════════════
def walk_forward(panel: pd.DataFrame, signal: str = 'fv_ratio', horizon: int = 5,
                 n_folds: int = 5, warmup: int = 252, n_perm: int = 3) -> dict:
    sys.path.insert(0, str(ROOT))
    from research.ml_lab import purged_walk_forward

    per_symbol, skipped = [], 0
    for sym, g in panel.groupby('sym'):
        g = g.dropna(subset=[signal, 'past5', 'past20', f'r{horizon}']).copy()
        if len(g) < warmup + n_folds * 20:
            skipped += 1
            continue
        X = g[[signal, 'past5', 'past20']].copy()
        y = (g[f'r{horizon}'] > 0).astype(int)
        try:
            real = purged_walk_forward(X, y, n_folds=n_folds, warmup=warmup,
                                       embargo=horizon)
            nulls = [purged_walk_forward(X, y, n_folds=n_folds, warmup=warmup,
                                         embargo=horizon, shuffle_train_labels=True,
                                         seed=sd).accuracy * 100
                     for sd in range(n_perm)]
        except Exception as e:
            skipped += 1
            print(f'  {sym:<12} skip ({type(e).__name__})')
            continue
        per_symbol.append({
            'sym': sym, 'accuracy_pct': round(real.accuracy * 100, 2),
            'baseline_pct': round(real.baseline * 100, 2),
            'edge_pp': round(real.edge_pp, 2),
            'shuffled_ceiling_pct': round(max(nulls), 2),
            'beats_shuffled': bool(real.accuracy * 100 > max(nulls)),
            'n_oos': real.n_oos,
        })
        r = per_symbol[-1]
        print(f"  {sym:<12} acc {r['accuracy_pct']:.2f}%  baseline {r['baseline_pct']:.2f}%  "
              f"edge {r['edge_pp']:+.2f}pp  shuffled-ceiling {r['shuffled_ceiling_pct']:.2f}%  "
              f"{'BEATS' if r['beats_shuffled'] else 'no'}  n_oos={r['n_oos']}")

    if not per_symbol:
        return {'error': 'koi symbol walk-forward ke layak nahi tha', 'skipped': skipped}
    accs = np.array([r['accuracy_pct'] for r in per_symbol])
    base = np.array([r['baseline_pct'] for r in per_symbol])
    ceil = np.array([r['shuffled_ceiling_pct'] for r in per_symbol])
    beats = int(sum(r['beats_shuffled'] for r in per_symbol))
    return {
        'method': 'research.ml_lab.purged_walk_forward (expanding window, '
                  f'warmup={warmup}, embargo={horizon}, folds={n_folds}, perm={n_perm})',
        'n_symbols_tested': len(per_symbol), 'n_symbols_skipped': skipped,
        'mean_accuracy_pct': round(float(accs.mean()), 2),
        'mean_baseline_pct': round(float(base.mean()), 2),
        'mean_edge_pp': round(float((accs - base).mean()), 2),
        'mean_shuffled_ceiling_pct': round(float(ceil.mean()), 2),
        'symbols_beating_shuffled': f'{beats}/{len(per_symbol)}',
        'per_symbol': per_symbol,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--collect', default='', help='symbol ka live OI snapshot save karo')
    ap.add_argument('--bars', type=int, default=1200)
    ap.add_argument('--walk-forward', action='store_true',
                    help='purged walk-forward chalao (split-half se zyada honest)')
    ap.add_argument('--json', default='tools/oi_signal_study.json')
    a = ap.parse_args()

    if a.collect:
        syms = [s.strip().upper() for s in a.collect.split(',') if s.strip()]
        bad = 0
        for i, s in enumerate(syms):
            bad += collect(s)
            if i < len(syms) - 1:
                time.sleep(2)          # Akamai rate-limit se bachna
        return bad

    panel = load_proxy(a.bars)
    if panel.empty:
        print('koi data nahi mila'); return 1
    if a.walk_forward:
        print('\n=== PURGED WALK-FORWARD (repo ka tested ml_lab) ===')
        wf_res = walk_forward(panel)
        if 'error' not in wf_res:
            print(f"\n  mean acc {wf_res['mean_accuracy_pct']:.2f}% vs baseline "
                  f"{wf_res['mean_baseline_pct']:.2f}% vs shuffled-ceiling "
                  f"{wf_res['mean_shuffled_ceiling_pct']:.2f}%")
            print(f"  symbols beating shuffled null: {wf_res['symbols_beating_shuffled']}")
    else:
        wf_res = None
    # walk-forward result run_proxy me bhejo taaki verdict isi se bane
    res = run_proxy(a.bars, panel=panel, walk_forward=wf_res)
    print(f'\n=== VERDICT: {res["verdict"]}')
    print(f'  {res["verdict_detail"]}')
    pathlib.Path(a.json).write_text(json.dumps(res, indent=2, ensure_ascii=False),
                                    encoding='utf-8')
    print(f'\nwritten: {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
