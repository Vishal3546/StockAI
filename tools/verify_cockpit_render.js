#!/usr/bin/env node
/**
 * tools/verify_cockpit_render.js — FIX-71 browser-level test
 * ============================================================================
 * Cockpit.html ka ASLI JavaScript jsdom me chalata hai aur rendered DOM par
 * assert karta hai. Ye isliye zaroori hai kyunki Python verifier sirf
 * market_cockpit.py (backend) aur HTML string dekhta hai — page ka JS branch
 * (clock-skew, stale badge, 503 empty-state, '—' fallback) pehle KABHI execute
 * nahi hua tha. Ab hota hai.
 *
 * Zaroorat: npm i jsdom   (na ho to SKIP, exit 2 — repo ka purana pattern)
 * Chalao:   node tools/verify_cockpit_render.js
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.log('⏭  SKIP — jsdom installed nahi (npm i jsdom)'); process.exit(2); }

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'Cockpit.html'), 'utf8');

const results = [];
const check = (n, ok, d = '') => {
  results.push(ok);
  console.log(`  ${ok ? '✅' : '❌'} ${n}` + (d ? ` — ${d}` : ''));
};

const BASE = {
  source: 'NSE India (allIndices)',
  timestamp: '06-Oct-2026 10:24',
  age_minutes: 3,
  breadth: { advances: 6579, declines: 2909, unchanged: 82, total: 9570, ad_ratio: 2.2616, pct_up: 68.75 },
  indices: [
    { name: 'NIFTY 50', last: 22653.05, pct: 0.43, change: 97.1 },
    { name: 'NIFTY BANK', last: 54994.8, pct: 0.51, change: 280.25 },
  ],
  vix: { name: 'INDIA VIX', last: 14.17, pct: -4.09, change: -0.6 },
  sectors: [
    { name: 'NIFTY METAL', last: 12624.3, pct: 1.01 },
    { name: 'NIFTY IT', last: 28073.75, pct: -0.85 },
    { name: 'NIFTY PHARMA', last: 26251.75, pct: null },
  ],
  movers: { top: [{ name: 'NIFTY METAL', pct: 1.01 }], bottom: [{ name: 'NIFTY IT', pct: -0.85 }] },
};

const ERR = { error: 'market data unavailable (NSE block / off-market) — residential IP + market hours par try karo' };

/** Page ko real jsdom me chalao, fetch ko stub karke, phir DOM ka snapshot lo. */
function render(payload, status = 200) {
  return new Promise((resolve) => {
    const dom = new JSDOM(html, {
      runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/',
      beforeParse(w) {
        w.fetch = () => Promise.resolve({
          ok: status === 200, status,
          json: () => Promise.resolve(status === 200 ? payload : ERR),
        });
      },
    });
    setTimeout(() => {
      const d = dom.window.document;
      // ⚠️ body.textContent me <script> ka SOURCE bhi aata hai — usse "clock skew"
      //    jaisi strings JS code se match ho jaati hain aur test jhootha pass/fail
      //    deta hai. Isliye scripts hata kar hi visible text lete hain.
      const vis = d.body.cloneNode(true);
      vis.querySelectorAll('script').forEach((n) => n.remove());
      const snap = {
        text: vis.textContent,
        metaHTML: d.getElementById('meta').innerHTML,
        breadthDisplay: d.getElementById('breadthPanel').style.display,
        heatTiles: d.getElementById('heat').querySelectorAll('.tile').length,
        // JS tile ka naam 'NIFTY ' prefix hata kar dikhata hai, isliye DOM se hi
        // per-tile padhte hain (string grep se ye pakda nahi jaata).
        heat: [...d.querySelectorAll('#heat .tile')].map((t) => ({
          n: t.querySelector('.n').textContent,
          p: t.querySelector('.p').textContent.trim(),
          cls: t.querySelector('.p').className.trim(),
          bg: t.getAttribute('style') || '',
        })),
        cardsHTML: d.getElementById('cards').innerHTML,
        heatHTML: d.getElementById('heat').innerHTML,
        breadthNote: d.getElementById('breadthNote').textContent,
        breadthSegs: d.getElementById('breadthBar').querySelectorAll('div').length,
        moverRows: d.getElementById('movers').querySelectorAll('tr').length,
      };
      dom.window.close();
      resolve(snap);
    }, 60);
  });
}

(async () => {
  console.log('='.repeat(84));
  console.log(' Cockpit.html — real JS jsdom me chala kar render assert');
  console.log('='.repeat(84));

  // ── 1. normal payload ────────────────────────────────────────────────────
  let s = await render(BASE);
  check('fresh data par "3 min purana" dikhta hai', s.text.includes('3 min purana'), s.metaHTML.slice(0, 120));
  check('fresh data par clock-skew warning NAHI aati', !s.text.includes('clock skew'));
  check('breadth panel visible hai', s.breadthDisplay !== 'none', `display="${s.breadthDisplay}"`);
  check('breadth bar me 3 segment (adv/unchanged/dec)', s.breadthSegs === 3, String(s.breadthSegs));
  check('A/D ratio + %up note me dikhte hain',
    s.breadthNote.includes('2.2616') && s.breadthNote.includes('68.75% stocks up'), s.breadthNote);
  check('NIFTY 50 card 22,653.05 ke saath render hua',
    s.cardsHTML.includes('NIFTY 50') && s.text.includes('22,653.05'));
  check('pct +0.43% green class ke saath', /class="d green">\+0\.43%/.test(s.cardsHTML), s.cardsHTML.slice(0, 200));
  check('INDIA VIX card alag se dikhta hai',
    s.cardsHTML.includes('INDIA VIX') && s.text.includes('14.17'));
  check('VIX negative change par red class', /class="d red">/.test(s.cardsHTML));
  check('heatmap me 3 tiles', s.heatTiles === 3, String(s.heatTiles));
  const pharma = s.heat.find((t) => t.n === 'PHARMA');
  check("pct null wala sector '—' dikhata hai (fake 0 nahi)",
    !!pharma && pharma.p === '—' && pharma.cls === 'p',
    pharma ? JSON.stringify(pharma) : 'tile nahi mila');
  check('null pct tile ka background neutral grey hai (red/green nahi)',
    !!pharma && pharma.bg.includes('#1c2440'), pharma && pharma.bg);
  const metal = s.heat.find((t) => t.n === 'METAL');
  check('positive sector ka tile GREEN background + green class leta hai',
    !!metal && metal.bg.includes('34,197,94') && metal.cls === 'p green',
    metal && `${metal.cls} | ${metal.bg}`);
  const it = s.heat.find((t) => t.n === 'IT');
  check('negative sector ka tile RED background + red class leta hai',
    !!it && it.bg.includes('239,68,68') && it.cls === 'p red',
    it && `${it.cls} | ${it.bg}`);
  check('tile me NIFTY prefix strip hota hai (compact heatmap)',
    s.heat.length > 0 && s.heat.every((t) => !t.n.startsWith('NIFTY')),
    s.heat.map((t) => t.n).join(','));
  check('movers table me top+bottom row hai', s.moverRows === 1 && s.text.includes('NIFTY METAL') && s.text.includes('NIFTY IT'),
    `rows=${s.moverRows}`);
  check('rendered page par koi SENSEX/BSE index card nahi',
    !/SENSEX|BSE\s?\d/.test(s.cardsHTML) && !/SENSEX/.test(s.heatHTML));
  check('SEBI disclosure page par maujood hai', s.text.includes('not SEBI registered'));
  check('"no validated edge" disclaimer maujood hai', /validated edge/i.test(s.text));
  check('NSE-only source note maujood hai', s.text.includes('SENSEX/BSE indices is page par nahi'));

  // ── 2. CLOCK SKEW (age negative) — pehle ye branch kabhi chala hi nahi tha ──
  s = await render({ ...BASE, age_minutes: -328 });
  check('clock skew par warning dikhti hai', s.text.includes('clock skew'), s.metaHTML.slice(0, 160));
  check('clock skew me sahi magnitude + "peeche" (direction galat nahi)',
    s.text.includes('328 min peeche'), s.metaHTML.slice(0, 160));
  check('clock skew par jhootha "purana" nahi dikhta', !s.text.includes('min purana'));
  check('clock skew warning amber (stale) class me hai', /class="stale"/.test(s.metaHTML));
  check('clock skew me bhi data render hota hai (page khaali nahi)', s.heatTiles === 3);

  // ── 3. age unknown / stale ───────────────────────────────────────────────
  s = await render({ ...BASE, age_minutes: null });
  check('age null par "age unknown" (jhoothi age nahi)', s.text.includes('age unknown'), s.metaHTML.slice(0, 120));

  s = await render({ ...BASE, age_minutes: 45 });
  check('45 min purana → stale (amber) badge', s.text.includes('45 min purana') && /class="stale"/.test(s.metaHTML));

  s = await render({ ...BASE, age_minutes: 5 });
  check('5 min par stale badge NAHI (threshold 15)', !/class="stale"/.test(s.metaHTML), s.metaHTML.slice(0, 120));

  // ── 4. NSE block → 503 ───────────────────────────────────────────────────
  s = await render(BASE, 503);
  check('503 par honest error dikhta hai', s.text.includes('residential IP'), s.metaHTML.slice(0, 160));
  check('503 par cards khaali (koi fake index nahi)', s.cardsHTML === '', s.cardsHTML.slice(0, 80));
  check('503 par heatmap khaali', s.heatHTML === '');
  check('503 par breadth panel chhup jaata hai', s.breadthDisplay === 'none', `display="${s.breadthDisplay}"`);

  // ── 5. breadth missing / sectors khaali ──────────────────────────────────
  s = await render({ ...BASE, breadth: null });
  check('breadth null par panel chhupa rehta hai', s.breadthDisplay === 'none');

  s = await render({ ...BASE, sectors: [], movers: { top: [], bottom: [] } });
  check('khaali sectors par "sectoral data nahi mila"', s.text.includes('sectoral data nahi mila'));
  check('khaali movers par placeholder row', s.moverRows === 1 && s.text.includes('—'));

  s = await render({ ...BASE, indices: [], vix: null });
  check('khaali indices par "koi index nahi mila"', s.text.includes('koi index nahi mila'));

  const p = results.filter(Boolean).length;
  console.log('='.repeat(84));
  console.log(` ${p} / ${results.length} checks passed`);
  console.log('='.repeat(84));
  process.exit(p === results.length ? 0 : 1);
})();
