import assert from 'node:assert/strict'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createPinia } from 'pinia'
import { renderToString } from 'vue/server-renderer'
import { computed, createSSRApp, effectScope, h, nextTick, shallowRef, ssrContextKey, type Component } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ApiError, configureAuth, notifySessionExpired, queryClient } from '../src/api/client'
import type { InvestigationDetail } from '../src/api/schemas'
import CoverageConflictPanel from '../src/features/investigations/CoverageConflictPanel.vue'
import EvidenceList from '../src/features/investigations/EvidenceList.vue'
import InvestigationDetailPage from '../src/features/investigations/InvestigationDetailPage.vue'
import RiskResultSections from '../src/features/investigations/RiskResultSections.vue'
import RunConnectionStatus from '../src/features/investigations/RunConnectionStatus.vue'
import RunEventTimeline from '../src/features/investigations/RunEventTimeline.vue'
import { createSseDecoder, isTerminalWorkflowEvent, timelineEventLabel, timelineStepLabel, type WorkflowStreamMessage } from '../src/features/investigations/sse'
import { useInvestigationEvents } from '../src/features/investigations/useInvestigationEvents'
import { useEvidenceNavigation } from '../src/features/investigations/useEvidenceNavigation'
import { detailPollInterval, detailPollingEnabled, investigationKeys } from '../src/features/investigations/useInvestigations'
import ReportFindingList from '../src/features/reports/ReportFindingList.vue'
import ReviewForm from '../src/features/reviews/ReviewForm.vue'
import QueryFeedback from '../src/ui/QueryFeedback.vue'

const encoder = new TextEncoder()
const runId = '11111111-1111-1111-1111-111111111111'
const eventId = '22222222-2222-2222-2222-222222222222'

function persisted(sequence: number, overrides: Record<string, unknown> = {}) {
  return {
    id: eventId,
    run_id: runId,
    sequence,
    event_type: 'node.completed',
    node_name: 'retrieve_evidence',
    run_status: 'running',
    metadata: { label: '证据已冻结' },
    created_at: '2026-09-23T08:00:00Z',
    ...overrides,
  }
}

function frame(data: unknown, options: { id?: number; event?: string; newline?: '\n' | '\r\n' } = {}) {
  const newline = options.newline ?? '\n'
  return [
    options.id === undefined ? null : `id: ${options.id}`,
    options.event === undefined ? null : `event: ${options.event}`,
    `data: ${JSON.stringify(data)}`,
    '',
    '',
  ].filter((line): line is string => line !== null).join(newline)
}

function persistedMessages(messages: WorkflowStreamMessage[]) {
  return messages.filter(message => message.kind === 'event')
}

function responseWith(text: string, status = 200): Response {
  return new Response(status === 200 ? new ReadableStream({ start(controller) { controller.enqueue(encoder.encode(text)); controller.close() } }) : text, {
    status,
    headers: status === 200 ? { 'Content-Type': 'text/event-stream' } : { 'Content-Type': 'application/json' },
  })
}

async function eventually(predicate: () => boolean, message: string) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (predicate()) return
    await new Promise(resolve => setTimeout(resolve, 0))
  }
  assert.fail(message)
}

function fakeTimers() {
  let nextId = 0
  const callbacks = new Map<number, () => void>()
  const delays: number[] = []
  return {
    callbacks,
    delays,
    setTimeout(callback: () => void, delay: number) { delays.push(delay); const id = ++nextId; callbacks.set(id, callback); return id },
    clearTimeout(id: unknown) { callbacks.delete(Number(id)) },
    runNext() { const entry = callbacks.entries().next().value as [number, () => void] | undefined; assert.ok(entry); callbacks.delete(entry[0]); entry[1]() },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

async function renderComponent(component: Component, props: Record<string, unknown>): Promise<string> {
  const app = createSSRApp({ render: () => h(component, props) })
  app.component('RouterLink', { props: { to: { type: [String, Object], required: true } }, setup: (linkProps, { slots }) => () => h('a', { href: String(linkProps.to) }, slots.default?.()) })
  return renderToString(app)
}

export async function runStage5EventBoundaryTests() {
  {
    const timers = fakeTimers()
    const investigation = shallowRef('investigation-1')
    const run = shallowRef(runId)
    let evidenceFocuses = 0
    let originFocuses = 0
    const evidence = { scrollIntoView: () => undefined, focus: () => { evidenceFocuses += 1 }, isConnected: true } as unknown as HTMLElement
    const origin = { focus: () => { originFocuses += 1 }, isConnected: true } as unknown as HTMLElement
    const scope = effectScope()
    const navigation = scope.run(() => useEvidenceNavigation(investigation, run, {
      getElementById: id => id === 'evidence-chunk-1' ? evidence : null,
      prefersReducedMotion: () => true,
      setTimeout: timers.setTimeout as typeof setTimeout,
      clearTimeout: timers.clearTimeout as typeof clearTimeout,
    }))!
    assert.equal(navigation.navigate('chunk-1', origin), true)
    assert.equal(evidenceFocuses, 1)
    assert.equal(navigation.activeChunkId.value, 'chunk-1')
    assert.equal(navigation.selectedChunkId.value, 'chunk-1')
    assert.equal(navigation.canReturn.value, true)
    timers.runNext()
    assert.equal(navigation.activeChunkId.value, null)
    assert.equal(navigation.selectedChunkId.value, 'chunk-1')
    assert.equal(navigation.canReturn.value, true)
    navigation.returnToOrigin()
    assert.equal(originFocuses, 1)
    assert.equal(navigation.selectedChunkId.value, null)
    assert.equal(navigation.canReturn.value, false)

    navigation.navigate('chunk-1', origin)
    assert.equal(navigation.navigate('missing', origin), false)
    assert.equal(navigation.activeChunkId.value, null)
    assert.equal(navigation.selectedChunkId.value, null)
    assert.equal(navigation.canReturn.value, false)
    assert.equal(timers.callbacks.size, 0)
    assert.match(navigation.unavailableMessage.value, /没有该精确分块/)

    navigation.navigate('chunk-1', origin)
    run.value = '33333333-3333-3333-3333-333333333333'
    await nextTick()
    assert.equal(navigation.activeChunkId.value, null)
    assert.equal(navigation.selectedChunkId.value, null)
    assert.equal(navigation.canReturn.value, false)
    assert.equal(timers.callbacks.size, 0)

    navigation.navigate('chunk-1', origin)
    scope.stop()
    assert.equal(navigation.activeChunkId.value, null)
    assert.equal(navigation.selectedChunkId.value, null)
    assert.equal(navigation.canReturn.value, false)
    assert.equal(timers.callbacks.size, 0)
  }

  {
    const decoder = createSseDecoder()
    const messages = decoder.push(encoder.encode(frame(persisted(1), { id: 1, event: 'node.completed', newline: '\r\n' })))
    assert.equal(messages.length, 1)
    assert.equal(messages[0]?.kind, 'event')
    assert.equal(messages[0]?.kind === 'event' ? messages[0].event.metadata.label : null, '证据已冻结')
  }

  {
    const timers = fakeTimers()
    let calls = 0
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async () => { calls += 1; return responseWith('', 503) }, getToken: () => 'token',
      setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout, retryBaseMs: 100, retryMaxMs: 400,
    }))!
    for (const expectedDelay of [100, 200, 400, 400]) {
      await eventually(() => timers.callbacks.size === 1, '5xx did not schedule reconnect')
      assert.equal(timers.delays.at(-1), expectedDelay)
      timers.runNext()
    }
    assert.ok(calls >= 4)
    assert.equal(state.pollingFallback.value, true)
    scope.stop()
    assert.equal(timers.callbacks.size, 0)
  }

  {
    const timers = fakeTimers()
    const legacyReady = frame({ history_drained: true, history_available: false, run_status: 'completed', high_water_mark: 0 }, { event: 'stream.ready' })
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async () => responseWith(legacyReady), getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    }))!
    await eventually(() => state.connectionStatus.value === 'ended', 'terminal stream.ready did not finish')
    assert.equal(state.historyAvailable.value, false)
    assert.equal(state.historyDrained.value, true)
    assert.deepEqual(state.events.value, [])
    scope.stop()
  }
  {
    const bytes = encoder.encode(frame(persisted(1, { metadata: { label: '跨块中文' } }), { id: 1, event: 'node.completed' }))
    const split = bytes.findIndex((value, index) => value >= 0xe4 && bytes[index + 1] !== undefined) + 1
    const decoder = createSseDecoder()
    assert.deepEqual(decoder.push(bytes.slice(0, split)), [])
    const messages = decoder.push(bytes.slice(split))
    assert.equal(messages[0]?.kind === 'event' ? messages[0].event.metadata.label : null, '跨块中文')
  }
  {
    const decoder = createSseDecoder()
    const twoFrames = `${frame(persisted(1), { id: 1, event: 'node.completed' })}${frame(persisted(2, { event_type: 'run.completed', node_name: null, run_status: 'completed' }), { id: 2, event: 'run.completed' })}`
    const messages = decoder.push(encoder.encode(twoFrames))
    assert.equal(persistedMessages(messages).length, 2)
    assert.equal(messages[1]?.kind === 'event' ? messages[1].event.event_type : null, 'run.completed')
  }
  {
    const decoder = createSseDecoder()
    const json = JSON.stringify(persisted(1))
    const midpoint = json.indexOf(',') + 1
    const multiline = `id: 1\nevent: node.completed\ndata: ${json.slice(0, midpoint)}\ndata: ${json.slice(midpoint)}\n\n`
    const messages = decoder.push(encoder.encode(multiline))
    assert.equal(messages[0]?.kind, 'event')
  }
  {
    const decoder = createSseDecoder()
    assert.deepEqual(decoder.push(encoder.encode(': heartbeat 10\n\nretry: 2500\n\n')), [{ kind: 'retry', milliseconds: 2500 }])
  }
  {
    const decoder = createSseDecoder()
    const ready = { history_drained: true, history_available: false, run_status: 'completed', high_water_mark: 0 }
    const messages = decoder.push(encoder.encode(frame(ready, { event: 'stream.ready' })))
    assert.deepEqual(messages, [{ kind: 'ready', ready }])
  }
  {
    const decoder = createSseDecoder()
    const partialTail = frame(persisted(1), { id: 1, event: 'node.completed' }).replace(/\n\n$/, '')
    assert.deepEqual(decoder.push(encoder.encode(partialTail)), [])
    assert.equal(decoder.finish()[0]?.kind, 'event')
  }
  for (const invalid of [
    'id: 1\nevent: node.completed\ndata: {bad json}\n\n',
    frame(persisted(1), { id: 0, event: 'node.completed' }),
    frame(persisted(2, { sequence: 1 }), { id: 2, event: 'node.completed' }),
    frame(persisted(1, { event_type: 'run.completed', node_name: null, run_status: 'failed' }), { id: 1, event: 'run.completed' }),
    frame(persisted(1, { event_type: 'run.failed', node_name: null, run_status: 'completed' }), { id: 1, event: 'run.failed' }),
    frame({ history_drained: false, history_available: false, run_status: 'completed', high_water_mark: 0 }, { event: 'stream.ready' }),
    frame(persisted(1), { event: 'node.completed' }),
  ]) {
    const decoder = createSseDecoder()
    assert.throws(() => decoder.push(encoder.encode(invalid)))
  }
  {
    const decoder = createSseDecoder(1)
    assert.throws(() => decoder.push(encoder.encode(frame(persisted(1), { id: 1, event: 'node.completed' }))))
  }
  assert.equal(isTerminalWorkflowEvent(persisted(1, { node_name: 'generate_report', run_status: 'completed' }) as never), false)
  assert.equal(isTerminalWorkflowEvent(persisted(1, { event_type: 'run.completed', node_name: null, run_status: 'completed' }) as never), true)
  console.log('Stage 5 decoder boundaries passed.')

  {
    const timers = fakeTimers()
    const requests: Array<{ url: string; headers: Headers; signal?: AbortSignal; credentials?: RequestCredentials }> = []
    const accepted: number[] = []
    const terminal = `${frame(persisted(1, { node_name: 'generate_report' }), { id: 1, event: 'node.completed' })}${frame(persisted(2, { event_type: 'run.completed', node_name: null, run_status: 'completed' }), { id: 2, event: 'run.completed' })}${frame({ history_drained: true, history_available: true, run_status: 'completed', high_water_mark: 2 }, { event: 'stream.ready' })}`
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async (input, init) => { requests.push({ url: String(input), headers: new Headers(init?.headers), signal: init?.signal, credentials: init?.credentials }); return responseWith(terminal) },
      getToken: () => 'secret-token',
      setTimeout: timers.setTimeout,
      clearTimeout: timers.clearTimeout,
      onAccepted: event => { accepted.push(event.sequence) },
    }))!
    await eventually(() => state.connectionStatus.value === 'ended', 'terminal stream did not end')
    assert.equal(requests[0]?.url, `/api/workflow-runs/${runId}/events`)
    assert.equal(requests[0]?.headers.get('Authorization'), 'Bearer secret-token')
    assert.equal(requests[0]?.headers.get('Last-Event-ID'), null)
    assert.equal(requests[0]?.credentials, 'omit')
    assert.ok(!requests[0]?.url.includes('secret-token'))
    assert.deepEqual(accepted, [1, 2])
    assert.equal(state.lastEventId.value, 2)
    assert.equal(state.historyDrained.value, true)
    assert.equal(state.historyAvailable.value, true)
    assert.equal(state.pollingFallback.value, false)
    assert.equal(timers.callbacks.size, 0)
    scope.stop()
  }

  {
    const timers = fakeTimers()
    const requests: Headers[] = []
    let call = 0
    const first = `${frame(persisted(1), { id: 1, event: 'node.completed' })}${frame({ history_drained: true, history_available: true, run_status: 'running', high_water_mark: 1 }, { event: 'stream.ready' })}`
    const second = `${frame(persisted(1), { id: 1, event: 'node.completed' })}${frame(persisted(2, { event_type: 'run.completed', node_name: null, run_status: 'completed' }), { id: 2, event: 'run.completed' })}`
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async (_input, init) => { requests.push(new Headers(init?.headers)); call += 1; return responseWith(call === 1 ? first : second) },
      getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
      retryBaseMs: 100, retryMaxMs: 400,
    }))!
    await eventually(() => timers.callbacks.size === 1, 'unexpected EOF did not schedule reconnect')
    assert.equal(state.pollingFallback.value, true)
    assert.deepEqual(timers.delays, [100])
    timers.runNext()
    await eventually(() => state.connectionStatus.value === 'ended', 'resumed terminal stream did not end')
    assert.equal(requests[1]?.get('Last-Event-ID'), '1')
    assert.deepEqual(state.events.value.map(event => event.sequence), [1, 2])
    assert.equal(state.pollingFallback.value, false)
    scope.stop()
  }

  {
    const timers = fakeTimers()
    const requests: Headers[] = []
    let call = 0
    const reportNodeOnly = frame(persisted(1, { node_name: 'generate_report', run_status: 'completed' }), { id: 1, event: 'node.completed' })
    const terminalOnly = frame(persisted(2, { event_type: 'run.completed', node_name: null, run_status: 'completed' }), { id: 2, event: 'run.completed' })
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async (_input, init) => { requests.push(new Headers(init?.headers)); call += 1; return responseWith(call === 1 ? reportNodeOnly : terminalOnly) },
      getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout, retryBaseMs: 25,
    }))!
    await eventually(() => timers.callbacks.size === 1, 'report node incorrectly certified terminal EOF')
    assert.equal(state.connectionStatus.value, 'retrying')
    assert.equal(state.historyDrained.value, false)
    assert.equal(state.lastEventId.value, 1)
    timers.runNext()
    await eventually(() => state.connectionStatus.value === 'ended', 'terminal row was not consumed after report-node EOF')
    assert.equal(requests[1]?.get('Last-Event-ID'), '1')
    assert.deepEqual(state.events.value.map(event => [event.sequence, event.event_type]), [[1, 'node.completed'], [2, 'run.completed']])
    scope.stop()
  }

  {
    const timers = fakeTimers()
    let call = 0
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async () => {
        call += 1
        if (call === 1) throw new TypeError('network down')
        return responseWith('event: stream.ready\ndata: {bad json}\n\n')
      },
      getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout, retryBaseMs: 50, retryMaxMs: 100,
    }))!
    await eventually(() => timers.callbacks.size === 1, 'network error did not enable fallback')
    assert.equal(state.pollingFallback.value, true)
    timers.runNext()
    await eventually(() => timers.callbacks.size === 1, 'parse error did not schedule reconnect')
    assert.equal(timers.delays.at(-1), 100)
    assert.equal(state.pollingFallback.value, true)
    scope.stop()
  }

  {
    const timers = fakeTimers()
    let cancelled = false
    const awaiting = `${frame(persisted(1, { event_type: 'run.awaiting_review', node_name: null, run_status: 'awaiting_review' }), { id: 1, event: 'run.awaiting_review' })}${frame({ history_drained: true, history_available: true, run_status: 'awaiting_review', high_water_mark: 1 }, { event: 'stream.ready' })}`
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async () => new Response(new ReadableStream({ start(controller) { controller.enqueue(encoder.encode(awaiting)) }, cancel() { cancelled = true } }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
      getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    }))!
    await eventually(() => state.historyDrained.value, 'awaiting-review history was not drained')
    assert.equal(state.connectionStatus.value, 'connected')
    assert.equal(state.events.value[0]?.event_type, 'run.awaiting_review')
    assert.equal(timers.callbacks.size, 0)
    scope.stop()
    assert.equal(cancelled, true)
  }

  {
    const timers = fakeTimers()
    const requests: Headers[] = []
    let call = 0
    const first = `${frame(persisted(1, { event_type: 'run.queued', node_name: null, run_status: 'running' }), { id: 1, event: 'run.queued' })}${frame({ history_drained: true, history_available: true, run_status: 'running', high_water_mark: 1 }, { event: 'stream.ready' })}`
    const terminalReady = frame({ history_drained: true, history_available: true, run_status: 'completed', high_water_mark: 1 }, { event: 'stream.ready' })
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async (_input, init) => { requests.push(new Headers(init?.headers)); call += 1; return responseWith(call === 1 ? first : terminalReady) },
      getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout, retryBaseMs: 20,
    }))!
    await eventually(() => timers.callbacks.size === 1, 'nonterminal EOF did not reconnect for terminal snapshot')
    timers.runNext()
    await eventually(() => state.connectionStatus.value === 'ended', 'terminal stream.ready did not certify reconnect')
    assert.equal(requests[1]?.get('Last-Event-ID'), '1')
    assert.deepEqual(state.events.value.map(event => event.sequence), [1])
    assert.equal(state.historyDrained.value, true)
    scope.stop()
  }

  {
    const timers = fakeTimers()
    const status = shallowRef<'running' | 'completed'>('completed')
    let signal: AbortSignal | undefined
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async (_input, init) => {
        signal = init?.signal
        return new Response(new ReadableStream({ start() {} }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      },
      getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    }))!
    const polling = computed(() => detailPollingEnabled(status.value, state.pollingFallback.value))
    await eventually(() => state.connectionStatus.value === 'connected', 'terminal-at-mount stream did not connect for history')
    assert.equal(polling.value, false)
    assert.equal(signal?.aborted, false)
    assert.equal(state.historyDrained.value, false)
    scope.stop()
  }

  {
    const timers = fakeTimers()
    const status = shallowRef<'running' | 'completed'>('running')
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async () => { throw new TypeError('history unavailable') },
      getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout, retryBaseMs: 20,
    }))!
    const polling = computed(() => detailPollingEnabled(status.value, state.pollingFallback.value))
    await eventually(() => timers.callbacks.size === 1, 'undrained failure did not schedule history reconnect')
    assert.equal(polling.value, true)
    status.value = 'completed'
    await nextTick()
    assert.equal(polling.value, false)
    assert.equal(timers.callbacks.size, 1)
    assert.equal(state.historyDrained.value, false)
    scope.stop()
  }

  {
    const timers = fakeTimers()
    const run = shallowRef(runId)
    const signals: AbortSignal[] = []
    let cancelled = 0
    const scope = effectScope()
    scope.run(() => useInvestigationEvents(run, shallowRef('investigation-1'), {
      fetch: async (_input, init) => {
        signals.push(init?.signal as AbortSignal)
        return new Response(new ReadableStream({ start() {}, cancel() { cancelled += 1 } }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      },
      getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    }))
    await eventually(() => signals.length === 1, 'initial connection not opened')
    run.value = '33333333-3333-3333-3333-333333333333'
    await nextTick()
    await eventually(() => signals.length === 2, 'Run change did not open a new connection')
    assert.equal(signals[0]?.aborted, true)
    assert.equal(cancelled, 1)
    scope.stop()
    assert.equal(signals[1]?.aborted, true)
    assert.equal(cancelled, 2)
    assert.equal(timers.callbacks.size, 0)
  }

  {
    const timers = fakeTimers()
    const otherRun = '33333333-3333-3333-3333-333333333333'
    const run = shallowRef(runId)
    const firstAccepted = deferred<void>()
    let acceptedA1 = false
    const oldBatch = `${frame(persisted(1), { id: 1, event: 'node.completed' })}${frame(persisted(2), { id: 2, event: 'node.completed' })}${frame({ history_drained: true, history_available: true, run_status: 'completed', high_water_mark: 2 }, { event: 'stream.ready' })}`
    const newBatch = frame(persisted(1, { run_id: otherRun }), { id: 1, event: 'node.completed' })
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(run, shallowRef('investigation-1'), {
      fetch: async input => responseWith(String(input).includes(otherRun) ? newBatch : oldBatch),
      getToken: () => 'token', setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
      onAccepted: event => {
        if (event.run_id === runId && event.sequence === 1) { acceptedA1 = true; return firstAccepted.promise }
      },
    }))!
    await eventually(() => acceptedA1, 'A1 callback did not pause the old batch')
    run.value = otherRun
    await nextTick()
    await eventually(() => state.events.value[0]?.run_id === otherRun, 'B1 was not accepted')
    firstAccepted.resolve()
    await new Promise(resolve => setTimeout(resolve, 0))
    assert.deepEqual(state.events.value.map(event => [event.run_id, event.sequence]), [[otherRun, 1]])
    assert.equal(state.lastEventId.value, 1)
    assert.equal(state.historyAvailable.value, null)
    assert.equal(state.historyDrained.value, false)
    assert.notEqual(state.connectionStatus.value, 'ended')
    scope.stop()
    assert.equal(timers.callbacks.size, 0)
  }

  {
    const timers = fakeTimers()
    const callback = deferred<void>()
    let acceptedA1 = false
    const oldBatch = `${frame(persisted(1), { id: 1, event: 'node.completed' })}${frame(persisted(2), { id: 2, event: 'node.completed' })}${frame({ history_drained: true, history_available: true, run_status: 'completed', high_water_mark: 2 }, { event: 'stream.ready' })}`
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async () => responseWith(oldBatch), getToken: () => 'token',
      setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
      onAccepted: event => { if (event.sequence === 1) { acceptedA1 = true; return callback.promise } },
    }))!
    await eventually(() => acceptedA1, 'disposal callback did not pause the old batch')
    scope.stop()
    callback.resolve()
    await new Promise(resolve => setTimeout(resolve, 0))
    assert.deepEqual(state.events.value.map(event => event.sequence), [1])
    assert.equal(state.lastEventId.value, 1)
    assert.equal(state.historyAvailable.value, null)
    assert.equal(state.historyDrained.value, false)
    assert.notEqual(state.connectionStatus.value, 'ended')
    assert.equal(timers.callbacks.size, 0)
  }

  {
    const timers = fakeTimers()
    const otherRun = '33333333-3333-3333-3333-333333333333'
    const run = shallowRef(runId)
    const oldResponse = deferred<Response>()
    let expired = 0
    let requestedA = false
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(run, shallowRef('investigation-1'), {
      fetch: async input => {
        if (String(input).includes(otherRun)) return responseWith('', 403)
        requestedA = true
        return oldResponse.promise
      },
      getToken: () => 'token', expireSession: () => { expired += 1; return true },
      setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    }))!
    await eventually(() => requestedA, 'A response was not pending')
    run.value = otherRun
    await nextTick()
    await eventually(() => state.connectionStatus.value === 'forbidden', 'B 403 did not settle')
    oldResponse.resolve(responseWith('', 401))
    await new Promise(resolve => setTimeout(resolve, 0))
    assert.equal(expired, 0)
    assert.equal(state.connectionStatus.value, 'forbidden')
    assert.equal(state.permissionError.value, '当前账号无权查看运行事件')
    assert.equal(timers.callbacks.size, 0)
    scope.stop()
  }

  {
    const timers = fakeTimers()
    let expired = 0
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async () => responseWith('not-json', 401), getToken: () => 'expired-token',
      expireSession: () => { expired += 1; return expired === 1 },
      setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    }))!
    await eventually(() => state.connectionStatus.value === 'unauthorized', '401 was not terminal')
    assert.equal(expired, 1)
    assert.deepEqual(state.events.value, [])
    assert.equal(state.pollingFallback.value, false)
    assert.equal(timers.callbacks.size, 0)
    state.retry()
    await new Promise(resolve => setTimeout(resolve, 0))
    assert.equal(expired, 1)
    scope.stop()
  }

  {
    const timers = fakeTimers()
    const scope = effectScope()
    const state = scope.run(() => useInvestigationEvents(shallowRef(runId), shallowRef('investigation-1'), {
      fetch: async () => responseWith('{"detail":"denied"}', 403), getToken: () => 'token',
      setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    }))!
    await eventually(() => state.connectionStatus.value === 'forbidden', '403 was not exposed')
    assert.equal(state.permissionError.value, '当前账号无权查看运行事件')
    assert.equal(timers.callbacks.size, 0)
    assert.equal(state.pollingFallback.value, false)
    scope.stop()
  }

  {
    let notified = 0
    let token: string | null = 'once-token'
    configureAuth(() => token, () => { notified += 1; token = null })
    queryClient.setQueryData(['stage5-secret'], { secret: true })
    assert.equal(notifySessionExpired(), true)
    assert.equal(notifySessionExpired(), false)
    assert.equal(notified, 1)
    assert.equal(queryClient.getQueryCache().getAll().length, 0)
  }
  assert.equal(detailPollInterval('running', true), 2000)
  assert.equal(detailPollInterval('awaiting_review', true), 5000)
  assert.equal(detailPollInterval('completed', true), false)
  assert.equal(detailPollInterval('running', false), false)
  assert.equal(detailPollingEnabled('running', true), true)
  assert.equal(detailPollingEnabled('awaiting_review', true), true)
  assert.equal(detailPollingEnabled('completed', true), false)
  assert.equal(detailPollingEnabled('failed', true), false)

  {
    assert.equal(timelineStepLabel('retrieve_evidence'), '证据检索完成')
    assert.equal(timelineStepLabel('raw_internal_name'), '流程步骤已完成')
    assert.equal(timelineEventLabel({ event_type: 'run.queued', node_name: null }), '调查已进入队列')
    assert.match(timelineEventLabel({ event_type: 'run.completed', node_name: null }), /风险报告已生成/)
    const run = { id: runId, run_number: 1, status: 'running', report_id: null }
    const fallback = await renderComponent(RunEventTimeline, { run, events: [], steps: [{ name: 'retrieve_evidence', attempt_count: 1 }], historyAvailable: null })
    assert.match(fallback, /正在核对事件历史/)
    assert.match(fallback, /证据检索完成/)
    assert.doesNotMatch(fallback, /事件时间线上线前|retrieve_evidence/)
    const durable = await renderComponent(RunEventTimeline, { run, events: [persisted(1)], steps: [{ name: 'raw_internal_name', attempt_count: 1 }], historyAvailable: null })
    assert.match(durable, /正在核对事件历史/)
    assert.match(durable, /证据检索完成/)
    assert.doesNotMatch(durable, /临时视图|raw_internal_name|node\.completed/)
    const legacy = await renderComponent(RunEventTimeline, { run: { ...run, status: 'completed' }, events: [], steps: [], historyAvailable: false })
    assert.match(legacy, /此运行创建于事件时间线上线前/)
    assert.match(legacy, /暂无已记录步骤/)
  }

  {
    const retrying = await renderComponent(RunConnectionStatus, { status: 'retrying', pollingFallback: true, permissionError: null })
    assert.match(retrying, /实时连接暂时中断，正在重试/)
    assert.match(retrying, /当前使用状态刷新/)
    assert.match(retrying, /立即重试/)
    const forbidden = await renderComponent(RunConnectionStatus, { status: 'forbidden', pollingFallback: false, permissionError: '固定权限提示' })
    assert.match(forbidden, /固定权限提示/)
    const component = RunConnectionStatus as unknown as {
      emits?: string[]
      setup?: (props: Record<string, unknown>, context: { emit: (name: string) => void; expose: () => void }) => { retry?: () => void }
    }
    assert.ok(component.emits?.includes('retry'))
    const emitted: string[] = []
    const probeApp = createSSRApp({ render: () => null })
    probeApp.provide(ssrContextKey, { modules: new Set<string>() })
    const setupState = probeApp.runWithContext(() => component.setup?.({ status: 'retrying', pollingFallback: true, permissionError: null }, { emit: name => emitted.push(name), expose: () => undefined }))
    assert.equal(typeof setupState?.retry, 'function')
    setupState?.retry?.()
    assert.deepEqual(emitted, ['retry'])
  }

  {
    const ids = Array.from({ length: 8 }, (_, index) => `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`)
    const finding = { finding_id: ids[3]!, finding_key: 'finding:1', finding_version: 1, run_id: runId, investigation_id: ids[0]!, company_id: ids[1]!, company_name: '合成企业', risk_type: 'judicial', claim_code: 'test', title: '<script>unsafe</script>', summary: 'original', reasoning: 'reasoning', citations: [{ evidence_id: ids[5]!, chunk_id: ids[6]!, content_hash: 'hash', quoted_excerpt: '<b>original quotation</b>' }], evidence_sufficiency: 'sufficient', confidence_level: 'high', review_recommended: true, support_status: 'supported' as const, support_reason: 'canonical_fact_match' as const }
    const pending: InvestigationDetail = { id: ids[0]!, company_id: ids[1]!, company_name: '合成企业', created_by: ids[4]!, as_of_date: '2026-09-11', current_run: { id: runId, run_number: 1, status: 'awaiting_review', report_id: null }, retrieval_state: 'pending', steps: [], rule_score: 35, rule_findings: [], retrieved_evidence: [], ai_findings: [finding], decisions: [], coverage: { entries: ['business', 'judicial', 'news', 'intellectual_property'].map(source_type => ({ source_type: source_type as 'business' | 'judicial' | 'news' | 'intellectual_property', status: 'missing' as const, coverage_id: null, checked_at: null })), insufficiency_details: [], evidence_sufficiency: 'unavailable' }, conflicts: [] }
    const ready: InvestigationDetail = { ...pending, retrieval_state: 'ready', current_run: { ...pending.current_run, status: 'completed', report_id: ids[7]! }, rule_findings: [{ id: ids[7]!, rule_code: 'JUDICIAL_CASE', rule_version: '1', score: 35, parameters: {}, evidence: [{ evidence_id: ids[5]!, content_hash: 'evidence-hash', chunk_id: ids[6]!, chunk_content_hash: 'chunk-hash' }] }], retrieved_evidence: [{ risk_type: 'judicial', rank: 1, chunk_id: ids[6]!, evidence_id: ids[5]!, source_uri: 'synthetic://judicial/case', title: '<img src=x onerror=alert(1)>', observed_at: '2026-09-11', fact_type: 'case', fact_key: 'one', fact_value: 'open', polarity: 'negative', content: '<script>evidence()</script>', content_hash: 'chunk-hash' }], coverage: { entries: pending.coverage.entries.map((entry, index) => ({ ...entry, status: index === 0 ? 'available' as const : 'missing' as const })), insufficiency_details: ['司法来源缺失'], evidence_sufficiency: 'insufficient' }, conflicts: [{ company_id: ids[1]!, risk_type: 'business', fact_type: 'status', fact_key: 'registration', authority_source: 'business', resolved_value: 'active', resolution_state: 'resolved', sides: [{ source_type: 'business', fact_value: 'active', observed_at: '2026-09-11', evidence_id: ids[5]!, content_hash: 'a' }, { source_type: 'news', fact_value: 'closed', observed_at: '2026-09-10', evidence_id: ids[6]!, content_hash: 'b' }] }] }
    const reviewHtml = await renderComponent(ReviewForm, { finding, pending: false })
    const reviewLabels = [...reviewHtml.matchAll(/<label\b[^>]*class="[^"]*\bn-radio\b[^"]*"[^>]*>[\s\S]*?<\/label>/g)].map(match => match[0])
    for (const [decision, text] of [['approve', '通过（approve）'], ['modify', '修改（modify）'], ['reject', '驳回（reject）']]) {
      const label = reviewLabels.find(markup => markup.includes(text))
      assert.ok(label, `${decision} has a clickable visible text label`)
      assert.match(label, new RegExp(`<input[^>]*value="${decision}"`), 'the visible label selects the matching native radio')
      assert.match(label, new RegExp(`<div[^>]*class="n-radio__label"[^>]*>${text}`))
    }
    const riskHtml = await renderComponent(RiskResultSections, { investigation: ready })
    for (const label of ['最终结果', '确定性规则', '原始 AI', '人工决定', '支持状态', 'canonical_fact_match', '证据充分性']) assert.match(riskHtml, new RegExp(label))
    assert.match(riskHtml, new RegExp(`data-citation-target="${ids[6]}"`))
    assert.match(await renderComponent(RiskResultSections, { investigation: pending }), /最终结果尚未形成/)
    const modified = { ...ready, decisions: [{ id: ids[7]!, ai_finding_id: finding.finding_id, finding_version: 1, reviewer_id: ids[4]!, decision: 'modify' as const, reason: '人工修改', human_finding_version_id: ids[6]!, human_version: 1, replacement: { ...finding, title: '<script>human()</script>', summary: '人工摘要', reasoning: '人工推理' } }] }
    const modifiedHtml = await renderComponent(RiskResultSections, { investigation: modified })
    assert.match(modifiedHtml, /人工替换版本/); assert.match(modifiedHtml, /人工摘要/); assert.match(modifiedHtml, /原 AI 发现完整保留/)
    assert.match(modifiedHtml, /&lt;script&gt;human\(\)&lt;\/script&gt;/); assert.doesNotMatch(modifiedHtml, /<script>human\(\)<\/script>/)
    assert.match(modifiedHtml, /&lt;script&gt;unsafe&lt;\/script&gt;/)
    const coverageHtml = await renderComponent(CoverageConflictPanel, { retrievalState: 'ready', coverage: ready.coverage, conflicts: ready.conflicts })
    for (const label of ['工商', '司法', '新闻', '知识产权', '司法来源缺失', 'active', 'closed', '权威来源', '不调整规则分']) assert.match(coverageHtml, new RegExp(label))
    assert.match(await renderComponent(CoverageConflictPanel, { retrievalState: 'pending', coverage: pending.coverage, conflicts: [] }), /检索尚未完成/)
    assert.match(await renderComponent(CoverageConflictPanel, { retrievalState: 'legacy_unavailable', coverage: pending.coverage, conflicts: [] }), /历史运行未保存/)
    const evidenceHtml = await renderComponent(EvidenceList, { evidence: ready.retrieved_evidence, retrievalState: 'ready', activeChunkId: ids[6], selectedChunkId: ids[6], canReturn: true })
    assert.match(evidenceHtml, new RegExp(`id="evidence-${ids[6]}"`)); assert.match(evidenceHtml, /tabindex="-1"/); assert.match(evidenceHtml, /返回引用位置/)
    assert.doesNotMatch(evidenceHtml, /<script>evidence\(\)<\/script>/); assert.match(evidenceHtml, /&lt;script&gt;evidence\(\)&lt;\/script&gt;/)
    const reportFinding = {
      id: ids[3]!, finding_key: 'finding:1', version: 1, risk_type: 'judicial', claim_code: 'test', title: '报告发现', summary: '摘要', reasoning: '理由',
      citations: [{ evidence_id: ids[5]!, chunk_id: ids[6]!, content_hash: 'hash', quoted_excerpt: '引用' }], evidence_sufficiency: 'sufficient', confidence_level: 'high', review_recommended: false,
      support_status: 'supported' as const, support_reason: 'canonical_fact_match' as const,
    }
    const reportFindingHtml = await renderComponent(ReportFindingList, { findings: [reportFinding] })
    assert.match(reportFindingHtml, /已保留精确分块标识；此不可变报告不提供页面内定位/)
    assert.doesNotMatch(reportFindingHtml, /定位精确证据|<button/)
    for (const [status, copy] of [[403, '无权访问'], [404, '记录不存在'], [409, '状态已变化'], [422, '请求内容无效']] as const) assert.match(await renderComponent(QueryFeedback, { error: new ApiError('http', 'boundary', status) }), new RegExp(copy))
    assert.match(await renderComponent(QueryFeedback, { loading: true }), /正在读取服务数据/); assert.match(await renderComponent(QueryFeedback, { refreshing: true }), /正在更新/); assert.match(await renderComponent(QueryFeedback, { empty: true, emptyText: '没有记录' }), /没有记录/)
  }

  {
    const investigationId = '44444444-4444-4444-4444-444444444444'
    const companyId = '55555555-5555-5555-5555-555555555555'
    const userId = '66666666-6666-6666-6666-666666666666'
    const detail = {
      id: investigationId, company_id: companyId, company_name: '页面集成合成企业', created_by: userId, as_of_date: '2026-09-24',
      current_run: { id: runId, run_number: 1, status: 'completed', report_id: null },
      retrieval_state: 'legacy_unavailable', steps: [], rule_score: 0, rule_findings: [], retrieved_evidence: [], ai_findings: [], decisions: [],
      coverage: { entries: ['business', 'judicial', 'news', 'intellectual_property'].map(source_type => ({ source_type, status: 'missing', coverage_id: null, checked_at: null })), insufficiency_details: [], evidence_sufficiency: 'unavailable' },
      conflicts: [],
    }
    queryClient.clear()
    queryClient.setQueryData(investigationKeys.detail(investigationId), detail)
    configureAuth(() => 'page-token', () => undefined)
    const originalFetch = globalThis.fetch
    const streamReady = frame({ history_drained: true, history_available: false, run_status: 'completed', high_water_mark: 0 }, { event: 'stream.ready' })
    globalThis.fetch = async input => String(input).includes('/workflow-runs/') ? responseWith(streamReady) : new Response(JSON.stringify(detail), { status: 200, headers: { 'Content-Type': 'application/json' } })
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/investigations/:id', component: InvestigationDetailPage },
      { path: '/investigations', component: { render: () => h('div') } },
      { path: '/reports/:id', component: { render: () => h('div') } },
    ] })
    await router.push(`/investigations/${investigationId}`)
    await router.isReady()
    const app = createSSRApp(InvestigationDetailPage)
    app.use(createPinia())
    app.use(VueQueryPlugin, { queryClient })
    app.use(router)
    try {
      const page = await renderToString(app)
      assert.match(page, /页面集成合成企业/)
      assert.match(page, /第 1 次运行 · 已完成/)
      assert.match(page, /正在核对事件历史/)
      assert.match(page, /合成数据，仅供演示/)
    } finally {
      globalThis.fetch = originalFetch
      queryClient.clear()
    }
  }
  console.log('Stage 5 connection boundaries passed.')
}
