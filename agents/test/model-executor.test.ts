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
  region: 'global',
  thinkingLevel: 'high',
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
    expect(invocation.region).toBe('global')
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

  it('repairs the production Candidate Audit status-contract failure once', async () => {
    const { task, result } = completeFixture('CANDIDATE_AUDIT')
    const invalid = {
      ...result,
      status: 'NEEDS_EVIDENCE',
      missing_claim_ids: [],
      reason_codes: ['INSUFFICIENT_CONTEXT'],
    }
    const generate = vi.fn(async (invocation: AgentModelInvocation) => ({
      kind: 'TEXT' as const,
      text: JSON.stringify(invocation.repairAttempt === 0 ? invalid : result),
    }))

    const repaired = await dispatchAgentTask(
      task,
      createModelExecutors({ generate }, APPROVED_MODEL),
    )

    expect(repaired).toEqual(result)
    expect(generate).toHaveBeenCalledTimes(2)
    expect(generate.mock.calls[0]?.[0].repairAttempt).toBe(0)
    expect(generate.mock.calls[1]?.[0].repairAttempt).toBe(1)
    expect(generate.mock.calls[1]?.[0].systemInstruction).toContain(PROMPTS['repair.v1'])
    expect(generate.mock.calls[1]?.[0].task.repair_context).toMatchObject({
      validator_errors: expect.arrayContaining([
        expect.objectContaining({ code: 'RESULT_SCHEMA_INVALID' }),
      ]),
    })
  })

  it('repairs incomplete Candidate Audit coverage before returning a final event', async () => {
    const { task, result } = completeFixture('CANDIDATE_AUDIT')
    const invalid = structuredClone(result)
    if (!invalid.payload || typeof invalid.payload !== 'object' || Array.isArray(invalid.payload)) {
      throw new Error('candidate audit fixture payload is invalid')
    }
    invalid.payload = { ...invalid.payload, candidate_audits: [] }
    const generate = vi.fn(async (invocation: AgentModelInvocation) => ({
      kind: 'TEXT' as const,
      text: JSON.stringify(invocation.repairAttempt === 0 ? invalid : result),
    }))

    const repaired = await dispatchAgentTask(
      task,
      createModelExecutors({ generate }, APPROVED_MODEL),
    )

    expect(repaired).toEqual(result)
    expect(generate).toHaveBeenCalledTimes(2)
    expect(generate.mock.calls[1]?.[0].task.repair_context).toMatchObject({
      validator_errors: expect.arrayContaining([
        expect.objectContaining({ code: 'CANDIDATE_AUDIT_COVERAGE_INVALID' }),
      ]),
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
