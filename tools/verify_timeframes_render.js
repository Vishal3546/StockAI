#!/usr/bin/env node
/**
 * tools/verify_timeframes_render.js — FIX-77 browser-level test
 * ============================================================================
 * Timeframes.html ka ASLI JavaScript jsdom me chalata hai. Verify karta hai:
 *
 *   • chaaro horizon (intraday/swing/longterm/master) sahi render hote hain
 *   • "basis" (N/M indicators measured) dikhta hai — chhupta nahi
 *   • score ke hisaab se sahi colour class lagti hai
 *   • fund_data None ho to "N/A" dikhta hai, 0.00 nahi
 *   • error par jhootha score NAHI aata
 *   • page khulte hi auto-fetch NAHI hota (sirf ?sym= deep-link par)
 *
 * Expected strings pehle jsdom me ACTUAL output dekh kar likhi gayi hain.
 *
 * Zaroorat: npm i jsdom   (na ho to SKIP, exit 2)
 * Chalao:   node tools/verify_timeframes_render.js
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.log('⏭  SKIP — jsdom installed nahi (npm i jsdom)'); process.exit(2); }

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'Timeframes.html'), 'utf8');

const results = [];
const check = (n, ok, d = '') => {
  results.push(ok);
  console.log(`  ${ok ? '✅' : '❌'} ${n}` + (d ? ` — ${d}` : ''));
};

// RELIANCE ke ASLI scores (06-Oct-2026) — /api/timeframe aur /api/stock dono se
// verify kiye gaye hain (tools/verify_timeframes.py section E).
const KPI = {
  intraday: { score: 23, action: 'SELL', basis: '7/7 indicators measured' },
  swing: { score: 43, action: 'HOLD', basis: '6/6 indicators measured' },
  longterm: { score: 39, action: 'WATCH', basis: '5/6 indicators measured' },
  master: { score: 35, action: 'NEUTRAL', basis: '18/19 indicators measured' },
};

function base(over = {}) {
  return Object.assign({
    ok: true, cached: false, symbol: 'RELIANCE', source: 'Yahoo Finance (NSE)',
    bars: 501, last_session: '2026-10-06', price: 1218.0, kpi: KPI,
    fund_data: { pe_val: 21.711231, roe_val: null, debt_val: 46.278 },
    fund_note: 'FUND NOTE', note: 'THE NOTE',
  }, over);
}

/** url + mode: 'ok' | 'reject' | 'http500' | 'apierr' */
function render(url, payload, mode = 'ok') {
  return new Promise((resolve) => {
    const dom = new JSDOM(html, {
      runScripts: 'dangerously', pretendToBeVisual: true, url,
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
      const snap = {
        cardsShown: d.getElementById('cards').style.display === 'grid',
        fundShown: d.getElementById('fund').style.display === 'block',
        meta: d.getElementById('meta').textContent.replace(/\s+/g, ' ').trim(),
        err: d.getElementById('err').textContent.replace(/\s+/g, ' ').trim(),
        errShown: d.getElementById('err').style.display !== 'none',
        fundtxt: d.getElementById('fundtxt').textContent.replace(/\s+/g, ' ').trim(),
        exwarn: d.getElementById('exwarn').textContent.replace(/\s+/g, ' ').trim(),
        exwarnShown: d.getElementById('exwarn').style.display !== 'none',
        h: {}, html: {},
      };
      for (const k of ['intraday', 'swing', 'longterm', 'master']) {
        const e = d.getElementById('h_' + k);
        snap.h[k] = e.textContent.replace(/\s+/g, ' ').trim();
        snap.html[k] = e.innerHTML;
      }
      dom.window.close();
      resolve(snap);
    }, 220);
  });
}

const DEEP = 'http://localhost/timeframes?sym=RELIANCE';
const PLAIN = 'http://localhost/timeframes';

(async () => {
  console.log('='.repeat(84));
  console.log(' Timeframes.html — real JS jsdom me chala kar render assert (FIX-77)');
  console.log('='.repeat(84));

  // ── 1. DEEP LINK (?sym=) ──────────────────────────────────────────────
  let s = await render(DEEP, base());
  check('deep link par cards dikhte hain', s.cardsShown === true);
  check('intraday 23 SELL + basis render hua',
    s.h.intraday === '23SELL7/7 indicators measured', s.h.intraday);
  check('swing 43 HOLD render hua', s.h.swing === '43HOLD6/6 indicators measured', s.h.swing);
  check('longterm 39 WATCH + "5/6" (partial) render hua',
    s.h.longterm === '39WATCH5/6 indicators measured', s.h.longterm);
  check('master 35 NEUTRAL + "18/19" render hua',
    s.h.master === '35NEUTRAL18/19 indicators measured', s.h.master);
  check('score 23 (<=35) red class me', /class="sc red"/.test(s.html.intraday));
  check('score 43 (mid) amber class me', /class="sc amber"/.test(s.html.swing));
  check('action pill par bhi wahi colour class', /class="act red"/.test(s.html.intraday));
  check('bar ki width score ke barabar hai (23%)',
    /width:23%/.test(s.html.intraday), (s.html.intraday.match(/width:\d+%/) || [])[0]);
  check('meta me symbol + price + bars + source', s.meta.includes('RELIANCE')
    && s.meta.includes('₹1218.00') && s.meta.includes('501 daily bars')
    && s.meta.includes('Yahoo Finance (NSE)'), s.meta.slice(0, 100));
  check('meta me last session dikhta hai', s.meta.includes('2026-10-06'));
  check('fundamentals panel dikhta hai', s.fundShown === true);
  check('P/E render hua', s.fundtxt.includes('21.71'), s.fundtxt.slice(0, 90));
  check('ROE null -> "N/A" (0.00 NAHI)', s.fundtxt.includes('ROE N/A'),
    s.fundtxt.slice(0, 110));
  check('fund_note + note dikhte hain', s.fundtxt.includes('FUND NOTE')
    && s.fundtxt.includes('THE NOTE'));
  check('error box chhupa hua hai', s.errShown === false);

  // ── 2. BINA ?sym= — auto-fetch NAHI hona chahiye ──────────────────────
  s = await render(PLAIN, base());
  check('plain URL par cards NAHI dikhte (auto-fetch nahi)', s.cardsShown === false);
  check('plain URL par prompt dikhta hai', s.meta.includes('Dekho'), s.meta);
  check('plain URL par fundamentals panel nahi', s.fundShown === false);

  // ── 3. CACHED response ────────────────────────────────────────────────
  s = await render(DEEP, base({ cached: true }));
  check('cached hone par user ko bataya jaata hai', s.meta.includes('cache se'), s.meta.slice(-60));

  // ── 4. API ne ok:false diya (data usable nahi) ────────────────────────
  s = await render(DEEP, { ok: false, error: 'ZZZZ ka data usable nahi: data frame khali hai' });
  check('api error -> err box dikhta hai', s.errShown === true);
  check('api error -> asli message (generic nahi)',
    s.err.includes('data frame khali hai'), s.err);
  check('api error -> cards NAHI dikhte (jhootha score nahi)', s.cardsShown === false);
  check('api error -> koi number render nahi hua',
    !/\d/.test(s.h.intraday + s.h.master), JSON.stringify(s.h.intraday));
  check('api error -> fundamentals panel nahi', s.fundShown === false);

  // ── 5. FETCH FAIL ─────────────────────────────────────────────────────
  for (const mode of ['reject', 'http500']) {
    s = await render(DEEP, base(), mode);
    check(`${mode}: err box dikhta hai`, s.errShown === true);
    check(`${mode}: "Load fail" message`, s.err.includes('Load fail'), s.err);
    check(`${mode}: cards nahi (jhootha score nahi)`, s.cardsShown === false);
    check(`${mode}: meta khaali`, s.meta === '', JSON.stringify(s.meta));
  }

  // ── 6. HIGH score -> green ────────────────────────────────────────────
  const hi = JSON.parse(JSON.stringify(KPI));
  hi.intraday = { score: 78, action: 'BUY', basis: '7/7 indicators measured' };
  hi.longterm = { score: 90, action: 'INVEST', basis: '6/6 indicators measured' };
  s = await render(DEEP, base({ kpi: hi }));
  check('score 78 -> green class', /class="sc green"/.test(s.html.intraday));
  check('score 90 -> green class', /class="sc green"/.test(s.html.longterm));
  check('score 78 -> bar 78%', /width:78%/.test(s.html.intraday));
  check('BUY action render hua', s.h.intraday.includes('BUY'), s.h.intraday);

  // ── 7. kpi missing -> "data nahi", crash nahi ─────────────────────────
  s = await render(DEEP, base({ kpi: {} }));
  check('kpi khaali -> "data nahi" (crash nahi)', s.h.intraday.includes('data nahi'), s.h.intraday);
  check('kpi khaali -> koi jhootha score nahi', !/\d/.test(s.h.master), s.h.master);

  // ── 8. EXCHANGE MISMATCH (FIX-80) — BSE maanga, NSE mila ──────────────
  const NOTE = 'Aapne BSE maanga tha, par BSE ka koi fresh source nahi mila — ye data NSE se aaya hai.';
  s = await render(DEEP, base({ exchange_requested: 'BSE', exchange_actual: 'NSE',
                                exchange_mismatch: true, exchange_note: NOTE }));
  check('mismatch par banner DIKHTA hai', s.exwarnShown === true);
  check('banner me poora note hai', s.exwarn.includes('BSE maanga tha')
    && s.exwarn.includes('NSE se aaya hai'), s.exwarn.slice(0, 90));
  check('banner me warning sign hai', s.exwarn.startsWith('⚠️'), s.exwarn.slice(0, 6));
  check('mismatch par cards phir bhi dikhte hain (data valid hai)', s.cardsShown === true);

  // ── 9. NO mismatch — banner chhupna chahiye ───────────────────────────
  s = await render(DEEP, base({ exchange_requested: 'NSE', exchange_actual: 'NSE',
                                exchange_mismatch: false, exchange_note: null }));
  check('match par banner chhupa hai', s.exwarnShown === false);

  // ── 10. fields absent (purana response) — crash nahi, banner nahi ─────
  s = await render(DEEP, base());
  check('exchange fields na hon to bhi crash nahi', s.cardsShown === true);
  check('exchange fields na hon to banner nahi', s.exwarnShown === false);

  // ── 11. error paths par banner chhupna chahiye (stale warning na rahe) ─
  s = await render(DEEP, { ok: false, error: 'data usable nahi', exchange_mismatch: true,
                           exchange_note: NOTE });
  check('api error par banner chhupta hai', s.exwarnShown === false);
  for (const mode of ['reject', 'http500']) {
    s = await render(DEEP, base({ exchange_mismatch: true, exchange_note: NOTE }), mode);
    check(`${mode}: banner chhupta hai`, s.exwarnShown === false);
  }

  const passed = results.filter(Boolean).length;
  console.log('='.repeat(84));
  console.log(` ${passed} / ${results.length} checks passed`);
  console.log('='.repeat(84));
  process.exit(passed === results.length ? 0 : 1);
})();
