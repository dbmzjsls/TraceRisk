import type { Investigation, InvestigationDetail } from '../../api/schemas'
export function statusLabel(status: Investigation['current_run']['status']) {
  return { queued: '等待执行', running: '执行中', awaiting_review: '等待人工复核', resume_queued: '等待恢复执行', completed: '已完成', failed: '执行失败' }[status]
}

export function approvedReviewCount(detail: InvestigationDetail | undefined): number {
  if (detail?.current_run.status !== 'completed') return 0
  return detail.decisions.every(item => item.decision === 'approve') ? detail.decisions.length : 0
}
