#!/usr/bin/env node
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..');
const contract=JSON.parse(fs.readFileSync(path.join(root,'contracts/evidence-replay/expected-results.json'),'utf8'));
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b), sorted=a=>[...a].sort();
function demand(ok,message){if(!ok)throw new Error(message)}

// Execute the actual page script with abort-ignoring deferred responses. This is
// a scheduling adversary, not a graph oracle; all successful data is unknown.
async function unitRace(){
 const html=fs.readFileSync(path.join(root,'scripts/ui/evidence_replay.html'),'utf8');
 const code=html.match(/<script>([\s\S]*?)<\/script>/)[1],elements={},pending=[],events={},timeouts=new Map();let sequence=0;
 function node(){return {dataset:{},children:[],attributes:{},textContent:'',value:'',style:{},append(n){this.children.push(n)},replaceChildren(){this.children=[]},setAttribute(k,v){this.attributes[k]=String(v)},getAttribute(k){return this.attributes[k]},addEventListener(){},querySelectorAll(){return []}}}
 const context=vm.createContext({document:{getElementById:id=>elements[id]??=(node()),createElement:()=>node(),createElementNS:()=>node()},window:{addEventListener:(k,v)=>events[k]=v},URLSearchParams,AbortController,console,setTimeout:fn=>{timeouts.set(++sequence,fn);return sequence},clearTimeout:id=>timeouts.delete(id),fetch:(url,options)=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject}))});
 vm.runInContext(code,context);
 const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
 const reply=(i,frame)=>pending[i].resolve({ok:true,json:async()=>({frame,mode:'mainline',generic_unknown:true,nodes:[],edges:[],sources:[]})});
 vm.runInContext('seek(1)',context);demand(pending[0].options.signal.aborted,'seek did not abort old request');reply(1,1);await flush();reply(0,0);await flush();
 demand(elements.replay.dataset.selection==='1:mainline'&&elements.replay.dataset.state==='ready','late success overwrote newer selection');
 vm.runInContext('seek(2);seek(1)',context);reply(3,1);await flush();pending[2].reject(new Error('late failure'));await flush();demand(elements.replay.dataset.state==='ready','late failure cleared newer result');
 vm.runInContext("$('play').onclick()",context);reply(4,1);await flush();demand(timeouts.size===1,'play should schedule exactly one timer');vm.runInContext('pause()',context);demand(timeouts.size===0,'pause left a timer');
 vm.runInContext('seek(3)',context);events.pagehide();demand(pending[5].options.signal.aborted,'unload did not abort');reply(5,3);await flush();demand(elements.replay.dataset.state==='disposed'&&timeouts.size===0,'unloaded result or timer survived');
 return {status:'PASS',stale_response:true,stale_failure:true,pause_clears_timer:true,unload_cancels:true};
}
if(process.argv[2]==='--unit-race'){unitRace().then(v=>console.log(JSON.stringify(v))).catch(e=>{console.error(e);process.exitCode=1});}
else { main().catch(e=>{console.error(e);process.exitCode=1}); }

async function main(){
 const [baseUrl,mode,reportPath,screenshots]=process.argv.slice(2),target=new URL(baseUrl);
 demand(target.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(target.hostname)&&!target.username&&!target.password&&['existing','unknown','missing'].includes(mode)&&reportPath&&screenshots,'loopback browser arguments required');
 let chromium;for(const name of ['/tools/node/lib/node_modules/playwright-core','playwright-core','playwright']){try{({chromium}=require(name));break}catch(e){if(e.code!=='MODULE_NOT_FOUND')throw e}}
 demand(chromium,'Playwright required; existing /tools or project-local installation');
 const report={status:'RUNNING',mode,assertions:[],assertion_count:0,flags:{},screenshots:[],desktop:null,mobile:null,accessibility:null,requests:[]};
 const check=(name,passed,detail=null)=>{report.assertions.push({name,passed:!!passed,detail});demand(passed,name+': '+JSON.stringify(detail))};
 let browser;
 try {
  for(const pin of contract.pinned_sources)check('pinned oracle '+pin.path,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,pin.path))).digest('hex')===pin.sha256);
  const executable=process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||process.env.CHROMIUM_PATH||'/usr/bin/chromium';
  const launch={headless:true,args:['--no-sandbox','--disable-dev-shm-usage']};if(fs.existsSync(executable))launch.executablePath=executable;
  browser=await chromium.launch(launch);
  const errors=[];
  for(const [viewportName,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:390,height:844}]]){
   const context=await browser.newContext({viewport,reducedMotion:'reduce'}),page=await context.newPage();
   page.on('pageerror',e=>errors.push(String(e)));
   await page.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==target.origin)return route.abort('blockedbyclient');report.requests.push({method:route.request().method(),path:u.pathname,query:u.search});return route.continue()});
   const ready=async(frame,expectedState='ready')=>{await page.waitForFunction(({frame,state})=>document.getElementById('replay')?.dataset.selection===frame+':'+(frame<4?'mainline':'hypothetical')&&document.getElementById('replay').dataset.state===state&&document.getElementById('results').getAttribute('aria-busy')==='false',{frame,state:expectedState},{timeout:12000})};
   const select=async frame=>{const selectedMode=frame<4?'mainline':'hypothetical';if(await page.locator('#mode').inputValue()!==selectedMode)await page.locator('#mode').selectOption(selectedMode);await page.locator(`#timeline button[data-frame="${frame}"]`).click();await ready(frame)};
   const capture=async()=>({text:await page.locator('body').innerText(),aria:await page.locator('body').ariaSnapshot(),edges:await page.locator('#graph .edge').evaluateAll(ns=>ns.map(n=>n.dataset.key).sort()),nodes:await page.locator('#graph .node').evaluateAll(ns=>ns.map(n=>n.dataset.key).sort())});
   await page.goto(target.origin+'/time-machine');await ready(0);
   const snapshots=[];
   for(const spec of contract.frames){
    await select(spec.frame);const snap=await capture();snapshots.push(snap);
    const unknown=mode==='unknown'||(mode==='missing'&&spec.mode==='hypothetical')||spec.unknown;
    if(unknown){check(viewportName+' frame '+spec.frame+' generic unknown',snap.edges.length===0&&snap.nodes.length===0&&snap.text.includes('Unknown — insufficient visible evidence.'));check('unknown has no identity hints',!/(SRC-|DEC-OPS-|SOP-OPS-|ROLE-|REGISTER-OPS-|CONVEYOR-C4)/.test(snap.text+snap.aria))}
    else {
     const edges=sorted(spec.edges.map(k=>contract.edge_definitions[k]));
     const nodes=sorted(new Set(edges.flatMap(k=>{const [a,,b]=k.split('|');return [a,b]})));
     check(viewportName+' frame '+spec.frame+' exact typed live edges',same(snap.edges,edges),snap.edges);
     check(viewportName+' frame '+spec.frame+' exact nodes',same(snap.nodes,nodes));
     check('list mirrors every visible node and edge',same(await page.locator('#evidence-list li').evaluateAll(ns=>ns.map(n=>n.dataset.key).sort()),sorted([...edges,...nodes])));
     for(const s of contract.sources.filter(s=>spec.sources.includes(s.id)))check('exact visible source '+s.id,snap.text.includes(s.id)&&snap.text.includes(s.title)&&snap.text.includes('Actual observed_at: '+s.observed_at));
     if(spec.frame===5)check('hypothetical keeps actual and assumed time separate',snap.text.includes('Hypothetical assumed observation: '+spec.assumed_observed_at+' (not source truth)'));
     if(spec.record)check('record stays open unresolved',snap.text.includes('open-unresolved'));
    }
    const hidden=contract.sources.filter(s=>unknown||!spec.sources.includes(s.id));
    const fullDom=await page.content();for(const s of hidden)check('no future evidence in DOM or accessibility '+spec.frame+' '+s.id,!fullDom.includes(s.id)&&!fullDom.includes(s.title)&&!snap.aria.includes(s.id)&&!snap.aria.includes(s.title));
    if(!spec.record||unknown)check('no early decision-record existence hints',!fullDom.includes(contract.record.id)&&!fullDom.includes(contract.record.title));
    check('stacked viewport without page overflow',(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)));
   }
   report[viewportName]=snapshots;report.flags[viewportName]=true;
   if(viewportName==='desktop')report.accessibility=snapshots.map(s=>s.aria);
   if(mode!=='existing'){
    report.flags.unknown=true;
    await select(mode==='missing'?4:3);
   }else {
    report.flags['six-live-frames']=true;report.flags['evidence-list']=true;report.flags['no-future-dom']=true;
    await select(3);
    const nodes=page.locator('#graph .node'),edges=page.locator('#graph .edge');
    await nodes.first().focus();await page.keyboard.press('ArrowRight');const nodeFocus=await page.evaluate(()=>document.activeElement?.dataset.key);check('keyboard traverses nodes',nodeFocus===await nodes.nth(1).getAttribute('data-key'));
    await page.keyboard.press('Home');check('keyboard jumps to first edge',await edges.first().evaluate(n=>n===document.activeElement));await page.keyboard.press('ArrowRight');check('keyboard traverses edges',await edges.nth(1).evaluate(n=>n===document.activeElement));await page.keyboard.press('Enter');check('keyboard exposes exact evidence detail',(await page.locator('#details').innerText()).includes('graph-derived'));await page.keyboard.press('End');check('keyboard reaches last node',await nodes.last().evaluate(n=>n===document.activeElement));
    report.flags['keyboard-nodes']=report.flags['keyboard-edges']=true;
    check('reduced motion removes graph animation',await nodes.first().evaluate(n=>getComputedStyle(n).animationName==='none'));report.flags['reduced-motion']=true;
    await page.locator('#evidence-list button').first().click();check('evidence list is inspectable',(await page.locator('#details').innerText()).length>20);
    // A real old graph response is deliberately delivered after a newer one,
    // while ignoring AbortSignal, to exercise the epoch guard independently.
    await select(1);
    await page.evaluate(()=>{window.__nativeFetch=window.fetch;window.__held=false;window.fetch=(url,options)=>{if(String(url).includes('frame=3')&&!window.__held){window.__held=true;const promise=window.__nativeFetch(url,{...options,signal:undefined});return new Promise(resolve=>{window.__release=()=>promise.then(resolve)})}return window.__nativeFetch(url,options)}});
    await page.locator('#timeline button[data-frame="3"]').click();await page.waitForFunction(()=>window.__held);await page.locator('#timeline button[data-frame="1"]').click();await ready(1);await page.evaluate(()=>window.__release());await page.waitForTimeout(150);
    check('stale response cannot overwrite newer selection',await page.locator('#replay').getAttribute('data-selection')==='1:mainline'&&(await page.locator('#sources').innerText()).includes('SRC-SOP-CURRENT-V2')&&!(await page.locator('#sources').innerText()).includes('SRC-SOP-CURRENT-V3'));report.flags['stale-response']=true;
    await page.evaluate(()=>{window.fetch=window.__nativeFetch});
    await page.locator('#reset').click();await ready(0);await page.locator('#play').click();await page.waitForFunction(()=>document.getElementById('play').textContent==='Pause');await page.locator('#play').click();await page.locator('#scrub').evaluate(n=>{n.value='2';n.dispatchEvent(new Event('input',{bubbles:true}))});await ready(2);await page.waitForTimeout(1700);
    check('rapid play pause seek leaves only selected frame',await page.locator('#replay').getAttribute('data-selection')==='2:mainline'&&await page.locator('#play').getAttribute('aria-pressed')==='false');report.flags['rapid-controls']=true;
    await page.locator('#previous').click();await ready(1);await page.locator('#next').click();await ready(2);
    await page.locator('#play').click();await ready(2);await page.waitForFunction(()=>document.getElementById('replay').dataset.selection==='3:mainline'&&document.getElementById('replay').dataset.state==='ready'&&document.getElementById('play').getAttribute('aria-pressed')==='false',{timeout:6000});check('playback advances live frame then stops at mode boundary',true);
    await page.locator('#reset').click();await ready(0);check('playback reset changes selectors only',await page.locator('#mode').inputValue()==='mainline'&&await page.locator('#scrub').inputValue()==='0');report.flags['reset-selector-only']=true;
    await select(3);const beforeUnavailable=await page.locator('#graph .edge').count();
    await page.route('**/api/replay-frame?*',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({state:'unavailable'})}));await page.locator('#timeline button[data-frame="3"]').click();await ready(3,'unavailable');
    check('unavailable clears previous facts and never animates canned evidence',beforeUnavailable>0&&await page.locator('#graph .edge').count()===0&&!(await page.locator('body').innerText()).includes('SRC-')&&(await page.locator('#status').innerText()).includes('Live graph unavailable'));report.flags.unavailable=true;
    await page.unroute('**/api/replay-frame?*');await select(viewportName==='mobile'?5:3);
   }
   fs.mkdirSync(screenshots,{recursive:true});const shot=path.join(screenshots,viewportName+'.png');await page.screenshot({path:shot,fullPage:true});report.screenshots.push(shot);
   if(mode==='existing'){
    // Lifecycle uses the real page script; verify its outstanding fetch and
    // single playback timer are cancelled before a held response can return.
    await page.evaluate(()=>{window.__signals=[];window.__nativeFetch=window.fetch;window.fetch=(url,opts)=>{window.__signals.push(opts.signal);return new Promise(()=>{})}});
    await page.locator('#play').click();await page.waitForFunction(()=>window.__signals.length>0);await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
    check('page unload aborts requests and stops playback',await page.evaluate(()=>window.__signals.every(s=>s.aborted)&&document.getElementById('play').getAttribute('aria-pressed')==='false'&&document.getElementById('replay').dataset.state==='disposed'));report.flags['unload-cancels']=true;
   }
   await context.close();
  }
  check('only read-only GETs requested',report.requests.every(r=>r.method==='GET')&&!report.requests.some(r=>r.path.includes('reset')||r.path.includes('confirm')));
  check('no browser script errors',errors.length===0,errors);
  const race=await unitRace();check('abort-ignoring stale response/failure and timer unit adversary',race.status==='PASS');
  report.status='PASS';
 }catch(error){report.status='FAIL';report.error=String(error);throw error}
 finally {if(browser)await browser.close();report.assertion_count=report.assertions.length;fs.mkdirSync(path.dirname(reportPath),{recursive:true});fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({status:report.status,assertions:report.assertion_count,report:reportPath}))}
}
