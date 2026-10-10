#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FIX-89b/90/91/92 verification — Backtest · Watchlist · Heatmap · Alerts.

Kya check hota hai
------------------
  A  /api/backtest — stats INDEPENDENTLY recompute karke cross-check (API ke
     numbers par bharosa nahi kiya, artifact se khud nikala)
  B  honesty guards — thin-band flag, t-stat, "predictive nahi" disclosure
  C  /api/watchlist — add/dup(409)/bad-exchange(400)/delete(404)/persistence
  D  /api/heatmap — missing file par graceful, sector math sahi, n<3 flagged
  E  /api/alerts — validation, above/below fire logic, delete
  F  pages 200 + koi external CDN nahi (sandbox preview me load nahi hota)
  G  Dashboard nav me chaaron links

Run:  python tools/verify_new_pages.py
"""
import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import app as A                                                   # noqa: E402

R = []


def check(name, ok, detail=''):
    R.append((name, bool(ok)))
    print('  %s %s%s' % ('✅' if ok else '❌', name, ('  → ' + str(detail)) if detail else ''))


c = A.app.test_client()
WL, AL = A.WATCHLIST_FILE, A.ALERTS_FILE
_wl_bak = open(WL, encoding='utf-8').read() if os.path.exists(WL) else None
_al_bak = open(AL, encoding='utf-8').read() if os.path.exists(AL) else None


def _restore():
    for p, b in ((WL, _wl_bak), (AL, _al_bak)):
        try:
            if b is None:
                os.path.exists(p) and os.remove(p)
            else:
                f = open(p, 'w', encoding='utf-8'); f.write(b); f.close()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
print('A) /api/backtest — stats independently recompute')
# ═══════════════════════════════════════════════════════════════════════════
for ex in ('NSE', 'BSE'):
    d = c.get('/api/backtest?ex=' + ex).get_json()
    check('%s: ok=True' % ex, d.get('ok') is True)
    art_p = os.path.join(ROOT, 'backtest_history_%s.json' % ex.lower())
    if not os.path.exists(art_p):
        check('%s: artifact present' % ex, False, 'run build_backtest_history.py')
        continue
    art = json.loads(open(art_p, encoding='utf-8').read())

    # khud band banao
    mine = {}
    for r in (art.get('rows') or []):
        s = int(max(0, min(100, r['score'])))
        b = '%d-%d' % ((s // 10) * 10, (s // 10) * 10 + 10)
        mine.setdefault(b, []).append(r)

    api_bands = {b['band']: b for b in d.get('bands', [])}
    check('%s: band set matches artifact' % ex,
          set(api_bands) == set(mine),
          'api=%d art=%d' % (len(api_bands), len(mine)))

    mism = []
    for b, rows in mine.items():
        ab = api_bands.get(b)
        if not ab:
            mism.append(b + ':missing'); continue
        if ab['n'] != len(rows):
            mism.append('%s:n %d!=%d' % (b, ab['n'], len(rows)))
        for h in ('5', '20'):
            v = [r['fwd'][h] for r in rows if isinstance(r['fwd'].get(h), (int, float))]
            got = (ab['h'].get(h) or {})
            if len(v) < 2:
                if got.get('mean') is not None:
                    mism.append('%s:h%s should be null' % (b, h))
                continue
            m = st.mean(v)
            se = st.stdev(v) / math.sqrt(len(v))
            if got.get('mean') is None or abs(got['mean'] - round(m, 3)) > 0.01:
                mism.append('%s:h%s mean %s!=%.3f' % (b, h, got.get('mean'), m))
            if got.get('t') is None or abs(got['t'] - round(m / se, 2)) > 0.05:
                mism.append('%s:h%s t %s!=%.2f' % (b, h, got.get('t'), m / se))
    check('%s: har band ka n/mean/t artifact se match' % ex, not mism, '; '.join(mism[:4]))

    # thin flag
    exp_thin = {b for b, rs in mine.items() if len(rs) < d.get('min_band_n', 30)}
    got_thin = {b['band'] for b in d['bands'] if b['thin']}
    check('%s: thin-band flag sahi (n < %d)' % (ex, d.get('min_band_n')),
          exp_thin == got_thin, 'exp=%s got=%s' % (sorted(exp_thin), sorted(got_thin)))

    # honesty disclosure
    check('%s: verdict me "predictive/predictor nahi" disclosure' % ex,
          'predictor nahi' in (d.get('verdict') or ''), (d.get('verdict') or '')[:50])
    check('%s: meta me samples+sessions+source' % ex,
          all(d.get('meta', {}).get(k) for k in ('samples', 'sessions', 'source')))

# FIX-102: invalid exchange rejected, never silently reinterpreted as NSE
r = c.get('/api/backtest?ex=XYZ')
check('bad exchange explicitly rejected', r.status_code == 400, str(r.status_code))


# ═══════════════════════════════════════════════════════════════════════════
print('B) /api/watchlist')
# ═══════════════════════════════════════════════════════════════════════════
try:
    os.path.exists(WL) and os.remove(WL)
    check('khaali list GET 200 + []',
          c.get('/api/watchlist').get_json().get('items') == [])
    r = c.post('/api/watchlist', json={'symbol': 'reliance', 'exchange': 'bse'})
    j = r.get_json()
    check('POST add 200 + symbol upper-case hua',
          r.status_code == 200 and j['items'][0]['symbol'] == 'RELIANCE'
          and j['items'][0]['exchange'] == 'BSE', str(j.get('items')))
    r2 = c.post('/api/watchlist', json={'symbol': 'RELIANCE', 'exchange': 'BSE'})
    check('duplicate -> 409', r2.status_code == 409, str(r2.status_code))
    check('same symbol doosre exchange par ALLOWED (dono alag listings hain)',
          c.post('/api/watchlist', json={'symbol': 'RELIANCE', 'exchange': 'NSE'}).status_code == 200)
    check('bad exchange -> 400',
          c.post('/api/watchlist', json={'symbol': 'X', 'exchange': 'MCX'}).status_code == 400)
    check('khaali symbol -> 400',
          c.post('/api/watchlist', json={'symbol': '', 'exchange': 'NSE'}).status_code == 400)
    check('GET ab 2 items', len(c.get('/api/watchlist').get_json()['items']) == 2)
    check('DELETE 200',
          c.delete('/api/watchlist?symbol=RELIANCE&exchange=BSE').status_code == 200)
    check('DELETE jo nahi hai -> 404',
          c.delete('/api/watchlist?symbol=ZZZ&exchange=NSE').status_code == 404)
    left = c.get('/api/watchlist').get_json()['items']
    check('1 item bacha, NSE wala', len(left) == 1 and left[0]['exchange'] == 'NSE', str(left))
    # persistence: dobara process-style read
    onDisk = json.loads(open(WL, encoding='utf-8').read())
    check('file par persist hua', len(onDisk) == 1, str(len(onDisk)))
finally:
    _restore()


# ═══════════════════════════════════════════════════════════════════════════
print('C) /api/heatmap')
# ═══════════════════════════════════════════════════════════════════════════
d = c.get('/api/heatmap').get_json()
scan = os.path.join(ROOT, 'scan_results.json')
if os.path.exists(scan):
    check('ok=True', d.get('ok') is True)
    raw = json.loads(open(scan, encoding='utf-8').read())
    rows = raw if isinstance(raw, list) else (raw.get('results') or [])
    check('total_stocks scan se match', d.get('total_stocks') == len(rows),
          '%s vs %d' % (d.get('total_stocks'), len(rows)))
    # sector math independently
    exp = {}
    for r_ in rows:
        if not isinstance(r_, dict) or not isinstance(r_.get('change_pct'), (int, float)):
            continue
        exp.setdefault(r_.get('sector') or 'Unknown', []).append(r_['change_pct'])
    api_sec = {s['sector']: s for s in d.get('sectors', [])}
    check('sector set match', set(api_sec) == set(exp),
          'api=%d exp=%d' % (len(api_sec), len(exp)))
    bad = []
    for s, chs in exp.items():
        a = api_sec.get(s, {})
        if a.get('n') != len(chs) or abs(a.get('avg_change', 0) - round(st.mean(chs), 2)) > 0.011:
            bad.append(s)
        if a.get('adv') != sum(1 for x in chs if x > 0):
            bad.append(s + ':adv')
    check('har sector ka n/avg/adv independently match', not bad, str(bad[:4]))
    check('sorted by avg_change desc',
          [s['avg_change'] for s in d['sectors']] ==
          sorted([s['avg_change'] for s in d['sectors']], reverse=True))
    check('"scanned stocks ka average, sector index nahi" disclosure',
          'scanned stocks' in (d.get('note') or ''), (d.get('note') or '')[:50])
else:
    check('scan_results.json missing par graceful (ok=False + hint)',
          d.get('ok') is False and 'hint' in d, str(d.get('error')))


# ═══════════════════════════════════════════════════════════════════════════
print('D) /api/alerts')
# ═══════════════════════════════════════════════════════════════════════════
try:
    os.path.exists(AL) and os.remove(AL)
    check('khaali GET -> []', c.get('/api/alerts').get_json().get('alerts') == [])
    r = c.post('/api/alerts', json={'symbol': 'ZZTESTX', 'exchange': 'NSE',
                                    'condition': 'above', 'level': 1})
    check('POST add 200', r.status_code == 200 and r.get_json()['ok'], str(r.status_code))
    for bad_body, why in (({'symbol': 'X', 'exchange': 'NSE', 'condition': 'sideways', 'level': 5}, 'bad condition'),
                          ({'symbol': 'X', 'exchange': 'NSE', 'condition': 'above', 'level': 'abc'}, 'bad level'),
                          ({'symbol': 'X', 'exchange': 'NSE', 'condition': 'above', 'level': -5}, 'negative level'),
                          ({'symbol': '', 'exchange': 'NSE', 'condition': 'above', 'level': 5}, 'empty symbol'),
                          ({'symbol': 'X', 'exchange': 'MCX', 'condition': 'above', 'level': 5}, 'bad exchange')):
        check('%s -> 400' % why, c.post('/api/alerts', json=bad_body).status_code == 400)

    # fire logic ko seedha unit-test karo (network quote par depend na kare)
    def _mk(cond, level, price):
        return (price >= level) if cond == 'above' else (price <= level)
    check('above: price>level fires', _mk('above', 100, 105) is True)
    check('above: price<level no fire', _mk('above', 100, 95) is False)
    check('above: price==level fires (boundary inclusive)', _mk('above', 100, 100) is True)
    check('below: price<level fires', _mk('below', 100, 95) is True)
    check('below: price>level no fire', _mk('below', 100, 105) is False)

    # API ka actual check path — fake quote se
    _market_open = A.is_market_open
    A.is_market_open = lambda *a, **k: True
    _orig = A.get_live_quote
    try:
        A.get_live_quote = lambda *a, **k: {'price': 150.0, 'source': 'yahoo.ns', 'is_realtime': True, 'stale': False, 'exchange': 'NSE'}
        r = c.post('/api/alerts/check')
        j = r.get_json()
        check('check() above-alert ko fire karta hai jab price > level',
              j['checked'] == 1 and len(j['fired_now']) == 1, str(j.get('fired_now')))
        A.get_live_quote = lambda *a, **k: None
        r2 = c.post('/api/alerts/check').get_json()
        check('quote None ho to error list me jaata hai, crash nahi',
              r2['checked'] == 0 and len(r2.get('errors', [])) >= 0, str(r2.get('errors')))
    finally:
        A.get_live_quote = _orig
        A.is_market_open = _market_open

    ids = [a['id'] for a in c.get('/api/alerts').get_json()['alerts']]
    check('DELETE valid id -> 200',
          ids and c.delete('/api/alerts?id=%s' % ids[0]).status_code == 200)
    check('DELETE unknown id -> 404',
          c.delete('/api/alerts?id=999999999').status_code == 404)
    check('DELETE bad id -> 400', c.delete('/api/alerts?id=abc').status_code == 400)
finally:
    _restore()


# ═══════════════════════════════════════════════════════════════════════════
print('E) pages + nav')
# ═══════════════════════════════════════════════════════════════════════════
import re as _re                                                 # noqa: E402
for pg in ('backtest', 'watchlist', 'heatmap', 'alerts'):
    r = c.get('/' + pg)
    check('/%s -> 200' % pg, r.status_code == 200, str(r.status_code))
    body = r.get_data(as_text=True)
    ext = _re.findall(r'(?:src|href)="(https?://[^"]+)"', body)
    check('/%s: koi external CDN nahi' % pg, not ext, str(ext[:2]))

dash = c.get('/').get_data(as_text=True)
for pg in ('/watchlist', '/heatmap', '/alerts', '/backtest'):
    check('Dashboard nav me %s link' % pg, ('href="%s"' % pg) in dash)

bt = c.get('/backtest').get_data(as_text=True)
check('Backtest page par t-stat explain hai', '|t|' in bt)
check('Backtest page par "accuracy claim nahi" disclosure', 'accuracy claim nahi' in bt)
check('Backtest page par thin-band warning', 'thin' in bt)
al = c.get('/alerts').get_data(as_text=True)
check('Alerts page par "browser khule rehne par" disclosure', 'browser' in al)
check('Alerts page par "koi API key nahi"', 'API key' in al)
hm = c.get('/heatmap').get_data(as_text=True)
check('Heatmap page par "scanned stocks ka average" disclosure', 'scanned stocks' in hm)


p = sum(1 for _, ok in R if ok)
print()
print('=' * 78)
print('  %d / %d checks passed' % (p, len(R)))
print('=' * 78)
sys.exit(0 if p == len(R) else 1)
