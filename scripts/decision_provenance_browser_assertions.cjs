#!/usr/bin/env node
// Desktop/mobile browser proof for the read-only decision docket.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');

const [url, mode, reportPath, screenshotDirectory] = process.argv.slice(2);
if (!url || !['seed-baseline', 'existing'].includes(mode) || !reportPath || !screenshotDirectory || !['localhost', '127.0.0.1'].includes(new URL(url).hostname)) {
  throw new Error('Usage: node scripts/decision_provenance_browser_assertions.cjs http://127.0.0.1:PORT seed-baseline|existing REPORT.json SCREENSHOT_DIR');
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const checks = [];
  const errors = [];
  const hosts = new Set();
  const screenshots = [];
  const check = (name, passed, detail = null) => {
    checks.push({ name, passed: Boolean(passed), detail });
    if (!passed) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  const capture = async () => ({
    body_text: await page.locator('body').innerText(),
    accessibility_tree: await page.locator('body').ariaSnapshot(),
    docket_text: await page.locator('#decision-docket').innerText(),
    docket_api: await page.evaluate(async () => await (await fetch('/api/decision-docket?scope=operations')).json()),
  });
  const snap = async name => {
    const file = path.join(screenshotDirectory, `decision-docket-${name}.png`);
    await page.screenshot({ path: file, fullPage: true });
    screenshots.push(file);
  };
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('request', request => { try { hosts.add(new URL(request.url()).host); } catch (_) {} });
  fs.mkdirSync(screenshotDirectory, { recursive: true });
  let report = { schema_version: 1, status: 'running', mode, assertions: checks, assertion_count: 0, errors, hosts: [], screenshots, cold: null, desktop: null, mobile: null };
  try {
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    check('loopback app page is available', response?.status() === 200, response?.status());
    if (mode === 'seed-baseline') {
      await page.waitForFunction(() => document.querySelector('#decision-docket-badge')?.textContent?.includes('Unknown'));
      report.cold = await capture();
      check('cold browser docket visibly remains generic unknown with no record identity', report.cold.docket_text.includes('Unknown · no evidence') && !/DEC-OPS-9-REVIEW-01|SRC-DEC-OPS-9-REVIEW-01|SOP-OPS-9 claim reconciliation review/.test(report.cold.docket_text), report.cold.docket_text);
      await snap('cold-desktop');
      await page.locator('#reset').click();
    }
    await page.waitForFunction(() => document.querySelector('#decision-docket-badge')?.textContent?.includes('Source-backed'));
    report.desktop = await capture();
    check('desktop shows the register-backed unresolved record and all three cited source timestamps',
      report.desktop.docket_text.includes('DEC-OPS-9-REVIEW-01') &&
      report.desktop.docket_text.includes('Synthetic Decision Register: SOP-OPS-9 Reconciliation') &&
      report.desktop.docket_text.includes('2026-10-14T16:05:00Z') &&
      report.desktop.docket_text.includes('Synthetic SOP-OPS-9 Current Text v2') &&
      report.desktop.docket_text.includes('2026-10-14T16:00:00Z') &&
      report.desktop.docket_text.includes('Synthetic SOP-OPS-9 Current Text v3') &&
      report.desktop.docket_text.includes('2026-10-14T16:04:00Z'), report.desktop.docket_text);
    check('desktop presents claims as unverified and does not select an owner or winner',
      report.desktop.docket_text.includes('unverified SOP claim') &&
      report.desktop.docket_text.includes('Neither claim is selected as authoritative') &&
      report.desktop.docket_api.current_owner_selected === false &&
      report.desktop.docket_api.docket_rows[0].selected_version === null, report.desktop.docket_api);
    await snap('desktop');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(150);
    report.mobile = await capture();
    const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    check('mobile keeps the same accessible docket content without horizontal overflow',
      report.mobile.docket_text === report.desktop.docket_text &&
      report.mobile.accessibility_tree.includes('Decision provenance docket') && dimensions.scrollWidth <= dimensions.width,
      { dimensions, mobile: report.mobile.docket_text });
    await snap('mobile');
    check('browser performs only loopback requests and has no runtime errors',
      [...hosts].every(host => host === new URL(url).host) && errors.length === 0,
      { hosts: [...hosts], errors });
    report.status = 'PASS';
  } catch (error) {
    report.status = 'FAIL';
    report.error = String(error?.stack || error);
  } finally {
    report.assertions = checks;
    report.assertion_count = checks.length;
    report.errors = errors;
    report.hosts = [...hosts];
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
    await browser.close();
  }
  if (report.status !== 'PASS') {
    process.stderr.write(`${report.error || 'browser assertions failed'}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write(`decision docket browser: PASS (${report.assertion_count} assertions, ${mode}, desktop/mobile)\n`);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
