<script setup lang="ts">
import { NButton } from 'naive-ui'
import QueryFeedback from '../../ui/QueryFeedback.vue'
import InvestigationList from './InvestigationList.vue'
import { useInvestigationList } from './useInvestigations'
const { data, isPending, isFetching, error, refetch } = useInvestigationList()
</script>
<template>
  <section class="feature-page">
    <header class="page-heading"><div><h1>调查</h1><p class="muted">当前运行与报告来自持久化记录。最多显示 100 条调查。</p></div><NButton :loading="isFetching" @click="refetch()">刷新</NButton></header>
    <QueryFeedback :loading="isPending" :refreshing="isFetching && !!data" :error="error" :empty="!!data && !data.items.length" empty-text="暂无调查。请从企业详情发起调查。" @refresh="refetch()" />
    <InvestigationList v-if="data?.items.length" :items="data.items" />
  </section>
</template>
