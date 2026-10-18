// Local-only browser evidence. Expectations come from pre-behavior literal files.
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('/tools/node/lib/node_modules/playwright-core');
const equal=require('node:util').isDeepStrictEqual;
const [url,mode,out,shots]=process.argv.slice(2);
const oracle=JSON.parse(fs.readFileSync(path.join(__dirname,'expected-results.json'),'utf8'));
if(new URL(url).hostname!=='127.0.0.1')throw Error('Loopback required');
(async()=>{
 const browser=await chromium.launch({args:['--no-sandbox','--disable-dev-shm-usage']});
 const p=await browser.newPage();const report={status:'FAIL',assertions:[],screenshots:[]};const errors=[],hosts=new Set();
 const check=(name,ok)=>{report.assertions.push({name,passed:!!ok});if(!ok)throw Error(name)};
 const ready=()=>p.waitForFunction(()=>window.roleDeskReady===true);
 p.on('pageerror',e=>errors.push(String(e)));p.on('request',r=>hosts.add(new URL(r.url()).host));fs.mkdirSync(shots,{recursive:true});
 try{
  for(const [view,width] of [['desktop',1440],['mobile',390]]){
   await p.setViewportSize({width,height:950});await p.goto(url+'/roles');await ready();report[view]={};
   const capture=async key=>{
    const data=await p.evaluate(()=>window.roleDeskData),text=await p.locator('#results').innerText();
    for(const w of data.ownership_witnesses){check(view+' path '+key,w.path.every(id=>text.includes(id)));for(const s of w.sources)check(view+' occurrence '+key+' '+s.source_id,text.includes(s.source_id)&&text.includes(s.title)&&text.includes(s.observed_at))}
    report[view][key]={data,text:await p.locator('main').innerText(),aria:await p.locator('main').ariaSnapshot()};return data;
   };
   let data=await capture('all-conveyor');
   if(mode==='unknown'){
    check(view+' unknown body',equal(data,oracle.unavailable));
    check(view+' role disabled',await p.locator('#role').isDisabled());
    check(view+' unknown not no owner',(await p.locator('#results').innerText()).includes('Unknown, not zero work or no owner'));
   }else{
    check(view+' exact discovery',JSON.stringify(data.ownership_witnesses.map(w=>w.path))===JSON.stringify(oracle.conveyor.paths));
    check(view+' roster occurrences',oracle.conveyor.roster_occurrences.every(t=>(report[view]['all-conveyor'].text).includes(t)));
    for(let i=0;i<2;i++){
     const role=oracle.conveyor.paths[i][4];await p.selectOption('#role',role);await ready();data=await capture(role);
     check(view+' selected role only '+role,data.selected_role===role&&data.ownership_witnesses.length===1&&JSON.stringify(data.ownership_witnesses[0].path)===JSON.stringify(oracle.conveyor.paths[i]));
    }
    await p.locator('#role').focus();await p.keyboard.press('Home');await p.keyboard.press('ArrowDown');await p.keyboard.press('Enter');await ready();
    check(view+' keyboard role',(await p.locator('#role').inputValue())===oracle.conveyor.paths[1][4]);report.keyboard_passed=true;
    await p.selectOption('#as-of',oracle.conveyor.restored_at);await ready();data=await capture('restored');
    check(view+' observed restoration',data.state==='restored-observed'&&data.responsibilities.length===0&&data.ownership_witnesses.length===2);
    check(view+' no safety inference',!data.safe_asserted&&!data.resolution_asserted);
    await p.selectOption('#context','dock');await ready();data=await capture('dock-duty');check(view+' dock duty',data.roles.length===1&&data.roles[0]==='ROLE-DOCK-DUTY');
    await p.selectOption('#effective','2026-10-14T20:30:00Z');await ready();data=await capture('dock-swing');check(view+' dock swing',data.roles.length===1&&data.roles[0]==='ROLE-DOCK-SWING');
    await p.selectOption('#known','2026-10-14T20:17:59Z');await ready();data=await capture('dock-crossed');check(view+' crossed unknown',equal(data,oracle.unavailable));
    await p.selectOption('#context','conveyor');await ready();await p.selectOption('#as-of',oracle.conveyor.as_of);await ready();
   }
   check(view+' no horizontal overflow',await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const shot=path.join(shots,view+'.png');await p.screenshot({path:shot,fullPage:true});report.screenshots.push(shot);
  }
  if(mode!=='unknown'){
   // Ignore AbortSignal deliberately: a response-generation token, not abort
   // alone, must prevent late successful/error responses overwriting a new read.
   const base=await p.evaluate(()=>window.roleDeskData);
   await p.evaluate(base=>{
    window.originalDeskFetch=window.fetch;window.deskFetchNumber=0;
    window.fetch=()=>{const n=++window.deskFetchNumber;const d=JSON.parse(JSON.stringify(base));d.message=n===1?'STALE RESPONSE MUST NOT DISPLAY':'CURRENT RESPONSE';return new Promise(resolve=>setTimeout(()=>resolve({ok:true,json:async()=>d}),n===1?400:20))};
   },base);
   await p.click('#reload');check('loading visible',(await p.locator('#status').innerText()).includes('Loading live graph'));
   await p.selectOption('#as-of',oracle.conveyor.restored_at);await p.waitForFunction(()=>document.getElementById('status').textContent==='CURRENT RESPONSE');await p.waitForTimeout(500);
   check('abort-ignoring stale success isolated',await p.locator('#status').innerText()==='CURRENT RESPONSE');
   await p.evaluate(base=>{window.fetch=async()=>{const d=JSON.parse(JSON.stringify(base));d.message='<img src=x onerror="window.deskXSS=true">';d.ownership_witnesses[0].sources[0].title='<script>window.deskXSS=true</script>';return {ok:true,json:async()=>d}}},base);
   await p.click('#reload');await ready();check('escaped text visible',(await p.locator('#status').innerText()).includes('<img src=x'));
   check('no injected DOM',await p.locator('#status img,#results script').count()===0&&!await p.evaluate(()=>window.deskXSS));
   await p.evaluate(()=>{window.fetch=async()=>{throw Error('SECRET NETWORK DETAIL')}});await p.click('#reload');await p.waitForFunction(()=>window.roleDeskReady==='error');
   check('error clears witnesses',await p.locator('#paths').innerText()===''&&!(await p.locator('main').innerText()).includes('SECRET NETWORK DETAIL'));
   check('error is unknown',(await p.locator('#status').innerText()).includes('unknown, not zero work'));
   await p.evaluate(()=>{window.fetch=window.originalDeskFetch});await p.click('#reload');await ready();check('retry recovers',await p.locator('#results').getAttribute('aria-busy')==='false');report.race_errors_escape_passed=true;
  }
  check('no JS errors',!errors.length);check('only app origin',[...hosts].every(h=>h===new URL(url).host));report.status='PASS';
 }catch(e){report.error=String(e);process.exitCode=1}
 finally{report.assertion_count=report.assertions.length;fs.writeFileSync(out,JSON.stringify(report,null,2));await browser.close()}
})();
