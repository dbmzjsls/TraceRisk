<script setup lang="ts">
import { computed } from 'vue'
import type { InvestigationDetail } from '../../api/schemas'
import CitationList, { type CitationEntry } from './CitationList.vue'
import { statusLabel } from './status'
import FindingList from './FindingList.vue'
import RiskScoreSummary from './RiskScoreSummary.vue'
import { failureLabels, modelExecutionLabel, reviewReasonLabels } from './analysisLabels'
import HumanReplacementList from '../reviews/HumanReplacementList.vue'

const props = defineProps<{ investigation: InvestigationDetail; reviewLinks?: boolean; reviewHistoryLinks?: boolean; selectedChunkId?: string | null }>()
defineEmits<{ navigate: [chunkId: string | null, origin: unknown] }>()
const terminal = computed(() => props.investigation.current_run.status === 'completed' || props.investigation.current_run.status === 'failed')
function ruleCitations(rule: InvestigationDetail['rule_findings'][number]): CitationEntry[] {
  return rule.evidence.map(reference => ({ evidence_id: reference.evidence_id, content_hash: reference.content_hash, chunk_id: reference.chunk_id, chunk_content_hash: reference.chunk_content_hash, label: `规则证据 ${reference.evidence_id}` }))
}
</script>

<template>
  <div class="investigation-columns">
    <div class="dossier-column">
      <RiskScoreSummary compact :score="investigation.rule_score" :rules="investigation.rule_findings" :coverage="investigation.coverage" :ready="investigation.steps.some(step => step.name === 'apply_rules')" />
      <details class="panel rule-details">
        <summary>规则命中明细（{{ investigation.rule_findings.length }}）</summary>
        <article v-for="rule in investigation.rule_findings" :key="rule.id" class="finding rule-record">
          <h3>{{ rule.rule_code }} · +{{ rule.score }}</h3>
          <p>规则版本 {{ rule.rule_version }}</p>
          <dl class="detail-grid"><template v-for="(value, key) in rule.parameters" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl>
          <CitationList :citations="ruleCitations(rule)" unavailable-text="历史规则引用没有精确分块" @navigate="(chunkId, origin) => $emit('navigate', chunkId, origin)" />
        </article>
        <p v-if="!investigation.rule_findings.length" class="muted">暂无确定性规则命中记录。</p>
      </details>
    </div>
    <section class="provenance-wrapper" aria-labelledby="ai-result-heading">
      <FindingList :findings="investigation.ai_findings" heading-id="ai-result-heading" :review-links="reviewLinks" :selected-chunk-id="selectedChunkId" @navigate="(chunkId, origin) => $emit('navigate', chunkId, origin)" />
    </section>
    <slot name="timeline" />
  </div>
  <section class="panel dossier-section final-result" aria-labelledby="final-result-heading">
    <h2 id="final-result-heading">最终结果</h2>
    <template v-if="investigation.current_run.status === 'completed'">
      <p>调查已完成，最终规则评分为 <strong>{{ investigation.rule_score }}</strong>。</p>
      <RouterLink v-if="investigation.current_run.report_id" :to="`/reports/${investigation.current_run.report_id}`">查看不可变风险报告</RouterLink>
    </template>
    <p v-else-if="investigation.current_run.status === 'failed'" class="state-note">运行失败，未形成完整最终结果。</p>
    <p v-else class="state-note" role="status">最终结果尚未形成；当前状态为 {{ statusLabel(investigation.current_run.status) }}。</p>
    <p class="provenance-note">最终结果来自持久化运行记录；规则分只由确定性规则组成。</p>
  </section>

  <section class="panel dossier-section" aria-labelledby="human-decision-heading">
    <h2 id="human-decision-heading">人工决定</h2>
    <article v-for="decision in investigation.decisions" :key="decision.id" class="finding">
      <h3>{{ decision.decision === 'approve' ? '通过' : decision.decision === 'modify' ? '修改' : '驳回' }} · {{ decision.ai_finding_id ? `发现版本 ${decision.finding_version}` : '运行级复核' }}</h3>
      <p>{{ decision.reason }}</p>
      <RouterLink
v-if="reviewHistoryLinks && decision.ai_finding_id"
        :to="{ path: `/reviews/${decision.ai_finding_id}`, query: { investigation: investigation.id } }">
查看复核记录
</RouterLink>
      <p class="mono muted">复核人 {{ decision.reviewer_id }}<br>决定 {{ decision.id }}<br>发现 {{ decision.ai_finding_id ?? '无 AI 发现' }}</p>
    </article>
    <p v-if="!investigation.decisions.length" class="muted">{{ terminal ? '没有人工决定。' : '人工决定尚未形成。' }}</p>
  </section>
  <HumanReplacementList :decisions="investigation.decisions" @navigate="(chunkId, origin) => $emit('navigate', chunkId, origin)" />
  <section class="panel dossier-section">
    <h2>分析方式与复核原因</h2>
    <p>{{ modelExecutionLabel(investigation.model_execution) }}</p>
    <ul v-if="investigation.review_reasons.length"><li v-for="reason in investigation.review_reasons" :key="reason">{{ reviewReasonLabels[reason] ?? reason }}</li></ul>
    <p v-else class="muted">{{ investigation.retrieval_state === 'ready' ? '当前未记录复核触发原因。' : '分析尚未完成，复核原因待确认。' }}</p>
    <p v-if="investigation.current_run.status === 'failed'" class="state-note error-note">本次调查失败，不能通过人工复核绕过校验。请检查模型配置、调用状态及引用是否属于本次冻结证据，再重新发起调查。</p>
    <p v-if="investigation.current_run.status === 'failed'">{{ failureLabels[investigation.failure_code ?? 'workflow_error'] ?? investigation.failure_code }}</p>
    <p v-if="investigation.model_execution.error_categories.length">调用失败类别：{{ investigation.model_execution.error_categories.join('、') }}</p>
    <RouterLink v-if="investigation.current_run.status === 'failed'" :to="`/companies/${investigation.company_id}`">返回企业重新发起调查</RouterLink>
  </section>
</template>

<style scoped>
.investigation-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) minmax(0, .95fr);
  align-items: start;
  gap: var(--panel-gap);
}
.rule-details summary { cursor: pointer; font-weight: 600; }
@media (max-width: 1050px) {
  .investigation-columns { grid-template-columns: minmax(0, 1fr) minmax(0, 1.75fr); }
  :slotted(.timeline-column) { grid-column: 1 / -1; }
}
@media (max-width: 767px) {
  .investigation-columns { grid-template-columns: minmax(0, 1fr); }
}
</style>
