import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useMutation, useQuery } from '@tanstack/vue-query'
import { apiRequest, queryClient } from '../../api/client'
import { investigationDetailSchema, investigationSchema, investigationsSchema, type Investigation } from '../../api/schemas'
import type { components } from '../../api/generated'
import { createRequestIdentity } from '../../api/requestIdentity'

export const investigationKeys = { all: ['investigations'] as const, list: ['investigations', 'list'] as const, detail: (id: string) => ['investigations', 'detail', id] as const }
export function pollInterval(status: Investigation['current_run']['status'] | undefined): number | false {
  if (status === 'awaiting_review') return 5000
  if (status === 'queued' || status === 'running' || status === 'resume_queued') return 2000
  return false
}
export function detailPollInterval(status: Investigation['current_run']['status'] | undefined, pollingEnabled: boolean): number | false {
  return pollingEnabled ? pollInterval(status) : false
}
export function detailPollingEnabled(status: Investigation['current_run']['status'] | undefined, pollingFallback: boolean): boolean {
  return status !== 'completed' && status !== 'failed' && pollingFallback
}
export function listPollInterval(items: Investigation[] | undefined): number | false {
  const intervals = (items ?? []).map(item => pollInterval(item.current_run.status)).filter((value): value is number => value !== false)
  return intervals.length ? Math.min(...intervals) : false
}
export function useInvestigationList() {
  return useQuery({ queryKey: investigationKeys.list, queryFn: ({ signal }) => apiRequest('/investigations?limit=100', investigationsSchema, { signal }), retry: false, refetchInterval: query => listPollInterval(query.state.data?.items) })
}
export function useInvestigation(id: MaybeRefOrGetter<string>, pollingEnabled: MaybeRefOrGetter<boolean> = true) {
  return useQuery(computed(() => ({
    queryKey: investigationKeys.detail(toValue(id)),
    queryFn: ({ signal }: { signal: AbortSignal }) => apiRequest(`/investigations/${encodeURIComponent(toValue(id))}`, investigationDetailSchema, { signal }),
    retry: false,
    enabled: !!toValue(id),
    refetchInterval: (query: { state: { data?: Investigation } }) => detailPollInterval(query.state.data?.current_run.status, toValue(pollingEnabled)),
  })))
}
export function useCreateInvestigation() {
  const identity = createRequestIdentity()
  const mutation = useMutation({ retry: false, mutationFn: (request: ReturnType<typeof identity.capture<components['schemas']['InvestigationCreateRequest']>>) => apiRequest(request.target, investigationSchema, { method: 'POST', body: request.body, headers: { 'Idempotency-Key': request.key } }), onSuccess: async () => { identity.clear(); await queryClient.invalidateQueries({ queryKey: investigationKeys.all }) } })
  async function submit(body: components['schemas']['InvestigationCreateRequest']) {
    if (mutation.isPending.value) return
    return mutation.mutateAsync(identity.capture('/investigations', body))
  }
  return { ...mutation, submit }
}
