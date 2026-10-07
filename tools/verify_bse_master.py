#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FIX-86 verification — BSE master list (TradingView scanner) + index-verified mirror.

User ki complaint: "BSE ka master list implement kar do", aur usse pehle
"search karne pe only NSE ke hi share dikh rhe he".

Kya badla
---------
1. `tools/build_bse_master.py` — TradingView ke public scanner API se BSE universe
   harvest karta hai (no key, no credentials). Scanner ek query me max 100 rows
   deta hai (measure kiya: range[100,100] -> data=None, range[200,100] -> HTTP 400),
   isliye universe ko market_cap -> close -> volume par adaptive binary split
   kiya jata hai. Result: 4770 unique BSE stocks = scanner ka 100 % reported total.
2. `bse_master.json` — tracked artifact (548 KB).
3. `app.py` — `_load_bse_master()` + `_set_master_db()`. DYNAMIC_STOCK_DB par ab
   kahin direct assignment nahi hai; har code path (fresh fetch / stale cache /
   offline / curated fallback / background refresh) isi helper se jata hai, to
   BSE kabhi chhoot nahi sakta. Index: 2570 NSE + 4770 BSE = 7340.
4. Mirror ab INDEX-VERIFIED hai. FIX-85 ka mirror blind tha aur BSE-only stock
   (TAPARIA, DHOOTIN) ke liye jhootha `ex='NSE'` entry bana deta tha.

BSE ki apni files is sandbox se nahi milti (07-Oct-2026 ko dobara measure kiya):
    api.bseindia.com/Msource/*   -> HTTP 403
    EQ_ISINCODE_*.CSV bhavcopy   -> HTTP 200 par 0 bytes
    List_Scrips.html             -> HTTP 200, sirf Angular shell
    tvDatafeed search_symbol()   -> [] (credentials chahiye)

Run:  python tools/verify_bse_master.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import app as A  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import build_bse_master as BM  # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(f"  {'✅' if ok else '❌'} {name}" + (f'  → {detail}' if detail else ''))


def skip(name, why):
    RESULTS.append((name, True))
    print(f"  ⏭  {name}  → skip: {why}")


c = A.app.test_client()
ART = os.path.join(ROOT, 'bse_master.json')


# ═══════════════════════════════════════════════════════════════════════════
print("A) bse_master.json artifact")
# ═══════════════════════════════════════════════════════════════════════════
art = None
if os.path.exists(ART):
    try:
        art = json.load(open(ART, encoding='utf-8'))
    except Exception as e:                                   # noqa: BLE001
        check("artifact parses", False, str(e))
else:
    check("bse_master.json exists", False, "run: python tools/build_bse_master.py")

if art:
    rows = art.get('stocks') or []
    check("artifact parses", True)
    check("stocks list non-empty", len(rows) > 0, f"{len(rows)} rows")
    check("coverage >= 3000 stocks", len(rows) >= 3000, f"{len(rows)}")
    check("total field matches len(stocks)", art.get('total') == len(rows),
          f"total={art.get('total')} len={len(rows)}")
    check("generated_at present", bool(art.get('generated_at')), str(art.get('generated_at')))
    check("source names the scanner API",
          'scanner.tradingview.com' in (art.get('source') or ''),
          (art.get('source') or '')[:60])
    check("scanner_reported_total recorded",
          isinstance(art.get('scanner_reported_total'), int) and art['scanner_reported_total'] > 0,
          str(art.get('scanner_reported_total')))
    # harvested count scanner ke reported total se match kare — coverage ka proof
    check("harvest == scanner reported total (100 % coverage)",
          art.get('total') == art.get('scanner_reported_total'),
          f"{art.get('total')} / {art.get('scanner_reported_total')}")
    check("no truncated cells", not art.get('truncated_cells'),
          f"{len(art.get('truncated_cells') or [])} truncated")
    check("cells_queried recorded", isinstance(art.get('cells_queried'), int)
          and art['cells_queried'] > 1, str(art.get('cells_queried')))

    syms = [r.get('symbol') for r in rows]
    check("every row has a symbol", all(syms), "")
    check("symbols unique", len(set(syms)) == len(syms),
          f"{len(set(syms))} unique / {len(syms)}")
    check("symbols uppercase+stripped",
          all(s == s.strip().upper() for s in syms if s), "")
    check("every row has a name", all((r.get('name') or '').strip() for r in rows), "")
    # BSE-only listings — inhi ke liye ye kaam kiya gaya tha
    for s in ('TAPARIA', 'DHOOTIN'):
        check(f"BSE-only listing present: {s}", s in set(syms))
    # sanity: badi companies bhi honi chahiye
    for s in ('RELIANCE', 'TATASTEEL', 'HDFCBANK'):
        check(f"large cap present: {s}", s in set(syms))

    check("validate_artifact() passes on the real file",
          BM.validate_artifact(ART, 3000) == [],
          str(BM.validate_artifact(ART, 3000)))
else:
    for n in ("coverage", "uniqueness", "source"):
        skip(n, "artifact missing")


# ═══════════════════════════════════════════════════════════════════════════
print("B) validate_artifact() bad files pakadta hai")
# ═══════════════════════════════════════════════════════════════════════════
check("missing file reported", len(BM.validate_artifact(os.path.join(ROOT, '__nope__.json'))) == 1)
BAD = os.path.join(ROOT, 'tools', '_tmp_bad_master.json')
try:
    json.dump({"stocks": [{"symbol": "X", "name": "X"}], "source": "somewhere else"},
              open(BAD, 'w', encoding='utf-8'))
    probs = BM.validate_artifact(BAD, 3000)
    check("too-few stocks caught", any('only 1 stocks' in p or 'expected >=' in p for p in probs), str(probs))
    check("missing generated_at caught", any('generated_at' in p for p in probs), str(probs))
    check("wrong source caught", any('source' in p for p in probs), str(probs))
    json.dump({"stocks": [{"symbol": "A", "name": "a"}, {"symbol": "A", "name": "b"}],
               "generated_at": "x", "source": "scanner.tradingview.com"},
              open(BAD, 'w', encoding='utf-8'))
    probs2 = BM.validate_artifact(BAD, 1)
    check("duplicate symbols caught", any('duplicate' in p for p in probs2), str(probs2))
finally:
    if os.path.exists(BAD):
        os.remove(BAD)


# ═══════════════════════════════════════════════════════════════════════════
print("C) app.py wiring — har code path BSE merge karta hai")
# ═══════════════════════════════════════════════════════════════════════════
src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
import re as _re                                            # noqa: E402

assigns = [ln for ln in src.splitlines()
           if _re.match(r'^\s*DYNAMIC_STOCK_DB\s*=\s*', ln) and not ln.strip().startswith('#')]
check("only 2 direct assignments remain (module init + helper)", len(assigns) == 2,
      str([a.strip()[:40] for a in assigns]))
check("_set_master_db() defined", 'def _set_master_db(' in src)
check("_load_bse_master() defined", 'def _load_bse_master(' in src)
check("_master_index_keys() defined", 'def _master_index_keys(' in src)
check("BSE_MASTER_FILE points at repo root", "parent / 'bse_master.json'" in src)
check("every master path calls _set_master_db",
      src.count('_set_master_db(') >= 7, f"{src.count('_set_master_db(')} call sites")

bse_rows = A._load_bse_master(verbose=False)
check("_load_bse_master returns rows", len(bse_rows) >= 3000, f"{len(bse_rows)}")
check("all rows ex='BSE'", all(r.get('ex') == 'BSE' for r in bse_rows))
check("rows carry sym+name", all(r.get('sym') and r.get('name') for r in bse_rows))
check("_load_bse_master is memoised", A._load_bse_master(verbose=False) is bse_rows)


# ═══════════════════════════════════════════════════════════════════════════
print("D) merged index")
# ═══════════════════════════════════════════════════════════════════════════
db = A.DYNAMIC_STOCK_DB
import collections                                          # noqa: E402
split = collections.Counter((x.get('ex') or '?') for x in db)
check("index has both exchanges", split.get('NSE', 0) > 0 and split.get('BSE', 0) > 0,
      str(dict(split)))
check("BSE entries >= 3000", split.get('BSE', 0) >= 3000, str(split.get('BSE')))
keys = [(str(x.get('sym') or '').upper(), (x.get('ex') or 'NSE').upper()) for x in db]
check("no duplicate (sym, ex) in index", len(set(keys)) == len(keys),
      f"{len(set(keys))} / {len(keys)}")

idx = A._master_index_keys()
check("index key set matches DYNAMIC_STOCK_DB size", len(idx) == len(db),
      f"{len(idx)} vs {len(db)}")
check("BSE-only symbol NOT in NSE key set", ('TAPARIA', 'NSE') not in idx)
check("BSE-only symbol IS in BSE key set", ('TAPARIA', 'BSE') in idx)
check("dual-listed symbol in BOTH key sets",
      ('RELIANCE', 'NSE') in idx and ('RELIANCE', 'BSE') in idx)

# cache invalidation: DB badle to key set rebuild ho
_saved_db, _saved_keys, _saved_sig = A.DYNAMIC_STOCK_DB, A._MASTER_IDX_KEYS, A._MASTER_IDX_SIG
try:
    A.DYNAMIC_STOCK_DB = list(A.DYNAMIC_STOCK_DB) + [{'sym': 'ZZTESTX', 'name': 'z',
                                                      'ex': 'BSE', 'sec': None}]
    k2 = A._master_index_keys()
    check("index cache rebuilds when DB changes", ('ZZTESTX', 'BSE') in k2)
    A._MASTER_IDX_KEYS = None
    A.DYNAMIC_STOCK_DB = _saved_db
    check("index cache reflects rollback", ('ZZTESTX', 'BSE') not in A._master_index_keys())
finally:
    A.DYNAMIC_STOCK_DB, A._MASTER_IDX_KEYS, A._MASTER_IDX_SIG = _saved_db, _saved_keys, _saved_sig


# ═══════════════════════════════════════════════════════════════════════════
print("E) mirror ab index-verified hai (jhoothi listing nahi)")
# ═══════════════════════════════════════════════════════════════════════════
def _q(q):
    r = c.get('/api/search?q=' + q)
    check(f"GET /api/search?q={q} -> 200", r.status_code == 200, str(r.status_code))
    return r.get_json() or []


def _pairs(rows):
    return [(x.get('sym'), (x.get('ex') or '').upper()) for x in rows]


# 1. BSE-only stock: NSE sibling NAHI aana chahiye
tap = _q('TAPARIA')
tp = _pairs(tap)
check("BSE-only TAPARIA returns >=1 result", len(tap) >= 1, str(len(tap)))
check("TAPARIA BSE present", ('TAPARIA', 'BSE') in tp, str(tp))
check("TAPARIA gets NO fake NSE sibling", ('TAPARIA', 'NSE') not in tp, str(tp))

dho = _q('DHOOTIN')
dp = _pairs(dho)
check("BSE-only DHOOTIN has no fake NSE sibling",
      ('DHOOTIN', 'BSE') in dp and ('DHOOTIN', 'NSE') not in dp, str(dp))

# 2. dual-listed: dono exchange, aur ADJACENT
rel = _q('RELIANC')
rp = _pairs(rel)
check("dual-listed RELIANCE has both exchanges",
      ('RELIANCE', 'NSE') in rp and ('RELIANCE', 'BSE') in rp, str(rp))
if ('RELIANCE', 'NSE') in rp and ('RELIANCE', 'BSE') in rp:
    check("RELIANCE NSE/BSE adjacent", abs(rp.index(('RELIANCE', 'NSE'))
                                          - rp.index(('RELIANCE', 'BSE'))) == 1, str(rp))

# 3. mirror ka behaviour fabricated index par — dono states prove karo
_orig_db, _orig_keys, _orig_sig = A.DYNAMIC_STOCK_DB, A._MASTER_IDX_KEYS, A._MASTER_IDX_SIG
try:
    # (a) index me sibling HAI -> mirror add kare
    A.DYNAMIC_STOCK_DB = [{'sym': 'FOOONE', 'name': 'Foo One', 'ex': 'NSE', 'sec': 'Equity'},
                          {'sym': 'FOOONE', 'name': 'Foo One', 'ex': 'BSE', 'sec': 'Equity'}]
    A._MASTER_IDX_KEYS = None
    rows = _q('FOOONE')
    fp = _pairs(rows)
    check("verified mirror adds sibling when index has it",
          ('FOOONE', 'NSE') in fp and ('FOOONE', 'BSE') in fp, str(fp))

    # (b) index me sibling NAHI -> mirror chup rahe
    A.DYNAMIC_STOCK_DB = [{'sym': 'FOOTWO', 'name': 'Foo Two', 'ex': 'BSE', 'sec': 'Equity'}]
    A._MASTER_IDX_KEYS = None
    rows = _q('FOOTWO')
    fp = _pairs(rows)
    check("mirror does NOT invent sibling when index lacks it",
          ('FOOTWO', 'NSE') not in fp, str(fp))
finally:
    A.DYNAMIC_STOCK_DB, A._MASTER_IDX_KEYS, A._MASTER_IDX_SIG = _orig_db, _orig_keys, _orig_sig

# 4. unknown symbol: exchange invent nahi hoga (FIX-85 regression)
zz = _q('ZZZQQQX')
check("unknown symbol -> ex=None, not a guessed exchange",
      len(zz) == 1 and zz[0].get('ex') is None,
      str([(x.get('sym'), x.get('ex')) for x in zz]))
check("unknown symbol name admits uncertainty",
      'exchange confirm nahi hua' in (zz[0].get('name') or '') if zz else False)

# 5. cap
big = _q('TATA')
check("result cap respected", len(big) <= A.CONFIG['SEARCH_MAX_RESULTS'],
      f"{len(big)} <= {A.CONFIG['SEARCH_MAX_RESULTS']}")
bex = collections.Counter((x.get('ex') or 'None') for x in big)
check("popular query returns both exchanges", bex.get('NSE', 0) > 0 and bex.get('BSE', 0) > 0,
      str(dict(bex)))

# 6. adjacency invariant across a broad query: same symbol ke entries saath hon
for probe in ('BANK', 'STEEL', 'RELIANC'):
    rows = _q(probe)
    pos = {}
    adj_ok = True
    for i, x in enumerate(rows):
        pos.setdefault((x.get('sym') or '').upper(), []).append(i)
    for sym, ii in pos.items():
        if len(ii) > 1 and (max(ii) - min(ii)) != (len(ii) - 1):
            adj_ok = False
            check(f"{probe}: {sym} siblings adjacent", False, str(ii))
    if adj_ok:
        check(f"{probe}: all siblings adjacent", True, f"{len(rows)} results")


# ═══════════════════════════════════════════════════════════════════════════
print("F) regressions — pehle ke fixes intact")
# ═══════════════════════════════════════════════════════════════════════════
check("SEARCH_MAX_RESULTS = 20", A.CONFIG.get('SEARCH_MAX_RESULTS') == 20,
      str(A.CONFIG.get('SEARCH_MAX_RESULTS')))
check("Layer 2 (Yahoo) always runs — no len() gate",
      "if len(results) < 5" not in src)
check("frontend abort raised to 90s", "90000" in open(
    os.path.join(ROOT, 'Dashboard.html'), encoding='utf-8').read()
    or "90 000" in open(os.path.join(ROOT, 'Dashboard.html'), encoding='utf-8').read())
check("strict exchange 409 still wired (FIX-83)", 'strict_exch' in src)
check("per-exchange calibration intact (FIX-84)",
      os.path.exists(os.path.join(ROOT, 'score_calibration_bse.json')))


# ═══════════════════════════════════════════════════════════════════════════
p = sum(1 for _, ok in RESULTS if ok)
print()
print(f"{'=' * 70}")
print(f"  {p} / {len(RESULTS)} checks passed")
print(f"{'=' * 70}")
sys.exit(0 if p == len(RESULTS) else 1)
