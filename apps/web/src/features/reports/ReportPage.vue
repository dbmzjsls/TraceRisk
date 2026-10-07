<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { NButton } from 'naive-ui'
import { apiRequest } from '../../api/client'
import { reportSchema } from '../../api/schemas'
import QueryFeedback from '../../ui/QueryFeedback.vue'
import ReportSections from './ReportSections.vue'
const route = useRoute()
const { data, isPending, isFetching, error, refetch } = useQuery({ queryKey: computed(() => ['reports', String(route.params.id)]), queryFn: ({ signal }) => apiRequest(`/reports/${encodeURIComponent(String(route.params.id))}`, reportSchema, { signal }), retry: false })
</script>
<template>
  <section class="feature-page">
    <header class="page-heading"><div><RouterLink to="/investigations">返回调查列表</RouterLink><h1>{{ data ? `${data.snapshot.company.name} · 风险报告` : '风险报告' }}</h1><p class="muted">已生成报告为不可变快照。合成数据，仅供演示。</p></div><NButton :loading="isFetching" @click="refetch()">刷新</NButton></header>
    <QueryFeedback :loading="isPending" :refreshing="isFetching && !!data" :error="error" @refresh="refetch()" />
    <template v-if="data"><ReportSections :snapshot="data.snapshot" /><section class="panel"><h2>存档 HTML 报告</h2><iframe class="report-frame" title="存档风险报告" sandbox="" referrerpolicy="no-referrer" :srcdoc="data.html_content" /></section></template>
  </section>
</template>
