#!/usr/bin/env python3
"""
H-11 · Engine weight analysis — measured, not assumed.

Audit ka purana claim tha: "Volume Profile aur Regime near-constant hain, weights
redesign chahiye." Ye tool wahi claim 250-session history par *measure* karta hai.

Do alag cheezein hain, aur dono zaroori hain:
  • CROSS-SECTIONAL spread (ek hi din, alag-alag stocks) — yahi ranking me
    information hai. Flat engine = sabko same offset = rank me zero asar.
  • TIME-SERIES spread (ek hi stock, alag-alag din) — ye batata hai engine kitna
    slow/fast move karta hai (timing information).

Saath me decision-relevant numbers: har engine ka composite se rank-correlation,
aur DROP-ONE simulation (engine hatakar weights renormalize → kitne session-stock
ka band badalta hai).

Run:  python3 tools/analyze_engine_dispersion.py [--artifact score_calibration.json]
      (offline — koi network nahi; stored per-engine history se chalta hai)
"""
import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import score_calibration as C  # noqa: E402


def _std(values):
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def _ranks(values):
    """Average ranks (ties share) — Spearman ke liye."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)          # ranks → Spearman rho
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else None


def band(score, thresholds):
    if score is None:
        return None
    if score >= thresholds['buy_breakout']:
        return 'breakout'
    if score >= thresholds['buy_dip']:
        return 'dip'
    if score <= thresholds['short_sell']:
        return 'short'
    return 'watch'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--artifact', type=pathlib.Path, default=C.ARTIFACT_PATH)
    args = ap.parse_args()

    artifact = json.loads(args.artifact.read_text(encoding='utf-8'))
    history = artifact.get('history') or []
    thresholds = artifact['thresholds']
    weights = artifact['weights']
    mismatch = C.verify_engine_history(history)

    print("=" * 92)
    print(" H-11 · Engine weight analysis — 250-session measured evidence")
    print("=" * 92)
    print(f" artifact      : {args.artifact.name}  ({artifact.get('asof_session')}, "
          f"{artifact.get('sessions')} sessions, {artifact.get('samples')} scores)")
    print(f" weights       : {weights}")
    print(f" integrity     : stored composite vs engine history mismatch = {mismatch}"
          + ("  ✅" if mismatch == 0 else "  ❌ (artifact edited/adhoora)"))

    # ── 1) cross-sectional spread ────────────────────────────────────────
    print("\n[1] CROSS-SECTIONAL spread (ek din, alag stocks) — yahi rank me information hai")
    print(f"    {'engine':<18} {'meanSD':>7} {'minSD':>7} {'maxSD':>7} {'flat<2':>7} "
          f"{'uniq':>5} {'range':>9} {'weight':>7}")
    disp = artifact.get('engine_dispersion') or C.engine_dispersion(history)
    for name, d in sorted(disp.items(), key=lambda kv: -kv[1]['mean_session_std']):
        print(f"    {name:<18} {d['mean_session_std']:>7.2f} {d['min_session_std']:>7.2f} "
              f"{d['max_session_std']:>7.2f} {d['flat_session_share'] * 100:>6.1f}% "
              f"{d['unique_values']:>5} {str(d['min_value']) + '-' + str(d['max_value']):>9} "
              f"{weights.get(name, 0):>7.2f}")

    flat = [n for n, d in disp.items() if d['mean_session_std'] < 2.0]
    print(f"\n    → cross-sectionally FLAT engines (meanSD < 2): {flat or 'KOI NAHI'}")
    if flat:
        print("      Inka rank me koi yogdaan nahi — drop/diagnostic karna justified hai.")
    else:
        print("      Charon engine stocks ko alag karte hain — is evidence par koi engine")
        print("      drop karna justified NAHI hai (audit ka 'near-constant' claim yahan")
        print("      cross-sectionally support nahi hua).")

    # ── 2) time-series spread ────────────────────────────────────────────
    print("\n[2] TIME-SERIES spread (ek stock, alag din) — timing information")
    per_symbol = {}
    for row in history:
        for symbol, engine_map in (row.get('engines') or {}).items():
            for k, v in engine_map.items():
                per_symbol.setdefault(k, {}).setdefault(symbol, []).append(v)
    print(f"    {'engine':<18} {'meanWithinSD':>13} {'minWithinSD':>12} {'maxWithinSD':>12}")
    for k, name in sorted(C.ENGINE_KEYS.items(), key=lambda kv: kv[1]):
        sds = [_std(vals) for vals in per_symbol.get(k, {}).values() if len(vals) >= 20]
        if not sds:
            continue
        print(f"    {name:<18} {sum(sds) / len(sds):>13.2f} {min(sds):>12.2f} {max(sds):>12.2f}")

    # ── 3) engine ↔ composite rank correlation ───────────────────────────
    print("\n[3] Engine ka composite se rank-correlation (Spearman rho, pooled)")
    comp, per_eng = [], {k: [] for k in C.ENGINE_KEYS}
    for row in history:
        engines = row.get('engines') or {}
        for symbol, score in (row.get('scores') or {}).items():
            m = engines.get(symbol) or {}
            if all(k in m for k in C.ENGINE_KEYS):
                comp.append(score)
                for k in C.ENGINE_KEYS:
                    per_eng[k].append(m[k])
    for k, name in sorted(C.ENGINE_KEYS.items(), key=lambda kv: kv[1]):
        rho = _pearson(per_eng[k], comp)
        print(f"    {name:<18} rho = {rho:+.3f}   (weight {weights.get(name, 0):.2f})"
              if rho is not None else f"    {name:<18} rho = n/a")

    # ── 4) drop-one simulation ───────────────────────────────────────────
    print("\n[4] DROP-ONE simulation — engine hatane par kitne band badalte hain")
    print(f"    {'dropped engine':<18} {'weight renormalised':>20} {'band change':>12} {'share':>7}")
    total = 0
    for drop_key in C.ENGINE_KEYS:
        drop_name = C.ENGINE_KEYS[drop_key]
        kept_names = [n for n in weights if n != drop_name]
        kept_short = [C.ENGINE_KEYS_REV[n] for n in kept_names if n in C.ENGINE_KEYS_REV]
        tw = sum(weights[n] for n in kept_names) or 1.0
        changed = total = 0
        for row in history:
            engines = row.get('engines') or {}
            for symbol, score in (row.get('scores') or {}).items():
                m = engines.get(symbol) or {}
                if not all(k in m for k in C.ENGINE_KEYS):
                    continue
                base = sum(m[k] * weights[C.ENGINE_KEYS[k]] for k in kept_short) / tw
                bullish = sum(1 for k in kept_short if m[k] >= 65)
                bearish = sum(1 for k in kept_short if m[k] <= 35)
                # Confluence policy JAISE-KA-TAISA (>=4 → +5, >=3 → +2, <=35 x4 → -8).
                # 3 engines par +5 branch pahunch me hi nahi aata — ye drop ka asli
                # side-effect hai, isliye rule chup-chaap badla nahi gaya.
                bonus = 5 if bullish >= 4 else 2 if bullish >= 3 else -8 if bearish >= 4 else 0
                alt = int(max(5, min(98, base + bonus)))
                total += 1
                if band(alt, thresholds) != band(score, thresholds):
                    changed += 1
        share = (changed / total * 100) if total else 0.0
        renorm = '/'.join(f'{weights[n] / tw:.2f}' for n in kept_names)
        print(f"    {drop_name:<18} {renorm:>20} {changed:>12} {share:>6.1f}%")

    print("\n" + "=" * 92)
    print(" NOTE: Ye sab RELATIVE-RANK diagnostics hain — koi bhi number ye nahi batata ki")
    print(" koi weighting profitable hai. Weight change ka faisla isi evidence par ho,")
    print(" aur change hone par poori calibration dobara fit karni padegi.")
    print("=" * 92)
    return 0


if __name__ == '__main__':
    sys.exit(main())
