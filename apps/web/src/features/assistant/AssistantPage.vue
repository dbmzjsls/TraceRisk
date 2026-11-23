<script setup lang="ts">
import { computed, nextTick, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton } from 'naive-ui'
import type { AssistantAnswer as Answer } from '../../api/schemas'
import { useAuthStore } from '../auth/store'
import { useCompanies } from '../companies/useCompanies'
import EvidenceList from '../investigations/EvidenceList.vue'
import RunConnectionStatus from '../investigations/RunConnectionStatus.vue'
import RunEventTimeline from '../investigations/RunEventTimeline.vue'
import { useEvidenceNavigation } from '../investigations/useEvidenceNavigation'
import { approvedReviewCount, statusLabel } from '../investigations/status'
import AssistantAnswer from './AssistantAnswer.vue'
import AssistantContext from './AssistantContext.vue'
import { contextQuestions, exampleDrafts, recommendedQuestions } from './questions'
import { useAssistant } from './useAssistant'

const route = useRoute()
const auth = useAuthStore()
const canCreate = computed(() => auth.role === 'analyst' || auth.role === 'admin')
const catalog = useCompanies()
const companies = computed(() => catalog.data.value?.items)
const initialId = typeof route.query.investigation === 'string' && /^[0-9a-f-]{36}$/i.test(route.query.investigation)
  ? route.query.investigation : ''
const assistant = useAssistant(companies, canCreate, initialId)
const { sessions, active, companyId, date, draft, notice, busy, detail, events, currentRunChanged } = assistant
const prompts = computed(() => companies.value?.find(item => item.id === companyId.value)?.is_synthetic
  ? recommendedQuestions : contextQuestions)
const exampleId = route.query.example === '02' ? '02' : '01'
if (!initialId) {
  const example = exampleDrafts[exampleId]
  let applied = false
  watch(companies, items => {
    if (!items || applied) return
    applied = true
    const company = items.find(item => item.unified_credit_code === example.companyCode)
    if (company && canCreate.value && !companyId.value && !draft.value) {
      assistant.setCompany(company.id)
      assistant.setDate('2026-09-11')
      draft.value = example.question
    }
  }, { immediate: true })
}
const questionInput = shallowRef<{ focus: () => void } | null>(null)
function fillQuestion(question: string) {
  draft.value = question
  questionInput.value?.focus()
}
const lastTurn = computed(() => active.value?.turns.at(-1))
const historicalTurns = computed(() => active.value?.turns.slice(0, -1) ?? [])
const currentDetail = computed(() => detail.data.value?.id === assistant.investigationId.value ? detail.data.value : undefined)
const approvals = computed(() => currentDetail.value?.current_run.id === assistant.runId.value
  ? approvedReviewCount(currentDetail.value) : 0)
const navigation = useEvidenceNavigation(assistant.investigationId, assistant.runId)
const { activeChunkId, selectedChunkId, canReturn, unavailableMessage } = navigation
const selectedAnswer = shallowRef<Answer>()
const evidence = computed(() => (selectedAnswer.value ?? lastTurn.value?.answer)?.citations ?? [])
watch([active, lastTurn], () => {
  selectedAnswer.value = undefined
  navigation.reset()
})

async function cite(answer: Answer, chunk: string, origin: unknown) {
  selectedAnswer.value = answer
  await nextTick()
  navigation.navigate(chunk, origin)
}
</script>

<template>
  <section class="feature-page assistant-page">
    <header class="page-heading">
      <div>
        <h1>调查助手</h1>
        <p class="muted">围绕一次授信前调查提问，查看规则、复核状态和冻结证据。</p>
        <span v-if="approvals" class="status-label" data-status="saved">人工复核全部通过（{{ approvals }} 项）</span>
      </div>
      <RouterLink to="/demo">查看比赛示例</RouterLink>
    </header>

    <p v-if="catalog.error.value" role="alert">{{ catalog.error.value.message }} <button type="button" class="text-action" @click="catalog.refetch()">刷新企业</button></p>
    <div class="assistant-layout">
      <aside class="assistant-settings">
        <AssistantContext
          :companies="companies ?? []" :company-id="companyId" :date="date" :busy="busy"
          :can-create="canCreate" :sessions="sessions" :selected-id="active?.id"
          @company="assistant.setCompany" @date="assistant.setDate" @select="assistant.select"
          @new="assistant.startNew" />
        <details v-if="currentDetail" class="panel assistant-progress" :open="busy">
          <summary>调查进度 <span class="status-label" :data-status="currentDetail.current_run.status">{{ statusLabel(currentDetail.current_run.status) }}</span></summary>
          <RunConnectionStatus
            :status="events.connectionStatus.value" :polling-fallback="events.pollingFallback.value"
            :permission-error="events.permissionError.value" @retry="events.retry" />
          <RunEventTimeline
            :run="currentDetail.current_run" :events="events.events.value"
            :steps="currentDetail.steps" :history-available="events.historyAvailable.value" />
          <RouterLink :to="'/investigations/' + currentDetail.id">查看调查详情</RouterLink>
        </details>
      </aside>

      <section class="assistant-conversation" aria-label="调查对话">
        <section class="panel assistant-prompts">
          <h2>示例问题</h2>
          <div class="page-actions">
            <NButton
              v-for="item in prompts" :key="item.label" secondary size="small"
              :disabled="busy" @click="fillQuestion(item.text)">
{{ item.label }}
</NButton>
          </div>
        </section>
        <p v-if="!active?.turns.length" class="state-note empty-conversation">{{ companyId && draft ? '已准备好示例调查，可直接点击发送；也可以更换企业和问题。' : '选择企业和基准日，然后发送问题。' }}后续追问沿用本次运行。</p>
        <p v-if="detail.error.value && !lastTurn?.error" role="alert">
          {{ detail.error.value.message }} <button type="button" class="text-action" @click="detail.refetch()">刷新调查</button>
        </p>
        <p v-if="currentRunChanged" role="alert">
          调查已切换运行。<button type="button" class="text-action" :disabled="busy" @click="assistant.confirmCurrentRun">在当前运行中重新提问</button>
        </p>
        <details v-for="turn in historicalTurns" :key="turn.id" class="panel assistant-history">
          <summary>{{ turn.question }}</summary>
          <AssistantAnswer
            v-if="turn.answer && active" :answer="turn.answer" :company="active.companyName" :date="active.date"
            @navigate="(chunk, origin) => turn.answer && cite(turn.answer, chunk, origin)" />
          <p v-else-if="turn.error">{{ turn.error }}</p>
        </details>
        <article v-if="lastTurn && active" class="assistant-turn" aria-label="当前问答">
          <p class="assistant-question"><strong>提问</strong> {{ lastTurn.question }}</p>
          <AssistantAnswer
            v-if="lastTurn.answer" :answer="lastTurn.answer" :company="active.companyName" :date="active.date"
            @navigate="(chunk, origin) => lastTurn?.answer && cite(lastTurn.answer, chunk, origin)" />
          <p v-else-if="lastTurn.error" class="state-note error-note" role="alert">{{ lastTurn.error }}</p>
          <p v-else-if="busy" class="state-note" role="status">{{ currentDetail?.retrieval_state === 'ready' && ['awaiting_review', 'completed'].includes(currentDetail.current_run.status) ? '正在解释本次调查…' : '等待调查结果…' }}</p>
          <NButton
            v-if="lastTurn.error && currentDetail?.current_run.status === 'failed' && canCreate" size="small"
            @click="assistant.startNew">
重新调查
</NButton>
        </article>

        <form class="panel assistant-form" @submit.prevent="assistant.submit">
          <label for="assistant-question">输入问题</label>
          <textarea
            id="assistant-question" ref="questionInput" v-model="draft" rows="3" maxlength="2000" :disabled="busy"
            placeholder="例如，司法规则命中的依据是什么？" />
          <p v-if="notice" class="state-note error-note" role="alert">{{ notice }}</p>
          <NButton type="primary" attr-type="submit" :disabled="busy">发送</NButton>
        </form>
      </section>

      <aside class="assistant-evidence" aria-label="回答引用证据">
        <p v-if="unavailableMessage" class="navigation-notice" role="status">{{ unavailableMessage }}</p>
        <EvidenceList
          v-if="evidence.length" :evidence="evidence" retrieval-state="ready" title="回答引用证据"
          :active-chunk-id="activeChunkId" :selected-chunk-id="selectedChunkId"
          :can-return="canReturn" @return="navigation.returnToOrigin" />
        <section v-else class="panel">
          <h2 class="source-heading">回答引用证据</h2>
          <p class="state-note">{{ lastTurn?.answer ? '这条回答没有引用冻结证据。' : '回答中的引用会在这里显示。' }}</p>
        </section>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.assistant-layout {
  display: grid;
  grid-template-columns: minmax(0, .85fr) minmax(0, 2fr) minmax(0, 1.13fr);
  gap: var(--panel-gap);
  align-items: start;
}
.assistant-settings, .assistant-evidence { display: grid; gap: var(--panel-gap); min-width: 0; }
.assistant-progress summary, .assistant-history summary { cursor: pointer; }
.assistant-progress :deep(.run-timeline) { border: 0; padding: .65rem 0; box-shadow: none; }
.assistant-progress :deep(.run-timeline h2) { display: none; }
.assistant-conversation, .assistant-turn { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
.assistant-history { padding: 8px 12px; }
.assistant-history:not([open]) summary { text-overflow: ellipsis; overflow: hidden; white-space: nowrap; }
.assistant-history[open] summary { margin-bottom: 12px; overflow-wrap: anywhere; }
.assistant-question {
  margin: 0; padding: 12px 16px; border-radius: 8px; background: var(--accent-wash);
  color: var(--selected-text); white-space: pre-wrap; overflow-wrap: anywhere;
}
.empty-conversation { margin: 0; }
.assistant-form { display: grid; gap: 8px; align-items: start; }
.assistant-form textarea {
  width: 100%; padding: 10px; resize: vertical; font: inherit; color: inherit;
  background: var(--panel-bg); border: 1px solid var(--border); border-radius: 6px;
}
.assistant-form button { justify-self: end; }
@media (max-width: 1090px) {
  .assistant-layout { grid-template-columns: minmax(0, .75fr) minmax(0, 2fr); }
  .assistant-evidence { grid-column: 2; }
}
@media (max-width: 767px) {
  .assistant-layout { grid-template-columns: minmax(0, 1fr); }
  .assistant-evidence { grid-column: 1; }
}
</style>
