import type { APIRequestContext, APIResponse } from '@playwright/test'

/** Never attach a transport error, response body, URL or credentials to reporter output. */
export async function safeRequest<T>(request: APIRequestContext, method: 'get' | 'post', url: string, options: Parameters<APIRequestContext['post']>[1], operation: 'api-read' | 'api-write' | 'login' | 'probe-login'): Promise<T> {
  let response: APIResponse
  try {
    response = await request[method](url, options)
  } catch {
    throw new Error(`${operation} transport failed`)
  }
  let status: number
  let ok: boolean
  try {
    status = response.status()
    ok = response.ok()
  } catch {
    throw new Error(`${operation} response inspection failed`)
  }
  if (!ok) throw new Error(`${operation} returned HTTP ${status}`)
  try {
    return await response.json() as T
  } catch {
    throw new Error(`${operation} response decode failed`)
  }
}
