import { describe, expect, it } from 'vitest'
import { buildSystemInstruction, PROMPTS } from '../src/prompts'

const rolePrompts = [
  'intent-interpreter.v2',
  'evidence-researcher.v1',
  'proposal-agent.v2',
  'document-analyst.v1',
  'typed-candidate-auditor.v2',
] as const

describe('agent prompts', () => {
  it('defines the common policy and every role prompt', () => {
    expect(PROMPTS['common-system.v1']).toContain('typed, non-autonomous component')
    for (const version of rolePrompts) expect(PROMPTS[version].length).toBeGreaterThan(80)
  })

  it('always composes common policy before the role prompt', () => {
    const instruction = buildSystemInstruction('proposal-agent.v2')
    expect(instruction.indexOf('typed, non-autonomous component')).toBeLessThan(instruction.indexOf('Your role is Proposal Agent'))
    expect(instruction).toContain('Do not invent a brand')
    expect(instruction).toContain('exactly requested_candidate_count')
    expect(instruction).toContain('does not justify an empty proposal')
    expect(instruction).toContain('never to evidence_refs')
  })

  it('constrains candidate-audit references to controller-supplied typed pools', () => {
    const instruction = buildSystemInstruction('typed-candidate-auditor.v2')
    expect(instruction).toContain('Reference fields are closed sets')
    expect(instruction).toContain('value_kind is neither DECLARED_ASSUMPTION nor UNKNOWN')
    expect(instruction).toContain('leave claim_refs and evidence_refs empty')
    expect(instruction).toContain('calculation_version, input_digest, output_digest, or candidate_id')
  })
})
