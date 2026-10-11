const fs=require('fs'),assert=require('assert'),{JSDOM}=require('jsdom');let pending=[];
const dom=new JSDOM(fs.readFileSync('Screener.html','utf8'),{url:'http://stockai.test',runScripts:'dangerously',beforeParse(w){w.AbortController=AbortController;w.fetch=url=>url.startsWith('/api/deep-analysis')?new Promise(resolve=>pending.push({url,resolve})):Promise.resolve({ok:true,json:async()=>({ok:false,error:'fixture'})})}});
const w=dom.window,d=w.document,tick=()=>new Promise(r=>setImmediate(r));
function payload(sym){return {symbol:sym,exchange:'NSE',note:'daily only',data:{as_of:'2026-10-09',source:'fixture',adjustment:'unverified',input_hash:'x'},ml:{available:false,error:'Insufficient history'},risk:{available:false,error:'unavailable'},sector:{available:false},fundamentals:{pe:0,roe:0,mcap:null,verification:'unverified'}}}
(async()=>{
 await tick();w.render({ok:true,facets:{sectors:{'<img src=x>':1},signals:{WATCH:1}},rows:[{symbol:'<img src=x>',sector:'<script>',signal:'WATCH',exchange:'NSE',source:'<img src=x>',signal_score:50}],matched:1,total:1,sort:{key:'signal_score',order:'desc'}});
 assert.equal(d.querySelectorAll('img').length,0);assert(d.querySelector('button.select-stock'));assert(d.getElementById('tbody').textContent.includes('<img src=x>'));
 w.selectDeep('INFY','NSE','x');w.selectDeep('TCS','NSE','x');assert.equal(pending.length,2);
 pending[1].resolve({ok:true,json:async()=>payload('TCS')});await tick();assert(d.getElementById('deep-status').textContent.includes('TCS'));assert(d.getElementById('deep-result').textContent.includes('P/E 0.00'));assert(d.getElementById('deep-result').textContent.includes('no default beta'));
 pending[0].resolve({ok:true,json:async()=>payload('INFY')});await tick();assert(d.getElementById('deep-status').textContent.includes('TCS'),'late INFY cannot overwrite TCS');
 w.selectDeep('INFY','NSE','x');pending[2].resolve({ok:false,json:async()=>({error:'<img src=x> server busy'})});await tick();assert.equal(d.querySelectorAll('img').length,0);assert(d.getElementById('deep-status').textContent.includes('server busy'));assert.equal(d.getElementById('deep-result').textContent,'');
 console.log('FIX108 DOM: same-page selection, text-safe rendering, stale response isolation, zero fundamentals, null benchmark and errors PASS');w.close();
})().catch(e=>{console.error(e);w.close();process.exit(1)});
