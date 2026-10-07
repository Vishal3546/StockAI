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
check('missing score par fake 50/100 nahi', !/Ensemble rank: 50\/100/.test(verdict), verdict.match(/Ensemble rank: [^ ]*/)?.[0]);
check('missing score "Ensemble rank: —/100" dikhata hai', /Ensemble rank: —\/100/.test(verdict), verdict.match(/Ensemble rank: [^ ]*/)?.[0]);
// honesty redesign ke baad wording "Master Score" se "Ensemble rank" hua tha;
// ye 2 checks purane wording par pinned the aur chupchap fail ho rahe the.
// ML data absent hai to baseline accuracy par number nahi, '?' aana chahiye.
check('ML absent par baseline accuracy "?" hai (fabricated number nahi)',
      /Baseline accuracy of \?%/.test(verdict), verdict.match(/Baseline accuracy of [^ ]+/)?.[0]);
check('ML confidence fake "LOW" nahi', !/LOW Confidence/.test(verdict), verdict.match(/\(([A-Z]+) Confidence\)/)?.[1]);
check('gauge khaali (needle 50 par nahi)', Math.abs(parseFloat(w.document.getElementById('gMaster').style.strokeDashoffset) - 2 * Math.PI * 47) < 0.01);

console.log('\n[2] partial payload — kuch fields hain, kuch nahi');
err = render({ price: 1234.5, ensemble: { score: 71, action: 'BUY', tradeable: true }, indicators: { rsi: 61.2 }, engines: {} });
check('crash nahi hota', !err, err || '');
check('asli RSI 61.2 dikhta hai', /RSI \(14\) 61.2/.test(txt('indList')));
check('missing risk par ₹0 nahi', !/₹0\b/.test(txt('riskPlan')));
check('verdict me real "Ensemble rank: 71/100"', /Ensemble rank: 71\/100/.test(txt('aiVerdict')));
check('tradeable verdict "NOT validated profit" disclose karta hai', /NOT validated profit/.test(txt('aiVerdict')));
check('rank ko "probability"/"chance" nahi bola jata', !/(probability|chance) of (profit|gain)/i.test(txt('aiVerdict')));

console.log('\n[2b] unfit history + no measured plan (FIX-33)');
err = render({ ensemble: { score: 72, action: 'WATCHLIST', tradeable: false,
    calibration: { ready: false, note: 'history missing; no BUY' } },
  risk: { qty: 0, win_rate_used: null, risk_note: 'ASSUMED fallback blocked; no trade',
    regime_exposure_factor: 0, qty_pre_regime: 0, regime_basis: 'UNKNOWN' }, engines: {} });
check('unfit payload crash nahi hota', !err, err || '');
check('score panel UNFITTED + no directional action batata hai',
  /UNFITTED.*history missing/.test(txt('scoreCalNote')));
check('unfit score panel me magic p80/65/78 nahi', !/p80 ≥/.test(txt('scoreCalNote')));
check('no-plan risk note visible even if win_rate_used null',
  /ASSUMED fallback blocked/.test(txt('riskPlan')));
check('unknown regime cap qty 0 visible', /Market-regime exposure cap .*0%.*0 → 0/.test(txt('riskPlan')));
err = render({ risk: { plan_hit_rate: 0.50, plan_sample_size: null,
    plan_breakeven: null, plan_hit_rate_lcb: null }, ensemble: {}, engines: {} });
check('partial measured-plan metadata crash nahi hota', !err, err || '');
check('partial plan me fake 0.0% lower-bound/breakeven nahi',
  /breakeven —, lower-bound —/.test(txt('riskPlan')));

const payloadPath = process.argv[2];
if (payloadPath && fs.existsSync(payloadPath)) {
  console.log('\n[3] real payload — ' + payloadPath);
  const p = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
  err = render(p);
  check('crash nahi hota', !err, err || '');
  const v = txt('aiVerdict'), ind2 = txt('indList');
  if (p.indicators?.rsi != null) check('real RSI ' + p.indicators.rsi + ' table me', ind2.includes(String(p.indicators.rsi)));
  if (p.ensemble?.score != null) check('real ensemble rank ' + p.ensemble.score, new RegExp('Ensemble rank: ' + p.ensemble.score + '/100').test(v));
  if (p.ml?.probability != null) check('real ML probability ' + p.ml.probability + '%', v.includes(p.ml.probability + '%'));
  check('gauges fill hue (khaali nahi)', Math.abs(parseFloat(w.document.getElementById('gMaster').style.strokeDashoffset) - 2 * Math.PI * 47) > 0.01);
  if (p.ensemble?.calibration) {
    const fit = p.ensemble.calibration;
    const label = txt('scoreCalNote');
    if (fit.ready) {
      check('fitted history sessions/sample/p80/p95 visible (FIX-33)',
        label.includes(String(fit.sessions) + ' past sessions') &&
        label.includes(String(fit.samples) + ' stock-scores') &&
        /p80 ≥ .*p95 ≥/.test(label) && /NOT profit probability/.test(label));
      check('score panel absolute as-of date visible', label.includes(fit.asof_session));
    } else {
      check('stale/unfit clearly labelled', /UNFITTED/.test(label));
    }
    if (p.risk?.regime_exposure_factor != null)
      check('live risk regime exposure cap visible',
        txt('riskPlan').includes((p.risk.regime_exposure_factor * 100).toFixed(0) + '%'));
    if (p.ensemble.action === 'BUY_BREAKOUT' && !p.ensemble.tradeable)
      check('top rank but plan edge missing → UI NO TRADE (not green recommendation)',
        /NO TRADE/.test(txt('targetsBox')) && !/🟢/.test(txt('aiVerdict')));
  }
  if (p.risk && p.risk.win_rate_used != null) {
    const riskText = txt('riskPlan');
    // FIX-31 ke baad measured plan available ho to ASSUMED nahi dikhna chahiye.
    // Fallback path me hi ASSUMED + model accuracy ki warning sahi hai.
    if (p.risk.plan_hit_rate != null) {
      check('measured plan disclosure dikhta hai (FIX-31)', /Plan ka ASLI hit-rate measured/.test(riskText));
      check('lower-bound + sample + breakeven dikhte hain',
        /Measured plan win-rate\s*\d.*n=\d+.*breakeven .*lower-bound/.test(riskText));
      check('measured case me ASSUMED win-rate nahi', !/ASSUMED win-rate/.test(riskText));
    } else {
      check('fallback par ASSUMED disclosure (FIX-30)', /ASSUMED win-rate/.test(riskText));
      check('fallback me model ki measured accuracy', /measured accuracy \d/.test(riskText));
    }
  }
} else {
  console.log('\n[3] real payload — skip (koi payload path nahi diya)');
}

const passed = results.filter(([, ok]) => ok).length;
const failed = results.length - passed;
console.log('\n' + '='.repeat(84));
console.log(` RESULT: ${passed} passed, ${failed} failed`);
console.log('='.repeat(84));
process.exit(failed ? 1 : 0);
