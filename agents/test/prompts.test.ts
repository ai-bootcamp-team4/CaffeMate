import { describe, expect, it } from 'vitest'
import { buildSystemInstruction, PROMPTS } from '../src/prompts'

const rolePrompts = [
  'intent-interpreter.v1',
  'evidence-researcher.v1',
  'proposal-agent.v1',
  'document-analyst.v1',
  'typed-candidate-auditor.v1',
] as const

describe('agent prompts', () => {
  it('defines the common policy and every role prompt', () => {
    expect(PROMPTS['common-system.v1']).toContain('typed, non-autonomous component')
    for (const version of rolePrompts) expect(PROMPTS[version].length).toBeGreaterThan(80)
  })

  it('always composes common policy before the role prompt', () => {
    const instruction = buildSystemInstruction('proposal-agent.v1')
    expect(instruction.indexOf('typed, non-autonomous component')).toBeLessThan(instruction.indexOf('Your role is Proposal Agent'))
    expect(instruction).toContain('Do not invent a brand')
  })
})
