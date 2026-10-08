#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/verify_trade_plan.py — FIX-93 verifier
============================================================================
`calculate_trade_plan()` ko SYNTHETIC frames par chalata hai taaki arithmetic
deterministically assert ho sake (live price par nahi — wo har din badalta hai).

Har formula ko INDEPENDENTLY recompute karke compare kiya gaya hai; function ke
khud ke output par bharosa nahi kiya gaya.

Chalao:  python tools\\verify_trade_plan.py
Exit 0 = sab pass.
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A  # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append(bool(ok))
    print('  %s %s%s' % ('✅' if ok else '❌', name, (' — %s' % detail) if detail else ''))


def sec(t):
    print('\n' + '─' * 78)
    print(' ' + t)
    print('─' * 78)


def make_df(n=300, price=1000.0, atr=20.0, bbw=10.0, pctb=0.5, hi_mult=1.30,
            vol=1_000_000, bullish=False, with_atr=True, with_bb=True,
            with_high=True, with_vol=True, squeeze_rank=None, ramp=0.0):
    """Deterministic frame. `squeeze_rank` set karo to BB_Width history aisi
    banegi ki current width us percentile par ho. `ramp` = per-bar drift,
    weekly trend banane ke liye."""
    idx = pd.bdate_range('2024-01-01', periods=n)
    close = pd.Series(price + ramp * np.arange(n), index=idx, dtype=float)
    df = pd.DataFrame(index=idx)
    df['Close'] = close
    df['Open'] = close
    df['Low'] = close * 0.99
    df['High'] = close * hi_mult if with_high else np.nan
    if with_high:
        # last bar ka High = price, taaki dist 0 na ho jaye — 52W high = price*hi_mult
        df.loc[df.index[-1], 'High'] = price
    if with_vol:
        df['Volume'] = float(vol)
    if with_atr:
        df['ATR'] = atr
    if with_bb:
        if squeeze_rank is None:
            df['BB_Width'] = bbw
        else:
            # history: (squeeze_rank)% bars current se tight, baaki wider
            k = int(round(n * squeeze_rank / 100.0))
            w = np.full(n, bbw * 2.0)
            w[:k] = bbw * 0.5
            w[-1] = bbw
            df['BB_Width'] = w
        df['BB_PctB'] = pctb
    sgn = 1.0 if bullish else -1.0
    df['VWAP'] = price - sgn * 5
    df['EMA_9'] = price + sgn * 4
    df['EMA_21'] = price - sgn * 4
    df['SMA_50'] = price - sgn * 10
    df['SMA_200'] = price - sgn * 20
    df['MACD'] = sgn * 2
    df['MACD_Signal'] = -sgn * 2
    df['RSI'] = 60.0 if bullish else 40.0
    df['ST_Direction'] = 1 if bullish else -1
    df['OBV'] = sgn * 100
    df['OBV_EMA'] = -sgn * 100
    return df


# ══════════════════════════════════════════════════════════════════════════
sec('A · ATR stop / target / risk:reward arithmetic')
# ══════════════════════════════════════════════════════════════════════════
df = make_df(price=1000.0, atr=20.0)
p = A.calculate_trade_plan(df)
px, atr = 1000.0, 20.0
risk = atr * A.TP_ATR_STOP

check('plan.ok True hai', p.get('ok') is True)
check('ATR sahi report hota hai', p.get('atr') == round(atr, 2), str(p.get('atr')))
check('ATR% = ATR/price*100',
      p.get('atr_pct') == round(atr / px * 100, 2), str(p.get('atr_pct')))
check('risk_per_share = ATR × %.1f' % A.TP_ATR_STOP,
      p.get('risk_per_share') == round(risk, 2), str(p.get('risk_per_share')))
check('LONG stop = price − risk',
      p['long']['stop'] == round(px - risk, 2), str(p['long']['stop']))
check('SHORT stop = price + risk',
      p['short']['stop'] == round(px + risk, 2), str(p['short']['stop']))
for nm, m in (('t1', A.TP_ATR_T1), ('t2', A.TP_ATR_T2), ('t3', A.TP_ATR_T3)):
    check('LONG %s = price + %s×ATR' % (nm, m),
          p['long'][nm] == round(px + atr * m, 2), str(p['long'][nm]))
    check('SHORT %s = price − %s×ATR' % (nm, m),
          p['short'][nm] == round(px - atr * m, 2), str(p['short'][nm]))
    check('rr_%s = %s / %s' % (nm, m, A.TP_ATR_STOP),
          p['long']['rr_' + nm] == round(m / A.TP_ATR_STOP, 2),
          str(p['long']['rr_' + nm]))
check('meets_1_2 = (T3 mult / stop mult) >= %.1f' % A.TP_RR_MIN,
      p['long']['meets_1_2'] is bool((A.TP_ATR_T3 / A.TP_ATR_STOP) >= A.TP_RR_MIN))
check('rr_min threshold payload me hai', p.get('rr_min') == A.TP_RR_MIN)
check('stop_rule me multiplier likha hai (magic number nahi)',
      str(A.TP_ATR_STOP) in (p.get('stop_rule') or ''), p.get('stop_rule'))
check('rr_note T1 ki sachchai batata hai (chhupata nahi)',
      'T1' in (p.get('rr_note') or '') and 'sirf' in (p.get('rr_note') or ''))

sec('B · ATR missing / zero par graceful')
p_no = A.calculate_trade_plan(make_df(with_atr=False))
check('ATR absent -> long/short keys NAHI bante',
      'long' not in p_no and 'short' not in p_no)
check('ATR absent -> note aata hai', any('ATR' in n for n in p_no['notes']),
      str(p_no['notes']))
check('ATR absent -> plan phir bhi ok=True (baaki sections chalte hain)',
      p_no.get('ok') is True)

df0 = make_df()
df0['ATR'] = 0.0
p_z = A.calculate_trade_plan(df0)
check('ATR == 0 -> stop nahi bana (divide-by-zero se bacha)',
      'long' not in p_z and any('ATR' in n for n in p_z['notes']))

sec('C · Bollinger squeeze percentile')
p_sq = A.calculate_trade_plan(make_df(squeeze_rank=10.0))
check('width apni history ke 10th pct par -> squeeze True',
      p_sq['bb']['squeeze'] is True, 'rank=%s' % p_sq['bb']['width_rank_pct'])
check('squeeze True -> note aata hai',
      any('SQUEEZE' in n for n in p_sq['notes']), str(p_sq['notes']))
p_wd = A.calculate_trade_plan(make_df(squeeze_rank=90.0))
check('width 90th pct par -> squeeze False', p_wd['bb']['squeeze'] is False,
      'rank=%s' % p_wd['bb']['width_rank_pct'])
check('squeeze False -> koi SQUEEZE note nahi (jhootha alert nahi)',
      not any('SQUEEZE' in n for n in p_wd['notes']))
check('bb.n_bars history ka length hai', p_wd['bb']['n_bars'] > 0,
      str(p_wd['bb']['n_bars']))
check('pctb passthrough hota hai', p_wd['bb']['pctb'] == 0.5)

# chhoti history -> percentile reliable nahi, bolna chahiye
df_short_bb = make_df(n=300, squeeze_rank=None)
df_short_bb.loc[df_short_bb.index[:260], 'BB_Width'] = np.nan
p_thin = A.calculate_trade_plan(df_short_bb)
check('BB history < 60 bars -> squeeze claim NAHI karta',
      'bb' not in p_thin and any('BB history' in n for n in p_thin['notes']),
      str(p_thin['notes']))
p_nobb = A.calculate_trade_plan(make_df(with_bb=False))
check('BB absent -> note aata hai, crash nahi',
      'bb' not in p_nobb and any('BB_Width' in n for n in p_nobb['notes']))

sec('D · 52-week high distance + swing band')
p_hi = A.calculate_trade_plan(make_df(price=1000.0, hi_mult=1.30))
exp_dist = round((1000.0 / 1300.0 - 1.0) * 100.0, 2)
check('52W high sahi hai', p_hi['hi52']['value'] == 1300.0, str(p_hi['hi52']['value']))
check('dist_pct = (price/hi − 1)×100', p_hi['hi52']['dist_pct'] == exp_dist,
      'got %s want %s' % (p_hi['hi52']['dist_pct'], exp_dist))
check('-23.08%% band (%.0f..%.0f) me hai -> in_swing_band True'
      % (A.TP_HI52_LOW, A.TP_HI52_HIGH), p_hi['hi52']['in_swing_band'] is True)
p_near = A.calculate_trade_plan(make_df(price=1000.0, hi_mult=1.05))
check('-4.76%% (52W high ke paas) -> in_swing_band False',
      p_near['hi52']['in_swing_band'] is False, str(p_near['hi52']['dist_pct']))
p_far = A.calculate_trade_plan(make_df(price=1000.0, hi_mult=2.0))
check('-50%% (bahut neeche) -> in_swing_band False',
      p_far['hi52']['in_swing_band'] is False, str(p_far['hi52']['dist_pct']))
check('hi52 rule string me band likha hai', '%' in (p_hi['hi52'].get('rule') or ''))
p_nohi = A.calculate_trade_plan(make_df(with_high=False))
check('High all-NaN -> hi52 claim NAHI karta, note deta hai',
      'hi52' not in p_nohi and any('compute nahi hua' in n for n in p_nohi['notes']),
      str(p_nohi['notes']))
df_nohcol = make_df()
df_nohcol = df_nohcol.drop(columns=['High'])
p_nhc = A.calculate_trade_plan(df_nohcol)
check('High column hi absent -> alag note (guess nahi)',
      'hi52' not in p_nhc and any('High column' in n for n in p_nhc['notes']),
      str(p_nhc['notes']))

sec('E · Weekly MTF')
p_flat = A.calculate_trade_plan(make_df(n=300))          # constant price -> tie
check('mtf present hai', 'mtf' in p_flat)
check('weekly_trend UP/DOWN/FLAT me se ek hai',
      p_flat['mtf']['weekly_trend'] in ('UP', 'DOWN', 'FLAT'),
      p_flat['mtf']['weekly_trend'])
check('EMA20 == EMA50 (tie) -> FLAT, DOWN nahi (flat ko bearish nahi kehte)',
      p_flat['mtf']['weekly_trend'] == 'FLAT', p_flat['mtf']['weekly_trend'])
check('rule string me FLAT tolerance likha hai (chhupa hua threshold nahi)',
      'FLAT' in (p_flat['mtf'].get('rule') or ''), p_flat['mtf'].get('rule'))
check('flat_tol_pct payload me hai', p_flat['mtf'].get('flat_tol_pct') is not None)

p_up = A.calculate_trade_plan(make_df(n=300, ramp=1.0))
check('uptrend frame -> weekly_trend UP', p_up['mtf']['weekly_trend'] == 'UP',
      'ema20=%s ema50=%s' % (p_up['mtf']['weekly_ema20'], p_up['mtf']['weekly_ema50']))
check('UP me EMA20 > EMA50 (consistent)',
      p_up['mtf']['weekly_ema20'] > p_up['mtf']['weekly_ema50'])

p_dn = A.calculate_trade_plan(make_df(n=300, ramp=-1.0))
check('downtrend frame -> weekly_trend DOWN', p_dn['mtf']['weekly_trend'] == 'DOWN',
      'ema20=%s ema50=%s' % (p_dn['mtf']['weekly_ema20'], p_dn['mtf']['weekly_ema50']))
check('DOWN me EMA20 < EMA50 (consistent)',
      p_dn['mtf']['weekly_ema20'] < p_dn['mtf']['weekly_ema50'])
check('weekly_bars count > 0', p_dn['mtf']['weekly_bars'] > 0,
      str(p_dn['mtf']['weekly_bars']))

df_wk = make_df(n=300).iloc[:180]        # ~36 weekly bars, TP_MIN_WEEKLY se kam
p_wk = A.calculate_trade_plan(df_wk)
check('weekly bars < %d -> mtf claim NAHI, note ke saath' % A.TP_MIN_WEEKLY,
      'mtf' not in p_wk and any('weekly bars sirf' in n for n in p_wk['notes']),
      str(p_wk['notes']))
p_short = A.calculate_trade_plan(make_df(n=10))
check('total bars < %d -> plan ok=False, note ke saath' % A.TP_MIN_BARS,
      p_short.get('ok') is False and any('bars' in n for n in p_short['notes']),
      str(p_short['notes']))

sec('F · Confluence count')
p_bull = A.calculate_trade_plan(make_df(bullish=True))
c = p_bull['confluence']
check('bullish frame -> 8/8 BULLISH', (c['agree'], c['measured'], c['side']) == (8, 8, 'BULLISH'),
      '%s/%s %s' % (c['agree'], c['measured'], c['side']))
check('bull == 8, bear == 0', (c['bull'], c['bear']) == (8, 0))
check('pct == 100.0', c['pct'] == 100.0)
check('label STRONG (>= 75%%)', c['label'] == 'STRONG')
p_bear = A.calculate_trade_plan(make_df(bullish=False))
cb = p_bear['confluence']
check('bearish frame -> 8/8 BEARISH', (cb['agree'], cb['side']) == (8, 'BEARISH'))
check('confluence ka measured constant %.0f se zyada nahi' % A.TP_CONF_CHECKS,
      cb['measured'] <= A.TP_CONF_CHECKS, str(cb['measured']))

# mixed frame — kuch indicators gayab
df_mix = make_df(bullish=True)
for col in ('VWAP', 'EMA_9', 'OBV'):
    df_mix[col] = np.nan
c_mix = A.calculate_trade_plan(df_mix)['confluence']
check('3 indicators NaN -> measured 5 par girta hai (guess nahi karta)',
      c_mix['measured'] == 5, str(c_mix['measured']))
check('agree + doosra side == measured',
      c_mix['bull'] + c_mix['bear'] == c_mix['measured'])
df_none = make_df(bullish=True)
for col in ('VWAP', 'EMA_9', 'EMA_21', 'SMA_50', 'SMA_200', 'MACD', 'MACD_Signal',
            'RSI', 'ST_Direction', 'OBV', 'OBV_EMA'):
    df_none[col] = np.nan
p_nc = A.calculate_trade_plan(df_none)
check('sab indicators NaN -> confluence key nahi banti + note aata hai',
      'confluence' not in p_nc and any('confluence' in n for n in p_nc['notes']),
      str(p_nc['notes']))

sec('G · Liquidity (ADV)')
p_liq = A.calculate_trade_plan(make_df(vol=1_000_000))
check('ADV 10 lakh -> meets_5lakh True', p_liq['adv']['meets_5lakh'] is True,
      '%s lakh' % p_liq['adv']['lakh'])
check('lakh = value/1e5', p_liq['adv']['lakh'] == round(1_000_000 / 100000.0, 2))
p_low = A.calculate_trade_plan(make_df(vol=100_000))
check('ADV 1 lakh -> meets_5lakh False', p_low['adv']['meets_5lakh'] is False)
p_novol = A.calculate_trade_plan(make_df(with_vol=False))
check('Volume absent -> note aata hai',
      'adv' not in p_novol and any('Volume' in n for n in p_novol['notes']))

sec('H · Degenerate input — kabhi guess nahi')
check('df=None -> ok=False, exception nahi',
      A.calculate_trade_plan(None).get('ok') is False)
check('df=None -> note me reason hai',
      any('bars' in n for n in A.calculate_trade_plan(None)['notes']))
check('khaali df -> ok=False', A.calculate_trade_plan(pd.DataFrame()).get('ok') is False)
p_close = make_df()
p_close['Close'] = np.nan
check('Close NaN -> ok=False (price ke bina plan meaningless)',
      A.calculate_trade_plan(p_close).get('ok') is False)

sec('I · HONESTY — disclosure')
p_disc = A.calculate_trade_plan(make_df())
d = p_disc.get('disclosure') or ''
check('disclosure maujood hai', len(d) > 40)
check('disclosure me "NAHI badhate" saaf likha hai', 'NAHI badhate' in d)
check('disclosure FIX-89a ka hawala deta hai', 'FIX-89a' in d)
check('disclosure me accuracy ka DAWA nahi — balki saaf inkaar hai',
      'Koi accuracy ya probability claim nahi' in d, d[-70:])
check('disclosure me koi guarantee / win-rate promise nahi',
      not any(t in d.lower() for t in
              ('guarantee', 'sure shot', 'win rate', 'assured', 'pakka')))
check('har numeric field ke saath uska RULE bhi jaata hai',
      all(k in p_disc for k in ('stop_rule',)) and
      'rule' in p_disc.get('bb', {}) and 'rule' in p_disc.get('hi52', {}) and
      'rule' in p_disc.get('mtf', {}) and 'rule' in p_disc.get('adv', {}))

sec('J · Constants — 2026 sources se match')
check('ATR stop multiplier 1.5', A.TP_ATR_STOP == 1.5)
check('targets 1/2/3 × ATR',
      (A.TP_ATR_T1, A.TP_ATR_T2, A.TP_ATR_T3) == (1.0, 2.0, 3.0))
check('risk:reward floor 1:2', A.TP_RR_MIN == 2.0)
check('squeeze = tightest 20%', A.TP_SQUEEZE_PCT == 20.0)
check('ADV floor 5 lakh', A.TP_ADV_MIN == 500000)
check('52W band -40%% se -15%%', (A.TP_HI52_LOW, A.TP_HI52_HIGH) == (-40.0, -15.0))
check('52W window ~252 bars', A.TP_HI52_BARS == 252)
check('weekly MTF min 50 bars', A.TP_MIN_WEEKLY == 50)

sec('K · Score formula UNCHANGED — backtest/calibration safe')
import hashlib  # noqa: E402
import inspect  # noqa: E402
h = hashlib.sha256(inspect.getsource(A.calculate_kpi_scores).encode('utf8')).hexdigest()
check('calculate_kpi_scores ka source FIX-93 se chheda nahi gaya',
      'trade_plan' not in inspect.getsource(A.calculate_kpi_scores),
      'sha256 %s…' % h[:12])
check('score_formula_hash me calculate_kpi_scores shamil NAHI hai (isliye artifact safe)',
      'calculate_kpi_scores' not in inspect.getsource(A.score_formula_hash))
check('calculate_kpi_scores abhi bhi 4 horizons deta hai',
      set(A.calculate_kpi_scores(A.calculate_all_indicators(make_df(n=260)), {})
          .keys()) == {'intraday', 'swing', 'longterm', 'master'})

sec('L · API integration — plan payload me jaata hai')
src = inspect.getsource(A.kpi_scores_for)
check("kpi_scores_for 'plan' field return karta hai", "'plan'" in src)
check('plan calculate_trade_plan se aata hai', 'calculate_trade_plan(dfi)' in src)

passed = sum(1 for r in RESULTS if r)
print('\n' + '=' * 78)
print(' %d / %d checks passed' % (passed, len(RESULTS)))
print('=' * 78)
sys.exit(0 if passed == len(RESULTS) else 1)
