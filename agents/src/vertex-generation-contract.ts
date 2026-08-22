import { AGENT_MODEL } from './registry'
import type { AgentModelResponse } from './model-executor'

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

export interface VertexGenerationRequestInput {
  systemInstruction: string
  userText: string
  responseJsonSchema: Record<string, unknown>
  maxOutputTokens: number
}

export function vertexGenerationEndpoint(
  projectId: string,
  region: typeof AGENT_MODEL.region,
  model: string,
): string {
  const host = region === 'global' ? 'aiplatform.googleapis.com' : `${region}-aiplatform.googleapis.com`
  return `https://${host}/v1/projects/${projectId}/locations/${region}/publishers/google/models/${encodeURIComponent(model)}:generateContent`
}

export function buildVertexGenerationRequest(input: VertexGenerationRequestInput): Record<string, unknown> {
  return {
    systemInstruction: { parts: [{ text: input.systemInstruction }] },
    contents: [{ role: 'user', parts: [{ text: input.userText }] }],
    generationConfig: {
      candidateCount: 1,
      responseMimeType: 'application/json',
      responseJsonSchema: input.responseJsonSchema,
      seed: 17,
      thinkingConfig: {
        thinkingLevel: AGENT_MODEL.thinkingLevel.toUpperCase(),
      },
      maxOutputTokens: input.maxOutputTokens,
    },
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function singleTextPart(candidate: Record<string, unknown>): string | null {
  const content = candidate.content
  if (!isRecord(content) || !Array.isArray(content.parts) || content.parts.length !== 1) return null
  const [part] = content.parts
  if (!isRecord(part)) return null
  const keys = Object.keys(part)
  if (keys.length !== 1 || keys[0] !== 'text') return null
  if (typeof part.text !== 'string' || !part.text.trim()) return null
  return part.text
}

export function parseVertexGenerationResponse(payload: unknown): AgentModelResponse {
  if (!isRecord(payload)) {
    throw new VertexAgentModelError('VERTEX_MODEL_RESPONSE_INVALID', 'generateContent response must be an object')
  }

  const promptFeedback = payload.promptFeedback
  if (isRecord(promptFeedback)
    && typeof promptFeedback.blockReason === 'string'
    && promptFeedback.blockReason) {
    return { kind: 'SAFETY_BLOCKED' }
  }

  const candidates = payload.candidates
  if (!Array.isArray(candidates) || candidates.length !== 1 || !isRecord(candidates[0])) {
    throw new VertexAgentModelError('VERTEX_MODEL_RESPONSE_INVALID', 'expected exactly one model candidate')
  }

  const [candidate] = candidates
  if (candidate.finishReason === 'SAFETY') return { kind: 'SAFETY_BLOCKED' }
  if (candidate.finishReason !== 'STOP') {
    throw new VertexAgentModelError(
      'VERTEX_MODEL_RESPONSE_INCOMPLETE',
      `candidate finished with ${typeof candidate.finishReason === 'string' ? candidate.finishReason : 'missing finish reason'}`,
    )
  }

  const text = singleTextPart(candidate)
  if (!text) {
    throw new VertexAgentModelError(
      'VERTEX_MODEL_RESPONSE_INVALID',
      'candidate must contain exactly one non-empty text-only part',
    )
  }
  return { kind: 'TEXT', text }
}