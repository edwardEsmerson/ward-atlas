#!/usr/bin/env node
// Interaction assertions for the locally served WardAtlas app. No remote hosts.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');
const shiftExpected = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'tests/shift-board-expected-results.json'), 'utf8'));

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
  let confirmationResponse = null;
  const check = (name, condition, detail) => {
    checks.push({ name, passed: Boolean(condition), detail: detail ?? null });
    if (!condition) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('request', request => { try { hosts.add(new URL(request.url()).host); } catch (_) {} });
  page.on('response', async response => {
    try {
      if (new URL(response.url()).pathname === '/api/handover/confirm') confirmationResponse = { status: response.status(), body: await response.json() };
    } catch (_) {}
  });
  try {
    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
    check('desktop app responds successfully', response && response.status() === 200, response && response.status());
    await page.locator('#shift-board-empty').waitFor({ state: 'visible' });
    await page.getByRole('heading', { name: 'A board with no defaults.' }).waitFor();
    check('cold browser board visibly says empty/unknown without default incident cases', await page.locator('#shift-board-badge').innerText() === 'Empty · unknown' && await page.locator('#dock-case').isHidden() && await page.locator('#conveyor-case').isHidden() && await page.locator('#legacy-grid').isHidden(), await page.locator('#shift-board-empty').innerText());
    const coldBody = await page.locator('body').innerText();
    check('cold browser has no invented incident, source, role, review, or SOP identifier', await page.locator('#dock-sources').innerText().then(text => text === '') && await page.locator('#dock-review').innerText().then(text => text === '') && !/INC-\d+|SRC-|ROLE-|HND-|TKT-|SOP-/.test(coldBody) && (await page.locator('#incident-status').innerText()).toLowerCase() === 'no graph-backed incident', coldBody);
    await page.getByRole('button', { name: 'Reset synthetic scenario' }).click();
    await page.getByRole('heading', { name: 'One outage. Two accountable paths.' }).waitFor();
    await page.locator('#dock-case').waitFor({ state: 'visible' });
    await page.locator('#conveyor-case').waitFor({ state: 'visible' });
    await page.getByText('INC-204 · OPEN', { exact: true }).waitFor();
    await page.getByText('Central sterile operations ← Material transfer service', { exact: true }).waitFor();
    await page.getByText('Materials logistics ← Material transfer service', { exact: true }).waitFor();
    const visibleRoles = await page.locator('.path-row .role-label').allInnerTexts();
    check('only the two explicitly current operating roles are visible', visibleRoles.join('|') === 'CURRENT · Sterile operations coordinator|CURRENT · Materials duty role', visibleRoles);
    await page.getByText('Stale reference · SOP-OPS-9-V1', { exact: true }).waitFor();
    await page.getByText('SOP-OPS-9-V2 claims ROLE-FACILITIES-DUTY', { exact: true }).waitFor();
    await page.getByText('SOP-OPS-9-V3 claims ROLE-MATERIALS-LEAD', { exact: true }).waitFor();
    check('graph impact, current roles and unresolved SOP claims render', true);

    const dockExpected = shiftExpected.cases.find(row => row.id === 'dock-role-time-and-source-evidence').expected;
    await page.locator('#dock-time').selectOption(dockExpected.before.as_of);
    await page.waitForFunction(() => document.querySelector('#dock-role-badge')?.textContent === 'ROLE-DOCK-DUTY');
    check('desktop dock panel shows the 20:29 source-backed ROLE-DOCK-DUTY path', await page.locator('#dock-role-badge').innerText() === 'ROLE-DOCK-DUTY' && await page.locator('#dock-path').innerText().then(text => text.includes(dockExpected.before.role_path.join(' → '))), await page.locator('#dock-path').innerText());
    await page.locator('#dock-time').selectOption(dockExpected.at_effective_time.as_of);
    await page.waitForFunction(() => document.querySelector('#dock-role-badge')?.textContent === 'ROLE-DOCK-SWING');
    const dockSources = await page.locator('#dock-sources').innerText();
    check('desktop dock panel exposes both roster versions and observed/effective timestamps', dockSources.includes('SRC-DOCK-ROSTER-R1') && dockSources.includes('2026-10-14T19:00:00Z') && dockSources.includes('SRC-DOCK-ROSTER-R2') && dockSources.includes('2026-10-14T20:18:00Z') && dockSources.includes(dockExpected.source_versions[1].effective_at), dockSources);
    const reviewExpected = shiftExpected.cases.find(row => row.id === 'explicit-edge-review-queue').expected;
    const reviewText = await page.locator('#dock-review').innerText();
    check('desktop review queue uses only the two explicit edges and excludes the lexical decoy', reviewText.includes(reviewExpected.review_candidates[0]) && reviewText.includes(reviewExpected.review_candidates[1]) && !reviewText.includes(reviewExpected.excluded_lexical_only_id) && reviewText.includes(reviewExpected.unchanged_handover_role_id) && reviewText.includes(reviewExpected.unchanged_handover_source_id), reviewText);
    check('desktop review coverage treats unlinked artifacts as unknown', (await page.locator('#dock-coverage').innerText()).includes('Unlinked artifact status: unknown'), await page.locator('#dock-coverage').innerText());

    const conveyorExpected = shiftExpected.cases.find(row => row.id === 'conveyor-observed-and-hypothetical-times').expected;
    const expectedPathText = conveyorExpected.observed_at_1830.paths.map(path => path.join('  →  '));
    let renderedConveyorPaths = await page.locator('#conveyor-paths .shift-path').allInnerTexts();
    check('desktop conveyor timeline shows both exact 18:30 graph paths', JSON.stringify(renderedConveyorPaths) === JSON.stringify(expectedPathText.map(path => path.replaceAll('  →  ', ' → '))), renderedConveyorPaths);
    check('desktop timeline labels the event as observed with its source provenance', (await page.locator('#conveyor-event').innerText()).includes('EVT-C4-UNAVAILABLE-01') && (await page.locator('#conveyor-event').innerText()).includes('SRC-ASSET-C4-OUTAGE') && (await page.locator('#conveyor-event').innerText()).includes('Observed source fact'), await page.locator('#conveyor-event').innerText());
    const branchExpected = conveyorExpected.hypothetical_branch_1850;
    const branchText = await page.locator('#branch-result').innerText();
    check('desktop branch is hypothetical and does not rewrite mainline paths', branchText.includes(branchExpected.branch_id) && branchText.includes(branchExpected.hypothetical_event_id) && branchText.includes('hypothetical branch') && branchText.includes('Mainline remains observed-impact with 2 paths') && branchText.includes('observed state mutated: false'), branchText);
    await page.getByRole('button', { name: 'Restored · 19:30Z' }).click();
    await page.waitForFunction(() => document.querySelector('#conveyor-status-badge')?.textContent === 'restored');
    check('desktop timeline selects the observed 19:30 restoration with zero active paths', await page.locator('#conveyor-paths').innerText().then(text => text.includes('No active impact paths at this observed time.')) && (await page.locator('#conveyor-event').innerText()).includes('EVT-C4-RESTORED-01'), { event: await page.locator('#conveyor-event').innerText(), paths: await page.locator('#conveyor-paths').innerText() });
    await page.getByRole('button', { name: 'Observed · 18:30Z' }).click();
    await page.waitForFunction(() => document.querySelector('#conveyor-status-badge')?.textContent === 'observed-impact');
    await page.getByRole('button', { name: 'Reset branch overlay' }).click();
    await page.waitForFunction(() => document.querySelector('#branch-result')?.textContent.includes('No branch overlay is seeded'));
    check('browser branch reset preserves the observed mainline impact', (await page.locator('#conveyor-status-badge').innerText()) === 'observed-impact' && await page.locator('#conveyor-paths .shift-path').count() === 2, await page.locator('#conveyor-paths').innerText());
    await page.getByRole('button', { name: 'Reset synthetic scenario' }).click();
    await page.waitForFunction(() => document.querySelector('#branch-result')?.textContent.includes('EVT-BR-RESTORE-01'));

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
    check('confirmation endpoint reports exactly one local action and zero external writes', confirmationResponse?.status === 201 && confirmationResponse.body.persisted === true && confirmationResponse.body.storage === 'local-only' && confirmationResponse.body.external_effects.length === 0 && shiftExpected.cases.find(row => row.id === 'explicit-local-confirmation-no-external-writes').expected.external_writes.length === 0, confirmationResponse);

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
