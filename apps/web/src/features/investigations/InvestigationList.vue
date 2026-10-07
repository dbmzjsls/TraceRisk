<script setup lang="ts">
import type { Investigation } from '../../api/schemas'
import { statusLabel } from './status'
defineProps<{ items: Investigation[] }>()
</script>
<template>
  <div class="record-list">
    <article v-for="item in items" :key="item.id" class="panel record-row">
      <div><RouterLink :to="`/investigations/${item.id}`" class="record-title">{{ item.company_name }}</RouterLink><p class="muted">基准日期 {{ item.as_of_date }} · 第 {{ item.current_run.run_number }} 次运行</p><p class="mono muted">{{ item.id }}</p></div>
      <div class="record-actions"><span class="status-label">{{ statusLabel(item.current_run.status) }}</span><RouterLink v-if="item.current_run.report_id" :to="`/reports/${item.current_run.report_id}`">查看报告</RouterLink></div>
    </article>
    <p v-if="!items.length" class="panel muted">暂无调查。请从企业详情发起调查。</p>
  </div>
</template>
