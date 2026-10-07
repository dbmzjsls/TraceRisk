<script setup lang="ts">
import { computed } from 'vue'
import { NAlert, NButton, NSpin } from 'naive-ui'
import { ApiError } from '../api/client'
const props = defineProps<{ loading?: boolean; refreshing?: boolean; error?: Error | null; empty?: boolean; emptyText?: string }>()
defineEmits<{ refresh: [] }>()
const title = computed(() => {
  const error = props.error
  if (!(error instanceof ApiError)) return '读取失败'
  return ({ 403: '无权访问', 404: '记录不存在或尚未生成', 409: '状态已变化，请刷新后确认', 422: '请求内容无效' } as Record<number, string>)[error.status ?? 0] ?? '读取失败'
})
</script>
<template>
  <NSpin v-if="loading" size="small"><span role="status">正在读取服务数据…</span></NSpin>
  <p v-else-if="refreshing" class="query-refresh" role="status">正在更新，当前内容仍可查看…</p>
  <NAlert v-if="error" type="error" :title="title" role="alert">
    {{ error.message }} <NButton size="small" @click="$emit('refresh')">重新读取</NButton>
  </NAlert>
  <p v-else-if="empty && !loading" class="panel muted" role="status">{{ emptyText ?? '暂无记录。' }}</p>
</template>
