#!/usr/bin/env python3
"""
FIX-35 verification — security hardening (M-11 CORS/auth/rate-limit, M-12 XSS sinks).

Pehle:
  • app.py me `CORS(app)` → har response par `Access-Control-Allow-Origin: *`
  • koi auth nahi, koi rate limit nahi → port internet par gaya to poora API khula
  • Dashboard.html me 14 `innerHTML` sinks; search dropdown external Yahoo
    `longname`/`symbol` ko seedha HTML me daalta tha (script inject ho sakta tha)

Ab:
  • CORS sirf explicit allowlist (default same-origin), security headers + CSP
  • optional token auth (`STOCKAI_API_TOKEN`) `/` aur `/api/*` par
  • per-IP sliding-window rate limit (429 + Retry-After), SSE exempt
  • dashboard me safeHtml`` tagged template + DOM-API search list (no inline onclick)

Run:  python3 tools/verify_security.py
      node tools/verify_xss_render.js      (jsdom — actual injection attempt)
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as A  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  → {detail}" if detail else ""))


print("=" * 84)
print(" FIX-35 — security hardening (CORS allowlist · token auth · rate limit · XSS)")
print("=" * 84)

# ── [1] default policy (koi env set nahi) ────────────────────────────────
print("\n[1] default policy — back-compat + security headers")
A.configure_security(token='', cors_origins=[], rate_limit_per_min=0)
A.reset_rate_limiter()
c = A.app.test_client()

r = c.get('/api/search?q=REL')
check("token set na ho to API khula rehta hai (back-compat)", r.status_code == 200, f"HTTP {r.status_code}")
check("wildcard CORS header NAHI aata",
      r.headers.get('Access-Control-Allow-Origin') != '*',
      repr(r.headers.get('Access-Control-Allow-Origin')))
check("X-Content-Type-Options: nosniff", r.headers.get('X-Content-Type-Options') == 'nosniff')
check("X-Frame-Options: DENY (clickjacking)", r.headers.get('X-Frame-Options') == 'DENY')
check("Referrer-Policy set", bool(r.headers.get('Referrer-Policy')), r.headers.get('Referrer-Policy'))
csp = r.headers.get('Content-Security-Policy', '')
check("CSP: frame-ancestors 'none'", "frame-ancestors 'none'" in csp)
check("CSP: object-src 'none'", "object-src 'none'" in csp)
check("CSP: script-src wildcard-free (koi * nahi)", '*' not in csp, csp[:60] + '…')
check("CSP: connect-src 'self' (dashboard sirf same-origin API call karta hai)", "connect-src 'self'" in csp)

# ── [2] CORS allowlist ───────────────────────────────────────────────────
print("\n[2] CORS — sirf allowlisted origin")
r = c.get('/api/search?q=REL', headers={'Origin': 'https://evil.example'})
check("unlisted Origin ko ACAO nahi milta",
      'Access-Control-Allow-Origin' not in r.headers, r.headers.get('Access-Control-Allow-Origin'))

A.configure_security(cors_origins=['https://ok.example'])
r = c.get('/api/search?q=REL', headers={'Origin': 'https://ok.example'})
check("allowlisted Origin ko exact origin echo hota hai",
      r.headers.get('Access-Control-Allow-Origin') == 'https://ok.example',
      r.headers.get('Access-Control-Allow-Origin'))
check("Vary: Origin (cache poisoning se bachao)", 'Origin' in (r.headers.get('Vary') or ''), r.headers.get('Vary'))
check("Allow-Headers me X-Api-Key", 'X-Api-Key' in (r.headers.get('Access-Control-Allow-Headers') or ''))
r = c.get('/api/search?q=REL', headers={'Origin': 'https://ok.example.attacker.com'})
check("prefix-spoof origin (ok.example.attacker.com) reject",
      'Access-Control-Allow-Origin' not in r.headers)
r = c.get('/api/search?q=REL', headers={'Origin': 'https://ok.example/'})
check("trailing-slash origin bhi match hota hai (normalize)",
      r.headers.get('Access-Control-Allow-Origin') == 'https://ok.example')
A.configure_security(cors_origins=[])

# ── [3] token auth ───────────────────────────────────────────────────────
print("\n[3] token auth (STOCKAI_API_TOKEN)")
A.configure_security(token='s3cret-token')
r = c.get('/api/search?q=REL')
check("token set ho to bina token 401", r.status_code == 401, f"HTTP {r.status_code}")
check("401 body me reason (silent fail nahi)", 'unauthorized' in r.get_data(as_text=True).lower())
r = c.get('/', )
check("dashboard `/` bhi protected hai", r.status_code == 401, f"HTTP {r.status_code}")
r = c.get('/api/search?q=REL', headers={'X-Api-Key': 's3cret-token'})
check("X-Api-Key header se 200", r.status_code == 200, f"HTTP {r.status_code}")
r = c.get('/api/search?q=REL', headers={'X-Api-Key': 'galat-token'})
check("galat token 401", r.status_code == 401, f"HTTP {r.status_code}")
# NOTE: fresh client — upar wale X-Api-Key request ne already cookie store kar di
# thi (test client cookies yaad rakhta hai), isliye yahan naya client.
cq = A.app.test_client()
r = cq.get('/api/search?q=REL&token=s3cret-token')
check("?token= query se 200", r.status_code == 200, f"HTTP {r.status_code}")
check("?token= par cookie set hoti hai (dashboard ke fetch chalenge)",
      'stockai_token=s3cret-token' in (r.headers.get('Set-Cookie') or ''), r.headers.get('Set-Cookie'))
check("cookie already set ho to dobara Set-Cookie nahi (har request par nahi)",
      'Set-Cookie' not in cq.get('/api/search?q=REL', headers={'X-Api-Key': 's3cret-token'}).headers)
check("cookie HttpOnly", 'HttpOnly' in (r.headers.get('Set-Cookie') or ''))
check("cookie SameSite=Lax", 'SameSite=Lax' in (r.headers.get('Set-Cookie') or ''))
c2 = A.app.test_client()
c2.set_cookie('stockai_token', 's3cret-token')
r = c2.get('/api/quote/RELIANCE')
check("cookie se subsequent request 200", r.status_code == 200, f"HTTP {r.status_code}")
r = c.get('/static/lightweight-charts.standalone.production.js')
check("static asset public rehta hai (token zaroori nahi)", r.status_code == 200, f"HTTP {r.status_code}")
A.configure_security(token='')
r = c.get('/api/search?q=REL')
check("token hataane ke baad wapas open", r.status_code == 200, f"HTTP {r.status_code}")

# ── [4] rate limit ───────────────────────────────────────────────────────
print("\n[4] per-IP rate limit")
A.configure_security(rate_limit_per_min=3)
codes = [c.get('/api/search?q=REL').status_code for _ in range(5)]
check("limit se pehle 200", codes[:3] == [200, 200, 200], str(codes))
check("limit ke baad 429", codes[3] == 429, str(codes))
r = c.get('/api/search?q=REL')
check("429 body me error", 'rate_limited' in r.get_data(as_text=True), r.get_data(as_text=True)[:80])
check("429 par Retry-After header", bool(r.headers.get('Retry-After')), r.headers.get('Retry-After'))
check("Retry-After numeric seconds", str(r.headers.get('Retry-After') or '').isdigit())
A.reset_rate_limiter()
check("reset ke baad 200 wapas", c.get('/api/search?q=REL').status_code == 200)

A.configure_security(rate_limit_per_min=2, trust_proxy=True)
A.reset_rate_limiter()
a_codes = [c.get('/api/search?q=REL', headers={'X-Forwarded-For': '10.0.0.1'}).status_code for _ in range(3)]
b_codes = [c.get('/api/search?q=REL', headers={'X-Forwarded-For': '10.0.0.2'}).status_code for _ in range(2)]
check("TRUST_PROXY=1 par alag XFF alag bucket rakhte hain",
      a_codes == [200, 200, 429] and b_codes == [200, 200], f"A={a_codes} B={b_codes}")

A.configure_security(rate_limit_per_min=2, trust_proxy=False)
A.reset_rate_limiter()
x_codes = [c.get('/api/search?q=REL', headers={'X-Forwarded-For': f'10.9.9.{i}'}).status_code for i in range(3)]
check("TRUST_PROXY=0 par spoofed XFF ignore (ek hi bucket)",
      x_codes == [200, 200, 429], str(x_codes))

A.configure_security(rate_limit_per_min=1)
A.reset_rate_limiter()
c.get('/api/search?q=REL')
r = c.get('/api/search?q=REL')
check("limit hit hone par bhi 429 JSON hai (HTML error page nahi)",
      r.status_code == 429 and r.mimetype == 'application/json', r.mimetype)

# SSE exempt
A.configure_security(rate_limit_per_min=1)
A.reset_rate_limiter()
src = (ROOT / 'app.py').read_text(encoding='utf-8')
check("SSE /api/stream limiter se exempt (long-lived connection)",
      "not path.startswith('/api/stream')" in src)

A.configure_security(rate_limit_per_min=0)
A.reset_rate_limiter()
codes = [c.get('/api/search?q=REL').status_code for _ in range(5)]
check("rate_limit=0 → limiter off", codes == [200] * 5, str(codes))

# ── [5] Dashboard source — XSS sinks ─────────────────────────────────────
print("\n[5] Dashboard.html — injection sinks")
dash = (ROOT / 'Dashboard.html').read_text(encoding='utf-8')
check("escHtml() defined", 'function escHtml(' in dash)
check("safeHtml`` tagged template defined", 'function safeHtml(' in dash)
check("raw() marker defined (nested markup ke liye)", 'function raw(' in dash)
check("escHtml 5 dangerous chars escape karta hai",
      all(x in dash for x in ["'&': '&amp;'", "'<': '&lt;'", "'>': '&gt;'", "'\"': '&quot;'", "'&#39;'"]))
check("search dropdown me inline onclick NAHI (quote-break sink gaya)",
      "onclick=\"selectStock('" not in dash)
check("search list DOM API + textContent se banti hai",
      "item.dataset.sym" in dash and 'symEl.textContent' in dash)
check("search click delegation use hota hai",
      "dropdown.addEventListener('click'" in dash and 'dataset.sym' in dash)

# har innerHTML assignment: ya to constant string, ya safeHtml-built accumulator
sinks = re.findall(r"(\w+(?:\.\w+)*)\.innerHTML\s*=\s*([^;\n]+)", dash)
allowed_accumulators = {'mlh', 'techHtmlMarkup', 'patternsHtmlMarkup', 'fundHtmlMarkup',
                        'mtfHtmlMarkup', 'finalVerdictHtml'}
bad = []
for target, expr in sinks:
    expr = expr.strip()
    if expr.startswith('safeHtml`'):
        continue
    if expr.startswith(("'", '"')):          # constant markup, koi interpolation nahi
        continue
    if expr in allowed_accumulators:
        continue
    bad.append(f'{target} = {expr[:40]}')
check("koi unescaped innerHTML sink nahi bacha", not bad, '; '.join(bad[:3]))

# accumulators khud safeHtml se banne chahiye
for acc in sorted(allowed_accumulators):
    if acc in dash:
        ok = re.search(rf"{acc}\s*\+?=\s*(raw\()?safeHtml`", dash) is not None
        check(f"accumulator `{acc}` safeHtml se banta hai", ok)

# nested markup raw() se mark ho (warna layout toot-ta) — regression guard
check("nested badge markup raw() se pass hota hai", 'raw(badgeMarkup)' in dash)
check("riskPlan ke nested rows raw(safeHtml…) hain",
      dash.count('raw(safeHtml`') >= 3, f"{dash.count('raw(safeHtml`')} nested blocks")

# ── [6] app.py source — wiring ───────────────────────────────────────────
print("\n[6] app.py — wiring")
check("wildcard CORS(app) call gaya (comment me zikr theek hai)",
      re.search(r'^\s*CORS\(app\)', src, re.M) is None)
check("flask_cors import gaya (manual allowlist)", 'flask_cors' not in src)
check("before_request security gate registered", '@app.before_request' in src)
check("configure_security() exposed (tests/ops ke liye)", 'def configure_security(' in src)
check("401 unauthorized path hai", "'unauthorized'" in src)
check("429 rate_limited path hai", "'rate_limited'" in src)
check("bind host configurable (STOCKAI_HOST)", "os.environ.get('STOCKAI_HOST'" in src)
check("startup par token warning print hoti hai", 'STOCKAI_API_TOKEN set NAHI hai' in src)
check("token cookie HttpOnly + SameSite", "httponly=True, samesite='Lax'" in src)

# ── [7] .env support (FIX-36) ────────────────────────────────────────────
print("\n[7] .env — local config file")
import os
import tempfile

src_app = (ROOT / 'app.py').read_text(encoding='utf-8')
check("load_dotenv_file() defined (koi third-party dotenv dependency nahi)",
      'def load_dotenv_file(' in src_app
      and 'import dotenv' not in src_app and 'from dotenv' not in src_app)
check("loader SECURITY banne se PEHLE chalta hai",
      src_app.index('DOTENV_KEYS = load_dotenv_file()') < src_app.index('SECURITY = {'))
check("refresh_security_from_env() available", 'def refresh_security_from_env(' in src_app)

# real parsing behaviour
tmp = pathlib.Path(tempfile.mkdtemp()) / '.env'
tmp.write_text(
    "# comment line\n"
    "\n"
    "STOCKAI_RATE_LIMIT=7\n"
    'STOCKAI_API_TOKEN="tok with spaces"\n'
    "export STOCKAI_HOST=127.0.0.1\n"
    "STOCKAI_CORS_ORIGINS=https://a.example, https://b.example\n"
    "BADLINE_WITHOUT_EQUALS\n"
    "  STOCKAI_TRUST_PROXY = 1 \n",
    encoding='utf-8')
loaded = A.load_dotenv_file(tmp, override=True)
check("KEY=VALUE parse hota hai", loaded.get('STOCKAI_RATE_LIMIT') == '7', str(loaded.get('STOCKAI_RATE_LIMIT')))
check("comment/blank lines skip", '# comment line' not in loaded and '' not in loaded)
check("equals ke bina line ignore", 'BADLINE_WITHOUT_EQUALS' not in loaded)
check("quotes hat jaate hain", loaded.get('STOCKAI_API_TOKEN') == 'tok with spaces', repr(loaded.get('STOCKAI_API_TOKEN')))
check("`export ` prefix handle hota hai", loaded.get('STOCKAI_HOST') == '127.0.0.1')
check("whitespace trim hota hai", loaded.get('STOCKAI_TRUST_PROXY') == '1')
A.refresh_security_from_env()
check("SECURITY .env se refresh hota hai (rate limit)", A.SECURITY['RATE_LIMIT_PER_MIN'] == 7,
      str(A.SECURITY['RATE_LIMIT_PER_MIN']))
check("SECURITY .env se refresh hota hai (token)", A.SECURITY['TOKEN'] == 'tok with spaces')
check("CORS list comma-split + trim", A.SECURITY['CORS_ORIGINS'] == ['https://a.example', 'https://b.example'],
      str(A.SECURITY['CORS_ORIGINS']))
check("TRUST_PROXY '1' → True", A.SECURITY['TRUST_PROXY'] is True)

# real env var jeetta hai (dotenv override=False default)
os.environ['STOCKAI_RATE_LIMIT'] = '99'
tmp2 = pathlib.Path(tempfile.mkdtemp()) / '.env'
tmp2.write_text("STOCKAI_RATE_LIMIT=7\n", encoding='utf-8')
A.load_dotenv_file(tmp2)
check("real env var .env se jeetta hai (override=False)", os.environ['STOCKAI_RATE_LIMIT'] == '99',
      os.environ['STOCKAI_RATE_LIMIT'])
A.load_dotenv_file(tmp2, override=True)
check("override=True par .env jeetti hai", os.environ['STOCKAI_RATE_LIMIT'] == '7')

# missing file → crash nahi
check(".env na ho to crash nahi, empty dict", A.load_dotenv_file(pathlib.Path(tempfile.mkdtemp()) / 'nope.env') == {})

# ANSI/cp1252 me save hui .env (Windows Notepad/PowerShell default) — crash nahi hona chahiye
ansi_dir = pathlib.Path(tempfile.mkdtemp())
(ansi_dir / '.env').write_bytes(b'STOCKAI_RATE_LIMIT=9\n# em-dash \x97 comment\n')
_rl_before = A.SECURITY['RATE_LIMIT_PER_MIN']
check("cp1252/ANSI .env par crash nahi (ignore + defaults)",
      A.load_dotenv_file(ansi_dir / '.env', override=True) == {})
check("ANSI .env ke baad SECURITY badla nahi (jo tha wahi raha)",
      A.SECURITY['RATE_LIMIT_PER_MIN'] == _rl_before,
      f"{A.SECURITY['RATE_LIMIT_PER_MIN']} == {_rl_before}")
check("loader UnicodeDecodeError catch karta hai", 'UnicodeDecodeError' in src_app)

# BOM wali .env (PowerShell 5.1 Set-Content -Encoding UTF8)
bom_dir = pathlib.Path(tempfile.mkdtemp())
(bom_dir / '.env').write_bytes('\ufeffSTOCKAI_RATE_LIMIT=11\nSTOCKAI_API_TOKEN=abc123\n'.encode('utf-8'))
_bom = A.load_dotenv_file(bom_dir / '.env', override=True)
check("BOM wali .env me pehla key corrupt NAHI hota",
      not any(k.startswith('\ufeff') for k in _bom) and _bom.get('STOCKAI_RATE_LIMIT') == '11', str(_bom))
check("loader utf-8-sig se padhta hai", "encoding='utf-8-sig'" in src_app)
for _k in ('STOCKAI_RATE_LIMIT', 'STOCKAI_API_TOKEN'):
    os.environ.pop(_k, None)
A.refresh_security_from_env()

# secret hygiene
gitignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
check(".gitignore me .env hai", '\n.env\n' in gitignore or gitignore.strip().endswith('.env'))
import subprocess
tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True).stdout.split()
check(".env git me track NAHI hoti", '.env' not in tracked)
check(".env.example track hoti hai (template team ke liye)", '.env.example' in tracked)
example = (ROOT / '.env.example').read_text(encoding='utf-8')
check(".env.example me token KHAALI hai (koi secret ship nahi hota)",
      '\nSTOCKAI_API_TOKEN=\n' in example or example.rstrip().endswith('STOCKAI_API_TOKEN='))

# defaults wapas
for k in ('STOCKAI_API_TOKEN', 'STOCKAI_CORS_ORIGINS', 'STOCKAI_RATE_LIMIT',
          'STOCKAI_HOST', 'STOCKAI_TRUST_PROXY'):
    os.environ.pop(k, None)
A.configure_security(token='', cors_origins=[], rate_limit_per_min=240)

# ── [8] startup auto-link (FIX-37) ───────────────────────────────────────
print("\n[8] startup par ready-to-click link")
check("startup_urls() defined", 'def startup_urls(' in src_app)
check("local_ip_addresses() defined", 'def local_ip_addresses(' in src_app)
check("auto_open_enabled() defined", 'def auto_open_enabled(' in src_app)

A.configure_security(token='')
u_off = A.startup_urls('0.0.0.0', 5000)
check("token OFF par link me ?token= NAHI", all('?token=' not in u for u in u_off), str(u_off[:2]))
check("pehla URL 127.0.0.1 (same PC)", u_off and u_off[0].startswith('http://127.0.0.1:5000/'), u_off[0] if u_off else '')

A.configure_security(token='abc123XYZ')
u_on = A.startup_urls('0.0.0.0', 5000)
check("token ON par ?token= apne aap jud jaata hai",
      all(u.endswith('?token=abc123XYZ') for u in u_on), u_on[0] if u_on else '')
check("link me port aata hai", ':5000/' in u_on[0])
check("LAN IP wali link bhi milti hai (phone ke liye)", len(u_on) >= 1)
u_alt = A.startup_urls('0.0.0.0', 8080)
check("custom port link me dikhta hai", ':8080/' in u_alt[0], u_alt[0])
u_bind = A.startup_urls('192.168.1.50', 5000)
check("STOCKAI_HOST bind ho to wahi host link me", u_bind[0].startswith('http://192.168.1.50:'), u_bind[0])
check("bind par 127.0.0.1 link nahi (bind hi serve karta hai)",
      all('127.0.0.1' not in u for u in u_bind), str(u_bind))

os.environ['STOCKAI_AUTO_OPEN'] = '0'
check("STOCKAI_AUTO_OPEN=0 → auto-open OFF", A.auto_open_enabled() is False)
os.environ['STOCKAI_AUTO_OPEN'] = '1'
check("STOCKAI_AUTO_OPEN=1 → auto-open ON", A.auto_open_enabled() is True)
os.environ.pop('STOCKAI_AUTO_OPEN', None)
check("default auto-open ON", A.auto_open_enabled() is True)
check("run block links print karta hai", 'Dashboard kholein' in src_app and 'webbrowser.open(urls[0])' in src_app)

# ── result ───────────────────────────────────────────────────────────────
A.configure_security(token='', cors_origins=[], rate_limit_per_min=240)
A.reset_rate_limiter()
passed = sum(1 for _, ok in results if ok)
failed = len(results) - passed
print("\n" + "=" * 84)
print(f" RESULT: {passed} passed, {failed} failed")
print("=" * 84)
sys.exit(1 if failed else 0)
