import { describe, expect, it, vi } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { buildModelInvocation } from '../src/model-executor'
import { VertexAgentModelClient, VertexAgentModelError } from '../src/vertex-model-client'
import type { AgentTask } from '../src/types'

const PROJECT_ID = 'proj-aj20-211200020328'
const REGION = 'global'
const MODEL_ID = 'gemini-3.7-flash'

function task(): AgentTask {
  return structuredClone(fixtureMatrix.cases[0]?.task) as unknown as AgentTask
}

describe('Vertex Agent model client', () => {
  it('calls only the explicit global Vertex endpoint with ADC bearer auth and JSON-only generation', async () => {
    const fetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      expect(String(url)).toBe(
        `https://aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/publishers/google/models/${MODEL_ID}:generateContent`,
      )
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer adc-token')
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      expect(body).toMatchObject({
        systemInstruction: { parts: [{ text: expect.any(String) }] },
        contents: [{ role: 'user', parts: [{ text: expect.any(String) }] }],
        generationConfig: {
          candidateCount: 1,
          responseMimeType: 'application/json',
          maxOutputTokens: 4096,
          seed: 17,
          thinkingConfig: { thinkingLevel: 'HIGH' },
          responseJsonSchema: {
            type: 'object',
            additionalProperties: false,
            required: expect.arrayContaining([
              'schema_version',
              'task_id',
              'invocation_id',
              'agent_name',
              'task_type',
              'head_fence_seen',
              'input_digest',
              'output_schema_id',
              'status',
              'payload',
            ]),
            properties: {
              schema_version: { type: 'string', enum: ['1.0.0'] },
              task_id: { type: 'string', enum: ['task-1-complete'] },
              invocation_id: { type: 'string', enum: ['inv-1-complete'] },
              agent_name: { type: 'string', enum: ['INTENT_INTERPRETER'] },
              task_type: { type: 'string', enum: ['INTENT_DELTA'] },
              output_schema_id: { type: 'string', enum: ['caffemate.agent.intent-result.v1'] },
              status: {
                type: 'string',
                enum: ['COMPLETE', 'NEEDS_EVIDENCE', 'NEEDS_HUMAN', 'ABSTAIN', 'INVALID'],
              },
              payload: {
                anyOf: [
                  {
                    type: 'object',
                    additionalProperties: false,
                    required: ['decision', 'operations', 'clarifying_questions', 'affected_workflow_codes', 'risk_flags'],
                    properties: {
                      decision: { enum: ['PROPOSE_DELTA', 'CLARIFY', 'NOOP', 'UNSUPPORTED'] },
                      operations: { type: 'array', items: expect.any(Object) },
                      clarifying_questions: { type: 'array', items: { type: 'string' } },
                      affected_workflow_codes: { type: 'array', items: { type: 'string' } },
                      risk_flags: { type: 'array', items: { type: 'string' } },
                    },
                  },
                  { type: 'null' },
                ],
              },
            },
          },
        },
      })
      expect((body.generationConfig as Record<string, unknown>).temperature).toBeUndefined()
      return Response.json({
        candidates: [{
          content: { parts: [{ text: '{"status":"ABSTAIN"}' }], role: 'model' },
          finishReason: 'STOP',
        }],
      })
    })
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl,
    })

    const response = await client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))

    expect(response).toEqual({ kind: 'TEXT', text: '{"status":"ABSTAIN"}' })
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('rejects any non-global generation configuration instead of silently switching locations', () => {
    expect(() => new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: 'asia-northeast3' as typeof REGION,
      accessToken: async () => 'adc-token',
    })).toThrowError('VERTEX_REGION_NOT_ALLOWED')
  })

  it('surfaces provider safety blocks as the model safety outcome', async () => {
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({
        candidates: [{ content: { role: 'model' }, finishReason: 'SAFETY' }],
      }),
    })

    await expect(client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))).resolves.toEqual({ kind: 'SAFETY_BLOCKED' })
  })

  it('fails closed when the provider candidate did not finish with STOP', async () => {
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({
        candidates: [{
          content: { role: 'model', parts: [{ text: '{"schema_version":"1.0.0"' }] },
          finishReason: 'MAX_TOKENS',
        }],
      }),
    })

    await expect(client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))).rejects.toMatchObject({
      code: 'VERTEX_MODEL_RESPONSE_INCOMPLETE',
    })
  })

  it('fails closed on HTTP errors without returning provider response text as an Agent result', async () => {
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({ error: { message: 'quota exhausted' } }, { status: 429 }),
    })

    await expect(client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))).rejects.toBeInstanceOf(VertexAgentModelError)
    await expect(client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))).rejects.toMatchObject({ code: 'VERTEX_MODEL_HTTP_ERROR', status: 429 })
  })
})