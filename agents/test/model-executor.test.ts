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

function semanticResult(result: AgentTaskResult) {
  return {
    status: result.status,
    payload: result.payload,
    evidence_refs: result.evidence_refs,
    missing_claim_ids: result.missing_claim_ids,
    reason_codes: result.reason_codes,
    warnings: result.warnings,
  }
}

describe('local model-backed Agent executors', () => {
  it('requires an approved model configuration and exposes no tool surface to the model', () => {
    const { task } = completeFixture('PROPOSE_INDEPENDENT')

    expect(() => buildModelInvocation(task)).toThrowError('MODEL_NOT_APPROVED')
    const invocation = buildModelInvocation(task, APPROVED_MODEL)

    expect(invocation.model).toBe('approved-model-after-gcp-preflight')
    expect(invocation.region).toBe('global')
    expect(invocation.thinkingLevel).toBe('low')
    expect(invocation.maxOutputTokens).toBe(4096)
    expect(invocation.outputSchemaId).toBe('caffemate.agent.independent-proposal-result.v1')
    expect(invocation.systemInstruction).toContain(PROMPTS['common-system.v1'])
    expect(invocation.systemInstruction).toContain(PROMPTS['proposal-agent.v3'])
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
      return { kind: 'TEXT' as const, text: JSON.stringify(semanticResult(result)) }
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

  it('rejects an invalid Candidate Audit result without another model generation', async () => {
    const { task, result } = completeFixture('CANDIDATE_AUDIT')
    const invalid = {
      ...result,
      status: 'NEEDS_EVIDENCE' as const,
      missing_claim_ids: [],
      reason_codes: ['INSUFFICIENT_CONTEXT'],
    }
    const generate = vi.fn(async (invocation: AgentModelInvocation) => {
      expect(invocation.repairAttempt).toBe(0)
      return {
        kind: 'TEXT' as const,
        text: JSON.stringify(semanticResult(invalid)),
      }
    })

    await expect(dispatchAgentTask(
      task, createModelExecutors({ generate }, APPROVED_MODEL),
    )).rejects.toMatchObject({ code: 'RESULT_SCHEMA_INVALID' })

    expect(generate).toHaveBeenCalledTimes(1)
    expect(generate.mock.calls[0]?.[0].repairAttempt).toBe(0)
  })

  it('returns partial Candidate Audit coverage after one model generation', async () => {
    const { task, result } = completeFixture('CANDIDATE_AUDIT')
    const invalid = structuredClone(result)
    if (!invalid.payload || typeof invalid.payload !== 'object' || Array.isArray(invalid.payload)) {
      throw new Error('candidate audit fixture payload is invalid')
    }
    invalid.payload = { ...invalid.payload, candidate_audits: [] }
    const generate = vi.fn(async () => ({
      kind: 'TEXT' as const,
      text: JSON.stringify(semanticResult(invalid)),
    }))

    const partial = await dispatchAgentTask(
      task,
      createModelExecutors({ generate }, APPROVED_MODEL),
    )

    expect(partial).toEqual(invalid)
    expect(generate).toHaveBeenCalledTimes(1)
  })

  it('returns Proposal abstention after one model generation', async () => {
    const { task, result } = completeFixture('PROPOSE_INDEPENDENT')
    const invalid = {
      ...result,
      status: 'ABSTAIN' as const,
      payload: null,
      reason_codes: ['INSUFFICIENT_CONTEXT'],
    }
    const generate = vi.fn(async () => ({
      kind: 'TEXT' as const,
      text: JSON.stringify(semanticResult(invalid)),
    }))
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined)

    try {
      const abstained = await dispatchAgentTask(
        task,
        createModelExecutors({ generate }, APPROVED_MODEL),
      )

      expect(abstained).toEqual(invalid)
      expect(generate).toHaveBeenCalledTimes(1)
      const events = info.mock.calls.map(([line]) => JSON.parse(String(line)))
      expect(events.at(-1)).toMatchObject({
        event: 'AGENT_RESULT_VALIDATION',
        task_type: 'PROPOSE_INDEPENDENT',
        repair_attempt: 0,
        outcome: 'VALID',
      })
    } finally {
      info.mockRestore()
    }
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

  it('rejects model attempts to generate Runtime-owned envelope fields', async () => {
    const { task, result } = completeFixture('INTENT_DELTA')
    const client: AgentModelClient = {
      generate: async () => ({
        kind: 'TEXT',
        text: JSON.stringify({
          ...semanticResult(result),
          task_id: 'model-controlled-task',
          head_fence_seen: { workflow_generation: 999 },
        }),
      }),
    }
    const executor = createModelExecutors(client, APPROVED_MODEL).INTENT_INTERPRETER
    if (!executor) throw new Error('missing intent executor')

    await expect(executor(task)).rejects.toMatchObject({
      code: 'MODEL_SEMANTIC_ENVELOPE_INVALID',
    })
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
