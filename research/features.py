"""
research/features.py — feature engineering + labelling (honest versions)
================================================================================
Do feature sets:
  • `app_28`  — jo abhi app.py me hai (comparison baseline ke liye)
  • `small_10`— de-correlated chhota set (research design)

Do labels:
  • `dir1`      — kal close aaj se upar? (app ka current target)
  • `ret5_atr`  — 5-din ka move ATR ke multiple se bada? (volatility-adjusted,
                  zyada signal-to-noise, aur horizon ke hisaab se embargo set hota hai)

Sab features sirf PAST data use karte hain (rolling/ewm) — koi look-ahead nahi.
"""
import numpy as np
import pandas as pd

APP_28 = ['ret_1d', 'ret_3d', 'ret_5d', 'ret_10d', 'ret_20d', 'rsi', 'macd', 'macd_sig',
          'macd_hist', 'bb_pctb', 'bb_width', 'atr_pct', 'vol_ratio', 'vol_change',
          'obv_slope', 'ema_cross', 'price_50', 'adx', 'plus_di', 'minus_di',
          'stoch_rsi', 'cci', 'willr', 'vol_20', 'vol_5', 'vwap_dist', 'hl_range', 'close_pos']

# 10 features jinme pairwise |corr| kam hai (raw indicators ko percentile/ratio
# form me rakha gaya hai taaki scale na bigde).
SMALL_10 = ['rsi_14', 'macd_hist_n', 'bb_pctb', 'atr_pct', 'vol_ratio_20',
            'ema_cross_pct', 'dist_sma50_pct', 'ret_20d', 'close_pos_20', 'range_pct_20']


def _atr(h, l, c, n=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def _rsi(c, n=14):
    d = c.diff()
    g = d.where(d > 0, 0.0).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    ls = (-d.where(d < 0, 0.0)).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    return 100 - (100 / (1 + g / (ls + 1e-10)))


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, h, l, v = (d[x].astype(float) for x in ('Close', 'High', 'Low', 'Volume'))

    # ── app's 28 (identical formulas to app.py) ──
    d['ret_1d'], d['ret_3d'], d['ret_5d'] = c.pct_change(1), c.pct_change(3), c.pct_change(5)
    d['ret_10d'], d['ret_20d'] = c.pct_change(10), c.pct_change(20)
    d['rsi'] = _rsi(c)
    e12, e26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    d['macd'] = e12 - e26
    d['macd_sig'] = d['macd'].ewm(span=9, adjust=False).mean()
    d['macd_hist'] = d['macd'] - d['macd_sig']
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    d['bb_pctb'] = (c - (mid - 2 * sd)) / (4 * sd + 1e-10)
    d['bb_width'] = (4 * sd) / (mid + 1e-10)
    d['atr'] = _atr(h, l, c)
    d['atr_pct'] = d['atr'] / (c + 1e-10) * 100
    d['vol_ratio'] = v / (v.rolling(20).mean() + 1)
    d['vol_change'] = v.pct_change(1)
    d['obv_slope'] = (np.sign(c.diff()) * v).fillna(0).cumsum().diff(5)
    e9, e21 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=21, adjust=False).mean()
    d['ema_cross'] = (e9 - e21) / (c + 1e-10) * 100
    s50 = c.rolling(50).mean()
    d['price_50'] = (c - s50) / (s50 + 1e-10) * 100
    pdm = h.diff().where((h.diff() > -l.diff()) & (h.diff() > 0), 0.0)
    mdm = (-l.diff()).where((-l.diff() > h.diff()) & (-l.diff() > 0), 0.0)
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / (d['atr'] + 1e-10)
    mdi = 100 * mdm.ewm(alpha=1 / 14, adjust=False).mean() / (d['atr'] + 1e-10)
    d['plus_di'], d['minus_di'] = pdi, mdi
    d['adx'] = (100 * (pdi - mdi).abs() / (pdi + mdi + 1e-10)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = d['rsi']
    d['stoch_rsi'] = ((rs - rs.rolling(14).min()) /
                      (rs.rolling(14).max() - rs.rolling(14).min() + 1e-10)).rolling(3).mean() * 100
    tp = (h + l + c) / 3
    d['cci'] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20)
                                               .apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True) + 1e-10)
    d['willr'] = ((h.rolling(14).max() - c) /
                  (h.rolling(14).max() - l.rolling(14).min() + 1e-10)) * -100
    d['vol_20'] = c.pct_change().rolling(20).std() * 100
    d['vol_5'] = c.pct_change().rolling(5).std() * 100
    d['vwap_dist'] = (c - (tp * v).cumsum() / (v.cumsum() + 1e-10)) / (c + 1e-10) * 100
    d['hl_range'] = (h - l) / (c + 1e-10) * 100
    d['close_pos'] = (c - l) / (h - l + 1e-10)

    # ── research's 10 (de-correlated, mostly normalised) ──
    d['rsi_14'] = d['rsi']
    d['macd_hist_n'] = d['macd_hist'] / (d['atr'] + 1e-10)
    d['vol_ratio_20'] = d['vol_ratio']
    d['ema_cross_pct'] = d['ema_cross']
    d['dist_sma50_pct'] = d['price_50']
    hh20, ll20 = h.rolling(20).max(), l.rolling(20).min()
    d['close_pos_20'] = (c - ll20) / (hh20 - ll20 + 1e-10)
    d['range_pct_20'] = (hh20 - ll20) / (c + 1e-10) * 100

    return d


def build_labels(df: pd.DataFrame, kind: str = 'dir1', atr_mult: float = 0.5) -> pd.DataFrame:
    """Label + `horizon` (bars) return karta hai — embargo isi se set hota hai."""
    d = df.copy()
    c = d['Close'].astype(float)
    if kind == 'dir1':
        d['label'] = (c.shift(-1) > c).astype(float)
        d.loc[d.index[-1], 'label'] = np.nan
        d['horizon'] = 1
    elif kind == 'ret5_atr':
        fwd = c.shift(-5) / c - 1.0
        thr = (d['atr_pct'] / 100.0) * atr_mult
        d['label'] = (fwd > thr).astype(float)
        d.loc[d.index[-5:], 'label'] = np.nan
        d['horizon'] = 5
    else:
        raise ValueError(f"unknown label kind: {kind}")
    return d


def decorrelate(df: pd.DataFrame, feats: list, threshold: float = 0.85) -> tuple:
    """
    Greedy de-correlation: |corr| > threshold wale pairs me se woh drop karta hai
    jiska average |corr| zyada hai. Returns (kept, dropped).
    """
    work = df[feats].replace([np.inf, -np.inf], np.nan).dropna()
    if len(work) < 30:
        return feats, []
    corr = work.corr().abs()
    np.fill_diagonal(corr.values, 0.0)
    dropped = []
    while True:
        i, j = np.unravel_index(np.argmax(corr.values), corr.shape)
        if corr.values[i, j] <= threshold:
            break
        a, b = corr.columns[i], corr.columns[j]
        drop = a if corr[a].mean() >= corr[b].mean() else b
        dropped.append(drop)
        corr = corr.drop(index=drop, columns=drop)
    kept = [f for f in feats if f not in dropped]
    return kept, dropped
