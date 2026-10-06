"""FIX-75: DAILY FII/DII collector — history banane ke liye.

NSE ka /api/fiidiiTradeReact endpoint sirf LATEST trading day deta hai (koi date
param nahi — maine page ka JS /dist/js/sections/reports/fii-dii.js padh kar
confirm kiya). Isliye history sirf ek hi tarah se banti hai: roz append karo.

Roz kaise chalao:
  • Market close ke BAAD (NSE ye data ~7-8 PM IST publish karta hai).
  • Idempotent: (date, scope) already hai to skip.
  • Dono scopes save hote hain: 'all' (NSE+BSE+MSEI) aur 'nse' (NSE only).
    Ye deliberately alag rows hain — mix karne se numbers galat ho jaate hain.

Windows Task Scheduler ya roz manually:
    python tools\\collect_fidii_daily.py
    python tools\\collect_fidii_daily.py --status

NOTE: ye script fixed +05:30 offset use karta hai (IST me DST nahi hota, isliye
exact) — market_cockpit.py ka bhi yahi pattern. ZoneInfo('Asia/Kolkata') bhi
chal jaata hai: pandas `tzdata>=2022.7` ko bina kisi platform marker ke require
karta hai, isliye `pip install -r requirements.txt` par tzdata Windows par bhi
install ho jaata hai (verify_fidii.py isi ko pin karta hai).
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import fidii  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30), 'IST')
OUT = ROOT / 'reports' / 'fidii_history.csv'
COLS = ['date', 'scope', 'fii_buy', 'fii_sell', 'fii_net',
        'dii_buy', 'dii_sell', 'dii_net', 'total_net']
ENDPOINTS = [('all', 'fiidiiTradeReact'), ('nse', 'fiidiiTradeNse')]
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


def _session():
    import requests
    s = requests.Session()
    s.headers.update({'User-Agent': UA})
    try:
        s.get('https://www.nseindia.com/reports/fii-dii', timeout=20)
    except Exception:
        pass
    return s


def _get(sess, path):
    try:
        r = sess.get('https://www.nseindia.com/api/' + path, timeout=25, headers={
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': 'https://www.nseindia.com/reports/fii-dii',
            'Accept-Encoding': 'identity'})
        if r.status_code == 200 and r.text and r.text[:1] in '{[':
            return r.json()
    except Exception:
        pass
    return None


def _existing():
    if not OUT.exists():
        return set()
    try:
        with open(OUT, encoding='utf-8') as fh:
            return {(r['date'], r['scope']) for r in csv.DictReader(fh)
                    if r.get('date') and r.get('scope')}
    except Exception:
        return set()


def _rows_from(recs):
    p = {r['category']: r for r in recs or []}
    f, d = p.get('FII/FPI'), p.get('DII')
    if not f or not d:
        return None
    return {
        'date': f['date'],
        'fii_buy': f['buy'], 'fii_sell': f['sell'], 'fii_net': f['net'],
        'dii_buy': d['buy'], 'dii_sell': d['sell'], 'dii_net': d['net'],
        'total_net': round(f['net'] + d['net'], 2),
    }


def status() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    have = _existing()
    dates = sorted({d for d, _ in have})
    print(f'  aaj (IST): {datetime.now(IST).date().isoformat()}')
    print(f'  file: {OUT}')
    print(f'  rows: {len(have)} (date,scope) pairs · {len(dates)} alag dates')
    if dates:
        print(f'  range: {dates[0]} .. {dates[-1]}')
        missing = [d for d in dates[-10:] if (d, 'all') not in have or (d, 'nse') not in have]
        if missing:
            print(f'  ⚠ dono scope nahi hain: {missing}')
    else:
        print('  · abhi koi data nahi — collector chalao (market close ke baad)')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--status', action='store_true')
    a = ap.parse_args()
    if a.status:
        return status()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    have = _existing()
    sess = _session()
    new = skip = fail = 0
    fresh = not OUT.exists()

    print(f'=== FII/DII collect · {datetime.now(IST).date().isoformat()} (IST) ===')
    with open(OUT, 'a', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        if fresh:
            w.writeheader()
        for scope, ep in ENDPOINTS:
            recs = fidii.parse_rows(_get(sess, ep))
            row = _rows_from(recs)
            if not row:
                print(f'  ✗ {scope:<4} ({ep}): data nahi mila — NSE block ya non-trading day')
                fail += 1
                continue
            if (row['date'], scope) in have:
                print(f'  · {scope:<4} {row["date"]}: already hai — skip')
                skip += 1
                continue
            if not all(r['net_matches'] for r in recs):
                print(f'  ⚠ {scope:<4} {row["date"]}: buy-sell != net (NSE data inconsistent) — '
                      f'phir bhi save kar rahe hain, source check karo')
            w.writerow({**row, 'scope': scope})
            have.add((row['date'], scope))
            new += 1
            print(f'  ✓ {scope:<4} {row["date"]}: FII {row["fii_net"]:+.2f} · '
                  f'DII {row["dii_net"]:+.2f} · total {row["total_net"]:+.2f}')

    print(f'=== new={new} skipped={skip} failed={fail} ===')
    if new == 0 and fail == 0:
        print('  (aaj ka data already tha — kuch nahi kiya)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
