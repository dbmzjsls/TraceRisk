<script setup lang="ts">
import DemoExample from './DemoExample.vue'
import examples from './examples.json'

const caseDescriptions: Record<string, string> = {
  '01': '汇总四类风险线索与规则命中',
  '02': '查看资料缺口如何影响判断',
  '03': '在同一次调查中查证司法得分',
}

const features = [
  { title: '规则得分', description: '按企业和基准日查看工商、司法、舆情与知识产权规则。' },
  { title: '证据追溯', description: '点击回答里的引用，核对证据原文、来源和观察日期。' },
  { title: '资料缺口', description: '标出缺失或过期的资料，并列出下一步核查事项。' },
]
</script>

<template>
  <div class="demo-page">
    <div class="demo-content">
      <header class="demo-header">
        <strong>TraceRisk</strong>
        <nav aria-label="首页导航">
          <a href="#overview">项目介绍</a>
          <a href="#features">主要功能</a>
          <a href="#examples">案例</a>
        </nav>
      </header>

      <main>
        <section id="overview" class="intro" aria-labelledby="demo-title">
          <p class="kicker">项目介绍</p>
          <h1 id="demo-title">中小企业授信前调查助手</h1>
          <p class="description">面向金融机构调查人员，在同一次调查中查看规则得分、追溯证据，并确认还需要补查哪些资料。</p>
          <div class="intro-actions">
            <RouterLink class="primary-action" to="/assistant">打开调查助手 <span aria-hidden="true">↗</span></RouterLink>
            <a class="secondary-action" href="#examples">浏览案例 <span aria-hidden="true">↓</span></a>
          </div>
        </section>

        <section id="features" class="feature-section" aria-labelledby="features-title">
          <h2 id="features-title">主要功能</h2>
          <ul class="feature-list">
            <li v-for="feature in features" :key="feature.title">
              <h3>{{ feature.title }}</h3>
              <p>{{ feature.description }}</p>
            </li>
          </ul>
        </section>

        <section id="examples" class="examples" aria-labelledby="examples-title">
          <div class="section-heading">
            <h2 id="examples-title">调查案例</h2>
            <p>比赛示例资料</p>
          </div>
          <DemoExample
            v-for="example in examples"
            :key="example.id"
            :example="example"
            :description="caseDescriptions[example.id] ?? ''"
          />
        </section>
      </main>
    </div>
  </div>
</template>

<style scoped>
.demo-page { height: 100%; overflow-y: auto; background: var(--shell-bg); color: var(--text); }
.demo-content { max-width: 1220px; margin: 0 auto; padding: 0 clamp(1rem, 4vw, 3rem) 2rem; }
.demo-header { display: flex; align-items: center; justify-content: space-between; gap: .75rem 1.25rem; min-height: 3.6rem; border-bottom: 1px solid var(--border); }
.demo-header strong { font-size: 1.15rem; }
.demo-header nav { display: flex; flex-wrap: wrap; gap: .45rem 1.25rem; }
.demo-header a { color: var(--ink-soft); font-size: .87rem; text-decoration: none; }
.demo-header a:hover { color: var(--accent); text-decoration: underline; text-underline-offset: .25em; }
.demo-content a:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; }
main > section { scroll-margin-top: .75rem; }
.intro { padding-block: clamp(1.5rem, 3.6vw, 2.7rem) 1.75rem; }
.intro p, h2, h3 { margin: 0; }
.kicker { color: var(--accent); font-weight: 650; font-size: .86rem; }
.intro h1 { margin: .35rem 0 .6rem; font-size: clamp(1.85rem, 4.2vw, 3.5rem); line-height: 1.16; letter-spacing: -.025em; }
.description { max-width: 39rem; color: var(--ink-soft); line-height: 1.65; font-size: clamp(.95rem, 1.7vw, 1.07rem); }
.intro-actions { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem 1.2rem; margin-top: 1rem; }
.intro-actions a { display: inline-flex; align-items: center; gap: .45rem; min-height: 2.55rem; font-size: .9rem; font-weight: 650; }
.primary-action { padding-inline: .85rem; border-radius: .3rem; background: var(--accent); color: var(--panel-bg); text-decoration: none; }
.primary-action:hover { filter: brightness(.94); }
.secondary-action { color: var(--accent); text-underline-offset: .25em; }
.feature-section { padding-block: 1rem; border-block: 1px solid var(--border); }
.demo-content h2 { font-size: 1.15rem; }
.feature-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .75rem 1.5rem; list-style: none; margin: .75rem 0 0; padding: 0; }
.feature-list h3 { font-size: .95rem; }
.feature-list p { margin: .25rem 0 0; color: var(--ink-soft); font-size: .87rem; line-height: 1.55; }
.examples { margin-top: 1.25rem; }
.section-heading { display: flex; align-items: baseline; justify-content: space-between; gap: .5rem; margin-bottom: .55rem; }
.section-heading p { margin: 0; color: var(--ink-soft); font-size: .82rem; }
@media (max-width: 620px) {
  .feature-list { grid-template-columns: 1fr; gap: .5rem; }
  .feature-list li { display: grid; grid-template-columns: 4.7rem minmax(0, 1fr); gap: .5rem; }
  .feature-list p { margin: 0; }
  .demo-header nav { gap: .45rem .8rem; }
}
</style>
