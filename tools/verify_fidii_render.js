#!/usr/bin/env node
/**
 * tools/verify_fidii_render.js — FIX-75 browser-level test
 * ============================================================================
 * Fidii.html ka ASLI JavaScript jsdom me chalata hai aur rendered DOM par assert
 * karta hai. tools/verify_fidii.py parse/maths pin karta hai; ye verify karta
 * hai ki PAGE wo numbers sahi DHIKHATA hai — aur khaas taur par:
 *
 *   • NSE fail ho to jhootha number NAHI aata (red message aata hai)
 *   • cached data "live" bankar nahi dikhta
 *   • dono scopes ke labels alag-alag dikhte hain (mix nahi hote)
 *   • integrity/dates mismatch par warning dikhti hai
 *
 * Expected strings pehle jsdom me ACTUAL output dekh kar likhi gayi hain
 * (guess nahi) — e.g. en-IN grouping "15,674.61" aur sign "+5,181.62".
 *
 * Zaroorat: npm i jsdom   (na ho to SKIP, exit 2 — repo ka purana pattern)
 * Chalao:   node tools/verify_fidii_render.js
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.log('⏭  SKIP — jsdom installed nahi (npm i jsdom)'); process.exit(2); }

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'Fidii.html'), 'utf8');

const results = [];
const check = (n, ok, d = '') => {
  results.push(ok);
  console.log(`  ${ok ? '✅' : '❌'} ${n}` + (d ? ` — ${d}` : ''));
};

// ── 05-Oct-2026 ka ASLI NSE data (verify_fidii.py me pin + Groww/niftytrader
//    se cross-checked). Yahan sirf RENDER test hota hai.
const ALLREC = [{ category: 'FII/FPI', date: '05-Oct-2026', buy: 15674.61, sell: 20373.75, net: -4699.14, net_matches: true },
                { category: 'DII',     date: '05-Oct-2026', buy: 20492.93, sell: 15311.31, net: 5181.62,  net_matches: true }];
const NSEREC = [{ category: 'FII/FPI', date: '05-Oct-2026', buy: 14888.13, sell: 18981.09, net: -4092.96, net_matches: true },
                { category: 'DII',     date: '05-Oct-2026', buy: 18076.94, sell: 13198.78, net: 4878.16,  net_matches: true }];

function base(over = {}) {
  return Object.assign({
    ok: true, as_of: '05-Oct-2026', live: true, fetched_at: 't',
    source: 'NSE India /reports/fii-dii', note: 'NSE ye endpoint sirf latest trading day deta hai',
    dates_match: true, history_scope: 'all', streak_fii: -7,
    history: [{ date: '05-Oct-2026', fii_net: '-4699.14', dii_net: '5181.62', total_net: '482.48' }],
    all_exchanges: { label: 'NSE, BSE and MSEI - Capital Market segment', records: ALLREC,
      fii_net: -4699.14, dii_net: 5181.62, total_net: 482.48, fii_gross: 36048.36,
      dii_gross: 35804.24, dii_absorption_pct: 110.27, integrity_ok: true, date: '05-Oct-2026' },
    nse_only: { label: 'NSE only - Capital Market segment', records: NSEREC,
      fii_net: -4092.96, dii_net: 4878.16, total_net: 785.2, fii_gross: 33869.22,
      dii_gross: 31275.72, dii_absorption_pct: 119.18, integrity_ok: true, date: '05-Oct-2026' },
  }, over);
}

const IDS = ['badge', 'asof', 'all_lab', 'nse_lab', 'all_out', 'nse_out', 'hist', 'hist_scope', 'src'];

/** mode: 'ok' | 'reject' (fetch throw) | 'http500' */
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
      const snap = {};
      for (const id of IDS) {
        const e = d.getElementById(id);
        snap[id] = e ? e.textContent.replace(/\s+/g, ' ').trim() : '<<MISSING>>';
        snap[id + '_cls'] = e ? e.className : '';
        snap[id + '_html'] = e ? e.innerHTML : '';
      }
      dom.window.close();
      resolve(snap);
    }, 120);
  });
}

// Money-jaisa number: sign + digits + 2 decimal (en-IN grouping ke saath ya bina).
// Plain /[0-9]/ galat tha — "Load fail: HTTP 500" me 500 match ho jaata tha.
const NUM = /[-+]?\d[\d,]*\.\d{2}/;

(async () => {
  console.log('='.repeat(84));
  console.log(' Fidii.html — real JS jsdom me chala kar render assert (FIX-75)');
  console.log('='.repeat(84));

  // ── 1. NORMAL (live) ─────────────────────────────────────────────────────
  let s = await render(base());
  check('badge = live', s.badge === 'live' && s.badge_cls.includes('b-live'), s.badge_cls);
  check('as-of date dikhta hai', s.asof.includes('Data date: 05-Oct-2026'), s.asof);
  check('ALL scope label = NSE ka heading',
    s.all_lab === 'NSE, BSE and MSEI - Capital Market segment', s.all_lab);
  check('NSE scope label = NSE ka heading', s.nse_lab === 'NSE only - Capital Market segment', s.nse_lab);
  check('dono labels ALAG hain (mix nahi)', s.all_lab !== s.nse_lab);
  check('FII net -4,699.14 render hua', s.all_out.includes('-4,699.14'), s.all_out.slice(0, 120));
  check('DII net +5,181.62 render hua', s.all_out.includes('+5,181.62'));
  check('total net +482.48 render hua', s.all_out.includes('+482.48'));
  check('FII buy 15,674.61 render hua', s.all_out.includes('15,674.61'));
  check('DII buy 20,492.93 render hua', s.all_out.includes('20,492.93'));
  check('absorption 110.27% render hua', s.all_out.includes('110.27%'));
  check('NSE panel ke APNE numbers hain (-4,092.96)', s.nse_out.includes('-4,092.96'));
  check('NSE panel me ALL wala number leak nahi hua', !s.nse_out.includes('-4,699.14'));
  check('ALL panel me NSE wala number leak nahi hua', !s.all_out.includes('-4,092.96'));
  // NOTE: className textContent ka part NAHI hota — isliye innerHTML par assert.
  // (pehle wala check `s.all_out.includes('red')` tha, jo kabhi pass nahi ho sakta tha)
  check('negative red class me hai', /class="red"/.test(s.all_out_html),
    (s.all_out_html.match(/class="(red|green)"/g) || []).join(','));
  check('positive green class me hai', /class="green"/.test(s.all_out_html));
  check('red aur green DONO lage hain (colour coding kaam kar rahi)',
    /class="red"/.test(s.all_out_html) && /class="green"/.test(s.all_out_html));
  check('history row render hua', s.hist.includes('05-Oct-2026') && s.hist.includes('+482.48'));
  check('streak count dikhta hai (7 din selling)', s.hist.includes('7 din selling'), s.hist.slice(-90));
  check('streak par "sirf count" disclaimer hai',
    s.hist.includes('sirf count') && s.hist.includes('koi verdict nahi'));
  check('history scope label dikhta hai', s.hist_scope.includes('NSE + BSE + MSEI'), s.hist_scope);
  check('source + note dikhte hain', s.src.includes('NSE India /reports/fii-dii')
    && s.src.includes('latest trading day'));

  // ── 2. CACHED (NSE down, purana data) — "live" bankar nahi dikhna chahiye ─
  s = await render(base({ live: false, fetched_at: 'old' }));
  check('NSE down -> badge "cached", "live" NAHI', s.badge === 'cached'
    && s.badge_cls.includes('b-cache'), s.badge + ' / ' + s.badge_cls);
  check('cached par user ko bataya jaata hai', s.asof.includes('purana cache'), s.asof);
  check('cached par bhi numbers dikhte hain (khaali nahi)', s.all_out.includes('-4,699.14'));

  // ── 3. UNAVAILABLE (koi data hi nahi) ───────────────────────────────────
  const empty = base({ ok: false, as_of: null, live: false, dates_match: null,
    history: [], streak_fii: 0,
    all_exchanges: { label: 'NSE, BSE and MSEI - Capital Market segment', records: [],
      fii_net: null, dii_net: null, total_net: null, fii_gross: null, dii_gross: null,
      dii_absorption_pct: null, integrity_ok: null, date: null },
    nse_only: { label: 'NSE only - Capital Market segment', records: [],
      fii_net: null, dii_net: null, total_net: null, fii_gross: null, dii_gross: null,
      dii_absorption_pct: null, integrity_ok: null, date: null } });
  s = await render(empty);
  check('koi data nahi -> badge "unavailable"', s.badge === 'unavailable'
    && s.badge_cls.includes('b-off'), s.badge);
  check('koi data nahi -> saaf message', s.asof.includes('Koi data nahi mila'), s.asof);
  check('koi data nahi -> panel me "Data nahi mila"', s.all_out.includes('Data nahi mila'));
  check('koi data nahi -> koi jhootha number NAHI (dono panels)',
    !NUM.test(s.all_out) && !NUM.test(s.nse_out), JSON.stringify([s.all_out, s.nse_out]));
  check('koi data nahi -> "0.00" jaisa fake zero nahi',
    !s.all_out.includes('0.00') && !s.nse_out.includes('0.00'));
  check('history khaali -> collector command dikhata hai',
    s.hist.includes('collect_fidii_daily.py'), s.hist);

  // ── 4. FETCH FAIL (network / 500) ──────────────────────────────────────
  for (const mode of ['reject', 'http500']) {
    s = await render(base(), mode);
    check(`${mode}: asof red error class me`, s.asof_cls === 'err', s.asof_cls);
    check(`${mode}: error message dikhta hai`, s.asof.includes('Load fail'), s.asof);
    check(`${mode}: dono panels red + message`, s.all_out_cls === 'err' && s.nse_out_cls === 'err'
      && s.all_out.includes('Load fail') && s.nse_out.includes('Load fail'));
    check(`${mode}: koi jhootha number NAHI`,
      !NUM.test(s.all_out) && !NUM.test(s.nse_out), JSON.stringify([s.all_out, s.nse_out]));
    check(`${mode}: badge "error"`, s.badge === 'error' && s.badge_cls.includes('b-off'), s.badge);
  }

  // ── 5. INTEGRITY FAIL (NSE ka buy-sell != net) ─────────────────────────
  const badInt = base();
  badInt.all_exchanges = Object.assign({}, badInt.all_exchanges, { integrity_ok: false });
  s = await render(badInt);
  check('integrity_ok false -> warning dikhti hai',
    s.all_out.includes('match nahi kar raha'), s.all_out.slice(-160));
  check('integrity warning sirf usi panel me hai (NSE panel clean)',
    !s.nse_out.includes('match nahi kar raha'));

  // ── 6. DATES MISMATCH (dono tables alag din ke) ────────────────────────
  s = await render(base({ dates_match: false }));
  check('dates_match false -> "compare mat karo" warning',
    s.asof.includes('compare mat karo'), s.asof);

  // ── 7. FII BUYING ho to absorption ratio nahi dikhna chahiye ───────────
  const buying = base();
  buying.all_exchanges = Object.assign({}, buying.all_exchanges, {
    fii_net: 1200.5, dii_net: -300.25, total_net: 900.25, dii_absorption_pct: null });
  s = await render(buying);
  check('FII buying -> absorption ratio dikhta hi nahi',
    !s.all_out.includes('absorb kiya'), s.all_out.slice(0, 200));
  check('FII buying -> positive number green me', s.all_out.includes('+1,200.50'));

  const passed = results.filter(Boolean).length;
  console.log('='.repeat(84));
  console.log(` ${passed} / ${results.length} checks passed`);
  console.log('='.repeat(84));
  process.exit(passed === results.length ? 0 : 1);
})();
