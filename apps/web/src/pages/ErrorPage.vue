<!-- Adapted upstream 403/404 NResult/footer structure. Copyright © 2023 Ronnie Zhang(大脸怪) | https://isme.top -->
<script setup lang="ts">
import { NButton, NResult } from 'naive-ui'
import { useRouter } from 'vue-router'
withDefaults(defineProps<{ status?: '403' | '404' | '500' }>(), { status: '404' })
const router = useRouter()
</script>
<template>
  <div class="error-page wh-full flex"><NResult class="m-auto" :status="status" :title="status === '403' ? '403 禁止访问' : status === '500' ? '服务暂时不可用' : '404 页面不存在'" :description="status === '403' ? '当前账号没有此页面的访问权限。' : status === '500' ? '请稍后重试登录。' : '请检查地址或返回工作台。'" size="large"><template #footer><NButton type="primary" @click="router.replace(status === '500' ? '/login' : '/companies')">{{ status === '500' ? '重新登录' : '返回工作台' }}</NButton></template></NResult></div>
</template>
