#!/usr/bin/env python3
"""
FIX-30 verification — risk plan apna win-rate assumption disclose karta hai.

Problem: calculate_risk() me win_rate = 0.62 / 0.55 / 0.45 ASSUMED constants the
(score-bucket heuristic). Wahi win_rate Kelly sizing me jaata hai
(win_rate -> kelly -> qty -> risk_amount), to dashboard par "Position Size (Kelly)"
aur "Risk at stop" ek verified number jaisa lagta tha.

Fix: mechanics bilkul wahi rakhe (SL/targets/qty caps me koi change nahi), par
ab har risk plan ye batata hai:
  - win_rate_used        : jo number actually use hua
  - win_rate_basis       : 'assumed (score-bucket heuristic)'
  - win_rate_measured    : model ki measured accuracy (agar available ho)
  - edge_verified        : True sirf tab jab measured accuracy > 52%
  - risk_note            : human-readable disclosure (UI par dikhta hai)

Run:  python3 tools/verify_risk_basis.py
"""
import inspect
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as A  # noqa: E402

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  → {detail}" if detail else ""))


print("=" * 84)
print(" FIX-30 — risk plan win-rate basis verification")
print("=" * 84)

print("\n[1] signature: measured_accuracy accept karta hai")
sig = inspect.signature(A.calculate_risk)
check("measured_accuracy param maujood", "measured_accuracy" in sig.parameters)

print("\n[2] disclosure fields (measured accuracy ke bina)")
r = A.calculate_risk(100.0, 2.0, 80, action="BUY")
for f in ("win_rate_used", "win_rate_basis", "win_rate_measured", "edge_verified", "risk_note"):
    check(f"field: {f}", f in r)
check("win_rate_used = 0.62 (score>=78 bucket)", r["win_rate_used"] == 0.62, str(r["win_rate_used"]))
check("basis 'assumed' likhta hai", "assumed" in r["win_rate_basis"])
check("measured None (koi data nahi)", r["win_rate_measured"] is None)
check("edge_verified False", r["edge_verified"] is False)
check("note me 'ASSUMED' shabd", "ASSUMED" in r["risk_note"])

print("\n[3] measured accuracy ke saath (asli ML value jaisa 51.7)")
r = A.calculate_risk(100.0, 2.0, 80, action="BUY", measured_accuracy=51.7)
check("win_rate_measured = 0.517", r["win_rate_measured"] == 0.517, str(r["win_rate_measured"]))
check("edge_verified False (51.7 < 52)", r["edge_verified"] is False)
check("note me measured 51.7% dikhta hai", "51.7%" in r["risk_note"])
check("note me 'verified edge NAHI mila'", "verified edge NAHI mila" in r["risk_note"])
check("note me gap (+10.3pp)", "+10.3pp" in r["risk_note"], r["risk_note"][:80])

print("\n[4] fraction input (0.60) + edge verified case")
r = A.calculate_risk(100.0, 2.0, 80, action="BUY", measured_accuracy=0.60)
check("0.60 fraction → 0.6", r["win_rate_measured"] == 0.6, str(r["win_rate_measured"]))
check("edge_verified True (60 > 52, WF nahi diya)", r["edge_verified"] is True)

print("\n[4b] CONSERVATIVE rule — achhi ensemble accuracy par bharosa nahi")
# RELIANCE live jaisa case: ensemble 53.9 (>=52) par walk-forward 49.4 < baseline 53.3
r = A.calculate_risk(100.0, 2.0, 40, action="SHORT_SELL",
                     measured_accuracy=53.9, measured_wf_accuracy=49.4, measured_baseline=53.3)
check("edge_verified FALSE (WF baseline se neeche)", r["edge_verified"] is False)
check("note me walk-forward number dikhta hai", "walk-forward 49.4%" in r["risk_note"])
check("note me baseline dikhta hai", "baseline 53.3%" in r["risk_note"])
check("note me 'verified edge NAHI mila'", "verified edge NAHI mila" in r["risk_note"])
check("fields: accuracy_walk_forward", r["accuracy_walk_forward"] == 49.4)
check("fields: accuracy_baseline", r["accuracy_baseline"] == 53.3)

print("\n[4c] dono numbers achhe → verified")
r = A.calculate_risk(100.0, 2.0, 80, action="BUY",
                     measured_accuracy=54.5, measured_wf_accuracy=55.2, measured_baseline=52.0)
check("edge_verified True (ensemble>52 aur WF>=baseline)", r["edge_verified"] is True)
check("note me 'verified edge mila hai'", "verified edge mila hai" in r["risk_note"])

print("\n[5] junk input crash nahi karta")
try:
    r = A.calculate_risk(100.0, 2.0, 80, action="BUY", measured_accuracy="abc")
    check("junk string → safely None", r["win_rate_measured"] is None and r["edge_verified"] is False)
except Exception as e:
    check("junk string → safely None", False, f"{type(e).__name__}: {e}")

print("\n[6] MATH REGRESSION — sizing bilkul pehle jaisi (mechanics unchanged)")
# independent formula: b = 2.5 / sl_mult ; kelly = (p*b - (1-p))/b, cap MAX_KELLY_PCT
C = A.CONFIG["MAX_KELLY_PCT"]
CAP = A.CONFIG["DEFAULT_CAPITAL"]
for score, exp_wr, exp_sl_mult in [(80, 0.62, 1.5), (65, 0.55, 2.0), (40, 0.45, 2.5)]:
    r = A.calculate_risk(100.0, 2.0, score, action="BUY")
    b = 2.5 / exp_sl_mult
    kelly = max(0.0, min((exp_wr * b - (1 - exp_wr)) / b, C))
    risk_ps = 2.0 * exp_sl_mult
    qty = max(0, min(int(CAP * kelly / risk_ps), int(CAP / 100.0)))
    check(f"score {score}: kelly {round(kelly*100,1)}%",
          abs(r["kelly_pct"] - round(kelly * 100, 1)) < 0.01, f'got {r["kelly_pct"]}')
    check(f"score {score}: rr 1:{round(b,2)}", abs(r["rr_ratio"] - round(b, 2)) < 0.001)
    check(f"score {score}: qty {qty}", r["qty"] == qty, f'got {r["qty"]}')
    check(f"score {score}: risk_amount {round(qty*risk_ps)}", r["risk_amount"] == round(qty * risk_ps))

print("\n[7] source-level: fix sach me laga hai")
src = (ROOT / "app.py").read_text(encoding='utf-8')
check("call site measured_accuracy bhejta hai", "measured_accuracy=_ml_acc" in src)
check("call site walk-forward bhi bhejta hai", "measured_wf_accuracy=_ml_wf" in src)
check("call site baseline bhi bhejta hai", "measured_baseline=_ml_base" in src)
check("purani chup-chaap call line gayi",
      "risk = calculate_risk(price, atr, ens['score'], action=ens['action'])\n" not in src)
dash = (ROOT / "Dashboard.html").read_text(encoding='utf-8')
check("Dashboard: win_rate_used check karta hai", "rk.win_rate_used" in dash)
check("Dashboard: risk_note dikhata hai", "rk.risk_note" in dash)

passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print("\n" + "=" * 84)
print(f" RESULT: {passed} passed, {failed} failed")
print("=" * 84)
sys.exit(0 if failed == 0 else 1)
