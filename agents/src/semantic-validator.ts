import { validateEvidencePlanSemantics } from './semantic-evidence-plan'
import { validateReferenceSemantics } from './semantic-references'
import type { AgentTask, AgentTaskResult } from './types'

export interface SemanticIssue {
  code: string
  path: string
  message: string
}

export type SemanticValidation =
  | { ok: true; issues: [] }
  | { ok: false; issues: SemanticIssue[] }

type JsonObject = Record<string, unknown>

function object(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {}
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function strings(value: unknown): string[] {
  return array(value).filter((item): item is string => typeof item === 'string')
}

function add(issues: SemanticIssue[], code: string, path: string, message: string): void {
  issues.push({ code, path, message })
}

function collectNamedStrings(value: unknown, keys: Set<string>, collected = new Set<string>()): Set<string> {
  if (Array.isArray(value)) {
    for (const child of value) collectNamedStrings(child, keys, collected)
    return collected
  }
  if (!value || typeof value !== 'object') return collected
  for (const [key, child] of Object.entries(value as JsonObject)) {
    if (keys.has(key)) {
      if (typeof child === 'string') collected.add(child)
      else for (const item of strings(child)) collected.add(item)
    }
    collectNamedStrings(child, keys, collected)
  }
  return collected
}

function collectEvidenceRecords(value: unknown, records = new Map<string, JsonObject>()): Map<string, JsonObject> {
  if (Array.isArray(value)) {
    for (const child of value) collectEvidenceRecords(child, records)
    return records
  }
  if (!value || typeof value !== 'object') return records
  const candidate = value as JsonObject
  if (typeof candidate.evidence_id === 'string' && typeof candidate.value_kind === 'string') {
    records.set(candidate.evidence_id, candidate)
  }
  for (const child of Object.values(candidate)) collectEvidenceRecords(child, records)
  return records
}

function canonicalJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalJson)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value as JsonObject)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, canonicalJson(item)]),
  )
}

function jsonKey(value: unknown): string {
  return JSON.stringify(canonicalJson(value))
}

function requirePoolMember(issues: SemanticIssue[], pool: Set<string>, value: unknown, path: string): void {
  if (typeof value !== 'string' || !pool.has(value)) {
    add(issues, 'OUTPUT_ID_NOT_IN_POOL', path, `output id ${String(value)} was not supplied by the controller`)
  }
}

function validateMoneyRanges(value: unknown, issues: SemanticIssue[], path = '/payload'): void {
  if (Array.isArray(value)) {
    for (const [index, child] of value.entries()) validateMoneyRanges(child, issues, `${path}/${index}`)
    return
  }
  if (!value || typeof value !== 'object') return

  const candidate = value as JsonObject
  if (candidate.kind === 'MONEY_RANGE') {
    const low = candidate.low
    const base = candidate.base
    const high = candidate.high
    if (typeof low === 'number' && typeof base === 'number' && typeof high === 'number' && !(low <= base && base <= high)) {
      add(issues, 'MONEY_RANGE_NON_MONOTONIC', path, 'known money range must satisfy low <= base <= high')
    }
  }
  for (const [key, child] of Object.entries(candidate)) validateMoneyRanges(child, issues, `${path}/${key}`)
}

function validateProposalCandidateCount(
  taskType: AgentTask['task_type'],
  taskPayload: JsonObject,
  resultPayload: JsonObject,
  issues: SemanticIssue[],
): void {
  if (taskType !== 'PROPOSE_INDEPENDENT' && taskType !== 'PROPOSE_FRANCHISE') return
  const sourceKey = taskType === 'PROPOSE_INDEPENDENT' ? 'model_seeds' : 'franchise_universe'
  const sources = array(taskPayload[sourceKey])
  const requestedCount = taskPayload.requested_candidate_count
  const proposals = array(resultPayload.candidate_proposals)
  if (!Number.isInteger(requestedCount) || Number(requestedCount) < 1 || sources.length < 1) return
  const expectedCount = Math.min(Number(requestedCount), sources.length)
  if (proposals.length > expectedCount) {
    add(
      issues,
      'PROPOSAL_COUNT_EXCEEDED',
      '/payload/candidate_proposals',
      `proposal task allows at most ${expectedCount} candidate proposals but returned ${proposals.length}`,
    )
  }
  const proposalIds = proposals
    .map((proposal) => object(proposal).proposal_id)
    .filter((proposalId): proposalId is string => typeof proposalId === 'string')
  if (new Set(proposalIds).size !== proposalIds.length) {
    add(
      issues,
      'PROPOSAL_SOURCE_DUPLICATE',
      '/payload/candidate_proposals',
      'proposal task must use each supplied proposal id at most once',
    )
  }
}

function validateProposalFitAssessments(
  task: AgentTask,
  resultPayload: JsonObject,
  issues: SemanticIssue[],
): void {
  if (task.prompt_version !== 'proposal-agent.v3') return

  for (const [proposalIndex, rawProposal] of array(resultPayload.candidate_proposals).entries()) {
    const proposal = object(rawProposal)
    const assessments = array(proposal.fit_assessments)
    const axes = assessments
      .map((rawAssessment) => object(rawAssessment).axis)
      .filter((axis): axis is string => typeof axis === 'string')
    if (axes.length !== new Set(axes).size) {
      add(
        issues,
        'PROPOSAL_FIT_AXES_INVALID',
        `/payload/candidate_proposals/${proposalIndex}/fit_assessments`,
        'proposal-agent.v3 must not repeat a typed fit axis',
      )
    }

    for (const [assessmentIndex, rawAssessment] of assessments.entries()) {
      const assessment = object(rawAssessment)
      const path = `/payload/candidate_proposals/${proposalIndex}/fit_assessments/${assessmentIndex}`
      const hasBasis = [
        assessment.input_field_refs,
        assessment.claim_refs,
        assessment.evidence_refs,
        assessment.assumption_refs,
        assessment.missing_context,
      ].some((value) => array(value).length > 0)
      if (!hasBasis) {
        add(
          issues,
          'PROPOSAL_FIT_BASIS_MISSING',
          path,
          'fit assessment must cite supplied context or state what context is missing',
        )
      }
      if (assessment.signal === 'UNKNOWN' && array(assessment.missing_context).length === 0) {
        add(
          issues,
          'PROPOSAL_FIT_UNKNOWN_CONTEXT_MISSING',
          `${path}/missing_context`,
          'UNKNOWN fit signal must state which context is missing',
        )
      }
    }
  }
}

function scopesDefinitelyMismatch(left: unknown, right: unknown): boolean {
  const expected = object(left)
  const actual = object(right)
  return typeof expected.scope_type === 'string'
    && expected.scope_type === actual.scope_type
    && typeof expected.scope_id === 'string'
    && typeof actual.scope_id === 'string'
    && expected.scope_id !== actual.scope_id
}

const INTENT_COLLECTION_FIELDS = new Set(['/founder/preferences', '/founder/avoidances'])
const INTENT_ENUM_VALUES: Readonly<Record<string, readonly string[]>> = Object.freeze({
  '/founder/borrowing_intent': ['YES', 'NO', 'UNDECIDED'],
  '/founder/cafe_type_preference': ['OPEN_TO_BOTH', 'INDEPENDENT_ONLY', 'FRANCHISE_ONLY'],
  '/founder/operation_mode': ['DIRECT_FULL_TIME', 'DIRECT_PART_TIME', 'EMPLOYEE_LED', 'UNDECIDED'],
})

function intentTypedValue(value: unknown): JsonObject {
  if (value === null) return { kind: 'NULL', value: null }
  if (typeof value === 'string') return { kind: 'STRING', value }
  if (typeof value === 'number' && Number.isInteger(value)) return { kind: 'INTEGER', value }
  return {}
}

function unicodeLength(value: string): number {
  return [...value].length
}

function validateIntentOperation(
  operation: JsonObject,
  founder: JsonObject,
  latestUserInput: string,
  issues: SemanticIssue[],
  index: number,
): void {
  if (typeof operation.field_path !== 'string') return
  const fieldPath = operation.field_path
  const fieldName = fieldPath.split('/').at(-1)
  if (!fieldName || !(fieldName in founder)) return
  const current = founder[fieldName]
  const expected = object(operation.expected_old_value)
  const typed = object(operation.typed_value)
  const path = `/payload/operations/${index}`
  const sourceSpan = object(operation.source_span)
  const spanStart = sourceSpan.start
  const spanEnd = sourceSpan.end
  if (!Number.isInteger(spanStart) || !Number.isInteger(spanEnd)
    || Number(spanStart) < 0 || Number(spanEnd) <= Number(spanStart)
    || Number(spanEnd) > unicodeLength(latestUserInput)) {
    add(
      issues,
      'INTENT_SOURCE_SPAN_INVALID',
      `${path}/source_span`,
      'source span must be a non-empty half-open Unicode code-point range within latest user input',
    )
  }

  if (INTENT_COLLECTION_FIELDS.has(fieldPath)) {
    const values = strings(current)
    const item = typed.value
    if (typed.kind !== 'STRING' || typeof item !== 'string' || !item.trim() || unicodeLength(item) > 64) {
      add(issues, 'INTENT_COLLECTION_VALUE_INVALID', `${path}/typed_value`, 'collection item must be non-blank text of at most 64 characters')
      return
    }
    if (operation.kind === 'ADD') {
      if (jsonKey(expected) !== jsonKey({ kind: 'NULL', value: null }) || values.includes(item) || values.length >= 8) {
        add(issues, 'INTENT_COLLECTION_PRECONDITION_INVALID', path, 'collection ADD requires a new item, available capacity and a null precondition')
      }
      return
    }
    if (operation.kind === 'REMOVE') {
      if (jsonKey(expected) !== jsonKey(typed) || !values.includes(item)) {
        add(issues, 'INTENT_COLLECTION_PRECONDITION_INVALID', path, 'collection REMOVE must echo the same existing item')
      }
      return
    }
    add(issues, 'INTENT_OPERATION_KIND_INVALID', `${path}/kind`, 'collection fields support only ADD or REMOVE')
    return
  }

  if (jsonKey(expected) !== jsonKey(intentTypedValue(current))) {
    add(issues, 'INTENT_SCALAR_PRECONDITION_INVALID', `${path}/expected_old_value`, 'scalar precondition must exactly match current State')
  }
  if (operation.kind === 'UNSET' && fieldPath === '/founder/max_loss_krw') {
    if (jsonKey(typed) !== jsonKey({ kind: 'NULL', value: null })) {
      add(issues, 'INTENT_SCALAR_VALUE_INVALID', `${path}/typed_value`, 'max loss UNSET requires null')
    }
    if (current === null) {
      add(issues, 'INTENT_SCALAR_VALUE_UNCHANGED', `${path}/typed_value`, 'UNSET must change a non-null max loss value')
    }
    return
  }
  if (operation.kind !== 'SET') {
    add(issues, 'INTENT_OPERATION_KIND_INVALID', `${path}/kind`, 'scalar fields support only SET')
    return
  }

  const value = typed.value
  if (fieldPath === '/founder/own_funds_krw' || fieldPath === '/founder/max_loss_krw') {
    if (typed.kind !== 'INTEGER' || typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
      add(issues, 'INTENT_SCALAR_VALUE_INVALID', `${path}/typed_value`, 'money field requires a non-negative integer')
    }
  } else {
    const allowed = INTENT_ENUM_VALUES[fieldPath]
    if (typed.kind !== 'STRING' || typeof value !== 'string' || !value.trim()
      || (fieldPath === '/founder/target_area_input' && unicodeLength(value) > 256)
      || (allowed && !allowed.includes(value))) {
      add(issues, 'INTENT_SCALAR_VALUE_INVALID', `${path}/typed_value`, 'string field value is invalid')
    }
  }
  if (jsonKey(typed) === jsonKey(intentTypedValue(current))) {
    add(issues, 'INTENT_SCALAR_VALUE_UNCHANGED', `${path}/typed_value`, 'SET must change the current scalar value')
  }
}

function validateIntent(taskPayload: JsonObject, resultPayload: JsonObject, issues: SemanticIssue[]): void {
  const opPool = new Set(strings(taskPayload.operation_id_pool))
  const fieldPool = new Set(strings(taskPayload.allowed_field_paths))
  const founder = object(object(taskPayload.current_state_projection).founder)
  const latestUserInput = typeof taskPayload.latest_user_input === 'string'
    ? taskPayload.latest_user_input
    : ''
  const producedFields: string[] = []
  for (const [index, rawOperation] of array(resultPayload.operations).entries()) {
    const operation = object(rawOperation)
    requirePoolMember(issues, opPool, operation.op_id, `/payload/operations/${index}/op_id`)
    if (typeof operation.field_path !== 'string' || !fieldPool.has(operation.field_path)) {
      add(issues, 'FIELD_PATH_NOT_ALLOWED', `/payload/operations/${index}/field_path`, 'field path is outside the controller-provided ontology')
    } else {
      producedFields.push(operation.field_path)
      validateIntentOperation(operation, founder, latestUserInput, issues, index)
    }
  }
  if (producedFields.length !== new Set(producedFields).size) {
    add(issues, 'DUPLICATE_INTENT_FIELD', '/payload/operations', 'intent output may contain at most one operation per field')
  }
}

function validateEvidenceAssess(taskPayload: JsonObject, resultPayload: JsonObject, issues: SemanticIssue[]): void {
  // 사용자 의도: 모델이 실제로 평가한 근거만 확인하고, 생략된 근거는 조건부로 남긴다.
  const claims = new Map<string, JsonObject>()
  for (const rawClaim of array(taskPayload.claims)) {
    const claim = object(rawClaim)
    if (typeof claim.claim_id === 'string') claims.set(claim.claim_id, claim)
  }
  const claimIds = new Set(claims.keys())
  const evidenceById = collectEvidenceRecords(taskPayload)
  for (const [index, rawAssessment] of array(resultPayload.assessments).entries()) {
    const assessment = object(rawAssessment)
    requirePoolMember(issues, claimIds, assessment.claim_id, `/payload/assessments/${index}/claim_id`)

    const claim = typeof assessment.claim_id === 'string' ? claims.get(assessment.claim_id) : undefined
    const evidence = typeof assessment.candidate_ref === 'string' ? evidenceById.get(assessment.candidate_ref) : undefined
    if (!claim || !evidence) continue

    if (assessment.scope_status === 'MATCH' && scopesDefinitelyMismatch(claim.geographic_scope, evidence.geographic_scope)) {
      add(
        issues,
        'EVIDENCE_SCOPE_OR_DATE_INVALID',
        `/payload/assessments/${index}/scope_status`,
        'MATCH contradicts the structured geographic scope ids',
      )
    }

    if (assessment.freshness_status === 'FRESH' && evidence.freshness_status !== 'FRESH') {
      add(
        issues,
        'EVIDENCE_SCOPE_OR_DATE_INVALID',
        `/payload/assessments/${index}/freshness_status`,
        `FRESH contradicts EvidenceRecord freshness_status=${String(evidence.freshness_status)}`,
      )
    }

    if (assessment.date_status === 'MATCH' && claim.required_freshness !== null && evidence.freshness_status !== 'FRESH') {
      add(
        issues,
        'EVIDENCE_SCOPE_OR_DATE_INVALID',
        `/payload/assessments/${index}/date_status`,
        'MATCH is not allowed when the claim requires freshness and the EvidenceRecord is not FRESH',
      )
    }
  }
  for (const [index, claimId] of strings(resultPayload.missing_claims).entries()) {
    requirePoolMember(issues, claimIds, claimId, `/payload/missing_claims/${index}`)
  }
}

function validateIndependentProposal(taskPayload: JsonObject, resultPayload: JsonObject, issues: SemanticIssue[]): void {
  const seeds = new Map<string, JsonObject>()
  for (const rawSeed of array(taskPayload.model_seeds)) {
    const seed = object(rawSeed)
    if (typeof seed.proposal_id === 'string') seeds.set(seed.proposal_id, seed)
  }
  for (const [index, rawProposal] of array(resultPayload.candidate_proposals).entries()) {
    const proposal = object(rawProposal)
    const proposalId = proposal.proposal_id
    requirePoolMember(issues, new Set(seeds.keys()), proposalId, `/payload/candidate_proposals/${index}/proposal_id`)
    const seed = typeof proposalId === 'string' ? seeds.get(proposalId) : undefined
    if (seed && proposal.seed_or_brand_id !== seed.model_id) {
      add(issues, 'SEED_REFERENCE_MISMATCH', `/payload/candidate_proposals/${index}/seed_or_brand_id`, 'proposal must reference the model seed paired with its proposal_id')
    }
    if (!seed) continue

    const allowedParameters = new Map<string, JsonObject>()
    for (const rawAllowed of array(seed.allowed_parameters)) {
      const allowed = object(rawAllowed)
      if (typeof allowed.field_path === 'string') allowedParameters.set(allowed.field_path, allowed)
    }

    for (const [parameterIndex, rawParameter] of array(proposal.adjusted_parameters).entries()) {
      const parameter = object(rawParameter)
      const fieldPath = parameter.field_path
      const path = `/payload/candidate_proposals/${index}/adjusted_parameters/${parameterIndex}`
      const allowed = typeof fieldPath === 'string' ? allowedParameters.get(fieldPath) : undefined
      if (!allowed) {
        add(issues, 'PARAMETER_FIELD_NOT_ALLOWED', `${path}/field_path`, 'adjusted parameter is outside the selected seed contract')
        continue
      }

      const typedValue = object(parameter.value)
      if (typedValue.kind !== allowed.value_kind) {
        add(issues, 'PARAMETER_VALUE_KIND_INVALID', `${path}/value`, 'adjusted parameter value kind differs from the selected seed contract')
      }
      if (parameter.unit !== allowed.unit) {
        add(issues, 'PARAMETER_UNIT_INVALID', `${path}/unit`, 'adjusted parameter unit differs from the selected seed contract')
      }

      const minimum = typeof allowed.minimum === 'number' ? allowed.minimum : null
      const maximum = typeof allowed.maximum === 'number' ? allowed.maximum : null
      const numericValues: number[] = []
      if ((typedValue.kind === 'INTEGER' || typedValue.kind === 'DECIMAL') && typeof typedValue.value === 'number') {
        numericValues.push(typedValue.value)
      } else if (typedValue.kind === 'MONEY_RANGE') {
        for (const key of ['low', 'base', 'high']) {
          const value = typedValue[key]
          if (typeof value === 'number') numericValues.push(value)
        }
      }
      if (numericValues.some((value) => (minimum !== null && value < minimum) || (maximum !== null && value > maximum))) {
        add(issues, 'PARAMETER_RANGE_INVALID', `${path}/value`, 'adjusted parameter value is outside the selected seed range')
      }
    }
  }
}

function validateFranchiseProposal(taskPayload: JsonObject, resultPayload: JsonObject, issues: SemanticIssue[]): void {
  const brands = new Map<string, JsonObject>()
  for (const rawBrand of array(taskPayload.franchise_universe)) {
    const brand = object(rawBrand)
    if (typeof brand.proposal_id === 'string') brands.set(brand.proposal_id, brand)
  }
  for (const [index, rawProposal] of array(resultPayload.candidate_proposals).entries()) {
    const proposal = object(rawProposal)
    const proposalId = proposal.proposal_id
    requirePoolMember(issues, new Set(brands.keys()), proposalId, `/payload/candidate_proposals/${index}/proposal_id`)
    const brand = typeof proposalId === 'string' ? brands.get(proposalId) : undefined
    if (brand && proposal.seed_or_brand_id !== brand.brand_id) {
      add(issues, 'BRAND_REFERENCE_MISMATCH', `/payload/candidate_proposals/${index}/seed_or_brand_id`, 'proposal must reference the verified brand paired with its proposal_id')
    }
  }
}

function validateDocumentExtract(taskPayload: JsonObject, resultPayload: JsonObject, issues: SemanticIssue[]): void {
  const claimPool = new Set(strings(taskPayload.claim_id_pool))
  const revisionId = object(taskPayload.document_revision).document_revision_id
  const allowedClaimTypes = new Set(strings(object(taskPayload.extraction_contract).claim_types))
  const allowedAnchors = new Set(
    array(taskPayload.parser_blocks).map((rawBlock) => jsonKey(object(rawBlock).anchor)),
  )
  for (const [index, rawClaim] of array(resultPayload.proposed_claims).entries()) {
    const claim = object(rawClaim)
    requirePoolMember(issues, claimPool, claim.claim_id, `/payload/proposed_claims/${index}/claim_id`)
    if (typeof claim.predicate !== 'string' || !allowedClaimTypes.has(claim.predicate)) {
      add(issues, 'CLAIM_TYPE_NOT_ALLOWED', `/payload/proposed_claims/${index}/predicate`, 'claim predicate is outside the supplied extraction contract')
    }
    if (claim.document_revision_id !== revisionId || object(claim.anchor).document_revision_id !== revisionId) {
      add(issues, 'DOCUMENT_REVISION_MISMATCH', `/payload/proposed_claims/${index}`, 'extracted claims and anchors must stay within the supplied document revision')
    }
    if (!allowedAnchors.has(jsonKey(claim.anchor))) {
      add(issues, 'DOCUMENT_ANCHOR_NOT_SUPPLIED', `/payload/proposed_claims/${index}/anchor`, 'claim anchor must exactly match an anchor supplied in parser blocks')
    }
  }
}

function validateCandidateAudit(taskPayload: JsonObject, resultPayload: JsonObject, issues: SemanticIssue[]): void {
  const expectedCandidateIds = array(taskPayload.candidates)
    .map((raw) => object(raw).candidate_id)
    .filter((id): id is string => typeof id === 'string')
  const candidateIds = new Set(expectedCandidateIds)
  const audits = array(resultPayload.candidate_audits)
  const producedCandidateIds: string[] = []
  for (const [index, rawAudit] of audits.entries()) {
    const audit = object(rawAudit)
    requirePoolMember(issues, candidateIds, audit.candidate_id, `/payload/candidate_audits/${index}/candidate_id`)
    if (typeof audit.candidate_id === 'string') producedCandidateIds.push(audit.candidate_id)
    if (audit.status === 'PASS' && array(audit.findings).length > 0) {
      add(
        issues,
        'CANDIDATE_AUDIT_STATUS_INCOHERENT',
        `/payload/candidate_audits/${index}`,
        'PASS audit cannot contain findings',
      )
    }
  }

  if (producedCandidateIds.length !== new Set(producedCandidateIds).size) {
    add(issues, 'CANDIDATE_AUDIT_DUPLICATED', '/payload/candidate_audits', 'candidate audit ids must be unique')
  }

  const calculation = object(taskPayload.calculation_snapshot)
  const allowedCalculationRefs = new Set([
    calculation.calculation_version,
    calculation.input_digest,
    calculation.output_digest,
    ...strings(calculation.candidate_ids),
  ].filter((value): value is string => typeof value === 'string'))
  const usedCalculationRefs = collectNamedStrings(resultPayload, new Set(['calculation_refs']))
  if ([...usedCalculationRefs].some((reference) => !allowedCalculationRefs.has(reference))) {
    add(
      issues,
      'CANDIDATE_AUDIT_CALCULATION_REFERENCE_INVALID',
      '/payload/candidate_audits',
      'audit used a calculation reference outside the supplied calculation snapshot',
    )
  }
}

function validateResultExplain(result: AgentTaskResult, resultPayload: JsonObject, issues: SemanticIssue[]): void {
  const payloadEvidenceRefs = strings(resultPayload.evidence_refs)
  if (jsonKey(payloadEvidenceRefs) !== jsonKey(result.evidence_refs)) {
    add(
      issues,
      'RESULT_EXPLAIN_EVIDENCE_REFERENCE_MISMATCH',
      '/payload/evidence_refs',
      'Result Explain payload evidence refs must exactly match top-level evidence_refs',
    )
  }
}

export function validateAgentSemantics(task: AgentTask, result: AgentTaskResult): SemanticValidation {
  const issues: SemanticIssue[] = []
  const taskPayload = object(task.payload)
  const resultPayload = object(result.payload)

  issues.push(...validateReferenceSemantics(task, result))
  validateMoneyRanges(result.payload, issues)
  validateProposalCandidateCount(task.task_type, taskPayload, resultPayload, issues)
  if (result.payload === null) return issues.length === 0 ? { ok: true, issues: [] } : { ok: false, issues }
  validateProposalFitAssessments(task, resultPayload, issues)

  switch (task.task_type) {
    case 'INTENT_DELTA': validateIntent(taskPayload, resultPayload, issues); break
    case 'EVIDENCE_PLAN': issues.push(...validateEvidencePlanSemantics(task, result)); break
    case 'EVIDENCE_ASSESS': validateEvidenceAssess(taskPayload, resultPayload, issues); break
    case 'PROPOSE_INDEPENDENT': validateIndependentProposal(taskPayload, resultPayload, issues); break
    case 'PROPOSE_FRANCHISE': validateFranchiseProposal(taskPayload, resultPayload, issues); break
    case 'DOCUMENT_EXTRACT': validateDocumentExtract(taskPayload, resultPayload, issues); break
    case 'CANDIDATE_AUDIT': validateCandidateAudit(taskPayload, resultPayload, issues); break
    case 'RESULT_EXPLAIN': validateResultExplain(result, resultPayload, issues); break
  }

  return issues.length === 0 ? { ok: true, issues: [] } : { ok: false, issues }
}
