"""Causal signal-ledger scale-out simulator, NOT a certification/auto-order engine.

One position at a time; signal at close, entry at next Open; 50% at 2.5 ATR,
50% at 4 ATR; stop moves to entry after T1 (gross, not fee break-even).
Daily ambiguous bars use adverse stop-first ordering. Timeout exits are included.
Full-horizon entry candidates only. The caller must supply a genuinely past-only
signal ledger: accepting a ledger cannot establish that it was not overfit.
"""
import math
from datetime import date
from safety import valid_ohlcv


def simulate(frame, signals, *, fee, horizon=20, notional=100000,
             instrument='cash_delivery', borrow_bps_per_day=None):
    if not valid_ohlcv(frame) or horizon < 1 or int(horizon)!=horizon or not math.isfinite(notional) or notional<=0:
        raise ValueError('invalid simulation inputs')
    if not callable(fee):raise ValueError('explicit per-fill fee model required')
    if instrument not in ('cash_delivery','research_borrowed_cash'):
        raise ValueError('daily bars cannot validate intraday/futures execution')
    if borrow_bps_per_day is not None and (not math.isfinite(borrow_bps_per_day) or borrow_bps_per_day<0):
        raise ValueError('invalid borrow assumption')
    dates=[date.fromisoformat(str(t)[:10]) for t in frame.index]
    if len(set(dates))!=len(dates):raise ValueError('one bar per daily session required')
    last_exit=-1;trades=[];excluded=[];seen=set()
    for s in sorted(signals,key=lambda x:x['index']):
        i=s['index'];direction=s['direction'];atr=float(s['atr']);mult=float(s['sl_mult'])
        if not isinstance(i,int) or i<0 or i>=len(frame) or i in seen:
            raise ValueError('invalid/duplicate signal index')
        seen.add(i)
        session=str(frame.index[i])[:10]
        if s.get('information_end')!=session or not s.get('rule_id'):
            raise ValueError('signal ledger needs rule id and same-session information cutoff')
        if direction not in ('LONG','SHORT') or not all(math.isfinite(v) and v>0 for v in (atr,mult)):
            raise ValueError('invalid signal geometry')
        if i<last_exit:
            excluded.append(dict(index=i,reason='overlapping_position'));continue
        if i+horizon>=len(frame):
            excluded.append(dict(index=i,reason='incomplete_future_horizon'));continue
        if direction=='SHORT' and (instrument!='research_borrowed_cash' or borrow_bps_per_day is None):
            excluded.append(dict(index=i,reason='overnight_cash_short_requires_explicit_borrow_model'));continue
        sign=1 if direction=='LONG' else -1
        entry=float(frame.iloc[i+1].Open);qty=notional/entry
        stop=entry-sign*mult*atr;t1=entry+sign*2.5*atr;t2=entry+sign*4*atr
        if min(stop,t1,t2)<=0:
            excluded.append(dict(index=i,reason='nonpositive_price_geometry'));continue
        entryside='buy' if sign==1 else 'sell'
        exitside='sell' if sign==1 else 'buy'
        def charge(n,side):
            v=float(fee(n,side))
            if not math.isfinite(v) or v<0:raise ValueError('invalid fee result')
            return v
        costs=charge(notional,entryside);gross=0.;remaining=1.;touched=False;fills=[];borrow=0.
        for j in range(i+1,i+horizon+1):
            if sign==-1:
                days=1 if j==i+1 else (dates[j]-dates[j-1]).days
                borrow+=notional*remaining*(borrow_bps_per_day or 0)/10000*days
            b=frame.iloc[j];op,hi,lo=float(b.Open),float(b.High),float(b.Low)
            def exit_at(price, fraction, reason):
                nonlocal gross,costs,remaining
                gross+=sign*(price-entry)*qty*fraction
                costs+=charge(price*qty*fraction,exitside)
                remaining-=fraction
                fills.append(dict(session=str(frame.index[j])[:10],price=price,fraction=fraction,reason=reason))
            stop_hit=lo<=stop if sign==1 else hi>=stop
            if stop_hit:
                exit_at(min(op,stop) if sign==1 else max(op,stop),remaining,'stop_or_gap');break
            hit1=hi>=t1 if sign==1 else lo<=t1
            hit2=hi>=t2 if sign==1 else lo<=t2
            if not touched and hit1:
                exit_at(t1,.5,'target1');touched=True;stop=entry
                # No invented favorable intrabar order after the first target.
                if (lo<=entry if sign==1 else hi>=entry):
                    exit_at(entry,remaining,'ambiguous_bar_entry_stop');break
            if touched and hit2:
                exit_at(t2,remaining,'target2');break
            if j==i+horizon:
                exit_at(float(b.Close),remaining,'time_exit')
        last_exit=j
        costs+=borrow
        trades.append(dict(signal_session=session,rule_id=s['rule_id'],entry_session=str(frame.index[i+1])[:10],
                           direction=direction,entry=entry,quantity=qty,fills=fills,exit_index=j,
                           gross_pnl=round(gross,4),costs=round(costs,4),borrow_cost=round(borrow,4),
                           net_pnl=round(gross-costs,4),net_return_pct=round((gross-costs)/notional*100,4)))
    return dict(schema=1,model='signal-ledger-scaleout-v1',trades=trades,excluded=excluded,
                trade_count=len(trades),net_win_rate_pct=round(100*sum(t['net_pnl']>0 for t in trades)/len(trades),2) if trades else None,
                total_net_pnl=round(sum(t['net_pnl'] for t in trades),4),
                execution_validated=False,independent_holdout=False,
                assumptions=dict(horizon=horizon,instrument=instrument,notional_per_trade=notional,
                    borrow_bps_per_day=borrow_bps_per_day,borrow_policy='Calendar days including weekends; outstanding fraction at beginning of day, conservative full-day accrual',fill_policy='next Open; stop-first adverse daily ambiguity; fractional research quantities',
                    costs='Explicit callback on entry and EACH partial exit; assumptions are not a verified broker bill',
                    signal_policy='Caller-supplied past-only rule ledger; no rank membership or live edge inferred',
                    capital_policy='Fixed research notional per sequential trade; not compounded portfolio returns'))
