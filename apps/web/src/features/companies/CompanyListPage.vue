<script setup lang="ts">
import { NButton } from 'naive-ui'
import QueryFeedback from '../../ui/QueryFeedback.vue'
import CompanyList from './CompanyList.vue'
import { useCompanies } from './useCompanies'
const { data, isPending, isFetching, error, refetch } = useCompanies()
</script>
<template>
  <section class="feature-page">
    <header class="page-heading"><div><h1>企业</h1><p class="muted">选择企业，按基准日期发起司法风险调查。</p></div><NButton :loading="isFetching" @click="refetch()">刷新</NButton></header>
    <QueryFeedback :loading="isPending" :refreshing="isFetching && !!data" :error="error" :empty="!!data && !data.items.length" empty-text="暂无企业记录。" @refresh="refetch()" />
    <CompanyList v-if="data?.items.length" :items="data.items" />
  </section>
</template>
