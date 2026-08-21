import { InMemoryRunner, isFinalResponse, isLlmAgent } from '@google/adk'
import { describe, expect, it, vi } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { createCaffeMateAdkRoot } from '../src/adk-runtime'
import { canonicalizeJson } from '../src/input-digest'
import type { AgentModelClient, ApprovedAgentModelConfig } from '../src/model-executor'
import type { AgentTask, AgentTaskResult } from '../src/types'

const approvedModel: ApprovedAgentModelConfig = {
  id: 'gemini-3.7-flash',
  region: 'global',
  thinkingLevel: 'medium',
}

function fixture(id: string): { task: AgentTask; result: AgentTaskResult } {
  const item = fixtureMatrix.cases.find((candidate) => candidate.id === id)
  if (!item) throw new Error(`missing fixture ${id}`)
  return structuredClone(item) as unknown as { task: AgentTask; result: AgentTaskResult }
}

async function collect(generator: AsyncGenerator<unknown, void, undefined>): Promise<unknown[]> {
  const events: unknown[] = []
  for await (const event of generator) events.push(event)
  return events
}

describe('ADK Agent Runtime adapter', () => {
  it('uses a deterministic non-LLM root and exactly the five pinned role children', () => {
    const modelClient: AgentModelClient = { generate: vi.fn() }
    const root = createCaffeMateAdkRoot({ modelClient, approvedModel: () => approvedModel })

    expect(root.name).toBe('CAFFEMATE_TASK_DISPATCHER')
    expect(isLlmAgent(root)).toBe(false)
    expect(root.subAgents.map((agent) => agent.name)).toEqual([
      'INTENT_INTERPRETER',
      'EVIDENCE_RESEARCHER',
      'PROPOSAL_AGENT',
      'DOCUMENT_ANALYST',
      'TYPED_CANDIDATE_AUDITOR',
    ])
    expect(root.subAgents.every((agent) => !isLlmAgent(agent))).toBe(true)
  })

  it('routes one validated task to exactly one role child and emits exactly one final child-authored JSON event', async () => {
    const selected = fixture('intent_delta-complete')
    const generate = vi.fn(async () => ({ kind: 'TEXT' as const, text: JSON.stringify(selected.result) }))
    const root = createCaffeMateAdkRoot({
      modelClient: { generate },
      approvedModel: () => approvedModel,
    })
    const runner = new InMemoryRunner({ agent: root, appName: 'caffemate-agents' })

    const events = await collect(runner.runEphemeral({
      userId: 'p-test',
      newMessage: { role: 'user', parts: [{ text: canonicalizeJson(selected.task) }] },
    })) as Array<{
      author?: string
      content?: { parts?: Array<{ text?: string }> }
    }>
    const finals = events.filter((event) => isFinalResponse(event as never))

    expect(generate).toHaveBeenCalledTimes(1)
    expect(finals).toHaveLength(1)
    expect(finals[0]?.author).toBe('INTENT_INTERPRETER')
    expect(finals[0]?.content?.parts).toHaveLength(1)
    expect(JSON.parse(finals[0]?.content?.parts?.[0]?.text ?? '')).toEqual(selected.result)
  })

  it('rejects a task with a stale digest before any child model invocation', async () => {
    const selected = fixture('intent_delta-complete')
    selected.task.input_digest = 'sha256:0000000000000000000000000000000000000000000000000000000000000000'
    const generate = vi.fn()
    const root = createCaffeMateAdkRoot({
      modelClient: { generate },
      approvedModel: () => approvedModel,
    })
    const runner = new InMemoryRunner({ agent: root, appName: 'caffemate-agents' })

    await expect(collect(runner.runEphemeral({
      userId: 'p-test',
      newMessage: { role: 'user', parts: [{ text: canonicalizeJson(selected.task) }] },
    }))).rejects.toThrowError('TASK_INPUT_DIGEST_MISMATCH')
    expect(generate).not.toHaveBeenCalled()
  })

  it('keeps the runtime blocked before the generation model is approved', async () => {
    const selected = fixture('intent_delta-complete')
    const generate = vi.fn()
    const root = createCaffeMateAdkRoot({
      modelClient: { generate },
      approvedModel: () => undefined,
    })
    const runner = new InMemoryRunner({ agent: root, appName: 'caffemate-agents' })

    await expect(collect(runner.runEphemeral({
      userId: 'p-test',
      newMessage: { role: 'user', parts: [{ text: canonicalizeJson(selected.task) }] },
    }))).rejects.toThrowError('MODEL_NOT_APPROVED')
    expect(generate).not.toHaveBeenCalled()
  })

  it('rejects request content that is not exactly one user text part', async () => {
    const modelClient: AgentModelClient = { generate: vi.fn() }
    const root = createCaffeMateAdkRoot({ modelClient, approvedModel: () => approvedModel })
    const runner = new InMemoryRunner({ agent: root, appName: 'caffemate-agents' })

    await expect(collect(runner.runEphemeral({
      userId: 'p-test',
      newMessage: { role: 'user', parts: [{ text: '{}' }, { text: '{}' }] },
    }))).rejects.toThrowError('RUNTIME_REQUEST_INVALID')
  })
})