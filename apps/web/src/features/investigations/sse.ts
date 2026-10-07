import { z } from 'zod'

const MAX_SEQUENCE = 2_147_483_647
const runStatusSchema = z.enum(['queued', 'running', 'awaiting_review', 'resume_queued', 'completed', 'failed'])
const eventTypeSchema = z.enum([
  'run.queued',
  'run.running',
  'node.completed',
  'run.awaiting_review',
  'run.resume_queued',
  'run.completed',
  'run.failed',
])

const persistedEventSchema = z.object({
  id: z.guid(),
  run_id: z.guid(),
  sequence: z.number().int().min(1).max(MAX_SEQUENCE),
  event_type: eventTypeSchema,
  node_name: z.string().min(1).nullable(),
  run_status: runStatusSchema,
  metadata: z.record(z.string(), z.unknown()),
  created_at: z.iso.datetime({ offset: true }),
}).strict().superRefine((value, context) => {
  if (value.event_type === 'run.completed' && value.run_status !== 'completed') context.addIssue({ code: 'custom', path: ['run_status'], message: 'run.completed requires completed status' })
  if (value.event_type === 'run.failed' && value.run_status !== 'failed') context.addIssue({ code: 'custom', path: ['run_status'], message: 'run.failed requires failed status' })
})

const streamReadySchema = z.object({
  history_drained: z.literal(true),
  history_available: z.boolean(),
  run_status: runStatusSchema,
  high_water_mark: z.number().int().min(0).max(MAX_SEQUENCE),
}).strict().superRefine((value, context) => {
  if (value.history_available !== (value.high_water_mark > 0)) context.addIssue({ code: 'custom', path: ['history_available'], message: 'History availability does not match the high-water mark' })
})

export type WorkflowEvent = z.infer<typeof persistedEventSchema>
export type StreamReady = z.infer<typeof streamReadySchema>
export type WorkflowStreamMessage =
  | { kind: 'event'; event: WorkflowEvent }
  | { kind: 'ready'; ready: StreamReady }
  | { kind: 'retry'; milliseconds: number }

const eventLabels: Record<WorkflowEvent['event_type'], string> = {
  'run.queued': '调查已进入队列',
  'run.running': '调查开始执行',
  'node.completed': '流程步骤已完成',
  'run.awaiting_review': '等待人工复核',
  'run.resume_queued': '复核完成，等待恢复执行',
  'run.completed': '调查完成，风险报告已生成',
  'run.failed': '调查执行失败',
}
const nodeLabels: Record<string, string> = {
  prepare_context: '调查上下文准备完成',
  retrieve_evidence: '证据检索完成',
  apply_rules: '风险规则计算完成',
  generate_validate_findings: 'AI 结论生成与校验完成',
  route_result: '流程路径确认完成',
  generate_report: '风险报告生成完成',
}
export function timelineStepLabel(nodeName: string): string { return nodeLabels[nodeName] ?? '流程步骤已完成' }
export function timelineEventLabel(event: Pick<WorkflowEvent, 'event_type' | 'node_name'>): string {
  return event.event_type === 'node.completed' && event.node_name ? timelineStepLabel(event.node_name) : eventLabels[event.event_type]
}
export function isTerminalWorkflowEvent(event: Pick<WorkflowEvent, 'event_type' | 'run_status'>): boolean {
  return (event.event_type === 'run.completed' && event.run_status === 'completed') || (event.event_type === 'run.failed' && event.run_status === 'failed')
}

interface Frame {
  data: string[]
  event?: string
  id?: string
  retry?: string
}

function emptyFrame(): Frame {
  return { data: [] }
}

function parseFrame(frame: Frame, lastSequence: number): { messages: WorkflowStreamMessage[]; lastSequence: number } {
  const messages: WorkflowStreamMessage[] = []
  if (frame.retry !== undefined) {
    if (!/^\d+$/.test(frame.retry)) throw new Error('SSE retry must be a non-negative decimal integer')
    const milliseconds = Number(frame.retry)
    if (!Number.isSafeInteger(milliseconds)) throw new Error('SSE retry exceeds the safe integer range')
    messages.push({ kind: 'retry', milliseconds })
  }
  if (!frame.data.length) return { messages, lastSequence }
  let payload: unknown
  try {
    payload = JSON.parse(frame.data.join('\n'))
  } catch {
    throw new Error('SSE data is not valid JSON')
  }
  if (frame.event === 'stream.ready') {
    if (frame.id !== undefined) throw new Error('stream.ready must not carry an event ID')
    messages.push({ kind: 'ready', ready: streamReadySchema.parse(payload) })
    return { messages, lastSequence }
  }
  if (!frame.event || !frame.id || !/^[1-9]\d*$/.test(frame.id)) throw new Error('Persisted SSE event requires a positive event ID and event name')
  const sequence = Number(frame.id)
  if (!Number.isSafeInteger(sequence) || sequence > MAX_SEQUENCE || sequence <= lastSequence) throw new Error('Persisted SSE event ID is invalid or not increasing')
  const event = persistedEventSchema.parse(payload)
  if (event.sequence !== sequence || event.event_type !== frame.event) throw new Error('Persisted SSE envelope does not match its payload')
  messages.push({ kind: 'event', event })
  return { messages, lastSequence: sequence }
}

export interface SseDecoder {
  push(bytes: Uint8Array): WorkflowStreamMessage[]
  finish(): WorkflowStreamMessage[]
}

export function createSseDecoder(startingSequence = 0): SseDecoder {
  if (!Number.isInteger(startingSequence) || startingSequence < 0 || startingSequence > MAX_SEQUENCE) throw new Error('Starting sequence is invalid')
  const decoder = new TextDecoder()
  let textBuffer = ''
  let frame = emptyFrame()
  let lastSequence = startingSequence
  let finished = false

  function dispatch(): WorkflowStreamMessage[] {
    const result = parseFrame(frame, lastSequence)
    frame = emptyFrame()
    lastSequence = result.lastSequence
    return result.messages
  }

  function acceptLine(line: string): WorkflowStreamMessage[] {
    if (line === '') return dispatch()
    if (line.startsWith(':')) return []
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    let value = colon < 0 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') frame.data.push(value)
    else if (field === 'event') frame.event = value
    else if (field === 'id') {
      if (value.includes('\0')) throw new Error('SSE event ID contains a null character')
      frame.id = value
    } else if (field === 'retry') frame.retry = value
    return []
  }

  function consume(final: boolean): WorkflowStreamMessage[] {
    const messages: WorkflowStreamMessage[] = []
    let cursor = 0
    while (cursor < textBuffer.length) {
      let end = cursor
      while (end < textBuffer.length && textBuffer[end] !== '\r' && textBuffer[end] !== '\n') end += 1
      if (end === textBuffer.length) break
      if (textBuffer[end] === '\r' && end + 1 === textBuffer.length && !final) break
      const line = textBuffer.slice(cursor, end)
      cursor = end + (textBuffer[end] === '\r' && textBuffer[end + 1] === '\n' ? 2 : 1)
      messages.push(...acceptLine(line))
    }
    textBuffer = textBuffer.slice(cursor)
    if (final) {
      if (textBuffer) messages.push(...acceptLine(textBuffer))
      textBuffer = ''
      if (frame.data.length || frame.event !== undefined || frame.id !== undefined || frame.retry !== undefined) messages.push(...dispatch())
    }
    return messages
  }

  return {
    push(bytes) {
      if (finished) throw new Error('SSE decoder is already finished')
      textBuffer += decoder.decode(bytes, { stream: true })
      return consume(false)
    },
    finish() {
      if (finished) return []
      finished = true
      textBuffer += decoder.decode()
      return consume(true)
    },
  }
}
