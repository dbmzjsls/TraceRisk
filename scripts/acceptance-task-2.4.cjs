/* Local synthetic demo acceptance; happy path always uses the real API.
 * See docs/evaluation/task-2.4-runbook.md for runtime and worker gate setup.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { createRequire } = require('node:module');
const loader = process.env.PLAYWRIGHT_MODULE ? createRequire(path.resolve(process.env.PLAYWRIGHT_MODULE, 'package.json')) : require;
const { chromium } = loader('playwright');
const root = path.resolve(__dirname, '..');
const base = process.env.WEB_URL || 'http://localhost:3200';
const output = path.join(root, 'docs/evaluation/task-2.4-browser-result.json');
const gate = path.join(root, '.superpowers/sdd/task-2.4-resume-ready');
const companyId = '20000000-0000-0000-0000-000000000001';
const passwords = { analyst: process.env.DEMO_ANALYST_PASSWORD || 'AnalystDemo!2026', reviewer: process.env.DEMO_REVIEWER_PASSWORD || 'ReviewerDemo!2026' };
const result = { startedAt: new Date().toISOString(), phase: 'starting', browser: '', checks: [], apiFailures: [], browserErrors: [], evidence: {} };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function save() { fs.writeFileSync(output, JSON.stringify(result, null, 2) + '\n'); }
function pass(name, detail) { result.checks.push({ name, detail }); console.log(`PASS ${name}`); save(); }
async function until(action, predicate, label, timeout = 120000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { const value = await action(); if (predicate(value)) return value; await delay(1000); }
  throw new Error(`Timed out: ${label}`);
}
(async () => {
  let browser;
  try {
    assert(!fs.existsSync(gate), 'Remove only the old worker gate file before starting a new acceptance run');
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    result.browser = browser.version();
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    let expectedNegative = false;
    page.on('pageerror', error => result.browserErrors.push(error.message));
    page.on('response', response => {
      const url = new URL(response.url());
      if (!expectedNegative && url.pathname.startsWith('/api/') && response.status() >= 400) result.apiFailures.push({ path: url.pathname, status: response.status() });
    });
    async function api(url, method = 'GET', body, key) {
      return page.evaluate(async ({ url, method, body, key }) => {
        const session = JSON.parse(sessionStorage.getItem('tracerisk.session') || 'null');
        const headers = { 'Content-Type': 'application/json', ...(session?.token ? { Authorization: `Bearer ${session.token}` } : {}), ...(key ? { 'Idempotency-Key': key } : {}) };
        const response = await fetch(`/api${url}`, { method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
        return { status: response.status, body: await response.json() };
      }, { url, method, body, key });
    }
    async function login(role) {
      await page.goto(`${base}/login`); await page.waitForLoadState('networkidle');
      await page.getByLabel('用户名', { exact: true }).fill(role);
      await page.getByLabel('密码', { exact: true }).fill(passwords[role]);
      await page.getByRole('button', { name: '登录', exact: true }).click();
      await page.waitForURL('**/companies');
      await page.getByRole('link', { name: '华辰智造（合成演示）有限公司', exact: true }).waitFor();
    }
    async function gotoCompany() {
      await page.goto(`${base}/companies/${companyId}`);
      await page.getByRole('heading', { name: '华辰智造（合成演示）有限公司', exact: true }).waitFor();
    }
    async function logout() {
      await page.getByRole('button', { name: '退出', exact: true }).click(); await page.waitForURL('**/login');
      assert.equal(await page.evaluate(() => sessionStorage.getItem('tracerisk.session')), null);
      const cacheCount = await page.evaluate(async () => { const { queryClient } = await import('/src/api/client.ts'); return queryClient.getQueryCache().getAll().length + queryClient.getMutationCache().getAll().length; });
      assert.equal(cacheCount, 0);
    }
    await page.goto(base); await page.waitForLoadState('networkidle');
    assert.match(await page.locator('body').innerText(), /合成数据，仅供演示/);
    expectedNegative = true;
    await page.getByLabel('用户名', { exact: true }).fill('analyst');
    await page.getByLabel('密码', { exact: true }).fill('wrong-password');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByRole('alert').waitFor();
    assert.equal(await page.evaluate(() => sessionStorage.getItem('tracerisk.session')), null);
    pass('wrong password rejected with visible feedback');
    expectedNegative = false;
    await login('analyst'); await gotoCompany();
    await page.getByRole('button', { name: '创建调查', exact: true }).waitFor();
    await page.getByLabel('基准日期', { exact: true }).fill('2026-09-11');
    let createPosts = 0;
    let creationKey;
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/investigations' && request.method() === 'POST') { createPosts++; creationKey = request.headers()['idempotency-key']; } });
    const createdResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/investigations' && r.request().method() === 'POST');
    await page.getByRole('button', { name: '创建调查', exact: true }).evaluate(button => { button.click(); button.click(); });
    const created = await (await createdResponse).json();
    await page.waitForURL(`**/investigations/${created.id}`);
    assert.equal(createPosts, 1);
    const creationBody = { company_id: companyId, as_of_date: '2026-09-11' };
    const replay = await api('/investigations', 'POST', creationBody, creationKey);
    assert.equal(replay.body.id, created.id);
    result.evidence.investigationId = created.id;
    result.evidence.runId = created.current_run.id;
    pass('analyst creates real investigation; double click guarded and API replay returns same investigation');
    const detailUrl = `/investigations/${created.id}`;
    const detail = await until(async () => (await api(detailUrl)).body, value => value.current_run?.status === 'awaiting_review', 'real worker awaiting_review');
    await page.getByText('流程等待人工复核，每 5 秒读取当前状态。', { exact: true }).waitFor();
    assert.equal(detail.rule_score, 35); assert.equal(detail.ai_findings.length, 1); assert.equal(detail.ai_findings[0].citations.length, 2);
    assert.equal(await page.getByRole('link', { name: '复核此发现', exact: true }).count(), 0);
    const finding = detail.ai_findings[0];
    result.evidence.findingId = finding.finding_id;
    result.evidence.persistedSteps = detail.steps;
    await page.screenshot({ path: path.join(root, 'docs/evaluation/task-2.4-awaiting-review.png'), fullPage: true });
    expectedNegative = true;
    const forbiddenReview = await api(`/reviews/${finding.finding_id}`, 'POST', { finding_version: finding.finding_version, decision: 'approve', reason: 'role rejection acceptance' }, randomUUID());
    assert.equal(forbiddenReview.status, 403);
    expectedNegative = false;
    pass('real awaiting_review: score 35, one AI, two citations; analyst review control hidden and API 403');
    result.phase = 'awaiting_review'; save(); console.log('AWAITING_REVIEW: stop old worker, start fresh worker, then create gate file');
    await until(async () => fs.existsSync(gate), Boolean, 'fresh worker coordination gate', 600000);
    result.evidence.freshWorkerGate = fs.readFileSync(gate, 'utf8').trim();
    await logout(); pass('logout clears session and all query/mutation cache');
    await login('reviewer'); await gotoCompany();
    assert.equal(await page.getByRole('button', { name: '创建调查', exact: true }).count(), 0);
    expectedNegative = true;
    assert.equal((await api('/investigations', 'POST', creationBody, randomUUID())).status, 403);
    assert.equal((await api(`/reviews/${finding.finding_id}`, 'POST', { finding_version: finding.finding_version + 1, decision: 'approve', reason: 'stale version acceptance' }, randomUUID())).status, 409);
    assert.equal((await api(`/reviews/${finding.finding_id}`, 'POST', { finding_version: finding.finding_version, decision: 'approve', reason: '' }, randomUUID())).status, 422);
    expectedNegative = false;
    pass('reviewer create control hidden, real create API 403, stale finding API 409, blank reason API 422');
    const reviewerId = await page.evaluate(() => JSON.parse(sessionStorage.getItem('tracerisk.session')).user.id);
    for (const variant of ['self', 'non-current-run', 'stale-version']) {
      const negative = structuredClone(detail);
      if (variant === 'self') negative.created_by = reviewerId;
      if (variant === 'non-current-run') negative.current_run.id = randomUUID();
      if (variant === 'stale-version') negative.ai_findings[0].finding_version++;
      await page.route(`**/api${detailUrl}`, route => route.fulfill({ json: negative }));
      await page.goto(`${base}/reviews/${finding.finding_id}`);
      await page.getByText('当前状态、版本或账号不允许提交此复核。服务端会再次校验资格。', { exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: '提交复核', exact: true }).count(), 0);
      await page.unroute(`**/api${detailUrl}`);
      pass(`review eligibility hides submit for ${variant} (controlled read fixture)`);
    }
    await page.goto(`${base}/reviews/${finding.finding_id}`);
    await page.getByLabel('复核理由', { exact: true }).waitFor();
    for (const status of [409, 422]) {
      expectedNegative = true;
      await page.route(`**/api/reviews/${finding.finding_id}`, route => route.fulfill({ status, json: { detail: `验收错误 ${status}` } }));
      await page.getByLabel('复核理由', { exact: true }).fill('核对两宗合成案件引用，批准本版本结论。');
      await page.getByRole('button', { name: '提交复核', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: `验收错误 ${status}` }).waitFor();
      await page.unroute(`**/api/reviews/${finding.finding_id}`);
      pass(`visible ${status} review feedback (controlled transport)`);
    }
    await page.route(`**/api/reviews/${finding.finding_id}`, route => route.abort('failed'));
    await page.getByRole('button', { name: '提交复核', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '无法连接服务，请检查连接后重试' }).waitFor();
    await page.unroute(`**/api/reviews/${finding.finding_id}`);
    pass('visible network failure with retryable form (controlled transport)');
    expectedNegative = false;
    await page.getByRole('button', { name: '提交复核', exact: true }).click();
    await page.waitForURL(`**/investigations/${created.id}`);
    const completed = await until(async () => (await api(detailUrl)).body, value => value.current_run?.status === 'completed', 'fresh worker resume completed');
    await page.getByText('运行已完成，自动刷新已停止。', { exact: true }).waitFor();
    let terminalRequests = 0;
    const terminalCounter = request => { if (new URL(request.url()).pathname === `/api${detailUrl}`) terminalRequests++; };
    page.on('request', terminalCounter); await delay(6500); page.off('request', terminalCounter);
    assert.equal(terminalRequests, 0);
    result.evidence.reportId = completed.current_run.report_id;
    pass('real reviewer approval and fresh worker checkpoint resume complete; terminal detail makes zero requests over 6.5s');
    await page.getByRole('link', { name: '查看本次运行报告', exact: true }).click();
    await page.getByRole('heading', { name: '最终结论 · 规则评分 35', exact: true }).waitFor();
    const reportPath = `/reports/${completed.current_run.report_id}`;
    const report = (await api(reportPath)).body;
    assert.equal(report.snapshot.rule_score, 35);
    assert.equal(report.snapshot.ai_findings.length, 1); assert.equal(report.snapshot.final_findings.length, 1);
    assert.equal(report.snapshot.ai_findings[0].citations.length, 2);
    assert.deepEqual(report.snapshot.final_findings[0], report.snapshot.ai_findings[0]);
    assert.equal(report.snapshot.human_decisions.length, 1); assert.equal(report.snapshot.human_decisions[0].decision, 'approve');
    assert.equal(await page.locator('iframe').getAttribute('sandbox'), '');
    await page.screenshot({ path: path.join(root, 'docs/evaluation/task-2.4-completed-report.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({ path: path.join(root, 'docs/evaluation/task-2.4-report-narrow.png'), fullPage: true });
    pass('stored report preserves original AI, approval, final score 35 and two citations; report fits 390px viewport');
    const malicious = structuredClone(report);
    const marker = '<img id="untrusted-probe" src=x onerror="window.__unsafe=1">';
    malicious.snapshot.company.name = marker;
    malicious.snapshot.ai_findings[0].summary = marker;
    malicious.html_content = '<script>parent.__unsafe=1;window.__unsafe=1</script><p>Sandbox probe</p>';
    await page.route(`**/api${reportPath}`, route => route.fulfill({ json: malicious }));
    await page.reload(); await page.getByRole('heading', { name: '最终结论 · 规则评分 35', exact: true }).waitFor();
    assert((await page.locator('body').innerText()).includes(marker));
    assert.equal(await page.locator('#untrusted-probe').count(), 0);
    assert.equal(await page.evaluate(() => window.__unsafe), undefined);
    assert.equal(await page.locator('iframe').getAttribute('sandbox'), '');
    const frame = await page.locator('iframe').contentFrame();
    await frame.getByText('Sandbox probe', { exact: true }).waitFor();
    assert.equal(await page.frames().find(value => value !== page.mainFrame()).evaluate(() => window.__unsafe), undefined);
    await page.unroute(`**/api${reportPath}`);
    pass('hostile dynamic report text is escaped; empty sandbox blocks script execution in archived HTML');
    expectedNegative = true;
    await page.route(`**/api${reportPath}`, route => route.fulfill({ status: 401, contentType: 'text/plain', body: 'expired' }));
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await page.waitForURL('**/login**');
    assert.equal(await page.evaluate(() => sessionStorage.getItem('tracerisk.session')), null);
    const cacheAfter401 = await page.evaluate(async () => { const { queryClient } = await import('/src/api/client.ts'); return [queryClient.getQueryCache().getAll().length, queryClient.getMutationCache().getAll().length]; });
    assert.deepEqual(cacheAfter401, [0, 0]);
    assert(!(await page.locator('body').innerText()).includes(marker));
    await page.unroute(`**/api${reportPath}`);
    pass('malformed 401 redirects to login and clears session, query and mutation caches, and sensitive DOM');
    assert.deepEqual(result.apiFailures, []); assert.deepEqual(result.browserErrors, []);
    result.phase = 'passed'; result.finishedAt = new Date().toISOString(); save();
    console.log(`PASS all ${result.checks.length} acceptance checks; ${output}`);
  } catch (error) {
    result.phase = 'failed'; result.failure = error.message; save(); console.error(error.message); process.exitCode = 1;
  } finally { if (browser) await browser.close(); }
})();
