<script setup lang="ts">
import { computed } from 'vue'
import type { InvestigationDetail } from '../../api/schemas'
import CitationList, { type CitationEntry } from './CitationList.vue'
import FindingList from './FindingList.vue'
import HumanReplacementList from '../reviews/HumanReplacementList.vue'

const props = defineProps<{ investigation: InvestigationDetail; reviewLinks?: boolean }>()
defineEmits<{ navigate: [chunkId: string | null, origin: unknown] }>()
const terminal = computed(() => props.investigation.current_run.status === 'completed' || props.investigation.current_run.status === 'failed')
function ruleCitations(rule: InvestigationDetail['rule_findings'][number]): CitationEntry[] {
  return rule.evidence.map(reference => ({ evidence_id: reference.evidence_id, content_hash: reference.content_hash, chunk_id: reference.chunk_id, chunk_content_hash: reference.chunk_content_hash, label: `规则证据 ${reference.evidence_id}` }))
}
</script>

<template>
  <section class="panel dossier-section final-result" aria-labelledby="final-result-heading">
    <div class="section-kicker">FINAL / DERIVED RESULT</div>
    <h2 id="final-result-heading">最终结果</h2>
    <template v-if="investigation.current_run.status === 'completed'">
      <p>调查已完成，最终规则评分为 <strong>{{ investigation.rule_score }}</strong>。</p>
      <RouterLink v-if="investigation.current_run.report_id" :to="`/reports/${investigation.current_run.report_id}`">查看不可变风险报告</RouterLink>
    </template>
    <p v-else-if="investigation.current_run.status === 'failed'" class="state-note">运行失败，未形成完整最终结果。</p>
    <p v-else class="state-note" role="status">最终结果尚未形成；当前状态为 {{ investigation.current_run.status }}。</p>
    <p class="provenance-note">最终结果来自持久化运行记录；规则分只由确定性规则组成。</p>
  </section>

  <section class="panel dossier-section" aria-labelledby="rule-result-heading">
    <div class="section-kicker">DETERMINISTIC / RULE ENGINE</div>
    <h2 id="rule-result-heading">确定性规则 · {{ investigation.rule_score }} 分</h2>
    <p class="provenance-note">确定性规则是唯一计分来源。</p>
    <article v-for="rule in investigation.rule_findings" :key="rule.id" class="finding rule-record">
      <h3>{{ rule.rule_code }} · +{{ rule.score }}</h3>
      <p>规则版本 {{ rule.rule_version }}</p>
      <dl class="detail-grid"><template v-for="(value, key) in rule.parameters" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl>
      <CitationList :citations="ruleCitations(rule)" unavailable-text="历史规则引用没有精确分块" @navigate="(chunkId, origin) => $emit('navigate', chunkId, origin)" />
    </article>
    <p v-if="!investigation.rule_findings.length" class="muted">暂无确定性规则命中记录。</p>
  </section>

  <section class="provenance-wrapper" aria-labelledby="ai-result-heading">
    <div class="section-kicker">RAW / AI FINDINGS</div>
    <h2 id="ai-result-heading" class="visually-contained-heading">原始 AI</h2>
    <FindingList :findings="investigation.ai_findings" :review-links="reviewLinks" @navigate="(chunkId, origin) => $emit('navigate', chunkId, origin)" />
  </section>

  <section class="panel dossier-section" aria-labelledby="human-decision-heading">
    <div class="section-kicker">HUMAN / REVIEW DECISIONS</div>
    <h2 id="human-decision-heading">人工决定</h2>
    <article v-for="decision in investigation.decisions" :key="decision.id" class="finding">
      <h3>{{ decision.decision === 'approve' ? '通过' : decision.decision === 'modify' ? '修改' : '驳回' }} · {{ decision.ai_finding_id ? `发现版本 ${decision.finding_version}` : '运行级复核' }}</h3>
      <p>{{ decision.reason }}</p>
      <p class="mono muted">复核人 {{ decision.reviewer_id }}<br>决定 {{ decision.id }}<br>发现 {{ decision.ai_finding_id ?? '无 AI 发现' }}</p>
    </article>
    <p v-if="!investigation.decisions.length" class="muted">{{ terminal ? '没有人工决定。' : '人工决定尚未形成。' }}</p>
  </section>
  <HumanReplacementList :decisions="investigation.decisions" @navigate="(chunkId, origin) => $emit('navigate', chunkId, origin)" />
</template>
