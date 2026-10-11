const fs=require('fs'),assert=require('assert'),{JSDOM}=require('jsdom');
const html=fs.readFileSync('ManualPlanner.html','utf8');
const result={quantity_estimate:42,mode:'intraday',direction:'long',binding_message:'estimate',capital_used_estimate:4200,stop_loss_estimate:100,target_net_estimate:200,net_reward_risk:2,executable_quantity:0,entry_fill_estimate:100,stop_fill_estimate:98,target_fill_estimate:105,fee_profile:'test',notes:['No orders'],fees:{entry:{total:5},stop_exit:{total:6},target_exit:{total:6}}};
let resolveFetch;const dom=new JSDOM(html,{url:'http://stockai.test',runScripts:'dangerously',beforeParse(w){w.AbortController=AbortController;w.fetch=()=>new Promise(r=>resolveFetch=r)}});const w=dom.window,d=w.document,$=id=>d.getElementById(id),tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
assert(!d.querySelector('script[src]'));assert($('productNote').textContent.includes('5-minute'));
$('mode').value='delivery';$('mode').dispatchEvent(new w.Event('input',{bubbles:true}));assert($('direction').options[1].disabled);assert.equal($('direction').value,'long');assert($('productNote').textContent.includes('daily'));
$('exchange').value='BSE';$('exchange').dispatchEvent(new w.Event('input',{bubbles:true}));assert($('exchangeNote').textContent.includes('blocked'));
$('planner').dispatchEvent(new w.Event('submit',{cancelable:true}));resolveFetch({ok:true,json:async()=>result});await tick();assert(!$('result').hidden);assert($('result').textContent.includes('42 whole shares'));assert($('result').textContent.includes('EXECUTION AUTHORIZED: NO'));
$('entry').dispatchEvent(new w.Event('input',{bubbles:true}));assert($('result').hidden);
$('planner').dispatchEvent(new w.Event('submit',{cancelable:true}));$('risk_rupees').dispatchEvent(new w.Event('input',{bubbles:true}));resolveFetch({ok:true,json:async()=>result});await tick();assert($('result').hidden,'stale asynchronous result hidden');
$('planner').dispatchEvent(new w.Event('submit',{cancelable:true}));resolveFetch({ok:false,json:async()=>({error:'<img src=x onerror=alert(1)>'})});await tick();assert(!$('error').querySelector('img'),'error inserted as text');
$('symbol').value='INFY';$('contextButton').click();$('mode').value='intraday';$('mode').dispatchEvent(new w.Event('input',{bubbles:true}));resolveFetch({ok:true,json:async()=>({symbol:'old'})});await tick();assert.equal($('context').textContent,'','old product context not shown');
console.log('FIX107 DOM: product separation, BSE block message, render, invalidation, stale responses, text-only errors PASS');w.close();
})().catch(e=>{console.error(e);w.close();process.exit(1)});
