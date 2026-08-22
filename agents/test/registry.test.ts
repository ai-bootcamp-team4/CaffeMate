import { describe, expect, it } from 'vitest'
import { AGENT_MODEL, GCP_LOCATIONS, TASK_REGISTRY } from '../src/registry'

const expected = {
  INTENT_DELTA: 'INTENT_INTERPRETER',
  EVIDENCE_PLAN: 'EVIDENCE_RESEARCHER',
  EVIDENCE_ASSESS: 'EVIDENCE_RESEARCHER',
  PROPOSE_INDEPENDENT: 'PROPOSAL_AGENT',
  PROPOSE_FRANCHISE: 'PROPOSAL_AGENT',
  DOCUMENT_EXTRACT: 'DOCUMENT_ANALYST',
  CANDIDATE_AUDIT: 'TYPED_CANDIDATE_AUDITOR',
} as const

const expectedDeadlines = {
  INTENT_DELTA: 30,
  EVIDENCE_PLAN: 30,
  EVIDENCE_ASSESS: 30,
  PROPOSE_INDEPENDENT: 30,
  PROPOSE_FRANCHISE: 30,
  DOCUMENT_EXTRACT: 60,
  CANDIDATE_AUDIT: 60,
} as const

describe('task registry', () => {
  it('maps every task type to exactly one fixed agent', () => {
    expect(Object.keys(TASK_REGISTRY).sort()).toEqual(Object.keys(expected).sort())
    for (const [taskType, agentName] of Object.entries(expected)) {
      const registration = TASK_REGISTRY[taskType as keyof typeof TASK_REGISTRY]
      expect(registration.agentName).toBe(agentName)
      expect(registration.deadlineSeconds).toBe(expectedDeadlines[taskType as keyof typeof expectedDeadlines])
    }
  })

  it('pins the approved global generation model while keeping runtime and RAG in Seoul', () => {
    expect(GCP_LOCATIONS).toEqual({
      runtime: 'asia-northeast3',
      generation: 'global',
      rag: 'asia-northeast3',
      embedding: 'asia-northeast3',
    })
    expect(AGENT_MODEL.id).toBe('gemini-3.7-flash')
    expect(AGENT_MODEL.approvalStatus).toBe('APPROVED')
    expect(AGENT_MODEL.region).toBe('global')
    expect(AGENT_MODEL.thinkingLevel).toBe('high')
    expect(AGENT_MODEL.allowGlobalFallback).toBe(false)
    expect(AGENT_MODEL.networkEnabled).toBe(true)
  })
})
