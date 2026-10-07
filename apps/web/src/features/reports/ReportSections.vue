<script setup lang="ts">
import type { ReportSnapshot } from '../../api/schemas'
import ReportFindingList from './ReportFindingList.vue'
import HumanReplacementList from '../reviews/HumanReplacementList.vue'

defineProps<{ snapshot: ReportSnapshot }>()
const sourceLabels = { business: '工商', judicial: '司法', news: '新闻', intellectual_property: '知识产权' } as const
</script>

<template>
  <section class="panel dossier-section">
    <div class="section-kicker">FINAL / IMMUTABLE SNAPSHOT</div><h2>最终结果 · 规则评分 {{ snapshot.rule_score }}</h2>
    <p class="availability-label">{{ snapshot.availability === 'ready' ? `${snapshot.snapshot_version === 'stage6-v1' ? 'Stage 6' : 'Stage 5'} 结构化快照可用` : '历史快照：部分 Stage 5 字段不可用' }}</p>
    <p class="synthetic">{{ snapshot.notice }}</p><p>企业：{{ snapshot.company.name }}</p>
    <p>证据充分性：{{ snapshot.coverage.evidence_sufficiency }} · 事实冲突：{{ snapshot.conflicts.length ? '有' : '无' }}</p>
    <p class="provenance-note">最终结果来自不可变快照；规则分只来自确定性规则。</p>
    <p v-if="snapshot.review_reasons.length">复核触发原因：{{ snapshot.review_reasons.join('、') }}</p><ReportFindingList :findings="snapshot.final_findings" />
  </section>
  <section class="panel dossier-section"><div class="section-kicker">DETERMINISTIC / RULE ENGINE</div><h2>确定性规则与分数</h2><article v-for="rule in snapshot.rules" :key="rule.id" class="finding"><h3>{{ rule.rule_code }} · +{{ rule.score }}</h3><p>规则版本 {{ rule.rule_version }}</p><dl class="detail-grid"><template v-for="(value, key) in rule.parameters" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl></article><p v-if="!snapshot.rules.length" class="muted">无确定性规则记录。</p></section>
  <section class="panel dossier-section"><div class="section-kicker">RAW / AI FINDINGS</div><h2>原始 AI 发现（不可变快照）</h2><ReportFindingList :findings="snapshot.ai_findings" /></section>
  <section class="panel dossier-section"><div class="section-kicker">HUMAN / REVIEW DECISIONS</div><h2>人工决定</h2><article v-for="decision in snapshot.human_decisions" :key="decision.id" class="finding"><h3>{{ decision.decision === 'approve' ? '通过' : decision.decision === 'modify' ? '修改' : '驳回' }} · {{ decision.ai_finding_id ? `发现版本 ${decision.finding_version}` : '运行级复核' }}</h3><p>{{ decision.reason }}</p><p class="mono muted">复核人 {{ decision.reviewer_id }}<br>决定 {{ decision.id }}<br>发现 {{ decision.ai_finding_id ?? '无 AI 发现' }}</p></article><p v-if="!snapshot.human_decisions.length" class="muted">无人工决定。</p></section>
  <HumanReplacementList :decisions="snapshot.human_decisions" :interactive="false" />
  <section class="panel dossier-section"><div class="section-kicker">COVERAGE / CONFLICTS</div><h2>来源覆盖与冲突</h2><p class="provenance-note">覆盖与冲突为解释信息，不调整规则分。</p><p v-if="snapshot.availability === 'legacy_unavailable'" class="state-note">历史快照未保存结构化四领域覆盖和冲突明细。</p><template v-else><div class="coverage-grid"><article v-for="entry in snapshot.coverage.entries" :key="entry.source_type" class="coverage-stamp" :data-status="entry.status"><strong>{{ sourceLabels[entry.source_type] }}</strong><span>{{ entry.status }}</span></article></div><ul v-if="snapshot.coverage.insufficiency_details.length" class="compact-list"><li v-for="detail in snapshot.coverage.insufficiency_details" :key="detail">{{ detail }}</li></ul><article v-for="conflict in snapshot.conflicts" :key="`${conflict.risk_type}:${conflict.fact_type}:${conflict.fact_key}`" class="conflict-record"><h3>{{ conflict.fact_type }} / {{ conflict.fact_key }}</h3><p v-for="side in conflict.sides" :key="`${side.source_type}:${side.evidence_id}`">{{ side.source_type }}：{{ side.fact_value }}（{{ side.observed_at }}）</p><p>权威来源 {{ conflict.authority_source ?? '未确定' }} · 解析值 {{ conflict.resolved_value ?? '未确定' }} · {{ conflict.resolution_state }}</p></article><p v-if="!snapshot.conflicts.length" class="muted">未记录事实冲突。</p></template></section>
  <section class="panel dossier-section"><h2>规则证据引用</h2><article v-for="reference in snapshot.evidence_references" :key="`${reference.rule_finding_id}:${reference.evidence_id}`" class="finding"><h3>{{ reference.title ?? '规则证据' }}</h3><p>{{ reference.content ?? '历史快照未保存证据正文。' }}</p><p class="mono muted">规则发现 {{ reference.rule_finding_id }}<br>证据 {{ reference.evidence_id }} · 分块 {{ reference.chunk_id ?? '精确目标不可用' }}<br>哈希 {{ reference.chunk_content_hash ?? reference.content_hash }}</p></article><p v-if="!snapshot.evidence_references.length" class="muted">无规则证据引用。</p></section>
  <section class="panel dossier-section"><h2>检索证据快照</h2><p v-if="snapshot.availability === 'legacy_unavailable'" class="state-note">历史快照的检索证据缺少 Stage 5 来源与排序字段。</p><article v-for="evidence in snapshot.retrieved_evidence" :key="evidence.chunk_id" class="finding"><p>{{ evidence.content }}</p><p class="mono muted">证据 {{ evidence.evidence_id }} · 分块 {{ evidence.chunk_id }}<br>哈希 {{ evidence.content_hash }}</p></article><p v-if="!snapshot.retrieved_evidence.length" class="muted">无检索证据快照。</p></section>
</template>
