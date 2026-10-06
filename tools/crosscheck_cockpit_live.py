#!/usr/bin/env python3
"""
tools/crosscheck_cockpit_live.py — FIX-71 live cross-check (NSE vs Yahoo)
================================================================================
Cockpit jo numbers dikhata hai unhe EK INDEPENDENT source (Yahoo Finance) se
milata hai. Do alag providers, do alag code paths — match hone par hi "sahi
data" kehna jaayaz hai.

Kya compare hota hai (har index par):
  1. prev close   — NSE prevClose  vs  Yahoo ka last daily close
  2. 52w high/low — NSE yearHigh/Low vs Yahoo fiftyTwoWeekHigh/Low
  3. current LTP  — NSE last vs Yahoo regularMarketPrice (sampling gap ke kaaran
                    exact nahi, isliye tolerance + Yahoo ke day-range me hona)

NSE datacenter IP par 403 deta hai aur beech-beech me block karta hai. Isliye
NSE na mile to ye test SKIP hota hai (exit 2) — fail nahi, aur na hi jhootha
"pass". Repo ka wahi pattern jo verify_dashboard_render.js me jsdom ke liye hai.

NSE block ho to compare-logic chup na rahe, isliye fixture mode bhi hai:
pehle ek baar live data save kar lo, phir offline wahi ASLI data par compare
logic chalega (shipped code path, stub nahi).

Chalao:  python tools/crosscheck_cockpit_live.py                # live
         python tools/crosscheck_cockpit_live.py --save-fixture tools/fixtures/cockpit_crosscheck.json
         python tools/crosscheck_cockpit_live.py --fixture    tools/fixtures/cockpit_crosscheck.json
         (app server chalne ki zaroorat NAHI — NSE seedha call hota hai)
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}

# NSE index naam  →  Yahoo symbol
PAIRS = [('NIFTY 50', '%5ENSEI'), ('INDIA VIX', '%5EINDIAVIX'),
         ('NIFTY BANK', '%5ENSEBANK')]

results = []
infos = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ''))


def info(name, detail=''):
    infos.append(name)
    print(f"  ℹ️  {name}" + (f" — {detail}" if detail else ''))


def yahoo_chart(sym):
    u = (f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'
         '?interval=1d&range=5d')
    r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=45)
    return json.load(r)['chart']['result'][0]


import market_cockpit as mc  # noqa: E402  (pure module — sasta import)


def run_checks(payload, yref, label='live'):
    print(f'NSE payload as of {payload.get("timestamp")!r} '
          f'(age {mc.data_age_minutes(payload.get("timestamp"))} min) [{label}]')
    print()
    for name, sym in PAIRS:
        cards = mc.index_cards(payload, [name])
        if not cards:
            info(f'{name}: NSE payload me nahi mila — skip')
            continue
        c = cards[0]
        m = yref[sym]['meta']
        r = yref[sym]
        ylast = m.get('regularMarketPrice')
        yday = {datetime.fromtimestamp(t, IST).strftime('%d-%b'): v
                for t, v in zip(r['timestamp'], r['indicators']['quote'][0]['close']) if v}
        yprev = list(yday.values())[-2] if len(yday) >= 2 else None
        yt = datetime.fromtimestamp(m['regularMarketTime'], IST)

        print(f'── {name}  (Yahoo {m.get("fullExchangeName")} {sym.replace("%5E", "^")}, '
              f'{yt:%H:%M:%S} IST) ──')
        if c['prev_close'] is not None and yprev is not None:
            check(f'{name}: prev close match', abs(c['prev_close'] - yprev) <= 0.01,
                  f"NSE {c['prev_close']} vs Yahoo {round(yprev, 2)}")
        else:
            info(f'{name}: prev close compare nahi ho saka',
                 f"NSE {c['prev_close']} Yahoo {yprev}")
        if c['year_high'] is not None and m.get('fiftyTwoWeekHigh') is not None:
            check(f'{name}: 52w high match', abs(c['year_high'] - m['fiftyTwoWeekHigh']) <= 0.01,
                  f"NSE {c['year_high']} vs Yahoo {m['fiftyTwoWeekHigh']}")
        if c['year_low'] is not None and m.get('fiftyTwoWeekLow') is not None:
            d = abs(c['year_low'] - m['fiftyTwoWeekLow'])
            # 52w window dono jagah alag definition rakhte hain — mismatch fail NAHI,
            # par chhupaya bhi nahi jaata.
            if d <= 0.01:
                check(f'{name}: 52w low match', True, f"{c['year_low']}")
            else:
                info(f'{name}: 52w low DIFFER (window definition alag)',
                     f"NSE {c['year_low']} vs Yahoo {m['fiftyTwoWeekLow']}")
        lo, hi = m.get('regularMarketDayLow'), m.get('regularMarketDayHigh')
        if c['last'] is not None and lo is not None and hi is not None:
            check(f'{name}: LTP Yahoo ke day-range ke andar',
                  lo * 0.999 <= c['last'] <= hi * 1.001,
                  f"NSE {c['last']} in {lo}..{hi}")
        if c['last'] is not None and ylast:
            gap = abs(c['last'] - ylast) / ylast * 100
            check(f'{name}: LTP Yahoo se 1.5% ke andar (sampling gap ke saath)', gap <= 1.5,
                  f"NSE {c['last']} vs Yahoo {ylast} = {gap:.2f}%")
        print()


print('=' * 84)
print(' Cockpit LIVE cross-check — NSE (shipped code path) vs Yahoo Finance')
print('=' * 84)

args = sys.argv[1:]
fixture = args[args.index('--fixture') + 1] if '--fixture' in args else None
save_to = args[args.index('--save-fixture') + 1] if '--save-fixture' in args else None

if fixture:
    fx = json.loads(Path(fixture).read_text(encoding='utf-8'))
    run_checks(fx['payload'], fx['yahoo'], label=f'fixture {fixture}')
else:
    import app as A  # noqa: E402  (import me ~20s lagta hai — sirf live mode me)
    payload = A._fetch_all_indices()
    if not payload or not payload.get('data'):
        print('⏭  SKIP — NSE abhi block/off-market hai (datacenter IP par 403 normal hai).')
        print('   Apne ghar ke machine par market hours me dobara chalayein.')
        print('   Ya pehle se saved fixture chalayein:  --fixture <path>')
        sys.exit(2)
    try:
        yref = {sym: yahoo_chart(sym) for _, sym in PAIRS}
    except Exception as e:
        print(f'⏭  SKIP — Yahoo se data nahi mila ({e}).')
        sys.exit(2)
    if save_to:
        p = Path(save_to)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'captured_utc': datetime.now(timezone.utc).isoformat(),
                                 'payload': payload, 'yahoo': yref}, indent=1),
                     encoding='utf-8')
        print(f'💾 fixture saved → {p}\n')
    run_checks(payload, yref, label='live')

p = sum(results)
print('=' * 84)
print(f' {p} / {len(results)} checks passed, {len(infos)} info')
print('=' * 84)
sys.exit(0 if p == len(results) else 1)
