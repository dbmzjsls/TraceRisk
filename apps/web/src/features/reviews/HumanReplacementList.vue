<script setup lang="ts">
import { computed } from 'vue'
import { humanReplacementSchema, type Decision } from '../../api/schemas'
import CitationList from '../investigations/CitationList.vue'
const props = defineProps<{ decisions: readonly Decision[]; interactive?: boolean }>()
defineEmits<{ navigate: [chunkId: string | null, origin: unknown] }>()
const versions = computed(() => props.decisions.flatMap(decision => {
  if (decision.decision !== 'modify' || !decision.replacement || !decision.human_finding_version_id) return []
  const content = humanReplacementSchema.parse(decision.replacement)
  return [{ id: decision.human_finding_version_id, version: decision.human_version, originalId: decision.ai_finding_id, content }]
}))
</script>
<template>
  <section class="panel dossier-section" aria-labelledby="human-replacement-heading">
    <div class="section-kicker">HUMAN / REPLACEMENT VERSIONS</div>
    <h2 id="human-replacement-heading">人工替换版本</h2>
    <p class="provenance-note">人工版本独立保存；原 AI 发现完整保留，最终结果采用复核后的版本。</p>
    <article v-for="entry in versions" :key="entry.id" class="finding">
      <h3>{{ entry.content.title }} · 人工版本 {{ entry.version }}</h3>
      <p class="muted">{{ entry.content.risk_type }} · {{ entry.content.claim_code }} · 证据充分性 {{ entry.content.evidence_sufficiency }} · 置信度 {{ entry.content.confidence_level }} · 建议复核 {{ entry.content.review_recommended ? '是' : '否' }}</p>
      <p>支持状态 {{ entry.content.support_status }} · {{ entry.content.support_reason }}</p>
      <p>{{ entry.content.summary }}</p><p>{{ entry.content.reasoning }}</p>
      <p class="mono muted">人工版本 {{ entry.id }}<br>原 AI 发现 {{ entry.originalId }}</p>
      <CitationList :citations="entry.content.citations" :interactive="interactive ?? true" @navigate="(chunkId, origin) => $emit('navigate', chunkId, origin)" />
    </article>
    <p v-if="!versions.length" class="muted">无人工替换版本。</p>
  </section>
</template>
