"""FIX-69: pure option-chain analytics — testable, no Flask/network.

Sab functions normalized `rows` lete hain:
    [{'strike': float, 'ce_oi': float, 'ce_ltp': float, 'ce_iv': float,
      'pe_oi': float, 'pe_ltp': float, 'pe_iv': float}, ...]

Ye NSE option-chain-v3 ke `records.data` se bante hain (app.py ka endpoint).
Koi network/FLASK dependency nahi — isliye unit-test synthetic data par ho sakte
hain (sandbox se NSE blocked hai).

Sab metrics DESCRIPTIVE hain (support/resistance/sentiment), predictive edge ka
proof NAHI — app ka standing stance (NO EDGE) yahan bhi lagu hai.
"""
from __future__ import annotations

import math


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def chain_totals(rows) -> dict:
    """Total call/put OI + PCR(OI)."""
    c = sum(_f(r.get('ce_oi')) for r in rows)
    p = sum(_f(r.get('pe_oi')) for r in rows)
    return {'total_call_oi': int(c), 'total_put_oi': int(p),
            'pcr_oi': round(p / c, 4) if c else None}


def oi_walls(rows) -> dict:
    """Max call OI strike = resistance; max put OI strike = support."""
    if not rows:
        return {'call_wall': None, 'put_wall': None}
    mc = max(rows, key=lambda r: _f(r.get('ce_oi')))
    mp = max(rows, key=lambda r: _f(r.get('pe_oi')))
    return {'call_wall': _f(mc.get('strike')) if _f(mc.get('ce_oi')) > 0 else None,
            'put_wall': _f(mp.get('strike')) if _f(mp.get('pe_oi')) > 0 else None}


def atm_iv(rows, spot) -> float | None:
    """ATM strike ki avg IV (CE+PE)."""
    if not rows or not spot:
        return None
    atm = min(rows, key=lambda r: abs(_f(r.get('strike')) - float(spot)))
    ivs = [_f(atm.get('ce_iv')), _f(atm.get('pe_iv'))]
    ivs = [v for v in ivs if v > 0]
    return round(sum(ivs) / len(ivs), 2) if ivs else None


def max_pain(rows) -> float | None:
    """Strike jahan option BUYERS ka total payoff minimum (writers ka max gain).

    Har candidate expiry-price S (= har strike) par:
        call writers' liability = sum CE_OI(K)*max(0, S-K)
        put  writers' liability = sum PE_OI(K)*max(0, K-S)
    Max pain = argmin(total liability). Descriptive — expiry-pin ka indication,
    guarantee nahi.
    """
    if not rows:
        return None
    if not any(_f(r.get('ce_oi')) > 0 or _f(r.get('pe_oi')) > 0 for r in rows):
        return None
    strikes = sorted(_f(r.get('strike')) for r in rows)
    best_s, best_loss = None, None
    for s in strikes:
        loss = 0.0
        for r in rows:
            k = _f(r.get('strike'))
            loss += _f(r.get('ce_oi')) * max(0.0, s - k)
            loss += _f(r.get('pe_oi')) * max(0.0, k - s)
        if best_loss is None or loss < best_loss:
            best_loss, best_s = loss, s
    return best_s


def straddle_price(rows, spot) -> dict | None:
    """ATM straddle = ATM CE LTP + ATM PE LTP (rough expected-move proxy)."""
    if not rows or not spot:
        return None
    atm = min(rows, key=lambda r: abs(_f(r.get('strike')) - float(spot)))
    c, p = _f(atm.get('ce_ltp')), _f(atm.get('pe_ltp'))
    if c <= 0 or p <= 0:
        return None
    price = c + p
    return {'atm_strike': _f(atm.get('strike')), 'straddle': round(price, 2),
            'expected_move_pct': round(price / float(spot) * 100, 2) if spot else None}


# ── Strategy builder (premium-based payoff) ────────────────────────────────
def strategy_payoff(legs, prices) -> list[float]:
    """legs = [{'opt':'CE'|'PE', 'side':'buy'|'sell', 'strike':float, 'premium':float}]
    returns P&L per underlying unit, before costs (multiply by lot size/quantity)."""
    out = []
    for s in prices:
        pnl = 0.0
        for leg in legs:
            k = _f(leg.get('strike')); prem = _f(leg.get('premium'))
            intrinsic = max(0.0, s - k) if leg.get('opt') == 'CE' else max(0.0, k - s)
            if leg.get('side') == 'buy':
                pnl += intrinsic - prem
            else:
                pnl += prem - intrinsic
        out.append(round(pnl, 2))
    return out


def strategy_stats(legs, prices) -> dict:
    """Analytical expiry payoff on S >= 0; no finite-grid bound claims.

    max_loss is a signed P&L; None with loss_unlimited=True means unbounded.
    Per unit, before costs; no margin/early exercise/assignment model.
    """
    if not legs:
        return {'max_profit': 0.0, 'max_loss': 0.0, 'breakevens': [],
                'profit_unlimited': False, 'loss_unlimited': False, 'payoff_at_spot': None}
    for leg in legs:
        if leg.get('opt') not in ('CE','PE') or leg.get('side') not in ('buy','sell'):
            raise ValueError('invalid option type/side')
        for name in ('strike','premium'):
            try:
                value = float(leg[name])
            except (KeyError, TypeError, ValueError):
                raise ValueError('finite strike/premium required')
            if not math.isfinite(value) or value < 0:
                raise ValueError('nonnegative finite strike/premium required')
    knots = sorted({0.0, *(float(l['strike']) for l in legs)})
    pay = strategy_payoff(legs, knots)
    tail = sum((1 if l['side']=='buy' else -1) for l in legs if l['opt']=='CE')
    roots = {knots[i] for i,v in enumerate(pay) if abs(v) < 1e-9}
    for i in range(1,len(knots)):
        if pay[i-1]*pay[i] < 0:
            roots.add(knots[i-1]-pay[i-1]*(knots[i]-knots[i-1])/(pay[i]-pay[i-1]))
    if tail:
        root = knots[-1]-pay[-1]/tail
        if root >= knots[-1]: roots.add(root)
    return {'max_profit': None if tail>0 else max(pay),
            'max_loss': None if tail<0 else min(pay),
            'profit_unlimited': tail>0, 'loss_unlimited': tail<0,
            'breakevens': sorted(round(r,2) for r in roots),
            'payoff_at_spot': None, 'units': 'per underlying unit before costs'}
