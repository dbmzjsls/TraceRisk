<script setup lang="ts">
import type { RunConnectionStatus } from './useInvestigationEvents'

defineProps<{ status: RunConnectionStatus; pollingFallback: boolean; permissionError: string | null }>()
const emit = defineEmits<{ retry: [] }>()
function retry() { emit('retry') }
</script>

<template>
  <aside class="connection-status" :data-status="status" aria-live="polite">
    <span v-if="status === 'connecting'">正在连接实时事件</span>
    <span v-else-if="status === 'connected'">实时事件已连接</span>
    <span v-else-if="status === 'retrying'">实时连接暂时中断，正在重试<span v-if="pollingFallback">；当前使用状态刷新</span></span>
    <span v-else-if="status === 'ended'">本次运行事件已同步</span>
    <span v-else-if="status === 'unauthorized'">登录已失效，正在返回登录页</span>
    <span v-else-if="status === 'forbidden'">{{ permissionError ?? '当前账号无权查看运行事件' }}</span>
    <span v-else>等待运行信息</span>
    <button v-if="status === 'retrying' || status === 'forbidden'" type="button" class="retry-button" @click="retry">立即重试</button>
  </aside>
</template>

<style scoped>
.connection-status { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: .5rem 1rem; border-radius: 0.5rem; background: var(--source-bg); color: var(--source-text); font-size: 13px; padding: 0.625rem 0.75rem; }
.connection-status[data-status="retrying"], .connection-status[data-status="forbidden"] { background: var(--warning-bg); color: var(--warning); }
.retry-button { border: 0; background: transparent; color: var(--accent); cursor: pointer; font: inherit; }
</style>
