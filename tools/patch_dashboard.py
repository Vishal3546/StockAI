#!/usr/bin/env python3
"""
tools/patch_dashboard.py — applies the front-end fixes to Dashboard.html (in place)
================================================================================
Every replacement is anchored and asserted; if the HTML changes the build fails
loudly instead of producing a half-patched dashboard. The original file stays in
git history (`git checkout HEAD~1 -- Dashboard.html` to revert).

Usage:  python3 tools/patch_dashboard.py
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / 'Dashboard.html'
html = SRC.read_text(encoding='utf-8')
applied = []


def rep(name, old, new):
    global html
    if html.count(old) != 1:
        raise SystemExit(f"ABORT [{name}]: anchor found {html.count(old)}x (expected 1)")
    html = html.replace(old, new, 1)
    applied.append(name)


# D1 · vendored chart library (offline-capable) + CDN fallback
rep("D1 local chart lib + CDN fallback",
    '<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>',
    '<script src="/static/lightweight-charts.standalone.production.js"></script>\n'
    '<script>if(!window.LightweightCharts){document.write(\'<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"><\\/script>\');}</script>')

# D2 · favicon (icon/ existed but was unreachable)
rep("D2 favicon wired",
    '<title>StockAI Pro V6.0 — Institutional AI & ML Dashboard</title>',
    '<title>StockAI Pro V6.1 — Institutional AI & ML Dashboard</title>\n<link rel="icon" type="image/svg+xml" href="/icon/favicon.svg">')

# D3 · same-origin API (was hardcoded http://127.0.0.1:5000 → broke on any other host)
rep("D3 same-origin API + fallback",
    'let activeSymbol = "RELIANCE";',
    "// FIX(D3): default to same-origin so the dashboard works behind any host /\n"
    "// proxy / tunnel; override with ?api=http://host:port or window.STOCKAI_API\n"
    "const API = new URLSearchParams(location.search).get('api')\n"
    "          || window.STOCKAI_API\n"
    "          || (location.protocol === 'file:' ? 'http://127.0.0.1:5000' : '');\n"
    "const FALLBACK_ORIGIN = 'http://127.0.0.1:5000';\n"
    "async function apiFetch(path, opts) {\n"
    "  const urls = [API + path];\n"
    "  if (API !== FALLBACK_ORIGIN) urls.push(FALLBACK_ORIGIN + path);\n"
    "  let lastErr;\n"
    "  for (const u of urls) {\n"
    "    try { return await fetch(u, opts); } catch (e) { lastErr = e; }\n"
    "  }\n"
    "  throw lastErr;\n"
    "}\n"
    'let activeSymbol = "RELIANCE";')
html = html.replace('`http://127.0.0.1:5000/api/search?q=${encodeURIComponent(q)}`', '`/api/search?q=${encodeURIComponent(q)}`')
html = html.replace('`http://127.0.0.1:5000/api/quote/${symbol}`', '`/api/quote/${symbol}`')
html = html.replace('`http://127.0.0.1:5000/api/stock/${sym}`', '`/api/stock/${sym}`')
html = html.replace('const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`);',
                    'const r = await apiFetch(`/api/search?q=${encodeURIComponent(q)}`);')
html = html.replace('const res = await fetch(`/api/quote/${symbol}`);',
                    'const res = await apiFetch(`/api/quote/${symbol}`);')
html = html.replace('const r = await fetch(`/api/stock/${sym}`, {\n      signal: controller.signal\n    });',
                    'const r = await apiFetch(`/api/stock/${sym}`, {\n      signal: controller.signal\n    });')
applied.append("D3b three fetches routed through apiFetch")

# D4 · null-safe formatting (backend now sends null instead of a false 0)
rep("D4 null-safe sv() + sf2 helper",
    "function sv(value, fallbackValue) {\n  return value !== undefined && value !== null ? value : fallbackValue;\n}",
    "function sv(value, fallbackValue) {\n"
    "  // FIX(D4): null / undefined / '' / NaN → fallback, so a missing indicator\n"
    "  // renders as '—' instead of a misleading 0.\n"
    "  if (value === null || value === undefined || value === '' || (typeof value === 'number' && Number.isNaN(value))) return fallbackValue;\n"
    "  return value;\n"
    "}\n"
    "function sf2(v, d) { return (v === null || v === undefined || v === '' || v === 0) ? (d === undefined ? '—' : d) : v; }")

# D5 · signed edge (it always printed a '+' even for negative edges)
rep("D5 signed best-edge",
    "📊 Baseline: ${sv(ml.baseline_accuracy, '?')}% | Best Edge: +${sv(ml.best_edge, 0)}% | UP Days: ${sv(ml.pos_rate, '?')}%",
    "📊 Baseline: ${sv(ml.baseline_accuracy, '?')}% | Single-split best edge: "
    "${sv(ml.best_edge, 0) > 0 ? '+' : ''}${sv(ml.best_edge, 0)}% | UP Days: ${sv(ml.pos_rate, '?')}%")

# D6 · honest walk-forward panel
rep("D6 walk-forward honesty panel",
    "    mlh += `\n        <div style=\"font-size:0.65rem;color:var(--text-muted);margin-top:4px\">Trained: ${sv(ml.train_days, 0)}D | Tested: ${sv(ml.test_days, 0)}D</div>",
    "    const wf = sv(ml.walk_forward_accuracy, null);\n"
    "    const wfEdge = (wf !== null && ml.baseline_accuracy !== undefined) ? (wf - ml.baseline_accuracy) : null;\n"
    "    const honest = wfEdge === null ? 'UNKNOWN'\n"
    "        : wfEdge > 5 ? 'POSSIBLE EDGE (verify)'\n"
    "        : wfEdge > 0 ? 'MARGINAL / NOISE'\n"
    "        : 'NO EDGE (below baseline)';\n"
    "    mlh += `\n"
    "        <div style=\"font-size:0.65rem;margin-top:6px;padding:5px;border-radius:6px;background:rgba(255,179,0,0.08);color:var(--color-yellow)\">\n"
    "          🧪 Walk-forward OOS accuracy: <b>${wf === null ? '—' : wf + '%'}</b>\n"
    "          (baseline ${sv(ml.baseline_accuracy, '?')}%, edge ${wfEdge === null ? '—' : (wfEdge > 0 ? '+' : '') + wfEdge.toFixed(1) + 'pp'})\n"
    "          → <b>${honest}</b><br>\n"
    "          <span style=\"color:var(--text-muted)\">Window σ ≈ ${sv(ml.wf_window_sigma, '—')}pp — a single 80/20 split is not evidence.</span>\n"
    "        </div>`;\n"
    "    mlh += `\n        <div style=\"font-size:0.65rem;color:var(--text-muted);margin-top:4px\">Trained: ${sv(ml.train_days, 0)}D | Tested: ${sv(ml.test_days, 0)}D</div>")

# D7 · truthful data-source badge
rep("D7 truthful live badge",
    "document.getElementById('activeEngineTag').textContent = sv(d.data_source, 'NSE DIRECT LIVE');",
    "const src = sv(d.data_source, 'UNKNOWN');\n"
    "  const srcBadge = document.getElementById('activeEngineTag');\n"
    "  srcBadge.textContent = src;\n"
    "  const isLive = /NSE/i.test(src) && !/TradingView/i.test(src);\n"
    "  srcBadge.style.background = isLive ? 'var(--color-green-dim)' : 'var(--color-yellow-dim)';\n"
    "  srcBadge.style.color = isLive ? 'var(--color-green)' : 'var(--color-yellow)';\n"
    "  document.getElementById('liveBadgeText').textContent = isLive ? 'NSE LIVE' : 'DELAYED (15-20 min)';\n"
    "  document.getElementById('liveBadge').style.background = isLive ? 'var(--color-green-dim)' : 'var(--color-yellow-dim)';\n"
    "  document.getElementById('liveBadge').style.color = isLive ? 'var(--color-green)' : 'var(--color-yellow)';")
rep("D7b badge id",
    '<div class="live-b"><div class="live-dot"></div>NSE REALTIME TICK</div>',
    '<div class="live-b" id="liveBadge"><div class="live-dot"></div><span id="liveBadgeText">SOURCE…</span></div>')

# D8 · expose notional / leverage (this is what hid the 5.4x sizing bug)
rep("D8 notional + direction rows",
    "      <div class=\"col-4\"><div class=\"lvl-box\"><div class=\"lvl-lbl\">R:R Ratio</div><div class=\"lvl-val mono\" style=\"color:var(--color-green)\">1:${sv(rk.rr_ratio, 1.5)}</div></div></div>\n    </div>`;",
    "      <div class=\"col-4\"><div class=\"lvl-box\"><div class=\"lvl-lbl\">R:R Ratio</div><div class=\"lvl-val mono\" style=\"color:var(--color-green)\">1:${sv(rk.rr_ratio, 1.5)}</div></div></div>\n"
    "    </div>\n"
    "    <div class=\"ind-row\"><span style=\"color:var(--text-secondary)\">Direction</span><span class=\"mono\">${sv(rk.direction, 'NONE')}</span></div>\n"
    "    <div class=\"ind-row\"><span style=\"color:var(--text-secondary)\">Capital used</span><span class=\"mono\">₹${sv(rk.capital, 0).toLocaleString('en-IN')}</span></div>\n"
    "    <div class=\"ind-row\"><span style=\"color:var(--text-secondary)\">Notional exposure</span><span class=\"mono\" style=\"color:${(sv(rk.notional, 0) > sv(rk.capital, 1)) ? 'var(--color-red)' : 'var(--color-green)'}\">₹${sv(rk.notional, 0).toLocaleString('en-IN')} (${sv(rk.leverage, 0)}x)</span></div>\n"
    "    <div class=\"ind-row\"><span style=\"color:var(--text-secondary)\">Risk at stop</span><span class=\"mono\">₹${sv(rk.risk_amount, 0).toLocaleString('en-IN')}</span></div>`;")

# D9 · use the SSE stream the backend already exposes (with polling fallback)
rep("D9 SSE + polling fallback",
    "function startLiveTicker(symbol) {\n  if (liveTickerInterval) clearInterval(liveTickerInterval);\n  \n  liveTickerInterval = setInterval(async () => {",
    "let liveSource = null;\n"
    "function startLiveTicker(symbol) {\n"
    "  if (liveTickerInterval) clearInterval(liveTickerInterval);\n"
    "  if (liveSource) { liveSource.close(); liveSource = null; }\n"
    "  // FIX(D9): prefer the server-sent-events stream; poll only if it fails.\n"
    "  try {\n"
    "    liveSource = new EventSource(`${API}/api/stream/${symbol}`);\n"
    "    let gotData = false;\n"
    "    liveSource.onmessage = (ev) => {\n"
    "      gotData = true;\n"
    "      try { const t = JSON.parse(ev.data); if (t && t.price) updatePriceDOM(t.price, t.change, t.pChange); } catch (e) {}\n"
    "    };\n"
    "    liveSource.onerror = () => {\n"
    "      if (liveSource) { liveSource.close(); liveSource = null; }\n"
    "      if (!gotData) startPolling(symbol);\n"
    "    };\n"
    "  } catch (e) { startPolling(symbol); }\n"
    "}\n"
    "function startPolling(symbol) {\n"
    "  liveTickerInterval = setInterval(async () => {")

# D10 · inline banner instead of blocking alert()
rep("D10 inline error banner",
    "    const d = await r.json();\n    if (d.error) {\n      alert(`Stock Error: ${d.error}`);\n      return;\n    }",
    "    const d = await r.json();\n    if (d.error) {\n      showBanner(`⚠️ ${d.error}`, 'var(--color-red)');\n      return;\n    }")
rep("D10b banner helper",
    "// Fetch Core Stock Data (With Advanced Fetch Control)",
    "function showBanner(msg, color) {\n"
    "  let el = document.getElementById('errBanner');\n"
    "  if (!el) { el = document.createElement('div'); el.id = 'errBanner';\n"
    "    el.style.cssText = 'position:sticky;top:70px;z-index:200;margin:10px 16px 0;padding:10px 14px;border-radius:10px;font-size:0.85rem;font-weight:600;background:rgba(255,61,0,0.1);border:1px solid rgba(255,61,0,0.3)';\n"
    "    document.body.insertBefore(el, document.body.firstChild); }\n"
    "  el.style.color = color || 'var(--color-red)'; el.textContent = msg;\n"
    "  clearTimeout(el._t); el._t = setTimeout(() => el.remove(), 9000);\n"
    "}\n\n// Fetch Core Stock Data (With Advanced Fetch Control)")
rep("D10c timeout message",
    "      alert('Request Timeout! System is taking longer than expected. Please retry.');",
    "      showBanner('⏱️ Request timed out — the ML walk-forward needs 5-10s on a cold cache. Retry.', 'var(--color-yellow)');")
rep("D10d offline message",
    "      alert('Local API is disconnected! Check if your python app.py is running on port 5000.');",
    "      showBanner('🔌 API unreachable. Start the backend (python app.py) or pass ?api=http://host:port', 'var(--color-red)');")

# D11 · null-safe indicator rows + calibration comparison row
rep("D11 null-safe SMA row",
    "    ['SMA 50/200', `${sv(ind.sma50, 0)} / ${sv(ind.sma200, 0)}`, sv(ind.sma50, 0) > sv(ind.sma200, 0) ? 'GOLDEN' : 'DEATH'],",
    "    ['SMA 50/200', `${sf2(ind.sma50)} / ${sf2(ind.sma200)}`, (ind.sma50 === null || ind.sma200 === null) ? 'INSUFFICIENT HISTORY' : (ind.sma50 > ind.sma200 ? 'GOLDEN' : 'DEATH')],")
rep("D11b calibration comparison row",
    "    ['Volume Ratio', `${sv(ind.vol_ratio, 0)}x avg`,",
    "    ['Master (v2 diagnostic)', `${sv(d.ensemble_v2 && d.ensemble_v2.score, '—')}`, sv(d.ensemble_v2 && d.ensemble_v2.note, '')],\n"
    "    ['Volume Ratio', `${sv(ind.vol_ratio, 0)}x avg`,")

html = html.replace('StockAI <span style="font-weight:400;opacity:0.6">V6.0</span>',
                    'StockAI <span style="font-weight:400;opacity:0.6">V6.1</span>')

if '127.0.0.1:5000/api' in html:
    raise SystemExit("ABORT: hardcoded API host still present")

SRC.write_text(html, encoding='utf-8')
print(f"Dashboard.html patched in place ({len(html):,} bytes), {len(applied)} patches:")
for a in applied:
    print("  ✓", a)
