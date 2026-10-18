#!/usr/bin/env node
// Desktop/mobile and keyboard proof for the graph-backed source-time branch.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');

const [url, mode, reportPath, screenshotDirectory] = process.argv.slice(2);
if (!url || !['cold-seed', 'existing', 'missing-branch'].includes(mode) || !reportPath || !screenshotDirectory || !['localhost', '127.0.0.1'].includes(new URL(url).hostname)) {
  throw new Error('Usage: node scripts/decision_source_branch_browser_assertions.cjs http://127.0.0.1:PORT cold-seed|existing|missing-branch REPORT.json SCREENSHOT_DIR');
}

const root = path.resolve(__dirname, '..');
const oracle = JSON.parse(fs.readFileSync(path.join(root, 'contracts/decision-source-branch/expected-results.json'), 'utf8'));
const group = id => oracle.groups.find(item => item.id === id).expected;
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const assertions = [];
  const errors = [];
  const hosts = new Set();
  const screenshots = [];
  let expectedBadRequestConsoleErrors = 0;
  const check = (name, pass, detail = null) => {
    assertions.push({ name, passed: Boolean(pass), detail });
    if (!pass) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  const snapshot = async cutoff => {
    const branchApi = await page.evaluate(async knownAt => {
      const query = new URLSearchParams({ scope: 'operations', known_at: knownAt });
      return await (await fetch(`/api/decision-source-branch?${query}`)).json();
    }, cutoff);
    return {
      cutoff,
      api: branchApi,
      text: await page.locator('#decision-source-branch').innerText(),
      accessibility_tree: await page.locator('#decision-source-branch').ariaSnapshot(),
      mainline_text: await page.locator('#source-branch-mainline').innerText(),
      hypothetical_text: await page.locator('#source-branch-hypothetical').innerText(),
    };
  };
  const screenshot = async label => {
    const file = path.join(screenshotDirectory, `source-branch-${label}.png`);
    await page.screenshot({ path: file, fullPage: true });
    screenshots.push(file);
  };
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('request', request => { try { hosts.add(new URL(request.url()).host); } catch (_) {} });
  fs.mkdirSync(screenshotDirectory, { recursive: true });
  const report = { schema_version: 1, status: 'running', mode, assertions, assertion_count: 0, errors, hosts: [], screenshots, desktop: null, keyboard: null, mobile: null };
  try {
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    check('loopback WardAtlas page is available', response?.status() === 200, response?.status());
    await page.waitForFunction(() => document.querySelector('#source-branch-cutoff')?.value === '2026-10-14T16:05:00Z');

    if (mode === 'cold-seed') {
      const cold = await page.evaluate(async () => await (await fetch('/api/decision-source-branch?scope=operations')).json());
      const coldExpected = group('cold-empty-branch-lookup');
      const coldProjection = cold.hypothetical_branch?.projection;
      check('cold browser/API projection is generic unknown without branch identity', same(coldProjection, coldExpected.projection) && !JSON.stringify(cold).includes('BR-SOP-V3-LATE-OBS') && !JSON.stringify(cold).includes('OBS-SOP-V3-LATE-OBS') && !JSON.stringify(cold).includes('SRC-SOP-CURRENT-V3'), cold);
      check('cold operations view has no count, existence, or absence hint', !Object.keys(cold).some(key => key.toLowerCase().includes('count') || key.toLowerCase().includes('existence')) && coldProjection?.generic_unknown === true && coldProjection?.negative_conclusion_asserted === false, cold);
      check('cold UI does not present a branch/source existence hint before any mainline evidence exists', await page.locator('#decision-source-branch').isHidden(), await page.locator('body').innerText());
      await page.locator('#reset').click();
      await page.waitForFunction(() => document.querySelector('#source-branch-badge')?.textContent?.includes('Graph-linked hypothetical'));
      await page.waitForFunction(() => document.querySelector('#source-branch-hypothetical')?.innerText?.includes('DEC-OPS-9-REVIEW-01'));
    }

    if (mode === 'missing-branch') {
      await page.waitForFunction(() => document.querySelector('#source-branch-badge')?.textContent?.includes('Branch evidence unknown'));
      const missingExpected = group('missing-edge-no-fallback-and-reseed').missing_override_to_source_edge_result.projection;
      const current = await snapshot('2026-10-14T16:05:00Z');
      check('missing graph edge renders exact unknown branch while mainline remains separately visible', same(current.api.hypothetical_branch?.projection, missingExpected) && current.api.mainline.claims.some(claim => claim.version_id === 'SOP-OPS-9-V3'), current.api);
      check('missing branch pane exposes no branch or source identity and never falls back to mainline', !current.api.hypothetical_branch?.scenario && !/BR-SOP-V3-LATE-OBS|OBS-SOP-V3-LATE-OBS|SRC-SOP-CURRENT-V3|SOP-OPS-9-V3|ROLE-MATERIALS-LEAD|Current Text v3/.test(current.hypothetical_text) && current.hypothetical_text.includes('Unknown / incomplete'), current.hypothetical_text);
      report.desktop = current;
      await screenshot('missing-desktop-1605');
    }

    const at1605 = mode === 'missing-branch' ? null : await snapshot('2026-10-14T16:05:00Z');
    if (mode === 'missing-branch') {
      const selector = page.getByRole('combobox', { name: 'Shared mainline and hypothetical knowledge cutoff' });
      await selector.focus();
      await page.keyboard.press('ArrowDown');
      await page.waitForFunction(() => document.querySelector('#source-branch-cutoff')?.value === '2026-10-14T16:06:00Z');
      const at1606 = await snapshot('2026-10-14T16:06:00Z');
      const missing1606 = { ...group('missing-edge-no-fallback-and-reseed').missing_override_to_source_edge_result.projection, known_at: '2026-10-14T16:06:00Z' };
      check('keyboard replay of a missing graph edge stays unknown at 16:06 without mainline fallback', same(at1606.api.hypothetical_branch?.projection, missing1606) && !at1606.api.hypothetical_branch?.scenario && at1606.api.mainline.claims.some(claim => claim.version_id === 'SOP-OPS-9-V3'), at1606.api);
      report.keyboard = at1606;
      await page.setViewportSize({ width: 390, height: 844 });
      const mobile = await snapshot('2026-10-14T16:06:00Z');
      const layout = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth, main: document.querySelector('#source-branch-mainline').getBoundingClientRect(), hypothetical: document.querySelector('#source-branch-hypothetical').getBoundingClientRect() }));
      check('missing-branch desktop/mobile text and accessibility remain equivalent with no mobile overflow', mobile.text === at1606.text && mobile.accessibility_tree === at1606.accessibility_tree && layout.scrollWidth <= layout.width && layout.hypothetical.top > layout.main.top, { layout, text_equal: mobile.text === at1606.text, accessibility_equal: mobile.accessibility_tree === at1606.accessibility_tree });
      report.mobile = { ...mobile, layout };
      await screenshot('missing-mobile-1606');
      check('browser requests stay on its loopback origin and page has no runtime errors', [...hosts].every(host => host === new URL(url).host) && errors.length === 0, { hosts: [...hosts], errors });
      report.status = 'PASS';
    } else {
    check('desktop two-pane API equals the complete frozen clean projection', same(at1605.api, group('decoy-and-restricted-invariance').clean_full_operations_projection), at1605.api);
    check('desktop mainline preserves V3 at actual observed time 16:04Z', at1605.api.mainline.claims.some(row => row.version_id === 'SOP-OPS-9-V3' && row.source.observed_at === '2026-10-14T16:04:00Z') && at1605.mainline_text.includes('2026-10-14T16:04:00Z'), at1605);
    const branch1605 = at1605.api.hypothetical_branch;
    check('16:05 branch suppresses V3 and conflict but retains V2 and open-unresolved record', same(branch1605.projection, group('branch-at-16-05-suppression').projection) && branch1605.projection.decision_record?.recorded_state === 'open-unresolved', branch1605);
    check('16:05 branch pane contains no V3 ID, title, role, source, or relationship', !/SOP-OPS-9-V3|ROLE-MATERIALS-LEAD|SRC-SOP-CURRENT-V3|Current Text v3|SOP_CONFLICT|CONSIDERS.*V3/.test(at1605.hypothetical_text), at1605.hypothetical_text);
    check('16:05 branch pane labels the scenario and retained record without choosing a winner', at1605.hypothetical_text.includes('Hypothetical observation-time branch') && at1605.hypothetical_text.includes('16:04:00Z') && at1605.hypothetical_text.includes('16:06:00Z') && at1605.hypothetical_text.includes('open-unresolved') && at1605.hypothetical_text.includes('SOP-OPS-9-V2'), at1605.hypothetical_text);
    const desktopLayout = await page.evaluate(() => {
      const left = document.querySelector('#source-branch-mainline').getBoundingClientRect();
      const right = document.querySelector('#source-branch-hypothetical').getBoundingClientRect();
      const select = document.querySelector('#source-branch-cutoff');
      return { leftTop: left.top, rightTop: right.top, leftRight: left.right, rightLeft: right.left, selectRole: select.getAttribute('aria-label'), grid: getComputedStyle(document.querySelector('.source-branch-grid')).gridTemplateColumns };
    });
    check('desktop panes are side-by-side with a named native keyboard selector', Math.abs(desktopLayout.leftTop - desktopLayout.rightTop) < 2 && desktopLayout.rightLeft > desktopLayout.leftRight && desktopLayout.selectRole === 'Shared mainline and hypothetical knowledge cutoff', desktopLayout);
    report.desktop = at1605;
    await screenshot('desktop-1605');

    // This changes a real native select using keyboard events, exercising the
    // same change handler a keyboard user operates rather than a JS-only path.
    const selector = page.getByRole('combobox', { name: 'Shared mainline and hypothetical knowledge cutoff' });
    await selector.focus();
    await page.keyboard.press('ArrowDown');
    await page.waitForFunction(() => document.querySelector('#source-branch-cutoff')?.value === '2026-10-14T16:06:00Z');
    await page.waitForFunction(() => document.querySelector('#source-branch-hypothetical')?.innerText?.includes('SOP-OPS-9-V3'));
    const at1606 = await snapshot('2026-10-14T16:06:00Z');
    check('keyboard selection agrees with the frozen API/mainline 16:06 projection', same(at1606.api, {
      scope: 'operations',
      mainline: group('mainline-isolation').mainline_at_16_06,
      hypothetical_branch: group('branch-at-16-06-restoration').scenario_not_source_truth === true
        ? { scenario: group('branch-at-16-06-restoration').branch_assumption, projection: group('branch-at-16-06-restoration').projection }
        : null,
    }), at1606.api);
    check('keyboard branch restores V3 and unresolved conflict only as a hypothetical observation', at1606.api.hypothetical_branch.projection.claims.some(row => row.version_id === 'SOP-OPS-9-V3' && row.source.observed_at === '2026-10-14T16:06:00Z') && at1606.api.hypothetical_branch.projection.conflict?.edge.status === 'unresolved' && at1606.api.hypothetical_branch.scenario.actual_source_observed_at === '2026-10-14T16:04:00Z', at1606.api.hypothetical_branch);
    check('keyboard-selected cutoff is the API value and branch view explicitly says hypothetical', at1606.cutoff === '2026-10-14T16:06:00Z' && at1606.hypothetical_text.includes('Hypothetical observation 2026-10-14T16:06:00Z') && at1606.hypothetical_text.includes('actual source observation 2026-10-14T16:04:00Z'), at1606);
    report.keyboard = at1606;
    await screenshot('keyboard-1606');

    await page.setViewportSize({ width: 390, height: 844 });
    const mobile = await snapshot('2026-10-14T16:06:00Z');
    const mobileLayout = await page.evaluate(() => {
      const left = document.querySelector('#source-branch-mainline').getBoundingClientRect();
      const right = document.querySelector('#source-branch-hypothetical').getBoundingClientRect();
      return { width: innerWidth, scrollWidth: document.documentElement.scrollWidth, leftTop: left.top, rightTop: right.top, columns: getComputedStyle(document.querySelector('.source-branch-grid')).gridTemplateColumns };
    });
    check('mobile shows stacked panes with no horizontal overflow', mobileLayout.scrollWidth <= mobileLayout.width && mobileLayout.rightTop > mobileLayout.leftTop && mobileLayout.columns.split(' ').length === 1, mobileLayout);
    check('mobile API, rendered text, accessibility tree, and selected cutoff match desktop keyboard view', same(mobile.api, at1606.api) && mobile.text === at1606.text && mobile.accessibility_tree === at1606.accessibility_tree && await selector.inputValue() === '2026-10-14T16:06:00Z', { mobileLayout, text_equal: mobile.text === at1606.text, accessibility_equal: mobile.accessibility_tree === at1606.accessibility_tree });
    report.mobile = { ...mobile, layout: mobileLayout };
    await screenshot('mobile-1606');
    check('browser requests stay on its loopback origin and page has no runtime errors', [...hosts].every(host => host === new URL(url).host) && errors.length === 0, { hosts: [...hosts], errors });
    report.status = 'PASS';
    }
  } catch (error) {
    report.status = 'FAIL';
    report.error = String(error?.stack || error);
  } finally {
    report.assertions = assertions;
    report.assertion_count = assertions.length;
    report.errors = errors;
    report.hosts = [...hosts];
    report.expected_bad_request_console_errors = expectedBadRequestConsoleErrors;
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    await browser.close();
  }
  if (report.status !== 'PASS') {
    process.stderr.write(`${report.error || 'source branch browser assertions failed'}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write(`decision source branch browser: PASS (${report.assertion_count} assertions, keyboard selection, desktop/mobile)\n`);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
