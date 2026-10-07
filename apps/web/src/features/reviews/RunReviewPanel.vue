<script setup lang="ts">
import { computed } from 'vue'
import type { InvestigationDetail } from '../../api/schemas'
import type { components } from '../../api/generated'
import QueryFeedback from '../../ui/QueryFeedback.vue'
import { useAuthStore } from '../auth/store'
import { canReviewRun, refreshReviewContext, useReviewSubmission } from './useReviews'
import ReviewForm from './ReviewForm.vue'
const props = defineProps<{ investigation: InvestigationDetail }>()
const auth = useAuthStore()
const submission = useReviewSubmission()
const eligible = computed(() => canReviewRun(props.investigation, auth.user) && props.investigation.ai_findings.length === 0 && !props.investigation.decisions.some(decision => decision.ai_finding_id === null))
async function submit(body: Omit<components['schemas']['ReviewRequest'], 'finding_version'>) {
  if (!eligible.value || submission.isPending.value || body.decision === 'modify') return
  try { await submission.submit(`/runs/${props.investigation.current_run.id}/review`, { decision: body.decision, reason: body.reason }) } catch { /* Display the authoritative conflict or transport error. */ }
}
</script>
<template>
  <section v-if="eligible || submission.error.value || submission.isSuccess.value" class="panel"><h2>运行级复核</h2><p class="muted">本次运行没有 AI 发现，复核整个运行的证据与规则记录。</p><QueryFeedback :error="submission.error.value" @refresh="refreshReviewContext()" /><p v-if="submission.isSuccess.value" role="status">复核已提交，请等待恢复执行。</p><ReviewForm v-if="eligible" :pending="submission.isPending.value" @submit="submit" /></section>
</template>
