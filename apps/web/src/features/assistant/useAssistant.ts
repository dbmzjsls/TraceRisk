import { computed, onScopeDispose, ref, shallowRef, watch, type Ref } from 'vue'
import { ApiError, apiRequest } from '../../api/client'
import { assistantAnswerSchema, type AssistantAnswer, type Company } from '../../api/schemas'
import { failureLabels } from '../investigations/analysisLabels'
import { useInvestigationEvents } from '../investigations/useInvestigationEvents'
import { detailPollingEnabled, useCreateInvestigation, useInvestigation } from '../investigations/useInvestigations'
import { scopeConflict } from './questions'

interface Turn {
  id: string
  question: string
  answer?: AssistantAnswer
  error?: string
}

interface Conversation {
  id: string
  companyId: string
  companyName: string
  date: string
  investigationId: string
  runId: string
  turns: Turn[]
}

interface PendingTurn {
  session: Conversation
  turn: Turn
  controller: AbortController
  answering: boolean
}

export function useAssistant(companies: Ref<Company[] | undefined>, canCreate: Readonly<Ref<boolean>>, existingId: string) {
  const sessions = ref<Conversation[]>([])
  const selected = shallowRef('')
  const companyId = shallowRef('')
  const date = shallowRef('2026-09-11')
  const draft = shallowRef('')
  const notice = shallowRef('')
  const pending = shallowRef<PendingTurn | null>(null)
  const create = useCreateInvestigation()
  const active = computed(() => sessions.value.find(item => item.id === selected.value))
  const investigationId = computed(() => active.value?.investigationId ?? '')
  const runId = computed(() => active.value?.runId ?? '')
  const polling = shallowRef(true)
  const detail = useInvestigation(investigationId, polling)
  const events = useInvestigationEvents(runId, investigationId)
  watch([() => detail.data.value?.current_run.status, events.pollingFallback], ([status, fallback]) => {
    polling.value = detailPollingEnabled(status, fallback)
  }, { immediate: true })
  const busy = computed(() => pending.value !== null)
  const currentRunChanged = computed(() => !!runId.value && !!detail.data.value && detail.data.value.current_run.id !== runId.value)
  let disposed = false
  let waitTimer: ReturnType<typeof setTimeout> | undefined

  function newConversation() {
    const company = companies.value?.find(item => item.id === companyId.value)
    sessions.value.push({
      id: crypto.randomUUID(), companyId: companyId.value, companyName: company?.name ?? '',
      date: date.value, investigationId: '', runId: '', turns: [],
    })
    const session = sessions.value[sessions.value.length - 1]
    selected.value = session.id
    return session
  }

  if (existingId) {
    const session = newConversation()
    session.investigationId = existingId
  }

  watch(detail.data, value => {
    const session = active.value
    if (!value || !session || value.id !== session.investigationId || session.runId) return
    session.companyId = value.company_id
    session.companyName = value.company_name
    session.date = value.as_of_date
    session.runId = value.current_run.id
    companyId.value = session.companyId
    date.value = session.date
  })

  function select(id: string) {
    if (busy.value) return
    const session = sessions.value.find(item => item.id === id)
    if (!session) return
    selected.value = id
    companyId.value = session.companyId
    date.value = session.date
    draft.value = ''
    notice.value = ''
  }

  function startNew() {
    if (busy.value) return
    selected.value = ''
    notice.value = ''
  }

  function setCompany(id: string) {
    if (busy.value || id === companyId.value) return
    startNew()
    companyId.value = id
  }

  function setDate(value: string) {
    if (busy.value || value === date.value) return
    startNew()
    date.value = value
  }

  function finish(work: PendingTurn) {
    if (pending.value !== work) return
    clearTimeout(waitTimer)
    pending.value = null
  }

  function fail(work: PendingTurn, message: string) {
    if (pending.value !== work) return
    work.turn.error = message
    draft.value = work.turn.question
    finish(work)
  }

  async function answer(work: PendingTurn) {
    work.answering = true
    clearTimeout(waitTimer)
    const payload = {
      investigation_id: work.session.investigationId,
      run_id: work.session.runId,
      question: work.turn.question,
    }
    try {
      const result = await apiRequest('/assistant/answers', assistantAnswerSchema, {
        method: 'POST', body: payload, signal: work.controller.signal,
      })
      if (pending.value !== work) return
      if (result.investigation_id !== payload.investigation_id || result.run_id !== payload.run_id ||
        result.question !== payload.question || currentRunChanged.value) {
        fail(work, '调查上下文已更新，请刷新后重新提问。')
        return
      }
      work.turn.answer = result
    } catch (error) {
      fail(work, error instanceof Error ? error.message : '问答失败，请重试。')
      if (error instanceof ApiError && error.kind === 'conflict') void detail.refetch()
    } finally {
      finish(work)
    }
  }

  watch([pending, detail.data, detail.error, detail.isFetching], ([work, data, error, fetching]) => {
    if (!work || work.answering || !work.session.investigationId) return
    if (error && !fetching) {
      fail(work, error.message)
      return
    }
    if (!data || data.id !== work.session.investigationId) return
    if (data.current_run.id !== work.session.runId) {
      fail(work, '调查已切换运行，请确认当前运行后重新提问。')
    } else if (data.current_run.status === 'failed') {
      fail(work, failureLabels[data.failure_code ?? 'workflow_error'] ?? failureLabels.workflow_error)
    } else if (data.current_run.status === 'completed' || data.current_run.status === 'awaiting_review') {
      if (data.retrieval_state === 'ready') void answer(work)
      else if (data.retrieval_state === 'legacy_unavailable') fail(work, '该运行没有可定位的冻结检索证据。')
    }
  })

  async function submit() {
    if (busy.value) return
    notice.value = ''
    const question = draft.value
    if (!question.trim() || [...question].length > 2000) {
      notice.value = '请输入 1 至 2000 个字符的问题。'
      return
    }
    if (!companyId.value || !date.value || (active.value?.investigationId && !runId.value)) {
      notice.value = '请先确认企业与基准日。'
      return
    }
    const mismatch = scopeConflict(question, companyId.value, date.value, companies.value ?? [])
    if (mismatch || currentRunChanged.value || (!active.value?.investigationId && !canCreate.value)) {
      notice.value = mismatch || (currentRunChanged.value ? '调查已切换运行，请确认当前运行。' : '当前账号可从调查详情进入已有运行。')
      return
    }
    const session = active.value ?? newConversation()
    session.turns.push({ id: crypto.randomUUID(), question })
    const turn = session.turns[session.turns.length - 1]
    const work = { session, turn, controller: new AbortController(), answering: false }
    draft.value = ''
    pending.value = work
    waitTimer = setTimeout(() => fail(work, '调查仍在执行，请稍后重新发送原问题。'), 90_000)
    if (session.investigationId) {
      if (detail.error.value) void detail.refetch()
      return
    }
    try {
      const result = await create.submit({ company_id: session.companyId, as_of_date: session.date })
      if (!result || disposed) return
      session.runId = result.current_run.id
      session.investigationId = result.id
    } catch (error) {
      fail(work, error instanceof Error ? error.message : '创建调查失败，请重试。')
    }
  }

  function confirmCurrentRun() {
    if (!currentRunChanged.value || !detail.data.value || busy.value) return
    const data = detail.data.value
    const session = newConversation()
    session.runId = data.current_run.id
    session.investigationId = data.id
  }

  onScopeDispose(() => {
    disposed = true
    clearTimeout(waitTimer)
    pending.value?.controller.abort()
    pending.value = null
  })

  return { sessions, active, investigationId, runId, companyId, date, draft, notice, busy, detail, events,
    currentRunChanged, select, setCompany, setDate, startNew, confirmCurrentRun, submit }
}
