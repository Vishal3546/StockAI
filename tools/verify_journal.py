#!/usr/bin/env python3
"""
tools/verify_journal.py — FIX-96 TRADE JOURNAL SUITE
================================================================================
  (A) R-MULTIPLE MATH — LONG/SHORT, exact stop-out = -1R, invalid record = None
      (jhootha 0.0 nahi).
  (B) STATS — win rate / avg win / avg loss / expectancy / profit factor /
      stop-breaches, aur sample-size honesty labels (anecdote/thin/rough/reliable).
  (C) API — validation (stop entry ke galat taraf = 422), cost reuse,
      persistence, DELETE, corrupt-file fail-closed (write BLOCK, 409).
  (D) HONESTY — stats sirf user ke logged trades se; koi "accuracy"/prediction
      claim nahi; chhote sample par ANECDOTE saaf likha jaata hai.
  (E) WIRING — Timeframes.html me panel/form/handlers, .gitignore me file.

Chalao:  python3 tools/verify_journal.py
"""
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as A  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


TMP = tempfile.mkdtemp(prefix='jn_verify')
A.JOURNAL_FILE = os.path.join(TMP, 'trade_journal.json')   # asli journal ko chhuyein nahi
C = A.app.test_client()

print("\n── A. R-multiple math ────────────────────────────────────────────")
R = A.journal_r_multiple
check('LONG winner: 100→120, stop 90 = +2.0R', R(100, 90, 120, 'LONG') == 2.0,
      str(R(100, 90, 120, 'LONG')))
check('LONG stop-out = exactly -1.0R', R(100, 90, 90, 'LONG') == -1.0, str(R(100, 90, 90, 'LONG')))
check('LONG gap-down beyond stop = -1.5R', R(100, 90, 85, 'LONG') == -1.5, str(R(100, 90, 85, 'LONG')))
check('SHORT winner: 100→80, stop 110 = +2.0R', R(100, 110, 80, 'SHORT') == 2.0,
      str(R(100, 110, 80, 'SHORT')))
check('SHORT stop-out = exactly -1.0R', R(100, 110, 110, 'SHORT') == -1.0,
      str(R(100, 110, 110, 'SHORT')))
check('SHORT winner partial = +0.5R', R(200, 210, 195, 'SHORT') == 0.5, str(R(200, 210, 195, 'SHORT')))
check('invalid LONG (stop ABOVE entry) → None, 0.0 nahi', R(100, 110, 120, 'LONG') is None)
check('invalid SHORT (stop BELOW entry) → None', R(100, 90, 80, 'SHORT') is None)
check('zero risk (stop == entry) → None', R(100, 100, 110, 'LONG') is None)
check('non-numeric input → None', R('abc', 90, 100, 'LONG') is None and R(100, None, 100, 'LONG') is None)

print("\n── B. Stats + sample-size honesty ────────────────────────────────")
S = A.journal_stats
e = S([])
check('khali journal: closed=0', e['closed'] == 0)
check('khali journal: expectancy None (0.0 fake nahi)', e['expectancy_r'] is None, str(e['expectancy_r']))
check('khali journal: win_rate None', e['win_rate'] is None)
check('khali journal: label ANECDOTE', e['confidence'] == 'anecdote' and 'ANECDOTE' in e['disclosure'],
      e['confidence'])

two = S([{'r': 2.0, 'pnl_net': 900.0}, {'r': -1.0, 'pnl_net': -500.0}])
check('2 trades: win_rate 50.0%', two['win_rate'] == 50.0, str(two['win_rate']))
check('2 trades: avg_win +2.0R', two['avg_win_r'] == 2.0, str(two['avg_win_r']))
check('2 trades: avg_loss -1.0R', two['avg_loss_r'] == -1.0, str(two['avg_loss_r']))
check('2 trades: expectancy +0.5R', two['expectancy_r'] == 0.5, str(two['expectancy_r']))
check('2 trades: profit_factor 2.0', two['profit_factor'] == 2.0, str(two['profit_factor']))
check('2 trades: net_pnl 400.0', two['net_pnl'] == 400.0, str(two['net_pnl']))
check('-1.0R exact stop breach NAHI gina jaata', two['stop_breaches'] == 0)
check('-1.5R breach GINA jaata hai', S([{'r': -1.5}])['stop_breaches'] == 1)
check('1e-8 past stop = breach (tolerance sirf float noise ke liye)',
      S([{'r': -1.00000001}])['stop_breaches'] == 1)
check('1e-10 past stop = float noise, breach NAHI',
      S([{'r': -1.0000000001}])['stop_breaches'] == 0)

op = S([{'r': 1.5}, {'exit': None}])
check('open trade stats me mix NAHI hota (closed=1, open=1)',
      op['closed'] == 1 and op['open'] == 1, f"closed={op['closed']} open={op['open']}")
check('non-dict rows ignore hote hain', S([{'r': 1.0}, 'junk', None])['closed'] == 1)

check('n=5  → anecdote', S([{'r': 1}] * 5)['confidence'] == 'anecdote')
check('n=20 → thin', S([{'r': 1}] * 20)['confidence'] == 'thin')
check('n=29 → thin', S([{'r': 1}] * 29)['confidence'] == 'thin')
check('n=30 → rough', S([{'r': 1}] * 30)['confidence'] == 'rough')
check('n=99 → rough', S([{'r': 1}] * 99)['confidence'] == 'rough')
check('n=100 → reliable', S([{'r': 1}] * 100)['confidence'] == 'reliable')
check('thresholds disclosed in payload',
      S([])['thresholds'] == {'anecdote_below': 20, 'rough_from': 30, 'reliable_from': 100})

print("\n── C. API: validation, cost reuse, persistence, corrupt ──────────")
r = C.get('/api/journal')
j = r.get_json()
check('GET khali journal 200 + ok', r.status_code == 200 and j['ok'] is True)
check('GET: items [] aur corrupt False', j['items'] == [] and j['corrupt'] is False)

check('POST bina symbol → 422', C.post('/api/journal', json={'side': 'LONG'}).status_code == 422)
check('POST galat side → 422',
      C.post('/api/journal', json={'symbol': 'X', 'side': 'MAYBE'}).status_code == 422)
bad = C.post('/api/journal', json={'symbol': 'X', 'side': 'LONG', 'entry': 100,
                                   'stop': 110, 'qty': 10})
check('POST LONG with stop ABOVE entry → 422', bad.status_code == 422)
check('  reason me "NEECHE" bataya gaya', 'NEECHE' in (bad.get_json() or {}).get('error', ''),
      (bad.get_json() or {}).get('error', ''))
bad2 = C.post('/api/journal', json={'symbol': 'X', 'side': 'SHORT', 'entry': 100,
                                    'stop': 90, 'qty': 10})
check('POST SHORT with stop BELOW entry → 422', bad2.status_code == 422
      and 'UPAR' in (bad2.get_json() or {}).get('error', ''))
check('POST qty=0 → 422', C.post('/api/journal', json={'symbol': 'X', 'side': 'LONG',
                                                       'entry': 100, 'stop': 90, 'qty': 0}).status_code == 422)
check('POST entry=abc → 422', C.post('/api/journal', json={'symbol': 'X', 'side': 'LONG',
                                                           'entry': 'abc', 'stop': 90, 'qty': 1}).status_code == 422)
check('POST exit=-5 → 422', C.post('/api/journal', json={'symbol': 'X', 'side': 'LONG', 'entry': 100,
                                                         'stop': 90, 'qty': 1, 'exit': -5}).status_code == 422)
check('galat POSTs ke baad bhi journal khaali', C.get('/api/journal').get_json()['items'] == [])

ok1 = C.post('/api/journal', json={'symbol': 'reliance', 'side': 'LONG', 'entry': 1000,
                                   'stop': 950, 'qty': 10, 'exit': 1100,
                                   'setup': 'x' * 90, 'note': 'n' * 400})
j1 = ok1.get_json()
rec = j1['item']
check('POST valid closed trade → 200', ok1.status_code == 200 and j1['ok'] is True)
check('symbol uppercase + trim hota hai', rec['symbol'] == 'RELIANCE', rec['symbol'])
check('R = (1100-1000)/(1000-950) = 2.0', rec['r'] == 2.0, str(rec['r']))
check('risk_rupees = 50 x 10 = 500', rec['risk_rupees'] == 500.0, str(rec['risk_rupees']))
check('pnl_gross = 1000.0', rec['pnl_gross'] == 1000.0, str(rec['pnl_gross']))
_exp_cost = round(A.trade_cost_pct('intraday', 1000 * 10) / 100.0 * (1000 * 10), 2)
check('cost NAYE model se nahi — trade_cost_pct() reuse', rec['cost'] == _exp_cost,
      f"{rec['cost']} vs {_exp_cost}")
check('cost > 0 (free trade ka jhooth nahi)', rec['cost'] > 0, str(rec['cost']))
check('pnl_net = gross - cost', rec['pnl_net'] == round(1000.0 - _exp_cost, 2), str(rec['pnl_net']))
check('setup 60 char par truncate', len(rec['setup']) == 60)
check('note 240 char par truncate', len(rec['note']) == 240)
check('exchange default NSE', rec['exchange'] == 'NSE')
check('logged_at bhara hua', bool(rec['logged_at']), rec['logged_at'])

ok2 = C.post('/api/journal', json={'symbol': 'TCS', 'side': 'SHORT', 'entry': 2000,
                                   'stop': 2050, 'qty': 5, 'exchange': 'bse',
                                   'mode': 'delivery'})
rec2 = ok2.get_json()['item']
check('open trade (exit nahi) → r None', rec2['r'] is None, str(rec2['r']))
check('open trade → pnl None (0 fake nahi)', rec2['pnl_gross'] is None and rec2['pnl_net'] is None)
check('exchange lowercase → BSE', rec2['exchange'] == 'BSE', rec2['exchange'])
check('mode delivery accept', rec2['mode'] == 'delivery')

st = C.get('/api/journal').get_json()
check('GET: 2 items, closed=1, open=1',
      len(st['items']) == 2 and st['stats']['closed'] == 1 and st['stats']['open'] == 1,
      f"closed={st['stats']['closed']} open={st['stats']['open']}")
check('stats.expectancy ab +2.0R (sirf closed se)', st['stats']['expectancy_r'] == 2.0,
      str(st['stats']['expectancy_r']))
check('file disk par valid JSON list hai',
      isinstance(json.loads(open(A.JOURNAL_FILE, encoding='utf-8').read()), list))

did = C.delete('/api/journal?id=' + rec['id'])
check('DELETE by id → 200 + 1 bacha', did.status_code == 200 and len(did.get_json()['items']) == 1)
check('DELETE unknown id → 404',
      C.delete('/api/journal?id=nope').status_code == 404)

# corrupt file → chup-chaap khaali nahi, aur write BLOCK
with open(A.JOURNAL_FILE, 'w', encoding='utf-8') as f:
    f.write('{not json')
cg = C.get('/api/journal').get_json()
check('corrupt file: GET me corrupt=True (chhupaya nahi)', cg['corrupt'] is True)
check('corrupt file: items [] par stats safe', cg['stats']['closed'] == 0)
cp = C.post('/api/journal', json={'symbol': 'X', 'side': 'LONG', 'entry': 100,
                                  'stop': 90, 'qty': 1})
check('corrupt file: POST → 409 (data-loss se bachao)', cp.status_code == 409, str(cp.status_code))
check('corrupt file: POST ke baad bhi file waisi hi (overwrite NAHI hua)',
      open(A.JOURNAL_FILE, encoding='utf-8').read() == '{not json')
check('corrupt file: DELETE → 409',
      C.delete('/api/journal?id=x').status_code == 409)

os.remove(A.JOURNAL_FILE)
check('file hatne ke baad POST phir chalta hai',
      C.post('/api/journal', json={'symbol': 'X', 'side': 'LONG', 'entry': 100,
                                   'stop': 90, 'qty': 1}).status_code == 200)

print("\n── D. Honesty ────────────────────────────────────────────────────")
d0 = A.journal_stats([])['disclosure']
check('disclosure: "aapke khud log kiye trades"', 'aapke khud log kiye trades' in d0)
check('disclosure: model/score ki accuracy NAHI (negation)', 'accuracy nahi' in d0)
check('disclosure: prediction NAHI (negation)', 'prediction' in d0)
check('chhota sample: ANECDOTE saaf likha', 'ANECDOTE' in A.journal_stats([{'r': 1}])['disclosure'])
check('100+: "reliable" likha', 'reliable' in A.journal_stats([{'r': 1}] * 100)['disclosure'])
check('confidence ek LABEL hai, number nahi (fake % nahi)',
      isinstance(A.journal_stats([])['confidence'], str))
check('stats me koi "accuracy" key nahi',
      not any('accuracy' in k for k in A.journal_stats([])))

print("\n── E. Wiring ─────────────────────────────────────────────────────")
html = (ROOT / 'Timeframes.html').read_text(encoding='utf-8')
for _id in ('journalbox', 'jstats', 'jtable', 'jadd', 'jfill', 'j_sym', 'j_side',
            'j_entry', 'j_stop', 'j_qty', 'j_exit', 'j_mode', 'j_setup', 'jerr'):
    check(f'Timeframes.html me #{_id} hai', f'id="{_id}"' in html)
for _fn in ('function renderJournal', 'function journalFill', 'function loadJournal',
            'function journalAdd', 'LASTPLAN = p'):
    check(f'JS me {_fn} defined', _fn in html)
check('page load par loadJournal() call hota hai', '\nloadJournal();' in html)
gi = (ROOT / '.gitignore').read_text(encoding='utf-8')
check('.gitignore me trade_journal.json (per-user data commit nahi)', 'trade_journal.json' in gi)
routes = {(r.rule, m) for r in A.app.url_map.iter_rules() for m in r.methods}
for _m in ('GET', 'POST', 'DELETE'):
    check(f'/api/journal {_m} route registered', ('/api/journal', _m) in routes)

n_pass = sum(results)
n_fail = len(results) - n_pass
print('\n' + '=' * 82)
print(f" {n_pass} / {len(results)} checks passed" + (f"  ({n_fail} FAILED)" if n_fail else ''))
print('=' * 82)
sys.exit(1 if n_fail else 0)
