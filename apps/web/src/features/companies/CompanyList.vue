<script setup lang="ts">
import type { Company } from '../../api/schemas'
defineProps<{ items: Company[] }>()
</script>
<template>
  <div class="record-list">
    <article v-for="company in items" :key="company.id" class="panel record-row">
      <div><RouterLink :to="`/companies/${company.id}`" class="record-title">{{ company.name }}</RouterLink><p class="muted">统一社会信用代码：{{ company.unified_credit_code }}</p></div>
      <span v-if="company.is_synthetic" class="synthetic">合成企业</span>
    </article>
    <p v-if="!items.length" class="panel muted">暂无企业记录。</p>
  </div>
</template>
