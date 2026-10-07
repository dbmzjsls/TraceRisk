<script setup lang="ts">
import { computed } from 'vue'
import { NButton } from 'naive-ui'
import QueryFeedback from '../../ui/QueryFeedback.vue'
import { useAuthStore } from '../auth/store'
import { useInvestigationList } from '../investigations/useInvestigations'
import { usePendingReviews } from './useReviews'
import ReviewList from './ReviewList.vue'
const auth = useAuthStore()
const pending = usePendingReviews()
const investigations = useInvestigationList()
const runs = computed(() => (investigations.data.value?.items ?? []).filter(item => item.current_run.status === 'awaiting_review' && item.created_by !== auth.user?.id))
const empty = computed(() => !!pending.data.value && pending.data.value.length === 0 && runs.value.length === 0)
async function refresh() { await Promise.all([pending.refetch(), investigations.refetch()]) }
</script>
<template>
  <section class="feature-page">
    <header class="page-heading"><div><h1>人工复核</h1><p class="muted">原始发现、引用与版本来自当前待复核记录。</p></div><NButton :loading="pending.isFetching.value || investigations.isFetching.value" @click="refresh">刷新</NButton></header>
    <QueryFeedback :loading="pending.isPending.value || investigations.isPending.value" :refreshing="(pending.isFetching.value || investigations.isFetching.value) && (!!pending.data.value || !!investigations.data.value)" :error="pending.error.value ?? investigations.error.value" :empty="empty" empty-text="暂无待复核发现或运行。" @refresh="refresh" />
    <ReviewList v-if="pending.data.value && !empty" :findings="pending.data.value" :runs="runs" />
  </section>
</template>
