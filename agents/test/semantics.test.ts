import { describe, expect, it } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { validateAgentTask, validateAgentTaskResult } from '../src/schema-validator'
import { validateAgentSemantics } from '../src/semantic-validator'
import type { AgentTask, AgentTaskResult } from '../src/types'

function fixture(taskType: string) {
  const found = fixtureMatrix.cases.find((item) => item.task.task_type === taskType && item.result.status === 'COMPLETE')
  if (!found) throw new Error(`missing fixture ${taskType}`)
  return structuredClone(found) as unknown as { task: AgentTask; result: AgentTaskResult }
}

function evidenceRecord({
  evidenceId,
  valueKind = 'EVIDENCED_FACT',
  scopeId = '11200690',
  freshnessStatus = 'FRESH',
  sourceDate = '2026-08-01',
}: {
  evidenceId: string
  valueKind?: 'EVIDENCED_FACT' | 'DECLARED_ASSUMPTION' | 'UNKNOWN'
  scopeId?: string
  freshnessStatus?: 'FRESH' | 'STALE' | 'UNKNOWN' | 'NOT_APPLICABLE'
  sourceDate?: string | null
}) {
  const unknown = valueKind === 'UNKNOWN'
  return {
    schema_version: '2.0.0',
    evidence_id: evidenceId,
    project_id: 'project-1',
    claim_type: 'AREA_POPULATION',
    value: unknown ? { kind: 'NULL', value: null } : { kind: 'INTEGER', value: 1000 },
    value_kind: valueKind,
    unit: null,
    geographic_scope: {
      scope_type: 'ADMINISTRATIVE_AREA',
      scope_id: scopeId,
      boundary_version: '2026-01',
    },
    source: {
      title: 'fixture source',
      source_ref: 'fixture://source',
      authority: 'PRIMARY_OFFICIAL',
      source_type: 'DATASET',
      published_or_data_date: sourceDate,
      source_observed_at: '2026-08-21T09:00:00Z',
      document_version: null,
      checksum: null,
    },
    original_anchor: { anchor_type: 'DATASET_ROW', locator: `row:${evidenceId}`, excerpt_hash: null },
    freshness_status: freshnessStatus,
    conflict_status: 'NONE',
    retrieved_at: '2026-08-21T09:00:00Z',
    missing_context: unknown ? ['value unavailable'] : [],
    durable_evidence_refs: [],
  }
}

function attachEvidenceAssessAction(task: AgentTask, evidence: ReturnType<typeof evidenceRecord>): void {
  const payload = task.payload as { executed_actions: unknown[] }
  payload.executed_actions = [{
    action_id: 'action-1',
    claim_id: 'claim-1',
    tool_name: 'get_area_profile',
    request_id: 'request-1',
    structured_result: {
      schema_version: '1.0.0',
      request_id: 'request-1',
      tool_name: 'get_area_profile',
      tool_version: '1.0.0',
      status: 'OK',
      project_id: 'project-1',
      evidence_records: [evidence],
      missing_fields: [],
      conflicts: [],
      source_trace: [],
      error_codes: [],
      observed_at: '2026-08-21T09:00:00Z',
      data: [],
    },
  }]
}

describe('agent semantic validator', () => {
  it('accepts all complete contract fixtures', () => {
    for (const item of fixtureMatrix.cases.filter((entry) => entry.result.status === 'COMPLETE')) {
      expect(validateAgentSemantics(item.task as AgentTask, item.result as AgentTaskResult), item.id).toEqual({ ok: true, issues: [] })
    }
  })

  it('rejects intent operations outside the controller-provided id pool', () => {
    const { task, result } = fixture('INTENT_DELTA')
    result.payload = {
      decision: 'PROPOSE_DELTA',
      operations: [{
        op_id: 'invented-op', kind: 'SET', field_path: 'founder.borrowing_intent',
        expected_old_value: { kind: 'STRING', value: 'NO' }, typed_value: { kind: 'STRING', value: 'YES' },
        unit: null, semantic_kind: 'HARD_CONSTRAINT', source_span: { start: 0, end: 4 }, ambiguity_codes: [],
      }],
      clarifying_questions: [], affected_workflow_codes: [], risk_flags: [],
    }

    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'OUTPUT_ID_NOT_IN_POOL')).toBe(true)
  })

  it('requires support and counterevidence actions for non-SQL evidence plans', () => {
    const { task, result } = fixture('EVIDENCE_PLAN')
    const payload = result.payload as { claim_plans: Array<{ counter_actions: unknown[] }> }
    payload.claim_plans[0].counter_actions = []

    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'COUNTEREVIDENCE_ACTION_REQUIRED')).toBe(true)
  })

  it('rejects proposal ids not supplied by the deterministic seed registry', () => {
    const { task, result } = fixture('PROPOSE_INDEPENDENT')
    const payload = result.payload as { candidate_proposals: Array<{ proposal_id: string }> }
    payload.candidate_proposals[0].proposal_id = 'invented-proposal'

    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'OUTPUT_ID_NOT_IN_POOL')).toBe(true)
  })

  it('rejects evidence and claim references that were not supplied in the frozen input', () => {
    const { task, result } = fixture('PROPOSE_INDEPENDENT')
    const payload = result.payload as { candidate_proposals: Array<{ claim_refs: string[]; evidence_refs: string[] }> }
    payload.candidate_proposals[0].claim_refs = ['invented-claim']
    payload.candidate_proposals[0].evidence_refs = ['invented-evidence']
    result.evidence_refs = ['invented-evidence']

    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'UNSUPPORTED_REFERENCE')).toBe(true)
  })

  it.each(['DECLARED_ASSUMPTION', 'UNKNOWN'] as const)(
    'rejects %s evidence ids when they are used as evidence coverage',
    (valueKind) => {
      const { task, result } = fixture('PROPOSE_INDEPENDENT')
      const evidence = evidenceRecord({
        evidenceId: `ev-${valueKind.toLowerCase()}`,
        valueKind,
        freshnessStatus: valueKind === 'UNKNOWN' ? 'UNKNOWN' : 'NOT_APPLICABLE',
        sourceDate: valueKind === 'UNKNOWN' ? null : '2026-08-01',
      })
      const taskPayload = task.payload as { evidence_records: unknown[] }
      taskPayload.evidence_records = [evidence]
      const resultPayload = result.payload as { candidate_proposals: Array<{ evidence_refs: string[] }> }
      resultPayload.candidate_proposals[0].evidence_refs = [evidence.evidence_id]
      result.evidence_refs = [evidence.evidence_id]

      expect(validateAgentTask(task).ok).toBe(true)
      expect(validateAgentTaskResult(result).ok).toBe(true)
      const validation = validateAgentSemantics(task, result)
      expect(validation.ok).toBe(false)
      expect(validation.issues.some((issue) => issue.code === 'ASSUMPTION_USED_AS_EVIDENCE')).toBe(true)
    },
  )

  it('rejects independent proposal adjustments outside the selected seed contract', () => {
    const { task, result } = fixture('PROPOSE_INDEPENDENT')
    const payload = result.payload as {
      candidate_proposals: Array<{
        adjusted_parameters: Array<{
          field_path: string
          value: { kind: string; value: number }
          unit: string | null
          support_refs: string[]
        }>
      }>
    }
    payload.candidate_proposals[0].adjusted_parameters = [{
      field_path: 'operations.seats',
      value: { kind: 'INTEGER', value: 99 },
      unit: 'seat',
      support_refs: ['seed-registry-1'],
    }]

    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'PARAMETER_RANGE_INVALID')).toBe(true)
  })

  it('rejects non-monotonic money ranges', () => {
    const { task, result } = fixture('PROPOSE_INDEPENDENT')
    const taskPayload = task.payload as { model_seeds: Array<{ allowed_parameters: unknown[] }> }
    taskPayload.model_seeds[0].allowed_parameters = [{
      field_path: 'finance.initial_cash', value_kind: 'MONEY_RANGE', unit: 'KRW', minimum: 0, maximum: 1000,
    }]
    const resultPayload = result.payload as { candidate_proposals: Array<{ adjusted_parameters: unknown[] }> }
    resultPayload.candidate_proposals[0].adjusted_parameters = [{
      field_path: 'finance.initial_cash',
      value: { kind: 'MONEY_RANGE', currency: 'KRW', low: 900, base: 500, high: 700 },
      unit: 'KRW',
      support_refs: ['seed-registry-1'],
    }]

    expect(validateAgentTask(task).ok).toBe(true)
    expect(validateAgentTaskResult(result).ok).toBe(true)
    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'MONEY_RANGE_NON_MONOTONIC')).toBe(true)
  })

  it('rejects evidence assessment claims that contradict structured scope and freshness metadata', () => {
    const { task, result } = fixture('EVIDENCE_ASSESS')
    const evidence = evidenceRecord({
      evidenceId: 'ev-stale-other-scope', scopeId: '26110520', freshnessStatus: 'STALE', sourceDate: '2020-01-01',
    })
    attachEvidenceAssessAction(task, evidence)
    result.payload = {
      assessments: [{
        claim_id: 'claim-1',
        candidate_ref: evidence.evidence_id,
        relation: 'SUPPORTS',
        scope_status: 'MATCH',
        date_status: 'MATCH',
        freshness_status: 'FRESH',
        anchor_status: 'VALID',
        authority_status: 'ACCEPTABLE',
        missing_context: [],
      }],
      missing_claims: [],
      conflict_proposals: [],
    }
    result.evidence_refs = [evidence.evidence_id]

    expect(validateAgentTask(task).ok).toBe(true)
    expect(validateAgentTaskResult(result).ok).toBe(true)
    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'EVIDENCE_SCOPE_OR_DATE_INVALID')).toBe(true)
  })

  it('rejects an Evidence Assess candidate_ref that was not returned by any executed action', () => {
    const { task, result } = fixture('EVIDENCE_ASSESS')
    result.payload = {
      assessments: [{
        claim_id: 'claim-1', candidate_ref: 'invented-evidence', relation: 'SUPPORTS',
        scope_status: 'UNKNOWN', date_status: 'UNKNOWN', freshness_status: 'UNKNOWN',
        anchor_status: 'MISSING', authority_status: 'UNKNOWN', missing_context: [],
      }],
      missing_claims: [], conflict_proposals: [],
    }

    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'UNSUPPORTED_REFERENCE')).toBe(true)
  })

  it('allows UNKNOWN evidence to be assessed as ambiguous without using it as evidence coverage', () => {
    const { task, result } = fixture('EVIDENCE_ASSESS')
    const evidence = evidenceRecord({
      evidenceId: 'ev-unknown-candidate', valueKind: 'UNKNOWN', freshnessStatus: 'UNKNOWN', sourceDate: null,
    })
    attachEvidenceAssessAction(task, evidence)
    result.payload = {
      assessments: [{
        claim_id: 'claim-1', candidate_ref: evidence.evidence_id, relation: 'AMBIGUOUS',
        scope_status: 'UNKNOWN', date_status: 'UNKNOWN', freshness_status: 'UNKNOWN',
        anchor_status: 'MISSING', authority_status: 'UNKNOWN', missing_context: ['not usable as evidence'],
      }],
      missing_claims: ['claim-1'], conflict_proposals: [],
    }
    result.evidence_refs = []

    expect(validateAgentTask(task).ok).toBe(true)
    expect(validateAgentTaskResult(result).ok).toBe(true)
    expect(validateAgentSemantics(task, result)).toEqual({ ok: true, issues: [] })
  })

  it('rejects document claims outside the supplied claim id pool', () => {
    const { task, result } = fixture('DOCUMENT_EXTRACT')
    const payload = result.payload as { proposed_claims: Array<{ claim_id: string }> }
    payload.proposed_claims[0].claim_id = 'invented-claim'

    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'OUTPUT_ID_NOT_IN_POOL')).toBe(true)
  })

  it('rejects document claims whose anchor was not supplied in parser blocks', () => {
    const { task, result } = fixture('DOCUMENT_EXTRACT')
    const payload = result.payload as { proposed_claims: Array<{ anchor: { page_index: number } }> }
    payload.proposed_claims[0].anchor.page_index = 9

    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'DOCUMENT_ANCHOR_NOT_SUPPLIED')).toBe(true)
  })
})
