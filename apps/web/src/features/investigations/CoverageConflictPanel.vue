<script setup lang="ts">
import type { InvestigationDetail } from '../../api/schemas'

defineProps<{
  retrievalState: InvestigationDetail['retrieval_state']
  coverage: InvestigationDetail['coverage']
  conflicts: InvestigationDetail['conflicts']
}>()

const sourceLabels = { business: '工商', judicial: '司法', news: '新闻', intellectual_property: '知识产权' } as const
const statusLabels = { available: '可用', unavailable: '不可用', stale: '已过期', missing: '缺失' } as const
</script>

<template>
  <section class="panel dossier-section" aria-labelledby="coverage-heading">
    <div class="section-kicker">PROVENANCE / COVERAGE</div>
    <h2 id="coverage-heading">来源覆盖与事实冲突</h2>
    <p class="provenance-note">覆盖与冲突仅解释证据质量，不调整规则分。</p>
    <p v-if="retrievalState === 'pending'" class="state-note" role="status">检索尚未完成，来源覆盖等待生成。</p>
    <p v-else-if="retrievalState === 'legacy_unavailable'" class="state-note">历史运行未保存结构化来源覆盖与冲突数据。</p>
    <template v-else>
      <div class="coverage-grid" aria-label="四领域来源覆盖">
        <article v-for="entry in coverage.entries" :key="entry.source_type" class="coverage-stamp" :data-status="entry.status">
          <strong>{{ sourceLabels[entry.source_type] }}</strong>
          <span>{{ statusLabels[entry.status] }}</span>
          <small>{{ entry.checked_at ? `核验 ${entry.checked_at}` : '无核验时间' }}</small>
        </article>
      </div>
      <p class="evidence-sufficiency">证据充分性：<strong>{{ coverage.evidence_sufficiency }}</strong></p>
      <ul v-if="coverage.insufficiency_details.length" class="compact-list">
        <li v-for="detail in coverage.insufficiency_details" :key="detail">{{ detail }}</li>
      </ul>
      <p v-else class="muted">没有来源不足说明。</p>
      <div class="conflict-list">
        <article v-for="conflict in conflicts" :key="`${conflict.risk_type}:${conflict.fact_type}:${conflict.fact_key}`" class="conflict-record">
          <h3>{{ conflict.risk_type }} · {{ conflict.fact_type }} / {{ conflict.fact_key }}</h3>
          <div class="conflict-sides">
            <div v-for="side in conflict.sides" :key="`${side.source_type}:${side.evidence_id}`">
              <span class="provenance-stamp">{{ side.source_type }}</span>
              <strong>{{ side.fact_value }}</strong>
              <small>{{ side.observed_at }}</small>
            </div>
          </div>
          <p>权威来源：{{ conflict.authority_source ?? '未确定' }} · 解析值：{{ conflict.resolved_value ?? '未确定' }} · {{ conflict.resolution_state === 'resolved' ? '已解决' : '未解决' }}</p>
        </article>
        <p v-if="!conflicts.length" class="muted">未记录事实冲突。</p>
      </div>
    </template>
  </section>
</template>
