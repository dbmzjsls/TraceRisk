<script setup lang="ts">
import { computed, onMounted, onUnmounted, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import type { components } from '../../api/generated'
import { citationSchema, type InvestigationDetail } from '../../api/schemas'
import QueryFeedback from '../../ui/QueryFeedback.vue'
import { useAuthStore } from '../auth/store'
import EvidenceList from '../investigations/EvidenceList.vue'
import RunEventTimeline from '../investigations/RunEventTimeline.vue'
import { statusLabel } from '../investigations/status'
import { useEvidenceNavigation } from '../investigations/useEvidenceNavigation'
import { useInvestigationEvents } from '../investigations/useInvestigationEvents'
import FindingList from '../investigations/FindingList.vue'
import { useInvestigation } from '../investigations/useInvestigations'
import { canReviewFinding, usePendingReviews, useReviewSubmission } from './useReviews'
import ReviewForm from './ReviewForm.vue'
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const pending = usePendingReviews()
const findingId = computed(() => String(route.params.id))
const pendingFinding = computed(() => pending.data.value?.find(item => item.finding_id === findingId.value))
const investigationId = computed(() => {
  const linkedId = route.query.investigation
  return pendingFinding.value?.investigation_id ??
    (typeof linkedId === 'string' && /^[0-9a-f-]{36}$/i.test(linkedId) ? linkedId : '')
})
const investigation = useInvestigation(investigationId)
const detail = computed(() => investigation.data.value)
const runId = computed(() => detail.value?.current_run.id ?? null)
const currentFinding = computed(() => detail.value?.ai_findings.find(item =>
  item.finding_id === findingId.value && item.run_id === runId.value &&
  (!pendingFinding.value || item.finding_version === pendingFinding.value.finding_version)))
const finding = computed(() => pendingFinding.value ?? currentFinding.value)
const savedDecision = computed(() => detail.value?.decisions.find(item =>
  item.ai_finding_id === currentFinding.value?.finding_id &&
  item.finding_version === currentFinding.value?.finding_version))
const decisionLabel = computed(() => savedDecision.value
  ? { approve: '已通过', modify: '已修改', reject: '已驳回' }[savedDecision.value.decision]
  : '')
const citations = computed(() => currentFinding.value?.citations.map(item => citationSchema.parse(item)) ?? [])
const evidence = computed(() => {
  const lookup = new Map(detail.value?.retrieved_evidence.map(item => [item.chunk_id, item]))
  const matched = new Map<string, InvestigationDetail['retrieved_evidence'][number]>()
  for (const citation of citations.value) {
    const item = lookup.get(citation.chunk_id)
    if (item && item.evidence_id === citation.evidence_id && item.content_hash === citation.content_hash) {
      matched.set(item.chunk_id, item)
    }
  }
  return [...matched.values()]
})
const citationNumbers = computed(() => new Map(
  [...new Set(citations.value.map(item => item.chunk_id))].map((id, index) => [id, index + 1]),
))
const unmatchedCitations = computed(() => citationNumbers.value.size - evidence.value.length)
const navigation = useEvidenceNavigation(investigationId, runId)
const { activeChunkId, selectedChunkId, canReturn, unavailableMessage } = navigation
watch([() => finding.value?.finding_id, () => finding.value?.finding_version], navigation.reset)
const events = useInvestigationEvents(runId, investigationId)
const progressMedia = globalThis.matchMedia?.('(min-width: 1360px)')
const expandProgress = shallowRef(progressMedia?.matches ?? false)
function syncProgress(event: { matches: boolean }) { expandProgress.value = event.matches }
onMounted(() => progressMedia?.addEventListener('change', syncProgress))
onUnmounted(() => progressMedia?.removeEventListener('change', syncProgress))
const eligible = computed(() => canReviewFinding(investigation.data.value, finding.value, auth.user))
const submission = useReviewSubmission()
async function refresh() { await pending.refetch(); if (investigationId.value) await investigation.refetch() }
async function submit(body: Omit<components['schemas']['ReviewRequest'], 'finding_version'>) {
  const selected = finding.value
  if (!selected || !eligible.value || submission.isPending.value) return
  try { const result = await submission.submit(`/reviews/${selected.finding_id}`, { ...body, finding_version: selected.finding_version }); if (result) await router.push(`/investigations/${selected.investigation_id}`) } catch { /* Render mutation error and refreshed conflict context. */ }
}
</script>
<template>
  <section class="feature-page">
    <header class="page-heading"><div><RouterLink v-if="savedDecision && investigationId" :to="`/investigations/${investigationId}`">返回调查详情</RouterLink><RouterLink v-else to="/reviews">返回待复核列表</RouterLink><h1>复核发现</h1><span v-if="savedDecision" class="status-label" :data-status="savedDecision.decision === 'reject' ? 'failed' : 'saved'">{{ decisionLabel }}</span><p v-if="finding" class="muted">{{ finding.company_name }}<template v-if="detail"> · 基准日 {{ detail.as_of_date }} · 第 {{ detail.current_run.run_number }} 次运行</template></p></div><NButton :loading="pending.isFetching.value || investigation.isFetching.value" @click="refresh">刷新</NButton></header>
    <QueryFeedback :loading="pending.isPending.value || (!!investigationId && investigation.isPending.value)" :refreshing="(pending.isFetching.value || investigation.isFetching.value) && (!!pending.data.value || !!investigation.data.value)" :error="pending.error.value ?? investigation.error.value" @refresh="refresh" />
    <QueryFeedback :error="submission.error.value" @refresh="refresh" />
    <p v-if="pending.data.value && !finding && (!investigationId || !investigation.isPending.value)" class="panel muted" role="status">该发现不存在、已被复核、版本已更新，或当前账号无权查看。请返回列表确认。</p>
    <ReviewForm v-if="finding" :finding="finding" :pending="submission.isPending.value" :read-only="!eligible" @submit="submit">
      <template #finding>
        <FindingList :findings="[currentFinding ?? finding]" :selected-chunk-id="selectedChunkId" :citation-numbers="citationNumbers" @navigate="navigation.navigate" />
      </template>
      <template #evidence>
        <p v-if="unavailableMessage" class="navigation-notice" role="status">{{ unavailableMessage }}</p>
        <p v-if="unmatchedCitations && evidence.length" class="state-note" role="status">有 {{ unmatchedCitations }} 条引用未匹配到本次运行的冻结摘录。</p>
        <EvidenceList
v-if="detail" :evidence="evidence" :retrieval-state="detail.retrieval_state"
          title="对照证据" :empty-message="currentFinding ? '当前发现没有匹配的冻结摘录。' : '调查运行或发现版本已更新，请刷新复核列表。'"
          :citation-numbers="citationNumbers" :active-chunk-id="activeChunkId" :selected-chunk-id="selectedChunkId"
          :can-return="canReturn" @return="navigation.returnToOrigin" />
        <section v-else class="panel"><h2 class="source-heading">对照证据</h2><p class="state-note">等待载入当前运行的冻结证据。</p></section>
      </template>
      <template #progress>
        <details v-if="detail" class="panel review-progress" :open="expandProgress">
          <summary>调查进度 · {{ statusLabel(detail.current_run.status) }}</summary>
          <RunEventTimeline :run="detail.current_run" :events="events.events.value" :steps="detail.steps" :history-available="events.historyAvailable.value" />
        </details>
      </template>
      <template #read-only>
        <template v-if="savedDecision">
          <h2>已保存的人工决定</h2>
          <p><strong>{{ decisionLabel }}</strong> · 发现版本 {{ savedDecision.finding_version }}</p>
          <p>{{ savedDecision.reason }}</p>
        </template>
        <p v-else-if="detail && !currentFinding" class="state-note" role="status">调查运行或发现版本已更新，请刷新复核列表后重新打开。</p>
        <p v-else-if="detail" class="state-note" role="status">当前运行状态或账号权限不允许提交此复核。</p>
      </template>
    </ReviewForm>
  </section>
</template>

<style scoped>
.review-progress summary { cursor: pointer; font-weight: 600; }
.review-progress :deep(.run-timeline) { padding: 12px 0 0; border: 0; box-shadow: none; }
.review-progress :deep(.run-timeline h2) { display: none; }
</style>
