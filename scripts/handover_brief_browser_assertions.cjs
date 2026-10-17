#!/usr/bin/env node
// Desktop/mobile handover snapshot checks against the pinned independent oracle.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');

const [url, reportPath, screenshotDirectory] = process.argv.slice(2);
if (!url || !reportPath || !screenshotDirectory || !['localhost', '127.0.0.1'].includes(new URL(url).hostname)) {
  throw new Error('Usage: node scripts/handover_brief_browser_assertions.cjs http://127.0.0.1:PORT /workspace/evidence/report.json /workspace/evidence/screenshots');
}
const oracle = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'tests/handover-brief-expected-results.json'), 'utf8'));
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  const expectedConsoleErrors = [];
  const hosts = new Set();
  const checks = [];
  let confirmationResponse = null;
  const report = { status: 'running', assertion_count: 0, assertions: checks, hosts: [], errors, expected_console_errors: expectedConsoleErrors, screenshots: [] };
  fs.mkdirSync(screenshotDirectory, { recursive: true });
  const check = (name, condition, detail) => {
    checks.push({ name, passed: Boolean(condition), detail: detail ?? null });
    if (!condition) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => {
    if (message.type() !== 'error') return;
    if (message.text() === 'Failed to load resource: the server responded with a status of 400 (Bad Request)') expectedConsoleErrors.push(message.text());
    else errors.push(message.text());
  });
  page.on('request', request => { try { hosts.add(new URL(request.url()).host); } catch (_) {} });
  page.on('response', async response => {
    if (!response.url().endsWith('/api/handover/confirm')) return;
    try { confirmationResponse = await response.json(); } catch (_) { confirmationResponse = null; }
  });
  try {
    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
    check('desktop local app responds on selected loopback origin', response && response.status() === 200, response && response.status());
    check('timestamp input defaults to the pinned UTC view', await page.locator('#handover-as-of').inputValue() === '2026-10-14T18:30:00Z', await page.locator('#handover-as-of').inputValue());

    await page.locator('#handover-as-of').fill(oracle.fixture.offset_input);
    await page.locator('#prepare').click();
    await page.locator('#handover-snapshot').waitFor({ state: 'visible', timeout: 15000 });
    const previewSnapshot = JSON.parse(await page.locator('#handover-snapshot').getAttribute('data-snapshot'));
    check('offset API/browser preview normalizes to the exact pinned snapshot', equal(previewSnapshot, oracle.canonical_snapshot), previewSnapshot);
    check('preview visually includes both graph paths and full source provenance',
      (await page.locator('#preview').innerText()).includes('SRC-OPS-ROSTER') &&
      (await page.locator('#preview').innerText()).includes('Synthetic Asset State Ledger') &&
      (await page.locator('#preview').innerText()).includes('2026-10-14T18:05:00Z') &&
      (await page.locator('#preview').innerText()).includes('ROLE-MATERIALS-DUTY'),
      await page.locator('#preview').innerText());
    check('preview shows explicit typed unresolved SOP witness, not owner contact',
      (await page.locator('#preview').innerText()).includes('SOP_CONFLICT') &&
      (await page.locator('#preview').innerText()).includes('unverified SOP claim') &&
      (await page.locator('#preview').innerText()).includes('unresolved') &&
      !(await page.locator('#preview').innerText()).includes('Confirm contact'),
      await page.locator('#preview').innerText());
    const preConfirm = await page.evaluate(async () => (await (await fetch('/api/actions')).json()));
    check('preview is not persisted before explicit confirmation', preConfirm.count === 0 && preConfirm.actions.length === 0, preConfirm);
    await page.screenshot({ path: path.join(screenshotDirectory, 'handover-preview-desktop.png'), fullPage: true });
    report.screenshots.push(path.join(screenshotDirectory, 'handover-preview-desktop.png'));

    await page.locator('#confirm-handover').click();
    await page.getByText(/Saved locally as HANDOVER-/).waitFor({ timeout: 15000 });
    const saved = await page.evaluate(async () => (await (await fetch('/api/actions')).json()));
    check('one explicit confirmation stores one local action with preview-equal snapshot',
      confirmationResponse?.persisted === true && confirmationResponse?.storage === 'local-only' &&
      equal(confirmationResponse?.action?.snapshot, previewSnapshot) && saved.count === 1 &&
      saved.actions.length === 1 && equal(saved.actions[0].snapshot, previewSnapshot), { confirmationResponse, saved });
    check('confirmed action is explicitly local and has no external effects', saved.storage === 'local-only', saved);
    check('preview and confirmation disclaim unresolved truth, performed work, clinical actions, and external writes',
      draftBoundary(previewSnapshot, confirmationResponse) && confirmationResponse.external_effects?.length === 0 &&
      confirmationResponse.action.action_boundary?.source_truth_resolved === false &&
      confirmationResponse.action.action_boundary?.work_claimed_performed === false &&
      confirmationResponse.action.action_boundary?.clinical_action_proposed === false &&
      confirmationResponse.action.action_boundary?.messages_sent === false &&
      confirmationResponse.action.action_boundary?.calls_made === false &&
      confirmationResponse.action.action_boundary?.external_tickets_created === false &&
      confirmationResponse.action.action_boundary?.external_writes?.length === 0,
      confirmationResponse);
    const savedCard = page.locator('#saved .saved-snapshot');
    await savedCard.waitFor({ state: 'visible' });
    const desktopSavedText = await savedCard.innerText();
    check('saved action visually retains source IDs, timestamps, and unresolved claim label',
      desktopSavedText.includes('SRC-SOP-CURRENT-V2') && desktopSavedText.includes('2026-10-14T16:04:00Z') && desktopSavedText.includes('unverified SOP claim'), desktopSavedText);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: 'networkidle' });
    const mobileSavedCard = page.locator('#saved .saved-snapshot');
    await mobileSavedCard.waitFor({ state: 'visible' });
    const mobileSnapshot = JSON.parse(await mobileSavedCard.getAttribute('data-snapshot'));
    const mobileSavedText = await mobileSavedCard.innerText();
    const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    check('mobile board renders the same confirmed snapshot without horizontal overflow',
      equal(mobileSnapshot, oracle.canonical_snapshot) && mobileSavedText === desktopSavedText && dimensions.scrollWidth <= dimensions.width,
      { width: dimensions.width, scrollWidth: dimensions.scrollWidth, text: mobileSavedText });
    await page.screenshot({ path: path.join(screenshotDirectory, 'handover-saved-mobile.png'), fullPage: true });
    report.screenshots.push(path.join(screenshotDirectory, 'handover-saved-mobile.png'));

    await page.locator('#handover-as-of').fill(oracle.fixture.malformed_timestamp_probe);
    await page.locator('#prepare').click();
    await page.waitForFunction(() => document.querySelector('#notice')?.textContent.includes('Invalid timestamp'), null, { timeout: 15000 });
    const afterInvalid = await page.evaluate(async () => (await (await fetch('/api/actions')).json()));
    check('malformed browser preview is rejected without a second action', afterInvalid.count === 1 && afterInvalid.actions.length === 1 && equal(afterInvalid.actions[0].snapshot, oracle.canonical_snapshot), afterInvalid);
    check('browser made only selected loopback requests; only the expected malformed-time 400 was logged', [...hosts].every(host => /^127\.0\.0\.1:\d+$|^localhost:\d+$/.test(host)) && errors.length === 0 && expectedConsoleErrors.length === 1, { hosts: [...hosts], errors, expectedConsoleErrors });
    report.status = 'PASS';
    report.assertion_count = checks.length;
    report.hosts = [...hosts];
  } finally {
    await browser.close();
    report.assertion_count = checks.length;
    report.hosts = [...hosts];
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

function draftBoundary(snapshot, confirmation) {
  return snapshot.sop_witness?.winner_selected === false && snapshot.sop_witness?.claims_are_current_owner_facts === false &&
    confirmation?.action?.action_boundary?.automatically_confirmed === false;
}
