#!/usr/bin/env node
/**
 * tools/verify_screener_render.js — FIX-76 browser-level test
 * ============================================================================
 * Screener.html ka ASLI JavaScript jsdom me chalata hai aur rendered DOM par
 * assert karta hai. tools/verify_screener.py module maths/filter pin karta hai;
 * ye verify karta hai ki PAGE wo sahi DHIKHATA hai — aur khaas taur par:
 *
 *   • stale data par banner dikhta hai (chhupta nahi)
 *   • scan missing / fetch fail par jhootha number NAHI aata
 *   • null values "—" bante hain, "0.00" nahi (fake data ka sabse aam roop)
 *   • ML+ / ML− distinction render hoti hai
 *   • facets se filter dropdowns bante hain
 *
 * Expected strings pehle jsdom me ACTUAL output dekh kar likhi gayi hain.
 *
 * Zaroorat: npm i jsdom   (na ho to SKIP, exit 2)
 * Chalao:   node tools/verify_screener_render.js
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.log('⏭  SKIP — jsdom installed nahi (npm i jsdom)'); process.exit(2); }

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'Screener.html'), 'utf8');

const results = [];
const check = (n, ok, d = '') => {
  results.push(ok);
  console.log(`  ${ok ? '✅' : '❌'} ${n}` + (d ? ` — ${d}` : ''));
};

// 03-Oct-2026 wale asli scan se do rows (KOTAKBANK = top BUY, TCS = STRONG SELL
// with null t1/t2/sl). Values tools/verify_screener.py me bhi pin hain.
const ROWS = [
  { symbol: 'KOTAKBANK', sector: 'Banking', price: 418.35, change_pct: 0.32, rsi: 57.6,
    vol_ratio: 4.52, signal: 'BUY', signal_score: 60, composite: 53, ensemble: 60,
    ml_prob: 45.7, ml_acc: 63, ml_baseline: 52, ml_edge: 11, ml_used_in_composite: true,
    t1: 435.95, t2: 449.15, sl: 405.15, source: 'Yahoo Finance', signal_basis: 'x' },
  { symbol: 'TCS', sector: 'IT', price: 3100.5, change_pct: -4.86, rsi: 21.8,
    vol_ratio: 0.88, signal: 'STRONG SELL', signal_score: 40, composite: 44, ensemble: 40,
    ml_prob: 20.7, ml_acc: 35, ml_baseline: 59, ml_edge: -24, ml_used_in_composite: false,
    t1: null, t2: null, sl: null, source: 'TradingView Direct', signal_basis: 'y' },
];

function base(over = {}) {
  return Object.assign({
    ok: true, as_of: '2026-10-03T12:27:39.808244', age_label: '3.2 days', age_verdict: 'old',
    total: 30, matched: ROWS.length, filters: {},
    facets: { sectors: { Banking: 4, IT: 4 }, signals: { SELL: 14, 'STRONG SELL': 9, WATCH: 6, BUY: 1 } },
    sort: { key: 'signal_score', order: 'desc', valid_keys: ['signal_score'] },
    ml_note_used: 'USED NOTE', ml_note_unused: 'UNUSED NOTE', note: 'NOTE', rows: ROWS,
  }, over);
}

/** mode: 'ok' | 'reject' | 'http500' */
function render(payload, mode = 'ok') {
  return new Promise((resolve) => {
    const dom = new JSDOM(html, {
      runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/',
      beforeParse(w) {
        w.fetch = () => {
          if (mode === 'reject') return Promise.reject(new Error('Failed to fetch'));
          if (mode === 'http500') return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
        };
      },
    });
    setTimeout(() => {
      const d = dom.window.document;
      const rows = [...d.querySelectorAll('#tbody tr')].map((tr) => tr.textContent.replace(/\s+/g, ' ').trim());
      const cells = [...d.querySelectorAll('#tbody tr')].map((tr) =>
        [...tr.querySelectorAll('td')].map((td) => td.textContent.replace(/\s+/g, ' ').trim()));
      const snap = {
        rows, nrows: rows.length, cells,
        tbodyHtml: d.getElementById('tbody').innerHTML,
        stale: d.getElementById('stale').textContent.replace(/\s+/g, ' ').trim(),
        staleCls: d.getElementById('stale').className,
        staleShown: d.getElementById('stale').style.display !== 'none',
        count: d.getElementById('count').textContent.replace(/\s+/g, ' ').trim(),
        mlnote: d.getElementById('mlnote').textContent.replace(/\s+/g, ' ').trim(),
        err: d.getElementById('err').textContent.replace(/\s+/g, ' ').trim(),
        errShown: d.getElementById('err').style.display !== 'none',
        sectors: [...d.getElementById('f_sector').options].map((o) => o.textContent),
        signals: [...d.getElementById('f_signal').options].map((o) => o.textContent),
      };
      dom.window.close();
      resolve(snap);
    }, 200);
  });
}

// money-jaisa number (plain "500" match nahi hona chahiye — FIX-75 ka lesson)
const NUM = /[-+]?\d[\d,]*\.\d{2}/;

(async () => {
  console.log('='.repeat(84));
  console.log(' Screener.html — real JS jsdom me chala kar render assert (FIX-76)');
  console.log('='.repeat(84));

  // ── 1. NORMAL (purana scan) ────────────────────────────────────────────
  let s = await render(base());
  check('2 rows render hue', s.nrows === 2, String(s.nrows));
  check('stale banner DIKHTA hai (chhupta nahi)', s.staleShown === true);
  check('stale banner amber class me hai', s.staleCls.includes('b-old'), s.staleCls);
  check('banner me age + timestamp hai', s.stale.includes('3.2 days')
    && s.stale.includes('2026-10-03T12:27:39'), s.stale.slice(0, 80));
  check('banner regenerate command batata hai', s.stale.includes('nifty_scanner.py'));
  check('count "2 / 30 stocks" dikhta hai', s.count.includes('2 / 30'), s.count);
  check('count me sort key/order dikhta hai', s.count.includes('signal_score desc'), s.count);
  check('KOTAKBANK row ke numbers sahi', s.rows[0].includes('418.35') && s.rows[0].includes('60.00')
    && s.rows[0].includes('57.60'), s.rows[0].slice(0, 90));
  check('positive change +0.32 sign ke saath', s.rows[0].includes('+0.32'));
  check('negative change -4.86 dikhta hai', s.rows[1].includes('-4.86'));
  check('BUY pill green class me', /class="pill p-buy"/.test(s.tbodyHtml));
  check('STRONG SELL pill red class me', /class="pill p-sell"/.test(s.tbodyHtml));
  check('ML+ marker render hua (used)', s.rows[0].includes('ML+'));
  check('ML− marker render hua (not used)', s.rows[1].includes('ML−'));
  check('ML+/ML− ke title me honest note hai',
    s.tbodyHtml.includes('USED NOTE') && s.tbodyHtml.includes('UNUSED NOTE'));
  check('ml edge -24.00 dikhta hai', s.rows[1].includes('-24.00'));
  // NOTE: pehle wala check `!s.rows[1].includes('0.00')` tha — GALAT, kyunki
  // "40.00" me "0.00" substring ki tarah aa jaata hai. Ab CELL-level check:
  // TCS ke t1/t2/sl cells exactly "—" hone chahiye, "0.00" nahi.
  const tcs = s.cells[1] || [];
  check('null t1/t2/sl -> teeno cells exactly "—"',
    tcs[12] === '—' && tcs[13] === '—' && tcs[14] === '—',
    JSON.stringify(tcs.slice(12, 15)));
  check('koi bhi cell "0.00" nahi hai (null ko 0 nahi banaya)',
    !s.cells.flat().includes('0.00'), JSON.stringify(s.cells.flat().filter((c) => c === '0.00')));
  check('mlnote legend dikhta hai', s.mlnote.includes('ML+') && s.mlnote.includes('edge < 0'));
  check('sector dropdown facets se bana (counts ke saath)',
    s.sectors.includes('Banking (4)') && s.sectors.includes('IT (4)'), s.sectors.join(' | '));
  check('signal dropdown facets se bana', s.signals.includes('SELL (14)')
    && s.signals.includes('BUY (1)'), s.signals.join(' | '));
  check('"Sab" default option hai', s.sectors[0] === 'Sab' && s.signals[0] === 'Sab');
  check('error box chhupa hua hai', s.errShown === false);

  // ── 2. FRESH scan ─────────────────────────────────────────────────────
  s = await render(base({ age_label: '12 min', age_verdict: 'fresh' }));
  check('fresh scan -> banner green class me', s.staleCls.includes('b-fresh'), s.staleCls);
  check('fresh banner me age dikhta hai', s.stale.includes('12 min'));

  // ── 3. CLOCK SKEW ─────────────────────────────────────────────────────
  s = await render(base({ age_label: 'future timestamp', age_verdict: 'clock_skew' }));
  check('clock_skew -> banner red (b-bad)', s.staleCls.includes('b-bad'), s.staleCls);

  // ── 4. SCAN MISSING (ok:false) ────────────────────────────────────────
  s = await render({ ok: false, error: 'scan_results.json nahi mila. Scanner chalao: python nifty_scanner.py',
                     rows: [], total: 0, matched: 0, facets: { sectors: {}, signals: {} } });
  check('scan missing -> error box DIKHTA hai', s.errShown === true);
  check('scan missing -> asli message (generic nahi)',
    s.err.includes('nifty_scanner.py'), s.err.slice(0, 70));
  check('scan missing -> stale banner chhup jaata hai', s.staleShown === false);
  check('scan missing -> koi data row nahi', s.nrows === 1 && s.rows[0] === '—', JSON.stringify(s.rows));
  check('scan missing -> koi jhootha number NAHI', !NUM.test(s.tbodyHtml), s.tbodyHtml.slice(0, 60));

  // ── 5. FETCH FAIL ─────────────────────────────────────────────────────
  for (const mode of ['reject', 'http500']) {
    s = await render(base(), mode);
    check(`${mode}: error box dikhta hai`, s.errShown === true);
    check(`${mode}: "Load fail" message`, s.err.includes('Load fail'), s.err);
    check(`${mode}: koi jhootha number NAHI`, !NUM.test(s.tbodyHtml), s.tbodyHtml.slice(0, 60));
    check(`${mode}: count khaali ho jaata hai`, s.count === '', JSON.stringify(s.count));
    check(`${mode}: stale banner chhup jaata hai`, s.staleShown === false);
  }

  // ── 6. FILTERS se koi match nahi ──────────────────────────────────────
  s = await render(base({ matched: 0, rows: [] }));
  check('koi match nahi -> saaf message', s.rows[0].includes('match nahi hua'), s.rows[0]);
  check('koi match nahi -> count "0 / 30"', s.count.includes('0 / 30'), s.count);
  check('koi match nahi -> koi jhootha number NAHI', !NUM.test(s.tbodyHtml));
  check('koi match nahi -> facets dropdowns phir bhi bhare hain',
    s.sectors.includes('Banking (4)'), s.sectors.join(' | '));

  const passed = results.filter(Boolean).length;
  console.log('='.repeat(84));
  console.log(` ${passed} / ${results.length} checks passed`);
  console.log('='.repeat(84));
  process.exit(passed === results.length ? 0 : 1);
})();
