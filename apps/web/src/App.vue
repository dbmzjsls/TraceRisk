<!-- Adapted upstream Naive UI Chinese locale/theme provider and router outlet. Copyright © 2023 Ronnie Zhang(大脸怪) | https://isme.top -->
<script setup lang="ts">
import { computed } from 'vue'
import { darkTheme, dateZhCN, NConfigProvider, NGlobalStyle, zhCN, type GlobalThemeOverrides } from 'naive-ui'
import { isDark } from './ui/theme'
const overrides = computed<GlobalThemeOverrides>(() => {
  const dark = isDark.value
  const primary = dark ? '#a6ccff' : '#24599a'
  const selected = dark ? '#d0e4ff' : '#174887'
  const wash = dark ? '#223953' : '#d8e8ff'
  return {
    common: {
      primaryColor: primary, primaryColorHover: dark ? '#c2dfff' : '#1e4c86',
      primaryColorPressed: dark ? '#8cbaf6' : '#174887', primaryColorSuppl: primary,
      textColorBase: dark ? '#edf5fa' : '#182d39', bodyColor: dark ? '#131e27' : '#f5f8fa',
      borderColor: dark ? '#425565' : '#cddbe3', borderRadius: '6px',
    },
    Button: {
      textColorPrimary: dark ? '#152737' : '#fff',
      textColorHoverPrimary: dark ? '#152737' : '#fff',
      textColorPressedPrimary: dark ? '#152737' : '#fff',
      textColorFocusPrimary: dark ? '#152737' : '#fff',
    },
    Menu: {
      itemColorActive: wash, itemColorActiveHover: wash,
      itemTextColorActive: selected, itemTextColorActiveHover: selected,
    },
    DataTable: {
      thColor: dark ? '#223953' : '#eaf2ff', thTextColor: selected,
      tdColor: dark ? '#1a2934' : '#fff', borderColor: dark ? '#425565' : '#cddbe3',
    },
  }
})
</script>
<template><NConfigProvider class="wh-full" :locale="zhCN" :date-locale="dateZhCN" :theme="isDark ? darkTheme : undefined" :theme-overrides="overrides"><NGlobalStyle /><RouterView /></NConfigProvider></template>
