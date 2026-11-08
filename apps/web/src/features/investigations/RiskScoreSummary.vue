<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  score: number
  rules: { rule_code: string; score: number }[]
  coverage: { evidence_sufficiency: string }
  ready: boolean
}>()
const level = computed(() => props.score >= 60 ? '高' : props.score >= 30 ? '中' : '低')
const ruleDefinitions = [
  { code: 'JUDICIAL_OPEN_CASE_V1', label: '司法', condition: '存在未结案件', score: 35 },
  { code: 'BUSINESS_ABNORMAL_V1', label: '经营', condition: '基准日仍有效的经营异常', score: 25 },
  { code: 'NEGATIVE_NEWS_V1', label: '新闻', condition: '基准日前含当日365天内至少两条独立负面报道', score: 20 },
  { code: 'CORE_IP_ANOMALY_V1', label: '知识产权', condition: '核心专利失效或存在权属争议', score: 15 },
]
</script>

<template>
  <section class="panel risk-profile" aria-label="企业风险画像与评分说明">
    <div class="section-kicker">RISK PROFILE</div><h2>企业风险画像</h2>
    <p v-if="!ready" class="state-note">规则结果尚未完整形成，当前分数不可作为最终结论。</p>
    <p v-else>规则分 <strong>{{ score }} / 95</strong> · 演示风险等级：{{ level }}</p>
    <p>证据覆盖：{{ coverage.evidence_sufficiency === 'sufficient' ? '充分' : coverage.evidence_sufficiency === 'insufficient' ? '不足' : '未确认' }}</p>
    <p v-if="coverage.evidence_sufficiency !== 'sufficient'" class="state-note" role="note">来源不足或覆盖未确认；零分不代表企业安全，缺少事实不按负面计分。</p>
    <div class="coverage-grid">
      <article v-for="rule in ruleDefinitions" :key="rule.code" class="coverage-stamp">
        <strong>{{ rule.label }} · {{ ready ? (props.rules.some(item => item.rule_code === rule.code) ? '命中' : '未命中') : '待确认' }}</strong>
        <span>{{ rule.condition }}：+{{ rule.score }} 分</span>
      </article>
    </div>
    <p class="provenance-note">每条规则最多计分一次，总分最高95。重复案件和新闻转载不重复计分；0～29为低、30～59为中、60～95为高。权重为演示配置，不是信用标准或违约概率；AI 与复核人员不能修改规则分。</p>
  </section>
</template>
