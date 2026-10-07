<script setup lang="ts">
import type { InvestigationDetail } from '../../api/schemas'
import { statusLabel } from './status'
defineProps<{ run: InvestigationDetail['current_run']; steps: InvestigationDetail['steps'] }>()
</script>
<template>
  <section class="panel">
    <h2>第 {{ run.run_number }} 次运行 · {{ statusLabel(run.status) }}</h2><p class="mono muted">运行 {{ run.id }}</p>
    <p v-if="run.status === 'awaiting_review'">流程等待人工复核，每 5 秒读取当前状态。</p>
    <p v-else-if="run.status === 'queued' || run.status === 'running' || run.status === 'resume_queued'">每 2 秒读取当前状态，步骤仅显示已持久化的执行记录。</p>
    <p v-else-if="run.status === 'failed'">运行失败。自动刷新已停止，可手动刷新确认最新状态。</p>
    <p v-else>运行已完成，自动刷新已停止。</p>
    <ul v-if="steps.length" class="step-list"><li v-for="step in steps" :key="step.name"><span>{{ step.name }}</span><span class="muted">执行尝试 {{ step.attempt_count }} 次</span></li></ul>
    <p v-else class="muted">暂无已记录步骤。</p>
    <RouterLink v-if="run.report_id" :to="`/reports/${run.report_id}`">查看本次运行报告</RouterLink><p v-else class="muted">报告尚未生成。</p>
  </section>
</template>
