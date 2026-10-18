/* Real browser assertions against a loopback live graph API. No stored answers. */
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('/tools/node/lib/node_modules/playwright-core');
const [url,mode,out,shots]=process.argv.slice(2);
const expected=JSON.parse(fs.readFileSync(path.join(__dirname,'expected-results.json'),'utf8'));
if(!['127.0.0.1','localhost'].includes(new URL(url).hostname))throw Error('Loopback required');
(async()=>{
 const browser=await chromium.launch({args:['--no-sandbox','--disable-dev-shm-usage']});
 const page=await browser.newPage();const r={status:'FAIL',assertions:[],screenshots:[],hosts:[]};const hosts=new Set(),errors=[];
 const check=(name,passed)=>{r.assertions.push({name,passed:!!passed});if(!passed)throw Error(name)};
 const known=expected.boundaries[3].known_at;
 const wait=async time=>page.waitForFunction(t=>window.sopCompareReady===t,time);
 const cutoff=async time=>{await page.locator('#known').evaluate((n,t)=>{n.value=t.replace('Z','');n.dispatchEvent(new Event('change',{bubbles:true}))},time);await wait(time)};
 page.on('pageerror',e=>errors.push(String(e)));page.on('request',q=>hosts.add(new URL(q.url()).host));fs.mkdirSync(shots,{recursive:true});
 try{
  for(const [view,width] of [['desktop',1440],['mobile',390]]){
   await page.setViewportSize({width,height:950});await page.goto(url+'/sop-compare');await wait(known);r[view]={};
   for(const boundary of expected.boundaries){
    await cutoff(boundary.known_at);const data=await page.evaluate(()=>window.sopCompareData);
    check(view+' source-known options '+boundary.known_at,JSON.stringify(data.options.versions)===JSON.stringify(mode==='cold'?[]:boundary.versions));
    const text=await page.locator('main').innerText();
    if(boundary.known_at==='2026-10-14T15:59:59Z')check(view+' no future identity/title/options/ARIA',!['SOP-OPS-9-V2','SOP-OPS-9-V3','SRC-SOP-CURRENT','Current Text'].some(s=>text.includes(s))&&!(await page.locator('main').ariaSnapshot()).includes('SOP-OPS-9-V2'));
    r[view][boundary.known_at]={data,text,dom:await page.locator('main').innerHTML(),aria:await page.locator('main').ariaSnapshot(),options:await page.locator('select').evaluateAll(ns=>ns.map(n=>[...n.options].map(o=>[o.value,o.textContent])))};
   }
   if(mode==='seeded'){
    for(const pair of expected.pairs){
     await page.selectOption('#left',pair[0]);await page.waitForFunction(v=>window.sopCompareData?.selection.left===v,pair[0]);
     await page.selectOption('#right',pair[1]);await page.waitForFunction(v=>window.sopCompareData?.selection.right===v,pair[1]);
     const data=await page.evaluate(()=>window.sopCompareData);const a=await page.locator('#left-pane').innerText(),b=await page.locator('#right-pane').innerText();
     for(const [pane,txt] of [[data.left,a],[data.right,b]]){
      check(view+' source citation '+pane.id,pane.sources.every(s=>txt.includes(s.id)&&txt.includes(s.title)&&txt.includes(s.observed_at)));
      check(view+' explicit typed edges '+pane.id,pane.applicability_edges.every(e=>txt.includes(e.relationship)&&txt.includes(e.to))&&pane.role_claims.every(c=>txt.includes(c.edge.relationship)&&txt.includes(c.edge.to)&&txt.includes('unverified SOP claim')));
     }
     r[view][pair.join(':')]={data,text:await page.locator('main').innerText(),dom:await page.locator('main').innerHTML(),aria:await page.locator('main').ariaSnapshot()};
    }
    await cutoff(known);await page.locator('#left').focus();await page.keyboard.press('ArrowUp');await page.keyboard.press('Enter');await page.waitForFunction(()=>window.sopCompareData?.selection.left==='SOP-OPS-9-V1');
    check(view+' keyboard selectable pair',(await page.locator('#left').inputValue())==='SOP-OPS-9-V1');
    await page.locator('#right').focus();await page.keyboard.press('ArrowUp');await page.keyboard.press('Enter');await page.waitForFunction(()=>window.sopCompareData?.selection.right==='SOP-OPS-9-V2');
    check(view+' keyboard counterpart',(await page.locator('#right').inputValue())==='SOP-OPS-9-V2');r.keyboard=true;
   }
   check(view+' no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));r.no_overflow=true;
   const shot=path.join(shots,view+'.png');await page.screenshot({path:shot,fullPage:true});r.screenshots.push(shot);
  }
  if(mode==='seeded'){
   // Deliberately ignore AbortSignal. A later request must defeat an older
   // late response even if that response completes after the earlier cutoff.
   await cutoff(known);
   await page.evaluate(async early=>{
    const original=window.fetch.bind(window);const late=await(await original('/api/sop-claim-comparison?known_at=2026-10-14T16:04:00Z')).json();
    const before=await(await original('/api/sop-claim-comparison?known_at='+early)).json();
    window.sopOriginalFetch=original;let calls=0;
    window.fetch=async()=>{const value=++calls===1?late:before;const delay=calls===1?250:20;await new Promise(r=>setTimeout(r,delay));return {ok:true,json:async()=>value}};
    const input=document.querySelector('#known');input.value='2026-10-14T16:04:00';input.dispatchEvent(new Event('change',{bubbles:true}));
    input.value=early.replace('Z','');input.dispatchEvent(new Event('change',{bubbles:true}));
   },expected.boundaries[0].known_at);
   check('previous future evidence erased immediately',!(await page.locator('main').innerText()).includes('SOP-OPS-9-V3'));
   await wait(expected.boundaries[0].known_at);await page.waitForTimeout(350);
   check('stale response cannot restore future identities',await page.evaluate(()=>window.sopCompareData.known_at==='2026-10-14T15:59:59Z')&&!(await page.locator('main').innerText()).includes('SOP-OPS-9-V3'));r.race_guard=true;
   await page.evaluate(()=>{window.fetch=window.sopOriginalFetch});await cutoff(known);
   await page.evaluate(async()=>{
    const original=window.fetch.bind(window),data=structuredClone(window.sopCompareData);
    data.left.sources[0].title='<img src=x onerror="window.sopXss=true"> & forged <button>Confirm</button>';
    window.sopEscapeFetch=original;window.fetch=async()=>({ok:true,json:async()=>data});
    document.querySelector('#controls').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
   });await wait(known);
   check('source text escaped, never interpreted as HTML',(await page.locator('#left-pane').innerText()).includes('<img src=x')&&await page.locator('#left-pane img,#left-pane button').count()===0&&!(await page.evaluate(()=>window.sopXss)));r.escaped_values=true;
   await page.evaluate(()=>{window.fetch=window.sopEscapeFetch});
  }
  check('no browser errors',errors.length===0);check('only owned loopback host',[...hosts].every(h=>h===new URL(url).host));r.status='PASS';
 }catch(e){r.error=String(e);process.exitCode=1}
 finally{r.hosts=[...hosts];r.assertion_count=r.assertions.length;fs.writeFileSync(out,JSON.stringify(r,null,2));await browser.close()}
})();
