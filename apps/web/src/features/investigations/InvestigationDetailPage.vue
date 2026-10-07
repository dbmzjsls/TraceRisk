<script setup lang="ts">
import { computed, shallowRef } from 'vue'
import { useRoute } from 'vue-router'
import { NButton } from 'naive-ui'
import QueryFeedback from '../../ui/QueryFeedback.vue'
import { useAuthStore } from '../auth/store'
import RunReviewPanel from '../reviews/RunReviewPanel.vue'
import { canReviewRun } from '../reviews/useReviews'
import { detailPollingEnabled, useInvestigation } from './useInvestigations'
import CoverageConflictPanel from './CoverageConflictPanel.vue'
import EvidenceList from './EvidenceList.vue'
import RiskResultSections from './RiskResultSections.vue'
import RunConnectionStatus from './RunConnectionStatus.vue'
import RunEventTimeline from './RunEventTimeline.vue'
import { useInvestigationEvents } from './useInvestigationEvents'
import { useEvidenceNavigation } from './useEvidenceNavigation'
const route = useRoute()
const auth = useAuthStore()
const investigationId = computed(() => String(route.params.id))
const wiringReady = shallowRef(false)
let investigationQuery!: ReturnType<typeof useInvestigation>
let eventStream!: ReturnType<typeof useInvestigationEvents>
const pollingEnabled = computed(() => wiringReady.value ? detailPollingEnabled(investigationQuery.data.value?.current_run.status, eventStream.pollingFallback.value) : true)
investigationQuery = useInvestigation(investigationId, pollingEnabled)
const { data, isPending, isFetching, error, refetch } = investigationQuery
const runId = computed(() => data.value?.current_run.id ?? null)
eventStream = useInvestigationEvents(runId, investigationId)
wiringReady.value = true
const reviewLinks = computed(() => canReviewRun(data.value, auth.user))
const evidenceNavigation = useEvidenceNavigation(investigationId, runId)
</script>
<template>
  <section class="feature-page">
    <header class="page-heading"><div><RouterLink to="/investigations">返回调查列表</RouterLink><h1>{{ data?.company_name ?? '调查详情' }}</h1><p v-if="data" class="muted">基准日期 {{ data.as_of_date }} · 第 {{ data.current_run.run_number }} 次运行 · 合成数据，仅供演示</p></div><NButton :loading="isFetching" @click="refetch()">刷新</NButton></header>
    <QueryFeedback :loading="isPending" :refreshing="isFetching && !!data" :error="error" @refresh="refetch()" />
    <template v-if="data">
      <RunConnectionStatus :status="eventStream.connectionStatus.value" :polling-fallback="eventStream.pollingFallback.value" :permission-error="eventStream.permissionError.value" @retry="eventStream.retry" />
      <p v-if="evidenceNavigation.unavailableMessage.value" class="navigation-notice" role="status">{{ evidenceNavigation.unavailableMessage.value }} <button type="button" class="text-action" @click="evidenceNavigation.clearUnavailable">关闭</button></p>
      <div class="dossier-layout">
        <aside class="timeline-column" aria-label="当前运行事件时间线"><RunEventTimeline :run="data.current_run" :events="eventStream.events.value" :steps="data.steps" :history-available="eventStream.historyAvailable.value" /></aside>
        <main class="dossier-main">
          <section class="panel run-summary"><div class="section-kicker">CURRENT RUN</div><h2>运行状态</h2><p>第 {{ data.current_run.run_number }} 次运行 · {{ data.current_run.status }}</p><p class="mono muted">Run {{ data.current_run.id }}</p></section>
          <RiskResultSections :investigation="data" :review-links="reviewLinks" @navigate="evidenceNavigation.navigate" />
          <CoverageConflictPanel :retrieval-state="data.retrieval_state" :coverage="data.coverage" :conflicts="data.conflicts" />
          <EvidenceList :evidence="data.retrieved_evidence" :retrieval-state="data.retrieval_state" :active-chunk-id="evidenceNavigation.activeChunkId.value" :selected-chunk-id="evidenceNavigation.selectedChunkId.value" :can-return="evidenceNavigation.canReturn.value" @return="evidenceNavigation.returnToOrigin" />
          <RunReviewPanel :key="data.current_run.id" :investigation="data" />
        </main>
      </div>
    </template>
  </section>
</template>
