import { describe, expect, it } from 'vitest'
import { buildSystemInstruction, PROMPTS } from '../src/prompts'

const rolePrompts = [
  'intent-interpreter.v2',
  'evidence-researcher.v1',
  'proposal-agent.v2',
  'proposal-agent.v3',
  'document-analyst.v1',
  'document-analyst.v2',
  'typed-candidate-auditor.v2',
] as const

describe('agent prompts', () => {
  it('defines the common policy and every role prompt', () => {
    expect(PROMPTS['common-system.v1']).toContain('typed, non-autonomous component')
    for (const version of rolePrompts) expect(PROMPTS[version].length).toBeGreaterThan(80)
  })

  it('always composes common policy before the role prompt', () => {
    const instruction = buildSystemInstruction('proposal-agent.v3')
    expect(instruction.indexOf('typed, non-autonomous component')).toBeLessThan(instruction.indexOf('Your role is Proposal Agent'))
    expect(instruction).toContain('Do not invent a brand')
    expect(instruction).toContain('exactly requested_candidate_count')
    expect(instruction).toContain('does not justify an empty proposal')
    expect(instruction).toContain('never to evidence_refs')
    expect(instruction).toContain('CAPITAL_FIT')
    expect(instruction).toContain('OPERATING_FIT')
    expect(instruction).toContain('USER_PREFERENCE_FIT')
    expect(instruction).toContain('AREA_FIT')
    expect(instruction).toContain('EVIDENCE_COMPLETENESS')
    expect(instruction).toContain('Do not assign numeric scores')
  })

  it('constrains candidate-audit references to controller-supplied typed pools', () => {
    const instruction = buildSystemInstruction('typed-candidate-auditor.v2')
    expect(instruction).toContain('Reference fields are closed sets')
    expect(instruction).toContain('value_kind is neither DECLARED_ASSUMPTION nor UNKNOWN')
    expect(instruction).toContain('leave claim_refs and evidence_refs empty')
    expect(instruction).toContain('calculation_version, input_digest, output_digest, or candidate_id')
  })

  it('constrains document extraction ids and anchors to controller-supplied pools', () => {
    const instruction = buildSystemInstruction('document-analyst.v2')
    expect(instruction).toContain('claim_id_pool')
    expect(instruction).toContain('copy one unused value exactly')
    expect(instruction).toContain('copy one parser_blocks[].anchor object exactly')
    expect(instruction).toContain('Do not create, shorten, translate, or reinterpret identifiers or anchors')
  })
})
