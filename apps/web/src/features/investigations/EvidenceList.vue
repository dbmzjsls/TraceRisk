<script setup lang="ts">
import { computed } from 'vue'
import type { InvestigationDetail } from '../../api/schemas'
import { evidenceElementId } from './useEvidenceNavigation'

const props = defineProps<{
  evidence: InvestigationDetail['retrieved_evidence']
  retrievalState: InvestigationDetail['retrieval_state']
  activeChunkId?: string | null
  selectedChunkId?: string | null
  canReturn?: boolean
}>()
defineEmits<{ return: [] }>()

const orderedEvidence = computed(() => [...props.evidence].sort((left, right) => left.risk_type.localeCompare(right.risk_type) || left.rank - right.rank))
</script>

<template>
  <section class="panel dossier-section evidence-section" aria-labelledby="evidence-heading">
    <div class="section-kicker">FROZEN SOURCE FILE</div>
    <h2 id="evidence-heading">检索证据</h2>
    <p v-if="retrievalState === 'pending'" class="state-note" role="status">检索尚未完成，证据清单等待生成。</p>
    <p v-else-if="retrievalState === 'legacy_unavailable'" class="state-note">历史运行没有可定位的冻结检索证据。</p>
    <p v-else-if="!orderedEvidence.length" class="state-note">检索已完成，本次运行没有返回证据分块。</p>
    <article
      v-for="item in orderedEvidence"
      :id="evidenceElementId(item.chunk_id)"
      :key="item.chunk_id"
      class="evidence-card"
      :class="{ 'citation-highlight': activeChunkId === item.chunk_id }"
      :data-chunk-id="item.chunk_id"
      tabindex="-1"
    >
      <header><span class="provenance-stamp">{{ item.risk_type }}</span><span>排名 {{ item.rank }}</span></header>
      <h3>{{ item.title }}</h3>
      <p>{{ item.content }}</p>
      <dl class="evidence-facts"><dt>观察日期</dt><dd>{{ item.observed_at }}</dd><dt>事实</dt><dd>{{ item.fact_type }} / {{ item.fact_key }} = {{ item.fact_value }} · {{ item.polarity }}</dd></dl>
      <p class="mono muted">来源 {{ item.source_uri }}<br>证据 {{ item.evidence_id }} · 分块 {{ item.chunk_id }}<br>哈希 {{ item.content_hash }}</p>
      <button v-if="canReturn && selectedChunkId === item.chunk_id" type="button" class="text-action" @click="$emit('return')">返回引用位置</button>
    </article>
  </section>
</template>
