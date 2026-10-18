#!/usr/bin/env node
'use strict';
// Read-only browser evidence. Expectations come from the committed contracts,
// never from a response observed from the candidate service.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

let chromium;
for (const moduleName of ['/tools/node/lib/node_modules/playwright-core', 'playwright-core', 'playwright']) {
  try { ({ chromium } = require(moduleName)); break; } catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
  }
}
if (!chromium) throw new Error('Playwright is required. Use the existing /tools/node installation or install playwright-core locally; no automatic network download is performed.');

const [baseUrl, mode, reportPath, screenshotDirectory] = process.argv.slice(2);
let target;
try { target = new URL(baseUrl); } catch (_) {}
if (!target || !['http:', 'https:'].includes(target.protocol) || !['localhost', '127.0.0.1', '[::1]'].includes(target.hostname) || !['existing', 'cold', 'missing'].includes(mode) || !reportPath || !screenshotDirectory || target.username || target.password) {
  throw new Error('Usage: node scripts/knowledge_diff_browser_assertions.cjs BASE_URL existing|cold|missing REPORT.json SCREENSHOTS_DIR (loopback only)');
}
if (target.pathname === '/') target.pathname = '/knowledge-diff';
const root = path.resolve(__dirname, '..');
const contract = JSON.parse(fs.readFileSync(path.join(root, 'contracts/knowledge-diff/expected-results.json'), 'utf8'));
const sourceContract = JSON.parse(fs.readFileSync(path.join(root, 'contracts/decision-source-branch/expected-results.json'), 'utf8'));
const group = id => contract.groups.find(item => item.id === id);
const sourceGroup = id => sourceContract.groups.find(item => item.id === id).expected;
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
const sorted = values => [...values].sort();
const DEFAULT = group('modes-at-1605').input;
const T04 = '2026-10-14T16:04:00Z';
const T05 = '2026-10-14T16:05:00Z';
const T06 = '2026-10-14T16:06:00Z';
const ALL_KEYS = [
  'claim:SOP-OPS-9-V2:ROLE-FACILITIES-DUTY',
  'claim:SOP-OPS-9-V3:ROLE-MATERIALS-LEAD',
  'source_observation:SRC-SOP-CURRENT-V2',
  'source_observation:SRC-SOP-CURRENT-V3',
  'source_observation:SRC-DEC-OPS-9-REVIEW-01',
  'conflict_relationship:SOP-OPS-9-V2:SOP_CONFLICT:SOP-OPS-9-V3',
  'decision_record:DEC-OPS-9-REVIEW-01',
  'considered_link:DEC-OPS-9-REVIEW-01:CONSIDERS:SOP-OPS-9-V2',
  'considered_link:DEC-OPS-9-REVIEW-01:CONSIDERS:SOP-OPS-9-V3',
];
const identities = /(?:SRC-|DEC-OPS-|SOP-OPS-|ROLE-|BR-SOP-|OBS-SOP-)/;
const paramsFor = selection => new URLSearchParams({ scope: 'operations', left_known_at: selection.left_known_at, right_known_at: selection.right_known_at, left_mode: selection.left_mode, right_mode: selection.right_mode }).toString();
const assertions = [];
const errors = [];
const hosts = new Set();
const report = { schema_version: 1, status: 'running', mode, assertions, assertion_count: 0, errors, hosts: [], screenshots: [], desktop: null, mobile: null, keyboard: false, stale_request_ignored: false, cases: {} };
function check(name, pass, detail = null) {
  assertions.push({ name, passed: Boolean(pass), detail });
  if (!pass) throw new Error(`${name}: ${JSON.stringify(detail)}`);
}

(async () => {
  let browser;
  let page;
  try {
    for (const pin of contract.pinned_sources) {
      const hash = crypto.createHash('sha256').update(fs.readFileSync(path.join(root, pin.path))).digest('hex');
      check(`frozen source contract ${pin.path}`, hash === pin.sha256, hash);
    }
    const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || process.env.CHROMIUM_PATH;
    browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(String(error)));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    // Fail closed: the browser cannot send requests to any external origin.
    await page.route('**/*', async route => {
      const requestUrl = new URL(route.request().url());
      if (!['http:', 'https:'].includes(requestUrl.protocol)) return route.continue();
      hosts.add(requestUrl.host);
      if (requestUrl.origin !== target.origin) { errors.push(`Blocked external origin: ${requestUrl.origin}`); return route.abort('blockedbyclient'); }
      return route.continue();
    });
    fs.mkdirSync(screenshotDirectory, { recursive: true });
    const workspace = page.locator('#knowledge-diff');
    const api = async selection => {
      const response = await page.request.get(new URL('/api/knowledge-diff?' + paramsFor(selection), target.origin).href);
      check('read-only comparison GET succeeds', response.status() === 200, response.status());
      return response.json();
    };
    const ready = async selection => {
      await page.waitForFunction(expected => {
        const root = document.getElementById('knowledge-diff');
        return root?.dataset.selection === expected && root.dataset.state !== 'loading' && document.getElementById('knowledge-diff-results')?.getAttribute('aria-busy') === 'false';
      }, paramsFor(selection), { timeout: 15000 });
      check('selected comparison is rendered without client error', await workspace.getAttribute('data-state') !== 'error', await page.locator('#diff-status').innerText());
    };
    const select = async selection => {
      for (const [key, id] of [['left_mode', 'left-mode'], ['left_known_at', 'left-known-at'], ['right_mode', 'right-mode'], ['right_known_at', 'right-known-at']]) {
        if (await page.locator('#' + id).inputValue() !== selection[key]) await page.locator('#' + id).selectOption(selection[key]);
      }
      await ready(selection);
    };
    const capture = async selection => ({
      selection: { ...selection },
      text: await page.locator('body').innerText(),
      accessibility_tree: await page.locator('body').ariaSnapshot(),
      api: await api(selection),
    });
    const screenshot = async name => {
      const file = path.join(screenshotDirectory, `knowledge-diff-${mode}-${name}.png`);
      await page.screenshot({ path: file, fullPage: true }); report.screenshots.push(file);
    };
    const domKeys = async status => page.locator('.delta-card' + (status ? `[data-status="${status}"]` : '')).evaluateAll(cards => cards.map(card => card.dataset.key).sort());
    const invariant = data => {
      check('comparison is bounded, read-only, and supports no negative conclusion', data.scope === 'operations' && data.read_only === true && data.delta.complete === false && data.delta.no_negative_conclusion === true, data.delta);
      for (const side of ['left', 'right']) check(`${side} is an independently selected public projection`, contract.policy.allowed_modes.includes(data[side].mode) && ['available', 'partial', 'comparison-unavailable'].includes(data[side].availability) && data[side].projection?.negative_conclusion_asserted === false, data[side].availability);
      check('all witness kinds and statuses use the agreed typed vocabulary', data.delta.items.every(item => contract.policy.kinds.includes(item.kind) && contract.policy.statuses.includes(item.status) && item.key.startsWith(item.kind + ':')), data.delta.items.map(({ key, status }) => ({ key, status })));
    };
    const frozenCase = async id => {
      const { input, expected } = group(id);
      await select(input);
      const current = await capture(input); invariant(current.api);
      check(`${id}: exact frozen delta and unchanged counts`, current.api.delta.substantive_delta_count === expected.substantive_delta_count && current.api.delta.unchanged_count === expected.unchanged_count, current.api.delta);
      if (expected.state) check(`${id}: expected comparison state`, current.api.delta.state === expected.state, current.api.delta.state);
      check(`${id}: exact frozen witness key set, not an API-derived oracle`, same(sorted(current.api.delta.items.map(item => item.key)), sorted(ALL_KEYS)) && same(await domKeys(), sorted(ALL_KEYS)), await domKeys());
      const expectedSets = {
        'projected-only-on-left': expected.only_left || [],
        'projected-only-on-right': expected.only_right || [],
        changed: expected.changed || [],
        incomparable: [],
      };
      const substantive = Object.values(expectedSets).flat();
      expectedSets.unchanged = ALL_KEYS.filter(key => !substantive.includes(key));
      for (const [status, keys] of Object.entries(expectedSets)) {
        const actual = current.api.delta.items.filter(item => item.status === status).map(item => item.key);
        check(`${id}: exact ${status} API and DOM key sets`, same(sorted(actual), sorted(keys)) && same(await domKeys(status), sorted(keys)), { expected: sorted(keys), api: sorted(actual), dom: await domKeys(status) });
      }
      check(`${id}: DOM preserves epistemic distinctions`, current.text.includes('unverified SOP claim') && current.text.includes('open-unresolved') && current.text.includes('Graph-derived relationship') && current.text.includes('not an endorsement'), current.text);
      report.cases[id] = current;
      return current;
    };
    const assertCitations = async () => {
      for (const expected of contract.citations) {
        const citations = page.locator(`[data-citation-id="${expected.id}"]`);
        check(`exact citation ${expected.id} is inspectable in the DOM`, await citations.count() > 0, expected);
        const rendered = await citations.allTextContents();
        check(`citation ${expected.id} preserves exact title and actual timestamp`, rendered.every(text => text.includes(expected.id) && text.includes(expected.title) && text.includes(expected.observed_at)), rendered);
        check(`citation ${expected.id} uses actual source observation metadata`, (await citations.evaluateAll(nodes => nodes.map(node => node.dataset.observedAt))).every(value => value === expected.observed_at), expected.observed_at);
      }
    };

    const response = await page.goto(target.href, { waitUntil: 'domcontentloaded', timeout: 30000 });
    check('local knowledge workspace is available', response?.status() === 200, response?.status());
    await ready(DEFAULT);
    check('exactly two snapshot fieldsets contain four named native selects', await page.locator('fieldset').count() === 2 && await page.getByRole('combobox').count() === 4 && await page.getByRole('combobox', { name: 'Left snapshot mode', exact: true }).count() === 1 && await page.getByRole('combobox', { name: 'Right snapshot knowledge cutoff', exact: true }).count() === 1);
    check('defaults are independent mainline/hypothetical 16:05 selections', await page.locator('#left-mode').inputValue() === 'mainline' && await page.locator('#right-mode').inputValue() === 'hypothetical' && await page.locator('#left-known-at').inputValue() === T05 && await page.locator('#right-known-at').inputValue() === T05);
    let desktop;
    if (mode === 'existing') {
      desktop = await frozenCase('modes-at-1605');
      check('default projections equal their frozen independent source projections', same(desktop.api.left.projection, sourceGroup('mainline-at-16-05').projection) && same(desktop.api.right.projection, sourceGroup('branch-at-16-05-suppression').projection));
      check('partial hypothetical side is not unavailable and contains no suppressed V3 identity', desktop.api.right.availability === 'partial' && !/SOP-OPS-9-V3|SRC-SOP-CURRENT-V3|ROLE-MATERIALS-LEAD|Current Text v3/.test(await page.locator('#right-snapshot').innerText()), desktop.api.right);
      await assertCitations();
    } else {
      desktop = await capture(DEFAULT); invariant(desktop.api);
      check(`${mode}: comparison is unavailable with unknown delta count`, desktop.api.delta.state === 'comparison-unavailable' && desktop.api.delta.substantive_delta_count === null && (await page.locator('#diff-status').innerText()).includes('Unknown / incomplete') && await page.locator('#delta-count').innerText() === 'Unknown', desktop.api.delta);
      check(`${mode}: unknown sides never imply one-sided deletion or mass removal`, desktop.api.delta.items.every(item => item.status === 'incomparable') && await page.locator('.delta-card[data-status="projected-only-on-left"], .delta-card[data-status="projected-only-on-right"]').count() === 0, desktop.api.delta);
      if (mode === 'cold') {
        check('cold graph exposes no witness identities, titles, or citations', desktop.api.left.availability === 'comparison-unavailable' && desktop.api.right.availability === 'comparison-unavailable' && same(desktop.api.delta.items, []) && !identities.test(JSON.stringify(desktop.api)) && !identities.test(desktop.text) && !identities.test(desktop.accessibility_tree) && await page.locator('[data-citation-id]').count() === 0, desktop.api);
      } else {
        const rightText = await page.locator('#right-snapshot').innerText();
        check('missing hypothetical witness stays generic unknown without mainline fallback', desktop.api.left.availability === 'available' && desktop.api.right.availability === 'comparison-unavailable' && desktop.api.right.projection.generic_unknown === true && !desktop.api.right.scenario && !identities.test(JSON.stringify(desktop.api.right)) && !identities.test(rightText), { api: desktop.api.right, text: rightText });
        check('missing side makes the exact nine known public witnesses incomparable', same(sorted(desktop.api.delta.items.map(item => item.key)), sorted(ALL_KEYS)) && desktop.api.delta.items.every(item => item.left && item.right === null), desktop.api.delta.items);
        check('incomparable right witness panes contain no copied left identity', (await page.locator('.witness[data-side="right"]').allTextContents()).every(text => text.includes('Unknown / incomplete') && !identities.test(text)));
      }
    }
    report.desktop = desktop;
    await screenshot('desktop-default');
    const desktopLayout = await page.locator('fieldset').evaluateAll(nodes => nodes.map(node => { const rect = node.getBoundingClientRect(); return { top: rect.top, left: rect.left, right: rect.right }; }));
    check('desktop independent snapshot controls are side-by-side', Math.abs(desktopLayout[0].top - desktopLayout[1].top) < 3 && desktopLayout[1].left > desktopLayout[0].right, desktopLayout);

    // Native key events, not programmatic change dispatch, exercise selection.
    await page.locator('#right-known-at').focus();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    const keyboardSelection = { ...DEFAULT, right_known_at: T06 };
    await ready(keyboardSelection);
    const keyboardApi = await api(keyboardSelection);
    check('keyboard changes only right knowledge time and retains left selection', await page.locator('#right-known-at').inputValue() === T06 && await page.locator('#left-known-at').inputValue() === T05 && keyboardApi.left.known_at === T05 && keyboardApi.right.known_at === T06, keyboardApi);
    await page.locator('#left-mode').focus();
    await page.keyboard.press('Tab');
    check('native controls follow a useful named keyboard tab order', await page.locator('#left-known-at').evaluate(node => node === document.activeElement));
    if (await page.locator('.delta-card').count() > 1) {
      await page.locator('.delta-card').first().focus();
      await page.keyboard.press('ArrowDown');
      check('delta cards support arrow-key navigation', await page.locator('.delta-card').nth(1).evaluate(node => node === document.activeElement));
      await page.keyboard.press('End');
      check('delta cards support End and Home navigation', await page.locator('.delta-card').last().evaluate(node => node === document.activeElement));
      await page.keyboard.press('Home');
      check('Home returns to the first semantic witness', await page.locator('.delta-card').first().evaluate(node => node === document.activeElement));
    }
    report.keyboard = true;
    report.keyboard_snapshot = await capture(keyboardSelection);

    if (mode === 'existing') {
      await frozenCase('identical');
      const boundary = await frozenCase('mainline-boundary');
      check('different cutoff times are independently preserved in projections and controls', boundary.api.left.known_at === T04 && boundary.api.right.known_at === T05 && await page.locator('#left-known-at').inputValue() === T04 && await page.locator('#right-known-at').inputValue() === T05);
      check('record source boundary does not invent a previous record', boundary.api.left.projection.decision_record === null && boundary.api.right.projection.decision_record?.recorded_state === 'open-unresolved');
      await frozenCase('branch-boundary');
      const assumed = await frozenCase('actual-assumed');
      const sourceItem = assumed.api.delta.items.find(item => item.key === 'source_observation:SRC-SOP-CURRENT-V3');
      check('only V3 observation changes; actual time stays 16:04 and assumption is separately 16:06', sourceItem.left.observed_at === contract.citations[1].observed_at && sourceItem.right.observed_at === contract.citations[1].observed_at && sourceItem.right.assumed_observed_at === group('actual-assumed').expected.assumed_observed_at && sourceItem.right.observation_mode === 'hypothetical', sourceItem);
      const v2 = assumed.api.delta.items.find(item => item.key === 'source_observation:SRC-SOP-CURRENT-V2');
      check('V2 observation does not inherit a hypothetical assumption', v2.status === 'unchanged' && v2.left.observed_at === contract.citations[0].observed_at && same(v2.left, v2.right) && !v2.right.assumed_observed_at && v2.right.observation_mode !== 'hypothetical', v2);
      await assertCitations();
      const observationCard = page.locator('.delta-card[data-key="source_observation:SRC-SOP-CURRENT-V3"]');
      check('actual and assumed times are distinct in source observation DOM', (await observationCard.innerText()).includes('Actual source observation') && (await observationCard.innerText()).includes(T04) && (await observationCard.innerText()).includes('Hypothetical observation') && (await observationCard.innerText()).includes(T06));
      const reverse = { left_mode: 'hypothetical', left_known_at: T05, right_mode: 'mainline', right_known_at: T05 };
      await select(reverse);
      const reverseApi = await api(reverse);
      check('reversing independent modes reverses only the exact frozen one-sided set', reverseApi.delta.substantive_delta_count === 4 && reverseApi.delta.unchanged_count === 5 && same(sorted(reverseApi.delta.items.filter(item => item.status === 'projected-only-on-right').map(item => item.key)), sorted(group('modes-at-1605').expected.only_left)) && reverseApi.delta.items.every(item => item.status !== 'projected-only-on-left'), reverseApi.delta);
    }

    // Intercept only local GET responses. The old request is deliberately kept
    // alive and fulfilled AFTER the newer response, proving generation guards
    // rather than assuming cancellation or mutating graph/server state.
    await select(DEFAULT);
    const olderSelection = { ...DEFAULT, right_known_at: T04 };
    const newerSelection = { ...DEFAULT, right_known_at: T06 };
    const olderApi = await api(olderSelection);
    const newerApi = await api(newerSelection);
    let sawOlder;
    let releaseOlder;
    let olderFulfilled;
    const olderSeen = new Promise(resolve => { sawOlder = resolve; });
    const release = new Promise(resolve => { releaseOlder = resolve; });
    const delivered = new Promise(resolve => { olderFulfilled = resolve; });
    const fulfillmentOrder = [];
    const interceptor = async route => {
      const requestUrl = new URL(route.request().url());
      if (requestUrl.searchParams.get('right_known_at') === T04) {
        sawOlder(); await release;
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(olderApi) });
        fulfillmentOrder.push('older'); olderFulfilled();
      } else if (requestUrl.searchParams.get('right_known_at') === T06) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(newerApi) });
        fulfillmentOrder.push('newer');
      } else await route.fallback();
    };
    await page.route(/\/api\/knowledge-diff(?:\?|$)/, interceptor);
    try {
      await page.locator('#right-known-at').selectOption(T04);
      await Promise.race([olderSeen, new Promise((_, reject) => { const timeout = setTimeout(() => reject(new Error('Older request did not reach the response interceptor')), 15000); timeout.unref(); })]);
      await page.locator('#right-known-at').selectOption(T06);
      await ready(newerSelection);
      const beforeText = await page.locator('body').innerText();
      const beforeAria = await page.locator('body').ariaSnapshot();
      const lateResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/knowledge-diff' && new URL(response.url()).searchParams.get('right_known_at') === T04);
      releaseOlder();
      await delivered;
      await (await lateResponse).finished();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      check('delayed local API responses are actually delivered out of order', same(fulfillmentOrder, ['newer', 'older']), fulfillmentOrder);
      check('late response cannot overwrite new selection, status, DOM or accessibility', await workspace.getAttribute('data-selection') === paramsFor(newerSelection) && await page.locator('#right-known-at').inputValue() === T06 && await page.locator('body').innerText() === beforeText && await page.locator('body').ariaSnapshot() === beforeAria);
      report.stale_request_ignored = true;
      report.stale_response_order = fulfillmentOrder;
    } finally { releaseOlder(); await page.unroute(/\/api\/knowledge-diff(?:\?|$)/, interceptor); }

    // Capture the exact same default selection at both widths, so a verifier
    // can compare complete text/ARIA/API evidence across clean and ACL runs.
    await select(DEFAULT);
    const restored = await capture(DEFAULT);
    check('repeated read-only default selection is deterministic', restored.text === desktop.text && restored.accessibility_tree === desktop.accessibility_tree && same(restored.api, desktop.api));
    await page.setViewportSize({ width: 390, height: 844 });
    const mobile = await capture(DEFAULT);
    const layout = await page.evaluate(() => {
      const boxes = [...document.querySelectorAll('fieldset')].map(node => { const rect = node.getBoundingClientRect(); return { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right }; });
      return { width: innerWidth, scroll_width: document.documentElement.scrollWidth, boxes };
    });
    check('mobile stacks snapshot controls without horizontal overflow', layout.scroll_width <= layout.width && layout.boxes[1].top > layout.boxes[0].bottom, layout);
    check('full mobile text, accessibility tree and API equal desktop defaults', mobile.text === desktop.text && mobile.accessibility_tree === desktop.accessibility_tree && same(mobile.api, desktop.api), { text_equal: mobile.text === desktop.text, accessibility_equal: mobile.accessibility_tree === desktop.accessibility_tree, api_equal: same(mobile.api, desktop.api) });
    report.mobile = { ...mobile, layout };
    await screenshot('mobile-default');
    check('no script injection surface or clickable external action is rendered', await page.locator('#knowledge-diff script, #knowledge-diff iframe, #knowledge-diff form').count() === 0 && await page.locator('#knowledge-diff a[href^="http"]').count() === 0);
    check('all browser requests stay on the selected loopback origin with no runtime errors', [...hosts].every(host => host === target.host) && errors.length === 0, { hosts: [...hosts], errors });
    report.status = 'PASS';
  } catch (error) {
    report.status = 'FAIL'; report.error = String(error.stack || error);
  } finally {
    report.assertion_count = assertions.length;
    report.hosts = [...hosts].sort();
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    if (browser) await browser.close();
  }
  if (report.status !== 'PASS') { process.stderr.write(`${report.error || 'Knowledge diff browser assertions failed'}\n`); process.exitCode = 1; }
  else process.stdout.write(`knowledge diff browser: PASS (${report.assertion_count} assertions; ${mode}; desktop/mobile, keyboard, stale responses)\n`);
})().catch(error => { console.error(error); process.exitCode = 1; });
