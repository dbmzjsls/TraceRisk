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
import { statusLabel } from '../investigations/status'
import AssistantAnswer from './AssistantAnswer.vue'
import AssistantContext from './AssistantContext.vue'
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
const lastTurn = computed(() => active.value?.turns.at(-1))
const historicalTurns = computed(() => active.value?.turns.slice(0, -1) ?? [])
const currentDetail = computed(() => detail.data.value?.id === assistant.investigationId.value ? detail.data.value : undefined)
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
      </div>
    </header>

    <p v-if="catalog.error.value" role="alert">{{ catalog.error.value.message }} <button type="button" class="text-action" @click="catalog.refetch()">刷新企业</button></p>
    <div class="assistant-layout">
      <aside class="assistant-settings">
        <AssistantContext
          :companies="companies ?? []" :company-id="companyId" :date="date" :busy="busy"
          :can-create="canCreate" :sessions="sessions" :selected-id="active?.id"
          @company="assistant.setCompany" @date="assistant.setDate" @select="assistant.select"
          @new="assistant.startNew" @fill="draft = $event" />
        <details v-if="currentDetail" class="panel assistant-progress" :open="busy">
          <summary>调查进度 · {{ statusLabel(currentDetail.current_run.status) }}</summary>
          <RunConnectionStatus
            :status="events.connectionStatus.value" :polling-fallback="events.pollingFallback.value"
            :permission-error="events.permissionError.value" @retry="events.retry" />
          <RunEventTimeline
            :run="currentDetail.current_run" :events="events.events.value"
            :steps="currentDetail.steps" :history-available="events.historyAvailable.value" />
          <RouterLink :to="'/investigations/' + currentDetail.id">查看调查详情</RouterLink>
        </details>
      </aside>

      <section class="panel assistant-conversation" aria-label="调查对话">
        <h2>对话</h2>
        <p v-if="!active?.turns.length" class="muted">选择企业和基准日，然后发送问题。后续追问沿用本次运行。</p>
        <p v-if="detail.error.value && !lastTurn?.error" role="alert">
          {{ detail.error.value.message }} <button type="button" class="text-action" @click="detail.refetch()">刷新调查</button>
        </p>
        <p v-if="currentRunChanged" role="alert">
          调查已切换运行。<button type="button" class="text-action" :disabled="busy" @click="assistant.confirmCurrentRun">在当前运行中重新提问</button>
        </p>
        <details v-for="turn in historicalTurns" :key="turn.id" class="assistant-history">
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
          <p v-else-if="lastTurn.error" role="alert">{{ lastTurn.error }}</p>
          <p v-else-if="busy" role="status">{{ currentDetail?.retrieval_state === 'ready' && ['awaiting_review', 'completed'].includes(currentDetail.current_run.status) ? '正在解释本次调查…' : '等待调查结果…' }}</p>
          <NButton
            v-if="lastTurn.error && currentDetail?.current_run.status === 'failed' && canCreate" size="small"
            @click="assistant.startNew">
重新调查
</NButton>
        </article>

        <form class="assistant-form" @submit.prevent="assistant.submit">
          <label for="assistant-question">输入问题</label>
          <textarea
            id="assistant-question" v-model="draft" rows="3" maxlength="2000" :disabled="busy"
            placeholder="例如，司法规则为什么计35分？" />
          <p v-if="notice" role="alert">{{ notice }}</p>
          <NButton type="primary" attr-type="submit" :disabled="busy">发送</NButton>
        </form>
      </section>

      <aside class="assistant-evidence" aria-label="回答引用证据">
        <p v-if="unavailableMessage" role="status">{{ unavailableMessage }}</p>
        <EvidenceList
          v-if="evidence.length" :evidence="evidence" retrieval-state="ready"
          :active-chunk-id="activeChunkId" :selected-chunk-id="selectedChunkId"
          :can-return="canReturn" @return="navigation.returnToOrigin" />
        <section v-else class="panel">
          <h2>回答引用证据</h2>
          <p class="state-note">{{ lastTurn?.answer ? '这条回答没有引用冻结证据。' : '回答中的引用会在这里显示。' }}</p>
        </section>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.assistant-page { max-width: 1700px; }
.assistant-layout { display: grid; grid-template-columns: minmax(190px, .8fr) minmax(0, 2.2fr) minmax(240px, 1.25fr); gap: 1rem; align-items: start; }
.assistant-settings, .assistant-evidence { display: grid; gap: 1rem; min-width: 0; }
.assistant-progress summary, .assistant-history summary { cursor: pointer; }
.assistant-progress :deep(.run-timeline) { border: 0; padding: .65rem 0; }
.assistant-conversation { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: .65rem; }
.assistant-conversation h2, .assistant-conversation p { margin: 0; }
.assistant-history { border-bottom: 1px solid var(--line, #c5d8cf); padding: .3rem 0 .5rem; }
.assistant-history:not([open]) summary { text-overflow: ellipsis; overflow: hidden; white-space: nowrap; }
.assistant-turn { display: grid; grid-template-columns: minmax(0, 1fr); gap: .85rem; }
.assistant-question { padding: .7rem .8rem; background: var(--accent-wash); border-radius: .45rem; white-space: pre-wrap; overflow-wrap: anywhere; }
.assistant-form { display: grid; gap: .45rem; align-items: start; margin-top: .55rem; }
.assistant-form textarea { width: 100%; padding: .55rem; resize: vertical; font: inherit; color: inherit; background: transparent; border: 1px solid var(--line, #c5d8cf); border-radius: .35rem; }
.assistant-form button { justify-self: end; }
.assistant-evidence :deep(.evidence-card) { overflow-wrap: anywhere; }
@media (max-width: 1200px) {
  .assistant-layout { grid-template-columns: minmax(180px, .75fr) minmax(0, 2fr); }
  .assistant-evidence { grid-column: 2; }
}
@media (max-width: 760px) {
  .assistant-layout { grid-template-columns: minmax(0, 1fr); }
  .assistant-evidence { grid-column: 1; }
}
</style>
