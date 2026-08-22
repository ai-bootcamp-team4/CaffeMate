import { canonicalizeJson } from './input-digest'
import { createApplicationDefaultGoogleCloudContext, type GoogleCloudContext } from './gcp-auth'
import { AGENT_MODEL } from './registry'
import { buildVertexRolePayloadSchema } from './vertex-response-schema'
import type { AgentModelClient, AgentModelInvocation, AgentModelResponse } from './model-executor'
import type { AgentTask } from './types'

export class VertexAgentModelError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status?: number,
  ) {
    super(`${code}: ${message}`)
    this.name = 'VertexAgentModelError'
  }
}

export interface VertexAgentModelClientOptions {
  projectId: string
  region: typeof AGENT_MODEL.region
  accessToken: () => Promise<string>
  fetchImpl?: typeof fetch
}

interface VertexCandidate {
  finishReason?: string
  content?: {
    parts?: Array<{ text?: string; functionCall?: unknown; functionResponse?: unknown }>
  }
}

interface VertexGenerateContentResponse {
  candidates?: VertexCandidate[]
  promptFeedback?: { blockReason?: string }
}

function generationEndpoint(projectId: string, region: typeof AGENT_MODEL.region, model: string): string {
  const host = region === 'global' ? 'aiplatform.googleapis.com' : `${region}-aiplatform.googleapis.com`
  return `https://${host}/v1/projects/${projectId}/locations/${region}/publishers/google/models/${encodeURIComponent(model)}:generateContent`
}

function textFrom(candidate: VertexCandidate): string | null {
  const parts = candidate.content?.parts ?? []
  if (parts.length !== 1) return null
  const [part] = parts
  if (!part || typeof part.text !== 'string' || !part.text.trim()) return null
  if (part.functionCall !== undefined || part.functionResponse !== undefined) return null
  return part.text
}

function nullableStringSchema(): Record<string, unknown> {
  return { anyOf: [{ type: 'string' }, { type: 'null' }] }
}

/**
 * Vertex structured output supports only a subset of JSON Schema. Keep this
 * provider schema self-contained and deliberately simpler than the authority
 * contract; Ajv validation after generation remains the final contract gate.
 */
export function buildAgentTaskResultResponseJsonSchema(task: AgentTask): Record<string, unknown> {
  return {
    type: 'object',
    additionalProperties: false,
    propertyOrdering: [
      'schema_version',
      'task_id',
      'invocation_id',
      'agent_name',
      'task_type',
      'workflow_run_id',
      'stage_run_id',
      'venture_project_id',
      'head_fence_seen',
      'input_digest',
      'output_schema_id',
      'status',
      'payload',
      'evidence_refs',
      'missing_claim_ids',
      'reason_codes',
      'warnings',
    ],
    required: [
      'schema_version',
      'task_id',
      'invocation_id',
      'agent_name',
      'task_type',
      'workflow_run_id',
      'stage_run_id',
      'venture_project_id',
      'head_fence_seen',
      'input_digest',
      'output_schema_id',
      'status',
      'payload',
      'evidence_refs',
      'missing_claim_ids',
      'reason_codes',
      'warnings',
    ],
    properties: {
      schema_version: { type: 'string', enum: [task.schema_version] },
      task_id: { type: 'string', enum: [task.task_id] },
      invocation_id: { type: 'string', enum: [task.invocation_id] },
      agent_name: { type: 'string', enum: [task.agent_name] },
      task_type: { type: 'string', enum: [task.task_type] },
      workflow_run_id: { type: 'string', enum: [task.workflow_run_id] },
      stage_run_id: { type: 'string', enum: [task.stage_run_id] },
      venture_project_id: { type: 'string', enum: [task.venture_project_id] },
      head_fence_seen: {
        type: 'object',
        additionalProperties: false,
        required: [
          'workflow_generation',
          'state_version',
          'founder_snapshot_id',
          'area_snapshot_id',
          'evidence_snapshot_id',
          'policy_snapshot_id',
          'index_generation_id',
          'seed_registry_id',
        ],
        properties: {
          workflow_generation: { type: 'integer' },
          state_version: { type: 'integer' },
          founder_snapshot_id: nullableStringSchema(),
          area_snapshot_id: nullableStringSchema(),
          evidence_snapshot_id: nullableStringSchema(),
          policy_snapshot_id: { type: 'string' },
          index_generation_id: nullableStringSchema(),
          seed_registry_id: nullableStringSchema(),
        },
      },
      input_digest: { type: 'string', enum: [task.input_digest] },
      output_schema_id: { type: 'string', enum: [task.output_schema_id] },
      status: {
        type: 'string',
        enum: ['COMPLETE', 'NEEDS_EVIDENCE', 'NEEDS_HUMAN', 'ABSTAIN', 'INVALID'],
      },
      payload: {
        anyOf: [
          buildVertexRolePayloadSchema(task),
          { type: 'null' },
        ],
      },
      evidence_refs: { type: 'array', items: { type: 'string' } },
      missing_claim_ids: { type: 'array', items: { type: 'string' } },
      reason_codes: { type: 'array', items: { type: 'string' } },
      warnings: { type: 'array', items: { type: 'string' } },
    },
  }
}

export class VertexAgentModelClient implements AgentModelClient {
  private readonly fetchImpl: typeof fetch

  constructor(private readonly options: VertexAgentModelClientOptions) {
    if (!options.projectId) throw new VertexAgentModelError('VERTEX_PROJECT_REQUIRED', 'GCP project id is required')
    if (options.region !== AGENT_MODEL.region) {
      throw new VertexAgentModelError(
        'VERTEX_REGION_NOT_ALLOWED',
        `Agent generation is pinned to ${AGENT_MODEL.region}`,
      )
    }
    this.fetchImpl = options.fetchImpl ?? fetch
  }

  async generate(invocation: AgentModelInvocation): Promise<AgentModelResponse> {
    if (invocation.region !== this.options.region) {
      throw new VertexAgentModelError('VERTEX_REGION_NOT_ALLOWED', 'invocation region differs from the pinned client region')
    }
    if (!invocation.model) throw new VertexAgentModelError('VERTEX_MODEL_REQUIRED', 'approved model id is required')

    const token = await this.options.accessToken()
    if (!token) throw new VertexAgentModelError('VERTEX_AUTH_TOKEN_MISSING', 'ADC did not return an access token')

    const endpoint = generationEndpoint(this.options.projectId, this.options.region, invocation.model)
    const response = await this.fetchImpl(endpoint, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: invocation.systemInstruction }] },
        contents: [{ role: 'user', parts: [{ text: canonicalizeJson(invocation.task) }] }],
        generationConfig: {
          candidateCount: 1,
          responseMimeType: 'application/json',
          responseJsonSchema: buildAgentTaskResultResponseJsonSchema(invocation.task),
          seed: 17,
          thinkingConfig: {
            thinkingLevel: invocation.thinkingLevel.toUpperCase(),
          },
          maxOutputTokens: invocation.maxOutputTokens,
        },
      }),
    })

    if (!response.ok) {
      throw new VertexAgentModelError(
        'VERTEX_MODEL_HTTP_ERROR',
        `generateContent returned HTTP ${response.status}`,
        response.status,
      )
    }

    const payload = await response.json() as VertexGenerateContentResponse
    if (payload.promptFeedback?.blockReason) return { kind: 'SAFETY_BLOCKED' }
    const candidates = payload.candidates ?? []
    if (candidates.length !== 1) {
      throw new VertexAgentModelError('VERTEX_MODEL_RESPONSE_INVALID', 'expected exactly one model candidate')
    }
    if (candidates[0]?.finishReason === 'SAFETY') return { kind: 'SAFETY_BLOCKED' }
    if (candidates[0]?.finishReason !== 'STOP') {
      throw new VertexAgentModelError(
        'VERTEX_MODEL_RESPONSE_INCOMPLETE',
        `candidate finished with ${candidates[0]?.finishReason ?? 'missing finish reason'}`,
      )
    }
    const text = candidates[0] ? textFrom(candidates[0]) : null
    if (!text) {
      throw new VertexAgentModelError('VERTEX_MODEL_RESPONSE_INVALID', 'candidate must contain exactly one non-empty text part')
    }
    return { kind: 'TEXT', text }
  }
}

export function createApplicationDefaultVertexAgentModelClient(
  cloud: GoogleCloudContext = createApplicationDefaultGoogleCloudContext(),
): AgentModelClient {
  let clientPromise: Promise<VertexAgentModelClient> | undefined
  return {
    async generate(invocation: AgentModelInvocation): Promise<AgentModelResponse> {
      clientPromise ??= cloud.projectId().then((projectId) => new VertexAgentModelClient({
        projectId,
        region: AGENT_MODEL.region,
        accessToken: cloud.accessToken,
      }))
      return (await clientPromise).generate(invocation)
    },
  }
}