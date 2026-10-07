#!/usr/bin/env python3
"""
FIX-86: BSE master list — TradingView scanner API se (no-key, no-credentials).

Kyun zaroori tha
----------------
`DYNAMIC_STOCK_DB` sirf NSE ke `EQUITY_L.csv` se banta tha → 2570 stocks, 100 % NSE.
BSE-only listings (TAPARIA, DHOOTIN, …) index me the hi nahi.

BSE ki apni files is sandbox se nahi milti (07-Oct-2026 ko dobara measure kiya):
  api.bseindia.com/Msource/*      -> HTTP 403
  EQ_ISINCODE_*.CSV (bhavcopy)    -> HTTP 200 par 0 bytes
  List_Scrips.html                -> HTTP 200, sirf Angular shell
  tvDatafeed search_symbol()      -> [] (credentials chahiye)

TradingView ka public scanner API chalta hai:
  POST https://scanner.tradingview.com/india/scan
BSE par type=stock -> 4770 symbols. **Par ek query me max 100 rows** milte hain
(range[100,100] -> data=None, range[200,100] -> HTTP 400). Isliye universe ko
market-cap par adaptive binary split karke harvest kiya jata hai.

Output: bse_master.json
  {"generated_at":..., "source":..., "total":N, "stocks":[{"symbol","name","close","volume","market_cap"}, ...]}

Usage:  python tools/build_bse_master.py [--output bse_master.json]
        python tools/build_bse_master.py --check      # existing file validate karo, network nahi
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_URL = "https://scanner.tradingview.com/india/scan"
DEFAULT_OUT = os.path.join(HERE, "bse_master.json")
PAGE_MAX = 100          # scanner ki hard limit (measure kiya: range[100,100] -> None)
MAX_DEPTH = 24
MCAP_FLOOR = 0.0
MCAP_CEIL = 1.0e15      # sabse badi BSE company se kaafi upar

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept-Encoding": "identity",
    "Content-Type": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}

_COLUMNS = ["name", "description", "exchange", "type", "subtype",
            "close", "volume", "market_cap_basic"]


class BseMasterError(RuntimeError):
    pass


def _query(filters, lo=0, n=1, tries=6, backoff=1.5):
    """Scanner ko call karo. `data is None` (rate-limit/soft-fail) par retry."""
    body = {
        "filter": filters,
        "options": {"lang": "en"},
        "markets": ["india"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": _COLUMNS,
        "sort": {"sortBy": "name", "sortOrder": "asc"},
        "range": [lo, n],
    }
    last = None
    for i in range(tries):
        try:
            r = requests.post(SCAN_URL, headers=_HEADERS, json=body, timeout=45)
            if r.status_code == 200:
                d = r.json()
                if d.get("data") is not None or n == 0:
                    return d
                last = "data=None"
            else:
                last = "HTTP %s" % r.status_code
        except Exception as e:                                    # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
        time.sleep(backoff * (i + 1))
    raise BseMasterError("scanner query fail (%s) filters=%s" % (last, filters))


def _count(filters):
    return int(_query(filters, 0, 1).get("totalCount") or 0)


def _fetch_all(filters, expected):
    """Ek cell ke saare rows (<= 100) laao."""
    d = _query(filters, 0, min(expected, PAGE_MAX))
    rows = d.get("data") or []
    if len(rows) < expected:
        # totalCount aur actual me halka drift normal hai; zyada ho to warn
        if len(rows) == 0:
            raise BseMasterError("cell me %d expected the, 0 mile" % expected)
    return rows


def _rows_to_stocks(rows):
    out = []
    for r in rows:
        c = r.get("d") or []
        if len(c) < len(_COLUMNS):
            continue
        sym = str(c[0] or "").strip().upper()
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "name": str(c[1] or "").strip(),
            "close": c[5],
            "volume": c[6],
            "market_cap": c[7],
        })
    return out


def harvest(verbose=True):
    """type=stock BSE universe ko numeric dimensions par adaptive split karke harvest karo.

    Scanner ek query me max 100 rows deta hai, isliye universe ko cells me todo.
    Primary dimension `market_cap_basic` hai; jab wo aage na bant paye (bahut se
    stocks ka mcap 0/null, ya float precision khatam) to `close`, phir `volume`
    par switch hota hai. Market_cap khaali wale stocks `in_range` me nahi aate,
    unke liye alag se `empty` filter chalaya jata hai.
    """
    base = [{"left": "exchange", "operation": "equal", "right": "BSE"},
            {"left": "type", "operation": "equal", "right": "stock"}]

    total = _count(base)
    if verbose:
        print("  BSE type=stock total = %d" % total)
    if total == 0:
        raise BseMasterError("scanner ne 0 BSE stocks bataye — kuch badla hai")

    stocks = {}
    cells = 0
    truncated = []
    # (dimension, lower, upper) — dimension badalte hi ceiling bhi reset hoti hai
    _CEIL = {"market_cap_basic": MCAP_CEIL, "close": 1.0e7, "volume": 1.0e12}
    _FLOOR = {"market_cap_basic": MCAP_FLOOR, "close": 0.0, "volume": 0.0}
    _ORDER = ["market_cap_basic", "close", "volume"]

    def walk(dim, lo, hi, depth, extra):
        nonlocal cells
        filt = extra + [{"left": dim, "operation": "in_range", "right": [lo, hi]}]
        c = _count(filt)
        if c == 0:
            return
        if c <= PAGE_MAX:
            for s in _rows_to_stocks(_fetch_all(filt, c)):
                stocks[s["symbol"]] = s
            cells += 1
            return

        mid = (lo + hi) / 2.0
        can_split = (depth < MAX_DEPTH) and (lo < mid < hi)
        if can_split:
            walk(dim, lo, mid, depth + 1, extra)
            walk(dim, mid, hi, depth + 1, extra)
            return

        # is dimension par aage nahi ban sakta — agli dimension try karo
        try:
            nxt = _ORDER[_ORDER.index(dim) + 1]
        except (ValueError, IndexError):
            nxt = None
        if nxt is not None:
            walk(nxt, _FLOOR[nxt], _CEIL[nxt], 0, filt)
            return

        for s in _rows_to_stocks(_fetch_all(filt, PAGE_MAX)):
            stocks[s["symbol"]] = s
        truncated.append((dim, lo, hi, c))
        cells += 1

    walk("market_cap_basic", MCAP_FLOOR, MCAP_CEIL, 0, base)

    # market_cap null/0 wale stocks `in_range` me nahi aate — `empty` se pakdo,
    # aur unhe `close` par baant do (593 stocks = 100-row cap se zyada).
    empty_mcap = base + [{"left": "market_cap_basic", "operation": "empty"}]
    try:
        no_mc = _count(empty_mcap)
    except BseMasterError:
        no_mc = 0
    if no_mc:
        if verbose:
            print("  market_cap khaali wale = %d (close par baant kar fetch)" % no_mc)
        if no_mc <= PAGE_MAX:
            for s in _rows_to_stocks(_fetch_all(empty_mcap, no_mc)):
                stocks.setdefault(s["symbol"], s)
            cells += 1
        else:
            walk("close", _FLOOR["close"], _CEIL["close"], 0, empty_mcap)

    return stocks, total, cells, truncated


def validate_artifact(path, min_stocks=3000):
    """Fresh clone / CI ke liye: file sane hai ya nahi (network nahi)."""
    problems = []
    if not os.path.exists(path):
        return ["bse_master.json missing — run: python tools/build_bse_master.py"]
    try:
        art = json.load(open(path, encoding="utf-8"))
    except Exception as e:                                       # noqa: BLE001
        return ["bse_master.json parse fail: %s" % e]
    rows = art.get("stocks") or []
    if len(rows) < min_stocks:
        problems.append("only %d stocks (expected >= %d)" % (len(rows), min_stocks))
    syms = [r.get("symbol") for r in rows]
    if len(set(syms)) != len(syms):
        problems.append("duplicate symbols present")
    if not art.get("generated_at"):
        problems.append("generated_at missing")
    if art.get("source", "").find("scanner.tradingview.com") < 0:
        problems.append("source field me scanner API nahi likha")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="BSE master list builder (TradingView scanner)")
    ap.add_argument("--output", default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true",
                    help="sirf existing file validate karo (network nahi)")
    ap.add_argument("--min-stocks", type=int, default=3000)
    a = ap.parse_args(argv)

    if a.check:
        probs = validate_artifact(a.output, a.min_stocks)
        if probs:
            for p in probs:
                print("  ❌ %s" % p)
            return 1
        art = json.load(open(a.output, encoding="utf-8"))
        print("  ✅ bse_master.json OK — %d stocks, generated %s"
              % (len(art["stocks"]), art.get("generated_at")))
        return 0

    print("[build_bse_master] TradingView scanner se BSE universe harvest kar rahe hain …")
    t0 = time.time()
    stocks, total, cells, truncated = harvest()
    rows = sorted(stocks.values(), key=lambda r: r["symbol"])
    ist = timezone(timedelta(hours=5, minutes=30))
    art = {
        "generated_at": datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S IST"),
        "source": "TradingView public scanner API (scanner.tradingview.com/india/scan), "
                  "exchange=BSE & type=stock — no API key / no credentials",
        "scanner_reported_total": total,
        "cells_queried": cells,
        "truncated_cells": [{"dim": t[0], "lo": t[1], "hi": t[2], "count": t[3]}
                            for t in truncated],
        "total": len(rows),
        "stocks": rows,
    }
    json.dump(art, open(a.output, "w", encoding="utf-8"), ensure_ascii=False)
    print("  ✅ %s — %d unique BSE stocks (scanner total %d), %d cells, %.1fs"
          % (os.path.basename(a.output), len(rows), total, cells, time.time() - t0))
    if truncated:
        print("  ⚠️  %d cell(s) me 100-row cap laga — coverage thodi kam ho sakti hai"
              % len(truncated))
    probs = validate_artifact(a.output, a.min_stocks)
    if probs:
        for p in probs:
            print("  ❌ %s" % p)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
