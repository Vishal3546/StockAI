#!/usr/bin/env python3
"""
tools/verify_calculators.py — FIX-74 CALCULATORS HUB SUITE
================================================================================
  (A) UNIT — calculators.py ke pure functions. Har expected value HAATH SE gina
      gaya hai (neeche har check par arithmetic likha hai), taaki formula galat
      ho to pakda jaaye. SIP ka future value ek INDEPENDENT loop se dobara
      nikal kar closed-form se milaya jaata hai.

  (B) RATES — statutory rates wahi hain jo source kehte hain (NSE circular
      27-Feb-2026 / Zerodha), aur FIX-73 wala 0.00307% drift nahi hua.

  (C) WIRING/HONESTY — routes, disclosures, koi fake accuracy/guarantee claim
      nahi.

Chalao:  python3 tools/verify_calculators.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import calculators as C  # noqa: E402
from research.costs import CostConfig  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


print('=' * 84)
print(' (A) UNIT — calculators.py')
print('=' * 84)

# ── TRADE COST: delivery ₹1,00,000, brokerage 0, slippage 0 ───────────────
# BUY : stt 0.1%×1L = 100 | exch 0.00307%×1L = 3.07 | sebi 0.0001%×1L = 0.10
#       stamp 0.015%×1L = 15 | gst 18%×(0+3.07+0.10) = 0.5706 → 0.57
#       total = 100+3.07+0.10+15+0.57 = 118.74
# SELL: stt 100 | exch 3.07 | sebi 0.10 | stamp 0 | gst 0.57 → 103.74
# ROUND TRIP = 222.48  → 0.22248% → 0.2225%
d = C.trade_costs(100000, 'delivery', 0, 0, 0)
check('delivery buy total = 118.74', d['buy']['total'] == 118.74, str(d['buy']))
check('delivery sell total = 103.74', d['sell']['total'] == 103.74, str(d['sell']))
check('delivery round trip = 222.48', d['total_charges'] == 222.48, str(d['total_charges']))
check('delivery cost % = 0.2225', d['total_pct'] == 0.2225, str(d['total_pct']))
check('delivery buy STT = 100 (0.1%)', d['buy']['stt'] == 100.0)
check('delivery sell STT = 100 (dono taraf lagta hai)', d['sell']['stt'] == 100.0)
check('delivery buy stamp = 15 (0.015%)', d['buy']['stamp'] == 15.0)
check('delivery sell stamp = 0 (sirf buy par)', d['sell']['stamp'] == 0.0)
check('exchange txn = ₹3.07 per lakh (FIX-73 rate)',
      d['buy']['exchange'] == 3.07, str(d['buy']['exchange']))
check('GST = 18% × (brokerage+exch+sebi) = 0.57', d['buy']['gst'] == 0.57, str(d['buy']['gst']))
check('breakeven_move_pct = total_pct (cost hurdle)',
      d['breakeven_move_pct'] == d['total_pct'], str(d['breakeven_move_pct']))

# ── TRADE COST: intraday ₹1,00,000, brokerage ₹20 cap / 0.03% ─────────────
# BUY : brokerage min(0.0003×1L=30, 20) = 20 | stt 0 (intraday buy nil)
#       exch 3.07 | sebi 0.10 | stamp 0.003%×1L = 3.00
#       gst 18%×(20+3.07+0.10) = 4.1706 → 4.17
#       total = 20+0+3.07+0.10+3.00+4.17 = 30.34
# SELL: brokerage 20 | stt 0.025%×1L = 25 | exch 3.07 | sebi 0.10 | stamp 0
#       gst 4.17 → total = 52.34
# ROUND TRIP = 82.68 → 0.0827%
i = C.trade_costs(100000, 'intraday', 20, 0.03, 0)
check('intraday buy total = 30.34', i['buy']['total'] == 30.34, str(i['buy']))
check('intraday sell total = 52.34', i['sell']['total'] == 52.34, str(i['sell']))
check('intraday round trip = 82.68', i['total_charges'] == 82.68, str(i['total_charges']))
check('intraday cost % = 0.0827', i['total_pct'] == 0.0827, str(i['total_pct']))
check('intraday BUY par STT nil (2026 verified)', i['buy']['stt'] == 0.0)
check('intraday SELL par STT = 25 (0.025%)', i['sell']['stt'] == 25.0)
check('intraday buy stamp = 3 (0.003%)', i['buy']['stamp'] == 3.0)
check('brokerage ₹20 cap bind hua (0.03% = ₹30 hota)',
      i['buy']['brokerage'] == 20.0, str(i['buy']['brokerage']))

# chhote notional par cap bind NAHI hota — 0.03% chalta hai
_s = C.trade_costs(25000, 'intraday', 20, 0.03, 0)
check('₹25k par brokerage = 0.03% × 25000 = 7.5 (cap bind nahi)',
      _s['buy']['brokerage'] == 7.5, str(_s['buy']['brokerage']))

# slippage explicit opt-in hai
_sl = C.trade_costs(100000, 'delivery', 0, 0, 5)
check('slippage 5bps → ₹50 per side', _sl['buy']['slippage'] == 50.0, str(_sl['buy']['slippage']))
check('slippage 0 default par 0 hi rehta hai', d['buy']['slippage'] == 0.0)

# ── POSITION SIZE ──────────────────────────────────────────────────────────
# capital 1L, risk 1% → ₹1000 | entry 2075 stop 2000 → per-share ₹75
# qty = floor(1000/75) = 13 | actual risk 975 | value 26975 → 26.98%
# stop distance = 75/2075 = 3.6145% → 3.61%
p = C.position_size(100000, 1, 2075, 2000, 'long')
check('qty = floor(1000/75) = 13', p['qty'] == 13, str(p))
check('risk budget = 1000', p['risk_amount'] == 1000.0)
check('actual risk = 975 (budget se kam, zyada nahi)', p['actual_risk'] == 975.0)
check('position value = 26975', p['position_value'] == 26975.0)
# capital_used_pct: 26975/100000 = 26.975. Python ka round(26.975, 2) = 26.97
# deta hai, 26.98 NAHI — kyunki binary float me 26.975 actually 26.974999...
# store hota hai (aur round() half-to-even hai). Maine pehle hand-math me
# round-half-up maan kar 26.98 likha tha — wo meri galti thi, code ki nahi.
# Repo bhar (research/costs.py samet) wahi round() use hota hai, isliye
# consistency ke liye yahan bhi wahi. Documented taaki dobara confuse na kare.
check('capital used = 26.97% (float 26.975 → round-half-even, hand-math 26.98 nahi)',
      p['capital_used_pct'] == 26.97, str(p['capital_used_pct']))
check('stop distance = 3.61%', p['stop_distance_pct'] == 3.61, str(p['stop_distance_pct']))
_sh = C.position_size(100000, 1, 2000, 2075, 'short')
check('short par same math (stop upar)', _sh['qty'] == 13, str(_sh))

# ── SIP ────────────────────────────────────────────────────────────────────
# ₹10k/mahina, 10 saal, 12%/yr → n=120, i=0.01, invested 12,00,000
s = C.sip(10000, 10, 12)
check('SIP invested = 10000×120 = 12,00,000', s['invested'] == 1200000.0, str(s['invested']))
check('SIP months = 120', s['months'] == 120)
# INDEPENDENT loop — closed-form par bharosa nahi, mahine-mahine jod kar dekha
_fv = 0.0
for _k in range(120):
    _fv += 10000 * (1.01 ** _k)
check('SIP FV closed-form == independent month-by-month loop',
      abs(s['future_value'] - round(_fv, 2)) <= 0.01,
      f"formula {s['future_value']} vs loop {round(_fv,2)}")
check('SIP gains = FV - invested', abs(s['gains'] - (s['future_value'] - s['invested'])) < 0.01)
check('SIP 0% return par FV == invested (koi fake growth nahi)',
      C.sip(10000, 10, 0)['future_value'] == 1200000.0)
check("SIP convention page ke liye disclosed hai", 'end-of-month' in s['convention'])

print('=' * 84)
print(' (B) RATES — source ke hisaab se')
print('=' * 84)

_cfg = CostConfig()
check('exch_pct = 0.00307% (NSE circular 27-Feb-2026)',
      abs(_cfg.exch_pct - 0.0000307) < 1e-15, str(_cfg.exch_pct))
check('STT delivery 0.1% dono taraf', _cfg.stt_buy == 0.001 and _cfg.stt_sell == 0.001)
check('STT intraday 0.025% sirf sell', _cfg.stt_intraday_sell == 0.00025)
check('stamp delivery-buy 0.015% / intraday-buy 0.003%',
      _cfg.stamp_buy == 0.00015 and _cfg.stamp_intraday_buy == 0.00003)
check('SEBI ₹10 per crore = 0.0001%', _cfg.sebi_pct == 0.000001)
check('GST 18%', _cfg.gst_rate == 0.18)
check('calculators rates_as_of set hai', bool(C.RATES_AS_OF), C.RATES_AS_OF)
check('rates source me NSE circular ka zikr hai', '27-Feb-2026' in C.RATES_SOURCE)

print('=' * 84)
print(' (C) EDGE CASES — galat input par crash ya fake value nahi')
print('=' * 84)

check('notional 0 → None', C.trade_costs(0) is None)
check('notional negative → None', C.trade_costs(-100) is None)
check("notional 'abc' → None", C.trade_costs('abc') is None)
check('notional None → None', C.trade_costs(None) is None)
_bad = C.position_size(100000, 1, 2000, 2075, 'long')
check('long me stop entry se upar → error (negative risk nahi)',
      isinstance(_bad, dict) and 'error' in _bad, str(_bad))
_bad2 = C.position_size(100000, 1, 2075, 2000, 'short')
check('short me stop entry se neeche → error',
      isinstance(_bad2, dict) and 'error' in _bad2, str(_bad2))
_tiny = C.position_size(1000, 0.1, 2075, 2000, 'long')
check('bahut chhota risk budget → error, qty 0/negative nahi',
      isinstance(_tiny, dict) and 'error' in _tiny, str(_tiny))
check('capital 0 → None', C.position_size(0, 1, 2075, 2000) is None)
check('risk 0% → None', C.position_size(100000, 0, 2075, 2000) is None)
check('risk 101% → None', C.position_size(100000, 101, 2075, 2000) is None)
check('entry 0 → None', C.position_size(100000, 1, 0, 2000) is None)
check('SIP monthly 0 → None', C.sip(0, 10, 12) is None)
check('SIP negative rate → None', C.sip(10000, 10, -5) is None)
check('SIP years 0 → None', C.sip(10000, 0, 12) is None)
check('calculators pure hai (no flask/requests/network)',
      'import flask' not in (ROOT / 'calculators.py').read_text(encoding='utf-8')
      and 'import requests' not in (ROOT / 'calculators.py').read_text(encoding='utf-8'))

print('=' * 84)
print(' (D) WIRING + HONESTY')
print('=' * 84)

app_src = (ROOT / 'app.py').read_text(encoding='utf-8', errors='ignore')
page = (ROOT / 'Calculators.html').read_text(encoding='utf-8', errors='ignore')
dash = (ROOT / 'Dashboard.html').read_text(encoding='utf-8', errors='ignore')

check('app.py calculators import karta hai', 'import calculators' in app_src)
for r in ('/api/calc/trade_costs', '/api/calc/position_size', '/api/calc/sip',
          '/api/calc/rates', '/calculators'):
    check(f'route {r} hai', f"@app.route('{r}')" in app_src)
check('Dashboard se Calculators link hai', 'href="/calculators"' in dash)
check('math server-side hai — page me formula NAHI (sirf display)',
      'future_value' in page and '(1+' not in page and 'Math.pow' not in page)
check('SEBI disclosure hai', 'not SEBI registered' in page)
check('brokerage broker-specific disclose hai', 'broker-specific' in page.lower()
      or 'Brokerage broker-specific' in page)
check('SIP return "guaranteed nahi" likha hai', 'guaranteed' in page.lower())
check('"investment advice nahi" likha hai', 'investment advice' in page.lower())
check('position size ko prediction nahi kaha', 'risk management' in page.lower())
check('relative /api/ fetch (localhost hardcode nahi)',
      "fetch(path+'?'" in page or "fetch('/api/calc" in page)
check('localhost hardcode nahi', 'localhost' not in page and '127.0.0.1' not in page)

_i = app_src.find('FIX-74: CALCULATORS HUB')
_j = app_src.find("@app.route('/')", _i) if _i >= 0 else -1
fix74 = app_src[_i:_j] if _i >= 0 and _j > _i else ''
check('app.py me FIX-74 block mila', bool(fix74), f'{len(fix74)} chars')
for label, src in (('calculators', (ROOT / 'calculators.py').read_text(encoding='utf-8')),
                   ('Calculators.html', page), ('app.py FIX-74 block', fix74)):
    bad = [k for k in ('78% accuracy', 'win rate', 'winrate', 'accuracy of',
                       'guaranteed profit', 'guaranteed return', 'sure shot')
           if k.lower() in src.lower()]
    check(f'{label} me fake accuracy/guarantee claim nahi', not bad, str(bad))

passed = sum(1 for _, ok, _ in results if ok)
print('=' * 84)
print(f' {passed} / {len(results)} checks passed')
print('=' * 84)
sys.exit(0 if passed == len(results) else 1)
