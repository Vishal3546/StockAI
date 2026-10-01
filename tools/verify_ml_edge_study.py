#!/usr/bin/env python3
"""
FIX-41 · C-2 verifier — recorded ML edge study + app wiring
===========================================================
Asserts:
  [1] ml_edge_study.json exists, schema/model match, OOS counts real
  [2] internal consistency (edge = accuracy − baseline, CI plausible)
  [3] verdict is the SAME rule recomputed from the numbers (no hand-written tag)
  [4] app.load_ml_study()/ml_study_payload() expose it; missing/tampered → fail CLOSED
  [5] /api/stock payload carries ml_study; Dashboard renders it and labels the
      in-app number a diagnostic
  [6] no overclaim: artifact never claims profitability

Run:  python tools/verify_ml_edge_study.py
"""
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = 0, 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✅ {msg}')
    else:
        FAIL += 1
        print(f'  ❌ {msg}')


def verdict_from(a, null_max):
    """research/tool verdict rule, recomputed here independently."""
    lo = a['accuracy_pct'] - a['ci95_pp']
    if null_max is not None and a['accuracy_pct'] <= null_max:
        return 'NO EDGE', False
    if a['edge_pp'] > 5 and lo > a['baseline_pct']:
        return 'POSSIBLE EDGE', True
    if a['edge_pp'] > 0:
        return 'MARGINAL', False
    return 'NO EDGE', False


def main():
    path = ROOT / 'ml_edge_study.json'

    print('[1] artifact')
    ok(path.exists(), 'ml_edge_study.json committed at repo root')
    if not path.exists():
        return finish()
    doc = json.loads(path.read_text(encoding='utf-8'))
    ok(doc.get('schema') == 1, f"schema == 1 (got {doc.get('schema')})")
    ok(doc.get('model') == 'ml-edge-purged-wf-v1', f"model tag = {doc.get('model')}")
    strat = doc.get('strategies') or {}
    ok(len(strat) >= 3, f"{len(strat)} strategies recorded")
    total_oos = sum(int(s.get('n_oos') or 0) for s in strat.values())
    ok(total_oos >= 10000, f"pooled OOS predictions = {total_oos:,} (real out-of-sample volume)")
    ok('purged' in (doc.get('method') or '').lower() and 'embargo' in (doc.get('method') or '').lower(),
       'method records purge + embargo (no leakage claim)')
    ok(len(doc.get('symbols_scored') or []) >= 5, f"{len(doc.get('symbols_scored') or [])} symbols scored")

    print('[2] internal consistency')
    for name, s in strat.items():
        ok(abs((s['accuracy_pct'] - s['baseline_pct']) - s['edge_pp']) <= 0.02,
           f"{name}: edge {s['edge_pp']:+.2f}pp == acc {s['accuracy_pct']} − baseline {s['baseline_pct']}")
        ok(s['accuracy_pct'] is not None and 0 <= s['accuracy_pct'] <= 100, f"{name}: accuracy in range")
        ok(s['baseline_pct'] >= 50.0, f"{name}: baseline {s['baseline_pct']}% >= coin-flip 50%")
        ok(s['ci95_pp'] is not None and s['ci95_pp'] > 0, f"{name}: CI width {s['ci95_pp']}pp reported")
        ok(s['n_oos'] >= 1000, f"{name}: n_oos = {s['n_oos']:,}")

    print('[3] verdict reproducibility')
    nul = doc.get('permutation_null') or {}
    proposed = strat.get('S3_ml_ret5atr_small10') or next(iter(strat.values()))
    exp_tag, exp_edge = verdict_from(proposed, nul.get('max_pct'))
    v = doc.get('verdict') or ''
    ok(v.startswith(exp_tag), f"verdict starts with recomputed tag {exp_tag!r} → {v[:60]}")
    ok(bool(doc.get('edge_found')) is exp_edge, f"edge_found == {exp_edge}")
    if nul:
        ok(nul.get('max_pct') is not None and proposed['accuracy_pct'] <= nul['max_pct'],
           f"proposed acc {proposed['accuracy_pct']}% <= shuffled-label ceiling {nul.get('max_pct')}%")

    print('[4] app wiring (fail CLOSED)')
    import app as A
    A.ML_STUDY_PATH = path
    A._ml_study_cache.update(mtime=None, size=None, data=None)
    loaded = A.load_ml_study()
    ok(isinstance(loaded, dict) and loaded.get('model') == doc.get('model'), 'load_ml_study() returns artifact')
    payload = A.ml_study_payload()
    ok(payload.get('ready') is True, 'ml_study_payload().ready is True')
    ok(payload.get('verdict') == v, 'payload verdict matches artifact')
    ok(payload.get('edge_found') is bool(doc.get('edge_found')), 'payload edge_found matches artifact')
    ok(len(payload.get('strategies') or {}) == len(strat), 'payload carries every strategy')
    ok(payload.get('stale') is False, f"freshness: age {payload.get('age_days')}d <= {A.ML_STUDY_MAX_AGE_DAYS}d")
    ok(bool(payload.get('disclosure')), 'disclosure text present')

    # missing file → honest "absent", never "edge found"
    with tempfile.TemporaryDirectory() as td:
        missing = pathlib.Path(td) / 'ml_edge_study.json'
        A.ML_STUDY_PATH = missing
        A._ml_study_cache.update(mtime=None, size=None, data=None)
        p = A.ml_study_payload()
        ok(p.get('ready') is False and 'build_ml_edge_study' in (p.get('error') or ''),
           'missing artifact → ready False + rebuild hint')
        ok(p.get('edge_found') is None, 'missing artifact does NOT imply edge')

        # tampered schema → rejected
        bad = pathlib.Path(td) / 'bad.json'
        tampered = dict(doc, schema=99)
        bad.write_text(json.dumps(tampered), encoding='utf-8')
        A.ML_STUDY_PATH = bad
        A._ml_study_cache.update(mtime=None, size=None, data=None)
        ok(A.load_ml_study() is None, 'schema mismatch → artifact rejected')
        A.ML_STUDY_PATH = path
        A._ml_study_cache.update(mtime=None, size=None, data=None)

    print('[5] API + Dashboard')
    src = (ROOT / 'app.py').read_text(encoding='utf-8')
    ok("'ml_study': ml_study_payload()" in src, '/api/stock payload includes ml_study')
    ok('ML accuracy is a single 80/20 split' not in src, 'stale in-sample disclaimer removed')
    dash = (ROOT / 'Dashboard.html').read_text(encoding='utf-8')
    ok('d.ml_study' in dash, 'Dashboard reads ml_study')
    ok('OOS study' in dash, 'Dashboard labels the recorded OOS study')
    ok('diagnostic' in dash, 'Dashboard labels in-app accuracy a diagnostic')
    ok('OOS ML study absent' in dash, 'Dashboard has an honest absent-state')
    ok(re.search(r"study\.edge_found \? 'var\(--color-green\)' : 'var\(--color-red\)'", dash) is not None,
       'verdict colour is driven by edge_found, not hard-coded green')

    print('[6] no overclaim')
    ok('profitab' not in v.lower(), 'verdict does not claim profitability')
    disc = (doc.get('disclosure') or '').lower()
    ok('profitab' in disc and re.search(r'\b(nahi|not)\b', disc) is not None,
       'disclosure explicitly denies being a profit proof')
    ok('predictive-edge' in disc, 'disclosure scopes the claim to predictive edge only')
    ok(bool(re.search(r'RESEARCH_REPORT', dash)), 'Dashboard points to net-of-cost report')
    return finish()


def finish():
    print(f'\n{PASS} passed, {FAIL} failed')
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
