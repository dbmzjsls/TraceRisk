<script setup lang="ts">
import { computed } from 'vue'
import type { AssistantAnswer } from '../../api/schemas'
import { modelExecutionLabel, reviewReasonLabels } from '../investigations/analysisLabels'

const props = defineProps<{ answer: AssistantAnswer; company: string; date: string }>()
const emit = defineEmits<{ navigate: [chunk: string, origin: unknown] }>()
const explanation = computed(() => props.answer.assistant_execution)
const reviewLabels = computed(() => props.answer.review_reasons.map(reason => reviewReasonLabels[reason] ?? reason))
const evidenceLabels = computed(() => new Map(props.answer.citations.map(item => [item.chunk_id, item.title])))
</script>

<template>
  <section class="assistant-answer" aria-label="调查助手回答">
    <header class="assistant-answer-heading">
      <strong>调查助手</strong>
      <span>{{ company }} · {{ date }}</span>
      <span class="provenance-stamp">{{ answer.run_status === 'awaiting_review' ? '待人工复核' : '已完成' }}</span>
      <strong v-if="answer.status === 'answer'">规则分 {{ answer.rule_score }}</strong>
    </header>

    <div v-if="answer.citations.length" class="assistant-citations" aria-label="本次回答引用">
      <button
        v-for="item in answer.citations" :key="item.chunk_id" type="button" class="text-action"
        :data-citation-chunk="item.chunk_id" @click="emit('navigate', item.chunk_id, $event.currentTarget)">
        引用 · {{ item.title }}
      </button>
    </div>

    <div v-for="section in answer.sections" :key="section.kind" class="assistant-section" :data-section="section.kind">
      <details v-if="section.kind === 'findings' || section.kind === 'original_ai' || section.kind === 'evidence'">
        <summary>{{ section.title }}</summary>
        <p v-for="(line, index) in section.lines" :key="index">{{ line }}</p>
        <div class="assistant-citations">
          <button
            v-for="chunk in section.citation_chunk_ids" :key="chunk" type="button" class="text-action"
            @click="emit('navigate', chunk, $event.currentTarget)">
{{ evidenceLabels.get(chunk) }}
</button>
        </div>
      </details>
      <template v-else>
        <h3>{{ section.title }}</h3>
        <p v-for="(line, index) in section.lines" :key="index">{{ line }}</p>
      </template>
    </div>
    <details class="assistant-provenance">
      <summary>模型来源与复核原因</summary>
      <p>调查生成模式 · {{ modelExecutionLabel(answer.investigation_model_execution) }}</p>
      <p>对话解释模式 · {{ explanation.mode === 'real' ? explanation.model : 'Mock 模拟模型' }}</p>
      <p>{{ explanation.prompt_version }} · {{ explanation.latency_ms }} ms</p>
      <p v-if="explanation.usage">输入 {{ explanation.usage.prompt_tokens ?? '未记录' }} · 输出 {{ explanation.usage.completion_tokens ?? '未记录' }} token</p>
      <p v-else>用量未记录</p>
      <p v-if="reviewLabels.length">{{ reviewLabels.join('、') }}</p>
    </details>
    <RouterLink v-if="answer.report_id" :to="'/reports/' + answer.report_id">查看已保存报告</RouterLink>
  </section>
</template>

<style scoped>
.assistant-answer { display: grid; gap: .7rem; overflow-wrap: anywhere; }
.assistant-answer-heading, .assistant-citations { display: flex; flex-wrap: wrap; gap: .55rem 1rem; align-items: center; }
.assistant-answer-heading { border-bottom: 1px solid var(--line, #c5d8cf); padding-bottom: .55rem; }
.assistant-section h3, .assistant-section p, .assistant-provenance p { margin: .16rem 0; }
.assistant-section h3, .assistant-section summary { font-size: .95rem; font-weight: 650; }
.assistant-section summary, .assistant-provenance summary { cursor: pointer; }
</style>
