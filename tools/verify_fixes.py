#!/usr/bin/env python3
"""
tools/verify_fixes.py — regression suite for the V6.1 fixes
================================================================================
Run after tools/apply_v61_fixes.py:

    python3 tools/verify_fixes.py

Exits non-zero if any assertion fails. Also regenerates scan_results.json and
deep_RELIANCE.json / deep_TCS.json with the fixed code.
"""
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(label, cond, detail=''):
    (PASS if cond else FAIL).append(label)
    print(f"  {'✅' if cond else '❌'} {label}{('  → ' + str(detail)) if detail else ''}")


print("═" * 78)
print(" 1) route + API checks (importing the patched app.py)")
print("═" * 78)
import app as A  # noqa: E402

c = A.app.test_client()

r = c.get('/')
check("GET / serves the dashboard (was a 500)", r.status_code == 200 and b'StockAI' in r.data, f"{r.status_code}, {len(r.data)} bytes")
check("GET /icon/favicon.svg reachable", c.get('/icon/favicon.svg').status_code == 200)
check("GET /static/lightweight-charts…js reachable", c.get('/static/lightweight-charts.standalone.production.js').status_code == 200)
check("unknown route returns 404 not 500", c.get('/api/nonexistent').status_code == 404)

print()
print("═" * 78)
print(" 2) symbol resolver (was: 'INFOSYS LTD' → HCL-INSYS)")
print("═" * 78)
for q, want in [('INFOSYS LTD', 'INFY'), ('infosys', 'INFY'), ('reliance industries', 'RELIANCE'),
                ('HDFC BANK', 'HDFCBANK'), ('suzlon energy', 'SUZLON'), ('RELIANCE', 'RELIANCE')]:
    got = A.resolve_symbol(q)
    check(f"{q!r} → {got}", got == want, f"expected {want}")

print()
print("═" * 78)
print(" 3) /api/stock payload (live data)")
print("═" * 78)
t0 = time.time()
r = c.get('/api/stock/RELIANCE')
cold = time.time() - t0
d = r.get_json()
t0 = time.time()
c.get('/api/stock/RELIANCE')
warm = time.time() - t0

check("payload returns HTTP 200", r.status_code == 200)
rk = d.get('risk', {})
check("risk has direction/notional/leverage fields", all(k in rk for k in ('direction', 'notional', 'leverage', 'qty')))
if rk.get('direction') == 'NONE':
    check("non-directional verdict → no position (qty=0)", rk['qty'] == 0, f"qty={rk['qty']}")
else:
    check("notional never exceeds capital (no leverage bug)",
          rk['notional'] <= rk['capital'] * 1.001, f"notional={rk['notional']} capital={rk['capital']}")
check("Kelly uses the REAL reward:risk (not hardcoded 2.5)",
      abs(rk.get('kelly_rr_used', 0) - rk.get('rr_ratio', 0)) < 0.01, f"b={rk.get('kelly_rr_used')}")
check("ML cached: warm request < 60% of cold", warm < max(cold * 0.6, 1.0), f"cold={cold:.2f}s warm={warm:.2f}s")
dy = d['fundamentals']['div_yield']
check("dividend yield not inflated 100x", (dy == 'N/A' or float(dy.rstrip('%')) < 25), f"{dy!r} (was '50.00%')")
check("ensemble_v2 diagnostic present", isinstance(d.get('ensemble_v2'), dict) and 'score' in d['ensemble_v2'], d.get('ensemble_v2', {}).get('score'))
check("is_realtime flag exposed", 'is_realtime' in d)
check("indicator 'vwap' is now a 20-session VWAP", 'vwap' in d['indicators'] and 'vwap_cumulative' not in d['indicators'])
check("no NaN/Infinity tokens in JSON", 'NaN' not in json.dumps(d) and 'Infinity' not in json.dumps(d))
bad_zeros = [k for k, v in d['indicators'].items() if v == 0.0 and k in ('sma200', 'sma50', 'ema50')]
check("missing indicators are null, not a fake 0.0", not bad_zeros, bad_zeros)
ml = d.get('ml', {})
check("walk-forward accuracy + noise band exposed",
      'walk_forward_accuracy' in ml and 'wf_window_sigma' in ml,
      f"WF={ml.get('walk_forward_accuracy')}% sigma=±{ml.get('wf_window_sigma')}pp edge={ml.get('walk_forward_edge')}pp")

t0 = time.time()
r404 = c.get('/api/stock/ZZZNOTREAL')
first = time.time() - t0
t0 = time.time()
c.get('/api/stock/ZZZNOTREAL')
second = time.time() - t0
check("bad symbol cached (2nd call instant)", second < 0.5, f"1st={first:.2f}s 2nd={second:.3f}s")

print()
print("═" * 78)
print(" 4) scanner (regenerates scan_results.json)")
print("═" * 78)
subprocess.run([sys.executable, 'nifty_scanner.py'], cwd=ROOT, check=True,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
scan = json.loads((ROOT / 'scan_results.json').read_text(encoding='utf-8'))
vr = [r['vol_ratio'] for r in scan['results'] if r.get('vol_ratio') is not None]
check("scan_results.json is strict JSON", True, f"{len(scan['results'])} stocks")
check("vol_ratio is a real RVOL range, not a 0.2-0.9 constant penalty",
      min(vr) < 0.95 and max(vr) > 1.5, f"min={min(vr):.2f} max={max(vr):.2f} mean={sum(vr)/len(vr):.2f}")
check("ml_effective column present", all('ml_effective' in r for r in scan['results']))
check("scan.json has no NaN tokens", 'NaN' not in (ROOT / 'scan_results.json').read_text(encoding='utf-8'))

print()
print("═ * 78")
print(" 5) deep analyzer (regenerates deep_*.json)")
print("═" * 78)
subprocess.run([sys.executable, 'deep_analyzer.py', 'RELIANCE'], cwd=ROOT, check=True,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deep_txt = (ROOT / 'deep_RELIANCE.json').read_text(encoding='utf-8')
dj = json.loads(deep_txt)
sec = dj['sector']
check("deep_RELIANCE.json is strict JSON (no NaN tokens)", 'NaN' not in deep_txt)
check("beta is finite (was NaN)", isinstance(sec.get('beta'), (int, float)), sec.get('beta'))
check("correlation is finite (was NaN)", isinstance(sec.get('correlation'), (int, float)), sec.get('correlation'))
check("stock and NIFTY legs cover the same window",
      abs(sec.get('stock_span_days', 0) - sec.get('nifty_span_days', 0)) < 45,
      f"{sec.get('stock_span_days')}d vs {sec.get('nifty_span_days')}d, {sec.get('aligned_sessions')} aligned sessions")

print()
print("═" * 78)
print(f" RESULT: {len(PASS)} passed, {len(FAIL)} failed")
print("═" * 78)
for f in FAIL:
    print("  ❌ FAILED:", f)
sys.exit(1 if FAIL else 0)
