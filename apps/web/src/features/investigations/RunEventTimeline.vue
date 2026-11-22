<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { InvestigationDetail } from '../../api/schemas'
import { timelineEventLabel, timelineStepLabel, type WorkflowEvent } from './sse'
import { statusLabel } from './status'

const props = defineProps<{
  run: InvestigationDetail['current_run']
  events: WorkflowEvent[]
  steps: InvestigationDetail['steps']
  historyAvailable: boolean | null
}>()

const rows = computed(() => props.events.map(event => ({
  id: event.sequence,
  label: timelineEventLabel(event),
  time: new Date(event.created_at).toLocaleString('zh-CN', { hour12: false }),
})))
const fallbackRows = computed(() => props.steps.map(step => ({
  name: step.name,
  label: timelineStepLabel(step.name),
  attempts: step.attempt_count,
})))
</script>

<template>
  <section class="panel run-timeline">
    <header class="timeline-heading">
      <h2>运行时间线</h2>
      <div><h3 class="status-label" :data-status="run.status">第 {{ run.run_number }} 次运行 · {{ statusLabel(run.status) }}</h3><p class="mono muted">运行 {{ run.id }}</p></div>
      <RouterLink v-if="run.report_id" :to="`/reports/${run.report_id}`">查看本次运行报告</RouterLink><span v-else class="muted">报告尚未生成</span>
    </header>
    <p v-if="historyAvailable === null" class="muted">正在核对事件历史。</p>
    <p v-else-if="historyAvailable === false" class="legacy-notice">此运行创建于事件时间线上线前</p>
    <ol v-if="rows.length" class="timeline-list">
      <li v-for="row in rows" :key="row.id" class="timeline-item"><strong>{{ row.label }}</strong><time class="muted">{{ row.time }}</time></li>
    </ol>
    <ul v-else-if="historyAvailable !== true && fallbackRows.length" class="step-list">
      <li v-for="step in fallbackRows" :key="step.name"><span>{{ step.label }}</span><span class="muted">执行尝试 {{ step.attempts }} 次</span></li>
    </ul>
    <p v-else-if="historyAvailable === true" class="muted">暂无运行事件。</p>
    <p v-else-if="!fallbackRows.length" class="muted">暂无已记录步骤。</p>
  </section>
</template>

<style scoped>
.timeline-heading { display: grid; align-items: start; gap: .3rem; }
.timeline-heading h2 { margin: 0 0 .35rem; }
.timeline-list { display: grid; gap: 0.75rem; margin: 1rem 0 0; padding: 0; list-style: none; }
.timeline-item { display: grid; gap: .2rem; border-left: 2px solid var(--accent); padding-left: .65rem; }
.timeline-heading .status-label { font-size: 13px; }
.timeline-heading p { margin: .25rem 0; }
.legacy-notice { border-radius: 0.5rem; background: rgba(148, 163, 184, 0.14); padding: 0.75rem; }
</style>
