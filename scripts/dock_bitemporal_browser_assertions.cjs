#!/usr/bin/env node
// Desktop/mobile interaction checks for the effective_at × known_at board.
// The browser may contact only the explicitly supplied loopback app origin.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');

const [url, evidenceDir] = process.argv.slice(2);
if (!url || !evidenceDir || !['localhost', '127.0.0.1'].includes(new URL(url).hostname)) {
  throw new Error('Usage: node scripts/dock_bitemporal_browser_assertions.cjs http://127.0.0.1:PORT /workspace/evidence/...');
}
const oracle = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'tests/dock-bitemporal-expected-results.json'), 'utf8'));
const expected = Object.fromEntries(oracle.cases.map(row => [row.id, row.expected]));
const canonical = value => Array.isArray(value)
  ? value.map(canonical)
  : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]))
    : value;
const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const checks = [];
  const errors = [];
  const hosts = new Set();
  const check = (name, passed, detail = null) => {
    checks.push({ name, passed: Boolean(passed), detail });
    if (!passed) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('request', request => { try { hosts.add(new URL(request.url()).host); } catch (_) {} });
  const getPair = async (effectiveAt, knownAt) => page.evaluate(async pair => {
    const query = new URLSearchParams({ scope: 'operations', effective_at: pair.effectiveAt, known_at: pair.knownAt });
    const response = await fetch(`/api/shift-board?${query}`);
    return { status: response.status, body: await response.json() };
  }, { effectiveAt, knownAt });
  try {
    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
    check('desktop product origin responds successfully', response && response.status() === 200, response && response.status());
    await page.locator('#dock-case').waitFor({ state: 'visible' });
    check('both temporal controls are visible with explicit axis labels', await page.locator('#dock-time').isVisible() && await page.locator('#dock-known-time').isVisible() && (await page.locator('label[for="dock-time"]').innerText()) === 'Effective at' && (await page.locator('label[for="dock-known-time"]').innerText()) === 'Sources known by');

    const legacy = await page.evaluate(async () => {
      const query = new URLSearchParams({ scope: 'operations', dock_at: '2026-10-14T20:29:00Z' });
      const response = await fetch(`/api/shift-board?${query}`);
      const body = await response.json();
      return { status: response.status, dock: body.dock };
    });
    check('legacy one-axis request remains available without known_at metadata', legacy.status === 200 && legacy.dock.role_id === 'ROLE-DOCK-DUTY' && legacy.dock.as_of === '2026-10-14T20:29:00Z' && !Object.hasOwn(legacy.dock, 'known_at') && legacy.dock.source_versions.length === 2, legacy.dock);

    const early = expected['dock-bitemporal-early-known-r1-duty'];
    await page.locator('#dock-time').selectOption(early.effective_at);
    await page.locator('#dock-known-time').selectOption(early.known_at);
    await page.waitForFunction(() => document.querySelector('#dock-role-badge')?.textContent === 'ROLE-DOCK-DUTY' && document.querySelector('#dock-time-context')?.textContent.includes('knowledge limited to sources observed by 2026-10-14T20:17:59Z') && document.querySelector('#dock-sources')?.textContent.includes('SRC-DOCK-ROSTER-R1') && !document.querySelector('#dock-sources')?.textContent.includes('SRC-DOCK-ROSTER-R2'));
    const earlyBody = (await getPair(early.effective_at, early.known_at)).body;
    const earlyRendered = await page.locator('body').innerText();
    check('earlier knowledge returns the exact duty path and only R1 source', earlyBody.dock.role_path.join('|') === early.role_path.join('|') && same(earlyBody.dock.source_versions, early.source_versions) && earlyBody.dock.review_candidates.length === 0, earlyBody.dock);
    check('full early-knowledge UI has no later-source marker, title, time, candidate, or paraphrase', !/SRC-DOCK-ROSTER-R2|Revision 2|ROLE-DOCK-SWING|HND-DOCK-2020|TKT-DOCK-88|20:18:00Z/.test(earlyRendered), earlyRendered);

    const known = expected['dock-bitemporal-known-r2-pending-review'];
    await page.locator('#dock-known-time').selectOption(known.known_at);
    await page.waitForFunction(() => document.querySelector('#dock-sources')?.textContent.includes('SRC-DOCK-ROSTER-R2') && document.querySelector('#dock-review')?.textContent.includes('TKT-DOCK-88'));
    const knownBody = (await getPair(known.effective_at, known.known_at)).body;
    check('advancing only knowledge reveals R2 and exact explicit review edges while ownership stays duty', knownBody.dock.role_id === 'ROLE-DOCK-DUTY' && same(knownBody.dock.source_versions, known.source_versions) && knownBody.dock.review_candidates.map(row => row.artifact_id).join('|') === known.review_candidates.join('|') && knownBody.dock.candidate_disposition === known.candidate_disposition, knownBody.dock);
    check('desktop review candidates are explicitly linked and not lexically guessed', (await page.locator('#dock-review').innerText()).includes('HND-DOCK-2020') && (await page.locator('#dock-review').innerText()).includes('TKT-DOCK-88') && !(await page.locator('#dock-review').innerText()).includes('HND-DOCK-LEXICAL-ONLY'));
    check('visual timestamp caption keeps effective and knowledge axes separate', (await page.locator('#dock-time-context').innerText()).includes('Effective at 2026-10-14T20:29:00Z') && (await page.locator('#dock-time-context').innerText()).includes('knowledge limited to sources observed by 2026-10-14T20:19:00Z'), await page.locator('#dock-time-context').innerText());

    const crossed = expected['dock-bitemporal-crossed-axes-unknown'];
    await page.locator('#dock-time').selectOption(crossed.effective_at);
    await page.locator('#dock-known-time').selectOption(crossed.known_at);
    await page.waitForFunction(() => document.querySelector('#dock-role-badge')?.textContent === 'Role unknown');
    const crossedBody = (await getPair(crossed.effective_at, crossed.known_at)).body;
    check('crossed axes show unknown instead of guessing duty or swing', crossedBody.dock.role_path.length === 0 && crossedBody.dock.role_id === null && crossedBody.dock.role_status === 'unknown' && crossedBody.dock.source_versions.length === 1 && crossedBody.dock.review_candidates.length === 0, crossedBody.dock);
    check('crossed-axis response contains no later source identity/title/time/existence hint', !/SRC-DOCK-ROSTER-R2|Revision 2|ROLE-DOCK-SWING|20:18:00Z/.test(JSON.stringify(crossedBody)));

    const future = expected['dock-bitemporal-known-future-effective-swing'];
    await page.locator('#dock-time').selectOption(future.effective_at);
    await page.locator('#dock-known-time').selectOption(future.known_at);
    await page.waitForFunction(() => document.querySelector('#dock-role-badge')?.textContent === 'ROLE-DOCK-SWING');
    const futureBody = (await getPair(future.effective_at, future.known_at)).body;
    check('known future-effective assignment switches only at effective boundary', futureBody.dock.role_path.join('|') === future.role_path.join('|') && futureBody.dock.interpretation === future.interpretation, futureBody.dock);

    // Leave the desktop/mobile screenshots at the memorable same-effective-time
    // comparison: R2 is known, but the 20:29 assignment remains duty.
    await page.locator('#dock-time').selectOption(known.effective_at);
    await page.locator('#dock-known-time').selectOption(known.known_at);
    await page.waitForFunction(() => document.querySelector('#dock-role-badge')?.textContent === 'ROLE-DOCK-DUTY');
    fs.mkdirSync(evidenceDir, { recursive: true });
    await page.screenshot({ path: path.join(evidenceDir, 'dock-bitemporal-desktop.png'), fullPage: true });
    const desktopApi = await getPair(known.effective_at, known.known_at);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(150);
    const mobileMetrics = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    check('mobile dock controls remain visible without page-level horizontal overflow', await page.locator('#dock-time').isVisible() && await page.locator('#dock-known-time').isVisible() && mobileMetrics.document <= mobileMetrics.viewport + 2 && mobileMetrics.body <= mobileMetrics.viewport + 2, mobileMetrics);
    const mobileApi = await getPair(known.effective_at, known.known_at);
    await page.screenshot({ path: path.join(evidenceDir, 'dock-bitemporal-mobile.png'), fullPage: true });
    check('bitemporal API results are identical at desktop and mobile widths', desktopApi.status === mobileApi.status && JSON.stringify(desktopApi.body) === JSON.stringify(mobileApi.body) && desktopApi.body.dock.role_id === 'ROLE-DOCK-DUTY', { desktop_status: desktopApi.status, mobile_status: mobileApi.status, role: desktopApi.body.dock.role_id });
    check('browser sends requests only to this allocated loopback app', [...hosts].length === 1 && [...hosts][0] === new URL(url).host, [...hosts]);
    check('browser has no console or page errors', errors.length === 0, errors);

    const report = { status: 'PASS', assertion_count: checks.length, checks, requested_hosts: [...hosts], api_equal_at_desktop_mobile: JSON.stringify(desktopApi.body) === JSON.stringify(mobileApi.body), desktop_viewport: { width: 1440, height: 1000 }, mobile_viewport: mobileMetrics, screenshots: ['dock-bitemporal-desktop.png', 'dock-bitemporal-mobile.png'] };
    fs.writeFileSync(path.join(evidenceDir, 'dock-bitemporal-browser.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify({ status: report.status, assertion_count: report.assertion_count, api_equal_at_desktop_mobile: report.api_equal_at_desktop_mobile, requested_hosts: report.requested_hosts }));
  } catch (error) {
    const report = { status: 'FAIL', assertion_count: checks.length, checks, error: String(error), browser_errors: errors, requested_hosts: [...hosts] };
    fs.mkdirSync(evidenceDir, { recursive: true });
    fs.writeFileSync(path.join(evidenceDir, 'dock-bitemporal-browser.json'), JSON.stringify(report, null, 2) + '\n');
    console.error(error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
