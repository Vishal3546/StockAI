"""FIX-100: next-open cash/holdings ledger, not a shifted-return approximation.

Signal observed at close t executes at Open[t + exec_lag], exec_lag >= 1.
Fixed units are held until the target allocation changes. Closing the last
position at final Close charges a real exit leg. All fills are fractional-unit
research fills; no volume constraints, borrow availability, margin liquidation,
circuit limits, stop orders or guaranteed executable prices are modelled.
Historical reports made with the old engine MUST be regenerated.
"""
from dataclasses import dataclass, field
from typing import Optional
import math
import numpy as np
import pandas as pd
from .costs import CostConfig, DEFAULT as DEFAULT_COSTS
TRADING_DAYS = 247
RF_ANNUAL = 0.065  # explicit research assumption, not live government yield

@dataclass
class BacktestResult:
    equity: pd.Series
    positions: pd.Series
    trades: list = field(default_factory=list)
    costs_total: float = 0.0
    metrics: dict = field(default_factory=dict)
    def to_dict(self):
        return {'metrics':self.metrics,'trades':self.trades,'costs_total':round(self.costs_total,2),
                'execution_model':'next-open-fixed-units-v2',
                'equity_curve':[{'time':str(i)[:10],'equity':round(float(v),2)} for i,v in self.equity.items()]}

def _metrics(equity, trades, costs_total, capital, exposure):
    if equity.empty: return {}
    path=np.r_[capital,equity.to_numpy(dtype=float)]
    rets=path[1:]/path[:-1]-1
    years=max(len(rets)/TRADING_DAYS,1/TRADING_DAYS)
    total=path[-1]/capital-1
    cagr=(path[-1]/capital)**(1/years)-1 if path[-1]>0 else -1.0
    excess=rets-((1+RF_ANNUAL)**(1/TRADING_DAYS)-1)
    sd=float(np.std(rets,ddof=1)) if len(rets)>1 else 0
    downside=float(np.sqrt(np.mean(np.minimum(excess,0)**2)))
    dd=path/np.maximum.accumulate(path)-1
    wins=[t for t in trades if t['pnl']>0]; losses=[t for t in trades if t['pnl']<=0]
    gw=sum(t['pnl'] for t in wins);gl=-sum(t['pnl'] for t in losses)
    return {'total_return_pct':round(total*100,2),'cagr_pct':round(cagr*100,2),
            'vol_pct':round(sd*np.sqrt(TRADING_DAYS)*100,2),
            'sharpe':round(float(excess.mean()/sd*np.sqrt(TRADING_DAYS)),2) if sd>0 else None,
            'sortino':round(float(excess.mean()/downside*np.sqrt(TRADING_DAYS)),2) if downside>0 else None,
            'max_drawdown_pct':round(float(dd.min()*100),2),
            'calmar':round(float(cagr/abs(dd.min())),2) if dd.min()<0 else None,
            'trades':len(trades),'win_rate_pct':round(len(wins)/len(trades)*100,1) if trades else None,
            'avg_win':round(float(np.mean([t['pnl'] for t in wins])),2) if wins else 0,
            'avg_loss':round(float(np.mean([t['pnl'] for t in losses])),2) if losses else 0,
            'profit_factor':round(gw/gl,2) if gl>0 else None,
            'avg_hold_days':round(float(np.mean([t['hold_days'] for t in trades])),1) if trades else None,
            'exposure_pct':round(exposure*100,1),'costs_paid':round(costs_total,2),
            'cost_drag_pct':round(costs_total/capital*100,2),
            'ledger_reconciliation_error':round(float(path[-1]-capital-sum(t['pnl'] for t in trades)),8)}

def run_backtest(df:pd.DataFrame, target_pos:pd.Series, capital:float=100000.,
                 cost:Optional[CostConfig]=None, exec_lag:int=1,
                 allow_short:bool=False, direction:int=1)->BacktestResult:
    if not math.isfinite(capital) or capital<=0: raise ValueError('positive finite capital required')
    if isinstance(exec_lag,bool) or not isinstance(exec_lag,int) or exec_lag<1:
        raise ValueError('exec_lag >= 1: close-derived signals cannot fill at the same open')
    if df.empty or not {'Open','Close'}.issubset(df.columns): raise ValueError('Open and Close required; do not invent fills')
    if not df.index.is_unique or not df.index.is_monotonic_increasing: raise ValueError('sorted unique index required')
    prices=df[['Open','Close']].astype(float)
    if not np.isfinite(prices.values).all() or (prices<=0).any().any():raise ValueError('invalid fill prices')
    raw=target_pos.reindex(df.index).fillna(0).astype(float)*(-1 if direction<0 else 1)
    if not np.isfinite(raw.values).all() or (raw.abs()>1).any():raise ValueError('allocation must be finite within [-1,1]')
    if not allow_short: raw=raw.clip(lower=0)
    pos=raw.shift(exec_lag).fillna(0)
    cost=cost or DEFAULT_COSTS
    cash=float(capital);units=0.;active=None;trades=[];curve=[];costs_total=0.;prev=0.;exposed=0
    def fee(price,quantity,side):
        return cost.cost(abs(price*quantity),side)
    def close_trade(price,ts,i,terminal=False):
        nonlocal cash,units,active,costs_total
        exit_fee=fee(price,units,'sell' if units>0 else 'buy')
        cash+=units*price-exit_fee;costs_total+=exit_fee
        pnl=units*(price-active['entry_price'])-active['entry_cost']-exit_fee
        trades.append({'entry':active['entry'],'exit':str(ts)[:10],
                       'entry_price':active['entry_price'],'exit_price':float(price),
                       'qty':abs(units),'side':'LONG' if units>0 else 'SHORT',
                       'entry_equity':active['entry_equity'],'exit_equity':cash,
                       'pnl':pnl,'pnl_pct':pnl/active['entry_equity']*100,
                       'costs':active['entry_cost']+exit_fee,'hold_days':i-active['bar'],
                       'terminal_liquidation':terminal})
        units=0.;active=None
    for i,(ts,row) in enumerate(prices.iterrows()):
        opening=float(row.Open);closing=float(row.Close);p=float(pos.iloc[i])
        if abs(p-prev)>1e-12:
            if active is not None:close_trade(opening,ts,i)
            if cash<=0:raise ValueError('portfolio insolvent; margin/gap loss exceeded equity')
            if p:
                # Solve notional + entry fee <= allocation budget. Conservative for shorts too.
                budget=cash*abs(p);lo=0.;hi=budget
                side='buy' if p>0 else 'sell' # overnight research uses delivery cost proxy
                for _ in range(60):
                    mid=(lo+hi)/2
                    if mid+cost.cost(mid,side)<=budget:lo=mid
                    else:hi=mid
                qty=lo/opening;units=qty if p>0 else -qty
                entry_fee=fee(opening,units,side)
                active={'entry':str(ts)[:10],'entry_price':opening,'entry_cost':entry_fee,
                        'entry_equity':cash,'bar':i}
                cash-=units*opening+entry_fee;costs_total+=entry_fee
            prev=p
        exposed+=int(active is not None)
        if i==len(prices)-1 and active is not None:close_trade(closing,ts,i,terminal=True)
        equity=cash+units*closing
        if not math.isfinite(equity) or equity<=0:raise ValueError('portfolio insolvent')
        curve.append(equity)
    eq=pd.Series(curve,index=df.index,name='equity')
    metrics=_metrics(eq,trades,costs_total,capital,exposed/len(df))
    return BacktestResult(eq,pos,trades,costs_total,metrics)

def buy_and_hold(df,capital=100000.,cost=None):
    """Benchmark buys at SECOND bar Open, same first eligible fill as lag-1 strategies."""
    return run_backtest(df,pd.Series(1.,index=df.index),capital=capital,cost=cost)

if __name__=='__main__':
    idx=pd.date_range('2026-01-01',periods=4)
    d=pd.DataFrame({'Open':[100,100,110,110],'Close':[100,110,110,110]},index=idx)
    r=run_backtest(d,pd.Series([1,0,0,0],index=idx))
    assert abs(r.equity.iloc[-1]-100000-sum(t['pnl'] for t in r.trades))<1e-7
    assert len(r.trades)==1 and r.costs_total>0
    print('next-open ledger reconciliation PASS')
