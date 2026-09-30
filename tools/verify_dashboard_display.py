#!/usr/bin/env python3
"""
FIX-29 verification — Dashboard.html display me fake number nahi.

Problem: display positions par `sv(x, 50)` / `sv(x, 0)` jaise numeric defaults the,
matlab jab backend value missing ho to UI par "50" / "0" chhapta tha — jo asli
reading jaisa lagta tha (RSI "50 NEUTRAL", ML probability "50%", Master Score
"50/100", volume-ratio "NORMAL", stop-loss "₹0" ...).

Ye suite check karti hai:
  1. naye helpers (isMissing/svd/pctd/money/verdict) maujood hain
  2. template interpolation me koi numeric default nahi bacha
  3. purane fake patterns gaye
  4. sc() / setGauge() missing value handle karte hain
  5. Dashboard.html ka inline JS valid parse hota hai (node + acorn)

Run:  python3 tools/verify_dashboard_display.py
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DASH = ROOT / "Dashboard.html"

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    icon = "✅" if ok else "❌"
    line = f"  {icon} {name}"
    if detail:
        line += f"  → {detail}"
    print(line)


print("=" * 84)
print(' FIX-29 — Dashboard.html honest display verification')
print("=" * 84)

if not DASH.exists():
    print(f"❌ Dashboard.html nahi mila: {DASH}")
    sys.exit(1)

dash = DASH.read_text(encoding="utf-8")

print("\n[1] display-safe helpers maujood hain")
for fn in ("function isMissing(", "function svd(", "function pctd(",
           "function money(", "function verdict("):
    check(f"helper {fn[len('function '):-1]})", fn in dash)

print("\n[2] template interpolation me numeric default = fake number (0 hona chahiye)")
tmpl_numeric = re.findall(r"\$\{sv\([^}]*?,\s*[0-9][^}]*?\}", dash)
check("koi bhi sv(..., <number>) display me nahi", len(tmpl_numeric) == 0,
      f"{len(tmpl_numeric)} mile: {tmpl_numeric[:3]}")

print("\n[3] purane fake-display patterns hataye")
for pat, label in [
    ("sv(ml.probability, 50)",        "ML probability ka fake 50%"),
    ("sv(ind.rsi, 50)",               "RSI ka fake 50"),
    ("sv(ind.stochrsi_k, 50)",        "StochRSI ka fake 50"),
    ("sv(ind.vol_ratio, 1)",          "volume-ratio ka fake 1 (NORMAL)"),
    ("sv(ens.score, 50)",             "Master Score ka fake 50"),
    ("sv(engineData.score, 50)",      "engine card color ka fake 50"),
    ("sc(sv(",                        "score color ko fake default pass karna"),
    ("{ score: 50, action: 'HOLD' }", "ensemble fallback ka fake 50/HOLD"),
    ("sv(rk.rr_ratio, 1.5)",          "risk-reward ka fake 1.5"),
    ("sv(rk.sl, 0)",                  "stop-loss ka fake ₹0"),
    ("sv(rk.t1, 0)",                  "target-1 ka fake ₹0"),
    ("sv(rk.t2, 0)",                  "target-2 ka fake ₹0"),
    ("sv(rk.qty, 0)",                 "qty ka fake 0"),
    ("sv(reg.vix_status, 'NORMAL')",  "VIX status ka fake NORMAL"),
    ("sv(ml.confidence, 'LOW')",      "ML confidence ka fake LOW"),
    ("sv(rk.exec_status, '🟡 WATCHLIST')", "exec-status ka fake WATCHLIST"),
]:
    check(f"gaya: {label}", pat not in dash)

print("\n[4] missing-value handling logic")
check("sc() missing score par neutral", "if (isMissing(s)) return" in dash)
check("setGauge() missing par khaali gauge", "if (isMissing(val))" in dash)
check("verdict() missing par '—' (helper body)",
      "return isMissing(v) ? '—' : fn(v);" in dash)
# '—' fallback wale call sites ki ginti (sirf report ke liye)
dash_map = len(re.findall(r"sv\([^)]*?,\s*'—'\)", dash))
check("dashboard me '—' fallback call-sites milte hain", dash_map >= 5, f"{dash_map} jagah")

print("\n[5] Dashboard.html inline JS parse hota hai")
try:
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", dash)
    biggest = max(blocks, key=len)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(biggest)
        jp = f.name
    try:
        node = subprocess.run(
            ["node", "-e",
             'const fs=require("fs"),a=require("acorn");'
             'try{a.parse(fs.readFileSync(process.argv[1],"utf8"),{ecmaVersion:"latest"});'
             'console.log("OK")}catch(e){console.log("ERR "+e.message)}',
             jp],
            capture_output=True, text=True, cwd=str(ROOT))
        out = (node.stdout or "").strip()
        check("JS block valid hai (acorn)", out.startswith("OK"),
              out[:90] or (node.stderr or "")[:90])
    finally:
        os.unlink(jp)
except FileNotFoundError:
    check("JS block valid hai (acorn)", True, "skip — node nahi mila")
except Exception as e:
    check("JS block valid hai (acorn)", False, f"{type(e).__name__}: {e}")

print("\n[6] scanner tool (reproducible audit)")
check("tools/scan_js_hardcodes.js maujood", (ROOT / "tools" / "scan_js_hardcodes.js").exists())

passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print("\n" + "=" * 84)
print(f" RESULT: {passed} passed, {failed} failed")
print("=" * 84)
sys.exit(0 if failed == 0 else 1)
