import { agentReferencePools } from './generation-constraints'
import type { AgentTask, AgentTaskResult } from './types'

export interface SemanticReferenceIssue {
  code: string
  path: string
  message: string
}

type JsonObject = Record<string, unknown>

function object(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {}
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
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

function unsupportedIssue(
  supported: Set<string>,
  referenced: Set<string>,
  path: string,
  kind: string,
): SemanticReferenceIssue[] {
  const unsupported = [...referenced].filter((reference) => !supported.has(reference)).sort()
  return unsupported.length === 0
    ? []
    : [{
        code: 'UNSUPPORTED_REFERENCE',
        path,
        message: `output used unsupported ${kind} refs: ${unsupported.join(', ')}`,
      }]
}

export function validateReferenceSemantics(task: AgentTask, result: AgentTaskResult): SemanticReferenceIssue[] {
  const pools = agentReferencePools(task)
  const resultPayload = object(result.payload)
  const issues = [
    ...unsupportedIssue(
      new Set(pools.evidenceRefs),
      collectNamedStrings(result, new Set(['evidence_refs'])),
      '/evidence_refs',
      'evidence',
    ),
    ...unsupportedIssue(
      new Set(pools.claimRefs),
      collectNamedStrings(result, new Set(['claim_id', 'claim_ids', 'missing_claim_ids', 'claim_refs'])),
      '/payload',
      'claim',
    ),
    ...unsupportedIssue(
      new Set(pools.candidateRefs),
      collectNamedStrings(resultPayload, new Set(['candidate_id', 'candidate_ids', 'candidate_ref', 'candidate_refs'])),
      '/payload',
      'candidate',
    ),
    ...unsupportedIssue(
      new Set(pools.assumptionRefs),
      collectNamedStrings(resultPayload, new Set(['assumption_refs'])),
      '/payload',
      'assumption',
    ),
    ...unsupportedIssue(
      new Set(pools.supportRefs),
      collectNamedStrings(resultPayload, new Set(['support_refs'])),
      '/payload',
      'support',
    ),
  ]

  const evidenceById = collectEvidenceRecords(task.payload)
  const coverageRefs = collectNamedStrings(result, new Set(['evidence_refs', 'support_refs']))
  if (result.task_type === 'EVIDENCE_ASSESS') {
    for (const rawAssessment of Array.isArray(resultPayload.assessments) ? resultPayload.assessments : []) {
      const assessment = object(rawAssessment)
      if ((assessment.relation === 'SUPPORTS' || assessment.relation === 'CONTRADICTS')
        && typeof assessment.candidate_ref === 'string') {
        coverageRefs.add(assessment.candidate_ref)
      }
    }
  }
  for (const reference of [...coverageRefs].sort()) {
    const evidence = evidenceById.get(reference)
    if (evidence?.value_kind === 'DECLARED_ASSUMPTION' || evidence?.value_kind === 'UNKNOWN') {
      issues.push({
        code: 'ASSUMPTION_USED_AS_EVIDENCE',
        path: '/payload',
        message: `${reference} cannot be used as evidence coverage`,
      })
    }
  }
  return issues
}
