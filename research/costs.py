"""
research/costs.py — Indian equity transaction-cost model
================================================================================
Ek honest backtest ke liye sabse pehle costs chahiye. NSE equity **delivery**
par round-trip cost roughly 0.3% + slippage hota hai — yaani har trade ko
break-even ke liye ~0.4% ka edge chahiye. Yahi number decide karta hai ki koi
signal tradeable hai ya nahi.

Sab rates configurable hain (defaults Sep-2026 NSE retail delivery ke hisaab se).

    python3 research/costs.py        # self-test: hand-calculated breakdown
"""
from dataclasses import dataclass, asdict


@dataclass
class CostConfig:
    # ── brokerage (flat ₹20 per order ya 0.03%, jo kam ho) ──
    brokerage_pct: float = 0.0003          # 0.03%
    brokerage_cap: float = 20.0            # ₹20 per order

    # ── Securities Transaction Tax ──
    stt_buy: float = 0.001                 # 0.10% delivery BUY
    stt_sell: float = 0.001                # 0.10% delivery SELL
    stt_intraday_sell: float = 0.00025     # 0.025% jab short intraday ho

    # ── exchange + regulator ──
    exch_pct: float = 0.0000325            # NSE transaction charge 0.00325%
    sebi_pct: float = 0.000001             # ₹10 per crore

    # ── stamp duty (sirf buy side, delivery) ──
    stamp_buy: float = 0.00015             # 0.015%

    gst_rate: float = 0.18                 # GST on (brokerage + exch + sebi)

    # ── market impact / slippage (configurable; 5 bps = conservative for Nifty50) ──
    slippage_bps: float = 5.0

    def breakdown(self, notional: float, side: str) -> dict:
        """Ek side ka poora cost breakdown (₹)."""
        if notional <= 0:
            return {k: 0.0 for k in ('brokerage', 'stt', 'exchange', 'sebi',
                                     'stamp', 'gst', 'slippage', 'total', 'total_pct')}
        brokerage = min(self.brokerage_pct * notional, self.brokerage_cap)
        if side == 'buy':
            stt, stamp = self.stt_buy * notional, self.stamp_buy * notional
        elif side == 'sell_short':
            stt, stamp = self.stt_intraday_sell * notional, 0.0
        else:  # 'sell' (delivery)
            stt, stamp = self.stt_sell * notional, 0.0
        exchange = self.exch_pct * notional
        sebi = self.sebi_pct * notional
        gst = self.gst_rate * (brokerage + exchange + sebi)
        slippage = self.slippage_bps / 10000.0 * notional
        total = brokerage + stt + exchange + sebi + stamp + gst + slippage
        return {
            'brokerage': round(brokerage, 2), 'stt': round(stt, 2),
            'exchange': round(exchange, 2), 'sebi': round(sebi, 2),
            'stamp': round(stamp, 2), 'gst': round(gst, 2),
            'slippage': round(slippage, 2), 'total': round(total, 2),
            'total_pct': round(total / notional * 100, 4),
        }

    def cost(self, notional: float, side: str) -> float:
        return self.breakdown(notional, side)['total']

    def round_trip_pct(self) -> float:
        """1 buy + 1 sell par % cost — yeh hai minimum hurdle."""
        b = self.breakdown(1_000_000, 'buy')['total_pct']
        s = self.breakdown(1_000_000, 'sell')['total_pct']
        return round(b + s, 4)

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT = CostConfig()


if __name__ == '__main__':
    cfg = CostConfig()
    N = 100_000
    print(f"{'='*78}\n NSE DELIVERY COST MODEL — self test on ₹{N:,} notional\n{'='*78}")
    for side in ('buy', 'sell'):
        bd = cfg.breakdown(N, side)
        print(f"\n  {side.upper()}")
        for k, v in bd.items():
            if k == 'total_pct':
                print(f"    {'TOTAL %':<12} {v:>10.4f}%")
            else:
                print(f"    {k:<12} ₹{v:>10,.2f}")
    rt = cfg.round_trip_pct()
    print(f"\n  ROUND TRIP (buy + sell) = {rt:.4f}%  →  ₹{N * rt / 100:,.2f} per ₹{N:,} trade")
    print(f"  Break-even edge needed per round trip ≈ {rt:.2f}% (+ market impact beyond slippage)")
    print(f"\n  🎯 Isliye: ek strategy ko daily trade karke sirf {rt:.2f}% se zyada ka")
    print(f"     per-trade edge chahiye — warna wo costs me hi mar jaati hai.\n")
