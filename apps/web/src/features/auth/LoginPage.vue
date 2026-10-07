<!-- Adapted centered responsive split login shell from vue-naive-admin. Copyright © 2023 Ronnie Zhang(大脸怪) | https://isme.top -->
<script setup lang="ts">
import { useRoute } from 'vue-router'
import LoginForm from './LoginForm.vue'
import { useAuth } from './useAuth'
const route = useRoute()
const { pending, error, login } = useAuth()
</script>
<template>
  <div class="login-page wh-full flex-col">
    <div class="login-card m-auto f-c-c rounded-lg auto-bg card-shadow">
      <div class="login-intro"><div class="section-kicker">RISK DOSSIER</div><h1>TraceRisk</h1><p>企业风险调查 · 证据与人工复核</p><p class="muted">分析员发起调查，复核员核验结论，管理员可查看全部工作入口。</p><p class="synthetic">合成数据，仅供演示</p></div>
      <main class="login-content flex-col"><h2>登录工作台</h2><p v-if="pending" role="status" class="muted">正在验证账号…</p><LoginForm :pending="pending" :error="error" @submit="login($event, route.query.redirect)" /></main>
    </div>
    <footer class="login-footer">TraceRisk · 合成数据，仅供演示</footer>
  </div>
</template>
