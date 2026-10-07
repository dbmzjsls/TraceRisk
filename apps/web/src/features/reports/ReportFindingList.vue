<script setup lang="ts">
import { citationSchema, type ReportFinding } from '../../api/schemas'
import CitationList, { type CitationEntry } from '../investigations/CitationList.vue'
defineProps<{ findings: readonly ReportFinding[] }>()
function citationsFor(finding: ReportFinding): CitationEntry[] { return finding.citations.map(citation => citationSchema.parse(citation)) }
</script>
<template>
  <article v-for="finding in findings" :key="finding.id" class="finding"><h3>{{ finding.title }} · 版本 {{ finding.version }}</h3><p v-if="finding.source" class="provenance-note">来源：{{ finding.source === 'human' ? `人工替换版本 ${finding.human_version}` : 'AI 发现' }} · 原 AI 发现 {{ finding.original_ai_finding_id }}</p><p class="muted">{{ finding.risk_type }} · {{ finding.claim_code }} · 证据充分性 {{ finding.evidence_sufficiency }} · 支持状态 {{ finding.support_status }}（{{ finding.support_reason }}）· 置信度 {{ finding.confidence_level }} · 建议复核 {{ finding.review_recommended ? '是' : '否' }}</p><p>{{ finding.summary }}</p><p>{{ finding.reasoning }}</p><CitationList :citations="citationsFor(finding)" unavailable-text="报告引用没有可用的页面内目标" :interactive="false" /></article>
  <p v-if="!findings.length" class="muted">无发现记录。</p>
</template>
