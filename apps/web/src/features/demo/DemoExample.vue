<script setup lang="ts">
import { computed } from 'vue'
import type examples from './examples.json'

type Example = typeof examples[number]
const props = defineProps<{ example: Example; description: string }>()
const liveExample = computed(() => props.example.reuse ?? props.example.id)
</script>

<template>
  <details class="example" name="tracerisk-cases">
    <summary class="example-summary">
      <span class="example-index">{{ example.id }}</span>
      <span class="example-label">
        <strong>{{ example.title }}</strong>
        <span>{{ description }}</span>
      </span>
      <span class="chevron" aria-hidden="true"></span>
    </summary>

    <div class="example-body">
      <p class="scope">{{ example.company }} · 基准日 {{ example.asOfDate }} · {{ example.metric }} {{ example.score }} 分</p>
      <div class="prompt">
        <h3>示例提示词</h3>
        <p>{{ example.question }}</p>
      </div>
      <section v-for="section in example.sections" :key="section.kind">
        <h3>{{ section.title }}</h3>
        <p v-for="(line, index) in section.lines" :key="index">{{ line }}</p>
      </section>
      <section v-if="example.citations.length" class="citations">
        <h3>本次冻结证据（{{ example.citations.length }} 条）</h3>
        <article v-for="citation in example.citations" :key="citation.chunk_id">
          <strong>{{ citation.title }}</strong>
          <p>{{ citation.content }}</p>
          <p class="scope">观察日期 {{ citation.observed_at }} · 来源 {{ citation.source_uri }}</p>
        </article>
      </section>
      <footer class="example-actions">
        <RouterLink :to="{ path: '/assistant', query: { example: liveExample } }">
          {{ example.reuse ? '从综合调查开始追问' : '带入助手试问' }}
        </RouterLink>
        <p v-if="example.reuse" class="scope">发送综合调查后，在同一对话选择「司法追问」。</p>
      </footer>
    </div>
  </details>
</template>

<style scoped>
.example { border-bottom: 1px solid var(--border); }
.example:first-of-type { border-top: 1px solid var(--border); }
.example-summary {
  display: grid;
  grid-template-columns: 2rem minmax(0, 1fr) 1rem;
  align-items: center;
  gap: .55rem;
  min-height: 3rem;
  padding: .5rem .55rem;
  cursor: pointer;
  list-style: none;
}
.example-summary::-webkit-details-marker { display: none; }
.example-summary:hover, .example[open] > .example-summary { background: var(--accent-wash); }
.example-summary:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.example-index { color: var(--accent); font-size: .8rem; font-variant-numeric: tabular-nums; }
.example-label { display: flex; flex-wrap: wrap; align-items: baseline; gap: .15rem .9rem; }
.example-label strong { font-size: .96rem; }
.example-label span { color: var(--ink-soft); font-size: .86rem; }
.chevron { justify-self: center; width: .46rem; height: .46rem; border-right: 1.5px solid var(--accent); border-bottom: 1.5px solid var(--accent); transform: rotate(45deg); }
.example[open] .chevron { transform: rotate(225deg); }
.example-body {
  display: grid;
  gap: .85rem;
  background: var(--panel-bg);
  padding: .95rem clamp(.65rem, 3.5vw, 3.1rem) 1rem;
  font-size: .9rem;
  line-height: 1.6;
}
.example-body h3, .example-body p { margin: 0; }
.example-body h3 { font-size: .91rem; }
.example-body h3 + p, .example-body section p + p { margin-top: .2rem; }
.scope { color: var(--ink-soft); font-size: .81rem; overflow-wrap: anywhere; }
.prompt { padding: .55rem .75rem; border-left: 2px solid var(--accent); background: var(--accent-wash); }
.citations article { margin-top: .45rem; padding: .5rem .65rem; background: var(--shell-bg); overflow-wrap: anywhere; }
.example-actions { display: grid; gap: .25rem; justify-items: start; }
.example-actions a { color: var(--accent); font-weight: 650; text-underline-offset: .2em; }
.example-actions a:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
</style>
