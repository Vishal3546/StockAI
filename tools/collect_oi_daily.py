"""FIX-66: DAILY real-OI collector — future ki definitive OI study ke liye.

Real-OI study (study_real_oi.py) abhi sirf ~9 mahine ke GitHub data par INDICATIVE
hai. Asli jawaab ke liye 6-12 mahine ka roz-ka OI chahiye. Ye script roz ek snapshot
leti hai (live NSE option-chain se, koi API key nahi) aur per-symbol CSV me append
karti hai — wahi schema jo study_oi_signal.collect() likhta hai.

Roz kaise chale:
  • Market close ke BAAD chalao (>= 15:40 IST) taaki us din ka final OI capture ho.
  • Idempotent: agar aaj ka snapshot already hai to symbol skip hota hai (dobara nahi).
  • Rate-limit safe: symbols ke beech 2s sleep (Akamai).

Windows par roz apne-aap chalane ke liye Task Scheduler use karo (neeche .bat + note),
ya roz manually:  python tools\\collect_oi_daily.py

Run:
    python3 tools/collect_oi_daily.py            # poora universe, aaj ka snapshot
    python3 tools/collect_oi_daily.py --syms NIFTY,RELIANCE
    python3 tools/collect_oi_daily.py --status   # kitne din ka data hai per symbol
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.study_oi_signal import collect, ROOT as _R  # noqa: E402

OUTDIR = ROOT / 'reports' / 'oi_history'
IST = ZoneInfo('Asia/Kolkata')

# F&O universe — indices + liquid stocks (score_calibration UNIVERSE se aligned).
UNIVERSE = ['NIFTY', 'BANKNIFTY',
            'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'SBIN',
            'BHARTIARTL', 'ITC', 'KOTAKBANK', 'LT', 'WIPRO', 'AXISBANK',
            'MARUTI', 'TMPV', 'BAJFINANCE', 'SUNPHARMA', 'TITAN', 'ADANIENT',
            'POWERGRID', 'NTPC', 'ONGC', 'COALINDIA', 'TATASTEEL', 'TECHM',
            'ASIANPAINT', 'ULTRACEMCO', 'NESTLEIND', 'BAJAJFINSV', 'DRREDDY',
            'JSWSTEEL']


def _last_date(sym: str) -> str | None:
    f = OUTDIR / f'{sym.replace("^", "_")}.csv'
    if not f.exists():
        return None
    try:
        df = pd.read_csv(f)
        if df.empty or 'asof' not in df.columns:
            return None
        return str(df['asof'].iloc[-1])[:10]
    except Exception:
        return None


def status() -> int:
    today = datetime.now(IST).date().isoformat()
    print(f'  aaj (IST): {today}')
    for sym in UNIVERSE:
        f = OUTDIR / f'{sym.replace("^", "_")}.csv'
        n = 0
        last = _last_date(sym)
        if f.exists():
            try:
                n = len(pd.read_csv(f))
            except Exception:
                n = 0
        flag = '✅' if last == today else '·'
        print(f'  {flag} {sym:<11} {n:>4} snapshots  last={last or "—"}')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms', default='', help='comma-separated (default poora universe)')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--sleep', type=float, default=2.0)
    a = ap.parse_args()

    if a.status:
        return status()

    syms = ([s.strip().upper() for s in a.syms.split(',') if s.strip()]
            if a.syms else UNIVERSE)
    today = datetime.now(IST).date().isoformat()
    print(f'=== Daily OI collect · aaj={today} · {len(syms)} symbols ===')
    done = skip = fail = 0
    for sym in syms:
        if _last_date(sym) == today:
            print(f'  · {sym}: aaj ka snapshot already hai — skip')
            skip += 1
            continue
        rc = collect(sym, outdir=OUTDIR)
        if rc == 0:
            done += 1
        else:
            fail += 1
        time.sleep(a.sleep)
    print(f'=== done={done} skipped={skip} failed={fail} ===')
    print('  Note: 6-12 mahine baad tools/study_real_oi.py isi data par definitive')
    print('  walk-forward chala sakta hai. Abhi ye sirf data jama kar raha hai.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
