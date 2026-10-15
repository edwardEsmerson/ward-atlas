#!/usr/bin/env node
// Interaction assertions for the locally served WardAtlas app. No remote hosts.
const fs = require('node:fs');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');

const [url, reportPath] = process.argv.slice(2);
if (!url || !reportPath || !['localhost', '127.0.0.1'].includes(new URL(url).hostname)) {
  throw new Error('Usage: node scripts/wardatlas_browser_assertions.cjs http://127.0.0.1:PORT /workspace/evidence/...json');
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  const hosts = new Set();
  const checks = [];
  const check = (name, condition, detail) => {
    checks.push({ name, passed: Boolean(condition), detail: detail ?? null });
    if (!condition) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('request', request => { try { hosts.add(new URL(request.url()).host); } catch (_) {} });
  try {
    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
    check('desktop app responds successfully', response && response.status() === 200, response && response.status());
    await page.getByRole('heading', { name: 'One outage. Two accountable paths.' }).waitFor();
    await page.getByText('INC-204 · OPEN', { exact: true }).waitFor();
    await page.getByText('Central sterile operations ← Material transfer service', { exact: true }).waitFor();
    await page.getByText('Materials logistics ← Material transfer service', { exact: true }).waitFor();
    const visibleRoles = await page.locator('.path-row .role-label').allInnerTexts();
    check('only the two explicitly current operating roles are visible', visibleRoles.join('|') === 'CURRENT · Sterile operations coordinator|CURRENT · Materials duty role', visibleRoles);
    await page.getByText('Stale reference · SOP-OPS-9-V1', { exact: true }).waitFor();
    await page.getByText('SOP-OPS-9-V2 claims ROLE-FACILITIES-DUTY', { exact: true }).waitFor();
    await page.getByText('SOP-OPS-9-V3 claims ROLE-MATERIALS-LEAD', { exact: true }).waitFor();
    check('graph impact, current roles and unresolved SOP claims render', true);

    const hidden = await page.locator('body').innerText();
    check('restricted source identifiers and fact are not rendered', !/SRC-RESTRICTED-92|DEPT-RESTRICTED-COMPLIANCE|ROLE-RESTRICTED-OWNER|Restricted Continuity Annex 92|contact the secure lift coordinator|EQUIP-RESTRICTED-92/.test(hidden));
    check('stale role is not represented as current ownership', !hidden.includes('ROLE-OLD-STERILE-ROTATION'));

    const beforeReset = await page.locator('#impact-count').innerText();
    await page.getByRole('button', { name: 'Reset synthetic scenario' }).click();
    await page.waitForFunction(() => document.querySelector('#impact-count')?.textContent === '2 impact paths');
    check('in-app reset reseeds the same pinned paths', beforeReset === '2 impact paths' && await page.locator('#impact-count').innerText() === '2 impact paths');

    await page.getByRole('button', { name: 'Prepare handover draft' }).click();
    await page.getByRole('button', { name: 'Confirm & save locally' }).waitFor();
    const preview = await page.evaluate(async () => {
      const response = await fetch('/api/actions');
      return { status: response.status, body: await response.json() };
    });
    check('unconfirmed browser draft is not persisted', preview.status === 200 && preview.body.count === 0 && preview.body.actions.length === 0, preview);
    const previewText = await page.locator('#preview').innerText();
    check('draft links proposed follow-up to current fixture sources', previewText.includes('SRC-ASSET-C4') && previewText.includes('SRC-SOP-CURRENT-V2') && previewText.includes('SRC-SOP-CURRENT-V3'));
    await page.getByRole('button', { name: 'Confirm & save locally' }).click();
    await page.getByText('1 saved', { exact: true }).waitFor();
    const afterConfirm = await page.evaluate(async () => {
      const response = await fetch('/api/actions');
      return { status: response.status, body: await response.json() };
    });
    check('explicit in-app confirmation persists exactly one local draft', afterConfirm.status === 200 && afterConfirm.body.count === 1 && afterConfirm.body.actions.length === 1 && afterConfirm.body.actions[0].storage === 'local-only', afterConfirm);
    check('confirmation response has no external effects', await page.locator('#notice').innerText().then(text => text.includes('No external action was taken')));

    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(150);
    const mobile = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    check('mobile viewport has no page-level horizontal overflow', mobile.document <= mobile.viewport + 2 && mobile.body <= mobile.viewport + 2, mobile);
    check('browser requests remain on the explicitly selected local origin', [...hosts].length === 1 && [...hosts][0] === new URL(url).host, [...hosts]);
    check('browser has no console/page errors', errors.length === 0, errors);

    const report = { status: 'PASS', url, assertion_count: checks.length, checks, browser_errors: errors, requested_hosts: [...hosts], desktop_viewport: { width: 1440, height: 1000 }, mobile_viewport: mobile };
    fs.mkdirSync(require('node:path').dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify({ status: report.status, assertion_count: report.assertion_count, requested_hosts: report.requested_hosts, mobile_viewport: mobile }));
  } catch (error) {
    const report = { status: 'FAIL', url, assertion_count: checks.length, checks, error: String(error), browser_errors: errors, requested_hosts: [...hosts] };
    fs.mkdirSync(require('node:path').dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    console.error(error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
