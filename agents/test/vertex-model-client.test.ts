import { describe, expect, it, vi } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import { buildModelInvocation } from '../src/model-executor'
import {
  buildAgentModelInput,
  safeGenerationTelemetry,
  VertexAgentModelClient,
  VertexAgentModelError,
} from '../src/vertex-model-client'
import type { AgentTask } from '../src/types'

const PROJECT_ID = 'proj-aj20-211200020328'
const REGION = 'global'
const MODEL_ID = 'gemini-3.7-flash'

function task(): AgentTask {
  const value = structuredClone(fixtureMatrix.cases[0]?.task) as unknown as AgentTask
  value.deadline_at = new Date(Date.now() + 60_000).toISOString()
  return value
}

function evidencePlanTask(): AgentTask {
  const item = fixtureMatrix.cases.find((entry) => entry.task.task_type === 'EVIDENCE_PLAN' && entry.result.status === 'COMPLETE')
  if (!item) throw new Error('missing EVIDENCE_PLAN fixture')
  const value = structuredClone(item.task) as unknown as AgentTask
  value.deadline_at = new Date(Date.now() + 60_000).toISOString()
  return value
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
          thinkingConfig: { thinkingLevel: 'LOW' },
          responseJsonSchema: {
            type: 'object',
            additionalProperties: false,
            required: expect.arrayContaining([
              'status',
              'payload',
              'evidence_refs',
              'missing_claim_ids',
              'reason_codes',
              'warnings',
            ]),
            properties: {
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
      const contents = body.contents as Array<{ parts: Array<{ text: string }> }>
      const modelInput = JSON.parse(String(contents[0]?.parts[0]?.text)) as Record<string, unknown>
      expect(modelInput).toEqual(buildAgentModelInput(task()))
      expect(modelInput).not.toHaveProperty('task_id')
      expect(modelInput).not.toHaveProperty('invocation_id')
      expect(modelInput).not.toHaveProperty('head_fence')
      expect(modelInput).not.toHaveProperty('input_digest')
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

  it('binds the provider fetch to the logical task deadline', async () => {
    const value = task()
    value.deadline_at = new Date(Date.now() - 1_000).toISOString()
    const fetchImpl = vi.fn(async () => Response.json({}))
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl,
    })

    await expect(client.generate(buildModelInvocation(value, {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))).rejects.toMatchObject({ code: 'RUNTIME_TIMED_OUT' })
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('accepts Gemini 3 final text carrying an opaque thought signature', async () => {
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({
        candidates: [{
          content: {
            role: 'model',
            parts: [{
              text: '{"status":"ABSTAIN"}',
              thoughtSignature: 'opaque-provider-signature',
            }],
          },
          finishReason: 'STOP',
        }],
      }),
    })

    await expect(client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))).resolves.toEqual({ kind: 'TEXT', text: '{"status":"ABSTAIN"}' })
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

  it('records bounded generation telemetry without task content or identifiers', async () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined)
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({
        candidates: [{
          content: { role: 'model', parts: [{ text: '{"status":"ABSTAIN"}' }] },
          finishReason: 'STOP',
        }],
        usageMetadata: {
          promptTokenCount: 120,
          candidatesTokenCount: 40,
          thoughtsTokenCount: 15,
          totalTokenCount: 175,
        },
      }),
    })

    await client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))

    const telemetry = JSON.parse(String(info.mock.calls.at(-1)?.[0])) as Record<string, unknown>
    expect(telemetry).toMatchObject({
      event: 'VERTEX_AGENT_GENERATION',
      task_type: 'INTENT_DELTA',
      preflight: false,
      repair_attempt: 0,
      thinking_level: 'low',
      max_output_tokens: 4096,
      http_status: 200,
      finish_reason: 'STOP',
      prompt_token_count: 120,
      candidate_token_count: 40,
      thoughts_token_count: 15,
      total_token_count: 175,
    })
    expect(telemetry.elapsed_ms).toEqual(expect.any(Number))
    expect(telemetry.request_bytes).toEqual(expect.any(Number))
    expect(JSON.stringify(telemetry)).not.toContain('task-1-complete')
    expect(JSON.stringify(telemetry)).not.toContain('inv-1-complete')
    info.mockRestore()
  })

  it('marks only release probe tasks as preflight telemetry', () => {
    const probeTask = task()
    probeTask.task_id = 'runtime-preflight-probe-1'
    const invocation = buildModelInvocation(probeTask, {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    })

    expect(safeGenerationTelemetry({
      invocation,
      elapsedMs: 10,
      requestBytes: 100,
      httpStatus: 200,
    }).preflight).toBe(true)
  })

  it('fails closed when the single response part mixes text with a non-text payload', async () => {
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({
        candidates: [{
          content: {
            role: 'model',
            parts: [{
              text: '{"status":"ABSTAIN"}',
              inlineData: { mimeType: 'application/octet-stream', data: 'AA==' },
            }],
          },
          finishReason: 'STOP',
        }],
      }),
    })

    await expect(client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))).rejects.toMatchObject({
      code: 'VERTEX_MODEL_RESPONSE_INVALID',
    })
  })

  it('fails closed when a thought signature is present but malformed', async () => {
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({
        candidates: [{
          content: {
            role: 'model',
            parts: [{
              text: '{"status":"ABSTAIN"}',
              thoughtSignature: '',
            }],
          },
          finishReason: 'STOP',
        }],
      }),
    })

    await expect(client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))).rejects.toMatchObject({
      code: 'VERTEX_MODEL_RESPONSE_INVALID',
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

  it('projects only EVIDENCE_PLAN provider-union extra keys before returning model text', async () => {
    const evidenceTask = evidencePlanTask()
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({
        candidates: [{
          content: {
            role: 'model',
            parts: [{
              text: JSON.stringify({
                payload: {
                  claim_plans: [{
                    support_actions: [{
                      tool_name: 'get_area_profile',
                      typed_arguments: {
                        administrative_code: '11680',
                        boundary_version: '2026-01',
                        as_of: '2026-08-22',
                        metrics: ['store_count'],
                      },
                    }],
                    counter_actions: [],
                  }],
                },
              }),
            }],
          },
          finishReason: 'STOP',
        }],
      }),
    })

    const generated = await client.generate(buildModelInvocation(evidenceTask, {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))

    expect(generated.kind).toBe('TEXT')
    if (generated.kind !== 'TEXT') throw new Error('expected text response')
    const normalized = JSON.parse(generated.text) as {
      payload: { claim_plans: Array<{ support_actions: Array<{ typed_arguments: Record<string, unknown> }> }> }
    }
    expect(normalized.payload.claim_plans[0]?.support_actions[0]?.typed_arguments).toEqual({
      administrative_code: '11680',
      boundary_version: '2026-01',
      as_of: '2026-08-22',
    })
  })

  it('preserves only a bounded provider error summary for non-2xx diagnostics', async () => {
    const client = new VertexAgentModelClient({
      projectId: PROJECT_ID,
      region: REGION,
      accessToken: async () => 'adc-token',
      fetchImpl: async () => Response.json({
        error: {
          code: 400,
          status: 'INVALID_ARGUMENT',
          message: `generationConfig.responseJsonSchema is invalid\n${'x'.repeat(600)}`,
          details: [{ '@type': 'sensitive-provider-detail', task: 'must-not-leak' }],
        },
      }, { status: 400 }),
    })

    await expect(client.generate(buildModelInvocation(task(), {
      id: MODEL_ID,
      region: REGION,
      thinkingLevel: 'high',
    }))).rejects.toMatchObject({
      code: 'VERTEX_MODEL_HTTP_ERROR',
      status: 400,
      providerStatus: 'INVALID_ARGUMENT',
      providerMessage: expect.stringMatching(/^generationConfig\.responseJsonSchema is invalid x+$/),
    })

    try {
      await client.generate(buildModelInvocation(task(), {
        id: MODEL_ID,
        region: REGION,
        thinkingLevel: 'high',
      }))
      expect.unreachable('expected VertexAgentModelError')
    } catch (error) {
      expect(error).toBeInstanceOf(VertexAgentModelError)
      const modelError = error as VertexAgentModelError
      expect(modelError.providerMessage.length).toBeLessThanOrEqual(300)
      expect(modelError.message).not.toContain('must-not-leak')
      expect(modelError.message).not.toContain('sensitive-provider-detail')
    }
  })
})
