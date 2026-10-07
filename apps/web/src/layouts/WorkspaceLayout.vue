<!-- Adapted normal layout, header, sidebar/logo/menu and theme action from vue-naive-admin. Copyright © 2023 Ronnie Zhang(大脸怪) | https://isme.top -->
<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, shallowRef } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { NButton, NMenu } from 'naive-ui'
import { useAuth } from '../features/auth/useAuth'
import { isDark, toggleTheme } from '../ui/theme'
const route = useRoute()
const collapsed = shallowRef(false)
const isMobile = shallowRef(false)
const { user, role, logout } = useAuth()
const menus = computed(() => [ { key: '/companies', label: () => h(RouterLink, { to: '/companies' }, () => '企业') }, { key: '/investigations', label: () => h(RouterLink, { to: '/investigations' }, () => '调查') }, ...(role.value === 'reviewer' || role.value === 'admin' ? [{ key: '/reviews', label: () => h(RouterLink, { to: '/reviews' }, () => '待复核') }] : []) ])
const selected = computed(() => '/' + route.path.split('/')[1])
const roleName = computed(() => ({ analyst: '分析员', reviewer: '复核员', admin: '管理员' })[role.value ?? 'analyst'])
type MediaQueryMatch = { matches: boolean }
type MobileQuery = MediaQueryMatch & {
  addEventListener: (type: 'change', listener: (event: MediaQueryMatch) => void) => void
  removeEventListener: (type: 'change', listener: (event: MediaQueryMatch) => void) => void
}
let mobileQuery: MobileQuery | undefined
function syncMobileLayout(event: MediaQueryMatch) {
  isMobile.value = event.matches
  collapsed.value = event.matches
}
function selectMenu() { collapsed.value = isMobile.value }
onMounted(() => {
  mobileQuery = globalThis.matchMedia('(max-width: 767px)')
  syncMobileLayout(mobileQuery)
  mobileQuery.addEventListener('change', syncMobileLayout)
})
onUnmounted(() => mobileQuery?.removeEventListener('change', syncMobileLayout))
</script>
<template>
  <div class="workspace wh-full flex">
    <aside class="workspace-sidebar flex-col" :class="{ 'sidebar-collapsed': collapsed }">
      <RouterLink to="/companies" class="side-logo">{{ collapsed ? 'TR' : 'TraceRisk' }}</RouterLink>
      <NMenu class="cus-scroll-y" :options="menus" :value="selected" :collapsed="collapsed" :collapsed-width="64" @update:value="selectMenu" />
    </aside>
    <article class="workspace-body flex-col">
      <header class="workspace-header"><NButton quaternary aria-label="展开或收起菜单" @click="collapsed = !collapsed">☰</NButton><strong class="workspace-title">企业风险调查</strong><NButton quaternary :aria-label="isDark ? '切换浅色主题' : '切换深色主题'" @click="toggleTheme">{{ isDark ? '☾' : '☀' }}</NButton><span class="user-label">{{ user?.username }} · {{ roleName }}</span><NButton size="small" @click="logout">退出</NButton></header>
      <div class="demo-notice" role="note">合成数据，仅供演示</div>
      <main class="workspace-main cus-scroll-y"><RouterView /></main>
    </article>
  </div>
</template>
