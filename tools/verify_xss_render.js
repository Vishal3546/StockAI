#!/usr/bin/env node
/**
 * FIX-35 (M-12) browser-level test — asli injection attempt.
 *
 * Dashboard.html par pehle 14 `innerHTML` sinks the; search dropdown external
 * Yahoo `longname`/`symbol` ko seedha HTML me daalta tha aur inline
 * `onclick="selectStock('${s.sym}')"` me quote-break possible tha. Ab safeHtml``
 * tagged template + DOM-API list hai. Ye test wahi payloads bhejta hai jo ek
 * compromised/malicious upstream bhej sakta hai aur assert karta hai ki:
 *   1) koi script execute nahi hua (window.__pwned* set nahi)
 *   2) koi <img>/<script>/<iframe> element DOM me inject nahi hua
 *   3) user ko payload *text* ki tarah dikhta hai (chhupa nahi, execute nahi)
 *
 * Zaroorat: npm i jsdom   (na ho to SKIP, exit 2)
 * Chalane:  node tools/verify_xss_render.js
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) {
  console.log('⏭  SKIP — jsdom installed nahi hai (npm i jsdom chalayein)');
  process.exit(2);
}

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'Dashboard.html'), 'utf8');
const clean = html.replace(/<script[^>]*\bsrc=[^>]*>\s*<\/script>/g, '').replace(/<link[^>]*>/g, '');
const series = () => ({ setData() {}, applyOptions() {}, setMarkers() {} });

const PAYLOAD_TAG = 'xss-probe';
const evil = (id) => `<img src=x onerror="window.__pwned_${id}=1" data-tag="${PAYLOAD_TAG}">`;
const evilScript = (id) => `<script>window.__pwned_${id}=1<\/script>`;

const dom = new JSDOM(clean, {
  runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/',
  beforeParse(w) {
    w.fetch = () => new Promise(() => {});
    w.Chart = function () { this.destroy = () => {}; };
    w.LightweightCharts = { createChart: () => ({
      addCandlestickSeries: series, addLineSeries: series, addHistogramSeries: series,
      priceScale: () => ({ applyOptions() {} }), timeScale: () => ({ fitContent() {} }),
      applyOptions() {}, remove() {} }) };
  }
});
const w = dom.window;
const results = [];
const check = (name, ok, detail = '') => {
  results.push([name, !!ok]);
  console.log(`  ${ok ? '✅' : '❌'} ${name}${detail ? '  → ' + detail : ''}`);
};
const txt = (id) => (w.document.getElementById(id)?.textContent || '').replace(/\s+/g, ' ').trim();
const injectedIn = (id) => w.document.querySelectorAll(
  `#${id} img[data-tag="${PAYLOAD_TAG}"], #${id} script, #${id} iframe`).length;

console.log('='.repeat(84));
console.log(' FIX-35 (M-12) — XSS injection attempt (jsdom, runScripts: dangerously)');
console.log('='.repeat(84));

console.log('\n[1] render() — malicious API payload');
const malicious = {
  symbol: 'RELIANCE', name: evil('name'), price: 1200, change: 1, pChange: 0.1,
  ensemble: { score: 62, action: evilScript('action'), tradeable: false, calibration: { ready: true } },
  ensemble_v2: { score: 55, note: evil('v2note') },
  ml: { prediction: evil('mlpred'), probability: 51, confidence: evil('mlconf'),
        error: evil('mlerror'), baseline_accuracy: 50, best_edge: null,
        walk_forward_accuracy: null, wf_window_sigma: null },
  patterns: [{ name: evilScript('pattern'), type: evil('ptype'), direction: 'BULLISH',
               candles: 3, strength: 80 }],
  fundamentals: { mcap: evil('mcap'), pe: evil('pe'), pb: '1', roe: '1', debt_equity: '1', div_yield: '1' },
  engines: {
    vol_profile: { score: 50, signal: evil('sig1') },
    rvol_cvd: { score: 50, signal: evil('sig2'), rvol: 1 },
    vcp: { score: 50, signal: evil('sig3'), contractions: 2, tightness: 40 },
    smc: { score: 50, signal: evil('sig4'), liquidity_sweep: evil('sweep'),
           fvg_bullish: [], fvg_bearish: [], order_blocks: [] },
    regime: { score: 50, signal: evil('sig5') },
    mtf: { score: 50, signal: evil('sig6'),
           timeframes: { '5m': { trend: evil('mtf5m'), rsi: 55 } } },
  },
  risk: { direction: 'LONG', qty: 0, kelly_pct: 0, kelly_pct_after_regime: 0, rr_ratio: 1.67,
          sl: 1180, t1: 1230, t2: 1260, sl_pct: -1.7, entry_zone: evil('entry'),
          exec_status: evilScript('exec'), trail_sl_plan: evil('trail'),
          risk_note: evil('risknote'), win_rate_used: 0.5, regime_basis: evil('regimebasis'),
          regime_exposure_factor: 0.75, plan_hit_rate: 0.5, plan_hit_rate_lcb: 0.47,
          plan_sample_size: 200, plan_breakeven: 0.5, edge_verified: false,
          qty_pre_regime: 0, notional: 0, capital: 100000, leverage: 0, risk_amount: 0 },
  indicators: {}, week52: { high: 1300, low: 1100 }, chart: [],
};
let err = null;
try { w.render(malicious); } catch (e) { err = e.message; }
check('malicious payload par crash nahi hota', !err, err || '');

const pwnedKeys = Object.keys(w).filter((k) => k.startsWith('__pwned_'));
check('koi injected script execute NAHI hua (window.__pwned_*)', pwnedKeys.length === 0, pwnedKeys.join(','));

const containers = ['mlBox', 'indList', 'targetsBox', 'patternsBox', 'fundBox',
                    'mtfGrid', 'riskPlan', 'smcDetails', 'aiVerdict'];
let totalInjected = 0;
containers.forEach((id) => { totalInjected += injectedIn(id); });
check('kisi container me <img>/<script>/<iframe> inject NAHI hua', totalInjected === 0,
      containers.map((id) => `${id}:${injectedIn(id)}`).join(' '));

// Payload chhupa nahi hona chahiye — user ko text dikhe (escaped form me)
check('ml.error payload TEXT ki tarah dikhta hai', txt('mlBox').includes('onerror='), txt('mlBox').slice(0, 60));
check('pattern name payload TEXT ki tarah dikhta hai', txt('patternsBox').includes('onerror='));
check('risk_note payload TEXT ki tarah dikhta hai', txt('riskPlan').includes('onerror='));
// NOTE: escape hone ke baad payload ka *literal text* dikhta hai — isliye yahan
// assert hai ki text me probe string hai aur koi script/img element nahi bana.
check('exec_status payload escaped TEXT me dikhta hai, element nahi',
      txt('targetsBox').includes('__pwned_exec') && injectedIn('targetsBox') === 0,
      txt('targetsBox').slice(0, 40));
check('mtf trend payload TEXT ki tarah dikhta hai', txt('mtfGrid').includes('onerror='));
check('aiVerdict me action payload text hai, execute NAHI hua',
      txt('aiVerdict').includes('__pwned_action') && injectedIn('aiVerdict') === 0
      && typeof w.__pwned_action === 'undefined');

console.log('\n[2] search dropdown — external Yahoo result injection');
(async () => {
  const evilSym = `X"><img src=x onerror="window.__pwned_search=1" data-tag="${PAYLOAD_TAG}">`;
  w.fetch = async () => ({ json: async () => ([{
    sym: evilSym, name: `${evilScript('searchname')}`, sec: `<b onmouseover="window.__pwned_sec=1">SEC</b>`,
  }]) });
  const input = w.document.getElementById('symInput');
  input.value = 'x';
  input.dispatchEvent(new w.Event('input', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 500));   // debounce 150ms + promise chain

  const dd = w.document.getElementById('searchDropdown');
  check('dropdown me koi <img>/<script> element NAHI',
        dd.querySelectorAll(`img[data-tag="${PAYLOAD_TAG}"], script`).length === 0,
        `children=${dd.children.length}`);
  check('dropdown me inline onclick attribute NAHI',
        dd.querySelectorAll('[onclick]').length === 0);
  check('sym payload TEXT ki tarah dikhta hai (escaped)',
        (dd.textContent || '').includes('onerror='), (dd.textContent || '').slice(0, 50));
  check('search injection se script execute NAHI hua',
        typeof w.__pwned_search === 'undefined' && typeof w.__pwned_sec === 'undefined'
        && typeof w.__pwned_searchname === 'undefined');
  const item = dd.querySelector('.search-item');
  check('data-sym attribute me raw symbol hai (click delegation chalega)',
        !!item && item.dataset.sym === evilSym, item ? item.dataset.sym.slice(0, 20) + '…' : 'no item');

  const passed = results.filter(([, ok]) => ok).length;
  const failed = results.length - passed;
  console.log('\n' + '='.repeat(84));
  console.log(` RESULT: ${passed} passed, ${failed} failed`);
  console.log('='.repeat(84));
  process.exit(failed ? 1 : 0);
})();
