import { z } from 'zod'

const guid = z.guid()
const citationSchema = z.object({ evidence_id: guid, chunk_id: guid, content_hash: z.string(), quoted_excerpt: z.string() }).passthrough()
const supportStatusSchema = z.enum(['supported', 'indeterminate'])
const supportReasonSchema = z.enum(['canonical_fact_match', 'unverified_free_text_extension', 'legacy-stage3'])
const findingBase = {
  id: guid, finding_key: z.string(), version: z.number().int().positive(), risk_type: z.string(), claim_code: z.string(),
  title: z.string(), summary: z.string(), reasoning: z.string(), citations: z.array(citationSchema), evidence_sufficiency: z.string(),
  confidence_level: z.string(), review_recommended: z.boolean(),
}
const provenanceFields = { source: z.enum(['ai', 'human']).optional(), original_ai_finding_id: guid.optional(), human_version: z.number().int().positive().nullable().optional() }
const modernFindingSchema = z.object({ ...findingBase, support_status: supportStatusSchema, support_reason: supportReasonSchema, ...provenanceFields })
const legacyFindingSchema = z.object({ ...findingBase, support_status: supportStatusSchema.optional(), support_reason: supportReasonSchema.optional() }).passthrough()
  .refine(finding => (finding.support_status === undefined) === (finding.support_reason === undefined), { message: 'Legacy support fields must be present together' })
  .transform(finding => ({ ...finding, support_status: finding.support_status ?? 'indeterminate' as const, support_reason: finding.support_reason ?? 'legacy-stage3' as const, source: undefined, original_ai_finding_id: undefined, human_version: undefined }))
const decisionSchema = z.object({ id: guid, ai_finding_id: guid.nullable(), finding_version: z.number().int().nullable(), reviewer_id: guid, decision: z.enum(['approve', 'reject']), reason: z.string() })
const humanReplacementSchema = modernFindingSchema.omit({ id: true, version: true, source: true, original_ai_finding_id: true, human_version: true }).strict()
const stage6DecisionSchema = decisionSchema.extend({ decision: z.enum(['approve', 'modify', 'reject']), human_finding_version_id: guid.nullable(), human_version: z.number().int().positive().nullable(), replacement: humanReplacementSchema.nullable() }).superRefine((decision, context) => {
  const modified = decision.decision === 'modify'
  if (modified !== (decision.replacement !== null) || modified !== (decision.human_finding_version_id !== null) || modified !== (decision.human_version !== null) || (modified && (!decision.ai_finding_id || !decision.finding_version))) context.addIssue({ code: 'custom', message: 'Modified decision must identify its original and human version' })
})
const stage6FinalFindingSchema = modernFindingSchema.extend({ source: z.enum(['ai', 'human']), original_ai_finding_id: guid, human_version: z.number().int().positive().nullable() }).superRefine((finding, context) => {
  if ((finding.source === 'human') !== (finding.human_version !== null)) context.addIssue({ code: 'custom', message: 'Human source requires a human version' })
})
const ruleSchema = z.object({ id: guid, rule_code: z.string(), rule_version: z.string(), score: z.number().int(), parameters: z.record(z.string(), z.unknown()) })
const coverageEntrySchema = z.object({ source_type: z.enum(['business', 'judicial', 'news', 'intellectual_property']), status: z.enum(['available', 'unavailable', 'stale', 'missing']), coverage_id: guid.nullable(), checked_at: z.iso.datetime({ offset: true }).nullable() })
const coverageEntriesSchema = z.array(coverageEntrySchema).length(4).superRefine((entries, context) => {
  const expected = ['business', 'judicial', 'news', 'intellectual_property']
  entries.forEach((entry, index) => { if (entry.source_type !== expected[index]) context.addIssue({ code: 'custom', path: [index, 'source_type'], message: 'Coverage entries are out of domain order' }) })
})
const conflictSchema = z.object({
  company_id: guid, risk_type: z.string(), fact_type: z.string(), fact_key: z.string(), authority_source: z.string().nullable(), resolved_value: z.string().nullable(),
  resolution_state: z.enum(['resolved', 'unresolved']),
  sides: z.array(z.object({ source_type: z.string(), fact_value: z.string(), observed_at: z.iso.date(), evidence_id: guid, content_hash: z.string() })).min(2),
})
const referenceSchema = z.object({ rule_finding_id: guid, evidence_id: guid, content_hash: z.string(), chunk_id: guid.nullable(), chunk_content_hash: z.string().nullable(), title: z.string().nullable(), content: z.string().nullable() })
const legacyRetrievedSchema = z.object({ chunk_id: guid, evidence_id: guid, content_hash: z.string(), content: z.string() }).passthrough()
const modernRetrievedSchema = z.object({
  risk_type: z.enum(['business', 'judicial', 'news', 'intellectual_property']), observed_at: z.iso.date(), fact_type: z.string(), fact_key: z.string(), fact_value: z.string(), polarity: z.string(),
  evidence_content_hash: z.string().regex(/^[0-9a-f]{64}$/), source_type: z.enum(['business', 'judicial', 'news', 'intellectual_property']), rank: z.number().int().min(1).max(5),
  chunk_id: guid, evidence_id: guid, content_hash: z.string().regex(/^[0-9a-f]{64}$/), content: z.string(),
}).strict()
const optionalReview = { review_id: guid.optional(), decision: z.enum(['approve', 'reject']).optional(), reason: z.string().optional(), kind: z.string().optional() }

const modernReportFields = {
  snapshot_version: z.literal('stage5-v1'), notice: z.string(), run_id: guid, company: z.object({ id: guid, name: z.string() }), rule_score: z.number().int(),
  rules: z.array(ruleSchema), ai_findings: z.array(modernFindingSchema), human_decisions: z.array(decisionSchema), final_findings: z.array(modernFindingSchema),
  evidence_references: z.array(referenceSchema), retrieved_evidence: z.array(modernRetrievedSchema),
  coverage: z.object({ scope: z.tuple([z.literal('business'), z.literal('judicial'), z.literal('news'), z.literal('intellectual_property')]), entries: coverageEntriesSchema, insufficiency_details: z.array(z.string()), evidence_sufficiency: z.enum(['sufficient', 'insufficient']) }),
  conflicts: z.array(conflictSchema), review_reasons: z.array(z.string()), evidence_sufficiency: z.enum(['sufficient', 'insufficient']), ...optionalReview,
}
export const modernReportSnapshotSchema = z.object(modernReportFields).strict().transform(snapshot => ({ ...snapshot, availability: 'ready' as const }))
export const stage6ReportSnapshotSchema = z.object({ ...modernReportFields, snapshot_version: z.literal('stage6-v1'), human_decisions: z.array(stage6DecisionSchema), final_findings: z.array(stage6FinalFindingSchema) }).strict().transform(snapshot => ({ ...snapshot, availability: 'ready' as const }))

const legacyReferenceSchema = z.object({ rule_finding_id: guid, evidence_id: guid, content_hash: z.string(), title: z.string().optional(), content: z.string().optional() }).passthrough()
export const legacyReportSnapshotSchema = z.object({
  notice: z.string(), run_id: guid, company: z.object({ id: guid, name: z.string() }), rule_score: z.number().int(), rules: z.array(ruleSchema),
  ai_findings: z.array(legacyFindingSchema), human_decisions: z.array(decisionSchema), final_findings: z.array(legacyFindingSchema),
  evidence_references: z.array(legacyReferenceSchema), retrieved_evidence: z.array(legacyRetrievedSchema),
  coverage: z.object({ scope: z.array(z.string()) }).passthrough(), review_reasons: z.array(z.string()), evidence_sufficiency: z.string(), ...optionalReview,
}).passthrough().refine(snapshot => !("snapshot_version" in snapshot), { message: 'Legacy snapshots must not claim a snapshot version' }).transform(snapshot => ({
  ...snapshot, snapshot_version: 'legacy' as const, availability: 'legacy_unavailable' as const,
  evidence_references: snapshot.evidence_references.map(reference => ({ ...reference, chunk_id: null, chunk_content_hash: null, title: reference.title ?? null, content: reference.content ?? null })),
  coverage: { scope: snapshot.coverage.scope, entries: [], insufficiency_details: [], evidence_sufficiency: 'unavailable' as const }, conflicts: [],
}))

export const reportSnapshotSchema = z.union([stage6ReportSnapshotSchema, modernReportSnapshotSchema, legacyReportSnapshotSchema])
export const parseReportSnapshot = (input: unknown) => reportSnapshotSchema.parse(input)

export const reportResponseSchema = z.object({ id: guid, run_id: guid, html_content: z.string(), snapshot: reportSnapshotSchema })
export type ReportSnapshot = ReturnType<typeof parseReportSnapshot>
export type ReportFinding = ReportSnapshot['final_findings'][number]
