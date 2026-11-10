<script setup lang="ts">
import { NButton } from 'naive-ui'
import type { Company } from '../../api/schemas'
import { recommendedQuestions } from './questions'

defineProps<{
  companies: Company[]
  companyId: string
  date: string
  busy: boolean
  canCreate: boolean
  sessions: { id: string; companyName: string; date: string }[]
  selectedId?: string
}>()
const emit = defineEmits<{
  company: [id: string]
  date: [value: string]
  select: [id: string]
  new: []
  fill: [question: string]
}>()
</script>

<template>
  <section class="panel assistant-context" aria-label="调查上下文">
    <h2>调查范围</h2>
    <label for="assistant-company">企业</label>
    <select
      id="assistant-company" :value="companyId" :disabled="busy || !canCreate"
      @change="emit('company', ($event.target as HTMLSelectElement).value)">
      <option value="" disabled>选择企业</option>
      <option v-for="company in companies" :key="company.id" :value="company.id">
        {{ company.name }}
      </option>
    </select>
    <label for="assistant-date">基准日</label>
    <input
      id="assistant-date" type="date" :value="date" :disabled="busy || !canCreate"
      @change="emit('date', ($event.target as HTMLInputElement).value)">
    <p v-if="!canCreate" class="muted">可从调查详情打开已有运行。</p>

    <h3>推荐问题</h3>
    <p class="muted">选择后填入输入框。</p>
    <NButton
      v-for="item in recommendedQuestions" :key="item.label" block secondary
      :disabled="busy" @click="emit('fill', item.text)">
{{ item.label }}
</NButton>

    <h3>本页会话</h3>
    <NButton v-if="canCreate" block :disabled="busy" @click="emit('new')">新对话</NButton>
    <div v-if="sessions.length" class="assistant-sessions">
      <button
        v-for="session in sessions" :key="session.id" type="button" class="assistant-session"
        :aria-pressed="session.id === selectedId" :disabled="busy" @click="emit('select', session.id)">
        <span>{{ session.companyName || '已有调查' }}</span>
        <small>{{ session.date }}</small>
      </button>
    </div>
  </section>
</template>

<style scoped>
.assistant-context { display: grid; gap: .55rem; align-content: start; }
.assistant-context h2, .assistant-context h3, .assistant-context p { margin: 0; }
.assistant-context h3 { margin-top: .8rem; }
.assistant-context select, .assistant-context input {
  width: 100%; padding: .5rem; border: 1px solid var(--line, #c5d8cf); border-radius: .35rem;
  background: transparent; color: inherit; font: inherit;
}
.assistant-sessions { display: grid; gap: .35rem; }
.assistant-session {
  display: grid; text-align: left; padding: .55rem; font: inherit; color: inherit;
  border: 1px solid var(--line, #c5d8cf); border-radius: .35rem; background: transparent; cursor: pointer;
}
.assistant-session[aria-pressed="true"] { border-color: #237b66; box-shadow: inset 3px 0 #237b66; }
</style>
