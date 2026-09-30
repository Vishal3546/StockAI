#!/usr/bin/env node
/**
 * tools/scan_js_hardcodes.js — Dashboard.html ka hardcoding scanner
 * ==============================================================================
 * app.py ke liye Python AST scanner chalaya tha; ye wahi kaam browser-side JS ke
 * liye karta hai (acorn parser, modern syntax support).
 *
 * Kya dhundhta hai:
 *   1. sv()/sf2() calls jo NUMERIC default dikhate hain — ye "fake number" pattern
 *      hai: value missing → UI me 50 / 0 / 1.0 chhapta hai, jo asli reading lagta hai
 *   2. numeric literals >= 100 (context ke saath — config vs magic number)
 *   3. hardcoded URLs / IPs / ports
 *   4. setInterval / setTimeout timings
 *   5. hardcoded uppercase strings (symbols, labels)
 *
 * Chalao:  node tools/scan_js_hardcodes.js           (repo root se)
 *          node tools/scan_js_hardcodes.js path.html
 */
const fs = require('fs');
const path = require('path');
const acorn = require('acorn');

const file = process.argv[2] || path.join(__dirname, '..', 'Dashboard.html');
const html = fs.readFileSync(file, 'utf8');

// saare inline <script> blocks (jisme src nahi hai) nikaalo
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const js = blocks.sort((a, b) => b.length - a.length)[0];
console.log(`file     : ${path.relative(process.cwd(), file)}`);
console.log(`inline JS: ${js.length} chars (${blocks.length} blocks)`);

const ast = acorn.parse(js, { ecmaVersion: 'latest', locations: true });
const lines = js.split('\n');
const lineOf = n => n.loc.start.line;
const srcOf = n => (lines[lineOf(n) - 1] || '').trim().slice(0, 100);

const nodes = [];
(function walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) return node.forEach(walk);
  if (typeof node.type === 'string') nodes.push(node);
  for (const k of Object.keys(node)) {
    if (k === 'loc' || k === 'start' || k === 'end') continue;
    walk(node[k]);
  }
})(ast);
console.log(`AST nodes: ${nodes.length}\n`);

const isLit = n => n && n.type === 'Literal';
const litVal = n => (isLit(n) ? n.value : undefined);

// ── 1. sv()/sf2() ke numeric/string defaults ────────────────────────────────
console.log('═══ 1. sv()/sf2() DEFAULT VALUES (UI me kya chhapta hai) ═══');
const calls = [];
for (const n of nodes) {
  if (n.type !== 'CallExpression') continue;
  const name = n.callee.type === 'Identifier' ? n.callee.name
             : (n.callee.type === 'MemberExpression' && n.callee.property ? n.callee.property.name : null);
  if ((name === 'sv' || name === 'sf2') && n.arguments.length >= 2) {
    calls.push({ name, def: litVal(n.arguments[1]), line: lineOf(n), src: srcOf(n) });
  }
}
const numDefaults = calls.filter(c => typeof c.def === 'number');
const strDefaults = calls.filter(c => typeof c.def === 'string');
console.log(`  total ${calls.length} calls | numeric defaults ${numDefaults.length} | string defaults ${strDefaults.length}`);
console.log(`  numeric default values: ${JSON.stringify(numDefaults.reduce((a, c) => (a[c.def] = (a[c.def] || 0) + 1, a), {}))}`);
console.log('');
const SUSPECT = v => v !== 0 && v !== '—' && v !== '?' && v !== 'N/A';
for (const c of numDefaults) {
  console.log(`   ${SUSPECT(c.def) ? '🔴 SUSPECT' : '   ok     '} L${String(c.line).padEnd(5)} ${c.name}(…, ${JSON.stringify(c.def)})   ${c.src}`);
}
console.log('');
for (const c of strDefaults) {
  console.log(`   ${SUSPECT(c.def) ? '🔴 SUSPECT' : '   ok     '} L${String(c.line).padEnd(5)} ${c.name}(…, ${JSON.stringify(c.def)})   ${c.src}`);
}

// ── 2. numeric literals >= 100 ──────────────────────────────────────────────
console.log('\n═══ 2. NUMERIC LITERALS >= 100 ═══');
const seen = new Map();
for (const n of nodes) {
  if (n.type === 'Literal' && typeof n.value === 'number' && Math.abs(n.value) >= 100) {
    const v = n.value;
    if (!seen.has(v)) seen.set(v, []);
    seen.get(v).push(lineOf(n));
  }
}
[...seen.entries()].sort((a, b) => Math.abs(b[0]) - Math.abs(a[0])).forEach(([v, ls]) => {
  console.log(`   ${String(v).padStart(10)} ×${String(ls.length).padEnd(3)} L${ls.slice(0, 6).join(', ')}`);
});

// ── 3. URLs / IPs / ports ───────────────────────────────────────────────────
console.log('\n═══ 3. HARDCODED URLs / HOSTS ═══');
const urls = new Set([...html.matchAll(/https?:\/\/[^\s'"<>)]+/g)].map(m => m[0]));
urls.forEach(u => console.log(`   ${u}`));
const ips = new Set([...html.matchAll(/\b\d{1,3}(?:\.\d{1,3}){3}\b/g)].map(m => m[0]));
console.log(`   IPs   : ${[...ips].join(', ') || '—'}`);
const ports = new Set([...html.matchAll(/(?:127\.0\.0\.1|localhost):(\d+)/g)].map(m => m[1]));
console.log(`   ports : ${[...ports].join(', ') || '—'}`);

// ── 4. timers ───────────────────────────────────────────────────────────────
console.log('\n═══ 4. TIMERS (setInterval/setTimeout) ═══');
for (const n of nodes) {
  if (n.type !== 'CallExpression' || !n.callee || n.callee.type !== 'Identifier') continue;
  if (!['setInterval', 'setTimeout'].includes(n.callee.name)) continue;
  const ms = litVal(n.arguments[1]);
  console.log(`   L${String(lineOf(n)).padEnd(5)} ${n.callee.name}(…, ${ms}ms)   ${srcOf(n)}`);
}

// ── 5. hardcoded uppercase strings ──────────────────────────────────────────
console.log('\n═══ 5. HARDCODED UPPERCASE STRINGS ═══');
const seenStr = new Set();
for (const n of nodes) {
  if (n.type === 'Literal' && typeof n.value === 'string' && /^[A-Z]{3,12}$/.test(n.value)) {
    if (seenStr.has(n.value)) continue;
    seenStr.add(n.value);
    console.log(`   L${String(lineOf(n)).padEnd(5)} '${n.value}'   ${srcOf(n)}`);
  }
}
console.log('');
