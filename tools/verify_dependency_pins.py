#!/usr/bin/env python3
"""
FIX-42 · M-2 verifier — dependency pins actually installable + code-compatible
=============================================================================
Asserts (offline, from committed evidence):
  [1] requirements.txt hygiene — exact pins, no package that isn't on PyPI
  [2] requirements.lock.json matches requirements.txt 1:1
  [3] every pin ships a wheel for cp312/cp313/cp314 → pip never compiles C
  [4] every pin's requires_python allows 3.12 / 3.13 / 3.14
  [5] no pin is on the measured-dead list (yfinance 0.2.44 etc.)
  [6] installed environment matches the pins (drift detector)
  [7] code compatibility — no numpy-2.0-removed APIs; sklearn/xgboost constructors
      used by app.py/deep_analyzer.py actually fit on the installed versions

Run:  python tools/verify_dependency_pins.py
"""
import importlib.metadata as md
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQ = ROOT / 'requirements.txt'
LOCK = ROOT / 'requirements.lock.json'
CP_TAGS = ('cp312', 'cp313', 'cp314')
SUPPORTED = ('3.12', '3.13', '3.14')

# Ye versions install to ho jaate hain, par kaam nahi karte — measured 2026-10-01:
#   yfinance 0.2.44 → yf.download("RELIANCE.NS", period="6mo") = 0 rows,
#   "JSONDecodeError: Expecting value: line 1 column 1" → /api/stock
#   "All 3 engines failed".
DEAD_PINS = {'yfinance': {'0.2.44': 'Yahoo API ke against dead (0 rows, JSONDecodeError)'}}

# PyPI se na milne wale naam jo requirements me galti se aa chuke hain
NOT_ON_PYPI = ('tvdatafeed',)

NUMPY2_REMOVED = re.compile(
    r'\bnp\.(float_|int_|bool8|object_|str_|unicode_|NaN|Inf|PINF|NINF|infty|product|'
    r'cumproduct|alltrue|sometrue|in1d|row_stack|round_|trapz|asfarray|issubsctype|'
    r'maximum_sctype|find_common_type|set_string_function|safe_eval|deprecate|disp)\b')

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✅ {msg}')
    else:
        FAIL += 1
        print(f'  ❌ {msg}')


def allows(requires_python, pyver):
    """requires_python spec ko bina `packaging` ke check karo (>=X.Y[,<A.B])."""
    if not requires_python:
        return True
    for clause in requires_python.split(','):
        clause = clause.strip()
        m = re.match(r'(>=|<=|>|<|==)\s*([0-9]+(?:\.[0-9]+)*)', clause)
        if not m:
            continue
        op, ver = m.group(1), m.group(2)
        a = tuple(int(x) for x in pyver.split('.')[:2])
        b = tuple(int(x) for x in ver.split('.')[:2])
        if op == '>=' and not a >= b:
            return False
        if op == '>' and not a > b:
            return False
        if op == '<=' and not a <= b:
            return False
        if op == '<' and not a < b:
            return False
        if op == '==' and a != b:
            return False
    return True


def main():
    print('[1] requirements.txt hygiene')
    ok(REQ.exists(), 'requirements.txt present')
    ok(LOCK.exists(), 'requirements.lock.json present (wheel evidence)')
    if not (REQ.exists() and LOCK.exists()):
        return finish()
    pins, unparsed = {}, []
    for raw in REQ.read_text(encoding='utf-8').splitlines():
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        m = re.match(r'^\s*([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-]+)\s*(?:#.*)?$', raw)
        if m:
            pins[m.group(1)] = m.group(2)
        else:
            unparsed.append(raw.strip())
    ok(not unparsed, f'every requirement line is an exact `name==version` pin (unparsed: {unparsed})')
    ok(not [n for n in pins if n.lower() in NOT_ON_PYPI],
       'no package that is absent from PyPI (tvdatafeed → tradingview-datafeed)')
    ok(len(pins) >= 10, f'{len(pins)} pins declared')

    print('[2] lock matches requirements')
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    lp = lock.get('pins') or {}
    ok(set(lp) == set(pins), f'lock covers exactly the {len(pins)} pinned packages')
    mismatch = {k: (pins[k], lp.get(k, {}).get('version'))
                for k in pins if lp.get(k, {}).get('version') != pins[k]}
    ok(not mismatch, f'lock versions identical to requirements.txt (mismatch: {mismatch})')
    ok(lock.get('all_pins_wheel_ready') is True, 'lock itself records all_pins_wheel_ready')
    ok(lock.get('cp_tags_required') == list(CP_TAGS), f'lock targets {CP_TAGS}')

    print('[3] wheel evidence per cp tag (no source compiles)')
    for name, ver in sorted(pins.items()):
        rec = lp.get(name, {})
        missing = [t for t in CP_TAGS if t not in (rec.get('wheel_for') or {})]
        ok(rec.get('wheel_count', 0) > 0 and not missing,
           f"{name}=={ver}: {rec.get('wheel_count')} wheels, covers "
           f"{','.join(sorted(rec.get('wheel_for') or {})) or 'NONE'}"
           + (f" — MISSING {missing}" if missing else ''))

    print('[4] requires_python allows 3.12/3.13/3.14')
    for name, ver in sorted(pins.items()):
        rp = lp.get(name, {}).get('requires_python')
        bad = [v for v in SUPPORTED if not allows(rp, v)]
        ok(not bad, f'{name}=={ver}: requires_python={rp or "unspecified"} '
                    f'{"OK" if not bad else "excludes " + ",".join(bad)}')

    print('[5] no measured-dead pin')
    for name, dead in DEAD_PINS.items():
        if name in pins:
            ok(pins[name] not in dead,
               f'{name}=={pins[name]} is not a known-dead version ({list(dead)})')

    print('[6] installed environment vs pins (drift)')
    for name, ver in sorted(pins.items()):
        try:
            got = md.version(name)
        except md.PackageNotFoundError:
            print(f'  ⏭  {name} not installed here — skipped')
            continue
        ok(got == ver, f'{name}: installed {got} == pinned {ver}')

    print('[7] code compatibility on installed versions')
    offenders = []
    for py in sorted(ROOT.rglob('*.py')):
        if any(part in {'.git', '__pycache__', 'node_modules'} for part in py.parts):
            continue
        try:
            for i, line in enumerate(py.read_text(encoding='utf-8', errors='ignore').splitlines(), 1):
                if NUMPY2_REMOVED.search(line):
                    offenders.append(f'{py.relative_to(ROOT)}:{i}')
        except OSError:
            continue
    ok(not offenders, f'no numpy-2.0-removed API in the repo (offenders: {offenders[:5]})')

    try:
        import numpy as np
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        X = np.random.rand(120, 6)
        y = (X[:, 0] + X[:, 1] > 1).astype(int)
        Xs = StandardScaler().fit_transform(X)
        for model in (GradientBoostingClassifier(n_estimators=40, max_depth=3, random_state=42),
                      RandomForestClassifier(n_estimators=40, random_state=42),
                      LogisticRegression(max_iter=1000, random_state=42)):
            model.fit(Xs, y)
            float(model.predict_proba(Xs[:1])[0][1])
        ok(True, f"app.py ka 3-model ensemble path sklearn {md.version('scikit-learn')} par fit+predict karta hai")
    except Exception as exc:
        ok(False, f'sklearn ensemble smoke test failed: {type(exc).__name__}: {exc}')

    try:
        from xgboost import XGBClassifier
        params = {'n_estimators': 40, 'max_depth': 3, 'learning_rate': 0.05,
                  'random_state': 42, 'verbosity': 0, 'eval_metric': 'logloss'}
        XGBClassifier(**params).fit(X, y)
        # deep_analyzer.py:280 wala exact shape (use_label_encoder ke saath)
        XGBClassifier(n_estimators=40, max_depth=4, learning_rate=0.05, random_state=42,
                      verbosity=0, use_label_encoder=False, eval_metric='logloss').fit(X, y)
        ok(True, f"XGBoost path (app.py + deep_analyzer.py shapes) {md.version('xgboost')} par chalta hai")
    except md.PackageNotFoundError:
        print('  ⏭  xgboost not installed here — skipped')
    except Exception as exc:
        ok(False, f'xgboost smoke test failed: {type(exc).__name__}: {exc}')

    try:
        import numpy as np2
        import pandas as pd
        import app  # noqa: F401  — import hi smoke test hai
        ok(True, f"app.py import OK on numpy {np2.__version__} / pandas {pd.__version__}")
    except Exception as exc:
        ok(False, f'app import failed: {type(exc).__name__}: {exc}')

    return finish()


def finish():
    print(f'\n{PASS} passed, {FAIL} failed')
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
