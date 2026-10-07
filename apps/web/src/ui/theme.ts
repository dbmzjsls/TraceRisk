// Adapted light/dark theme state from vue-naive-admin ToggleTheme.
// Copyright © 2023 Ronnie Zhang(大脸怪) | https://isme.top
import { shallowRef, watch } from 'vue'
export const isDark = shallowRef(window.matchMedia('(prefers-color-scheme: dark)').matches)
watch(isDark, value => document.documentElement.classList.toggle('dark', value), { immediate: true })
export function toggleTheme() { isDark.value = !isDark.value }
