import assert from 'node:assert/strict'
import { createSSRApp, effectScope, nextTick, shallowRef } from 'vue'
import { timeoutManager, VUE_QUERY_CLIENT } from '@tanstack/vue-query'
import { apiRequest, ApiError, configureAuth, queryClient } from '../src/api/client'
import { createRequestIdentity } from '../src/api/requestIdentity'
import { companiesSchema, companySchema, citationSchema, investigationDetailSchema, reportSchema, type InvestigationDetail, type PendingFinding, type User } from '../src/api/schemas'
import { modernReportSnapshotSchema, parseReportSnapshot } from '../src/api/reportSnapshots'
import { investigationKeys, listPollInterval, pollInterval, useCreateInvestigation, useInvestigation } from '../src/features/investigations/useInvestigations'
import { canReviewFinding, canReviewRun, pendingKey, usePendingReviews, useReviewSubmission } from '../src/features/reviews/useReviews'
import { replacementDraftSchema, replacementFromFinding } from '../src/features/reviews/replacementDraft'

const ids = Array.from({ length: 8 }, (_, index) => `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`)
// Actual backend seed IDs intentionally have no RFC UUID version/variant bits.
ids[1] = '20000000-0000-0000-0000-000000000001'
ids[5] = '21000000-0000-0000-0000-000000000001'
const finding: PendingFinding = { finding_id: ids[3], finding_key: 'finding:1', finding_version: 1, run_id: ids[2], investigation_id: ids[0], company_id: ids[1], company_name: '合成企业', risk_type: 'judicial', claim_code: 'test', title: '<script>unsafe</script>', summary: 'original', reasoning: 'reasoning', citations: [{ evidence_id: ids[5], chunk_id: ids[6], content_hash: 'a'.repeat(64), quoted_excerpt: '<b>original quotation</b>' }], evidence_sufficiency: 'sufficient', confidence_level: 'high', review_recommended: true, support_status: 'supported', support_reason: 'canonical_fact_match' }
const detail: InvestigationDetail = { id: ids[0], company_id: ids[1], company_name: '合成企业', created_by: ids[4], as_of_date: '2026-09-11', current_run: { id: ids[2], run_number: 1, status: 'awaiting_review', report_id: null }, retrieval_state: 'pending', steps: [], rule_score: 35, rule_findings: [], retrieved_evidence: [], ai_findings: [finding], decisions: [], coverage: { entries: ['business', 'judicial', 'news', 'intellectual_property'].map(source_type => ({ source_type: source_type as 'business' | 'judicial' | 'news' | 'intellectual_property', status: 'missing' as const, coverage_id: null, checked_at: null })), insufficiency_details: ['business:missing'], evidence_sufficiency: 'unavailable' }, conflicts: [] }
const reviewer: User = { id: ids[7], username: 'reviewer', role: 'reviewer' }
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); await nextTick() }

export async function runBusinessBoundaryTests() {
  const originalFetch = globalThis.fetch
  const intervals = new Map<number, { callback: () => void; delay: number }>()
  let timerId = 0
  timeoutManager.setTimeoutProvider({ setTimeout: (callback, delay) => setTimeout(callback, delay).unref(), clearTimeout, setInterval: (callback, delay) => { const id = ++timerId; intervals.set(id, { callback, delay }); return id }, clearInterval: id => { intervals.delete(Number(id)) } })
  configureAuth(() => 'boundary-test', () => queryClient.clear())
  const app = createSSRApp({})
  app.provide(VUE_QUERY_CLIENT, queryClient)
  const scopes: ReturnType<typeof effectScope>[] = []
  function setup<T>(factory: () => T) {
    const scope = effectScope()
    scopes.push(scope)
    return { scope, result: app.runWithContext(() => scope.run(factory)!) }
  }
  try {
    const company = { id: ids[1], name: '华辰智造（合成演示）有限公司', unified_credit_code: 'SYNTHETIC-COMPANY-001', is_synthetic: true }
    globalThis.fetch = async () => response({ notice: '合成数据，仅供演示', items: [company] })
    assert.deepEqual((await apiRequest('/companies', companiesSchema)).items, [company])
    for (const evidenceId of [ids[5], '21000000-0000-0000-0000-000000000002', '21000000-0000-0000-0000-000000000003']) {
      assert.equal(citationSchema.parse({ evidence_id: evidenceId, chunk_id: ids[6], content_hash: 'seed-hash', quoted_excerpt: 'seed evidence' }).evidence_id, evidenceId)
    }
    for (const invalidId of ['not-an-id', '20000000000000000000000000000001', '20000000-0000-0000-0000-00000000000g', `${ids[1]} trailing`]) {
      assert.equal(companySchema.safeParse({ ...company, id: invalidId }).success, false)
      assert.equal(citationSchema.safeParse({ evidence_id: invalidId, chunk_id: ids[6], content_hash: 'hash', quoted_excerpt: 'invalid' }).success, false)
    }
    for (const status of ['queued', 'running', 'resume_queued'] as const) assert.equal(pollInterval(status), 2000)
    assert.equal(pollInterval('awaiting_review'), 5000)
    for (const status of ['completed', 'failed', undefined] as const) assert.equal(pollInterval(status), false)
    assert.equal(listPollInterval([]), false)
    assert.equal(listPollInterval([detail, { ...detail, current_run: { ...detail.current_run, status: 'running' } }]), 2000)
    assert.equal(listPollInterval([detail]), 5000)

    let key = 0
    const identity = createRequestIdentity(() => `key-${++key}`)
    const body = { reason: 'original', finding_version: 1 }
    const captured = identity.capture('/reviews/one', body)
    body.reason = 'edited'
    assert.equal(captured.body.reason, 'original')
    assert.equal(identity.capture('/reviews/one', { reason: 'original', finding_version: 1 }).key, captured.key)
    assert.notEqual(identity.capture('/reviews/one', body).key, captured.key)
    const editedKey = identity.capture('/reviews/one', body).key
    assert.notEqual(identity.capture('/reviews/two', body).key, editedKey)
    identity.clear()
    assert.notEqual(identity.capture('/reviews/one', body).key, editedKey)

    assert.equal(canReviewFinding(detail, finding, reviewer), true)
    assert.equal(canReviewFinding(detail, finding, { ...reviewer, role: 'admin' }), true)
    for (const user of [null, { ...reviewer, role: 'analyst' as const }, { ...reviewer, id: detail.created_by }]) assert.equal(canReviewFinding(detail, finding, user), false)
    for (const status of ['queued', 'running', 'resume_queued', 'completed', 'failed'] as const) assert.equal(canReviewRun({ ...detail, current_run: { ...detail.current_run, status } }, reviewer), false)
    assert.equal(canReviewFinding(detail, { ...finding, run_id: ids[6] }, reviewer), false)
    assert.equal(canReviewFinding(detail, { ...finding, finding_version: 2 }, reviewer), false)
    assert.equal(canReviewFinding({ ...detail, decisions: [{ id: ids[6], ai_finding_id: finding.finding_id, finding_version: 1, reviewer_id: reviewer.id, decision: 'approve', reason: 'done' }] }, finding, reviewer), false)

    const requests: { path: string; body: Record<string, unknown>; key: string | null }[] = []
    let outcome: 'network' | 'success' | 'conflict' = 'network'
    let release: (() => void) | undefined
    globalThis.fetch = async (input, options) => {
      requests.push({ path: String(input), body: JSON.parse(String(options?.body)), key: new Headers(options?.headers).get('Idempotency-Key') })
      await new Promise<void>(resolve => { release = resolve })
      if (outcome === 'network') throw new TypeError('transport lost')
      if (outcome === 'conflict') return response({ detail: 'version changed' }, 409)
      return response(String(input).endsWith('/investigations') ? detail : { decision_id: ids[6] })
    }
    const creation = setup(useCreateInvestigation).result
    const creationBody = { company_id: ids[1], as_of_date: '2026-09-11' }
    let first = creation.submit(creationBody)
    const duplicate = creation.submit(creationBody)
    creationBody.as_of_date = '2026-09-12'
    await flush()
    assert.equal(requests.length, 1, 'same-tick duplicate must not issue another POST')
    assert.equal(await duplicate, undefined)
    assert.equal(requests[0].body.as_of_date, '2026-09-11', 'in-flight body is captured')
    let rejected = assert.rejects(first, (error: unknown) => error instanceof ApiError && error.kind === 'network')
    release!(); await rejected
    first = creation.submit({ ...creationBody, as_of_date: '2026-09-11' }); await flush()
    assert.equal(requests[1].key, requests[0].key)
    rejected = assert.rejects(first); release!(); await rejected
    first = creation.submit(creationBody); await flush()
    assert.notEqual(requests[2].key, requests[1].key)
    outcome = 'success'; release!(); assert.equal((await first)?.id, detail.id)
    first = creation.submit(creationBody); await flush()
    assert.notEqual(requests[3].key, requests[2].key, 'successful submission clears identity')
    release!(); await first

    queryClient.setQueryData(pendingKey, [finding])
    queryClient.setQueryData(investigationKeys.detail(detail.id), detail)
    queryClient.setQueryData(investigationKeys.list, { items: [detail] })
    const review = setup(useReviewSubmission).result
    const reviewBody = { decision: 'approve' as const, reason: 'original review', finding_version: 1 }
    outcome = 'network'
    let reviewing = review.submit(`/reviews/${finding.finding_id}`, reviewBody)
    const reviewDuplicate = review.submit(`/reviews/${finding.finding_id}`, reviewBody)
    reviewBody.finding_version = 2
    await flush(); assert.equal(await reviewDuplicate, undefined)
    assert.equal(requests[4].body.finding_version, 1)
    rejected = assert.rejects(reviewing); release!(); await rejected
    reviewing = review.submit(`/reviews/${finding.finding_id}`, { ...reviewBody, finding_version: 1 }); await flush()
    assert.equal(requests[5].key, requests[4].key)
    outcome = 'conflict'
    rejected = assert.rejects(reviewing, (error: unknown) => error instanceof ApiError && error.status === 409)
    release!(); await rejected
    for (const queryKey of [pendingKey, investigationKeys.detail(detail.id), investigationKeys.list]) assert.equal(queryClient.getQueryState(queryKey)?.isInvalidated, true)
    queryClient.setQueryData(pendingKey, [finding])
    queryClient.setQueryData(investigationKeys.detail(detail.id), detail)
    outcome = 'success'
    reviewing = review.submit(`/reviews/${finding.finding_id}`, reviewBody); await flush()
    assert.notEqual(requests[6].key, requests[5].key, 'new finding version gets a new key')
    release!(); await reviewing
    assert.equal(queryClient.getQueryState(pendingKey)?.isInvalidated, true)
    assert.equal(queryClient.getQueryState(investigationKeys.detail(detail.id))?.isInvalidated, true)
    reviewing = review.submit(`/runs/${detail.current_run.id}/review`, { decision: 'reject', reason: 'zero findings' }); await flush()
    assert.equal(requests[7].path, `/api/runs/${detail.current_run.id}/review`)
    assert.equal('finding_version' in requests[7].body, false)
    release!(); await reviewing
    queryClient.clear()

    // Conflicts refetch mounted context; this also exercises the real GET paths.
    const contextReads = { pending: 0, detail: 0 }
    globalThis.fetch = async (input, options) => {
      if (options?.method === 'POST') return response({ detail: 'version changed' }, 409)
      if (String(input).endsWith('/reviews/pending')) { contextReads.pending++; return response([{ ...finding, finding_version: 2 }]) }
      contextReads.detail++
      return response({ ...detail, ai_findings: [{ ...finding, finding_version: 2 }] })
    }
    const pendingContext = setup(usePendingReviews)
    const detailContext = setup(() => useInvestigation(detail.id))
    await flush()
    const readsBeforeConflict = { ...contextReads }
    await assert.rejects(review.submit(`/reviews/${finding.finding_id}`, { decision: 'approve', reason: 'old version', finding_version: 1 }))
    assert.equal(contextReads.pending, readsBeforeConflict.pending + 1)
    assert.equal(contextReads.detail, readsBeforeConflict.detail + 1)
    assert.equal(pendingContext.result.data.value?.[0].finding_version, 2)
    pendingContext.scope.stop(); detailContext.scope.stop(); queryClient.clear()

    let status: InvestigationDetail['current_run']['status'] = 'queued'
    const readPaths: string[] = []
    let signal: AbortSignal | null | undefined
    let holdRead = false
    globalThis.fetch = async (input, options) => {
      readPaths.push(String(input)); signal = options?.signal
      if (holdRead) return new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }))
      return response({ ...detail, id: String(input).split('/').at(-1), current_run: { ...detail.current_run, status } })
    }
    const id = shallowRef(detail.id)
    const investigation = setup(() => useInvestigation(id))
    await flush()
    assert.deepEqual([...intervals.values()].map(timer => timer.delay), [2000])
    const readsBeforeTick = readPaths.length
    status = 'running'
    intervals.values().next().value!.callback(); await flush()
    assert.equal(readPaths.length, readsBeforeTick + 1, 'scheduled interval reads the API')
    assert.equal(investigation.result.data.value?.current_run.status, 'running')
    status = 'awaiting_review'
    await investigation.result.refetch(); await flush()
    assert.deepEqual([...intervals.values()].map(timer => timer.delay), [5000])
    status = 'resume_queued'
    await investigation.result.refetch(); await flush()
    assert.deepEqual([...intervals.values()].map(timer => timer.delay), [2000])
    for (status of ['completed', 'failed']) {
      await investigation.result.refetch(); await flush()
      assert.equal(intervals.size, 0, `${status} stops actual observer timers`)
    }
    id.value = ids[6]; await flush()
    assert.equal(investigation.result.data.value?.id, ids[6])
    assert.equal(readPaths.at(-1), `/api/investigations/${ids[6]}`)
    assert.ok(queryClient.getQueryData(investigationKeys.detail(detail.id)), 'route change keeps distinct cached records')
    status = 'running'
    await investigation.result.refetch(); await flush()
    assert.equal(intervals.size, 1)
    holdRead = true
    const refetching = investigation.result.refetch(); await flush()
    assert.equal(signal?.aborted, false)
    investigation.scope.stop(); await refetching
    assert.equal(signal?.aborted, true, 'unmount aborts fetch')
    assert.equal(intervals.size, 0)
    assert.equal(queryClient.getQueryCache().find({ queryKey: investigationKeys.detail(ids[6]) })?.getObserversCount(), 0)

    assert.equal(investigationDetailSchema.safeParse({ ...detail, current_run: { ...detail.current_run, status: 'invented' } }).success, false)
    const legacyRuleTarget = { ...detail, rule_findings: [{ id: ids[7], rule_code: 'LEGACY_RULE', rule_version: '1', score: 5, parameters: {}, evidence: [{ evidence_id: ids[5], content_hash: 'legacy-hash', chunk_id: null, chunk_content_hash: null }] }] }
    assert.equal(investigationDetailSchema.safeParse(legacyRuleTarget).success, true, 'legacy rule references retain an explicit unavailable exact target')
    const report = { id: ids[6], run_id: ids[2], html_content: '<script>unsafe()</script>', snapshot: { notice: '合成数据，仅供演示', run_id: ids[2], company: { id: ids[1], name: '合成企业' }, rule_score: 35, rules: [], ai_findings: [], human_decisions: [], final_findings: [], evidence_references: [], retrieved_evidence: [], coverage: { scope: ['judicial'], conflict: false, evidence_sufficiency: 'sufficient' }, review_reasons: [], evidence_sufficiency: 'sufficient' } }
    assert.equal(reportSchema.parse(report).snapshot.rule_score, 35)
    assert.equal(parseReportSnapshot(report.snapshot).availability, 'legacy_unavailable')
    assert.equal(reportSchema.parse({ ...report, snapshot: { ...report.snapshot, evidence_references: [{ rule_finding_id: ids[0], evidence_id: ids[5], content_hash: 'seed-hash', title: 'seed evidence', content: 'seed evidence content' }], retrieved_evidence: [{ chunk_id: ids[6], evidence_id: ids[5], content_hash: 'seed-hash', content: 'seed evidence content' }] } }).snapshot.evidence_references[0].evidence_id, ids[5])
    assert.equal(reportSchema.safeParse({ ...report, snapshot: { ...report.snapshot, human_decisions: undefined } }).success, false)
    const modern = { ...report.snapshot, snapshot_version: 'stage5-v1', coverage: { scope: ['business', 'judicial', 'news', 'intellectual_property'], entries: detail.coverage.entries, insufficiency_details: [], evidence_sufficiency: 'sufficient' }, conflicts: [] }
    assert.equal(modernReportSnapshotSchema.safeParse(modern).success, true)
    assert.throws(() => parseReportSnapshot({ ...modern, coverage: { ...modern.coverage, entries: undefined } }))
    const modernRetrieved = { risk_type: 'judicial', observed_at: '2026-09-11', fact_type: 'judicial_case_status', fact_key: 'case-1', fact_value: 'open', polarity: 'negative', evidence_content_hash: 'a'.repeat(64), source_type: 'judicial', rank: 1, chunk_id: ids[6], evidence_id: ids[5], content_hash: 'b'.repeat(64), content: 'evidence' }
    assert.equal(modernReportSnapshotSchema.safeParse({ ...modern, retrieved_evidence: [modernRetrieved] }).success, true)
    for (const [field, value] of Object.entries({ risk_type: 42, rank: 'bad', observed_at: false, evidence_content_hash: null, source_type: [], fact_type: {}, fact_key: 1, fact_value: 2, polarity: 3 })) {
      assert.equal(modernReportSnapshotSchema.safeParse({ ...modern, retrieved_evidence: [{ ...modernRetrieved, [field]: value }] }).success, false, `modern retrieved_evidence.${field} must reject corrupt values`)
    }
    const stage4Finding = { id: ids[3], finding_key: 'case', version: 1, risk_type: 'judicial', claim_code: 'case', title: 'Case', summary: 'Summary', reasoning: 'Reasoning', citations: [], evidence_sufficiency: 'sufficient', confidence_level: 'high', review_recommended: false, support_status: 'supported', support_reason: 'canonical_fact_match' }
    const stage4 = parseReportSnapshot({ ...report.snapshot, ai_findings: [stage4Finding], final_findings: [stage4Finding] })
    assert.equal(stage4.ai_findings[0].support_status, 'supported')

    const originalFinding = structuredClone(finding)
    const draft = replacementDraftSchema.parse(replacementFromFinding(finding, { title: '人工标题', summary: '人工摘要', reasoning: '人工推理' }))
    assert.deepEqual(finding, originalFinding, 'editing a replacement never mutates AI content')
    assert.equal(draft.finding_key, finding.finding_key)
    assert.deepEqual(draft.evidence_citations, finding.citations)
    for (const field of ['title', 'summary', 'reasoning']) assert.equal(replacementDraftSchema.safeParse({ ...draft, [field]: ' ' }).success, false, `${field} is mandatory`)
    for (const field of ['score', 'support_status', 'citations']) assert.equal(replacementDraftSchema.safeParse({ ...draft, [field]: 1 }).success, false, `${field} is not a draft field`)
    assert.equal(replacementDraftSchema.safeParse({ ...draft, evidence_citations: [] }).success, false)
    assert.equal(replacementDraftSchema.safeParse({ ...draft, title: 'x'.repeat(513) }).success, false)
    const replacement = { ...draft, citations: draft.evidence_citations, support_status: 'indeterminate', support_reason: 'unverified_free_text_extension' }
    delete (replacement as Partial<typeof replacement>).evidence_citations
    const modifiedDecision = { id: ids[7], ai_finding_id: finding.finding_id, finding_version: 1, reviewer_id: reviewer.id, decision: 'modify', reason: '人工修订', human_finding_version_id: ids[6], human_version: 1, replacement }
    const finalHuman = { ...replacement, id: ids[6], version: 1, source: 'human', original_ai_finding_id: finding.finding_id, human_version: 1 }
    const stage6 = { ...modern, snapshot_version: 'stage6-v1', ai_findings: [stage4Finding], human_decisions: [modifiedDecision], final_findings: [finalHuman] }
    const parsedStage6 = parseReportSnapshot(stage6)
    assert.equal(parsedStage6.snapshot_version, 'stage6-v1')
    assert.equal(parsedStage6.final_findings[0].source, 'human')
    assert.equal(parsedStage6.final_findings[0].title, '人工标题')
    assert.equal(parsedStage6.ai_findings[0].title, stage4Finding.title)
    for (const field of ['replacement', 'human_finding_version_id', 'human_version']) assert.throws(() => parseReportSnapshot({ ...stage6, human_decisions: [{ ...modifiedDecision, [field]: null }] }), `${field} must identify the modification`)
    assert.throws(() => parseReportSnapshot({ ...stage6, final_findings: [{ ...finalHuman, source: 'human', human_version: null }] }))
    assert.throws(() => parseReportSnapshot({ ...stage6, final_findings: [{ ...finalHuman, source: undefined }] }))

  } finally {
    for (const scope of scopes) scope.stop()
    queryClient.clear()
    globalThis.fetch = originalFetch
  }
  console.log('Business boundary checks passed: captured retry keys, duplicate writes, review conflicts/cache, role/version guards, polling timers, route keys, abort/unmount and response schemas.')
}
