#!/usr/bin/env python3
"""
FIX-31 verification — Kelly ab ASLI measured win-rate se chalti hai.

Pehle: win_rate = 0.62 / 0.55 / 0.45 (assumed, score-bucket heuristic) →
       kelly → qty → risk_amount. Dashboard par ye verified numbers jaise lagte the.
Ab:    wahi plan geometry (SL = sl_mult x ATR, T1 = 2.5 x ATR) symbol ke apne
       2 saal ke daily data par backtest hoti hai (T1 pehle aaya ya SL) aur
       usi measured hit-rate se Kelly banti hai — aur point estimate ki jagah
       conservative LOWER BOUND (1 sd) se, kyunki n chhota hota hai
       (RELIANCE: 50.9% ± 3.4pp, breakeven 50% → ye sampling error ke andar hai).

Run:  python3 tools/verify_kelly_measured.py
"""
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as A  # noqa: E402

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  → {detail}" if detail else ""))


def mkdf(closes, high_pad=1.0, low_pad=1.0):
    n = len(closes)
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({'Open': c, 'High': c + high_pad, 'Low': c - low_pad,
                         'Close': c, 'Volume': np.full(n, 1e6)},
                        index=pd.date_range('2024-01-01', periods=n, freq='D'))


print("=" * 84)
print(" FIX-31 — measured Kelly win-rate (plan geometry backtest)")
print("=" * 84)

print("\n[1] measurement function — deterministic synthetic cases")
up = mkdf(100 + np.arange(200) * 1.0)          # strong uptrend
r_long = A.measure_plan_hit_rate(up, 'LONG', 1.5)
r_short = A.measure_plan_hit_rate(up, 'SHORT', 1.5)
check("uptrend LONG sl1.5 → 100% hit", r_long and r_long['rate'] == 1.0, str(r_long and r_long['rate']))
check("uptrend SHORT sl1.5 → 0% hit (mirror sahi)", r_short and r_short['rate'] == 0.0)
check("breakeven = 37.5% (b=2.5/1.5)", r_long and abs(r_long['breakeven'] - 0.375) < 1e-6)
check("sample n >= 30", r_long and r_long['n'] >= 30, str(r_long and r_long['n']))

flat = mkdf(np.full(200, 100.0))               # koi resolution nahi
check("flat market → None (koi setup resolve nahi hua)", A.measure_plan_hit_rate(flat, 'LONG', 2.0) is None)
check("chhota data → None", A.measure_plan_hit_rate(mkdf(100 + np.arange(40) * 1.0), 'LONG', 1.5) is None)
check("direction NONE → None", A.measure_plan_hit_rate(up, 'NONE', 1.5) is None)
check("min_n bada → None", A.measure_plan_hit_rate(up, 'LONG', 1.5, min_n=10**6) is None)
check("golden: scope 'unconditional' likha hai", r_long and 'unconditional' in r_long.get('scope', ''))

print("\n[2] calculate_risk — measured edge HAI (lcb > breakeven)")
plan = {'rate': 0.60, 'n': 200, 'breakeven': 0.5, 'basis': 'test', 'scope': 'test'}
r = A.calculate_risk(100.0, 2.0, 80, action='BUY', plan_measure=plan)
se = (0.60 * 0.40 / 200) ** 0.5
lcb = 0.60 - A.CONFIG['PLAN_LCB_Z'] * se
check("win_rate_used = lcb", abs(r['win_rate_used'] - round(lcb, 4)) < 1e-9, f'{r["win_rate_used"]} vs {lcb:.4f}')
check("basis 'measured'", 'measured' in r['win_rate_basis'])
check("edge_verified True", r['edge_verified'] is True)
check("qty > 0 (measured edge par size)", r['qty'] > 0, str(r['qty']))
check("fields: plan_hit_rate/lcb/se/n/breakeven", all(
    r[k] is not None for k in ('plan_hit_rate', 'plan_hit_rate_lcb', 'plan_std_err',
                               'plan_sample_size', 'plan_breakeven')))
check("note me 'measured' + lcb", 'hit-rate measured' in r['risk_note'] and 'lower-bound' in r['risk_note'])

print("\n[3] calculate_risk — REAL case: 50.9% par breakeven 50% (noise ke andar)")
real = {'rate': 0.5093, 'n': 214, 'breakeven': 0.5, 'basis': 'test', 'scope': 'test'}
r = A.calculate_risk(100.0, 2.0, 32, action='SHORT_SELL', plan_measure=real)
check("edge_verified False (sampling error)", r['edge_verified'] is False)
check("kelly 0%", r['kelly_pct'] == 0.0)
check("qty 0", r['qty'] == 0)
check("exec_status 'NO TRADE (measured edge nahi)'", 'NO TRADE (measured edge nahi)' in r['exec_status'], r['exec_status'])
check("note 'sampling error ke andar'", 'sampling error ke andar' in r['risk_note'])

print("\n[4] fallback (koi measurement nahi) — purana behaviour intact")
r = A.calculate_risk(100.0, 2.0, 80, action='BUY')
check("basis 'assumed'", 'assumed' in r['win_rate_basis'])
check("win_rate_used = 0.62", r['win_rate_used'] == 0.62, str(r['win_rate_used']))
check("win_rate_assumed field", r['win_rate_assumed'] == 0.62)
check("plan_hit_rate None", r['plan_hit_rate'] is None)
# mechanics regression (same formula as before FIX-31)
b = 2.5 / 1.5
kelly = min((0.62 * b - 0.38) / b, A.CONFIG['MAX_KELLY_PCT'])
check("kelly = purana formula (regression)", abs(r['kelly_pct'] - round(kelly * 100, 1)) < 0.01, f'{r["kelly_pct"]} vs {round(kelly*100,1)}')

print("\n[5] direction NONE → saaf message")
r = A.calculate_risk(100.0, 2.0, 40, action='AVOID')
check("basis 'n/a (no directional trade)'", r['win_rate_basis'] == 'n/a (no directional trade)', r['win_rate_basis'])
check("note 'Koi directional trade nahi'", 'Koi directional trade nahi' in r['risk_note'])
check("qty 0", r['qty'] == 0)

print("\n[6] plan_geometry buckets")
check("score 80 → (1.5, 0.62)", A.plan_geometry(80) == (1.5, 0.62))
check("score 65 → (2.0, 0.55)", A.plan_geometry(65) == (2.0, 0.55))
check("score 40 → (2.5, 0.45)", A.plan_geometry(40) == (2.5, 0.45))

print("\n[7] source-level wiring")
src = (ROOT / "app.py").read_text()
check("call site measured plan completed bars par call karta hai", "measure_plan_hit_rate(ranked_df, _dir, _sl_mult, symbol=resolved)" in src)
check("call site plan_measure bhejta hai", "plan_measure=_plan" in src)
check("CONFIG me PLAN_MEASURE_MIN_N", "'PLAN_MEASURE_MIN_N'" in src)
check("CONFIG me PLAN_LCB_Z", "'PLAN_LCB_Z'" in src)
dash = (ROOT / "Dashboard.html").read_text()
check("dashboard measured win-rate row", "Measured plan win-rate" in dash)
check("dashboard lower-bound dikhata hai", "plan_hit_rate_lcb" in dash)

passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print("\n" + "=" * 84)
print(f" RESULT: {passed} passed, {failed} failed")
print("=" * 84)
sys.exit(0 if failed == 0 else 1)
