#!/usr/bin/env python3
"""
tools/verify_option_analytics.py — FIX-69 OPTIONS ANALYTICS SUITE
================================================================================
Do tarah ke checks:

  (A) UNIT — option_analytics ke pure functions ek synthetic chain par.
      Values pehle se haath se gin ke likhi hain, isliye formula galat ho to
      pakda jaata hai. (NSE sandbox se blocked hai, isliye live fetch test
      nahi hota — sirf maths + wiring.)

  (B) WIRING/HONESTY — app.py me route hai, Options.html me disclosure hai,
      aur kitne me koi fake "accuracy / win-rate" claim nahi ghusa.

Chalao:  python3 tools/verify_option_analytics.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import option_analytics as oa  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


# ── synthetic chain (haath se gini hui expected values) ───────────────────
def row(k, cei, ce_ltp, ce_iv, pei, pe_ltp, pe_iv):
    return {'strike': k, 'ce_oi': cei, 'ce_ltp': ce_ltp, 'ce_iv': ce_iv,
            'pe_oi': pei, 'pe_ltp': pe_ltp, 'pe_iv': pe_iv}


ROWS = [row(100, 200, 6, 20, 500, 2, 18),
        row(105, 800, 3, 19, 300, 3, 19),
        row(110, 400, 1, 21, 500, 6, 22)]
SPOT = 104.5

print('=' * 84)
print(' (A) UNIT — option_analytics pure functions')
print('=' * 84)

t = oa.chain_totals(ROWS)
check('chain_totals call OI = 1400', t['total_call_oi'] == 1400, str(t['total_call_oi']))
check('chain_totals put OI = 1300', t['total_put_oi'] == 1300, str(t['total_put_oi']))
check('PCR = 1300/1400 = 0.9286', abs(t['pcr_oi'] - 0.9286) < 1e-3, str(t['pcr_oi']))

w = oa.oi_walls(ROWS)
check('call wall = 105 (max CE OI)', w['call_wall'] == 105, str(w['call_wall']))
check('put wall = 100 (max PE OI)', w['put_wall'] == 100, str(w['put_wall']))
check('oi_walls sirf call_wall/put_wall deta hai',
      set(w) == {'call_wall', 'put_wall'}, str(sorted(w)))

check('ATM IV (spot 104.5) = 19 (105 strike)', oa.atm_iv(ROWS, SPOT) == 19,
      str(oa.atm_iv(ROWS, SPOT)))

mp = oa.max_pain(ROWS)
check('max pain = 105', mp == 105, str(mp))


def _liab(s):
    return sum(r['ce_oi'] * max(0, s - r['strike']) + r['pe_oi'] * max(0, r['strike'] - s)
               for r in ROWS)


_ks = sorted(r['strike'] for r in ROWS)
_argmin = min(_ks, key=lambda s: (_liab(s), s))
check('max pain = independently recomputed argmin(writers liability)', mp == _argmin,
      f'{mp} vs {_argmin}, liability={_liab(mp)}')

st = oa.straddle_price(ROWS, SPOT)
check('ATM strike = 105', st['atm_strike'] == 105, str(st['atm_strike']))
check('straddle = 3+3 = 6', st['straddle'] == 6, str(st['straddle']))
check('expected move % = 6/104.5 = 5.74', abs(st['expected_move_pct'] - 5.74) < 0.02,
      str(st['expected_move_pct']))

pay = oa.strategy_payoff([{'opt': 'CE', 'side': 'buy', 'strike': 105, 'premium': 3}],
                         [100, 102, 108])
check('long call payoff = [-3, -3, 0]', pay == [-3.0, -3.0, 0.0], str(pay))

sp = oa.strategy_stats([{'opt': 'CE', 'side': 'buy', 'strike': 105, 'premium': 3},
                        {'opt': 'CE', 'side': 'sell', 'strike': 110, 'premium': 1}],
                       [x / 2 for x in range(180, 250)])
check('bull call spread max profit = 3 (width 5 - debit 2)',
      abs(sp['max_profit'] - 3.0) < 0.01, str(sp['max_profit']))
check('bull call spread max loss = -2 (net debit)',
      abs(sp['max_loss'] + 2.0) < 0.01, str(sp['max_loss']))
check('bull call spread breakeven = 107 (105 + debit 2)',
      len(sp['breakevens']) == 1 and abs(sp['breakevens'][0] - 107) < 0.5,
      str(sp['breakevens']))

# edge cases — khaali/adhoora data par crash ya fake value nahi
e = oa.chain_totals([])
check('khaali chain → PCR None (fake 0 nahi)', e['pcr_oi'] is None, str(e))
check('khaali chain → max_pain None', oa.max_pain([]) is None, repr(oa.max_pain([])))
check('khaali chain → straddle None', oa.straddle_price([], 100) is None)
check('khaali chain → walls None/None',
      oa.oi_walls([])['call_wall'] is None and oa.oi_walls([])['put_wall'] is None)
_sp0 = oa.straddle_price(ROWS, None)
check('spot None par expected_move % None (divide-by-zero nahi)',
      _sp0 is None or _sp0.get('expected_move_pct') is None, repr(_sp0))

print('=' * 84)
print(' (B) WIRING + HONESTY')
print('=' * 84)

app_src = (ROOT / 'app.py').read_text(encoding='utf-8', errors='ignore')
page = (ROOT / 'Options.html').read_text(encoding='utf-8', errors='ignore')
dash = (ROOT / 'Dashboard.html').read_text(encoding='utf-8', errors='ignore')
mod = (ROOT / 'option_analytics.py').read_text(encoding='utf-8', errors='ignore')

check('app.py option_analytics import karta hai', 'import option_analytics' in app_src)
check("app.py /api/option_chain route hai", "@app.route('/api/option_chain/" in app_src)
check('app.py /options page route hai', "@app.route('/options')" in app_src)
check('app.py Option chain NSE v3 endpoint use karta hai', 'option-chain-v3' in app_src)
check('app.py expiry contract-info se leta hai (v3 ke liye zaroori)',
      'option-chain-contract-info' in app_src)
check('Dashboard se Options page ka link hai', 'href="/options"' in dash)

check('option_analytics pure hai (no flask/requests import)',
      'import flask' not in mod and 'import requests' not in mod)
check('Options.html SEBI disclosure rakhta hai', 'not SEBI registered' in page)
check('Options.html "no predictive edge" disclaimer rakhta hai',
      'predictive edge' in page.lower() or 'no proof' in page.lower()
      or 'predictive edge' in page)
check('Options.html relative /api/ fetch karta hai (localhost hardcode nahi)',
      "fetch('/api/option_chain/" in page and 'localhost' not in page)

# fake-accuracy guard — blueprint me "78% accuracy" jaisa claim tha; yahan NAHI hona chahiye.
# app.py ke PURANE hisson me "GUARANTEED"/"assumed win rates" jaise legit disclosure
# pehle se hain, isliye guard sirf FIX-69 block tak scope hai.
_i = app_src.find('FIX-69: OPTIONS ANALYTICS')
_j = app_src.find("@app.route('/')", _i) if _i >= 0 else -1
fix69 = app_src[_i:_j] if _i >= 0 and _j > _i else ''
check('app.py me FIX-69 block mila', bool(fix69), f'{len(fix69)} chars')

for label, src in (('option_analytics', mod), ('Options.html', page),
                   ('app.py FIX-69 block', fix69)):
    bad = [k for k in ('78% accuracy', 'win rate', 'winrate', 'accuracy of',
                       'guaranteed profit', 'guaranteed return')
           if k.lower() in src.lower()]
    check(f'{label} me fake accuracy/edge claim nahi', not bad, str(bad))

passed = sum(1 for _, ok, _ in results if ok)
print('=' * 84)
print(f' {passed} / {len(results)} checks passed')
print('=' * 84)
sys.exit(0 if passed == len(results) else 1)
