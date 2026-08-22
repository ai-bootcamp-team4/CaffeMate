import { isBaseAgent, isLlmAgent } from '@google/adk'
import { describe, expect, it } from 'vitest'
import { rootAgent } from '../caffemate-agents/agent'

const ROLE_NAMES = [
  'INTENT_INTERPRETER',
  'EVIDENCE_RESEARCHER',
  'PROPOSAL_AGENT',
  'DOCUMENT_ANALYST',
  'TYPED_CANDIDATE_AUDITOR',
]

describe('Agent Engine runtime entrypoint', () => {
  it('exports the deterministic ADK root expected by the ADK loader', () => {
    expect(isBaseAgent(rootAgent)).toBe(true)
    expect(isLlmAgent(rootAgent)).toBe(false)
    expect(rootAgent.name).toBe('CAFFEMATE_TASK_DISPATCHER')
    expect(rootAgent.subAgents.map((agent) => agent.name)).toEqual(ROLE_NAMES)
  })
})
