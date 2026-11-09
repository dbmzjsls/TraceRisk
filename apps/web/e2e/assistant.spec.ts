import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'
import type { components } from '../src/api/generated'
import { safeRequest } from './safeRequest'

type Answer = components['schemas']['AssistantAnswerResponse']
type Detail = components['schemas']['InvestigationDetailResponse']

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const examples = [
  { id: '01', recommendation: '综合调查', company: '远景精工（合成）有限公司', code: 'SYNTHETIC-COMPANY-003', score: 95 },
  { id: '02', recommendation: '来源不足', company: '海晟服务（合成）有限公司', code: 'SYNTHETIC-COMPANY-007', score: 0 },
  { id: '03', recommendation: '司法追问', company: '远景精工（合成）有限公司', code: 'SYNTHETIC-COMPANY-003', score: 95 },
] as const
const viewport = { width: 1600, height: 1000 }
const capture = process.env.E2E_CAPTURE_DIR
const mode = process.env.E2E_ASSISTANT_MODE ?? 'mock'

function required(key: string) {
  const value = process.env[key]
  if (!value) throw new Error(key + ' is required')
  return value
}

function writeJson(file: string, value: unknown) {
  writeFileSync(file, JSON.stringify(value, null, 2) + '\n', 'utf8')
}

async function login(page: Page) {
  await page.goto('/assistant')
  await expect(page).toHaveURL(/\/login\?redirect=/)
  await page.locator('#username').fill('analyst')
  await page.locator('#password').fill(required('E2E_ANALYST_PASSWORD'))
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL(/\/assistant$/)
  await expect(page.getByRole('heading', { name: '调查助手', exact: true })).toBeVisible()
}

test.beforeEach(async ({ request }) => {
  const database = required('E2E_DB_NAME')
  const base = new URL(required('E2E_BASE_URL'))
  const record = JSON.parse(readFileSync(required('E2E_OWNERSHIP_RECORD'), 'utf8')) as { name: string; purpose: string; run_id: string }
  if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname) ||
    record.purpose !== 'e2e' || !/^[0-9a-f]{32}$/.test(record.run_id) ||
    database !== record.name || database !== 'tracerisk_e2e_' + record.run_id) {
    throw new Error('Assistant E2E requires a locally owned disposable target')
  }
  await safeRequest(request, 'post', '/api/auth/login', {
    data: { username: 'e2e_probe_' + record.run_id, password: required('E2E_ANALYST_PASSWORD') },
  }, 'probe-login')
})

test('sends three real browser questions and reuses the first Run', async ({ page, browser, request }) => {
  test.setTimeout(250_000)
  await login(page)
  const auth = await safeRequest<{ access_token: string }>(request, 'post', '/api/auth/login', {
    data: { username: 'analyst', password: required('E2E_ANALYST_PASSWORD') },
  }, 'login')
  const headers = { Authorization: 'Bearer ' + auth.access_token }
  let capturePage = page
  let recording: Awaited<ReturnType<typeof browser.newContext>> | undefined
  if (capture) {
    if (!isAbsolute(capture)) throw new Error('Capture directory must be absolute')
    mkdirSync(capture)
    const session = await page.evaluate(() => sessionStorage.getItem('tracerisk.session'))
    if (!session) throw new Error('Browser session unavailable')
    const baseURL = required('E2E_BASE_URL')
    recording = await browser.newContext({ baseURL, viewport, recordVideo: { dir: capture, size: viewport } })
    await recording.addInitScript(({ origin, sessionValue }) => {
      if (location.origin === origin) sessionStorage.setItem('tracerisk.session', sessionValue)
    }, { origin: new URL(baseURL).origin, sessionValue: session })
    capturePage = await recording.newPage()
    await capturePage.goto('/assistant')
  } else {
    await capturePage.setViewportSize(viewport)
  }

  const answers: Answer[] = []
  let creations = 0
  capturePage.on('request', req => {
    if (req.method() === 'POST' && new URL(req.url()).pathname === '/api/investigations') creations++
  })
  const company = capturePage.getByLabel('企业', { exact: true })
  const date = capturePage.getByLabel('基准日', { exact: true })
  const input = capturePage.getByLabel('输入问题')
  const send = capturePage.getByRole('button', { name: '发送', exact: true })
  const turn = capturePage.getByRole('article', { name: '当前问答' })
  let casePath: string | undefined

  try {
    for (const example of examples) {
      casePath = capture ? join(capture, example.id) : undefined
      if (casePath) mkdirSync(casePath)
      if (example.id === '03') {
        await capturePage.getByRole('button', { name: new RegExp(example.company.replace(/[（）]/g, '.')) }).click()
      } else {
        if (example.id === '02') await capturePage.getByRole('button', { name: '新对话' }).click()
        await company.selectOption({ label: example.company })
        await date.fill('2026-09-11')
      }
      const question = readFileSync(join(root, 'docs/competition/conversations', example.id, 'prompt.txt'), 'utf8').trimEnd()
      await capturePage.getByRole('button', { name: example.recommendation, exact: true }).click()
      await expect(input).toHaveValue(question)
      if (capture) await capturePage.waitForTimeout(2300)

      const replied = capturePage.waitForResponse(res =>
        res.request().method() === 'POST' && new URL(res.url()).pathname === '/api/assistant/answers',
      { timeout: 95_000 })
      await send.click()
      const response = await Promise.race([
        replied,
        turn.getByRole('alert').waitFor({ state: 'visible', timeout: 95_000 }).then(async () => {
          throw new Error('The application displayed an error: ' + await turn.getByRole('alert').innerText())
        }),
      ])
      const payload = response.request().postDataJSON() as components['schemas']['AssistantAnswerRequest']
      const result = await response.json()
      if (casePath) {
        writeJson(join(casePath, 'request.json'), payload)
        writeJson(join(casePath, 'response.json'), result)
      }
      expect(response.status()).toBe(200)
      const answer = result as Answer
      answers.push(answer)
      expect(payload).toEqual({ question, investigation_id: answer.investigation_id, run_id: answer.run_id })
      const detail = await safeRequest<Detail>(request, 'get', '/api/investigations/' + answer.investigation_id, { headers }, 'api-read')
      if (casePath) writeJson(join(casePath, 'context.json'), { detail, report: null, company_credit_code: example.code })
      expect(answer.status).toBe('answer')
      expect(answer.investigation_model_execution.mode).toBe(mode)
      expect(answer.assistant_execution.mode).toBe(mode)
      expect(answer.rule_score).toBe(example.score)
      expect(answer.run_status).toBe('awaiting_review')
      await expect(turn).toContainText('待人工复核')
      await expect(turn).toContainText('规则分 ' + example.score)
      await expect(turn).toContainText(question)
      if (example.id === '02') {
        await expect(turn).toContainText('工商来源不可用')
        await expect(turn).toContainText('规则分0分不代表安全')
        await expect(turn).toContainText('本次运行没有待审AI发现')
      } else {
        if (example.id === '01') {
          const domains = new Set(answer.citations.map(item => item.risk_type))
          expect(domains).toEqual(new Set(['business', 'judicial', 'news', 'intellectual_property']))
        } else {
          expect(answer.run_id).toBe(answers[0].run_id)
          expect(answer.investigation_id).toBe(answers[0].investigation_id)
          expect(answer.citations.map(item => item.risk_type)).toContain('judicial')
          expect(answer.citations.every(item => item.risk_type === 'judicial')).toBe(true)
          await expect(turn.locator('[data-section="rules"]')).toContainText('计35分')
        }
        const chunk = answer.citations[0].chunk_id
        const citation = turn.locator('[data-citation-chunk="' + chunk + '"]')
        await citation.click()
        const evidence = capturePage.locator('#evidence-' + chunk)
        await expect(evidence).toBeFocused()
        await expect(evidence).toContainText(answer.citations[0].content)
        if (capture) await capturePage.waitForTimeout(1800)
        await evidence.getByRole('button', { name: '返回引用位置' }).click()
        await expect(citation).toBeFocused()
      }
      if (casePath) {
        if (example.id === '03') await turn.getByText('模型来源与复核原因', { exact: true }).click()
        await capturePage.locator('.workspace-main').evaluate(element => { element.scrollTop = 0 })
        await capturePage.screenshot({ path: join(casePath, 'screenshot.png') })
        await capturePage.waitForTimeout(1800)
      }
    }
    expect(creations).toBe(2)
    expect(answers[1].run_id).not.toBe(answers[0].run_id)
    if (mode === 'mock') {
      await capturePage.getByRole('button', { name: '来源不足', exact: true }).click()
      await send.click()
      await expect(capturePage.getByRole('alert')).toContainText('企业与当前选择不同')
      expect(creations).toBe(2)
      await capturePage.setViewportSize({ width: 390, height: 844 })
      await expect.poll(() => capturePage.evaluate(() => {
        const chat = document.querySelector('.assistant-conversation')?.getBoundingClientRect()
        const evidence = document.querySelector('.assistant-evidence')?.getBoundingClientRect()
        return !!chat && !!evidence && evidence.top >= chat.bottom
      })).toBe(true)
    }
  } catch (error) {
    if (casePath) await capturePage.screenshot({ path: join(casePath, 'failed.png') }).catch(() => undefined)
    throw error
  } finally {
    if (recording && capture) {
      const video = capturePage.video()
      await recording.close()
      await video?.saveAs(join(capture, 'operations.webm'))
      await video?.delete()
    }
  }
})

test('opens a completed investigation after refresh and answers on its saved Run', async ({ page, request }) => {
  test.skip(mode !== 'mock' || !!capture, 'Isolated Mock regression only')
  await login(page)
  const auth = await safeRequest<{ access_token: string }>(request, 'post', '/api/auth/login', {
    data: { username: 'analyst', password: required('E2E_ANALYST_PASSWORD') },
  }, 'login')
  const headers = { Authorization: 'Bearer ' + auth.access_token }
  const { items } = await safeRequest<{ items: components['schemas']['CompanyResponse'][] }>(request, 'get', '/api/companies', { headers }, 'api-read')
  const company = items.find(item => item.unified_credit_code === 'SYNTHETIC-COMPANY-002')
  expect(company).toBeDefined()
  const created = await safeRequest<components['schemas']['InvestigationResponse']>(request, 'post', '/api/investigations', {
    headers: { ...headers, 'Idempotency-Key': randomUUID() },
    data: { company_id: company?.id, as_of_date: '2026-09-11' },
  }, 'api-write')
  await page.goto('/investigations/' + created.id)
  await page.getByRole('link', { name: '在调查助手中提问' }).click()
  await expect(page).toHaveURL(/\/assistant\?investigation=/)
  await page.reload()
  await expect(page).toHaveURL(/\/assistant\?investigation=/)
  await expect(page.getByLabel('企业', { exact: true })).toHaveValue(company!.id)
  await page.getByLabel('输入问题').fill('请说明这次调查的最终结论。')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  const turn = page.getByRole('article', { name: '当前问答' })
  await expect(turn).toContainText('已完成')
  await expect(turn.getByRole('link', { name: '查看已保存报告' })).toBeVisible()
})
