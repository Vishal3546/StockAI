"""FIX-76: AI SCREENER — pure module (koi flask, koi requests).

Input: scan_results.json (nifty_scanner.py likhta hai). Ye file ab tak sirf
scanner aur verify_fixes.py padhte the — app.py me iska koi route tha hi nahi.
Ye module uska pehla structured consumer hai.

HONESTY (repo ka FIX-44 rule, aur tools/verify_ml_edge_study.py ka nateeja):
  • signal_score / composite / ensemble = RELATIVE RANK hain, probability nahi.
  • ml_acc  = GradientBoosting ki accuracy ek HI 20% holdout split par, usi
              stock ki apni history par. Walk-forward validated NAHI. Isliye ye
              "accuracy" claim nahi hai — diagnostic hai.
  • ml_edge = ml_acc - majority-class baseline. edge < 0 matlab model
              majority-class se bhi peeche -> ml_used_in_composite False.
  • 30 me se sirf 8 stocks par ML composite me actually use hua tha (measured,
      03-Oct-2026 wale scan me). Baaki 22 par ml_prob sirf number hai.
  Isliye page har row par batata hai "ML used: haan/nahi".

Timestamps: scanner `datetime.now().isoformat()` likhta hai = user ke machine ka
local time (IST). Naive parse karke IST maante hain — market_cockpit.py ka hi
pattern. Fixed +05:30 offset (IST me DST nahi), ZoneInfo par depend nahi.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), 'IST')

# Sort karne layak fields — WHITELIST. User-supplied key seedha use nahi karte
# (arbitrary attribute access / KeyError se bachne ke liye).
SORT_KEYS = ('signal_score', 'composite', 'ensemble', 'rsi', 'vol_ratio',
             'change_pct', 'price', 'ml_edge', 'ml_prob', 'symbol', 'sector')

# Page par jo fields dikhenge (baaki internal hain).
DISPLAY_FIELDS = ('symbol', 'sector', 'price', 'change_pct', 'rsi', 'vol_ratio',
                  'signal', 'signal_score', 'composite', 'ensemble',
                  'ml_prob', 'ml_acc', 'ml_baseline', 'ml_edge',
                  'ml_used_in_composite', 't1', 't2', 'sl', 'source',
                  'signal_basis')

_NUM_FIELDS = ('price', 'change_pct', 'rsi', 'vol_ratio', 'signal_score',
               'composite', 'ensemble', 'ml_prob', 'ml_acc', 'ml_baseline',
               'ml_edge', 't1', 't2', 'sl')


def _num(v):
    if isinstance(v, bool) or v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(',', ''))
    except (ValueError, TypeError):
        return None


def normalize_rows(results):
    """Scanner ke raw dicts -> typed rows. Whitelisted fields only."""
    if not isinstance(results, list):
        return []
    out = []
    for r in results:
        if not isinstance(r, dict):
            continue
        sym = str(r.get('symbol') or '').strip().upper()
        if not sym:
            continue
        row = {'symbol': sym}
        for f in _NUM_FIELDS:
            row[f] = _num(r.get(f))
        for f in ('sector', 'signal', 'source', 'signal_basis'):
            row[f] = (str(r[f]).strip() if r.get(f) is not None else None)
        row['ml_used_in_composite'] = bool(r.get('ml_used_in_composite'))
        # sirf wahi fields rakho jo page ko chahiye (future internal fields leak na hon)
        out.append({k: v for k, v in row.items() if k in DISPLAY_FIELDS or k == 'symbol'})
    return out


def load_scan(path):
    """scan_results.json -> dict, ya {'error': ...} agar missing/bad."""
    try:
        import json
        with open(path, encoding='utf-8') as fh:
            txt = fh.read()
    except FileNotFoundError:
        return {'error': ('scan_results.json nahi mila. Scanner chalao: '
                          'python nifty_scanner.py')}
    except Exception as e:
        return {'error': f'scan_results.json padha nahi gaya: {type(e).__name__}: {e}'}
    if not txt.strip():
        return {'error': 'scan_results.json khaali hai. Scanner chalao: python nifty_scanner.py'}
    try:
        d = json.loads(txt)
    except Exception as e:
        return {'error': f'scan_results.json valid JSON nahi: {type(e).__name__}: {e}'}
    if not isinstance(d, dict) or not isinstance(d.get('results'), list):
        return {'error': 'scan_results.json ka shape galat hai (results[] chahiye)'}
    rows = normalize_rows(d['results'])
    if not rows:
        return {'error': 'scan_results.json me koi valid row nahi hai'}
    return {
        'timestamp': str(d.get('timestamp') or ''),
        'total_scanned': _num(d.get('total_scanned')),
        'scan_time_seconds': _num(d.get('scan_time_seconds')),
        'rows': rows,
    }


def parse_scan_ts(ts):
    """'2026-10-03T12:27:39.808244' -> aware IST datetime, ya None."""
    s = str(ts or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s[:26] if '.' in s else s, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=IST)
    except ValueError:
        return None


def staleness(ts, now=None):
    """Kitna purana hai. Market-open/closed ka claim NAHI karta.

    Scanner manually chalta hai, isliye data roz fresh nahi hota. Ye sirf age batata
    hai — "market khula hai" jaisa inference YAHAN SE NAHI nikalna (02-Oct-2026
    Gandhi Jayanti thi: weekday tha, market band tha).
    """
    dt = parse_scan_ts(ts)
    if dt is None:
        return {'age_minutes': None, 'age_label': 'unknown', 'verdict': 'unknown'}
    ref = now if now is not None else datetime.now(IST)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=IST)
    mins = (ref - dt).total_seconds() / 60.0
    if mins < 0:
        # clock skew ya future timestamp — chhupao mat
        return {'age_minutes': round(mins, 1), 'age_label': 'future timestamp',
                'verdict': 'clock_skew'}
    if mins < 60:
        label, verdict = f'{int(mins)} min', 'fresh'
    elif mins < 60 * 24:
        label, verdict = f'{mins / 60:.1f} hours', 'today'
    else:
        label, verdict = f'{mins / (60 * 24):.1f} days', 'old'
    return {'age_minutes': round(mins, 1), 'age_label': label, 'verdict': verdict,
            'scan_dt': dt.isoformat()}


def filter_rows(rows, sector=None, signal=None, min_score=None, min_vol=None,
                q=None, ml_used=None):
    """Sab filters optional. None = apply nahi karna."""
    out = []
    sec = (sector or '').strip()
    sig = (signal or '').strip().upper()
    needle = (q or '').strip().upper()
    for r in rows:
        if sec and sec.lower() != 'all' and (r.get('sector') or '') != sec:
            continue
        if sig and sig != 'ALL' and (r.get('signal') or '').upper() != sig:
            continue
        if min_score is not None and not (r.get('signal_score') or 0) >= min_score:
            continue
        if min_vol is not None and not (r.get('vol_ratio') or 0) >= min_vol:
            continue
        if ml_used is not None and bool(r.get('ml_used_in_composite')) != bool(ml_used):
            continue
        if needle and needle not in r.get('symbol', ''):
            continue
        out.append(r)
    return out


def sort_rows(rows, key='signal_score', order='desc'):
    k = key if key in SORT_KEYS else 'signal_score'
    rev = str(order).lower() != 'asc'
    # None values hamesha NEECHE jaate hain, chahe asc ho ya desc. (reverse=True
    # ke saath sentinel-tuple wala trick ulta ho jaata — isliye alag kar diya.)
    have = [r for r in rows if r.get(k) is not None]
    miss = [r for r in rows if r.get(k) is None]
    have.sort(key=lambda r: (r[k].lower() if isinstance(r[k], str) else r[k]),
              reverse=rev)
    return have + miss


def facets(rows):
    """Filter UI banane ke liye counts (poore dataset ke, filtered ke nahi)."""
    sec, sig = {}, {}
    for r in rows:
        s = r.get('sector') or 'Unknown'
        sec[s] = sec.get(s, 0) + 1
        g = r.get('signal') or 'Unknown'
        sig[g] = sig.get(g, 0) + 1
    return {'sectors': dict(sorted(sec.items())),
            'signals': dict(sorted(sig.items(), key=lambda kv: -kv[1]))}


def ml_note(row):
    """ML fields ka honest matlab — page isi ko tooltip/footnote ki tarah use karta hai."""
    if row.get('ml_used_in_composite'):
        return ('ML composite me USE hua (edge >= 0). Par ml_acc ek hi holdout '
                'split ka hai, walk-forward validated nahi — diagnostic hai.')
    return ('ML composite me use NAHI hua (edge < 0, yaani majority-class se bhi '
            'peeche). ml_prob sirf number hai, iska signal se koi lena-dena nahi.')
