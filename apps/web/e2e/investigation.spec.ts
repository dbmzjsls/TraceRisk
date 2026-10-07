import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test'
import { safeRequest } from './safeRequest'

type Status = 'queued' | 'running' | 'awaiting_review' | 'resume_queued' | 'completed' | 'failed'
type Finding = { finding_id: string; finding_key: string; finding_version: number; title: string; summary: string; reasoning: string }
type Detail = {
  id: string
  company_name: string
  current_run: { id: string; status: Status; report_id: string | null }
  rule_score: number
  ai_findings: Finding[]
  decisions: { id: string; ai_finding_id: string | null; decision: string }[]
  conflicts: {
    fact_type: string
    fact_key: string
    authority_source: string | null
    resolved_value: string | null
    resolution_state: string
    sides: { source_type: string; fact_value: string }[]
  }[]
  coverage: { evidence_sufficiency: string }
}
type Report = {
  id: string
  snapshot: {
    ai_findings: { id: string; title: string; summary: string; reasoning: string }[]
    final_findings: { id: string; title: string; summary: string; reasoning: string; source: string; original_ai_finding_id: string; human_version: number | null }[]
    human_decisions: { decision: string }[]
  }
}
type DbEvidence = {
  status: Status
  rule_score: number
  ai_findings: { id: string; version: number; content_sha256: string }[]
  decisions: { id: string; ai_finding_id: string | null; finding_version: number | null; decision: string }[]
  events: { sequence: number; type: string; key: string }[]
  outbox: Record<string, number>
  outbox_dispatched: Record<string, number>
  resume_jobs: { id: number; status: string }[]
  report_count: number
  report_id: string | null
  report_bytes_sha256: string | null
  report_ai_ids: string[]
  report_final_ids: string[]
  human_versions: { id: string; review_decision_id: string; ai_finding_id: string; finding_version: number; version: number; content_sha256: string }[]
  report_final_sources: { id: string; source: string; original_ai_finding_id: string; human_version: number | null }[]
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const database = process.env.E2E_DB_NAME ?? ''
const phase = process.env.E2E_PHASE ?? 'normal'
const names: Record<string, string> = {
  'clean-operations': 'SYNTHETIC-COMPANY-002',
  'all-rules-95': 'SYNTHETIC-COMPANY-003',
  'cross-source-conflict': 'SYNTHETIC-COMPANY-006',
  'unavailable-source': 'SYNTHETIC-COMPANY-007',
}

function required(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`${name} is required for the E2E run`)
  return value
}

function evidence(runId: string): DbEvidence {
  if (!/^tracerisk_e2e_[0-9a-f]{32}$/.test(database)) {
    throw new Error('E2E_DB_NAME must name an owned disposable E2E database')
  }
  const python = required('E2E_PYTHON')
  try {
    const output = execFileSync(python, [path.join(root, 'scripts/assert_e2e.py'), '--database', database, '--run-id', runId], {
      cwd: root,
      env: { ...process.env, POSTGRES_DB: database, PYTHONPATH: [path.join(root, 'apps/api/src'), root].join(path.delimiter) },
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    })
    return JSON.parse(output) as DbEvidence
  } catch {
    throw new Error('Read-only E2E database assertion failed')
  }
}

async function json<T>(request: APIRequestContext, method: 'get' | 'post', url: string, token: string, data?: object, key?: string): Promise<T> {
  const headers = { Authorization: `Bearer ${token}`, ...(key ? { 'Idempotency-Key': key } : {}) }
  return safeRequest<T>(request, method, `/api${url}`, { headers, data }, method === 'get' ? 'api-read' : 'api-write')
}

async function loginApi(request: APIRequestContext, username: 'analyst' | 'reviewer'): Promise<string> {
  const body = await safeRequest<{ access_token: string }>(request, 'post', '/api/auth/login', { data: { username, password: required(username === 'analyst' ? 'E2E_ANALYST_PASSWORD' : 'E2E_REVIEWER_PASSWORD') } }, 'login')
  return body.access_token
}

async function loginUi(page: Page, username: 'analyst' | 'reviewer'): Promise<void> {
  await page.goto('/login')
  await page.locator('#username').fill(username)
  await page.locator('#password').fill(required(username === 'analyst' ? 'E2E_ANALYST_PASSWORD' : 'E2E_REVIEWER_PASSWORD'))
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/companies(?:\?|$)/)
}

async function companyId(request: APIRequestContext, token: string, scenario: string): Promise<string> {
  const response = await json<{ items: { id: string; unified_credit_code: string }[] }>(request, 'get', '/companies', token)
  const found = response.items.find(item => item.unified_credit_code === names[scenario])
  if (!found) throw new Error(`Scenario ${scenario} is missing from the public catalog`)
  return found.id
}

async function createRun(request: APIRequestContext, token: string, scenario: string): Promise<Detail> {
  const company_id = await companyId(request, token, scenario)
  const created = await json<{ id: string }>(request, 'post', '/investigations', token, { company_id, as_of_date: '2026-09-11' }, randomUUID())
  return json<Detail>(request, 'get', `/investigations/${created.id}`, token)
}

async function detail(request: APIRequestContext, token: string, id: string): Promise<Detail> {
  return json<Detail>(request, 'get', `/investigations/${id}`, token)
}

async function waitStatus(request: APIRequestContext, token: string, id: string, status: Status): Promise<Detail> {
  await expect.poll(async () => (await detail(request, token, id)).current_run.status, { timeout: 60_000 }).toBe(status)
  return detail(request, token, id)
}

async function pageState(page: Page, item: Detail, status: Status): Promise<void> {
  await page.goto(`/investigations/${item.id}`)
  await expect(page.locator('.run-summary')).toContainText(status)
  await expect(page.locator('.run-timeline')).toContainText(item.current_run.id)
  await expect(page.locator('.timeline-list')).toBeVisible()
}

async function report(request: APIRequestContext, token: string, item: Detail): Promise<Report> {
  if (!item.current_run.report_id) throw new Error('Completed Run lacks a report ID')
  return json<Report>(request, 'get', `/reports/${item.current_run.report_id}`, token)
}

async function reviewFindings(request: APIRequestContext, token: string, item: Detail, decision: 'approve' | 'reject', duplicate = false): Promise<string[]> {
  const ids: string[] = []
  for (const finding of item.ai_findings) {
    const key = randomUUID()
    const payload = { finding_version: finding.finding_version, decision, reason: 'E2E synthetic evidence reviewed' }
    const first = await json<{ decision_id: string }>(request, 'post', `/reviews/${finding.finding_id}`, token, payload, key)
    if (duplicate) {
      const second = await json<{ decision_id: string }>(request, 'post', `/reviews/${finding.finding_id}`, token, payload, key)
      expect(second.decision_id).toBe(first.decision_id)
    }
    ids.push(first.decision_id)
  }
  return ids
}

async function reviewerPage(browser: Browser, item: Detail): Promise<void> {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  try {
    const page = await context.newPage()
    await loginUi(page, 'reviewer')
    await page.goto('/reviews')
    await expect(page.getByText(item.ai_findings[0].title)).toBeVisible()
  } finally {
    await context.close()
  }
}

test.beforeEach(async ({ request }) => {
  if (!/^tracerisk_e2e_[0-9a-f]{32}$/.test(database)) throw new Error('An owned E2E database is required')
  const base = new URL(required('E2E_BASE_URL'))
  if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname)) {
    throw new Error('E2E Web URL must be local')
  }
  const record = required('E2E_OWNERSHIP_RECORD')
  const proof = required('E2E_WORKER_PROOF')
  const python = required('E2E_PYTHON')
  let runId: string
  try {
    const owned = JSON.parse(readFileSync(record, 'utf8')) as { name: string; purpose: string; run_id: string }
    if (owned.name !== database || owned.purpose !== 'e2e' || !/^[0-9a-f]{32}$/.test(owned.run_id)) throw new Error()
    runId = owned.run_id
    execFileSync(python, [path.join(root, 'scripts/e2e_runtime.py'), 'check', '--record', record, '--database', database, '--phase', phase, '--proof', proof], {
      cwd: root,
      env: { ...process.env, POSTGRES_DB: database, PYTHONPATH: [path.join(root, 'apps/api/src'), path.join(root, 'scripts'), root].join(path.delimiter) },
      stdio: ['ignore', 'ignore', 'ignore'],
    })
  } catch {
    throw new Error('Owned E2E runtime and live worker preflight failed')
  }
  // The unique account exists only in the recorded DB. A successful public
  // login binds the Web/API behind this URL to that exact disposable target.
  await safeRequest(request, 'post', '/api/auth/login', { data: { username: `e2e_probe_${runId}`, password: required('E2E_ANALYST_PASSWORD') } }, 'probe-login')
})

test('path 1: low risk completes without review', async ({ page, request }) => {
  test.skip(phase !== 'normal', 'Normal worker phase only')
  const analyst = await loginApi(request, 'analyst')
  const started = await createRun(request, analyst, 'clean-operations')
  const done = await waitStatus(request, analyst, started.id, 'completed')
  await loginUi(page, 'analyst')
  await pageState(page, done, 'completed')
  await page.getByRole('link', { name: '查看不可变风险报告' }).click()
  await expect(page.locator('.page-heading h1')).toContainText(done.company_name)
  await expect(page.locator('section.dossier-section').first()).toContainText('最终结果 · 规则评分 0')
  await expect(page.locator('section.dossier-section').first()).toContainText('无发现记录。')
  const saved = evidence(done.current_run.id)
  expect(saved.rule_score).toBe(0)
  expect(saved.decisions).toHaveLength(0)
  expect(saved.report_count).toBe(1)
  expect(saved.events.filter(event => event.type === 'run.completed')).toHaveLength(1)
})

test('path 2: high risk waits for review and resumes once', async ({ page, request, browser }) => {
  test.skip(phase !== 'normal', 'Normal worker phase only')
  const analyst = await loginApi(request, 'analyst')
  const reviewer = await loginApi(request, 'reviewer')
  const started = await createRun(request, analyst, 'all-rules-95')
  const waiting = await waitStatus(request, analyst, started.id, 'awaiting_review')
  expect(waiting.ai_findings.length).toBeGreaterThan(0)
  await loginUi(page, 'analyst')
  await pageState(page, waiting, 'awaiting_review')
  await expect(page.locator('.timeline-list')).toContainText('等待人工复核')
  await reviewerPage(browser, waiting)
  await reviewFindings(request, reviewer, waiting, 'approve')
  const done = await waitStatus(request, analyst, started.id, 'completed')
  await pageState(page, done, 'completed')
  await expect(page.locator('.timeline-list')).toContainText('复核完成，等待恢复执行')
  await expect(page.locator('.timeline-list')).toContainText('调查完成，风险报告已生成')
  const saved = evidence(done.current_run.id)
  expect(saved.rule_score).toBe(95)
  expect(saved.decisions).toHaveLength(waiting.ai_findings.length)
  expect(saved.outbox.resume).toBe(1)
  expect(saved.report_count).toBe(1)
  expect(saved.events.filter(event => event.type === 'run.resume_queued')).toHaveLength(1)
  expect(saved.events.filter(event => event.type === 'run.completed')).toHaveLength(1)
})

test('path 3: modified finding uses human replacement', async ({ page, request }) => {
  test.skip(phase !== 'normal', 'Normal worker phase only')
  const analyst = await loginApi(request, 'analyst')
  const started = await createRun(request, analyst, 'all-rules-95')
  const waiting = await waitStatus(request, analyst, started.id, 'awaiting_review')
  expect(waiting.ai_findings.length).toBeGreaterThan(0)
  const original = waiting.ai_findings[0]
  const edited = { title: `人工复核 · ${original.title}`, summary: '人工复核确认原引用，对风险范围补充限定。', reasoning: '依据保留的合成证据进行人工复核，采用此独立人工版本。' }
  const before = evidence(waiting.current_run.id)
  expect(before.human_versions).toHaveLength(0)
  await loginUi(page, 'reviewer')
  await page.goto(`/reviews/${original.finding_id}`)
  await expect(page.getByRole('heading', { name: '原始 AI 发现', exact: true })).toBeVisible()
  await expect(page.locator('.finding').first()).toContainText(original.title)
  const approveOption = page.locator('.form-panel').getByText('通过（approve）', { exact: true })
  const modifyOption = page.locator('.form-panel').getByText('修改（modify）', { exact: true })
  const rejectOption = page.locator('.form-panel').getByText('驳回（reject）', { exact: true })
  await expect(approveOption).toBeVisible()
  await expect(modifyOption).toBeVisible()
  await expect(rejectOption).toBeVisible()
  await modifyOption.click()
  await expect(page.getByRole('radio', { name: '修改（modify）', exact: true })).toBeChecked()
  await expect(page.locator('#replacement-title')).toHaveValue(original.title)
  await expect(page.locator('#replacement-summary')).toHaveValue(original.summary)
  await expect(page.locator('#replacement-reasoning')).toHaveValue(original.reasoning)
  await expect(page.locator('.replacement-editor')).toContainText('原 AI 内容不会被覆盖')
  await page.locator('#review-reason').fill('人工修改并保留原始 AI 快照')
  await page.locator('#replacement-title').fill(' ')
  await expect(page.getByRole('button', { name: '提交复核' })).toBeDisabled()
  await page.locator('#replacement-title').fill(edited.title)
  await page.locator('#replacement-summary').fill(edited.summary)
  await page.locator('#replacement-reasoning').fill(edited.reasoning)
  await page.getByRole('button', { name: '提交复核' }).click()
  await expect(page).toHaveURL(new RegExp(`/investigations/${waiting.id}$`))
  await expect(page.locator('[aria-labelledby="human-decision-heading"]')).toContainText('修改')
  await expect(page.locator('[aria-labelledby="human-replacement-heading"]')).toContainText(edited.title)
  await expect(page.locator('[aria-labelledby="human-replacement-heading"]')).toContainText(edited.summary)
  await expect(page.locator('.provenance-wrapper')).toContainText(original.title)
  await expect(page.locator('.provenance-wrapper')).toContainText(original.summary)
  await expect(page.locator('.provenance-wrapper')).not.toContainText(edited.title)
  // Complete the remaining reviews through the same public page flow.
  for (const finding of waiting.ai_findings.slice(1)) {
    await page.goto(`/reviews/${finding.finding_id}`)
    await expect(approveOption).toBeVisible()
    await approveOption.click()
    await expect(page.getByRole('radio', { name: '通过（approve）', exact: true })).toBeChecked()
    await page.locator('#review-reason').fill('保留原 AI 发现，已核对合成引用')
    await page.getByRole('button', { name: '提交复核' }).click()
    await expect(page).toHaveURL(new RegExp(`/investigations/${waiting.id}$`))
  }
  const done = await waitStatus(request, analyst, started.id, 'completed')
  await pageState(page, done, 'completed')
  await expect(page.locator('[aria-labelledby="human-replacement-heading"]')).toContainText(edited.reasoning)
  await page.getByRole('link', { name: '查看不可变风险报告' }).click()
  const finalSection = page.locator('section.dossier-section').first()
  await expect(finalSection).toContainText(edited.title)
  await expect(finalSection).toContainText(edited.summary)
  await expect(finalSection).toContainText(edited.reasoning)
  await expect(finalSection).toContainText('来源：人工替换版本 1')
  await expect(page.locator('section.dossier-section').filter({ hasText: '原始 AI 发现（不可变快照）' })).toContainText(original.title)
  await expect(page.locator('section.dossier-section').filter({ hasText: '原始 AI 发现（不可变快照）' })).not.toContainText(edited.title)
  await expect(page.locator('[aria-labelledby="human-replacement-heading"]')).toContainText(edited.title)
  const saved = evidence(done.current_run.id)
  const snapshot = await report(request, analyst, done)
  expect(saved.ai_findings).toEqual(before.ai_findings)
  expect(saved.human_versions).toHaveLength(1)
  const human = saved.human_versions[0]
  expect(human.ai_finding_id).toBe(original.finding_id)
  expect(human.finding_version).toBe(original.finding_version)
  expect(human.version).toBe(1)
  expect(saved.decisions.filter(decision => decision.decision === 'modify')).toEqual([{ id: human.review_decision_id, ai_finding_id: original.finding_id, finding_version: original.finding_version, decision: 'modify' }])
  expect(saved.report_final_sources.filter(finding => finding.source === 'human')).toEqual([{ id: human.id, source: 'human', original_ai_finding_id: original.finding_id, human_version: 1 }])
  expect(saved.report_ai_ids).toContain(original.finding_id)
  expect(saved.report_final_ids).not.toContain(original.finding_id)
  expect(saved.report_final_ids).toContain(human.id)
  expect(snapshot.snapshot.ai_findings.find(finding => finding.id === original.finding_id)).toMatchObject({ title: original.title, summary: original.summary, reasoning: original.reasoning })
  expect(snapshot.snapshot.final_findings.find(finding => finding.id === human.id)).toMatchObject({ ...edited, source: 'human', original_ai_finding_id: original.finding_id, human_version: 1 })
  expect(saved.decisions).toHaveLength(waiting.ai_findings.length)
  expect(saved.rule_score).toBe(before.rule_score)
  expect(saved.report_count).toBe(1)
  expect(saved.outbox.resume).toBe(1)
  expect(saved.events.filter(event => event.type === 'run.completed')).toHaveLength(1)
})

test('path 4: rejected finding is absent from final findings', async ({ page, request }) => {
  test.skip(phase !== 'normal', 'Normal worker phase only')
  const analyst = await loginApi(request, 'analyst')
  const reviewer = await loginApi(request, 'reviewer')
  const started = await createRun(request, analyst, 'all-rules-95')
  const waiting = await waitStatus(request, analyst, started.id, 'awaiting_review')
  await reviewFindings(request, reviewer, waiting, 'reject')
  const done = await waitStatus(request, analyst, started.id, 'completed')
  await loginUi(page, 'analyst')
  await pageState(page, done, 'completed')
  await expect(page.locator('.provenance-wrapper')).toContainText(waiting.ai_findings[0].title)
  await expect(page.locator('[aria-labelledby="human-decision-heading"]')).toContainText('驳回')
  await page.getByRole('link', { name: '查看不可变风险报告' }).click()
  await expect(page.locator('.page-heading h1')).toContainText(done.company_name)
  await expect(page.locator('section.dossier-section').first()).toContainText('无发现记录。')
  await expect(page.locator('section.dossier-section').filter({ hasText: '原始 AI 发现（不可变快照）' })).toContainText(waiting.ai_findings[0].title)
  await expect(page.locator('section.dossier-section').filter({ hasText: '人工决定' })).toContainText('驳回')
  const saved = evidence(done.current_run.id)
  const snapshot = await report(request, analyst, done)
  expect(saved.ai_findings.length).toBeGreaterThan(0)
  expect(saved.decisions.every(decision => decision.decision === 'reject')).toBeTruthy()
  expect(snapshot.snapshot.ai_findings).toHaveLength(saved.ai_findings.length)
  expect(snapshot.snapshot.final_findings).toHaveLength(0)
  expect(saved.report_ai_ids).toHaveLength(saved.ai_findings.length)
  expect(saved.report_final_ids).toHaveLength(0)
})

test('path 5: source conflict forces review below threshold', async ({ page, request }) => {
  test.skip(phase !== 'normal', 'Normal worker phase only')
  const analyst = await loginApi(request, 'analyst')
  const started = await createRun(request, analyst, 'cross-source-conflict')
  const waiting = await waitStatus(request, analyst, started.id, 'awaiting_review')
  await loginUi(page, 'analyst')
  await pageState(page, waiting, 'awaiting_review')
  await expect(page.locator('.timeline-list')).toContainText('等待人工复核')
  const conflict = waiting.conflicts.find(item => item.fact_type === 'judicial_case_status' && item.fact_key === 'CONFLICT-CASE-001')
  expect(conflict).toBeDefined()
  expect(conflict?.sides.map(side => [side.source_type, side.fact_value]).sort()).toEqual([['business', 'closed'], ['judicial', 'open']])
  expect(conflict?.authority_source).toBe('judicial')
  expect(conflict?.resolved_value).toBe('open')
  const record = page.locator('.conflict-record').filter({ has: page.getByRole('heading', { name: /judicial_case_status \/ CONFLICT-CASE-001/ }) })
  await expect(record).toBeVisible()
  await expect(record.locator('.conflict-sides > div').filter({ hasText: 'business' })).toContainText('closed')
  await expect(record.locator('.conflict-sides > div').filter({ hasText: 'judicial' })).toContainText('open')
  await expect(record).toContainText('权威来源：judicial · 解析值：open · 已解决')
  expect(waiting.rule_score).toBeLessThan(60)
  expect(waiting.conflicts.some(conflict => conflict.resolution_state === 'resolved')).toBeTruthy()
  const saved = evidence(waiting.current_run.id)
  expect(saved.status).toBe('awaiting_review')
  expect(saved.report_count).toBe(0)
  expect(saved.events.filter(event => event.type === 'run.awaiting_review')).toHaveLength(1)
})

test('path 6: unavailable source with zero score requires run-level review', async ({ page, request, browser }) => {
  test.skip(phase !== 'normal', 'Normal worker phase only')
  const analyst = await loginApi(request, 'analyst')
  const started = await createRun(request, analyst, 'unavailable-source')
  const waiting = await waitStatus(request, analyst, started.id, 'awaiting_review')
  await loginUi(page, 'analyst')
  await pageState(page, waiting, 'awaiting_review')
  await expect(page.locator('.coverage-stamp[data-status="unavailable"]')).toBeVisible()
  await expect(page.locator('.evidence-sufficiency')).toContainText('insufficient')
  await expect(page.locator('.provenance-wrapper')).toContainText('本次运行暂无 AI 发现。')
  const reviewerContext = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  try {
    const reviewerPage = await reviewerContext.newPage()
    await loginUi(reviewerPage, 'reviewer')
    await reviewerPage.goto(`/investigations/${waiting.id}`)
    await expect(reviewerPage.getByRole('heading', { name: '运行级复核' })).toBeVisible()
  } finally {
    await reviewerContext.close()
  }
  expect(waiting.rule_score).toBe(0)
  expect(waiting.ai_findings).toHaveLength(0)
  expect(waiting.coverage.evidence_sufficiency).toBe('insufficient')
  const saved = evidence(waiting.current_run.id)
  expect(saved.rule_score).toBe(0)
  expect(saved.ai_findings).toHaveLength(0)
  expect(saved.report_count).toBe(0)
})

test('path 7: invalid citation fails durably without finding or report', async ({ page, request }) => {
  test.skip(phase !== 'invalid', 'Requires the E2E-only replacement worker')
  required('E2E_CITATION_MUTATION')
  const analyst = await loginApi(request, 'analyst')
  const started = await createRun(request, analyst, 'all-rules-95')
  const failed = await waitStatus(request, analyst, started.id, 'failed')
  await loginUi(page, 'analyst')
  await pageState(page, failed, 'failed')
  await expect(page.locator('.final-result')).toContainText('运行失败')
  const saved = evidence(failed.current_run.id)
  expect(saved.ai_findings).toHaveLength(0)
  expect(saved.report_count).toBe(0)
  expect(saved.events.filter(event => event.type === 'run.failed')).toHaveLength(1)
  expect(saved.events.some(event => event.key === 'node:apply_rules:completed')).toBeTruthy()
  expect(saved.events.some(event => event.key === 'node:generate_validate_findings:completed')).toBeFalsy()
})

test('path 8: duplicate review and resume redelivery preserve one report', async ({ page, request }) => {
  test.skip(phase !== 'normal', 'Normal worker phase only')
  const analyst = await loginApi(request, 'analyst')
  const reviewer = await loginApi(request, 'reviewer')
  const started = await createRun(request, analyst, 'all-rules-95')
  const waiting = await waitStatus(request, analyst, started.id, 'awaiting_review')
  await reviewFindings(request, reviewer, waiting, 'approve', true)
  const done = await waitStatus(request, analyst, started.id, 'completed')
  await expect.poll(() => evidence(done.current_run.id).resume_jobs, { timeout: 60_000 })
    .toEqual([{ id: expect.any(Number), status: 'succeeded' }])
  const before = evidence(done.current_run.id)
  expect(before.outbox.resume).toBe(1)
  const originalJobId = before.resume_jobs[0].id
  const python = required('E2E_PYTHON')
  const record = required('E2E_OWNERSHIP_RECORD')
  try {
    execFileSync(python, [path.join(root, 'scripts/replay_e2e.py'), '--record', record, '--run-id', done.current_run.id], {
      cwd: root,
      env: { ...process.env, POSTGRES_DB: database, PYTHONPATH: [path.join(root, 'apps/api/src'), root].join(path.delimiter) },
      stdio: ['ignore', 'ignore', 'ignore'],
    })
  } catch {
    throw new Error('Guarded E2E resume replay failed')
  }
  await expect.poll(() => evidence(done.current_run.id).outbox_dispatched.resume, { timeout: 60_000 }).toBe(1)
  await expect.poll(() => evidence(done.current_run.id).resume_jobs.find(job => job.id > originalJobId)?.status, { timeout: 60_000 })
    .toBe('succeeded')
  await loginUi(page, 'analyst')
  await pageState(page, done, 'completed')
  const after = evidence(done.current_run.id)
  expect(after.resume_jobs).toHaveLength(2)
  expect(after.resume_jobs.every(job => job.status === 'succeeded')).toBeTruthy()
  expect(after.decisions).toHaveLength(waiting.ai_findings.length)
  expect(after.outbox.resume).toBe(1)
  expect(after.report_count).toBe(1)
  expect(after.report_bytes_sha256).toBe(before.report_bytes_sha256)
  expect(after.events).toEqual(before.events)
  expect(after.events.filter(event => event.type === 'run.completed')).toHaveLength(1)
  expect(after.events.filter(event => event.type === 'run.resume_queued')).toHaveLength(1)
})
