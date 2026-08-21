import { describe, expect, it } from 'vitest'
import { AGENT_MODEL, TASK_REGISTRY } from '../src/registry'

const expected = {
  INTENT_DELTA: 'INTENT_INTERPRETER',
  EVIDENCE_PLAN: 'EVIDENCE_RESEARCHER',
  EVIDENCE_ASSESS: 'EVIDENCE_RESEARCHER',
  PROPOSE_INDEPENDENT: 'PROPOSAL_AGENT',
  PROPOSE_FRANCHISE: 'PROPOSAL_AGENT',
  DOCUMENT_EXTRACT: 'DOCUMENT_ANALYST',
  CANDIDATE_AUDIT: 'TYPED_CANDIDATE_AUDITOR',
} as const

describe('task registry', () => {
  it('maps every task type to exactly one fixed agent', () => {
    expect(Object.keys(TASK_REGISTRY).sort()).toEqual(Object.keys(expected).sort())
    for (const [taskType, agentName] of Object.entries(expected)) {
      expect(TASK_REGISTRY[taskType as keyof typeof TASK_REGISTRY].agentName).toBe(agentName)
    }
  })

  it('pins the user-selected model without enabling a network fallback', () => {
    expect(AGENT_MODEL.id).toBeNull()
    expect(AGENT_MODEL.approvalStatus).toBe('PENDING_HUMAN_DECISION')
    expect(AGENT_MODEL.region).toBe('asia-northeast3')
    expect(AGENT_MODEL.allowGlobalFallback).toBe(false)
    expect(AGENT_MODEL.networkEnabled).toBe(false)
  })
})
