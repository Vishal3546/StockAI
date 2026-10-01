#!/usr/bin/env python3
"""
FIX-43 · C-3 verifier — fitted scanner signal bands
===================================================
Asserts:
  [1] scanner_bands.json integrity (schema/model, sessions, symbols, histogram)
  [2] bands are the percentiles they claim to be (histogram se recompute)
  [3] the OLD hardcoded bands really were dead (measured, not asserted)
  [4] load_scanner_bands() fail-CLOSED: missing / stale / tampered / non-monotonic
      → (None, reason), aur signal 'UNRATED' rehta hai (guess nahi)
  [5] scanner wiring: ML ab signal ko gate nahi karta, signal_score ens hai,
      basis payload me aata hai
  [6] no overclaim: relative ranking hai, profit ka proof nahi

Run:  python tools/verify_scanner_bands.py
"""
import json
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✅ {msg}')
    else:
        FAIL += 1
        print(f'  ❌ {msg}')


def main():
    path = ROOT / 'scanner_bands.json'
    print('[1] artifact integrity')
    ok(path.exists(), 'scanner_bands.json committed at repo root')
    if not path.exists():
        return finish()
    doc = json.loads(path.read_text(encoding='utf-8'))
    ok(doc.get('schema') == 1, f"schema == 1 (got {doc.get('schema')})")
    ok(doc.get('model') == 'scanner-bands-ens-percentile-v1', f"model = {doc.get('model')}")
    n = doc.get('n_stock_sessions') or 0
    ok(n >= 2000, f'n_stock_sessions = {n:,} (real measurement volume)')
    ok(len(doc.get('symbols_scored') or []) >= 10,
       f"{len(doc.get('symbols_scored') or [])} symbols scored")
    hist = doc.get('histogram') or {}
    ok(sum(hist.values()) == n, f'histogram sums to n_stock_sessions ({sum(hist.values()):,})')
    bands = doc.get('bands') or {}
    ok(all(k in bands for k in ('STRONG BUY', 'BUY', 'WATCH', 'SELL')),
       f"all four band edges present: {bands}")
    ok(bands.get('STRONG BUY', 0) > bands.get('BUY', 0) > bands.get('WATCH', 0) > bands.get('SELL', 0),
       'bands monotonically decreasing')

    print('[2] bands == claimed percentiles (histogram se recompute)')
    total = sum(hist.values())
    cum, pct_at = 0, {}
    for k in sorted(hist, key=lambda x: int(x)):
        cum += hist[k]
        pct_at[int(k)] = 100.0 * cum / total
    for name, q in (('STRONG BUY', 95), ('BUY', 80), ('WATCH', 40), ('SELL', 10)):
        want = min(v for v, p in pct_at.items() if p >= q)
        ok(abs(bands[name] - want) < 1e-9,
           f'{name} band {bands[name]} == p{q} recomputed from histogram ({want})')
    dist = doc.get('distribution') or {}
    ok(dist.get('min') == min(int(k) for k in hist) and dist.get('max') == max(int(k) for k in hist),
       f"distribution min/max match histogram ({dist.get('min')}–{dist.get('max')})")

    print('[3] purane hardcoded bands measured-dead the')
    ob = doc.get('old_bands') or {}
    mc = ob.get('measured_counts_ml_neutralised') or {}
    ok(mc.get('STRONG BUY') == 0 and mc.get('BUY') == 0,
       f"old bands gave 0 STRONG BUY and 0 BUY (measured: {mc})")
    ok(mc.get('STRONG SELL') == 0, f"old bands gave 0 STRONG SELL too ({mc.get('STRONG SELL')})")
    ok(ob.get('buy_gate_unreachable_when_neutralised') is True,
       'BUY gate (eff>=52) unreachable when ML neutralised to 50.0')
    ok(ob.get('strong_buy_gate_unreachable_when_neutralised') is True,
       'STRONG BUY gate (eff>=55) unreachable when ML neutralised to 50.0')
    ok(ob.get('ens_needed_for_composite_70', 0) > ob.get('observed_max_ens', 10**9),
       f"composite>=70 needs ens>={ob.get('ens_needed_for_composite_70')} but observed max "
       f"ens={ob.get('observed_max_ens')}")
    ok(ob.get('sessions_reaching_composite_70') == 0,
       f"0/{n:,} sessions ever reached composite 70")
    fc = doc.get('fitted_signal_counts') or {}
    ok(all(fc.get(k, 0) > 0 for k in ('STRONG BUY', 'BUY', 'WATCH', 'SELL', 'STRONG SELL')),
       f'fitted bands make all five signals non-empty: {fc}')
    watch_share = 100.0 * mc.get('WATCH', 0) / max(total, 1)
    fitted_watch = 100.0 * fc.get('WATCH', 0) / max(total, 1)
    ok(watch_share > 90 and fitted_watch < watch_share,
       f'WATCH share {watch_share:.1f}% (old) → {fitted_watch:.1f}% (fitted)')

    print('[4] fail CLOSED')
    import nifty_scanner as NS
    NS.BANDS_PATH = str(path)
    NS._bands_cache.update(mtime=None, size=None, data=None)
    b, err = NS.load_scanner_bands()
    ok(b is not None and err is None, f'load_scanner_bands() ok (age {b.get("_age_days")}d)')
    ok(b is not None and NS.signal_from_bands(int(b['bands']['STRONG BUY']), b['bands']) == 'STRONG BUY',
       'score at the p95 edge maps to STRONG BUY')
    # har band edge inclusive hai: edge par wahi signal, ek neeche agla signal
    edge_cases = ((('STRONG BUY', 95), 'STRONG BUY'), (('BUY', 80), 'BUY'),
                  (('WATCH', 40), 'WATCH'), (('SELL', 10), 'SELL'))
    for (key, q), expected in edge_cases:
        got = NS.signal_from_bands(int(b['bands'][key]), b['bands'])
        ok(got == expected, f'score at p{q} ({int(b["bands"][key])}) → {got} (inclusive edge)')
    ok(NS.signal_from_bands(int(b['bands']['SELL']) - 1, b['bands']) == 'STRONG SELL',
       f"score just below p10 ({int(b['bands']['SELL']) - 1}) → STRONG SELL")

    with tempfile.TemporaryDirectory() as td:
        missing = pathlib.Path(td) / 'scanner_bands.json'
        NS.BANDS_PATH = str(missing)
        NS._bands_cache.update(mtime=None, size=None, data=None)
        b2, err2 = NS.load_scanner_bands()
        ok(b2 is None and 'build_scanner_bands' in (err2 or ''),
           f'missing artifact → (None, rebuild hint): {err2}')

        bad = pathlib.Path(td) / 'bad.json'
        bad.write_text(json.dumps(dict(doc, schema=99)), encoding='utf-8')
        NS.BANDS_PATH = str(bad)
        NS._bands_cache.update(mtime=None, size=None, data=None)
        b3, err3 = NS.load_scanner_bands()
        ok(b3 is None and 'schema' in (err3 or ''), f'schema mismatch rejected: {err3}')

        flat = pathlib.Path(td) / 'flat.json'
        flat_bands = dict(doc['bands'], BUY=doc['bands']['STRONG BUY'])
        flat.write_text(json.dumps(dict(doc, bands=flat_bands)), encoding='utf-8')
        NS.BANDS_PATH = str(flat)
        NS._bands_cache.update(mtime=None, size=None, data=None)
        b4, err4 = NS.load_scanner_bands()
        ok(b4 is None and 'monoton' in (err4 or ''), f'non-monotonic bands rejected: {err4}')

        old = pathlib.Path(td) / 'old.json'
        from datetime import datetime, timedelta, timezone
        stale_ts = (datetime.now(timezone.utc) - timedelta(days=NS.BANDS_MAX_AGE_DAYS + 5)).isoformat()
        old.write_text(json.dumps(dict(doc, generated_at_utc=stale_ts)), encoding='utf-8')
        NS.BANDS_PATH = str(old)
        NS._bands_cache.update(mtime=None, size=None, data=None)
        b5, err5 = NS.load_scanner_bands()
        ok(b5 is None and 'purane' in (err5 or ''), f'stale bands rejected: {err5}')

        NS.BANDS_PATH = str(path)
        NS._bands_cache.update(mtime=None, size=None, data=None)

    print('[5] scanner wiring')
    src = (ROOT / 'nifty_scanner.py').read_text(encoding='utf-8')
    ok("signal = 'UNRATED'" in src, 'unfitted bands → UNRATED, not a guessed signal')
    ok('signal_from_bands(ens_score' in src, 'signal driven by ens via fitted bands')
    ok("'signal_score': ens_score" in src, 'payload exposes the score that drives the signal')
    ok("'signal_basis': signal_basis" in src, 'payload exposes the band basis')
    ok('composite >= 70 and effective_ml_prob >= 55' not in src,
       'old hardcoded ML-gated bands removed')
    ok('ML diagnostic only' in src or 'diagnostic' in src, 'ML labelled diagnostic, not a gate')

    print('[6] no overclaim')
    disc = (doc.get('disclosure') or '').lower()
    ok('relative ranking' in disc and 'not' in disc,
       'disclosure says relative ranking, not validated profit')
    ok('research_report' in disc, 'disclosure points to the net-of-cost research report')
    ok('relative ranking' in src.lower() or 'validated profit nahi' in src,
       'scanner prints the same caveat')
    return finish()


def finish():
    print(f'\n{PASS} passed, {FAIL} failed')
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
