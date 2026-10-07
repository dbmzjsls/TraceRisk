<script setup lang="ts">
import type { PendingFinding, Investigation } from '../../api/schemas'
defineProps<{ findings: PendingFinding[]; runs: Investigation[] }>()
</script>
<template>
  <section class="panel"><h2>待复核发现</h2><article v-for="finding in findings" :key="finding.finding_id" class="finding"><RouterLink :to="`/reviews/${finding.finding_id}`" class="record-title">{{ finding.title }}</RouterLink><p class="muted">{{ finding.company_name }} · 版本 {{ finding.finding_version }} · {{ finding.risk_type }}</p><p>{{ finding.summary }}</p></article><p v-if="!findings.length" class="muted">暂无可复核的 AI 发现。自己的调查不进入待复核列表。</p></section>
  <section class="panel"><h2>等待复核的运行</h2><p class="muted">无 AI 发现的运行可在调查详情进行运行级复核。详情会确认当前状态和复核权限。</p><article v-for="run in runs" :key="run.id" class="finding"><RouterLink :to="`/investigations/${run.id}`">{{ run.company_name }} · 第 {{ run.current_run.run_number }} 次运行</RouterLink><p class="muted">基准日期 {{ run.as_of_date }}</p></article><p v-if="!runs.length" class="muted">暂无其他账号创建的待复核运行。</p></section>
</template>
