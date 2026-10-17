#!/usr/bin/env node
// Responsive proof for the new source-observation-time replay component.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');

const [url, mode, reportPath, screenshotDirectory] = process.argv.slice(2);
if (!url || !['cold-seed-replay', 'baseline-cutoff', 'shifted-cutoff'].includes(mode) || !reportPath || !screenshotDirectory || !['localhost', '127.0.0.1'].includes(new URL(url).hostname)) {
  throw new Error('Usage: node scripts/decision_knowledge_timeline_browser_assertions.cjs http://127.0.0.1:PORT cold-seed-replay|baseline-cutoff|shifted-cutoff REPORT.json SCREENSHOT_DIR');
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const checks = [];
  const errors = [];
  let expectingBadRequest = false;
  let expectedBadRequestConsoleErrors = 0;
  const hosts = new Set();
  const screenshots = [];
  const check = (name, passed, detail = null) => {
    checks.push({ name, passed: Boolean(passed), detail });
    if (!passed) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  const capture = async () => ({
    text: await page.locator('#decision-knowledge-timeline').innerText(),
    accessibility_tree: await page.locator('#decision-knowledge-timeline').ariaSnapshot(),
    api: await page.evaluate(async () => {
      const value = document.querySelector('#knowledge-time').value;
      const params = new URLSearchParams({ scope: 'operations', known_at: value });
      return await (await fetch(`/api/decision-timeline?${params}`)).json();
    }),
  });
  const snap = async name => {
    const file = path.join(screenshotDirectory, `decision-timeline-${name}.png`);
    await page.screenshot({ path: file, fullPage: true });
    screenshots.push(file);
  };
  const replay = async knownAt => {
    await page.locator(`[data-known-at="${knownAt}"]`).click();
    await page.waitForFunction(cutoff => document.querySelector('#knowledge-timeline-context')?.textContent?.includes(cutoff), knownAt);
    await page.waitForTimeout(60);
    return await capture();
  };
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => {
    if (message.type() !== 'error') return;
    if (expectingBadRequest && /status of 400/.test(message.text())) { expectedBadRequestConsoleErrors++; return; }
    errors.push(message.text());
  });
  page.on('request', request => { try { hosts.add(new URL(request.url()).host); } catch (_) {} });
  fs.mkdirSync(screenshotDirectory, { recursive: true });
  const report = { schema_version: 1, status: 'running', mode, assertions: checks, assertion_count: 0, errors, expected_bad_request_console_errors: expectedBadRequestConsoleErrors, hosts: [], screenshots, cold: null, desktop: null, mobile: null };
  try {
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    check('loopback app is available', response?.status() === 200, response?.status());
    await page.waitForFunction(() => document.querySelector('#knowledge-timeline-context')?.textContent?.includes('Known-at'));

    if (mode === 'cold-seed-replay') {
      await replay('2026-10-14T15:59:59Z');
      report.cold = await capture();
      const generic = await page.evaluate(async () => await (await fetch('/api/decision-timeline?scope=operations')).json());
      check('cold graph cutoff stays generic unknown without IDs or counts',
        generic.known_at === null && generic.generic_unknown === true && generic.claims.length === 0 && generic.decision_record === null && !('count' in generic) && !JSON.stringify(generic).includes('DEC-OPS-9-REVIEW-01'), generic);
      check('initial past projection has no future record identity in replay section',
        !/DEC-OPS-9-REVIEW-01|SRC-DEC-OPS-9-REVIEW-01|SOP-OPS-9 claim reconciliation review/.test(report.cold.text), report.cold.text);
      await snap('cold');
      await page.locator('#reset').click();
      await page.waitForFunction(() => document.querySelector('#notice')?.textContent?.includes('reseeded'));

      const before = await replay('2026-10-14T15:59:59Z');
      check('before-first-observation browser exposes only unknown context', before.api.generic_unknown && before.api.claims.length === 0 && !before.text.includes('DEC-OPS-9-REVIEW-01'), before);
      const v2 = await replay('2026-10-14T16:00:00Z');
      check('V2 cutoff displays one unverified source-backed claim', v2.api.claims.length === 1 && v2.api.claims[0].version_id === 'SOP-OPS-9-V2' && v2.text.includes('SRC-SOP-CURRENT-V2'), v2);
      check('V2-only replay does not disclose the later source or record', !/SOP-OPS-9-V3|SRC-SOP-CURRENT-V3|DEC-OPS-9-REVIEW-01/.test(v2.text), v2.text);
      const v3 = await replay('2026-10-14T16:04:00Z');
      check('V3 boundary reveals graph-derived conflict without a record', v3.api.claims.length === 2 && v3.api.conflict?.epistemic_type === 'graph-derived relationship' && v3.api.decision_record === null && v3.text.includes('SOP-OPS-9-V3'), v3);
      check('pre-record replay contains no record identifier or considered edge', !v3.text.includes('DEC-OPS-9-REVIEW-01') && v3.api.decision_considers_edges.length === 0, v3);
      const record = await replay('2026-10-14T16:05:00Z');
      check('record appears at its own source boundary with only evidence-known links', record.api.decision_record?.recorded_state === 'open-unresolved' && record.api.decision_record.source.observed_at === '2026-10-14T16:05:00Z' && record.api.decision_considers_edges.length === 2 && record.text.includes('DEC-OPS-9-REVIEW-01'), record);
      check('record replay does not choose owner, procedure, or winner', record.api.current_owner_or_procedure_selected === false && record.api.contact_or_action_supplied === false && record.api.conflict?.authoritative_rule === false, record.api);
      report.desktop = await capture();
      await snap('desktop');
      await page.setViewportSize({ width: 390, height: 844 });
      const mobile = await replay('2026-10-14T16:05:00Z');
      report.mobile = mobile;
      const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
      check('mobile replay matches desktop accessible/API content without horizontal overflow',
        mobile.text === report.desktop.text && JSON.stringify(mobile.api) === JSON.stringify(report.desktop.api) && mobile.accessibility_tree.includes('Replay what was knowable') && dimensions.scrollWidth <= dimensions.width,
        { dimensions, text_equal: mobile.text === report.desktop.text });
      await snap('mobile');
    } else if (mode === 'baseline-cutoff') {
      const desktop = await replay('2026-10-14T16:05:00Z');
      report.desktop = desktop;
      check('baseline replay shows both independent claims and the separately sourced record', desktop.api.claims.length === 2 && desktop.api.decision_record?.recorded_state === 'open-unresolved' && desktop.api.decision_considers_edges.length === 2, desktop.api);
      check('baseline record provenance and unverified claims are inspectable', desktop.text.includes('DEC-OPS-9-REVIEW-01') && desktop.text.includes('SRC-SOP-CURRENT-V2') && desktop.text.includes('SRC-SOP-CURRENT-V3') && desktop.text.includes('unverified SOP claim'), desktop.text);
      await snap('baseline-desktop');
      expectingBadRequest = true;
      await page.locator('#knowledge-time').fill('not-a-timestamp');
      await page.locator('#knowledge-replay').click();
      await page.waitForFunction(() => document.querySelector('#knowledge-timeline-notice')?.textContent?.includes('Invalid timestamp'));
      expectingBadRequest = false;
      const rejectedText = await page.locator('#decision-knowledge-timeline').innerText();
      const rejectedDocket = await page.locator('#decision-docket').innerText();
      check('invalid browser cutoff clears stale timeline and current docket identity', !/DEC-OPS-9-REVIEW-01|SRC-DEC-OPS-9-REVIEW-01|SOP-OPS-9 claim reconciliation review/.test(rejectedText) && !/DEC-OPS-9-REVIEW-01|SRC-DEC-OPS-9-REVIEW-01/.test(rejectedDocket), { timeline: rejectedText, docket: rejectedDocket });
      check('invalid browser cutoff reports no current-time fallback', (await page.locator('#knowledge-timeline-notice').innerText()).includes('No current-time substitution was attempted.'), await page.locator('#knowledge-timeline-notice').innerText());
      check('invalid browser cutoff produced only its expected HTTP 400 console warning', expectedBadRequestConsoleErrors === 1, expectedBadRequestConsoleErrors);
      await replay('2026-10-14T16:05:00Z');
      await page.setViewportSize({ width: 390, height: 844 });
      const mobile = await replay('2026-10-14T16:05:00Z');
      report.mobile = mobile;
      const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
      check('baseline mobile projection matches desktop without overflow', mobile.text === desktop.text && JSON.stringify(mobile.api) === JSON.stringify(desktop.api) && dimensions.scrollWidth <= dimensions.width, { dimensions });
      await snap('baseline-mobile');
    } else {
      const desktop = await replay('2026-10-14T16:05:00Z');
      report.desktop = desktop;
      check('shifted V3 is suppressed at 16:05 while sourced record remains', desktop.api.claim_context_state === 'incomplete/unknown' && desktop.api.claims.length === 1 && desktop.api.claims[0].version_id === 'SOP-OPS-9-V2' && desktop.api.decision_record?.recorded_state === 'open-unresolved', desktop.api);
      check('shifted browser view contains no V3 literals, edges, or conflict', !/SOP-OPS-9-V3|ROLE-MATERIALS-LEAD|SRC-SOP-CURRENT-V3|Current Text v3|SOP_CONFLICT/.test(desktop.text) && desktop.api.conflict === null, desktop.text);
      check('replay renders retained record and only V2 considered relationship', desktop.text.includes('DEC-OPS-9-REVIEW-01') && desktop.text.includes('CONSIDERS') && !desktop.text.includes('SOP-OPS-9-V3'), desktop.text);
      await snap('shifted-desktop');
      await page.setViewportSize({ width: 390, height: 844 });
      const mobile = await replay('2026-10-14T16:05:00Z');
      report.mobile = mobile;
      const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
      check('shifted mobile projection matches desktop without overflow', mobile.text === desktop.text && JSON.stringify(mobile.api) === JSON.stringify(desktop.api) && dimensions.scrollWidth <= dimensions.width, { dimensions });
      await snap('shifted-mobile');
    }
    check('browser uses only this loopback origin and has no runtime errors', [...hosts].every(host => host === new URL(url).host) && errors.length === 0, { hosts: [...hosts], errors });
    report.status = 'PASS';
  } catch (error) {
    report.status = 'FAIL';
    report.error = String(error?.stack || error);
  } finally {
    report.assertions = checks;
    report.assertion_count = checks.length;
    report.errors = errors;
    report.expected_bad_request_console_errors = expectedBadRequestConsoleErrors;
    report.hosts = [...hosts];
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
    await browser.close();
  }
  if (report.status !== 'PASS') {
    process.stderr.write(`${report.error || 'browser assertions failed'}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write(`decision knowledge timeline browser: PASS (${report.assertion_count} assertions, ${mode}, desktop/mobile)\n`);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
