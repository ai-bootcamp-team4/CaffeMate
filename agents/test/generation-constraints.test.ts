import { describe, expect, it } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { buildAgentGenerationConstraints } from '../src/generation-constraints'
import { buildAgentModelInput } from '../src/vertex-model-client'
import type { AgentTask } from '../src/types'

function task(taskType: AgentTask['task_type']): AgentTask {
  const fixture = fixtureMatrix.cases.find(
    (item) => item.task.task_type === taskType && item.result.status === 'COMPLETE',
  )
  if (!fixture) throw new Error(`missing COMPLETE fixture for ${taskType}`)
  return structuredClone(fixture.task) as unknown as AgentTask
}

describe('task-derived generation constraints', () => {
  it('tells EVIDENCE_PLAN the exact claim, action, tool-version, polarity and scope rules', () => {
    expect(buildAgentGenerationConstraints(task('EVIDENCE_PLAN'))).toMatchObject({
      allocated_action_ids: ['action-1', 'action-2'],
      claim_rules: [{
        claim_id: 'claim-1',
        geographic_scope: {
          scope_type: 'ADMINISTRATIVE_AREA',
          scope_id: '11200690',
          boundary_version: '2026-01',
        },
      }],
      allowed_tools: [
        { tool_name: 'get_area_profile', tool_version: '1.0.0' },
        { tool_name: 'get_source_health', tool_version: '1.0.0' },
      ],
      complete_claim_coverage_exact: true,
      action_ids_globally_unique: true,
      support_action_polarity: 'SUPPORT',
      counter_action_polarity: 'COUNTER',
    })
  })

  it('exposes only supplied EVIDENCE_ASSESS claim and candidate ids as closed sets', () => {
    const value = task('EVIDENCE_ASSESS')
    const payload = value.payload as Record<string, unknown>
    payload.executed_actions = [{
      structured_result: {
        evidence_records: [
          { evidence_id: 'evidence-known', value_kind: 'INTEGER' },
          { evidence_id: 'evidence-unknown', value_kind: 'UNKNOWN' },
        ],
      },
    }]

    expect(buildAgentGenerationConstraints(value)).toMatchObject({
      reference_fields_are_closed_sets: true,
      allowed_evidence_refs: ['evidence-known'],
      allowed_claim_refs: ['claim-1'],
      allowed_candidate_refs: ['evidence-known', 'evidence-unknown'],
      allowed_assumption_refs: [],
      assessment_claim_ids: ['claim-1'],
      assessment_candidate_refs: ['evidence-known', 'evidence-unknown'],
    })
  })

  it('keeps Proposal seed assumptions separate from Evidence and ignores area evidence ids', () => {
    expect(buildAgentGenerationConstraints(task('PROPOSE_INDEPENDENT'))).toMatchObject({
      allowed_evidence_refs: [],
      allowed_assumption_refs: ['seed-registry-1'],
      allocated_proposal_id: 'proposal-independent-1',
      allocated_source_id: 'independent-small-v1',
      allowed_parameter_contracts: [
        expect.objectContaining({
          field_path: 'operations.seats',
          value_kind: 'INTEGER',
          unit: 'seat',
          minimum: 8,
          maximum: 24,
        }),
      ],
    })

    expect(buildAgentGenerationConstraints(task('PROPOSE_FRANCHISE'))).toMatchObject({
      allowed_evidence_refs: ['ev-franchise-1'],
      allowed_assumption_refs: [],
      allocated_proposal_id: 'proposal-franchise-1',
      allocated_source_id: 'brand-1',
    })
  })

  it('keeps UNKNOWN Evidence out of independent parameter support refs', () => {
    const value = task('PROPOSE_INDEPENDENT')
    const payload = value.payload as Record<string, unknown>
    payload.evidence_records = [{ evidence_id: 'ev-unknown', value_kind: 'UNKNOWN' }]

    expect(buildAgentGenerationConstraints(value)).toMatchObject({
      allowed_assumption_refs: ['ev-unknown', 'seed-registry-1'],
      allowed_parameter_support_refs: ['seed-registry-1'],
    })
  })

  it('exposes Candidate Audit candidate and calculation references without widening them', () => {
    expect(buildAgentGenerationConstraints(task('CANDIDATE_AUDIT'))).toMatchObject({
      audit_candidate_ids: ['candidate-1'],
      allowed_candidate_refs: ['candidate-1'],
      allowed_calculation_refs: [
        'calc-v1',
        'candidate-1',
        `sha256:${'b'.repeat(64)}`,
        `sha256:${'c'.repeat(64)}`,
      ],
      pass_findings_must_be_empty: true,
    })
  })

  it('gives DOCUMENT_EXTRACT the exact controller-issued ids, revision and parser anchors', () => {
    expect(buildAgentGenerationConstraints(task('DOCUMENT_EXTRACT'))).toMatchObject({
      allocated_claim_ids: ['doc-claim-1'],
      allowed_predicates: ['LEASE_DEPOSIT'],
      document_revision_id: 'docrev-1',
      allowed_anchors: [{
        document_revision_id: 'docrev-1',
        page_index: 0,
        section_path: '임대조건',
        table_id: null,
        row: null,
        column: null,
        bbox: null,
      }],
    })
  })

  it('states the hidden RESULT_EXPLAIN ref equality contract and current evidence set', () => {
    expect(buildAgentGenerationConstraints(task('RESULT_EXPLAIN'))).toMatchObject({
      allowed_evidence_refs: ['evidence-1'],
      payload_evidence_refs_must_equal_top_level_evidence_refs: true,
    })
  })

  it('projects INTENT_DELTA field-operation pairing into the model input', () => {
    const value = task('INTENT_DELTA')
    const constraints = buildAgentGenerationConstraints(value)
    expect(constraints).toMatchObject({
      operation_rules: [{
        field_path: '/founder/borrowing_intent',
        allowed_kinds: ['SET'],
        typed_value_kinds: ['STRING'],
        expected_old_value_rule: 'EXACT_CURRENT_STATE_VALUE',
      }],
      propose_delta_affected_workflow_codes: ['FIRST_PROPOSAL'],
    })
    expect(buildAgentModelInput(value)).toMatchObject({
      task_type: 'INTENT_DELTA',
      generation_constraints: constraints,
      payload: value.payload,
    })
  })
})
