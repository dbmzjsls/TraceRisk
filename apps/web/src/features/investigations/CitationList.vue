<script setup lang="ts">
export interface CitationEntry {
  evidence_id: string
  content_hash: string
  chunk_id: string | null
  chunk_content_hash?: string | null
  quoted_excerpt?: string
  label?: string
}

withDefaults(defineProps<{ citations: readonly CitationEntry[]; unavailableText?: string; interactive?: boolean; selectedChunkId?: string | null; citationNumbers?: ReadonlyMap<string, number> }>(), { interactive: true, unavailableText: '精确证据不可用', selectedChunkId: null, citationNumbers: undefined })
defineEmits<{ navigate: [chunkId: string | null, origin: unknown] }>()
</script>

<template>
  <div class="citation-list">
    <blockquote v-for="(citation, index) in citations" :key="`${citation.evidence_id}:${citation.chunk_id ?? index}`" class="citation">
      <span v-if="citation.chunk_id && citationNumbers?.has(citation.chunk_id)" class="source-marker">引用 {{ citationNumbers.get(citation.chunk_id) }}</span>
      <p v-if="citation.quoted_excerpt">{{ citation.quoted_excerpt }}</p>
      <p v-else-if="citation.label">{{ citation.label }}</p>
      <footer class="mono muted">证据 {{ citation.evidence_id }}<br>分块 {{ citation.chunk_id ?? '精确目标不可用' }}<br>哈希 {{ citation.chunk_content_hash ?? citation.content_hash }}</footer>
      <button
        v-if="interactive"
        type="button"
        class="citation-trigger"
        :aria-current="citation.chunk_id && citation.chunk_id === selectedChunkId ? 'true' : undefined"
        :data-citation-target="citation.chunk_id ?? 'unavailable'"
        @click="$emit('navigate', citation.chunk_id, $event.currentTarget)"
      >
        {{ citation.chunk_id ? '定位精确证据' : (unavailableText ?? '精确证据不可用') }}
      </button>
      <p v-else class="citation-availability muted">{{ citation.chunk_id ? '已保留精确分块标识；此不可变报告不提供页面内定位' : (unavailableText ?? '精确证据不可用') }}</p>
    </blockquote>
  </div>
</template>
