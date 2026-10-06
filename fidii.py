"""FIX-75: FII/DII activity — pure maths/parse module (koi flask, koi requests).

NSE ke /reports/fii-dii page par DO tables hain, aur dono ke numbers ALAG hain.
Ye confusion real hai — maine khud pehle galat endpoint guess kiya tha. NSE ke
apne page ke headings se authoritative labels:

    api/fiidiiTradeNse    -> "FII/FPI & DII trading activity on NSE
                              in Capital Market Segment"
    api/fiidiiTradeReact  -> "FII/FPI & DII trading activity on NSE, BSE and
                              MSEI in Capital Market Segment"

Isliye React > Nse (usme BSE + MSEI bhi judta hai). 05-Oct-2026 par measured:

    endpoint        FII net     DII net
    fiidiiTradeReact  -4699.14   +5181.62   <- Groww + niftytrader yahi dikhate hain
    fiidiiTradeNse    -4092.96   +4878.16   <- sirf NSE ka subset

Dono sahi hain, bas scope alag hai. Ye module DONO rakhta hai aur LABEL ke saath
deta hai taaki koi number bina context ke na dikhe.

NSE ye endpoint sirf LATEST trading day deta hai (koi date param nahi — page ka
JS /dist/js/sections/reports/fii-dii.js padh kar confirm kiya). History ke liye
tools/collect_fidii_daily.py roz append karta hai.

Values NSE se STRING me aate hain ("20,492.93") — comma ke saath. Yahi trap
allIndices me bhi hai (pe/advances strings). _f() handle karta hai.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# IST me DST nahi hota, isliye fixed offset exact hai. zoneinfo/tzdata Windows
# par aksar missing hota hai (requirements me nahi hai) — isliye ZoneInfo AVOID.
IST = timezone(timedelta(hours=5, minutes=30), 'IST')

# NSE ke apne page ke headings — inhi ko label ki tarah use karo, khud se
# "NSE only" mat likhna.
LABEL_ALL = 'NSE, BSE and MSEI - Capital Market segment'
LABEL_NSE = 'NSE only - Capital Market segment'
SOURCE = ('NSE India /reports/fii-dii '
          '(api/fiidiiTradeReact = all exchanges, api/fiidiiTradeNse = NSE only)')

_CAT_DII = 'DII'
_CAT_FII = 'FII/FPI'


def _f(v):
    """'20,492.93' -> 20492.93.  Garbage/None -> None (0.0 nahi)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(',', '').replace('\u20b9', '').strip()
    if not s or s in ('-', '--'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm_cat(c):
    s = str(c or '').strip().upper()
    if s.startswith('FII') or s.startswith('FPI'):
        return _CAT_FII
    if s.startswith('DII') or s.startswith('DPII'):
        return _CAT_DII
    return None


def parse_rows(rows, tol=0.011):
    """NSE payload -> normalised records, ya None agar shape galat hai.

    Har record me `net_matches` hota hai: net == buy - sell (NSE ka apna data
    internally consistent hona chahiye — ye ek asli integrity check hai, sirf
    display nahi). 05-Oct-2026: 20492.93 - 15311.31 = 5181.62 exact.
    """
    if not isinstance(rows, list) or not rows:
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cat = _norm_cat(r.get('category'))
        if cat is None:
            continue
        buy, sell, net = _f(r.get('buyValue')), _f(r.get('sellValue')), _f(r.get('netValue'))
        if buy is None or sell is None or net is None:
            continue
        out.append({
            'category': cat,
            'date': str(r.get('date') or '').strip(),
            'buy': round(buy, 2),
            'sell': round(sell, 2),
            'net': round(net, 2),
            'net_matches': abs((buy - sell) - net) <= tol,
        })
    return out or None


def _pair(recs):
    """[records] -> {'DII': rec, 'FII/FPI': rec} (jo mile)."""
    d = {}
    for r in recs or []:
        d.setdefault(r['category'], r)
    return d


def _derived(recs):
    """Arithmetic-only derived metrics. Koi prediction / signal verdict nahi."""
    p = _pair(recs)
    fii, dii = p.get(_CAT_FII), p.get(_CAT_DII)
    out = {
        'records': recs or [],
        'fii_net': fii['net'] if fii else None,
        'dii_net': dii['net'] if dii else None,
        'date': (fii or dii or {}).get('date') or None,
        'total_net': None,
        'dii_absorption_pct': None,
        'fii_gross': None,
        'dii_gross': None,
        'integrity_ok': None,
    }
    if fii and dii:
        out['total_net'] = round(fii['net'] + dii['net'], 2)
        out['integrity_ok'] = bool(fii['net_matches'] and dii['net_matches'])
    if fii:
        out['fii_gross'] = round(fii['buy'] + fii['sell'], 2)
    if dii:
        out['dii_gross'] = round(dii['buy'] + dii['sell'], 2)
        # FII ne becha aur DII ne kitna absorb kiya. Sirf tab meaningful jab
        # FII net negative ho; warna ratio ka koi matlab nahi -> None.
        if fii and fii['net'] < 0:
            out['dii_absorption_pct'] = round(100.0 * dii['net'] / abs(fii['net']), 2)
    return out


def snapshot(all_rows=None, nse_rows=None):
    """Dono variants ka combined snapshot. Kabhi fake number nahi banata."""
    a = _derived(parse_rows(all_rows))
    n = _derived(parse_rows(nse_rows))
    dates = {x['date'] for x in (a, n) if x['date']}
    return {
        'ok': bool(a['records'] or n['records']),
        'as_of': a['date'] or n['date'],
        'all_exchanges': {**a, 'label': LABEL_ALL},
        'nse_only': {**n, 'label': LABEL_NSE},
        'dates_match': (len(dates) <= 1) if dates else None,
        'source': SOURCE,
        'note': ('NSE ye endpoint sirf latest trading day deta hai. History '
                 'tools/collect_fidii_daily.py roz append karta hai.'),
    }


def streak(hist):
    """FII net ke consecutive same-sign din. hist = [(date, fii_net)] oldest->newest.

    Signed return: +3 = 3 din buying, -7 = 7 din selling. Khali/invalid -> 0.
    Ye sirf COUNT hai — "bullish/bearish" verdict NAHI.
    """
    if not isinstance(hist, list):
        return 0
    vals = []
    for item in hist:
        v = item[1] if isinstance(item, (list, tuple)) and len(item) >= 2 else item
        v = _f(v)
        if v is None:
            continue
        vals.append(v)
    if not vals:
        return 0
    last = vals[-1]
    if last == 0:
        return 0
    sign = 1 if last > 0 else -1
    n = 0
    for v in reversed(vals):
        if v * sign > 0:
            n += 1
        else:
            break
    return sign * n


def parse_nse_date(s):
    """'05-Oct-2026' -> date, ya None."""
    try:
        return datetime.strptime(str(s).strip(), '%d-%b-%Y').date()
    except (ValueError, TypeError):
        return None


def is_trading_day(d=None):
    """Sirf weekend check. HOLIDAYS isme nahi hain — isliye ye market-open
    claim NAHI hai (02-Oct-2026 Gandhi Jayanti weekday tha, market band tha)."""
    d = d or datetime.now(IST).date()
    return d.weekday() < 5
