import { QueryClient } from '@tanstack/vue-query'
import type { z } from 'zod'
import { apiErrorSchema } from './schemas'

export const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
export type ApiErrorKind = 'unauthorized' | 'forbidden' | 'conflict' | 'validation' | 'network' | 'response' | 'http'
export class ApiError extends Error {
  constructor(public readonly kind: ApiErrorKind, message: string, public readonly status?: number) { super(message); this.name = 'ApiError' }
}
let getToken: () => string | null = () => null
let onUnauthorized: () => void = () => { queryClient.clear() }
let expiredToken: string | null = null
export function configureAuth(token: () => string | null, unauthorized: () => void) { getToken = token; onUnauthorized = unauthorized; expiredToken = null }
export function getAuthToken(): string | null { return getToken() }
export function notifySessionExpired(): boolean {
  const token = getToken()
  if (!token || token === expiredToken) return false
  expiredToken = token
  queryClient.clear()
  onUnauthorized()
  return true
}
export interface RequestOptions { method?: 'GET' | 'POST'; body?: unknown; headers?: Record<string, string>; signal?: AbortSignal }

export async function apiRequest<T>(path: string, schema: z.ZodType<T>, options: RequestOptions = {}): Promise<T> {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) throw new ApiError('response', '无效的 API 地址')
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try {
    response = await fetch(`/api${path}`, { method: options.method ?? 'GET', headers, body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: options.signal, cache: 'no-store' })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('network', '无法连接服务，请检查连接后重试')
  }
  // Clear sensitive state even if the server sends a malformed/non-JSON 401.
  if (response.status === 401) notifySessionExpired()
  let payload: unknown
  try { payload = await response.json() } catch {
    if (response.ok) throw new ApiError('response', '服务响应不是有效 JSON', response.status)
    payload = null
  }
  if (!response.ok) {
    const result = apiErrorSchema.safeParse(payload)
    const kind = ({ 401: 'unauthorized', 403: 'forbidden', 409: 'conflict', 422: 'validation' } as const)[response.status as 401 | 403 | 409 | 422] ?? 'http'
    const fallback = { unauthorized: '登录已失效，请重新登录', forbidden: '当前账号无权执行此操作', conflict: '请求与当前状态冲突', validation: '请检查提交内容', http: '请求失败' }[kind]
    const detail = result.success ? result.data.detail : null
    throw new ApiError(kind, typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(item => item.msg).join('；') : fallback, response.status)
  }
  const parsed = schema.safeParse(payload)
  if (!parsed.success) throw new ApiError('response', '服务响应格式不符合约定', response.status)
  return parsed.data
}
