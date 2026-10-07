import { useMutation, useQuery } from '@tanstack/vue-query'
import { ApiError, apiRequest, queryClient } from '../../api/client'
import { pendingSchema, reviewResponseSchema, type InvestigationDetail, type PendingFinding, type User } from '../../api/schemas'
import type { components } from '../../api/generated'
import { createRequestIdentity } from '../../api/requestIdentity'
import { investigationKeys } from '../investigations/useInvestigations'
export const pendingKey = ['reviews', 'pending'] as const
export function usePendingReviews() { return useQuery({ queryKey: pendingKey, queryFn: ({ signal }) => apiRequest('/reviews/pending', pendingSchema, { signal }), retry: false, refetchInterval: 5000 }) }
export function canReviewRun(investigation: InvestigationDetail | undefined, user: User | null): boolean {
  return !!investigation && !!user && (user.role === 'reviewer' || user.role === 'admin') && investigation.created_by !== user.id && investigation.current_run.status === 'awaiting_review'
}
export function canReviewFinding(investigation: InvestigationDetail | undefined, finding: PendingFinding | undefined, user: User | null): boolean {
  return canReviewRun(investigation, user) && !!finding && investigation!.current_run.id === finding.run_id && investigation!.ai_findings.some(current => current.finding_id === finding.finding_id && current.finding_version === finding.finding_version) && !investigation!.decisions.some(decision => decision.ai_finding_id === finding.finding_id && decision.finding_version === finding.finding_version)
}
export async function refreshReviewContext() {
  await Promise.all([queryClient.invalidateQueries({ queryKey: pendingKey }), queryClient.invalidateQueries({ queryKey: investigationKeys.all })])
}
type ReviewBody = components['schemas']['ReviewRequest'] | components['schemas']['RunReviewRequest']
export function useReviewSubmission() {
  const identity = createRequestIdentity()
  const mutation = useMutation({ retry: false,
    mutationFn: (request: ReturnType<typeof identity.capture<ReviewBody>>) => apiRequest(request.target, reviewResponseSchema, { method: 'POST', body: request.body, headers: { 'Idempotency-Key': request.key } }),
    onSuccess: async () => { identity.clear(); await refreshReviewContext() },
    onError: async error => { if (error instanceof ApiError && error.status === 409) await refreshReviewContext() },
  })
  async function submit(target: string, body: ReviewBody) {
    if (mutation.isPending.value) return
    return mutation.mutateAsync(identity.capture(target, body))
  }
  return { ...mutation, submit }
}
