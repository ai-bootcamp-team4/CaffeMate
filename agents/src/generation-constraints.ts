import type { AgentTask } from './types'

type JsonObject = Record<string, unknown>

export interface AgentReferencePools {
  evidenceRefs: string[]
  claimRefs: string[]
  candidateRefs: string[]
  assumptionRefs: string[]
  supportRefs: string[]
  calculationRefs: string[]
}

function object(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : {}
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function sortedUnique(values: Iterable<string>): string[] {
  return [...new Set(values)].sort()
}

function collectNamedStrings(value: unknown, keys: ReadonlySet<string>, collected = new Set<string>()): Set<string> {
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

export function agentReferencePools(task: AgentTask): AgentReferencePools {
  const payload = object(task.payload)
  const records = collectEvidenceRecords(payload)

  const evidenceRefs = new Set<string>()
  const includeEvidenceRecords = new Set([
    'EVIDENCE_ASSESS',
    'PROPOSE_INDEPENDENT',
    'PROPOSE_FRANCHISE',
    'CANDIDATE_AUDIT',
  ]).has(task.task_type)
  if (includeEvidenceRecords) {
    for (const [evidenceId, evidence] of records) {
      if (evidence.value_kind !== 'DECLARED_ASSUMPTION' && evidence.value_kind !== 'UNKNOWN') {
        evidenceRefs.add(evidenceId)
      }
    }
  }
  if (task.task_type === 'PROPOSE_FRANCHISE') {
    for (const value of strings(proposalSource(task).evidence_refs)) evidenceRefs.add(value)
  }
  if (task.task_type === 'RESULT_EXPLAIN') {
    for (const item of Array.isArray(payload.evidence_catalog) ? payload.evidence_catalog : []) {
      const evidenceId = object(item).evidence_id
      if (typeof evidenceId === 'string') evidenceRefs.add(evidenceId)
    }
  }

  const claimRefs = task.task_type === 'INTENT_DELTA' || task.task_type === 'RESULT_EXPLAIN'
    ? new Set<string>()
    : collectNamedStrings(payload, new Set(['claim_id', 'claim_ids', 'claim_id_pool', 'claim_refs']))

  let candidateRefs = new Set<string>()
  if (task.task_type === 'EVIDENCE_ASSESS') {
    candidateRefs = new Set(records.keys())
  } else if (task.task_type === 'CANDIDATE_AUDIT') {
    candidateRefs = collectNamedStrings(payload.candidates, new Set(['candidate_id']))
  }

  const assumptionRefs = new Set<string>()
  if (task.task_type === 'PROPOSE_INDEPENDENT' || task.task_type === 'CANDIDATE_AUDIT') {
    for (const value of collectNamedStrings(payload, new Set(['assumption_refs', 'support_refs']))) {
      assumptionRefs.add(value)
    }
    for (const [evidenceId, evidence] of records) {
      if (evidence.value_kind === 'DECLARED_ASSUMPTION' || evidence.value_kind === 'UNKNOWN') {
        assumptionRefs.add(evidenceId)
      }
    }
  }

  const supportRefs = new Set<string>()
  if (task.task_type === 'PROPOSE_INDEPENDENT') {
    const forbiddenEvidenceRefs = new Set(
      [...records.entries()]
        .filter(([, evidence]) => evidence.value_kind === 'DECLARED_ASSUMPTION' || evidence.value_kind === 'UNKNOWN')
        .map(([evidenceId]) => evidenceId),
    )
    const sources = Array.isArray(payload.model_seeds) ? payload.model_seeds : []
    for (const rawSource of sources) {
      for (const value of strings(object(rawSource).support_refs)) {
        if (!forbiddenEvidenceRefs.has(value)) supportRefs.add(value)
      }
    }
  } else if (task.task_type === 'PROPOSE_FRANCHISE') {
    for (const value of evidenceRefs) supportRefs.add(value)
  }

  const calculation = object(payload.calculation_snapshot)
  const calculationRefs = new Set<string>()
  if (task.task_type === 'CANDIDATE_AUDIT') {
    for (const value of [
      calculation.calculation_version,
      calculation.input_digest,
      calculation.output_digest,
      ...strings(calculation.candidate_ids),
    ]) {
      if (typeof value === 'string') calculationRefs.add(value)
    }
  }

  return {
    evidenceRefs: sortedUnique(evidenceRefs),
    claimRefs: sortedUnique(claimRefs),
    candidateRefs: sortedUnique(candidateRefs),
    assumptionRefs: sortedUnique(assumptionRefs),
    supportRefs: sortedUnique(supportRefs),
    calculationRefs: sortedUnique(calculationRefs),
  }
}

export function proposalSource(task: AgentTask): JsonObject {
  const payload = object(task.payload)
  const key = task.task_type === 'PROPOSE_INDEPENDENT' ? 'model_seeds' : 'franchise_universe'
  const sources = Array.isArray(payload[key]) ? payload[key] : []
  const source = object(sources[0])
  if (sources.length !== 1 || Object.keys(source).length === 0) {
    throw new Error('AGENT_GENERATION_PROPOSAL_SOURCE_INVALID')
  }
  return source
}

export function proposalParameterContracts(task: AgentTask): JsonObject[] {
  if (task.task_type !== 'PROPOSE_INDEPENDENT') return []
  const source = proposalSource(task)
  return Array.isArray(source.allowed_parameters)
    ? source.allowed_parameters.map(object).filter((item) => Object.keys(item).length > 0)
    : []
}

export function documentGenerationContract(task: AgentTask): {
  claimIds: string[]
  predicates: string[]
  documentRevisionId: string | null
  anchors: JsonObject[]
} {
  const payload = object(task.payload)
  const revision = object(payload.document_revision)
  const extraction = object(payload.extraction_contract)
  const blocks = Array.isArray(payload.parser_blocks) ? payload.parser_blocks : []
  return {
    claimIds: sortedUnique(strings(payload.claim_id_pool)),
    predicates: sortedUnique(strings(extraction.claim_types)),
    documentRevisionId: typeof revision.document_revision_id === 'string' ? revision.document_revision_id : null,
    anchors: blocks.map((block) => object(object(block).anchor)).filter((anchor) => Object.keys(anchor).length > 0),
  }
}

export function intentOperationRules(task: AgentTask): JsonObject[] {
  if (task.task_type !== 'INTENT_DELTA') return []
  const payload = object(task.payload)
  const founder = object(object(payload.current_state_projection).founder)
  const fields = strings(payload.allowed_field_paths)
  const rules: JsonObject[] = []
  for (const fieldPath of fields) {
    const fieldName = fieldPath.split('/').at(-1)
    if (!fieldName || !(fieldName in founder)) continue
    const current = founder[fieldName]
    if (fieldPath === '/founder/preferences' || fieldPath === '/founder/avoidances') {
      rules.push({
        field_path: fieldPath,
        allowed_kinds: ['ADD', ...(Array.isArray(current) && current.length > 0 ? ['REMOVE'] : [])],
        typed_value_kinds: ['STRING'],
        expected_old_value_rule: 'ADD_NULL_REMOVE_EXACT_ITEM',
      })
      continue
    }
    const isMoney = fieldPath === '/founder/own_funds_krw' || fieldPath === '/founder/max_loss_krw'
    rules.push({
      field_path: fieldPath,
      allowed_kinds: fieldPath === '/founder/max_loss_krw' && current !== null ? ['SET', 'UNSET'] : ['SET'],
      typed_value_kinds: isMoney ? ['INTEGER', ...(fieldPath === '/founder/max_loss_krw' ? ['NULL'] : [])] : ['STRING'],
      expected_old_value_rule: 'EXACT_CURRENT_STATE_VALUE',
    })
  }
  return rules
}

export function buildAgentGenerationConstraints(task: AgentTask): JsonObject {
  const pools = agentReferencePools(task)
  const common: JsonObject = {
    reference_fields_are_closed_sets: true,
    status_requirements: {
      COMPLETE: { payload: 'OBJECT' },
      NEEDS_EVIDENCE: { min_missing_claim_ids: 1, min_reason_codes: 1 },
      NEEDS_HUMAN: { min_reason_codes: 1 },
      ABSTAIN: { min_reason_codes: 1 },
      INVALID: { payload: 'NULL', min_reason_codes: 1 },
    },
    allowed_evidence_refs: pools.evidenceRefs,
    allowed_claim_refs: pools.claimRefs,
    allowed_candidate_refs: pools.candidateRefs,
    allowed_assumption_refs: pools.assumptionRefs,
    allowed_support_refs: pools.supportRefs,
    allowed_calculation_refs: pools.calculationRefs,
  }

  switch (task.task_type) {
    case 'EVIDENCE_PLAN': {
      const payload = object(task.payload)
      const constraints = object(payload.planning_constraints)
      const allowedTools = new Set(strings(constraints.allowed_tools))
      const toolVersions = (Array.isArray(task.available_tool_catalog) ? task.available_tool_catalog : [])
        .map(object)
        .filter((item) => typeof item.tool_name === 'string' && allowedTools.has(item.tool_name))
        .map((item) => ({ tool_name: item.tool_name, tool_version: item.tool_version }))
      const claimRules = (Array.isArray(payload.claims) ? payload.claims : [])
        .map(object)
        .filter((claim) => typeof claim.claim_id === 'string')
        .map((claim) => ({ claim_id: claim.claim_id, geographic_scope: claim.geographic_scope }))
      return {
        ...common,
        allocated_action_ids: sortedUnique(strings(payload.action_id_pool)),
        claim_rules: claimRules,
        allowed_tools: toolVersions,
        complete_claim_coverage_exact: true,
        action_ids_globally_unique: true,
        support_action_polarity: 'SUPPORT',
        counter_action_polarity: 'COUNTER',
      }
    }
    case 'EVIDENCE_ASSESS':
      return {
        ...common,
        assessment_candidate_refs: pools.candidateRefs,
        assessment_claim_ids: pools.claimRefs,
      }
    case 'PROPOSE_INDEPENDENT':
    case 'PROPOSE_FRANCHISE': {
      const source = proposalSource(task)
      return {
        ...common,
        allocated_proposal_id: source.proposal_id,
        allocated_source_id: task.task_type === 'PROPOSE_INDEPENDENT' ? source.model_id : source.brand_id,
        allocated_display_name: source.display_name,
        allowed_parameter_contracts: proposalParameterContracts(task),
        allowed_parameter_support_refs: pools.supportRefs,
      }
    }
    case 'CANDIDATE_AUDIT':
      return {
        ...common,
        audit_candidate_ids: pools.candidateRefs,
        pass_findings_must_be_empty: true,
      }
    case 'DOCUMENT_EXTRACT': {
      const contract = documentGenerationContract(task)
      return {
        ...common,
        allocated_claim_ids: contract.claimIds,
        allowed_predicates: contract.predicates,
        document_revision_id: contract.documentRevisionId,
        allowed_anchors: contract.anchors,
      }
    }
    case 'RESULT_EXPLAIN':
      return {
        ...common,
        payload_evidence_refs_must_equal_top_level_evidence_refs: true,
      }
    case 'INTENT_DELTA':
      return {
        ...common,
        operation_rules: intentOperationRules(task),
        propose_delta_affected_workflow_codes: ['FIRST_PROPOSAL'],
      }
    default:
      return common
  }
}
