import { z } from 'zod'
import type { components } from '../../api/generated'
import type { PendingFinding } from '../../api/schemas'

export type ReplacementDraft = components['schemas']['RiskFindingDraft']
const text = z.string().min(1).max(10000).regex(/\S/)
export const replacementDraftSchema: z.ZodType<ReplacementDraft> = z.object({
  finding_key: z.string().min(1).max(512).regex(/\S/),
  risk_type: z.enum(['business', 'judicial', 'news', 'intellectual_property']),
  claim_code: z.string().min(1).max(256).regex(/\S/),
  title: z.string().min(1).max(512).regex(/\S/), summary: text, reasoning: text,
  evidence_citations: z.array(z.object({ evidence_id: z.guid(), chunk_id: z.guid(), content_hash: z.string().regex(/^[0-9a-f]{64}$/), quoted_excerpt: text }).strict()).min(1).max(100),
  evidence_sufficiency: z.enum(['sufficient', 'insufficient']), confidence_level: z.enum(['low', 'medium', 'high']), review_recommended: z.boolean(),
}).strict()

// Project the public AI response into the strict request contract. Internal
// support fields and unrelated metadata never enter a replacement submission.
export function replacementFromFinding(finding: PendingFinding, edits: { title: string; summary: string; reasoning: string } = finding) {
  return {
    finding_key: finding.finding_key, risk_type: finding.risk_type, claim_code: finding.claim_code,
    title: edits.title, summary: edits.summary, reasoning: edits.reasoning,
    evidence_citations: finding.citations.map(citation => ({ evidence_id: String(citation.evidence_id), chunk_id: String(citation.chunk_id), content_hash: String(citation.content_hash), quoted_excerpt: String(citation.quoted_excerpt) })),
    evidence_sufficiency: finding.evidence_sufficiency, confidence_level: finding.confidence_level, review_recommended: finding.review_recommended,
  }
}
