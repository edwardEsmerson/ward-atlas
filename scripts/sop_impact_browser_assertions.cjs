#!/usr/bin/env node
// Desktop/mobile browser checks for the separate graph-backed SOP impact view.
// Every request must stay on the caller-selected local origin.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');

const [url, reportPath, screenshotDirectory] = process.argv.slice(2);
if (!url || !reportPath || !screenshotDirectory || !['localhost', '127.0.0.1'].includes(new URL(url).hostname)) {
  throw new Error('Usage: node scripts/sop_impact_browser_assertions.cjs http://127.0.0.1:PORT /workspace/evidence/report.json /workspace/evidence/screenshots');
}
const oracle = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'tests/sop-impact-expected-results.json'), 'utf8'));
const expected = id => oracle.cases.find(row => row.id === id).expected;
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  const expectedConsoleErrors = [];
  const hosts = new Set();
  const checks = [];
  fs.mkdirSync(screenshotDirectory, { recursive: true });
  const check = (name, condition, detail) => {
    checks.push({ name, passed: Boolean(condition), detail: detail ?? null });
    if (!condition) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => {
    if (message.type() !== 'error') return;
    if (message.text() === 'Failed to load resource: the server responded with a status of 503 (Service Unavailable)') expectedConsoleErrors.push(message.text());
    else errors.push(message.text());
  });
  page.on('request', request => { try { hosts.add(new URL(request.url()).host); } catch (_) {} });
  let failSopImpactOnce = true;
  await page.route('**/api/sop-impact*', async route => {
    if (failSopImpactOnce) {
      failSopImpactOnce = false;
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Synthetic isolated endpoint-failure test.' }) });
    } else await route.continue();
  });
  try {
    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
    check('desktop app serves a local browser view', response && response.status() === 200, response && response.status());
    await page.getByText('Trace unavailable', { exact: true }).waitFor();
    check('SOP trace endpoint failure does not suppress the existing briefing or shift board',
      await page.getByRole('heading', { name: 'A board with no defaults.' }).isVisible() &&
      await page.locator('#shift-board-empty').isVisible() &&
      (await page.locator('#sop-impact-notice').innerText()).includes('existing briefing and shift board remain available'),
      { heading: await page.locator('#main-title').innerText(), shift: await page.locator('#shift-board-badge').innerText(), notice: await page.locator('#sop-impact-notice').innerText() });
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('#sop-impact-badge').waitFor();
    await page.locator('#sop-impact-empty').waitFor({ state: 'visible' });
    check('cold browser view is empty/unknown without source/path identifiers',
      await page.locator('#sop-impact-badge').innerText() === 'Empty · unknown' &&
      await page.locator('#sop-impact-content').isHidden() &&
      !/INC-\d+|SRC-|ROLE-|SOP-OPS-/.test(await page.locator('body').innerText()),
      await page.locator('body').innerText());

    await page.getByRole('button', { name: 'Reset synthetic scenario' }).click();
    await page.getByRole('heading', { name: 'One outage. Two accountable paths.' }).waitFor();
    await page.getByText('Source-linked · unresolved', { exact: true }).waitFor();
    const observedExpected = expected('sop-impact-observed-outage-paths');
    const conflictExpected = expected('sop-impact-conflict-provenance');
    const desktopApi = await page.evaluate(async () => {
      const response = await fetch('/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z');
      return { status: response.status, body: await response.json() };
    });
    check('desktop browser API returns the pinned exact paths and source chronology', desktopApi.status === 200 &&
      equal(desktopApi.body.impact_paths, observedExpected.impact_paths) && equal(desktopApi.body.path_evidence, observedExpected.path_evidence), desktopApi);
    const normalizeText = text => text.replace(/\s+/g, ' ').trim();
    const visiblePathRows = (await page.locator('#sop-impact-paths .sop-impact-path').allInnerTexts()).map(normalizeText);
    const expectedVisiblePathRows = observedExpected.impact_paths.map((path, index) => {
      const evidence = observedExpected.path_evidence[index];
      const trail = evidence.source_ids.map((id, sourceIndex) => `${id} · ${evidence.source_titles[sourceIndex]} · observed ${evidence.observed_at[sourceIndex]}`).join(' → ');
      return normalizeText(`${path.join(' → ')} CURRENT OWNER PATH · ${trail}`);
    });
    check('rendered desktop path rows and each source ID/title/time match the live API snapshot',
      equal(visiblePathRows, expectedVisiblePathRows), { rendered: visiblePathRows, expected: expectedVisiblePathRows });
    check('desktop trace labels role IDs as unverified SOP claims, separate from owner paths',
      await page.locator('.sop-impact-claim strong').allInnerTexts().then(rows => rows.join('|')) ===
        'SOP-OPS-9-V2 · unverified SOP claim → ROLE-FACILITIES-DUTY|SOP-OPS-9-V3 · unverified SOP claim → ROLE-MATERIALS-LEAD',
      await page.locator('.sop-impact-claim strong').allInnerTexts());
    const edgeText = await page.locator('#sop-impact-edge').innerText();
    check('desktop view exposes explicit applicability and unresolved conflict relationship types',
      edgeText.includes('SOP-OPS-9-V2 -[APPLIES_TO]-> CONVEYOR-C4') &&
      edgeText.includes('SOP-OPS-9-V3 -[APPLIES_TO]-> CONVEYOR-C4') &&
      edgeText.includes('SOP-OPS-9-V2 -[SOP_CONFLICT · unresolved]-> SOP-OPS-9-V3'), edgeText);
    check('desktop view preserves stale V1/C3 as visible history outside C4 witness',
      (await page.locator('#sop-impact-stale').innerText()).includes('SOP-OPS-9-V1') &&
      (await page.locator('#sop-impact-stale').innerText()).includes('CONVEYOR-C3') &&
      (await page.locator('#sop-impact-stale').innerText()).includes('not joined to the C4 conflict witness'),
      await page.locator('#sop-impact-stale').innerText());
    check('desktop browser APIs retain the two source-backed fixed claims',
      equal(desktopApi.body.role_claims, conflictExpected.claim_evidence) &&
      desktopApi.body.winner_selected === false && desktopApi.body.claims_are_current_owner_facts === false,
      desktopApi.body.role_claims);

    const offset = await page.evaluate(async () => {
      const response = await fetch('/api/sop-impact?scope=operations&as_of=2026-10-14T20%3A30%3A00%2B02%3A00');
      return { status: response.status, body: await response.json() };
    });
    check('browser offset request normalizes to the canonical UTC result', offset.status === 200 &&
      offset.body.as_of === expected('sop-impact-time-browser-local-boundary').normalized_as_of &&
      equal(offset.body.impact_paths, observedExpected.impact_paths), offset.body);
    await page.screenshot({ path: path.join(screenshotDirectory, 'sop-impact-desktop.png'), fullPage: true });

    await page.getByRole('button', { name: 'Restored · 19:30Z' }).click();
    await page.waitForFunction(() => document.querySelector('#sop-impact-as-of')?.textContent === '2026-10-14T19:30:00Z');
    check('desktop restoration empties active paths while unresolved SOP claims persist',
      (await page.locator('#sop-impact-paths').innerText()).includes('No active outage paths') &&
      (await page.locator('#sop-impact-edge').innerText()).includes('unresolved') &&
      await page.locator('.sop-impact-claim').count() === 2 &&
      (await page.locator('#sop-impact-paths').innerText()).includes('remains a separate unresolved fact'),
      { paths: await page.locator('#sop-impact-paths').innerText(), claims: await page.locator('.sop-impact-claim').allInnerTexts() });
    await page.getByRole('button', { name: 'Observed · 18:30Z' }).click();
    await page.waitForFunction(() => document.querySelector('#sop-impact-as-of')?.textContent === '2026-10-14T18:30:00Z');
    const desktopTraceText = await page.locator('#sop-impact-content').innerText();

    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(100);
    const mobile = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    check('mobile layout has no page-level horizontal overflow', mobile.document <= mobile.viewport + 2 && mobile.body <= mobile.viewport + 2, mobile);
    check('desktop/mobile impact trace content is semantically identical', await page.locator('#sop-impact-content').innerText().then(text => text === desktopTraceText), await page.locator('#sop-impact-content').innerText());
    const mobileApi = await page.evaluate(async () => {
      const response = await fetch('/api/sop-impact?scope=operations&as_of=2026-10-14T18%3A30%3A00Z');
      return { status: response.status, body: await response.json() };
    });
    check('mobile API and desktop API return identical fixed semantic snapshots', mobileApi.status === 200 &&
      equal(mobileApi.body.impact_paths, desktopApi.body.impact_paths) &&
      equal(mobileApi.body.path_evidence, desktopApi.body.path_evidence) &&
      equal(mobileApi.body.role_claims, desktopApi.body.role_claims) &&
      equal(mobileApi.body.conflict_edge, desktopApi.body.conflict_edge),
      { status: mobileApi.status, paths: mobileApi.body.impact_paths, claims: mobileApi.body.role_claims });
    await page.screenshot({ path: path.join(screenshotDirectory, 'sop-impact-mobile.png'), fullPage: true });
    check('all browser requests stay on the explicitly selected loopback origin', hosts.size === 1 && [...hosts][0] === new URL(url).host, [...hosts]);
    check('one expected HTTP 503 is isolated to the deliberate SOP endpoint failure probe', failSopImpactOnce === false && expectedConsoleErrors.length === 1, expectedConsoleErrors);
    check('browser finishes without console or page errors', errors.length === 0, errors);

    const report = { status: 'PASS', url, assertion_count: checks.length, checks, browser_errors: errors,
      requested_hosts: [...hosts], expected_console_errors: expectedConsoleErrors, desktop_viewport: { width: 1440, height: 1000 }, mobile_viewport: mobile,
      screenshots: [path.join(screenshotDirectory, 'sop-impact-desktop.png'), path.join(screenshotDirectory, 'sop-impact-mobile.png')] };
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.mkdirSync(screenshotDirectory, { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify({ status: report.status, assertion_count: report.assertion_count, requested_hosts: report.requested_hosts, mobile_viewport: mobile }));
  } catch (error) {
    const report = { status: 'FAIL', url, assertion_count: checks.length, checks, error: String(error), browser_errors: errors, requested_hosts: [...hosts] };
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    console.error(error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
