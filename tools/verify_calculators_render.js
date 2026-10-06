#!/usr/bin/env node
/**
 * tools/verify_calculators_render.js — FIX-74 browser-level test
 * ============================================================================
 * Calculators.html ka ASLI JavaScript jsdom me chalata hai aur rendered DOM par
 * assert karta hai. Python verifier maths pin karta hai; ye verify karta hai ki
 * page wo maths sahi DHIKHATA hai (aur error par jhootha number nahi).
 *
 * Zaroorat: npm i jsdom   (na ho to SKIP, exit 2 — repo ka purana pattern)
 * Chalao:   node tools/verify_calculators_render.js
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.log('⏭  SKIP — jsdom installed nahi (npm i jsdom)'); process.exit(2); }

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'Calculators.html'), 'utf8');

const results = [];
const check = (n, ok, d = '') => {
  results.push(ok);
  console.log(`  ${ok ? '✅' : '❌'} ${n}` + (d ? ` — ${d}` : ''));
};

// ── API responses: ye values tools/verify_calculators.py me HAATH se gini
//    gayi hain (222.48 / 13 shares / 23,00,386.89). Yahan sirf ye test hota hai
//    ki PAGE inhe sahi render karta hai ya nahi.
const TC = { notional: 100000, mode: 'delivery',
  buy: { brokerage: 0, stt: 100, exchange: 3.07, sebi: 0.1, stamp: 15, gst: 0.57, slippage: 0, total: 118.74 },
  sell: { brokerage: 0, stt: 100, exchange: 3.07, sebi: 0.1, stamp: 0, gst: 0.57, slippage: 0, total: 103.74 },
  total_charges: 222.48, total_pct: 0.2225, breakeven_move_pct: 0.2225,
  rates_as_of: '06-Oct-2026' };
const PS = { side: 'long', qty: 13, risk_amount: 1000, per_share_risk: 75,
  actual_risk: 975, position_value: 26975, capital_used_pct: 26.97, stop_distance_pct: 3.61 };
const SIP = { monthly: 10000, months: 120, annual_rate_pct: 12, invested: 1200000,
  future_value: 2300386.89, gains: 1100386.89,
  convention: 'end-of-month investment (har kist mahine ke ant me)' };
const RATES = { as_of: '06-Oct-2026',
  source: 'NSE circular 27-Feb-2026 (effective 01-Mar-2026): equity cash 0.00307%',
  note: 'Brokerage broker-specific hai, isliye wo input hai.' };

/** failPaths: jin endpoints ko 400 dena hai (error rendering test karne ke liye) */
function render(failPaths = []) {
  return new Promise((resolve) => {
    const dom = new JSDOM(html, {
      runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/',
      beforeParse(w) {
        w.fetch = (url) => {
          const p = String(url).split('?')[0];
          const bad = failPaths.includes(p);
          const body = bad
            ? { error: p.includes('position')
                ? 'long ke liye stop entry se neeche hona chahiye'
                : 'notional 0 se bada number hona chahiye' }
            : ({ '/api/calc/trade_costs': TC, '/api/calc/position_size': PS,
                 '/api/calc/sip': SIP, '/api/calc/rates': RATES }[p] || {});
          return Promise.resolve({ ok: !bad, status: bad ? 400 : 200,
                                   json: () => Promise.resolve(body) });
        };
      },
    });
    setTimeout(() => {
      const d = dom.window.document;
      const snap = {
        tc: d.getElementById('tc_out').textContent,
        tcCls: d.getElementById('tc_out').className,
        ps: d.getElementById('ps_out').textContent,
        psCls: d.getElementById('ps_out').className,
        sip: d.getElementById('sip_out').textContent,
        rates: d.getElementById('rates').textContent,
        ratesCls: d.getElementById('rates').className,
        page: d.body.textContent,
      };
      dom.window.close();
      resolve(snap);
    }, 80);
  });
}

(async () => {
  console.log('='.repeat(84));
  console.log(' Calculators.html — real JS jsdom me chala kar render assert');
  console.log('='.repeat(84));

  let s = await render();
  // ── trade cost ──────────────────────────────────────────────────────────
  check('trade cost total ₹222.48 render hua', s.tc.includes('₹222.48'), s.tc.slice(0, 90));
  check('buy 118.74 + sell 103.74 dikhte hain',
    s.tc.includes('₹118.74') && s.tc.includes('₹103.74'));
  check('break-even move 0.2225% dikhta hai', s.tc.includes('0.2225%'));
  check('STT ₹200 (buy 100 + sell 100) dikhta hai', s.tc.includes('₹200.00'), s.tc.slice(0, 200));
  check('exchange ₹6.14 (3.07 dono taraf) dikhta hai', s.tc.includes('₹6.14'));
  check('stamp ₹15.00 dikhta hai', s.tc.includes('₹15.00'));
  check('mode + rates date dikhte hain', s.tc.includes('delivery') && s.tc.includes('06-Oct-2026'));

  // ── position size ───────────────────────────────────────────────────────
  check('position size 13 shares render hua', s.ps.includes('13 shares'), s.ps.slice(0, 90));
  check('risk budget ₹1,000.00 dikhta hai', s.ps.includes('₹1,000.00'));
  check('actual risk ₹975.00 dikhta hai', s.ps.includes('₹975.00'));
  check('position value ₹26,975.00 dikhta hai', s.ps.includes('₹26,975.00'));
  check('capital used 26.97% dikhta hai', s.ps.includes('26.97%'));

  // ── SIP ─────────────────────────────────────────────────────────────────
  check('SIP FV ₹23,00,386.89 render hua (Indian grouping)',
    s.sip.includes('₹23,00,386.89'), s.sip.slice(0, 90));
  check('SIP invested ₹12,00,000.00 dikhta hai', s.sip.includes('₹12,00,000.00'));
  check('SIP gains ₹11,00,386.89 dikhte hain', s.sip.includes('₹11,00,386.89'));
  check('SIP par "assumed rate — guaranteed nahi" warning hai',
    /assumed rate/.test(s.sip) && /guaranteed/.test(s.sip));

  // ── rates disclosure ────────────────────────────────────────────────────
  check('rates box me date + NSE circular source hai',
    s.rates.includes('06-Oct-2026') && s.rates.includes('27-Feb-2026'), s.rates.slice(0, 120));
  check('rates box me broker-specific note hai', /broker-specific/i.test(s.rates));

  // ── static disclosures ──────────────────────────────────────────────────
  check('SEBI disclosure page par hai', s.page.includes('not SEBI registered'));
  check('"investment advice nahi" likha hai', /investment advice/i.test(s.page));
  check('"risk management" framing hai (prediction nahi)', /risk management/i.test(s.page));

  // ── error rendering: jhootha number nahi aana chahiye ───────────────────
  s = await render(['/api/calc/position_size']);
  check('400 par position-size error message dikhta hai',
    s.ps.includes('stop entry se neeche'), s.ps.slice(0, 120));
  check('400 par error red class me hai', s.psCls.includes('err'), s.psCls);
  check('400 par koi fake qty nahi dikti', !/shares/.test(s.ps), s.ps.slice(0, 120));
  check('ek calculator fail ho to baaki chalte rehte hain',
    s.tc.includes('₹222.48') && s.sip.includes('₹23,00,386.89'));

  s = await render(['/api/calc/trade_costs']);
  check('trade-cost 400 par honest error', s.tc.includes('notional 0 se bada'), s.tc.slice(0, 120));
  check('trade-cost 400 par fake total nahi', !/₹/.test(s.tc), s.tc.slice(0, 120));

  const p = results.filter(Boolean).length;
  console.log('='.repeat(84));
  console.log(` ${p} / ${results.length} checks passed`);
  console.log('='.repeat(84));
  process.exit(p === results.length ? 0 : 1);
})();
