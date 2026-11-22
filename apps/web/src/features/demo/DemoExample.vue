<script setup lang="ts">
import type publicCases from './publicCases.json'

defineProps<{ example: typeof publicCases[number] }>()
</script>

<template>
  <details class="example" name="tracerisk-cases">
    <summary class="example-summary">
      <span class="example-label">
        <strong>{{ example.title }}</strong>
        <span>{{ example.company }} · <time :datetime="example.publishedAt">{{ example.publishedAt }}</time></span>
      </span>
      <span class="chevron" aria-hidden="true"></span>
    </summary>

    <div class="example-body">
      <p>{{ example.description }}</p>
      <section class="citations">
        <h3>公告摘录</h3>
        <article v-for="quote in example.quotes" :key="quote.id">
          <p>{{ quote.text }}</p>
          <p class="scope">{{ quote.page }}</p>
        </article>
      </section>
      <a :href="example.url" target="_blank" rel="noopener noreferrer">查看公告原文 · {{ example.announcementNo }}</a>
    </div>
  </details>
</template>

<style scoped>
.example { margin-top: 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel-bg); }
.example-summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 1rem;
  align-items: center;
  gap: .55rem;
  min-height: 3rem;
  padding: .7rem 1rem; border-radius: inherit;
  cursor: pointer;
  list-style: none;
}
.example-summary::-webkit-details-marker { display: none; }
.example-summary:hover, .example[open] > .example-summary { background: var(--accent-wash); }
.example-summary:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
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
.citations article .scope { margin-top: .35rem; }
.example-body a { color: var(--accent); font-weight: 600; text-underline-offset: .2em; }
.scope { color: var(--ink-soft); font-size: .81rem; overflow-wrap: anywhere; }
.citations h3 { color: var(--source-text); }
.citations article { margin-top: .45rem; padding: .5rem .65rem; border-left: 2px solid var(--source-text); background: var(--source-bg); overflow-wrap: anywhere; }
</style>
