/* FIX-100: execute the actual browser payoff functions, not copied formulas. */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'Options.html'), 'utf8');
const start = html.indexOf('function payoff(');
const end = html.indexOf('function build()', start);
assert(start >= 0 && end > start);
const context = {};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
const cases = [
  {legs:[{opt:'CE',side:'buy',strike:100,premium:5}], mp:'Unlimited', ml:-5, be:[105]},
  {legs:[{opt:'CE',side:'sell',strike:100,premium:5}], mp:5, ml:'Unlimited loss', be:[105]},
  {legs:[{opt:'PE',side:'sell',strike:100,premium:5}], mp:5, ml:-95, be:[95]},
  {legs:[{opt:'CE',side:'buy',strike:100,premium:5},{opt:'CE',side:'sell',strike:110,premium:2}],mp:7,ml:-3,be:[103]},
];
for (const c of cases) {
  // A deliberately tiny chart range cannot determine global extrema.
  const r = context.stats(c.legs, [99,100,101]);
  assert.equal(r.mp,c.mp); assert.equal(r.ml,c.ml);
  assert.equal(JSON.stringify(r.be),JSON.stringify(c.be));
}
console.log('PASS: 4 actual-browser payoff cases; tails, bounded puts, spreads, roots beyond chart range');
