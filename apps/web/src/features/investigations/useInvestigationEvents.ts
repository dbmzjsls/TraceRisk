import { onScopeDispose, shallowRef, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { getAuthToken, notifySessionExpired, queryClient } from '../../api/client'
import { createSseDecoder, isTerminalWorkflowEvent, type StreamReady, type WorkflowEvent, type WorkflowStreamMessage } from './sse'
import { investigationKeys } from './useInvestigations'

export type RunConnectionStatus = 'idle' | 'connecting' | 'connected' | 'retrying' | 'ended' | 'unauthorized' | 'forbidden'

export interface InvestigationEventOptions {
  fetch?: typeof fetch
  getToken?: () => string | null
  expireSession?: () => boolean
  setTimeout?: (callback: () => void, delay: number) => unknown
  clearTimeout?: (handle: unknown) => void
  onAccepted?: (event: WorkflowEvent) => void | Promise<void>
  retryBaseMs?: number
  retryMaxMs?: number
}

const terminalStatuses = new Set(['completed', 'failed'])

export function useInvestigationEvents(
  runId: MaybeRefOrGetter<string | null | undefined>,
  investigationId: MaybeRefOrGetter<string | null | undefined>,
  options: InvestigationEventOptions = {},
) {
  const fetcher = options.fetch ?? globalThis.fetch.bind(globalThis)
  const readToken = options.getToken ?? getAuthToken
  const expireSession = options.expireSession ?? notifySessionExpired
  const schedule = options.setTimeout ?? ((callback, delay) => globalThis.setTimeout(callback, delay))
  const cancelSchedule = options.clearTimeout ?? (handle => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>))
  const retryBaseMs = options.retryBaseMs ?? 1000
  const retryMaxMs = options.retryMaxMs ?? 30_000
  const events = shallowRef<WorkflowEvent[]>([])
  const lastEventId = shallowRef(0)
  const connectionStatus = shallowRef<RunConnectionStatus>('idle')
  const historyDrained = shallowRef(false)
  const historyAvailable = shallowRef<boolean | null>(null)
  const pollingFallback = shallowRef(true)
  const permissionError = shallowRef<string | null>(null)
  let controller: AbortController | null = null
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  let retryHandle: unknown = null
  let retryAttempt = 0
  let serverRetryMs: number | null = null
  let generation = 0
  let disposed = false

  function clearRetry() {
    if (retryHandle !== null) cancelSchedule(retryHandle)
    retryHandle = null
  }

  function abortCurrent() {
    controller?.abort()
    controller = null
    const activeReader = reader
    reader = null
    if (activeReader) void activeReader.cancel().catch(() => undefined)
  }

  function resetForRun() {
    clearRetry()
    abortCurrent()
    events.value = []
    lastEventId.value = 0
    connectionStatus.value = 'idle'
    historyDrained.value = false
    historyAvailable.value = null
    pollingFallback.value = true
    permissionError.value = null
    retryAttempt = 0
    serverRetryMs = null
  }

  async function accepted(event: WorkflowEvent, activeRunId: string) {
    if (event.run_id !== activeRunId) throw new Error('Workflow event belongs to a different Run')
    if (event.sequence <= lastEventId.value) return
    if (event.sequence !== lastEventId.value + 1) throw new Error('Workflow event sequence has a gap')
    events.value = [...events.value, event]
    lastEventId.value = event.sequence
    if (options.onAccepted) await options.onAccepted(event)
    else {
      const id = toValue(investigationId)
      if (id) await queryClient.invalidateQueries({ queryKey: investigationKeys.detail(id) })
    }
  }

  function scheduleReconnect(activeGeneration: number) {
    if (disposed || activeGeneration !== generation || connectionStatus.value === 'unauthorized' || connectionStatus.value === 'forbidden') return
    clearRetry()
    pollingFallback.value = true
    connectionStatus.value = 'retrying'
    const base = serverRetryMs ?? retryBaseMs
    const delay = Math.min(base * 2 ** retryAttempt, retryMaxMs)
    retryAttempt += 1
    retryHandle = schedule(() => {
      retryHandle = null
      if (!disposed && activeGeneration === generation) void connect(activeGeneration)
    }, delay)
  }

  async function applyMessages(messages: WorkflowStreamMessage[], activeRunId: string, isActive: () => boolean): Promise<StreamReady | null> {
    let latestReady: StreamReady | null = null
    for (const message of messages) {
      if (!isActive()) break
      if (message.kind === 'retry') serverRetryMs = Math.max(1, message.milliseconds)
      else if (message.kind === 'event') await accepted(message.event, activeRunId)
      else {
        if (message.ready.high_water_mark !== lastEventId.value) throw new Error('stream.ready does not certify the accepted history')
        latestReady = message.ready
        historyDrained.value = message.ready.history_drained
        historyAvailable.value = message.ready.history_available
      }
    }
    return latestReady
  }

  async function connect(activeGeneration: number) {
    const activeRunId = toValue(runId)
    if (disposed || activeGeneration !== generation || !activeRunId) return
    const token = readToken()
    if (disposed || activeGeneration !== generation || toValue(runId) !== activeRunId) return
    if (!token) {
      connectionStatus.value = 'unauthorized'
      pollingFallback.value = false
      return
    }
    abortCurrent()
    const activeController = new AbortController()
    controller = activeController
    const isActive = () => !disposed && activeGeneration === generation && !activeController.signal.aborted && controller === activeController && toValue(runId) === activeRunId
    connectionStatus.value = retryAttempt ? 'retrying' : 'connecting'
    permissionError.value = null
    let terminalEventSeen = false
    let latestReady: StreamReady | null = null
    try {
      const headers = new Headers({ Accept: 'text/event-stream', Authorization: `Bearer ${token}` })
      if (lastEventId.value > 0) headers.set('Last-Event-ID', String(lastEventId.value))
      const response = await fetcher(`/api/workflow-runs/${encodeURIComponent(activeRunId)}/events`, {
        method: 'GET', headers, signal: activeController.signal, cache: 'no-store', credentials: 'omit',
      })
      if (!isActive()) return
      if (response.status === 401) {
        events.value = []
        lastEventId.value = 0
        historyDrained.value = false
        historyAvailable.value = null
        pollingFallback.value = false
        connectionStatus.value = 'unauthorized'
        clearRetry()
        expireSession()
        return
      }
      if (response.status === 403) {
        pollingFallback.value = false
        connectionStatus.value = 'forbidden'
        permissionError.value = '当前账号无权查看运行事件'
        clearRetry()
        return
      }
      if (!response.ok) {
        if (response.status >= 500) throw new Error(`Workflow event stream failed with ${response.status}`)
        pollingFallback.value = false
        connectionStatus.value = 'ended'
        return
      }
      if (!response.body) throw new Error('Workflow event stream has no response body')
      connectionStatus.value = 'connected'
      pollingFallback.value = false
      const decoder = createSseDecoder()
      const activeReader = response.body.getReader()
      reader = activeReader
      try {
        while (true) {
          const result = await activeReader.read()
          if (!isActive()) return
          if (result.done) break
          const messages = decoder.push(result.value)
          const ready = await applyMessages(messages, activeRunId, isActive)
          if (!isActive()) return
          if (messages.some(message => message.kind !== 'retry')) retryAttempt = 0
          if (ready) latestReady = ready
          if (messages.some(message => message.kind === 'event' && isTerminalWorkflowEvent(message.event))) terminalEventSeen = true
        }
        if (!isActive()) return
        const tail = decoder.finish()
        const ready = await applyMessages(tail, activeRunId, isActive)
        if (!isActive()) return
        if (tail.some(message => message.kind !== 'retry')) retryAttempt = 0
        if (ready) latestReady = ready
        if (tail.some(message => message.kind === 'event' && isTerminalWorkflowEvent(message.event))) terminalEventSeen = true
      } finally {
        if (reader === activeReader) reader = null
        activeReader.releaseLock()
      }
      if (!isActive()) return
      const terminalReady = latestReady !== null && latestReady.history_drained && terminalStatuses.has(latestReady.run_status)
      if (terminalEventSeen || terminalReady) {
        historyDrained.value = true
        if (terminalEventSeen && historyAvailable.value === null) historyAvailable.value = events.value.length > 0
        connectionStatus.value = 'ended'
        pollingFallback.value = false
        return
      }
      scheduleReconnect(activeGeneration)
    } catch {
      if (!isActive()) return
      scheduleReconnect(activeGeneration)
    } finally {
      if (controller === activeController) controller = null
    }
  }

  function retry() {
    if (disposed || connectionStatus.value === 'unauthorized') return
    generation += 1
    clearRetry()
    abortCurrent()
    retryAttempt = 0
    serverRetryMs = null
    permissionError.value = null
    pollingFallback.value = true
    void connect(generation)
  }

  const stopWatch = watch(
    () => toValue(runId),
    current => {
      generation += 1
      resetForRun()
      if (current) void connect(generation)
    },
    { immediate: true },
  )

  function stop() {
    if (disposed) return
    disposed = true
    generation += 1
    stopWatch()
    clearRetry()
    abortCurrent()
  }
  onScopeDispose(stop)

  return { events, lastEventId, connectionStatus, historyDrained, historyAvailable, pollingFallback, permissionError, retry, stop }
}
