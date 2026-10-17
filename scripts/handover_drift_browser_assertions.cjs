#!/usr/bin/env node
// Browser evidence for comparing an immutable local handover with live graph state.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');

const [url, reportPath, screenshotDirectory] = process.argv.slice(2);
if (!url || !reportPath || !screenshotDirectory || !['localhost', '127.0.0.1'].includes(new URL(url).hostname)) {
  throw new Error('Usage: node scripts/handover_drift_browser_assertions.cjs http://127.0.0.1:PORT /workspace/evidence/report.json /workspace/evidence/screenshots');
}
const oracle = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'tests/handover-drift-expected-results.json'), 'utf8'));
const byId = Object.fromEntries(oracle.cases.map(row => [row.id, row.expected]));
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  const expected400s = [];
  const badRequestResponses = [];
  const hosts = new Set();
  const requests = [];
  const checks = [];
  const report = { status: 'running', assertion_count: 0, assertions: checks, hosts: [], requests, errors, expected_400_responses: expected400s, screenshots: [] };
  fs.mkdirSync(screenshotDirectory, { recursive: true });
  const check = (name, condition, detail) => {
    checks.push({ name, passed: Boolean(condition), detail: detail ?? null });
    if (!condition) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => {
    if (message.type() !== 'error') return;
    if (message.text() === 'Failed to load resource: the server responded with a status of 400 (Bad Request)') expected400s.push(message.text());
    else errors.push(message.text());
  });
  page.on('response', response => {
    if (response.status() === 400) badRequestResponses.push({ status: response.status(), path: new URL(response.url()).pathname });
  });
  page.on('request', request => {
    try { hosts.add(new URL(request.url()).host); } catch (_) {}
    requests.push({ method: request.method(), url: new URL(request.url()).pathname });
  });
  try {
    const pageResponse = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
    check('desktop browser opens the selected loopback app', pageResponse && pageResponse.status() === 200, pageResponse && pageResponse.status());
    const savedCard = page.locator('#saved .saved-item').first();
    await savedCard.waitFor({ state: 'visible', timeout: 15000 });
    const snapshot = JSON.parse(await savedCard.locator('.saved-snapshot').getAttribute('data-snapshot'));
    const actionId = await savedCard.locator('[data-compare-action]').getAttribute('data-compare-action');
    const beforeActions = await page.evaluate(async () => (await (await fetch('/api/actions')).json()));
    check('confirmed action exposes the exact immutable canonical snapshot', equal(snapshot, oracle.canonical_snapshot) && beforeActions.count === 1 && beforeActions.actions[0].id === actionId, { snapshot, actions: beforeActions });

    await savedCard.locator('[data-compare-action]').click();
    const result = savedCard.locator(`[data-compare-result="${actionId}"]`);
    await result.waitFor({ state: 'visible' });
    await page.waitForFunction(id => {
      const node = document.querySelector(`[data-compare-result="${CSS.escape(id)}"]`);
      return Boolean(node && node.dataset.comparison);
    }, actionId, { timeout: 20000 });
    const comparison = JSON.parse(await result.getAttribute('data-comparison'));
    const restoreExpected = byId['handover-drift-observed-restoration-delta'];
    const branchExpected = byId['handover-drift-hypothetical-branch-isolation'];
    check('comparison identifies the saved historical snapshot and separate confirmation timestamp',
      equal(comparison.historical_snapshot, oracle.canonical_snapshot) && comparison.action.confirmed_at === beforeActions.actions[0].confirmed_at && comparison.historical_snapshot_label === 'saved historical evidence',
      { action: comparison.action, historical_snapshot: comparison.historical_snapshot });
    check('observed restoration shows exactly the pinned removed paths and no additions',
      comparison.observed.as_of === restoreExpected.as_of && comparison.observed.current_state === restoreExpected.current_status &&
      JSON.stringify(comparison.observed.current_impact_paths) === JSON.stringify(restoreExpected.current_active_paths) &&
      JSON.stringify(comparison.observed.added_paths) === JSON.stringify(restoreExpected.added_paths) &&
      JSON.stringify(comparison.observed.removed_paths.map(row => row.path)) === JSON.stringify(restoreExpected.removed_paths),
      comparison.observed);
    check('removed path evidence names the pinned source IDs, titles and observation times',
      equal(comparison.observed.removed_paths.map(row => row.provenance), oracle.canonical_snapshot.impact.path_evidence),
      comparison.observed.removed_paths);
    check('observed restoration carries provenance and leaves SOP disagreement unresolved and unchanged',
      comparison.observed.restoration_source_fact?.event_id === restoreExpected.restoration_source_fact.event_id &&
      comparison.observed.restoration_source_fact?.source_id === restoreExpected.restoration_source_fact.source_id &&
      comparison.observed.restoration_source_fact?.event_at === restoreExpected.restoration_source_fact.event_at &&
      comparison.observed.sop_conflict_status === 'unresolved' && comparison.observed.sop_witness_unchanged === true &&
      equal(comparison.observed.sop_witness, oracle.canonical_snapshot.sop_witness),
      { restoration: comparison.observed.restoration_source_fact, witness: comparison.observed.sop_witness });
    check('hypothetical drill is isolated from the observed mainline',
      comparison.hypothetical_branch.as_of === branchExpected.as_of && comparison.hypothetical_branch.branch_id === branchExpected.branch_id &&
      comparison.hypothetical_branch.status === branchExpected.branch_status && comparison.hypothetical_branch.delta_label === 'hypothetical' &&
      comparison.hypothetical_branch.paths.length === 0 && comparison.hypothetical_branch.observed_mainline_status === branchExpected.observed_mainline_status &&
      JSON.stringify(comparison.hypothetical_branch.observed_mainline_paths.map(row => row.path)) === JSON.stringify(branchExpected.observed_mainline_paths) &&
      comparison.hypothetical_branch.observed_state_mutated === false,
      comparison.hypothetical_branch);
    const desktopText = await result.innerText();
    check('desktop view visibly renders both removed path provenance trails and unresolved SOP witness',
      desktopText.includes('SRC-INC-204') && desktopText.includes('Synthetic Service Dependency Register') &&
      desktopText.includes('2026-10-14T18:05:00Z') && desktopText.includes('unresolved') && desktopText.includes('SOP-OPS-9-V2'), desktopText);
    check('desktop view distinguishes hypothetical empty branch from two-path observed mainline',
      desktopText.includes('HYPOTHETICAL ONLY') && desktopText.includes('observed-impact · 2 paths · observed state mutated: false') &&
      desktopText.includes('No branch paths in this hypothetical state'), desktopText);
    await page.screenshot({ path: path.join(screenshotDirectory, 'handover-drift-desktop.png'), fullPage: true });
    report.screenshots.push(path.join(screenshotDirectory, 'handover-drift-desktop.png'));

    const offsetResponse = await page.evaluate(async action => {
      const query = new URLSearchParams({ scope: 'operations', action_id: action, observed_at: '2026-10-14T20:30:00+02:00', branch_at: '2026-10-14T20:50:00+02:00' });
      const response = await fetch(`/api/handover/compare?${query}`);
      return { status: response.status, body: await response.json() };
    }, actionId);
    check('browser-triggered API normalizes both offset times to UTC', offsetResponse.status === 200 &&
      offsetResponse.body.observed.as_of === '2026-10-14T18:30:00Z' && offsetResponse.body.hypothetical_branch.as_of === '2026-10-14T18:50:00Z', offsetResponse);
    const malformedResponse = await page.evaluate(async action => {
      const query = new URLSearchParams({ scope: 'operations', action_id: action, observed_at: 'not-a-timestamp', branch_at: '2026-10-14T18:50:00Z' });
      const response = await fetch(`/api/handover/compare?${query}`);
      return { status: response.status, body: await response.json() };
    }, actionId);
    check('browser/API rejects malformed time before comparison', malformedResponse.status === 400 && /Invalid timestamp/.test(malformedResponse.body.error), malformedResponse);
    check('the only HTTP 400 observed by the browser is the explicitly expected malformed-time probe', badRequestResponses.length === 1 && badRequestResponses[0].path === '/api/handover/compare', badRequestResponses);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: 'networkidle' });
    const mobileCard = page.locator('#saved .saved-item').first();
    await mobileCard.locator('[data-compare-action]').click();
    const mobileResult = mobileCard.locator(`[data-compare-result="${actionId}"]`);
    await page.waitForFunction(id => {
      const node = document.querySelector(`[data-compare-result="${CSS.escape(id)}"]`);
      return Boolean(node && node.dataset.comparison);
    }, actionId, { timeout: 20000 });
    const mobileComparison = JSON.parse(await mobileResult.getAttribute('data-comparison'));
    const mobileText = await mobileResult.innerText();
    const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    check('mobile comparison keeps desktop semantics and has no horizontal overflow',
      equal(mobileComparison, comparison) && mobileText === desktopText && dimensions.scrollWidth <= dimensions.width,
      { dimensions, mobileText });
    await page.screenshot({ path: path.join(screenshotDirectory, 'handover-drift-mobile.png'), fullPage: true });
    report.screenshots.push(path.join(screenshotDirectory, 'handover-drift-mobile.png'));

    const afterActions = await page.evaluate(async () => (await (await fetch('/api/actions')).json()));
    check('read-only browser comparison leaves the confirmed action and its snapshot unchanged',
      equal(afterActions, beforeActions) && equal(afterActions.actions[0].snapshot, oracle.canonical_snapshot), afterActions);
    const unsafeRequests = requests.filter(item => item.method !== 'GET' && item.url.startsWith('/api/handover/'));
    check('comparison UI issues only GETs; no draft, confirmation or external write is triggered', unsafeRequests.length === 0, unsafeRequests);
    check('browser requests remain on selected loopback origin with no unexpected errors',
      [...hosts].every(host => /^127\.0\.0\.1:\d+$|^localhost:\d+$/.test(host)) && errors.length === 0 && expected400s.length === 1,
      { hosts: [...hosts], errors, expected400s });
    report.status = 'PASS';
    report.hosts = [...hosts];
  } catch (error) {
    report.status = 'FAIL';
    report.error = String(error);
    throw error;
  } finally {
    report.assertion_count = checks.length;
    report.hosts = [...hosts];
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
