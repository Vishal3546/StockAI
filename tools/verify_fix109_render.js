const fs=require('fs'),assert=require('assert'),{JSDOM}=require('jsdom');let lookups=[];
const dom=new JSDOM(fs.readFileSync('Screener.html','utf8'),{url:'http://stockai.test',runScripts:'dangerously',beforeParse(w){w.AbortController=AbortController;w.fetch=url=>{
 if(url.includes('q='))return new Promise(resolve=>lookups.push({url,resolve}));
 if(url.endsWith('/status'))return Promise.resolve({ok:true,json:async()=>({state:'IDLE',processed:0,total:0,remaining:0})});
 return Promise.resolve({ok:true,json:async()=>({ok:false,error:'fixture'})});};}});
const w=dom.window,d=w.document,$=id=>d.getElementById(id),tick=()=>new Promise(r=>setImmediate(r)),delay=ms=>new Promise(r=>setTimeout(r,ms));
function data(signals){return {ok:true,total:30,matched:30,age_label:'0 min',as_of:'2026-10-11T10:12:33+05:30',sort:{key:'signal_score',order:'desc'},facets:{sectors:{Banking:30},signals},rows:[{symbol:'KOTAKBANK',exchange:'NSE',sector:'Banking',signal:'STRONG BUY',signal_score:64,data_as_of:'2026-10-09'}]};}
(async()=>{
 await tick();$('scan_scope').value='COHORT';w.render(data({BUY:1,SELL:10,'STRONG SELL':16,WATCH:3}));
 w.render(data({'STRONG BUY':1,SELL:14,'STRONG SELL':11,WATCH:4}));
 assert($('f_signal').textContent.includes('STRONG BUY (1)'));assert(!$('f_signal').querySelector('option[value="BUY"]'));assert($('f_signal').textContent.includes('SELL (14)'));
 $('f_signal').value='SELL';w.render(data({SELL:2,WATCH:28}));assert.equal($('f_signal').value,'SELL','valid selected filter preserved');
 assert($('stale').textContent.includes('Scan generated:'));assert($('stale').textContent.includes('2026-10-09'));assert($('stale').textContent.includes('Use Refresh'));assert(!$('stale').textContent.includes('python nifty_scanner.py'));
 w.rstat({autorefresh:true,max_age_hours:12});assert($('rstat').textContent.includes('NOT a repeating timer'));
 $('stock_lookup').value='TOR';$('stock_lookup').dispatchEvent(new w.Event('input'));await delay(280);
 $('stock_lookup').value='EM';$('stock_lookup').dispatchEvent(new w.Event('input'));await delay(280);
 const a=lookups.shift(),b=lookups.shift();assert(a.url.includes('q=TOR'));assert(b.url.includes('q=EM'));
 b.resolve({ok:true,json:async()=>({matched:2,rows:[{symbol:'EMIL',exchange:'BSE',name:'Electronic Mart'},{symbol:'EMIL',exchange:'NSE',name:'<img src=x>'}]})});await tick();
 a.resolve({ok:true,json:async()=>({matched:1,rows:[{symbol:'TORNTPHARM',exchange:'NSE',name:'Torrent'}]})});await tick();
 assert.equal($('stock_suggestions').children.length,2);assert(!$('stock_suggestions').textContent.includes('TORNTPHARM'));assert.equal(d.querySelectorAll('img').length,0);
 let selection;w.selectDeep=(...args)=>selection=args;$('stock_suggestions').firstChild.click();assert.deepEqual(selection.slice(0,2),['EMIL','BSE']);assert.equal($('stock_lookup').getAttribute('aria-expanded'),'false');
 $('f_signal').value='';$('scan_scope').value='ALL';w.render({...data({UNRATED:7344}),scope:'ALL',offset:50,limit:50,matched:7344,total:7344,states:{PENDING:7344}});
 assert.equal($('page_prev').disabled,false);assert.equal($('page_next').disabled,false);assert($('page_label').textContent.includes('51–100 of 7344'));
 let late;w.fetch=()=>new Promise(resolve=>late=resolve);$('scan_scope').value='COHORT';w.cohortRefresh();const oldRefresh=late;
 $('scan_scope').value='ALL';$('scan_scope').onchange();const label=$('f_refresh').textContent;
 oldRefresh({ok:true,json:async()=>({status:{running:true}})});await tick();assert.equal($('f_refresh').textContent,label);assert.equal($('f_refresh').disabled,false);
 // Late cohort responses must not change full-market controls.
 console.log('FIX109 DOM PASS: refreshed facets/counts, selection preservation, clocks, startup wording, all-listing pagination, dual-exchange lookup, stale lookup isolation, safe names and exact exchange selection');w.close();
})().catch(e=>{console.error(e);w.close();process.exit(1)});
