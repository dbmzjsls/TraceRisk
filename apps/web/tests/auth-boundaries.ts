import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { z } from 'zod'
import { apiRequest, ApiError, configureAuth, queryClient } from '../src/api/client'
import { useAuthStore } from '../src/features/auth/store'
import { clearSession, safeRedirect } from '../src/features/auth/useAuth'

export async function runAuthBoundaryTests() {
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  } })
  setActivePinia(createPinia())
  const auth = useAuthStore()
  for (const unsafe of ['https://evil.example', '//evil.example', '/\\evil.example', '/%2f%2fevil.example', '/../..//evil.example', '/login?redirect=/companies', '/%00evil', '/%zz']) assert.equal(safeRedirect(unsafe), '/companies')
  assert.equal(safeRedirect('/investigations/abc?view=rules#evidence'), '/investigations/abc?view=rules#evidence')
  auth.setToken('test-token')
  auth.setUser({ id: 'b59d8088-d9a3-4f78-a537-42b30c0c01bf', username: 'analyst', role: 'analyst' })
  const serialized = values.get('tracerisk.session') ?? ''
  assert.deepEqual(Object.keys(JSON.parse(serialized)).sort(), ['token', 'user'])
  auth.restore()
  assert.equal(auth.role, 'analyst')
  configureAuth(() => auth.token, clearSession)
  const originalFetch = globalThis.fetch
  try {
    queryClient.setQueryData(['company', 'old-user'], { confidential: true })
    globalThis.fetch = async (_input, options) => {
      assert.equal(new Headers(options?.headers).get('Authorization'), 'Bearer test-token')
      return new Response('not JSON', { status: 401 })
    }
    await assert.rejects(apiRequest('/companies', z.object({ items: z.array(z.unknown()) })), (error: unknown) => error instanceof ApiError && error.kind === 'unauthorized')
    assert.equal(auth.token, null)
    assert.equal(auth.user, null)
    assert.equal(values.size, 0)
    assert.equal(queryClient.getQueryCache().getAll().length, 0)
    auth.setToken('next-user')
    queryClient.setQueryData(['reports'], { confidential: true })
    clearSession()
    assert.equal(auth.token, null)
    assert.equal(queryClient.getQueryCache().getAll().length, 0)
    globalThis.fetch = async () => new Response(JSON.stringify({ items: 'wrong structure' }), { status: 200 })
    await assert.rejects(apiRequest('/companies', z.object({ items: z.array(z.unknown()) })), (error: unknown) => error instanceof ApiError && error.kind === 'response')
    values.set('tracerisk.session', '{bad json')
    auth.restore()
    assert.equal(auth.token, null)
    assert.equal(values.size, 0)
  } finally { globalThis.fetch = originalFetch; queryClient.clear() }
  console.log('Auth boundary checks passed: internal redirects, session restore, malformed 401/cache clear, logout, response validation.')
}
