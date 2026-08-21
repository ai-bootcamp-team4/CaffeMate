import { describe, expect, it, vi } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { dispatchAgentTask } from '../src/dispatcher'
import {
  AgentModelError,
  buildModelInvocation,
  createModelExecutors,
  type AgentModelClient,
  type AgentModelInvocation,
} from '../src/model-executor'
import { PROMPTS } from '../src/prompts'
import type { AgentTask, AgentTaskResult } from '../src/types'

const APPROVED_MODEL = {
  id: 'approved-model-after-gcp-preflight',
  region: 'asia-northeast3',
  thinkingLevel: 'medium',
} as const

function completeFixture(taskType: string) {
  const item = fixtureMatrix.cases.find((entry) => entry.task.task_type === taskType && entry.result.status === 'COMPLETE')
  if (!item) throw new Error(`missing fixture ${taskType}`)
  return structuredClone(item) as unknown as { task: AgentTask; result: AgentTaskResult }
}

describe('local model-backed Agent executors', () => {
  it('requires an approved model configuration and exposes no tool surface to the model', () => {
    const { task } = completeFixture('PROPOSE_INDEPENDENT')

    expect(() => buildModelInvocation(task)).toThrowError('MODEL_NOT_APPROVED')
    const invocation = buildModelInvocation(task, APPROVED_MODEL)

    expect(invocation.model).toBe('approved-model-after-gcp-preflight')
    expect(invocation.region).toBe('asia-northeast3')
    expect(invocation.thinkingLevel).toBe('medium')
    expect(invocation.maxOutputTokens).toBe(8192)
    expect(invocation.outputSchemaId).toBe('caffemate.agent.independent-proposal-result.v1')
    expect(invocation.systemInstruction).toContain(PROMPTS['common-system.v1'])
    expect(invocation.systemInstruction).toContain(PROMPTS['proposal-agent.v1'])
    expect(invocation.systemInstruction).not.toContain(PROMPTS['repair.v1'])
    expect('tools' in invocation).toBe(false)
    expect('temperature' in invocation).toBe(false)
    expect('candidateCount' in invocation).toBe(false)
  })

  it('adds the repair instruction only for a repair task', () => {
    const { task } = completeFixture('INTENT_DELTA')
    const repairTask = {
      ...task,
      invocation_id: 'repair-invocation-1',
      repair_attempt: 1,
      repair_of_invocation_id: task.invocation_id,
      repair_context: {
        previous_response_text: '{"bad":true}',
        previous_response_digest: `sha256:${'b'.repeat(64)}`,
        validator_errors: [{ code: 'RESULT_SCHEMA_INVALID', json_pointer: '/payload', message: 'invalid payload' }],
      },
    } as AgentTask

    const invocation = buildModelInvocation(repairTask, APPROVED_MODEL)

    expect(invocation.systemInstruction).toContain(PROMPTS['repair.v1'])
    expect(invocation.repairAttempt).toBe(1)
  })

  it('parses one JSON result and lets the deterministic dispatcher validate it', async () => {
    const { task, result } = completeFixture('EVIDENCE_PLAN')
    const generate = vi.fn(async (invocation: AgentModelInvocation) => {
      expect(invocation.taskType).toBe('EVIDENCE_PLAN')
      return { kind: 'TEXT' as const, text: JSON.stringify(result) }
    })
    const client: AgentModelClient = { generate }

    const dispatched = await dispatchAgentTask(task, createModelExecutors(client, APPROVED_MODEL))

    expect(dispatched).toEqual(result)
    expect(generate).toHaveBeenCalledTimes(1)
    expect(generate.mock.calls[0]?.[0]).toMatchObject({
      model: 'approved-model-after-gcp-preflight',
      taskType: 'EVIDENCE_PLAN',
      agentName: 'EVIDENCE_RESEARCHER',
      outputSchemaId: 'caffemate.agent.evidence-plan-result.v1',
    })
  })

  it('rejects prose or Markdown instead of extracting JSON from it', async () => {
    const { task } = completeFixture('INTENT_DELTA')
    const client: AgentModelClient = {
      generate: async () => ({ kind: 'TEXT', text: '```json\n{"status":"COMPLETE"}\n```' }),
    }
    const executor = createModelExecutors(client, APPROVED_MODEL).INTENT_INTERPRETER
    if (!executor) throw new Error('missing intent executor')

    await expect(executor(task)).rejects.toMatchObject({ code: 'MODEL_JSON_INVALID' })
  })

  it('surfaces a safety block as a transport outcome instead of an Agent status', async () => {
    const { task } = completeFixture('INTENT_DELTA')
    const client: AgentModelClient = { generate: async () => ({ kind: 'SAFETY_BLOCKED' }) }
    const executor = createModelExecutors(client, APPROVED_MODEL).INTENT_INTERPRETER
    if (!executor) throw new Error('missing intent executor')

    await expect(executor(task)).rejects.toBeInstanceOf(AgentModelError)
    await expect(executor(task)).rejects.toMatchObject({ code: 'SAFETY_BLOCKED' })
  })
})
