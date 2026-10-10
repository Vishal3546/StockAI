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
    exch_pct: float = 0.0000307            # NSE cash txn + IPFT 0.00307%, effective 2026-03-01
    # https://nsearchives.nseindia.com/content/circulars/FA73061.pdf
    sebi_pct: float = 0.000001             # ₹10 per crore

    # ── stamp duty (sirf buy side) ──
    stamp_buy: float = 0.00015             # 0.015% DELIVERY buy
    # FIX-59: intraday buy ka stamp alag hota hai — 0.003%. Pehle sirf delivery
    # rate tha, isliye intraday round-trip calculate hi nahi ho sakta tha.
    stamp_intraday_buy: float = 0.00003    # 0.003% INTRADAY buy

    gst_rate: float = 0.18                 # GST on (brokerage + exch + sebi)

    # ── market impact / slippage (configurable; 5 bps = conservative for Nifty50) ──
    slippage_bps: float = 5.0

    def breakdown(self, notional: float, side: str) -> dict:
        """Ek side ka poora cost breakdown (₹)."""
        if notional <= 0:
            return {k: 0.0 for k in ('brokerage', 'stt', 'exchange', 'sebi',
                                     'stamp', 'gst', 'slippage', 'total', 'total_pct')}
        brokerage = min(self.brokerage_pct * notional, self.brokerage_cap)
        if side == 'buy':                       # delivery buy
            stt, stamp = self.stt_buy * notional, self.stamp_buy * notional
        elif side == 'sell_short':              # intraday short sell
            stt, stamp = self.stt_intraday_sell * notional, 0.0
        elif side == 'buy_intraday':
            # FIX-59: intraday BUY par STT NIL hota hai (verified 2026 rates:
            # delivery 0.1% dono taraf, intraday 0.025% SIRF sell par).
            stt, stamp = 0.0, self.stamp_intraday_buy * notional
        elif side == 'sell_intraday':
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

    def round_trip_pct(self, intraday: bool = False,
                       notional: float = 1_000_000) -> float:
        """1 buy + 1 sell par % cost — yeh hai minimum hurdle.

        FIX-59: ab mode + notional aware. Do wajah se zaroori tha:
          • Delivery STT **dono taraf** 0.1% hai, intraday STT **sirf sell** par
            0.025% (buy leg nil) — pehle intraday ka round-trip banta hi nahi tha.
          • Brokerage ₹20 par CAPPED hai, isliye chhote notional par % cost zyada
            hota hai (₹1L par 0.02%/side, ₹25k par 0.03%/side). Flat % galat tha.
        Default args purane callers (run_study.py) ke liye same result dete hain.
        """
        b_side, s_side = (('buy_intraday', 'sell_intraday') if intraday
                          else ('buy', 'sell'))
        b = self.breakdown(notional, b_side)['total_pct']
        s = self.breakdown(notional, s_side)['total_pct']
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
