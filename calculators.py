"""
calculators.py — FIX-74: CALCULATORS HUB (pure maths, koi Flask/network nahi)
==============================================================================
Teen calculators. Sab kuch yahan Python me calculate hota hai — page sirf
dikhata hai. Iska matlab numbers TESTED code se aate hain, untested JS se nahi.

  • trade_costs()   — brokerage/STT/exchange/SEBI/stamp/GST ka poora breakdown
                      (research/costs.py ke FIX-59/73 verified model par)
  • position_size() — risk-per-trade se quantity
  • sip()           — SIP future value

Rates ke source research/costs.py me cited hain (NSE circular 27-Feb-2026 etc).
Brokerage BROKER-SPECIFIC hai, isliye wo input hai — koi ek broker ka rate
"universal" bana kar nahi dikhaya gaya.

Koi predictive claim nahi. Ye arithmetic hai, salaah nahi.
"""
from __future__ import annotations

from research.costs import CostConfig

# Rates kis date ke hain — page par dikhaya jaata hai taaki stale na lage.
RATES_AS_OF = '06-Oct-2026'
RATES_SOURCE = ('NSE circular 27-Feb-2026 (effective 01-Mar-2026): equity cash '
                '0.00307%, futures 0.00183%, options 0.03553% (premium par); '
                'IPFT ab transaction charge me included. STT delivery 0.1% dono '
                'taraf, intraday 0.025% sirf sell. Stamp delivery-buy 0.015%, '
                'intraday-buy 0.003%. SEBI ₹10/crore. GST 18% (brokerage+txn par).')


def trade_costs(notional, mode='delivery', brokerage_per_order=0.0,
                brokerage_pct=0.0, slippage_bps=0.0) -> dict | None:
    """Ek round-trip (buy + sell) ka poora cost breakdown.

    brokerage: `min(brokerage_pct% * notional, brokerage_per_order)` per side.
      ⚠️ `brokerage_pct` PERCENT me hai (0.03 = 0.03%), fraction me NAHI —
      CostConfig fraction leta hai, isliye yahan /100 hota hai. Ye bug FIX-74
      me test ne pakda: seedha pass karne par 0.03 matlab 3% ban jaata tha aur
      brokerage hamesha cap par bind hoti thi (bade notional par sahi lagta tha,
      chhote par galat).
      • Delivery par zyadaatar discount broker ₹0 lete hain → dono 0 bhejo.
      • Intraday/F&O aam taur par ₹20 ya 0.03% jo kam ho.
    slippage default 0 hai — user ko statutory+broker cost dikhta hai; market
    impact chahe to explicit bharde.
    """
    n = _num(notional)
    if n is None or n <= 0:
        return None
    intraday = str(mode).lower() != 'delivery'
    cfg = CostConfig(brokerage_pct=max(0.0, _num(brokerage_pct) or 0.0) / 100.0,
                     brokerage_cap=max(0.0, _num(brokerage_per_order) or 0.0),
                     slippage_bps=max(0.0, _num(slippage_bps) or 0.0))
    b = cfg.breakdown(n, 'buy_intraday' if intraday else 'buy')
    s = cfg.breakdown(n, 'sell_intraday' if intraday else 'sell')
    total = round(b['total'] + s['total'], 2)
    return {
        'notional': n,
        'mode': 'intraday' if intraday else 'delivery',
        'buy': b, 'sell': s,
        'total_charges': total,
        'total_pct': round(total / n * 100, 4),
        # Sabse useful number: itna % move chahiye sirf costs cover karne ke liye.
        'breakeven_move_pct': round(total / n * 100, 4),
        'rates_as_of': RATES_AS_OF,
    }


def position_size(capital, risk_pct, entry, stop, side='long') -> dict | None:
    """Risk-per-trade se quantity. Ye risk management hai, prediction nahi.

    qty = floor(risk_amount / per-share-risk) — hamesha NEICHE round, taaki
    risk kabhi budget se zyada na ho.
    """
    c, rp, e, st = _num(capital), _num(risk_pct), _num(entry), _num(stop)
    if None in (c, rp, e, st) or c <= 0 or e <= 0 or not (0 < rp <= 100):
        return None
    per_share = (e - st) if side == 'long' else (st - e)
    if per_share <= 0:
        # Long me stop entry ke neeche hona chahiye, short me upar. Warna
        # "risk" negative ban jaata hai aur qty garbage aati hai.
        return {'error': ('long ke liye stop entry se neeche hona chahiye'
                          if side == 'long'
                          else 'short ke liye stop entry se upar hona chahiye')}
    risk_amount = c * rp / 100.0
    qty = int(risk_amount // per_share)
    if qty <= 0:
        return {'error': 'itne chhote risk budget me 1 share bhi nahi aata — '
                         'risk % badhao ya stop tight karo'}
    value = qty * e
    return {
        'side': side, 'qty': qty,
        'risk_amount': round(risk_amount, 2),
        'per_share_risk': round(per_share, 4),
        'actual_risk': round(qty * per_share, 2),
        'position_value': round(value, 2),
        'capital_used_pct': round(value / c * 100, 2),
        'stop_distance_pct': round(per_share / e * 100, 2),
    }


def sip(monthly, years, annual_rate_pct) -> dict | None:
    """SIP future value. Har mahine ki kist MAHINE KE ANT me invest hoti hai
    (end-of-period convention) — page par bhi yahi likha hai.
    """
    p, y, r = _num(monthly), _num(years), _num(annual_rate_pct)
    if None in (p, y, r) or p <= 0 or y <= 0 or r < 0:
        return None
    n = int(round(y * 12))
    if n <= 0:
        return None
    invested = p * n
    i = r / 100.0 / 12.0
    if i == 0:
        fv = invested
    else:
        fv = p * (((1 + i) ** n - 1) / i)
    return {
        'monthly': p, 'months': n, 'annual_rate_pct': r,
        'invested': round(invested, 2),
        'future_value': round(fv, 2),
        'gains': round(fv - invested, 2),
        'convention': 'end-of-month investment (har kist mahine ke ant me)',
    }


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN guard
