/* Actual browser downloads, then renders those downloaded HTML bytes offline. */
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const {spawnSync} = require('node:child_process');
const {chromium} = require('/tools/node/lib/node_modules/playwright-core');
const [base, mode, output, directory] = process.argv.slice(2);
if (!['127.0.0.1','localhost'].includes(new URL(base).hostname)) throw Error('Loopback required');
const spec = JSON.parse(fs.readFileSync(path.join(__dirname,'../contracts/evidence-briefcase/expected-results.json'),'utf8'));
const report = {status:'FAIL', assertions:[], downloads:[], offline_count:0, offline_desktop:0, offline_mobile:0, offline_network_requests:[], stale_selection_passed:false, keyboard_passed:false, responsive_passed:false, downloaded_bytes_equal:false};
function check(name, value) { report.assertions.push({name,passed:!!value}); if (!value) throw Error(name); }
function readZip(file) {
  const code = "import sys,json,base64;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from verify_evidence_briefcase import read_archive_bytes;print(json.dumps({k:base64.b64encode(v).decode('ascii') for k,v in read_archive_bytes(Path(sys.argv[2]).read_bytes()).items()}))";
  const result = spawnSync('python3',['-c',code,__dirname,file],{encoding:'utf8',maxBuffer:8*1024*1024});
  if (result.status !== 0) throw Error('Downloaded ZIP verification: '+result.stderr);
  return Object.fromEntries(Object.entries(JSON.parse(result.stdout)).map(([k,v])=>[k,Buffer.from(v,'base64')]));
}
(async()=>{
  fs.mkdirSync(directory,{recursive:true});
  const browser=await chromium.launch({args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({acceptDownloads:true}); const page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  const requests=[];page.on('request',r=>requests.push(r.url()));
  let serial=0, downloadEvents=0;page.on('download',()=>downloadEvents++);
  async function ready(c,t) {await page.waitForFunction(({c,t})=>{const e=window.briefcaseData?.evidence;return window.briefcaseReady&&e?.selection.context===c&&(e.selection.as_of||e.selection.known_at)===t;},{c,t});}
  async function downloadAndOpen(view, width) {
    const bundle=await page.evaluate(()=>window.briefcaseData);
    const promise=page.waitForEvent('download');await page.click('#download');const download=await promise;
    check(view+' fixed safe download filename '+serial,download.suggestedFilename()==='wardatlas-evidence.zip');
    const stem=path.join(directory,view+'-'+serial++),zip=stem+'.zip',html=stem+'.html';await download.saveAs(zip);
    check(view+' actual ZIP bytes equal displayed capture '+serial,fs.readFileSync(zip).equals(Buffer.from(bundle.zip_base64,'base64')));
    const files=readZip(zip);
    check(view+' exact report bytes '+serial,files['report.html'].equals(Buffer.from(bundle.report_html,'utf8')));
    const evidence=JSON.parse(files['evidence.json']);
    const lateClock=mode==='source-clock-mutation'&&evidence.selection.context==='sop-hypothetical'&&evidence.selection.known_at==='2026-10-14T16:06:00Z';
    if(lateClock){
      const citation=evidence.source_occurrences.find(s=>s.source_id==='SRC-SOP-CURRENT-V3');
      const fact=evidence.source_facts.find(f=>f.type==='unverified-sop-claim'&&f.claim.source.id==='SRC-SOP-CURRENT-V3');
      check(view+' actual late source clock in downloaded JSON',citation?.observed_at==='2026-10-14T16:07:00Z'&&citation?.projected_observed_at==='2026-10-14T16:06:00Z'&&fact?.claim.source.observed_at==='2026-10-14T16:07:00Z');
      check(view+' unrelated V2 source clock unchanged',evidence.source_occurrences.find(s=>s.source_id==='SRC-SOP-CURRENT-V2')?.observed_at==='2026-10-14T16:00:00Z');
    }
    check(view+' downloaded selection equals displayed '+serial,JSON.stringify(evidence.selection)===JSON.stringify(bundle.evidence.selection));
    check(view+' exact evidence JSON semantic '+serial,JSON.stringify(evidence)===JSON.stringify(bundle.evidence));
    const manifest=JSON.parse(files['manifest.json']);
    check(view+' exact content hashes '+serial,['evidence.json','report.html'].every(n=>crypto.createHash('sha256').update(files[n]).digest('hex')===manifest.files[n].sha256));
    fs.writeFileSync(html,files['report.html']);report.downloads.push({zip,html,selection:evidence.selection});
    const offlineContext=await browser.newContext({viewport:{width,height:950},offline:true,javaScriptEnabled:false});
    const offline=await offlineContext.newPage();
    await offlineContext.route('**/*',route=>{const u=route.request().url();if(u.startsWith('file://'))return route.continue();report.offline_network_requests.push(u);return route.abort();});
    offline.on('request',r=>{if(/^https?:/.test(r.url()))report.offline_network_requests.push(r.url());});
    await offline.goto('file://'+path.resolve(html));
    const text=await offline.locator('main').innerText();
    if(lateClock){
      const citations=await offline.locator('section').filter({has:offline.getByRole('heading',{name:'Citation occurrences · ID / title / observation UTC',exact:true})}).innerText();
      const facts=await offline.locator('section').filter({has:offline.getByRole('heading',{name:'Source facts · unverified claims remain claims',exact:true})}).innerText();
      check(view+' actual and assumed clocks in downloaded offline citation section',citations.includes('2026-10-14T16:07:00Z')&&citations.includes('2026-10-14T16:06:00Z'));
      check(view+' actual clock in downloaded offline source facts',facts.includes('2026-10-14T16:07:00Z'));
    }
    check(view+' offline warning '+serial,text.includes('not current truth')&&text.includes('not a signature'));
    check(view+' offline selection and capture '+serial,Object.values(evidence.selection).every(v=>text.includes(v))&&text.includes(evidence.captured_at));
    check(view+' offline every citation occurrence '+serial,evidence.source_occurrences.every(s=>text.includes(s.source_id)&&text.includes(s.title)&&text.includes(s.observed_at)&&(!s.projected_observed_at||text.includes(s.projected_observed_at))));
    check(view+' offline no active assets links scripts '+serial,await offline.locator('script,a,link,img,iframe,object,embed,form').count()===0);
    check(view+' accessible structured witnesses '+serial,(await offline.locator('main').ariaSnapshot()).includes('heading "Typed witnesses and accessible relationship paths"'));
    if(mode==='cold')check(view+' generic unknown offline '+serial,evidence.witnesses.length===0&&evidence.source_occurrences.length===0&&!/SRC-|SOP-OPS-|INC-204|ROLE-/.test(text));
    check(view+' offline responsive '+serial,await offline.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await offline.screenshot({path:stem+'.png',fullPage:false});
    await offlineContext.close();report.offline_count++;report['offline_'+view]++;
  }
  try {
    for(const [view,width] of [['desktop',1440],['mobile',390]]) {
      await page.setViewportSize({width,height:950});await page.goto(base+'/briefcase');
      await ready('outage-snapshot','2026-10-14T18:30:00Z');
      check(view+' no automatic download',downloadEvents===report.downloads.length);
      for(const c of spec.cases) {
        await page.selectOption('#context',c.context);
        await page.selectOption('#time',c.as_of||c.known_at);await ready(c.context,c.as_of||c.known_at);
        check(view+' selection alone never downloads '+c.id,downloadEvents===report.downloads.length);
        const displayed=await page.locator('#selection').innerText();
        check(view+' selection visibly labeled '+c.id,displayed.includes(c.as_of||c.known_at)&&displayed.includes('Captured at UTC'));
        const frame=page.frameLocator('#preview');await frame.locator('h1').waitFor();
        check(view+' preview same report text '+c.id,(await frame.locator('main').innerText()).includes('Selected historical UTC axes'));
        await downloadAndOpen(view,width);
      }
      check(view+' no layout overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.screenshot({path:path.join(directory,view+'-workspace.png'),fullPage:false});
    }
    report.responsive_passed=true;
    // Hold an older request, finish the newer selection, then release the old
    // result. Download must remain the NEW displayed capture, with no requery.
    let held=null, heldData=null, signal;const intercepted=new Promise(r=>signal=r);
    await page.route('**/api/evidence-briefcase?**',async route=>{
      const u=new URL(route.request().url());
      if(u.searchParams.get('context')==='sop-mainline'&&u.searchParams.get('known_at')==='2026-10-14T16:04:00Z'&&!held){held=route;heldData=await context.request.get(route.request().url());signal();return;}
      await route.continue();
    });
    await page.selectOption('#context','sop-mainline');await intercepted;
    check('pending selection disables stale download',await page.locator('#download').isDisabled()&&await page.evaluate(()=>window.briefcaseData===null));
    await page.selectOption('#time','2026-10-14T16:05:00Z');await ready('sop-mainline','2026-10-14T16:05:00Z');
    const current=await page.evaluate(()=>window.briefcaseData);
    await held.fulfill({response:heldData});await page.waitForTimeout(150);
    check('stale response cannot replace selection',JSON.stringify(await page.evaluate(()=>window.briefcaseData))===JSON.stringify(current));
    await page.unroute('**/api/evidence-briefcase?**');
    const before=requests.length;await downloadAndOpen('mobile',390);
    check('download needs no new API request',requests.length===before);report.stale_selection_passed=true;
    await page.locator('#time').focus();await page.keyboard.press('Home');await page.keyboard.press('Enter');await ready('sop-mainline','2026-10-14T16:04:00Z');
    check('keyboard changes view-local selection',await page.locator('#time').inputValue()==='2026-10-14T16:04:00Z');report.keyboard_passed=true;
    check('no operational or external browser requests',requests.every(u=>u.startsWith('blob:')||(new URL(u).origin===new URL(base).origin&&['/briefcase','/api/evidence-briefcase'].includes(new URL(u).pathname))));
    check('no browser errors',errors.length===0);check('offline network blocked with zero requests',report.offline_network_requests.length===0);
    report.downloaded_bytes_equal=true;report.status='PASS';
  } catch(e) {report.error=String(e);process.exitCode=1;} finally {
    report.assertion_count=report.assertions.length;report.browser_errors=errors;fs.writeFileSync(output,JSON.stringify(report,null,2));await browser.close();
  }
})();
