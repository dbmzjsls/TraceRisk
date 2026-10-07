import { computed, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { userSchema, type User } from '../../api/schemas'

const storageKey = 'tracerisk.session'
export const useAuthStore = defineStore('auth', () => {
  const token = shallowRef<string | null>(null)
  const user = shallowRef<User | null>(null)
  const role = computed(() => user.value?.role ?? null)
  function persist() { sessionStorage.setItem(storageKey, JSON.stringify({ token: token.value, user: user.value })) }
  function setToken(value: string) { token.value = value; user.value = null; persist() }
  function setUser(value: User) { user.value = value; persist() }
  function clear() { token.value = null; user.value = null; sessionStorage.removeItem(storageKey) }
  function restore() {
    try {
      const raw: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null')
      if (!raw || typeof raw !== 'object' || !('token' in raw) || typeof raw.token !== 'string' || !raw.token) { clear(); return }
      token.value = raw.token
      user.value = 'user' in raw && userSchema.safeParse(raw.user).success ? userSchema.parse(raw.user) : null
    } catch { clear() }
  }
  return { token, user, role, setToken, setUser, clear, restore }
})
