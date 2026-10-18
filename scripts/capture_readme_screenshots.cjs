// Only real app requests and native element/clipped screenshots. No DOM edits,
// injected styles, recorder captions, overlays, mocked APIs or stitched images.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.README_PLAYWRIGHT_MODULE || '/tools/node/lib/node_modules/playwright-core');
const [base, output, reportPath] = process.argv.slice(2);
const target = new URL(base);
if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1' || !target.port || target.username || target.password) throw Error('Owned loopback URL required');

(async () => {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox','--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:1});
  const report = {status:'FAIL', screenshots:[], assertions:[], browser_errors:[]};
  const check = (name, value) => { report.assertions.push({name,passed:!!value}); if(!value) throw Error(name); };
  page.on('pageerror', e => report.browser_errors.push(String(e)));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if(url.origin !== target.origin) {report.browser_errors.push('Unexpected external request'); return route.abort();}
    return route.continue();
  });
  async function box(selector) {
    const element = page.locator(selector).first();
    await element.waitFor({state:'visible'});
    return element.evaluate(e => {const r=e.getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height};});
  }
  async function shot(name, clip, state) {
    // Crop coordinates are document-relative; never resize/recompose pixels.
    clip={x:Math.floor(clip.x),y:Math.floor(clip.y),width:Math.ceil(clip.width),height:Math.ceil(clip.height)};
    let filename = name+'.png';
    let data = await page.screenshot({type:'png',clip,fullPage:true,animations:'disabled'});
    if(data.length>600000) {filename=name+'.jpg'; data=await page.screenshot({type:'jpeg',quality:85,clip,fullPage:true,animations:'disabled'});}
    check(name+' is within 600 KB',data.length<=600000);
    fs.writeFileSync(path.join(output,filename),data);
    report.screenshots.push({file:filename,bytes:data.length,clip,state});
  }
  async function card(id) {return box(`section:has(> .card-head #${id})`);}
  try {
    await page.goto(base);
    const reset = page.waitForResponse(r => r.url().endsWith('/api/scenario/reset') && r.request().method()==='POST');
    await page.click('#reset');
    check('synthetic reset succeeds',(await reset).status()===200);
    await page.waitForFunction(()=>document.querySelector('#db-status').textContent.includes('Live FalkorDB') && document.querySelectorAll('#paths .path-row').length===2 && !document.querySelector('#reset').disabled);
    const health = await (await page.request.get(base+'/api/health')).json();
    check('actual FalkorDB 6.0.2',health.database==='FalkorDB'&&health.module_version===60002);
    const graph = await card('graph-title'), evidence = await card('evidence-title');
    check('source trail rendered',(await page.locator('#facts').innerText()).includes('SRC-INC-204'));
    await shot('control-room', {x:graph.x,y:graph.y,width:graph.width,height:evidence.y+evidence.height-graph.y}, 'INC-204 impact map and adjacent source-trail card after reset');
    const sop = await card('sop-title');
    const sopText = await page.locator('#sop-panel').innerText();
    check('V2/V3 unresolved and stale V1',['SOP-OPS-9-V1','SOP-OPS-9-V2','SOP-OPS-9-V3','unresolved'].every(s=>sopText.includes(s)));
    await shot('sop-conflict',sop,'Unresolved V2/V3 claims; stale V1/C3 history and exact source cards');

    await page.selectOption('#dock-known-time','2026-10-14T20:19:00Z');
    await page.waitForFunction(()=>document.querySelector('#dock-path').textContent.includes('ROLE-DOCK-SWING') && document.querySelector('#dock-time-context').textContent.includes('20:19:00Z'));
    await shot('shift-board',await box('#dock-case'),'INC-207 effective 20:30Z boundary, sources known by 20:19Z');

    // Confirm one actual preview, then prepare a second in-memory preview. A
    // continuous crop shows the confirm control and the existing saved receipt.
    await page.click('#prepare');
    await page.locator('#confirm-handover').waitFor({state:'visible'});
    check('preview is not persisted',!((await (await page.request.get(base+'/api/actions')).json()).actions.length));
    await page.click('#confirm-handover');
    await page.waitForFunction(()=>document.querySelector('#saved-count').textContent==='1 saved');
    check('explicit confirmation saved one local handover',((await (await page.request.get(base+'/api/actions')).json()).actions.length)===1);
    await page.click('#prepare');
    await page.locator('#confirm-handover').waitFor({state:'visible'});
    const tasks = await box('#preview .task-list'), saved = await card('saved-title');
    await shot('handover', {x:saved.x,y:tasks.y-10,width:saved.width,height:saved.y+Math.min(saved.height,550)-(tasks.y-10)}, 'Second unsaved preview with confirm button above first genuinely confirmed local handover');

    await page.goto(base+'/clocks');
    await page.waitForFunction(()=>document.querySelector('#atlas-workbench').getAttribute('aria-busy')==='false');
    await page.click('button[data-cell-id="e1-k1"]');
    await page.waitForFunction(()=>document.querySelector('#selected-detail').dataset.cellId==='e1-k1');
    check('atlas four cells',await page.locator('#atlas-grid [role=gridcell]').count()===4);
    await shot('ownership-clocks',await box('#atlas-workbench > section:first-child'),'Four effective/known clock pairs; 20:30Z / 20:19Z selected');

    await page.goto(base+'/knowledge-diff');
    await page.waitForFunction(()=>document.querySelector('#knowledge-diff-results').getAttribute('aria-busy')==='false');
    check('knowledge diff populated',await page.locator('.delta-card').count()>0);
    const diffTop = await box('.summary'), delta = await box('.delta-card');
    await shot('knowledge-diff',{x:diffTop.x,y:diffTop.y,width:diffTop.width,height:delta.y+delta.height-diffTop.y}, 'Mainline versus hypothetical at 16:05Z: first semantic difference with both projections');

    await page.goto(base+'/ask');
    await page.click('#question-submit');
    await page.locator('#question-answer').waitFor({state:'visible'});
    check('Ask answer has citations',await page.locator('#answer-citations .citation').count()>=4);
    await shot('ask-citations',await box('#question-answer'),'Deterministic INC-204 impact answer at 18:30Z with source observations; no model requested');

    await page.goto(base+'/resilience');
    await page.waitForFunction(()=>window.resilienceReady==='baseline');
    await page.selectOption('#mode','omit-central-sterile-owner');
    await page.waitForFunction(()=>window.resilienceReady==='omit-central-sterile-owner');
    const resilience = await page.evaluate(()=>window.resilienceData);
    check('one missing supporting edge',resilience.actual.ownership.paths.length===2 && resilience.hypothetical.ownership.paths.length===1 && resilience.dependent_evidence_state==='unknown');
    const controls=await box('.controls'), panes=await box('#panes');
    // Upper ownership comparison, not the long unchanged SOP/source appendix.
    await shot('evidence-resilience',{x:controls.x,y:controls.y,width:panes.width,height:panes.y-controls.y+540}, 'Hypothetical missing Central Sterile OWNED_BY support; live baseline still has two paths');
    check('eight distinct real screenshots',report.screenshots.length===8);
    check('5 MB total',report.screenshots.reduce((n,s)=>n+s.bytes,0)<=5000000);
    check('no browser errors',report.browser_errors.length===0);
    report.status='PASS';
  } catch(e) {report.error=String(e); process.exitCode=1;}
  finally {fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n');await browser.close();}
})();
