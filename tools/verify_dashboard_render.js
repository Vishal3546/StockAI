#!/usr/bin/env node
/**
 * FIX-29 browser-level test — Dashboard.html ka render() real DOM me chala kar
 * dekhta hai ki missing data par FAKE reading nahi chhapti aur real data par
 * asli numbers dikhte hain.
 *
 * Zaroorat:  npm i jsdom        (jsdom na ho to ye test SKIP hota hai, exit 2)
 * Chalane:   node tools/verify_dashboard_render.js [real_payload.json]
 *            (payload optional — /api/stock/SYMBOL ka JSON)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) {
  console.log('⏭  SKIP — jsdom installed nahi hai (' + (process.argv[2] ? '' : '') + 'npm i jsdom chalayein)');
  process.exit(2);
}

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'Dashboard.html'), 'utf8');
const clean = html.replace(/<script[^>]*\bsrc=[^>]*>\s*<\/script>/g, '').replace(/<link[^>]*>/g, '');
const series = () => ({ setData() {}, applyOptions() {}, setMarkers() {} });
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
const txt = id => (w.document.getElementById(id)?.textContent || '').replace(/\s+/g, ' ').trim();
const render = d => { try { w.render(d); return null; } catch (e) { return e.message; } };

console.log('='.repeat(84));
console.log(' FIX-29 — Dashboard render() behaviour test (jsdom)');
console.log('='.repeat(84));

console.log('\n[1] render({}) — data hi nahi (sabse important case)');
let err = render({});
check('crash nahi hota', !err, err || '');
const ind = txt('indList'), verdict = txt('aiVerdict'), risk = txt('riskPlan');
check('RSI row fake "50" nahi', !/RSI \(14\) 50\b/.test(ind), ind.match(/RSI \(14\) [^ ]*/)?.[0]);
check('RSI row par "—"', /RSI \(14\) —/.test(ind));
check('volume-ratio fake "NORMAL" nahi', !/NORMAL/.test(ind));
check('risk plan me fake ₹0 nahi', !/₹0\b/.test(risk));
check('master score fake 50/100 nahi', !/Score: 50\/100/.test(verdict));
check('master score "—/100"', /Master Score: —\/100/.test(verdict));
check('ML confidence fake "LOW" nahi', !/LOW Confidence/.test(verdict), verdict.match(/\(([A-Z]+) Confidence\)/)?.[1]);
check('gauge khaali (needle 50 par nahi)', Math.abs(parseFloat(w.document.getElementById('gMaster').style.strokeDashoffset) - 2 * Math.PI * 47) < 0.01);

console.log('\n[2] partial payload — kuch fields hain, kuch nahi');
err = render({ price: 1234.5, ensemble: { score: 71, action: 'BUY', tradeable: true }, indicators: { rsi: 61.2 }, engines: {} });
check('crash nahi hota', !err, err || '');
check('asli RSI 61.2 dikhta hai', /RSI \(14\) 61.2/.test(txt('indList')));
check('missing risk par ₹0 nahi', !/₹0\b/.test(txt('riskPlan')));
check('verdict me 71/100', /Master Score: 71\/100/.test(txt('aiVerdict')));

const payloadPath = process.argv[2];
if (payloadPath && fs.existsSync(payloadPath)) {
  console.log('\n[3] real payload — ' + payloadPath);
  const p = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
  err = render(p);
  check('crash nahi hota', !err, err || '');
  const v = txt('aiVerdict'), ind2 = txt('indList');
  if (p.indicators?.rsi != null) check('real RSI ' + p.indicators.rsi + ' table me', ind2.includes(String(p.indicators.rsi)));
  if (p.ensemble?.score != null) check('real master score ' + p.ensemble.score, new RegExp('Master Score: ' + p.ensemble.score + '/100').test(v));
  if (p.ml?.probability != null) check('real ML probability ' + p.ml.probability + '%', v.includes(p.ml.probability + '%'));
  check('gauges fill hue (khaali nahi)', Math.abs(parseFloat(w.document.getElementById('gMaster').style.strokeDashoffset) - 2 * Math.PI * 47) > 0.01);
} else {
  console.log('\n[3] real payload — skip (koi payload path nahi diya)');
}

const passed = results.filter(([, ok]) => ok).length;
const failed = results.length - passed;
console.log('\n' + '='.repeat(84));
console.log(` RESULT: ${passed} passed, ${failed} failed`);
console.log('='.repeat(84));
process.exit(failed ? 1 : 0);
