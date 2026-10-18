#!/usr/bin/env node
// Live clock-atlas browser evidence. Never fulfill a request with oracle data.
// Usage: node scripts/clock_atlas_browser_assertions.cjs
//   APP_URL DB_PORT OWNED_GRAPH EVIDENCE_DIR RESTRICTED_QUERIES_JSON
// One browser, baseline then injected restricted fixture. The caller cleans its
// owned graph afterward. No server or database is started or reset here.
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');

const ROOT = path.resolve(__dirname, '..');
const DESKTOP = { width: 1440, height: 1600 };
const MOBILE = { width: 390, height: 844 };
const TIMEOUT = 30000;
const HIDDEN = [
  'SRC-RESTRICTED-DOCK-99', 'Restricted Dock Continuity Annex 99',
  'Restricted-only note: logistics bay status unresolved.', 'EQ-RESTRICTED-DOCK-99',
  'DEPT-RESTRICTED-DOCK-99', 'ROLE-RESTRICTED-DOCK-OWNER',
  'SRC-RESTRICTED-92', 'Restricted Continuity Annex 92',
  'Restricted-only instruction: contact the secure lift coordinator.',
  'EV-C4-RESTRICTED-UPDATE', 'REL-INC207-RESTRICTED-DOCK',
  'REL-RESTRICTED-DOCK-SVC', 'REL-SVC-RESTRICTED-DOCK',
  'REL-RESTRICTED-DOCK-OWNER', 'REL-RESTRICTED-DOCK-SOURCE',
  'REL-C4-RESTRICTED-UPDATE', 'REL-C4-RESTRICTED-SOURCE',
];
const FUTURE = /SRC-DOCK-ROSTER-R2|Revision 2|20:18:00Z|ROLE-DOCK-SWING|HND-DOCK-2020|TKT-DOCK-88|supersedes|recorded\/pending/i;
const WITNESSES = /(?:SRC-DOCK-ROSTER-R[12]|ROLE-DOCK-(?:DUTY|SWING)|HND-DOCK-2020|TKT-DOCK-88)/;
const canonical = value => Array.isArray(value) ? value.map(canonical)
  : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const writeJSON = (file, value) => fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');

function options(argv) {
  const [app, dbPort, graph, evidence, fixture] = argv;
  const usage = 'Usage: node scripts/clock_atlas_browser_assertions.cjs APP_URL DB_PORT OWNED_GRAPH EVIDENCE_DIR FIXTURE_JSON';
  if (argv.length !== 5 || argv.some(value => !value)) throw new Error(usage);
  const result = { app: new URL(app), evidence: path.resolve(evidence), dbPort: Number(dbPort), graph, fixture };
  if (!['http:', 'https:'].includes(result.app.protocol)
      || !['127.0.0.1', 'localhost', '[::1]'].includes(result.app.hostname)
      || !result.app.port || result.app.username || result.app.password) {
    throw new Error('Supply an explicit port on the owned loopback app; credentials and non-loopback hosts are forbidden.');
  }
  if (!Number.isInteger(result.dbPort) || result.dbPort < 1 || result.dbPort > 65535) throw new Error('Invalid owned DB port.');
  if (!/^[A-Za-z0-9_:-]+$/.test(result.graph)) throw new Error('Supply a bounded owned graph name.');
  return result;
}

function playwright() {
  // Normal resolution honors project node_modules and NODE_PATH. Also support
  // isolated npm --prefix .local/{npm,node} installs, without installing anything.
  const attempts = [];
  if (process.env.CLOCK_ATLAS_PLAYWRIGHT_MODULE) attempts.push(process.env.CLOCK_ATLAS_PLAYWRIGHT_MODULE);
  for (const prefix of [ROOT, path.join(ROOT, '.local'), path.join(ROOT, '.local/npm'), path.join(ROOT, '.local/node')]) {
    const localRequire = createRequire(path.join(prefix, 'package.json'));
    for (const name of ['playwright-core', 'playwright']) {
      try { attempts.push(localRequire.resolve(name)); } catch (_) { /* Try the next explicit installation. */ }
    }
  }
  attempts.push('/tools/node/lib/node_modules/playwright-core');
  for (const modulePath of [...new Set(attempts)]) {
    try {
      const loaded = require(modulePath);
      if (loaded.chromium) return { chromium: loaded.chromium, modulePath };
    } catch (_) { /* The final error lists every attempted installation. */ }
  }
  throw new Error(`Playwright is unavailable. Install playwright-core locally or set NODE_PATH/CLOCK_ATLAS_PLAYWRIGHT_MODULE. Tried: ${attempts.join(', ')}`);
}

function parseResp(buffer, offset = 0) {
  if (offset >= buffer.length) return null;
  const marker = String.fromCharCode(buffer[offset]);
  const end = buffer.indexOf('\r\n', offset);
  if (end < 0) return null;
  const line = buffer.toString('utf8', offset + 1, end);
  const next = end + 2;
  if (marker === '-') throw new Error(line);
  if (marker === '+') return { value: line, end: next };
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
      values.push(item.value);
      position = item.end;
    }
    return { value: values, end: position };
  }
  throw new Error(`Unsupported RESP marker ${marker}`);
}

function command(parts) {
  return Buffer.concat([Buffer.from(`*${parts.length}\r\n`), ...parts.flatMap(part => {
    const value = Buffer.from(String(part));
    return [Buffer.from(`$${value.length}\r\n`), value, Buffer.from('\r\n')];
  })]);
}

function graphQuery(config, cypher) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: '127.0.0.1', port: config.dbPort });
    let buffer = Buffer.alloc(0);
    socket.setTimeout(10000);
    socket.once('connect', () => socket.write(command(['GRAPH.QUERY', config.graph, cypher])));
    socket.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      try {
        const parsed = parseResp(buffer);
        if (!parsed) return;
        socket.end();
        resolve(parsed.value);
      } catch (error) { socket.destroy(); reject(error); }
    });
    socket.once('timeout', () => { socket.destroy(); reject(new Error('Owned graph query timed out.')); });
    socket.once('error', reject);
  });
}

async function fixtureConnected(config) {
  const dock = await graphQuery(config, "MATCH (:Incident {id:'INC-207'})-[:AFFECTS]->(:Equipment {id:'EQ-RESTRICTED-DOCK-99'})-[:SUPPORTS]->(:Service {id:'SVC-INBOUND-TRANSFER'})-[:SERVES]->(:Department {id:'DEPT-RESTRICTED-DOCK-99'})-[:OWNED_BY]->(:Role {id:'ROLE-RESTRICTED-DOCK-OWNER'})-[:EVIDENCED_BY]->(:Evidence {id:'SRC-RESTRICTED-DOCK-99'}) RETURN count(*) AS count");
  const other = await graphQuery(config, "MATCH (:Incident {id:'INC-204'})-[:HAS_STATE_EVENT]->(:StateEvent {id:'EV-C4-RESTRICTED-UPDATE'})-[:EVIDENCED_BY]->(:Evidence {id:'SRC-RESTRICTED-92'}) RETURN count(*) AS count");
  return { connected: Number(dock[1][0][0]) > 0 && Number(other[1][0][0]) > 0,
    dock_paths: Number(dock[1][0][0]), other_paths: Number(other[1][0][0]) };
}

async function graphSnapshot(config) {
  // Exclude query timing/statistics, but retain every label, property, identity,
  // relationship type and endpoint. Counts alone do not establish read-only use.
  const nodes = await graphQuery(config, 'MATCH (n) RETURN id(n) AS internal_id, labels(n) AS labels, properties(n) AS props ORDER BY internal_id');
  const edges = await graphQuery(config, 'MATCH (a)-[r]->(b) RETURN id(r) AS internal_id, id(a) AS from_id, type(r) AS kind, properties(r) AS props, id(b) AS to_id ORDER BY internal_id');
  return { nodes: nodes[1], edges: edges[1] };
}

async function main() {
  const config = options(process.argv.slice(2));
  fs.mkdirSync(config.evidence, { recursive: true });
  const reportPath = path.join(config.evidence, 'clock-atlas-browser.json');
  const baselinePath = path.join(config.evidence, 'clock-atlas-browser-baseline.json');
  const expectedBytes = fs.readFileSync(path.join(ROOT, 'contracts/clock-atlas/expected-results.json'));
  const matrixBytes = fs.readFileSync(path.join(ROOT, 'contracts/clock-atlas/adversarial-matrix.json'));
  const oracle = JSON.parse(expectedBytes);
  const cells = oracle.cells;
  const cellById = Object.fromEntries(cells.map(cell => [cell.id, cell]));
  const checks = [], errors = [], consoleErrors = [], networkFailures = [], requests = [], blocked = [], liveSelections = [];
  const screenshots = [], jobs = [], groups = {}, samples = { detail: [], sources: [], hindsight: [], labels: [], links: [] };
  const hosts = new Set();
  let browser, context, page, intervention = null, allowExpectedNetworkError = false, baseline, restricted;
  let playwrightInfo, fixtureEvidence = null, injectedQueries = 0, acl = null, apiEqual = false;
  const graphSnapshots = {};
  const check = (name, passed, detail = null) => {
    checks.push({ name, passed: Boolean(passed), detail });
    if (!passed) throw new Error(`${name}: ${JSON.stringify(detail)}`);
    return Boolean(passed);
  };
  const queryFor = cell => new URLSearchParams({ scope: 'operations', effective_at: cell.effective_at, known_at: cell.known_at });
  const appFor = cell => new URL(`/clocks?${queryFor(cell)}`, config.app.origin).href;
  const isAPI = value => {
    try { const url = new URL(value); return url.origin === config.app.origin && url.pathname === '/api/clock-atlas'; }
    catch (_) { return false; }
  };
  const matches = (value, cell) => {
    const url = new URL(value);
    return isAPI(value) && url.searchParams.get('scope') === 'operations'
      && url.searchParams.get('effective_at') === cell.effective_at && url.searchParams.get('known_at') === cell.known_at;
  };
  const clean = value => !HIDDEN.some(marker => JSON.stringify(value).toLowerCase().includes(marker.toLowerCase()));
  const project = (value, expected) => Object.fromEntries(Object.keys(expected).map(key => [key, value?.[key]]));
  const assertAPI = (actual, selected, label) => {
    check(`${label}: successful uncached live atlas response`, actual.status === 200 && actual.service_worker !== true, actual.status);
    check(`${label}: exact independent axes and four literal contract cells`, actual.body.scope === oracle.scope
      && same(actual.body.effective_times, oracle.effective_times) && same(actual.body.known_times, oracle.known_times)
      && Array.isArray(actual.body.cells) && actual.body.cells.length === 4
      && same(actual.body.cells.map((value, index) => project(value, cells[index])), cells), actual.body);
    check(`${label}: selected cell is exactly its own live result`, actual.body.selected_cell_id === selected.id
      && same(actual.body.selected_cell, actual.body.cells.find(cell => cell.id === selected.id))
      && same(project(actual.body.selected_cell, selected), selected), actual.body.selected_cell);
    check(`${label}: no denied or lexical-decoy API markers`, clean(actual) && !JSON.stringify(actual).includes('HND-DOCK-LEXICAL-ONLY'));
  };
  const assertGroup = (name, observed) => {
    check(`${name}: every named frozen browser assertion maps to live evidence`, same(Object.keys(observed).sort(), Object.keys(oracle.groups[name]).sort())
      && same(observed, oracle.groups[name]), observed);
    groups[name] = observed;
  };
  const ready = cell => page.waitForFunction(id => document.querySelector('#atlas-workbench')?.getAttribute('aria-busy') === 'false'
    && document.querySelector('#selected-detail')?.dataset.cellId === id
    && document.querySelector('#atlas-status')?.textContent.startsWith('Ready'), cell.id);
  const settle = async () => {
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  };
  const accessibility = locator => locator.ariaSnapshot();
  const view = async () => ({
    visible_text: await page.locator('body').innerText(),
    accessibility: await accessibility(page.locator('body')),
    // Include every rendered element and accessible-name input, not just the
    // chosen detail fragment. Scripts/styles and hidden retry controls are not a view.
    rendered_dom: await page.locator('body').evaluate(body => {
      function visit(node) {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent.trim() || null;
        if (node.nodeType !== Node.ELEMENT_NODE || ['SCRIPT', 'STYLE'].includes(node.tagName)) return null;
        const style = getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden' || node.hidden) return null;
        return { tag: node.tagName.toLowerCase(), attributes: Object.fromEntries([...node.attributes]
          .map(attribute => [attribute.name, attribute.value]).sort(([a], [b]) => a.localeCompare(b))),
        children: [...node.childNodes].map(visit).filter(value => value !== null) };
      }
      return visit(body);
    }),
  });
  const detail = async () => ({ text: await page.locator('#selected-detail').innerText(),
    accessibility: await accessibility(page.locator('#selected-detail')),
    attributes: await page.locator('#selected-detail').evaluate(node => [...node.querySelectorAll('*')]
      .flatMap(child => [...child.attributes].map(attribute => [attribute.name, attribute.value]))) });
  const sourceLines = source => [source.source_id, source.title, `Observed ${source.observed_at}`,
    ...(source.effective_at ? [`Effective ${source.effective_at}`] : []),
    ...(source.supersedes ? [`Explicitly supersedes ${source.supersedes}`] : []), ...(source.status ? [source.status] : [])];
  const assertDOM = async (cell, label) => {
    const actual = await page.evaluate(() => ({
      detail_id: document.querySelector('#selected-detail').dataset.cellId,
      axes: document.querySelector('.detail-axes')?.textContent,
      path: [...document.querySelectorAll('#detail-path li')].map(node => node.dataset.nodeId),
      path_text: [...document.querySelectorAll('#detail-path li')].map(node => node.lastElementChild?.textContent),
      sources: [...document.querySelectorAll('#detail-sources .source-card')].map(card => ({ source_id: card.dataset.sourceId,
        lines: [...card.children].map(child => child.textContent) })),
      reviews: [...document.querySelectorAll('#detail-review > div')].map(node => node.textContent),
      status: document.querySelector('.detail-status')?.textContent,
      buttons: [...document.querySelectorAll('button[data-cell-id]')].map(button => ({ id: button.dataset.cellId,
        label: button.getAttribute('aria-label'), pressed: button.getAttribute('aria-pressed'), tabindex: button.tabIndex,
        selected: button.closest('.clock-cell').classList.contains('selected') })),
      overview: [...document.querySelectorAll('.clock-cell')].map(card => ({ id: card.dataset.cellId,
        role: card.querySelector('.cell-role').textContent, path: card.querySelector('.cell-path').textContent,
        state: card.querySelector('.cell-state').textContent, reviews: card.querySelector('.cell-reviews').textContent,
        sources: [...card.querySelector('.cell-sources').children].map(source => ({
          id: source.querySelector('strong')?.textContent, title: source.children[1]?.textContent,
          observation: source.children[2]?.textContent,
        })), text: card.innerText })),
      overview_label: document.querySelector('#atlas-grid').closest('section').querySelector('.eyebrow').textContent,
      grid: { role: document.querySelector('#atlas-grid').getAttribute('role'),
        rows: document.querySelector('#atlas-grid').getAttribute('aria-rowcount'),
        columns: document.querySelector('#atlas-grid').getAttribute('aria-colcount'),
        cells: [...document.querySelectorAll('[role="gridcell"]')].map(node => [node.dataset.cellId,
          node.getAttribute('aria-rowindex'), node.getAttribute('aria-colindex')]) },
    }));
    const overviewExpected = cells.map(expected => ({ id: expected.id, role: expected.role_id || 'No role established',
      path: expected.role_path.length ? expected.role_path.join(' → ') : 'No complete ownership path',
      state: expected.role_status === 'known' ? 'KNOWN PATH' : 'UNKNOWN · incomplete evidence',
      reviews: expected.review_candidates.length ? `${expected.review_candidates.join(' · ')} · pending review` : 'Review linkage not established',
      sources: expected.source_versions.map(source => ({ id: source.source_id, title: source.title,
        observation: `Observed ${source.observed_at}${source.effective_at ? ` · effective ${source.effective_at}` : ''}${source.status ? ` · ${source.status}` : ''}` })),
    }));
    check(`${label}: all four overview paths and source cards are literal and independently labeled`,
      same(actual.overview.map(({ text, ...rest }) => rest), overviewExpected)
      && actual.overview_label === 'Matrix overview · separately labeled snapshots', actual.overview);
    const selectedCorrect = actual.detail_id === cell.id && same(actual.path, cell.role_path) && same(actual.path_text, cell.role_path)
      && actual.axes === `Effective at ${cell.effective_at} · sources known by ${cell.known_at}`
      && actual.status === (cell.role_path.length ? `Known path · ${cell.role_id}` : 'Ownership unknown · no complete path')
      && same(actual.reviews, cell.review_candidates.length
        ? [...cell.review_candidates.map(id => `${id} · pending review`), ...(cell.candidate_disposition ? [cell.candidate_disposition] : [])]
        : ['No explicit review candidate established. Linkage is unknown.']);
    samples.detail.push(check(`${label}: detail contains only the selected path, axes and pending review candidates`, selectedCorrect, actual));
    samples.sources.push(check(`${label}: exact source-card IDs, titles, observation/effective instants and supersession metadata`,
      same(actual.sources, cell.source_versions.map(source => ({ source_id: source.source_id, lines: sourceLines(source) }))), actual.sources));
    const labelsCorrect = actual.buttons.length === 4 && actual.buttons.every(button => {
      const expected = cellById[button.id];
      return expected && button.label === `Effective ${expected.effective_at} · known by ${expected.known_at}`
        && button.pressed === String(button.id === cell.id) && button.selected === (button.id === cell.id)
        && button.tabindex === (button.id === cell.id ? 0 : -1);
    }) && same(actual.grid, { role: 'grid', rows: '2', columns: '2',
      cells: cells.map(expected => [expected.id, String(expected.row + 1), String(expected.column + 1)]) });
    samples.labels.push(check(`${label}: semantic matrix, explicit non-color states and isolated accessible button names`, labelsCorrect, actual.buttons));
    if (cell.column === 0) {
      const selectedView = await detail();
      const selectedButton = page.locator(`button[data-cell-id="${cell.id}"]`);
      const noHindsight = !FUTURE.test(JSON.stringify(selectedView))
        && !FUTURE.test(await accessibility(selectedButton))
        && actual.overview.filter(card => cellById[card.id].column === 0).every(card => !FUTURE.test(card.text))
        && (cell.role_status !== 'unknown' || (!/ROLE-DOCK-DUTY/.test(JSON.stringify(selectedView)) && actual.path.length === 0));
      samples.hindsight.push(check(`${label}: early/crossed visible detail and accessibility have no future-source or inferred-role hints`, noHindsight, selectedView));
    }
    const link = new URL(await page.locator('#selected-link').getAttribute('href'), config.app.origin);
    samples.links.push(check(`${label}: selected deep link preserves both independent clocks`, link.href === appFor(cell), link.href));
  };
  const loadCell = async (cell, trigger, action) => {
    const responsePromise = page.waitForResponse(response => matches(response.url(), cell) && response.request().method() === 'GET');
    responsePromise.catch(() => {});
    await action();
    const response = await responsePromise;
    const actual = { status: response.status(), body: await response.json(), service_worker: response.fromServiceWorker() };
    assertAPI(actual, cell, trigger);
    await ready(cell);
    await assertDOM(cell, trigger);
    liveSelections.push({ trigger, url: response.url(), selected_cell_id: actual.body.selected_cell_id, status: actual.status, live: true });
    return actual;
  };
  const clickCell = (cell, trigger) => loadCell(cell, trigger, () => page.locator(`button[data-cell-id="${cell.id}"]`).click());
  const navigateCell = (cell, trigger) => loadCell(cell, trigger, async () => {
    const response = await page.goto(appFor(cell), { waitUntil: 'domcontentloaded' });
    check(`${trigger}: direct clock-pair navigation responds on the owned origin`, response?.status() === 200 && new URL(response.url()).origin === config.app.origin, response?.status());
  });
  const metrics = () => page.evaluate(() => {
    const box = node => { const rect = node.getBoundingClientRect(); return { x: rect.x + scrollX, y: rect.y + scrollY,
      width: rect.width, height: rect.height, right: rect.right + scrollX, bottom: rect.bottom + scrollY }; };
    return { viewport: { width: innerWidth, height: innerHeight }, document_width: document.documentElement.scrollWidth,
      body_width: document.body.scrollWidth, cards: [...document.querySelectorAll('.clock-cell')].map(box),
      detail: box(document.querySelector('#selected-detail')) };
  });
  const noOverflow = value => value.document_width <= value.viewport.width + 2 && value.body_width <= value.viewport.width + 2;
  const desktopLayout = value => value.cards.length === 4 && noOverflow(value)
    && value.cards.every(card => card.width > 0 && card.height > 0 && card.x >= 0 && card.right <= value.viewport.width + 1 && card.bottom <= value.viewport.height)
    && Math.abs(value.cards[0].y - value.cards[1].y) < 2 && Math.abs(value.cards[2].y - value.cards[3].y) < 2
    && value.cards[1].x > value.cards[0].x && value.cards[2].y >= value.cards[0].bottom;
  const mobileLayout = value => value.cards.length === 4 && noOverflow(value)
    && value.cards.every((card, index) => card.width > 0 && card.height > 0 && card.x >= 0 && card.right <= value.viewport.width + 1
      && Math.abs(card.x - value.cards[0].x) < 2 && Math.abs(card.width - value.cards[0].width) < 2
      && (index === 0 || card.y >= value.cards[index - 1].bottom))
    && value.detail.y >= value.cards[3].bottom;
  const captureViews = async phase => {
    const result = {};
    for (const [name, viewport] of [['desktop', DESKTOP], ['mobile', MOBILE]]) {
      await page.setViewportSize(viewport);
      result[name] = {};
      for (const cell of cells) {
        const api = await navigateCell(cell, `${phase}/${name}/${cell.id}`);
        await settle();
        const layout = await metrics();
        check(`${phase}/${name}/${cell.id}: four ${name === 'mobile' ? 'stacked' : 'visible two-by-two'} cards without horizontal overflow`,
          name === 'mobile' ? mobileLayout(layout) : desktopLayout(layout), layout);
        result[name][cell.id] = { api, view: await view(), metrics: layout };
      }
      await clickCell(cellById['e0-k1'], `${phase}/${name}/screenshot-pair`);
      const filename = `clock-atlas-${name}${phase === 'restricted' ? '-restricted' : ''}.png`;
      await page.screenshot({ path: path.join(config.evidence, filename), fullPage: true });
      screenshots.push(filename);
    }
    return result;
  };
  const clearedDetail = async word => {
    const content = await detail();
    const state = await page.locator('#selected-detail').evaluate(node => ({ id: node.dataset.cellId || null,
      paths: node.querySelectorAll('#detail-path li').length, sources: node.querySelectorAll('.source-card').length,
      links: node.querySelectorAll('#selected-link').length }));
    return { cleared: state.id === null && state.paths === 0 && state.sources === 0 && state.links === 0
      && content.text.includes(word) && !WITNESSES.test(JSON.stringify(content)), state, content };
  };
  const arm = (kind, cell) => {
    const job = { kind, cell, ready: deferred(), release: deferred(), done: deferred(), result: null, delivery: null };
    // Keep an early route failure reportable even if it precedes click completion.
    job.ready.promise.catch(() => {});
    jobs.push(job);
    intervention = job;
    return job;
  };
  const waitJob = async (promise, label) => {
    let timer;
    try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} timed out`)), TIMEOUT); })]); }
    finally { clearTimeout(timer); }
  };
  const keyboard = async () => {
    await page.setViewportSize(DESKTOP);
    // Offset deep links must retain a keyboard entry point before the first
    // response, not merely after server-normalized evidence has arrived.
    const offset = arm('delay', cellById['e1-k0']);
    const offsetURL = new URL('/clocks', config.app.origin);
    offsetURL.search = new URLSearchParams({ scope: 'operations', effective_at: '2026-10-14T22:30:00+02:00', known_at: '2026-10-14T22:17:59+02:00' });
    await page.goto(offsetURL.href, { waitUntil: 'domcontentloaded' });
    const offsetResponse = await waitJob(offset.ready.promise, 'Offset deep-link live response');
    assertAPI(offsetResponse, offset.cell, 'offset/initial-delayed-response');
    const offsetEntry = await page.evaluate(() => ({ busy: document.querySelector('#atlas-workbench').getAttribute('aria-busy'),
      tabs: [...document.querySelectorAll('button[data-cell-id]')].filter(node => node.tabIndex === 0).map(node => node.dataset.cellId) }));
    check('offset deep link preserves its normalized roving keyboard entry while busy', same(offsetEntry, { busy: 'true', tabs: ['e1-k0'] }), offsetEntry);
    offset.release.resolve();
    await waitJob(offset.done.promise, 'Offset deep-link delivery');
    await ready(offset.cell);
    await assertDOM(offset.cell, 'offset/normalized-detail');
    await navigateCell(cellById['e0-k0'], 'keyboard/start');
    await page.locator('button[data-cell-id="e0-k0"]').focus();
    const moves = [['ArrowRight', 'e0-k1'], ['ArrowDown', 'e1-k1'], ['ArrowLeft', 'e1-k0'], ['ArrowUp', 'e0-k0'],
      ['ArrowLeft', 'e0-k0'], ['ArrowUp', 'e0-k0'], ['End', 'e1-k1'], ['Home', 'e0-k0']];
    const observations = [];
    for (const [key, id] of moves) {
      await loadCell(cellById[id], `keyboard/${key}/${id}`, () => page.keyboard.press(key));
      const state = await page.evaluate(() => ({ focused: document.activeElement?.dataset.cellId,
        pressed: [...document.querySelectorAll('button[data-cell-id][aria-pressed="true"]')].map(node => node.dataset.cellId),
        tabstops: [...document.querySelectorAll('button[data-cell-id]')].filter(node => node.tabIndex === 0).map(node => node.dataset.cellId) }));
      observations.push(check(`keyboard/${key}: moves focus and sole selection within matrix bounds`, same(state, { focused: id, pressed: [id], tabstops: [id] }), state));
    }
    return observations.every(Boolean) && observations.length === moves.length;
  };
  const requestIsolation = async () => {
    await clickCell(cellById['e1-k1'], 'isolation/prior-swing-evidence');
    // Fault-inject ineffective cancellation, not an answer. The delayed real
    // response must actually reach fetch, proving the sequence guard rather
    // than passing only because AbortController discarded the first request.
    await page.evaluate(() => {
      window.__clockAtlasNativeAbortController = window.AbortController;
      window.AbortController = class extends window.AbortController { abort() {} };
    });
    const first = arm('delay', cellById['e0-k0']);
    await page.locator('button[data-cell-id="e0-k0"]').click();
    const actualFirst = await waitJob(first.ready.promise, 'First real response capture');
    assertAPI(actualFirst, first.cell, 'isolation/delayed-real-response');
    const busy = await clearedDetail('Busy');
    const busyFlag = await page.locator('#atlas-workbench').getAttribute('aria-busy');
    const busyCleared = check('isolation: busy state clears prior path, sources, review IDs, deep link and accessible detail', busyFlag === 'true' && busy.cleared, busy);
    await clickCell(cellById['e1-k0'], 'isolation/second-selection-before-first-release');
    const beforeRelease = await view();
    first.release.resolve();
    await waitJob(first.done.promise, 'Delayed response release');
    await settle();
    const afterRelease = await view();
    const staleIgnored = check('isolation: releasing the first real response cannot overwrite the newer crossed/unknown selection',
      first.result?.status === 200 && first.delivery === 'released-real-response' && !first.failure && same(beforeRelease, afterRelease)
      && new URL(page.url()).href === appFor(cellById['e1-k0']), { delivery: first.delivery, result: first.result, equal: same(beforeRelease, afterRelease) });
    await page.evaluate(() => { window.AbortController = window.__clockAtlasNativeAbortController; delete window.__clockAtlasNativeAbortController; });
    const failure = arm('error', cellById['e0-k1']);
    allowExpectedNetworkError = true;
    await page.locator('button[data-cell-id="e0-k1"]').click();
    const realBeforeError = await waitJob(failure.ready.promise, 'Error-test real response capture');
    assertAPI(realBeforeError, failure.cell, 'isolation/real-response-before-network-error');
    failure.release.resolve();
    await waitJob(failure.done.promise, 'Injected transport failure');
    await page.waitForFunction(() => document.querySelector('#atlas-status')?.textContent.startsWith('Error')
      && document.querySelector('#atlas-workbench')?.getAttribute('aria-busy') === 'false'
      && !document.querySelector('#atlas-retry')?.hidden);
    await settle();
    const errorDetail = await clearedDetail('Error');
    const errorGrid = await page.locator('#atlas-grid').innerText();
    const errorsVisible = await page.locator('.cell-state').allTextContents();
    const errorCleared = check('isolation: a real request transport error clears detail and all overview evidence with a visible retry',
      failure.delivery === 'aborted-for-error-test' && errorDetail.cleared && !WITNESSES.test(errorGrid)
      && errorsVisible.length === 4 && errorsVisible.every(value => value === 'ERROR · evidence unavailable'), { errorDetail, errorGrid });
    allowExpectedNetworkError = false;
    const retry = await loadCell(cellById['e0-k1'], 'isolation/explicit-retry-live-read', () => page.locator('#atlas-retry').click());
    const recovered = check('isolation: retry obtains a new successful live response and restores only the selected evidence',
      retry.status === 200 && await page.locator('#atlas-retry').isHidden()
      && await page.locator('#atlas-workbench').getAttribute('aria-busy') === 'false');
    return { busy_clears_detail: busyCleared, error_clears_detail: errorCleared, retry_recovers: recovered,
      stale_response_ignored: staleIgnored, selection_reads_live: liveSelections.length > 0 && liveSelections.every(item => item.live)
        && matches(actualFirst.url, first.cell) && matches(realBeforeError.url, failure.cell) };
  };
  const onSignal = async signal => {
    for (const job of jobs) job.release.resolve();
    if (browser) await browser.close().catch(() => {});
    process.exit(signal === 'SIGINT' ? 130 : 143);
  };
  process.once('SIGINT', onSignal);
  process.once('SIGTERM', onSignal);
  try {
    for (const source of oracle.pinned_sources) check(`pinned source oracle remains unchanged: ${source.file}`,
      sha256(fs.readFileSync(path.join(ROOT, source.file))) === source.sha256);
    graphSnapshots.before_baseline_reads = await graphSnapshot(config);
    const loaded = playwright();
    playwrightInfo = { module: loaded.modulePath, chromium_executable: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || 'Playwright-managed headless shell' };
    const launchOptions = { headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] };
    // executablePath() identifies full Chrome, not the managed headless shell.
    // Let Playwright select the installed shell unless explicitly configured.
    if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) launchOptions.executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
    browser = await loaded.chromium.launch(launchOptions);
    context = await browser.newContext({ viewport: DESKTOP, serviceWorkers: 'block', locale: 'en-GB', timezoneId: 'UTC', reducedMotion: 'reduce' });
    // Guard every browser request before connecting, including redirects.
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== config.app.origin) {
        blocked.push(request.url());
        await route.abort('blockedbyclient');
        return;
      }
      const job = intervention;
      if (!job || !matches(request.url(), job.cell)) { await route.continue(); return; }
      intervention = null;
      try {
        // Fetch the actual app first. The only interventions are withholding this
        // real response or aborting its transport; no JSON answer is synthesized.
        const response = await route.fetch({ timeout: TIMEOUT, maxRedirects: 0 });
        job.result = { status: response.status(), body: await response.json(), url: request.url(), service_worker: false };
        job.ready.resolve(job.result);
        await job.release.promise;
        if (job.kind === 'error') {
          await route.abort('failed');
          job.delivery = 'aborted-for-error-test';
        } else {
          try { await route.fulfill({ response }); job.delivery = 'released-real-response'; }
          catch (error) {
            // AbortController may cancel the superseded request before release.
            if (!request.failure() && !/already handled|closed|canceled|cancelled|aborted/i.test(String(error))) throw error;
            job.delivery = 'superseded-request-cancelled';
          }
        }
      } catch (error) {
        job.failure = String(error);
        job.ready.reject(error);
        await route.abort('failed').catch(() => {});
      } finally { job.done.resolve(); }
    });
    page = await context.newPage();
    page.setDefaultTimeout(TIMEOUT);
    page.setDefaultNavigationTimeout(TIMEOUT);
    page.on('pageerror', error => errors.push(String(error)));
    page.on('console', message => {
      if (message.type() !== 'error') return;
      const expected = allowExpectedNetworkError && isAPI(message.location().url)
        && /Failed to load resource: net::ERR_FAILED/.test(message.text());
      consoleErrors.push({ text: message.text(), location: message.location(), expected });
      if (!expected) errors.push(message.text());
    });
    page.on('request', request => {
      const url = new URL(request.url());
      hosts.add(url.host);
      requests.push({ method: request.method(), url: request.url() });
    });
    page.on('requestfailed', request => networkFailures.push({ url: request.url(), error: request.failure()?.errorText,
      expected: isAPI(request.url()) && (allowExpectedNetworkError || request.failure()?.errorText === 'net::ERR_ABORTED') }));

    const arrowSelection = await keyboard();
    assertGroup('request-isolation', await requestIsolation());
    const views = await captureViews('baseline');
    assertGroup('keyboard-mobile', {
      desktop_four_visible: Object.values(views.desktop).every(item => desktopLayout(item.metrics)),
      mobile_four_stacked: Object.values(views.mobile).every(item => mobileLayout(item.metrics)),
      arrow_selection: arrowSelection, deep_link_pair: samples.links.length >= 8 && samples.links.every(Boolean),
      detail_only_selected: samples.detail.length >= 8 && samples.detail.every(Boolean),
      source_cards_exact: samples.sources.length >= 8 && samples.sources.every(Boolean),
      unknown_no_hindsight: samples.hindsight.length >= 4 && samples.hindsight.every(Boolean),
      non_color_labels: samples.labels.length >= 8 && samples.labels.every(Boolean),
    });
    baseline = { schema_version: 1, app_origin: config.app.origin, expected_sha256: sha256(expectedBytes),
      matrix_sha256: sha256(matrixBytes), groups: { ...groups }, views, live_selections: [...liveSelections] };
    writeJSON(baselinePath, baseline);
    graphSnapshots.after_baseline_reads = await graphSnapshot(config);
    check('all baseline browser reads leave complete node and relationship properties unchanged',
      same(graphSnapshots.before_baseline_reads, graphSnapshots.after_baseline_reads));

    const fixture = JSON.parse(fs.readFileSync(config.fixture, 'utf8'));
    check('restricted fixture is a nonempty explicit array of graph queries', Array.isArray(fixture)
      && fixture.length > 0 && fixture.every(query => typeof query === 'string' && query.trim().length > 0));
    for (const query of fixture) { await graphQuery(config, query); injectedQueries++; }
    fixtureEvidence = await fixtureConnected(config);
    check('accepted restricted tripwires are really connected in the supplied owned graph', fixtureEvidence.connected, fixtureEvidence);
    graphSnapshots.before_restricted_reads = await graphSnapshot(config);
    restricted = await captureViews('restricted');
    graphSnapshots.after_restricted_reads = await graphSnapshot(config);
    check('all restricted browser reads leave complete node and relationship properties unchanged',
      same(graphSnapshots.before_restricted_reads, graphSnapshots.after_restricted_reads));
    const viewEqual = name => cells.every(cell => same(baseline.views[name][cell.id].view, restricted[name][cell.id].view));
    apiEqual = ['desktop', 'mobile'].every(name => cells.every(cell => same(baseline.views[name][cell.id].api, restricted[name][cell.id].api)));
    acl = { desktop_view_equal: viewEqual('desktop'), mobile_view_equal: viewEqual('mobile'),
      no_hidden_markers: clean(baseline.views) && clean(restricted) };
    check('ACL: complete visible/accessibility desktop view is equal for every selected pair', acl.desktop_view_equal);
    check('ACL: complete visible/accessibility mobile view is equal for every selected pair', acl.mobile_view_equal);
    check('ACL: full real API responses are invariant across both viewports and every selection', apiEqual);
    check('ACL: no denied literal appears in any full API, visible DOM or accessible view', acl.no_hidden_markers);
    writeJSON(path.join(config.evidence, 'clock-atlas-browser-restricted.json'), { app_origin: config.app.origin, views: restricted });
    writeJSON(path.join(config.evidence, 'clock-atlas-browser-graph-snapshots.json'), graphSnapshots);
    await settle();
    check('browser contacts only the explicitly selected loopback app', blocked.length === 0 && hosts.size === 1
      && [...hosts][0] === config.app.host && requests.every(item => new URL(item.url).origin === config.app.origin), { hosts: [...hosts], blocked });
    check('all browser app requests are read-only GETs', requests.length > 0 && requests.every(item => item.method === 'GET'), requests);
    check('no unexpected browser, console or request failures', errors.length === 0 && networkFailures.every(item => item.expected), { errors, consoleErrors, networkFailures });
    const report = { status: 'PASS', assertion_count: checks.length, checks, groups,
      acl, acl_full_view_invariant: acl.desktop_view_equal && acl.mobile_view_equal && apiEqual,
      full_api_equal: apiEqual,
      graph_read_only: same(graphSnapshots.before_baseline_reads, graphSnapshots.after_baseline_reads)
        && same(graphSnapshots.before_restricted_reads, graphSnapshots.after_restricted_reads),
      graph_snapshots_file: 'clock-atlas-browser-graph-snapshots.json',
      api_equal_at_desktop_mobile: cells.every(cell => same(baseline.views.desktop[cell.id].api, baseline.views.mobile[cell.id].api))
        && (!restricted || cells.every(cell => same(restricted.desktop[cell.id].api, restricted.mobile[cell.id].api))),
      expected_sha256: sha256(expectedBytes), matrix_sha256: sha256(matrixBytes), playwright: playwrightInfo,
      requested_hosts: [...hosts], requests, live_selections: liveSelections, browser_errors: errors, console_errors: consoleErrors,
      network_failures: networkFailures, desktop_viewport: DESKTOP, mobile_viewport: MOBILE,
      request_interventions: jobs.map(job => ({ kind: job.kind, selected_cell_id: job.cell.id, actual_response: job.result,
        delivery: job.delivery, failure: job.failure || null })),
      fixture_query_count: injectedQueries, fixture_connection: fixtureEvidence, fixture_cleanup: 'owned graph cleanup is the caller responsibility; no scenario reset',
      baseline_file: path.basename(baselinePath), restricted_file: restricted ? 'clock-atlas-browser-restricted.json' : null,
      screenshots, external_writes: [], fixture_api_answers: false };
    check('desktop and mobile compare the same complete actual API for all pairs', report.api_equal_at_desktop_mobile);
    report.assertion_count = checks.length;
    writeJSON(reportPath, report);
    console.log(JSON.stringify({ status: report.status, assertion_count: report.assertion_count, groups: report.groups,
      acl, graph_read_only: report.graph_read_only, report: reportPath, requested_hosts: report.requested_hosts, screenshots }));
  } catch (error) {
    writeJSON(reportPath, { status: 'FAIL', assertion_count: checks.length, checks, groups, acl,
      error: String(error), stack: error.stack, browser_errors: errors, console_errors: consoleErrors,
      network_failures: networkFailures, requested_hosts: [...hosts], blocked, requests, live_selections: liveSelections,
      request_interventions: jobs.map(job => ({ kind: job.kind, selected_cell_id: job.cell.id, delivery: job.delivery,
        failure: job.failure || null, actual_response: job.result })), screenshots });
    console.error(error);
    process.exitCode = 1;
  } finally {
    for (const job of jobs) job.release.resolve();
    if (browser) await browser.close();
    process.removeListener('SIGINT', onSignal);
    process.removeListener('SIGTERM', onSignal);
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
