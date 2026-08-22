export interface RuntimeErrorRecord {
  event: 'RUNTIME_STREAM_EXECUTION_FAILED'
  error_name: string
  error_message: string
}

const TERMINAL_AGENT_ERROR_CODES = new Set([
  'MODEL_JSON_INVALID',
  'RESULT_ECHO_MISMATCH',
  'RESULT_SCHEMA_INVALID',
  'RESULT_SEMANTIC_INVALID',
  'SAFETY_BLOCKED',
  'VERTEX_MODEL_RESPONSE_INCOMPLETE',
  'VERTEX_MODEL_RESPONSE_INVALID',
])

export interface RuntimeHttpFailure {
  status: 422 | 500
  body: { error: string }
}

function bounded(value: string): string {
  return value.replace(/[\r\n]+/g, ' ').slice(0, 500)
}

export function runtimeErrorRecord(error: unknown): RuntimeErrorRecord {
  return {
    event: 'RUNTIME_STREAM_EXECUTION_FAILED',
    error_name: bounded(error instanceof Error ? error.name : 'UnknownError'),
    error_message: bounded(error instanceof Error ? error.message : String(error)),
  }
}

export function runtimeHttpFailure(error: unknown): RuntimeHttpFailure {
  const code = error && typeof error === 'object' && 'code' in error
    && typeof error.code === 'string'
    ? error.code
    : null
  const providerStatus = error && typeof error === 'object' && 'status' in error
    && typeof error.status === 'number'
    ? error.status
    : null
  const errorName = error instanceof Error ? error.name : null
  const terminalProviderStatus = providerStatus !== null
    && providerStatus >= 400
    && providerStatus < 500
    && providerStatus !== 408
    && providerStatus !== 429
  const terminalAgentClass = errorName === 'AgentDispatchError'
    || errorName === 'AgentModelError'
    || errorName === 'VertexAgentModelError'
  if (terminalProviderStatus
    || (code && TERMINAL_AGENT_ERROR_CODES.has(code))
    || (code && terminalAgentClass && providerStatus === null)) {
    return { status: 422, body: { error: code ?? 'RUNTIME_AGENT_OUTPUT_INVALID' } }
  }
  return { status: 500, body: { error: 'RUNTIME_EXECUTION_FAILED' } }
}
