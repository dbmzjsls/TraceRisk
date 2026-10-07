<script setup lang="ts">
import { computed, reactive, shallowRef, watch } from 'vue'
import { NAlert, NButton, NFormItem, NInput, NRadio, NRadioGroup } from 'naive-ui'
import type { components } from '../../api/generated'
import type { PendingFinding } from '../../api/schemas'
import { replacementDraftSchema, replacementFromFinding } from './replacementDraft'
const props = defineProps<{ pending: boolean; finding?: PendingFinding }>()
const emit = defineEmits<{ submit: [body: Omit<components['schemas']['ReviewRequest'], 'finding_version'>] }>()
const decision = shallowRef<'approve' | 'modify' | 'reject'>('approve')
const reason = shallowRef('')
const edits = reactive({ title: '', summary: '', reasoning: '' })
watch(() => props.finding ? `${props.finding.finding_id}:${props.finding.finding_version}` : 'run', () => {
  if (props.finding) Object.assign(edits, { title: props.finding.title, summary: props.finding.summary, reasoning: props.finding.reasoning })
  decision.value = 'approve'; reason.value = ''
}, { immediate: true })
const replacement = computed(() => props.finding ? replacementDraftSchema.safeParse(replacementFromFinding(props.finding, edits)) : null)
const valid = computed(() => reason.value.trim().length > 0 && reason.value.length <= 2000 && (decision.value !== 'modify' || replacement.value?.success))
function submit() {
  if (!valid.value || props.pending) return
  if (decision.value === 'modify' && replacement.value?.success) emit('submit', { decision: 'modify', reason: reason.value, replacement: replacement.value.data })
  else if (decision.value !== 'modify') emit('submit', { decision: decision.value, reason: reason.value })
}
</script>
<template>
  <form class="form-panel" @submit.prevent="submit">
    <NFormItem label="复核决定" :label-props="{ id: 'review-decision-label' }"><NRadioGroup v-model:value="decision" name="decision" aria-labelledby="review-decision-label" :disabled="pending"><NRadio value="approve">通过（approve）</NRadio><NRadio v-if="finding" value="modify">修改（modify）</NRadio><NRadio value="reject">驳回（reject）</NRadio></NRadioGroup></NFormItem>
    <section v-if="decision === 'modify' && finding" class="replacement-editor" aria-labelledby="replacement-heading">
      <h3 id="replacement-heading">人工替换稿</h3>
      <NAlert type="info" :show-icon="false">已从原 AI 发现预填。提交后另存人工版本，原 AI 内容不会被覆盖；最终报告使用人工替换版本。</NAlert>
      <NFormItem label="替换标题（必填）" :label-props="{ for: 'replacement-title' }"><NInput v-model:value="edits.title" :input-props="{ id: 'replacement-title' }" :maxlength="512" :disabled="pending" /></NFormItem>
      <NFormItem label="替换摘要（必填）" :label-props="{ for: 'replacement-summary' }"><NInput v-model:value="edits.summary" :input-props="{ id: 'replacement-summary' }" type="textarea" :maxlength="10000" :disabled="pending" /></NFormItem>
      <NFormItem label="替换推理（必填）" :label-props="{ for: 'replacement-reasoning' }"><NInput v-model:value="edits.reasoning" :input-props="{ id: 'replacement-reasoning' }" type="textarea" :maxlength="10000" :disabled="pending" /></NFormItem>
      <p class="muted">发现标识 {{ finding.finding_key }} · 风险类型 {{ finding.risk_type }} · 主张 {{ finding.claim_code }}</p>
      <p class="muted">沿用原引用 {{ finding.citations.length }} 条 · 证据充分性 {{ finding.evidence_sufficiency }} · 置信度 {{ finding.confidence_level }} · 建议复核 {{ finding.review_recommended ? '是' : '否' }}。引用和结构字段只读。</p>
      <p v-if="!replacement?.success" class="state-note" role="status">标题、摘要和推理不能为空；引用及结构字段必须符合替换稿要求。</p>
    </section>
    <NFormItem label="复核理由" :label-props="{ for: 'review-reason' }"><NInput v-model:value="reason" :input-props="{ id: 'review-reason' }" type="textarea" :maxlength="2000" show-count :disabled="pending" placeholder="说明复核依据（必填）" /></NFormItem>
    <NButton attr-type="submit" type="primary" :loading="pending" :disabled="pending || !valid">提交复核</NButton>
  </form>
</template>
<style scoped>
.replacement-editor { border-left: 3px solid var(--color-primary, #31565a); padding-left: 20px; margin-bottom: 24px; }
</style>
