<script setup lang="ts">
import { citationSchema, type PendingFinding } from '../../api/schemas'
import type { CitationEntry } from './CitationList.vue'
import CitationList from './CitationList.vue'
defineProps<{ findings: PendingFinding[]; reviewLinks?: boolean }>()
defineEmits<{ navigate: [chunkId: string | null, origin: unknown] }>()
function citationsFor(finding: PendingFinding): CitationEntry[] { return finding.citations.map(citation => citationSchema.parse(citation)) }
</script>
<template>
  <section class="panel">
    <h2>原始 AI 发现</h2><p class="muted">AI 发现与人工决定分别保留，不增加规则分数。</p>
    <article v-for="finding in findings" :key="finding.finding_id" class="finding">
      <h3>{{ finding.title }} <span class="muted">版本 {{ finding.finding_version }}</span></h3><p class="muted">{{ finding.risk_type }} · {{ finding.claim_code }} · 证据充分性 {{ finding.evidence_sufficiency }} · 置信度 {{ finding.confidence_level }} · 建议复核 {{ finding.review_recommended ? '是' : '否' }}</p>
      <p class="support-line">支持状态 {{ finding.support_status }} · {{ finding.support_reason }}</p>
      <p>{{ finding.summary }}</p><p>{{ finding.reasoning }}</p><CitationList :citations="citationsFor(finding)" @navigate="(chunkId, origin) => $emit('navigate', chunkId, origin)" />
      <RouterLink v-if="reviewLinks" :to="`/reviews/${finding.finding_id}`">复核此发现</RouterLink>
    </article>
    <p v-if="!findings.length" class="muted">本次运行暂无 AI 发现。</p>
  </section>
</template>
