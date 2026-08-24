import { canonicalizeJson } from './input-digest'
import { createApplicationDefaultGoogleCloudContext, type GoogleCloudContext } from './gcp-auth'
import { AGENT_MODEL } from './registry'
import {
  buildVertexRolePayloadSchema,
  evidenceAssessOutputBounds,
  normalizeVertexEvidencePlanResult,
} from './vertex-response-schema'
import type { AgentModelClient, AgentModelInvocation, AgentModelResponse } from './model-executor'
import type { AgentTask } from './types'
import { injectCurrentTrace, setCurrentSpanAttributes, withActiveSpan } from './telemetry'
import {
  buildVertexGenerationRequest,
  parseVertexGenerationResponse,
  VertexAgentModelError,
  vertexGenerationEndpoint,
} from './vertex-generation-contract'

export { VertexAgentModelError } from './vertex-generation-contract'

export interface VertexAgentModelClientOptions {
  projectId: string
  region: typeof AGENT_MODEL.region
  accessToken: () => Promise<string>
  fetchImpl?: typeof fetch
}

interface SafeGenerationTelemetry {
  event: 'VERTEX_AGENT_GENERATION'
  task_type: AgentTask['task_type']
  preflight: boolean
  repair_attempt: number
  elapsed_ms: number
  request_bytes: number
  thinking_level: AgentModelInvocation['thinkingLevel']
  max_output_tokens: number
  http_status: number
  finish_reason: string | null
  prompt_token_count: number | null
  candidate_token_count: number | null
  thoughts_token_count: number | null
  total_token_count: number | null
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function collectProposalEvidenceIds(payload: unknown): string[] {
  const candidate = record(payload)
  const records = Array.isArray(candidate?.evidence_records) ? candidate.evidence_records : []
  return [...new Set(records.flatMap((value) => {
    const evidence = record(value)
    return typeof evidence?.evidence_id === 'string' ? [evidence.evidence_id] : []
  }))]
}

function collectProposalClaimIds(payload: unknown): string[] {
  const candidate = record(payload)
  const values = Array.isArray(candidate?.claim_id_pool) ? candidate.claim_id_pool : []
  return [...new Set(values.filter((value): value is string => typeof value === 'string'))]
}

function numericMetric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function safeGenerationTelemetry(input: {
  invocation: AgentModelInvocation
  elapsedMs: number
  requestBytes: number
  httpStatus: number
  providerPayload?: unknown
}): SafeGenerationTelemetry {
  const payload = record(input.providerPayload)
  const candidates = Array.isArray(payload?.candidates) ? payload.candidates : []
  const candidate = record(candidates[0])
  const usage = record(payload?.usageMetadata)
  return {
    event: 'VERTEX_AGENT_GENERATION',
    task_type: input.invocation.taskType,
    preflight: input.invocation.task.task_id.startsWith('runtime-preflight-'),
    repair_attempt: input.invocation.repairAttempt,
    elapsed_ms: Math.max(0, Math.round(input.elapsedMs)),
    request_bytes: input.requestBytes,
    thinking_level: input.invocation.thinkingLevel,
    max_output_tokens: input.invocation.maxOutputTokens,
    http_status: input.httpStatus,
    finish_reason: typeof candidate?.finishReason === 'string' ? candidate.finishReason : null,
    prompt_token_count: numericMetric(usage?.promptTokenCount),
    candidate_token_count: numericMetric(usage?.candidatesTokenCount),
    thoughts_token_count: numericMetric(usage?.thoughtsTokenCount),
    total_token_count: numericMetric(usage?.totalTokenCount),
  }
}

function boundedProviderMessage(value: unknown): string {
  if (typeof value !== 'string') return ''
  return value.replace(/[\r\n]+/g, ' ').trim().slice(0, 300)
}

async function providerErrorSummary(response: Response): Promise<{ status?: string; message: string }> {
  try {
    const body = await response.json() as unknown
    if (!body || typeof body !== 'object' || Array.isArray(body)) return { message: '' }
    const error = (body as Record<string, unknown>).error
    if (!error || typeof error !== 'object' || Array.isArray(error)) return { message: '' }
    const providerError = error as Record<string, unknown>
    return {
      status: typeof providerError.status === 'string' ? providerError.status.slice(0, 80) : undefined,
      message: boundedProviderMessage(providerError.message),
    }
  } catch {
    return { message: '' }
  }
}

export function buildAgentModelInput(task: AgentTask): Record<string, unknown> {
  return {
    task_type: task.task_type,
    repair_attempt: task.repair_attempt,
    input_artifacts: task.input_artifacts,
    available_tool_catalog: task.available_tool_catalog,
    payload: task.payload,
    ...(task.repair_context === undefined
      ? {}
      : { repair_context: task.repair_context }),
  }
}

/**
 * Vertex structured output supports only a subset of JSON Schema. Keep this
 * provider schema self-contained and deliberately simpler than the authority
 * contract; Ajv validation after generation remains the final contract gate.
 */
export function buildAgentTaskResultResponseJsonSchema(task: AgentTask): Record<string, unknown> {
  const evidenceBounds = evidenceAssessOutputBounds(task)
  const intentOutput = task.task_type === 'INTENT_DELTA'
  const proposalOutput = task.task_type === 'PROPOSE_INDEPENDENT' || task.task_type === 'PROPOSE_FRANCHISE'
  const proposalEvidenceIds = proposalOutput
    ? collectProposalEvidenceIds(task.payload)
    : []
  const proposalClaimIds = proposalOutput
    ? collectProposalClaimIds(task.payload)
    : []
  const evidenceRefs = task.task_type === 'EVIDENCE_ASSESS'
    ? { type: 'array', items: { type: 'string' }, maxItems: evidenceBounds.candidateCount }
    : proposalOutput
      ? {
          type: 'array',
          items: { type: 'string' },
          maxItems: proposalEvidenceIds.length,
        }
    : { type: 'array', items: { type: 'string' }, ...(intentOutput ? { maxItems: 0 } : {}) }
  const missingClaimIds = task.task_type === 'EVIDENCE_ASSESS'
    ? { type: 'array', items: { type: 'string' }, maxItems: evidenceBounds.claimCount }
    : proposalOutput
      ? {
          type: 'array',
          items: { type: 'string' },
          maxItems: proposalClaimIds.length,
        }
    : { type: 'array', items: { type: 'string' }, ...(intentOutput ? { maxItems: 0 } : {}) }
  return {
    type: 'object',
    additionalProperties: false,
    propertyOrdering: [
      'status',
      'payload',
      'evidence_refs',
      'missing_claim_ids',
      'reason_codes',
      'warnings',
    ],
    required: [
      'status',
      'payload',
      'evidence_refs',
      'missing_claim_ids',
      'reason_codes',
      'warnings',
    ],
    properties: {
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
      evidence_refs: evidenceRefs,
      missing_claim_ids: missingClaimIds,
      reason_codes: { type: 'array', items: { type: 'string' }, ...(intentOutput ? { maxItems: 5 } : {}) },
      warnings: { type: 'array', items: { type: 'string' }, ...(intentOutput ? { maxItems: 5 } : {}) },
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
    return withActiveSpan(
      'gen_ai.generate_content',
      {
        'caffemate.agent.task_type': invocation.taskType,
        'caffemate.prompt.version': invocation.task.prompt_version,
        'gen_ai.request.model': invocation.model,
      },
      () => this.generateTraced(invocation),
    )
  }

  private async generateTraced(invocation: AgentModelInvocation): Promise<AgentModelResponse> {
    if (invocation.region !== this.options.region) {
      throw new VertexAgentModelError('VERTEX_REGION_NOT_ALLOWED', 'invocation region differs from the pinned client region')
    }
    if (!invocation.model) throw new VertexAgentModelError('VERTEX_MODEL_REQUIRED', 'approved model id is required')

    const token = await this.options.accessToken()
    if (!token) throw new VertexAgentModelError('VERTEX_AUTH_TOKEN_MISSING', 'ADC did not return an access token')

    const endpoint = vertexGenerationEndpoint(this.options.projectId, this.options.region, invocation.model)
    const requestBody = JSON.stringify(buildVertexGenerationRequest({
      systemInstruction: invocation.systemInstruction,
      userText: canonicalizeJson(buildAgentModelInput(invocation.task)),
      responseJsonSchema: buildAgentTaskResultResponseJsonSchema(invocation.task),
      thinkingLevel: invocation.thinkingLevel,
      maxOutputTokens: invocation.maxOutputTokens,
    }))
    const startedAt = Date.now()
    const response = await this.fetchImpl(endpoint, {
      method: 'POST',
      headers: injectCurrentTrace({
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      }),
      body: requestBody,
    })

    if (!response.ok) {
      console.info(JSON.stringify(safeGenerationTelemetry({
        invocation,
        elapsedMs: Date.now() - startedAt,
        requestBytes: new TextEncoder().encode(requestBody).byteLength,
        httpStatus: response.status,
      })))
      const provider = await providerErrorSummary(response)
      throw new VertexAgentModelError(
        'VERTEX_MODEL_HTTP_ERROR',
        `generateContent returned HTTP ${response.status}`,
        response.status,
        provider.status,
        provider.message,
      )
    }

    const providerPayload = await response.json()
    const usage = record(record(providerPayload)?.usageMetadata)
    setCurrentSpanAttributes({
      'http.response.status_code': response.status,
      ...(numericMetric(usage?.promptTokenCount) === null
        ? {}
        : { 'gen_ai.usage.input_tokens': numericMetric(usage?.promptTokenCount) as number }),
      ...(numericMetric(usage?.candidatesTokenCount) === null
        ? {}
        : { 'gen_ai.usage.output_tokens': numericMetric(usage?.candidatesTokenCount) as number }),
    })
    console.info(JSON.stringify(safeGenerationTelemetry({
      invocation,
      elapsedMs: Date.now() - startedAt,
      requestBytes: new TextEncoder().encode(requestBody).byteLength,
      httpStatus: response.status,
      providerPayload,
    })))
    const generated = parseVertexGenerationResponse(providerPayload)
    if (generated.kind !== 'TEXT' || invocation.task.task_type !== 'EVIDENCE_PLAN') return generated
    try {
      const parsed = JSON.parse(generated.text) as unknown
      return {
        kind: 'TEXT',
        text: JSON.stringify(normalizeVertexEvidencePlanResult(invocation.task, parsed)),
      }
    } catch {
      return generated
    }
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
