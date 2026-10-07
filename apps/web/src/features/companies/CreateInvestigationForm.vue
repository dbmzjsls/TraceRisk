<script setup lang="ts">
import { shallowRef } from 'vue'
import { NButton, NFormItem, NInput } from 'naive-ui'
defineProps<{ pending: boolean }>()
const emit = defineEmits<{ submit: [date: string] }>()
const date = shallowRef('2026-09-11')
function submit() { if (/^\d{4}-\d{2}-\d{2}$/.test(date.value)) emit('submit', date.value) }
</script>
<template>
  <form class="panel form-panel" @submit.prevent="submit">
    <h2>发起调查</h2><p class="muted">按所选日期分析现有证据，完成后查看风险报告。</p>
    <NFormItem label="基准日期" :label-props="{ for: 'as-of-date' }"><NInput v-model:value="date" :input-props="{ id: 'as-of-date', type: 'date' }" :disabled="pending" placeholder="YYYY-MM-DD" /></NFormItem>
    <NButton attr-type="submit" type="primary" :loading="pending" :disabled="pending || !/^\d{4}-\d{2}-\d{2}$/.test(date)">创建调查</NButton>
  </form>
</template>
