<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import QueryFeedback from '../../ui/QueryFeedback.vue'
import { useAuthStore } from '../auth/store'
import { useCreateInvestigation } from '../investigations/useInvestigations'
import { useCompany } from './useCompanies'
import CreateInvestigationForm from './CreateInvestigationForm.vue'
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { data, isPending, isFetching, error, refetch } = useCompany(() => String(route.params.id))
const creation = useCreateInvestigation()
const canCreate = computed(() => auth.role === 'analyst' || auth.role === 'admin')
async function create(date: string) {
  if (!data.value || !canCreate.value || creation.isPending.value) return
  try { const result = await creation.submit({ company_id: data.value.id, as_of_date: date }); if (result) await router.push(`/investigations/${result.id}`) } catch { /* Mutation error is rendered below; identity remains for retry. */ }
}
</script>
<template>
  <section class="feature-page">
    <RouterLink to="/companies">返回企业列表</RouterLink>
    <QueryFeedback :loading="isPending" :refreshing="isFetching && !!data" :error="error" @refresh="refetch()" />
    <header v-if="data" class="page-heading"><div><h1>{{ data.name }}</h1><p class="muted">统一社会信用代码：{{ data.unified_credit_code }}</p><p v-if="data.is_synthetic" class="synthetic">合成数据，仅供演示</p></div></header>
    <QueryFeedback :refreshing="creation.isPending.value" :error="creation.error.value" @refresh="refetch()" />
    <CreateInvestigationForm v-if="data && canCreate" :pending="creation.isPending.value" @submit="create" />
    <p v-if="data && !canCreate" class="panel muted">当前账号可查看调查；发起调查需要分析员或管理员权限。</p>
  </section>
</template>
