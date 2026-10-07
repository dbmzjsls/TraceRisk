<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import type { components } from '../../api/generated'
import QueryFeedback from '../../ui/QueryFeedback.vue'
import { useAuthStore } from '../auth/store'
import FindingList from '../investigations/FindingList.vue'
import { useInvestigation } from '../investigations/useInvestigations'
import { canReviewFinding, usePendingReviews, useReviewSubmission } from './useReviews'
import ReviewForm from './ReviewForm.vue'
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const pending = usePendingReviews()
const finding = computed(() => pending.data.value?.find(item => item.finding_id === String(route.params.id)))
const investigation = useInvestigation(() => finding.value?.investigation_id ?? '')
const eligible = computed(() => canReviewFinding(investigation.data.value, finding.value, auth.user))
const submission = useReviewSubmission()
async function refresh() { await pending.refetch(); if (finding.value) await investigation.refetch() }
async function submit(body: Omit<components['schemas']['ReviewRequest'], 'finding_version'>) {
  const selected = finding.value
  if (!selected || !eligible.value || submission.isPending.value) return
  try { const result = await submission.submit(`/reviews/${selected.finding_id}`, { ...body, finding_version: selected.finding_version }); if (result) await router.push(`/investigations/${selected.investigation_id}`) } catch { /* Render mutation error and refreshed conflict context. */ }
}
</script>
<template>
  <section class="feature-page">
    <header class="page-heading"><div><RouterLink to="/reviews">返回待复核列表</RouterLink><h1>复核发现</h1></div><NButton :loading="pending.isFetching.value || investigation.isFetching.value" @click="refresh">刷新</NButton></header>
    <QueryFeedback :loading="pending.isPending.value || (!!finding && investigation.isPending.value)" :refreshing="(pending.isFetching.value || investigation.isFetching.value) && (!!pending.data.value || !!investigation.data.value)" :error="pending.error.value ?? investigation.error.value" @refresh="refresh" />
    <QueryFeedback :error="submission.error.value" @refresh="refresh" />
    <p v-if="pending.data.value && !finding" class="panel muted" role="status">该发现不存在、已被复核、版本已更新，或当前账号无权查看。请返回列表确认。</p>
    <FindingList v-if="finding" :findings="[finding]" />
    <section v-if="eligible && finding" class="panel"><h2>提交版本 {{ finding.finding_version }} 的决定</h2><ReviewForm :finding="finding" :pending="submission.isPending.value" @submit="submit" /></section>
    <p v-else-if="finding && investigation.data.value" class="panel muted">当前状态、版本或账号不允许提交此复核。服务端会再次校验资格。</p>
  </section>
</template>
