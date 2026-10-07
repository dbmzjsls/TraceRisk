import { z } from 'zod'
import type { components } from './generated'

export const roleSchema = z.enum(['analyst', 'reviewer', 'admin'])
export type Role = z.infer<typeof roleSchema>
// Backend UUID strings include fixed seed IDs without RFC version/variant bits.
// Validate their canonical hex shape with guid(), not the stricter uuid().
export const userSchema: z.ZodType<components['schemas']['UserResponse']> = z.object({ id: z.guid(), username: z.string(), role: roleSchema })
export type User = z.infer<typeof userSchema>
export const tokenSchema: z.ZodType<components['schemas']['TokenResponse']> = z.object({ access_token: z.string().min(1), token_type: z.literal('bearer'), expires_in: z.number().int().positive() })
export const apiErrorSchema = z.object({ detail: z.union([z.string(), z.array(z.object({ msg: z.string(), loc: z.array(z.union([z.string(), z.number()])).optional() }))]) })

export type Company = components['schemas']['CompanyResponse']
export type Investigation = components['schemas']['InvestigationResponse']
export type InvestigationDetail = components['schemas']['InvestigationDetailResponse']
export type PendingFinding = components['schemas']['PendingFindingResponse']
export type Decision = components['schemas']['DecisionResponse']
const objectSchema = z.record(z.string(), z.unknown())
export const companySchema: z.ZodType<Company> = z.object({ id: z.guid(), name: z.string(), unified_credit_code: z.string(), is_synthetic: z.boolean() })
export const companiesSchema: z.ZodType<components['schemas']['CompanyListResponse']> = z.object({ notice: z.string(), items: z.array(companySchema) })
export const runSchema: z.ZodType<components['schemas']['CurrentRunResponse']> = z.object({ id: z.guid(), run_number: z.number().int(), status: z.enum(['queued', 'running', 'awaiting_review', 'resume_queued', 'completed', 'failed']), report_id: z.guid().nullable() })
const investigationFields = { id: z.guid(), company_id: z.guid(), company_name: z.string(), created_by: z.guid(), as_of_date: z.iso.date(), current_run: runSchema }
export const investigationSchema: z.ZodType<Investigation> = z.object(investigationFields)
export const investigationsSchema: z.ZodType<components['schemas']['InvestigationListResponse']> = z.object({ items: z.array(investigationSchema) })
export const citationSchema = z.object({ evidence_id: z.guid(), chunk_id: z.guid(), content_hash: z.string(), quoted_excerpt: z.string() }).passthrough()
const supportStatusSchema = z.enum(['supported', 'indeterminate'])
const supportReasonSchema = z.enum(['canonical_fact_match', 'unverified_free_text_extension', 'legacy-stage3'])
const findingFields = { risk_type: z.string(), claim_code: z.string(), title: z.string(), summary: z.string(), reasoning: z.string(), citations: z.array(citationSchema), evidence_sufficiency: z.string(), confidence_level: z.string(), review_recommended: z.boolean(), support_status: supportStatusSchema, support_reason: supportReasonSchema }
export const pendingFindingSchema: z.ZodType<PendingFinding> = z.object({ ...findingFields, finding_id: z.guid(), finding_key: z.string(), finding_version: z.number().int().positive(), run_id: z.guid(), investigation_id: z.guid(), company_id: z.guid(), company_name: z.string() })
export const pendingSchema = z.array(pendingFindingSchema)
export const humanReplacementSchema = z.object({ ...findingFields, finding_key: z.string() })
export type HumanReplacement = z.infer<typeof humanReplacementSchema>
export const decisionSchema: z.ZodType<Decision> = z.object({ id: z.guid(), ai_finding_id: z.guid().nullable(), finding_version: z.number().int().nullable(), reviewer_id: z.guid(), decision: z.enum(['approve', 'modify', 'reject']), reason: z.string(), human_finding_version_id: z.guid().nullable().optional(), human_version: z.number().int().positive().nullable().optional(), replacement: humanReplacementSchema.nullable().optional() })
const ruleFields = { id: z.guid(), rule_code: z.string(), rule_version: z.string(), score: z.number().int(), parameters: objectSchema }
const ruleSchema: z.ZodType<components['schemas']['RuleFindingResponse']> = z.object({ ...ruleFields, evidence: z.array(z.object({ evidence_id: z.guid(), content_hash: z.string(), chunk_id: z.guid().nullable(), chunk_content_hash: z.string().nullable() })) })
const retrievedSchema: z.ZodType<components['schemas']['RetrievedEvidenceResponse']> = z.object({ risk_type: z.string(), rank: z.number().int(), chunk_id: z.guid(), evidence_id: z.guid(), source_uri: z.string(), title: z.string(), observed_at: z.iso.date(), fact_type: z.string(), fact_key: z.string(), fact_value: z.string(), polarity: z.string(), content: z.string(), content_hash: z.string() })
const coverageEntrySchema = z.object({ source_type: z.enum(['business', 'judicial', 'news', 'intellectual_property']), status: z.enum(['available', 'unavailable', 'stale', 'missing']), coverage_id: z.guid().nullable(), checked_at: z.iso.datetime({ offset: true }).nullable() })
const coverageEntriesSchema = z.array(coverageEntrySchema).length(4).superRefine((entries, context) => {
  const expected = ['business', 'judicial', 'news', 'intellectual_property']
  entries.forEach((entry, index) => { if (entry.source_type !== expected[index]) context.addIssue({ code: 'custom', path: [index, 'source_type'], message: 'Coverage entries are out of domain order' }) })
})
const coverageSchema = z.object({ entries: coverageEntriesSchema, insufficiency_details: z.array(z.string()), evidence_sufficiency: z.enum(['sufficient', 'insufficient', 'unavailable']) })
const conflictSideSchema = z.object({ source_type: z.string(), fact_value: z.string(), observed_at: z.iso.date(), evidence_id: z.guid(), content_hash: z.string() })
const conflictSchema = z.object({ company_id: z.guid(), risk_type: z.string(), fact_type: z.string(), fact_key: z.string(), authority_source: z.string().nullable(), resolved_value: z.string().nullable(), resolution_state: z.enum(['resolved', 'unresolved']), sides: z.array(conflictSideSchema).min(2) })
export const investigationDetailSchema: z.ZodType<InvestigationDetail> = z.object({ ...investigationFields, retrieval_state: z.enum(['pending', 'legacy_unavailable', 'ready']), steps: z.array(z.object({ name: z.string(), attempt_count: z.number().int().nonnegative() })), rule_score: z.number().int(), rule_findings: z.array(ruleSchema), retrieved_evidence: z.array(retrievedSchema), ai_findings: z.array(pendingFindingSchema), decisions: z.array(decisionSchema), coverage: coverageSchema, conflicts: z.array(conflictSchema) })
export const reviewResponseSchema: z.ZodType<components['schemas']['ReviewResponse']> = z.object({ decision_id: z.guid() })
export { reportResponseSchema as reportSchema } from './reportSnapshots'
export type { ReportFinding, ReportSnapshot } from './reportSnapshots'
