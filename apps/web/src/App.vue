<!-- Adapted upstream Naive UI Chinese locale/theme provider and router outlet. Copyright © 2023 Ronnie Zhang(大脸怪) | https://isme.top -->
<script setup lang="ts">
import { darkTheme, dateZhCN, NConfigProvider, NGlobalStyle, zhCN } from 'naive-ui'
import { isDark } from './ui/theme'
</script>
<template><NConfigProvider class="wh-full" :locale="zhCN" :date-locale="dateZhCN" :theme="isDark ? darkTheme : undefined"><NGlobalStyle /><RouterView /></NConfigProvider></template>
