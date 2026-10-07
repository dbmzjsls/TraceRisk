<!-- Adapted username/password fields and loading button from vue-naive-admin. Copyright © 2023 Ronnie Zhang(大脸怪) | https://isme.top -->
<script setup lang="ts">
import { reactive } from 'vue'
import { NAlert, NButton, NFormItem, NInput } from 'naive-ui'
defineProps<{ pending: boolean; error: string }>()
const emit = defineEmits<{ submit: [credentials: { username: string; password: string }] }>()
const credentials = reactive({ username: '', password: '' })
function submit() {
  emit('submit', { username: credentials.username.trim(), password: credentials.password })
  credentials.password = ''
}
</script>
<template>
  <form class="login-form" @submit.prevent="submit">
    <NFormItem label="用户名" :label-props="{ for: 'username' }"><NInput v-model:value="credentials.username" :input-props="{ id: 'username' }" autofocus autocomplete="username" :maxlength="64" :disabled="pending" placeholder="请输入用户名" /></NFormItem>
    <NFormItem label="密码" :label-props="{ for: 'password' }"><NInput v-model:value="credentials.password" :input-props="{ id: 'password' }" type="password" autocomplete="current-password" :maxlength="1024" :disabled="pending" placeholder="请输入密码" /></NFormItem>
    <NAlert v-if="error" type="error" role="alert" class="mb-4">{{ error }}</NAlert>
    <NButton attr-type="submit" type="primary" block :loading="pending" :disabled="pending || !credentials.username.trim() || !credentials.password">登录</NButton>
  </form>
</template>
