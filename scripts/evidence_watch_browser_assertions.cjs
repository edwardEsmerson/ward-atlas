#!/usr/bin/env node
// Desktop/mobile UI and watch-view ACL invariance checks on one private app.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { chromium } = require('/tools/node/lib/node_modules/playwright-core');

const [url, dbPortText, graphName, evidenceDir, fixtureFile, tokenFile] = process.argv.slice(2);
if (!url || !dbPortText || !graphName || !evidenceDir || !fixtureFile || !tokenFile || !['localhost', '127.0.0.1'].includes(new URL(url).hostname)) {
  throw new Error('Usage: node evidence_watch_browser_assertions.cjs http://127.0.0.1:PORT DB_PORT GRAPH_NAME EVIDENCE_DIR FIXTURE_JSON OWNED_TOKEN_FILE');
}
const dbPort = Number(dbPortText);
const fixtures = JSON.parse(fs.readFileSync(fixtureFile, 'utf8'));
const pairs = [
  { effective_at: '2026-10-14T20:29:00Z', known_at: '2026-10-14T20:17:59Z', status: 'unchanged' },
  { effective_at: '2026-10-14T20:29:00Z', known_at: '2026-10-14T20:19:00Z', status: 'new-source-known' },
  { effective_at: '2026-10-14T20:30:00Z', known_at: '2026-10-14T20:17:59Z', status: 'unknown' },
  { effective_at: '2026-10-14T20:30:00Z', known_at: '2026-10-14T20:19:00Z', status: 'changed' },
];
const hidden = [
  'SRC-RESTRICTED-DOCK-99', 'Restricted Dock Continuity Annex 99',
  'Restricted-only note: logistics bay status unresolved.', 'EQ-RESTRICTED-DOCK-99',
  'DEPT-RESTRICTED-DOCK-99', 'ROLE-RESTRICTED-DOCK-OWNER',
  'SRC-RESTRICTED-92', 'Restricted Continuity Annex 92',
  'Restricted-only instruction: contact the secure lift coordinator.',
];
const checks = [];
const errors = [];
const hosts = new Set();
const reportPath = path.join(evidenceDir, 'evidence-watch-browser.json');
const check = (name, passed, detail = null) => {
  checks.push({ name, passed: Boolean(passed), detail });
  if (!passed) throw new Error(`${name}: ${JSON.stringify(detail)}`);
};

function parseResp(buffer, offset = 0) {
  if (offset >= buffer.length) return null;
  const marker = String.fromCharCode(buffer[offset]);
  const lineEnd = buffer.indexOf('\r\n', offset);
  if (lineEnd < 0) return null;
  const line = buffer.toString('utf8', offset + 1, lineEnd);
  const next = lineEnd + 2;
  if (marker === '+' || marker === '-') return { value: marker === '-' ? new Error(line) : line, end: next };
  if (marker === ':') return { value: Number(line), end: next };
  if (marker === '$') {
    const length = Number(line);
    if (length === -1) return { value: null, end: next };
    if (buffer.length < next + length + 2) return null;
    return { value: buffer.toString('utf8', next, next + length), end: next + length + 2 };
  }
  if (marker === '*') {
    const count = Number(line);
    if (count === -1) return { value: null, end: next };
    const values = [];
    let position = next;
    for (let i = 0; i < count; i++) {
      const item = parseResp(buffer, position);
      if (!item) return null;
      if (item.value instanceof Error) throw item.value;
      values.push(item.value);
      position = item.end;
    }
    return { value: values, end: position };
  }
  throw new Error(`Unsupported RESP marker ${marker}`);
}

function encodeCommand(parts) {
  const buffers = [Buffer.from(`*${parts.length}\r\n`)];
  for (const part of parts) {
    const value = Buffer.from(String(part));
    buffers.push(Buffer.from(`$${value.length}\r\n`), value, Buffer.from('\r\n'));
  }
  return Buffer.concat(buffers);
}

function graphQuery(cypher) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: '127.0.0.1', port: dbPort });
    let buffer = Buffer.alloc(0);
    socket.setTimeout(5000);
    socket.once('connect', () => socket.write(encodeCommand(['GRAPH.QUERY', graphName, cypher])));
    socket.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      try {
        const parsed = parseResp(buffer);
        if (!parsed) return;
        socket.end();
        resolve(parsed.value);
      } catch (error) { socket.destroy(); reject(error); }
    });
    socket.once('timeout', () => { socket.destroy(); reject(new Error('owned FalkorDB query timed out')); });
    socket.once('error', reject);
  });
}

async function getWatch(page, pair) {
  return page.evaluate(async selected => {
    const query = new URLSearchParams({ scope: 'operations', effective_at: selected.effective_at, known_at: selected.known_at });
    const response = await fetch(`/api/evidence-watch?${query}`);
    return { status: response.status, body: await response.json() };
  }, pair);
}

async function graphCounts() {
  const nodes = await graphQuery('MATCH (n) RETURN count(n) AS count');
  const relationships = await graphQuery('MATCH ()-[r]->() RETURN count(r) AS count');
  return { nodes: Number(nodes[1][0][0]), relationships: Number(relationships[1][0][0]) };
}

async function getWatchConfig(page) {
  return page.evaluate(async () => {
    const response = await fetch('/api/evidence-watch?scope=operations');
    return { status: response.status, body: await response.json() };
  });
}

async function getActions(page) {
  return page.evaluate(async () => {
    const response = await fetch('/api/actions');
    return { status: response.status, body: await response.json() };
  });
}

async function refreshVisible(page, pair) {
  await page.locator('#watch-effective-at').fill(pair.effective_at);
  await page.locator('#watch-known-at').fill(pair.known_at);
  await page.locator('#watch-refresh-button').click();
  await page.waitForFunction(expected => {
    const box = document.querySelector('#watch-live');
    return box?.innerText.includes(`${expected} ·`);
  }, pair.status);
  await page.waitForFunction(() => document.querySelector('#watch-notice')?.textContent.includes('Read-only FalkorDB refresh complete'));
  return page.locator('#evidence-watch').innerText();
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('request', request => { try { hosts.add(new URL(request.url()).host); } catch (_) {} });
  try {
    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
    check('desktop app responds on the selected private origin', response && response.status() === 200, response && response.status());
    await page.locator('#evidence-watch').waitFor({ state: 'visible' });
    await page.waitForFunction(() => /Saved local watch|No confirmed watch/.test(document.querySelector('#watch-live')?.innerText || ''));
    const effectiveLabel = await page.locator('label[for="watch-effective-at"]').innerText();
    const knowledgeLabel = await page.locator('label[for="watch-known-at"]').innerText();
    check('watch stays visible alongside explicit separate temporal controls', await page.locator('#watch-effective-at').isVisible() && await page.locator('#watch-known-at').isVisible() && effectiveLabel.includes('Effective at') && knowledgeLabel.includes('Sources known by'));

    const initialWatch = await getWatchConfig(page);
    const confirmationFlow = { attempted: initialWatch.body.watch_count === 0 };
    if (confirmationFlow.attempted) {
      const beforeGraph = await graphCounts();
      const beforeActions = await getActions(page);
      const draftResponsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/evidence-watch/draft');
      await page.locator('#watch-preview-button').click();
      const draftResponse = await draftResponsePromise;
      const draft = await draftResponse.json();
      await page.locator('#confirm-evidence-watch').waitFor({ state: 'visible' });
      const afterPreviewWatch = await getWatchConfig(page);
      const afterPreviewActions = await getActions(page);
      const afterPreviewGraph = await graphCounts();
      check('browser preview displays the source-backed R1 path and creates no saved watch', draftResponse.status() === 201 && draft.preview.role_path.join('|') === 'INC-207|EQ-DOCK-CART-2|SVC-INBOUND-TRANSFER|DEPT-RECEIVING-DOCK|ROLE-DOCK-DUTY' && initialWatch.body.watch_count === 0 && afterPreviewWatch.body.watch_count === 0 && page.locator('#confirm-evidence-watch') !== null, { draft_status: draftResponse.status(), preview: draft.preview, watch_before: initialWatch.body.watch_count, watch_after: afterPreviewWatch.body.watch_count });
      check('browser preview does not write graph or confirmed actions', JSON.stringify(beforeGraph) === JSON.stringify(afterPreviewGraph) && JSON.stringify(beforeActions.body) === JSON.stringify(afterPreviewActions.body), { beforeGraph, afterPreviewGraph, beforeActions, afterPreviewActions });
      const confirmResponsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/evidence-watch/confirm');
      await page.locator('#confirm-evidence-watch').click();
      const confirmResponse = await confirmResponsePromise;
      const confirmation = await confirmResponse.json();
      await page.waitForFunction(() => document.querySelector('#watch-live')?.innerText.includes('Saved local watch'));
      const afterConfirmation = await getWatchConfig(page);
      const afterActions = await getActions(page);
      const afterConfirmGraph = await graphCounts();
      check('one explicit browser confirmation stores exactly one local watch separately from actions', confirmResponse.status() === 201 && confirmation.persisted === true && afterConfirmation.body.watch_count === 1 && afterConfirmation.body.watch.id === confirmation.watch.id && JSON.stringify(beforeActions.body) === JSON.stringify(afterActions.body), { status: confirmResponse.status(), persisted: confirmation.persisted, count: afterConfirmation.body.watch_count, actions_unchanged: JSON.stringify(beforeActions.body) === JSON.stringify(afterActions.body) });
      check('browser confirmation writes no graph facts or external side effects', JSON.stringify(beforeGraph) === JSON.stringify(afterConfirmGraph) && JSON.stringify(confirmation.external_writes) === '[]', { beforeGraph, afterConfirmGraph, external_writes: confirmation.external_writes });
      const replay = await page.evaluate(async token => {
        const response = await fetch('/api/evidence-watch/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, confirm: true }) });
        return { status: response.status, body: await response.json() };
      }, draft.confirmation_token);
      check('same browser confirmation token replays idempotently', replay.status === 201 && replay.body.idempotent_replay === true && replay.body.watch.id === confirmation.watch.id, replay);
      fs.mkdirSync(path.dirname(tokenFile), { recursive: true });
      fs.writeFileSync(tokenFile, draft.confirmation_token, { mode: 0o600 });
      confirmationFlow.preview = draft.preview;
      confirmationFlow.preview_status = draftResponse.status();
      confirmationFlow.watch_count_before = initialWatch.body.watch_count;
      confirmationFlow.watch_count_after_preview = afterPreviewWatch.body.watch_count;
      confirmationFlow.watch_count_after_confirmation = afterConfirmation.body.watch_count;
      confirmationFlow.graph_before = beforeGraph;
      confirmationFlow.graph_after_preview = afterPreviewGraph;
      confirmationFlow.graph_after_confirmation = afterConfirmGraph;
      confirmationFlow.actions_unchanged = JSON.stringify(beforeActions.body) === JSON.stringify(afterActions.body);
      confirmationFlow.confirmation_status = confirmResponse.status();
      confirmationFlow.persisted_watch = confirmation.watch;
      confirmationFlow.idempotent_replay = replay.body.idempotent_replay;
      confirmationFlow.external_writes = confirmation.external_writes;
    }

    const beforeApi = {};
    const beforeDom = {};
    for (const pair of pairs) {
      const dom = await refreshVisible(page, pair);
      const api = await getWatch(page, pair);
      check(`browser renders ${pair.status} from the matching live API result`, api.status === 200 && api.body.current_result.status === pair.status && dom.includes(pair.status) && dom.includes(pair.effective_at) && dom.includes(pair.known_at), { api: api.body.current_result, dom });
      beforeApi[pair.status] = api;
      beforeDom[pair.status] = dom;
      const responseText = JSON.stringify(api.body);
      check(`watch response for ${pair.status} contains no denied-source literal`, !hidden.some(marker => responseText.toLowerCase().includes(marker.toLowerCase())), pair.status);
    }
    await refreshVisible(page, pairs[3]);
    fs.mkdirSync(evidenceDir, { recursive: true });
    await page.screenshot({ path: path.join(evidenceDir, 'evidence-watch-desktop.png'), fullPage: true });
    const desktopApi = await getWatch(page, pairs[3]);
    const desktopMetrics = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));

    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(150);
    const mobileBeforeDom = await refreshVisible(page, pairs[3]);
    const mobileBeforeApi = await getWatch(page, pairs[3]);
    const mobileMetrics = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    check('mobile watch axes stay visible without page-level horizontal overflow', await page.locator('#watch-effective-at').isVisible() && await page.locator('#watch-known-at').isVisible() && mobileMetrics.document <= mobileMetrics.viewport + 2 && mobileMetrics.body <= mobileMetrics.viewport + 2, mobileMetrics);
    check('desktop and mobile render the same current watch API result', JSON.stringify(desktopApi.body) === JSON.stringify(mobileBeforeApi.body) && desktopApi.body.current_result.status === 'changed', { desktop: desktopApi.body.current_result, mobile: mobileBeforeApi.body.current_result });

    for (const query of fixtures) await graphQuery(query);
    const afterApi = {};
    const afterDom = {};
    await page.setViewportSize({ width: 1440, height: 1000 });
    for (const pair of pairs) {
      const dom = await refreshVisible(page, pair);
      const api = await getWatch(page, pair);
      check(`full watch API remains invariant after denied fixture at ${pair.status}`, JSON.stringify(api.body) === JSON.stringify(beforeApi[pair.status].body), { before: beforeApi[pair.status].body, after: api.body });
      check(`accessible desktop watch DOM remains invariant after denied fixture at ${pair.status}`, dom === beforeDom[pair.status], { before: beforeDom[pair.status], after: dom });
      afterApi[pair.status] = api;
      afterDom[pair.status] = dom;
      check(`complete visible response has no hidden-source or dependent path leak at ${pair.status}`, !hidden.some(marker => (JSON.stringify(api.body) + dom).toLowerCase().includes(marker.toLowerCase())), pair.status);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    const mobileAfterDom = await refreshVisible(page, pairs[3]);
    const mobileAfterApi = await getWatch(page, pairs[3]);
    check('accessible mobile watch DOM remains invariant after denied fixture', mobileAfterDom === mobileBeforeDom, { before: mobileBeforeDom, after: mobileAfterDom });
    check('mobile watch API remains equal to its pre-injection result', JSON.stringify(mobileAfterApi.body) === JSON.stringify(mobileBeforeApi.body), { before: mobileBeforeApi.body, after: mobileAfterApi.body });
    await page.screenshot({ path: path.join(evidenceDir, 'evidence-watch-mobile.png'), fullPage: true });
    const reset = await page.evaluate(async () => {
      const response = await fetch('/api/scenario/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      return { status: response.status, body: await response.json() };
    });
    check('browser removes only its owned restricted test fixture through scenario reseed', reset.status === 200 && reset.body.status === 'reset', reset);
    check('browser makes requests only to this explicitly selected loopback app', hosts.size === 1 && [...hosts][0] === new URL(url).host, [...hosts]);
    check('browser reports no page or console errors', errors.length === 0, errors);

    const report = {
      status: 'PASS', assertion_count: checks.length, checks, requested_hosts: [...hosts],
      confirmation_flow: confirmationFlow,
      api_equal_at_desktop_mobile: JSON.stringify(desktopApi.body) === JSON.stringify(mobileAfterApi.body),
      acl_full_view_invariant: pairs.every(pair => JSON.stringify(beforeApi[pair.status].body) === JSON.stringify(afterApi[pair.status].body) && beforeDom[pair.status] === afterDom[pair.status]) && mobileBeforeDom === mobileAfterDom,
      desktop_viewport: { width: 1440, height: 1000, ...desktopMetrics }, mobile_viewport: mobileMetrics,
      denied_fixture_query_count: fixtures.length,
      screenshots: ['evidence-watch-desktop.png', 'evidence-watch-mobile.png'],
      external_writes: [],
    };
    check('complete desktop/mobile watch view is invariant across all four ACL probes', report.acl_full_view_invariant === true, report.acl_full_view_invariant);
    report.assertion_count = checks.length;
    report.checks = checks;
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify({ status: report.status, assertion_count: report.assertion_count, api_equal_at_desktop_mobile: report.api_equal_at_desktop_mobile, acl_full_view_invariant: report.acl_full_view_invariant, requested_hosts: report.requested_hosts }));
  } catch (error) {
    const report = { status: 'FAIL', assertion_count: checks.length, checks, error: String(error), browser_errors: errors, requested_hosts: [...hosts] };
    fs.mkdirSync(evidenceDir, { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
    console.error(error);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
