"""
research/backtest.py — vectorised long/flat backtester (cost-aware)
================================================================================
Design decisions (yeh sab honest measurement ke liye hain):

  • EXECUTION LAG: signal bar `t` ki close par banta hai, position bar `t+1` par
    lagti hai. `exec_lag=1` default hai — look-ahead ka sabse common source
    yahi hota hai, aur usko yahan explicitly model kiya gaya hai.
  • COSTS: har position change par `research.costs.CostConfig` ke hisaab se
    charge lagta hai (delivery STT + brokerage + stamp + GST + slippage).
  • COMPOUNDING: equity curve percent-of-equity par compound hoti hai, aur
    costs equity se hi kate jaate hain.
  • TRADES: round-trip P&L record hota hai (win-rate / profit-factor ke liye).

    python3 research/backtest.py      # invariants self-test
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .costs import CostConfig, DEFAULT as DEFAULT_COSTS

# FIX-60: 252 US convention hai. NSE par actual trading days MEASURE kiye
# (RELIANCE, yfinance 1240 bars): 2022=248, 2023=245, 2024=246, 2025=249
# -> average 247.0. 252 use karne se annualised return/vol +2.02% overstate
# hote the, aur usse Sharpe/Sortino bhi. Ab measured value.
TRADING_DAYS = 247
RF_ANNUAL = 0.065          # India ~6.5% risk-free (app.py CONFIG['RISK_FREE_RATE']
                           # aur deep_analyzer.py rf_rate se MATCH hona chahiye —
                           # tools/verify_live_quote.py guard karta hai, drift par fail)


@dataclass
class BacktestResult:
    equity: pd.Series
    positions: pd.Series
    trades: list = field(default_factory=list)
    costs_total: float = 0.0
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'metrics': self.metrics,
            'trades': self.trades,
            'costs_total': round(self.costs_total, 2),
            'equity_curve': [{'time': str(i)[:10], 'equity': round(float(v), 2)}
                             for i, v in self.equity.items()],
        }


def _metrics(equity: pd.Series, trades: list, costs_total: float,
             capital: float, exposure: float) -> dict:
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    n = len(rets)
    years = max(n / TRADING_DAYS, 1e-9)

    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)
    vol = float(rets.std() * np.sqrt(TRADING_DAYS))
    sharpe = float((cagr - RF_ANNUAL) / vol) if vol > 1e-9 else 0.0

    downside = float(rets[rets < 0].std() * np.sqrt(TRADING_DAYS))
    sortino = float((cagr - RF_ANNUAL) / downside) if downside > 1e-9 else 0.0

    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = float(dd.min())
    calmar = float(cagr / abs(max_dd)) if abs(max_dd) > 1e-9 else 0.0

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gross_win = sum(t['pnl'] for t in wins)
    gross_loss = abs(sum(t['pnl'] for t in losses))

    return {
        'total_return_pct': round(total_ret * 100, 2),
        'cagr_pct': round(cagr * 100, 2),
        'vol_pct': round(vol * 100, 2),
        'sharpe': round(sharpe, 2),
        'sortino': round(sortino, 2),
        'max_drawdown_pct': round(max_dd * 100, 2),
        'calmar': round(calmar, 2),
        'trades': len(trades),
        'win_rate_pct': round(len(wins) / len(trades) * 100, 1) if trades else None,
        'avg_win': round(float(np.mean([t['pnl'] for t in wins])), 2) if wins else 0.0,
        'avg_loss': round(float(np.mean([t['pnl'] for t in losses])), 2) if losses else 0.0,
        'profit_factor': round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        'avg_hold_days': round(float(np.mean([t['hold_days'] for t in trades])), 1) if trades else None,
        'exposure_pct': round(exposure * 100, 1),
        'costs_paid': round(costs_total, 2),
        'cost_drag_pct': round(costs_total / capital * 100, 2),
    }


def run_backtest(df: pd.DataFrame, target_pos: pd.Series, capital: float = 100_000.0,
                 cost: Optional[CostConfig] = None, exec_lag: int = 1,
                 allow_short: bool = False, direction: int = 1) -> BacktestResult:
    """
    `target_pos` — 0/1 (ya -1/1 agar allow_short) ka series, BAR `t` ki close
    tak ki information se bana hua. Position bar `t+exec_lag` par effective hoti hai.
    `direction` — -1 kar do to long signal ko short bana dete hain (mirror test ke liye).
    """
    cost = cost or DEFAULT_COSTS
    px = df['Close'].astype(float)
    rets = px.pct_change().fillna(0.0)

    pos = target_pos.reindex(px.index).fillna(0.0).astype(float)
    if direction < 0:
        pos = -pos
    if not allow_short:
        pos = pos.clip(lower=0.0)
    pos = pos.shift(exec_lag).fillna(0.0)          # ← look-ahead guard

    equity = capital
    curve, prev_pos = [], 0.0
    trades, costs_total = [], 0.0
    open_trade = None
    bars_in_market = 0

    for i, (ts, r) in enumerate(rets.items()):
        p = float(pos.iloc[i])

        # 1) mark-to-market (bar ka return, position pehle se li hui hai)
        equity *= (1.0 + p * r)

        # 2) position change par costs + trade bookkeeping
        if abs(p - prev_pos) > 1e-12:
            traded_notional = equity * abs(p - prev_pos)
            side = 'buy' if p > prev_pos else ('sell_short' if p < 0 else 'sell')
            c = cost.cost(traded_notional, side) if traded_notional > 0 else 0.0
            equity -= c
            costs_total += c

            if prev_pos != 0 and open_trade is not None:      # close
                open_trade.update({
                    'exit': str(ts)[:10],
                    'exit_equity': round(equity, 2),
                    'pnl': round(equity - open_trade['entry_equity'], 2),
                    'pnl_pct': round((equity / open_trade['entry_equity'] - 1) * 100, 2),
                    'costs': round(open_trade['costs'] + c, 2),
                    'hold_days': i - open_trade.pop('entry_bar', i),
                })
                trades.append(open_trade)
                open_trade = None
            if p != 0:                                        # open
                open_trade = {'entry': str(ts)[:10], 'entry_equity': round(equity, 2),
                              'side': 'LONG' if p > 0 else 'SHORT', 'costs': round(c, 2),
                              'entry_bar': i}
            prev_pos = p

        if abs(p) > 1e-12:
            bars_in_market += 1
        curve.append(equity)

    if open_trade is not None:                                # aakhir me open trade band karo
        open_trade.update({'exit': str(px.index[-1])[:10], 'exit_equity': round(equity, 2),
                           'pnl': round(equity - open_trade['entry_equity'], 2),
                           'pnl_pct': round((equity / open_trade['entry_equity'] - 1) * 100, 2),
                           'hold_days': len(px) - 1 - open_trade.pop('entry_bar', 0)})
        trades.append(open_trade)

    eq = pd.Series(curve, index=px.index, name='equity')
    exposure = bars_in_market / max(len(px), 1)
    return BacktestResult(equity=eq, positions=pos, trades=trades,
                          costs_total=costs_total,
                          metrics=_metrics(eq, trades, costs_total, capital, exposure))


def buy_and_hold(df: pd.DataFrame, capital: float = 100_000.0,
                 cost: Optional[CostConfig] = None) -> BacktestResult:
    """Benchmark: pehle bar par buy, last bar par sell (costs ke saath)."""
    pos = pd.Series(0.0, index=df.index)
    pos.iloc[1:] = 1.0
    return run_backtest(df, pos, capital=capital, cost=cost, exec_lag=1)


# ═══════════════════════════════════════════════════════════════════════════
#  SELF-TEST — invariants jo backtester me tootna aasan hota hai
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    from .costs import CostConfig

    rng = np.random.default_rng(7)
    n = 300
    idx = pd.date_range('2024-01-01', periods=n, freq='B')
    px = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n))), index=idx)
    df = pd.DataFrame({'Open': px, 'High': px * 1.01, 'Low': px * 0.99,
                       'Close': px, 'Volume': 1_000_000}, index=idx)

    print("=" * 78); print(" BACKTEST INVARIANTS"); print("=" * 78)

    # 1) buy&hold with ZERO costs == price return
    free = CostConfig(brokerage_pct=0, brokerage_cap=0, stt_buy=0, stt_sell=0,
                      stt_intraday_sell=0, exch_pct=0, sebi_pct=0, stamp_buy=0,
                      gst_rate=0, slippage_bps=0)
    bh = buy_and_hold(df, cost=free)
    price_ret = px.iloc[-1] / px.iloc[1] - 1
    eq_ret = bh.equity.iloc[-1] / 100_000 - 1
    ok1 = abs(price_ret - eq_ret) < 1e-9
    print(f"  1) zero-cost buy&hold == price return : {price_ret:.6f} vs {eq_ret:.6f}  → {'PASS' if ok1 else 'FAIL'}")

    # 2) no-look-ahead: future price change must not move the past equity
    sig = (px > px.rolling(20).mean()).astype(float)
    a = run_backtest(df, sig, cost=free)
    df2 = df.copy(); df2.iloc[-1, df2.columns.get_loc('Close')] *= 1.10
    b = run_backtest(df2, sig, cost=free)
    same = np.allclose(a.equity.values[:-1], b.equity.values[:-1])
    print(f"  2) last bar ka future change → past equity unchanged : {'PASS' if same else 'FAIL'}")

    # 3) costs kamai khaate hain
    with_costs = run_backtest(df, sig, cost=CostConfig())
    print(f"  3) costs reduce equity : {a.equity.iloc[-1]:,.0f} (free) → "
          f"{with_costs.equity.iloc[-1]:,.0f} (with costs), drag ₹{with_costs.costs_total:,.0f}"
          f"  → {'PASS' if with_costs.costs_total > 0 and with_costs.equity.iloc[-1] < a.equity.iloc[-1] else 'FAIL'}")

    # 4) exec_lag=0 vs 1 differ (lag actually lag raha hai)
    lag0 = run_backtest(df, sig, cost=free, exec_lag=0)
    print(f"  4) exec_lag 0 vs 1 differ : {lag0.equity.iloc[-1]:,.0f} vs {a.equity.iloc[-1]:,.0f}"
          f"  → {'PASS' if not np.isclose(lag0.equity.iloc[-1], a.equity.iloc[-1]) else 'FAIL'}")

    # 5) metrics sanity
    m = with_costs.metrics
    print(f"  5) metrics computed : trades={m['trades']} win_rate={m['win_rate_pct']}% "
          f"maxDD={m['max_drawdown_pct']}% sharpe={m['sharpe']} exposure={m['exposure_pct']}%"
          f"  → {'PASS' if m['trades'] > 0 else 'FAIL'}")
