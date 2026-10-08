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
        // FIX-93: trade plan panel
        planShown: d.getElementById('planbox').style.display === 'block',
        planbody: d.getElementById('planbody').textContent.replace(/\s+/g, ' ').trim(),
        planhtml: d.getElementById('planbody').innerHTML,
        plandisc: d.getElementById('plandisc').textContent.replace(/\s+/g, ' ').trim(),
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

  // ── FIX-83: strict-exchange miss (BSE maanga, sirf NSE available) ──────────
  // Server ab ok:false + available_exchange bhejta hai. Page ko red error ki
  // jagah AMBER warning dikhani chahiye jisme doosra exchange chunne ka rasta ho.
  const strictPayload = {
    ok: false, error: "BSE par 'RELIANCE' ka data abhi nahi mil raha.",
    requested_exchange: 'BSE', available_exchange: 'NSE',
    available_source: 'NSE Direct', exchange_mismatch: false,
  };
  s = await render(DEEP, strictPayload);
  check('strict miss: cards nahi dikhte (NSE data se BSE score nahi)',
    s.cardsShown === false);
  check('strict miss: amber warning DIKHTI hai', s.exwarnShown === true);
  check('strict miss: warning me doosra exchange available batata hai',
    s.exwarn.includes('NSE') && s.exwarn.includes('available'), s.exwarn.slice(0, 80));
  check('strict miss: red error box chhupa hai (duplicate nahi)', s.errShown === false,
    s.err.slice(0, 50));
  check('strict miss: warning ⚠️ se shuru hoti hai', s.exwarn.startsWith('⚠️'),
    s.exwarn.slice(0, 6));

  // strict miss ke BAAD normal request — warning stale nahi rehni chahiye
  s = await render(DEEP, base({ exchange_mismatch: false, exchange_note: null }));
  check('strict miss ke baad normal request par warning saaf hai',
    s.exwarnShown === false && s.cardsShown === true);

  // ── FIX-93: Trade Plan (2026 execution layer) ──────────────────────────────
  // Ye numbers /api/timeframe/RELIANCE?exch=NSE se ASLI me nikale gaye hain
  // (08-Oct-2026), invent nahi kiye.
  const PLAN = {
    ok: true, price: 1187.9, atr: 21.53, atr_pct: 1.81,
    stop_rule: '1.5 × ATR(14)', risk_per_share: 32.3, rr_min: 2.0,
    long: { stop: 1155.61, t1: 1209.43, t2: 1230.96, t3: 1252.48,
            rr_t1: 0.67, rr_t2: 1.33, rr_t3: 2.0, meets_1_2: true },
    short: { stop: 1220.19, t1: 1166.37, t2: 1144.84, t3: 1123.32 },
    rr_note: '1:2 tabhi milta hai jab T3 (3.0×ATR) tak hold kiya jaye — T1 par RR sirf 1:0.67 hai.',
    bb: { width: 10.31, width_rank_pct: 70.6, pctb: 0.25, n_bars: 252,
          squeeze: false, rule: 'width apni 252-bar history ke tightest 20% me' },
    hi52: { value: 1611.8, dist_pct: -26.3, in_swing_band: true,
            rule: '15-40% below 52W high' },
    mtf: { weekly_ema20: 1282.34, weekly_ema50: 1335.86, weekly_trend: 'DOWN',
           weekly_bars: 106, rule: 'weekly EMA20 vs EMA50' },
    confluence: { agree: 8, measured: 8, pct: 100.0, bull: 0, bear: 8,
                  side: 'BEARISH', label: 'STRONG' },
    adv: { value: 12610000, lakh: 126.1, meets_5lakh: true,
           rule: '20-day ADV vs 5 lakh' },
    notes: [],
    disclosure: ('Ye levels ATR se bane RISK-MANAGEMENT conventions hain — loss cap '
                 + 'karte hain, jeetne ki sambhavna NAHI badhate.'),
  };

  console.log('');
  console.log('─'.repeat(84));
  console.log(' FIX-93: Trade Plan panel (ATR stop · RR · squeeze · MTF · confluence · ADV)');
  console.log('─'.repeat(84));

  s = await render(DEEP, base({ plan: PLAN }));
  check('FIX-93: plan ok:true -> panel DIKHTA hai', s.planShown === true);
  check('FIX-93: ATR(14) value render hoti hai', s.planbody.includes('21.53'), '₹21.53');
  check('FIX-93: ATR price ka % bhi dikhta hai', s.planbody.includes('1.81'));
  check('FIX-93: stop rule dikhta hai (magic number nahi)',
    s.planbody.includes('1.5 × ATR(14)'), 'rule visible');
  check('FIX-93: LONG stop render hota hai', s.planbody.includes('1155.61'));
  check('FIX-93: T3 render hota hai', s.planbody.includes('1252.48'));
  check('FIX-93: SHORT stop bhi render hota hai', s.planbody.includes('1220.19'));
  check('FIX-93: teeno RR ratios dikhte hain',
    s.planbody.includes('1:0.67') && s.planbody.includes('1:1.33') && s.planbody.includes('1:2.00'));
  check('FIX-93: 1:2 rule ka verdict dikhta hai (✔)', s.planbody.includes('1:2 ✔'));
  check('FIX-93: rr_note dikhta hai — T1 par 1:2 nahi milta, ye chhupaya nahi gaya',
    s.planbody.includes('T1 par RR sirf'));
  check('FIX-93: BB width + percentile dikhta hai',
    s.planbody.includes('10.31') && s.planbody.includes('70.6'));
  check('FIX-93: squeeze=false -> "nahi" dikhta hai (jhootha HAAN nahi)',
    s.planbody.includes('Squeeze?') && !s.planbody.includes('HAAN — breakout setup'));
  check('FIX-93: %B render hota hai', s.planbody.includes('0.25'));
  check('FIX-93: 52W high value dikhti hai', s.planbody.includes('1611.80'));
  check('FIX-93: 52W se doori % me dikhti hai', s.planbody.includes('-26.30%'));
  check('FIX-93: swing band verdict HAAN (kyunki -26.3 band me hai)',
    s.planbody.includes('Swing band me?') && s.planbody.includes('HAAN'));
  check('FIX-93: weekly MTF trend DOWN dikhta hai', s.planbody.includes('DOWN'));
  check('FIX-93: weekly bars count dikhta hai (106)', s.planbody.includes('106'));
  check('FIX-93: confluence 8/8 BEARISH dikhta hai',
    s.planbody.includes('8/8 BEARISH') && s.planbody.includes('100.0%'));
  check('FIX-93: confluence ka bull/bear split dikhta hai',
    s.planbody.includes('0 bullish / 8 bearish'));
  check('FIX-93: ADV lakh me dikhta hai', s.planbody.includes('126.10 lakh'));
  check('FIX-93: 5-lakh liquidity verdict HAAN', s.planbody.includes('5 lakh se zyada?'));
  check('FIX-93: HONESTY — disclosure me "NAHI badhate" saaf likha hai',
    s.plandisc.includes('NAHI badhate'), s.plandisc.slice(0, 90));
  check('FIX-93: disclosure me accuracy/probability claim NAHI hai',
    !/accuracy|probability|guarantee/i.test(s.plandisc) || s.plandisc.includes('nahi'));
  check('FIX-93: sab 6 sections numbered hain',
    ['1 ·', '2 ·', '3 ·', '4 ·', '5 ·', '6 ·'].every((t) => s.planbody.includes(t)));

  // squeeze TRUE wala case — alag branch
  s = await render(DEEP, base({ plan: Object.assign({}, PLAN, {
    bb: Object.assign({}, PLAN.bb, { width_rank_pct: 12.5, squeeze: true }),
  })}));
  check('FIX-93: squeeze=true -> "HAAN — breakout setup" dikhta hai',
    s.planbody.includes('HAAN — breakout setup'));

  // weekly FLAT — tie ko DOWN kehna flat market ko bearish dikhana hoga
  s = await render(DEEP, base({ plan: Object.assign({}, PLAN, {
    mtf: Object.assign({}, PLAN.mtf, { weekly_trend: 'FLAT', weekly_ema20: 1300.00,
                                       weekly_ema50: 1300.00 }),
  })}));
  check('FIX-93: weekly FLAT render hota hai (DOWN nahi)', s.planbody.includes('FLAT'));
  check('FIX-93: FLAT par "trend confirm nahi" note dikhta hai',
    s.planbody.includes('trend confirm nahi'));
  check('FIX-93: FLAT amber me hai, red me nahi',
    s.planhtml.includes('var(--amber)">FLAT'));

  // FIX-95: prev session H/L/C + gap render
  s = await render(DEEP, base({ plan: Object.assign({}, PLAN, {
    prevday: { close: 1187.0, high: 1210.5, low: 1175.0, gap_pct: -0.8, gap: 'DOWN',
               above_prev_high: false, below_prev_low: false,
               rule: 'prev session H/L/C — intraday key S/R' },
  })}));
  check('FIX-95: prev high/low/close render hote hain',
    s.planbody.includes('1210.50') && s.planbody.includes('1175.00') && s.planbody.includes('1187.00'));
  check('FIX-95: gap DOWN red me render hota hai', s.planbody.includes('DOWN -0.80%'));
  check('FIX-95: "Previous session" section header dikhta hai',
    s.planbody.includes('Previous session'));
  check('FIX-95: breakout/breakdown flags render hote hain',
    s.planbody.includes('prev high ke upar') && s.planbody.includes('prev low ke neeche'));

  // prevday absent -> section nahi dikhta (jhootha S/R nahi)
  s = await render(DEEP, base({ plan: PLAN }));
  check('FIX-95: prevday absent -> section chhupa rehta hai',
    !s.planbody.includes('Previous session'));

  // notes (missing-data warnings) render hone chahiye — chhupne nahi chahiye
  s = await render(DEEP, base({ plan: Object.assign({}, PLAN, {
    notes: ['ATR(14) missing ya 0 — stop/target nahi banaye',
            'weekly bars sirf 12 — MTF ke liye kam se kam 50 chahiye'],
  })}));
  check('FIX-93: notes render hote hain (missing data chhupta nahi)',
    s.planbody.includes('ATR(14) missing') && s.planbody.includes('weekly bars sirf 12'));

  // XSS: note string me HTML ho to escape hona chahiye
  s = await render(DEEP, base({ plan: Object.assign({}, PLAN, {
    notes: ['<img src=x onerror=alert(1)>'],
  })}));
  check('FIX-93: note me HTML inject ho to escape hota hai (XSS safe)',
    !s.planhtml.includes('<img') && s.planbody.includes('<img src=x onerror=alert(1)>'));

  // plan absent / ok:false -> panel chhupna chahiye (jhootha plan nahi)
  s = await render(DEEP, base({}));
  check('FIX-93: plan field absent -> panel CHHUPA rehta hai', s.planShown === false);
  s = await render(DEEP, base({ plan: { ok: false, notes: ['kam bars'] } }));
  check('FIX-93: plan.ok=false -> panel chhupa (adhura plan nahi dikhaya)',
    s.planShown === false);

  // error / reject par panel chhupna chahiye
  s = await render(DEEP, base({ plan: PLAN }), 'http500');
  check('FIX-93: HTTP 500 par plan panel chhupta hai', s.planShown === false);
  s = await render(DEEP, base({ plan: PLAN }), 'reject');
  check('FIX-93: fetch reject par plan panel chhupta hai', s.planShown === false);

  // ── FIX-97: selection filters (beta / ATR% / gap) render ────────────────
  const FILT = { beta: 1.45, beta_corr: 0.88, beta_n: 60, beta_ok: true,
    beta_ideal_ok: true,
    beta_note: 'beta ≥ 0.8 — market ke saath move karta hai | ideal band 1.2–1.8 ke andar',
    atr_pct: 1.91, atr_pct_ok: true, gap_pct: -2.64, gap_skip: true,
    rules: { beta_min: 0.8, beta_ideal: [1.2, 1.8], beta_max: 2.0, atr_pct_min: 1.5,
             gap_skip_pct: 1.5, beta_bars: 60, beta_min_bars: 30 } };
  s = await render(DEEP, base({ plan: Object.assign({}, PLAN, { filters: FILT }) }));
  check('FIX-97: filters section render hota hai', s.planbody.includes('Selection filters'));
  check('FIX-97: beta value + corr dikhte hain',
    s.planbody.includes('1.45') && s.planbody.includes('corr 0.88'));
  check('FIX-97: beta note (ideal band) dikhta hai', s.planbody.includes('ideal band 1.2'));
  check('FIX-97: ATR% par HAAN + value', s.planbody.includes('HAAN (1.91%)'));
  check('FIX-97: gap > 1.5% par NAHI + skip advice',
    s.planbody.includes('NAHI (-2.64%)') && s.planbody.includes('skip'));
  check('FIX-97: thresholds (rules) dikhte hain, chhupe nahi',
    s.planbody.includes('≥0.8') && s.planbody.includes('>2 erratic')
    && s.planbody.includes('60 daily returns'));

  s = await render(DEEP, base({ plan: Object.assign({}, PLAN, { filters: Object.assign({}, FILT,
    { beta: null, beta_corr: null, beta_n: 0, beta_ok: null,
      beta_note: 'index fetch fail: Timeout' }) }) }));
  check('FIX-97: beta null -> "measure nahi hua" + reason (0.0/1.0 guess nahi)',
    s.planbody.includes('measure nahi hua') && s.planbody.includes('index fetch fail: Timeout'));
  const _brow = s.planhtml.split('Beta vs NIFTY')[1].split('ATR%')[0];
  check('FIX-97: beta null -> us row me HAAN/NAHI ka jhootha verdict nahi',
    _brow.length > 0 && !/HAAN|NAHI/.test(_brow));

  s = await render(DEEP, base({ plan: PLAN }));
  check('FIX-97: filters absent -> section chhupa rehta hai',
    !s.planbody.includes('Selection filters'));

  // ── FIX-96: Trade Journal (panel hamesha dikhta hai, plan se independent) ──
  function journalDom(payload, initial) {
    return new Promise((resolve) => {
      const dom = new JSDOM(html, {
        runScripts: 'dangerously', pretendToBeVisual: true, url: PLAIN,
        beforeParse(w) {
          w.fetch = () => Promise.resolve({ ok: true, status: 200,
            json: () => Promise.resolve(initial || { ok: true, items: [], stats: {}, corrupt: false }) });
        },
      });
      setTimeout(() => {
        dom.window.renderJournal(payload);
        const d = dom.window.document;
        resolve({ stats: d.getElementById('jstats').textContent.replace(/\s+/g, ' ').trim(),
                  statsHtml: d.getElementById('jstats').innerHTML,
                  table: d.getElementById('jtable').textContent.replace(/\s+/g, ' ').trim(),
                  tableHtml: d.getElementById('jtable').innerHTML });
      }, 80);
    });
  }

  const JSTATS0 = { closed: 0, open: 0, win_rate: null, avg_win_r: null, avg_loss_r: null,
    expectancy_r: null, profit_factor: null, worst_r: null, stop_breaches: 0, net_pnl: 0,
    confidence: 'anecdote',
    disclosure: ('0 closed trades — 20 se kam, isliye ye ANECDOTE hai: ek-do trade poora '
      + 'average hila dete hain. Pattern mat nikalo. Ye aapke khud log kiye trades ka '
      + 'record hai — kisi model/score ki accuracy nahi, aur na hi koi prediction.') };

  let js = await journalDom({ ok: true, items: [], stats: JSTATS0, corrupt: false });
  check('FIX-96: khali journal par honest empty-state (jhootha 0% nahi)',
    js.table.includes('Abhi koi trade log nahi hua'));
  check('FIX-96: n=0 par metrics "—" (0.00R fake nahi)',
    js.stats.includes('Expectancy') && js.stats.includes('—'));
  check('FIX-96: ANECDOTE disclosure dikhta hai', js.stats.includes('ANECDOTE'));
  check('FIX-96: "aapke khud log kiye trades" saaf likha',
    js.stats.includes('aapke khud log kiye trades'));

  const J2 = { ok: true, corrupt: false, items: [
    { id: 'a1', symbol: 'RELIANCE', exchange: 'NSE', side: 'LONG', entry: 1000, stop: 950,
      qty: 10, exit: 1100, r: 2.0, pnl_gross: 1000, pnl_net: 977.5, risk_rupees: 500,
      setup: 'prev-low bounce', logged_at: '2026-10-08 14:10' },
    { id: 'b2', symbol: 'TCS', exchange: 'BSE', side: 'SHORT', entry: 2000, stop: 2050,
      qty: 5, exit: null, r: null, pnl_net: null, risk_rupees: 250, setup: '',
      logged_at: '2026-10-08 14:20' }],
    stats: { closed: 1, open: 1, wins: 1, losses: 0, win_rate: 100.0, avg_win_r: 2.0,
      avg_loss_r: null, expectancy_r: 2.0, profit_factor: null, worst_r: 2.0,
      stop_breaches: 0, net_pnl: 977.5, confidence: 'anecdote',
      disclosure: '1 closed trades — ANECDOTE' } };
  js = await journalDom(J2);
  check('FIX-96: dono trades table me dikhte hain',
    js.table.includes('RELIANCE') && js.table.includes('TCS'));
  check('FIX-96: closed trade ka R "+2.00R"', js.table.includes('+2.00R'));
  check('FIX-96: open trade ka R "—" (0 nahi)', js.table.includes('—'));
  check('FIX-96: open row par exit input + close button',
    js.tableHtml.includes('id="x_b2"') && js.tableHtml.includes('data-close="b2"'));
  check('FIX-96: closed row par close button NAHI (dobara close nahi)',
    !js.tableHtml.includes('data-close="a1"'));
  check('FIX-96: delete button har row par',
    (js.tableHtml.match(/data-del=/g) || []).length === 2);
  check('FIX-96: stats me closed/open alag dikhte hain',
    js.stats.includes('Closed trades') && js.stats.includes('Open'));
  check('FIX-96: net P&L ₹ me (cost ke baad)', js.stats.includes('₹'));
  check('FIX-96: expectancy +2.00R stats me', js.stats.includes('+2.00R'));

  js = await journalDom({ ok: true, items: [], corrupt: false,
    stats: Object.assign({}, JSTATS0, { stop_breaches: 2 }) });
  check('FIX-96: −1R se bure trades par execution-leak warning',
    js.stats.includes('execution leak'));

  js = await journalDom({ ok: true, items: [], corrupt: true, stats: JSTATS0 });
  check('FIX-96: corrupt file par RED warning (chup-chaap khaali nahi)',
    js.stats.includes('corrupt hai') && js.statsHtml.includes('var(--red)'));
  check('FIX-96: corrupt par write-block bataya jaata hai', js.stats.includes('write block'));

  js = await journalDom({ ok: true, items: [], corrupt: false, dropped_rows: 3, stats: JSTATS0 });
  check('FIX-97a: dropped_rows par amber warning (chup-chaap rows nahi jaate)',
    js.stats.includes('3 row trade_journal.json') && js.statsHtml.includes('var(--amber)'));

  js = await journalDom({ ok: true, corrupt: false, items: [
    { id: 'x1', symbol: '<img src=x onerror=alert(1)>', exchange: 'NSE', side: 'LONG',
      entry: 100, stop: 90, qty: 1, exit: null, r: null, pnl_net: null, risk_rupees: 10,
      setup: '<b>bold</b>', logged_at: 'now' }], stats: JSTATS0 });
  check('FIX-96: symbol/setup me HTML inject ho to escape (XSS safe)',
    !js.tableHtml.includes('<img') && js.tableHtml.includes('&lt;img')
    && js.tableHtml.includes('&lt;b&gt;')
    && js.table.includes('<img src=x onerror=alert(1)>'));

  // journalFill — plan se prefill (BEARISH plan → SHORT + short leg ka stop)
  await new Promise((resolve) => {
    const dom = new JSDOM(html, {
      runScripts: 'dangerously', pretendToBeVisual: true, url: PLAIN,
      beforeParse(w) { w.fetch = () => Promise.resolve({ ok: true, status: 200,
        json: () => Promise.resolve({ ok: true, items: [], stats: {}, corrupt: false }) }); },
    });
    setTimeout(() => {
      const d = dom.window.document;
      dom.window.renderPlan(PLAN);
      d.getElementById('sym').value = 'reliance';
      d.getElementById('jfill').click();
      check('FIX-96: jfill par side confluence se (BEARISH→SHORT)',
        d.getElementById('j_side').value === 'SHORT', d.getElementById('j_side').value);
      check('FIX-96: entry plan.price se bharta hai',
        d.getElementById('j_entry').value === String(PLAN.price), d.getElementById('j_entry').value);
      check('FIX-96: stop SHORT leg ke stop se bharta hai',
        d.getElementById('j_stop').value === String(PLAN.short.stop), d.getElementById('j_stop').value);
      check('FIX-96: symbol uppercase ho jaata hai',
        d.getElementById('j_sym').value === 'RELIANCE', d.getElementById('j_sym').value);
      check('FIX-96: setup me confluence count (8/8)',
        d.getElementById('j_setup').value.includes('8/8'), d.getElementById('j_setup').value);
      resolve();
    }, 80);
  });

  // no external CDN (sandbox preview me bhi chalna chahiye)
  check('FIX-93: Timeframes.html me koi external CDN/script nahi',
    !/(https?:)?\/\/(?!localhost)[^"'\s]*\.(js|css)/.test(html.replace(/<!--[\s\S]*?-->/g, '')));

  const passed = results.filter(Boolean).length;
  console.log('='.repeat(84));
  console.log(` ${passed} / ${results.length} checks passed`);
  console.log('='.repeat(84));
  process.exit(passed === results.length ? 0 : 1);
})();
