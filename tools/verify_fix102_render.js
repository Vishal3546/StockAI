// Run the actual Dashboard functions with controlled async browser boundaries.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync(path.join(__dirname,'..','Dashboard.html'),'utf8');
const stream=html.slice(html.indexOf('let liveSource = null;'),html.indexOf('// FIX-25:'));
const loader=html.slice(html.indexOf('let stockRequestSeq = 0;'),html.indexOf('// Main Advanced Dashboard Render Engine'));
const manual=html.slice(html.indexOf('async function manualRefresh()'),html.indexOf("document.addEventListener('keydown'"));
function setup(){
 const sources=[],updates=[],intervals=[],nodes={},renders=[],banners=[];
 const node=id=>nodes[id]||(nodes[id]={style:{},classList:{contains:()=>false,add:()=>{},remove:()=>{}}});
 const c={API:'',activeExchange:'NSE',activeSymbol:'TCS',liveTickerInterval:null,
 EventSource:function(url){this.url=url;this.closed=false;this.close=()=>this.closed=true;sources.push(this)},
 updatePriceDOM:(...args)=>updates.push(args),clearInterval:()=>{},
 setInterval:fn=>{intervals.push(fn);return intervals.length},
 setTimeout:(fn,ms)=>{if(ms<1000)queueMicrotask(fn);return 1},clearTimeout:()=>{},AbortController,
 symInput:{value:'TCS'},dropdown:{classList:{remove:()=>{}}},document:{getElementById:node},
 showBanner:(...args)=>banners.push(args),render:d=>renders.push(d),console,
 apiFetch:async()=>({json:async()=>({error:'Unavailable',requested_exchange:'BSE'})})};
 vm.createContext(c);vm.runInContext(stream+'\n'+loader+'\n'+manual,c);
 return {c,sources,updates,intervals,nodes,renders,banners,run:s=>vm.runInContext(s,c)};
}
const tick=(symbol='TCS',exchange='NSE')=>({symbol,exchange,price:100,source:exchange,is_realtime:false});
const event=t=>({data:JSON.stringify(t)});
const tfHTML=fs.readFileSync(path.join(__dirname,'..','Timeframes.html'),'utf8');
const tfLoader=tfHTML.slice(tfHTML.indexOf('let timeframeGeneration = 0;'),tfHTML.indexOf("document.getElementById('exch').addEventListener('change'"));
function setupTF(){
 const nodes={},plans=[];
 const node=id=>nodes[id]||(nodes[id]={style:{},value:id==='sym'?'TCS':id==='exch'?'NSE':'',innerHTML:'',textContent:''});
 const c={document:{getElementById:node},LASTPLAN:null,renderPlan:p=>plans.push(p),card:()=>'',fetch:async()=>({ok:true,status:200,json:async()=>({})})};
 vm.createContext(c);vm.runInContext(tfLoader,c);
 return {c,nodes,plans,run:()=>vm.runInContext('load()',c),node};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
let count=0;
async function test(name,fn){await fn();count++;console.log('PASS '+name)}
(async()=>{
 await test('Failed BSE selection closes prior NSE stream and hides old analysis',async()=>{
  const e=setup();e.run('startLiveTicker("TCS")');e.c.activeExchange='BSE';await e.run('loadStock()');
  e.sources[0].onmessage(event(tick()));assert(e.sources[0].closed);assert.equal(e.updates.length,0);assert.equal(e.nodes.mainContent.style.visibility,'hidden');
 });
 await test('Old error callback cannot close a newer stream',()=>{
  const e=setup();e.run('startLiveTicker("TCS")');e.c.activeExchange='BSE';e.run('startLiveTicker("TCS")');e.sources[0].onerror();assert.equal(e.sources[1].closed,false);assert.equal(e.intervals.length,0);
 });
 await test('Same-symbol wrong-exchange and missing identity ticks rejected',()=>{
  const e=setup();e.c.activeExchange='BSE';e.run('startLiveTicker("TCS")');
  e.sources[0].onmessage(event(tick('TCS','NSE')));e.sources[0].onmessage(event({price:100}));assert.equal(e.updates.length,0);
 });
 await test('Correct exchange tick accepted, wrong symbol rejected',()=>{
  const e=setup();e.run('startLiveTicker("TCS")');e.sources[0].onmessage(event(tick('JIOFIN')));e.sources[0].onmessage(event(tick()));assert.equal(e.updates.length,1);
 });
 await test('Same-symbol/exchange previous generation rejected',()=>{
  const e=setup();e.run('startLiveTicker("TCS")');e.run('startLiveTicker("TCS")');e.sources[0].onmessage(event(tick()));assert.equal(e.updates.length,0);
 });
 await test('Late poll response discarded after exchange switch',async()=>{
  const e=setup();let resolve;let requested;
  e.c.apiFetch=url=>{requested=url;return new Promise(r=>resolve=r)};
  e.run('startLiveTicker("TCS")');e.sources[0].onerror();const pending=e.intervals[0]();
  e.c.activeExchange='BSE';e.run('startLiveTicker("TCS")');resolve({json:async()=>tick()});await pending;
  assert.equal(requested,'/api/quote/TCS?ex=NSE');assert.equal(e.updates.length,0);
 });
 await test('Late manual refresh discarded',async()=>{
  const e=setup();let resolve;e.c.apiFetch=()=>new Promise(r=>resolve=r);
  e.run('startLiveTicker("TCS")');const pending=e.run('manualRefresh()');e.c.activeExchange='BSE';e.run('startLiveTicker("TCS")');resolve({json:async()=>tick()});await pending;assert.equal(e.updates.length,0);
 });
 await test('Wrong-exchange stock response never rendered',async()=>{
  const e=setup();e.c.activeExchange='BSE';e.c.apiFetch=async()=>({json:async()=>({symbol:'TCS',frame_exchange:'NSE'})});await e.run('loadStock()');assert.equal(e.renders.length,0);assert.equal(e.sources.length,0);
 });
 await test('Correct stock response renders and starts matching stream',async()=>{
  const e=setup();e.c.activeExchange='BSE';e.c.apiFetch=async()=>({json:async()=>({symbol:'TCS',frame_exchange:'BSE'})});await e.run('loadStock()');assert.equal(e.renders.length,1);assert.equal(e.sources[0].url,'/api/stream/TCS?ex=BSE');assert.equal(e.nodes.mainContent.style.visibility,'visible');
 });
 await test('Superseded analysis cannot render or start old stream',async()=>{
  const e=setup();const pending=[];e.c.apiFetch=()=>new Promise(r=>pending.push(r));
  const first=e.run('loadStock()');e.c.activeExchange='BSE';const second=e.run('loadStock()');
  pending[1]({json:async()=>({symbol:'TCS',frame_exchange:'BSE'})});await second;
  pending[0]({json:async()=>({symbol:'TCS',frame_exchange:'NSE'})});await first;
  assert.equal(e.renders.length,1);assert.equal(e.renders[0].frame_exchange,'BSE');assert.equal(e.sources.length,1);
 });
 await test('Historical-only result explicitly warns',async()=>{
  const e=setup();e.c.apiFetch=async()=>({json:async()=>({symbol:'TCS',frame_exchange:'NSE',historical_only:true,analysis_session:'2026-10-08',expected_session:'2026-10-09'})});await e.run('loadStock()');assert(e.banners.some(x=>x[0].includes('HISTORICAL ONLY')));assert(html.includes('id="historyWarning"'));
 });
 await test('Timeframes discards superseded NSE result after BSE succeeds',async()=>{
  const e=setupTF(),pending=[];e.c.fetch=()=>new Promise(resolve=>pending.push(resolve));
  e.run();e.node('exch').value='BSE';e.run();
  pending[1]({ok:true,status:200,json:async()=>({symbol:'TCS',exchange_requested:'BSE',exchange_actual:'BSE',source:'TradingView Direct (BSE)',bars:30,kpi:{}})});await flush();
  pending[0]({ok:true,status:200,json:async()=>({symbol:'TCS',exchange_requested:'NSE',exchange_actual:'NSE',source:'NSE',bars:30,kpi:{}})});await flush();
  assert(e.node('meta').innerHTML.includes('BSE'));assert.equal(e.plans.length,1);
 });
 await test('Timeframes rejects mismatched identity before rendering plan',async()=>{
  const e=setupTF();e.node('exch').value='BSE';e.c.fetch=async()=>({ok:true,status:200,json:async()=>({symbol:'TCS',exchange_requested:'BSE',exchange_actual:'NSE',kpi:{}})});e.run();await flush();assert.equal(e.node('cards').style.display,'none');assert.equal(e.plans.length,0);
 });
 await test('Timeframes stale history has no current trade plan',async()=>{
  const e=setupTF();e.c.fetch=async()=>({ok:true,status:200,json:async()=>({symbol:'TCS',exchange_requested:'NSE',exchange_actual:'NSE',historical_only:true,last_session:'2026-10-08',expected_session:'2026-10-09',kpi:{},plan:{ok:true}})});e.run();await flush();assert.equal(e.plans[0],null);assert(e.node('exwarn').textContent.includes('HISTORICAL ONLY'));
 });
 console.log(`${count}/${count} FIX-102 browser race/identity cases passed`);
})().catch(e=>{console.error(e);process.exitCode=1});
