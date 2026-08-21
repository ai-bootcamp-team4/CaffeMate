import { describe, expect, it } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { validateAgentSemantics } from '../src/semantic-validator'
import type { AgentTask, AgentTaskResult } from '../src/types'

function fixture(taskType: string) {
  const found = fixtureMatrix.cases.find((item) => item.task.task_type === taskType && item.result.status === 'COMPLETE')
  if (!found) throw new Error(`missing fixture ${taskType}`)
  return structuredClone(found) as unknown as { task: AgentTask; result: AgentTaskResult }
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

  it('rejects document claims outside the supplied claim id pool', () => {
    const { task, result } = fixture('DOCUMENT_EXTRACT')
    const payload = result.payload as { proposed_claims: Array<{ claim_id: string }> }
    payload.proposed_claims[0].claim_id = 'invented-claim'

    const validation = validateAgentSemantics(task, result)
    expect(validation.ok).toBe(false)
    expect(validation.issues.some((issue) => issue.code === 'OUTPUT_ID_NOT_IN_POOL')).toBe(true)
  })
})
