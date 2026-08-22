import { describe, expect, it } from 'vitest'
import { runtimeErrorRecord, runtimeHttpFailure } from '../src/runtime-error'

describe('Agent Runtime error telemetry', () => {
  it('records only bounded error identity without request contents', () => {
    const record = runtimeErrorRecord(new Error(`generation failed\n${'x'.repeat(1000)}`))

    expect(record.event).toBe('RUNTIME_STREAM_EXECUTION_FAILED')
    expect(record.error_name).toBe('Error')
    expect(record.error_message).not.toContain('\n')
    expect(record.error_message.length).toBe(500)
    expect(Object.keys(record)).toEqual(['event', 'error_name', 'error_message'])
  })
})

describe('Agent Runtime HTTP failure classification', () => {
  it('makes incomplete model output terminal instead of transport-retryable', () => {
    expect(runtimeHttpFailure(Object.assign(new Error('cut off'), {
      code: 'VERTEX_MODEL_RESPONSE_INCOMPLETE',
    }))).toEqual({
      status: 422,
      body: { error: 'VERTEX_MODEL_RESPONSE_INCOMPLETE' },
    })
  })

  it('keeps unknown execution failures retryable without exposing raw messages', () => {
    expect(runtimeHttpFailure(new Error('sensitive provider details'))).toEqual({
      status: 500,
      body: { error: 'RUNTIME_EXECUTION_FAILED' },
    })
  })

  it('preserves only the safe cleanup code so Control API can enqueue deletion', () => {
    expect(runtimeHttpFailure(Object.assign(new Error('private delete details'), {
      code: 'RUNTIME_SESSION_CLEANUP_FAILED',
    }))).toEqual({
      status: 500,
      body: { error: 'RUNTIME_SESSION_CLEANUP_FAILED' },
    })
  })

  it('keeps transient provider status retryable and terminal provider status non-retryable', () => {
    const providerError = (status: number) => Object.assign(new Error('provider'), {
      name: 'VertexAgentModelError',
      code: 'VERTEX_MODEL_HTTP_ERROR',
      status,
    })

    expect(runtimeHttpFailure(providerError(429)).status).toBe(500)
    expect(runtimeHttpFailure(providerError(503)).status).toBe(500)
    expect(runtimeHttpFailure(providerError(400))).toEqual({
      status: 422,
      body: { error: 'VERTEX_MODEL_HTTP_ERROR' },
    })
  })
})
