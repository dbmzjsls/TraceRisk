export const reviewReasonLabels: Record<string, string> = {
  high_risk: '规则分达到高风险阈值（60分）',
  evidence_insufficient: '来源覆盖不足或冲突尚未解决',
  conflict: '同一事实存在跨来源冲突',
  model_evidence_insufficient: '模型发现的证据不足',
  model_recommended: '模型或支持性校验建议复核',
  unverified_free_text_extension: '自然语言扩展尚无法确认支持程度',
}
export function modelExecutionLabel(execution?: { mode: string; models: string[] }) {
  if (!execution || execution.mode === 'unknown') return '模型来源未记录，不能确认 Mock 或真实调用'
  return execution.mode === 'mock' ? 'Mock 模拟模型（未调用真实模型）' : `真实模型调用：${execution.models.join('、')}`
}

export const failureLabels: Record<string, string> = {
  model_generation_failed: '真实模型调用或生成失败，请检查模型配置和调用失败类别后重试。',
  citation_validation_failed: '引用或结构化声明校验失败，请核对企业归属、冻结证据、版本和原文摘录后重新调查。',
  retrieval_integrity_failed: '冻结检索证据完整性校验失败，请检查证据与索引版本。',
  structured_output_failed: '模型结构化输出不符合约定，请检查模型兼容性后重试。',
  workflow_error: '工作流执行失败，请检查运行记录后重新调查。',
}
