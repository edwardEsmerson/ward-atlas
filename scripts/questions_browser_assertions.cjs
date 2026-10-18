#!/usr/bin/env node
'use strict';
/*
 * Read-only, local-origin browser evidence. No reset/seed, credentials, or model calls.
 * CLI (base URL is automatically resolved to /ask):
 *   node scripts/questions_browser_assertions.cjs --url http://127.0.0.1:PORT \
 *     --phase cold|seeded --evidence-dir DIR [--report REPORT.json]
 *     [--snapshot SNAPSHOT.json] [--compare-snapshot BASELINE.json]
 * Also accepts URL REPORT.json SCREENSHOT_DIR [cold|seeded] for older runners.
 * Playwright discovery: PLAYWRIGHT_MODULE, repository-installed packages, then
 * the existing workshop /tools/node/lib/node_modules/playwright-core location.
 *
 * Stable UI selectors:
 * #questions-app, #questions-form; #question-impact/#question-sop/#question-decision
 * (native radios named intent); #question-input; #question-time (one input, name
 * as_of or known_at); #question-time-label; #question-axis; #question-compose;
 * #question-submit; #question-notice[data-state]; #question-answer[data-status]
 * [data-method][data-template]; #answer-heading; #answer-status; #answer-time;
 * #answer-method; #method-detail; #submitted-question; #answer-sections;
 * #source-facts/#graph-paths/#missing-evidence > [data-fact-id]; each fact has
 * .canonical-kind/.canonical-text/.canonical-id, optional .path-node,
 * .relationship-{from,type,to,status}, .canonical-data, and .citation rows.
 * Citations expose .citation-id/.citation-title/.citation-observed-at. Global
 * observations live in #answer-citations. IDs are never deduplicated by source.
 * Decision records expose .record-title, .derived-evidence/.derived-label.
 *
 * JSON report: status, assertion_count, assertions (also checks), desktop/mobile
 * {api,text,accessibility_tree,payload,rendered}, keyboard_submission,
 * output_escaped, screenshots, snapshots, browser_errors, requested_hosts.
 * Seeded final desktop/mobile snapshots ALWAYS contain the live 18:30 impact
 * answer. text/ARIA are RESULT-only and contain no port, clock, or request ID.
 * A standalone snapshot file supports exact before/after ACL comparison; the
 * optional --compare-snapshot asserts API, DOM text, and ARIA invariance.
 * Isolated route fixtures only test escaping/layout/error handling and are
 * explicitly excluded from live API evidence and final ACL snapshots.
 */
const fs = require('node:fs');
const path = require('node:path');

function options(argv) {
  if (argv[0] && !argv[0].startsWith('--')) {
    const [url, report, evidenceDir, phase = 'seeded'] = argv;
    if (!url || !report || !evidenceDir || argv.length > 4) throw new Error('Invalid positional arguments.');
    return { url, report, evidenceDir, phase };
  }
  const result = { phase: 'seeded' };
  const names = { '--url': 'url', '--phase': 'phase', '--evidence-dir': 'evidenceDir', '--report': 'report', '--snapshot': 'snapshot', '--compare-snapshot': 'compareSnapshot' };
  for (let index = 0; index < argv.length; index += 2) {
    const name = names[argv[index]];
    if (!name || !argv[index + 1] || argv[index + 1].startsWith('--') || name in result && name !== 'phase') throw new Error(`Invalid option: ${argv[index]}`);
    result[name] = argv[index + 1];
  }
  return result;
}
function playwright() {
  const candidates = [process.env.PLAYWRIGHT_MODULE, 'playwright', 'playwright-core', '/tools/node/lib/node_modules/playwright-core'].filter(Boolean);
  for (const candidate of candidates) {
    try { return require(candidate); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
  }
  throw new Error('Playwright not found. Set PLAYWRIGHT_MODULE to its local module path.');
}
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const equal = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
const writeJSON = (file, value) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); };
const clone = value => JSON.parse(JSON.stringify(value));
const CONTEXT = 'inc204-operations';
const TIMES = { impact: '2026-10-14T18:30:00Z', restored: '2026-10-14T19:30:00Z', before: '2026-10-14T15:59:59Z', claims: '2026-10-14T16:04:00Z', record: '2026-10-14T16:05:00Z' };
const TYPES = {
  impact: { selector: '#question-impact', axis: 'as_of', question: 'Who is affected by INC-204?' },
  sop_disagreement: { selector: '#question-sop', axis: 'known_at', question: 'Explain the SOP-OPS-9 disagreement' },
  decision: { selector: '#question-decision', axis: 'known_at', question: 'What was known about the decision?' }
};

async function run(config) {
  const target = new URL(config.url);
  if (!['http:', 'https:'].includes(target.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname) || target.username || target.password) throw new Error('Select one credential-free loopback URL.');
  if (!['cold', 'seeded'].includes(config.phase) || !config.evidenceDir) throw new Error('Supply --phase cold|seeded and --evidence-dir DIR.');
  const url = new URL('/ask', target.origin).href;
  const apiURL = new URL('/api/questions', target.origin).href;
  const reportPath = config.report || path.join(config.evidenceDir, `questions-browser-${config.phase}.json`);
  const snapshotPath = config.snapshot || path.join(config.evidenceDir, `questions-accessible-${config.phase}.json`);
  fs.mkdirSync(config.evidenceDir, { recursive: true });
  const checks = [];
  const errors = [];
  const expectedConsoleErrors = [];
  const origins = new Set();
  const screenshots = [];
  const requests = [];
  const report = { schema_version: 1, status: 'running', phase: config.phase, url, assertion_count: 0, assertions: checks, checks, desktop: null, mobile: null, keyboard_submission: false, output_escaped: false, screenshots, snapshots: {}, browser_errors: errors, expected_console_errors: expectedConsoleErrors, requested_hosts: [] };
  let browser;
  let page;
  let expectedHTTPError = false;
  const check = (name, condition, detail = null) => {
    checks.push({ name, passed: Boolean(condition), detail });
    if (process.env.QUESTIONS_BROWSER_TRACE) process.stderr.write(`${condition ? 'PASS' : 'FAIL'} ${name}\n`);
    if (!condition) throw new Error(`${name}: ${JSON.stringify(detail)}`);
  };
  try {
    const { chromium } = playwright();
    browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
    context.setDefaultTimeout(15000);
    page = await context.newPage();
    page.setDefaultTimeout(15000);
    await context.route('**/*', route => {
      const request = route.request();
      if (new URL(request.url()).origin !== target.origin) return route.abort('blockedbyclient');
      return route.continue();
    });
    page.on('pageerror', error => errors.push(String(error)));
    page.on('console', message => {
      if (message.type() !== 'error') return;
      if (expectedHTTPError && /Failed to load resource.*(?:400|503)/.test(message.text())) expectedConsoleErrors.push(message.text());
      else errors.push(message.text());
    });
    page.on('request', request => {
      const parsed = new URL(request.url());
      origins.add(parsed.origin);
      requests.push({ method: request.method(), path: parsed.pathname });
    });
    const screenshot = async name => {
      const file = path.join(config.evidenceDir, `questions-${config.phase}-${name}.png`);
      await page.screenshot({ path: file, fullPage: true });
      screenshots.push(file);
    };
    const capture = async (api, payload) => ({
      api, payload,
      text: await page.locator('#question-answer').innerText(),
      accessibility_tree: await page.locator('#question-answer').ariaSnapshot(),
      rendered: await page.locator('#question-answer').evaluate(root => {
        const text = (row, selector) => row.querySelector(selector)?.textContent ?? null;
        const citations = row => [...row.querySelectorAll('.citation')].map(item => ({ source_id: text(item, '.citation-id'), title: text(item, '.citation-title'), observed_at: text(item, '.citation-observed-at') }));
        return {
          status: root.dataset.status, method: root.dataset.method, template: root.dataset.template,
          sections: Object.fromEntries(['source-facts', 'graph-paths', 'missing-evidence'].map(id => [id.replaceAll('-', '_'), [...root.querySelectorAll(`#${id} > [data-fact-id]`)].map(row => ({
            id: row.dataset.factId, kind: text(row, '.canonical-kind'), text: text(row, '.canonical-text'),
            citations: citations(row), path: [...row.querySelectorAll('.path-node')].map(item => item.textContent),
            relationships: [...row.querySelectorAll(':scope > .relationships > .relationship')].map(edge => {
              const result = { from: text(edge, '.relationship-from'), relationship: text(edge, '.relationship-type'), to: text(edge, '.relationship-to') };
              const status = text(edge, '.relationship-status');
              if (status) result.status = status;
              return result;
            }),
            data: row.querySelector('.canonical-data') ? JSON.parse(text(row, '.canonical-data')) : null
          }))])),
          citations: citations(root.querySelector('#answer-citations')),
          section_order: [...root.querySelectorAll('#answer-sections > [data-section]')].map(section => section.dataset.section)
        };
      })
    });
    const assertParity = (name, snapshot) => {
      const { api, rendered } = snapshot;
      check(`${name}: response status and honest method match the API`, api.status === rendered.status && api.method === rendered.method, { status: api.status, method: api.method });
      for (const key of ['source_facts', 'graph_paths', 'missing_evidence']) {
        const expected = api[key].map(item => ({ id: item.id, kind: item.kind, text: item.text, citations: item.citations || [], path: item.path || [], relationships: item.relationships || [], data: item.data || null }));
        const rank = api.presentation.fact_ids;
        expected.sort((a, b) => rank.indexOf(a.id) - rank.indexOf(b.id));
        check(`${name}: canonical ${key} IDs, wording, data, paths, and citations equal API`, equal(rendered.sections[key], expected));
      }
      check(`${name}: global source ID/title/observation tuples exactly match API`, equal(rendered.citations, api.citations), rendered.citations);
    };
    const prepare = async (type, at, question = TYPES[type].question) => {
      await page.locator(TYPES[type].selector).check();
      await page.locator('#question-input').fill(question);
      await page.locator('#question-time').fill(at);
      await page.locator('#question-compose').uncheck();
    };
    const submit = async ({ keyboard = false, status = 200, live = true } = {}) => {
      const responsePromise = page.waitForResponse(response => response.url() === apiURL && response.request().method() === 'POST');
      if (keyboard) {
        await page.locator('#question-input').focus();
        await page.keyboard.press('Enter');
      } else await page.locator('#question-submit').click();
      const response = await responsePromise;
      const payload = response.request().postDataJSON();
      // The UI deliberately does not consume an error response body. Chromium
      // can discard that closed HTTP/1.1 body before CDP retrieves it; assert
      // the actual HTTP status and rendered error instead of awaiting it.
      const api = status === 200 ? await response.json() : null;
      await page.waitForFunction(() => !document.querySelector('#question-submit').disabled && document.querySelector('#question-notice').dataset.state !== 'loading');
      check(`question POST returns expected HTTP ${status}`, response.status() === status, response.status());
      const axes = ['as_of', 'known_at'].filter(axis => axis in payload);
      check('UI submits exactly one axis and explicit scope/context, not internal intent fields', axes.length === 1 && payload.scope === 'operations' && payload.context === CONTEXT && typeof payload.compose === 'boolean' && Object.keys(payload).length === 5, payload);
      if (status !== 200) return { api, payload };
      if (live) {
        const direct = await context.request.post(apiURL, { data: payload, timeout: 15000 });
        check('rendered POST response equals an independent actual API read', direct.status() === 200 && equal(await direct.json(), api));
      }
      return await capture(api, payload);
    };
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    check('integrated /ask is served successfully', response?.status() === 200, response?.status());
    check('form context is explicitly requested INC-204 operations, not a graph assertion', (await page.locator('#question-context').innerText()).includes('Requested context, not an established incident fact.'));
    check('initial view makes no preloaded facts or source observations', await page.locator('#question-answer').isHidden() && await page.locator('[data-fact-id], .citation').count() === 0);
    check('one labelled explicit time input exists; no browser-local datetime axis', await page.locator('#question-time').count() === 1 && await page.locator('#question-time').getAttribute('type') === 'text' && await page.locator('input[type=datetime-local]').count() === 0);
    check('impact starts on as_of with an offset-explicit timestamp', await page.locator('#question-time').getAttribute('name') === 'as_of' && await page.locator('#question-axis').innerText() === 'as_of' && await page.locator('#question-time').inputValue() === TIMES.impact);
    const keyboardLabels = await page.locator('#questions-form').ariaSnapshot();
    check('native question radios, time, question, composition, and submit are accessible', /radio "Outage impact"/.test(keyboardLabels) && /radio "SOP disagreement"/.test(keyboardLabels) && /radio "As-known decision"/.test(keyboardLabels) && keyboardLabels.includes('textbox "Your question"') && keyboardLabels.includes('button "Ask the evidence'));
    await page.locator('#question-impact').focus();
    await page.keyboard.press('ArrowRight');
    check('native arrow-key navigation selects SOP and updates the one time axis', await page.locator('#question-sop').isChecked() && await page.locator('#question-time').getAttribute('name') === 'known_at' && await page.locator('#question-input').inputValue() === TYPES.sop_disagreement.question);
    await page.keyboard.press('ArrowRight');
    check('native arrow-key navigation reaches decision without a pointer or second time field', await page.locator('#question-decision').isChecked() && await page.locator('#question-time').count() === 1 && await page.locator('#question-input').inputValue() === TYPES.decision.question);

    await prepare('impact', TIMES.impact);
    let impact = await submit({ keyboard: true });
    assertParity('initial impact', impact);
    report.keyboard_submission = true;
    check('Enter in question input submits natively and focuses the result heading', await page.locator('#answer-heading').evaluate(element => document.activeElement === element));
    if (config.phase === 'cold') {
      check('cold graph renders generic unknown without source, path, or record existence hints', impact.api.status === 'unknown' && impact.api.source_facts.length === 0 && impact.api.graph_paths.length === 0 && impact.api.citations.length === 0 && !/SRC-|SOP-OPS|DEC-OPS|ROLE-|REGISTER-|CONSIDERS|TRACKS|EVIDENCED_BY/.test(impact.text), impact.text);
      check('cold unknown explicitly avoids negative ownership and safety conclusions', impact.api.safety_conclusion === false && impact.text.includes('Unknown does not establish absence, resolution, ownership, or safety.'));
      report.snapshots.cold = impact;
    } else {
      const oracle = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'contracts/questions/expected-results.json'), 'utf8'));
      const expected = id => oracle.groups.find(group => group.id === id).expected;
      check('seeded impact contains the independently pinned exact multihop chains', impact.api.status === 'answered' && equal(impact.api.graph_paths.map(item => item.path), expected('impact').paths), impact.api.graph_paths.map(item => item.path));
      check('both same-ID roster observations remain independently inspectable', equal(impact.rendered.citations.filter(item => item.source_id === 'SRC-OPS-ROSTER').map(item => item.observed_at), expected('impact').roster_observation_variants));
      check('each impact relationship label stays explicit and ordered', impact.rendered.sections.graph_paths.every(item => equal(item.relationships.map(edge => edge.relationship), ['AFFECTS', 'SUPPORTS', 'SERVES', 'OWNED_BY'])));
      check('source facts, graph derivations, and missing evidence have separate semantic sections', equal(impact.rendered.section_order, ['source_facts', 'graph_paths', 'missing_evidence']) && impact.text.includes('Canonical source facts') && impact.text.includes('Graph-derived paths & relationships') && impact.text.includes('Missing evidence & limits'));
      report.snapshots.impact = impact;

      await prepare('impact', TIMES.restored);
      check('editing time immediately removes the old answer and citations', await page.locator('#question-answer').isHidden() && await page.locator('[data-fact-id], .citation').count() === 0);
      const restored = await submit();
      assertParity('restoration', restored);
      check('restoration has no active paths and does not establish safety', restored.api.status === 'answered' && restored.api.graph_paths.length === 0 && restored.api.source_facts[0].data.value === 'available' && restored.api.safety_conclusion === false && restored.text.includes('does not establish safety'));
      report.snapshots.restoration = restored;

      await prepare('sop_disagreement', TIMES.record);
      check('SOP question changes the single input axis and visible time label', await page.locator('#question-time').getAttribute('name') === 'known_at' && await page.locator('#question-axis').innerText() === 'known_at' && await page.locator('#question-time-label').innerText() === 'Evidence known at');
      const sop = await submit();
      assertParity('SOP disagreement', sop);
      check('SOP disagreement shows only unverified claims and graph-derived unresolved conflict', equal(sop.api.source_facts.map(item => item.data.version_id), expected('disagreement').versions) && sop.api.source_facts.every(item => item.text.includes('unverified SOP claim')) && equal(sop.api.graph_paths[0].relationships[0], expected('disagreement').conflict) && !sop.text.includes('DEC-OPS'));
      check('SOP view never selects an authoritative version, owner, or procedure', equal(sop.api.sop_selection, { version: null, owner: null, procedure: null }) && sop.text.includes('not a quotation or authoritative rule'));
      report.snapshots.sop = sop;

      await prepare('decision', TIMES.before);
      check('decision uses known_at without adding an as_of input', await page.locator('#question-time').getAttribute('name') === 'known_at' && await page.locator('input[name=as_of]').count() === 0);
      const before = await submit();
      assertParity('before first observation', before);
      check('past unknown result leaks no later SOP, source, record, or relationship identity', before.api.status === 'unknown' && before.api.source_facts.length === 0 && before.api.citations.length === 0 && !/SRC-|SOP-OPS|DEC-OPS|ROLE-|REGISTER-|CONSIDERS|TRACKS|EVIDENCED_BY/.test(before.text), before.text);
      report.snapshots.before = before;

      await prepare('decision', TIMES.claims);
      const claims = await submit();
      assertParity('claims known', claims);
      check('16:04 reveals both independent claims and conflict, but no future decision record', claims.api.source_facts.length === 2 && claims.api.graph_paths.length === 1 && !/DEC-OPS|SRC-DEC|REGISTER-|CONSIDERS|TRACKS/.test(claims.text));
      report.snapshots.claims = claims;

      await prepare('decision', TIMES.record);
      const decision = await submit();
      assertParity('decision record known', decision);
      const record = decision.api.source_facts.find(item => item.data?.id === expected('record-known').record_id);
      check('decision appears at its own source boundary with unresolved state and title visible', record?.data.recorded_state === 'open-unresolved' && equal(record.citations[0], expected('record-known').source) && decision.text.includes(expected('record-known').record_title));
      check('decision provenance and independently known considered edges are visibly graph-derived', await page.locator('.derived-evidence').isVisible() && (await page.locator('.derived-label').innerText()).includes('Graph-derived') && equal(await page.locator('.derived-evidence .relationship-type').allTextContents(), ['TRACKS', 'EVIDENCED_BY', 'CONSIDERS', 'CONSIDERS']));
      await page.locator('#source-facts > [data-fact-id]').last().locator('summary').focus();
      await page.keyboard.press('Enter');
      check('complete canonical structured record data is keyboard-inspectable, not replaced by friendly labels', await page.locator('#source-facts > [data-fact-id]').last().locator('.canonical-data').isVisible() && (await page.locator('#source-facts > [data-fact-id]').last().locator('.canonical-data').innerText()).includes('DEC-OPS-9-REVIEW-01'));
      report.snapshots.decision = decision;

      // Local synthetic fixtures exercise rendering only; no composer request.
      await prepare('impact', TIMES.impact);
      const assisted = clone(impact.api);
      assisted.method = 'model-assisted';
      assisted.presentation = { template: 'paths-first', fact_ids: [...assisted.presentation.fact_ids].reverse() };
      await page.route(apiURL, route => route.fulfill({ status: 200, json: assisted }), { times: 1 });
      const arranged = await submit({ live: false });
      assertParity('isolated bounded layout fixture', arranged);
      check('model-assisted label describes layout only and preserves epistemic sections', arranged.text.includes('Model-assisted layout · deterministic facts') && arranged.text.includes('No generated prose or new claims.') && equal(arranged.rendered.section_order, ['graph_paths', 'source_facts', 'missing_evidence']));
      check('bounded layout can reorder canonical IDs but cannot change facts or citations', equal(arranged.api.source_facts, impact.api.source_facts) && equal(arranged.api.graph_paths, impact.api.graph_paths) && equal(arranged.api.citations, impact.api.citations) && equal(arranged.rendered.sections.graph_paths.map(item => item.id), [...impact.api.graph_paths].reverse().map(item => item.id)));

      const invalidLayout = clone(assisted);
      invalidLayout.presentation.fact_ids[0] = 'injected-unknown-fact';
      await page.route(apiURL, route => route.fulfill({ status: 200, json: invalidLayout }), { times: 1 });
      await submit({ live: false });
      check('out-of-bound assisted IDs reject the whole rendering without stale canonical facts', await page.locator('#question-notice').getAttribute('data-state') === 'error' && await page.locator('#question-answer').isHidden() && await page.locator('[data-fact-id], .citation').count() === 0);

      const escaped = clone(impact.api);
      const hostile = '<svg onload="window.__questionsSourceXss=1">& unsafe source</svg>';
      escaped.source_facts[0].text = hostile;
      escaped.source_facts[0].citations[0].title = hostile;
      escaped.citations[0].title = hostile;
      await page.route(apiURL, route => route.fulfill({ status: 200, json: escaped }), { times: 1 });
      await submit({ live: false });
      check('isolated hostile server fact and citation strings render literally, not as HTML', await page.locator('#source-facts .canonical-text').first().textContent() === hostile && await page.locator('#source-facts .citation-title').first().textContent() === hostile && await page.locator('#question-answer svg').count() === 0 && await page.evaluate(() => !window.__questionsSourceXss));
    }

    // Real endpoint: an escaped unsupported question must clarify, not execute.
    const hostileQuestion = '<img src=x onerror="window.__questionsInputXss=1">';
    await prepare('impact', TIMES.impact, hostileQuestion);
    const escapedInput = await submit({ keyboard: true });
    check('actual API treats markup question as bounded clarification without citations', escapedInput.api.status === 'clarification' && escapedInput.api.source_facts.length === 0 && escapedInput.api.citations.length === 0);
    check('user question is displayed as literal text and cannot create DOM or execute', await page.locator('#submitted-question').textContent() === hostileQuestion && await page.locator('#questions-app img').count() === 0 && await page.evaluate(() => !window.__questionsInputXss));
    report.output_escaped = true;

    await prepare('impact', 'not-a-timestamp');
    expectedHTTPError = true;
    await submit({ status: 400 });
    await page.waitForFunction(() => document.querySelector('#question-notice').dataset.state === 'error');
    expectedHTTPError = false;
    check('invalid explicit time shows error and clears all stale source and path data', await page.locator('#question-answer').isHidden() && await page.locator('[data-fact-id], .citation').count() === 0 && (await page.locator('#question-notice').innerText()).includes('Supply one explicit time'));

    await prepare('impact', TIMES.impact);
    await page.route(apiURL, route => route.fulfill({ status: 503, json: { error: 'Isolated endpoint failure test.' } }), { times: 1 });
    expectedHTTPError = true;
    await submit({ status: 503, live: false });
    expectedHTTPError = false;
    check('endpoint failure shows no stale answer, source IDs, or current-time fallback', await page.locator('#question-answer').isHidden() && await page.locator('[data-fact-id], .citation').count() === 0 && (await page.locator('#question-notice').innerText()).includes('No previous answer or current-time substitute'));

    // Restore the actual deterministic impact response for invariant evidence.
    await prepare('impact', TIMES.impact);
    await page.setViewportSize({ width: 1440, height: 1050 });
    report.desktop = await submit({ keyboard: true });
    assertParity('final desktop', report.desktop);
    check('final desktop is the actual deterministic 18:30 impact result', report.desktop.api.intent === 'impact' && equal(report.desktop.api.time, { axis: 'as_of', value: TIMES.impact }) && report.desktop.api.method === 'deterministic-graph');
    await screenshot('desktop');
    await page.setViewportSize({ width: 390, height: 844 });
    await prepare('impact', TIMES.impact);
    report.mobile = await submit({ keyboard: true });
    assertParity('final mobile', report.mobile);
    const dimensions = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    check('mobile has no page-level horizontal overflow', dimensions.document <= dimensions.width && dimensions.body <= dimensions.width, dimensions);
    check('mobile and desktop actual API answers, result text, and ARIA are identical', equal(report.desktop.api, report.mobile.api) && report.desktop.text === report.mobile.text && report.desktop.accessibility_tree === report.mobile.accessibility_tree);
    check('mobile question controls and evidence submission remain available', await page.locator('#question-input').isVisible() && await page.locator('#question-time').isVisible() && await page.locator('#question-submit').isVisible());
    await screenshot('mobile');

    const invariant = { schema_version: 1, phase: config.phase, desktop: report.desktop, mobile: report.mobile };
    writeJSON(snapshotPath, invariant);
    report.accessible_snapshot = snapshotPath;
    if (config.compareSnapshot) {
      const baseline = JSON.parse(fs.readFileSync(config.compareSnapshot, 'utf8'));
      check('baseline and current API, visible result DOM, and ARIA snapshots are entirely ACL invariant', equal(invariant, baseline));
    }
    check('browser performs no reset, seed, action, or other mutation requests', requests.every(request => request.method === 'GET' || request.method === 'POST' && request.path === '/api/questions'), requests);
    check('all browser requests remain on the selected local origin', origins.size === 1 && [...origins][0] === target.origin, [...origins]);
    check('browser completes without unexpected console or page errors', errors.length === 0, errors);
    check('keyboard and escaped-output proofs completed', report.keyboard_submission && report.output_escaped);
    report.status = 'PASS';
  } catch (error) {
    report.status = 'FAIL';
    report.error = String(error?.stack || error);
    if (page) {
      try { await page.screenshot({ path: path.join(config.evidenceDir, `questions-${config.phase}-failure.png`), fullPage: true }); } catch (_) {}
    }
  } finally {
    report.assertion_count = checks.length;
    report.requested_hosts = [...origins].map(origin => new URL(origin).host).sort();
    writeJSON(reportPath, report);
    if (browser) await browser.close();
  }
  process.stdout.write(JSON.stringify({ status: report.status, phase: report.phase, assertion_count: report.assertion_count, keyboard_submission: report.keyboard_submission, output_escaped: report.output_escaped, report: reportPath, screenshots }) + '\n');
  if (report.status !== 'PASS') {
    process.stderr.write((report.error || 'Browser assertions failed.') + '\n');
    process.exitCode = 1;
  }
}

if (require.main === module) {
  try {
    const config = options(process.argv.slice(2));
    if (!config.url) throw new Error('Usage: node scripts/questions_browser_assertions.cjs --url BASE --phase cold|seeded --evidence-dir DIR [--report REPORT.json]');
    run(config).catch(error => { console.error(error); process.exitCode = 1; });
  } catch (error) { console.error(String(error)); process.exitCode = 1; }
}
module.exports = { run, options, canonical };
