"""
market_cockpit.py — FIX-70: MARKET COCKPIT (pure analytics, koi Flask/network nahi)
====================================================================================
NSE `/api/allIndices` ka RAW payload lekar cockpit ke numbers nikaalta hai.

Raw shape (verified from NSE-India-Scrapper sample response):
    {
      "timestamp": "06-Oct-2026 15:30:00",
      "advances": 884, "declines": 1909, "unchanged": 25,   ← WHOLE-MARKET, int
      "data": [ {
          "key": "BROAD MARKET INDICES",       ← grouping
          "index": "NIFTY 50", "indexSymbol": "NIFTY 50",
          "last": 22421.95, "variation": -78.55, "percentChange": -0.35,
          "pe": "26.45", "pb": "4.01", "dy": "1.2",        ← STRINGS
          "advances": "19", "declines": "31", "unchanged": "0",  ← STRINGS
          ...
      } ]
    }

⚠️ Trap: per-index fields STRING hain, top-level breadth INT. Dono alag handle hote
   hain. '-' / '' / missing → None (fake 0 nahi).

Sab kuch DESCRIPTIVE hai (market ka state dikhana). Koi predictive edge claim nahi.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# NSE ka timestamp hamesha IST hota hai. IST me DST nahi hota, isliye fixed
# +05:30 offset hi exact hai — zoneinfo/tzdata (Windows par aksar missing) ki
# zaroorat nahi. Isse machine ka apna timezone kuch bhi ho, age sahi aata hai.
IST = timezone(timedelta(hours=5, minutes=30), 'IST')

TOP_KEYS = ('advances', 'declines', 'unchanged')


def _f(v):
    """Safe float. NSE strings ('26.45'), '-' aur None sab handle karta hai."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(',', '').replace('%', '')
    if not s or s in {'-', '--', 'NA', 'N.A.', 'null', 'None'}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def market_breadth(payload) -> dict | None:
    """Whole-market advance/decline (top-level ints se).

    Returns dict with advances/declines/unchanged/total/ad_ratio/pct_up.
    Jis cheez ka data nahi, wo None — fake 0 nahi.
    """
    if not isinstance(payload, dict):
        return None
    a, d, u = (_f(payload.get(k)) for k in TOP_KEYS)
    parts = [x for x in (a, d, u) if x is not None]
    if not parts:
        return None
    total = sum(parts)
    return {
        'advances': a, 'declines': d, 'unchanged': u, 'total': total,
        'ad_ratio': round(a / d, 4) if (a is not None and d) else None,
        'pct_up': round(a / total * 100, 2) if (a is not None and total) else None,
    }


def _card(raw) -> dict:
    # NSE naye indices (jaise FINANCIAL SERVICES 25/50) par yearHigh/yearLow 0 bhejta
    # hai — wo asli value nahi, isliye 0 → None (jhootha 0 dikhane se behtar '—').
    return {
        'name': raw.get('index') or raw.get('indexSymbol'),
        'last': _f(raw.get('last')),
        'change': _f(raw.get('variation')),
        'pct': _f(raw.get('percentChange')),
        'open': _f(raw.get('open')), 'high': _f(raw.get('high')),
        'low': _f(raw.get('low')), 'prev_close': _f(raw.get('previousClose')),
        'year_high': _f(raw.get('yearHigh')) or None,
        'year_low': _f(raw.get('yearLow')) or None,
        'pe': _f(raw.get('pe')), 'pb': _f(raw.get('pb')), 'div_yield': _f(raw.get('dy')),
        'advances': _f(raw.get('advances')), 'declines': _f(raw.get('declines')),
    }


def index_cards(payload, names) -> list:
    """Maange gaye indices ke cards, usi order me. Na mile to skip (fake nahi)."""
    if not isinstance(payload, dict):
        return []
    by_name = {str(r.get('index') or r.get('indexSymbol') or '').upper(): r
               for r in (payload.get('data') or [])}
    out = []
    for n in names:
        raw = by_name.get(str(n).upper())
        if raw is not None:
            out.append(_card(raw))
    return out


def find_vix(payload):
    """INDIA VIX ka card (naam se dhoondhte hain — key group bharosemand nahi)."""
    cards = index_cards(payload, ['INDIA VIX'])
    return cards[0] if cards else None


def sector_heatmap(payload, key='SECTORAL INDICES') -> list:
    """Sectoral indices, percentChange ke hisaab se best → worst."""
    if not isinstance(payload, dict):
        return []
    rows = [_card(r) for r in (payload.get('data') or [])
            if str(r.get('key') or '').upper() == key.upper()]
    rows = [r for r in rows if r.get('name')]
    rows.sort(key=lambda r: (r['pct'] is None, -(r['pct'] or 0.0)))
    return rows


def extremes(sectors, n=3) -> dict:
    """Best/worst n sectors. Khaali list par khaali dict-list (fake nahi)."""
    ranked = [s for s in (sectors or []) if s.get('pct') is not None]
    return {'top': ranked[:n], 'bottom': list(reversed(ranked[-n:])) if ranked else []}


def data_age_minutes(timestamp, now=None) -> int | None:
    """NSE timestamp kitna purana hai — minutes me.

    ⚠️ NSE do format bhejta hai (live verify kiya 06-Oct-2026):
         '06-Oct-2026 10:24'      ← allIndices intraday (seconds NAHI)
         '06-Oct-2026 15:30:00'   ← seconds ke saath
       Dono accept hote hain. Na samajh aaye to None (jhoothi age nahi).
    """
    if not timestamp:
        return None
    ts = None
    for fmt in ('%d-%b-%Y %H:%M:%S', '%d-%b-%Y %H:%M'):
        try:
            ts = datetime.strptime(str(timestamp).strip(), fmt)
            break
        except ValueError:
            continue
    if ts is None:
        return None
    ts = ts.replace(tzinfo=IST)                      # NSE ka time IST hai
    ref = now if now is not None else datetime.now(IST)
    if ref.tzinfo is None:                           # naive now = IST maano
        ref = ref.replace(tzinfo=IST)
    delta = (ref - ts).total_seconds() / 60.0
    return int(round(delta))
