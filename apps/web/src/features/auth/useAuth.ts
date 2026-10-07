import { computed, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { apiRequest, queryClient } from '../../api/client'
import { tokenSchema, userSchema } from '../../api/schemas'
import type { components } from '../../api/generated'
import { useAuthStore } from './store'

export function safeRedirect(value: unknown): string {
  const unsafe = (text: string) => /[\\\s]/.test(text) || [...text].some(character => character.charCodeAt(0) < 32)
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || unsafe(value)) return '/companies'
  try {
    const decoded = decodeURIComponent(value)
    if (decoded.startsWith('//') || unsafe(decoded) || decoded.split(/[?#]/)[0] === '/login') return '/companies'
    const url = new URL(value, 'http://tracerisk.local')
    if (url.origin !== 'http://tracerisk.local' || url.pathname === '/login' || url.pathname.startsWith('//')) return '/companies'
    return url.pathname + url.search + url.hash
  } catch { return '/companies' }
}
export async function refreshSession() {
  const auth = useAuthStore()
  if (!auth.token) return
  auth.setUser(await apiRequest('/auth/me', userSchema))
}
export function clearSession() { useAuthStore().clear(); queryClient.clear() }
export function useAuth() {
  const auth = useAuthStore()
  const router = useRouter()
  const pending = shallowRef(false)
  const error = shallowRef('')
  async function login(credentials: components['schemas']['LoginRequest'], redirect: unknown) {
    if (pending.value) return
    pending.value = true; error.value = ''
    try {
      queryClient.clear()
      const result = await apiRequest('/auth/login', tokenSchema, { method: 'POST', body: credentials })
      auth.setToken(result.access_token)
      await refreshSession()
      await router.replace(safeRedirect(redirect))
    } catch (cause) {
      clearSession(); error.value = cause instanceof Error ? cause.message : '登录失败'
    } finally { pending.value = false }
  }
  async function logout() { clearSession(); await router.replace('/login') }
  return { user: computed(() => auth.user), role: computed(() => auth.role), pending, error, login, logout }
}
